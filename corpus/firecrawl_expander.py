"""
corpus/firecrawl_expander.py
Expand the SocialProof corpus using Firecrawl's crawl + scrape API.

WHAT THIS DOES
──────────────
Given a URL (or a list of URLs from a topic query), Firecrawl:
  1. Crawls the site / page and returns clean markdown text
  2. Provides title, description, metadata, and links
Then this script:
  3. Cleans and splits the text into sentences
  4. Runs each sentence through the existing quality gate
  5. Inserts articles + sentences into corpus.db (same schema)
  6. Sentences are ready for re-indexing with:
       python retrieval/build_index.py --rebuild

USAGE
──────
# Crawl a single URL and ingest into corpus
python corpus/firecrawl_expander.py --url "https://www.rappler.com/science/health" --limit 20

# Crawl multiple seed URLs from a file (one URL per line)
python corpus/firecrawl_expander.py --seeds seeds.txt --limit 50

# Scrape a single article page (no crawling)
python corpus/firecrawl_expander.py --scrape "https://rappler.com/some-article"

# Dry-run: show what would be inserted without writing to DB
python corpus/firecrawl_expander.py --url "https://www.who.int/news" --dry-run

ENVIRONMENT
───────────
Set FIRECRAWL_API_KEY in your .env or export it before running.
Replace the placeholder below with your key, or keep it in .env.

INTEGRATION
───────────
This script is intentionally standalone — it imports only from corpus.*
(db, scraper utilities) so it can be run independently or called from
a pipeline. After ingestion, run build_index.py to rebuild the FAISS index.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv()

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from corpus.db import insert_article, insert_pipeline_sentences, article_exists
from corpus.scraper import clean_text, split_sentences

# ── Config ────────────────────────────────────────────────────────────────────
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "YOUR_API_KEY_HERE")
FIRECRAWL_BASE    = "https://api.firecrawl.dev/v1"

# How long to poll for async crawl jobs (seconds)
CRAWL_POLL_INTERVAL = 3
CRAWL_POLL_TIMEOUT  = 300   # 5 minutes max

# Sentence cap per article (matches existing scraper defaults)
DEFAULT_SENTENCE_CAP = 60

# Pipeline to assign ingested sentences — change to "factcheck" or "stats" if needed
DEFAULT_PIPELINE = "news"


# ─────────────────────────────────────────────────────────────────────────────
# Firecrawl API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type":  "application/json",
    }


def scrape_url(url: str, formats: list[str] | None = None) -> dict | None:
    """
    Call Firecrawl /scrape on a single URL.
    Returns the parsed page dict or None on failure.

    Firecrawl returns:
      {
        "success": true,
        "data": {
          "markdown": "...",
          "metadata": { "title": "...", "description": "...", "sourceURL": "...", ... }
        }
      }
    """
    formats = formats or ["markdown"]
    payload = {
        "url": url,
        "formats": formats,
        "onlyMainContent": True,   # strip nav / footers automatically
        "removeBase64Images": True,
    }
    try:
        resp = requests.post(
            f"{FIRECRAWL_BASE}/scrape",
            headers=_headers(),
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("success"):
            return result.get("data", {})
        print(f"  [firecrawl] scrape failed: {result}")
    except Exception as e:
        print(f"  [firecrawl] scrape error for {url}: {e}")
    return None


def crawl_url(
    url: str,
    limit: int = 20,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> list[dict]:
    """
    Call Firecrawl /crawl (async) on a seed URL.
    Polls until complete, returns list of page dicts.

    Each page dict has the same shape as scrape_url() output:
      { "markdown": "...", "metadata": { "title": ..., "sourceURL": ... } }
    """
    payload: dict[str, Any] = {
        "url": url,
        "limit": limit,
        "scrapeOptions": {
            "formats": ["markdown"],
            "onlyMainContent": True,
            "removeBase64Images": True,
        },
    }
    if include_paths:
        payload["includePaths"] = include_paths
    if exclude_paths:
        payload["excludePaths"] = exclude_paths

    # Submit crawl job
    try:
        resp = requests.post(
            f"{FIRECRAWL_BASE}/crawl",
            headers=_headers(),
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        job = resp.json()
    except Exception as e:
        print(f"  [firecrawl] crawl submit error: {e}")
        return []

    if not job.get("success"):
        print(f"  [firecrawl] crawl rejected: {job}")
        return []

    job_id = job.get("id")
    print(f"  [firecrawl] crawl job started: {job_id}")

    # Poll for results
    deadline = time.time() + CRAWL_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(CRAWL_POLL_INTERVAL)
        try:
            poll = requests.get(
                f"{FIRECRAWL_BASE}/crawl/{job_id}",
                headers=_headers(),
                timeout=30,
            )
            poll.raise_for_status()
            status = poll.json()
        except Exception as e:
            print(f"  [firecrawl] poll error: {e}")
            continue

        state = status.get("status", "")
        completed = status.get("completed", 0)
        total     = status.get("total", "?")
        print(f"  [firecrawl] status={state} pages={completed}/{total}")

        if state == "completed":
            return status.get("data", [])
        if state in ("failed", "cancelled"):
            print(f"  [firecrawl] job ended with state={state}")
            return []

    print(f"  [firecrawl] crawl timed out after {CRAWL_POLL_TIMEOUT}s")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion helpers
# ─────────────────────────────────────────────────────────────────────────────

def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return "unknown"


def _ingest_page(
    page: dict,
    pipeline: str = DEFAULT_PIPELINE,
    sentence_cap: int = DEFAULT_SENTENCE_CAP,
    dry_run: bool = False,
) -> dict:
    """
    Parse one Firecrawl page dict and insert into corpus.db.
    Returns a summary dict: { url, title, sentences_added, skipped }
    """
    meta   = page.get("metadata", {})
    url    = meta.get("sourceURL") or meta.get("url", "")
    title  = meta.get("title", "")
    domain = _domain_from_url(url)
    raw_md = page.get("markdown", "")

    if not url or not raw_md:
        return {"url": url, "title": title, "sentences_added": 0, "skipped": True, "reason": "empty"}

    # Skip if already in corpus
    if not dry_run and article_exists(url):
        return {"url": url, "title": title, "sentences_added": 0, "skipped": True, "reason": "duplicate"}

    # Clean markdown → plain text (strip headers, links, images)
    plain = _md_to_plain(raw_md)
    plain = clean_text(plain)

    sentences = split_sentences(plain)[:sentence_cap]
    word_count = len(plain.split())

    if dry_run:
        print(f"  [dry-run] {url}")
        print(f"            title={title!r}")
        print(f"            sentences={len(sentences)}, words={word_count}")
        return {"url": url, "title": title, "sentences_added": len(sentences), "skipped": False}

    # Insert article
    date_pub = meta.get("publishedTime") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    article_id = insert_article(
        source_domain  = domain,
        url            = url,
        title          = title,
        content        = plain,
        date_published = date_pub,
        word_count     = word_count,
    )

    if article_id == -1:
        return {"url": url, "title": title, "sentences_added": 0, "skipped": True, "reason": "insert_failed"}

    # Insert sentences (quality gate runs inside insert_pipeline_sentences)
    added = insert_pipeline_sentences(
        article_id  = article_id,
        sentences   = sentences,
        source_domain = domain,
        url         = url,
        pipeline    = pipeline,
    )

    return {"url": url, "title": title, "sentences_added": added, "skipped": False}


_MD_NOISE = re.compile(
    r"!\[.*?\]\(.*?\)"       # images
    r"|(?<!!)\[([^\]]*)\]\([^\)]*\)"  # links → keep label
    r"|^#{1,6}\s+"           # headings
    r"|^\s*[-*_]{3,}\s*$"   # horizontal rules
    r"|\*{1,2}([^*]+)\*{1,2}"  # bold/italic → keep text
    r"|`{1,3}[^`]*`{1,3}",  # code spans/blocks
    re.MULTILINE,
)


def _md_to_plain(md: str) -> str:
    """
    Strip markdown syntax to plain text, preserving readable content.
    Keeps link labels (drops URLs), strips images entirely.
    """
    # Remove images first (before the link pattern absorbs them)
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", md)
    # Links → keep label text
    text = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)
    # Headings → plain text (remove # prefix)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold / italic → unwrap
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
    # Inline / fenced code → remove
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    # Horizontal rules
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(results: list[dict]) -> None:
    total     = len(results)
    ingested  = sum(1 for r in results if not r["skipped"])
    skipped   = total - ingested
    sentences = sum(r["sentences_added"] for r in results)
    print()
    print("─" * 60)
    print(f"  Pages processed : {total}")
    print(f"  Ingested        : {ingested}")
    print(f"  Skipped         : {skipped}")
    print(f"  Sentences added : {sentences}")
    print("─" * 60)
    if ingested:
        print()
        print("  Next step → rebuild FAISS index:")
        print("    python retrieval/build_index.py --rebuild")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand SocialProof corpus via Firecrawl",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url",    help="Seed URL to crawl (discovers sub-pages)")
    group.add_argument("--scrape", help="Single article URL to scrape (no crawling)")
    group.add_argument("--seeds",  help="Path to a .txt file with one URL per line (crawls each)")

    parser.add_argument("--limit",    type=int, default=20,
                        help="Max pages to crawl per seed URL (default: 20)")
    parser.add_argument("--pipeline", default=DEFAULT_PIPELINE,
                        choices=["news", "factcheck", "stats"],
                        help="Sentence pipeline type (default: news)")
    parser.add_argument("--sentence-cap", type=int, default=DEFAULT_SENTENCE_CAP,
                        help="Max sentences to store per article (default: 60)")
    parser.add_argument("--include-paths", nargs="*",
                        help="URL path patterns to include during crawl (e.g. /news/ /articles/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be inserted without writing to DB")
    parser.add_argument("--output-json",
                        help="Write ingestion summary to this JSON file")

    args = parser.parse_args()

    if FIRECRAWL_API_KEY == "YOUR_API_KEY_HERE":
        print("ERROR: Set FIRECRAWL_API_KEY in your .env or environment.")
        sys.exit(1)

    pages: list[dict] = []

    # ── Collect pages ─────────────────────────────────────────────────────────
    if args.scrape:
        print(f"Scraping: {args.scrape}")
        page = scrape_url(args.scrape)
        if page:
            pages.append(page)

    elif args.url:
        print(f"Crawling: {args.url}  (limit={args.limit})")
        pages = crawl_url(
            args.url,
            limit=args.limit,
            include_paths=args.include_paths,
        )
        print(f"  Retrieved {len(pages)} pages from crawl.")

    elif args.seeds:
        seed_file = Path(args.seeds)
        if not seed_file.exists():
            print(f"ERROR: seeds file not found: {seed_file}")
            sys.exit(1)
        urls = [u.strip() for u in seed_file.read_text().splitlines() if u.strip()]
        print(f"Crawling {len(urls)} seed URLs (limit={args.limit} each)…")
        for u in urls:
            print(f"\n→ {u}")
            found = crawl_url(u, limit=args.limit, include_paths=args.include_paths)
            print(f"  Retrieved {len(found)} pages.")
            pages.extend(found)

    # ── Ingest ────────────────────────────────────────────────────────────────
    if not pages:
        print("No pages retrieved. Nothing to ingest.")
        sys.exit(0)

    print(f"\nIngesting {len(pages)} pages into corpus (pipeline={args.pipeline}) …\n")
    results = []
    for i, page in enumerate(pages, 1):
        meta  = page.get("metadata", {})
        url   = meta.get("sourceURL") or meta.get("url", f"page-{i}")
        print(f"  [{i}/{len(pages)}] {url[:80]}")
        result = _ingest_page(
            page,
            pipeline     = args.pipeline,
            sentence_cap = args.sentence_cap,
            dry_run      = args.dry_run,
        )
        if result["skipped"]:
            reason = result.get("reason", "unknown")
            print(f"            ↳ skipped ({reason})")
        else:
            print(f"            ↳ +{result['sentences_added']} sentences")
        results.append(result)

    _print_summary(results)

    if args.output_json:
        out = Path(args.output_json)
        out.write_text(json.dumps(results, indent=2))
        print(f"  Summary written to {out}")


if __name__ == "__main__":
    main()
