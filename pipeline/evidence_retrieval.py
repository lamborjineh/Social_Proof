"""
SocialProof — Module 5: Evidence Retrieval
v4.0 — Live search primary + FAISS fallback
       + hardcoded corpus emergency fallback

Retrieval priority:
  1. Live search (Google News RSS + Google Fact Check API)
     → always tried first for fresh, query-specific results
  2. FAISS retriever (BGE-M3, built corpus)
     → fallback when live search returns nothing (timeout, obscure query,
       future event, rate limit)
  3. Hardcoded corpus (~10 entries, always available, no setup required)
     → emergency fallback when FAISS index files are missing entirely

"""

import hashlib
import os
import re
import requests as _requests
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

from sentence_transformers import util

from config import logger
from core.model_registry import ModelRegistry
from corpus.source_registry import get_publisher_name

# v4.0 MIL: softer threshold — prefer more context over strict precision.
# 0.07 vs old 0.10: hardcoded fallback corpus is small (~10 entries),
# so being strict here left many topics with zero results.
SIM_THRESHOLD = 0.07


# ── Unverified-claim tracking (corpus gap analysis) ──────────────────────────
# Capped at 500 unique claims to prevent unbounded memory growth in long-running servers.
_MAX_UNVERIFIED_TRACKED = 500
_unverified_counter: Dict[str, int] = defaultdict(int)
_unverified_texts:   Dict[str, str] = {}
_faiss_retriever = None


def record_unverified(claim_text: str) -> None:
    h = hashlib.md5(claim_text.encode()).hexdigest()
    # Only track new claims if we haven't hit the cap
    if h not in _unverified_texts and len(_unverified_texts) >= _MAX_UNVERIFIED_TRACKED:
        return
    _unverified_counter[h] += 1
    _unverified_texts[h] = claim_text
    if _unverified_counter[h] >= 3:
        logger.warning(
            f"[CORPUS GAP] Claim seen {_unverified_counter[h]}x with no evidence — "
            f"add to corpus: '{claim_text[:80]}…'"
        )


def get_unverified_log() -> List[Dict]:
    return sorted(
        [{"claim": _unverified_texts[h], "count": c}
         for h, c in _unverified_counter.items()],
        key=lambda x: x["count"],
        reverse=True,
    )


def _try_load_faiss_retriever():
    global _faiss_retriever
    if _faiss_retriever is not None:
        return _faiss_retriever

    try:
        from retrieval.retriever import get_retriever
        r = get_retriever()
        if r.has_index():
            _faiss_retriever = r
            logger.info(
                "[EvidenceRetrieval] FAISS retriever loaded — "
                "using real corpus for evidence retrieval."
            )
        else:
            _faiss_retriever = False
            logger.warning("[EvidenceRetrieval] FAISS retriever has no index.")
    except FileNotFoundError as e:
        _faiss_retriever = False
        logger.warning(f"[EvidenceRetrieval] FAISS index files not found: {e}")
    except ImportError as e:
        _faiss_retriever = False
        logger.warning(f"[EvidenceRetrieval] FAISS dependencies missing: {e}")
    except Exception as e:
        _faiss_retriever = False
        logger.warning(f"[EvidenceRetrieval] FAISS retriever unavailable: {e}")

    return _faiss_retriever


