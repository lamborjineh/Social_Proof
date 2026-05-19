"""
SocialProof — Router: Admin Dashboard  v4.0

Endpoints:
  Platform health
    GET  /admin/stats                     — user counts, eval totals, DAU/WAU

  Quiz management (new)
    GET  /admin/quiz/questions            — list all questions (filterable)
    POST /admin/quiz/questions            — create a question
    PUT  /admin/quiz/questions/{id}       — edit a question
    DELETE /admin/quiz/questions/{id}     — delete a question
    GET  /admin/quiz/questions/{id}/stats — per-question accuracy stats

  Lesson management
    GET  /admin/lessons                   — list lessons with trigger stats
    POST /admin/lessons                   — create a lesson
    PUT  /admin/lessons/{id}              — edit a lesson
    DELETE /admin/lessons/{id}            — delete a lesson
    GET  /admin/lessons/impact            — trigger counts + read rate per lesson

  User management
    GET  /admin/users                     — list all users
    PUT  /admin/users/{id}/role           — promote / demote role
    DELETE /admin/users/{id}              — deactivate / delete account

  Research & analytics
    GET  /admin/analytics/skills          — aggregate MIL skill level distribution
    GET  /admin/analytics/lessons-heatmap — lesson trigger heatmap across all users
    GET  /admin/analytics/pretest         — pre-test vs post-test improvement
    GET  /admin/analytics/quiz            — quiz accuracy by topic & difficulty

  Corpus management
    POST /admin/corpus/ingest             — save curated sentences to corpus.db
    GET  /admin/corpus/stats              — sentence count, sources, pipelines

  API & data health
    GET  /admin/api-usage                 — factcheck cache, MBFC coverage, system accuracy
    POST /admin/mbfc/sync                 — trigger MBFC sync (runs sync_mbfc.py)

Removed vs v3:
  - GET /admin/submissions     (submission browsing)
  - GET /admin/research-metrics (re-evaluation / correction-rate metrics removed)
  - system_score, system_label, user_label, confidence fields never returned
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
import bcrypt
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import logger
from database.models import engine
from routers.auth import _verify
from services.audit_log import log_action, extract_admin_context

_CORPUS_DB = Path(__file__).resolve().parent / "data" / "corpus.db"

router = APIRouter(prefix="/admin")


# ── Auth helper ───────────────────────────────────────────────────────────────

def _require_admin(authorization: Optional[str], request: Request = None):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    elif request is not None:
        token = request.cookies.get("sp_jwt")
    if not token:
        raise HTTPException(status_code=401, detail="Authorization required.")
    payload = _verify(token)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return payload  # callers can capture this for audit context


# ── File Upload ───────────────────────────────────────────────────────────────

_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "assets" / "uploads"
_ALLOWED_EXTENSIONS = {
    # images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    # video
    ".mp4", ".webm", ".mov", ".avi",
    # documents / files
    ".pdf", ".doc", ".docx", ".txt", ".csv",
}
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_media(
    file:          UploadFile      = File(...),
    request:       Request         = None,
    authorization: str             = Header(None),
):
    """
    Upload an image, video, or file for use in quiz questions.
    Returns { url, filename, media_type, size }.
    Files are stored under assets/uploads/ and served at /assets/uploads/<filename>.
    """
    _require_admin(authorization, request)

    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"File type '{suffix}' not allowed. Allowed: {sorted(_ALLOWED_EXTENSIONS)}"
        )

    # Detect media_type from extension
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        detected_type = "image"
    elif suffix in {".mp4", ".webm", ".mov", ".avi"}:
        detected_type = "video"
    else:
        detected_type = "file"

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    unique_name = f"{uuid.uuid4().hex}{suffix}"
    dest = _UPLOAD_DIR / unique_name

    size = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="File exceeds 50 MB limit.")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")

    url = f"/assets/uploads/{unique_name}"
    logger.info(f"[Upload] Saved {unique_name} ({size} bytes, {detected_type})")
    return {"url": url, "filename": unique_name, "media_type": detected_type, "size": size}


@router.delete("/upload/cleanup-orphans")
async def cleanup_orphaned_uploads(
    request:       Request = None,
    authorization: str     = Header(None),
):
    """
    Scan assets/uploads/ and delete any file whose URL is not referenced by
    any quiz_question (image_url, video_url) or lessons (image_url) row.
    Returns a summary of deleted and retained file counts.
    """
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        # Collect every URL stored in the DB that lives under /assets/uploads/
        used_urls: set[str] = set()
        try:
            for row in db.execute(sa.text(
                "SELECT image_url, video_url FROM quiz_questions WHERE image_url IS NOT NULL OR video_url IS NOT NULL"
            )).fetchall():
                if row.image_url: used_urls.add(row.image_url)
                if row.video_url: used_urls.add(row.video_url)
        except Exception:
            # video_url column may not exist on older schema — fall back to image_url only
            for row in db.execute(sa.text(
                "SELECT image_url FROM quiz_questions WHERE image_url IS NOT NULL"
            )).fetchall():
                if row.image_url: used_urls.add(row.image_url)
        for row in db.execute(sa.text(
            "SELECT image_url FROM lessons WHERE image_url IS NOT NULL"
        )).fetchall():
            if row.image_url: used_urls.add(row.image_url)

        deleted, retained = [], []
        if _UPLOAD_DIR.exists():
            for f in _UPLOAD_DIR.iterdir():
                if not f.is_file():
                    continue
                url_path = f"/assets/uploads/{f.name}"
                if url_path in used_urls:
                    retained.append(f.name)
                else:
                    f.unlink(missing_ok=True)
                    deleted.append(f.name)
                    logger.info(f"[Cleanup] Deleted orphaned upload: {f.name}")

        _adm = _require_admin(authorization, request)
        log_action(
            "upload.cleanup_orphans", "upload", None,
            entity_label=f"deleted={len(deleted)}",
            detail={"deleted": deleted},
            **extract_admin_context(request, _adm),
        )
        return {
            "deleted_count": len(deleted),
            "retained_count": len(retained),
            "deleted_files": deleted,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


# ── Pydantic models ───────────────────────────────────────────────────────────

class QuizQuestionCreate(BaseModel):
    lesson_id:      Optional[int]       = None
    question_text:  str                 = Field(..., min_length=10)
    question_type:  str                 = Field("multiple_choice", description="multiple_choice|multiple_answer|true_false|identification|scenario_based")
    options:        List[str]           = Field(default_factory=list)
    correct_index:  int                 = Field(0, ge=0)
    correct_indices: Optional[List[int]] = None   # for multiple_answer
    correct_answer: Optional[str]       = None    # for identification (case-insensitive)
    scenario_text:  Optional[str]       = None    # for scenario_based
    explanation:    Optional[str]       = None
    hint:           Optional[str]       = None
    topic:          str                 = Field(..., description="claim_detection|source_verification|bias_detection|evidence_evaluation|general")
    difficulty:     str                 = Field("beginner", description="beginner|intermediate|advanced")
    image_url:      Optional[str]       = Field(None, max_length=512)
    video_url:      Optional[str]       = Field(None, max_length=1024)
    media_type:     str                 = Field("text", description="text|image|video|file")
    mindmap_node_id: Optional[str]      = Field(None, max_length=64)


class QuizQuestionUpdate(BaseModel):
    lesson_id:      Optional[int]       = None
    question_text:  Optional[str]       = None
    question_type:  Optional[str]       = None
    options:        Optional[List[str]] = None
    correct_index:  Optional[int]       = None
    correct_indices: Optional[List[int]] = None
    correct_answer: Optional[str]       = None
    scenario_text:  Optional[str]       = None
    explanation:    Optional[str]       = None
    hint:           Optional[str]       = None
    topic:          Optional[str]       = None
    difficulty:     Optional[str]       = None
    image_url:      Optional[str]       = Field(None, max_length=512)
    video_url:      Optional[str]       = Field(None, max_length=1024)
    media_type:     Optional[str]       = None
    mindmap_node_id: Optional[str]      = Field(None, max_length=64)


class LessonCreate(BaseModel):
    lesson_key:             str           = Field(..., min_length=3, max_length=100)
    title:                  str           = Field(..., min_length=3, max_length=255)
    content:                str           = Field(..., min_length=10)
    topic:                  str
    difficulty:             str           = "beginner"
    mil_skill:              Optional[str] = None
    sort_order:             Optional[int] = None
    prerequisite_lesson_id: Optional[int] = None
    image_url:              Optional[str] = Field(None, max_length=512)
    mindmap_node_id:        Optional[str] = Field(None, max_length=64)


class LessonUpdate(BaseModel):
    title:                  Optional[str]  = None
    content:                Optional[str]  = None
    topic:                  Optional[str]  = None
    difficulty:             Optional[str]  = None
    mil_skill:              Optional[str]  = None
    sort_order:             Optional[int]  = None
    prerequisite_lesson_id: Optional[int]  = None
    image_url:              Optional[str]  = Field(None, max_length=512)
    is_published:           Optional[bool] = None
    mindmap_node_id:        Optional[str]  = Field(None, max_length=64)


class RoleUpdate(BaseModel):
    role: str = Field(..., description="user | admin")


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email:    str = Field(..., min_length=5, max_length=150)
    password: str = Field(..., min_length=6)
    role:     str = Field("user", description="user | admin")


class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email:    Optional[str] = Field(None, min_length=5, max_length=150)
    password: Optional[str] = Field(None, min_length=6)
    role:     Optional[str] = None


class CorpusIngestRequest(BaseModel):
    sentences:     List[str]
    source_domain: str
    source_name:   str
    url:           Optional[str] = ""
    pipeline:      str           = "stats"
    reputation:    float         = 0.95


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_admin_stats(request: Request, authorization: str = Header(None)):
    """Platform health overview — user counts, eval totals, DAU/WAU."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        overview = db.execute(sa.text("""
            SELECT
                (SELECT COUNT(*) FROM users WHERE role = 'user')          AS total_users,
                (SELECT COUNT(*) FROM users WHERE role = 'admin')         AS total_admins,
                (SELECT COUNT(*) FROM submissions)                        AS total_submissions,
                (SELECT COUNT(*) FROM submissions WHERE user_id IS NULL)  AS anonymous_submissions,
                (SELECT COUNT(*) FROM lesson_completions)                 AS total_lesson_completions,
                (SELECT COUNT(*) FROM quiz_attempts)                      AS total_quiz_attempts,
                (SELECT COUNT(*) FROM lessons_triggered WHERE was_read=1) AS lessons_read,
                (SELECT COUNT(*) FROM lessons_triggered)                  AS lessons_triggered_total
        """)).fetchone()

        # DAU — distinct users with any activity in the last 7 days
        dau_rows = db.execute(sa.text("""
            SELECT DATE(created_at) AS day, COUNT(DISTINCT user_id) AS active_users
            FROM submissions
            WHERE user_id IS NOT NULL
              AND created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """)).fetchall()

        # New user registrations per day (last 14 days)
        reg_rows = db.execute(sa.text("""
            SELECT DATE(created_at) AS day, COUNT(*) AS new_users
            FROM users
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """)).fetchall()

        lesson_read_rate = 0.0
        total_triggered = overview.lessons_triggered_total or 0
        if total_triggered > 0:
            lesson_read_rate = round((overview.lessons_read or 0) / total_triggered * 100, 1)

        return {
            "overview": {
                "total_users":               overview.total_users or 0,
                "total_admins":              overview.total_admins or 0,
                "total_submissions":         overview.total_submissions or 0,
                "anonymous_submissions":     overview.anonymous_submissions or 0,
                "total_lesson_completions":  overview.total_lesson_completions or 0,
                "total_quiz_attempts":       overview.total_quiz_attempts or 0,
                "lesson_read_rate_pct":      lesson_read_rate,
            },
            "dau_7d":           [{"day": str(r.day), "active_users": r.active_users} for r in dau_rows],
            "registrations_14d":[{"day": str(r.day), "new_users": r.new_users} for r in reg_rows],
        }
    finally:
        db.close()


class TopicCreate(BaseModel):
    key:             str = Field(..., min_length=2, max_length=60,
                                 pattern=r'^[a-z][a-z0-9_]*$',
                                 description="snake_case key, e.g. critical_thinking")
    label:           str = Field(..., min_length=2, max_length=100)
    icon:            str = Field("📄", max_length=10)
    color_hue:       int = Field(220, ge=0, le=359)
    sort_order:      int = Field(0)
    quiz_limit:      Optional[int] = Field(None, ge=1, description="Max questions shown per session; null = no limit")
    linked_quiz_ids: Optional[List[int]] = Field(None, description="Explicit list of question IDs for this topic")

class TopicUpdate(BaseModel):
    label:           Optional[str] = Field(None, min_length=2, max_length=100)
    icon:            Optional[str] = Field(None, max_length=10)
    color_hue:       Optional[int] = Field(None, ge=0, le=359)
    sort_order:      Optional[int] = None
    quiz_limit:      Optional[int] = Field(None, ge=1)
    clear_quiz_limit: Optional[bool] = None   # set True to remove the limit
    linked_quiz_ids: Optional[List[int]] = None  # None = don't touch; [] = remove all links


@router.get("/topics")
async def list_topics(request: Request, authorization: str = Header(None)):
    """Return all topics from the lesson_topics registry (admin-managed)."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text(
            "SELECT `key`, label, icon, color_hue, sort_order, quiz_limit "
            "FROM lesson_topics ORDER BY sort_order, `key`"
        )).fetchall()
        link_rows = db.execute(sa.text(
            "SELECT topic_key, question_id FROM topic_quiz_links ORDER BY topic_key, question_id"
        )).fetchall()
        links_by_topic: dict = {}
        for lr in link_rows:
            links_by_topic.setdefault(lr[0], []).append(lr[1])
        return {"topics": [
            {"key": r[0], "label": r[1], "icon": r[2],
             "color_hue": r[3], "sort_order": r[4],
             "quiz_limit": r[5],
             "linked_quiz_ids": links_by_topic.get(r[0], None)}
            for r in rows
        ]}
    finally:
        db.close()


