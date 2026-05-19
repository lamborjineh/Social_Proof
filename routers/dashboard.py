"""
SocialProof — Router: User Dashboard  v6.0
GET /dashboard/{user_id}

Returns everything from v4.0, plus:
  skill_history          — append-only log of per-topic level changes
                           (drives sparklines in renderSkillProgress)
  journal_entries        — last 5 Reasoning Journal entries from user_reflections
                           (drives renderReasoningJournal; Bloom's L4-5 data)
  confidence_trend       — last 10 confidence_before/after snapshots
                           (drives renderConfidenceTrend; Kirkpatrick L2/3)
  source_diversity_summary — aggregated source-type counts across all sessions
                           (drives renderSourceDiversitySummary; UNESCO MIL)

v6.0 Design Rules (same as v4.0, extended):
  - user_label, confidence_level, user_score NEVER in history (judgment fields)
  - re_evaluations NEVER queried or returned
  - system_score, system_label NEVER returned
  - journal_entries: free text only — no NLI or verdict data
  - confidence_trend: numeric 1-5 and delta — no label inference
"""

from datetime import date, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from config import logger
from database.models import engine
from routers.auth import get_current_user

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

_TOPIC_DISPLAY = {
    "claim_detection":    "Claim Detection",
    "source_verification": "Source Verification",
    "bias_detection":     "Bias Detection",
    "evidence_evaluation": "Evidence Evaluation",
    "general":            "General MIL",
}

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}

