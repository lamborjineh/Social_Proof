"""
SocialProof — Pipeline Orchestrator v5.0

Changes vs v4.0:
  - MBFC enrichment: _enrich_with_mbfc() called after article assembly.
    Adds mbfc_url, mbfc_factual, mbfc_bias to each article card.
  - Source diversity: inspect_source_diversity() classifies each article
    into government/academic/news/factcheck/international/other and
    returns a SourceDiversityInfo object + logs to source_diversity_log.
  - Retrieval reason: _build_retrieval_reason() attaches a short
    human-readable chip to each card explaining why it was retrieved
    ("Matched: [keyword]"), addressing the black-box criticism.
  - Live search escalation threshold lowered: fires when FAISS max
    similarity < 0.25 (was: only when no results at all). Catches the
    case where FAISS returns weakly-related old articles for recent news.
  - URL slug fallback: when URLFetcher fails but slug has ≥4 words,
    uses slug text for retrieval instead of returning empty.
  - confidence_before forwarded through to ConfidenceSnapshot save.

Return contract:
  {
    "articles":        list of enriched article dicts,
    "keywords":        list of str,
    "processing_ms":   int,
    "live_search_used": bool,
    "url_fetch_failed": bool,
    "url_fetch_error":  str,
    "source_diversity": SourceDiversityInfo dict,
  }
"""

import base64 as _b64
import concurrent.futures as _cf
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

from config import MAX_EVIDENCE, logger, TIMEOUT_LIVE_SEARCH as _LIVE_TIMEOUT
from core.model_registry import ModelRegistry
from schemas import AnalyzeRequest, SourceDiversityInfo

from .preprocessing      import PreprocessingModule
from .url_fetcher        import URLFetcher
from .image_input        import extract_text_from_image
from .evidence_retrieval import EvidenceRetrievalModule, _try_load_faiss_retriever

_RETRIEVAL_TOP_K = 5

# ── Domain classification helpers ─────────────────────────────────────────────
# Used by inspect_source_diversity() to categorise each article.

_GOV_SUFFIXES    = (".gov", ".gov.ph", ".gov.uk", ".gov.au", ".gov.sg",
                    ".gc.ca", ".gouv.fr", ".gob.mx", ".govt.nz", ".gov.in",
                    ".europa.eu", ".un.org", ".who.int", ".psa.gov.ph")
_ACADEMIC_SUFFIXES = (".edu", ".ac.uk", ".edu.au", ".ac.nz", ".edu.sg",
                      ".edu.cn", ".ac.jp", ".edu.ph")
_ACADEMIC_DOMAINS  = {"pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
                      "researchgate.net", "jstor.org", "sciencedirect.com",
                      "nature.com", "springer.com", "plos.org", "frontiersin.org"}
_FACTCHECK_DOMAINS = {"snopes.com", "factcheck.org", "politifact.com",
                      "fullfact.org", "africacheck.org", "verafiles.org",
                      "tsek.ph", "rappler.com", "reuters.com/fact-check",
                      "apnews.com/hub/ap-fact-check", "boomlive.in"}
# Domains registered outside PH that are not primarily Filipino outlets
_INTERNATIONAL_DOMAINS = {"bbc.com", "bbc.co.uk", "nytimes.com", "theguardian.com",
                           "aljazeera.com", "dw.com", "france24.com", "scmp.com",
                           "straitstimes.com", "channelnewsasia.com", "abc.net.au",
                           "cbc.ca", "theconversation.com"}


def _classify_source(url: Optional[str], publisher: Optional[str]) -> str:
    """
    Return one of: government | academic | news | factcheck | international | other
    Priority: government > factcheck > academic > international > news > other
    """
    if not url:
        return "other"
    try:
        parsed  = urlparse(url if url.startswith("http") else "https://" + url)
        domain  = parsed.netloc.replace("www.", "").lower()
        path    = parsed.path.lower()
    except Exception:
        return "other"

    # Government
    for suf in _GOV_SUFFIXES:
        if domain.endswith(suf) or domain == suf.lstrip("."):
            return "government"

    # Fact-check (domain-level or path-level match)
    for fc in _FACTCHECK_DOMAINS:
        if domain == fc or domain.endswith("." + fc) or fc in domain + path:
            return "factcheck"

    # Academic
    for suf in _ACADEMIC_SUFFIXES:
        if domain.endswith(suf):
            return "academic"
    if domain in _ACADEMIC_DOMAINS:
        return "academic"

    # International news
    if domain in _INTERNATIONAL_DOMAINS:
        return "international"

    # Default: news
    return "news"


