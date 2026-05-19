"""
SocialProof — Audit Log Service
services/audit_log.py

Provides a single write-once helper: log_action().

Every admin-initiated mutation is recorded here.  The table is
append-only (no UPDATE/DELETE ever runs against it) so the log
cannot be silently tampered with via normal application code.

Schema (admin_audit_log):

    id            INT PK AUTO_INCREMENT
    admin_id      INT  — FK to users.id (nullable: system/startup actions)
    admin_username VARCHAR(50)  — snapshot so deleting the admin doesn't blank history
    action        ENUM('create','update','delete','role_change','upload','reorder')
    resource_type VARCHAR(50)   — 'lesson' | 'quiz_question' | 'user' | etc.
    resource_id   VARCHAR(100)  — PK of affected row (string so it works for varchar PKs too)
    detail        TEXT          — JSON payload (before/after diff or relevant context)
    ip_address    VARCHAR(45)   — IPv4 or IPv6 (nullable)
    performed_at  DATETIME      — UTC, set by DB DEFAULT CURRENT_TIMESTAMP

Actions (enum values): create, update, delete, role_change, upload, reorder

Usage pattern — pass the full dot-separated action (e.g. 'lesson.create') and
log_action() extracts the enum verb automatically.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# Lazy import to avoid circular imports — engine is imported on first call.
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from database.models import engine
        _engine = engine
    return _engine


# ── Public API ────────────────────────────────────────────────────────────────

_VALID_ACTIONS = {'create', 'update', 'delete', 'role_change', 'upload', 'reorder'}

def _resolve_action(action: str) -> str:
    """
    Accepts either a bare enum value ('create') or a dot-namespaced string
    ('lesson.create', 'user.role_change').  Extracts the verb and maps it
    to the nearest valid enum value.  Falls back to 'update' if unrecognised.
    """
    verb = action.split('.')[-1].lower()
    if verb in _VALID_ACTIONS:
        return verb
    # Fuzzy fallback mappings
    _map = {
        'publish': 'update', 'unpublish': 'update',
        'activate': 'update', 'deactivate': 'update',
        'ingest': 'upload', 'sync': 'update',
    }
    return _map.get(verb, 'update')


def log_action(
    action:        str,
    entity_type:   str,
    entity_id:     Any,
    entity_label:  str                    = "",   # kept for call-site compat; stored in detail
    admin_id:      Optional[int]          = None,
    admin_name:    str                    = "system",
    detail:        Optional[Dict[str, Any]] = None,
    ip_address:    Optional[str]          = None,
    user_agent:    Optional[str]          = None, # kept for call-site compat; not stored
) -> None:
    """
    Append one row to admin_audit_log.  Never raises — on DB failure the
    error is logged at WARNING level so the caller's transaction is not
    disrupted.

    Args:
        action:       Dot-separated or bare verb string, e.g. 'lesson.create' or 'create'.
        entity_type:  Resource category (resource_type), e.g. 'lesson'.
        entity_id:    Primary key of the affected row (int or str).
        entity_label: Human-readable label — folded into detail JSON for context.
        admin_id:     users.id of the acting admin (None for system actions).
        admin_name:   Username snapshot stored as admin_username.
        detail:       Dict with before/after fields or relevant payload.
        ip_address:   Client IP.
        user_agent:   Ignored (column removed from schema).
    """
    # Merge entity_label into detail so it's not lost
    if entity_label:
        detail = dict(detail or {})
        detail.setdefault('label', entity_label)

    resolved_action = _resolve_action(action)

    try:
        engine = _get_engine()
        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                    INSERT INTO admin_audit_log
                        (admin_id, admin_username, action, resource_type,
                         resource_id, detail, ip_address)
                    VALUES
                        (:admin_id, :admin_username, :action, :resource_type,
                         :resource_id, :detail, :ip)
                """),
                {
                    "admin_id":      admin_id,
                    "admin_username": (admin_name or "system")[:50],
                    "action":        resolved_action,
                    "resource_type": entity_type[:50],
                    "resource_id":   str(entity_id)[:100] if entity_id is not None else None,
                    "detail":        json.dumps(detail) if detail else None,
                    "ip":            (ip_address or "")[:45] or None,
                },
            )
    except Exception as exc:
        logger.warning(f"[AuditLog] Failed to write log entry (action={action}): {exc}")


def extract_admin_context(request, payload: dict) -> dict:
    """
    Pull admin identity + request metadata from a FastAPI Request and
    decoded JWT payload.  Returns a dict ready to splat into log_action().

    Usage:
        ctx = extract_admin_context(request, payload)
        log_action('lesson.create', 'lesson', new_id, title, **ctx)
    """
    ip = None
    ua = None
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        ip = forwarded.split(",")[0].strip() if forwarded else str(request.client.host) if request.client else None
        ua = request.headers.get("user-agent")
    return {
        "admin_id":   payload.get("sub"),
        "admin_name": payload.get("user") or payload.get("sub", "admin"),
        "ip_address": ip,
    }