_BEHAVIOR_INSIGHTS = {
    "source_verification": {
        "flag":         "trusts_sources_unchecked",
        "title":        "Source checking gap",
        "body":         "You've been prompted to re-examine sources several times. Practise running a quick domain check before trusting a claim.",
        "mil_skill":    "Evaluate & Assess",
        "action":       "lessons.html#evaluating-sources",
        "action_label": "Review: Evaluating Sources →",
    },
    "bias_detection": {
        "flag":         "trusts_emotional",
        "title":        "Emotional language slips through",
        "body":         "Emotionally charged framing is appearing in content you've marked as credible. Recognising loaded language is one of the highest-leverage MIL skills.",
        "mil_skill":    "Evaluate & Assess",
        "action":       "lessons.html#bias-emotional-language",
        "action_label": "Review: Bias & Emotional Language →",
    },
    "claim_detection": {
        "flag":         "skips_claims",
        "title":        "Claim identification step skipped",
        "body":         "Isolating exactly what is being claimed is the first step in any fact-check. Skipping it means reasoning on the wrong thing.",
        "mil_skill":    "Understand",
        "action":       "lessons.html#what-is-a-claim",
        "action_label": "Review: What Is a Claim? →",
    },
    "evidence_evaluation": {
        "flag":         "skips_evidence",
        "title":        "Evidence step skipped",
        "body":         "You've bypassed the evidence step in several sessions. Evidence quality — not just presence — determines how much weight a claim deserves.",
        "mil_skill":    "Evaluate & Assess",
        "action":       "lessons.html#reading-evidence",
        "action_label": "Review: Reading Evidence →",
    },
    "general": {
        "flag":         "overconfident",
        "title":        "High confidence, varied outcomes",
        "body":         "Keep building your calibration. The goal isn't certainty — it's scepticism proportional to the evidence.",
        "mil_skill":    "Reflect",
        "action":       "lessons.html#putting-it-together",
        "action_label": "Review: Putting It Together →",
    },
    # v6.0: metacognition insight triggered when calibration_gap fires repeatedly
    "metacognition_bias": {
        "flag":         "calibration_gap",
        "title":        "Confidence-thoroughness mismatch detected",
        "body":         "In several sessions your confidence was high but key steps were incomplete. This Dunning-Kruger pattern is the #1 way misinformation spreads — even among careful readers. ",
        "mil_skill":    "Reflect",
        "action":       "lessons.html#metacognition-bias",
        "action_label": "Review: Metacognitive Bias →",
    },
}


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/dashboard/{user_id}")
async def get_dashboard(user_id: int, request: Request):
    try:
        payload = get_current_user(request, request.headers.get("authorization"))
        if payload["sub"] != user_id and payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Forbidden.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    db = Session(engine)
    try:

        # ── 1. Core stats ─────────────────────────────────────────────────────
        total_evals = db.execute(sa.text(
            "SELECT COUNT(*) FROM submissions WHERE user_id = :uid"
        ), {"uid": user_id}).scalar() or 0

        lessons_done = db.execute(sa.text(
            "SELECT COUNT(*) FROM lesson_completions WHERE user_id = :uid"
        ), {"uid": user_id}).scalar() or 0

        streak = 0
        try:
            streak_rows = db.execute(sa.text("""
                SELECT DATE(attempted_at) AS d
                FROM quiz_attempts
                WHERE user_id = :uid
                GROUP BY DATE(attempted_at)
                ORDER BY d DESC
                LIMIT 30
            """), {"uid": user_id}).fetchall()
            if streak_rows:
                today = date.today()
                for i, row in enumerate(streak_rows):
                    expected = today - timedelta(days=i)
                    if row.d == expected:
                        streak += 1
                    else:
                        break
        except Exception:
            streak = 0

        total_quiz_attempts = db.execute(sa.text(
            "SELECT COUNT(*) FROM quiz_attempts WHERE user_id = :uid"
        ), {"uid": user_id}).scalar() or 0

        stats = {
            "total_submissions":   total_evals,
            "lessons_completed":   lessons_done,
            "quiz_streak":         streak,
            "total_quiz_attempts": total_quiz_attempts,
        }

        # ── 2. Skill progress ─────────────────────────────────────────────────
        skill_rows = db.execute(sa.text("""
            SELECT topic, current_level, quiz_accuracy_pct, lessons_completed
            FROM user_skill_progress
            WHERE user_id = :uid
        """), {"uid": user_id}).fetchall()

        skill_map = {r.topic: r for r in skill_rows}
        skill_progress = []
        for topic in ["claim_detection", "source_verification", "bias_detection",
                      "evidence_evaluation", "general"]:
            row = skill_map.get(topic)
            skill_progress.append({
                "topic":             topic,
                "display_name":      _TOPIC_DISPLAY.get(topic, topic),
                "current_level":     row.current_level if row else "beginner",
                "level_index":       _LEVEL_ORDER.get(row.current_level if row else "beginner", 0),
                "quiz_accuracy_pct": round(row.quiz_accuracy_pct) if (row and row.quiz_accuracy_pct) else None,
                "lessons_completed": row.lessons_completed if row else 0,
            })

        # ── 3. Lesson triggers (weakness bars) ────────────────────────────────
        trigger_rows = db.execute(sa.text("""
            SELECT l.topic, COUNT(*) AS trigger_count
            FROM lessons_triggered lt
            JOIN submissions s ON s.id = lt.submission_id
            JOIN lessons l ON l.id = lt.lesson_id
            WHERE s.user_id = :uid
            GROUP BY l.topic
            ORDER BY trigger_count DESC
            LIMIT 5
        """), {"uid": user_id}).fetchall()

        lesson_triggers = [
            {"topic": r.topic, "trigger_count": r.trigger_count,
             "display_name": _TOPIC_DISPLAY.get(r.topic, r.topic)}
            for r in trigger_rows
        ]

        # ── 4. Behavior insight cards ─────────────────────────────────────────
        behavior_cards = []

        # v6.0: check for calibration gap pattern (≥2 sessions flagged)
        try:
            calibration_count = db.execute(sa.text("""
                SELECT COUNT(*) FROM confidence_snapshots
                WHERE user_id = :uid AND calibration_flag = 1
            """), {"uid": user_id}).scalar() or 0
            if calibration_count >= 2:
                tmpl = _BEHAVIOR_INSIGHTS["metacognition_bias"]
                behavior_cards.append({
                    "flag":          tmpl["flag"],
                    "title":         tmpl["title"],
                    "body":          tmpl["body"],
                    "mil_skill":     tmpl["mil_skill"],
                    "action":        tmpl["action"],
                    "action_label":  tmpl["action_label"],
                    "trigger_count": calibration_count,
                })
        except Exception:
            pass

        for tr in trigger_rows:
            if len(behavior_cards) >= 3:
                break
            if tr.trigger_count >= 2 and tr.topic in _BEHAVIOR_INSIGHTS:
                tmpl = _BEHAVIOR_INSIGHTS[tr.topic]
                behavior_cards.append({
                    "flag":          tmpl["flag"],
                    "title":         tmpl["title"],
                    "body":          tmpl["body"],
                    "mil_skill":     tmpl["mil_skill"],
                    "action":        tmpl["action"],
                    "action_label":  tmpl["action_label"],
                    "trigger_count": tr.trigger_count,
                })

        # ── 5. Evaluation history — activity only, NO judgment fields ─────────
        history_rows = db.execute(sa.text("""
            SELECT s.id          AS eval_id,
                   s.raw_content AS content_preview,
                   s.created_at,
                   s.input_type
            FROM submissions s
            WHERE s.user_id = :uid
            ORDER BY s.created_at DESC
            LIMIT 20
        """), {"uid": user_id}).fetchall()

        history = []
        for r in history_rows:
            raw     = r.content_preview or ""
            preview = raw[:80] + ("…" if len(raw) > 80 else "")
            history.append({
                "eval_id":         r.eval_id,
                "content_preview": preview,
                "input_type":      r.input_type or "text",
                "created_at":      r.created_at.isoformat() if r.created_at else None,
            })

        # ── 6. Quiz history ───────────────────────────────────────────────────
        quiz_rows = db.execute(sa.text("""
            SELECT qa.id, qa.question_id, qa.is_correct, qa.attempted_at,
                   qq.topic, qq.difficulty, qq.question_text
            FROM quiz_attempts qa
            JOIN quiz_questions qq ON qq.id = qa.question_id
            WHERE qa.user_id = :uid
            ORDER BY qa.attempted_at DESC
            LIMIT 20
        """), {"uid": user_id}).fetchall()

        quiz_history = []
        for r in quiz_rows:
            quiz_history.append({
                "attempt_id":    r.id,
                "question_id":   r.question_id,
                "question_text": (r.question_text or "")[:80],
                "topic":         r.topic,
                "display_name":  _TOPIC_DISPLAY.get(r.topic, r.topic),
                "difficulty":    r.difficulty,
                "is_correct":    bool(r.is_correct),
                "attempted_at":  r.attempted_at.isoformat() if r.attempted_at else None,
            })

        # ── 7. Lesson recommendations (triggered but not completed) ───────────
        recommended_rows = db.execute(sa.text("""
            SELECT l.id, l.lesson_key, l.title, l.topic, l.difficulty, l.mil_skill
            FROM lessons_triggered lt
            JOIN submissions s ON s.id = lt.submission_id
            JOIN lessons l ON l.id = lt.lesson_id
            WHERE s.user_id = :uid
              AND lt.was_read = 0
              AND l.id NOT IN (
                SELECT lesson_id FROM lesson_completions WHERE user_id = :uid
              )
            GROUP BY l.id, l.lesson_key, l.title, l.topic, l.difficulty, l.mil_skill
            ORDER BY MAX(lt.id) DESC
            LIMIT 5
        """), {"uid": user_id}).fetchall()

        recommended = [
            {
                "lesson_id":    r.id,
                "lesson_key":   r.lesson_key,
                "title":        r.title,
                "topic":        r.topic,
                "display_name": _TOPIC_DISPLAY.get(r.topic, r.topic),
                "difficulty":   r.difficulty,
                "mil_skill":    r.mil_skill,
            }
            for r in recommended_rows
        ]

        # ── 8. Pre-test vs post-test ──────────────────────────────────────────
        pretest = None
        try:
            pt_rows = db.execute(sa.text("""
                SELECT phase, score_pct, correct, total, submitted_at
                FROM pretest_results
                WHERE user_id = :uid
                ORDER BY submitted_at ASC
            """), {"uid": user_id}).fetchall()

            pt_map = {r.phase: r for r in pt_rows}
            if pt_map:
                pre  = pt_map.get("pretest")
                post = pt_map.get("posttest")

                # Compute longitudinal pairing metrics (ms2)
                days_between  = None
                score_gain_pct = None
                if pre and post:
                    try:
                        delta_td      = post.submitted_at - pre.submitted_at
                        days_between  = round(delta_td.total_seconds() / 86400, 1)
                        score_gain_pct = round(post.score_pct - pre.score_pct, 1)
                    except Exception:
                        pass

                pretest = {
                    "pretest":       {"score_pct": pre.score_pct,  "correct": pre.correct,  "total": pre.total,  "submitted_at": pre.submitted_at.isoformat()  if pre.submitted_at  else None}  if pre  else None,
                    "posttest":      {"score_pct": post.score_pct, "correct": post.correct, "total": post.total, "submitted_at": post.submitted_at.isoformat() if post.submitted_at else None} if post else None,
                    "delta":         score_gain_pct,
                    "days_between":  days_between,
                    "score_gain_pct": score_gain_pct,
                    "pretest_only":  bool(pre and not post),
                }
        except Exception:
            pretest = None

        # ── 9. Mindmap progress — node discovery count ────────────────────────
        mindmap_progress = {"discovered": 0, "total": 0, "unlocked_list": []}
        try:
            # Count distinct nodes the user has discovered in mindmap_progress
            discovered_row = db.execute(sa.text("""
                SELECT COUNT(DISTINCT node_id) AS discovered
                FROM mindmap_progress
                WHERE user_id = :uid AND map_id = 'main'
            """), {"uid": user_id}).fetchone()
            discovered = int(discovered_row.discovered) if discovered_row else 0

            # Total active nodes in the main map
            total_row = db.execute(sa.text(
                "SELECT COUNT(*) AS total FROM mindmap_nodes WHERE map_id = 'main' AND active = 1"
            )).fetchone()
            total = int(total_row.total) if total_row else 0

            # List of unlocked node_ids for the frontend (e.g. to highlight map nodes)
            unlocked_rows = db.execute(sa.text(
                "SELECT DISTINCT node_id FROM mindmap_progress WHERE user_id = :uid AND map_id = 'main'"
            ), {"uid": user_id}).fetchall()
            unlocked_list = [r.node_id for r in unlocked_rows]

            mindmap_progress = {
                "discovered":    discovered,
                "total":         total,
                "unlocked_list": unlocked_list,
            }
        except Exception as mp_err:
            logger.warning(f"[Dashboard] mindmap_progress query failed: {mp_err}")
            mindmap_progress = {"discovered": 0, "total": 0, "unlocked_list": []}

        # ── 11. Activity heatmap (16-week daily counts) ───────────────────────
        activity_by_day = []
        try:
            act_rows = db.execute(sa.text("""
                SELECT DATE(attempted_at) AS date, COUNT(*) AS count
                FROM quiz_attempts
                WHERE user_id = :uid
                  AND attempted_at >= DATE(NOW() - INTERVAL 112 DAY)
                GROUP BY DATE(attempted_at)
                ORDER BY date ASC
            """), {"uid": user_id}).fetchall()
            activity_by_day = [
                {"date": str(r.date), "count": r.count}
                for r in act_rows
            ]
        except Exception:
            activity_by_day = []

        # ── NEW v6.0: 12. Skill history (per-topic level change log) ──────────
        # Powers sparklines in the Skills section of the dashboard.
        # Returns up to 6 history entries per topic, ordered oldest → newest.
        skill_history = []
        try:
            sh_rows = db.execute(sa.text("""
                SELECT topic, level_from, level_to, quiz_accuracy, trigger_event,
                       changed_at
                FROM user_skill_history
                WHERE user_id = :uid
                ORDER BY changed_at ASC
                LIMIT 60
            """), {"uid": user_id}).fetchall()
            skill_history = [
                {
                    "topic":         r.topic,
                    "level_from":    r.level_from,
                    "level_to":      r.level_to,
                    "quiz_accuracy": r.quiz_accuracy,
                    "trigger_event": r.trigger_event,
                    "changed_at":    r.changed_at.isoformat() if r.changed_at else None,
                }
                for r in sh_rows
            ]
        except Exception as e:
            logger.warning(f"[Dashboard] skill_history query failed: {e}")
            skill_history = []

        # ── NEW v6.0: 13. Reasoning Journal entries ───────────────────────────
        # Last 5 entries from user_reflections. Free text only.
        # Powers renderReasoningJournal() — primary qualitative data for
        # Bloom's L4-5 analysis and Kirkpatrick Level 3 evidence.
        journal_entries = []
        try:
            jr_rows = db.execute(sa.text("""
                SELECT id, stage, what_noticed, still_uncertain, would_check_next,
                       free_reasoning, verdict_position, bloom_level,
                       total_word_count, submitted_at
                FROM user_reflections
                WHERE user_id = :uid
                ORDER BY submitted_at DESC
                LIMIT 5
            """), {"uid": user_id}).fetchall()
            journal_entries = [
                {
                    "id":              r.id,
                    "stage":           r.stage,
                    "what_noticed":    r.what_noticed,
                    "still_uncertain": r.still_uncertain,
                    "would_check_next": r.would_check_next,
                    "free_reasoning":  r.free_reasoning,
                    "verdict_position": r.verdict_position,
                    "bloom_level":     r.bloom_level,
                    "total_word_count": r.total_word_count,
                    "submitted_at":    r.submitted_at.isoformat() if r.submitted_at else None,
                }
                for r in jr_rows
            ]
        except Exception as e:
            logger.warning(f"[Dashboard] journal_entries query failed: {e}")
            journal_entries = []

        # ── NEW v6.0: 14. Confidence trend ───────────────────────────────────
        # Last 10 confidence_before / confidence_after snapshots.
        # Powers renderConfidenceTrend() — Kirkpatrick Level 2/3 evidence
        # and Dunning-Kruger calibration analysis.
        confidence_trend = []
        try:
            ct_rows = db.execute(sa.text("""
                SELECT confidence_before, confidence_after, confidence_delta,
                       calibration_flag, confidence_label, recorded_at
                FROM confidence_snapshots
                WHERE user_id = :uid
                ORDER BY recorded_at DESC
                LIMIT 10
            """), {"uid": user_id}).fetchall()
            confidence_trend = [
                {
                    "confidence_before": r.confidence_before,
                    "confidence_after":  r.confidence_after,
                    "confidence_delta":  r.confidence_delta,
                    "calibration_flag":  bool(r.calibration_flag),
                    "confidence_label":  r.confidence_label,
                    "recorded_at":       r.recorded_at.isoformat() if r.recorded_at else None,
                }
                for r in ct_rows
            ]
        except Exception as e:
            logger.warning(f"[Dashboard] confidence_trend query failed: {e}")
            confidence_trend = []

        # ── NEW v6.0: 15. Source diversity summary ────────────────────────────
        # Aggregated across all the user's sessions.
        # Powers renderSourceDiversitySummary() — UNESCO MIL alignment evidence.
        source_diversity_summary = None
        try:
            sd_row = db.execute(sa.text("""
                SELECT
                    COUNT(*)                      AS session_count,
                    SUM(count_government)         AS total_government,
                    SUM(count_academic)           AS total_academic,
                    SUM(count_news)               AS total_news,
                    SUM(count_factcheck)          AS total_factcheck,
                    SUM(count_international)      AS total_international,
                    SUM(count_other)              AS total_other,
                    AVG(diversity_score)          AS avg_diversity_score
                FROM source_diversity_log sdl
                JOIN submissions s ON s.id = sdl.submission_id
                WHERE s.user_id = :uid
            """), {"uid": user_id}).fetchone()

            if sd_row and sd_row.session_count:
                source_diversity_summary = {
                    "session_count":      sd_row.session_count,
                    "total_government":   int(sd_row.total_government  or 0),
                    "total_academic":     int(sd_row.total_academic    or 0),
                    "total_news":         int(sd_row.total_news        or 0),
                    "total_factcheck":    int(sd_row.total_factcheck   or 0),
                    "total_international":int(sd_row.total_international or 0),
                    "total_other":        int(sd_row.total_other       or 0),
                    "avg_diversity_score": round(float(sd_row.avg_diversity_score or 0), 2),
                }
        except Exception as e:
            logger.warning(f"[Dashboard] source_diversity_summary query failed: {e}")
            source_diversity_summary = None

        # ── NEW checklist: Composite Learning Outcome Score (ms1) ─────────────
        # Weighted composite: (avg bloom_level × 0.3) + (quiz_accuracy × 0.4)
        # + (transfer_score proxy × 0.3). Answers "is this person learning?"
        # rather than just showing activity counts.
        composite_learning_score = None
        try:
            # avg bloom_level from reflections (0–5 scale, normalised to 0–1)
            bloom_row = db.execute(sa.text("""
                SELECT AVG(bloom_level) AS avg_bloom, COUNT(*) AS n
                FROM user_reflections
                WHERE user_id = :uid AND bloom_level IS NOT NULL
            """), {"uid": user_id}).fetchone()
            avg_bloom = float(bloom_row.avg_bloom or 0) / 5.0 if bloom_row else 0.0

            # quiz accuracy from user_skill_progress
            qa_row = db.execute(sa.text("""
                SELECT AVG(quiz_accuracy_pct) AS avg_qa
                FROM user_skill_progress
                WHERE user_id = :uid AND quiz_accuracy_pct IS NOT NULL
            """), {"uid": user_id}).fetchone()
            avg_quiz = float(qa_row.avg_qa or 0) / 100.0 if qa_row else 0.0

            # transfer score proxy: score_gain_pct normalised (pretest → posttest delta / 100)
            transfer = 0.0
            if pretest and pretest.get("score_gain_pct") is not None:
                raw_gain   = pretest["score_gain_pct"]
                transfer   = max(0.0, min(1.0, (raw_gain + 100) / 200.0))  # centre at 0, range -100..+100

            composite = round(
                (avg_bloom * 0.3) + (avg_quiz * 0.4) + (transfer * 0.3),
                3
            )
            composite_learning_score = {
                "score":          composite,
                "avg_bloom_norm": round(avg_bloom, 3),
                "avg_quiz_norm":  round(avg_quiz, 3),
                "transfer_norm":  round(transfer, 3),
                "components":     {"bloom_weight": 0.3, "quiz_weight": 0.4, "transfer_weight": 0.3},
            }
        except Exception as e:
            logger.warning(f"[Dashboard] composite_learning_score failed: {e}")
            composite_learning_score = None

        return {
            # Unchanged from v4.0
            "stats":              stats,
            "skill_progress":     skill_progress,
            "behavior_cards":     behavior_cards,
            "lesson_triggers":    lesson_triggers,
            "history":            history,
            "quiz_history":       quiz_history,
            "recommended":        recommended,
            "pretest":            pretest,
            "mindmap_progress":   mindmap_progress,
            "activity_by_day":    activity_by_day,
            # New in v6.0
            "skill_history":           skill_history,
            "journal_entries":         journal_entries,
            "confidence_trend":        confidence_trend,
            "source_diversity_summary": source_diversity_summary,
            # New checklist additions
            "composite_learning_score":    composite_learning_score,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Dashboard] Error for user {user_id}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Dashboard query failed.")
    finally:
        db.close()
