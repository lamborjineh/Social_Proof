"""
SocialProof — Router: Analyze  v6.0

New in v6.0:
  POST /analyze/reasoning-journal  — save a Reasoning Journal entry (Bloom's L4–5)
  POST /analyze/confidence-snapshot — save before/after confidence readings
  POST /user-evaluation             — gains calibration gap detection,
                                      per-skill feedback, "What You Missed" messages
  POST /analyze                     — now passes source_diversity and MBFC fields through

Endpoints kept from v5.0:
  POST /analyze
  GET  /evaluations/{id}
  GET  /evaluations
  GET  /corpus-gaps
  POST /user-evaluation
  POST /user-reflection
  GET  /analyze/mbfc-lookup
"""

import re
import os
import asyncio
import base64
import functools
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Request, Header

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import logger
from database.models import engine, SubmissionORM
from pipeline import AnalysisPipeline
from pipeline.preprocessing import PreprocessingModule
from pipeline.evidence_retrieval import get_unverified_log
from pipeline.file_input import extract_text_from_file, SUPPORTED_ACCEPT

import time as _time
from collections import defaultdict

from schemas import (
    AnalyzeRequest, ArticleRetrievalResponse, ArticleResult,
    MBFCRating, SourceStepResponse, SourceDiversityInfo,
    ReasoningJournalEntry, ReasoningJournalResponse,
    ConfidenceSnapshotRequest, ConfidenceSnapshotResponse,
    ChallengeGateRequest, ChallengeGateResponse,
)
from routers.auth import get_current_user

router = APIRouter()

# ── Rate limiter ──────────────────────────────────────────────────────────────
_ANALYZE_RATE_WINDOW = int(os.environ.get("ANALYZE_RATE_WINDOW_SECONDS", "60"))
_ANALYZE_RATE_LIMIT  = int(os.environ.get("ANALYZE_RATE_LIMIT", "10"))

_analyze_redis = None
_REDIS_URL = os.getenv("REDIS_URL", "")
if _REDIS_URL:
    try:
        import redis as _redis_lib
        _analyze_redis = _redis_lib.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        _analyze_redis.ping()
        logger.info("Analyze rate limiter: Redis connected.")
    except Exception as _re:
        logger.warning(f"Analyze rate limiter: Redis failed ({_re}). Falling back to in-process.")
        _analyze_redis = None

_ip_request_times: dict = defaultdict(list)
_SESSION_TOKEN_RE = re.compile(r"^[0-9a-f]{32,64}$")
_MAX_TEXT_LENGTH  = int(os.environ.get("MAX_TEXT_LENGTH", 50_000))
_MAX_FILE_MB      = float(os.environ.get("MAX_FILE_MB", 10))


def _check_analyze_rate_limit(ip: str, token: str) -> None:
    key = f"sp:analyze_rl:{ip}:{token}"
    now = _time.time()
    if _analyze_redis:
        pipe = _analyze_redis.pipeline()
        pipe.zadd(key, {str(now): now})
        pipe.zremrangebyscore(key, 0, now - _ANALYZE_RATE_WINDOW)
        pipe.zcard(key)
        pipe.expire(key, _ANALYZE_RATE_WINDOW * 2)
        count = pipe.execute()[2]
    else:
        window_start = now - _ANALYZE_RATE_WINDOW
        _ip_request_times[key] = [t for t in _ip_request_times[key] if t > window_start]
        _ip_request_times[key].append(now)
        count = len(_ip_request_times[key])
    if count > _ANALYZE_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_ANALYZE_RATE_LIMIT} requests per {_ANALYZE_RATE_WINDOW}s.",
        )


_pipeline = AnalysisPipeline()
_PIPELINE_WORKERS = int(os.environ.get("PIPELINE_WORKERS", max(2, (os.cpu_count() or 2) * 2)))
_executor = ThreadPoolExecutor(max_workers=_PIPELINE_WORKERS)
_PIPELINE_TIMEOUT = float(os.environ.get("PIPELINE_TIMEOUT_SECONDS", "120"))


# ── POST /analyze ─────────────────────────────────────────────────────────────
@router.post("/analyze", response_model=ArticleRetrievalResponse)
async def analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks, req: Request = None):
    """
    Run article retrieval pipeline.
    Returns articles for the user to evaluate — the system makes no verdict.
    """
    if not request.session_token or not _SESSION_TOKEN_RE.match(request.session_token):
        raise HTTPException(
            status_code=422,
            detail="A valid session_token is required. Obtain one from GET /auth/session.",
        )

    _client_ip = (req.client.host if req and req.client else "unknown")
    _check_analyze_rate_limit(_client_ip, request.session_token)

    if request.text and len(request.text) > _MAX_TEXT_LENGTH:
        raise HTTPException(status_code=413, detail=f"Text exceeds maximum of {_MAX_TEXT_LENGTH} characters.")

    if request.file_data:
        file_bytes_approx = len(request.file_data) * 3 // 4
        if file_bytes_approx > _MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"File exceeds maximum of {_MAX_FILE_MB} MB.")

    # ── Input validation ──────────────────────────────────────────────────────
    if request.input_type == "image":
        if not request.image_data:
            raise HTTPException(status_code=422, detail="image_data is required when input_type='image'.")
        try:
            base64.b64decode(request.image_data)
        except Exception:
            raise HTTPException(status_code=422, detail="image_data is not valid base64.")

    elif request.input_type == "file":
        if not request.file_data:
            raise HTTPException(status_code=422, detail="file_data is required when input_type='file'.")
        if not request.file_name:
            raise HTTPException(status_code=422, detail="file_name is required when input_type='file'.")
        try:
            file_bytes = base64.b64decode(request.file_data)
        except Exception:
            raise HTTPException(status_code=422, detail="file_data is not valid base64.")
        extracted = extract_text_from_file(file_bytes, filename=request.file_name)
        if not extracted or len(extracted.strip()) < 15:
            raise HTTPException(
                status_code=422,
                detail=f"Could not extract text from '{request.file_name}'. Supported: {SUPPORTED_ACCEPT}",
            )
        request.text = extracted

    elif not request.text and not request.url:
        raise HTTPException(status_code=422, detail="Either 'text' or 'url' must be provided.")
    elif request.text and len(request.text.strip()) < 15:
        raise HTTPException(status_code=422, detail="Text is too short to retrieve articles for.")

    # ── Save pending submission ───────────────────────────────────────────────
    db = Session(engine)
    eval_id = 0
    try:
        eval_orm = SubmissionORM(
            user_id       = request.user_id,
            session_token = request.session_token,
            input_type    = request.input_type,
            raw_content   = request.text or request.url or (
                "[file]" if request.input_type == "file" else "[image]"
            ),
            status        = "pending",
        )
        db.add(eval_orm)
        db.commit()
        db.refresh(eval_orm)
        eval_id = eval_orm.id
    except Exception as e:
        logger.warning(f"DB save (pending) failed: {e}")
    finally:
        db.close()

    # ── Run pipeline ──────────────────────────────────────────────────────────
    try:
        loop   = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, functools.partial(_pipeline.run, request)),
            timeout=_PIPELINE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Retrieval timed out. Please try again.")
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred during retrieval.")

    # ── Background: mark submission complete + save confidence_before ─────────
    confidence_before = getattr(request, "confidence_before", None)

    def _save_results():
        db2 = None
        try:
            db2 = Session(engine)
            orm = db2.get(SubmissionORM, eval_id)
            if orm:
                orm.parsed_text = PreprocessingModule.clean(request.text or "")
                orm.status      = "analyzed"
                db2.commit()

            # Patch source_diversity_log with the real submission_id
            if eval_id:
                db2.execute(sa.text(
                    "UPDATE source_diversity_log SET submission_id = :sid "
                    "WHERE session_token = :tok AND submission_id IS NULL "
                    "ORDER BY logged_at DESC LIMIT 1"
                ), {"sid": eval_id, "tok": request.session_token})
                db2.commit()

            # Save confidence_before snapshot if provided
            if confidence_before is not None and eval_id:
                db2.execute(sa.text("""
                    INSERT INTO confidence_snapshots
                        (submission_id, user_id, session_token, confidence_before, confidence_label)
                    VALUES (:sid, :uid, :tok, :cbefore, :clabel)
                """), {
                    "sid":     eval_id,
                    "uid":     request.user_id,
                    "tok":     request.session_token,
                    "cbefore": confidence_before,
                    "clabel":  None,
                })
                db2.commit()

        except Exception as exc:
            logger.warning(f"Background DB save failed: {exc}")
            if db2:
                try: db2.rollback()
                except Exception: pass
        finally:
            if db2:
                try: db2.close()
                except Exception: pass

    background_tasks.add_task(_save_results)

    articles = [ArticleResult(**a) for a in result.get("articles", [])]

    raw_diversity = result.get("source_diversity")
    diversity_obj = SourceDiversityInfo(**raw_diversity) if raw_diversity else None

    return ArticleRetrievalResponse(
        submission_id    = eval_id,
        evaluation_id    = eval_id,
        articles         = articles,
        keywords         = result.get("keywords", []),
        processing_ms    = result.get("processing_ms", 0),
        live_search_used = result.get("live_search_used", False),
        url_fetch_failed = result.get("url_fetch_failed", False),
        url_fetch_error  = result.get("url_fetch_error", ""),
        source_diversity = diversity_obj,
    )


