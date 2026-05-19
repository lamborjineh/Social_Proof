/* ══════════════════════════════════════════════════════════════
   mindmap.js  —  SocialProof  (User-Authored v5)
   · FIX: Images stored separately → no more localStorage overflow
   · FIX: Positions saved separately → survive after images added
   · FIX: Box color & text color now independent
   · FIX: Article/content field added to nodes
   · All v4 features preserved (edge styles, arrows, line types)
══════════════════════════════════════════════════════════════ */

const API_BASE = '';

(function roleGate() {
  const role = localStorage.getItem('sp_role');
  if (role === 'admin') window.location.href = 'dashboard.html';
})();

function isGuest() { return !localStorage.getItem('sp_user_id'); }

/* ── Guest storage key privacy fix ──────────────────────────────────────────
   We namespace guest map data under a random token so that a subsequent
   visitor on the same shared device cannot read the previous guest's map.
   The token is stored in localStorage (so it survives F5 refresh) but is
   DELETED on logout, which is the actual privacy boundary we need.

   Why not sessionStorage?  sessionStorage survives F5 in the same tab but
   is wiped when the tab is closed or duplicated — causing map data to
   disappear on any reload in a new tab, which is the bug this fixes.

   SCOPE: only the five GUEST_* mindmap keys below are namespaced.
   All other sp_* keys (sp_role, sp_user_id, sp_read_lessons, sp_age_mode,
   sp_pretest_done, sp_session …) are unaffected — they are shared across
   pages as designed and cleared by localStorage.clear() on logout.       ── */
(function ensureGuestToken() {
  if (!localStorage.getItem('sp_guest_token')) {
    localStorage.setItem('sp_guest_token',
      'g_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 9));
  }
})();
function _guestToken() { return localStorage.getItem('sp_guest_token') || 'g_default'; }

const GUEST_STORAGE_KEY       = () => `sp_guest_mindmap:${_guestToken()}`;
const GUEST_EDGES_STORAGE_KEY = () => `sp_guest_mindmap_edges:${_guestToken()}`;
const GUEST_POSITIONS_KEY     = () => `sp_guest_positions:${_guestToken()}`;
const GUEST_IMG_PREFIX        = () => `sp_guest_img_${_guestToken()}_`;
const GUEST_VIEWPORT_KEY      = () => `sp_guest_viewport:${_guestToken()}`;

function guestSaveViewport() {
  try { localStorage.setItem(GUEST_VIEWPORT_KEY(), JSON.stringify({ panX: state.panX, panY: state.panY, scale: state.scale })); } catch {}
}
function guestLoadViewport() {
  try { const r = localStorage.getItem(GUEST_VIEWPORT_KEY()); return r ? JSON.parse(r) : null; } catch { return null; }
}

/* ── Guest image storage (separate keys to avoid overflow) ── */
function guestStoreImage(nodeId, dataUrl) {
  try { localStorage.setItem(GUEST_IMG_PREFIX() + nodeId, dataUrl); return true; } catch { return false; }
}
function guestLoadImage(nodeId) {
  return localStorage.getItem(GUEST_IMG_PREFIX() + nodeId) || null;
}
function guestClearImage(nodeId) {
  localStorage.removeItem(GUEST_IMG_PREFIX() + nodeId);
}

/* ── Guest position storage (separate key so positions survive image saves) ── */
function guestSavePositions() {
  const pos = {};
  NODES.forEach(n => { pos[n.id] = { x: Math.round(n.x||0), y: Math.round(n.y||0) }; });
  try { localStorage.setItem(GUEST_POSITIONS_KEY(), JSON.stringify(pos)); } catch {}
}
function guestLoadPositions() {
  try { const r = localStorage.getItem(GUEST_POSITIONS_KEY()); return r ? JSON.parse(r) : {}; } catch { return {}; }
}

/* ── Guest node save/load (strips local image data to avoid overflow) ── */
function guestSaveNodes() {
  try {
    const stripped = NODES.map(n => {
      if (n.image_url && n.image_url.startsWith('data:')) {
        guestStoreImage(n.id, n.image_url);
        return { ...n, image_url: '__local__' };
      }
      return n;
    });
    localStorage.setItem(GUEST_STORAGE_KEY(), JSON.stringify(stripped));
  } catch (e) {
    // Fallback: save without any images at all
    try {
      const noImg = NODES.map(n => ({ ...n, image_url: n.image_url?.startsWith('data:') ? '__local__' : n.image_url }));
      localStorage.setItem(GUEST_STORAGE_KEY(), JSON.stringify(noImg));
    } catch {}
  }
}
function guestLoadNodes() {
  try {
    const r = localStorage.getItem(GUEST_STORAGE_KEY());
    if (!r) return [];
    const nodes = JSON.parse(r);
    // Positions key is the authoritative source for x/y (updated on every drag).
    // Node data is fallback in case positions key is missing an entry.
    const positions = guestLoadPositions();
    return nodes.map(n => {
      const pos = positions[n.id];
      const img = n.image_url === '__local__' ? guestLoadImage(n.id) : n.image_url;
      // Always prefer saved position; fall back to node's stored x/y
      const x = (pos && pos.x != null) ? pos.x : (n.x || 1800);
      const y = (pos && pos.y != null) ? pos.y : (n.y || 1500);
      return { ...n, image_url: img || null, x, y };
    });
  } catch { return []; }
}
function guestLoadEdges() {
  try { const r = localStorage.getItem(GUEST_EDGES_STORAGE_KEY()); return r ? JSON.parse(r) : []; } catch { return []; }
}
function guestSaveEdges() {
  try { localStorage.setItem(GUEST_EDGES_STORAGE_KEY(), JSON.stringify(EDGES)); } catch {}
}

let NODES = [];
let EDGES = [];

const state = {
  discovered: new Set(), activeNode: null, pendingProgress: new Set(),
  panX: 0, panY: 0, scale: 1,
  isPanning: false, panStartX: 0, panStartY: 0, panStartTX: 0, panStartTY: 0,
};

const mapRoot = document.getElementById('map-root');
const svgEl   = document.getElementById('connections');
const nodeEls = {};
const edgeEls = {};

function getUserMapId() {
  const userId = localStorage.getItem('sp_user_id');
  return userId ? `user_${userId}` : null;
}

const EDGE_STYLES = [
  { val: 'solid',  label: 'Solid'    },
  { val: 'dashed', label: 'Dashed'   },
  { val: 'dotted', label: 'Dotted'   },
  { val: 'double', label: 'Double'   },
  { val: 'strong', label: 'Strong'   },
];
const EDGE_ARROWS = [
  { val: 'forward',  label: '→ Forward' },
  { val: 'backward', label: '← Backward' },
  { val: 'both',     label: '↔ Both' },
  { val: 'none',     label: '— None' },
];
const EDGE_LINE_TYPES = [
  { val: 'curved',   label: '~ Curved' },
  { val: 'straight', label: '— Straight' },
  { val: 'step',     label: '⌐ Step' },
];

function edgeDefault() {
  return { label: '', style: 'solid', arrow: 'forward', lineType: 'curved' };
}

function ensureSVGDefs() {
  if (svgEl.querySelector('defs')) return;
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  svgEl.insertBefore(defs, svgEl.firstChild);
}

function getOrCreateMarker(color, direction) {
  const defs = svgEl.querySelector('defs'); if (!defs) return 'none';
  const safeColor = color.replace('#', '');
  const id = `arrow-${direction}-${safeColor}`;
  if (defs.querySelector(`#${id}`)) return `url(#${id})`;
  const m = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
  m.setAttribute('id', id); m.setAttribute('markerWidth', '8'); m.setAttribute('markerHeight', '8');
  m.setAttribute('refX', direction === 'fwd' ? '6' : '2'); m.setAttribute('refY', '3');
  m.setAttribute('orient', 'auto'); m.setAttribute('markerUnits', 'strokeWidth');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  if (direction === 'fwd') p.setAttribute('d', 'M0,0 L0,6 L8,3 z');
  else                     p.setAttribute('d', 'M8,0 L8,6 L0,3 z');
  p.setAttribute('fill', color); m.appendChild(p); defs.appendChild(m);
  return `url(#${id})`;
}

// Return {w, h} half-dimensions of a node element for border intersection
function nodeHalfDims(n) {
  const el = nodeEls[n.id];
  if (el) {
    const body = el.querySelector('.node-body') || el;
    const w = body.offsetWidth || el.offsetWidth;
    const h = body.offsetHeight || el.offsetHeight;
    if (w > 0 && h > 0) return { w: w / 2, h: h / 2 };
  }
  // Fallback before DOM is ready
  const shape = (n.shape && n.shape !== 'null') ? n.shape : 'rounded';
  if (shape === 'circle') return { w: 50, h: 50 };
  return { w: 68, h: 32 };
}

// Find the point on the border of a node (at cx,cy) along direction (dx,dy)
function nodeBorderPoint(n, dx, dy) {
  const { w, h } = nodeHalfDims(n);
  if (Math.abs(dx) < 0.0001 && Math.abs(dy) < 0.0001) return { x: n.x, y: n.y };
  // Clamp to rectangle border: find t where ray exits the box
  const shape = (n.shape && n.shape !== 'null') ? n.shape : 'rounded';
  if (shape === 'circle') {
    // For circles use radius along direction
    const r = Math.max(w, h);
    const len = Math.sqrt(dx*dx + dy*dy);
    return { x: n.x + (dx/len)*r, y: n.y + (dy/len)*r };
  }
  // Rectangle/rounded/diamond/hex: use axis-aligned bounding box intersection
  const tx = dx !== 0 ? w / Math.abs(dx) : Infinity;
  const ty = dy !== 0 ? h / Math.abs(dy) : Infinity;
  const t = Math.min(tx, ty);
  return { x: n.x + dx * t, y: n.y + dy * t };
}

// Legacy alias used by edgeMidpoint
function nodeRadius(n) {
  const { w, h } = nodeHalfDims(n);
  return Math.max(w, h);
}

function calcEdgePath(src, tgt, lineType) {
  const dx0 = tgt.x - src.x, dy0 = tgt.y - src.y;
  const dist = Math.sqrt(dx0*dx0 + dy0*dy0) || 1;
  const ux = dx0/dist, uy = dy0/dist;
  // Attach to actual node border
  const p1 = nodeBorderPoint(src,  ux,  uy);
  const p2 = nodeBorderPoint(tgt, -ux, -uy);
  const x1 = p1.x, y1 = p1.y, x2 = p2.x, y2 = p2.y;
  if (lineType === 'straight') return `M${x1},${y1} L${x2},${y2}`;
  if (lineType === 'step') {
    const mx = (x1 + x2) / 2;
    return `M${x1},${y1} L${mx},${y1} L${mx},${y2} L${x2},${y2}`;
  }
  const cx = (x1+x2)/2, cy = (y1+y2)/2;
  const ddx = x2-x1, ddy = y2-y1;
  const len = Math.sqrt(ddx*ddx+ddy*ddy)||1;
  const offset = Math.min(len*0.22, 80);
  const nx = -ddy/len, ny = ddx/len;
  return `M${x1},${y1} Q${cx+nx*offset},${cy+ny*offset} ${x2},${y2}`;
}

function edgeMidpoint(src, tgt, lineType) {
  const dx0 = tgt.x-src.x, dy0 = tgt.y-src.y;
  const dist = Math.sqrt(dx0*dx0+dy0*dy0)||1;
  const ux = dx0/dist, uy = dy0/dist;
  const x1 = src.x+ux*nodeRadius(src), y1 = src.y+uy*nodeRadius(src);
  const x2 = tgt.x-ux*nodeRadius(tgt), y2 = tgt.y-uy*nodeRadius(tgt);
  if (lineType === 'step') return { x: (x1+x2)/2, y: (y1+y2)/2 };
  if (lineType === 'straight') return { x: (x1+x2)/2, y: (y1+y2)/2 };
  const cx = (x1+x2)/2, cy = (y1+y2)/2;
  const ddx = x2-x1, ddy = y2-y1;
  const len = Math.sqrt(ddx*ddx+ddy*ddy)||1;
  const offset = Math.min(len*0.25, 80);
  const nx = -ddy/len, ny = ddx/len;
  const cpx = cx+nx*offset, cpy = cy+ny*offset;
  return { x: 0.25*x1+0.5*cpx+0.25*x2, y: 0.25*y1+0.5*cpy+0.25*y2 };
}

function getEdgeColor(edge) {
  const tgt = NODES.find(n => n.id === edge.toId);
  return tgt?.color || '#4488ff';
}

function buildEdgeSVG(edge) {
  const key = edge.id;
  if (edgeEls[key]) return;
  const src = NODES.find(n => n.id === edge.fromId);
  const tgt = NODES.find(n => n.id === edge.toId);
  if (!src || !tgt) return;
  const grp = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  grp.setAttribute('class', 'edge-group visible');
  grp.setAttribute('id', `edge-grp-${key}`);
  grp.style.cursor = 'pointer';
  const hitPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  hitPath.setAttribute('fill', 'none'); hitPath.setAttribute('stroke', 'transparent'); hitPath.setAttribute('stroke-width', '14');
  const path2 = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path2.setAttribute('fill', 'none'); path2.style.display = 'none';
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('fill', 'none'); path.setAttribute('class', 'edge visible');
  // Flow / traveling-dot path
  const flowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  flowPath.setAttribute('class', 'edge-flow');
  const labelBg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  labelBg.setAttribute('rx', '4'); labelBg.style.display = 'none';
  const textEl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  textEl.setAttribute('text-anchor', 'middle'); textEl.setAttribute('dominant-baseline', 'middle');
  textEl.setAttribute('font-family', 'DM Sans, sans-serif'); textEl.setAttribute('font-size', '10');
  textEl.setAttribute('pointer-events', 'none');
  grp.appendChild(hitPath); grp.appendChild(path2); grp.appendChild(path);
  grp.appendChild(flowPath);
  grp.appendChild(labelBg); grp.appendChild(textEl); svgEl.appendChild(grp);
  edgeEls[key] = { grp, path, path2, hitPath, textEl, labelBg, flowPath };
  grp.addEventListener('click', e => { e.stopPropagation(); openEdgeEditModal(edge.id); });
  updateEdgeSVG(edge);
  // Animate edge draw
  requestAnimationFrame(() => {
    const len = path.getTotalLength ? path.getTotalLength() : 400;
    path.style.setProperty('--edge-len', len);
    path.classList.add('mm-drawing');
    setTimeout(() => path.classList.remove('mm-drawing'), 600);
  });
}

function updateEdgeSVG(edge) {
  const els = edgeEls[edge.id]; if (!els) return;
  const src = NODES.find(n => n.id === edge.fromId);
  const tgt = NODES.find(n => n.id === edge.toId);
  if (!src || !tgt) return;
  const color    = getEdgeColor(edge);
  const style    = edge.style    || 'solid';
  const arrow    = edge.arrow    || 'forward';
  const lineType = edge.lineType || 'curved';
  const label    = edge.label    || '';
  const d = calcEdgePath(src, tgt, lineType);
  const { x: mx, y: my } = edgeMidpoint(src, tgt, lineType);
  let dasharray = 'none', strokeWidth = '1.8', opacity = 0.55;
  if (style === 'dashed') dasharray = '8 4';
  if (style === 'dotted') dasharray = '2 5';
  if (style === 'strong') { strokeWidth = '3.5'; opacity = 0.75; }
  els.path.setAttribute('d', d); els.path.setAttribute('stroke', color);
  els.path.setAttribute('stroke-width', strokeWidth); els.path.setAttribute('stroke-dasharray', dasharray);
  els.path.style.opacity = opacity; els.hitPath.setAttribute('d', d);
  if (style === 'double') {
    els.path2.style.display = ''; els.path2.setAttribute('d', d); els.path2.setAttribute('stroke', color);
    els.path2.setAttribute('stroke-width', '4.5'); els.path2.setAttribute('fill', 'none'); els.path2.style.opacity = '0.2';
    els.path.setAttribute('stroke-width', '1.5');
  } else { els.path2.style.display = 'none'; }
  const fwdMarker = getOrCreateMarker(color, 'fwd');
  const bwdMarker = getOrCreateMarker(color, 'bwd');
  els.path.setAttribute('marker-end',   (arrow === 'forward'  || arrow === 'both') ? fwdMarker : 'none');
  els.path.setAttribute('marker-start', (arrow === 'backward' || arrow === 'both') ? bwdMarker : 'none');

  // ── Flow path (traveling dot animation) ──────────────────────
  if (els.flowPath) {
    els.flowPath.setAttribute('d', d);
    els.flowPath.setAttribute('stroke', color);
    els.flowPath.className.baseVal = 'edge-flow' + (
      arrow === 'none'     ? ' flow-none' :
      arrow === 'backward' ? ' flow-bwd'  :
      arrow === 'both'     ? ' flow-both' : ''
    );
  }

  if (label) {
    const tw = label.length * 5.8 + 10, th = 14;
    els.labelBg.setAttribute('x', mx - tw/2); els.labelBg.setAttribute('y', my - th/2);
    els.labelBg.setAttribute('width', tw); els.labelBg.setAttribute('height', th);
    els.labelBg.setAttribute('fill', '#0d0f14'); els.labelBg.setAttribute('stroke', color);
    els.labelBg.setAttribute('stroke-width', '0.8'); els.labelBg.style.opacity = '0.9'; els.labelBg.style.display = '';
    els.textEl.textContent = label; els.textEl.setAttribute('x', mx); els.textEl.setAttribute('y', my);
    els.textEl.setAttribute('fill', color); els.textEl.style.display = '';
  } else { els.textEl.style.display = 'none'; els.labelBg.style.display = 'none'; }
}

function redrawEdgesForNode(nodeId) {
  EDGES.forEach(edge => { if (edge.fromId === nodeId || edge.toId === nodeId) updateEdgeSVG(edge); });
}

function removeEdgeSVG(edgeId) {
  const els = edgeEls[edgeId]; if (els) { els.grp.remove(); delete edgeEls[edgeId]; }
}

function makeEdgeId(fromId, toId) { return `${fromId}_${toId}`; }
function findEdge(fromId, toId) { return EDGES.find(e => e.fromId === fromId && e.toId === toId); }

function ensureEdge(fromId, toId) {
  let e = findEdge(fromId, toId);
  if (!e) { e = { id: makeEdgeId(fromId, toId), fromId, toId, ...edgeDefault() }; EDGES.push(e); if (isGuest()) guestSaveEdges(); }
  return e;
}

function updateEdgeData(eid, updates) {
  const e = EDGES.find(e => e.id === eid); if (!e) return;
  Object.assign(e, updates); if (isGuest()) guestSaveEdges(); updateEdgeSVG(e);
}

function deleteEdgeData(eid) {
  EDGES = EDGES.filter(e => e.id !== eid); removeEdgeSVG(eid); if (isGuest()) guestSaveEdges();
}

async function fetchUserGraph() {
  const mapId = getUserMapId(); if (!mapId) return null;
  try {
    const res = await fetch(`${API_BASE}/api/mindmap/graph?map=${mapId}`);
    if (!res.ok) throw new Error('API not ready');
    return await res.json();
  } catch { return null; }
}

async function apiCreateNode(body) {
  const mapId = getUserMapId(); if (!mapId) throw new Error('Not logged in');
  const res = await fetch(`${API_BASE}/api/user/mindmap/nodes`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, map_id: mapId }),
  });
  if (!res.ok) { const e = await res.json().catch(()=>({})); throw new Error(e.detail || 'Failed to create node'); }
  return res.json();
}