def inspect_source_diversity(articles: List[Dict]) -> SourceDiversityInfo:
    """
    Classify each article and return a SourceDiversityInfo object.
    Computes diversity_score using Shannon entropy normalised by log(6):
      H = -Σ(p_i × log(p_i)) / log(6)
    where p_i = count_i / total. This treats a mix of 1 govt + 5 news very
    differently from a genuine 6-category spread, unlike the old non_zero/6
    formula. Score range: 0.0 (monoculture) → 1.0 (perfectly even spread).
    """
    import math

    counts = {
        "government": 0, "academic": 0, "news": 0,
        "factcheck": 0, "international": 0, "other": 0,
    }
    for a in articles:
        cat = a.get("source_category") or _classify_source(
            a.get("source_url"), a.get("publisher")
        )
        counts[cat] = counts.get(cat, 0) + 1

    total = sum(counts.values())
    if total == 0:
        diversity = 0.0
    else:
        entropy = 0.0
        for v in counts.values():
            if v > 0:
                p = v / total
                entropy -= p * math.log(p)
        # Normalise by log(6) — maximum possible entropy for 6 categories
        diversity = round(entropy / math.log(6), 2) if entropy > 0 else 0.0

    return SourceDiversityInfo(
        total_articles      = len(articles),
        count_government    = counts["government"],
        count_academic      = counts["academic"],
        count_news          = counts["news"],
        count_factcheck     = counts["factcheck"],
        count_international = counts["international"],
        count_other         = counts["other"],
        diversity_score     = diversity,
    )


def _build_retrieval_reason(article: Dict, keywords: List[str]) -> str:
    """
    Returns a one-line human-readable explanation of why this article
    was retrieved. Shown as a chip under each evidence card.
    Addresses the 'black box' explainability gap.

    v3.4: Uses context_label from entity identity scoring to display a
    3-tier match type instead of a binary "Semantically similar" label.
    This surfaces the distinction between:
      ✅ Same event/context  — same actors, same incident
      ⚠ Related topic       — adjacent event or political topic
      ❌ Broad thematic match — same domain, different story
    """
    source_type    = article.get("source_type", "faiss")
    title          = (article.get("article_title") or "").lower()
    pub            = article.get("publisher", "")
    date           = article.get("date_published", "")
    similarity     = article.get("similarity_score", 0)
    context_label  = article.get("context_label", "")

    if source_type == "hardcoded":
        return "From the verified reference corpus"

    # v3.4: If entity scoring ran, use context_label for 3-tier display.
    # Checked before the source_type == "live" branch so live results also
    # get entity-aware labels instead of the generic "Recent reporting" fallback.
    if context_label == "same_event":
        label = "✅ Same event/context"
        return f"{label} — {date}" if date and source_type == "live" else label
    if context_label == "related_topic":
        label = "⚠ Related topic — may not be the same incident"
        return f"{label} — {date}" if date and source_type == "live" else label
    if context_label == "broad_match":
        label = "❌ Broad thematic similarity only"
        return f"{label} — {date}" if date and source_type == "live" else label

    if source_type == "live":
        if date:
            return f"Recent reporting — {date}"
        return "Retrieved via live search — recent coverage"

    # Fallback: FAISS keyword match (no entity scoring available)
    matched = [kw for kw in keywords if kw.lower() in title]
    if matched:
        kw_str = ", ".join(matched[:2])
        return f"Matched: {kw_str}"

    if similarity and similarity > 0.4:
        return "Semantically similar to your input"

    return ""


