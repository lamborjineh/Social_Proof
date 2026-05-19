"""
SocialProof — Router: Quiz & Practice
Endpoints:
  GET  /quiz                    — fetch randomised questions (filterable by topic)
  POST /quiz/attempt            — record a quiz attempt + return immediate feedback
  GET  /quiz/stats/{user_id}    — per-topic accuracy summary for a logged-in user

Implements System_Requirements §5.7 Quiz and Practice Module.
"""
from config import logger, QUIZ_QUESTIONS_PER_SESSION

from typing import Optional, Dict, Any
import random

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request, Header
from sqlalchemy.orm import Session

from database.models import engine, QuizQuestionORM, QuizAttemptORM, UserSkillProgressORM


def _unlock_mindmap_node(db: Session, user_id: int, node_id: str) -> None:
    """
    Insert a mindmap_progress row for the given user+node, silently skipping
    duplicates (INSERT IGNORE semantics via ON DUPLICATE KEY UPDATE).
    Safe to call unconditionally after any content completion event.
    """
    try:
        db.execute(
            sa.text("""
                INSERT INTO mindmap_progress (user_id, map_id, node_id, viewed_at)
                VALUES (:uid, 'main', :node_id, NOW())
                ON DUPLICATE KEY UPDATE viewed_at = viewed_at
            """),
            {"uid": user_id, "node_id": node_id},
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[MindmapUnlock] Failed for user={user_id} node={node_id}: {e}")
from schemas import QuizAttemptRequest, QuizAttemptResponse
from routers.auth import get_current_user, _verify as _quiz_verify

router = APIRouter()

# ── MIL skill metadata per topic (feature ⑨: "why this matters") ─────────────
# Returned with every quiz attempt so the frontend can display a skill chip
# telling the user exactly which competency they just practiced.
_TOPIC_SKILL = {
    "claim_detection": {
        "skill_used":        "claim_detection",
        "skill_label":       "Claim Detection",
        "skill_description": "Spotting specific, checkable assertions hidden inside everyday language",
    },
    "source_verification": {
        "skill_used":        "source_verification",
        "skill_label":       "Source Verification",
        "skill_description": "Tracing where information comes from and whether that origin is trustworthy",
    },
    "bias_detection": {
        "skill_used":        "bias_detection",
        "skill_label":       "Bias Detection",
        "skill_description": "Recognising emotional framing, loaded language, and selective emphasis",
    },
    "evidence_evaluation": {
        "skill_used":        "evidence_evaluation",
        "skill_label":       "Evidence Evaluation",
        "skill_description": "Judging whether sources actually prove what a claim asserts",
    },
    "general": {
        "skill_used":        "general_mil",
        "skill_label":       "Media & Information Literacy",
        "skill_description": "Applying critical thinking across the full fact-checking workflow",
    },
}


@router.get("/quiz/settings")
async def get_quiz_settings():
    """
    Return public quiz configuration so the frontend can display
    the correct session limit without hard-coding it.
    Admins change QUIZ_QUESTIONS_PER_SESSION in .env to adjust.
    """
    return {"questions_per_session": QUIZ_QUESTIONS_PER_SESSION}


@router.get("/quiz")
async def get_quiz_questions(
    topic:     Optional[str] = Query(None, description="Filter by topic"),
    lesson_id: Optional[int] = Query(None, description="Filter by lesson ID"),
    limit: int               = Query(QUIZ_QUESTIONS_PER_SESSION, ge=1, le=50, description="Number of questions"),
):
    """
    Return randomly sampled quiz questions.
    Filter by lesson_id to get questions tied to a specific lesson.
    Answers are shuffled server-side on every request.
    """
    db = Session(engine)
    try:
        query  = "SELECT * FROM quiz_questions WHERE (is_active IS NULL OR is_active = 1)"
        params: Dict[str, Any] = {}
        if lesson_id:
            query += " AND lesson_id = :lesson_id"
            params["lesson_id"] = lesson_id
        elif topic:
            query += " AND topic = :topic"
            params["topic"] = topic
        query += " ORDER BY RAND() LIMIT :limit"
        params["limit"] = limit
        rows = db.execute(sa.text(query), params).fetchall()
        questions = []
        for r in rows:
            q = dict(r._mapping)
            qt = q.get("question_type") or "multiple_choice"
            options = q.get("options") or []
            if isinstance(options, str):
                import json as _json
                try:
                    options = _json.loads(options)
                except Exception:
                    options = []

            # Deserialize correct_indices JSON column
            correct_indices = q.get("correct_indices")
            if isinstance(correct_indices, str):
                try:
                    correct_indices = _json.loads(correct_indices)
                except Exception:
                    correct_indices = []
            if correct_indices is None:
                correct_indices = []

            if qt in ("multiple_choice", "multiple_answer", "scenario_based"):
                # Shuffle options; remap correct_index / correct_indices
                indexed = list(enumerate(options))
                random.shuffle(indexed)
                q["options"] = [text for _, text in indexed]
                orig_to_new = {orig: new for new, (orig, _) in enumerate(indexed)}

                if qt == "multiple_answer":
                    q["correct_indices"] = [orig_to_new[i] for i in correct_indices if i in orig_to_new]
                    q["correct_index"]   = 0
                else:
                    ci = q.get("correct_index", 0)
                    q["correct_index"] = orig_to_new.get(ci, 0)
                    q["correct_indices"] = []

            elif qt == "true_false":
                # Keep True/False fixed; no shuffling needed
                q["options"] = options
                q["correct_indices"] = []

            elif qt == "identification":
                # No options; correct answer is a text string
                q["options"] = []
                q["correct_indices"] = []

            # Strip server-side correct answer from identification before sending
            # (correct_answer is only used for server-side evaluation)
            q.pop("correct_answer", None)
            questions.append(q)
        return questions
    except Exception as e:
        logger.error(f"[Quiz] error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        db.close()


@router.post("/quiz/attempt", response_model=QuizAttemptResponse)
async def submit_quiz_attempt(request: QuizAttemptRequest):
    """
    Record a quiz attempt, return immediate correctness feedback.
    Supports: multiple_choice, multiple_answer, true_false, identification, scenario_based.
    """
    db = Session(engine)
    try:
        question = db.get(QuizQuestionORM, request.question_id)
        if not question:
            raise HTTPException(status_code=404, detail="Question not found.")

        qt = getattr(question, "question_type", None) or "multiple_choice"

        if qt == "identification":
            # Case-insensitive text match
            typed = (getattr(request, "typed_answer", None) or "").strip().lower()
            stored = (getattr(question, "correct_answer", None) or "").strip().lower()
            is_correct = int(typed == stored)
        elif qt == "multiple_answer":
            # submitted correct_indices must exactly match stored correct_indices
            import json as _json
            stored_ci = getattr(question, "correct_indices", None)
            if isinstance(stored_ci, str):
                try:
                    stored_ci = _json.loads(stored_ci)
                except Exception:
                    stored_ci = []
            stored_set = set(stored_ci or [])
            submitted  = set(getattr(request, "selected_indices", None) or [])
            is_correct = int(submitted == stored_set)
        else:
            is_correct = int(request.selected_index == question.correct_index)

        db.add(QuizAttemptORM(
            user_id        = request.user_id,
            question_id    = request.question_id,
            selected_index = request.selected_index,
            is_correct     = is_correct,
        ))
        db.commit()

        if request.user_id:
            _maybe_advance_skill(db, request.user_id, question.topic)
            # ── mindmap unlock: correct answer + logged-in user + node tagged ──
            if is_correct and getattr(question, "mindmap_node_id", None):
                _unlock_mindmap_node(db, request.user_id, question.mindmap_node_id)

        return QuizAttemptResponse(
            is_correct    = bool(is_correct),
            correct_index = question.correct_index,
            explanation   = question.explanation,
            hint          = question.hint,
            topic         = question.topic,
            difficulty    = question.difficulty,
            **_TOPIC_SKILL.get(question.topic, _TOPIC_SKILL["general"]),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Quiz] error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        db.close()


# ── Skill advancement helper ──────────────────────────────────────────────────

_LEVEL_ORDER = ["beginner", "intermediate", "advanced"]
_ADVANCE_THRESHOLD = {
    "beginner":     70,   # accuracy_pct needed to move to intermediate
    "intermediate": 80,   # accuracy_pct needed to move to advanced
}
_WINDOW = 10   # rolling window of recent attempts per topic


def _maybe_advance_skill(db: Session, user_id: int, topic: str) -> None:
    """
    Recalculate rolling accuracy for this user+topic and advance
    current_level in user_skill_progress if the threshold is met.
    Creates the row if it doesn't exist yet.
    """
    try:
        # Rolling accuracy over last _WINDOW attempts for this topic
        rows = db.execute(
            sa.text("""
                SELECT qa.is_correct
                FROM quiz_attempts qa
                JOIN quiz_questions qq ON qq.id = qa.question_id
                WHERE qa.user_id = :uid AND qq.topic = :topic
                  AND qa.question_id > 0
                ORDER BY qa.attempted_at DESC
                LIMIT :window
            """),
            {"uid": user_id, "topic": topic, "window": _WINDOW},
        ).fetchall()

        if not rows:
            return

        accuracy = round(sum(r.is_correct for r in rows) / len(rows) * 100)

        # Get or create skill progress row
        prog = db.execute(
            sa.text(
                "SELECT id, current_level, lessons_completed FROM user_skill_progress "
                "WHERE user_id = :uid AND topic = :topic LIMIT 1"
            ),
            {"uid": user_id, "topic": topic},
        ).fetchone()

        if prog is None:
            db.execute(
                sa.text("""
                    INSERT INTO user_skill_progress
                        (user_id, topic, current_level, quiz_accuracy_pct, lessons_completed)
                    VALUES (:uid, :topic, 'beginner', :acc, 0)
                """),
                {"uid": user_id, "topic": topic, "acc": accuracy},
            )
            db.commit()
            return

        current_level = prog.current_level
        threshold     = _ADVANCE_THRESHOLD.get(current_level)

        # Always update accuracy regardless of advancement
        new_level = current_level
        if threshold is not None and accuracy >= threshold:
            idx = _LEVEL_ORDER.index(current_level)
            if idx < len(_LEVEL_ORDER) - 1:
                new_level = _LEVEL_ORDER[idx + 1]
                logger.info(
                    f"[SkillProgress] user={user_id} topic={topic} "
                    f"{current_level} → {new_level} (accuracy={accuracy}%)"
                )

        db.execute(
            sa.text("""
                UPDATE user_skill_progress
                SET current_level = :level, quiz_accuracy_pct = :acc
                WHERE user_id = :uid AND topic = :topic
            """),
            {"level": new_level, "acc": accuracy, "uid": user_id, "topic": topic},
        )

        # Write a history entry whenever the level changes so dashboard
        # sparklines (user_skill_history) have data to display.
        if new_level != current_level:
            try:
                db.execute(
                    sa.text("""
                        INSERT INTO user_skill_history
                            (user_id, topic, level_from, level_to,
                             quiz_accuracy, trigger_event, changed_at)
                        VALUES
                            (:uid, :topic, :from_level, :to_level,
                             :acc, 'quiz_advance', NOW())
                    """),
                    {
                        "uid":        user_id,
                        "topic":      topic,
                        "from_level": current_level,
                        "to_level":   new_level,
                        "acc":        accuracy,
                    },
                )
            except Exception as hist_err:
                logger.warning(f"[SkillHistory] Insert failed: {hist_err}")

        db.commit()

    except Exception as e:
        logger.warning(f"[SkillProgress] Update failed for user={user_id} topic={topic}: {e}")


@router.get("/quiz/stats/{user_id}")
async def get_quiz_stats(user_id: int, req: Request, authorization: str = Header(None)):
    """Return per-topic quiz performance summary for a logged-in user.
    H-1 FIX: Requires authentication; only the owner or an admin may access.
    """
    payload = get_current_user(req, authorization)
    if payload["sub"] != user_id and payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden.")
    db = Session(engine)
    try:
        rows = db.execute(
            sa.text("""
                SELECT
                    qq.topic,
                    COUNT(*)                     AS topic_attempts,
                    SUM(qa.is_correct)           AS topic_correct,
                    ROUND(AVG(qa.is_correct)*100) AS accuracy_pct
                FROM quiz_attempts qa
                JOIN quiz_questions qq ON qq.id = qa.question_id
                WHERE qa.user_id = :uid
                GROUP BY qq.topic
            """),
            {"uid": user_id},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        logger.error(f"[Quiz] error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        db.close()



# ── Pre-test / Post-test (DB-driven claims) ───────────────────────────────────
# Claims are now managed via /admin/pretest/claims (no hardcoded limit).
# The table is seeded with the original 5 on first access.

def _get_active_pretest_claims(db):
    """Fetch active pretest claims from DB, ordered by sort_order."""
    try:
        rows = db.execute(sa.text(
            "SELECT id, text, question_type, correct_answer, options, correct_index FROM pretest_claims "
            "WHERE is_active = 1 ORDER BY sort_order, id"
        )).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        # Table may not exist yet — return hardcoded fallback
        return [
            {"id": 1, "text": "The Philippines is the most populous country in Southeast Asia.", "question_type": "true_false", "correct_answer": "False", "options": None, "correct_index": 0},
            {"id": 2, "text": "The COVID-19 vaccine was developed in under one year.", "question_type": "true_false", "correct_answer": "True", "options": None, "correct_index": 1},
            {"id": 3, "text": "Facebook was founded in 2004 by Mark Zuckerberg.", "question_type": "true_false", "correct_answer": "True", "options": None, "correct_index": 1},
            {"id": 4, "text": "The Great Wall of China is visible from space with the naked eye.", "question_type": "true_false", "correct_answer": "False", "options": None, "correct_index": 0},
            {"id": 5, "text": "Telemundo is an English-language television network.", "question_type": "true_false", "correct_answer": "False", "options": None, "correct_index": 0},
        ]


@router.get("/quiz/pretest")
async def get_pretest():
    """
    Return the active pretest claim set from the database.
    Managed via /admin/pretest/claims — no hardcoded limit.
    """
    db = Session(engine)
    try:
        claims = _get_active_pretest_claims(db)
        # Return everything needed for the frontend to render each question type
        return {"claims": [
            {
                "id": c["id"],
                "text": c["text"],
                "question_type": c.get("question_type") or "true_false",
                "options": c.get("options"),
                "correct_index": c.get("correct_index", 0),
            }
            for c in claims
        ]}
    finally:
        db.close()


@router.post("/quiz/pretest/submit")
async def submit_pretest(payload: dict):
    """
    Record pre-test answers and return the score.

    Body: {"user_id": int (optional), "session_token": str, "answers": {"1": "True", ...}}

    Writes to pretest_results table.
    """
    answers     = payload.get("answers", {})
    session_tok = payload.get("session_token", "anonymous")
    user_id     = payload.get("user_id")

    db = Session(engine)
    try:
        claims = _get_active_pretest_claims(db)
        correct = 0
        scoreable_total = 0
        results = []
        for claim in claims:
            user_ans = answers.get(str(claim["id"]), "").strip()
            qtype    = claim.get("question_type") or "true_false"
            is_scored = qtype not in ("scale", "open")
            is_correct = False
            correct_display = claim.get("correct_answer") or ""

            if is_scored:
                scoreable_total += 1
                is_correct = user_ans.lower() == (correct_display or "").lower()
                if is_correct:
                    correct += 1

            results.append({
                "id":             claim["id"],
                "text":           claim["text"],
                "question_type":  qtype,
                "your_answer":    user_ans,
                "correct_answer": correct_display if is_scored else None,
                "is_correct":     is_correct if is_scored else None,
                "scored":         is_scored,
            })

        total     = scoreable_total or len(claims)
        score_pct = round(correct / total * 100) if total > 0 else 0

        try:
            db.execute(
                sa.text("""
                    INSERT INTO pretest_results
                        (user_id, session_token, phase, score_pct, correct, total)
                    VALUES (:uid, :tok, 'pretest', :score, :correct, :total)
                """),
                {
                    "uid":     user_id,
                    "tok":     session_tok,
                    "score":   score_pct,
                    "correct": correct,
                    "total":   total,
                },
            )
            db.commit()
        except Exception as e:
            logger.warning(f"[Pretest] DB write failed: {e}")

        return {
            "score_pct":     score_pct,
            "correct":       correct,
            "total":         total,
            "results":       results,
            "session_token": session_tok,
            "phase":         "pretest",
        }
    finally:
        db.close()


@router.post("/quiz/posttest/submit")
async def submit_posttest(payload: dict):
    """
    Record post-test answers using the same active claims and return score + delta.

    Body: {"user_id": int (optional), "session_token": str, "answers": {"1": "True", ...},
           "pretest_score": int (optional, for inline delta calculation)}
    """
    answers     = payload.get("answers", {})
    session_tok = payload.get("session_token", "anonymous")
    user_id     = payload.get("user_id")
    pre_score   = payload.get("pretest_score")

    db = Session(engine)
    try:
        claims  = _get_active_pretest_claims(db)
        correct = 0
        results = []
        for claim in claims:
            user_ans   = answers.get(str(claim["id"]), "").strip()
            is_correct = user_ans.lower() == claim["answer"].lower()
            if is_correct:
                correct += 1
            results.append({
                "id":             claim["id"],
                "text":           claim["text"],
                "your_answer":    user_ans,
                "correct_answer": claim["answer"],
                "is_correct":     is_correct,
            })

        total     = len(claims)
        score_pct = round(correct / total * 100) if total > 0 else 0

        # Look up pretest score from DB if not supplied inline
        if pre_score is None:
            try:
                row = db.execute(
                    sa.text("""
                        SELECT score_pct FROM pretest_results
                        WHERE phase = 'pretest'
                        AND (user_id = :uid OR session_token = :tok)
                        ORDER BY submitted_at DESC LIMIT 1
                    """),
                    {"uid": user_id, "tok": session_tok},
                ).fetchone()
                if row:
                    pre_score = row.score_pct
            except Exception:
                pass

        delta = (score_pct - pre_score) if pre_score is not None else None

        try:
            db.execute(
                sa.text("""
                    INSERT INTO pretest_results
                        (user_id, session_token, phase, score_pct, correct, total)
                    VALUES (:uid, :tok, 'posttest', :score, :correct, :total)
                """),
                {
                    "uid":     user_id,
                    "tok":     session_tok,
                    "score":   score_pct,
                    "correct": correct,
                    "total":   total,
                },
            )
            db.commit()
        except Exception as e:
            logger.warning(f"[Posttest] DB write failed: {e}")

        return {
            "score_pct": score_pct,
            "correct":   correct,
            "total":     total,
            "results":   results,
            "phase":     "posttest",
            "delta":     delta,
            "improved":  (delta > 0) if delta is not None else None,
        }
    finally:
        db.close()


# ── PATCH /quiz/questions/{question_id}/toggle-active  (admin only) ───────────


def _require_quiz_admin(authorization: Optional[str], request: Request):
    """Raise 401/403 unless caller is an authenticated admin."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif request is not None:
        token = request.cookies.get("sp_jwt")
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required.")
    try:
        payload = _quiz_verify(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token.")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return payload


@router.patch("/quiz/questions/{question_id}/toggle-active")
async def toggle_quiz_question_active(
    question_id: int,
    req: Request,
    authorization: str = Header(None),
):
    """
    Toggle is_active for a quiz question (admin only).
    Deactivated questions are excluded from quiz sessions but not deleted.
    """
    _require_quiz_admin(authorization, req)

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT id, is_active FROM quiz_questions WHERE id = :id"),
            {"id": question_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Question not found.")

        # is_active may not exist in older schema — default to 1
        current = getattr(row, "is_active", 1) if row else 1
        new_state = 0 if current else 1
        try:
            db.execute(
                sa.text("UPDATE quiz_questions SET is_active = :state WHERE id = :id"),
                {"state": new_state, "id": question_id},
            )
            db.commit()
        except Exception:
            # Column may not exist; add it first
            db.rollback()
            db.execute(sa.text(
                "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS "
                "is_active TINYINT(1) NOT NULL DEFAULT 1"
            ))
            db.execute(
                sa.text("UPDATE quiz_questions SET is_active = :state WHERE id = :id"),
                {"state": new_state, "id": question_id},
            )
            db.commit()

        return {"id": question_id, "is_active": bool(new_state)}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[Quiz] toggle_active error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not update question.")
    finally:
        db.close()


@router.get("/quiz/global-stats")
async def get_quiz_global_stats(req: Request, authorization: str = Header(None)):
    """
    Admin-level global quiz stats: total attempts, accuracy by topic,
    most-missed questions.  Requires authentication (admin or own data).
    """
    from routers.auth import get_current_user as _gcu
    payload = _gcu(req, authorization)
    db = Session(engine)
    try:
        topic_rows = db.execute(sa.text("""
            SELECT
                qq.topic,
                COUNT(*)                      AS total_attempts,
                SUM(qa.is_correct)            AS total_correct,
                ROUND(AVG(qa.is_correct)*100) AS accuracy_pct
            FROM quiz_attempts qa
            JOIN quiz_questions qq ON qq.id = qa.question_id
            GROUP BY qq.topic
            ORDER BY qq.topic
        """)).fetchall()

        missed_rows = db.execute(sa.text("""
            SELECT
                qq.id,
                qq.question_text,
                qq.topic,
                COUNT(*)                      AS attempts,
                SUM(qa.is_correct)            AS correct,
                ROUND(AVG(qa.is_correct)*100) AS accuracy_pct
            FROM quiz_attempts qa
            JOIN quiz_questions qq ON qq.id = qa.question_id
            GROUP BY qq.id
            HAVING attempts >= 3
            ORDER BY accuracy_pct ASC
            LIMIT 5
        """)).fetchall()

        return {
            "by_topic":       [dict(r._mapping) for r in topic_rows],
            "most_missed":    [dict(r._mapping) for r in missed_rows],
        }
    except Exception as e:
        logger.error(f"[Quiz] global stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not load stats.")
    finally:
        db.close()