async function apiUpdateNode(nodeId, body) {
  const mapId = getUserMapId(); if (!mapId) throw new Error('Not logged in');
  const res = await fetch(`${API_BASE}/api/user/mindmap/nodes/${nodeId}`, {
    method: 'PUT', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, map_id: mapId }),
  });
  if (!res.ok) throw new Error('Failed to update node');
  return res.json();
}

async function apiCreateEdge(fromId, toId) {
  const mapId = getUserMapId(); if (!mapId) throw new Error('Not logged in');
  const res = await fetch(`${API_BASE}/api/user/mindmap/edges`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ map_id: mapId, from_id: fromId, to_id: toId }),
  });
  // 409 = duplicate edge (already exists) — treat as success
  if (!res.ok && res.status !== 409) throw new Error('Failed to create edge');
  return res.json().catch(() => ({ from: fromId, to: toId }));
}

async function apiDeleteAllNodes() {
  const mapId = getUserMapId(); if (!mapId) throw new Error('Not logged in');
  const res = await fetch(`${API_BASE}/api/user/mindmap/all?map=${mapId}`, {
    method: 'DELETE', credentials: 'include',
  });
  if (!res.ok) throw new Error('Failed to delete all nodes');
  return res.json();
}

async function apiDeleteNode(nodeId) {
  const mapId = getUserMapId();
  const res = await fetch(`${API_BASE}/api/user/mindmap/nodes/${nodeId}?map=${mapId}`, {
    method: 'DELETE', credentials: 'include',
  });
  if (!res.ok) throw new Error('Failed to delete node');
  return res.json();
}

function flushProgress() {
  if (!state.pendingProgress.size) return;
  const mapId = getUserMapId(); if (!mapId) { state.pendingProgress.clear(); return; }
  const ids = [...state.pendingProgress]; state.pendingProgress.clear();
  fetch(`${API_BASE}/api/mindmap/progress`, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ map_id: mapId, node_ids: ids }),
  }).catch(()=>{});
}

window.addEventListener('pagehide', flushProgress);
window.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushProgress(); });

function guestNodeId() { return 'g_' + Date.now() + '_' + Math.random().toString(36).slice(2,7); }

function guestCreateNode(body) {
  const node = { ...body, id: guestNodeId(), revealedBy: body.revealedBy || [] };
  NODES.push(node); guestSaveNodes(); guestSavePositions(); return node;
}
function guestUpdateNode(nodeId, body) {
  const idx = NODES.findIndex(n => n.id === nodeId);
  if (idx !== -1) { NODES[idx] = { ...NODES[idx], ...body }; guestSaveNodes(); guestSavePositions(); return NODES[idx]; }
  throw new Error('Node not found');
}
function guestDeleteNode(nodeId) {
  guestClearImage(nodeId);
  NODES = NODES.filter(n => n.id !== nodeId);
  guestSaveNodes(); guestSavePositions();
}
function guestCreateEdge(fromId, toId) {
  const node = NODES.find(n => n.id === toId);
  if (node && !node.revealedBy.includes(fromId)) { node.revealedBy.push(fromId); guestSaveNodes(); }
}

async function doCreateNode(body) {
  if (isGuest()) return guestCreateNode(body); return apiCreateNode(body);
}
async function doUpdateNode(nodeId, body) {
  if (isGuest()) return guestUpdateNode(nodeId, body); return apiUpdateNode(nodeId, body);
}
async function doCreateEdge(fromId, toId) {
  if (isGuest()) { guestCreateEdge(fromId, toId); return; } return apiCreateEdge(fromId, toId);
}
async function doDeleteNode(nodeId) {
  if (isGuest()) { guestDeleteNode(nodeId); return; } return apiDeleteNode(nodeId);
}

/* ── Undo / Redo ─────────────────────────────────────────────── */
const MM_HISTORY = [];
const MM_FUTURE  = [];
const MM_MAX_HISTORY = 50;

function mmSnapshot() {
  return { nodes: JSON.parse(JSON.stringify(NODES)), edges: JSON.parse(JSON.stringify(EDGES)) };
}

function mmPushHistory() {
  MM_HISTORY.push(mmSnapshot());
  if (MM_HISTORY.length > MM_MAX_HISTORY) MM_HISTORY.shift();
  MM_FUTURE.length = 0;
  mmUpdateUndoRedoBtns();
}

function mmUpdateUndoRedoBtns() {
  const u = document.getElementById('undo-btn'), r = document.getElementById('redo-btn');
  if (u) u.disabled = MM_HISTORY.length === 0;
  if (r) r.disabled = MM_FUTURE.length === 0;
}

function mmRestoreSnapshot(snap) {
  NODES = JSON.parse(JSON.stringify(snap.nodes));
  EDGES = JSON.parse(JSON.stringify(snap.edges));
  Object.keys(nodeEls).forEach(id => { if (nodeEls[id]) nodeEls[id].remove(); delete nodeEls[id]; });
  document.querySelectorAll('.edge-svg, .edge-group').forEach(el => el.remove());
  Object.keys(edgeEls).forEach(k => delete edgeEls[k]);
  ensureSVGDefs();
  NODES.forEach(n => buildNode(n, false));
  EDGES.forEach(e => buildEdgeSVG(e));
  if (isGuest()) { guestSaveNodes(); guestSaveEdges(); guestSavePositions(); }
  closePanel(); updateProgress(); renderTree();
}