class EvidenceRetrievalModule:
    """
    Evidence retrieval:
      Primary   — Live search (Google News RSS + Fact Check API)
      Fallback1 — FAISS (BGE-M3 corpus) when live returns nothing
      Fallback2 — Hardcoded corpus when FAISS index missing entirely

    Pre-retrieval:
      Local heuristic scores each claim for check-worthiness.
      Low-worthiness claims are annotated but still processed.
    """

    # ── Hardcoded test corpus (emergency fallback only) ───────────────────────
    CORPUS: List[Dict] = [
        {
            "article_title": "IARC Monographs on Coffee, Mate, and Very Hot Beverages",
            "publisher":     "World Health Organization",
            "source_url":    "https://www.iarc.who.int/news-events/iarc-monographs-on-coffee-mate-and-very-hot-beverages/",
            "date_published": "2016-06-15",
            "text": "The World Health Organization has not classified coffee as a Group 1 carcinogen.",
        },
        {
            "article_title": "Vaccines Do Not Cause Autism",
            "publisher":     "Centers for Disease Control and Prevention",
            "source_url":    "https://www.cdc.gov/vaccinesafety/concerns/autism.html",
            "date_published": "2023-01-01",
            "text": "Vaccines do not cause autism. The original 1998 study claiming this link was retracted.",
        },
        {
            "article_title": "Vaccine Safety",
            "publisher":     "World Health Organization",
            "source_url":    "https://www.who.int/news-room/questions-and-answers/item/vaccines-and-immunization-vaccine-safety",
            "date_published": "2022-12-01",
            "text": "Vaccines are safe and effective. The scientific consensus from WHO, CDC confirms benefits.",
        },
        {
            "article_title": "Myth Busters: 5G Mobile Networks Do NOT Spread COVID-19",
            "publisher":     "World Health Organization",
            "source_url":    "https://www.who.int/emergencies/diseases/novel-coronavirus-2019/advice-for-public/myth-busters",
            "date_published": "2020-04-05",
            "text": "5G technology does not spread COVID-19. Viruses cannot travel on radio waves.",
        },
        {
            "article_title": "Understanding mRNA COVID-19 Vaccines",
            "publisher":     "Centers for Disease Control and Prevention",
            "source_url":    "https://www.cdc.gov/coronavirus/2019-ncov/vaccines/different-vaccines/mrna.html",
            "date_published": "2021-03-04",
            "text": "mRNA COVID-19 vaccines do not alter human DNA. mRNA never enters the cell nucleus.",
        },
        {
            "article_title": "Climate Change 2021: The Physical Science Basis",
            "publisher":     "Intergovernmental Panel on Climate Change",
            "source_url":    "https://www.ipcc.ch/assessment-report/ar6/",
            "date_published": "2021-08-09",
            "text": "Climate change is real and primarily driven by human activities, according to the IPCC.",
        },
        {
            "article_title": "Consumer Price Index Summary",
            "publisher":     "Philippine Statistics Authority",
            "source_url":    "https://psa.gov.ph/statistics/survey/price/summary-inflation-report-consumer-price-index",
            "date_published": "2024-01-01",
            "text": "The Philippine Statistics Authority publishes official monthly inflation statistics.",
        },
        {
            "article_title": "About VERA Files",
            "publisher":     "VERA Files",
            "source_url":    "https://verafiles.org/about",
            "date_published": "2023-01-01",
            "text": "VERA Files is an independent fact-checking organization accredited by the IFCN.",
        },
        {
            "article_title": "Amnesty International Report on the Philippines",
            "publisher":     "Amnesty International",
            "source_url":    "https://www.amnesty.org/en/location/asia-and-the-pacific/south-east-asia-and-the-pacific/philippines/report-philippines/",
            "date_published": "2023-04-01",
            "text": "Historical records confirm that Martial Law in the Philippines (1972-1981) involved systematic human rights violations.",
        },
        {
            "article_title": "What Does 'Natural' Really Mean?",
            "publisher":     "Science-Based Medicine",
            "source_url":    "https://sciencebasedmedicine.org",
            "date_published": "2022-06-01",
            "text": "Natural does not mean safe. Many natural substances are toxic (arsenic, cyanide).",
        },
    ]

    def __init__(self):
        self._embeddings = None
        self._texts      = [c["text"] for c in self.CORPUS]

    # ── Public interface ──────────────────────────────────────────────────────

    def retrieve(
        self,
        claim_text: str,
        top_k: int = 5,
        mode: str = "claim",
        model=None,
    ) -> Tuple[List[Dict], bool]:
        """
        Return (results, any_found).

        Priority: live search → FAISS → hardcoded corpus.

        model is the embedding model passed in from the orchestrator.
        If not provided, ModelRegistry.embed() is called internally.
        mode is forwarded to FAISS retriever.search() when used as fallback.
        """
        # 1. Live search — always first
        _model = model or ModelRegistry.embed()
        live_results, live_found = self.retrieve_live(claim_text, _model, top_k)
        if live_found:
            return live_results, True

        # 2. FAISS — fallback when live returns nothing
        logger.info("[EvidenceRetrieval] Live search empty — falling back to FAISS.")
        retriever = _try_load_faiss_retriever()
        if retriever:
            faiss_results, faiss_found = self._retrieve_faiss(
                claim_text, top_k, retriever, mode=mode
            )
            if faiss_found:
                return faiss_results, True

        # 3. Hardcoded corpus — last resort when FAISS index missing
        logger.info("[EvidenceRetrieval] FAISS empty/unavailable — using hardcoded corpus.")
        return self._retrieve_hardcoded(claim_text, top_k)

    def retrieve_live(self, claim_text: str, model, top_k: int = 5) -> Tuple[List[Dict], bool]:
        """
        Live search fallback — called by orchestrator when FAISS coverage == 0.
        Requires the embedding model to be passed in (no second model load).
        Returns one card per article: title, publisher, URL, date.
        """
        try:
            from retrieval.live_search import live_search
            raw = live_search(claim_text, model, k=top_k)
            if not raw:
                return [], False

            # Deduplicate by URL — keep highest similarity per article.
            seen_urls: dict = {}
            for r in raw:
                url = r.get("url", "")
                if not url:
                    continue
                sim = float(r.get("similarity", 0.35))
                if url not in seen_urls or sim > seen_urls[url]["_sim"]:
                    seen_urls[url] = {**r, "_sim": sim}

            mapped = []
            for r in sorted(seen_urls.values(), key=lambda x: x["_sim"], reverse=True)[:top_k]:
                domain = r.get("source_domain") or r.get("domain", "")
                pub    = r.get("source_label") or get_publisher_name(domain) or domain
                mapped.append({
                    # ── Internal fields (NLI + orchestrator need these) ───────
                    "text":             r.get("text", ""),
                    "source_label":     pub,
                    "source_url":       r.get("url", ""),
                    # ── Display fields (shown to user) ────────────────────────
                    "article_title":    r.get("article_title") or r.get("title") or "",
                    "publisher":        pub,
                    "date_published":   r.get("date_published") or "",
                    # ── Scoring ───────────────────────────────────────────────
                    "similarity_score": float(r.get("similarity", 0.35)),
                    "nli_confidence":   float(r.get("nli_confidence", 0.5)),
                    "found":            True,
                    "source_type":      r.get("source_type", "live"),
                })
            return mapped, True
        except Exception as e:
            logger.warning(f"[EvidenceRetrieval] Live search failed: {e}")
            return [], False

    # ── FAISS retrieval ───────────────────────────────────────────────────────

    def _retrieve_faiss(
        self, claim_text: str, top_k: int, retriever, mode: str = "claim"
    ) -> Tuple[List[Dict], bool]:
        """
        v4.0 MIL: requests top_k * 3 candidates (was top_k * 2).
        The extra headroom gives the MMR reranker in retriever.py more
        material to enforce diversity across genuinely different angles.

        mode is forwarded to retriever.search() to select MMR lambda:
          "submission" → λ=0.5 (broader, more diverse — corroboration section)
          "claim"      → λ=0.7 (tighter, relevance-first — user claim section)
        """
        try:
            raw_results = retriever.search(claim_text, k=top_k * 3, mode=mode)
        except Exception as e:
            logger.warning(f"[EvidenceRetrieval] FAISS search failed: {e}. Falling back.")
            return self._retrieve_hardcoded(claim_text, top_k)

        if not raw_results:
            return [], False

        # Group by article URL — keep the highest-scoring sentence per article.
        # The sentence is used only for semantic matching; the card shows
        # article title, publisher, URL, and date instead.
        seen_urls: dict = {}
        for r in raw_results:
            url = r.get("url", "")
            if not url:
                continue
            if url not in seen_urls or r["similarity"] > seen_urls[url]["similarity"]:
                seen_urls[url] = r

        mapped = []
        for r in sorted(seen_urls.values(), key=lambda x: x["similarity"], reverse=True)[:top_k]:
            domain = r.get("domain", "")
            mapped.append({
                # ── Internal fields (NLI + orchestrator need these) ───────────
                "text":             r.get("text", ""),
                "source_label":     r.get("publisher") or get_publisher_name(domain) or domain,
                "source_url":       r.get("url", ""),
                # ── Display fields (shown to user instead of raw sentence) ────
                "article_title":    r.get("article_title") or "",
                "publisher":        r.get("publisher") or get_publisher_name(domain) or domain,
                "date_published":   r.get("date_published") or "",
                # ── Scoring ───────────────────────────────────────────────────
                "similarity_score": float(r["similarity"]),
                "found":            True,
                "source_type":      "faiss",
            })

        return mapped, True

    # ── Hardcoded corpus retrieval ────────────────────────────────────────────

    def _get_hardcoded_embeddings(self):
        if self._embeddings is None:
            logger.info(f"Pre-computing {len(self._texts)} hardcoded corpus embeddings…")
            self._embeddings = ModelRegistry.embed().encode(
                self._texts, convert_to_tensor=True, show_progress_bar=False
            )
        return self._embeddings

    def _retrieve_hardcoded(
        self, claim_text: str, top_k: int
    ) -> Tuple[List[Dict], bool]:
        claim_emb  = ModelRegistry.embed().encode(claim_text, convert_to_tensor=True)
        corpus_emb = self._get_hardcoded_embeddings()
        scores     = util.cos_sim(claim_emb, corpus_emb)[0].tolist()

        ranked  = sorted(zip(scores, self.CORPUS), key=lambda x: x[0], reverse=True)
        results = []
        for sim_score, entry in ranked[:top_k]:
            if sim_score < SIM_THRESHOLD:
                break
            results.append({
                # ── Internal fields (NLI needs these) ────────────────────────
                "text":             entry.get("text", ""),
                "source_label":     entry.get("publisher", ""),
                "source_url":       entry.get("source_url", ""),
                # ── Display fields ────────────────────────────────────────────
                "article_title":    entry.get("article_title", ""),
                "publisher":        entry.get("publisher", ""),
                "date_published":   entry.get("date_published", ""),
                # ── Scoring ───────────────────────────────────────────────────
                "similarity_score": round(float(sim_score), 4),
                "found":            True,
                "source_type":      "hardcoded",
            })

        return results, len(results) > 0