@router.post("/topics", status_code=201)
async def create_topic(
    body: TopicCreate,
    request: Request,
    authorization: str = Header(None),
):
    """Create a new topic. Key must be unique snake_case."""
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        exists = db.execute(sa.text(
            "SELECT COUNT(*) FROM lesson_topics WHERE `key` = :k"
        ), {"k": body.key}).scalar()
        if exists:
            raise HTTPException(status_code=409, detail=f"Topic key '{body.key}' already exists.")
        db.execute(sa.text(
            "INSERT INTO lesson_topics (`key`, label, icon, color_hue, sort_order, quiz_limit) "
            "VALUES (:key, :label, :icon, :hue, :sort, :quiz_limit)"
        ), {"key": body.key, "label": body.label, "icon": body.icon,
            "hue": body.color_hue, "sort": body.sort_order, "quiz_limit": body.quiz_limit})
        db.commit()
        if body.linked_quiz_ids is not None:
            db.execute(sa.text("DELETE FROM topic_quiz_links WHERE topic_key = :k"), {"k": body.key})
            for qid in body.linked_quiz_ids:
                db.execute(sa.text(
                    "INSERT IGNORE INTO topic_quiz_links (topic_key, question_id) VALUES (:k, :qid)"
                ), {"k": body.key, "qid": qid})
            db.commit()
        log_action("topic.create", "topic", body.key,
                   entity_label=body.label,
                   detail={"label": body.label, "icon": body.icon},
                   **extract_admin_context(request, ctx))
        return {"key": body.key, "label": body.label, "icon": body.icon,
                "color_hue": body.color_hue, "sort_order": body.sort_order,
                "quiz_limit": body.quiz_limit, "linked_quiz_ids": body.linked_quiz_ids}
    finally:
        db.close()