function mmUndo() {
  if (MM_HISTORY.length === 0) return;
  MM_FUTURE.push(mmSnapshot());
  mmRestoreSnapshot(MM_HISTORY.pop());
  mmUpdateUndoRedoBtns();
  showToast('↩ Undo');
}

function mmRedo() {
  if (MM_FUTURE.length === 0) return;
  MM_HISTORY.push(mmSnapshot());
  mmRestoreSnapshot(MM_FUTURE.pop());
  mmUpdateUndoRedoBtns();
  showToast('↪ Redo');
}

document.addEventListener('keydown', e => {
  const tag = document.activeElement?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'z') { e.preventDefault(); mmUndo(); }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.shiftKey && e.key === 'z'))) { e.preventDefault(); mmRedo(); }
});

/* ── Color palettes ── */
const COLORS = [
  { label:'Blue',   val:'#4488ff' }, { label:'Red',    val:'#ff3b3b' },
  { label:'Violet', val:'#9b6eff' }, { label:'Teal',   val:'#38d4d4' },
  { label:'Orange', val:'#ff7a30' }, { label:'Yellow', val:'#f5b731' },
  { label:'Green',  val:'#2fd469' }, { label:'Pink',   val:'#e857c0' },
  { label:'White',  val:'#e8eaf0' }, { label:'Slate',  val:'#8b95a8' },
];
const TEXT_COLORS = [
  { label:'Light',  val:'#e8eaf0' }, { label:'Gray',   val:'#b0b8c8' },
  { label:'Blue',   val:'#4488ff' }, { label:'Violet', val:'#9b6eff' },
  { label:'Teal',   val:'#38d4d4' }, { label:'Orange', val:'#ff7a30' },
  { label:'Yellow', val:'#f5b731' }, { label:'Green',  val:'#2fd469' },
  { label:'Pink',   val:'#e857c0' }, { label:'Dark',   val:'#1c1f2e' },
];
const ICONS = ['📌','💡','🧠','🔍','⚠️','✅','❌','🌐','📊','🎯','🔑','💬','📢','🤔','⚡','🛡️','🔗','📝','🏷️','📰','🎭','🔬','💰','🌍','📱','🎓','🔒','🏆','⭐','🚀'];
const SHAPES = [
  { val:'rounded', label:'Rounded',   css:'border-radius:18px;' },
  { val:'rect',    label:'Rectangle', css:'border-radius:6px;' },
  { val:'pill',    label:'Pill',      css:'border-radius:50px;' },
  { val:'circle',  label:'Circle',    css:'border-radius:50%;' },
  { val:'diamond', label:'Diamond',   css:'clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%);border-radius:0;' },
  { val:'hex',     label:'Hexagon',   css:'clip-path:polygon(25% 0%,75% 0%,100% 50%,75% 100%,25% 100%,0% 50%);border-radius:0;' },
];

