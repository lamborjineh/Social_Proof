"""
retrieval/live_search.py
SETUP:
  pip install playwright scraperapi-sdk
  playwright install chromium
  Set SCRAPER_API_KEY in .env (optional)
"""

import re
import os
import time
import random
import hashlib
import requests
import concurrent.futures
from bs4 import BeautifulSoup
from typing import List, Dict, Set, Optional
from urllib.parse import quote_plus
from pathlib import Path
from datetime import datetime, timedelta
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SCRAPER_API_KEY: str          = os.getenv("SCRAPER_API_KEY", "")
GOOGLE_FACTCHECK_API_KEY: str = os.getenv("GOOGLE_FACTCHECK_API_KEY", "")

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_IMPORTED = True
except ImportError:
    _PLAYWRIGHT_IMPORTED = False

# Circuit breaker: set True once any call raises NotImplementedError or
# the startup probe fails. All subsequent calls skip immediately.
_playwright_broken = False


def _probe_playwright() -> bool:
    """
    Launch and immediately close a headless Chromium instance at startup.
    Returns False if the environment can't spawn subprocesses (e.g. Windows/
    Python 3.11 ThreadPoolExecutor threads where ProactorEventLoop blocks
    asyncio.create_subprocess_exec). Failing fast here avoids burning the
    entire live-search timeout on repeated NotImplementedError raises.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


if _PLAYWRIGHT_IMPORTED:
    PLAYWRIGHT_AVAILABLE = _probe_playwright()
    if not PLAYWRIGHT_AVAILABLE:
        print("[LiveSearch] Playwright: startup probe failed — "
              "browser scraping disabled for this session. "
              "RSS + requests fallbacks will be used instead.")
else:
    PLAYWRIGHT_AVAILABLE = False

# ── Source registry imports ───────────────────────────────────────────────────
from corpus.source_registry import (
    ALL_DOMAINS, TIER1_DOMAINS, TIER2_DOMAINS, STATS_DOMAINS,
    FACTCHECK_DOMAINS, NEWS_DOMAINS, REPUTATION,
    get_reputation, REPUTATION_THRESHOLD, get_publisher_name,
)

# ── Shared retrieval utilities ────────────────────────────────────────────────
# These were previously duplicated here. Single source of truth is utils.py.
from retrieval.utils import (
    recency_boost, trust_normalised, hybrid_score,
    is_numeric_query, split_sentences,
)

# ── Domain sets ───────────────────────────────────────────────────────────────
TRUSTED_DOMAINS: Set[str]       = TIER1_DOMAINS | TIER2_DOMAINS | NEWS_DOMAINS
ALL_CREDIBLE_DOMAINS: Set[str]  = ALL_DOMAINS
FACT_CHECK_DOMAINS: Set[str]    = FACTCHECK_DOMAINS | {
    "snopes.com", "factcheck.org", "politifact.com", "fullfact.org",
    "africacheck.org", "reuters.com", "apnews.com",
}

_EXTRA_OPEN_WEB: Set[str] = {
    "bloomberg.com", "ft.com", "wsj.com",
    "sciencedirect.com", "pubmed.ncbi.nlm.nih.gov",
    "factcheck.org", "politifact.com", "fullfact.org", "africacheck.org",
    "nytimes.com", "washingtonpost.com", "time.com",
    "theatlantic.com", "vox.com", "wired.com",
    "en.wikipedia.org",
    "cdc.gov", "nih.gov", "nature.com",
}
ALL_CREDIBLE_DOMAINS = ALL_CREDIBLE_DOMAINS | _EXTRA_OPEN_WEB

# ── Config ────────────────────────────────────────────────────────────────────
LIVE_FETCH_LIMIT    = 20
MIN_TRUSTED_RESULTS = 3
LIVE_TOP_K          = 7
LIVE_THRESHOLD      = 0.35
MIN_SENT_LEN        = 30
MAX_SENT_LEN        = 600
MAX_WORKERS         = 5
try:
    from config import TIMEOUT_LIVE_SEARCH as ARTICLE_TIMEOUT
except ImportError:
    ARTICLE_TIMEOUT = 12  # fallback if config unavailable
PLAYWRIGHT_TIMEOUT  = 15000  # ms

CACHE_TTL_MIN = 30
CACHE_TTL_MAX = 60

# ── Niche query detection ─────────────────────────────────────────────────────
# Signals that the query is about entertainment, culture, gaming, or other
# topics not covered by the curated PH/news/stats corpus.
# For these queries we skip domain-whitelist and reputation filters at the
# RETRIEVAL stage — the reranker is responsible for quality selection.
_NICHE_QUERY_RE = re.compile(
    r"\b("
    r"anime|manga|manhwa|webtoon|light novel|visual novel|"
    r"jujutsu|naruto|bleach|pokemon|one piece|demon slayer|attack on titan|"
    r"genshin|valorant|minecraft|roblox|fortnite|league of legends|dota|"
    r"esport|streamer|youtuber|tiktok|twitch|"
    r"movie|film|series|episode|season|trailer|sequel|prequel|"
    r"actor|actress|celebrity|singer|band|kpop|jpop|bts|blackpink|"
    r"album|song|lyrics|concert|tour|spotify|"
    r"recipe|cuisine|restaurant|chef|food blog|"
    r"fashion|cosplay|outfit|streetwear|sneaker|"
    r"travel|tourism|itinerary|airbnb|"
    r"nba|pba|mlb|nhl|nfl|fifa|ufc|boxing|wrestling"
    r")\b",
    re.I,
)

# Module-level event keyword regex — used for smart lowercase normalization
# in _extract_query_terms() so action words are never capitalized into fake names.
# Intentionally broad (stem-based): covers base forms + common inflections
# without hardcoding exhaustive verb lists.
_EVENT_KW_RE_GLOBAL = re.compile(
    r"\b(shoot\w*|kill\w*|wound\w*|injur\w*|dead|death|die\w*|died|"
    r"casualt\w*|disappear\w*|missing|murder\w*|assassin\w*|"
    r"arrest\w*|detain\w*|warrant\w*|jail\w*|imprison\w*|charg\w*|indict\w*|releas\w*|"
    r"convict\w*|acquit\w*|sentenc\w*|verdict\w*|"
    r"impeach\w*|resign\w*|appoint\w*|elect\w*|vote\w*|"
    r"raid\w*|ambush\w*|attack\w*|clash\w*|riot\w*|bomb\w*|"
    r"protest\w*|rally|rallied|rallies|crackdown|dispers\w*|"
    r"surrender\w*|escap\w*|fle[ed]|flee\w*|"
    r"testif\w*|hear\w*|appear\w*|"
    r"order\w*|issu\w*|command\w*|direct\w*|declar\w*|announc\w*|"
    r"found|ruled|said|denied|confirm\w*|reveal\w*|"
    r"disinfect\w*|spread\w*|transmit\w*|infect\w*|contaminat\w*|"
    r"reclassif\w*|classif\w*|redefin\w*|reclassif\w*|"
    r"ban\w*|remov\w*|block\w*|censor\w*|"
    r"standoff|lockdown|manhunt|siege|hostage|"
    r"fired|sacked|suspend\w*|dismiss\w*)\b",
    re.I,
)


def _is_niche_query(claim: str) -> bool:
    """
    Return True when the query is about entertainment, pop culture, gaming,
    or other niche topics absent from the curated news/stats/factcheck corpus.

    Niche queries need wide retrieval coverage (no domain whitelist, no
    reputation floor) so the reranker pool has material to work with.
    Numeric/stats queries are excluded — those stay in the curated path.
    """
    if is_numeric_query(claim):
        return False
    return bool(_NICHE_QUERY_RE.search(claim))

# BUG 8 FIX: Rotate User-Agents so gov sites (PSA, BSP, WHO) don't 403 on a
# single static string. The original single UA was flagged as a bot by .gov.ph.
_USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/122.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/121.0.0.0 Safari/537.36"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
     "Gecko/20100101 Firefox/123.0"),
    ("Mozilla/5.0 (X11; Linux x86_64) "
     "AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/120.0.0.0 Safari/537.36"),
]

HEADERS = {
    "User-Agent": _USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-PH,en;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

RSS_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


# ── In-process query cache ────────────────────────────────────────────────────
_query_cache: dict = {}


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def _cache_get(query: str) -> Optional[List[Dict]]:
    key = _cache_key(query)
    entry = _query_cache.get(key)
    if entry:
        expiry, results = entry
        if datetime.now() < expiry:
            return results
        del _query_cache[key]
    return None


def _cache_set(query: str, results: List[Dict]) -> None:
    ttl_minutes = random.randint(CACHE_TTL_MIN, CACHE_TTL_MAX)
    expiry = datetime.now() + timedelta(minutes=ttl_minutes)
    _query_cache[_cache_key(query)] = (expiry, results)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\x20-\x7E]', ' ', text)
    return text.strip()


def _get_domain(url: str) -> str:
    m = re.search(r'https?://(?:www\.)?([^/]+)', url)
    return m.group(1) if m else url[:50]


def _parse_html_to_sentences(html: str, domain: str, url: str,
                              source_type: str = "live") -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "footer",
                               "header", "aside", "form", "iframe"]):
        tag.decompose()

    # BUG 8 FIX: Expanded container selector chain.
    # Standard news: article > div.article-body > main
    # Gov/PSA/BSP: div.field-item, div.press-release, section.content,
    #              div.entry, div.post, div.node-content, table (stat pages)
    container = (
        soup.find("article") or
        soup.find("div", class_=re.compile(
            r"article.?body|article.?content|entry.?content|"
            r"post.?content|story.?body|news.?content|"
            r"article.?text|post.?body|content.?body", re.I)) or
        soup.find("main") or
        soup.find("div", attrs={"id": re.compile(r"article|content|story|main", re.I)}) or
        # Gov site patterns (PSA, BSP, NEDA, DOH use these structures)
        soup.find("div", class_=re.compile(
            r"field.?item|press.?release|node.?content|"
            r"view.?content|region.?content|page.?content|"
            r"entry|post|release|statistic", re.I)) or
        soup.find("section", class_=re.compile(r"content|main|body|article", re.I)) or
        soup.find("div", class_=re.compile(r"panel.?body|tab.?content|accordion", re.I))
    )

    paragraphs = container.find_all("p") if container else soup.find_all("p")

    # BUG 8 FIX: If no container found OR container yields very little text,
    # fall back to ALL <p> tags on the page filtered by minimum length.
    # This catches gov sites that use tables + loose <p> tags without containers.
    if not container or len(" ".join(p.get_text() for p in paragraphs)) < 100:
        paragraphs = soup.find_all("p")

    content = " ".join(
        _clean(p.get_text()) for p in paragraphs
        if len(_clean(p.get_text())) > 40
    )

    if len(content) < 100:
        return []

    pipeline = ("stats"     if domain in STATS_DOMAINS     else
                "factcheck" if domain in FACT_CHECK_DOMAINS else
                "news")

    # Use utils.split_sentences with live_search thresholds
    sents = split_sentences(content, min_len=MIN_SENT_LEN, max_len=MAX_SENT_LEN)
    return [
        {"text": s, "domain": domain, "url": url,
         "source_type": source_type, "pipeline_type": pipeline}
        for s in sents
    ]


# ── Domain trust checks ───────────────────────────────────────────────────────

def _is_trusted_item(item: Dict) -> bool:
    src = item.get("source_domain", "")
    dom = _get_domain(item["url"])
    return (src in TRUSTED_DOMAINS) or (dom in TRUSTED_DOMAINS)


def _is_credible_item(item: Dict) -> bool:
    src = item.get("source_domain", "")
    dom = _get_domain(item["url"])
    return (src in ALL_CREDIBLE_DOMAINS) or (dom in ALL_CREDIBLE_DOMAINS)


def _is_stats_item(item: Dict) -> bool:
    src = item.get("source_domain", "")
    dom = _get_domain(item["url"])
    return (src in STATS_DOMAINS) or (dom in STATS_DOMAINS)


def _query_google_factcheck_api(
    claim_text: str,
    lang_code: str = "en",
    page_size: int = 5,
) -> List[Dict]:
    """
    Query the Google Fact Check Tools API.

    Returns up to `page_size` IFCN-verified fact-check results for the claim.
    This single call covers ALL connected IFCN partners simultaneously:
    Vera Files (PH), AFP Fact Check PH, Rappler, Reuters, AP, Snopes, etc.

    lang_code options:
      "en"  — international English results
      "fil" — Filipino-language results (covers Vera Files / Rappler PH)
      ""    — no language filter (broadest, mixes both)

    Returns [] silently on any failure so the pipeline degrades gracefully.
    Requires GOOGLE_FACTCHECK_API_KEY in .env (free key, 10k req/day).
    """
    if not GOOGLE_FACTCHECK_API_KEY:
        return []

    try:
        params: Dict = {
            "query":        claim_text,
            "key":          GOOGLE_FACTCHECK_API_KEY,
            "pageSize":     page_size,
        }
        if lang_code:
            params["languageCode"] = lang_code

        resp = requests.get(
            "https://factchecktools.googleapis.com/v1alpha1/claims:search",
            params=params,
            timeout=5,
        )
        if resp.status_code != 200:
            return []

        data    = resp.json()
        results = []
        for item in data.get("claims", []):
            review      = (item.get("claimReview") or [{}])[0]
            publisher   = review.get("publisher", {})
            source_url  = review.get("url", "")
            source_name = publisher.get("name", "Google Fact Check")
            domain      = _get_domain(source_url) if source_url else "factchecktools.googleapis.com"
            results.append({
                "text":          item.get("text", ""),
                "url":           source_url,
                "source_domain": domain,
                "source_label":  source_name,
                "rating":        review.get("textualRating", ""),
                "source_type":   "gfct_api",
                # Pre-set reputation to 0.90 — all GFCT entries are IFCN-certified
                "_reputation":   0.90,
            })
        return results

    except Exception as e:
        logger.warning(f"[LiveSearch] Google Fact Check API error: {e}")
        return []


def _is_fact_check_item(item: Dict) -> bool:
    src = item.get("source_domain", "")
    dom = _get_domain(item["url"])
    return (src in FACT_CHECK_DOMAINS) or (dom in FACT_CHECK_DOMAINS)


def _passes_reputation(item: Dict) -> bool:
    src = item.get("source_domain", "") or _get_domain(item["url"])
    return get_reputation(src) >= REPUTATION_THRESHOLD


# ── Query extraction ──────────────────────────────────────────────────────────

def _extract_query_terms(claim: str, max_terms: int = 10) -> str:
    # Normalize: if claim is all-lowercase, recover proper nouns smartly.
    # Run _EVENT_KW_RE on the raw claim first, then capitalize only tokens
    # that are NOT event keywords and NOT grammar words — so "arrested" stays
    # lowercase (becoming an event keyword) while "bato dela rosa" gets
    # capitalized (becoming a name). No hardcoded verb lists needed.
    if claim == claim.lower():
        _event_tokens = {m.lower() for m in _EVENT_KW_RE_GLOBAL.findall(claim)}
        _grammar = {
            "the", "a", "an", "is", "are", "was", "were", "in", "of", "to",
            "for", "that", "this", "and", "or", "but", "on", "at", "by",
            "from", "with", "has", "have", "had", "be", "been", "being",
            "will", "would", "could", "should", "may", "might", "do", "does",
            "did", "its", "it", "who", "what", "when", "where", "how",
            "said", "also", "than", "then", "not", "no", "as", "up", "so",
            "if", "can", "all", "more", "some", "very", "just", "only",
        }
        claim = " ".join(
            w if (w in _event_tokens or w in _grammar) else w.capitalize()
            for w in claim.split()
        )
    claim = re.sub(r'[""«»\']', '', claim)

    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "in", "of", "to",
        "for", "that", "this", "and", "or", "but", "on", "at", "by", "from",
        "with", "has", "have", "had", "be", "been", "being", "will", "would",
        "could", "should", "may", "might", "do", "does", "did", "its", "it",
        "which", "who", "what", "when", "where", "how", "said", "says",
        "also", "than", "then", "their", "they", "them", "these", "those",
        "not", "no", "as", "up", "about", "into", "through", "during",
        "only", "just", "very", "so", "if", "can", "all", "more", "some",
        "philippines", "philippine", "senator", "president", "government",
    }

    # Name-part connectors (PH, Spanish, Arabic, Dutch, etc.)
    _CONNECTORS = {"de", "la", "del", "dela", "delos", "los", "las",
                   "bin", "ibn", "van", "von", "el", "al", "ng"}
    _LEADING_ARTICLES = {"The", "A", "An", "Its", "This", "That"}

    def _is_title(tok): return bool(re.match(r'^[A-Z][a-z]{1,}$', tok))
    def _is_upper(tok): return bool(re.match(r'^[A-Z]{2,6}$', tok))

    # Tokenise and strip punctuation
    raw_tokens = [re.sub(r'[^\w]', '', t) for t in re.findall(r'\b\S+\b', claim)]
    raw_tokens = [t for t in raw_tokens if t]

    # Walk tokens and stitch consecutive Title Case words, joining across
    # lowercase connectors (e.g. "Bato dela Rosa", "Juan de la Cruz").
    # ALLCAPS abbreviations (ICC, BSP) start their own phrase and are NOT
    # merged into a preceding name — they're separate entities.
    phrases = []
    i = 0
    while i < len(raw_tokens):
        tok = raw_tokens[i]
        if _is_title(tok) or _is_upper(tok):
            phrase_tokens = [tok]
            j = i + 1
            while j < len(raw_tokens):
                next_tok = raw_tokens[j]
                if next_tok.lower() in _CONNECTORS and j + 1 < len(raw_tokens):
                    after = raw_tokens[j + 1]
                    if _is_title(after):   # only Title Case after connector, not ALLCAPS
                        phrase_tokens.append(next_tok)
                        phrase_tokens.append(after)
                        j += 2
                        continue
                if _is_title(next_tok):    # consecutive Title Case words
                    phrase_tokens.append(next_tok)
                    j += 1
                    continue
                break
            phrases.append(" ".join(phrase_tokens))
            i = j
        else:
            i += 1

    entity_quoted = []
    seen_phrases  = set()
    for phrase in phrases:
        for art in _LEADING_ARTICLES:
            if phrase.startswith(art + " "):
                phrase = phrase[len(art) + 1:]
                break
        words = phrase.split()
        lower = phrase.lower()
        if lower in seen_phrases:
            continue
        if len(words) == 1 and phrase.lower() in stopwords:
            continue
        if len(words) >= 2:
            entity_quoted.append(f'"{phrase}"')
        elif _is_upper(phrase):
            entity_quoted.append(phrase)
        elif phrase.lower() not in stopwords and len(phrase) >= 3:
            entity_quoted.append(phrase)
        seen_phrases.add(lower)

    _EVENT_KW_RE = re.compile(
        r'\b(gunfire|shooting|shot|standoff|lockdown|'
        r'arrest|arrested|arrests|warrant|warrants|'
        r'detained|detention|charged|charges|indicted|jailed|imprisoned|released|'
        r'raid|siege|manhunt|hearing|testimony|'
        r'surrender|surrendered|escape|escaped|fled|flee|'
        r'impeach|impeached|resign|resigned|convict|convicted|acquit|acquitted|'
        r'drug.?war|crackdown|killed|wounded|'
        r'hostage|barricade|confrontation|protest|clash|riot|bombing|attack|attacked)\b',
        re.I,
    )
    event_kws = list(dict.fromkeys(m.lower() for m in _EVENT_KW_RE.findall(claim)))

    combined_parts = entity_quoted[:6] + event_kws[:4]
    query = " ".join(combined_parts[:max_terms])

    if not query.strip():
        tokens = re.findall(r'[\d]+(?:[.,]\d+)?%?|[A-Za-z]+', claim)
        priority, secondary = [], []
        for tok in tokens:
            lower = tok.lower()
            if lower in stopwords or len(tok) < 3:
                continue
            if re.match(r'^\d', tok) or tok[0].isupper():
                priority.append(tok)
            else:
                secondary.append(tok)
        combined = (priority + secondary)[:max_terms]
        query = " ".join(combined) if combined else claim[:80]

    return query


# ── Google News URL resolver ──────────────────────────────────────────────────

def _resolve_gnews_url(url: str, timeout: int = 8) -> str:
    """
    Resolve a Google News RSS redirect URL to the real article URL.

    Google News RSS returns URLs like:
      https://news.google.com/rss/articles/CBMi...
    These are redirect pages. _fetch_with_requests() already follows redirects
    via allow_redirects=True, but _fetch_with_playwright() calls page.goto()
    directly on the redirect URL, which causes:
      "Unable to retrieve content because the page is navigating"

    Fix: follow the redirect chain with a plain HEAD request before handing
    the URL to Playwright. _fetch_with_requests() is unchanged (already safe).
    Falls back to the original URL on any error.
    """
    if "news.google.com" not in url:
        return url

    try:
        resp = requests.head(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
            timeout=timeout,
        )
        final = resp.url
        if "news.google.com" in final or "google.com/sorry" in final:
            resp2 = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
                timeout=timeout,
            )
            final = resp2.url

        if final and "news.google.com" not in final:
            return final

    except Exception:
        pass

    return url


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _fetch_with_requests(url: str, domain: str) -> Optional[List[Dict]]:
    try:
        import urllib3
        # BUG 8 FIX: Rotate User-Agent per request so gov sites don't fingerprint us.
        # Also use verify=False for .gov.ph sites that have SSL cert issues — suppress
        # the urllib3 InsecureRequestWarning to keep logs clean.
        ua      = random.choice(_USER_AGENTS)
        headers = {**HEADERS, "User-Agent": ua}
        is_gov  = domain.endswith(".gov.ph") or ".gov.ph" in url
        if is_gov:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        time.sleep(random.uniform(0.5, 1.5))
        resp = requests.get(url, headers=headers, timeout=ARTICLE_TIMEOUT,
                            allow_redirects=True, verify=(not is_gov))
        if resp.status_code in (403, 429):
            return None
        resp.raise_for_status()
        final_url = resp.url
        if "consent.google.com" in final_url:
            return None
        try:
            html = resp.content.decode("utf-8", errors="replace")
        except Exception:
            html = resp.content.decode("latin-1", errors="replace")
        sentences = _parse_html_to_sentences(html, domain, final_url, "live")
        return sentences if sentences else None
    except Exception:
        return None


def _fetch_with_playwright(url: str, domain: str) -> Optional[List[Dict]]:
    global _playwright_broken
    if not PLAYWRIGHT_AVAILABLE or _playwright_broken:
        return None
    try:
        # Resolve Google News redirect URL before handing to Playwright.
        # Without this, page.goto() fires on a redirect page and crashes
        # with "Unable to retrieve content because the page is navigating".
        url = _resolve_gnews_url(url)

        print(f"  [LiveSearch] Playwright: trying → {domain}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=HEADERS["User-Agent"], locale="en-PH")
            page    = context.new_page()
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,css}",
                       lambda route: route.abort())
            page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            final_url = page.url
            html      = page.content()
            browser.close()
        if "consent.google.com" in final_url:
            return None
        sentences = _parse_html_to_sentences(html, domain, final_url, "live_playwright")
        return sentences if sentences else None
    except NotImplementedError:
        # asyncio.create_subprocess_exec is blocked in this environment
        # (e.g. Windows Python 3.11 ProactorEventLoop inside a ThreadPoolExecutor).
        # Permanently disable Playwright so every subsequent call skips instantly
        # rather than burning the live-search timeout on guaranteed failures.
        _playwright_broken = True
        print("  [LiveSearch] Playwright: permanently disabled "
              "(subprocess spawn not supported in this environment)")
        return None
    except Exception as e:
        print(f"  [LiveSearch] Playwright: failed → {domain} ({type(e).__name__})")
        return None


def _fetch_with_scraperapi(url: str, domain: str) -> Optional[List[Dict]]:
    if not SCRAPER_API_KEY:
        return None
    try:
        print(f"  [LiveSearch] ScraperAPI: trying → {domain}")
        api_url = (f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}"
                   f"&url={quote_plus(url)}&country_code=ph")
        resp = requests.get(api_url, timeout=30)
        if resp.status_code != 200:
            return None
        html = resp.content.decode("utf-8", errors="replace")
        sentences = _parse_html_to_sentences(html, domain, url, "live_scraperapi")
        return sentences if sentences else None
    except Exception:
        return None


def _rss_description_fallback(item: Dict, domain: str) -> List[Dict]:
    description = item.get("description", "")
    if not description or len(description) < MIN_SENT_LEN:
        return []
    sents = split_sentences(description, min_len=MIN_SENT_LEN, max_len=MAX_SENT_LEN)
    if not sents and len(description) >= MIN_SENT_LEN:
        sents = [description[:MAX_SENT_LEN]]
    pipeline = ("stats"     if domain in STATS_DOMAINS     else
                "factcheck" if domain in FACT_CHECK_DOMAINS else
                "news")
    return [
        {"text": s, "domain": domain, "url": item["url"],
         "source_type": "live_rss_desc", "pipeline_type": pipeline}
        for s in sents
    ]


def _fetch_article_sentences(item: Dict) -> List[Dict]:
    url    = item["url"]
    domain = item.get("source_domain", "") or _get_domain(url)
    # Metadata to attach to every sentence from this article
    article_meta = {
        "article_title":  item.get("title", ""),
        "date_published": item.get("date_published", ""),
        "source_label":   item.get("source_label", ""),
    }

    result = _fetch_with_requests(url, domain)
    if result is not None:
        for s in result:
            s.update(article_meta)
        return result
    result = _fetch_with_playwright(url, domain)
    if result is not None:
        for s in result:
            s.update(article_meta)
        return result
    result = _fetch_with_scraperapi(url, domain)
    if result is not None:
        for s in result:
            s.update(article_meta)
        return result
    fallback = _rss_description_fallback(item, domain)
    for s in fallback:
        s.update(article_meta)
    return fallback


def _fetch_articles_concurrent(articles: List[Dict]) -> List[Dict]:
    """
    Concurrent article fetch with partial-results logic.
    If a source times out or errors, it is skipped and logged — whatever
    other sources responded is still returned. Live search never fails entirely.
    """
    all_sentences = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_article_sentences, art): art for art in articles}
        for future in concurrent.futures.as_completed(futures):
            art = futures[future]
            try:
                sentences = future.result(timeout=ARTICLE_TIMEOUT + 5)
                all_sentences.extend(sentences)
            except concurrent.futures.TimeoutError:
                print(f"[LiveSearch] Timeout: {art.get('url','?')[:70]} — skipping (partial results)")
            except Exception as e:
                print(f"[LiveSearch] Error: {art.get('url','?')[:70]}: {type(e).__name__} — skipping")
    return all_sentences


# ── Google News RSS ───────────────────────────────────────────────────────────

def _google_news_rss(query: str, limit: int, lang: str = "en-PH",
                     gl: str = "PH") -> List[Dict]:
    encoded = quote_plus(query)
    rss_url = (f"https://news.google.com/rss/search"
               f"?q={encoded}&hl={lang}&gl={gl}&ceid={gl}:{lang[:2]}")
    try:
        resp = requests.get(rss_url, headers=RSS_HEADERS, timeout=10)
        resp.raise_for_status()
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except Exception:
            soup = BeautifulSoup(resp.content, "html.parser")

        results = []
        for item in soup.find_all("item")[:limit]:
            url = ""
            link_tag = item.find("link")
            if link_tag:
                url = link_tag.get_text(strip=True)
                if not url:
                    sib = link_tag.next_sibling
                    if sib and isinstance(sib, str):
                        url = sib.strip()
                if not url:
                    url = link_tag.get("href", "")
            if not url or not url.startswith("http"):
                guid = item.find("guid")
                if guid:
                    url = guid.get_text(strip=True)
            if not url or not url.startswith("http"):
                continue

            title_tag = item.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""

            source_domain = ""
            source_tag = item.find("source")
            if source_tag:
                source_url = source_tag.get("url", "")
                if source_url:
                    source_domain = _get_domain(source_url)

            description = ""
            desc_tag = item.find("description")
            if desc_tag:
                raw_desc = desc_tag.get_text(strip=True)
                raw_desc = re.sub(r'<[^>]+>', ' ', raw_desc)
                raw_desc = re.sub(r'\s*-\s*\w[\w\s]{0,30}$', '', raw_desc).strip()
                if len(raw_desc) >= MIN_SENT_LEN:
                    description = raw_desc

            results.append({
                "url":           url,
                "title":         title,
                "source_domain": source_domain,
                "description":   description,
            })

        print(f"  [LiveSearch] RSS({gl}): {len(results)} items for '{query[:40]}'")
        return results

    except Exception as e:
        print(f"  [LiveSearch] RSS query failed: {e}")
        return []


# ── Encoding ──────────────────────────────────────────────────────────────────

def _encode_texts(model, texts: list):
    import numpy as np
    try:
        output = model.encode(
            texts,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
            batch_size=64,
        )
        vecs = np.array(output["dense_vecs"], dtype="float32")
    except (TypeError, KeyError):
        vecs = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            batch_size=64,
            show_progress_bar=False,
        ).astype("float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vecs / norms


# ── Ranking ───────────────────────────────────────────────────────────────────

def _rank_and_filter(sentences: List[Dict], claim_emb, model,
                     threshold: float, top_k: int,
                     max_per_domain: int = 3,
                     numeric_boost: bool = False,
                     min_reputation: float = REPUTATION_THRESHOLD) -> List[Dict]:
    """
    Rank and filter candidate sentences by hybrid score.

    min_reputation controls the reputation floor:
      - Default (REPUTATION_THRESHOLD = 0.65): curated news/stats mode —
        only known-credible domains pass through.
      - 0.0: niche/open mode — all domains pass; the reranker downstream
        selects quality from the full candidate pool.

    Semantic threshold, domain diversity cap, and hybrid scoring always apply
    regardless of min_reputation.
    """
    import numpy as np
    if not sentences:
        return []

    texts     = [s["text"] for s in sentences]
    sent_embs = _encode_texts(model, texts)
    semantic_sims = np.dot(sent_embs, claim_emb)

    scored = []
    for idx, sem_sim in enumerate(semantic_sims):
        sent   = sentences[idx]
        domain = sent["domain"]

        if get_reputation(domain) < min_reputation:
            continue

        # hybrid_score from utils — includes numeric boost
        final = hybrid_score(float(sem_sim), domain, sent["url"],
                             numeric_boost=numeric_boost)
        scored.append((idx, final))

    scored.sort(key=lambda x: x[1], reverse=True)

    results, seen, domain_counts = [], set(), {}
    for idx, score in scored:
        if score < threshold:
            break
        sent   = sentences[idx]
        domain = sent["domain"]
        short  = sent["text"][:80]
        if short in seen:
            continue
        seen.add(short)
        if domain_counts.get(domain, 0) >= max_per_domain:
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        results.append({**sent, "similarity": round(score, 4)})
        if len(results) >= top_k:
            break
    return results


# ── Main public function ──────────────────────────────────────────────────────

def live_search(claim: str, model, k: int = LIVE_TOP_K) -> List[Dict]:
    """
    Live evidence retrieval — three pipelines merged with hybrid ranking.
    Results cached 30–60 min per query to reduce repeated scraping.

    The model parameter is the embedding model from Retriever — passed in
    by api/main.py as retriever.model, so no second model instance is loaded.

    Pipeline A — News:      Phase 1 (trusted PH) + Phase 2 (open web)
                            Phase 2b (wide open, niche queries only)
    Pipeline B — Stats:     extra search targeting Tier 1/2 stats sources
    Pipeline C — Fact-check: targeted fact-check query patterns

    Adaptive coverage mode (v3.7):
      News/stats/factcheck queries → strict domain whitelist + reputation floor.
      Niche queries (anime, gaming, culture …) → Phase 2 skips domain whitelist
        and Phase 2b runs unconditionally with min_reputation=0.0. The reranker
        is responsible for final quality selection in the expanded pool.
      Design principle: widen the RETRIEVAL candidate pool, keep RANKING tight.
    """
    import numpy as np

    # Normalize claim casing: if the whole claim is lowercase, capitalize only
    # tokens that are NOT event keywords (e.g. "arrested", "killed") — those
    # stay lowercase so they become event anchors, not fake proper nouns.
    # Uses _EVENT_KW_RE_GLOBAL (stem-based, no hardcoded verb lists).
    if claim == claim.lower():
        _ev_toks = {m.lower() for m in _EVENT_KW_RE_GLOBAL.findall(claim)}
        _grammar = {
            "the", "a", "an", "is", "are", "was", "were", "in", "of", "to",
            "for", "that", "this", "and", "or", "but", "on", "at", "by",
            "from", "with", "has", "have", "had", "be", "been", "being",
            "will", "would", "could", "should", "may", "might", "do", "does",
            "did", "its", "it", "who", "what", "when", "where", "how",
            "said", "also", "than", "then", "not", "no", "as", "up", "so",
            "if", "can", "all", "more", "some", "very", "just", "only",
        }
        claim = " ".join(
            w if (w in _ev_toks or w in _grammar) else w.capitalize()
            for w in claim.split()
        )

    query       = _extract_query_terms(claim)
    numeric_q   = is_numeric_query(claim)   # from utils
    niche_q     = _is_niche_query(claim)    # NEW: niche/entertainment detection

    # ── Settled-fact / historical early exit ─────────────────────────────────
    # Detects claims that are structurally settled facts (historical events,
    # scientific truths, biographical facts) rather than breaking news.
    # Live search on these returns weakly-related noise, so we exit early
    # and let FAISS handle it.
    #
    # Fully dynamic — no hardcoded names or lists. Uses:
    #   - Structural signals: claim length, stative/past-action verbs
    #   - Science signals: physical/chemical domain words
    #   - News signals: present-tense breaking-news verbs (negates settled)
    #   - Year signals: future year = not settled; recent year = news
    #   - Current-status signal: "X is the president/senator/..." = checkable

    _SF_STATIVE = re.compile(
        r"\b(is|are|was|were|has been|have been|remains?|became?)\b", re.I
    )
    _SF_PAST_ACTION = re.compile(
        r"\b(died|executed|exiled|born|founded|signed|ended|abolished|"
        r"discovered|invented|declared|proclaimed|ratified|enacted|"
        r"reclassif\w*|classif\w*|redefin\w*|declass\w*)\b",
        re.I,
    )
    _SF_NEWS_SIGNAL = re.compile(
        r"\b(arrested|impeached|charged|convicted|sentenced|raided|"
        r"ordered|shooting|killed\s+by|according|says|said|claims|"
        r"announced|reported)\b",
        re.I,
    )
    _SF_SCIENCE = re.compile(
        r"\b(boils?|freezes?|melts?|degrees?|celsius|fahrenheit|"
        r"km|miles?|meters?|gravity|speed\s+of\s+light|atoms?|"
        r"molecules?|cells?|dna|orbit\w*|element\w*|compound\w*|chemical\w*)\b",
        re.I,
    )
    _SF_FUTURE_YEAR  = re.compile(r"\b(202[6-9]|20[3-9]\d|2[1-9]\d{2})\b")
    _SF_RECENT_YEAR  = re.compile(r"\b(202[0-5])\b")
    _SF_CURR_STATUS  = re.compile(
        r"\b(is|are)\s+(the\s+)?(president|senator|mayor|governor|chief|"
        r"secretary|minister|chairman|ceo|head|leader|director)\b",
        re.I,
    )

    _sf_words       = claim.split()
    _sf_short       = len(_sf_words) <= 8
    _sf_stative     = bool(_SF_STATIVE.search(claim))
    _sf_past        = bool(_SF_PAST_ACTION.search(claim))
    _sf_news        = bool(_SF_NEWS_SIGNAL.search(claim))
    _sf_science     = bool(_SF_SCIENCE.search(claim))
    _sf_future_yr   = bool(_SF_FUTURE_YEAR.search(claim))
    _sf_recent_yr   = bool(_SF_RECENT_YEAR.search(claim))
    _sf_curr_status = bool(_SF_CURR_STATUS.search(claim))

    _is_settled = False
    if not _sf_future_yr and not _sf_curr_status:
        if _sf_recent_yr and not _sf_past:
            _is_settled = False       # recent year + no historical action = news
        elif _sf_short and _sf_science:
            _is_settled = True        # science facts
        elif _sf_short and (_sf_stative or _sf_past) and not _sf_news:
            _is_settled = True        # biographical / historical

    if _is_settled:
        logger.info("[LiveSearch] Settled-fact claim detected — skipping live search.")
        return []
    # ─────────────────────────────────────────────────────────────────────────

    cache_key_q = f"{query}|{numeric_q}|{niche_q}"

    cached = _cache_get(cache_key_q)
    if cached is not None:
        print(f"  [LiveSearch] Cache hit for '{query[:40]}'")
        return cached

    print(f"  [LiveSearch] Query: '{query}' (numeric={numeric_q}, niche={niche_q})")
    print(f"  [LiveSearch] Playwright: {'available' if PLAYWRIGHT_AVAILABLE else 'NOT installed'}")
    print(f"  [LiveSearch] ScraperAPI: {'configured' if SCRAPER_API_KEY else 'NOT configured (optional)'}")

    claim_emb = _encode_texts(model, [claim])[0]

    # ── Phase 1 / Pipeline A: Trusted Philippine + general news ──────────────
    # Niche queries also run Phase 1 — trusted PH sources sometimes cover
    # entertainment (e.g. Rappler/Inquirer entertainment sections).
    raw_results      = _google_news_rss(query, limit=LIVE_FETCH_LIMIT)
    trusted_articles = [r for r in raw_results
                        if _is_trusted_item(r) and _passes_reputation(r)]
    print(f"  [LiveSearch] Phase 1 — {len(trusted_articles)} trusted articles")

    trusted_sentences = _fetch_articles_concurrent(trusted_articles) if trusted_articles else []
    trusted_results   = _rank_and_filter(
        trusted_sentences, claim_emb, model,
        threshold=LIVE_THRESHOLD, top_k=k, max_per_domain=3,
        numeric_boost=numeric_q,
    )
    print(f"  [LiveSearch] Phase 1 results: {len(trusted_results)}")

    # ── Phase 2 / Pipeline A cont.: Open web fallback ─────────────────────────
    # Curated-domain path: only runs when trusted results are scarce AND
    # the query is not niche (niche gets its own Phase 2b below).
    open_articles, open_results = [], []
    if len(trusted_results) < MIN_TRUSTED_RESULTS and not niche_q:
        raw_ph     = _google_news_rss(query, limit=LIVE_FETCH_LIMIT, lang="en-PH", gl="PH")
        raw_global = _google_news_rss(query, limit=LIVE_FETCH_LIMIT, lang="en-US", gl="US")
        seen_urls  = {a["url"] for a in trusted_articles}
        for r in raw_ph + raw_global:
            if (r["url"] not in seen_urls
                    and _is_credible_item(r)
                    and _passes_reputation(r)):
                open_articles.append(r)
                seen_urls.add(r["url"])
        print(f"  [LiveSearch] Phase 2 — {len(open_articles)} open web articles")
        if open_articles:
            open_sentences = _fetch_articles_concurrent(open_articles[:10])
            open_results   = _rank_and_filter(
                open_sentences, claim_emb, model,
                threshold=LIVE_THRESHOLD,
                top_k=k - len(trusted_results),
                max_per_domain=2,
                numeric_boost=numeric_q,
            )
            for r in open_results:
                r["source_type"] = r.get("source_type", "live_open")
        print(f"  [LiveSearch] Phase 2 results: {len(open_results)}")

    # ── Phase 2b / Wide-open niche fallback ───────────────────────────────────
    # Only runs for niche queries (anime, gaming, culture, sports, etc.).
    # No domain whitelist, no reputation floor — fetches any article returned
    # by Google News and lets the reranker select quality from the wider pool.
    # Per-domain cap (max_per_domain=2) is kept so no single source floods the
    # reranker pool. The semantic threshold is slightly relaxed (×0.85) so
    # niche content, which tends to have lower overlap with general vocabulary,
    # still clears the bar.
    niche_results = []
    if niche_q:
        raw_ph_niche  = _google_news_rss(query, limit=LIVE_FETCH_LIMIT, lang="en-PH", gl="PH")
        raw_us_niche  = _google_news_rss(query, limit=LIVE_FETCH_LIMIT, lang="en-US", gl="US")
        seen_niche    = {a["url"] for a in trusted_articles}
        niche_articles = []
        for r in raw_ph_niche + raw_us_niche:
            if r["url"] not in seen_niche:
                niche_articles.append(r)
                seen_niche.add(r["url"])
        print(f"  [LiveSearch] Phase 2b (niche wide-open) — {len(niche_articles)} candidate articles")
        if niche_articles:
            niche_sentences = _fetch_articles_concurrent(niche_articles[:15])
            niche_results   = _rank_and_filter(
                niche_sentences, claim_emb, model,
                threshold=LIVE_THRESHOLD * 0.85,   # slightly relaxed for niche vocab
                top_k=k - len(trusted_results),
                max_per_domain=2,                   # still cap per domain for diversity
                numeric_boost=False,
                min_reputation=0.0,                 # no reputation floor — reranker selects
            )
            for r in niche_results:
                r["source_type"] = r.get("source_type", "live_niche")
        print(f"  [LiveSearch] Phase 2b results: {len(niche_results)}")

    # ── Phase B / Pipeline B: Stats sources (numeric query) ──────────────────
    stats_results = []
    if numeric_q:
        stats_query    = f"{query} statistics data"
        raw_stats_ph   = _google_news_rss(stats_query, limit=15, lang="en-PH", gl="PH")
        raw_stats_us   = _google_news_rss(stats_query, limit=10, lang="en-US", gl="US")
        seen_stats     = {a["url"] for a in trusted_articles} | {a["url"] for a in open_articles}
        stats_articles = [
            r for r in raw_stats_ph + raw_stats_us
            if _is_stats_item(r) and r["url"] not in seen_stats and _passes_reputation(r)
        ]
        if stats_articles:
            stats_sentences = _fetch_articles_concurrent(stats_articles[:8])
            stats_results   = _rank_and_filter(
                stats_sentences, claim_emb, model,
                threshold=LIVE_THRESHOLD * 0.9, top_k=4, max_per_domain=2,
                numeric_boost=True,
            )
            for r in stats_results:
                r["source_type"] = r.get("source_type", "live_stats")
        print(f"  [LiveSearch] Phase B (stats) results: {len(stats_results)}")

    # ── Phase 3 / Pipeline C: Fact-check targeted search ─────────────────────
    fact_results  = []
    seen_urls_all = (
        {a["url"] for a in trusted_articles}
        | {a["url"] for a in open_articles}
    )

    # ── Step 3a: Google Fact Check Tools API (primary, fastest) ──────────────
    # Covers all IFCN-certified partners in one call (Vera Files, AFP PH,
    # Rappler, Reuters, AP, Snopes, etc.). Falls back silently if key missing.
    gfct_en  = _query_google_factcheck_api(claim, lang_code="en",  page_size=5)
    gfct_fil = _query_google_factcheck_api(claim, lang_code="fil", page_size=5)
    gfct_all = []
    for r in gfct_en + gfct_fil:
        if r["url"] and r["url"] not in seen_urls_all and r.get("text"):
            gfct_all.append(r)
            seen_urls_all.add(r["url"])

    if gfct_all:
        # Convert GFCT results directly to the evidence format
        # (no article fetch needed — the claim text IS the evidence)
        for r in gfct_all:
            domain = r.get("source_domain", "")
            fact_results.append({
                "text":          r["text"],
                "url":           r["url"],
                "source_domain": domain,
                "source_label":  r["source_label"],
                "article_title": r.get("article_title", r["text"][:120]),
                "date_published": r.get("date_published", ""),
                "publisher":     r["source_label"] or get_publisher_name(domain) or domain,
                "source_type":   "gfct_api",
                "similarity":    LIVE_THRESHOLD + 0.05,  # above threshold by default
                "trust":         r.get("_reputation", 0.90),
                "rating":        r.get("rating", ""),
            })
        print(f"  [LiveSearch] Phase 3a (GFCT API): {len(fact_results)} results "
              f"(en={len(gfct_en)}, fil={len(gfct_fil)})")
    else:
        if GOOGLE_FACTCHECK_API_KEY:
            print("  [LiveSearch] Phase 3a (GFCT API): 0 results")
        else:
            print("  [LiveSearch] Phase 3a (GFCT API): skipped — "
                  "GOOGLE_FACTCHECK_API_KEY not set in .env")

    # ── Step 3b: Google News RSS fallback (runs regardless of GFCT result) ───
    fc_queries = [
        f"{query} fact check",
        f"{query} debunked",
        f"{query} true or false",
        f"{query} misleading",
    ]
    fact_articles = []
    for fc_q in fc_queries:
        raw_fc = _google_news_rss(fc_q, limit=5, lang="en-US", gl="US")
        for r in raw_fc:
            if _is_fact_check_item(r) and r["url"] not in seen_urls_all and _passes_reputation(r):
                fact_articles.append(r)
                seen_urls_all.add(r["url"])

    if fact_articles:
        fact_sentences = _fetch_articles_concurrent(fact_articles[:5])
        rss_fact_results = _rank_and_filter(
            fact_sentences, claim_emb, model,
            threshold=LIVE_THRESHOLD * 0.8, top_k=3, max_per_domain=2,
        )
        for r in rss_fact_results:
            r["source_type"] = r.get("source_type", "fact_check_rss")
        fact_results.extend(rss_fact_results)
    print(f"  [LiveSearch] Phase 3 (fact-check total) results: {len(fact_results)}")

    # ── Merge, dedup, return ──────────────────────────────────────────────────
    # Priority order: fact-check > stats > trusted news > niche/open web.
    # niche_results and open_results are mutually exclusive (only one runs
    # per query based on niche_q flag), so summing them is safe.
    all_results = fact_results + stats_results + trusted_results + open_results + niche_results

    # ── Entity identity scoring (v3.4) ────────────────────────────────────────
    # Apply after all pipelines merge so entity scoring sees the full candidate
    # pool. This separates "same event" from "same topic" — e.g. "Duterte ICC"
    # articles are penalised when the claim is about "Bato dela Rosa ICC"
    # because "Bato" / "dela Rosa" are absent from their text/title.
    #
    # Skipped for:
    #   - numeric/stats claims — entity filtering is less meaningful for data
    #   - precise named-entity queries — RSS was already searched with quoted
    #     entity terms (e.g. '"Ronald Bato Dela Rosa" shooting'), so results
    #     are already entity-targeted. Applying entity scoring on top would
    #     discard on-topic articles that mention the person in a slightly
    #     different context, reducing recall with no benefit.
    #
    # apply_entity_rerank() mutates dicts in-place (adds entity_score,
    # context_label, adjusts similarity) and re-sorts by adjusted similarity.
    _query_is_precise = '"' in query  # quoted terms → already entity-filtered by RSS
    _skip_entity_scoring = numeric_q or _query_is_precise

    if not _skip_entity_scoring:
        try:
            from retrieval.utils import apply_entity_rerank
            all_results = apply_entity_rerank(all_results, claim)
            print(
                f"  [LiveSearch] Entity identity scoring applied. "
                f"Labels: { {r.get('context_label', '?') for r in all_results[:5]} }"
            )
        except Exception as _e:
            print(f"  [LiveSearch] Entity scoring skipped: {_e}")
            all_results.sort(key=lambda x: x["similarity"], reverse=True)
    else:
        reason = "numeric query" if numeric_q else "precise named-entity query (RSS already filtered)"
        print(f"  [LiveSearch] Entity identity scoring skipped ({reason}).")
        all_results.sort(key=lambda x: x["similarity"], reverse=True)
        # Assign neutral labels so the merge logic below keeps everything
        for r in all_results:
            r.setdefault("context_label", "same_event")
            r.setdefault("entity_score", 0.0)

    seen, final = set(), []
    related_fallback = []
    broad_fallback   = []

    for r in all_results:
        short = r["text"][:80]
        if short in seen:
            continue
        seen.add(short)

        label = r.get("context_label", "")
        if label == "broad_match":
            broad_fallback.append(r)
            continue
        if label == "related_topic":
            related_fallback.append(r)
            continue

        final.append(r)
        if len(final) >= k:
            break

    # Pad with related_topic only when same_event results are scarce.
    # Always padding (teammate's version) leaks irrelevant-but-thematically-
    # similar results back in once we have enough on-target hits.
    if len(final) < max(2, k // 2):
        for r in related_fallback:
            if len(final) >= k:
                break
            final.append(r)

    # Last resort: broad_match only when almost nothing else found
    if len(final) < 2:
        for r in broad_fallback:
            if len(final) >= k:
                break
            final.append(r)

    summary = (
        f"{len(final)} total "
        f"({len(trusted_results)} trusted, {len(open_results)} open, "
        f"{len(niche_results)} niche, "
        f"{len(stats_results)} stats, {len(fact_results)} fact-check)"
    )
    print(f"  [LiveSearch] Done: {summary}")

    if final:
        final[0]["_live_search_summary"]  = summary
        final[0]["_live_trusted_count"]   = len(trusted_results)
        final[0]["_live_open_count"]      = len(open_results)
        final[0]["_live_niche_count"]     = len(niche_results)
        final[0]["_live_stats_count"]     = len(stats_results)
        final[0]["_live_fact_count"]      = len(fact_results)

    _cache_set(cache_key_q, final)
    return final
