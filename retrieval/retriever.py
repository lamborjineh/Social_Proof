"""
retrieval/retriever.py
Evidence retrieval using BGE-M3 hybrid search (dense + sparse).
  - DOMAIN_DIVERSE_K: 5 → 6 for news/stats/mil pipelines.
      Gives MMR (added below) more candidates per domain to diversify from.

  - MMR (Maximal Marginal Relevance) added as Stage 3 in search().
      After cross-encoder reranking, the top results can still be near-identical
      articles saying the same thing. MMR penalises results too similar to
      already-selected ones. Lambda=0.7 (70% relevance, 30% diversity).
      New helpers: _mmr() and _get_candidate_embeddings().

  - mil pipeline added alongside news/stats/factcheck in _load().
      Only runs if a "mil" index has been built (explainers, media analysis,
      opinion pieces). Gracefully absent if the index file doesn't exist.

  - Merge order for non-numeric queries changed:
      news + mil + stats + factcheck (news and context-rich MIL first).

Model: BAAI/bge-m3 (Chen et al., 2024)
MMR:   Carbonell & Goldstein (1998) — The Use of MMR, Diversity-Based
       Reranking for Reordering Documents and Producing Summaries.
       ACM SIGIR. https://dl.acm.org/doi/10.1145/290941.291025
"""

import numpy as np
import json
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from corpus.source_registry import STATS_DOMAINS
from retrieval.utils import (
    index_files, recency_boost, trust_normalised,
    hybrid_score, is_numeric_query, pipeline_timer,
)
from retrieval.reranker import rerank as _rerank

MODEL_NAME_BASE  = "BAAI/bge-m3"
_FINETUNED_PATH  = Path(__file__).parent.parent / "models" / "bge_ph_finetuned"
MODEL_NAME       = str(_FINETUNED_PATH) if _FINETUNED_PATH.exists() else MODEL_NAME_BASE

# ── Retrieval config ──────────────────────────────────────────────────────────
# v4.0 MIL: Lowered 0.30 → 0.20 for wider recall.
# Fact-checking needed precision: only return something if it's probably about
# the exact claim. MIL needs recall: a loosely related article on media
# framing or source credibility is still valuable context for a learner.
RELEVANCE_THRESHOLD = 0.20

# v4.0 MIL: Raised 5 → 6 for news + stats + mil pipelines.
# Gives MMR (added in search()) more candidates per domain to diversify from.
# If only 5 candidates enter MMR and 3 are near-duplicates, you're left with
# 2 real articles. 6 per domain means more material to select from.
DOMAIN_DIVERSE_K             = 6   # news + stats + mil pipelines
DOMAIN_DIVERSE_K_FACTCHECK   = 8   # factcheck: richer pool for nuanced claims (unchanged)

# MAX_PER_DOMAIN: legacy cap used ONLY in the "all" fallback index pipeline.
MAX_PER_DOMAIN      = 3

SPARSE_WEIGHT       = 0.3

# ── MMR config ────────────────────────────────────────────────────────────────
# Lambda: 1.0 = pure relevance, 0.0 = pure diversity.
#
# MMR_LAMBDA_CLAIM = 0.7 — used when searching against the user's typed claim
#   (Step 1). 70% relevance, 30% diversity. Keeps results tightly on-topic
#   since the claim is a precise, user-authored query.
#
# MMR_LAMBDA_SUBMISSION = 0.5 — used when searching against the raw submission
#   content (corroboration / Section 1 cross-check). Equal weight between
#   relevance and diversity. The submission text is longer and less focused, so
#   a looser lambda casts a wider net across perspectives — good for surfacing
#   corroborating or contrasting sources the learner can compare.
MMR_LAMBDA_CLAIM      = 0.7   # claim search: relevance-first
MMR_LAMBDA_SUBMISSION = 0.5   # submission search: broader, more diverse
MMR_LAMBDA            = MMR_LAMBDA_CLAIM  # backward-compat default

try:
    import faiss as _faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False