function getShapeCSS(shape) { const s = shape && shape !== 'null' ? shape : 'rounded'; return SHAPES.find(sh=>sh.val===s)?.css||'border-radius:18px;'; }
function hexAlpha(hex, a) {
  const r=parseInt(hex.slice(1,3),16), g=parseInt(hex.slice(3,5),16), b=parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${a})`;
}
function getShapeSizeCSS(shape) {
  switch(shape){
    case 'circle':  return 'width:100px;height:100px;min-width:100px;max-width:100px;padding:10px;';
    case 'diamond': return 'width:120px;height:120px;min-width:120px;max-width:120px;padding:20px 12px;';
    case 'hex':     return 'width:130px;height:104px;min-width:130px;max-width:130px;padding:14px 10px;';
    default:        return '';
  }
}

function buildNode(n, animate=false) {
  const el = document.createElement('div');
  const isParent = !n.revealedBy?.length;
  el.className = `node node-${n.type||(isParent?'cat':'leaf')} discovered`;
  el.id = `node-${n.id}`;
  el.style.left = n.x+'px'; el.style.top = n.y+'px';

  if (animate) {
    el.classList.add(isParent ? 'mm-entering' : 'mm-entering-child');
    setTimeout(() => { el.classList.remove('mm-entering','mm-entering-child'); }, 500);
  }

  const color     = n.color     || '#4488ff';
  const textColor = n.textColor || '#e8eaf0';
  const shape     = n.shape && n.shape !== 'null' ? n.shape : 'rounded';
  const isClip = shape === 'diamond' || shape === 'hex';

  // DOM API build — no innerHTML, no template strings for images.
  // Reason: the inline <style> in mindmap.html has a bare .node-body rule
  // (no body.mindmap-page prefix) that sets min-width/max-width/padding and
  // fights every inline style string we set via innerHTML.  Building the element
  // offline with .style properties before appending to the DOM guarantees our
  // values win regardless of what the stylesheet does.
  const body = document.createElement('div');
  body.className = 'node-body';
  body.dataset.shape = shape;
  body.style.background     = hexAlpha(color, 0.09);
  body.style.borderColor    = hexAlpha(color, 0.55);
  body.style.borderWidth    = isParent ? '2px'   : '1.5px';
  body.style.borderStyle    = isParent ? 'solid' : 'dashed';
  body.style.boxShadow      = `0 0 22px ${hexAlpha(color, 0.22)}`;
  body.style.display        = 'flex';
  body.style.flexDirection  = 'column';
  body.style.alignItems     = 'center';
  body.style.justifyContent = 'center';
  body.style.textAlign      = 'center';
  body.style.position       = 'relative';
  body.style.gap            = '4px';
  body.style.boxSizing      = 'border-box';
  body.style.transition     = 'transform .25s cubic-bezier(.34,1.56,.64,1), box-shadow .3s, border-color .3s';
  body.style.clipPath        = 'none';
  body.style.backdropFilter  = 'blur(8px)';
  body.style.overflow        = 'hidden';

  switch (shape) {
    case 'rect':
      body.style.borderRadius = '6px';
      body.style.width = 'auto';     body.style.height = 'auto';
      body.style.minWidth = '110px'; body.style.maxWidth = '170px';
      body.style.padding = '12px 14px';
      break;
    case 'pill':
      body.style.borderRadius = '999px';
      body.style.width = 'auto';     body.style.height = 'auto';
      body.style.minWidth = '110px'; body.style.maxWidth = '170px';
      body.style.padding = '10px 18px';
      break;
    case 'circle':
      body.style.borderRadius = '50%';
      body.style.width = '100px';    body.style.height = '100px';
      body.style.minWidth = '100px'; body.style.maxWidth = '100px';
      body.style.padding = '10px';
      break;
    case 'diamond':
      body.style.clipPath     = 'polygon(50% 0%,100% 50%,50% 100%,0% 50%)';
      body.style.borderRadius = '0';
      body.style.width = '120px';    body.style.height = '120px';
      body.style.minWidth = '120px'; body.style.maxWidth = '120px';
      body.style.padding = '20px 10px';
      body.style.backdropFilter = 'none';
      body.style.overflow = 'visible';
      break;
    case 'hex':
      body.style.clipPath     = 'polygon(25% 0%,75% 0%,100% 50%,75% 100%,25% 100%,0% 50%)';
      body.style.borderRadius = '0';
      body.style.width = '130px';    body.style.height = '104px';
      body.style.minWidth = '130px'; body.style.maxWidth = '130px';
      body.style.padding = '14px 10px';
      body.style.backdropFilter = 'none';
      body.style.overflow = 'visible';
      break;
    default: // rounded
      body.style.borderRadius = '18px';
      body.style.width = 'auto';     body.style.height = 'auto';
      body.style.minWidth = '110px'; body.style.maxWidth = '170px';
      body.style.padding = '12px 14px';
      break;
  }

  const inner = document.createElement('div');
  inner.style.cssText = `display:flex;flex-direction:column;align-items:center;gap:3px;width:100%;overflow:${isClip ? 'visible' : 'hidden'};`;

  if (n.image_url) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'width:100%;overflow:hidden;border-radius:5px;margin-bottom:3px;max-height:68px;flex-shrink:0;';
    const img = new Image();
    img.src = n.image_url;
    img.alt = '';
    img.style.cssText = 'width:100%;height:68px;object-fit:cover;display:block;';
    img.onerror = () => wrap.remove();
    wrap.appendChild(img);
    inner.appendChild(wrap);
  }

  if (n.icon) {
    const iconEl = document.createElement('div');
    iconEl.className = 'node-icon';
    iconEl.textContent = n.icon;
    inner.appendChild(iconEl);
  }

  if (n.label) {
    const labelEl = document.createElement('div');
    labelEl.className = 'node-label';
    labelEl.style.color = textColor;
    labelEl.textContent = n.label;
    inner.appendChild(labelEl);
  }

  if (n.sub) {
    const subEl = document.createElement('div');
    subEl.className = 'node-sub';
    subEl.style.cssText = `color:${textColor};opacity:0.7;`;
    subEl.textContent = n.sub;
    inner.appendChild(subEl);
  }

  body.appendChild(inner);

  const delBtn = document.createElement('button');
  delBtn.className = 'node-delete-btn';
  delBtn.title = 'Delete node';
  delBtn.dataset.id = n.id;
  delBtn.textContent = '✕';

  el.appendChild(body);
  el.appendChild(delBtn);
  let _dragging=false, _dragMoved=false, _histPushed=false, _sx, _sy, _nx, _ny;
  el.addEventListener('mousedown', e => {
    if (e.target.closest('.node-delete-btn')) return;
    e.stopPropagation(); _dragging=true; _dragMoved=false; _histPushed=false;
    _sx=e.clientX; _sy=e.clientY;
    const nd=NODES.find(nd=>nd.id===n.id);
    _nx=nd?nd.x:n.x; _ny=nd?nd.y:n.y;
    el.style.cursor='grabbing'; el.style.zIndex=50;
  });
  const _onMove = e => {
    if (!_dragging) return;
    const dx=(e.clientX-_sx)/state.scale, dy=(e.clientY-_sy)/state.scale;
    if (Math.abs(dx)>3||Math.abs(dy)>3) {
      if (!_dragMoved && !_histPushed) { mmPushHistory(); _histPushed=true; } _dragMoved=true;
    }
    if (!_dragMoved) return;
    const nd=NODES.find(nd=>nd.id===n.id); if (!nd) return;
    nd.x=_nx+dx; nd.y=_ny+dy;
    el.style.left=nd.x+'px'; el.style.top=nd.y+'px';
    redrawEdgesForNode(n.id);
  };
  const _onUp = async () => {
    if (!_dragging) return; _dragging=false; el.style.cursor=''; el.style.zIndex='';
    if (_dragMoved) {
      const nd=NODES.find(nd=>nd.id===n.id);
      if (nd) {
        nd.x = Math.round(nd.x); nd.y = Math.round(nd.y);
        if (isGuest()) {
          // Write x/y into BOTH storage keys — belt and braces
          guestSaveNodes();
          guestSavePositions();
        } else {
          try { await doUpdateNode(n.id, { x: nd.x, y: nd.y }); } catch {}
        }
      }
    }
  };
  window.addEventListener('mousemove', _onMove);
  window.addEventListener('mouseup', _onUp);
  el._cleanup = () => { window.removeEventListener('mousemove', _onMove); window.removeEventListener('mouseup', _onUp); };
  el.addEventListener('click', e => {
    if (e.target.closest('.node-delete-btn')) { e.stopPropagation(); confirmDeleteNode(n.id); return; }
    if (_dragMoved) { _dragMoved=false; return; }
    onNodeClick(n.id);
  });
  mapRoot.appendChild(el);
  nodeEls[n.id] = el;
}

function refreshNodeEl(nodeId) {
  const old=nodeEls[nodeId];
  if (old) { if (old._cleanup) old._cleanup(); old.remove(); }
  delete nodeEls[nodeId];
  const n=NODES.find(n=>n.id===nodeId); if (n) buildNode(n, true);
}

function buildAll() {
  mapRoot.querySelectorAll('.node').forEach(el=>{ if (el._cleanup) el._cleanup(); el.remove(); });
  svgEl.querySelectorAll('.edge-group').forEach(el=>el.remove());
  Object.keys(nodeEls).forEach(k=>delete nodeEls[k]);
  Object.keys(edgeEls).forEach(k=>delete edgeEls[k]);
  ensureSVGDefs();
  NODES.forEach(n=>buildNode(n, false));
  EDGES.forEach(e=>buildEdgeSVG(e));
  updateProgress(); renderTree();
  // Canvas ready fade-in
  const cv = document.getElementById('canvas');
  cv.classList.remove('mm-ready');
  requestAnimationFrame(()=>{ cv.classList.add('mm-ready'); });
}

function openEdgeEditModal(eid) {
  const edge=EDGES.find(e=>e.id===eid); if (!edge) return;
  const src=NODES.find(n=>n.id===edge.fromId), tgt=NODES.find(n=>n.id===edge.toId);
  const title=`${src?.icon||''} ${src?.label||'?'} → ${tgt?.icon||''} ${tgt?.label||'?'}`;
  const ex=document.getElementById('edge-edit-modal'); if (ex) ex.remove();
  const overlay=document.createElement('div');
  overlay.id='edge-edit-modal'; overlay.className='modal-overlay';
  const styleOpts=EDGE_STYLES.map(s=>`<option value="${s.val}"${edge.style===s.val?' selected':''}>${s.label}</option>`).join('');
  const arrowOpts=EDGE_ARROWS.map(a=>`<option value="${a.val}"${edge.arrow===a.val?' selected':''}>${a.label}</option>`).join('');
  const lineOpts=EDGE_LINE_TYPES.map(l=>`<option value="${l.val}"${edge.lineType===l.val?' selected':''}>${l.label}</option>`).join('');
  overlay.innerHTML=`<div class="modal" style="max-width:440px">
    <div class="modal-title">✦ Connection
      <span style="font-size:.72rem;font-weight:400;color:var(--muted);margin-left:6px">${title}</span>
      <button class="modal-close" id="ee-close">✕</button>
    </div>
    <div class="form-group">
      <label class="form-label">Label <span style="font-size:.72rem;color:var(--muted)">(shown on the line)</span></label>
      <input class="form-input" id="ee-label" placeholder="e.g. causes, supports, contradicts…" maxlength="40" value="${edge.label||''}">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px;">
      <div><label class="form-label">Line style</label>
        <select class="form-input" id="ee-style" style="font-size:.78rem;padding:6px 8px;">${styleOpts}</select></div>
      <div><label class="form-label">Arrow</label>
        <select class="form-input" id="ee-arrow" style="font-size:.78rem;padding:6px 8px;">${arrowOpts}</select></div>
      <div><label class="form-label">Line type</label>
        <select class="form-input" id="ee-linetype" style="font-size:.78rem;padding:6px 8px;">${lineOpts}</select></div>
    </div>
    <div style="margin-bottom:14px;">
      <label class="form-label">Preview</label>
      <svg id="ee-preview-svg" width="100%" height="58" style="display:block;border:1px solid var(--border);border-radius:8px;background:rgba(255,255,255,.02);">
        <defs>
          <marker id="ee-mfwd" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="#4488ff"/></marker>
          <marker id="ee-mbwd" markerWidth="8" markerHeight="8" refX="2" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M8,0 M8,6 L0,3 z" fill="#4488ff"/></marker>
        </defs>
        <path id="ee-pp2" fill="none" stroke="#4488ff" stroke-width="4.5" style="display:none;opacity:.2"/>
        <path id="ee-pp" fill="none" stroke="#4488ff" stroke-width="1.8" d="M20,29 Q190,5 370,29"/>
        <rect id="ee-pb" fill="#0d0f14" stroke="#4488ff" stroke-width="0.8" rx="4" style="display:none;opacity:.9"/>
        <text id="ee-pt" text-anchor="middle" dominant-baseline="middle" font-family="DM Sans,sans-serif" font-size="10" fill="#4488ff" style="display:none"/>
      </svg>
    </div>
    <div class="form-actions">
      <button class="btn" style="color:var(--red,#ff3b3b);border-color:rgba(255,59,59,.3);margin-right:auto" id="ee-delete">Delete connection</button>
      <button class="btn" id="ee-cancel">Cancel</button>
      <button class="btn btn-primary" id="ee-save">Save</button>
    </div>
  </div>`;
  document.body.appendChild(overlay); setTimeout(()=>overlay.classList.add('open'),10);
  overlay.querySelector('#ee-close').onclick = overlay.querySelector('#ee-cancel').onclick = ()=>overlay.remove();

  function updatePreview() {
    const st=overlay.querySelector('#ee-style').value, arr=overlay.querySelector('#ee-arrow').value;
    const lt=overlay.querySelector('#ee-linetype').value, lbl=overlay.querySelector('#ee-label').value;
    const color=getEdgeColor(edge);
    const pp=overlay.querySelector('#ee-pp'), pp2=overlay.querySelector('#ee-pp2');
    const pt=overlay.querySelector('#ee-pt'), pb=overlay.querySelector('#ee-pb');
    const d=lt==='straight'?'M20,29 L370,29':lt==='step'?'M20,29 L195,29 L195,29 L370,29':'M20,29 Q190,5 370,29';
    pp.setAttribute('d',d); pp.setAttribute('stroke',color); pp2.setAttribute('d',d); pp2.setAttribute('stroke',color);
    let da='none',sw='1.8'; if(st==='dashed')da='8 4'; if(st==='dotted')da='2 5'; if(st==='strong')sw='3.5';
    pp.setAttribute('stroke-dasharray',da); pp.setAttribute('stroke-width',sw);
    pp2.style.display=st==='double'?'':'none';
    overlay.querySelectorAll('#ee-preview-svg defs marker path').forEach(p=>p.setAttribute('fill',color));
    pp.setAttribute('marker-end',(arr==='forward'||arr==='both')?'url(#ee-mfwd)':'none');
    pp.setAttribute('marker-start',(arr==='backward'||arr==='both')?'url(#ee-mbwd)':'none');
    if(lbl){const tw=lbl.length*5.8+10;pb.setAttribute('x',195-tw/2);pb.setAttribute('y',22);pb.setAttribute('width',tw);pb.setAttribute('height',14);pb.setAttribute('stroke',color);pb.style.display='';pt.textContent=lbl;pt.setAttribute('x','195');pt.setAttribute('y','29');pt.setAttribute('fill',color);pt.style.display='';}
    else{pt.style.display='none';pb.style.display='none';}
  }
  ['#ee-style','#ee-arrow','#ee-linetype'].forEach(sel=>overlay.querySelector(sel).addEventListener('change',updatePreview));
  overlay.querySelector('#ee-label').addEventListener('input',updatePreview);
  updatePreview();
  overlay.querySelector('#ee-save').onclick=()=>{
    updateEdgeData(eid,{label:overlay.querySelector('#ee-label').value.trim(),style:overlay.querySelector('#ee-style').value,arrow:overlay.querySelector('#ee-arrow').value,lineType:overlay.querySelector('#ee-linetype').value});
    showToast('✦ Connection updated'); overlay.remove();
  };
  overlay.querySelector('#ee-delete').onclick=()=>{
    const e=EDGES.find(e=>e.id===eid); if(!e){overlay.remove();return;}
    const toNode=NODES.find(n=>n.id===e.toId);
    if(toNode){toNode.revealedBy=(toNode.revealedBy||[]).filter(id=>id!==e.fromId);if(isGuest())guestSaveNodes();}
    deleteEdgeData(eid); overlay.remove(); renderTree(); showToast('Connection removed');
  };
}

function onNodeClick(nodeId) {
  if (state.activeNode && state.activeNode !== nodeId) flushProgress();
  state.activeNode=nodeId; state.discovered.add(nodeId); state.pendingProgress.add(nodeId);
  Object.values(nodeEls).forEach(el=>el.classList.remove('active'));
  if (nodeEls[nodeId]) nodeEls[nodeId].classList.add('active');
  Object.values(edgeEls).forEach(({grp})=>grp.classList.remove('active-edge'));
  EDGES.filter(e=>e.fromId===nodeId||e.toId===nodeId).forEach(e=>{
    const els=edgeEls[e.id]; if(els)els.grp.classList.add('active-edge');
  });
  const node=NODES.find(n=>n.id===nodeId);
  openNodePanel(node); centerOn(node); updateProgress();
}

function openNodePanel(node) {
  if (!node) return;
  document.getElementById('panel-icon').textContent = node.icon||'📌';
  document.getElementById('panel-title').textContent = node.label||'(untitled)';
  const body=document.getElementById('panel-body'); body.innerHTML='';

  if (node.sub) { const p=document.createElement('p'); p.className='panel-context'; p.textContent=node.sub; body.appendChild(p); }

  if (node.image_url) {
    const d=document.createElement('div'); d.style.cssText='margin:.6rem 0;border-radius:8px;overflow:hidden;';
    d.innerHTML=`<img src="${node.image_url}" alt="" style="width:100%;max-height:150px;object-fit:cover;" onerror="this.parentElement.style.display='none'">`;
    body.appendChild(d);
  }

  const editBtn=document.createElement('button'); editBtn.className='btn';
  editBtn.style.cssText='width:100%;margin-top:.5rem;font-size:.8rem;'; editBtn.textContent='✏ Edit this node';
  editBtn.onclick=()=>openEditModal(node.id); body.appendChild(editBtn);

  // Connect button
  const connBtn=document.createElement('button'); connBtn.className='btn';
  connBtn.style.cssText='width:100%;margin-top:.4rem;font-size:.8rem;border-color:rgba(68,136,255,.3);color:var(--blue,#4488ff);';
  connBtn.textContent='🔗 Connect to another node';
  connBtn.onclick=()=>{ closePanel(); startConnectMode(node.id); }; body.appendChild(connBtn);

  // ── Unified connections section (edges + sub-nodes combined) ──────────────
  const connectedNodeIds = new Set();
  const connectedItems = []; // { otherId, direction, viaEdge, edgeId }

  // Collect EDGE connections
  EDGES.forEach(edge => {
    if (edge.fromId === node.id) {
      if (!connectedNodeIds.has(edge.toId)) {
        connectedNodeIds.add(edge.toId);
        connectedItems.push({ otherId: edge.toId, direction: edge.arrow || 'forward', edgeId: edge.id });
      }
    } else if (edge.toId === node.id) {
      if (!connectedNodeIds.has(edge.fromId)) {
        connectedNodeIds.add(edge.fromId);
        connectedItems.push({ otherId: edge.fromId, direction: edge.arrow === 'forward' ? 'incoming' : edge.arrow === 'backward' ? 'outgoing' : edge.arrow, edgeId: edge.id });
      }
    }
  });

  // Collect sub-nodes (revealedBy this node) not already in edge list
  NODES.forEach(n => {
    if ((n.revealedBy||[]).includes(node.id) && !connectedNodeIds.has(n.id)) {
      connectedNodeIds.add(n.id);
      connectedItems.push({ otherId: n.id, direction: 'sub', edgeId: null });
    }
  });

  // Collect parent nodes (this node is a sub-node of)
  (node.revealedBy||[]).forEach(parentId => {
    if (!connectedNodeIds.has(parentId)) {
      connectedNodeIds.add(parentId);
      connectedItems.push({ otherId: parentId, direction: 'parent', edgeId: null });
    }
  });

  if (connectedItems.length) {
    const sec=document.createElement('div'); sec.className='panel-children-section';
    sec.innerHTML=`<div class="nearby-label" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
      <span>Connections <span style="background:rgba(68,136,255,.15);color:var(--blue,#4488ff);border-radius:10px;padding:1px 7px;font-size:.7rem;font-weight:700;margin-left:4px;">${connectedItems.length}</span></span>
      <span style="font-size:.66rem;color:var(--muted)">click a line to edit it</span>
    </div>`;
    const tags=document.createElement('div'); tags.className='nearby-tags';
    connectedItems.forEach(item => {
      const other = NODES.find(n => n.id === item.otherId);
      if (!other) return;
      const t=document.createElement('div'); t.className='nearby-tag';
      t.style.cssText='display:flex;align-items:center;gap:5px;';
      let arrow = '';
      if      (item.direction === 'forward')  arrow = '<span style="opacity:.6;font-size:.7rem;">→</span>';
      else if (item.direction === 'backward') arrow = '<span style="opacity:.6;font-size:.7rem;">←</span>';
      else if (item.direction === 'both')     arrow = '<span style="opacity:.6;font-size:.7rem;">↔</span>';
      else if (item.direction === 'incoming') arrow = '<span style="opacity:.6;font-size:.7rem;">←</span>';
      else if (item.direction === 'outgoing') arrow = '<span style="opacity:.6;font-size:.7rem;">→</span>';
      else if (item.direction === 'sub')      arrow = '<span style="opacity:.5;font-size:.7rem;">↳</span>';
      else if (item.direction === 'parent')   arrow = '<span style="opacity:.5;font-size:.7rem;">↑</span>';
      const dotColor = other.color || '#4488ff';
      t.innerHTML = `${arrow}<span style="width:6px;height:6px;border-radius:50%;background:${dotColor};flex-shrink:0;display:inline-block;"></span>${other.icon||''} ${other.label||'(untitled)'}`;
      t.onclick=()=>{closePanel();setTimeout(()=>onNodeClick(other.id),150);};
      tags.appendChild(t);
    });
    sec.appendChild(tags); body.appendChild(sec);
  }

  const addBtn=document.createElement('button'); addBtn.className='btn btn-primary';
  addBtn.style.cssText='margin-top:1rem;width:100%;'; addBtn.textContent='+ Add sub-node here';
  addBtn.onclick=()=>openCreateModal(node.id); body.appendChild(addBtn);
  body.appendChild(buildSuggestFooter(node.id));
  document.getElementById('panel').classList.add('open');
  document.getElementById('canvas').classList.add('panel-open');
  document.getElementById('progress-strip')?.classList.add('panel-offset');
}

function closePanel() {
  flushProgress();
  document.getElementById('panel').classList.remove('open');
  document.getElementById('canvas').classList.remove('panel-open');
  document.getElementById('progress-strip')?.classList.remove('panel-offset');
  Object.values(edgeEls).forEach(({grp})=>grp.classList.remove('active-edge'));
  Object.values(nodeEls).forEach(el=>el.classList.remove('active'));
  state.activeNode=null;
}

function buildSuggestFooter(fromNodeId) {
  const f=document.createElement('div'); f.className='suggest-footer';
  f.innerHTML=`<button class="suggest-btn" id="suggest-open-btn">✦ AI suggest a node</button>`;
  f.querySelector('#suggest-open-btn').onclick=()=>openAISuggestModal(fromNodeId); return f;
}

/* ── Connect mode: click source node → click target node → create edge ── */
let _connectFromId = null;
let _connectBanner = null;

function startConnectMode(fromNodeId) {
  _connectFromId = fromNodeId;
  // Show banner
  if (_connectBanner) _connectBanner.remove();
  _connectBanner = document.createElement('div');
  _connectBanner.id = 'connect-banner';
  _connectBanner.style.cssText = [
    'position:fixed','bottom:80px','left:50%','transform:translateX(-50%)',
    'background:rgba(68,136,255,.92)','color:#fff','border-radius:12px',
    'padding:10px 20px','font-size:.82rem','font-family:\'DM Sans\',sans-serif',
    'font-weight:600','z-index:500','display:flex','align-items:center','gap:10px',
    'box-shadow:0 4px 24px rgba(68,136,255,.4)','backdrop-filter:blur(8px)',
    'pointer-events:auto'
  ].join(';');
  const srcNode = NODES.find(n=>n.id===fromNodeId);
  _connectBanner.innerHTML = `<span>🔗 Click any node to connect from <em>${srcNode?.icon||''} ${srcNode?.label||'node'}</em></span>
    <button id="connect-cancel-btn" style="background:rgba(255,255,255,.2);border:none;color:#fff;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:.78rem;">Cancel</button>`;
  document.body.appendChild(_connectBanner);
  _connectBanner.querySelector('#connect-cancel-btn').onclick = cancelConnectMode;

  // Highlight all nodes as clickable targets
  Object.entries(nodeEls).forEach(([id, el]) => {
    if (id === fromNodeId) {
      el.style.opacity = '0.45';
      return;
    }
    el.classList.add('mm-connect-target');
    el._connectHandler = () => finishConnectMode(id);
    el.addEventListener('click', el._connectHandler, { once: true });
  });

  // ESC to cancel
  window._connectEscHandler = e => { if (e.key==='Escape') cancelConnectMode(); };
  window.addEventListener('keydown', window._connectEscHandler);
}

function cancelConnectMode() {
  _connectFromId = null;
  if (_connectBanner) { _connectBanner.remove(); _connectBanner = null; }
  Object.entries(nodeEls).forEach(([id, el]) => {
    el.classList.remove('mm-connect-target');
    el.style.opacity = '';
    if (el._connectHandler) { el.removeEventListener('click', el._connectHandler); delete el._connectHandler; }
  });
  if (window._connectEscHandler) { window.removeEventListener('keydown', window._connectEscHandler); delete window._connectEscHandler; }
}

async function finishConnectMode(toNodeId) {
  const fromNodeId = _connectFromId;
  cancelConnectMode();
  if (!fromNodeId || !toNodeId || fromNodeId === toNodeId) return;
  // Check if edge already exists in memory (either direction)
  const already = EDGES.find(e=>(e.fromId===fromNodeId&&e.toId===toNodeId)||(e.fromId===toNodeId&&e.toId===fromNodeId));
  if (already) { showToast('Connection already exists — click the line to edit it'); return; }
  try {
    mmPushHistory();
    const result = await doCreateEdge(fromNodeId, toNodeId);
    const edge = ensureEdge(fromNodeId, toNodeId);
    // Sync real DB id back so delete works correctly
    if (result && result.id) edge.id = result.id;
    buildEdgeSVG(edge);
    showToast('🔗 Connection added');
  } catch(e) { showToast('Could not create connection'); }
}

async function confirmDeleteAllNodes() {
  if (!confirm('Delete ALL your nodes and connections? This cannot be undone.')) return;
  try {
    if (!isGuest()) {
      await apiDeleteAllNodes();
    }
    // Clear local state
    NODES.forEach(n => { const el = nodeEls[n.id]; if (el) el.remove(); delete nodeEls[n.id]; });
    document.querySelectorAll('.edge-svg, .edge-group').forEach(el => el.remove());
    Object.keys(edgeEls).forEach(k => delete edgeEls[k]);
    NODES = []; EDGES = [];
    if (isGuest()) { guestSaveNodes(); guestSaveEdges(); }
    updateProgress(); renderTree();
    showToast('🗑️ All nodes deleted');
  } catch(e) { showToast('Could not delete all nodes'); }
}

/* ── Node form HTML (now includes textColor + content) ── */
function buildNodeFormHTML(opts={}) {
  const { icon='', label='', sub='', color='#4488ff', textColor='#e8eaf0', shape='rounded', image_url='', titleText='Node' } = opts;
  return `<div class="modal-title">${titleText}<button class="modal-close" id="nf-close">✕</button></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px;">
      <div><label class="form-label">Icon / Emoji</label>
        <div id="nf-icon-grid" class="icon-grid" style="max-height:88px;overflow-y:auto;"></div>
        <input id="nf-icon-hidden" type="hidden" value="${icon}"></div>
      <div><label class="form-label">Shape</label>
        <div id="nf-shape-grid" style="display:flex;flex-wrap:wrap;gap:5px;"></div>
        <input id="nf-shape-hidden" type="hidden" value="${shape}"></div>
    </div>
    <div class="form-group"><label class="form-label">Label <span style="font-size:.75rem;color:var(--muted)">(optional)</span></label>
      <input class="form-input" id="nf-label" placeholder="e.g. Social Media Bias" maxlength="80" value="${label}"></div>
    <div class="form-group"><label class="form-label">Description <span style="font-size:.75rem;color:var(--muted)">(short subtitle)</span></label>
      <textarea class="form-textarea" id="nf-sub" style="min-height:40px" placeholder="Short description">${sub}</textarea></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px;">
      <div class="form-group" style="margin-bottom:0"><label class="form-label">Box Color</label>
        <div id="nf-color-grid" class="color-grid"></div>
        <input id="nf-color-hidden" type="hidden" value="${color}"></div>
      <div class="form-group" style="margin-bottom:0"><label class="form-label">Text Color</label>
        <div id="nf-textcolor-grid" class="color-grid"></div>
        <input id="nf-textcolor-hidden" type="hidden" value="${textColor}"></div>
    </div>
    <div class="form-group"><label class="form-label">Image <span style="font-size:.72rem;color:var(--muted)">(optional)</span></label>
      <div style="display:flex;gap:.4rem;align-items:center;">
        <input type="file" id="nf-img-file" accept="image/*" style="display:none;">
        <button type="button" id="nf-img-pick-btn" class="btn" style="font-size:.75rem;padding:.3rem .7rem;">📁 Upload &amp; Crop</button>
        <button type="button" id="nf-img-clear" class="btn" style="font-size:.73rem;padding:.3rem .7rem;display:${image_url?'':'none'};">✕ Remove</button>
      </div>
      <input id="nf-img-hidden" type="hidden" value="${image_url}">
      <div id="nf-img-preview" style="margin-top:5px;">${image_url?`<img src="${image_url}" style="max-width:100%;max-height:90px;border-radius:7px;">`:''}</div></div>
    <div id="nf-error" class="form-error"></div>
    <div class="form-actions"><button class="btn" id="nf-cancel">Cancel</button><button class="btn btn-primary" id="nf-submit">Save</button></div>`;
}

function wireNodeForm(overlay, defaults={}) {
  const dIcon=defaults.icon||'', dColor=defaults.color||'#4488ff', dShape=defaults.shape||'rounded', dTextColor=defaults.textColor||'#e8eaf0';

  // Icon grid
  const iconGrid=overlay.querySelector('#nf-icon-grid'), iconHidden=overlay.querySelector('#nf-icon-hidden');
  const noneBtn=document.createElement('button'); noneBtn.className='icon-btn'; noneBtn.title='No icon'; noneBtn.type='button';
  noneBtn.style.cssText='font-size:.65rem;color:var(--muted);letter-spacing:0;'; noneBtn.textContent='∅';
  if (!dIcon) noneBtn.classList.add('selected');
  noneBtn.onclick=()=>{iconGrid.querySelectorAll('.icon-btn').forEach(b=>b.classList.remove('selected'));noneBtn.classList.add('selected');iconHidden.value='';};
  iconGrid.appendChild(noneBtn);
  ICONS.forEach(ic=>{
    const btn=document.createElement('button'); btn.className='icon-btn'; btn.textContent=ic; btn.type='button';
    if (ic===dIcon) btn.classList.add('selected');
    btn.onclick=()=>{iconGrid.querySelectorAll('.icon-btn').forEach(b=>b.classList.remove('selected'));btn.classList.add('selected');iconHidden.value=ic;};
    iconGrid.appendChild(btn);
  });

  // Shape grid
  const shapeGrid=overlay.querySelector('#nf-shape-grid'), shapeHidden=overlay.querySelector('#nf-shape-hidden');
  SHAPES.forEach(s=>{
    const btn=document.createElement('button'); btn.type='button'; btn.title=s.label;
    btn.style.cssText=`width:40px;height:36px;background:var(--surface2,rgba(255,255,255,.05));border:2px solid ${s.val===dShape?'var(--blue,#4488ff)':'transparent'};border-radius:6px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;`;
    const previewCSS=s.css.replace('aspect-ratio:1;','').replace('min-width:88px;','').replace('transform:rotate(45deg);','');
    const isSquare=['circle','diamond','hex'].includes(s.val);
    btn.innerHTML=`<div style="width:${isSquare?'20px':'28px'};height:${isSquare?'20px':'14px'};background:rgba(68,136,255,.4);border:1.5px solid rgba(68,136,255,.7);${previewCSS}"></div>`;
    btn.onclick=()=>{shapeGrid.querySelectorAll('button').forEach(b=>b.style.borderColor='transparent');btn.style.borderColor='var(--blue,#4488ff)';shapeHidden.value=s.val;};
    shapeGrid.appendChild(btn);
  });

  // Box color grid
  const colorGrid=overlay.querySelector('#nf-color-grid'), colorHidden=overlay.querySelector('#nf-color-hidden');
  COLORS.forEach(c=>{
    const btn=document.createElement('button'); btn.className='color-swatch'; btn.style.background=c.val; btn.title=c.label; btn.type='button';
    if (c.val===dColor) btn.classList.add('selected');
    btn.onclick=()=>{colorGrid.querySelectorAll('.color-swatch').forEach(b=>b.classList.remove('selected'));btn.classList.add('selected');colorHidden.value=c.val;};
    colorGrid.appendChild(btn);
  });

  // Text color grid (independent picker)
  const tcGrid=overlay.querySelector('#nf-textcolor-grid'), tcHidden=overlay.querySelector('#nf-textcolor-hidden');
  TEXT_COLORS.forEach(c=>{
    const btn=document.createElement('button'); btn.className='color-swatch'; btn.style.background=c.val; btn.title=c.label; btn.type='button';
    if (c.val===dTextColor) btn.classList.add('selected');
    btn.onclick=()=>{tcGrid.querySelectorAll('.color-swatch').forEach(b=>b.classList.remove('selected'));btn.classList.add('selected');tcHidden.value=c.val;};
    tcGrid.appendChild(btn);
  });

  // Image upload
  const imgFile=overlay.querySelector('#nf-img-file'), imgHidden=overlay.querySelector('#nf-img-hidden'), imgPreview=overlay.querySelector('#nf-img-preview');
  const pickBtn=overlay.querySelector('#nf-img-pick-btn');
  if (pickBtn && imgFile) {
    pickBtn.onclick=()=>{
      imgFile.value='';
      imgFile.onchange=()=>{
        const file=imgFile.files[0]; if (!file) return;
        const reader=new FileReader();
        reader.onload=ev=>_mmOpenCrop(ev.target.result, imgHidden, imgPreview, overlay.querySelector('#nf-img-clear'));
        reader.readAsDataURL(file);
      };
      imgFile.click();
    };
  }
  const clearBtn=overlay.querySelector('#nf-img-clear');
  if (clearBtn) clearBtn.onclick=()=>{imgHidden.value='';imgPreview.innerHTML='';clearBtn.style.display='none';if(imgFile)imgFile.value='';};
  overlay.querySelector('#nf-close').onclick = overlay.querySelector('#nf-cancel').onclick = ()=>overlay.remove();

  return {
    getValues: ()=>({
      label:    overlay.querySelector('#nf-label').value.trim(),
      sub:      overlay.querySelector('#nf-sub').value.trim(),
      content:  null,
      icon:     iconHidden.value,
      color:    colorHidden.value,
      textColor: tcHidden.value,
      shape:    shapeHidden.value,
      image_url: imgHidden.value || null,
    }),
    errEl:     overlay.querySelector('#nf-error'),
    submitBtn: overlay.querySelector('#nf-submit'),
  };
}

function openCreateModal(parentId=null) {
  const existing=document.getElementById('create-node-modal'); if (existing) existing.remove();
  const parentNode=parentId?NODES.find(n=>n.id===parentId):null;
  const nodeType=parentId?'leaf':'cat';
  let defaultX=1800, defaultY=1500;
  if (parentNode) {
    const ch=NODES.filter(n=>(n.revealedBy||[]).includes(parentId));
    defaultX=parentNode.x+300; defaultY=parentNode.y+(ch.length*160)-(ch.length>0?80:0);
  } else if (NODES.length>0) {
    const roots=NODES.filter(n=>!n.revealedBy?.length), last=roots[roots.length-1];
    defaultX=last?last.x+320:1800; defaultY=last?last.y:1500;
  }
  const overlay=document.createElement('div'); overlay.id='create-node-modal'; overlay.className='modal-overlay';
  overlay.innerHTML=`<div class="modal" style="max-width:520px">${buildNodeFormHTML({titleText:parentNode?`Add sub-node to <em>${parentNode.icon||''} ${parentNode.label}</em>`:'Add a new parent node'})}</div>`;
  document.body.appendChild(overlay); setTimeout(()=>overlay.classList.add('open'),10);
  const {getValues,errEl,submitBtn}=wireNodeForm(overlay,{});
  submitBtn.textContent='Create node';
  submitBtn.onclick=async()=>{
    const v=getValues(); submitBtn.disabled=true; submitBtn.textContent='Creating…';
    try {
      mmPushHistory();
      const newNode=await doCreateNode({label:v.label,sub:v.sub||null,content:v.content||null,icon:v.icon,color:v.color,textColor:v.textColor,shape:v.shape,image_url:v.image_url,type:nodeType,x:Math.round(defaultX),y:Math.round(defaultY),start_visible:true,active:true,revealedBy:parentId?[parentId]:[]});
      if (parentId&&!isGuest()) await doCreateEdge(parentId,newNode.id);
      if (!NODES.find(n=>n.id===newNode.id)) NODES.push(newNode);
      buildNode(newNode, true);
      if (parentId) { const edge=ensureEdge(parentId,newNode.id); buildEdgeSVG(edge); }
      updateProgress(); renderTree(); overlay.remove(); centerOn(newNode);
      showToast(`✦ ${v.icon||''} ${v.label||'Node'} added`.trim());
    } catch(e) { submitBtn.disabled=false; submitBtn.textContent='Create node'; errEl.textContent=e.message||'Could not create node.'; errEl.style.display='block'; }
  };
}

function openEditModal(nodeId) {
  const node=NODES.find(n=>n.id===nodeId); if (!node) return;
  const existing=document.getElementById('edit-node-modal'); if (existing) existing.remove();
  const overlay=document.createElement('div'); overlay.id='edit-node-modal'; overlay.className='modal-overlay';
  overlay.innerHTML=`<div class="modal" style="max-width:520px">${buildNodeFormHTML({titleText:'Edit node',icon:node.icon||'📌',label:node.label,sub:node.sub||'',content:node.content||'',color:node.color||'#4488ff',textColor:node.textColor||'#e8eaf0',shape:node.shape||'rounded',image_url:node.image_url||''})}</div>`;
  document.body.appendChild(overlay); setTimeout(()=>overlay.classList.add('open'),10);
  const {getValues,errEl,submitBtn}=wireNodeForm(overlay,{icon:node.icon||'',color:node.color||'#4488ff',textColor:node.textColor||'#e8eaf0',shape:node.shape||'rounded'});
  submitBtn.textContent='Save changes';
  submitBtn.onclick=async()=>{
    const v=getValues(); submitBtn.disabled=true; submitBtn.textContent='Saving…';
    try {
      mmPushHistory();
      const updated=await doUpdateNode(nodeId,{label:v.label,sub:v.sub||null,content:v.content||null,icon:v.icon,color:v.color,textColor:v.textColor,shape:v.shape,image_url:v.image_url});
      const idx=NODES.findIndex(n=>n.id===nodeId); if (idx!==-1) NODES[idx]={...NODES[idx],...updated};
      refreshNodeEl(nodeId);
      EDGES.forEach(e=>{if(e.fromId===nodeId||e.toId===nodeId)updateEdgeSVG(e);});
      renderTree(); if(state.activeNode===nodeId)openNodePanel(NODES.find(n=>n.id===nodeId));
      overlay.remove(); showToast('✦ Node updated');
    } catch(e) { submitBtn.disabled=false; submitBtn.textContent='Save changes'; errEl.textContent=e.message||'Could not save.'; errEl.style.display='block'; }
  };
}

function confirmDeleteNode(nodeId) {
  const node=NODES.find(n=>n.id===nodeId); if (!node) return;
  const ch=NODES.filter(n=>(n.revealedBy||[]).includes(nodeId));
  const msg=ch.length?`Delete "${node.label}" and its ${ch.length} sub-node(s)?`:`Delete "${node.label}"?`;
  const ex=document.getElementById('confirm-modal'); if (ex) ex.remove();
  const overlay=document.createElement('div'); overlay.id='confirm-modal'; overlay.className='modal-overlay';
  overlay.innerHTML=`<div class="modal" style="max-width:380px;text-align:center">
    <div class="modal-title" style="justify-content:center">Confirm delete</div>
    <p style="font-size:.85rem;color:var(--muted);margin:0 0 1.4rem">${msg}</p>
    <div class="form-actions" style="justify-content:center">
      <button class="btn" id="conf-cancel">Cancel</button>
      <button class="btn" style="background:var(--red);color:#fff" id="conf-ok">Delete</button>
    </div></div>`;
  document.body.appendChild(overlay); setTimeout(()=>overlay.classList.add('open'),10);
  overlay.querySelector('#conf-cancel').onclick=()=>overlay.remove();
  overlay.querySelector('#conf-ok').onclick=async()=>{overlay.remove();await execDeleteNode(nodeId);};
}

async function execDeleteNode(nodeId) {
  try {
    mmPushHistory();
    // 1. Collect descendants while NODES is still intact (before any mutation).
    const toRemove = collectDescendants(nodeId);

    // 2. Remove DOM elements for all affected nodes/edges immediately.
    toRemove.forEach(id => {
      const el = nodeEls[id]; if (el) el.remove(); delete nodeEls[id];
      EDGES.filter(e => e.fromId === id || e.toId === id).forEach(e => removeEdgeSVG(e.id));
    });

    // 3. Update in-memory arrays.
    EDGES = EDGES.filter(e => !toRemove.includes(e.fromId) && !toRemove.includes(e.toId));
    NODES = NODES.filter(n => !toRemove.includes(n.id));
    NODES.forEach(n => { if (n.revealedBy) n.revealedBy = n.revealedBy.filter(id => !toRemove.includes(id)); });

    // 4. Persist AFTER memory is already correct.
    //    For guests: write the now-correct NODES/EDGES to localStorage so a
    //    refresh reflects the current in-memory state (not the pre-delete state).
    //    For logged-in users: hit the API.  Either way, errors here are non-fatal
    //    because the UI is already consistent.
    if (isGuest()) {
      guestSaveNodes(); guestSaveEdges(); guestSavePositions();
    } else {
      // Delete each node individually; swallow errors so partial failures
      // don't leave the UI in a broken state.
      await Promise.allSettled(toRemove.map(id => apiDeleteNode(id)));
    }

    if (toRemove.includes(state.activeNode)) closePanel();
    updateProgress(); renderTree(); showToast('Node deleted');
  } catch(e) { showToast('Could not delete node'); }
}

function collectDescendants(nodeId) {
  const result=[nodeId];
  NODES.filter(n=>(n.revealedBy||[]).includes(nodeId)).forEach(c=>result.push(...collectDescendants(c.id)));
  return result;
}

async function openAISuggestModal(fromNodeId) {
  const ex=document.getElementById('ai-suggest-modal'); if (ex) ex.remove();
  const fromNode=NODES.find(n=>n.id===fromNodeId);
  const overlay=document.createElement('div'); overlay.id='ai-suggest-modal'; overlay.className='modal-overlay';
  overlay.innerHTML=`<div class="modal" style="max-width:500px">
    <div class="modal-title">✦ AI Node Suggestions<button class="modal-close" id="ai-close">✕</button></div>
    <p style="font-size:.75rem;color:var(--muted);margin:0 0 .55rem;display:flex;align-items:center;gap:5px;">
      <span style="background:rgba(155,110,255,.15);color:#9b6eff;border:1px solid rgba(155,110,255,.3);border-radius:4px;padding:1px 6px;font-size:.68rem;font-weight:600;letter-spacing:.03em;">AI-generated</span>
      Suggestions are produced by an AI model and may be inaccurate. Review before adding.
    </p>
    <p style="font-size:.82rem;color:var(--muted);margin:0 0 .8rem">${fromNode?`Branch ideas from <strong style="color:var(--text)">${fromNode.icon||''} ${fromNode.label}</strong>`:'Ideas for a new parent node'}</p>
    <div class="form-group"><label class="form-label">Add context <span style="color:var(--muted);font-weight:400">(optional)</span></label>
      <textarea class="form-textarea" id="ai-context" style="min-height:48px" placeholder="e.g. Philippine media landscape"></textarea></div>
    <button class="btn btn-primary" id="ai-generate" style="width:100%">✦ Generate suggestions</button>
    <div id="ai-results" style="margin-top:1rem"></div>
  </div>`;
  document.body.appendChild(overlay); setTimeout(()=>overlay.classList.add('open'),10);
  overlay.querySelector('#ai-close').onclick=()=>overlay.remove();
  const btn=overlay.querySelector('#ai-generate'), results=overlay.querySelector('#ai-results');
  btn.onclick=async()=>{
    const ctx=overlay.querySelector('#ai-context').value.trim();
    btn.disabled=true; btn.textContent='✦ Thinking…';
    results.innerHTML=`<p style="font-size:.82rem;color:var(--muted);text-align:center">Generating…</p>`;
    try {
      const suggestions=await fetchAISuggestions(fromNode,ctx);
      results.innerHTML='';
      if (!suggestions.length) { results.innerHTML=`<p style="color:var(--muted);font-size:.82rem;text-align:center">No suggestions returned. Try adding more context.</p>`; btn.disabled=false; btn.textContent='✦ Generate suggestions'; return; }
      const grid=document.createElement('div'); grid.style.cssText='display:flex;flex-direction:column;gap:10px;';
      suggestions.forEach(s=>{
        const card=document.createElement('div'); card.className='ai-suggestion-card';
        card.innerHTML=`<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><span style="font-size:1.2rem">${s.icon}</span><strong style="color:var(--text);font-size:.9rem">${s.label}</strong></div>
          <p style="font-size:.78rem;color:var(--muted);margin:0 0 8px;line-height:1.5">${s.reason}</p>
          <button class="btn btn-primary" style="font-size:.75rem;padding:5px 12px;">+ Add this node</button>`;
        card.querySelector('button').onclick=async()=>{
          card.querySelector('button').textContent='Adding…'; card.querySelector('button').disabled=true;
          try {
            mmPushHistory();
            const newNode=await doCreateNode({label:s.label,sub:s.reason,icon:s.icon,color:s.color||'#9b6eff',textColor:'#e8eaf0',shape:'rounded',type:fromNodeId?'leaf':'cat',x:(fromNode?.x||1800)+300,y:(fromNode?.y||1500)+(Math.random()*300-150),start_visible:true,active:true,revealedBy:fromNodeId?[fromNodeId]:[]});
            if (fromNodeId&&!isGuest()) await doCreateEdge(fromNodeId,newNode.id);
            if (!NODES.find(n=>n.id===newNode.id)) NODES.push(newNode);
            buildNode(newNode, true);
            if (fromNodeId) { const edge=ensureEdge(fromNodeId,newNode.id); buildEdgeSVG(edge); }
            updateProgress(); renderTree(); card.style.opacity='0.4'; card.querySelector('button').textContent='✓ Added';
            showToast(`✦ ${s.icon} ${s.label} added`);
          } catch(e) { card.querySelector('button').textContent='Failed'; card.querySelector('button').disabled=false; }
        };
        grid.appendChild(card);
      });
      results.appendChild(grid);
    } catch(e) { results.innerHTML=`<p style="color:var(--red);font-size:.82rem">Could not reach AI. Try again shortly.</p>`; }
    btn.disabled=false; btn.textContent='✦ Regenerate';
  };
}

async function fetchAISuggestions(fromNode, userContext) {
  const existingLabels=NODES.map(n=>n.label).join(', ');
  const res=await fetch(`${API_BASE}/api/mindmap/ai-suggest`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({node_label:fromNode?.label||'a new topic',node_sub:fromNode?.sub||null,user_context:userContext||null,existing_labels:existingLabels||null})});
  if (!res.ok) { const err=await res.json().catch(()=>({})); throw new Error(err.detail||'AI API error'); }
  const data=await res.json(); return data.suggestions||[];
}

function renderTree() {
  const panel=document.getElementById('tree-panel'); if (!panel) return;
  const roots=NODES.filter(n=>!n.revealedBy?.length);
  if (roots.length===0) {
    panel.innerHTML=`<div class="tree-header">Add Node</div>
      <div class="tree-empty">
        <div style="font-size:2rem;margin-bottom:.5rem">🗺️</div>
        <p>Your map is empty.<br>Add your first node to get started.</p>
        <button class="btn btn-primary" onclick="openCreateModal(null)" style="margin-top:.5rem;font-size:.8rem">+ Add node</button>
      </div>`;
    return;
  }
  // Preserve existing search query across re-renders
  const prevQ = document.getElementById('tree-search')?.value || '';
  let html=`<div class="tree-header">Nodes <span class="tree-count">${NODES.length}</span></div>
    <div class="tree-search-wrap"><input type="text" id="tree-search" placeholder="🔍  Search nodes…" autocomplete="off" value="${prevQ.replace(/"/g,'&quot;')}"></div>
    <ul class="tree-root-list" id="tree-main-list">`;
  roots.forEach(r=>{ html+=renderTreeNode(r,0); });
  html+=`</ul><div style="padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:8px">
    <button class="btn btn-primary" onclick="openCreateModal(null)" style="flex:1;font-size:.8rem">+ Add node</button>
    <button class="btn" onclick="confirmDeleteAllNodes()" style="font-size:.8rem;background:rgba(255,60,60,.12);color:var(--red,#ff4444);border:1px solid rgba(255,60,60,.25);padding:6px 10px" title="Delete all nodes">🗑 All</button>
  </div>`;
  if (isGuest()) html+=`<div style="padding:8px 14px;background:rgba(68,136,255,.06);border-top:1px solid var(--border);font-size:.71rem;color:var(--muted);text-align:center;line-height:1.5">Guest mode — saved locally only.<br><a href="login.html" style="color:var(--blue)">Log in to save permanently →</a></div>`;
  panel.innerHTML=html;
  panel.querySelectorAll('[data-tree-node]').forEach(el=>{el.addEventListener('click',e=>{e.stopPropagation();const id=el.dataset.treeNode;closePanel();setTimeout(()=>onNodeClick(id),100);});});
  panel.querySelectorAll('[data-tree-add]').forEach(el=>{el.addEventListener('click',e=>{e.stopPropagation();openCreateModal(el.dataset.treeAdd);});});
  panel.querySelectorAll('[data-tree-edit]').forEach(el=>{el.addEventListener('click',e=>{e.stopPropagation();openEditModal(el.dataset.treeEdit);});});
  panel.querySelectorAll('[data-tree-del]').forEach(el=>{el.addEventListener('click',e=>{e.stopPropagation();confirmDeleteNode(el.dataset.treeDel);});});
  // Wire search
  const searchEl = panel.querySelector('#tree-search');
  if (searchEl) {
    if (prevQ) filterTree(prevQ.toLowerCase());
    searchEl.addEventListener('input', function() { filterTree(this.value.trim().toLowerCase()); });
    searchEl.addEventListener('keydown', e => e.stopPropagation()); // prevent H / Esc shortcuts
  }
}

function filterTree(q) {
  const list = document.getElementById('tree-main-list'); if (!list) return;
  if (!q) {
    list.querySelectorAll('.tree-node').forEach(li => li.style.display = '');
    return;
  }
  // Show nodes whose label matches; always show parent if any child matches
  function showIfMatch(li) {
    const label = li.querySelector('.tree-node-label')?.textContent?.toLowerCase() || '';
    const matches = label.includes(q);
    const childItems = [...li.querySelectorAll(':scope > ul > .tree-node')];
    const childVisible = childItems.some(c => showIfMatch(c));
    li.style.display = (matches || childVisible) ? '' : 'none';
    return matches || childVisible;
  }
  list.querySelectorAll(':scope > .tree-node').forEach(li => showIfMatch(li));
}



function renderTreeNode(node, depth) {
  const children=NODES.filter(n=>(n.revealedBy||[]).includes(node.id));
  const isActive=state.activeNode===node.id, indent=depth*14, color=node.color||'#4488ff';
  const isParent=!node.revealedBy?.length;
  const dotHTML=!isParent?`<span style="width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;opacity:.75;"></span>`:'';
  let html=`<li class="tree-node${isActive?' tree-node-active':''}" style="padding-left:${indent}px">
    <div class="tree-node-row" data-tree-node="${node.id}">
      ${dotHTML}<span class="tree-node-icon">${node.icon||(isParent?'◉':'')}</span>
      <span class="tree-node-label" style="color:${isActive?color:''};font-weight:${isParent?'600':'400'}">${node.label||'<em style="opacity:.5">untitled</em>'}</span>
      <span class="tree-node-actions">
        <button class="tree-action-btn" title="Add child" data-tree-add="${node.id}">+</button>
        <button class="tree-action-btn" title="Edit" data-tree-edit="${node.id}" style="font-size:.55rem">✏</button>
        <button class="tree-action-btn tree-del-btn" title="Delete" data-tree-del="${node.id}">✕</button>
      </span>
    </div>`;
  if (children.length) { html+=`<ul class="tree-children">`; children.forEach(c=>{html+=renderTreeNode(c,depth+1);}); html+=`</ul>`; }
  html+=`</li>`;
  return html;
}

function updateProgress() {
  const textEl=document.getElementById('progress-text'), dotsEl=document.getElementById('progress-dots');
  if (!textEl||!dotsEl) return;
  const total=NODES.length, found=state.discovered.size;
  if (total===0) { textEl.textContent='No nodes yet — add one!'; dotsEl.innerHTML=''; return; }
  textEl.textContent=found===total?`All ${total} nodes visited ✦`:`${found} of ${total} visited`;
  dotsEl.innerHTML='';
  for (let i=0;i<Math.min(total,30);i++) { const d=document.createElement('div'); d.className='progress-dot'+(i<found?' lit':''); dotsEl.appendChild(d); }
}

function openHelpModal(){const el=document.getElementById('help-modal-overlay');if(el)el.classList.add('open');}
function closeHelpModal(){const el=document.getElementById('help-modal-overlay');if(el)el.classList.remove('open');}
document.addEventListener('DOMContentLoaded',()=>{
  const overlay=document.getElementById('help-modal-overlay');
  if(overlay)overlay.addEventListener('click',e=>{if(e.target===overlay)closeHelpModal();});
});

function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');clearTimeout(t._timer);t._timer=setTimeout(()=>t.classList.remove('show'),2200);}

const canvas=document.getElementById('canvas');
let _vpSaveTimer = null;
function applyTransform(){
  mapRoot.style.transform=`translate(${state.panX}px,${state.panY}px) scale(${state.scale})`;
  // Debounced viewport persistence
  if (isGuest()) {
    clearTimeout(_vpSaveTimer);
    _vpSaveTimer = setTimeout(guestSaveViewport, 300);
  }
}

function centerOn(node, offset=true) {
  if (!node) return;
  const cw=canvas.clientWidth, ch=canvas.clientHeight;
  const targetX=cw/2-node.x*state.scale-(offset?-80:0), targetY=ch/2-node.y*state.scale;
  const startX=state.panX, startY=state.panY, startT=performance.now(), dur=500;
  function step(now){const t=Math.min((now-startT)/dur,1),ease=t<.5?2*t*t:-1+(4-2*t)*t;state.panX=startX+(targetX-startX)*ease;state.panY=startY+(targetY-startY)*ease;applyTransform();if(t<1)requestAnimationFrame(step);}
  requestAnimationFrame(step);
}

function zoomToFit(){closePanel();if(!NODES.length)return;state.scale=0.9;centerOn(NODES[0],false);}

canvas.addEventListener('mousedown',e=>{
  if(e.target.closest('.node,.opt-btn,input,.tap-zone,.comment,button'))return;
  state.isPanning=true;state.panStartX=e.clientX;state.panStartY=e.clientY;state.panStartTX=state.panX;state.panStartTY=state.panY;canvas.classList.add('panning');
});
window.addEventListener('mousemove',e=>{if(!state.isPanning)return;state.panX=state.panStartTX+e.clientX-state.panStartX;state.panY=state.panStartTY+e.clientY-state.panStartY;applyTransform();});
window.addEventListener('mouseup',()=>{state.isPanning=false;canvas.classList.remove('panning');});
canvas.addEventListener('wheel',e=>{
  e.preventDefault();const factor=e.deltaY>0?0.92:1.08;const rect=canvas.getBoundingClientRect();
  const mx=e.clientX-rect.left,my=e.clientY-rect.top;const newScale=Math.min(2,Math.max(0.35,state.scale*factor));
  const sd=newScale/state.scale;state.panX=mx-(mx-state.panX)*sd;state.panY=my-(my-state.panY)*sd;state.scale=newScale;applyTransform();
},{passive:false});

document.addEventListener('DOMContentLoaded',()=>{
  function zoomStep(factor){const rect=canvas.getBoundingClientRect();const mx=rect.width/2,my=rect.height/2;const newScale=Math.min(2,Math.max(0.35,state.scale*factor));const sd=newScale/state.scale;state.panX=mx-(mx-state.panX)*sd;state.panY=my-(my-state.panY)*sd;state.scale=newScale;applyTransform();}
  const zoomIn=document.getElementById('zoom-in-btn'),zoomOut=document.getElementById('zoom-out-btn');
  if(zoomIn)zoomIn.addEventListener('click',()=>zoomStep(1.2));
  if(zoomOut)zoomOut.addEventListener('click',()=>zoomStep(1/1.2));
});

let lastTouches=null,lastPinchDist=null;
canvas.addEventListener('touchstart',e=>{lastTouches=[...e.touches];state.panStartTX=state.panX;state.panStartTY=state.panY;if(e.touches.length===2)lastPinchDist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);},{passive:true});
canvas.addEventListener('touchmove',e=>{
  if(e.touches.length===1&&lastTouches?.length===1){state.panX+=e.touches[0].clientX-lastTouches[0].clientX;state.panY+=e.touches[0].clientY-lastTouches[0].clientY;applyTransform();}
  else if(e.touches.length===2&&lastPinchDist){const dist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);const factor=dist/lastPinchDist;const midX=(e.touches[0].clientX+e.touches[1].clientX)/2,midY=(e.touches[0].clientY+e.touches[1].clientY)/2;const rect=canvas.getBoundingClientRect();const mx=midX-rect.left,my=midY-rect.top;const newScale=Math.min(2,Math.max(0.35,state.scale*factor));const sd=newScale/state.scale;state.panX=mx-(mx-state.panX)*sd;state.panY=my-(my-state.panY)*sd;state.scale=newScale;applyTransform();lastPinchDist=dist;}
  lastTouches=[...e.touches];
},{passive:true});

document.addEventListener('keydown',e=>{if(e.key==='Escape')closePanel();if((e.key==='h'||e.key==='H')&&!e.target.matches('input,textarea'))zoomToFit();});
document.getElementById('panel-close').addEventListener('click',closePanel);

const _sidebar=document.getElementById('sidebar'),_overlay=document.getElementById('sidebar-overlay'),_burgerBtn=document.getElementById('burger-btn');
let _sidebarOpen=window.innerWidth>=900;
function initSidebar(){if(!_sidebar||!_burgerBtn)return;if(window.innerWidth<900){_sidebar.classList.add('collapsed');_burgerBtn.classList.add('visible');_sidebarOpen=false;}else{_sidebar.classList.remove('collapsed');_burgerBtn.classList.remove('visible');_sidebarOpen=true;}}
function toggleSidebar(){if(!_sidebar||!_burgerBtn)return;_sidebarOpen=!_sidebarOpen;if(_sidebarOpen){_sidebar.classList.remove('collapsed');if(window.innerWidth<900&&_overlay)_overlay.classList.add('visible');_burgerBtn.classList.remove('visible');}else{_sidebar.classList.add('collapsed');if(_overlay)_overlay.classList.remove('visible');_burgerBtn.classList.add('visible');}}
window.addEventListener('resize',initSidebar);initSidebar();

(function authSidebar(){
  const username=localStorage.getItem('sp_username');
  const loginLink=document.getElementById('sidebar-login-link');
  const loginLinkM=document.getElementById('sidebar-login-link-m');
  if(username){
    const logoutHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>Log out`;
    const doLogout=async e=>{e.preventDefault();await fetch('/auth/cookie-logout',{method:'POST',credentials:'include'}).catch(()=>{});localStorage.clear();window.location.href='login.html';};
    if(loginLink){loginLink.innerHTML=logoutHTML;loginLink.href='#';loginLink.style.color='var(--red)';loginLink.onclick=doLogout;}
    if(loginLinkM){loginLinkM.innerHTML=logoutHTML;loginLinkM.href='#';loginLinkM.style.color='var(--red)';loginLinkM.onclick=doLogout;}
  }
})();

