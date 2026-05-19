"""
SocialProof — Router: User Mindmap API

Allows authenticated users to build their own personal mindmaps.
Each user's map is stored under map_id = "user_{user_id}".

User endpoints (auth required):
  POST   /api/user/mindmap/nodes           — create a node in the user's map
  DELETE /api/user/mindmap/nodes/{node_id} — delete a node (+ its children's edges)
  POST   /api/user/mindmap/edges           — create an edge between user's nodes
  DELETE /api/user/mindmap/edges/{edge_id} — delete an edge
"""

import re
import uuid
from typing import List, Optional

import sqlalchemy as sa
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import logger
from database.models import (
    MindmapEdgeORM,
    MindmapNodeORM,
    engine,
)
from routers.auth import get_current_user

router = APIRouter()


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_user_id(request: Request, authorization: str = None) -> int:
    payload = get_current_user(request, authorization)
    return int(payload["sub"])


def _user_map_id(user_id: int) -> str:
    return f"user_{user_id}"


def _node_to_dict(n, revealed_by: list = None) -> dict:
    return {
        "id":           n.id,
        "map_id":       n.map_id,
        "type":         n.type,
        "icon":         n.icon,
        "label":        n.label,
        "sub":          n.sub,
        "content":      getattr(n, "content", None),
        "color":        n.color,
        "textColor":    getattr(n, "text_color", None) or getattr(n, "textColor", None) or "#e8eaf0",
        "shape":        getattr(n, "shape", "rounded") or "rounded",
        "image_url":    getattr(n, "image_url", None),
        "x":            n.x,
        "y":            n.y,
        "startVisible": bool(n.start_visible),
        "sort_order":   n.sort_order,
        "active":       bool(n.active),
        "revealedBy":   revealed_by or [],
    }


