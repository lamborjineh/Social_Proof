"""
retrieval/utils.py — Shared retrieval helpers
SocialProof v3.4

Fixes applied in this version:
  #5  — trust_normalised() now uses the full REPUTATION registry properly;
         hybrid_score() exposes domain_tier so callers can boost Tier-1 on
         all queries, not just numeric ones.
  #9  — pipeline_timer() context manager added for latency logging.
  #13 — recency_boost() updated: evidence older than 2 yr gets 0.5x weight,
         older than 5 yr gets 0.3x. date_published preferred over URL parsing
         when available.
  #14 — Entity identity scoring added (v3.4).
         Addresses "topic neighbor vs same event" retrieval problem:
         "Duterte ICC" and "Bato ICC" are semantically close but
         contextually different events. entity_identity_score() extracts
         named entities from a claim and scores candidate text on:
           - entity overlap bonus  (shared actors/places/events)
           - entity mismatch penalty (central claim entities absent from text)
         apply_entity_rerank() applies this as a post-retrieval adjustment to
         hybrid_score, keeping semantic similarity but penalising topic drift.
         context_identity_label() maps the combined score to a 3-tier UI label:
           ✅ Same event/context | ⚠ Related topic | ❌ Broad thematic similarity

Import from here in every retrieval module. Do NOT redefine these locally.
"""
from __future__ import annotations

import re
import time
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional

from corpus.source_registry import get_reputation, STATS_DOMAINS, TIER_MAP

# ── Index directory ───────────────────────────────────────────────────────────

INDEX_DIR = Path(__file__).parent.parent / "data"

# ── Hybrid ranking weights ────────────────────────────────────────────────────
W_SEMANTIC = 0.6
W_TRUST    = 0.2
W_RECENCY  = 0.2

# ── Numeric query detection ───────────────────────────────────────────────────

_NUMERIC_DOMAIN_RE = re.compile(
    r"\b(rate|percent|%|gdp|inflat\w*|unemploy\w*|pover\w*|popul\w*|"
    r"statistic\w*|econom\w*|financ\w*|fiscal\w*|monetar\w*|"
    r"trade|tariff|deficit|surplus|budget\w*|revenue\w*|"
    r"price\w*|cost\w*|wage\w*|incom\w*|"
    r"food\w*|hunger\w*|famin\w*|malnutrit\w*|"
    r"supply\w*|demand\w*|commodit\w*|harvest\w*|agri\w*|"
    r"currenc\w*|peso|exchang\w*|depreciat\w*|appreciat\w*|"
    r"growth|productiv\w*|output\w*|export\w*|import\w*|"
    r"climat\w*|emiss\w*|vulnerab\w*|insecur\w*|"
    r"index\w*|indicator\w*|forecast\w*|outlook\w*)\b",
    re.I,
)

_NUMERIC_STRUCTURAL_RE = re.compile(
    r"\b(top\s+\d+|among\s+top|rank\w*|"
    r"impact\w*|at\s+risk|risk\s+of|"
    r"increas\w*|decreas\w*|surg\w*|spik\w*|drop\w*|declin\w*|"
    r"highest|lowest|biggest|largest|worst|best)\b",
    re.I,
)


def is_numeric_query(claim: str) -> bool:
    """Return True when the query is about measurable/economic topics.

    Uses domain stems rather than a fixed word list so new phrasings
    ('depreciation', 'economies', 'malnutrition') are caught automatically.
    Structural signals alone ('top 10 most wanted') are not enough —
    a domain stem must also be present.
    """
    return bool(_NUMERIC_DOMAIN_RE.search(claim))


# ── Niche / entertainment query detection ────────────────────────────────────
# Used by live_search.py to decide whether to lower the reputation floor and
# bypass the ALL_CREDIBLE_DOMAINS whitelist for Phase 2 retrieval.
# Extend this set freely — false positives are safe (niche mode only widens
# is_niche_query removed — live_search.py uses its own _is_niche_query() with broader logic.


# ── Per-pipeline index file resolution ───────────────────────────────────────