async function init() {
  ensureSVGDefs();
  if (isGuest()) {
    NODES=guestLoadNodes();   // images and positions are merged inside
    EDGES=guestLoadEdges();
    NODES.forEach(n=>{(n.revealedBy||[]).forEach(srcId=>{ensureEdge(srcId,n.id);});});
    buildAll();
    // Restore saved viewport or default to centered view
    const savedVp = guestLoadViewport();
    if (savedVp) {
      state.panX=savedVp.panX; state.panY=savedVp.panY; state.scale=savedVp.scale; applyTransform();
    } else if (NODES.length>0) { state.scale=0.9; centerOn(NODES[0],false); }
    else { state.scale=0.9; state.panX=canvas.clientWidth/2-1800*state.scale; state.panY=canvas.clientHeight/2-1500*state.scale; applyTransform(); }
    updateProgress(); renderTree(); return;
  }
  const graphData=await fetchUserGraph();
  if (graphData) {
    NODES=graphData.nodes||[];
    NODES.forEach(n=>{(n.revealedBy||[]).forEach(srcId=>{ensureEdge(srcId,n.id);});});
  }
  buildAll();
  if (NODES.length>0) { state.scale=0.9; centerOn(NODES[0],false); }
  else { state.scale=0.9; state.panX=canvas.clientWidth/2-1800*state.scale; state.panY=canvas.clientHeight/2-1500*state.scale; applyTransform(); }
  updateProgress(); renderTree();
}