@router.put("/topics/{key}")
async def update_topic(
    key: str,
    body: TopicUpdate,
    request: Request,
    authorization: str = Header(None),
):
    """Edit label, icon, color_hue, or sort_order of an existing topic."""
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        exists = db.execute(sa.text(
            "SELECT COUNT(*) FROM lesson_topics WHERE `key` = :k"
        ), {"k": key}).scalar()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Topic '{key}' not found.")
        fields, params = [], {"k": key}
        if body.label      is not None: fields.append("label = :label");           params["label"] = body.label
        if body.icon       is not None: fields.append("icon = :icon");             params["icon"]  = body.icon
        if body.color_hue  is not None: fields.append("color_hue = :hue");         params["hue"]   = body.color_hue
        if body.sort_order is not None: fields.append("sort_order = :sort");       params["sort"]  = body.sort_order
        if body.clear_quiz_limit:
            fields.append("quiz_limit = NULL")
        elif body.quiz_limit is not None:
            fields.append("quiz_limit = :quiz_limit"); params["quiz_limit"] = body.quiz_limit
        if not fields and body.linked_quiz_ids is None:
            raise HTTPException(status_code=400, detail="Nothing to update.")
        if fields:
            db.execute(sa.text(
                f"UPDATE lesson_topics SET {', '.join(fields)} WHERE `key` = :k"
            ), params)
            db.commit()
        if body.linked_quiz_ids is not None:
            db.execute(sa.text("DELETE FROM topic_quiz_links WHERE topic_key = :k"), {"k": key})
            for qid in body.linked_quiz_ids:
                db.execute(sa.text(
                    "INSERT IGNORE INTO topic_quiz_links (topic_key, question_id) VALUES (:k, :qid)"
                ), {"k": key, "qid": qid})
            db.commit()
        log_action("topic.update", "topic", key,
                   detail=body.model_dump(exclude_none=True),
                   **extract_admin_context(request, ctx))
        row = db.execute(sa.text(
            "SELECT `key`, label, icon, color_hue, sort_order, quiz_limit FROM lesson_topics WHERE `key` = :k"
        ), {"k": key}).fetchone()
        link_rows = db.execute(sa.text(
            "SELECT question_id FROM topic_quiz_links WHERE topic_key = :k ORDER BY question_id"
        ), {"k": key}).fetchall()
        linked_ids = [lr[0] for lr in link_rows] if link_rows else None
        return {"key": row[0], "label": row[1], "icon": row[2],
                "color_hue": row[3], "sort_order": row[4],
                "quiz_limit": row[5], "linked_quiz_ids": linked_ids}
    finally:
        db.close()


@router.delete("/topics/{key}", status_code=204)
async def delete_topic(
    key: str,
    request: Request,
    authorization: str = Header(None),
):
    """
    Delete a topic from the registry.
    Lessons/quiz questions that already use this key are NOT deleted — they
    just won't match a registry entry until reassigned.
    """
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        exists = db.execute(sa.text(
            "SELECT COUNT(*) FROM lesson_topics WHERE `key` = :k"
        ), {"k": key}).scalar()
        if not exists:
            raise HTTPException(status_code=404, detail=f"Topic '{key}' not found.")
        db.execute(sa.text("DELETE FROM lesson_topics WHERE `key` = :k"), {"k": key})
        db.commit()
        log_action("topic.delete", "topic", key,
                   **extract_admin_context(request, ctx))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# QUIZ MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/quiz/questions")