def index_files(pipeline: str) -> Tuple[Path, Path, Path, Path]:
    """
    Return (faiss_path, npy_path, meta_path, type_path) for a given pipeline.
    Pipeline 'all' omits the suffix (backward-compatible combined index).
    """
    suffix = f"_{pipeline}" if pipeline != "all" else ""
    return (
        INDEX_DIR / f"embeddings{suffix}.faiss",
        INDEX_DIR / f"embeddings{suffix}.npy",
        INDEX_DIR / f"sentences_meta{suffix}.json",
        INDEX_DIR / f"index_type{suffix}.txt",
    )


# ── Scoring helpers ───────────────────────────────────────────────────────────

def recency_boost(url: str, date_published: Optional[str] = None) -> float:
    """
    Score recency in [0, 1].

    Fix #13: prefer date_published (ISO YYYY-MM-DD) over URL-pattern guessing.
    Decay schedule (linear):
      ≤ 30 days  → 1.0
      ≤ 730 days → linear 1.0 → 0.5
      ≤ 1825 days (5 yr) → linear 0.5 → 0.3
      > 5 yr     → 0.3
    Falls back to 0.5 (neutral) when no date is parseable.
    """
    days_old: Optional[int] = None

    # Prefer explicit date_published field
    if date_published:
        try:
            pub = datetime.fromisoformat(date_published[:10])
            days_old = (datetime.now() - pub).days
        except Exception:
            pass

    # Fallback: extract year/month from URL
    if days_old is None:
        m = re.search(r"/(20\d{2})(?:/(\d{2}))?", url)
        if m:
            try:
                year  = int(m.group(1))
                month = int(m.group(2)) if m.group(2) else 6
                days_old = (datetime.now() - datetime(year, month, 1)).days
            except Exception:
                pass

    if days_old is None:
        return 0.5

    if days_old <= 30:
        return 1.0
    if days_old <= 730:
        return 1.0 - 0.5 * (days_old - 30) / 700.0
    if days_old <= 1825:
        return 0.5 - 0.2 * (days_old - 730) / 1095.0
    return 0.3


def trust_normalised(domain: str) -> float:
    """
    Normalise a domain's reputation score to [0, 1].

    Fix (audit §MED): unknown domains previously returned 0.0 because
    get_reputation() defaults to 0.5 and the normalization range starts at 0.5,
    mapping exactly to 0.0.  Unknown domains should be neutral (0.5), not
    penalised.  We detect unknowns by checking REPUTATION directly and short-
    circuit before the normalization formula.
    """
    from corpus.source_registry import REPUTATION
    if domain not in REPUTATION:
        return 0.5   # neutral — don't penalise evidence from unknown sources
    rep = REPUTATION[domain]
    lo, hi = 0.50, 1.0
    return max(0.0, min(1.0, (rep - lo) / (hi - lo)))


def hybrid_score(
    semantic: float,
    domain: str,
    url: str,
    numeric_boost: bool = False,
    date_published: Optional[str] = None,
) -> float:
    """
    Weighted hybrid ranking score:
        final = 0.6 × semantic + 0.2 × domain_trust + 0.2 × recency

    Fix #5: numeric_boost now applies to ALL Tier-1 sources (was STATS_DOMAINS
    only). Tier-1 covers PSA, BSP, WHO, World Bank, etc.

    Fix #13: date_published passed through to recency_boost().
    """
    semantic = min(1.0, max(0.0, semantic))
    score = (
        W_SEMANTIC * semantic
        + W_TRUST   * trust_normalised(domain)
        + W_RECENCY * recency_boost(url, date_published=date_published)
    )
    tier = TIER_MAP.get(domain, 3)
    if numeric_boost and tier == 1:
        score = min(1.0, score * 1.15)
    return score


# ── Sentence splitting ────────────────────────────────────────────────────────

def split_sentences(
    text: str,
    min_len: int = 20,
    max_len: int = 500,
) -> list[str]:
    """
    Split text into sentences on .!? followed by a capital letter, digit, or
    opening quote. Filters by length.
    """
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])', text)
    return [s.strip() for s in parts if min_len <= len(s.strip()) <= max_len]