init();

// ── Mindmap node image crop modal ─────────────────────────────────────────────
(function(){
  const div = document.createElement('div');
  div.id = 'mm-crop-overlay';
  div.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.78);z-index:9999;align-items:center;justify-content:center;';
  div.innerHTML = `
  <div style="background:var(--card-bg,#1a1a2e);border:1px solid var(--border,#333);border-radius:14px;padding:1.1rem;width:min(96vw,580px);max-height:92vh;display:flex;flex-direction:column;gap:.65rem;">
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <span style="font-weight:700;font-size:.95rem;">Crop &amp; Resize Image</span>
      <button id="mmcrop-cancel-x" style="background:none;border:none;color:var(--muted,#888);cursor:pointer;font-size:1.1rem;">✕</button>
    </div>
    <div style="position:relative;overflow:hidden;border-radius:8px;border:1px solid var(--border,#333);background:#111;flex:1;min-height:0;display:flex;align-items:center;justify-content:center;">
      <canvas id="mmcrop-canvas" style="display:block;max-width:100%;max-height:300px;cursor:crosshair;touch-action:none;"></canvas>
    </div>
    <div style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;font-size:.8rem;color:var(--muted,#888);">
      <label style="display:flex;align-items:center;gap:.3rem;">W<input id="mmcrop-w" type="number" min="10" style="width:64px;background:var(--input-bg,#0d0d1a);border:1px solid var(--border,#333);border-radius:5px;color:var(--text,#fff);padding:.2rem .35rem;font-size:.8rem;">px</label>
      <label style="display:flex;align-items:center;gap:.3rem;">H<input id="mmcrop-h" type="number" min="10" style="width:64px;background:var(--input-bg,#0d0d1a);border:1px solid var(--border,#333);border-radius:5px;color:var(--text,#fff);padding:.2rem .35rem;font-size:.8rem;">px</label>
      <label style="display:flex;align-items:center;gap:.3rem;cursor:pointer;"><input id="mmcrop-lock" type="checkbox" checked style="accent-color:var(--accent,#6c63ff);"> Lock ratio</label>
      <span style="margin-left:auto;font-size:.73rem;">Drag to select crop area</span>
    </div>
    <div style="display:flex;gap:.5rem;align-items:center;font-size:.8rem;color:var(--muted,#888);">
      <label style="display:flex;align-items:center;gap:.3rem;">Quality<input id="mmcrop-quality" type="range" min="50" max="100" value="88" style="width:80px;accent-color:var(--accent,#6c63ff);"><span id="mmcrop-qval">88%</span></label>
    </div>
    <div style="display:flex;gap:.5rem;justify-content:flex-end;">
      <button id="mmcrop-cancel" class="btn">Cancel</button>
      <button id="mmcrop-reset" class="btn">Reset</button>
      <button id="mmcrop-apply" class="btn btn-primary">Apply</button>
    </div>
  </div>`;
  document.body.appendChild(div);

  let _img=null,_scale=1,_natW=0,_natH=0,_ar=1;
  let _cx=0,_cy=0,_cw=0,_ch=0;
  let _drag=false,_dx=0,_dy=0;
  let _targetHidden=null,_targetPreview=null,_targetClear=null;

  function draw(){
    const c=document.getElementById('mmcrop-canvas');if(!c||!_img)return;
    const ctx=c.getContext('2d');
    ctx.clearRect(0,0,c.width,c.height);ctx.drawImage(_img,0,0,c.width,c.height);
    const rx=Math.round(_cx*_scale),ry=Math.round(_cy*_scale),rw=Math.round(_cw*_scale),rh=Math.round(_ch*_scale);
    ctx.fillStyle='rgba(0,0,0,.5)';ctx.fillRect(0,0,c.width,ry);ctx.fillRect(0,ry+rh,c.width,c.height-ry-rh);
    ctx.fillRect(0,ry,rx,rh);ctx.fillRect(rx+rw,ry,c.width-rx-rw,rh);
    ctx.strokeStyle='#6c63ff';ctx.lineWidth=1.5;ctx.strokeRect(rx,ry,rw,rh);
    ctx.fillStyle='#fff';
    [[rx,ry],[rx+rw,ry],[rx,ry+rh],[rx+rw,ry+rh]].forEach(([hx,hy])=>{ctx.beginPath();ctx.arc(hx,hy,4.5,0,Math.PI*2);ctx.fill();ctx.stroke();});
  }

  function xy(e,c){const r=c.getBoundingClientRect();return[Math.max(0,Math.min((e.clientX-r.left)/_scale,_natW)),Math.max(0,Math.min((e.clientY-r.top)/_scale,_natH))];}
  function onDown(e,c){const[x,y]=xy(e,c);_drag=true;_dx=x;_dy=y;_cx=x;_cy=y;_cw=0;_ch=0;}
  function onMove(e,c){
    if(!_drag)return;
    const[x,y]=xy(e,c);
    const rw=x-_dx,rh=y-_dy;
    _cx=rw>=0?_dx:x;_cy=rh>=0?_dy:y;_cw=Math.abs(rw);_ch=Math.abs(rh);
    if(document.getElementById('mmcrop-lock').checked&&_cw>1)_ch=_cw/_ar;
    document.getElementById('mmcrop-w').value=Math.round(_cw);
    document.getElementById('mmcrop-h').value=Math.round(_ch);
    draw();
  }

  window._mmOpenCrop=function(dataUrl,hiddenEl,previewEl,clearEl){
    _targetHidden=hiddenEl;_targetPreview=previewEl;_targetClear=clearEl;
    const img=new Image();
    img.onload=()=>{
      _img=img;_natW=img.naturalWidth;_natH=img.naturalHeight;_ar=_natW/_natH;
      const maxW=Math.min(540,window.innerWidth-80),maxH=300;
      _scale=Math.min(maxW/_natW,maxH/_natH,1);
      const c=document.getElementById('mmcrop-canvas');
      c.width=Math.round(_natW*_scale);c.height=Math.round(_natH*_scale);
      _cx=0;_cy=0;_cw=_natW;_ch=_natH;
      document.getElementById('mmcrop-w').value=_natW;
      document.getElementById('mmcrop-h').value=_natH;
      document.getElementById('mmcrop-quality').oninput=function(){document.getElementById('mmcrop-qval').textContent=this.value+'%';};
      const wI=document.getElementById('mmcrop-w'),hI=document.getElementById('mmcrop-h'),lk=document.getElementById('mmcrop-lock');
      wI.oninput=()=>{const nw=Math.max(10,parseInt(wI.value)||10);if(lk.checked){const nh=Math.round(nw/_ar);hI.value=nh;_ch=Math.min(nh,_natH);}_cw=Math.min(nw,_natW);draw();};
      hI.oninput=()=>{const nh=Math.max(10,parseInt(hI.value)||10);if(lk.checked){const nw=Math.round(nh*_ar);wI.value=nw;_cw=Math.min(nw,_natW);}_ch=Math.min(nh,_natH);draw();};
      c.onmousedown=(e)=>onDown(e,c);c.onmousemove=(e)=>onMove(e,c);c.onmouseup=c.onmouseleave=()=>_drag=false;
      c.ontouchstart=(e)=>{e.preventDefault();onDown(e.touches[0],c);};
      c.ontouchmove=(e)=>{e.preventDefault();onMove(e.touches[0],c);};
      c.ontouchend=()=>_drag=false;
      draw();div.style.display='flex';
    };
    img.src=dataUrl;
  };

  function cancel(){div.style.display='none';}
  function reset(){if(!_img)return;_cx=0;_cy=0;_cw=_natW;_ch=_natH;document.getElementById('mmcrop-w').value=_natW;document.getElementById('mmcrop-h').value=_natH;draw();}
  function apply(){
    if(!_img||!_targetHidden)return;
    const outW=Math.max(1,Math.round(_cw)),outH=Math.max(1,Math.round(_ch));
    const off=document.createElement('canvas');off.width=outW;off.height=outH;
    off.getContext('2d').drawImage(_img,_cx,_cy,_cw,_ch,0,0,outW,outH);
    const q=parseInt(document.getElementById('mmcrop-quality').value)/100;
    const url=off.toDataURL('image/jpeg',q);
    _targetHidden.value=url;
    if(_targetPreview)_targetPreview.innerHTML=`<img src="${url}" style="max-width:100%;max-height:90px;border-radius:7px;margin-top:5px;">`;
    if(_targetClear)_targetClear.style.display='';
    cancel();
  }

  document.getElementById('mmcrop-cancel-x').onclick=cancel;
  document.getElementById('mmcrop-cancel').onclick=cancel;
  document.getElementById('mmcrop-reset').onclick=reset;
  document.getElementById('mmcrop-apply').onclick=apply;
})();