def _assert_node_owned(db, node_id: str, map_id: str):
    """Raise 404 if node doesn't exist in this user's map."""
    node = db.query(MindmapNodeORM).filter_by(id=node_id, map_id=map_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found in your map.")
    return node


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class UserNodeCreateBody(BaseModel):
    map_id:        str                  # must equal "user_{user_id}" — validated server-side
    label:         str
    icon:          str = "📌"
    sub:           Optional[str] = None
    color:         str = "#4488ff"
    shape:         str = "rounded"
    image_url:     Optional[str] = None
    type:          str = "leaf"         # "cat" for parent, "leaf" for child
    x:             int = 1800
    y:             int = 1500
    start_visible: bool = True
    active:        bool = True


class UserNodeUpdateBody(BaseModel):
    map_id:     str
    label:      Optional[str] = None
    icon:       Optional[str] = None
    sub:        Optional[str] = None
    content:    Optional[str] = None
    color:      Optional[str] = None
    textColor:  Optional[str] = None
    shape:      Optional[str] = None
    image_url:  Optional[str] = None
    x:          Optional[int] = None
    y:          Optional[int] = None


class UserEdgeCreateBody(BaseModel):
    map_id:  str
    from_id: str
    to_id:   str


# ══════════════════════════════════════════════════════════════════════════════
#  CREATE NODE
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/user/mindmap/nodes", status_code=201)
async def user_create_node(
    body: UserNodeCreateBody,
    request: Request,
    authorization: str = Header(None),
):
    user_id   = _get_user_id(request, authorization)
    owned_map = _user_map_id(user_id)

    # Security: user may only write to their own map
    if body.map_id != owned_map:
        raise HTTPException(status_code=403, detail="You can only create nodes in your own map.")

    # Validate type
    if body.type not in ("cat", "leaf"):
        raise HTTPException(status_code=422, detail="type must be 'cat' or 'leaf'.")

    # Auto-generate a stable node ID from label
    slug    = re.sub(r'[^a-z0-9]+', '_', body.label.lower()).strip('_')[:40]
    node_id = f"{slug}_{uuid.uuid4().hex[:6]}"

    db = Session(engine)
    try:
        node = MindmapNodeORM(
            id            = node_id,
            map_id        = owned_map,
            type          = body.type,
            icon          = body.icon[:8],          # guard against huge emoji strings
            label         = body.label[:120],
            sub           = (body.sub or "")[:120] or None,
            color         = body.color[:10],
            x             = max(0, min(body.x, 9999)),
            y             = max(0, min(body.y, 9999)),
            start_visible = True,                   # user nodes always visible
            sort_order    = 0,
            active        = True,
        )
        # Set optional fields if the ORM model supports them
        if hasattr(node, "shape"):
            node.shape = body.shape or "rounded"
        if hasattr(node, "image_url"):
            node.image_url = body.image_url or None
        db.add(node)
        db.commit()
        db.refresh(node)
        return _node_to_dict(node)
    except Exception as e:
        db.rollback()
        logger.error(f"[UserMindmap] create node error user={user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create node.")
    finally:
        db.close()



# ══════════════════════════════════════════════════════════════════════════════
#  UPDATE NODE  (label, icon, sub, color, shape, image_url)
# ══════════════════════════════════════════════════════════════════════════════

@router.put("/api/user/mindmap/nodes/{node_id}", status_code=200)
async def user_update_node(
    node_id: str,
    body: UserNodeUpdateBody,
    request: Request,
    authorization: str = Header(None),
):
    user_id   = _get_user_id(request, authorization)
    owned_map = _user_map_id(user_id)

    if body.map_id != owned_map:
        raise HTTPException(status_code=403, detail="Cannot modify another user's map.")

    db = Session(engine)
    try:
        node = _assert_node_owned(db, node_id, owned_map)

        if body.label is not None:
            node.label = body.label[:120]
        if body.icon is not None:
            node.icon = body.icon[:8]
        if body.sub is not None:
            node.sub = body.sub[:300] or None
        if body.color is not None:
            node.color = body.color[:10]
        if body.textColor is not None and hasattr(node, "text_color"):
            node.text_color = body.textColor[:10]
        if body.content is not None and hasattr(node, "content"):
            node.content = body.content or None
        if body.shape is not None and hasattr(node, "shape"):
            node.shape = body.shape
        if body.image_url is not None and hasattr(node, "image_url"):
            node.image_url = body.image_url or None
        if body.x is not None:
            node.x = max(0, min(body.x, 9999))
        if body.y is not None:
            node.y = max(0, min(body.y, 9999))

        db.commit()
        db.refresh(node)

        # Build revealedBy list from edges
        edges = db.execute(
            sa.text("SELECT from_id FROM mindmap_edges WHERE map_id=:mid AND to_id=:nid"),
            {"mid": owned_map, "nid": node_id},
        ).fetchall()
        revealed_by = [row.from_id for row in edges]

        return _node_to_dict(node, revealed_by)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[UserMindmap] update node error user={user_id} node={node_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update node.")
    finally:
        db.close()

# ══════════════════════════════════════════════════════════════════════════════
#  DELETE NODE  (cascade: removes all edges + children edges too)
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/api/user/mindmap/nodes/{node_id}", status_code=200)
async def user_delete_node(
    node_id: str,
    request: Request,
    map: str = Query(...),
    authorization: str = Header(None),
):
    user_id   = _get_user_id(request, authorization)
    owned_map = _user_map_id(user_id)

    if map != owned_map:
        raise HTTPException(status_code=403, detail="Cannot modify another user's map.")

    db = Session(engine)
    try:
        node = _assert_node_owned(db, node_id, owned_map)

        # Collect all descendants to delete (BFS)
        to_delete = [node_id]
        queue     = [node_id]
        while queue:
            current = queue.pop()
            children = db.execute(
                sa.text("SELECT to_id FROM mindmap_edges WHERE map_id=:mid AND from_id=:fid"),
                {"mid": owned_map, "fid": current},
            ).fetchall()
            for row in children:
                if row.to_id not in to_delete:
                    to_delete.append(row.to_id)
                    queue.append(row.to_id)

        # Delete edges for all nodes in the subtree
        for nid in to_delete:
            db.execute(
                sa.text("DELETE FROM mindmap_edges WHERE map_id=:mid AND (from_id=:nid OR to_id=:nid)"),
                {"mid": owned_map, "nid": nid},
            )

        # Delete all nodes in the subtree
        for nid in to_delete:
            n = db.query(MindmapNodeORM).filter_by(id=nid, map_id=owned_map).first()
            if n:
                db.delete(n)

        db.commit()
        return {"ok": True, "deleted": to_delete}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[UserMindmap] delete node error user={user_id} node={node_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete node.")
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  CREATE EDGE
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/api/user/mindmap/edges", status_code=201)
async def user_create_edge(
    body: UserEdgeCreateBody,
    request: Request,
    authorization: str = Header(None),
):
    user_id   = _get_user_id(request, authorization)
    owned_map = _user_map_id(user_id)

    if body.map_id != owned_map:
        raise HTTPException(status_code=403, detail="Cannot modify another user's map.")

    db = Session(engine)
    try:
        # Both nodes must belong to the user's map
        _assert_node_owned(db, body.from_id, owned_map)
        _assert_node_owned(db, body.to_id,   owned_map)

        # Prevent self-loop
        if body.from_id == body.to_id:
            raise HTTPException(status_code=422, detail="Cannot connect a node to itself.")

        # INSERT IGNORE so duplicate edges succeed silently (idempotent)
        db.execute(
            sa.text(
                "INSERT IGNORE INTO mindmap_edges (map_id, from_id, to_id) "
                "VALUES (:mid, :fid, :tid)"
            ),
            {"mid": owned_map, "fid": body.from_id, "tid": body.to_id},
        )
        db.commit()
        row = db.execute(
            sa.text(
                "SELECT id FROM mindmap_edges WHERE map_id=:mid AND from_id=:fid AND to_id=:tid"
            ),
            {"mid": owned_map, "fid": body.from_id, "tid": body.to_id},
        ).fetchone()
        return {"id": row.id if row else 0, "from": body.from_id, "to": body.to_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[UserMindmap] create edge error user={user_id}: {e}")
        raise HTTPException(status_code=409, detail="Edge already exists or DB error.")
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  DELETE EDGE
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/api/user/mindmap/edges/{edge_id}", status_code=200)
async def user_delete_edge(
    edge_id: int,
    request: Request,
    map: str = Query(...),
    authorization: str = Header(None),
):
    user_id   = _get_user_id(request, authorization)
    owned_map = _user_map_id(user_id)

    if map != owned_map:
        raise HTTPException(status_code=403, detail="Cannot modify another user's map.")

    db = Session(engine)
    try:
        # Verify the edge belongs to this user's map before deleting
        row = db.execute(
            sa.text("SELECT id FROM mindmap_edges WHERE id=:eid AND map_id=:mid"),
            {"eid": edge_id, "mid": owned_map},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Edge not found in your map.")

        db.execute(sa.text("DELETE FROM mindmap_edges WHERE id=:eid"), {"eid": edge_id})
        db.commit()
        return {"ok": True, "deleted": edge_id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  DELETE ALL NODES (wipes entire user map)
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/api/user/mindmap/all", status_code=200)
async def user_delete_all(
    request: Request,
    map: str = Query(...),
    authorization: str = Header(None),
):
    user_id   = _get_user_id(request, authorization)
    owned_map = _user_map_id(user_id)

    if map != owned_map:
        raise HTTPException(status_code=403, detail="Cannot modify another user's map.")

    db = Session(engine)
    try:
        db.execute(sa.text("DELETE FROM mindmap_edges WHERE map_id=:mid"), {"mid": owned_map})
        db.execute(sa.text("DELETE FROM mindmap_nodes WHERE map_id=:mid"), {"mid": owned_map})
        db.commit()
        return {"ok": True, "map_id": owned_map}
    except Exception as e:
        db.rollback()
        logger.error(f"[UserMindmap] delete all error user={user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete all nodes.")
    finally:
        db.close()