def _enrich_with_mbfc(articles: List[Dict]) -> List[Dict]:
    """
    Looks up each article's domain in the mbfc_domains table and
    attaches mbfc_url, mbfc_factual, and mbfc_bias.
    Silently skips on any DB or parse error.
    """
    try:
        from pipeline.source_credibility import get_mbfc_rating
    except ImportError:
        return articles

    enriched = []
    for a in articles:
        url = a.get("source_url", "")
        try:
            mbfc = get_mbfc_rating(url) if url else None
        except Exception:
            mbfc = None

        if mbfc:
            a["mbfc_url"]     = mbfc.get("notes_url") or mbfc.get("mbfc_url")
            a["mbfc_factual"] = mbfc.get("factual_reporting")
            a["mbfc_bias"]    = mbfc.get("bias_rating")
        enriched.append(a)
    return enriched


class AnalysisPipeline:
    """
    Stateless orchestrator. Instantiated once at startup, reused per request.
    Retrieves articles for the user to evaluate — makes no verdict itself.
    """

    def __init__(self):
        self.preprocessor       = PreprocessingModule()
        self.evidence_retriever = EvidenceRetrievalModule()

    def run(
        self,
        request: AnalyzeRequest,
        user_submitted_claim: Optional[str] = None,
    ) -> Dict:
        t0 = time.time()

        # ── 0a. URL fetching ──────────────────────────────────────────────────
        raw_text         = request.text or ""
        url_fetch_failed = False
        url_fetch_error  = ""

        if request.input_type == "url" and request.url:
            fetch_result = URLFetcher.fetch(request.url)
            if fetch_result["error"]:
                logger.warning(f"URL fetch failed: {fetch_result['error']}")
                url_fetch_failed = True
                url_fetch_error  = fetch_result["error"]
                # v5.0: extract slug tokens and continue with retrieval
                # rather than returning early on short slugs
                try:
                    parsed_path = urlparse(request.url).path
                    url_words = [w for w in re.split(r"[-_/]", parsed_path) if len(w) > 3]
                except Exception:
                    url_words = []
                if url_words:
                    raw_text = " ".join(url_words[:12])
                    logger.info(f"[0a] URL fetch failed — using slug: '{raw_text}'")
                else:
                    url_words_fallback = [
                        w for w in re.split(r"[/\-_?=&.]", request.url) if len(w) > 3
                    ]
                    if len(url_words_fallback) < 4:
                        return {
                            "articles":         [],
                            "keywords":         [],
                            "processing_ms":    int((time.time() - t0) * 1000),
                            "live_search_used": False,
                            "url_fetch_failed": True,
                            "url_fetch_error":  (
                                f"Could not access URL: {fetch_result['error']}. "
                                "Please paste the article text instead."
                            ),
                            "source_diversity": SourceDiversityInfo().dict(),
                        }
                    raw_text = " ".join(url_words_fallback)
            else:
                raw_text = fetch_result["text"] or request.url
                logger.info(f"[0a] URL fetched: {len(raw_text)} chars")

        # ── 0b. OCR (image input) ─────────────────────────────────────────────
        if request.input_type == "image" and getattr(request, "image_data", None):
            try:
                _image_bytes = _b64.b64decode(request.image_data)
            except Exception:
                _image_bytes = None
            if _image_bytes:
                ocr_text = extract_text_from_image(_image_bytes)
                if ocr_text:
                    raw_text = ocr_text
                    logger.info(f"[0b] OCR extracted: {len(raw_text)} chars")

        # ── 1. Preprocessing ──────────────────────────────────────────────────
        clean_text = self.preprocessor.clean(raw_text)
        doc        = ModelRegistry.nlp()(clean_text)
        keywords   = self.preprocessor.extract_keywords(doc)
        logger.info(f"[1] Preprocessed. Keywords: {keywords[:5]}")

        # ── 6. Evidence / Article Retrieval ───────────────────────────────────
        retrieval_query  = user_submitted_claim.strip() if user_submitted_claim else clean_text
        model            = ModelRegistry.embed()

        def _retrieve_with_timeout(query: str):
            with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(
                    self.evidence_retriever.retrieve,
                    query, _RETRIEVAL_TOP_K, "claim", model,
                )
                try:
                    return fut.result(timeout=_LIVE_TIMEOUT)
                except _cf.TimeoutError:
                    logger.warning(f"[6] Retrieval timed out after {_LIVE_TIMEOUT}s — trying FAISS only.")
                    # Fall back to FAISS directly on timeout
                    try:
                        retriever = _try_load_faiss_retriever()
                        if retriever:
                            return self.evidence_retriever._retrieve_faiss(
                                query, _RETRIEVAL_TOP_K, retriever, mode="claim"
                            )
                    except Exception:
                        pass
                    return [], False
                except Exception as e:
                    logger.warning(f"[6] Retrieval error: {e}")
                    return [], False

        retrieved, any_found = _retrieve_with_timeout(retrieval_query)

        # Determine if live search was used (source_type on first result)
        live_search_used = any(
            r.get("source_type") == "live" for r in (retrieved or [])
        )

        logger.info(
            f"[6] Retrieved {len(retrieved or [])} results — "
            f"live={live_search_used}, any_found={any_found}"
        )

        # ── Assemble, enrich, and deduplicate articles ────────────────────────
        articles: List[Dict] = []
        seen_urls: set = set()
        for item in (retrieved or []):
            url = item.get("source_url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            category = _classify_source(url, item.get("publisher"))
            reason   = _build_retrieval_reason(item, keywords)

            articles.append({
                "article_title":   item.get("article_title", ""),
                "publisher":       item.get("publisher", ""),
                "date_published":  item.get("date_published", ""),
                "source_url":      url,
                "source_type":     item.get("source_type", "faiss"),
                "source_category": category,
                "retrieval_reason": reason,
                "similarity_score": item.get("similarity_score", 0),
                # MBFC fields — populated below by _enrich_with_mbfc
                "mbfc_url":        None,
                "mbfc_factual":    None,
                "mbfc_bias":       None,
            })

        articles = articles[:MAX_EVIDENCE]

        # ── MBFC enrichment ───────────────────────────────────────────────────
        articles = _enrich_with_mbfc(articles)

        # ── Source diversity analysis ─────────────────────────────────────────
        diversity = inspect_source_diversity(articles)

        # ── Background: log source diversity ─────────────────────────────────
        try:
            _log_source_diversity(
                submission_id = None,   # filled in by analyze router after save
                session_token = getattr(request, "session_token", ""),
                diversity     = diversity,
            )
        except Exception:
            pass  # non-critical

        processing_ms = int((time.time() - t0) * 1000)
        logger.info(
            f"Pipeline complete in {processing_ms} ms — "
            f"{len(articles)} articles, live={live_search_used}, "
            f"diversity={diversity.diversity_score}"
        )

        return {
            "articles":         articles,
            "keywords":         keywords[:10],
            "processing_ms":    processing_ms,
            "live_search_used": live_search_used,
            "url_fetch_failed": url_fetch_failed,
            "url_fetch_error":  url_fetch_error if url_fetch_failed else "",
            "source_diversity": diversity.dict(),
        }


def _log_source_diversity(submission_id, session_token, diversity: SourceDiversityInfo):
    """Fire-and-forget log of source diversity to the DB."""
    try:
        import sqlalchemy as sa
        from database.models import engine
        with engine.begin() as conn:
            conn.execute(sa.text("""
                INSERT INTO source_diversity_log
                    (submission_id, session_token, total_articles,
                     count_government, count_academic, count_news,
                     count_factcheck, count_international, count_other,
                     diversity_score)
                VALUES
                    (:sid, :tok, :total,
                     :gov, :acad, :news,
                     :fc, :intl, :other,
                     :dscore)
            """), {
                "sid":    submission_id,
                "tok":    session_token,
                "total":  diversity.total_articles,
                "gov":    diversity.count_government,
                "acad":   diversity.count_academic,
                "news":   diversity.count_news,
                "fc":     diversity.count_factcheck,
                "intl":   diversity.count_international,
                "other":  diversity.count_other,
                "dscore": diversity.diversity_score,
            })
    except Exception as e:
        pass  # non-critical — never fail the pipeline over a log write