# ── Fix #9: Pipeline timing context manager ───────────────────────────────────

@contextlib.contextmanager
def pipeline_timer(stage: str, log_fn=None):
    """
    Context manager that measures wall-clock time for a pipeline stage.

    Usage:
        with pipeline_timer("retrieval", log_fn=db.log_event) as t:
            results = retriever.search(claim)
        # t.elapsed_ms is available after the block exits

    log_fn, if provided, is called as log_fn("INFO", "pipeline", message, details).
    Suitable for passing corpus.db.log_event directly.
    """
    class _Timer:
        elapsed_ms: float = 0.0

    t = _Timer()
    start = time.perf_counter()
    try:
        yield t
    finally:
        t.elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        msg = f"[pipeline] {stage} completed in {t.elapsed_ms} ms"
        if log_fn:
            try:
                log_fn("INFO", "pipeline_timer", msg,
                       f'{{"stage": "{stage}", "elapsed_ms": {t.elapsed_ms}}}')
            except Exception:
                pass


# ── Fix #14: Entity identity scoring (v3.4) ───────────────────────────────────
#
# Problem: semantic embeddings understand "topic similarity" but retrieval
# needs "event/context identity."  "Duterte ICC" and "Bato dela Rosa ICC" are
# politically close in embedding space but are different contextual events —
# different actors, different hearings, different timelines.
#
# Solution: extract named entities (proper nouns + key action nouns) from the
# claim, then score each candidate on:
#   - OVERLAP BONUS  — entities from the claim that appear in the text
#   - MISMATCH PENALTY — claim entities that are completely absent from the text
#
# The entity score is blended into hybrid_score at call sites via
# apply_entity_rerank(), keeping semantic similarity dominant but penalising
# articles whose central actors differ from the claim.
#
# Design principles:
#   - No external NLP dependency (spaCy/NLTK not required).  Uses regex-based
#     proper-noun extraction: capitalised tokens 4+ chars, not stopwords.
#   - Penalty is asymmetric: missing entities hurt more than extra ones.
#     A claim about "Bato" that finds an article mentioning both "Bato" and
#     "Duterte" should NOT be penalised — the article is still on-topic.
#   - Weights are kept conservative (entity contributes ≤30% of final score)
#     so we don't over-filter when entities are legitimately absent (e.g. a
#     statistical claim where the actor is implied).

# Entity extraction config
_ENTITY_STOPWORDS = {
    "The", "This", "That", "These", "Those", "Their", "There",
    "Which", "Where", "When", "What", "Who", "How", "Why",
    "Also", "Both", "Such", "More", "Most", "Some", "Many",
    "Each", "About", "After", "Before", "During", "While",
    "With", "From", "Into", "Upon", "Over", "Under", "Between",
    "Senate", "House", "Court", "Law", "Act", "Republic",  # too generic alone
}

# Action nouns that anchor event identity (match lowercase)
_EVENT_ANCHOR_RE = re.compile(
    r"\b(standoff|lockdown|hearing|"
    r"arrest|arrested|arrests|warrant|warrants|"
    r"detained|detention|detain|"
    r"charged|charge|charges|indicted|indictment|"
    r"jailed|imprisoned|released|"
    r"shootout|gunshot|gunfire|firing|ambush|raid|operation|manhunt|"
    r"testimony|appearance|surrender|surrendered|"
    r"flight|escape|escaped|fled|"
    r"acquitted|acquittal|convicted|conviction|sentenced|verdict|"
    r"impeachment|impeached|resignation|resigned|"
    r"appointment|appointed|confirmation|confirmed|vote|voted|election|elected|"
    r"shooting|shot|killed|kill|wounded|injured|dead|casualt|attack|attacked|clash|clashed|"
    r"hike|cut|pause|freeze|policy|ruling|"
    r"protest|protested|rally|demonstration|crackdown|dispersal|fired|sacked)\b",
    re.I,
)

