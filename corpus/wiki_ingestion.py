"""
corpus/wiki_ingestion.py
Fetches Wikipedia article summaries via the REST API and seeds them
into the corpus DB as factcheck-pipeline sentences.

Fixes vs previous version:
  - Uses split_sentences() from scraper.py (spaCy + regex fallback)
    instead of a local regex — sentences now go through the real quality gate
  - Uses insert_article() from db.py instead of a raw INSERT with content=""
    — article rows now have real content (the Wikipedia extract)
  - Uses insert_pipeline_sentences() from db.py instead of manual INSERTs
    — duplicate handling and quality gate are applied automatically
  - Checks article_exists() before hitting the Wikipedia API at all
    — re-running the script won't re-fetch topics already in the corpus

Usage:
    python corpus/wiki_ingestion.py
    python corpus/wiki_ingestion.py --topics "climate change" "vaccine"
    python corpus/wiki_ingestion.py --dry-run
    python corpus/wiki_ingestion.py --limit 100

After running, rebuild the index:
    python retrieval/build_index.py --rebuild
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from corpus.db import (
    insert_article,
    insert_pipeline_sentences,
    article_exists,
)
from corpus.scraper import split_sentences, clean_text

DEFAULT_TOPICS = [
    # ── Philippines — politics & history ─────────────────────────────────────
    "Philippines",
    "History of the Philippines",
    "Ferdinand Marcos",
    "Imelda Marcos",
    "Rodrigo Duterte",
    "Bongbong Marcos",
    "Emilio Aguinaldo",
    "People Power Revolution",
    "Philippine independence",
    "Bangsamoro",
    "Jose Rizal",
    "Andres Bonifacio",
    "Corazon Aquino",
    "Benigno Aquino III",
    "Leni Robredo",
    "Philippine Constitution",
    "COMELEC",
    "Martial law in the Philippines",
    "Philippine–American War",
    "Moro Islamic Liberation Front",
    "Abu Sayyaf",
    "New People's Army Philippines",
    "Duterte drug war",
    "International Criminal Court Philippines",
    "EDSA Revolution",
    "Ferdinand Marcos human rights violations",

    # ── Philippines — geography & economy ────────────────────────────────────
    "Philippine peso",
    "Bangko Sentral ng Pilipinas",
    "Philippine Statistics Authority",
    "Overseas Filipino Worker",
    "Manila",
    "Quezon City",
    "Davao City",
    "Cebu",
    "Luzon",
    "Mindanao",
    "Visayas",
    "Mount Apo",
    "Laguna de Bay",
    "Typhoon Haiyan",
    "Typhoon Odette",
    "Mount Pinatubo",
    "NEDA Philippines",
    "Philippine economy",
    "Inflation Philippines",
    "Philippine poverty",
    "Jeepney",
    "Maguindanao massacre",

    # ── Philippines — health & science ───────────────────────────────────────
    "Department of Health Philippines",
    "PhilHealth",
    "Dengue fever Philippines",
    "Tuberculosis in the Philippines",
    "Leptospirosis",
    "Rabies Philippines",
    "Philippine measles outbreak",
    "Dengvaxia controversy",
    "COVID-19 Philippines",
    "COVID-19 vaccination Philippines",
    "Philippine Red Cross",
    "National Kidney and Transplant Institute",
    "PAGASA",
    "PHIVOLCS",
    "Philippine Institute of Volcanology and Seismology",

    # ── Global health — vaccines & immunization ───────────────────────────────
    "COVID-19 pandemic",
    "COVID-19 vaccine",
    "Vaccination",
    "Vaccine safety",
    "Herd immunity",
    "mRNA vaccine",
    "AstraZeneca COVID-19 vaccine",
    "Pfizer–BioNTech COVID-19 vaccine",
    "Sinovac COVID-19 vaccine",
    "Ivermectin",
    "Hydroxychloroquine",
    "Vaccines and autism",
    "MMR vaccine",
    "Polio vaccine",
    "HPV vaccine",
    "Influenza vaccine",
    "Vaccine hesitancy",
    "Anti-vaccination movement",
    "VAERS",
    "COVID-19 misinformation",

    # ── Global health — diseases ──────────────────────────────────────────────
    "Antibiotic resistance",
    "HIV/AIDS",
    "Malaria",
    "Tuberculosis",
    "Ebola virus disease",
    "Monkeypox",
    "Influenza",
    "Cancer",
    "Breast cancer",
    "Lung cancer",
    "Colorectal cancer",
    "Diabetes",
    "Type 2 diabetes",
    "Hypertension",
    "Cardiovascular disease",
    "Stroke",
    "Alzheimer's disease",
    "Mental health",
    "Depression",
    "Anxiety disorder",
    "Autism spectrum disorder",
    "Obesity",

    # ── Global health — nutrition & lifestyle ─────────────────────────────────
    "Vitamin D",
    "Vitamin C",
    "Zinc",
    "Sugar",
    "Saturated fat",
    "Trans fat",
    "Processed food",
    "Organic food",
    "Genetically modified organism",
    "GMO safety",
    "Fluoride",
    "Fluoridation of water",
    "Aspartame",
    "MSG food additive",
    "Intermittent fasting",
    "Detox diet",
    "Alkaline diet",
    "Raw water",
    "Essential oil",
    "Homeopathy",
    "Alternative medicine",
    "Traditional Chinese medicine",
    "Colloidal silver",
    "Bleach therapy",
    "Cancer cure misinformation",

    # ── Climate & environment ─────────────────────────────────────────────────
    "Climate change",
    "Global warming",
    "IPCC",
    "Paris Agreement",
    "Carbon dioxide in Earth's atmosphere",
    "Greenhouse effect",
    "Ozone layer",
    "Ozone depletion",
    "Sea level rise",
    "Arctic ice",
    "Coral reef",
    "Amazon rainforest",
    "Deforestation",
    "Renewable energy",
    "Solar power",
    "Wind power",
    "Electric vehicle",
    "Nuclear power",
    "Fossil fuel",
    "Carbon footprint",
    "Great Barrier Reef",
    "Ocean acidification",
    "Extreme weather event",
    "Climate change denial",

    # ── Science consensus topics ──────────────────────────────────────────────
    "Evolution",
    "Natural selection",
    "Age of the universe",
    "Big Bang",
    "DNA",
    "Human genome",
    "Photosynthesis",
    "Germ theory of disease",
    "Speed of light",
    "Black hole",
    "Tectonic plates",
    "Antibiotic",
    "Penicillin",
    "Stem cell",
    "CRISPR",
    "Artificial intelligence",
    "5G technology",
    "Electromagnetic radiation",
    "Microwave oven",
    "Wi-Fi health effects",

    # ── Common misinformation targets ─────────────────────────────────────────
    "Flat Earth",
    "Moon landing conspiracy theory",
    "Chemtrails",
    "Microchip implant conspiracy",
    "QAnon",
    "COVID-19 conspiracy theories",
    "Bill Gates conspiracy theories",
    "Pizzagate",
    "Deep state",
    "Great Reset",
    "Agenda 21",
    "Population control conspiracy",
    "Vaccine microchip conspiracy",
    "5G COVID conspiracy",
    "Soros conspiracy theories",

    # ── Media & information literacy ──────────────────────────────────────────
    "Misinformation",
    "Disinformation",
    "Fake news",
    "Media literacy",
    "Fact-checking",
    "Propaganda",
    "Social media and misinformation",
    "Deepfake",
    "Confirmation bias",
    "Echo chamber (media)",
    "Filter bubble",
    "Clickbait",
    "Satire",
    "Parody news",
    "Source credibility",

    # ── History ───────────────────────────────────────────────────────────────
    "World War II",
    "World War I",
    "Holocaust",
    "United Nations",
    "Moon landing",
    "Cold War",
    "Berlin Wall",
    "French Revolution",
    "Ferdinand Magellan",
    "Spanish-American War",
    "Battle of Mactan",
    "Katipunan",
    "American Civil War",
    "Hiroshima and Nagasaki atomic bombings",
    "Nuremberg trials",

    # ── Global institutions & organizations ───────────────────────────────────
    "World Health Organization",
    "Centers for Disease Control and Prevention",
    "United Nations Children's Fund",
    "International Monetary Fund",
    "World Bank",
    "Amnesty International",
    "Human Rights Watch",
    "International Criminal Court",
    "ASEAN",
    "European Union",
    "NATO",

    # ── Drug policy (PH-relevant) ─────────────────────────────────────────────
    "War on drugs Philippines",
    "Illegal drug trade in the Philippines",
    "Philippine Drug Enforcement Agency",
    "Shabu drug Philippines",
    "Drug-related killings Philippines",
]

WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
DOMAIN   = "en.wikipedia.org"
PIPELINE = "factcheck"


def _topic_url(topic: str) -> str:
    return f"https://{DOMAIN}/wiki/{topic.replace(' ', '_')}"


def fetch_summary(topic: str) -> Optional[dict]:
    """
    Fetch Wikipedia summary for a topic.
    Returns { title, extract, description } or None on failure.
    """
    url = WIKI_API.format(requests.utils.quote(topic.replace(" ", "_")))
    try:
        resp = requests.get(
            url, timeout=10,
            headers={"User-Agent": "SocialProof/1.0 wiki_ingestion.py"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "title":       data.get("title", topic),
                "extract":     data.get("extract", ""),
                "description": data.get("description", ""),
            }
        elif resp.status_code == 404:
            print(f"  [wiki] Not found: '{topic}'")
        else:
            print(f"  [wiki] HTTP {resp.status_code} for '{topic}'")
    except Exception as e:
        print(f"  [wiki] Request failed for '{topic}': {e}")
    return None


def ingest_topic(topic: str, dry_run: bool = False) -> int:
    """
    Fetch Wikipedia summary for one topic and insert into corpus.db.

    Uses:
      - split_sentences() from scraper.py  → spaCy splitter, same as main corpus
      - insert_article()                   → real content stored, not empty string
      - insert_pipeline_sentences()        → quality gate applied automatically
      - article_exists()                   → skips if already in corpus

    Returns number of sentences added (0 on skip/dry-run).
    """
    url = _topic_url(topic)

    # Skip if already ingested — avoids re-fetching Wikipedia on repeat runs
    if not dry_run and article_exists(url):
        print(f"  already in corpus, skipping")
        return 0

    data = fetch_summary(topic)
    if not data or not data["extract"]:
        return 0

    extract    = data["extract"]
    title      = data["title"]
    sentences  = split_sentences(clean_text(extract))

    if dry_run:
        print(f"  [dry-run] {len(sentences)} sentences would be added")
        return len(sentences)

    # Insert a real article row with actual content
    article_id = insert_article(
        source_domain  = DOMAIN,
        url            = url,
        title          = f"[Wikipedia] {title}",
        content        = extract,           # real text, not ""
        date_published = datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        word_count     = len(extract.split()),
    )

    if article_id == -1:
        # URL already exists (race condition or re-run) — still try sentences
        from corpus.db import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM articles WHERE url=?", (url,))
        row = c.fetchone()
        conn.close()
        if not row:
            return 0
        article_id = row[0]

    # insert_pipeline_sentences runs _sentence_quality_gate on every sentence
    added = insert_pipeline_sentences(
        article_id    = article_id,
        source_domain = DOMAIN,
        url           = url,
        sentences     = sentences,
        pipeline      = PIPELINE,
    )
    return added


def run(topics: List[str], dry_run: bool = False, limit: int = 0) -> None:
    total_added   = 0
    total_skipped = 0
    i = 0

    for i, topic in enumerate(topics, 1):
        print(f"[{i}/{len(topics)}] '{topic}' ...", end=" ", flush=True)

        added = ingest_topic(topic, dry_run=dry_run)

        if added == 0:
            total_skipped += 1
            # message already printed by ingest_topic or fetch_summary
        else:
            total_added += added
            print(f"+{added} sentences")

        if limit and total_added >= limit:
            print(f"[wiki] Hit --limit {limit}. Stopping.")
            break

        time.sleep(0.4)   # be polite to Wikipedia's API

    print(f"\n[wiki] {'DRY RUN — ' if dry_run else ''}Summary:")
    print(f"  Topics attempted   : {i}")
    print(f"  Skipped (existing) : {total_skipped}")
    print(f"  Sentences added    : {total_added}")
    if not dry_run and total_added > 0:
        print(f"\n[wiki] Done. Rebuild the index:")
        print(f"       python retrieval/build_index.py --rebuild")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Wikipedia summaries into corpus"
    )
    parser.add_argument("--topics",  nargs="+", default=None,
                        help="Specific topics to ingest (default: full DEFAULT_TOPICS list)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be added without writing to DB")
    parser.add_argument("--limit",   type=int, default=0,
                        help="Stop after adding this many sentences total (0 = no limit)")
    args = parser.parse_args()

    topics = args.topics if args.topics else DEFAULT_TOPICS
    print(f"[wiki] {'DRY RUN — ' if args.dry_run else ''}Ingesting {len(topics)} topics\n")
    run(topics, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()