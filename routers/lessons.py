"""
SocialProof — Router: Lessons  v5.0

Changes from v4.1:
  Added full admin CRUD for lessons:
    POST   /lessons                      — create a lesson (admin only)
    PUT    /lessons/{lesson_id}          — update a lesson (admin only)
    DELETE /lessons/{lesson_id}          — delete a lesson (admin only)

  Bug fixes carried over from v4.1:
    mark_lesson_read: UPDATE scoped to caller's own submissions.
    mark_lesson_read_by_id: UPDATE scoped to caller's own user_id / session_token.
"""

import sqlalchemy as sa
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import logger
from database.models import engine
from routers.auth import get_current_user, _verify


def _unlock_mindmap_node(db: Session, user_id: int, node_id: str) -> None:
    """Insert mindmap_progress row; ON DUPLICATE KEY UPDATE silently skips dupes."""
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

router = APIRouter()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_optional_user(authorization: Optional[str], request: Request):
    """Return decoded payload or None (does not raise)."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif request is not None:
        token = request.cookies.get("sp_jwt")
    if not token:
        return None
    try:
        return _verify(token)
    except Exception:
        return None


def _require_admin(authorization: Optional[str], request: Request):
    """Raise 401/403 unless caller is an authenticated admin."""
    payload = _get_optional_user(authorization, request)
    if not payload:
        raise HTTPException(status_code=401, detail="Authorization required.")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return payload


# ── Pydantic models ───────────────────────────────────────────────────────────

class LessonCreate(BaseModel):
    lesson_key:             str           = Field(..., min_length=3, max_length=100)
    title:                  str           = Field(..., min_length=3, max_length=255)
    content:                str           = Field(..., min_length=10)
    topic:                  str           = Field(..., description="claim_detection|source_verification|bias_detection|evidence_evaluation|general")
    difficulty:             str           = Field("beginner", description="beginner|intermediate|advanced")
    mil_skill:              Optional[str] = Field(None, max_length=50)
    sort_order:             Optional[int] = None
    prerequisite_lesson_id: Optional[int] = None
    image_url:              Optional[str] = Field(None, max_length=512)
    is_published:           bool          = True


class LessonUpdate(BaseModel):
    title:                  Optional[str] = Field(None, min_length=3, max_length=255)
    content:                Optional[str] = Field(None, min_length=10)
    topic:                  Optional[str] = None
    difficulty:             Optional[str] = None
    mil_skill:              Optional[str] = Field(None, max_length=50)
    sort_order:             Optional[int] = None
    prerequisite_lesson_id: Optional[int] = None
    image_url:              Optional[str] = Field(None, max_length=512)
    is_published:           Optional[bool] = None


_VALID_TOPICS = {"claim_detection", "source_verification", "bias_detection",
                 "evidence_evaluation", "general"}
_VALID_DIFFICULTIES = {"beginner", "intermediate", "advanced"}


# ── GET /topics (public) ──────────────────────────────────────────────────────
@router.get("/topics")
async def list_topics_public():
    """
    Public endpoint — returns all topics from the lesson_topics registry
    with full metadata (key, label, icon, color_hue, sort_order).
    Used by lessons.js to drive dynamic filter buttons, quiz cards, and tag colours.
    """
    db = Session(engine)
    try:
        rows = db.execute(sa.text(
            "SELECT `key`, label, icon, color_hue, sort_order "
            "FROM lesson_topics ORDER BY sort_order, `key`"
        )).fetchall()
        return {"topics": [
            {"key": r[0], "label": r[1], "icon": r[2],
             "color_hue": r[3], "sort_order": r[4]}
            for r in rows
        ]}
    finally:
        db.close()



@router.get("/lessons")
async def list_lessons(
    req: Request,
    topic: str = None,
    difficulty: str = None,
    include_unpublished: int = 0,
    authorization: str = Header(None),
):
    """
    List lessons. By default only published lessons are returned.
    Admins may pass include_unpublished=1 to also see deactivated lessons
    (is_published=0). The is_published field is included in every row so the
    admin UI can correctly label and toggle each card.
    """
    # Only admins can request unpublished lessons
    if include_unpublished:
        payload = _get_optional_user(authorization, req)
        if not payload or payload.get("role") != "admin":
            include_unpublished = 0

    db = Session(engine)
    try:
        filters = []
        params  = {}
        if not include_unpublished:
            filters.append("l.is_published = 1")
        if topic:
            filters.append("l.topic = :topic")
            params["topic"] = topic
        if difficulty:
            filters.append("l.difficulty = :difficulty")
            params["difficulty"] = difficulty

        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        rows = db.execute(
            sa.text(f"""
                SELECT l.id, l.lesson_key, l.title, l.content, l.topic, l.difficulty,
                       l.mil_skill, l.sort_order, l.image_url, l.is_published, l.created_at
                FROM lessons l
                {where}
                ORDER BY l.sort_order ASC, l.created_at ASC
            """),
            params,
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()



# ── GET /lessons/triggered ────────────────────────────────────────────────────
@router.get("/lessons/triggered")
async def get_triggered_lessons(
    req: Request,
    session_token: str = None,
    authorization: str = Header(None),
):
    db = Session(engine)
    try:
        if authorization and authorization.startswith("Bearer "):
            payload = _get_optional_user(authorization, req)
            if not payload:
                return []
            rows = db.execute(
                sa.text("""
                    SELECT l.*, lt.id AS trigger_id, lt.trigger_reason, lt.was_read
                    FROM lessons_triggered lt
                    JOIN lessons l ON l.id = lt.lesson_id
                    JOIN submissions s ON s.id = lt.submission_id
                    WHERE s.user_id = :uid AND l.is_published = 1
                    ORDER BY lt.triggered_at DESC
                """),
                {"uid": payload["sub"]},
            ).fetchall()
        elif session_token:
            rows = db.execute(
                sa.text("""
                    SELECT l.*, lt.id AS trigger_id, lt.trigger_reason, lt.was_read
                    FROM lessons_triggered lt
                    JOIN lessons l ON l.id = lt.lesson_id
                    JOIN submissions s ON s.id = lt.submission_id
                    WHERE s.session_token = :tok AND l.is_published = 1
                    ORDER BY lt.triggered_at DESC
                """),
                {"tok": session_token},
            ).fetchall()
        else:
            return []
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()



# ── POST /lessons/mark-read  (bare — called from index.js markLessonRead()) ──
# index.js sends {lesson_key, session_token, user_id} without a trigger ID.
# Records a lesson_completion row so the read state persists across sessions.
@router.post("/lessons/mark-read")
async def mark_lesson_read_by_key(body: dict):
    """
    Mark a lesson as read using its lesson_key.
    Called from the main evaluation flow (index.js) where only the key is known.
    Upserts a row in lesson_completions.
    """
    lesson_key    = (body.get("lesson_key") or "").strip()
    session_token = (body.get("session_token") or "").strip()
    user_id       = body.get("user_id")

    if not lesson_key:
        raise HTTPException(status_code=422, detail="lesson_key is required.")

    db = Session(engine)
    try:
        lesson_row = db.execute(
            sa.text("SELECT id FROM lessons WHERE lesson_key = :k LIMIT 1"),
            {"k": lesson_key},
        ).fetchone()
        if not lesson_row:
            return {"ok": True, "skipped": True}

        lesson_id = lesson_row.id

        existing = db.execute(sa.text(
            "SELECT id FROM lesson_completions "
            "WHERE lesson_id = :lid AND (user_id = :uid OR session_token = :tok) LIMIT 1"
        ), {"lid": lesson_id, "uid": user_id, "tok": session_token}).fetchone()

        if not existing:
            db.execute(sa.text("""
                INSERT INTO lesson_completions (lesson_id, user_id, session_token, completed_at)
                VALUES (:lid, :uid, :tok, NOW())
            """), {"lid": lesson_id, "uid": user_id, "tok": session_token})
            db.commit()

        # ── mindmap unlock: logged-in user + lesson has a node tagged ─────────
        if user_id:
            node_row = db.execute(
                sa.text("SELECT mindmap_node_id FROM lessons WHERE id = :id LIMIT 1"),
                {"id": lesson_id},
            ).fetchone()
            if node_row and node_row.mindmap_node_id:
                _unlock_mindmap_node(db, user_id, node_row.mindmap_node_id)

        return {"ok": True, "lesson_id": lesson_id}
    except Exception as e:
        db.rollback()
        logger.warning(f"[mark-read] error: {e}")
        return {"ok": True}
    finally:
        db.close()


# ── POST /lessons/mark-read/{lesson_trigger_id} ───────────────────────────────
@router.post("/lessons/mark-read/{lesson_trigger_id}")
async def mark_lesson_read(
    lesson_trigger_id: int,
    req: Request,
    authorization: str = Header(None),
):
    """
    Mark a triggered lesson as read.

    BUG FIX (v4.1): UPDATE is now scoped to the caller's own submissions via
    a WHERE EXISTS subquery checking submissions.user_id = caller_id.
    """
    payload   = get_current_user(req, authorization)
    caller_id = payload["sub"]
    caller_role = payload.get("role", "")

    db = Session(engine)
    try:
        trigger_row = db.execute(
            sa.text("SELECT id FROM lessons_triggered WHERE id = :tid"),
            {"tid": lesson_trigger_id},
        ).fetchone()
        if not trigger_row:
            raise HTTPException(status_code=404, detail="Lesson trigger not found.")

        if caller_role == "admin":
            db.execute(
                sa.text("UPDATE lessons_triggered SET was_read = 1 WHERE id = :id"),
                {"id": lesson_trigger_id},
            )
        else:
            result = db.execute(
                sa.text("""
                    UPDATE lessons_triggered lt
                    JOIN submissions s ON s.id = lt.submission_id
                    SET lt.was_read = 1
                    WHERE lt.id = :tid
                      AND s.user_id = :uid
                """),
                {"tid": lesson_trigger_id, "uid": caller_id},
            )
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=403,
                    detail="Forbidden — this lesson trigger does not belong to your account.",
                )

        db.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ── POST /lessons/{lesson_id}/mark-read ──────────────────────────────────────

# ── GET /lessons/completions ──────────────────────────────────────────────────
@router.get("/lessons/completions")
async def get_lesson_completions(
    req: Request,
    session_token: str = None,
    authorization: str = Header(None),
):
    db = Session(engine)
    try:
        if authorization and authorization.startswith("Bearer "):
            payload = _get_optional_user(authorization, req)
            if not payload:
                return []
            rows = db.execute(
                sa.text("""
                    SELECT lc.id, lc.lesson_id, lc.completed_at,
                           l.title, l.topic, l.difficulty
                    FROM lesson_completions lc
                    JOIN lessons l ON l.id = lc.lesson_id
                    WHERE lc.user_id = :uid
                    ORDER BY lc.completed_at DESC
                """),
                {"uid": payload["sub"]},
            ).fetchall()
        elif session_token:
            rows = db.execute(
                sa.text("""
                    SELECT lc.id, lc.lesson_id, lc.completed_at,
                           l.title, l.topic, l.difficulty
                    FROM lesson_completions lc
                    JOIN lessons l ON l.id = lc.lesson_id
                    WHERE lc.session_token = :tok
                    ORDER BY lc.completed_at DESC
                """),
                {"tok": session_token},
            ).fetchall()
        else:
            return []
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


# ── POST /lessons/{lesson_id}/complete ───────────────────────────────────────
# ── GET /lessons/{lesson_id} ──────────────────────────────────────────────────
@router.get("/lessons/{lesson_id}")
async def get_lesson(lesson_id: int, req: Request, authorization: str = Header(None)):
    payload = _get_optional_user(authorization, req)
    is_admin = payload and payload.get("role") == "admin"
    db = Session(engine)
    try:
        pub_filter = "" if is_admin else "AND is_published = 1"
        row = db.execute(
            sa.text(f"""
                SELECT id, lesson_key, title, content, topic, difficulty,
                       mil_skill, sort_order, image_url, is_published, created_at
                FROM lessons WHERE id = :id {pub_filter}
            """),
            {"id": lesson_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        return dict(row._mapping)
    finally:
        db.close()


# ── POST /lessons  (admin only — create a lesson) ─────────────────────────────
@router.post("/lessons", status_code=201)
async def create_lesson(
    body: LessonCreate,
    req: Request,
    authorization: str = Header(None),
):
    _require_admin(authorization, req)

    if body.topic not in _VALID_TOPICS:
        raise HTTPException(status_code=422, detail=f"Invalid topic. Must be one of: {_VALID_TOPICS}")
    if body.difficulty not in _VALID_DIFFICULTIES:
        raise HTTPException(status_code=422, detail=f"Invalid difficulty. Must be one of: {_VALID_DIFFICULTIES}")

    db = Session(engine)
    try:
        # Check for duplicate lesson_key
        existing = db.execute(
            sa.text("SELECT id FROM lessons WHERE lesson_key = :key"),
            {"key": body.lesson_key},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"lesson_key '{body.lesson_key}' already exists.")

        now = datetime.now(timezone.utc)
        result = db.execute(
            sa.text("""
                INSERT INTO lessons
                    (lesson_key, title, content, topic, difficulty, mil_skill,
                     sort_order, prerequisite_lesson_id, image_url, is_published,
                     created_at, updated_at)
                VALUES
                    (:lesson_key, :title, :content, :topic, :difficulty, :mil_skill,
                     :sort_order, :prereq, :image_url, :is_published, :now, :now)
            """),
            {
                "lesson_key":   body.lesson_key,
                "title":        body.title,
                "content":      body.content,
                "topic":        body.topic,
                "difficulty":   body.difficulty,
                "mil_skill":    body.mil_skill,
                "sort_order":   body.sort_order,
                "prereq":       body.prerequisite_lesson_id,
                "image_url":    body.image_url,
                "is_published": int(body.is_published),
                "now":          now,
            },
        )
        new_id = result.lastrowid
        db.commit()

        row = db.execute(
            sa.text("SELECT * FROM lessons WHERE id = :id"),
            {"id": new_id},
        ).fetchone()
        return dict(row._mapping)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("create_lesson error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ── PUT /lessons/{lesson_id}  (admin only — update a lesson) ─────────────────
@router.put("/lessons/{lesson_id}")
async def update_lesson(
    lesson_id: int,
    body: LessonUpdate,
    req: Request,
    authorization: str = Header(None),
):
    _require_admin(authorization, req)

    if body.topic is not None and body.topic not in _VALID_TOPICS:
        raise HTTPException(status_code=422, detail=f"Invalid topic. Must be one of: {_VALID_TOPICS}")
    if body.difficulty is not None and body.difficulty not in _VALID_DIFFICULTIES:
        raise HTTPException(status_code=422, detail=f"Invalid difficulty. Must be one of: {_VALID_DIFFICULTIES}")

    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT id FROM lessons WHERE id = :id"),
            {"id": lesson_id},
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Lesson not found.")

        # Build dynamic SET clause from provided fields only
        updates = {}
        if body.title        is not None: updates["title"]                  = body.title
        if body.content      is not None: updates["content"]                = body.content
        if body.topic        is not None: updates["topic"]                  = body.topic
        if body.difficulty   is not None: updates["difficulty"]             = body.difficulty
        if body.mil_skill    is not None: updates["mil_skill"]              = body.mil_skill
        if body.sort_order   is not None: updates["sort_order"]             = body.sort_order
        if body.image_url    is not None: updates["image_url"]              = body.image_url
        if body.is_published is not None: updates["is_published"]           = int(body.is_published)
        if body.prerequisite_lesson_id is not None:
            updates["prerequisite_lesson_id"] = body.prerequisite_lesson_id

        if not updates:
            raise HTTPException(status_code=422, detail="No fields provided to update.")

        set_clause = ", ".join(f"`{k}` = :{k}" for k in updates)
        updates["id"] = lesson_id

        db.execute(
            sa.text(f"UPDATE lessons SET {set_clause}, `updated_at` = NOW() WHERE id = :id"),
            updates,
        )
        db.commit()

        row = db.execute(
            sa.text("SELECT * FROM lessons WHERE id = :id"),
            {"id": lesson_id},
        ).fetchone()
        return dict(row._mapping)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("update_lesson error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ── PATCH /lessons/{lesson_id}/toggle-published  (admin only — activate/deactivate) ──
@router.patch("/lessons/{lesson_id}/toggle-published")
async def toggle_lesson_published(
    lesson_id: int,
    req: Request,
    authorization: str = Header(None),
):
    """
    Toggle is_published for a lesson (admin only).
    Deactivating hides the lesson from users without deleting data.
    """
    _require_admin(authorization, req)

    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT id, is_published FROM lessons WHERE id = :id"),
            {"id": lesson_id},
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Lesson not found.")

        new_state = 0 if existing.is_published else 1
        db.execute(
            sa.text("UPDATE lessons SET is_published = :state, updated_at = NOW() WHERE id = :id"),
            {"state": new_state, "id": lesson_id},
        )
        db.commit()

        row = db.execute(
            sa.text("SELECT * FROM lessons WHERE id = :id"),
            {"id": lesson_id},
        ).fetchone()
        return dict(row._mapping)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("toggle_lesson_published error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()



@router.delete("/lessons/{lesson_id}", status_code=204)
async def delete_lesson(
    lesson_id: int,
    req: Request,
    authorization: str = Header(None),
):
    """
    Hard-delete a lesson.

    Safety: quiz_questions.lesson_id is FK ON DELETE SET NULL, and
    eval_questions.skip_lesson_id / lessons_triggered.lesson_id are
    ON DELETE CASCADE / SET NULL per the schema, so referential
    integrity is maintained automatically.
    """
    _require_admin(authorization, req)

    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT id, title FROM lessons WHERE id = :id"),
            {"id": lesson_id},
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Lesson not found.")

        db.execute(sa.text("DELETE FROM lessons WHERE id = :id"), {"id": lesson_id})
        db.commit()
        return  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("delete_lesson error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ── POST /lessons/{lesson_id}/mark-read ──────────────────────────────────────
@router.post("/lessons/{lesson_id}/mark-read")
async def mark_lesson_read_by_id(
    lesson_id: int,
    req: Request,
    body: dict = None,
    authorization: str = Header(None),
):
    """
    Mark the most recent triggered lesson row for this lesson as read.

    BUG FIX (v4.1): UPDATE scoped to caller's own user_id or session_token.
    """
    db = Session(engine)
    try:
        if authorization and authorization.startswith("Bearer "):
            payload   = get_current_user(req, authorization)
            caller_id = payload["sub"]

            result = db.execute(
                sa.text("""
                    UPDATE lessons_triggered lt
                    JOIN submissions s ON s.id = lt.submission_id
                    SET lt.was_read = 1
                    WHERE lt.lesson_id = :lid
                      AND s.user_id   = :uid
                      AND lt.was_read = 0
                """),
                {"lid": lesson_id, "uid": caller_id},
            )
        else:
            session_token = (body or {}).get("session_token", "")
            if not session_token:
                raise HTTPException(status_code=422, detail="session_token required.")

            result = db.execute(
                sa.text("""
                    UPDATE lessons_triggered lt
                    JOIN submissions s ON s.id = lt.submission_id
                    SET lt.was_read = 1
                    WHERE lt.lesson_id     = :lid
                      AND s.session_token  = :tok
                      AND lt.was_read      = 0
                """),
                {"lid": lesson_id, "tok": session_token},
            )

        db.commit()
        return {"ok": True, "rows_updated": result.rowcount}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.post("/lessons/{lesson_id}/complete")
async def complete_lesson(
    lesson_id: int,
    req: Request,
    body: dict = None,
    authorization: str = Header(None),
):
    """Record lesson completion for the calling user or session."""
    db = Session(engine)
    try:
        lesson = db.execute(
            sa.text("SELECT id FROM lessons WHERE id = :id AND is_published = 1"),
            {"id": lesson_id},
        ).fetchone()
        if not lesson:
            raise HTTPException(status_code=404, detail="Lesson not found.")

        now = datetime.now(timezone.utc)

        if authorization and authorization.startswith("Bearer "):
            payload = get_current_user(req, authorization)
            user_id = payload["sub"]
            existing = db.execute(
                sa.text("SELECT id FROM lesson_completions WHERE user_id = :uid AND lesson_id = :lid"),
                {"uid": user_id, "lid": lesson_id},
            ).fetchone()
            if not existing:
                db.execute(
                    sa.text("INSERT INTO lesson_completions (user_id, lesson_id, completed_at) VALUES (:uid, :lid, :now)"),
                    {"uid": user_id, "lid": lesson_id, "now": now},
                )
                # Keep user_skill_progress.lessons_completed in sync so the
                # dashboard skill cards and admin analytics show accurate counts.
                lesson_topic = db.execute(
                    sa.text("SELECT topic FROM lessons WHERE id = :id LIMIT 1"),
                    {"id": lesson_id},
                ).scalar()
                if lesson_topic:
                    db.execute(
                        sa.text("""
                            INSERT INTO user_skill_progress
                                (user_id, topic, current_level, quiz_accuracy_pct, lessons_completed)
                            VALUES (:uid, :topic, 'beginner', NULL, 1)
                            ON DUPLICATE KEY UPDATE
                                lessons_completed = lessons_completed + 1
                        """),
                        {"uid": user_id, "topic": lesson_topic},
                    )
                # ── mindmap unlock ─────────────────────────────────────────────
                node_row = db.execute(
                    sa.text("SELECT mindmap_node_id FROM lessons WHERE id = :id LIMIT 1"),
                    {"id": lesson_id},
                ).fetchone()
                if node_row and node_row.mindmap_node_id:
                    _unlock_mindmap_node(db, user_id, node_row.mindmap_node_id)
        else:
            session_token = (body or {}).get("session_token", "")
            if not session_token:
                raise HTTPException(status_code=422, detail="session_token required.")
            existing = db.execute(
                sa.text("SELECT id FROM lesson_completions WHERE session_token = :tok AND lesson_id = :lid"),
                {"tok": session_token, "lid": lesson_id},
            ).fetchone()
            if not existing:
                db.execute(
                    sa.text("INSERT INTO lesson_completions (session_token, lesson_id, completed_at) VALUES (:tok, :lid, :now)"),
                    {"tok": session_token, "lid": lesson_id, "now": now},
                )

        db.commit()
        return {"lesson_id": lesson_id, "completed_at": now.isoformat(), "message": "Lesson marked complete."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