# Weights for entity scoring blend
_W_ENTITY  = 0.25   # max entity bonus added to hybrid score
_W_PENALTY = 0.45   # max entity mismatch penalty subtracted


def extract_claim_entities(claim: str) -> dict:
    """
    Extract named entities and event anchors from a claim string.

    Returns:
        {
          "proper_nouns": set of capitalised tokens (person/place names),
          "event_anchors": set of action/event nouns (lowercase),
          "all_entities": union of both sets (lowercase for matching),
        }

    Uses regex only — no spaCy/NLTK required.

    Examples:
        "Ronald dela Rosa faces ICC arrest warrant after Senate standoff"
        → proper_nouns: {"Ronald", "Rosa", "Senate", "ICC"}
        → event_anchors: {"arrest", "standoff"}
    """
    # Normalize: if claim is all-lowercase, we need to recover proper nouns.
    # Strategy: extract event anchors from the raw lowercase claim FIRST (they
    # are unambiguous via regex), then capitalize only the remaining tokens so
    # action verbs like "arrested" are never mistaken for person names.
    # This avoids any hardcoded verb/stopword lists.
    if claim == claim.lower():
        # Find which token positions are event anchors (keep lowercase)
        event_token_set = {m.lower() for m in _EVENT_ANCHOR_RE.findall(claim)}
        # Also skip generic grammar words
        _GRAMMAR = {
            "the", "a", "an", "is", "are", "was", "were", "in", "of", "to",
            "for", "that", "this", "and", "or", "but", "on", "at", "by",
            "from", "with", "has", "have", "had", "be", "been", "will",
            "would", "could", "should", "may", "might", "do", "does", "did",
            "its", "it", "who", "what", "when", "where", "how", "said",
            "also", "than", "then", "not", "no", "as", "up", "so", "if",
            "can", "all", "more", "some", "very", "just", "only",
        }
        # Capitalize only tokens that are NOT event anchors and NOT grammar
        claim = " ".join(
            w if (w in event_token_set or w in _GRAMMAR) else w.capitalize()
            for w in claim.split()
        )

    # Proper nouns: capitalised, 3+ chars, not in stopword list
    proper_nouns = {
        tok for tok in re.findall(r"\b[A-Z][a-z]{2,}\b", claim)
        if tok not in _ENTITY_STOPWORDS
    }
    # Also grab all-caps abbreviations (ICC, BSP, DOJ, etc.)
    proper_nouns |= set(re.findall(r"\b[A-Z]{2,6}\b", claim))

    # Event anchors: key action/event words
    event_anchors = set(m.lower() for m in _EVENT_ANCHOR_RE.findall(claim))

    all_entities = {e.lower() for e in proper_nouns} | event_anchors

    return {
        "proper_nouns":  proper_nouns,
        "event_anchors": event_anchors,
        "all_entities":  all_entities,
    }


def entity_identity_score(
    claim_entities: dict,
    candidate_text: str,
    candidate_title: str = "",
) -> float:
    """
    Score how well a candidate text matches the claim's entity identity.

    Returns a float in [-1.0, +1.0]:
      Positive → entities overlap well (same event/context)
      Zero     → neutral (no entity info to judge)
      Negative → key claim entities are absent (different event)

    Algorithm:
      1. Check which claim entities appear in the candidate text + title.
      2. overlap_ratio = matched / total_claim_entities
      3. missing_ratio = (central entities missing) / total_claim_entities
         "Central" = proper nouns (person/org names), not generic event words.
      4. score = overlap_bonus - mismatch_penalty
         overlap_bonus  = overlap_ratio         (up to +1.0)
         mismatch_penalty = missing_ratio * 1.5  (amplified — absence matters more)
      5. Clamp to [-1.0, +1.0].

    Why asymmetric penalty:
      An article about "Bato + Duterte + ICC" matching a query about "Bato + ICC"
      has overlap_ratio=1.0 (all claim entities found) → no penalty, correct.
      An article about "Duterte + ICC" matching "Bato + ICC" has overlap_ratio=0.5
      but missing_ratio=0.5 (Bato absent) → penalised, correct.
    """
    all_entities   = claim_entities.get("all_entities", set())
    proper_nouns   = claim_entities.get("proper_nouns", set())

    if not all_entities:
        return 0.0   # no entity info — neutral, don't penalise

    search_text = (candidate_text + " " + candidate_title).lower()

    import re as _re
    matched  = sum(
        1 for e in all_entities
        if _re.search(r"\b" + _re.escape(e) + r"\b", search_text)
    )
    overlap_ratio = matched / len(all_entities)

    # Only proper nouns (person/org names) contribute to mismatch penalty.
    # Generic event words like "arrest" or "standoff" are too common to penalise on.
    pn_lower = {e.lower() for e in proper_nouns}
    if pn_lower:
        missing_pn     = sum(
            1 for e in pn_lower
            if not _re.search(r"\b" + _re.escape(e) + r"\b", search_text)
        )
        missing_ratio  = missing_pn / len(pn_lower)
    else:
        missing_ratio  = 0.0

    raw = overlap_ratio - (missing_ratio * 1.5)
    return max(-1.0, min(1.0, raw))


