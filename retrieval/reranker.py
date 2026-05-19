"""
retrieval/reranker.py
Cross-encoder reranking for evidence relevance — upgraded to L-12.
Entity identity scoring integrated (v3.4).

Problem with bi-encoder (BGE-M3 dense) alone:
  Even with hybrid scoring, the initial retrieval ranks by approximate
  semantic + lexical overlap. A cross-encoder is more accurate because
  it reads the claim and evidence TOGETHER (not independently encoded)
  and scores their specific logical relationship.

  Additionally, semantic similarity alone does not distinguish "same event"
  from "same topic." "Duterte ICC" and "Bato dela Rosa ICC" are close in
  embedding space but are different contextual events. Entity identity
  scoring (v3.4) addresses this as a third reranking stage.

Solution — three-stage retrieval:
  Stage 1: BGE-M3 hybrid retrieval → top-30 candidates (fast, ~ms)
  Stage 2: Cross-encoder → re-score each (claim, sentence) pair (accurate, ~1-2s)
  Stage 3: Entity identity → adjust scores for actor/event alignment (~<10ms)
  Result:  Keep top-k by final blended score

Model: cross-encoder/ms-marco-MiniLM-L-12-v2
  Upgraded from L-6 to L-12 (12 transformer layers vs 6).
  More accurate relevance scoring for the same MS MARCO passage retrieval task.
  Speed difference on CPU: ~0.5s per query (7 pairs) — acceptable for a web app.
  Size: ~133MB

Model priority (auto-detected):
  1. models/crossencoder_ph_finetuned  — fine-tuned on PH claim-evidence pairs
  2. cross-encoder/ms-marco-MiniLM-L-12-v2  — base L-12 model (default)

Academic basis:
  Nogueira, R., & Cho, K. (2019). Passage re-ranking with BERT.
  arXiv:1901.04085. https://arxiv.org/abs/1901.04085

  Bajaj, P., Campos, D., Craswell, N., Deng, L., Gao, J., Liu, X., ...
  & Mitra, B. (2016). MS MARCO: A human generated machine reading
  comprehension dataset. arXiv:1611.09268.
  https://arxiv.org/abs/1611.09268
"""

from typing import List, Dict, Optional
from pathlib import Path

# ── Model auto-detection ──────────────────────────────────────────────────────
_ROOT           = Path(__file__).parent.parent
_FINETUNED_PATH = _ROOT / "models" / "crossencoder_ph_finetuned"
_BASE_MODEL     = "cross-encoder/ms-marco-MiniLM-L-12-v2"
RERANKER_MODEL  = str(_FINETUNED_PATH) if _FINETUNED_PATH.exists() else _BASE_MODEL

_reranker = None


def get_reranker():
    """Lazy-load the cross-encoder. Called on first rerank() invocation."""
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder
            print(f"[Reranker] Loading cross-encoder: {RERANKER_MODEL}")
            if RERANKER_MODEL != _BASE_MODEL:
                print("[Reranker] Using fine-tuned Philippine cross-encoder.")
            # max_length=512 — truncates very long evidence sentences safely
            _reranker = CrossEncoder(RERANKER_MODEL, max_length=512)
            print("[Reranker] Cross-encoder loaded (L-12 — higher accuracy than L-6).")
        except Exception as e:
            print(f"[Reranker] Could not load cross-encoder: {e}")
            print("[Reranker] Falling back to BGE-M3 ordering.")
            _reranker = None
    return _reranker


