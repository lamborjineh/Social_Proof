"""
SocialProof — LessonTriggerService
services/lesson_trigger.py

Separated from services/comparison.py per docx §5.2.
Converts ComparisonEngine output into a list of lesson triggers with
the correct difficulty level for the user's current skill progress.

compute_triggers() is the single public entry point.
It applies trigger rules from docx §4.1 Step 6:

  Rule 1 — Skipped claim identification step
  Rule 2 — Rated source differently from system (medium/high confidence)
  Rule 3 — Did not detect bias that system found
  Rule 4 — Skipped evidence assessment step
  Rule 5 — Score diverged by 30+ points from system score
  Rule 6 — High confidence + wrong label (overconfidence)
  Rule 7 — Any re-evaluation requested

Lesson keys are resolved dynamically from the lessons table, so new
difficulty tiers added by admins are picked up automatically.

Called from routers/user_evaluation.py after ComparisonEngine.compare().
Results are inserted into lessons_triggered.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

import sqlalchemy as sa
from sqlalchemy.orm import Session


# ── Trigger rules ─────────────────────────────────────────────────────────────

def compute_triggers(
    comparison_result: Dict[str, Any],
    user_eval_data:    Dict[str, Any],
    db_session:        Optional[Session] = None,
) -> List[Dict[str, str]]:
    """
    Convert a ComparisonEngine result into a list of lesson trigger objects.

    Args:
        comparison_result: Output from ComparisonEngine.compare().
        user_eval_data:    Dict with keys:
            - skipped_steps     List[str]
            - confidence_level  str | None
            - source_credible   str | None   ("yes" | "no" | "unsure")
            - bias_detected     bool | None
            - user_label        str | None
            - user_score        int
        db_session:        Optional open SQLAlchemy Session for reading
                           user_skill_progress to pick the right difficulty.
                           If None, defaults to "beginner".

    Returns:
        List of dicts: [{lesson_key, trigger_reason, difficulty}]
    """
    triggers: List[Dict[str, str]] = []

    skipped_steps    = user_eval_data.get("skipped_steps") or []
    confidence       = user_eval_data.get("confidence_level")
    source_credible  = user_eval_data.get("source_credible")
    bias_detected    = user_eval_data.get("bias_detected")
    user_label       = user_eval_data.get("user_label")
    user_score       = user_eval_data.get("user_score", 50)
    user_id          = user_eval_data.get("user_id")

    system_label     = comparison_result.get("system_label", "Uncertain")
    missed_bias      = comparison_result.get("missed_bias", False)
    missed_claims    = comparison_result.get("missed_claims", False)
    source_mismatch  = comparison_result.get("source_mismatch", False)
    score_diff       = comparison_result.get("score_diff", 0)
    label_match      = comparison_result.get("label_match", True)

    # Build per-topic skill level lookup (defaults to beginner if no DB data)
    skill_levels: Dict[str, str] = _get_skill_levels(db_session, user_id)

    # ── Rule 1: Skipped claim identification step ─────────────────────────────
    if missed_claims or "claims" in skipped_steps:
        topic = "claim_detection"
        triggers.append({
            "lesson_key":     _pick_lesson_key(topic, skill_levels, db_session),
            "trigger_reason": (
                "You skipped the claim identification step. "
                "Isolating what is being claimed is the first step in any fact-check."
            ),
            "difficulty":     skill_levels.get(topic, "beginner"),
        })

    # ── Rule 2: Source rated differently from system (medium/high confidence) ─
    if source_mismatch and confidence in ("medium", "high"):
        topic = "source_verification"
        triggers.append({
            "lesson_key":     _pick_lesson_key(topic, skill_levels, db_session),
            "trigger_reason": (
                f"You rated the source as credible (with {confidence} confidence), "
                "but several credibility signals for this domain — such as MBFC bias "
                "rating, factual-reporting history, or domain age — suggest caution. "
                "This lesson helps you build a repeatable source-checking routine."
            ),
            "difficulty":     skill_levels.get(topic, "beginner"),
        })

    # ── Rule 3: Did not detect bias that system found ─────────────────────────
    if missed_bias:
        topic = "bias_detection"
        triggers.append({
            "lesson_key":     _pick_lesson_key(topic, skill_levels, db_session),
            "trigger_reason": (
                "Linguistic analysis flagged patterns associated with emotional "
                "or manipulative framing in this content that were not noted during "
                "your evaluation. Recognising these patterns is one of the most "
                "important and transferable media literacy skills."
            ),
            "difficulty":     skill_levels.get(topic, "beginner"),
        })

    # ── Rule 4: Skipped evidence assessment step ──────────────────────────────
    if "evidence" in skipped_steps:
        topic = "evidence_evaluation"
        triggers.append({
            "lesson_key":     _pick_lesson_key(topic, skill_levels, db_session),
            "trigger_reason": (
                "You skipped the evidence assessment step. "
                "Checking whether claims are backed by verifiable evidence — "
                "not just asserted — separates fact from opinion."
            ),
            "difficulty":     skill_levels.get(topic, "beginner"),
        })

    # ── Rule 5: Score diverged by 30+ points ─────────────────────────────────
    if abs(score_diff) >= 30:
        direction = "more lenient" if score_diff > 0 else "more critical"
        topic = "general"
        triggers.append({
            "lesson_key":     _pick_lesson_key(topic, skill_levels, db_session),
            "trigger_reason": (
                f"Your credibility score differed significantly from the "
                f"evidence-based credibility indicators for this content — "
                f"you were notably {direction}. "
                "This lesson covers calibrated skepticism and how to anchor "
                "your assessment to verifiable signals rather than intuition."
            ),
            "difficulty":     skill_levels.get(topic, "beginner"),
        })

    # ── Rule 6: High confidence + label mismatch (overconfidence signal) ──────
    # Note: system_label is a pedagogical scaffold, not a ground-truth verdict.
    # The trigger fires on the pattern (high confidence + low thoroughness),
    # not to tell the user they were "wrong". The trigger_reason intentionally
    # does NOT reference the system's conclusion.
    if confidence == "high" and not label_match and user_label is not None:
        topic = "general"
        # Avoid duplicate with Rule 5
        already_added = any(t["lesson_key"].startswith("general_") for t in triggers)
        if not already_added:
            triggers.append({
                "lesson_key":     _pick_lesson_key(topic, skill_levels, db_session),
                "trigger_reason": (
                    f"You submitted your verdict ('{user_label}') with high confidence "
                    "but several evaluation steps were skipped or lightly completed. "
                    "High confidence without thorough checking is a common pattern in "
                    "confirmation bias — this lesson walks through how to slow down "
                    "and stress-test your own reasoning."
                ),
                "difficulty":     skill_levels.get(topic, "beginner"),
            })

    # ── Deduplicate (keep first occurrence per lesson_key) ────────────────────
    seen: set = set()
    deduped: List[Dict[str, str]] = []
    for t in triggers:
        if t["lesson_key"] not in seen:
            seen.add(t["lesson_key"])
            deduped.append(t)

    return deduped


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick_lesson_key(
    topic: str,
    skill_levels: Dict[str, str],
    db_session: Optional[Session] = None,
) -> str:
    """
    Return the lesson_key for the user's current skill level in a topic.

    Looks up the lessons table dynamically so new lessons added by admins
    are picked up immediately without code changes.  Falls back to the
    conventional naming pattern (topic_level) if no DB row is found.
    """
    level = skill_levels.get(topic, "beginner")
    fallback = f"{topic}_{level}"

    if db_session is None:
        return fallback

    try:
        row = db_session.execute(
            sa.text(
                "SELECT lesson_key FROM lessons "
                "WHERE topic = :topic AND difficulty = :level "
                "  AND is_published = 1 "
                "ORDER BY sort_order ASC, id ASC "
                "LIMIT 1"
            ),
            {"topic": topic, "level": level},
        ).fetchone()
        if row:
            return row.lesson_key
    except Exception:
        pass

    return fallback


def _get_skill_levels(
    db_session: Optional[Session],
    user_id: Optional[int],
) -> Dict[str, str]:
    """
    Look up the user's current skill level per topic from user_skill_progress.
    Returns a dict {topic: level}. Defaults to 'beginner' for missing topics.
    """
    defaults = {
        "claim_detection":    "beginner",
        "source_verification": "beginner",
        "bias_detection":     "beginner",
        "evidence_evaluation": "beginner",
        "general":            "beginner",
    }

    if db_session is None or not user_id:
        return defaults

    try:
        rows = db_session.execute(
            sa.text(
                "SELECT topic, current_level FROM user_skill_progress "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        ).fetchall()
        for row in rows:
            defaults[row.topic] = row.current_level
    except Exception:
        pass  # Return defaults if table doesn't exist yet

    return defaults