def apply_entity_rerank(
    candidates: list,
    claim: str,
    entity_weight: float = _W_ENTITY,
    penalty_weight: float = _W_PENALTY,
) -> list:
    """
    Apply entity identity scoring as a post-retrieval score adjustment.

    Mutates each candidate dict in-place, adding:
      "entity_score"    — raw [-1, +1] entity identity score
      "context_label"   — UI label: "same_event" | "related_topic" | "broad_match"

    Also adjusts "similarity" by blending entity score:
      new_similarity = original_similarity
                       + entity_weight  * max(0, entity_score)   ← bonus
                       - penalty_weight * max(0, -entity_score)  ← penalty

    The blend is additive so existing semantic + trust + recency scoring is
    preserved. Entity scoring only nudges — it never completely overrides a
    high-semantic-similarity result.

    Args:
        candidates:     List of evidence dicts (each has "text", "similarity",
                        optionally "article_title").
        claim:          Original claim string.
        entity_weight:  Max bonus for full entity overlap (default 0.25).
        penalty_weight: Max penalty for full entity mismatch (default 0.20).

    Returns:
        Candidates re-sorted by adjusted similarity (descending).
    """
    if not candidates or not claim:
        return candidates

    claim_entities = extract_claim_entities(claim)

    if not claim_entities["all_entities"]:
        # No extractable entities (e.g. pure numeric/stats claim) — skip
        for c in candidates:
            c["entity_score"]  = 0.0
            c["context_label"] = context_identity_label(c.get("similarity", 0), 0.0)
        return candidates

    for c in candidates:
        text  = c.get("text", "")
        title = c.get("article_title", "") or c.get("title", "")
        e_score = entity_identity_score(claim_entities, text, title)

        bonus   =  entity_weight  * max(0.0, e_score)
        penalty =  penalty_weight * max(0.0, -e_score)
        original = float(c.get("similarity", 0.0))
        adjusted = max(0.0, min(1.0, original + bonus - penalty))

        c["entity_score"]       = round(e_score, 4)
        c["similarity_original"] = round(original, 4)
        c["similarity"]         = round(adjusted, 4)
        c["context_label"]      = context_identity_label(adjusted, e_score)

    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates


def context_identity_label(similarity: float, entity_score: float) -> str:
    """
    Map (similarity, entity_score) to a 3-tier context identity UI label.

    Tiers:
      "same_event"    → ✅  High similarity AND entities align
                            (same actors, same incident)
      "related_topic" → ⚠   High/medium similarity but entity overlap is partial
                            (related political topic, adjacent event)
      "broad_match"   → ❌  Low entity alignment or low similarity
                            (same broad theme, different story)

    These labels are for UI display only; the similarity score drives ranking.
    """
    if similarity >= 0.52 and entity_score >= 0.25:
        return "same_event"
    if similarity >= 0.42 and entity_score >= 0.15:
        return "related_topic"
    return "broad_match"