def rerank(
    claim: str,
    candidates: List[Dict],
    top_k: int = 10,
    apply_entity_scoring: bool = True,
) -> List[Dict]:
    """
    Rerank a list of candidate evidence sentences using the cross-encoder,
    followed by entity identity scoring (Stage 3, v3.4).

    Three-stage pipeline:
      Stage 1 (upstream): BGE-M3 hybrid retrieval → top-30 candidates
      Stage 2 (here):     Cross-encoder re-scores (claim, evidence) pairs
      Stage 3 (here):     Entity identity scoring adjusts for actor/event alignment

    Stage 2 — Cross-encoder:
      Reads (claim, evidence) as a single concatenated input and outputs a
      relevance score. Slower than bi-encoder cosine similarity but significantly
      more accurate for passage relevance.

    Stage 3 — Entity identity scoring:
      Applies apply_entity_rerank() from utils.py to adjust rerank_score by:
        +0.25 max bonus for entity overlap  (same actors/event anchors found)
        -0.20 max penalty for entity mismatch (key actors absent from text)
      This separates "same event" from "same topic":
        "Bato + ICC standoff" vs "Duterte + ICC detention" are semantically
        close but entity-mismatched → Duterte articles are penalised.
      Entity score and context_label are preserved on each dict for UI display.

    top_k raised 7 → 10:
        rerank() feeds MMR in retriever.search(), which selects the final
        diverse top-k from this pool. Returning 10 gives MMR more candidates.

    rerank_score preserved on every dict:
        MMR uses rerank_score as its relevance signal. Do not remove this field.

    Args:
        claim:                 The submitted claim text.
        candidates:            Evidence dicts from BGE-M3 retrieval.
        top_k:                 How many to return after reranking (default 10).
        apply_entity_scoring:  Whether to run Stage 3 entity adjustment.
                               Set False for pure numeric/stats claims where
                               entity filtering is irrelevant.

    Returns:
        Top-k candidates sorted by final score (descending).
        Each dict gains/updates:
          rerank_score    (float) — cross-encoder score (Stage 2)
          entity_score    (float) — entity identity score in [-1, +1] (Stage 3)
          context_label   (str)  — "same_event" | "related_topic" | "broad_match"
          similarity      (float) — entity-adjusted similarity (Stage 3)
        Falls back to BGE-M3 ordering if cross-encoder unavailable.

    Pipeline position:
        retriever.search()
          → [pool of k*3 candidates]
          → rerank()            ← YOU ARE HERE (Stages 2 + 3, returns top 10)
          → _mmr()              (selects diverse final top-k from those 10)
          → final result

    Performance note (CPU, 10 pairs):
        L-12 cross-encoder: ~550ms. Entity scoring: <10ms. MMR: ~50ms.

    Academic basis:
        Nogueira & Cho (2019) — Passage Re-ranking with BERT
        MS MARCO cross-encoder: trained on ~500K human-labeled query-passage pairs
    """
    if not candidates:
        return candidates

    reranker = get_reranker()

    # ── Stage 2: Cross-encoder scoring ───────────────────────────────────────
    if reranker is None:
        # No cross-encoder available — fall through to entity scoring on BGE-M3 order
        print("[Reranker] Cross-encoder unavailable. Using BGE-M3 order for Stage 2.")
        stage2_results = candidates[:top_k]
    else:
        pairs = [(claim, ev["text"]) for ev in candidates]
        try:
            scores = reranker.predict(pairs)
            for ev, score in zip(candidates, scores):
                ev["rerank_score"] = round(float(score), 4)
            stage2_results = sorted(
                candidates,
                key=lambda x: x.get("rerank_score", 0),
                reverse=True,
            )[:top_k]
        except Exception as e:
            print(f"[Reranker] Cross-encoder prediction failed: {e}. Using BGE-M3 order.")
            stage2_results = candidates[:top_k]

    # ── Stage 3: Entity identity scoring ─────────────────────────────────────
    if not apply_entity_scoring:
        # Skip entity scoring for pure numeric/stats claims
        for ev in stage2_results:
            ev.setdefault("entity_score", 0.0)
            ev.setdefault("context_label", "related_topic")
        return stage2_results

    try:
        from retrieval.utils import apply_entity_rerank, is_numeric_query
        # For numeric claims, entity scoring is less meaningful — skip penalty
        if is_numeric_query(claim):
            for ev in stage2_results:
                ev.setdefault("entity_score", 0.0)
                ev.setdefault("context_label", "related_topic")
            return stage2_results

        # Apply entity identity scoring — mutates dicts in-place, re-sorts
        stage3_results = apply_entity_rerank(stage2_results, claim)
        print(
            f"[Reranker] Stage 3 entity scoring applied. "
            f"Labels: { {ev.get('context_label') for ev in stage3_results} }"
        )
        return stage3_results

    except Exception as e:
        print(f"[Reranker] Entity scoring failed: {e}. Returning Stage 2 results.")
        return stage2_results