def _load_one_index(pipeline: str):
    faiss_path, npy_path, meta_path, type_path = index_files(pipeline)

    if not meta_path.exists():
        return None

    with open(meta_path, "r", encoding="utf-8") as f:
        metadata = json.load(f)

    index_type = type_path.read_text().strip() if type_path.exists() else "numpy"

    if index_type == "faiss" and FAISS_AVAILABLE and faiss_path.exists():
        faiss_index = _faiss.read_index(str(faiss_path))
        return faiss_index, None, metadata, "faiss"
    elif npy_path.exists():
        embeddings = np.load(npy_path)
        return None, embeddings, metadata, "numpy"
    else:
        return None


class Retriever:
    """
    Loads pre-built BGE-M3 embedding indices and performs hybrid search.
    """

    def __init__(self):
        self.model       = None
        self._use_bge    = False
        self._indices: dict = {}
        self.stale_index_warning: Optional[str] = None
        self._load()

    def _load(self):
        try:
            from FlagEmbedding import BGEM3FlagModel
            print(f"[Retriever] Loading BGE-M3: {MODEL_NAME}")
            self.model    = BGEM3FlagModel(MODEL_NAME, use_fp16=False)
            self._use_bge = True
            print("[Retriever] BGE-M3 loaded — hybrid dense+sparse mode active.")
        except ImportError:
            print("[Retriever] FlagEmbedding not installed. Using SentenceTransformer.")
            from sentence_transformers import SentenceTransformer
            self.model    = SentenceTransformer(MODEL_NAME_BASE)
            self._use_bge = False

        loaded_any = False
        # v4.0: mil pipeline loaded alongside news/stats/factcheck.
        # Only activates if a "mil" index has been built — safe to skip.
        for pipeline in ["news", "stats", "factcheck", "mil"]:
            result = _load_one_index(pipeline)
            if result is not None:
                self._indices[pipeline] = result
                fi, _, meta, itype = result
                count = fi.ntotal if fi else len(meta)
                print(f"[Retriever] [{pipeline}] {count} vectors ({itype})")
                loaded_any = True

        result = _load_one_index("all")
        if result is not None:
            self._indices["all"] = result
            fi, _, meta, itype = result
            count = fi.ntotal if fi else len(meta)
            print(f"[Retriever] [all] {count} vectors ({itype}) — fallback index")
            loaded_any = True

        if not loaded_any:
            raise FileNotFoundError(
                "No embedding index found.\n"
                "Run: python retrieval/build_index.py"
            )

        # BUG 9 FIX: Stale index detection.
        self._check_index_staleness()

    def _check_index_staleness(self):
        """
        BUG 9: Compare FAISS index file mtime against newest sentence in the DB.
        Sets self.stale_index_warning if the index is more than 24h stale.
        Safe to fail silently — just logs a warning, never raises.
        """
        import os
        try:
            oldest_index_mtime = float("inf")
            for pipeline in ["news", "stats", "factcheck", "all"]:
                faiss_path, npy_path, meta_path, _ = index_files(pipeline)
                for p in (faiss_path, npy_path):
                    if p.exists():
                        mtime = p.stat().st_mtime
                        if mtime < oldest_index_mtime:
                            oldest_index_mtime = mtime

            if oldest_index_mtime == float("inf"):
                return

            from corpus.db import get_connection
            conn = get_connection()
            c    = conn.cursor()
            try:
                c.execute("SELECT MAX(created_at) FROM sentences")
                row = c.fetchone()
                newest_db_ts = row[0] if row and row[0] else None
            except Exception:
                newest_db_ts = None
            conn.close()

            if newest_db_ts is None:
                return

            from datetime import datetime as _dt
            try:
                if isinstance(newest_db_ts, (int, float)):
                    newest_db_dt = _dt.fromtimestamp(newest_db_ts)
                else:
                    newest_db_dt = _dt.fromisoformat(str(newest_db_ts)[:19])
            except Exception:
                return

            import time as _time
            index_dt    = _dt.fromtimestamp(oldest_index_mtime)
            stale_hours = (newest_db_dt - index_dt).total_seconds() / 3600.0

            if stale_hours > 24:
                msg = (
                    f"[Retriever] ⚠ STALE INDEX WARNING: The embedding index "
                    f"is ~{stale_hours:.0f}h behind the corpus DB. "
                    f"New sentences (seeded after {index_dt.strftime('%Y-%m-%d %H:%M')}) "
                    f"will NOT be retrieved until you rebuild.\n"
                    f"  Run: python retrieval/build_index.py --rebuild"
                )
                print(msg)
                self.stale_index_warning = msg
        except Exception as _e:
            print(f"[Retriever] Staleness check skipped: {_e}")

    def _encode_query(self, claim: str) -> Tuple[np.ndarray, Optional[dict]]:
        if self._use_bge:
            output = self.model.encode(
                [claim.strip()],
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
                batch_size=1,
            )
            dense_vec       = np.array(output["dense_vecs"][0], dtype="float32")
            lexical_weights = output["lexical_weights"][0]
        else:
            dense_vec = self.model.encode(
                [claim.strip()],
                normalize_embeddings=True,
                convert_to_numpy=True,
            )[0].astype("float32")
            lexical_weights = None

        norm = np.linalg.norm(dense_vec)
        if norm > 0:
            dense_vec = dense_vec / norm

        return dense_vec, lexical_weights

    def _sparse_boost(self, lexical_weights: dict, sentence_text: str) -> float:
        if not lexical_weights:
            return 0.0
        sentence_lower = sentence_text.lower()
        raw = sum(
            float(weight)
            for token, weight in lexical_weights.items()
            if isinstance(token, str) and token.lower() in sentence_lower
        )
        return min(raw * 0.05, 0.15)

    def _search_index(self, pipeline: str, dense_vec: np.ndarray,
                      lexical_weights: Optional[dict],
                      claim: str, k: int,
                      numeric_boost: bool = False,
                      apply_domain_cap: bool = False) -> List[Dict]:
        """
        Search one pipeline index. Returns ALL candidates above RELEVANCE_THRESHOLD,
        scored and sorted — domain diversification is handled upstream by _diversify().

        apply_domain_cap=True activates the legacy MAX_PER_DOMAIN hard cap, used
        only for the "all" fallback index where the per-pipeline split isn't available.

        Fix #6: dense_score < RELEVANCE_THRESHOLD → skip immediately, before
        sparse boost. This prevents low-quality matches from being inflated.

        Fix #13: date_published is included in metadata so hybrid_score()
        can use actual publish date for recency decay.
        """
        if pipeline not in self._indices:
            return []

        faiss_index, embeddings, metadata, itype = self._indices[pipeline]
        # Fetch a generous pool: 10× k so domain diversification has material to work with.
        fetch_k = min(k * 10, len(metadata))

        if faiss_index is not None:
            scores, idxs = faiss_index.search(dense_vec.reshape(1, -1), fetch_k)
            scored_pairs = list(zip(idxs[0], scores[0]))
        else:
            sims     = np.dot(embeddings, dense_vec)
            top_idxs = np.argsort(sims)[::-1][:fetch_k]
            scored_pairs = [(int(i), float(sims[i])) for i in top_idxs]

        candidates    = []
        domain_counts = {}

        for idx, dense_score in scored_pairs:
            # Fix #6: strict threshold check BEFORE any boosting
            if dense_score < RELEVANCE_THRESHOLD:
                break
            if idx < 0 or idx >= len(metadata):
                continue

            meta   = metadata[idx]
            domain = meta["domain"]

            # Legacy cap: only active for the "all" fallback pipeline
            if apply_domain_cap and domain_counts.get(domain, 0) >= MAX_PER_DOMAIN:
                continue

            sparse   = self._sparse_boost(lexical_weights, meta["text"])
            semantic = min(1.0, max(0.0, float(dense_score) + SPARSE_WEIGHT * sparse))

            # Fix #13: pass date_published (may be None if metadata predates v3.3)
            final = hybrid_score(
                semantic, domain, meta["url"],
                numeric_boost=numeric_boost,
                date_published=meta.get("date_published"),
            )

            if apply_domain_cap:
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

            candidates.append({
                "sentence_id":     meta["id"],
                "text":            meta["text"],
                "domain":          domain,
                "url":             meta["url"],
                "similarity":      round(final, 4),
                "pipeline_type":   meta.get("pipeline_type", pipeline),
                "numeric_density": meta.get("numeric_density", 0.0),
                "date_published":  meta.get("date_published"),
                "article_title":   meta.get("article_title", ""),
                "publisher":       meta.get("publisher", domain),
            })

        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        return candidates

    def _diversify(self, candidates: List[Dict], per_domain_k: int = DOMAIN_DIVERSE_K, pipeline: str = None) -> List[Dict]:
        if pipeline == "factcheck" and per_domain_k == DOMAIN_DIVERSE_K:
            per_domain_k = DOMAIN_DIVERSE_K_FACTCHECK
        """
        Domain-diversified pooling: for each domain, keep the top per_domain_k
        candidates (already sorted by similarity). Pool all domain buckets and
        return sorted by similarity descending.

        This ensures every domain that has relevant evidence gets equal
        representation in the reranker pool, regardless of domain size in the
        FAISS index. A domain with 10k sentences and a domain with 50 sentences
        both contribute up to per_domain_k candidates if they clear the threshold.

        The reranker then decides final ordering from this balanced pool.
        """
        buckets: Dict[str, List[Dict]] = defaultdict(list)

        # Input is sorted by similarity desc — first N per domain are the best N
        for c in candidates:
            domain = c["domain"]
            if len(buckets[domain]) < per_domain_k:
                buckets[domain].append(c)

        pool = [c for bucket in buckets.values() for c in bucket]
        pool.sort(key=lambda x: x["similarity"], reverse=True)

        domain_summary = {d: len(b) for d, b in buckets.items()}
        print(
            f"[Retriever] _diversify: {len(candidates)} candidates → "
            f"{len(pool)} pooled across {len(buckets)} domains "
            f"(top-{per_domain_k} each) | {domain_summary}"
        )

        return pool

    def _search_index_per_domain(
        self,
        pipeline: str,
        dense_vec: np.ndarray,
        lexical_weights: Optional[dict],
        claim: str,
        numeric_boost: bool = False,
        per_domain_k: int = DOMAIN_DIVERSE_K,
    ) -> List[Dict]:
        """
        v3.6: Per-domain guaranteed retrieval for one pipeline index.

        Problem this solves:
            The v3.5 approach queried FAISS once per pipeline with a large
            fetch_k, then applied _diversify() to cap per domain. But FAISS
            returns the globally nearest vectors — a large domain (e.g.
            rappler.com, 200 sentences) fills most of the top-N slots, leaving
            small domains (psa.gov.ph, 20 sentences) with zero rows in the raw
            result. _diversify() had nothing to cap for the small domain.

        This method:
            1. Builds a per-domain index map from the pipeline's metadata
               (done once per pipeline, O(N) scan).
            2. For each domain, scores only its rows against the query vector
               and keeps the top per_domain_k above RELEVANCE_THRESHOLD.
            3. Returns the flat list of all domain buckets combined.

        Every domain is guaranteed up to per_domain_k slots if it has any
        sentences above threshold. Volume no longer determines eligibility.

        Note on FAISS sub-search:
            FAISS IndexFlatIP does not support row-subset search natively.
            For the per-domain case we use numpy dot-product over the domain's
            row subset — the index is still used for the "all" fallback which
            is volume-dominant and benefits from FAISS speed. Per-pipeline
            indices are small enough (thousands of rows) that numpy is fast.
        """
        if pipeline not in self._indices:
            return []

        faiss_index, embeddings, metadata, itype = self._indices[pipeline]

        # ── Step 1: build domain → [row_indices] map ─────────────────────────
        # O(N) over metadata; result is cached on the index tuple for reuse
        # within the same Retriever lifetime via a side-dict on self.
        cache_key = f"_domain_map_{pipeline}"
        domain_map: Dict[str, List[int]] = getattr(self, cache_key, None)
        if domain_map is None:
            domain_map = defaultdict(list)
            for row_idx, meta in enumerate(metadata):
                domain_map[meta["domain"]].append(row_idx)
            setattr(self, cache_key, domain_map)

        # ── Step 2: For each domain, score its rows and keep top per_domain_k ─
        all_domain_results: List[Dict] = []

        # We need a dense embedding matrix for numpy dot-product sub-search.
        # If the index was loaded as FAISS (no embeddings matrix in memory),
        # reconstruct it once per pipeline and cache it.
        embed_cache_key = f"_embed_cache_{pipeline}"
        embed_matrix: Optional[np.ndarray] = getattr(self, embed_cache_key, None)
        if embed_matrix is None and faiss_index is not None:
            try:
                n = faiss_index.ntotal
                d = faiss_index.d
                embed_matrix = np.zeros((n, d), dtype="float32")
                faiss_index.reconstruct_n(0, n, embed_matrix)
                setattr(self, embed_cache_key, embed_matrix)
                print(f"[Retriever] [{pipeline}] Reconstructed {n}×{d} embed matrix for per-domain search.")
            except Exception as e:
                # reconstruct_n not available on all index types (e.g. IVF).
                # Fall back to the original _search_index for this pipeline.
                print(f"[Retriever] [{pipeline}] reconstruct_n failed ({e}); falling back to _search_index.")
                return self._search_index(
                    pipeline, dense_vec, lexical_weights, claim,
                    k=per_domain_k * len(domain_map),
                    numeric_boost=numeric_boost,
                    apply_domain_cap=False,
                )
        elif embed_matrix is None:
            # numpy path — embeddings already in memory
            embed_matrix = embeddings

        for domain, row_indices in domain_map.items():
            if not row_indices:
                continue

            # Score this domain's rows
            rows       = embed_matrix[row_indices]          # shape (D_size, dim)
            sims       = np.dot(rows, dense_vec)            # shape (D_size,)
            top_local  = np.argsort(sims)[::-1][:per_domain_k * 2]  # extra headroom

            domain_hits: List[Dict] = []
            for local_i in top_local:
                dense_score = float(sims[local_i])
                if dense_score < RELEVANCE_THRESHOLD:
                    break  # sorted desc — no point continuing
                global_idx = row_indices[local_i]
                meta       = metadata[global_idx]

                sparse   = self._sparse_boost(lexical_weights, meta["text"])
                semantic = min(1.0, max(0.0, dense_score + SPARSE_WEIGHT * sparse))
                final    = hybrid_score(
                    semantic, domain, meta["url"],
                    numeric_boost=numeric_boost,
                    date_published=meta.get("date_published"),
                )

                domain_hits.append({
                    "sentence_id":     meta["id"],
                    "text":            meta["text"],
                    "domain":          domain,
                    "url":             meta["url"],
                    "similarity":      round(final, 4),
                    "pipeline_type":   meta.get("pipeline_type", pipeline),
                    "numeric_density": meta.get("numeric_density", 0.0),
                    "date_published":  meta.get("date_published"),
                    "article_title":   meta.get("article_title", ""),
                    "publisher":       meta.get("publisher", domain),
                })
                if len(domain_hits) >= per_domain_k:
                    break

            all_domain_results.extend(domain_hits)

        all_domain_results.sort(key=lambda x: x["similarity"], reverse=True)

        domain_summary = defaultdict(int)
        for r in all_domain_results:
            domain_summary[r["domain"]] += 1
        print(
            f"[Retriever] [{pipeline}] per-domain search: "
            f"{len(domain_map)} domains, {len(all_domain_results)} candidates "
            f"(top-{per_domain_k} each) | {dict(domain_summary)}"
        )

        return all_domain_results

    def _get_candidate_embeddings(self, candidates: List[Dict]) -> Optional[np.ndarray]:
        """
        Encode candidate texts for MMR inter-candidate similarity computation.
        Reuses the already-loaded BGE-M3 model — no second model load.
        Returns None if encoding fails; MMR is then gracefully skipped.
        """
        try:
            texts = [c["text"] for c in candidates]
            if self._use_bge:
                output = self.model.encode(
                    texts,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False,
                    batch_size=16,
                )
                vecs = np.array(output["dense_vecs"], dtype="float32")
            else:
                vecs = self.model.encode(
                    texts,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                ).astype("float32")
            return vecs
        except Exception as e:
            print(f"[Retriever] MMR embedding failed: {e}. Skipping MMR.")
            return None

    def _mmr(
        self,
        candidates: List[Dict],
        embed_matrix: np.ndarray,
        top_k: int,
        lam: float = MMR_LAMBDA,
    ) -> List[Dict]:
        """
        Maximal Marginal Relevance reranking for result diversity.

        Selects results one at a time. Each pick maximises:
            lam * relevance_score  -  (1 - lam) * max_similarity_to_already_selected

        This prevents near-duplicate articles from all landing in the final
        top-k. Lambda=0.7: still relevance-first, but won't show the learner
        three versions of the same article.

        Args:
            candidates:   Dicts already reranked by cross-encoder (same order as rows).
            embed_matrix: Dense embeddings for each candidate (N × dim).
            top_k:        How many results to select.
            lam:          MMR_LAMBDA = 0.7 default.

        Academic basis:
            Carbonell & Goldstein (1998). The Use of MMR, Diversity-Based
            Reranking for Reordering Documents and Producing Summaries.
            ACM SIGIR. https://dl.acm.org/doi/10.1145/290941.291025
        """
        if len(candidates) <= top_k:
            return candidates

        # Normalise embeddings for cosine similarity.
        norms  = np.linalg.norm(embed_matrix, axis=1, keepdims=True)
        norms  = np.where(norms == 0, 1e-9, norms)
        normed = embed_matrix / norms

        # Relevance scores: prefer rerank_score (cross-encoder), fall back to similarity.
        # Normalise to [0, 1] for stable lambda weighting.
        rel_scores = np.array([
            float(c.get("rerank_score", c.get("similarity", 0)))
            for c in candidates
        ])
        r_min, r_max = rel_scores.min(), rel_scores.max()
        if r_max > r_min:
            rel_scores = (rel_scores - r_min) / (r_max - r_min)

        selected_indices = []
        remaining        = list(range(len(candidates)))

        for _ in range(min(top_k, len(candidates))):
            if not remaining:
                break
            if not selected_indices:
                # First pick: highest relevance — same as reranker order.
                best = max(remaining, key=lambda i: rel_scores[i])
            else:
                sel_vecs = normed[selected_indices]   # shape (S, dim)
                best, best_score = None, -float("inf")
                for i in remaining:
                    max_sim   = float(np.dot(normed[i], sel_vecs.T).max())
                    mmr_score = lam * rel_scores[i] - (1 - lam) * max_sim
                    if mmr_score > best_score:
                        best, best_score = i, mmr_score
            selected_indices.append(best)
            remaining.remove(best)

        print(
            f"[Retriever] MMR: {len(candidates)} candidates → "
            f"{len(selected_indices)} selected (λ={lam})"
        )
        return [candidates[i] for i in selected_indices]

    def search(self, claim: str, k: int = 7, mode: str = "claim") -> List[Dict]:
        """
        Find top-k relevant and diverse evidence sentences for a claim.

        Args:
            claim:  The query text (user claim or raw submission content).
            k:      Number of final results to return.
            mode:   "claim"      — tighter MMR (λ=0.7), relevance-first.
                                   Used when the query is the user's Step 1 claim.
                    "submission" — looser MMR (λ=0.5), more diversity.
                                   Used when the query is the raw submission content
                                   (corroboration / cross-checking section). Casts
                                   a wider net across perspectives since the input
                                   is longer and less focused than a single claim.

        v4.0 flow:
          1. Per-domain-per-pipeline retrieval (unchanged from v3.6).
             news, stats, factcheck, mil pipelines queried independently.
             RELEVANCE_THRESHOLD now 0.20 (wider net vs 0.30).
          2. Merge + dedup across pipelines (unchanged).
          3. Cross-encoder reranking — returns top 10 (was top k).
          4. NEW — MMR diversity pass:
             Re-encode the reranked candidates and apply Maximal Marginal
             Relevance to select final top-k. Articles that are near-identical
             are penalised so the learner sees genuinely different perspectives.

        Fix #9: wrapped in pipeline_timer for latency logging.
        """
        if not claim or len(claim.strip()) < 5:
            return []

        try:
            from corpus.db import log_event as _log
        except Exception:
            _log = None

        with pipeline_timer("retrieval", log_fn=_log) as t:
            dense_vec, lexical_weights = self._encode_query(claim)
            numeric_q = is_numeric_query(claim)

            per_pipeline_available = any(
                p in self._indices for p in ["news", "stats", "factcheck"]
            )

            if per_pipeline_available:
                # v3.6: query each pipeline with guaranteed per-domain slots.
                results_news  = self._search_index_per_domain(
                    "news",      dense_vec, lexical_weights, claim,
                    numeric_boost=numeric_q,
                    per_domain_k=DOMAIN_DIVERSE_K,
                )
                results_stats = self._search_index_per_domain(
                    "stats",     dense_vec, lexical_weights, claim,
                    numeric_boost=numeric_q,
                    per_domain_k=DOMAIN_DIVERSE_K,
                )
                results_fact  = self._search_index_per_domain(
                    "factcheck", dense_vec, lexical_weights, claim,
                    numeric_boost=numeric_q,
                    per_domain_k=DOMAIN_DIVERSE_K,
                )
                # v4.0 MIL: mil pipeline — explainers, media analysis, opinion.
                # Only runs if a "mil" index has been built; otherwise empty list.
                results_mil = self._search_index_per_domain(
                    "mil", dense_vec, lexical_weights, claim,
                    numeric_boost=False,
                    per_domain_k=DOMAIN_DIVERSE_K,
                ) if "mil" in self._indices else []

                # v4.0 MIL merge order: news + mil first (contextually richest),
                # then stats + factcheck. Numeric queries still put stats first.
                if numeric_q:
                    merge_order = results_stats + results_news + results_fact + results_mil
                else:
                    merge_order = results_news + results_mil + results_stats + results_fact

                seen           = set()
                all_candidates = []
                for r in sorted(merge_order, key=lambda x: x["similarity"], reverse=True):
                    key = r["text"][:80]
                    if key not in seen:
                        seen.add(key)
                        all_candidates.append(r)

                rerank_pool = all_candidates[: k * 3]

                print(
                    f"[Retriever] search pool: {len(all_candidates)} unique candidates "
                    f"across {len({r['domain'] for r in all_candidates})} domains → "
                    f"passing top {len(rerank_pool)} to reranker."
                )

            else:
                # Fallback "all" index: use legacy MAX_PER_DOMAIN cap
                rerank_pool = self._search_index(
                    "all", dense_vec, lexical_weights, claim, k, numeric_q,
                    apply_domain_cap=True,
                )

        # ── Stage 2: Cross-encoder reranking ─────────────────────────────────
        # Returns top 10 (reranker default) to feed MMR with a larger pool.
        if not rerank_pool:
            return []
        reranked = _rerank(claim, rerank_pool, top_k=k * 2)

        # ── Stage 3: MMR diversity pass ───────────────────────────────────────
        # Re-encode reranked candidates and apply Maximal Marginal Relevance to
        # ensure the final top-k contains genuinely different content angles,
        # not near-duplicates of the same article rephrased.
        #
        # mode="submission" uses a looser lambda (0.5) so corroboration results
        # draw from a broader spread of sources — the submission text is longer
        # and less focused than a typed claim, so diversity matters more here.
        mmr_lambda = MMR_LAMBDA_SUBMISSION if mode == "submission" else MMR_LAMBDA_CLAIM
        if len(reranked) > k:
            embed_matrix = self._get_candidate_embeddings(reranked)
            if embed_matrix is not None:
                reranked = self._mmr(reranked, embed_matrix, top_k=k, lam=mmr_lambda)
            else:
                reranked = reranked[:k]
        else:
            reranked = reranked[:k]

        return reranked

    def has_index(self) -> bool:
        return bool(self._indices)


# ── Singleton accessor ────────────────────────────────────────────────────────
_retriever_instance = None

def get_retriever() -> Retriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = Retriever()
    return _retriever_instance


if __name__ == "__main__":
    r = Retriever()
    test_claim = "The Bangko Sentral ng Pilipinas raised interest rates in 2024"
    print(f"\nSearching for: '{test_claim}'\n")
    results = r.search(test_claim, k=5)
    if not results:
        print("No results. Scrape corpus first: python corpus/scraper.py --limit 200")
        print("Then build index: python retrieval/build_index.py")
    else:
        for i, res in enumerate(results, 1):
            print(f"[{i}] Score: {res['similarity']:.4f} | {res['domain']} | {res['pipeline_type']}")
            print(f"     {res['text'][:120]}...")
            print(f"     URL: {res['url']}")
            if res.get("date_published"):
                print(f"     Published: {res['date_published']}")
            print()