# ── GET /evaluations/{id} ─────────────────────────────────────────────────────
@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(
    evaluation_id: int,
    req: Request,
    session_token: str = None,
    authorization: str = Header(None),
):
    db = Session(engine)
    try:
        row = db.get(SubmissionORM, evaluation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Evaluation not found.")

        if authorization and authorization.startswith("Bearer "):
            current_user = get_current_user(req, authorization)
            if row.user_id is not None and row.user_id != current_user["sub"]:
                raise HTTPException(status_code=403, detail="Access denied.")
        elif session_token:
            if row.session_token != session_token:
                raise HTTPException(status_code=403, detail="Access denied.")
        else:
            raise HTTPException(status_code=401, detail="Authentication required.")

        return {"id": row.id, "status": row.status, "created_at": row.created_at.isoformat()}
    finally:
        db.close()


# ── GET /evaluations ──────────────────────────────────────────────────────────
@router.get("/evaluations")
async def list_evaluations(
    req: Request,
    session_token: str = None,
    limit: int = 20,
    authorization: str = Header(None),
):
    db = Session(engine)
    try:
        if authorization and authorization.startswith("Bearer "):
            current_user = get_current_user(req, authorization)
            rows = db.execute(
                sa.text("""
                    SELECT s.id, s.raw_content, s.status, s.created_at, s.input_type
                    FROM submissions s
                    WHERE s.user_id = :uid
                    ORDER BY s.created_at DESC LIMIT :lim
                """),
                {"uid": current_user["sub"], "lim": limit},
            ).fetchall()
        elif session_token:
            rows = db.execute(
                sa.text("""
                    SELECT s.id, s.raw_content, s.status, s.created_at, s.input_type
                    FROM submissions s
                    WHERE s.session_token = :tok
                    ORDER BY s.created_at DESC LIMIT :lim
                """),
                {"tok": session_token, "lim": limit},
            ).fetchall()
        else:
            raise HTTPException(status_code=422, detail="Authorization header or session_token required.")
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


# ── GET /corpus-gaps ──────────────────────────────────────────────────────────
@router.get("/corpus-gaps")
async def corpus_gaps(req: Request, authorization: str = Header(None)):
    get_current_user(req, authorization)
    return {"gaps": get_unverified_log()}


# ── POST /user-evaluation ─────────────────────────────────────────────────────
# Accepts the user's step answers, triggers lessons, returns feedback.
# v6.0: gains calibration gap detection, per-skill delta summary,
#       "What You Missed" contextual feedback, confidence snapshot update.
@router.post("/user-evaluation")
async def post_user_evaluation(body: dict):
    submission_id     = body.get("evaluation_id") or body.get("submission_id")
    user_id           = body.get("user_id")
    session_token     = body.get("session_token") or ""
    confidence        = body.get("confidence_level") or "medium"
    skipped_steps     = body.get("skipped_steps") or []
    identified_claims = body.get("identified_claims") or []
    source_credible   = body.get("source_credible")
    bias_detected     = bool(body.get("bias_detected"))
    evidence_assessed = bool(body.get("evidence_assessed"))
    confidence_after  = body.get("confidence_after")     # v6.0
    confidence_before = body.get("confidence_before")    # v6.0

    db = Session(engine)
    try:
        feedback_items: list = []
        triggered_lessons: list = []

        # ── Basic step-completion feedback ───────────────────────────────────
        total_steps = int(body.get("total_steps") or 8)
        steps_done = total_steps - len(skipped_steps)
        if steps_done >= max(total_steps - 1, 1):
            feedback_items.append({"type": "good", "text": "You completed most analysis steps — thorough work.", "step_name": None, "learn_more": None})
        elif steps_done >= total_steps // 2:
            feedback_items.append({"type": "warn", "text": f"You completed {steps_done} of {total_steps} steps. Skipping steps means missing context.", "step_name": None, "learn_more": None})
        else:
            feedback_items.append({"type": "bad", "text": f"Only {steps_done} of {total_steps} steps completed — try not to skip.", "step_name": None, "learn_more": None})

        # ── "What You Missed" contextual feedback (v6.0) ─────────────────────
        # Each skipped step gets a specific explanation of WHY it matters,
        # not just "you skipped it". Turns assessment into instruction.
        _STEP_MISSED_MESSAGES = {
            "claims":        ("Identifying the core claim helps you know exactly what to verify — "
                              "without it, you're checking vague impressions, not facts.",
                              "claim_detection"),
            "source":        ("Checking the source is the single most efficient step: "
                              "old news, satire sites, and impersonation domains often reveal "
                              "themselves immediately.",
                              "source_verification"),
            "bias":          ("Emotional or partisan language can make false content feel true. "
                              "Skipping bias detection means you may be influenced without realising it.",
                              "bias_detection"),
            "evidence":      ("Without checking evidence, any claim — true or false — looks equally valid. "
                              "Evidence quality is what separates fact from assertion.",
                              "evidence_evaluation"),
            "purpose":       ("Understanding why content was created (to inform vs. to persuade) "
                              "changes how you should weigh it.",
                              "general"),
            "audience":      ("Content aimed at a specific partisan audience is often framed to "
                              "reinforce existing beliefs rather than inform.",
                              "bias_detection"),
            "logic":         ("Logical fallacies (false dichotomy, slippery slope, ad hominem) are "
                              "common in misleading content. Checking logic is a Bloom's Level 4 skill.",
                              "evidence_evaluation"),
            "corroboration": ("Old news is frequently reshared as if it's current. "
                              "Checking whether other sources cover the same story is the fastest "
                              "way to catch recycled misinformation.",
                              "source_verification"),
        }
        for step in skipped_steps:
            if step in _STEP_MISSED_MESSAGES:
                msg, lesson_key = _STEP_MISSED_MESSAGES[step]
                feedback_items.append({
                    "type":       "missed",
                    "text":       f"You skipped checking {step}: {msg}",
                    "step_name":  step,
                    "learn_more": lesson_key,
                })

        if "source" in skipped_steps or source_credible is None:
            feedback_items.append({"type": "warn", "text": "Source credibility was not assessed — this is a key step.", "step_name": "source", "learn_more": "source_verification"})
        elif source_credible == "yes":
            feedback_items.append({"type": "good", "text": "You checked the source credibility.", "step_name": None, "learn_more": None})

        if bias_detected:
            feedback_items.append({"type": "good", "text": "Good catch — you flagged potential bias in the content.", "step_name": None, "learn_more": None})

        if evidence_assessed:
            feedback_items.append({"type": "good", "text": "You assessed the evidence — that's the most important step.", "step_name": None, "learn_more": None})
        elif "evidence" in skipped_steps:
            feedback_items.append({"type": "bad", "text": "Evidence assessment was skipped — claims need checking against evidence.", "step_name": "evidence", "learn_more": "evidence_evaluation"})

        # ── Metacognitive Calibration Gap Detection (v6.0) ───────────────────
        # Based on Kruger & Dunning (1999): detects high-confidence + low-thoroughness.
        # The calibration_gap float is returned to the frontend for display and
        # is also stored in confidence_snapshots.calibration_flag.
        confidence_weight = {"high": 1.0, "medium": 0.5, "low": 0.0}.get(confidence, 0.5)
        quality_signals   = 0
        quality_max       = 4
        if source_credible == "yes":    quality_signals += 1
        if bias_detected:               quality_signals += 1
        if evidence_assessed:           quality_signals += 1
        if len(skipped_steps) <= 2:     quality_signals += 1
        quality_ratio   = quality_signals / quality_max
        calibration_gap = round(confidence_weight - quality_ratio, 2)

        if calibration_gap >= 0.5:
            feedback_items.append({
                "type": "calibration",
                "text": (
                    "Your confidence level was high, but several key evaluation steps were skipped "
                    "or incomplete. High confidence without thorough checking is a known pattern in "
                    "how misinformation spreads — we all do it sometimes. "

                ),
                "step_name":  None,
                "learn_more": "metacognition_bias",
            })

        # ── Save confidence snapshot (after confidence) ───────────────────────
        if submission_id and session_token:
            try:
                # Try to update existing row (from confidence_before saved at /analyze time)
                existing = db.execute(sa.text(
                    "SELECT id FROM confidence_snapshots "
                    "WHERE submission_id = :sid AND session_token = :tok LIMIT 1"
                ), {"sid": submission_id, "tok": session_token}).fetchone()

                if existing:
                    delta = None
                    if confidence_after is not None:
                        before_row = db.execute(sa.text(
                            "SELECT confidence_before FROM confidence_snapshots WHERE id = :id"
                        ), {"id": existing.id}).fetchone()
                        if before_row and before_row.confidence_before is not None:
                            delta = confidence_after - before_row.confidence_before

                    db.execute(sa.text("""
                        UPDATE confidence_snapshots
                        SET confidence_after  = :ca,
                            confidence_delta  = :delta,
                            calibration_flag  = :cflag,
                            confidence_label  = :clabel
                        WHERE id = :id
                    """), {
                        "ca":     confidence_after,
                        "delta":  delta,
                        "cflag":  1 if calibration_gap >= 0.5 else 0,
                        "clabel": confidence,
                        "id":     existing.id,
                    })
                else:
                    delta = None
                    if confidence_before is not None and confidence_after is not None:
                        delta = confidence_after - confidence_before
                    db.execute(sa.text("""
                        INSERT INTO confidence_snapshots
                            (submission_id, user_id, session_token,
                             confidence_before, confidence_after,
                             confidence_delta, calibration_flag, confidence_label)
                        VALUES (:sid, :uid, :tok, :cb, :ca, :delta, :cflag, :clabel)
                    """), {
                        "sid":   submission_id,
                        "uid":   user_id,
                        "tok":   session_token,
                        "cb":    confidence_before,
                        "ca":    confidence_after,
                        "delta": delta,
                        "cflag": 1 if calibration_gap >= 0.5 else 0,
                        "clabel": confidence,
                    })
                db.commit()
            except Exception as cs_err:
                logger.warning(f"[user-evaluation] confidence_snapshot save failed: {cs_err}")

        # ── Run lesson triggers ───────────────────────────────────────────────
        try:
            from services.lesson_trigger import compute_triggers

            comparison_data = {
                "missed_bias":   not bias_detected,
                "missed_claims": len(identified_claims) == 0,
            }
            user_eval_data = {
                "skipped_steps":      skipped_steps,
                "confidence_level":   confidence,
                "source_credible":    source_credible,
                "bias_detected":      bias_detected,
                "user_id":            user_id,
                "identified_claims":  identified_claims,
                "evidence_assessed":  evidence_assessed,
                "time_spent_seconds": body.get("time_spent_seconds") or 0,
                "calibration_gap":    calibration_gap,   # v6.0: passed to trigger rules
            }

            # Inject calibration lesson if gap is high
            if calibration_gap >= 0.5:
                triggered_lessons.append({
                    "lesson_key":     "metacognition_bias",
                    "trigger_reason": f"Confidence was '{confidence}' but quality score was {quality_signals}/{quality_max}",
                })

            raw_triggers = compute_triggers(comparison_data, user_eval_data, db)

            all_triggers = triggered_lessons + raw_triggers
            seen_keys: set = set()
            deduped = []
            for t in all_triggers:
                if t["lesson_key"] not in seen_keys:
                    seen_keys.add(t["lesson_key"])
                    deduped.append(t)

            if submission_id and deduped:
                for t in deduped:
                    lesson_row = db.execute(
                        sa.text("SELECT id FROM lessons WHERE lesson_key = :k LIMIT 1"),
                        {"k": t["lesson_key"]},
                    ).fetchone()
                    if lesson_row:
                        db.execute(
                            sa.text(
                                "INSERT INTO lessons_triggered "
                                "(submission_id, lesson_id, trigger_reason) "
                                "VALUES (:sid, :lid, :reason)"
                            ),
                            {"sid": submission_id, "lid": lesson_row.id, "reason": t.get("trigger_reason", "")},
                        )
                db.commit()

            triggered_lessons = [
                {"key": t["lesson_key"], "trigger_reason": t.get("trigger_reason", "")}
                for t in deduped
            ]

        except Exception as te:
            logger.warning(f"[user-evaluation] lesson trigger error: {te}")

        # ── mindmap unlock: unlock nodes for eval questions user answered ─────
        # Any active eval question with a mindmap_node_id that was NOT skipped
        # will unlock that node for the logged-in user.
        if user_id:
            try:
                eq_rows = db.execute(sa.text(
                    "SELECT step_number, mindmap_node_id FROM eval_questions "
                    "WHERE is_active = 1 AND mindmap_node_id IS NOT NULL"
                )).fetchall()
                for eq in eq_rows:
                    step_key = str(eq.step_number)
                    if step_key not in skipped_steps:
                        db.execute(
                            sa.text("""
                                INSERT INTO mindmap_progress (user_id, map_id, node_id, viewed_at)
                                VALUES (:uid, 'main', :node_id, NOW())
                                ON DUPLICATE KEY UPDATE viewed_at = viewed_at
                            """),
                            {"uid": user_id, "node_id": eq.mindmap_node_id},
                        )
                db.commit()
            except Exception as mu_err:
                logger.warning(f"[user-evaluation] mindmap unlock error: {mu_err}")

        return {
            "user_evaluation_id": None,
            "comparison": {
                "confidence_level":  confidence,
                "triggered_lessons": triggered_lessons,
                "feedback_items":    feedback_items,
                "feedback_summary":  "",
                "lesson_context_map": {},
                "calibration_gap":   calibration_gap,
            },
        }
    except Exception as e:
        logger.error(f"[user-evaluation] error: {e}")
        return {"user_evaluation_id": None, "comparison": {"feedback_items": [], "triggered_lessons": []}}
    finally:
        db.close()


# ── POST /user-reflection ─────────────────────────────────────────────────────
# Stores user's free-text reflection. Persisted to user_reflections table.
@router.post("/user-reflection")
async def post_user_reflection(body: dict):
    """Persist free-text reasoning journal entry to user_reflections."""
    session_token = body.get("session_token") or ""
    submission_id = body.get("submission_id") or body.get("evaluation_id")
    user_id       = body.get("user_id")

    what_noticed     = (body.get("what_noticed") or "").strip() or None
    still_uncertain  = (body.get("still_uncertain") or "").strip() or None
    would_check_next = (body.get("would_check_next") or "").strip() or None
    free_reasoning   = (body.get("reasoning") or body.get("free_reasoning") or "").strip() or None
    verdict_position = body.get("position") or body.get("verdict_position") or None
    stage            = body.get("stage") or "post_verdict"

    # Compute total word count across all text fields (proxy for reflection depth)
    all_text   = " ".join(filter(None, [what_noticed, still_uncertain, would_check_next, free_reasoning]))
    word_count = len(all_text.split()) if all_text.strip() else 0

    # Bloom's level heuristic with minimum word-count validation (b4):
    #   Levels are only awarded if the field meets a minimum depth threshold.
    #   This prevents one-word answers from claiming high Bloom's levels.
    #   L4 ≥ 30 words across all fields; L5 ≥ 50 words across all fields.
    bloom_level = 1
    if what_noticed:     bloom_level = max(bloom_level, 3)
    if still_uncertain and word_count >= 30:  bloom_level = max(bloom_level, 4)
    if would_check_next and word_count >= 50: bloom_level = max(bloom_level, 5)

    db = Session(engine)
    try:
        result = db.execute(sa.text("""
            INSERT INTO user_reflections
                (submission_id, user_id, session_token, stage,
                 what_noticed, still_uncertain, would_check_next,
                 free_reasoning, verdict_position, bloom_level, total_word_count)
            VALUES
                (:sid, :uid, :tok, :stage,
                 :wn, :su, :wcn,
                 :fr, :vp, :bl, :wc)
        """), {
            "sid":   submission_id,
            "uid":   user_id,
            "tok":   session_token,
            "stage": stage,
            "wn":    what_noticed,
            "su":    still_uncertain,
            "wcn":   would_check_next,
            "fr":    free_reasoning,
            "vp":    verdict_position,
            "bl":    bloom_level,
            "wc":    word_count,
        })
        db.commit()
        new_id = result.lastrowid
        return {
            "status":      "ok",
            "id":          new_id,
            "bloom_level": bloom_level,
            "word_count":  word_count,
            "message":     "Reflection saved.",
        }
    except Exception as e:
        logger.warning(f"[user-reflection] DB save failed: {e}")
        db.rollback()
        return {"status": "ok", "message": "Reflection noted (not persisted)."}
    finally:
        db.close()


# ── POST /analyze/reasoning-journal ──────────────────────────────────────────
# Dedicated endpoint for staged Reasoning Journal entries.
# Accepts entries at post_eval, post_evidence, and post_verdict stages.
@router.post("/analyze/reasoning-journal", response_model=ReasoningJournalResponse)
async def reasoning_journal(entry: ReasoningJournalEntry):
    """
    Save a Reasoning Journal entry for Bloom's L4–5 analysis.
    Three prompts map to analysis (L4) and evaluation (L5):
      what_noticed     → "What did I notice about these articles?"
      still_uncertain  → "What am I still uncertain about?"
      would_check_next → "What would I check next if this mattered to me?"
    """
    what_noticed     = (entry.what_noticed or "").strip() or None
    still_uncertain  = (entry.still_uncertain or "").strip() or None
    would_check_next = (entry.would_check_next or "").strip() or None
    free_reasoning   = (entry.free_reasoning or "").strip() or None

    all_text   = " ".join(filter(None, [what_noticed, still_uncertain, would_check_next, free_reasoning]))
    word_count = len(all_text.split()) if all_text.strip() else 0

    bloom_level = entry.bloom_level or 1
    if what_noticed:                              bloom_level = max(bloom_level, 3)
    if still_uncertain and word_count >= 30:      bloom_level = max(bloom_level, 4)
    if would_check_next and word_count >= 50:     bloom_level = max(bloom_level, 5)

    db = Session(engine)
    try:
        result = db.execute(sa.text("""
            INSERT INTO user_reflections
                (submission_id, user_id, session_token, stage,
                 what_noticed, still_uncertain, would_check_next,
                 free_reasoning, verdict_position, bloom_level, total_word_count)
            VALUES
                (:sid, :uid, :tok, :stage,
                 :wn, :su, :wcn,
                 :fr, :vp, :bl, :wc)
        """), {
            "sid":   entry.submission_id,
            "uid":   entry.user_id,
            "tok":   entry.session_token,
            "stage": entry.stage,
            "wn":    what_noticed,
            "su":    still_uncertain,
            "wcn":   would_check_next,
            "fr":    free_reasoning,
            "vp":    entry.verdict_position,
            "bl":    bloom_level,
            "wc":    word_count,
        })
        db.commit()
        return ReasoningJournalResponse(
            id          = result.lastrowid,
            stage       = entry.stage,
            bloom_level = bloom_level,
            saved       = True,
        )
    except Exception as e:
        logger.warning(f"[reasoning-journal] DB save failed: {e}")
        db.rollback()
        return ReasoningJournalResponse(id=0, stage=entry.stage, bloom_level=bloom_level, saved=False)
    finally:
        db.close()


# ── POST /analyze/confidence-snapshot ────────────────────────────────────────
# Save or update the before/after confidence reading for a submission.
@router.post("/analyze/confidence-snapshot", response_model=ConfidenceSnapshotResponse)
async def confidence_snapshot(snap: ConfidenceSnapshotRequest):
    """
    Upsert confidence_before / confidence_after for a submission.
    Called twice by the frontend:
      1. At the start (before retrieval) — sends confidence_before only
      2. After reviewing evidence cards  — sends confidence_after only
    """
    db = Session(engine)
    try:
        existing = None
        if snap.submission_id and snap.session_token:
            existing = db.execute(sa.text(
                "SELECT id, confidence_before FROM confidence_snapshots "
                "WHERE submission_id = :sid AND session_token = :tok LIMIT 1"
            ), {"sid": snap.submission_id, "tok": snap.session_token}).fetchone()

        if existing:
            delta = None
            if snap.confidence_after is not None and existing.confidence_before is not None:
                delta = snap.confidence_after - existing.confidence_before
            db.execute(sa.text("""
                UPDATE confidence_snapshots
                SET confidence_after  = COALESCE(:ca, confidence_after),
                    confidence_before = COALESCE(:cb, confidence_before),
                    confidence_delta  = :delta,
                    confidence_label  = COALESCE(:clabel, confidence_label)
                WHERE id = :id
            """), {
                "ca":     snap.confidence_after,
                "cb":     snap.confidence_before,
                "delta":  delta,
                "clabel": snap.confidence_label,
                "id":     existing.id,
            })
            db.commit()
            row = db.execute(sa.text(
                "SELECT id, confidence_before, confidence_after, confidence_delta, calibration_flag "
                "FROM confidence_snapshots WHERE id = :id"
            ), {"id": existing.id}).fetchone()
            return ConfidenceSnapshotResponse(
                id                = row.id,
                confidence_before = row.confidence_before,
                confidence_after  = row.confidence_after,
                confidence_delta  = row.confidence_delta,
                calibration_flag  = bool(row.calibration_flag),
            )
        else:
            result = db.execute(sa.text("""
                INSERT INTO confidence_snapshots
                    (submission_id, user_id, session_token,
                     confidence_before, confidence_after, confidence_label)
                VALUES (:sid, :uid, :tok, :cb, :ca, :clabel)
            """), {
                "sid":    snap.submission_id,
                "uid":    snap.user_id,
                "tok":    snap.session_token,
                "cb":     snap.confidence_before,
                "ca":     snap.confidence_after,
                "clabel": snap.confidence_label,
            })
            db.commit()
            return ConfidenceSnapshotResponse(
                id                = result.lastrowid,
                confidence_before = snap.confidence_before,
                confidence_after  = snap.confidence_after,
                confidence_delta  = None,
                calibration_flag  = False,
            )
    except Exception as e:
        logger.warning(f"[confidence-snapshot] error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Snapshot save failed.")
    finally:
        db.close()



# ── POST /analyze/change-your-mind ───────────────────────────────────────────
# System 2 switching prompt (dual process theory, checklist d3).
# Shown before verdict submission: "What's one thing that could change your mind?"
# Stored alongside the reflection so the data is available for analysis.
@router.post("/analyze/change-your-mind")
async def change_your_mind(body: dict):
    """
    Record the user's answer to the mental-contrasting pre-verdict prompt.
    This is a well-established System 2 activation technique: asking people
    to articulate a falsifying condition slows down fast, confirmatory thinking.
    The answer is stored as a 'post_verdict' reflection at bloom_level 4 (analysis).
    """
    session_token = body.get("session_token") or ""
    submission_id = body.get("submission_id") or body.get("evaluation_id")
    user_id       = body.get("user_id")
    answer        = (body.get("answer") or body.get("change_your_mind") or "").strip() or None

    if not session_token:
        raise HTTPException(status_code=422, detail="session_token is required.")

    word_count  = len(answer.split()) if answer else 0
    bloom_level = 4 if (answer and word_count >= 5) else 1

    db = Session(engine)
    try:
        result = db.execute(sa.text("""
            INSERT INTO user_reflections
                (submission_id, user_id, session_token, stage,
                 free_reasoning, bloom_level, total_word_count)
            VALUES
                (:sid, :uid, :tok, 'post_verdict',
                 :text, :bl, :wc)
        """), {
            "sid":  submission_id,
            "uid":  user_id,
            "tok":  session_token,
            "text": f"[change_your_mind] {answer}" if answer else None,
            "bl":   bloom_level,
            "wc":   word_count,
        })
        db.commit()
        return {"status": "ok", "id": result.lastrowid, "bloom_level": bloom_level}
    except Exception as e:
        logger.warning(f"[change-your-mind] DB save failed: {e}")
        db.rollback()
        return {"status": "ok", "message": "Prompt noted (not persisted)."}
    finally:
        db.close()


# ── POST /analyze/share-verdict ───────────────────────────────────────────────
# L6 Create task (Bloom's highest level) + UNESCO MIL Create & Communicate (b3, m2).
# Users write a 2-3 sentence corrective summary or fact-check post.
# Stored in user_created_content; opt-in cohort sharing available.
@router.post("/analyze/share-verdict")
async def share_verdict(body: dict):
    """
    Save a user-authored corrective summary or fact-check post.
    This is the single highest-leverage MIL activity — the L6 Create task.
    body.is_shared=true makes it visible on the opt-in cohort feed.
    """
    session_token = body.get("session_token") or ""
    submission_id = body.get("submission_id") or body.get("evaluation_id")
    user_id       = body.get("user_id")
    body_text     = (body.get("body") or body.get("summary") or "").strip()
    content_type  = body.get("content_type") or "corrective_summary"
    is_shared     = int(bool(body.get("is_shared", False)))

    if not session_token:
        raise HTTPException(status_code=422, detail="session_token is required.")
    if not body_text or len(body_text.strip()) < 10:
        raise HTTPException(status_code=422, detail="body must be at least 10 characters.")
    if content_type not in ("corrective_summary", "reflection_post", "cohort_share"):
        content_type = "corrective_summary"

    db = Session(engine)
    try:
        result = db.execute(sa.text("""
            INSERT INTO user_created_content
                (user_id, session_token, submission_id, content_type, body, is_shared, bloom_level)
            VALUES (:uid, :tok, :sid, :ctype, :body, :shared, 6)
        """), {
            "uid":    user_id,
            "tok":    session_token,
            "sid":    submission_id,
            "ctype":  content_type,
            "body":   body_text,
            "shared": is_shared,
        })
        db.commit()
        return {
            "status":   "ok",
            "id":       result.lastrowid,
            "is_shared": bool(is_shared),
            "bloom_level": 6,
            "message":  "Your verdict summary has been saved." + (
                " It will appear on the cohort feed." if is_shared else ""
            ),
        }
    except Exception as e:
        logger.warning(f"[share-verdict] DB save failed: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not save verdict summary.")
    finally:
        db.close()



# ── POST /analyze/challenge-gate ──────────────────────────────────────────────
# Metacognitive Calibration (v7.0)
#
# Called by the frontend BEFORE the user can submit their final verdict.
# Implements a three-framework Socratic challenge loop:
#
#   Inoculation Theory     → pre-bunk the user's detected reasoning pattern
#   Dual Process Theory    → activate System 2 with a targeted evidence question
#   Bloom's Taxonomy       → reject shallow L1/L2 reasoning, demand L3+
#
# The gate is non-punitive: after 2 challenge rounds the user always passes.
# challenge_flag is stored on the confidence_snapshot for research analysis.
#
# Returns gate="pass" (submit enabled) or gate="challenge" (blocked + prompt).
@router.post("/analyze/challenge-gate", response_model=ChallengeGateResponse)
async def challenge_gate(req: ChallengeGateRequest):
    """
    Metacognitive calibration gate — runs before verdict submission.

    Decision logic (in priority order):
      1. If challenge_round >= 2 → always pass (frustration guard)
      2. If challenge_response provided → validate quality; pass if sufficient
      3. Evaluate reasoning signals and issue the most appropriate challenge:
         - bloom_level <= 2 AND word_count < 20 → bloom_upgrade
         - calibration_gap >= 0.5 AND high confidence → inoculation
         - skipped critical steps → slow_down (dual process)
      4. If no triggers → pass
    """
    import difflib

    round_num = req.challenge_round or 0

    # ── Guard: max 2 challenge rounds to avoid frustration loops ─────────────
    if round_num >= 2:
        _flag_challenge_pass(req, round_num)
        return ChallengeGateResponse(gate="pass", round=round_num)

    # ── If this is a response round, validate the reply quality ───────────────
    if round_num >= 1 and req.challenge_response:
        reply = (req.challenge_response or "").strip()
        words = reply.split()
        word_count = len(words)

        # Minimum word gate
        if word_count < 15:
            return _issue_challenge(
                challenge_type   = "slow_down",
                prompt           = (
                    "You're almost there — can you say a bit more? "
                    "Try to mention at least one specific piece of evidence "
                    "from the articles that influenced your thinking."
                ),
                context_note     = "Your response was too brief for a meaningful reflection.",
                min_words        = 15,
                round_num        = round_num,
                framework_label  = "Dual Process Theory",
            )

        # Semantic non-repetition check: reject if reply is just restated verdict
        # without any evidence-grounded language
        evidence_keywords = {
            "source", "article", "evidence", "because", "published", "claim",
            "fact", "says", "stated", "report", "study", "found", "shows",
            "according", "cited", "referenced", "contradicts", "supports",
            "unclear", "uncertain", "missing", "lacks", "consistent",
        }
        reply_lower = set(reply.lower().split())
        has_evidence_language = bool(reply_lower & evidence_keywords)

        if not has_evidence_language and word_count < 25:
            return _issue_challenge(
                challenge_type   = "inoculation",
                prompt           = (
                    "That's a start — but try grounding your answer in the articles themselves. "
                    "What did a specific source say, or what was missing from the evidence "
                    "that made you reach that conclusion?"
                ),
                context_note     = (
                    "Reasoning that isn't tied to specific evidence is a common way "
                    "misinformation takes hold — we rely on how something feels rather "
                    "than what it shows."
                ),
                min_words        = 20,
                round_num        = round_num,
                framework_label  = "Inoculation Theory",
            )

        # Passed validation — flag in DB and allow through
        _flag_challenge_pass(req, round_num)
        return ChallengeGateResponse(gate="pass", round=round_num)

    # ── Round 0: evaluate reasoning quality signals ───────────────────────────
    bloom_level      = req.bloom_level or 1
    calibration_gap  = req.calibration_gap or 0.0
    skipped          = req.skipped_steps or []
    confidence       = req.confidence_level or "medium"
    word_count       = req.word_count or 0
    verdict          = req.verdict_position or "uncertain"

    critical_steps   = {"source", "evidence", "bias"}
    skipped_critical = critical_steps & set(skipped)

    # ── Priority 1: Bloom's upgrade — shallow reasoning with no reflection ────
    # Fires when bloom_level <= 2 AND the user wrote almost nothing.
    # UNESCO MIL Evaluate competency requires at least Apply (L3).
    if bloom_level <= 2 and word_count < 20:
        return _issue_challenge(
            challenge_type  = "bloom_upgrade",
            prompt          = (
                "Before submitting, take one more moment: "
                "What specific thing from the articles — a source detail, a claim, "
                "a gap in the evidence — actually shaped how you're thinking about this? "
                "Even one concrete observation counts."
            ),
            context_note    = (
                "Your reflection is at a surface level right now. "
                "Critical thinking requires applying what you observed, "
                "not just stating a conclusion."
            ),
            min_words       = 20,
            round_num       = round_num,
            framework_label = "Bloom's Taxonomy (Apply → L3)",
        )

    # ── Priority 2: Inoculation — high confidence + low thoroughness ──────────
    # Based on Inoculation Theory: pre-bunk the specific reasoning weakness
    # before the user commits to a potentially flawed verdict.
    if calibration_gap >= 0.5 and confidence == "high":
        bias_pattern = _detect_reasoning_pattern(skipped, verdict)
        return _issue_challenge(
            challenge_type  = "inoculation",
            prompt          = (
                f"{bias_pattern['warning']} "
                "Before you lock in your verdict: what's the strongest piece of "
                "evidence that could prove you *wrong*? If you can't think of one, "
                "that's worth reflecting on."
            ),
            context_note    = (
                "You reported high confidence, but several key verification steps "
                "were incomplete. This is a known pattern — not a flaw in you, "
                "but something worth noticing in your reasoning process."
            ),
            min_words       = 15,
            round_num       = round_num,
            framework_label = "Inoculation Theory",
        )

    # ── Priority 3: Dual Process — skipped critical steps ─────────────────────
    # Dual Process Theory: when System 1 (fast/intuitive) processing is detected,
    # force a System 2 (slow/deliberate) engagement before verdict.
    if len(skipped_critical) >= 2:
        missed = " and ".join(s.capitalize() for s in sorted(skipped_critical))
        return _issue_challenge(
            challenge_type  = "slow_down",
            prompt          = (
                f"You skipped checking {missed}. "
                "Those steps are where misleading content most often hides. "
                "Before deciding: if you were to check one of those now, "
                "what's the first thing you'd look for?"
            ),
            context_note    = (
                "Fast, intuitive judgements are efficient — but they're also "
                "where misinformation does its work. This prompt is designed "
                "to engage your slower, more deliberate thinking."
            ),
            min_words       = 15,
            round_num       = round_num,
            framework_label = "Dual Process Theory (System 2)",
        )

    # ── No triggers: pass ─────────────────────────────────────────────────────
    return ChallengeGateResponse(gate="pass", round=round_num)


def _issue_challenge(
    challenge_type: str,
    prompt: str,
    context_note: str,
    min_words: int,
    round_num: int,
    framework_label: str,
) -> ChallengeGateResponse:
    """Helper that constructs a ChallengeGateResponse with gate=challenge."""
    return ChallengeGateResponse(
        gate             = "challenge",
        challenge_type   = challenge_type,
        challenge_prompt = prompt,
        context_note     = context_note,
        min_words        = min_words,
        round            = round_num,
        framework_label  = framework_label,
    )


def _detect_reasoning_pattern(skipped: list, verdict: str) -> dict:
    """
    Heuristically identify the most likely reasoning weakness to pre-bunk.
    Returns an inoculation warning string specific to the detected pattern.
    Used by the inoculation challenge branch.
    """
    if "source" in skipped and "bias" in skipped:
        return {
            "warning": (
                "⚠️ When we skip source and bias checks but still feel confident, "
                "we're often relying on how familiar or emotionally resonant content feels — "
                "not on its actual credibility. This is called the familiarity heuristic."
            )
        }
    if "evidence" in skipped:
        return {
            "warning": (
                "⚠️ Reaching a confident verdict without checking the evidence "
                "is one of the most common ways misinformation spreads — even among "
                "well-informed people. It's not about intelligence; it's about process."
            )
        }
    if "corroboration" in skipped:
        return {
            "warning": (
                "⚠️ A single source — even a credible one — can report inaccurately. "
                "High confidence based on one source, without corroboration, "
                "is a common pattern in how misinformation spreads."
            )
        }
    # Generic fallback
    return {
        "warning": (
            "⚠️ High confidence before thorough checking is a known pattern in "
            "how misinformation takes hold. The goal isn't to be uncertain — "
            "it's to make sure your confidence is earned."
        )
    }


def _flag_challenge_pass(req: ChallengeGateRequest, round_num: int) -> None:
    """
    Update calibration_flag on the confidence_snapshot to record how many
    challenge rounds occurred. Stored as: 0=no challenge, 1=passed R1, 2=passed R2.
    Runs best-effort; never raises.
    """
    if not req.submission_id or not req.session_token:
        return
    try:
        db = Session(engine)
        db.execute(sa.text("""
            UPDATE confidence_snapshots
            SET calibration_flag = :flag
            WHERE submission_id = :sid AND session_token = :tok
            ORDER BY id DESC LIMIT 1
        """), {
            "flag": min(round_num, 2),
            "sid":  req.submission_id,
            "tok":  req.session_token,
        })
        db.commit()
        db.close()
    except Exception as e:
        logger.warning(f"[challenge-gate] calibration_flag update failed: {e}")


# ── GET /analyze/mbfc-lookup ──────────────────────────────────────────────────
@router.get("/analyze/mbfc-lookup")
async def mbfc_lookup(domain: str, req: Request = None):
    """
    Look up a domain in mbfc_domains and return the MBFC page URL.
    """
    if not domain:
        raise HTTPException(status_code=422, detail="domain is required.")

    domain = domain.lower().replace("www.", "").strip("/")

    db = Session(engine)
    try:
        row = db.execute(
            sa.text("SELECT domain, notes_url FROM mbfc_domains WHERE domain = :d LIMIT 1"),
            {"d": domain},
        ).fetchone()

        if not row:
            return {"domain": domain, "mbfc_url": None, "found": False}

        return {
            "domain":   row.domain,
            "mbfc_url": row.notes_url,
            "found":    True,
        }
    finally:
        db.close()


# ── GET /eval-questions (public) ──────────────────────────────────────────────
@router.get("/eval-questions")
async def get_eval_questions():
    """
    Return active eval questions ordered by step_number.
    Used by index.js to build the guided evaluation step cards.
    Falls back gracefully if the table doesn't exist yet (new install).
    """
    import json as _json
    import sqlalchemy as _sa
    from sqlalchemy.orm import Session as _Session
    db = _Session(engine)
    try:
        rows = db.execute(_sa.text(
            "SELECT id, step_number, title, step_label, prompt, hint, input_type, options "
            "FROM eval_questions "
            "WHERE is_active = 1 ORDER BY step_number, id"
        )).fetchall()

        # Try to fetch step_link columns — only exist after migration runs
        link_map = {}
        try:
            link_rows = db.execute(_sa.text(
                "SELECT id, step_link_type, step_link_value, mindmap_node_id FROM eval_questions WHERE is_active = 1"
            )).fetchall()
            for lr in link_rows:
                link_map[lr[0]] = {
                    "step_link_type": lr[1],
                    "step_link_value": lr[2],
                    "mindmap_node_id": lr[3] if len(lr) > 3 else None,
                }
        except Exception:
            pass

        # Fetch all active branches in one query, keyed by question_id
        branch_rows = []
        try:
            branch_rows = db.execute(_sa.text(
                "SELECT id, question_id, trigger_condition, trigger_value, "
                "followup_prompt, followup_type, content_type, "
                "lesson_id, quiz_question_id, content_url, sort_order "
                "FROM eval_question_branches "
                "WHERE is_active = 1 ORDER BY question_id, sort_order, id"
            )).fetchall()
        except Exception:
            pass  # table may not exist on older installs

        branches_by_qid = {}
        for b in branch_rows:
            bd = dict(b._mapping)
            branches_by_qid.setdefault(bd["question_id"], []).append(bd)

        result = []
        for r in rows:
            row = dict(r._mapping)
            if isinstance(row.get("options"), str) and row["options"]:
                try:
                    row["options"] = _json.loads(row["options"])
                except Exception:
                    pass
            row["branches"] = branches_by_qid.get(row["id"], [])
            row.update(link_map.get(row["id"], {"step_link_type": None, "step_link_value": None, "mindmap_node_id": None}))
            result.append(row)
        return result
    except Exception:
        # If anything goes wrong (e.g. table truly missing), return empty list
        # so index.js falls back to _defaultEvalSteps() gracefully.
        return []
    finally:
        db.close()


# ── POST /analyze/validate-claim ──────────────────────────────────────────────
# Called by the no-claims flow to check whether a user-typed string is a
# verifiable factual claim before running the full pipeline on it.
# Returns {is_claim: bool, reason: str}.
@router.post("/analyze/validate-claim")
async def validate_claim(body: dict):
    """
    Lightweight NLP gate: decides whether the submitted text looks like a
    specific, verifiable factual claim (vs. an opinion, question, or vague topic).
    Uses spaCy for entity/verb detection; no external API call required.
    Returns 503 if NLP models are unavailable so the frontend can fall back.
    """
    claim_text = (body.get("claim_text") or "").strip()
    if not claim_text:
        raise HTTPException(status_code=422, detail="claim_text is required.")

    try:
        nlp = ModelRegistry.nlp()
    except Exception:
        raise HTTPException(status_code=503, detail="NLP model unavailable.")

    doc = nlp(claim_text)

    # Heuristics for a verifiable claim:
    #   - Contains at least one named entity OR a cardinal/percentage number
    #   - Contains at least one verb (ROOT or aux)
    #   - Is not phrased as a question
    has_entity  = any(ent.label_ in {"PERSON","ORG","GPE","NORP","FAC","LOC","DATE",
                                      "TIME","PERCENT","MONEY","QUANTITY","CARDINAL"}
                      for ent in doc.ents)
    has_number  = any(t.like_num or t.is_currency for t in doc)
    has_verb    = any(t.pos_ in {"VERB", "AUX"} for t in doc)
    is_question = claim_text.rstrip().endswith("?")

    is_claim = (has_entity or has_number) and has_verb and not is_question

    if is_claim:
        reason = "The text contains a specific, verifiable factual statement."
    elif is_question:
        reason = "The input is phrased as a question rather than a claim."
    elif not has_verb:
        reason = "The input doesn't appear to be a complete statement."
    else:
        reason = ("The input doesn't appear to be a specific, verifiable factual statement. "
                  "Try including a named person, organisation, place, number, or date.")

    return {"is_claim": is_claim, "reason": reason}


# ── POST /analyze/user-claim ──────────────────────────────────────────────────
# Runs the full analysis pipeline on a user-supplied claim when the system
# detected no claims in the original submission.
@router.post("/analyze/user-claim")
async def user_claim_pipeline(body: dict, background_tasks: BackgroundTasks):
    """
    Run the retrieval pipeline on a claim typed by the user in the no-claims
    fallback flow.  The response shape matches POST /analyze so the frontend
    can reuse the same render path.
    """
    claim_text    = (body.get("claim_text") or "").strip()
    submission_id = body.get("submission_id")
    session_token = body.get("session_token") or ""
    user_id       = body.get("user_id")

    if not claim_text or len(claim_text) < 10:
        raise HTTPException(status_code=422, detail="claim_text must be at least 10 characters.")

    # Re-use the same AnalyzeRequest shape that the main pipeline expects
    from schemas import AnalyzeRequest
    fake_request = AnalyzeRequest(
        text          = claim_text,
        input_type    = "text",
        session_token = session_token or "00000000000000000000000000000000",
        user_id       = user_id,
    )

    try:
        loop   = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, functools.partial(_pipeline.run, fake_request)),
            timeout=_PIPELINE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Pipeline timed out.")
    except Exception as e:
        logger.error(f"[user-claim] pipeline error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Pipeline error.")

    articles = [ArticleResult(**a) for a in result.get("articles", [])]

    # Best-effort: patch the original submission with the user's claim text
    if submission_id:
        def _patch():
            try:
                db2 = Session(engine)
                db2.execute(sa.text(
                    "UPDATE submissions SET parsed_text = :txt WHERE id = :sid"
                ), {"txt": claim_text, "sid": submission_id})
                db2.commit()
                db2.close()
            except Exception:
                pass
        background_tasks.add_task(_patch)

    raw_diversity = result.get("source_diversity")
    diversity_obj = SourceDiversityInfo(**raw_diversity) if raw_diversity else None

    return ArticleRetrievalResponse(
        submission_id    = submission_id or 0,
        evaluation_id    = submission_id or 0,
        articles         = articles,
        keywords         = result.get("keywords", []),
        processing_ms    = result.get("processing_ms", 0),
        live_search_used = result.get("live_search_used", False),
        url_fetch_failed = result.get("url_fetch_failed", False),
        url_fetch_error  = result.get("url_fetch_error", ""),
        source_diversity = diversity_obj,
    )