async def list_quiz_questions(
    topic:      Optional[str] = None,
    difficulty: Optional[str] = None,
    lesson_id:  Optional[int] = None,
    request:    Request       = None,
    authorization: str        = Header(None),
):
    """List all quiz questions with optional filters."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        base = """
            SELECT q.*, l.title AS lesson_title,
                   COUNT(qa.id) AS attempt_count,
                   ROUND(AVG(qa.is_correct) * 100, 1) AS accuracy_pct
            FROM quiz_questions q
            LEFT JOIN lessons l ON l.id = q.lesson_id
            LEFT JOIN quiz_attempts qa ON qa.question_id = q.id
        """
        wheres, params = [], {}
        if topic:
            wheres.append("q.topic = :topic"); params["topic"] = topic
        if difficulty:
            wheres.append("q.difficulty = :difficulty"); params["difficulty"] = difficulty
        if lesson_id:
            wheres.append("q.lesson_id = :lesson_id"); params["lesson_id"] = lesson_id
        if wheres:
            base += " WHERE " + " AND ".join(wheres)
        base += " GROUP BY q.id ORDER BY q.topic, q.difficulty, q.id"
        rows = db.execute(sa.text(base), params).fetchall()
        result = []
        for r in rows:
            row = dict(r._mapping)
            if isinstance(row.get("options"), str):
                try:
                    row["options"] = json.loads(row["options"])
                except Exception:
                    pass
            if isinstance(row.get("correct_indices"), str):
                try:
                    row["correct_indices"] = json.loads(row["correct_indices"])
                except Exception:
                    pass
            result.append(row)
        return result
    finally:
        db.close()


@router.post("/quiz/questions", status_code=201)
async def create_quiz_question(
    body:          QuizQuestionCreate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Create a new quiz question."""
    _require_admin(authorization, request)
    valid_diffs  = {"beginner", "intermediate", "advanced"}
    if not body.topic or not body.topic.strip():
        raise HTTPException(status_code=422, detail="Topic is required.")
    if body.difficulty not in valid_diffs:
        raise HTTPException(status_code=422, detail=f"Invalid difficulty. Must be one of: {', '.join(valid_diffs)}")
    if body.correct_index >= len(body.options):
        raise HTTPException(status_code=422, detail="correct_index out of range for given options list.")
    if body.lesson_id:
        db = Session(engine)
        lesson = db.execute(sa.text("SELECT id FROM lessons WHERE id = :id"), {"id": body.lesson_id}).fetchone()
        db.close()
        if not lesson:
            raise HTTPException(status_code=404, detail="lesson_id not found.")
    db = Session(engine)
    try:
        result = db.execute(sa.text("""
            INSERT INTO quiz_questions
                (lesson_id, question_text, question_type, options, correct_index, correct_indices,
                 correct_answer, scenario_text, explanation, hint, topic, difficulty, image_url, video_url, media_type,
                 mindmap_node_id)
            VALUES
                (:lesson_id, :question_text, :question_type, :options, :correct_index, :correct_indices,
                 :correct_answer, :scenario_text, :explanation, :hint, :topic, :difficulty, :image_url, :video_url, :media_type,
                 :mindmap_node_id)
        """), {
            "lesson_id":      body.lesson_id,
            "question_text":  body.question_text,
            "question_type":  body.question_type or "multiple_choice",
            "options":        json.dumps(body.options),
            "correct_index":  body.correct_index,
            "correct_indices": json.dumps(body.correct_indices) if body.correct_indices is not None else None,
            "correct_answer": body.correct_answer,
            "scenario_text":  body.scenario_text,
            "explanation":    body.explanation,
            "hint":           body.hint,
            "topic":          body.topic,
            "difficulty":     body.difficulty,
            "image_url":      body.image_url,
            "video_url":      body.video_url,
            "media_type":     body.media_type or "text",
            "mindmap_node_id": body.mindmap_node_id,
        })
        db.commit()
        new_id = result.lastrowid
        row = db.execute(sa.text("SELECT * FROM quiz_questions WHERE id = :id"), {"id": new_id}).fetchone()
        out = dict(row._mapping)
        if isinstance(out.get("options"), str):
            out["options"] = json.loads(out["options"])
        _adm = _require_admin(authorization, request)
        log_action(
            "quiz_question.create", "quiz_question", new_id,
            entity_label=(body.question_text or "")[:80],
            detail={"topic": body.topic, "difficulty": body.difficulty},
            **extract_admin_context(request, _adm),
        )
        return out
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.put("/quiz/questions/{question_id}")
async def update_quiz_question(
    question_id:   int,
    body:          QuizQuestionUpdate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Edit an existing quiz question."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT * FROM quiz_questions WHERE id = :id"), {"id": question_id}
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Question not found.")

        fields, params = [], {"id": question_id}
        if body.question_text is not None:
            fields.append("question_text = :question_text"); params["question_text"] = body.question_text
        if body.question_type is not None:
            fields.append("question_type = :question_type"); params["question_type"] = body.question_type
        if body.options is not None:
            # Validate correct_index against new options if both provided
            ci = body.correct_index if body.correct_index is not None else existing.correct_index
            if body.question_type not in ("identification", "multiple_answer") and ci >= len(body.options):
                raise HTTPException(status_code=422, detail="correct_index out of range for given options.")
            fields.append("options = :options"); params["options"] = json.dumps(body.options)
        if body.correct_index is not None:
            fields.append("correct_index = :correct_index"); params["correct_index"] = body.correct_index
        if body.correct_indices is not None:
            fields.append("correct_indices = :correct_indices"); params["correct_indices"] = json.dumps(body.correct_indices)
        if body.correct_answer is not None:
            fields.append("correct_answer = :correct_answer"); params["correct_answer"] = body.correct_answer
        if body.scenario_text is not None:
            fields.append("scenario_text = :scenario_text"); params["scenario_text"] = body.scenario_text
        if body.explanation is not None:
            fields.append("explanation = :explanation"); params["explanation"] = body.explanation
        if body.hint is not None:
            fields.append("hint = :hint"); params["hint"] = body.hint
        if body.topic is not None:
            fields.append("topic = :topic"); params["topic"] = body.topic
        if body.difficulty is not None:
            fields.append("difficulty = :difficulty"); params["difficulty"] = body.difficulty
        if body.lesson_id is not None:
            fields.append("lesson_id = :lesson_id"); params["lesson_id"] = body.lesson_id
        if body.image_url is not None:
            fields.append("image_url = :image_url"); params["image_url"] = body.image_url
        if body.video_url is not None:
            fields.append("video_url = :video_url"); params["video_url"] = body.video_url
        if body.media_type is not None:
            fields.append("media_type = :media_type"); params["media_type"] = body.media_type
        if body.mindmap_node_id is not None:
            fields.append("mindmap_node_id = :mindmap_node_id"); params["mindmap_node_id"] = body.mindmap_node_id or None

        if not fields:
            return {"detail": "Nothing to update."}

        db.execute(sa.text(f"UPDATE quiz_questions SET {', '.join(fields)} WHERE id = :id"), params)
        db.commit()
        row = db.execute(sa.text("SELECT * FROM quiz_questions WHERE id = :id"), {"id": question_id}).fetchone()
        out = dict(row._mapping)
        if isinstance(out.get("options"), str):
            out["options"] = json.loads(out["options"])
        _adm = _require_admin(authorization, request)
        log_action(
            "quiz_question.update", "quiz_question", question_id,
            entity_label=(body.question_text or existing.question_text or "")[:80],
            detail={k: params[k] for k in fields if k not in ("id",) and k in params},
            **extract_admin_context(request, _adm),
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.delete("/quiz/questions/{question_id}", status_code=204)
async def delete_quiz_question(
    question_id:   int,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Delete a quiz question and all its attempt records.
    If the question's image_url points to a local upload that is not referenced
    by any other quiz question or lesson, the file is also removed from disk.
    """
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT id, image_url FROM quiz_questions WHERE id = :id"), {"id": question_id}
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Question not found.")

        image_url_to_check = existing.image_url  # may be None
        # Also grab video_url if the column exists
        video_url_to_check = None
        try:
            vrow = db.execute(
                sa.text("SELECT video_url FROM quiz_questions WHERE id = :id"), {"id": question_id}
            ).fetchone()
            if vrow:
                video_url_to_check = vrow.video_url
        except Exception:
            pass  # column not yet migrated

        db.execute(sa.text("DELETE FROM quiz_questions WHERE id = :id"), {"id": question_id})
        db.commit()

        # ── Orphan cleanup: delete any local upload only if no other record references it ──
        for url_to_check in [image_url_to_check, video_url_to_check]:
            if not url_to_check or not url_to_check.startswith("/assets/uploads/"):
                continue
            other_quiz_img = db.execute(
                sa.text("SELECT id FROM quiz_questions WHERE image_url = :url LIMIT 1"),
                {"url": url_to_check},
            ).fetchone()
            other_quiz_vid = None
            try:
                other_quiz_vid = db.execute(
                    sa.text("SELECT id FROM quiz_questions WHERE video_url = :url LIMIT 1"),
                    {"url": url_to_check},
                ).fetchone()
            except Exception:
                pass
            other_lesson = db.execute(
                sa.text("SELECT id FROM lessons WHERE image_url = :url LIMIT 1"),
                {"url": url_to_check},
            ).fetchone()
            if not other_quiz_img and not other_quiz_vid and not other_lesson:
                file_path = _UPLOAD_DIR / Path(url_to_check).name
                file_path.unlink(missing_ok=True)
                logger.info(f"[Upload] Deleted orphaned file {file_path.name}")

        _adm = _require_admin(authorization, request)
        log_action(
            "quiz_question.delete", "quiz_question", question_id,
            entity_label=str(question_id),
            **extract_admin_context(request, _adm),
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.get("/quiz/questions/{question_id}/stats")
async def get_question_stats(
    question_id:   int,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Per-question accuracy breakdown — how many users got it right, wrong, and per option."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        q = db.execute(
            sa.text("SELECT * FROM quiz_questions WHERE id = :id"), {"id": question_id}
        ).fetchone()
        if not q:
            raise HTTPException(status_code=404, detail="Question not found.")

        attempts_row = db.execute(sa.text("""
            SELECT
                COUNT(*)                                        AS total_attempts,
                SUM(is_correct)                                 AS correct_count,
                ROUND(AVG(is_correct) * 100, 1)                AS accuracy_pct,
                COUNT(DISTINCT user_id)                         AS unique_users
            FROM quiz_attempts
            WHERE question_id = :qid
        """), {"qid": question_id}).fetchone()

        option_rows = db.execute(sa.text("""
            SELECT selected_index, COUNT(*) AS count
            FROM quiz_attempts
            WHERE question_id = :qid
            GROUP BY selected_index
            ORDER BY selected_index
        """), {"qid": question_id}).fetchall()

        options = json.loads(q.options) if isinstance(q.options, str) else (q.options or [])
        option_breakdown = []
        option_counts = {r.selected_index: r.count for r in option_rows}
        total = attempts_row.total_attempts or 0
        for i, opt_text in enumerate(options):
            cnt = option_counts.get(i, 0)
            option_breakdown.append({
                "index":      i,
                "text":       opt_text,
                "is_correct": i == q.correct_index,
                "count":      cnt,
                "pct":        round(cnt / total * 100, 1) if total > 0 else 0,
            })

        return {
            "question_id":    question_id,
            "question_text":  q.question_text,
            "topic":          q.topic,
            "difficulty":     q.difficulty,
            "total_attempts": total,
            "correct_count":  attempts_row.correct_count or 0,
            "accuracy_pct":   attempts_row.accuracy_pct or 0,
            "unique_users":   attempts_row.unique_users or 0,
            "option_breakdown": option_breakdown,
        }
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# LESSON MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/lessons")
async def admin_list_lessons(request: Request, authorization: str = Header(None)):
    """List all lessons with trigger counts and read rates."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text("""
            SELECT
                l.*,
                COUNT(DISTINCT qq.id)               AS question_count
            FROM lessons l
            LEFT JOIN quiz_questions qq ON qq.lesson_id = l.id
            GROUP BY l.id
            ORDER BY COALESCE(l.sort_order, 9999), l.topic, l.id
        """)).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


@router.post("/lessons", status_code=201)
async def create_lesson(
    body:          LessonCreate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Create a new lesson."""
    _require_admin(authorization, request)
    valid_diffs  = {"beginner", "intermediate", "advanced"}
    if not body.topic or not body.topic.strip():
        raise HTTPException(status_code=422, detail="Topic is required.")
    if body.difficulty not in valid_diffs:
        raise HTTPException(status_code=422, detail=f"Invalid difficulty.")
    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT id FROM lessons WHERE lesson_key = :k"), {"k": body.lesson_key}
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="lesson_key already exists.")
        result = db.execute(sa.text("""
            INSERT INTO lessons (lesson_key, title, content, topic, difficulty, mil_skill, sort_order, prerequisite_lesson_id, image_url, mindmap_node_id, created_at)
            VALUES (:lesson_key, :title, :content, :topic, :difficulty, :mil_skill, :sort_order, :prereq, :image_url, :mindmap_node_id, :now)
        """), {
            "lesson_key": body.lesson_key, "title": body.title, "content": body.content,
            "topic": body.topic, "difficulty": body.difficulty, "mil_skill": body.mil_skill,
            "sort_order": body.sort_order, "prereq": body.prerequisite_lesson_id,
            "image_url": body.image_url, "mindmap_node_id": body.mindmap_node_id,
            "now": datetime.now(timezone.utc),
        })
        db.commit()
        new_id = result.lastrowid
        row = db.execute(sa.text("SELECT * FROM lessons WHERE id = :id"), {"id": new_id}).fetchone()
        _adm = _require_admin(authorization, request)
        log_action(
            "lesson.create", "lesson", new_id,
            entity_label=body.title,
            detail={"topic": body.topic, "difficulty": body.difficulty, "lesson_key": body.lesson_key},
            **extract_admin_context(request, _adm),
        )
        return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.put("/lessons/{lesson_id}")
async def update_lesson(
    lesson_id:     int,
    body:          LessonUpdate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Edit an existing lesson."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(sa.text("SELECT id FROM lessons WHERE id = :id"), {"id": lesson_id}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        fields, params = [], {"id": lesson_id}
        _LESSON_UPDATE_ALLOWED_COLS = {"title", "content", "topic", "difficulty", "mil_skill", "sort_order"}
        for col in ["title", "content", "topic", "difficulty", "mil_skill", "sort_order"]:
            assert col in _LESSON_UPDATE_ALLOWED_COLS, f"Unexpected column: {col}"
            val = getattr(body, col)
            if val is not None:
                fields.append(f"{col} = :{col}"); params[col] = val
        if body.prerequisite_lesson_id is not None:
            fields.append("prerequisite_lesson_id = :prereq"); params["prereq"] = body.prerequisite_lesson_id
        if body.image_url is not None:
            fields.append("image_url = :image_url"); params["image_url"] = body.image_url
        if body.is_published is not None:
            fields.append("is_published = :is_published"); params["is_published"] = int(body.is_published)
        if body.mindmap_node_id is not None:
            fields.append("mindmap_node_id = :mindmap_node_id"); params["mindmap_node_id"] = body.mindmap_node_id or None
        if not fields:
            return {"detail": "Nothing to update."}
        db.execute(sa.text(f"UPDATE lessons SET {', '.join(fields)} WHERE id = :id"), params)
        db.commit()
        row = db.execute(sa.text("SELECT * FROM lessons WHERE id = :id"), {"id": lesson_id}).fetchone()
        out = dict(row._mapping)
        _adm = _require_admin(authorization, request)
        log_action(
            "lesson.update", "lesson", lesson_id,
            entity_label=out.get("title", ""),
            detail={k: params[k] for k in params if k not in ("id",)},
            **extract_admin_context(request, _adm),
        )
        return out
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.delete("/lessons/{lesson_id}", status_code=204)
async def delete_lesson(
    lesson_id:     int,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Delete a lesson. Will fail if quiz questions are still linked to it."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(sa.text("SELECT id, title FROM lessons WHERE id = :id"), {"id": lesson_id}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Lesson not found.")
        linked = db.execute(
            sa.text("SELECT COUNT(*) FROM quiz_questions WHERE lesson_id = :id"), {"id": lesson_id}
        ).scalar()
        if linked:
            raise HTTPException(status_code=409, detail=f"Cannot delete: {linked} quiz question(s) are linked to this lesson. Remove them first.")
        lesson_title = existing[1] if existing else ""
        db.execute(sa.text("DELETE FROM lessons WHERE id = :id"), {"id": lesson_id})
        db.commit()
        _adm = _require_admin(authorization, request)
        log_action(
            "lesson.delete", "lesson", lesson_id,
            entity_label=lesson_title,
            **extract_admin_context(request, _adm),
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.get("/lessons/impact")
async def get_lesson_impact(request: Request, authorization: str = Header(None)):
    """Lesson impact: trigger count, read rate, completion count."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text("""
            SELECT
                l.id, l.lesson_key, l.title, l.topic, l.difficulty,
                COUNT(DISTINCT lt.id)               AS trigger_count,
                SUM(COALESCE(lt.was_read, 0))       AS read_count,
                ROUND(AVG(COALESCE(lt.was_read,0)) * 100, 1) AS read_rate_pct,
                COUNT(DISTINCT lc.id)               AS completion_count
            FROM lessons l
            LEFT JOIN lessons_triggered lt ON lt.lesson_id = l.id
            LEFT JOIN lesson_completions lc ON lc.lesson_id = l.id
            GROUP BY l.id, l.lesson_key, l.title, l.topic, l.difficulty
            ORDER BY trigger_count DESC
        """)).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(request: Request, authorization: str = Header(None)):
    """List all users with activity stats."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text("""
            SELECT
                u.id, u.username, u.email, u.role, u.created_at,
                COUNT(DISTINCT s.id)  AS submission_count,
                COUNT(DISTINCT lc.id) AS lessons_completed,
                COUNT(DISTINCT qa.id) AS quiz_attempts,
                MAX(s.created_at)     AS last_active_at
            FROM users u
            LEFT JOIN submissions        s  ON s.user_id  = u.id
            LEFT JOIN lesson_completions lc ON lc.user_id = u.id
            LEFT JOIN quiz_attempts      qa ON qa.user_id  = u.id
            GROUP BY u.id, u.username, u.email, u.role, u.created_at
            ORDER BY u.created_at DESC
        """)).fetchall()
        result = []
        for r in rows:
            row = dict(r._mapping)
            row["created_at"]     = str(row["created_at"])    if row.get("created_at")     else None
            row["last_active_at"] = str(row["last_active_at"]) if row.get("last_active_at") else None
            result.append(row)
        return result
    finally:
        db.close()


@router.post("/users", status_code=201)
async def create_user(
    body:          UserCreate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Create a new user account (admin-side)."""
    _require_admin(authorization, request)
    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=422, detail="Role must be 'user' or 'admin'.")
    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    db = Session(engine)
    try:
        exists = db.execute(
            sa.text("SELECT id FROM users WHERE email=:e OR username=:u"),
            {"e": body.email, "u": body.username},
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="Email or username already taken.")
        result = db.execute(sa.text("""
            INSERT INTO users (username, email, password_hash, role, created_at, updated_at)
            VALUES (:username, :email, :pw, :role, :now, :now)
        """), {"username": body.username, "email": body.email, "pw": pw_hash,
               "role": body.role, "now": datetime.now(timezone.utc)})
        db.commit()
        new_user_id = result.lastrowid
        row = db.execute(sa.text(
            "SELECT id, username, email, role, created_at FROM users WHERE id = :id"
        ), {"id": new_user_id}).fetchone()
        _adm = _require_admin(authorization, request)
        log_action(
            "user.create", "user", new_user_id,
            entity_label=body.username,
            detail={"email": body.email, "role": body.role},
            **extract_admin_context(request, _adm),
        )
        return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.put("/users/{user_id}")
async def update_user(
    user_id:       int,
    body:          UserUpdate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Edit a user's username, email, password, or role."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(
            sa.text("SELECT id, role FROM users WHERE id = :id"), {"id": user_id}
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found.")
        if body.role and body.role not in ("user", "admin"):
            raise HTTPException(status_code=422, detail="Role must be 'user' or 'admin'.")
        # Prevent demoting the last admin
        if body.role == "user" and existing.role == "admin":
            admin_count = db.execute(sa.text("SELECT COUNT(*) FROM users WHERE role='admin'")).scalar()
            if admin_count <= 1:
                raise HTTPException(status_code=409, detail="Cannot demote the last admin account.")
        fields, params = [], {"id": user_id}
        if body.username is not None:
            # Check uniqueness
            clash = db.execute(sa.text(
                "SELECT id FROM users WHERE username=:u AND id != :id"
            ), {"u": body.username, "id": user_id}).fetchone()
            if clash:
                raise HTTPException(status_code=409, detail="Username already taken.")
            fields.append("username = :username"); params["username"] = body.username
        if body.email is not None:
            clash = db.execute(sa.text(
                "SELECT id FROM users WHERE email=:e AND id != :id"
            ), {"e": body.email, "id": user_id}).fetchone()
            if clash:
                raise HTTPException(status_code=409, detail="Email already taken.")
            fields.append("email = :email"); params["email"] = body.email
        if body.password is not None:
            pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
            fields.append("password_hash = :pw"); params["pw"] = pw_hash
        if body.role is not None:
            fields.append("role = :role"); params["role"] = body.role
        fields.append("updated_at = :now"); params["now"] = datetime.now(timezone.utc)
        if len(fields) == 1:  # only updated_at
            return {"detail": "Nothing to update."}
        db.execute(sa.text(f"UPDATE users SET {', '.join(fields)} WHERE id = :id"), params)
        db.commit()
        row = db.execute(sa.text(
            "SELECT id, username, email, role, created_at FROM users WHERE id = :id"
        ), {"id": user_id}).fetchone()
        _adm = _require_admin(authorization, request)
        changed = {k: v for k, v in body.dict(exclude_none=True).items() if k != "password"}
        if "password" in body.dict(exclude_none=True):
            changed["password"] = "***"
        log_action(
            "user.update", "user", user_id,
            entity_label=body.username or str(user_id),
            detail=changed,
            **extract_admin_context(request, _adm),
        )
        return dict(row._mapping)
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id:       int,
    body:          RoleUpdate,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Promote or demote a user's role."""
    _require_admin(authorization, request)
    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=422, detail="Role must be 'user' or 'admin'.")
    db = Session(engine)
    try:
        existing = db.execute(sa.text("SELECT id FROM users WHERE id = :id"), {"id": user_id}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found.")
        db.execute(sa.text("UPDATE users SET role = :role WHERE id = :id"), {"role": body.role, "id": user_id})
        db.commit()
        _adm = _require_admin(authorization, request)
        log_action(
            "user.role_change", "user", user_id,
            entity_label=str(user_id),
            detail={"new_role": body.role},
            **extract_admin_context(request, _adm),
        )
        return {"user_id": user_id, "role": body.role}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id:       int,
    request:       Request = None,
    authorization: str     = Header(None),
):
    """Delete a user account and all associated data."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(sa.text("SELECT id, role FROM users WHERE id = :id"), {"id": user_id}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found.")
        if existing.role == "admin":
            # Prevent deleting the last admin
            admin_count = db.execute(sa.text("SELECT COUNT(*) FROM users WHERE role = 'admin'")).scalar()
            if admin_count <= 1:
                raise HTTPException(status_code=409, detail="Cannot delete the last admin account.")
        username_snap = db.execute(sa.text("SELECT username FROM users WHERE id = :id"), {"id": user_id}).fetchone()
        username_label = username_snap.username if username_snap else str(user_id)
        db.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
        _adm = _require_admin(authorization, request)
        log_action(
            "user.delete", "user", user_id,
            entity_label=username_label,
            **extract_admin_context(request, _adm),
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH & ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/analytics/skills")
async def get_skill_distribution(request: Request, authorization: str = Header(None)):
    """Aggregate MIL skill level distribution across all users."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text("""
            SELECT
                topic,
                current_level,
                COUNT(*) AS user_count,
                ROUND(AVG(COALESCE(quiz_accuracy_pct, 0)), 1) AS avg_quiz_accuracy,
                SUM(lessons_completed) AS total_lessons_completed
            FROM user_skill_progress
            GROUP BY topic, current_level
            ORDER BY topic, FIELD(current_level, 'beginner', 'intermediate', 'advanced')
        """)).fetchall()

        # Also get top weak topics from lesson triggers
        weak_topics = db.execute(sa.text("""
            SELECT l.topic, COUNT(*) AS trigger_count
            FROM lessons_triggered lt
            JOIN lessons l ON l.id = lt.lesson_id
            GROUP BY l.topic
            ORDER BY trigger_count DESC
        """)).fetchall()

        return {
            "skill_distribution": [dict(r._mapping) for r in rows],
            "weak_topics":        [dict(r._mapping) for r in weak_topics],
        }
    finally:
        db.close()


@router.get("/analytics/lessons-heatmap")
async def get_lessons_heatmap(request: Request, authorization: str = Header(None)):
    """Lesson trigger heatmap — which topics are triggered most, and when."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        by_topic = db.execute(sa.text("""
            SELECT l.topic, l.title, l.lesson_key,
                   COUNT(lt.id) AS trigger_count,
                   SUM(lt.was_read) AS read_count,
                   ROUND(AVG(lt.was_read) * 100, 1) AS read_rate_pct
            FROM lessons l
            LEFT JOIN lessons_triggered lt ON lt.lesson_id = l.id
            GROUP BY l.id, l.topic, l.title, l.lesson_key
            ORDER BY trigger_count DESC
        """)).fetchall()

        by_day = db.execute(sa.text("""
            SELECT DATE(lt.triggered_at) AS day, COUNT(*) AS trigger_count
            FROM lessons_triggered lt
            WHERE lt.triggered_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
            GROUP BY DATE(lt.triggered_at)
            ORDER BY day ASC
        """)).fetchall()

        return {
            "by_lesson": [dict(r._mapping) for r in by_topic],
            "by_day_30d": [{"day": str(r.day), "trigger_count": r.trigger_count} for r in by_day],
        }
    finally:
        db.close()


@router.get("/analytics/pretest")
async def get_pretest_analytics(request: Request, authorization: str = Header(None)):
    """Pre-test vs post-test improvement stats."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text("""
            SELECT
                phase,
                COUNT(*) AS total_submissions,
                ROUND(AVG(score_pct), 1) AS avg_score_pct,
                ROUND(MIN(score_pct), 1) AS min_score_pct,
                ROUND(MAX(score_pct), 1) AS max_score_pct
            FROM pretest_results
            GROUP BY phase
        """)).fetchall()

        # Users who completed both
        paired = db.execute(sa.text("""
            SELECT
                COUNT(*) AS paired_users,
                ROUND(AVG(post.score_pct - pre.score_pct), 1) AS avg_improvement_pct
            FROM pretest_results pre
            JOIN pretest_results post
              ON post.user_id = pre.user_id
             AND post.phase = 'posttest'
            WHERE pre.phase = 'pretest'
        """)).fetchone()

        return {
            "by_phase":      [dict(r._mapping) for r in rows],
            "paired_users":  paired.paired_users or 0,
            "avg_improvement_pct": paired.avg_improvement_pct,
        }
    finally:
        db.close()


@router.get("/analytics/quiz")
async def get_quiz_analytics(request: Request, authorization: str = Header(None)):
    """Quiz accuracy breakdown by topic and difficulty."""
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        by_topic = db.execute(sa.text("""
            SELECT
                q.topic,
                COUNT(DISTINCT qa.id)                   AS total_attempts,
                COUNT(DISTINCT qa.user_id)              AS unique_users,
                ROUND(AVG(qa.is_correct) * 100, 1)     AS accuracy_pct
            FROM quiz_attempts qa
            JOIN quiz_questions q ON q.id = qa.question_id
            GROUP BY q.topic
            ORDER BY accuracy_pct ASC
        """)).fetchall()

        by_difficulty = db.execute(sa.text("""
            SELECT
                q.difficulty,
                COUNT(DISTINCT qa.id)                   AS total_attempts,
                ROUND(AVG(qa.is_correct) * 100, 1)     AS accuracy_pct
            FROM quiz_attempts qa
            JOIN quiz_questions q ON q.id = qa.question_id
            GROUP BY q.difficulty
            ORDER BY FIELD(q.difficulty, 'beginner', 'intermediate', 'advanced')
        """)).fetchall()

        # Hardest questions (lowest accuracy, min 5 attempts)
        hardest = db.execute(sa.text("""
            SELECT
                q.id, q.question_text, q.topic, q.difficulty,
                COUNT(qa.id) AS attempts,
                ROUND(AVG(qa.is_correct) * 100, 1) AS accuracy_pct
            FROM quiz_questions q
            JOIN quiz_attempts qa ON qa.question_id = q.id
            GROUP BY q.id, q.question_text, q.topic, q.difficulty
            HAVING attempts >= 5
            ORDER BY accuracy_pct ASC
            LIMIT 10
        """)).fetchall()

        return {
            "by_topic":      [dict(r._mapping) for r in by_topic],
            "by_difficulty": [dict(r._mapping) for r in by_difficulty],
            "hardest_questions": [dict(r._mapping) for r in hardest],
        }
    finally:
        db.close()



# ══════════════════════════════════════════════════════════════════════════════
# PRETEST CLAIMS
# ══════════════════════════════════════════════════════════════════════════════

class PretestClaimBody(BaseModel):
    text: str
    question_type: str = "true_false"
    correct_answer: Optional[str] = "True"
    options: Optional[str] = None
    correct_index: int = 0
    sort_order: Optional[int] = None
    is_active: Optional[int] = 1

@router.get("/pretest-claims")
async def list_pretest_claims(request: Request, authorization: str = Header(None)):
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text(
            "SELECT * FROM pretest_claims ORDER BY sort_order, id"
        )).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()

@router.post("/pretest-claims", status_code=201)
async def create_pretest_claim(body: PretestClaimBody, request: Request, authorization: str = Header(None)):
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        result = db.execute(sa.text(
            "INSERT INTO pretest_claims (text, question_type, correct_answer, options, correct_index, sort_order, is_active) "
            "VALUES (:text, :qtype, :answer, :options, :cidx, :sort, :active)"
        ), {"text": body.text, "qtype": body.question_type, "answer": body.correct_answer,
            "options": body.options, "cidx": body.correct_index,
            "sort": body.sort_order or 0, "active": body.is_active})
        db.commit()
        new_id = result.lastrowid
        log_action("pretest_claim.create", "pretest_claim", new_id,
                   entity_label=(body.text or "")[:80],
                   detail={"question_type": body.question_type},
                   **extract_admin_context(request, ctx))
        return {"id": new_id}
    finally:
        db.close()

@router.put("/pretest-claims/{claim_id}")
async def update_pretest_claim(claim_id: int, body: PretestClaimBody, request: Request, authorization: str = Header(None)):
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        db.execute(sa.text(
            "UPDATE pretest_claims SET text=:text, question_type=:qtype, correct_answer=:answer, "
            "options=:options, correct_index=:cidx, sort_order=:sort, is_active=:active, "
            "updated_at=NOW() WHERE id=:id"
        ), {"text": body.text, "qtype": body.question_type, "answer": body.correct_answer,
            "options": body.options, "cidx": body.correct_index,
            "sort": body.sort_order or 0, "active": body.is_active, "id": claim_id})
        db.commit()
        log_action("pretest_claim.update", "pretest_claim", claim_id,
                   entity_label=(body.text or "")[:80],
                   **extract_admin_context(request, ctx))
        return {"ok": True}
    finally:
        db.close()

@router.delete("/pretest-claims/{claim_id}", status_code=204)
async def delete_pretest_claim(claim_id: int, request: Request, authorization: str = Header(None)):
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(sa.text("SELECT text FROM pretest_claims WHERE id=:id"), {"id": claim_id}).fetchone()
        db.execute(sa.text("DELETE FROM pretest_claims WHERE id=:id"), {"id": claim_id})
        db.commit()
        log_action("pretest_claim.delete", "pretest_claim", claim_id,
                   entity_label=(existing[0] if existing else "")[:80],
                   **extract_admin_context(request, ctx))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# EVAL QUESTIONS
# ══════════════════════════════════════════════════════════════════════════════

class BranchBody(BaseModel):
    id: Optional[int] = None
    trigger_condition: str = "equals"
    trigger_value: Optional[str] = None
    followup_prompt: Optional[str] = None
    followup_type: str = "hint"
    content_type: Optional[str] = None
    lesson_id: Optional[int] = None
    quiz_question_id: Optional[int] = None
    content_url: Optional[str] = None
    is_active: int = 1

class EvalQuestionBody(BaseModel):
    step_number: int = 1
    title: str
    step_label: Optional[str] = None
    prompt: str
    hint: Optional[str] = None
    input_type: str = "text"
    options: Optional[str] = None
    is_active: Optional[int] = 1
    step_link_type: Optional[str] = None   # url | lesson | quiz | mindmap | dashboard
    step_link_value: Optional[str] = None  # the URL, lesson_key, quiz id, etc.
    mindmap_node_id: Optional[str] = None  # FK → mindmap_nodes.id; completing this step unlocks the node
    branches: Optional[list] = []

def _load_branches(db, question_id: int):
    rows = db.execute(sa.text(
        "SELECT * FROM eval_question_branches WHERE question_id=:qid ORDER BY sort_order, id"
    ), {"qid": question_id}).fetchall()
    return [dict(r._mapping) for r in rows]

def _save_branches(db, question_id: int, branches: list):
    """Upsert branches — keep existing IDs, delete removed ones."""
    existing_ids = [r[0] for r in db.execute(sa.text(
        "SELECT id FROM eval_question_branches WHERE question_id=:qid"
    ), {"qid": question_id}).fetchall()]
    kept_ids = []
    for b in (branches or []):
        if not b.get("followup_prompt"):
            continue
        bid = b.get("id")
        params = {
            "qid": question_id, "cond": b.get("trigger_condition", "equals"),
            "tval": b.get("trigger_value") or "",
            "prompt": b.get("followup_prompt"), "ftype": b.get("followup_type", "hint"),
            "ctype": b.get("content_type") or None, "lid": b.get("lesson_id") or None,
            "qqid": b.get("quiz_question_id") or None, "curl": b.get("content_url") or None,
            "active": int(b.get("is_active", 1)),
        }
        if bid and int(bid) in existing_ids:
            db.execute(sa.text(
                "UPDATE eval_question_branches SET trigger_condition=:cond, trigger_value=:tval, "
                "followup_prompt=:prompt, followup_type=:ftype, content_type=:ctype, "
                "lesson_id=:lid, quiz_question_id=:qqid, content_url=:curl, is_active=:active "
                "WHERE id=:bid AND question_id=:qid"
            ), {**params, "bid": int(bid)})
            kept_ids.append(int(bid))
        else:
            result = db.execute(sa.text(
                "INSERT INTO eval_question_branches "
                "(question_id, trigger_condition, trigger_value, followup_prompt, followup_type, "
                "content_type, lesson_id, quiz_question_id, content_url, is_active) "
                "VALUES (:qid,:cond,:tval,:prompt,:ftype,:ctype,:lid,:qqid,:curl,:active)"
            ), params)
            kept_ids.append(result.lastrowid)
    # Delete removed branches
    for eid in existing_ids:
        if eid not in kept_ids:
            db.execute(sa.text("DELETE FROM eval_question_branches WHERE id=:id"), {"id": eid})

@router.get("/eval-questions")
async def list_eval_questions(request: Request, authorization: str = Header(None)):
    _require_admin(authorization, request)
    db = Session(engine)
    try:
        rows = db.execute(sa.text(
            "SELECT * FROM eval_questions ORDER BY step_number, id"
        )).fetchall()
        questions = [dict(r._mapping) for r in rows]
        for q in questions:
            q["branches"] = _load_branches(db, q["id"])
        return questions
    finally:
        db.close()

@router.post("/eval-questions", status_code=201)
async def create_eval_question(body: EvalQuestionBody, request: Request, authorization: str = Header(None)):
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        result = db.execute(sa.text(
            "INSERT INTO eval_questions (step_number, title, step_label, prompt, hint, input_type, options, is_active, step_link_type, step_link_value, mindmap_node_id) "
            "VALUES (:step, :title, :step_label, :prompt, :hint, :itype, :options, :active, :link_type, :link_value, :mindmap_node_id)"
        ), {"step": body.step_number, "title": body.title, "step_label": body.step_label,
            "prompt": body.prompt, "hint": body.hint, "itype": body.input_type,
            "options": body.options, "active": body.is_active,
            "link_type": body.step_link_type, "link_value": body.step_link_value,
            "mindmap_node_id": body.mindmap_node_id})
        qid = result.lastrowid
        _save_branches(db, qid, body.branches or [])
        db.commit()
        log_action("eval_question.create", "eval_question", qid,
                   entity_label=(body.title or body.prompt or "")[:80],
                   detail={"step_number": body.step_number, "input_type": body.input_type},
                   **extract_admin_context(request, ctx))
        return {"id": qid}
    finally:
        db.close()

@router.put("/eval-questions/{question_id}")
async def update_eval_question(question_id: int, body: EvalQuestionBody, request: Request, authorization: str = Header(None)):
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        db.execute(sa.text(
            "UPDATE eval_questions SET step_number=:step, title=:title, step_label=:step_label, prompt=:prompt, "
            "hint=:hint, input_type=:itype, options=:options, is_active=:active, "
            "step_link_type=:link_type, step_link_value=:link_value, mindmap_node_id=:mindmap_node_id, "
            "updated_at=NOW() WHERE id=:id"
        ), {"step": body.step_number, "title": body.title, "step_label": body.step_label,
            "prompt": body.prompt, "hint": body.hint, "itype": body.input_type,
            "options": body.options, "active": body.is_active,
            "link_type": body.step_link_type, "link_value": body.step_link_value,
            "mindmap_node_id": body.mindmap_node_id,
            "id": question_id})
        _save_branches(db, question_id, body.branches or [])
        db.commit()
        log_action("eval_question.update", "eval_question", question_id,
                   entity_label=(body.title or body.prompt or "")[:80],
                   **extract_admin_context(request, ctx))
        return {"ok": True}
    finally:
        db.close()

@router.delete("/eval-questions/{question_id}", status_code=204)
async def delete_eval_question(question_id: int, request: Request, authorization: str = Header(None)):
    ctx = _require_admin(authorization, request)
    db = Session(engine)
    try:
        existing = db.execute(sa.text("SELECT title, prompt FROM eval_questions WHERE id=:id"), {"id": question_id}).fetchone()
        db.execute(sa.text("DELETE FROM eval_question_branches WHERE question_id=:id"), {"id": question_id})
        db.execute(sa.text("DELETE FROM eval_questions WHERE id=:id"), {"id": question_id})
        db.commit()
        label = ((existing[0] or existing[1]) if existing else "")[:80]
        log_action("eval_question.delete", "eval_question", question_id,
                   entity_label=label,
                   **extract_admin_context(request, ctx))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# CORPUS MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/corpus/stats")
async def get_corpus_stats(request: Request, authorization: str = Header(None)):
    """
    Return basic statistics about the SQLite corpus used for evidence retrieval.
    Reads from corpus.db (relative to project root) via sqlite3.
    """
    _require_admin(authorization, request)

    corpus_paths = [
        _CORPUS_DB,                                          # routers/data/corpus.db
        Path(__file__).resolve().parent.parent / "corpus.db",  # project root
        Path(__file__).resolve().parent.parent / "data" / "corpus.db",
    ]
    db_path = next((p for p in corpus_paths if p.exists()), None)

    if db_path is None:
        return {
            "sentence_count": 0,
            "source_count":   0,
            "pipelines":      [],
            "size_mb":        0,
            "db_path":        None,
            "message":        "corpus.db not found — ingest sentences to create it.",
        }

    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()

        sentence_count = cur.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]
        try:
            source_count = cur.execute(
                "SELECT COUNT(DISTINCT source_domain) FROM corpus"
            ).fetchone()[0]
        except Exception:
            source_count = 0

        try:
            pipeline_rows = cur.execute(
                "SELECT DISTINCT pipeline FROM corpus ORDER BY pipeline"
            ).fetchall()
            pipelines = [r[0] for r in pipeline_rows if r[0]]
        except Exception:
            pipelines = []

        con.close()
        size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)

        return {
            "sentence_count": sentence_count,
            "source_count":   source_count,
            "pipelines":      pipelines,
            "size_mb":        size_mb,
            "db_path":        str(db_path),
        }
    except Exception as exc:
        logger.warning(f"[corpus/stats] error: {exc}")
        raise HTTPException(status_code=500, detail=f"Could not read corpus.db: {exc}")


@router.post("/corpus/ingest", status_code=201)
async def ingest_corpus(
    body:          dict,
    request:       Request,
    authorization: str = Header(None),
):
    """
    Save curated sentences to the SQLite corpus used for evidence retrieval.
    body: {sentences: list[str], source_domain: str, source_name: str, pipeline: str}
    Creates corpus.db if it doesn't exist.
    """
    _require_admin(authorization, request)

    sentences     = body.get("sentences") or []
    source_domain = (body.get("source_domain") or "").strip()
    source_name   = (body.get("source_name") or source_domain).strip()
    pipeline      = (body.get("pipeline") or "manual").strip()

    if not sentences:
        raise HTTPException(status_code=422, detail="sentences list is required and must not be empty.")
    if not source_domain:
        raise HTTPException(status_code=422, detail="source_domain is required.")

    # Prefer the project-root corpus.db; create it if missing
    db_path = Path(__file__).resolve().parent.parent / "corpus.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS corpus (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                sentence      TEXT    NOT NULL,
                source_domain TEXT,
                source_name   TEXT,
                pipeline      TEXT,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        clean = [s.strip() for s in sentences if isinstance(s, str) and s.strip()]
        cur.executemany(
            "INSERT INTO corpus (sentence, source_domain, source_name, pipeline) VALUES (?,?,?,?)",
            [(s, source_domain, source_name, pipeline) for s in clean],
        )
        con.commit()
        inserted = cur.rowcount
        con.close()

        return {
            "inserted":      inserted,
            "source_domain": source_domain,
            "pipeline":      pipeline,
            "db_path":       str(db_path),
        }
    except Exception as exc:
        logger.error(f"[corpus/ingest] error: {exc}")
        raise HTTPException(status_code=500, detail=f"Corpus ingest failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/audit-log")
async def get_audit_log(
    request: Request,
    authorization: str = Header(None),
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    action: str = "",
    resource_type: str = "",
    resource: str = "",
):
    _require_admin(authorization, request)
    per_page = max(10, min(per_page, 200))
    offset = (page - 1) * per_page

    # Support both ?resource_type= and ?resource= for the filter
    effective_resource = resource or resource_type

    # Build dynamic WHERE clause
    conditions = []
    params: dict = {"limit": per_page, "offset": offset}

    if search:
        conditions.append(
            "(admin_username LIKE :search OR resource_id LIKE :search OR detail LIKE :search)"
        )
        params["search"] = f"%{search}%"
    if action:
        conditions.append("action = :action")
        params["action"] = action
    if effective_resource:
        conditions.append("resource_type = :resource_type")
        params["resource_type"] = effective_resource

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    db = Session(engine)
    try:
        rows = db.execute(sa.text(
            f"SELECT id, admin_id, admin_username, action, resource_type, resource_id, detail, ip_address, performed_at "
            f"FROM admin_audit_log {where} ORDER BY performed_at DESC LIMIT :limit OFFSET :offset"
        ), params).fetchall()
        count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
        total = db.execute(sa.text(
            f"SELECT COUNT(*) FROM admin_audit_log {where}"
        ), count_params).scalar()
        resource_types = [r[0] for r in db.execute(sa.text(
            "SELECT DISTINCT resource_type FROM admin_audit_log ORDER BY resource_type"
        )).fetchall()]
        return {
            "rows": [dict(r._mapping) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "resource_types": resource_types,
        }
    finally:
        db.close()


# ── Quiz Settings ─────────────────────────────────────────────────────────────

class QuizSettingsPatch(BaseModel):
    questions_per_session: int = Field(..., ge=1, le=50)

@router.get("/quiz/settings")
async def admin_get_quiz_settings(req: Request, authorization: str = Header(None)):
    """Return current quiz settings."""
    from config import QUIZ_QUESTIONS_PER_SESSION
    _require_admin(authorization, req)
    return {"questions_per_session": QUIZ_QUESTIONS_PER_SESSION}

@router.patch("/quiz/settings")
async def admin_patch_quiz_settings(body: QuizSettingsPatch, req: Request, authorization: str = Header(None)):
    """Update QUIZ_QUESTIONS_PER_SESSION in .env and reload config."""
    _require_admin(authorization, req)
    env_path = Path(".env")
    key = "QUIZ_QUESTIONS_PER_SESSION"
    new_val = str(body.questions_per_session)

    if env_path.exists():
        lines = env_path.read_text().splitlines(keepends=True)
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                lines[i] = f"{key}={new_val}\n"
                found = True
                break
        if not found:
            lines.append(f"{key}={new_val}\n")
        env_path.write_text("".join(lines))
    else:
        env_path.write_text(f"{key}={new_val}\n")

    # Update the live value in config module
    import config as _cfg
    _cfg.QUIZ_QUESTIONS_PER_SESSION = body.questions_per_session
    # Also update quiz router's imported copy
    import routers.quiz as _qr
    _qr.QUIZ_QUESTIONS_PER_SESSION = body.questions_per_session

    logger.info(f"[Admin] quiz questions_per_session updated to {new_val}")
    return {"questions_per_session": body.questions_per_session, "message": "Updated successfully."}