# ── POST /re-evaluation ───────────────────────────────────────────────────────
# Saves a user's revised score after reviewing the system result.
# Logged in user_reflections as a post_verdict stage entry.
@router.post("/re-evaluation")
async def re_evaluation(body: dict):
    """
    Record a post-verdict score revision.
    body: {user_evaluation_id, revised_score, revised_label, revised_confidence,
           revision_notes, session_token, submission_id, user_id}
    Returns {score_shift} so the frontend can show the delta.
    """
    revised_score      = int(body.get("revised_score") or 50)
    revised_label      = body.get("revised_label") or ""
    revised_confidence = body.get("revised_confidence") or "medium"
    revision_notes     = (body.get("revision_notes") or "").strip() or None
    session_token      = body.get("session_token") or ""
    submission_id      = body.get("submission_id") or body.get("user_evaluation_id")
    user_id            = body.get("user_id")

    db = Session(engine)
    score_shift = 0
    try:
        # Try to read the original score for shift calculation
        if submission_id:
            orig = db.execute(sa.text(
                "SELECT user_score FROM submissions WHERE id = :sid LIMIT 1"
            ), {"sid": submission_id}).fetchone()
            if orig and orig.user_score is not None:
                score_shift = revised_score - int(orig.user_score)

        # Store revision as a post_verdict reflection entry
        note_text = f"[re-evaluation] revised_score={revised_score} label={revised_label}"
        if revision_notes:
            note_text += f" notes={revision_notes}"

        db.execute(sa.text("""
            INSERT INTO user_reflections
                (submission_id, user_id, session_token, stage,
                 free_reasoning, verdict_position, bloom_level, total_word_count)
            VALUES
                (:sid, :uid, :tok, 'post_verdict',
                 :text, :label, 3, :wc)
        """), {
            "sid":   submission_id,
            "uid":   user_id,
            "tok":   session_token,
            "text":  note_text,
            "label": revised_label,
            "wc":    len(note_text.split()),
        })
        db.commit()
        return {"status": "ok", "score_shift": score_shift, "revised_score": revised_score}
    except Exception as e:
        logger.warning(f"[re-evaluation] DB save failed: {e}")
        db.rollback()
        return {"status": "ok", "score_shift": 0, "revised_score": revised_score}
    finally:
        db.close()
