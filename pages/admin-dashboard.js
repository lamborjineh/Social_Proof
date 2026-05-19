// ─────────────────────────────────────────────────────────────────────────────
// SocialProof — admin-dashboard.js
// Admin-only dashboard. Redirects non-admins to dashboard.html.
// ─────────────────────────────────────────────────────────────────────────────

const API = '';

// ── Shared utility helpers ────────────────────────────────────────────────────
function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3500);
}

// ── Dynamic topic registry ────────────────────────────────────────────────────
let _dashTopicRegistry = {};

async function _loadDashTopics() {
  try {
    const data = await apiFetch('/admin/topics');
    _dashTopicRegistry = {};
    (data.topics || []).forEach(t => { _dashTopicRegistry[t.key] = t; });
  } catch(_) {}
}

function _topicHue(topic) {
  let h = 0;
  for (let i = 0; i < (topic||'').length; i++) h = (h * 31 + topic.charCodeAt(i)) & 0xffff;
  return h % 360;
}

function _topicLabel(t) {
  const r = _dashTopicRegistry[t];
  if (r) return r.label;
  return (t || 'general').replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
}

function _topicStyle(t) {
  const r   = _dashTopicRegistry[t];
  const hue = r ? r.color_hue : _topicHue(t);
  return `background:hsla(${hue},70%,65%,.13);color:hsl(${hue},70%,72%);`;
}

function topicBadge(t) {
  return `<span class="badge" style="${_topicStyle(t)}border:none;">${_topicLabel(t)}</span>`;
}

function topicLabel(t) { return _topicLabel(t); }

// ── Panel title map — declared before the IIFE so showAdminPanel() can access it ──
const PANEL_TITLES = {
  analytics: 'Analytics', quiz: 'Quiz Questions', lessons: 'Lessons',
  users: 'Users', topics: 'Topics',
  'eval-questions': 'Eval Questions',
  preposttest: 'Pre / Post-test Questions', 'audit-log': 'Audit Log',
};

// ── Admin sidebar nav initialisation ─────────────────────────────────────────
(async function initAdminDashboard() {
  const username = localStorage.getItem('sp_username');
  const role     = localStorage.getItem('sp_role');

  // Guard: non-admins go to user dashboard
  if (role !== 'admin') {
    window.location.replace('dashboard.html');
    return;
  }

  // Show username badge
  const usernameEl = document.getElementById('topbar-username-admin');
  if (usernameEl) usernameEl.textContent = username || 'Admin';

  // Wire up logout
  const logoutBtn = document.getElementById('sidebar-logout-btn');
  if (logoutBtn) {
    logoutBtn.onclick = async function(e) {
      e.preventDefault();
      await fetch('/auth/cookie-logout', { method: 'POST', credentials: 'include' }).catch(() => {});
      localStorage.clear();
      window.location.href = 'login.html';
    };
  }

  // Sidebar toggle (for mobile)
  var sidebar   = document.getElementById('sidebar');
  var burgerBtn = document.getElementById('burger-btn');
  var overlay   = document.getElementById('sidebar-overlay');
  var sidebarOpen = window.innerWidth >= 900;

  function _syncSidebar() {
    if (window.innerWidth < 900) {
      sidebar.classList.add('collapsed');
      burgerBtn.classList.add('visible');
      sidebarOpen = false;
    } else {
      sidebar.classList.remove('collapsed');
      burgerBtn.classList.remove('visible');
      sidebarOpen = true;
    }
  }

  window.toggleSidebar = function() {
    sidebarOpen = !sidebarOpen;
    if (sidebarOpen) {
      sidebar.classList.remove('collapsed');
      if (overlay) overlay.classList.add('visible');
      burgerBtn.classList.remove('visible');
    } else {
      sidebar.classList.add('collapsed');
      if (overlay) overlay.classList.remove('visible');
      burgerBtn.classList.add('visible');
    }
  };

  if (overlay) overlay.addEventListener('click', window.toggleSidebar);
  window.addEventListener('resize', _syncSidebar);
  _syncSidebar();

  // Restore last active panel, fall back to analytics
  const lastPanel = localStorage.getItem('sp_admin_panel');
  showAdminPanel(lastPanel && document.getElementById('panel-' + lastPanel) ? lastPanel : 'analytics');
})();


// ══ ADMIN CONSOLE JS ═════════════════════════════════════════════════════════

async function showAdminPanel(name) {
  localStorage.setItem('sp_admin_panel', name);
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item, .nav-sub-item, .nav-sub-child').forEach(n => n.classList.remove('active'));
  const panel = document.getElementById('panel-' + name);
  if (panel) panel.classList.add('active');
  const navBtn = document.getElementById('nav-sub-' + name);
  if (navBtn) {
    navBtn.classList.add('active');
    const parentGroup = navBtn.closest('.nav-dropdown-group');
    if (parentGroup) parentGroup.classList.add('open');
  }
  const titleEl = document.getElementById('admin-panel-title');
  if (titleEl) titleEl.textContent = PANEL_TITLES[name] || name;
  const subEl = document.getElementById('admin-panel-sub');
  if (subEl) subEl.textContent = 'ADMIN CONSOLE · ' + (PANEL_TITLES[name] || name).toUpperCase();
  if (name === 'analytics')              await loadAnalytics();
  if (name === 'quiz')                   await loadQuiz();
  if (name === 'lessons')                await loadLessons();
  if (name === 'users')                  await loadUsers();
  if (name === 'topics')                 await _refreshTopics();
  if (name === 'eval-questions') { _injectEvalQModal(); await loadEvalQuestions(); }
  if (name === 'preposttest')            await loadPrePostTest();
  if (name === 'audit-log')             await loadAuditLog(1);
}

// ── State ──────────────────────────────────────────────────────────────────
let _quizData    = [];
let _lessonsData = [];
let _usersData   = [];
let _lessonsList = []; // for quiz modal dropdown

// ── Auth ───────────────────────────────────────────────────────────────────
function getHeaders() {
  return { 'Content-Type': 'application/json' };
}

// ── Init ───────────────────────────────────────────────────────────────────
// (Admin panel data is loaded by loadAdminPanel via initDashboard above)

// ── Navigation ─────────────────────────────────────────────────────────────
// toggleAdminGroup kept below
function toggleAdminGroup(btn) {
  const isOpen = btn.classList.toggle('open');
  const sub = btn.nextElementSibling;
  if (sub) sub.classList.toggle('open', isOpen);
}

// ── API helpers ────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, { credentials: 'include', headers: getHeaders(), ...opts });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    // FastAPI Pydantic validation errors return detail as an array of objects
    const detail = err.detail;
    const message = Array.isArray(detail)
      ? detail.map(e => `${e.loc ? e.loc.slice(-1)[0] + ': ' : ''}${e.msg}`).join(' | ')
      : (detail || res.statusText);
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ── HELPERS ────────────────────────────────────────────────────────────────
function statCard(label, value, sub) {
  return `<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value">${value ?? '—'}</div>${sub ? `<div class="stat-sub">${sub}</div>` : ''}</div>`;
}

function renderBarChart(chartId, labelsId, values, labels, color) {
  if (!values.length) { document.getElementById(chartId).innerHTML = '<div style="color:var(--muted);font-size:.75rem;font-family:DM Mono,monospace">No data yet</div>'; return; }
  const max = Math.max(...values, 1);
  document.getElementById(chartId).innerHTML = values.map(v =>
    `<div class="bar" style="height:${Math.max(4, Math.round(v / max * 74))}px;background:${color};opacity:.8"></div>`
  ).join('');
  document.getElementById(labelsId).innerHTML = labels.map(l =>
    `<div class="bar-label">${l}</div>`
  ).join('');
}

// ── QUIZ ───────────────────────────────────────────────────────────────────

// Fallback topic list — shown in datalists when the API hasn't loaded yet or fails
const _KNOWN_TOPICS = [
  { key: 'claim_detection',    label: 'Claim Detection',    icon: '🎯', color_hue: 220, sort_order: 1 },
  { key: 'source_verification',label: 'Source Verification',icon: '🔍', color_hue: 158, sort_order: 2 },
  { key: 'bias_detection',     label: 'Bias Detection',     icon: '⚡', color_hue:  38, sort_order: 3 },
  { key: 'evidence_evaluation',label: 'Evidence Evaluation',icon: '📊', color_hue: 340, sort_order: 4 },
  { key: 'general',            label: 'General MIL',        icon: '📖', color_hue: 260, sort_order: 5 },
];

let _mindmapNodes = []; // cache of main-map nodes for dropdowns

async function _refreshMindmapNodes() {
  try {
    const data = await apiFetch('/api/mindmap/graph?map=main');
    _mindmapNodes = (data.nodes || []).sort((a, b) => a.label.localeCompare(b.label));
  } catch(_) { _mindmapNodes = []; }
  const opts = '<option value="">— no node —</option>' +
    _mindmapNodes.map(n => `<option value="${escAttr(n.id)}">${escHtml(n.icon ? n.icon + ' ' : '')}${escHtml(n.label)}${n.sub ? ' — ' + escHtml(n.sub) : ''}</option>`).join('');
  ['qm-mindmap-node', 'lm-mindmap-node'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const prev = el.value;
    el.innerHTML = opts;
    if (prev && el.querySelector(`option[value="${escAttr(prev)}"]`)) el.value = prev;
  });
}

async function _refreshTopics() {
  try {
    await _loadDashTopics();
    // If API returned nothing, fall back to hardcoded known topics
    if (!Object.keys(_dashTopicRegistry).length) {
      _KNOWN_TOPICS.forEach(t => { _dashTopicRegistry[t.key] = t; });
    }
    const topics = Object.values(_dashTopicRegistry);
    // Populate topic filter select
    const filterSel = document.getElementById('quiz-filter-topic');
    if (filterSel) {
      const prev = filterSel.value;
      filterSel.innerHTML = '<option value="">All topics</option>' +
        topics.map(t => `<option value="${escAttr(t.key)}">${escHtml(t.label)}</option>`).join('');
      filterSel.value = prev;
    }
    // Populate quiz modal topic select
    const dl = document.getElementById('qm-topic');
    if (dl) {
      const prev = dl.value;
      dl.innerHTML = topics.map(t => `<option value="${escAttr(t.key)}">${escHtml(t.icon ? t.icon + ' ' : '')}${escHtml(t.label)}</option>`).join('');
      if (prev && dl.querySelector(`option[value="${escAttr(prev)}"]`)) dl.value = prev;
      else if (dl.options.length) dl.value = dl.options[0].value;
    }
    // Populate lesson modal topic select
    const ldl = document.getElementById('lm-topic');
    if (ldl) {
      const prev2 = ldl.value;
      ldl.innerHTML = topics.map(t => `<option value="${escAttr(t.key)}">${escHtml(t.icon ? t.icon + ' ' : '')}${escHtml(t.label)}</option>`).join('');
      if (prev2 && ldl.querySelector(`option[value="${escAttr(prev2)}"]`)) ldl.value = prev2;
      else if (ldl.options.length) ldl.value = ldl.options[0].value;
    }
    // Refresh topics management tab
    _renderTopicsTable();
  } catch(_) {}
}

async function loadQuiz() {
  await _refreshTopics();
  try {
    _quizData = await apiFetch('/admin/quiz/questions');
    renderQuizTable(_quizData);
    _lessonsList = await apiFetch('/admin/lessons');
    populateLessonDropdown('qm-lesson', _lessonsList);
  } catch(e) {
    document.getElementById('quiz-table-body').innerHTML = `<tr><td colspan="6" style="color:var(--red);text-align:center;padding:1rem;font-size:.8rem">${e.message}</td></tr>`;
  }
}

function filterQuiz() {
  const q = (document.getElementById('quiz-search')?.value || '').toLowerCase().trim();
  const topic = document.getElementById('quiz-filter-topic').value;
  const diff  = document.getElementById('quiz-filter-diff').value;
  const filtered = (_quizData || []).filter(item => {
    const matchQ = !q || (item.question_text||'').toLowerCase().includes(q);
    const matchT = !topic || item.topic === topic;
    const matchD = !diff  || item.difficulty === diff;
    return matchQ && matchT && matchD;
  });
  renderQuizTable(filtered);
}

const _QTYPE_LABEL = {
  multiple_choice:  'Multiple Choice',
  multiple_answer:  'Multiple Answer',
  true_false:       'True / False',
  identification:   'Identification',
  scenario_based:   'Scenario-Based',
};

function qtypeBadge(qt) {
  const colors = {
    multiple_choice: 'background:rgba(99,102,241,.15);color:#818cf8',
    multiple_answer: 'background:rgba(59,130,246,.15);color:#60a5fa',
    true_false:      'background:rgba(16,185,129,.15);color:#34d399',
    identification:  'background:rgba(245,158,11,.15);color:#fbbf24',
    scenario_based:  'background:rgba(236,72,153,.15);color:#f472b6',
  };
  const style = colors[qt] || 'background:rgba(156,163,175,.15);color:#9ca3af';
  return `<span style="font-size:.68rem;padding:.2rem .45rem;border-radius:4px;font-weight:500;white-space:nowrap;${style}">${_QTYPE_LABEL[qt] || qt || 'MC'}</span>`;
}

function renderQuizTable(data) {
  if (!data.length) {
    document.getElementById('quiz-table-body').innerHTML = '<tr><td colspan="5"><div class="empty-state"><p>No questions found.</p></div></td></tr>';
    return;
  }
  document.getElementById('quiz-table-body').innerHTML = data.map(q => {
    const isActive = q.is_active !== false && q.is_active !== 0;
    return `<tr style="${isActive ? '' : 'opacity:.55;'}">
      <td style="max-width:280px"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.82rem">${escHtml(q.question_text)}</div>${q.lesson_title ? `<div class="td-muted" style="font-size:.7rem;margin-top:2px">${escHtml(q.lesson_title)}</div>` : ''}${!isActive ? '<div style="font-size:.68rem;color:var(--muted);margin-top:2px">⏸ Deactivated</div>' : ''}</td>
      <td>${topicBadge(q.topic)}</td>
      <td>${diffBadge(q.difficulty)}</td>
      <td>${qtypeBadge(q.question_type)}</td>
      <td>
        <div style="display:flex;gap:.35rem">
          <button class="btn btn-sm btn-icon" title="Edit" onclick='editQuestion(${q.id})'><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
          <button class="btn btn-sm btn-icon" title="${isActive ? 'Deactivate' : 'Activate'}" style="color:${isActive ? 'var(--muted)' : 'var(--green)'}" onclick="toggleQuizQuestion(${q.id}, ${!isActive})">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px">
              <path d="${isActive ? 'M18 6L6 18M6 6l12 12' : 'M20 6L9 17l-5-5'}"/>
            </svg>
          </button>
          <button class="btn btn-sm btn-icon btn-danger" title="Delete" onclick="confirmDeleteQuestion(${q.id}, '${escAttr(q.question_text.slice(0,50))}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

// Quiz modal
let _optionCount = 0;
let _correctIndex = 0;
let _correctIndices = new Set(); // for multiple_answer

function _currentQType() {
  return (document.getElementById('qm-question-type') || {}).value || 'multiple_choice';
}

function onQuestionTypeChange() {
  const qt = _currentQType();
  const isIdent    = qt === 'identification';
  const isTF       = qt === 'true_false';
  const isScenario = qt === 'scenario_based';
  const hasOptions = !isIdent;

  document.getElementById('qm-options-wrap').style.display       = hasOptions  ? '' : 'none';
  document.getElementById('qm-identification-wrap').style.display = isIdent     ? '' : 'none';
  document.getElementById('qm-scenario-wrap').style.display       = isScenario  ? '' : 'none';

  const addBtn = document.getElementById('qm-add-option-btn');
  if (addBtn) addBtn.style.display = (isTF || isIdent) ? 'none' : '';

  if (isTF) {
    _optionCount = 0; _correctIndex = 0; _correctIndices = new Set();
    document.getElementById('qm-options-list').innerHTML = '';
    _addOptionRaw('True');
    _addOptionRaw('False');
  } else if (isIdent) {
    document.getElementById('qm-options-list').innerHTML = '';
    _optionCount = 0;
  }
  // Re-render option inputs with correct input type (radio vs checkbox)
  _refreshOptionControls();
}

function _refreshOptionControls() {
  const qt = _currentQType();
  const isMultiAnswer = qt === 'multiple_answer';
  document.querySelectorAll('.option-radio').forEach((ctrl, i) => {
    if (isMultiAnswer) {
      ctrl.type = 'checkbox';
      ctrl.name = '';
      ctrl.checked = _correctIndices.has(i);
      ctrl.onchange = () => {
        if (ctrl.checked) _correctIndices.add(i); else _correctIndices.delete(i);
      };
    } else {
      ctrl.type = 'radio';
      ctrl.name = 'correct-option';
      ctrl.checked = i === _correctIndex;
      ctrl.onchange = () => { _correctIndex = i; };
    }
  });
}

async function openQuizModal(data = null) {
  document.getElementById('quiz-edit-id').value = '';
  document.getElementById('quiz-modal-title').textContent = 'New quiz question';
  document.getElementById('qm-text').value = '';
  document.getElementById('qm-explanation').value = '';
  if (document.getElementById('qm-hint')) document.getElementById('qm-hint').value = '';
  document.getElementById('qm-image').value = '';
  document.getElementById('qm-image-preview').style.display = 'none';
  document.getElementById('qm-topic').value = '';
  document.getElementById('qm-difficulty').value = 'beginner';
  document.getElementById('qm-lesson').value = '';
  document.getElementById('qm-question-type').value = 'multiple_choice';
  document.getElementById('qm-scenario').value = '';
  document.getElementById('qm-correct-answer').value = '';
  document.getElementById('quiz-modal-error').textContent = '';
  _optionCount = 0; _correctIndex = 0; _correctIndices = new Set();
  document.getElementById('qm-options-list').innerHTML = '';
  await _refreshTopics(); // ensure select is populated before setting value
  await _refreshMindmapNodes();
  onQuestionTypeChange(); // sets up panels
  if (!data) { addOption(''); addOption(''); }
  if (data) populateQuizModal(data);
  openModal('quiz-modal');
}

function populateQuizModal(q) {
  document.getElementById('quiz-edit-id').value = q.id;
  document.getElementById('quiz-modal-title').textContent = 'Edit question';
  document.getElementById('qm-text').value = q.question_text;
  document.getElementById('qm-explanation').value = q.explanation || '';
  document.getElementById('qm-hint').value = q.hint || '';
  document.getElementById('qm-topic').value = q.topic;
  document.getElementById('qm-difficulty').value = q.difficulty;
  document.getElementById('qm-lesson').value = q.lesson_id || '';
  document.getElementById('qm-question-type').value = q.question_type || 'multiple_choice';
  document.getElementById('qm-scenario').value = q.scenario_text || '';
  document.getElementById('qm-correct-answer').value = q.correct_answer || '';
  // Mindmap node — set after _refreshMindmapNodes has populated the select
  setTimeout(() => {
    const nmEl = document.getElementById('qm-mindmap-node');
    if (nmEl) nmEl.value = q.mindmap_node_id || '';
  }, 60);

  const imgVal = q.image_url || '';
  document.getElementById('qm-image').value = imgVal;
  const prev  = document.getElementById('qm-image-preview');
  const thumb = document.getElementById('qm-image-thumb');
  if (imgVal) { thumb.src = imgVal; prev.style.display = 'block'; } else { prev.style.display = 'none'; }

  _optionCount = 0; _correctIndex = q.correct_index || 0;
  _correctIndices = new Set(Array.isArray(q.correct_indices) ? q.correct_indices : []);
  document.getElementById('qm-options-list').innerHTML = '';

  onQuestionTypeChange(); // set up panels before adding options

  const qt = q.question_type || 'multiple_choice';
  if (qt !== 'identification') {
    (q.options || []).forEach(o => _addOptionRaw(o));
    _refreshOptionControls();
  }
}

function _addOptionRaw(val) {
  const qt  = _currentQType();
  const isMA = qt === 'multiple_answer';
  const idx  = _optionCount++;
  const li   = document.createElement('div');
  li.className = 'option-row';
  li.dataset.idx = idx;
  // Note: no letter label — answer identity is by value, not by letter position
  li.innerHTML = `
    <input class="form-input" style="flex:1" value="${escAttr(val)}" placeholder="Option text…" id="opt-${idx}">
    <input type="${isMA ? 'checkbox' : 'radio'}" class="option-radio" name="${isMA ? '' : 'correct-option'}" value="${idx}"
      ${isMA ? (_correctIndices.has(idx) ? 'checked' : '') : (idx === _correctIndex ? 'checked' : '')}
      title="${isMA ? 'Mark as one of the correct answers' : 'Mark as correct answer'}"
      onchange="${isMA ? `if(this.checked)_correctIndices.add(${idx});else _correctIndices.delete(${idx})` : `_correctIndex=${idx}`}">
    <button type="button" class="btn btn-sm btn-icon btn-danger" title="Remove option"
      onclick="this.closest('.option-row').remove()" style="padding:.2rem .35rem">✕</button>
  `;
  document.getElementById('qm-options-list').appendChild(li);
}

function addOption(val = '') {
  const qt = _currentQType();
  if (qt === 'true_false') return; // fixed options
  _addOptionRaw(val);
}

function previewQuestion() {
  const qt      = _currentQType();
  const text    = document.getElementById('qm-text').value.trim();
  const options = qt === 'identification' ? [] : getOptions();
  const exp     = document.getElementById('qm-explanation').value.trim();
  const imgUrl  = document.getElementById('qm-image').value.trim();
  const scenario = document.getElementById('qm-scenario').value.trim();
  if (!text) { adminToast('Fill in the question text first.', 'error'); return; }
  if (qt !== 'identification' && options.length < 2) { adminToast('Add at least 2 options first.', 'error'); return; }

  const correctAnswer = document.getElementById('qm-correct-answer').value.trim();

  let optionsHtml = '';
  if (qt === 'identification') {
    optionsHtml = `<div style="margin:.5rem 0;font-size:.8rem;color:var(--muted)">Type-in answer: <strong>${escHtml(correctAnswer || '(not set)')}</strong></div>`;
  } else if (qt === 'multiple_answer') {
    optionsHtml = `<ul class="preview-options">${options.map((o, i) => {
      const ck = _correctIndices.has(i);
      return `<li class="${ck ? 'correct' : ''}">${ck ? '<svg class="check-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>' : `<span style="display:inline-block;width:14px;height:14px;border:1px solid var(--border);border-radius:3px;flex-shrink:0"></span>`} ${escHtml(o)}</li>`;
    }).join('')}</ul>`;
  } else {
    optionsHtml = `<ul class="preview-options">${options.map((o, i) => {
      const ck = i === _correctIndex;
      return `<li class="${ck ? 'correct' : ''}">${ck ? '<svg class="check-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>' : `<span style="display:inline-block;width:14px;height:14px;border:1px solid var(--border);border-radius:3px;flex-shrink:0"></span>`} ${escHtml(o)}</li>`;
    }).join('')}</ul>`;
  }

  // Render media (image, PDF, or video) based on file extension
  let mediaHtml = '';
  if (imgUrl) {
    const lower = imgUrl.toLowerCase();
    if (lower.endsWith('.pdf')) {
      mediaHtml = `<iframe src="${escHtml(imgUrl)}" style="width:100%;height:280px;border:1px solid var(--border);border-radius:8px;margin-bottom:.75rem;" loading="lazy" title="PDF attachment"></iframe>`;
    } else if (lower.match(/\.(mp4|webm|mov|avi)$/)) {
      mediaHtml = `<video controls style="width:100%;max-height:200px;border-radius:8px;margin-bottom:.75rem;border:1px solid var(--border);"><source src="${escHtml(imgUrl)}"></video>`;
    } else {
      mediaHtml = `<img src="${escHtml(imgUrl)}" alt="question media" style="width:100%;max-height:200px;object-fit:cover;border-radius:8px;margin-bottom:.75rem;border:1px solid var(--border);" onerror="this.style.display='none'">`;
    }
  }

  document.getElementById('preview-content').innerHTML = `
    <div class="preview-box">
      ${mediaHtml}
      ${scenario ? `<div style="font-size:.78rem;color:var(--muted);background:rgba(255,255,255,.04);border-left:3px solid var(--accent);padding:.6rem .75rem;border-radius:0 8px 8px 0;margin-bottom:.75rem;line-height:1.55">${escHtml(scenario)}</div>` : ''}
      <div style="font-size:.88rem;font-weight:500;line-height:1.5;margin-bottom:.75rem">${escHtml(text)}</div>
      ${optionsHtml}
      ${exp ? `<div style="margin-top:.75rem;padding:.6rem .75rem;background:rgba(255,255,255,.03);border-radius:8px;font-size:.78rem;color:var(--muted)">${escHtml(exp)}</div>` : ''}
    </div>`;
  openModal('preview-modal');
}

function getOptions() {
  return Array.from(document.querySelectorAll('.option-row')).map(r => {
    const idx = r.dataset.idx;
    return (document.getElementById('opt-' + idx) || {}).value?.trim() || '';
  }).filter(Boolean);
}

async function saveQuestion() {
  const editId  = document.getElementById('quiz-edit-id').value;
  const qt      = _currentQType();
  const text    = document.getElementById('qm-text').value.trim();
  const options = qt === 'identification' ? [] : getOptions();
  const errEl   = document.getElementById('quiz-modal-error');
  errEl.textContent = '';

  if (!text) { errEl.textContent = 'Question text is required.'; return; }
  const topicVal = document.getElementById('qm-topic').value.trim();
  if (!topicVal) { errEl.textContent = 'Topic is required.'; return; }

  if (qt === 'identification') {
    const ca = document.getElementById('qm-correct-answer').value.trim();
    if (!ca) { errEl.textContent = 'Correct answer is required for Identification questions.'; return; }
  } else {
    if (options.length < 2) { errEl.textContent = 'At least 2 options required.'; return; }
    if (qt === 'multiple_answer') {
      if (_correctIndices.size === 0) { errEl.textContent = 'Select at least one correct answer.'; return; }
    } else {
      if (_correctIndex >= options.length) { errEl.textContent = 'Select a valid correct answer.'; return; }
    }
  }

  const body = {
    question_text:   text,
    question_type:   qt,
    options:         options,
    correct_index:   qt === 'multiple_answer' || qt === 'identification' ? 0 : _correctIndex,
    correct_indices: qt === 'multiple_answer' ? Array.from(_correctIndices) : null,
    correct_answer:  qt === 'identification' ? document.getElementById('qm-correct-answer').value.trim() : null,
    scenario_text:   qt === 'scenario_based'  ? document.getElementById('qm-scenario').value.trim() || null : null,
    topic:           topicVal,
    difficulty:      document.getElementById('qm-difficulty').value,
    explanation:     document.getElementById('qm-explanation').value.trim() || null,
    hint:            document.getElementById('qm-hint')?.value.trim() || null,
    lesson_id:       parseInt(document.getElementById('qm-lesson').value) || null,
    image_url:       document.getElementById('qm-image').value.trim() || null,
    media_type:      document.getElementById('qm-image').value.trim() ? 'image' : (document.getElementById('qm-video')?.value.trim() ? 'video' : 'text'),
    mindmap_node_id: document.getElementById('qm-mindmap-node')?.value || null,
  };
  try {
    if (editId) {
      await apiFetch(`/admin/quiz/questions/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
      adminToast('Question updated.');
    } else {
      await apiFetch('/admin/quiz/questions', { method: 'POST', body: JSON.stringify(body) });
      adminToast('Question created.');
    }
    closeModal('quiz-modal');
    await loadQuiz();
  } catch(e) { errEl.textContent = e.message; }
}

function editQuestion(id) {
  const q = _quizData.find(x => x.id === id);
  if (q) openQuizModal(q);
}

function confirmDeleteQuestion(id, preview) {
  document.getElementById('confirm-title').textContent = 'Delete question';
  document.getElementById('confirm-body').innerHTML = `This will permanently delete the question: <strong>"${escHtml(preview)}…"</strong> and all its attempt records.`;
  document.getElementById('confirm-ok-btn').onclick = async () => {
    try {
      await apiFetch(`/admin/quiz/questions/${id}`, { method: 'DELETE' });
      adminToast('Question deleted.');
      closeModal('confirm-modal');
      await loadQuiz();
    } catch(e) { adminToast(e.message, 'error'); }
  };
  openModal('confirm-modal');
}

async function toggleQuizQuestion(id, activate) {
  try {
    const res = await fetch(`/quiz/questions/${id}/toggle-active`, {
      method: 'PATCH', credentials: 'include',
    });
    if (!res.ok) throw new Error(`Error ${res.status}`);
    adminToast(activate ? 'Question activated.' : 'Question deactivated.');
    await loadQuiz();
  } catch(e) { adminToast(e.message || 'Could not update question.', 'error'); }
}

// ── Shared media renderer (mirrors _renderQuestionMedia in lessons.js) ────────
function _adminRenderMedia(imageUrl) {
  if (!imageUrl) return '';
  const lower = imageUrl.toLowerCase();
  if (lower.endsWith('.pdf'))
    return `<iframe src="${escHtml(imageUrl)}" style="width:100%;height:280px;border:1px solid var(--border);border-radius:10px;margin-bottom:.85rem;" loading="lazy" title="PDF attachment"></iframe>`;
  if (lower.match(/\.(mp4|webm|mov|avi)$/))
    return `<video controls style="width:100%;max-height:220px;border-radius:10px;margin-bottom:.85rem;border:1px solid var(--border);"><source src="${escHtml(imageUrl)}"><p style="font-size:.8rem;color:var(--muted)">Your browser doesn't support video. <a href="${escHtml(imageUrl)}" target="_blank">Download</a></p></video>`;
  return `<img src="${escHtml(imageUrl)}" alt="question media" style="width:100%;max-height:220px;object-fit:cover;border-radius:10px;margin-bottom:.85rem;border:1px solid var(--border);" onerror="this.style.display='none'">`;
}

async function viewQuestionStats(id) {
  document.getElementById('stats-modal-content').innerHTML = '<div class="loading">Loading…</div>';
  openModal('stats-modal');
  try {
    const d = await apiFetch(`/admin/quiz/questions/${id}/stats`);
    const max = Math.max(...d.option_breakdown.map(o => o.count), 1);
    document.getElementById('stats-modal-content').innerHTML = `
      <div class="preview-box" style="margin-bottom:1rem">
        ${_adminRenderMedia(d.image_url)}
        <div style="font-size:.85rem;font-weight:500">${escHtml(d.question_text)}</div>
        <div style="display:flex;gap:.5rem;margin-top:.5rem">${topicBadge(d.topic)} ${diffBadge(d.difficulty)}</div>
      </div>
      <div class="stat-grid" style="margin-bottom:1.25rem">
        ${statCard('Total attempts', d.total_attempts, '')}
        ${statCard('Correct', d.correct_count, '')}
        ${statCard('Accuracy', (d.accuracy_pct || 0) + '%', '')}
        ${statCard('Unique users', d.unique_users, '')}
      </div>
      <div style="font-family:'DM Mono',monospace;font-size:.68rem;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.75rem">Answer distribution</div>
      ${d.option_breakdown.map(o => `
        <div style="margin-bottom:.65rem">
          <div style="display:flex;justify-content:space-between;font-size:.8rem;margin-bottom:.25rem">
            <span style="${o.is_correct ? 'color:var(--green)' : ''}">${escHtml(o.text)} ${o.is_correct ? '✓' : ''}</span>
            <span style="font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted)">${o.count} (${o.pct}%)</span>
          </div>
          <div class="progress-bar"><div class="progress-fill ${o.is_correct ? 'fill-green' : 'fill-blue'}" style="width:${Math.round(o.count / max * 100)}%"></div></div>
        </div>`).join('')}`;
  } catch(e) { document.getElementById('stats-modal-content').innerHTML = `<p style="color:var(--red);font-size:.82rem">${e.message}</p>`; }
}

// ── LESSONS ────────────────────────────────────────────────────────────────
async function loadLessons() {
  await _refreshTopics();
  try {
    _lessonsData = await apiFetch('/admin/lessons');
    // Populate topic filter dropdown
    const topicSel = document.getElementById('lesson-filter-topic');
    if (topicSel) {
      const seen = new Set();
      const opts = ['<option value="">All topics</option>'];
      (_lessonsData||[]).forEach(l => {
        if (l.topic && !seen.has(l.topic)) {
          seen.add(l.topic);
          const tp = _dashTopicRegistry[l.topic];
          opts.push(`<option value="${escAttr(l.topic)}">${escHtml(tp ? (tp.icon+' '+tp.label) : l.topic)}</option>`);
        }
      });
      topicSel.innerHTML = opts.join('');
    }
    renderLessonsTable(_lessonsData);
  } catch(e) {
    document.getElementById('lessons-table-body').innerHTML = `<tr><td colspan="6" style="color:var(--red);text-align:center;padding:1rem;font-size:.8rem">${e.message}</td></tr>`;
  }
}

function filterLessons() {
  const q = (document.getElementById('lesson-search')?.value || '').toLowerCase().trim();
  const topic = document.getElementById('lesson-filter-topic')?.value || '';
  const status = document.getElementById('lesson-filter-status')?.value || '';
  const filtered = (_lessonsData || []).filter(item => {
    const matchQ = !q || (item.title||'').toLowerCase().includes(q) || (item.lesson_key||'').toLowerCase().includes(q);
    const matchT = !topic || item.topic === topic;
    const matchS = !status || (status === 'published' ? !!item.is_published : !item.is_published);
    return matchQ && matchT && matchS;
  });
  renderLessonsTable(filtered);
}

function renderLessonsTable(data) {
  if (!data.length) { document.getElementById('lessons-table-body').innerHTML = '<tr><td colspan="6"><div class="empty-state"><p>No lessons yet.</p></div></td></tr>'; return; }
  document.getElementById('lessons-table-body').innerHTML = data.map(l => {
    return `<tr>
      <td style="width:44px;text-align:center"><span style="font-size:.68rem;padding:.15rem .4rem;border-radius:4px;background:var(--surface2);color:var(--muted);font-family:monospace">#${l.id}</span></td>
      <td style="max-width:220px"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.82rem">${escHtml(l.title)}</div></td>
      <td>${topicBadge(l.topic)}</td>
      <td>${diffBadge(l.difficulty)}</td>
      <td><button onclick="toggleLessonPublished(${l.id}, ${!!l.is_published})" title="${l.is_published ? 'Click to unpublish' : 'Click to publish'}" style="display:inline-flex;align-items:center;gap:.35rem;padding:.25rem .6rem;border-radius:20px;font-size:.72rem;font-family:'DM Mono',monospace;cursor:pointer;border:1px solid ${l.is_published ? 'rgba(52,211,153,.3)' : 'rgba(255,255,255,.1)'};background:${l.is_published ? 'rgba(52,211,153,.12)' : 'rgba(255,255,255,.05)'};color:${l.is_published ? 'var(--green)' : 'var(--muted)'};transition:all .15s"><span style="width:22px;height:12px;border-radius:6px;background:${l.is_published ? 'var(--green)' : 'rgba(255,255,255,.18)'};display:inline-block;position:relative;flex-shrink:0"><span style="position:absolute;top:2px;left:${l.is_published ? '12px' : '2px'};width:8px;height:8px;border-radius:50%;background:#fff;transition:left .15s"></span></span>${l.is_published ? 'Published' : 'Draft'}</button></td>
      <td style="padding:.4rem .6rem;white-space:nowrap">
        <div style="display:inline-flex;gap:.3rem;align-items:center;flex-wrap:nowrap">
          <button class="btn btn-sm btn-icon" title="View" onclick="previewLesson(${l.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
          <button class="btn btn-sm btn-icon" title="Edit" onclick='editLesson(${l.id})'><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
          <button class="btn btn-sm btn-icon btn-danger" title="Delete" onclick="confirmDeleteLesson(${l.id}, '${escAttr(l.title)}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function lmAutoKey() {
  const editId = document.getElementById('lm-edit-id').value;
  if (editId) return; // never overwrite key on edit
  const title = document.getElementById('lm-title').value;
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 80);
  document.getElementById('lm-key').value = slug;
  const vis = document.getElementById('lm-key-visible');
  if (vis) vis.value = slug;
  const row = document.getElementById('lm-key-row');
  if (row) row.style.display = slug ? '' : 'none';
}

async function openLessonModal(data = null) {
  document.getElementById('lm-edit-id').value = '';
  document.getElementById('lesson-modal-title').textContent = 'New lesson';
  ['lm-key','lm-title','lm-content','lm-milskill','lm-sort','lm-image'].forEach(id => document.getElementById(id).value = '');
  const kvis = document.getElementById('lm-key-visible');
  if (kvis) kvis.value = '';
  const krow = document.getElementById('lm-key-row');
  if (krow) krow.style.display = 'none';
  document.getElementById('lm-image-preview').style.display = 'none';
  document.getElementById('lm-topic').value = '';
  document.getElementById('lm-difficulty').value = 'beginner';
  document.getElementById('lm-published').checked = true;
  document.getElementById('lesson-modal-error').textContent = '';
  // Refresh topic select — must await so options exist before setting value
  await _refreshTopics();
  await _refreshMindmapNodes();
  if (data) {
    document.getElementById('lm-edit-id').value = data.id;
    document.getElementById('lesson-modal-title').textContent = 'Edit lesson';
    document.getElementById('lm-key').value = data.lesson_key || '';
    if (kvis) kvis.value = data.lesson_key || '';
    if (krow) krow.style.display = data.lesson_key ? '' : 'none';
    document.getElementById('lm-title').value = data.title || '';
    document.getElementById('lm-content').value = data.content || '';
    document.getElementById('lm-milskill').value = data.mil_skill || '';
    document.getElementById('lm-sort').value = data.sort_order ?? '';
    document.getElementById('lm-topic').value = data.topic || 'general';
    document.getElementById('lm-difficulty').value = data.difficulty || 'beginner';
    document.getElementById('lm-published').checked = data.is_published !== 0 && data.is_published !== false;
    // Mindmap node
    setTimeout(() => {
      const nmEl = document.getElementById('lm-mindmap-node');
      if (nmEl) nmEl.value = data.mindmap_node_id || '';
    }, 60);
    // Image — optional, never blocks save
    const imgVal = data.image_url || '';
    document.getElementById('lm-image').value = imgVal;
    const prev = document.getElementById('lm-image-preview');
    const thumb = document.getElementById('lm-image-thumb');
    if (imgVal && prev && thumb) { thumb.src = imgVal; prev.style.display = 'block'; } else if (prev) { prev.style.display = 'none'; }
  }
  openModal('lesson-modal');
}

function editLesson(id) {
  const l = _lessonsData.find(x => x.id === id);
  if (l) openLessonModal(l);
}

function previewLesson(id) {
  const l = _lessonsData.find(x => x.id === id);
  if (!l) return;
  document.querySelector('#preview-modal .modal-title').childNodes[0].textContent = l.title + ' ';
  document.getElementById('preview-content').innerHTML = `
    <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.75rem;">
      ${topicBadge(l.topic)} ${diffBadge(l.difficulty)}
    </div>
    <div style="white-space:pre-wrap;font-size:.83rem;line-height:1.65;color:var(--text);max-height:60vh;overflow-y:auto;">${escHtml(l.content)}</div>
  `;
  openModal('preview-modal');
}

async function saveLesson() {
  const editId  = document.getElementById('lm-edit-id').value;
  const errEl   = document.getElementById('lesson-modal-error');
  errEl.textContent = '';
  const topicVal = document.getElementById('lm-topic').value.trim();
  if (!topicVal) { errEl.textContent = 'Topic is required.'; return; }
  const titleVal = document.getElementById('lm-title').value.trim();
  if (!titleVal) { errEl.textContent = 'Title is required.'; return; }
  // Auto-generate internal key from title if not already set (new lessons only)
  let keyVal = document.getElementById('lm-key').value.trim();
  if (!keyVal && !editId) {
    keyVal = titleVal.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 80);
    document.getElementById('lm-key').value = keyVal;
  }
  const body = {
    lesson_key:  keyVal,
    title:       titleVal,
    content:     document.getElementById('lm-content').value.trim(),
    topic:       topicVal,
    difficulty:  document.getElementById('lm-difficulty').value,
    mil_skill:   document.getElementById('lm-milskill').value.trim() || null,
    sort_order:  parseInt(document.getElementById('lm-sort').value) || null,
    image_url:   document.getElementById('lm-image').value.trim() || null,
    is_published: document.getElementById('lm-published').checked,
    mindmap_node_id: document.getElementById('lm-mindmap-node')?.value || null,
  };
  if (!body.content) { errEl.textContent = 'Content is required.'; return; }
  try {
    if (editId) {
      await apiFetch(`/admin/lessons/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
      adminToast('Lesson updated.');
    } else {
      await apiFetch('/admin/lessons', { method: 'POST', body: JSON.stringify(body) });
      adminToast('Lesson created.');
    }
    closeModal('lesson-modal');
    await loadLessons();
    await _refreshTopics();
  } catch(e) { errEl.textContent = e.message; }
}

function confirmDeleteLesson(id, title) {
  document.getElementById('confirm-title').textContent = 'Delete lesson';
  document.getElementById('confirm-body').innerHTML =
    `Permanently delete <strong>"${escHtml(title)}"</strong>?<br><span style="font-size:.8rem;color:var(--muted)">If quiz questions are linked to it, first unlink them in the Quiz panel (set Lesson → "no lesson"), then delete here.</span>`;
  document.getElementById('confirm-ok-btn').onclick = async () => {
    try {
      await apiFetch(`/admin/lessons/${id}`, { method: 'DELETE' });
      adminToast('Lesson deleted.');
      closeModal('confirm-modal');
      await loadLessons();
      await _refreshTopics();
    } catch(e) {
      closeModal('confirm-modal');
      adminToast(e.message, 'error');
    }
  };
  openModal('confirm-modal');
}

async function toggleLessonPublished(id, currentPublished) {
  try {
    await fetch(`/lessons/${id}/toggle-published`, { method: 'PATCH', credentials: 'include' });
    adminToast(currentPublished ? 'Lesson unpublished.' : 'Lesson published.');
    await loadLessons();
  } catch(e) { adminToast(e.message || 'Could not update lesson.', 'error'); }
}

// ── USERS ──────────────────────────────────────────────────────────────────
async function loadUsers() {
  try {
    _usersData = await apiFetch('/admin/users');
    renderUsersTable(_usersData);
  } catch(e) {
    document.getElementById('users-table-body').innerHTML = `<tr><td colspan="5" style="color:var(--red);text-align:center;padding:1rem;font-size:.8rem">${e.message}</td></tr>`;
  }
}

function filterUsers() {
  const q = document.getElementById('user-search').value.toLowerCase();
  renderUsersTable(_usersData.filter(u => u.username.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)));
}

function renderUsersTable(data) {
  const countEl = document.getElementById('users-count-label');
  if (countEl) countEl.textContent = `${data.length} account${data.length !== 1 ? 's' : ''} registered`;
  if (!data.length) { document.getElementById('users-table-body').innerHTML = '<tr><td colspan="5"><div class="empty-state"><p>No users found.</p></div></td></tr>'; return; }
  const me = localStorage.getItem('sp_user_id');
  document.getElementById('users-table-body').innerHTML = data.map(u => `<tr>
    <td><div style="font-size:.82rem">${escHtml(u.username)}</div><div class="td-muted" style="font-size:.72rem">${escHtml(u.email)}</div></td>
    <td><span class="badge badge-${u.role}">${u.role}</span></td>
    <td class="td-muted td-mono" style="font-size:.72rem">${(u.created_at || '').slice(0,10)}</td>
    <td class="td-muted td-mono" style="font-size:.72rem">${u.last_active_at ? u.last_active_at.slice(0,10) : '—'}</td>
    <td>
      <div style="display:flex;gap:.35rem;align-items:center">
        <button class="btn btn-sm btn-icon" title="Edit user" onclick="openUserModal(${JSON.stringify(u).replace(/"/g,'&quot;')})">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        ${String(u.id) !== String(me) ? `<button class="btn btn-sm btn-icon btn-danger" title="Delete user" onclick="confirmDeleteUser(${u.id}, '${escAttr(u.username)}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg></button>` : ''}
      </div>
    </td>
  </tr>`).join('');
}

async function changeRole(id, role) {
  try {
    await apiFetch(`/admin/users/${id}/role`, { method: 'PUT', body: JSON.stringify({ role }) });
    adminToast(`User role updated to ${role}.`);
    await loadUsers();
  } catch(e) { adminToast(e.message, 'error'); }
}

function confirmDeleteUser(id, username) {
  document.getElementById('confirm-title').textContent = 'Delete user';
  document.getElementById('confirm-body').innerHTML = `This will permanently delete account <strong>"${escHtml(username)}"</strong> and all their data. This cannot be undone.`;
  document.getElementById('confirm-ok-btn').onclick = async () => {
    try {
      await apiFetch(`/admin/users/${id}`, { method: 'DELETE' });
      adminToast('User deleted.');
      closeModal('confirm-modal');
      await loadUsers();
    } catch(e) { adminToast(e.message, 'error'); }
  };
  openModal('confirm-modal');
}

function toggleUserPw() {
  const input  = document.getElementById('um-password');
  const eye    = document.getElementById('um-pw-eye');
  const eyeOff = document.getElementById('um-pw-eye-off');
  const show   = input.type === 'password';
  input.type   = show ? 'text' : 'password';
  eye.style.display    = show ? 'none'  : '';
  eyeOff.style.display = show ? ''      : 'none';
  input.focus();
}

function openUserModal(user) {
  document.getElementById('user-modal-error').textContent = '';
  const isEdit = !!user;
  document.getElementById('user-modal-title').textContent = isEdit ? `Edit user — ${user.username}` : 'New user';
  document.getElementById('um-edit-id').value = isEdit ? user.id : '';
  document.getElementById('um-username').value = isEdit ? (user.username || '') : '';
  document.getElementById('um-email').value    = isEdit ? (user.email || '') : '';
  document.getElementById('um-password').value = '';
  // Always reset to hidden when opening
  document.getElementById('um-password').type = 'password';
  document.getElementById('um-pw-eye').style.display     = '';
  document.getElementById('um-pw-eye-off').style.display = 'none';
  document.getElementById('um-role').value     = isEdit ? (user.role || 'user') : 'user';
  document.getElementById('um-pw-label').textContent = isEdit ? 'New password (leave blank to keep current)' : 'Password';
  document.getElementById('um-pw-hint').textContent  = isEdit ? 'Only fill this if you want to change the password.' : '';
  openModal('user-modal');
}

async function saveUser() {
  const errEl = document.getElementById('user-modal-error');
  errEl.textContent = '';
  const editId   = document.getElementById('um-edit-id').value;
  const username = document.getElementById('um-username').value.trim();
  const email    = document.getElementById('um-email').value.trim();
  const password = document.getElementById('um-password').value;
  const role     = document.getElementById('um-role').value;

  if (!username) { errEl.textContent = 'Username is required.'; return; }
  if (!email)    { errEl.textContent = 'Email is required.'; return; }
  if (!editId && !password) { errEl.textContent = 'Password is required for new accounts.'; return; }
  if (password && password.length < 8) { errEl.textContent = 'Password must be at least 8 characters.'; return; }

  try {
    if (editId) {
      const body = { username, email, role };
      if (password) body.password = password;
      await apiFetch(`/admin/users/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
      adminToast('User updated.');
    } else {
      await apiFetch('/admin/users', { method: 'POST', body: JSON.stringify({ username, email, password, role }) });
      adminToast('User created.');
    }
    closeModal('user-modal');
    await loadUsers();
  } catch(e) { errEl.textContent = e.message; }
}

// ── ANALYTICS ─────────────────────────────────────────────────────────────
async function loadAuditLog(page = 1) {
  const container = document.getElementById('audit-log-content');
  const pagination = document.getElementById('audit-pagination');
  if (!container) return;

  container.innerHTML = '<div class="loading">Loading…</div>';
  if (pagination) pagination.innerHTML = '';

  const search   = (document.getElementById('audit-search')          || {}).value || '';
  const action   = (document.getElementById('audit-filter-action')   || {}).value || '';
  const resource = (document.getElementById('audit-filter-resource') || {}).value || '';
  const perPage  = parseInt((document.getElementById('audit-page-size') || {}).value || '50', 10);

  const qs = new URLSearchParams({ page, per_page: perPage });
  if (search)   qs.set('search', search);
  if (action)   qs.set('action', action);
  if (resource) qs.set('resource_type', resource);

  try {
    const data = await apiFetch(`/admin/audit-log?${qs}`);

    if (!data.rows || !data.rows.length) {
      container.innerHTML = '<p style="text-align:center;color:var(--muted);padding:2rem;">No audit entries found.</p>';
      return;
    }

    const actionBg = {
      create: 'rgba(52,211,153,.15)', update: 'rgba(251,191,36,.12)',
      delete: 'rgba(239,68,68,.12)', role_change: 'rgba(139,92,246,.15)',
      upload: 'rgba(59,130,246,.12)', reorder: 'rgba(255,255,255,.07)',
    };
    const actionFg = {
      create: 'var(--green)', update: '#fbbf24',
      delete: 'var(--red)', role_change: '#a78bfa',
      upload: '#60a5fa', reorder: 'var(--muted)',
    };

    container.innerHTML = `
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:.78rem;">
          <thead>
            <tr style="border-bottom:1px solid var(--border);color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;">
              <th style="padding:.5rem .6rem;text-align:left;white-space:nowrap;">Time (UTC)</th>
              <th style="padding:.5rem .6rem;text-align:left;">Admin</th>
              <th style="padding:.5rem .6rem;text-align:left;">Action</th>
              <th style="padding:.5rem .6rem;text-align:left;">Resource</th>
              <th style="padding:.5rem .6rem;text-align:left;">ID</th>
              <th style="padding:.5rem .6rem;text-align:left;">Detail</th>
            </tr>
          </thead>
          <tbody>
            ${data.rows.map(r => `<tr style="border-bottom:1px solid rgba(255,255,255,.04);">
              <td style="padding:.45rem .6rem;font-family:'DM Mono',monospace;font-size:.7rem;white-space:nowrap;color:var(--muted);">
                ${r.performed_at ? r.performed_at.replace('T',' ').slice(0,16) : '—'}
              </td>
              <td style="padding:.45rem .6rem;">${escHtml(r.admin_username || '—')}</td>
              <td style="padding:.45rem .6rem;">
                <span style="font-size:.7rem;padding:.15rem .5rem;border-radius:12px;
                  background:${actionBg[r.action] || 'rgba(255,255,255,.07)'};
                  color:${actionFg[r.action] || 'var(--text)'};">
                  ${escHtml(r.action)}
                </span>
              </td>
              <td style="padding:.45rem .6rem;">${escHtml(r.resource_type)}</td>
              <td style="padding:.45rem .6rem;font-family:'DM Mono',monospace;font-size:.7rem;">${escHtml(r.resource_id || '—')}</td>
              <td style="padding:.45rem .6rem;color:var(--muted);max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                  title="${escAttr(r.detail || '')}">${escHtml(r.detail || '')}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;

    if (pagination) {
      const totalPages = Math.ceil(data.total / data.per_page);
      pagination.innerHTML = `
        <span>${data.total} entr${data.total === 1 ? 'y' : 'ies'} · page ${data.page} of ${totalPages || 1}</span>
        <div style="display:flex;gap:.4rem;">
          <button class="btn btn-ghost btn-sm" ${page <= 1 ? 'disabled' : ''} onclick="loadAuditLog(${page - 1})">← Prev</button>
          <button class="btn btn-ghost btn-sm" ${page >= totalPages ? 'disabled' : ''} onclick="loadAuditLog(${page + 1})">Next →</button>
        </div>`;
    }
    _populateAuditFilters(data);
  } catch(e) {
    container.innerHTML = `<p style="color:var(--red);text-align:center;padding:1rem;font-size:.8rem;">${escHtml(e.message)}</p>`;
  }
}

// ── Pre/Post-test Claims ───────────────────────────────────────────────────

function _populateAuditFilters(data) {
  const actionSel = document.getElementById('audit-filter-action');
  if (actionSel && actionSel.options.length <= 1) {
    ['create','update','delete','role_change','upload','reorder'].forEach(a => {
      const o = document.createElement('option'); o.value = a; o.textContent = a; actionSel.appendChild(o);
    });
  }
  const resSel = document.getElementById('audit-filter-resource');
  if (resSel && data.resource_types && resSel.options.length <= 1) {
    data.resource_types.forEach(rt => {
      const o = document.createElement('option'); o.value = rt; o.textContent = rt; resSel.appendChild(o);
    });
  }
}

let _pptData = [];

async function loadPrePostTest() {
  document.getElementById('ppt-table-body').innerHTML = '<tr><td colspan="4" class="loading">Loading…</td></tr>';
  try {
    _pptData = await apiFetch('/admin/pretest-claims');
    renderPptTable(_pptData);
  } catch(e) {
    document.getElementById('ppt-table-body').innerHTML = `<tr><td colspan="4" style="color:var(--red);text-align:center;padding:1rem;font-size:.8rem">${e.message}</td></tr>`;
  }
}

function filterPpt() {
  const q = (document.getElementById('ppt-search')?.value || '').toLowerCase().trim();
  const type = document.getElementById('ppt-filter-type')?.value || '';
  const filtered = (_pptData || []).filter(item => {
    const matchQ = !q || (item.text||'').toLowerCase().includes(q);
    const matchT = !type || (item.question_type||'true_false') === type;
    return matchQ && matchT;
  });
  renderPptTable(filtered);
}

function renderPptTable(data) {
  if (!data.length) {
    document.getElementById('ppt-table-body').innerHTML = '<tr><td colspan="5"><div class="empty-state"><p>No questions yet.</p></div></td></tr>';
    return;
  }
  const typeLabels = { true_false:'True / False', multiple_choice:'Multiple Choice', yes_no:'Yes / No', scale:'Scale (1–5)', open:'Open-ended' };
  document.getElementById('ppt-table-body').innerHTML = data.map(c => {
    const qtype = c.question_type || 'true_false';
    const qtLabel = typeLabels[qtype] || qtype;
    let answerPreview = c.correct_answer || '—';
    if (qtype === 'multiple_choice' && c.options) {
      try {
        const opts = JSON.parse(c.options);
        answerPreview = opts[parseInt(c.correct_index)] || answerPreview;
      } catch(_) {}
    }
    return `<tr>
    <td style="font-size:.82rem">${escHtml(c.text)}</td>
    <td><span style="font-size:.7rem;padding:.15rem .45rem;border-radius:12px;background:rgba(255,255,255,.07)">${qtLabel}</span></td>
    <td style="font-size:.78rem;color:var(--muted)">${escHtml(answerPreview)}</td>
    <td style="text-align:center;font-family:'DM Mono',monospace;font-size:.78rem">${c.attempt_count ?? 0}</td>
    <td style="white-space:nowrap">
      <button class="btn btn-sm btn-icon" title="Edit" onclick='openPptModal(${JSON.stringify(c).replace(/'/g,"&#39;")})'><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
      <button class="btn btn-sm btn-icon btn-danger" title="Delete" onclick="confirmDeletePpt(${c.id},'${escAttr(c.text.slice(0,40))}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button>
    </td>
  </tr>`;
  }).join('');
}

function openPptModal(data = null) {
  document.getElementById('ppt-edit-id').value = data ? data.id : '';
  document.getElementById('ppt-modal-title').textContent = data ? 'Edit question' : 'New question';
  document.getElementById('ppt-text').value = data ? data.text : '';
  const qtype = data?.question_type || 'true_false';
  document.getElementById('ppt-question-type').value = qtype;
  document.getElementById('ppt-modal-error').textContent = '';
  // Image URL
  const imgInput = document.getElementById('ppt-image-url');
  const imgPreview = document.getElementById('ppt-image-preview');
  const imgEl = document.getElementById('ppt-image-preview-img');
  if (imgInput) imgInput.value = data?.image_url || '';
  if (imgPreview && imgEl) {
    if (data?.image_url) { imgEl.src = data.image_url; imgPreview.style.display = ''; }
    else { imgPreview.style.display = 'none'; imgEl.src = ''; }
  }
  _pptToggleAnswerFields(qtype, data);
  openModal('ppt-modal');
}

function _pptToggleAnswerFields(qtype, data) {
  const tfWrap  = document.getElementById('ppt-tf-wrap');
  const ynWrap  = document.getElementById('ppt-yn-wrap');
  const mcWrap  = document.getElementById('ppt-mc-wrap');
  const scWrap  = document.getElementById('ppt-scale-wrap');
  const openWrap= document.getElementById('ppt-open-wrap');
  [tfWrap, ynWrap, mcWrap, scWrap, openWrap].forEach(el => { if (el) el.style.display = 'none'; });

  if (qtype === 'true_false' && tfWrap) {
    tfWrap.style.display = '';
    const sel = document.getElementById('ppt-answer');
    if (sel && data) sel.value = data.correct_answer || 'True';
  } else if (qtype === 'yes_no' && ynWrap) {
    ynWrap.style.display = '';
    const sel = document.getElementById('ppt-yn-answer');
    if (sel && data) sel.value = data.correct_answer || 'Yes';
  } else if (qtype === 'multiple_choice' && mcWrap) {
    mcWrap.style.display = '';
    const optEl = document.getElementById('ppt-mc-options');
    const idxEl = document.getElementById('ppt-mc-correct-index');
    if (optEl && data?.options) {
      try { optEl.value = JSON.parse(data.options).join('\n'); } catch(_) { optEl.value = ''; }
    } else if (optEl) { optEl.value = ''; }
    if (idxEl && data) idxEl.value = data.correct_index ?? 0;
  } else if (qtype === 'scale' && scWrap) {
    scWrap.style.display = '';
  } else if (qtype === 'open' && openWrap) {
    openWrap.style.display = '';
  }
}

async function saveClaim() {
  const editId = document.getElementById('ppt-edit-id').value;
  const text   = document.getElementById('ppt-text').value.trim();
  const qtype  = document.getElementById('ppt-question-type').value;
  const errEl  = document.getElementById('ppt-modal-error');
  if (!text) { errEl.textContent = 'Question text is required.'; return; }

  let body = { text, question_type: qtype, image_url: (document.getElementById('ppt-image-url')?.value.trim() || null) };

  if (qtype === 'true_false') {
    body.correct_answer = document.getElementById('ppt-answer').value;
    body.correct_index  = body.correct_answer === 'True' ? 1 : 0;
  } else if (qtype === 'yes_no') {
    body.correct_answer = document.getElementById('ppt-yn-answer').value;
    body.correct_index  = ['Yes','No','Unsure'].indexOf(body.correct_answer);
  } else if (qtype === 'multiple_choice') {
    const optsRaw = (document.getElementById('ppt-mc-options')?.value || '').trim();
    if (!optsRaw) { errEl.textContent = 'Add at least one option.'; return; }
    const opts = optsRaw.split('\n').map(s => s.trim()).filter(Boolean);
    body.options = JSON.stringify(opts);
    body.correct_index = parseInt(document.getElementById('ppt-mc-correct-index')?.value) || 0;
    body.correct_answer = opts[body.correct_index] || opts[0];
  } else if (qtype === 'scale') {
    body.correct_answer = null;
    body.correct_index  = -1;
  } else {
    body.correct_answer = null;
    body.correct_index  = -1;
  }

  try {
    if (editId) {
      await apiFetch(`/admin/pretest-claims/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
      adminToast('Question updated.');
    } else {
      await apiFetch('/admin/pretest-claims', { method: 'POST', body: JSON.stringify(body) });
      adminToast('Question created.');
    }
    closeModal('ppt-modal');
    await loadPrePostTest();
  } catch(e) { errEl.textContent = e.message; }
}

function confirmDeletePpt(id, preview) {
  document.getElementById('confirm-title').textContent = 'Delete claim';
  document.getElementById('confirm-body').innerHTML = `Permanently delete claim: <strong>"${escHtml(preview)}…"</strong>?`;
  document.getElementById('confirm-ok-btn').onclick = async () => {
    try {
      await apiFetch(`/admin/pretest-claims/${id}`, { method: 'DELETE' });
      adminToast('Claim deleted.');
      closeModal('confirm-modal');
      await loadPrePostTest();
    } catch(e) { closeModal('confirm-modal'); adminToast(e.message, 'error'); }
  };
  openModal('confirm-modal');
}

// ── Eval Questions ─────────────────────────────────────────────────────────

let _evalQData = [];

function _injectEvalQModal() {
  if (document.getElementById('eval-q-modal')) return;
  const modal = document.createElement('div');
  modal.id = 'eval-q-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `<div class="modal" style="max-width:640px;max-height:90vh;overflow-y:auto;">
    <div class="modal-title"><span id="eq-modal-title">New eval question</span><button class="modal-close" onclick="closeModal('eval-q-modal')">&#x2715;</button></div>
    <div>
      <input type="hidden" id="eq-edit-id">

      <div style="display:grid;grid-template-columns:80px 1fr;gap:.75rem;align-items:start;margin-bottom:.75rem;">
        <div>
          <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Step #</label>
          <input type="number" id="eq-step" min="1" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;color:var(--text);font-size:.83rem">
        </div>
        <div>
          <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Title</label>
          <input type="text" id="eq-title" placeholder="Short label shown in stepper…" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.55rem .8rem;color:var(--text);font-size:.83rem">
        </div>
      </div>

      <div style="margin-bottom:.75rem;">
        <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Progress Bar Label <span style="color:var(--muted);font-weight:400">(optional — overrides auto-generated label in the step tracker)</span></label>
        <input type="text" id="eq-step-label" placeholder="e.g. Content Type, Headline, Sources…" maxlength="80" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.55rem .8rem;color:var(--text);font-size:.83rem">
      </div>

      <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Question / Prompt</label>
      <textarea id="eq-prompt" rows="3" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.6rem .8rem;color:var(--text);font-size:.83rem;resize:vertical;margin-bottom:.75rem"></textarea>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-bottom:.75rem;">
        <div>
          <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Input type</label>
          <select id="eq-input-type" onchange="eqToggleOptions()" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.55rem .8rem;color:var(--text);font-size:.83rem">
            <option value="text">Text (open-ended)</option>
            <option value="radio">Radio (single choice)</option>
            <option value="checkbox">Checkbox (multi-select)</option>
            <option value="scale">Scale (1–5 slider)</option>
            <option value="yesno">Yes / No / Unsure</option>
          </select>
        </div>
        <div>
          <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Hint (optional)</label>
          <input type="text" id="eq-hint" placeholder="Optional hint shown to user…" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.55rem .8rem;color:var(--text);font-size:.83rem">
        </div>
      </div>

      <div id="eq-options-wrap">
        <label style="font-size:.8rem;color:var(--muted);display:block;margin-bottom:.3rem">Options (one per line)</label>
        <textarea id="eq-options" rows="4" placeholder="Option A&#10;Option B&#10;Option C" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.6rem .8rem;color:var(--text);font-size:.83rem;resize:vertical;margin-bottom:.75rem"></textarea>
      </div>

      <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:1rem;">
        <input type="checkbox" id="eq-active" checked style="width:14px;height:14px">
        <label for="eq-active" style="font-size:.82rem;color:var(--text)">Enabled</label>
      </div>

      <div style="border-top:1px solid var(--border);padding-top:.85rem;margin-bottom:.75rem;">
        <div style="font-size:.82rem;font-weight:600;color:var(--text);margin-bottom:.3rem;">Step Link <span style="font-weight:400;color:var(--muted);font-size:.75rem;">— optional deep-link on the recap card</span></div>
        <div style="display:grid;grid-template-columns:160px 1fr;gap:.6rem;align-items:start;">
          <div>
            <label style="font-size:.75rem;color:var(--muted);display:block;margin-bottom:.25rem;">Link type</label>
            <select id="eq-link-type" onchange="eqToggleLinkValue()" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .7rem;color:var(--text);font-size:.8rem;">
              <option value="">None</option>
              <option value="url">External URL</option>
              <option value="lesson">Lesson</option>
              <option value="quiz">Quiz question</option>
              <option value="mindmap">Mind Map</option>
              <option value="dashboard">Dashboard section</option>
            </select>
          </div>
          <div id="eq-link-value-wrap">
            <label style="font-size:.75rem;color:var(--muted);display:block;margin-bottom:.25rem;" id="eq-link-value-label">Value</label>
            <input type="text" id="eq-link-value-text" placeholder="" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;color:var(--text);font-size:.8rem;">
            <select id="eq-link-value-lesson" style="display:none;width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;color:var(--text);font-size:.8rem;"><option value="">— pick a lesson —</option></select>
            <select id="eq-link-value-quiz" style="display:none;width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;color:var(--text);font-size:.8rem;"><option value="">— pick a quiz question —</option></select>
            <select id="eq-link-value-dashboard" style="display:none;width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;color:var(--text);font-size:.8rem;">
              <option value="">— pick a section —</option>
              <option value="overview">Overview</option>
              <option value="lessons">Lessons</option>
              <option value="quiz">Quiz</option>
              <option value="mindmap">Mind Map</option>
              <option value="progress">Progress</option>
            </select>
          </div>
        </div>
      </div>

      <div style="border-top:1px solid var(--border);padding-top:.85rem;margin-bottom:.75rem;">
        <div style="font-size:.82rem;font-weight:600;color:var(--text);margin-bottom:.5rem;">Mindmap Node <span style="font-weight:400;color:var(--muted);font-size:.75rem;">— optional · unlocks this node when step is completed</span></div>
        <select id="eq-mindmap-node" style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;color:var(--text);font-size:.8rem;">
          <option value="">— no node —</option>
        </select>
      </div>

      <div style="border-top:1px solid var(--border);padding-top:.85rem;margin-bottom:.5rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.65rem;">
          <div>
            <div style="font-size:.82rem;font-weight:600;color:var(--text)">Follow-up Branches</div>
            <div style="font-size:.72rem;color:var(--muted);margin-top:1px;">Add one branch per answer option. Each branch shows a follow-up question when that answer is chosen. No limit.</div>
          </div>
          <button class="btn btn-sm" onclick="eqAddBranch()" style="font-size:.77rem;">+ Add branch</button>
        </div>
        <div id="eq-branches-list" style="display:flex;flex-direction:column;gap:.6rem;"></div>
      </div>

      <div id="eq-modal-error" style="color:var(--red);font-size:.78rem;margin-top:.5rem"></div>
    </div>
    <div class="form-actions">
      <button class="btn" onclick="closeModal('eval-q-modal')">Cancel</button>
      <button class="btn btn-primary" onclick="saveEvalQuestion()">Save</button>
    </div>
  </div>`;
  document.body.appendChild(modal);
}

function eqToggleOptions() {
  const type = document.getElementById('eq-input-type')?.value;
  const wrap = document.getElementById('eq-options-wrap');
  if (!wrap) return;
  wrap.style.display = (type === 'radio' || type === 'checkbox') ? 'block' : 'none';
  _eqRefreshBranchTriggerValues();
}

function eqToggleLinkValue() {
  const type = document.getElementById('eq-link-type')?.value;
  const labelEl = document.getElementById('eq-link-value-label');
  const textEl   = document.getElementById('eq-link-value-text');
  const lessonEl = document.getElementById('eq-link-value-lesson');
  const quizEl   = document.getElementById('eq-link-value-quiz');
  const dashEl   = document.getElementById('eq-link-value-dashboard');
  if (!textEl) return;
  // Hide all
  [textEl, lessonEl, quizEl, dashEl].forEach(el => el.style.display = 'none');
  if (!type) return;
  if (type === 'url') {
    textEl.placeholder = 'https://…';
    labelEl.textContent = 'URL';
    textEl.style.display = 'block';
  } else if (type === 'lesson') {
    labelEl.textContent = 'Lesson';
    lessonEl.style.display = 'block';
    // Populate if needed
    if (lessonEl.options.length <= 1 && _lessonsData?.length) {
      _lessonsData.forEach(l => {
        const o = document.createElement('option');
        o.value = l.lesson_key; o.textContent = l.title;
        lessonEl.appendChild(o);
      });
    }
  } else if (type === 'quiz') {
    labelEl.textContent = 'Quiz question';
    quizEl.style.display = 'block';
    if (quizEl.options.length <= 1 && _quizData?.length) {
      _quizData.forEach(q => {
        const o = document.createElement('option');
        o.value = q.id;
        const label = q.question_text ? q.question_text.slice(0, 80) + (q.question_text.length > 80 ? '…' : '') : `Question #${q.id}`;
        o.textContent = label;
        quizEl.appendChild(o);
      });
    }
  } else if (type === 'mindmap') {
    textEl.placeholder = 'topic slug or leave blank for default';
    labelEl.textContent = 'Topic (optional)';
    textEl.style.display = 'block';
  } else if (type === 'dashboard') {
    labelEl.textContent = 'Dashboard section';
    dashEl.style.display = 'block';
  }
}


// ── Smart trigger-value field: dropdown for yesno/radio, free-text otherwise ──
function _eqGetCurrentInputType() {
  return (document.getElementById('eq-input-type')?.value || 'text').toLowerCase();
}

function _eqGetCurrentOptions() {
  const raw = document.getElementById('eq-options')?.value || '';
  return raw.split('\n').map(s => s.trim()).filter(Boolean);
}

function _eqBranchTriggerValueHtml(idx, currentVal) {
  const itype = _eqGetCurrentInputType();
  const isYesNo = itype === 'yesno' || itype === 'yes_no';
  const isRadio = itype === 'radio' || itype === 'multiple_choice';
  const isCheckbox = itype === 'checkbox' || itype === 'multiple_answer';

  const selectStyle = 'width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:7px;padding:.4rem .65rem;color:var(--text);font-size:.8rem';
  const inputStyle  = selectStyle;

  if (isYesNo) {
    const opts = ['Yes', 'No', 'Unsure'];
    const optHtml = opts.map(o => `<option value="${o}" ${currentVal===o?'selected':''}>${o}</option>`).join('');
    return `<select data-field="trigger_value" style="${selectStyle}"><option value="">— pick an answer —</option>${optHtml}</select>`;
  }

  if (isRadio || isCheckbox) {
    const opts = _eqGetCurrentOptions();
    if (opts.length > 0) {
      const optHtml = opts.map(o => `<option value="${o}" ${currentVal===o?'selected':''}>${o}</option>`).join('');
      return `<select data-field="trigger_value" style="${selectStyle}"><option value="">— pick an answer —</option>${optHtml}</select>`;
    }
    // No options defined yet — fall through to free text with hint
    return `<input data-field="trigger_value" value="${currentVal}" placeholder="Type the exact option text…" style="${inputStyle}">`;
  }

  // text / scale / default — keep free-text
  return `<input data-field="trigger_value" value="${currentVal}" placeholder="e.g. No — I have concerns…" style="${inputStyle}">`;
}

// Called when input-type changes — refresh all existing branch trigger-value fields
function _eqRefreshBranchTriggerValues() {
  document.querySelectorAll('#eq-branches-list > div[id^="eq-branch-"]').forEach(row => {
    const idx = row.id.replace('eq-branch-', '');
    const wrap = document.getElementById(`eq-branch-val-wrap-${idx}`);
    if (!wrap || wrap.style.display === 'none') return;
    const current = wrap.querySelector('[data-field="trigger_value"]')?.value || '';
    wrap.innerHTML = `<label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Trigger value (which answer triggers this)</label>${_eqBranchTriggerValueHtml(idx, current)}`;
  });
}

let _eqBranchCount = 0;
function eqAddBranch(data) {
  const list = document.getElementById('eq-branches-list');
  if (!list) return;
  const idx = _eqBranchCount++;
  const d   = data || {};

  const row = document.createElement('div');
  row.id = `eq-branch-${idx}`;
  row.dataset.branchId = d.id || '';
  row.style.cssText = 'background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.75rem .9rem;position:relative;';
  row.innerHTML = `
    <button onclick="document.getElementById('eq-branch-${idx}').remove()" title="Remove branch"
      style="position:absolute;top:.5rem;right:.5rem;background:none;border:none;color:var(--muted);
             cursor:pointer;font-size:.9rem;line-height:1;padding:.15rem;">\u2715</button>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-bottom:.5rem;">
      <div>
        <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Trigger condition</label>
        <select data-field="trigger_condition"
          onchange="eqBranchConditionChange(${idx})"
          style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:7px;padding:.4rem .65rem;color:var(--text);font-size:.8rem">
          <option value="equals" ${d.trigger_condition==='equals'?'selected':''}>Equals</option>
          <option value="includes" ${d.trigger_condition==='includes'?'selected':''}>Includes</option>
          <option value="skipped" ${d.trigger_condition==='skipped'?'selected':''}>Skipped</option>
        </select>
      </div>
      <div id="eq-branch-val-wrap-${idx}" style="${d.trigger_condition==='skipped'?'display:none':''}">
        <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Trigger value (which answer triggers this)</label>
        ${_eqBranchTriggerValueHtml(idx, d.trigger_value||'')}
      </div>
    </div>

    <div style="margin-bottom:.5rem;">
      <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Follow-up question shown to user</label>
      <textarea data-field="followup_prompt" rows="2"
        style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:7px;padding:.4rem .65rem;color:var(--text);font-size:.8rem;resize:vertical"
        placeholder="Ask the user something based on their answer… e.g. Why did you choose No?">${escHtml(d.followup_prompt||'')}</textarea>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-bottom:.5rem;">
      <div>
        <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Type</label>
        <select data-field="followup_type"
          style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:7px;padding:.4rem .65rem;color:var(--text);font-size:.8rem">
          <option value="hint" ${(d.followup_type||'hint')==='hint'?'selected':''}>Hint (informational nudge)</option>
          <option value="block" ${d.followup_type==='block'?'selected':''}>Block (must acknowledge to continue)</option>
        </select>
      </div>
      <div>
        <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Attach extra resource (optional)</label>
        <select data-field="content_type" onchange="eqBranchContentChange(${idx}, this.value)"
          style="width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:7px;padding:.4rem .65rem;color:var(--text);font-size:.8rem">
          <option value="" ${!d.content_type?'selected':''}>None</option>
          <option value="lesson" ${d.content_type==='lesson'?'selected':''}>Lesson</option>
          <option value="quiz" ${d.content_type==='quiz'?'selected':''}>Quiz question</option>
          <option value="image" ${d.content_type==='image'?'selected':''}>Image (URL)</option>
          <option value="text" ${d.content_type==='text'?'selected':''}>Extra text / HTML</option>
        </select>
      </div>
    </div>

    <div id="eq-branch-content-${idx}">
      ${_eqBranchContentHtml(idx, d.content_type, d)}
    </div>

    <div style="display:flex;align-items:center;gap:.4rem;margin-top:.4rem;">
      <input type="checkbox" data-field="is_active" id="eq-branch-active-${idx}"
        ${(d.is_active!==0&&d.is_active!==false)?'checked':''} style="width:13px;height:13px">
      <label for="eq-branch-active-${idx}" style="font-size:.75rem;color:var(--muted)">Active</label>
    </div>`;
  list.appendChild(row);
}

function _eqBranchContentHtml(idx, ct, d) {
  const ss = 'width:100%;background:var(--input-bg);border:1px solid var(--border);border-radius:7px;padding:.4rem .65rem;color:var(--text);font-size:.8rem';
  if (!ct) return '';

  if (ct === 'lesson') {
    const lessons = Array.isArray(_lessonsData) ? _lessonsData : [];
    const opts = lessons.map(l =>
      `<option value="${l.id}" ${d.lesson_id==l.id?'selected':''}>${escHtml(l.title)}</option>`
    ).join('');
    const noData = !opts ? '<option value="">Loading lessons…</option>' : '<option value="">— pick a lesson —</option>';
    return `<div style="margin-top:.4rem">
      <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Link to lesson</label>
      <select data-field="lesson_id" style="${ss}">${noData}${opts}</select>
    </div>`;
  }

  if (ct === 'quiz') {
    const questions = Array.isArray(_quizData) ? _quizData : [];
    const opts = questions.map(q => {
      const preview = (q.question_text || '').slice(0, 60) + ((q.question_text||'').length > 60 ? '…' : '');
      return `<option value="${q.id}" ${d.quiz_question_id==q.id?'selected':''}>[${q.id}] ${escHtml(preview)}</option>`;
    }).join('');
    const noData = !opts ? '<option value="">Loading questions…</option>' : '<option value="">— pick a quiz question —</option>';
    return `<div style="margin-top:.4rem">
      <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Link to quiz question</label>
      <select data-field="quiz_question_id" style="${ss}">${noData}${opts}</select>
    </div>`;
  }

  if (ct === 'image') {
    return `<div style="margin-top:.4rem">
      <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Image URL</label>
      <input data-field="content_url" type="url" value="${escAttr(d.content_url||'')}"
        placeholder="https://example.com/image.png" style="${ss}">
    </div>`;
  }

  if (ct === 'text') {
    return `<div style="margin-top:.4rem">
      <label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Extra content shown to user</label>
      <textarea data-field="content_url" rows="3" style="${ss};resize:vertical"
        placeholder="Any extra explanation, context, or instructions shown alongside the follow-up question…">${escHtml(d.content_url||'')}</textarea>
    </div>`;
  }

  return '';
}

function eqBranchConditionChange(idx) {
  const sel  = document.querySelector(`#eq-branch-${idx} [data-field="trigger_condition"]`);
  const wrap = document.getElementById(`eq-branch-val-wrap-${idx}`);
  if (!wrap) return;
  const isSkipped = sel?.value === 'skipped';
  wrap.style.display = isSkipped ? 'none' : '';
  if (!isSkipped) {
    // Re-render smart trigger value field in case input type changed
    const current = wrap.querySelector('[data-field="trigger_value"]')?.value || '';
    wrap.innerHTML = `<label style="font-size:.72rem;color:var(--muted);display:block;margin-bottom:.2rem;">Trigger value (which answer triggers this)</label>${_eqBranchTriggerValueHtml(idx, current)}`;
  }
}

function eqBranchContentChange(idx, ct) {
  const container = document.getElementById(`eq-branch-content-${idx}`);
  const row = document.getElementById(`eq-branch-${idx}`);
  if (!container || !row) return;
  container.innerHTML = _eqBranchContentHtml(idx, ct, {});
}

function _readBranchesFromModal() {
  const rows = document.querySelectorAll('#eq-branches-list > div[id^="eq-branch-"]');
  return Array.from(rows).map(row => {
    const get = f => row.querySelector(`[data-field="${f}"]`);
    return {
      id:                parseInt(row.dataset.branchId) || null,
      trigger_condition: get('trigger_condition')?.value || 'equals',
      trigger_value:     get('trigger_value')?.value?.trim() || '',
      followup_prompt:   get('followup_prompt')?.value?.trim() || '',
      followup_type:     get('followup_type')?.value || 'hint',
      content_type:      get('content_type')?.value || null,
      lesson_id:         parseInt(get('lesson_id')?.value) || null,
      quiz_question_id:  parseInt(get('quiz_question_id')?.value) || null,
      content_url:       get('content_url')?.value?.trim() || null,
      is_active:         get('is_active')?.checked ? 1 : 0,
    };
  }).filter(b => b.followup_prompt);
}

async function loadEvalQuestions() {
  document.getElementById('eval-q-tbody').innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:2rem;">Loading…</td></tr>';
  try {
    _evalQData = await apiFetch('/admin/eval-questions');
    renderEvalQTable(_evalQData);
  } catch(e) {
    document.getElementById('eval-q-tbody').innerHTML = `<tr><td colspan="6" style="color:var(--red);text-align:center;padding:1rem;font-size:.8rem">${e.message}</td></tr>`;
  }
}

function filterEvalQ() {
  const q = (document.getElementById('eval-search')?.value || '').toLowerCase().trim();
  const type = document.getElementById('eval-filter-type')?.value || '';
  const filtered = (_evalQData || []).filter(item => {
    const matchQ = !q || (item.title||'').toLowerCase().includes(q) || (item.prompt||'').toLowerCase().includes(q);
    const matchT = !type || item.input_type === type || (item.input_type === 'yes_no' && type === 'yesno');
    return matchQ && matchT;
  });
  renderEvalQTable(filtered);
}

function renderEvalQTable(data) {
  if (!data.length) {
    document.getElementById('eval-q-tbody').innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:2rem;">No eval questions yet.</td></tr>';
    return;
  }
  document.getElementById('eval-q-tbody').innerHTML = data.map(q => {
    return `<tr>
    <td class="td-mono" style="font-size:.78rem">${q.step_number}</td>
    <td style="font-size:.82rem;max-width:260px"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(q.title)}</div><div style="font-size:.7rem;color:var(--muted);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(q.prompt)}</div></td>
    <td><span style="font-size:.7rem;padding:.15rem .45rem;border-radius:12px;background:rgba(255,255,255,.07)">${({'text':'Open-ended','radio':'Multiple Choice','checkbox':'Checkbox','scale':'Scale','yesno':'Yes / No','yes_no':'Yes / No','multiple_choice':'Multiple Choice','multiple_answer':'Multi-select'}[q.input_type]||q.input_type||'—')}</span></td>
    <td>
      <button onclick="toggleEvalActive(${q.id}, ${!!q.is_active})" title="${q.is_active ? 'Click to disable' : 'Click to enable'}" style="display:inline-flex;align-items:center;gap:.35rem;padding:.25rem .6rem;border-radius:20px;font-size:.72rem;font-family:'DM Mono',monospace;cursor:pointer;border:1px solid ${q.is_active ? 'rgba(52,211,153,.3)' : 'rgba(255,255,255,.1)'};background:${q.is_active ? 'rgba(52,211,153,.12)' : 'rgba(255,255,255,.05)'};color:${q.is_active ? 'var(--green)' : 'var(--muted)'};transition:all .15s">
        <span style="width:22px;height:12px;border-radius:6px;background:${q.is_active ? 'var(--green)' : 'rgba(255,255,255,.18)'};display:inline-block;position:relative;flex-shrink:0"><span style="position:absolute;top:2px;left:${q.is_active ? '12px' : '2px'};width:8px;height:8px;border-radius:50%;background:#fff;transition:left .15s"></span></span>
        ${q.is_active ? 'Published' : 'Draft'}
      </button>
    </td>
    <td style="white-space:nowrap">
      <button class="btn btn-sm btn-icon" title="Edit" onclick="openEvalQuestionModal(${q.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
      <button class="btn btn-sm btn-icon btn-danger" title="Delete" onclick="confirmDeleteEvalQ(${q.id},'${escAttr(q.title)}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button>
    </td>
  </tr>`;
  }).join('');
}

async function toggleEvalActive(id, currentActive) {
  try {
    const q = _evalQData.find(x => x.id === id);
    if (!q) return;
    const body = {
      step_number: q.step_number, title: q.title, step_label: q.step_label,
      prompt: q.prompt, hint: q.hint, input_type: q.input_type,
      options: q.options, is_active: currentActive ? 0 : 1,
      step_link_type: q.step_link_type, step_link_value: q.step_link_value, branches: []
    };
    await apiFetch(`/admin/eval-questions/${id}`, { method: 'PUT', body: JSON.stringify(body) });
    await loadEvalQuestions();
  } catch(e) { adminToast(e.message, 'error'); }
}

function openEvalQuestionModal(id) {
  _injectEvalQModal();
  _eqBranchCount = 0;
  const q = id ? _evalQData.find(x => x.id === id) : null;
  document.getElementById('eq-edit-id').value   = q ? q.id : '';
  // Pre-load lessons and quiz questions so dropdowns are populated
  if (!_lessonsData || _lessonsData.length === 0) {
    apiFetch('/admin/lessons').then(d => { _lessonsData = d || []; }).catch(() => {});
  }
  if (!_quizData || _quizData.length === 0) {
    apiFetch('/admin/quiz/questions').then(d => { _quizData = d || []; }).catch(() => {});
  }
  document.getElementById('eq-modal-title').textContent = q ? 'Edit eval question' : 'New eval question';
  document.getElementById('eq-step').value      = q ? q.step_number : (_evalQData.length + 1);
  document.getElementById('eq-title').value     = q ? q.title : '';
  document.getElementById('eq-step-label').value = q ? (q.step_label || '') : '';
  document.getElementById('eq-prompt').value    = q ? q.prompt : '';
  document.getElementById('eq-input-type').value = q ? q.input_type : 'text';
  document.getElementById('eq-hint').value      = q ? (q.hint || '') : '';
  document.getElementById('eq-options').value   = q && q.options ? JSON.parse(q.options).join('\n') : '';
  document.getElementById('eq-active').checked  = q ? !!q.is_active : true;
  document.getElementById('eq-modal-error').textContent = '';
  // Load step link
  const linkTypeEl = document.getElementById('eq-link-type');
  if (linkTypeEl) {
    linkTypeEl.value = (q?.step_link_type) || '';
    eqToggleLinkValue();
    // Set the value after toggle (which shows the right widget)
    const ltype = q?.step_link_type || '';
    const lval  = q?.step_link_value || '';
    if (ltype === 'lesson') {
      // Wait a tick for options to populate then set
      setTimeout(() => { document.getElementById('eq-link-value-lesson').value = lval; }, 50);
    } else if (ltype === 'quiz') {
      setTimeout(() => { document.getElementById('eq-link-value-quiz').value = lval; }, 50);
    } else if (ltype === 'dashboard') {
      document.getElementById('eq-link-value-dashboard').value = lval;
    } else if (ltype) {
      document.getElementById('eq-link-value-text').value = lval;
    }
  }
  // Populate mindmap node dropdown then set value
  const eqNodeSel = document.getElementById('eq-mindmap-node');
  if (eqNodeSel) {
    if (!_mindmapNodes.length) await _refreshMindmapNodes();
    const nodeOpts = '<option value="">— no node —</option>' +
      _mindmapNodes.map(n => `<option value="${escAttr(n.id)}">${escHtml(n.icon ? n.icon + ' ' : '')}${escHtml(n.label)}${n.sub ? ' — ' + escHtml(n.sub) : ''}</option>`).join('');
    eqNodeSel.innerHTML = nodeOpts;
    eqNodeSel.value = q?.mindmap_node_id || '';
  }
  // Load existing branches
  const branchesList = document.getElementById('eq-branches-list');
  if (branchesList) branchesList.innerHTML = '';
  (q?.branches || []).forEach(b => eqAddBranch(b));
  eqToggleOptions();
  openModal('eval-q-modal');
}

async function saveEvalQuestion() {
  const editId = document.getElementById('eq-edit-id').value;
  const errEl  = document.getElementById('eq-modal-error');
  errEl.textContent = '';
  const optionsRaw = document.getElementById('eq-options').value.trim();
  const options = optionsRaw ? JSON.stringify(optionsRaw.split('\n').map(s => s.trim()).filter(Boolean)) : null;
  const stepNum = parseInt(document.getElementById('eq-step').value) || 1;

  // Validate duplicate step_number
  const otherSameStep = _evalQData.filter(x => x.step_number === stepNum && String(x.id) !== String(editId));
  if (otherSameStep.length) {
    errEl.textContent = `Step #${stepNum} is already used by "${otherSameStep[0].title}". Pick a different number.`;
    return;
  }

  const body = {
    step_number: stepNum,
    title:       document.getElementById('eq-title').value.trim(),
    step_label:  document.getElementById('eq-step-label').value.trim() || null,
    prompt:      document.getElementById('eq-prompt').value.trim(),
    input_type:  document.getElementById('eq-input-type').value,
    hint:        document.getElementById('eq-hint').value.trim() || null,
    options,
    is_active:   document.getElementById('eq-active').checked ? 1 : 0,
    step_link_type:  document.getElementById('eq-link-type')?.value || null,
    step_link_value: (() => {
      const lt = document.getElementById('eq-link-type')?.value;
      if (!lt) return null;
      if (lt === 'lesson')    return document.getElementById('eq-link-value-lesson')?.value || null;
      if (lt === 'quiz')      return document.getElementById('eq-link-value-quiz')?.value || null;
      if (lt === 'dashboard') return document.getElementById('eq-link-value-dashboard')?.value || null;
      return document.getElementById('eq-link-value-text')?.value.trim() || null;
    })(),
    mindmap_node_id: document.getElementById('eq-mindmap-node')?.value || null,
    branches:    _readBranchesFromModal(),
  };
  if (!body.title || !body.prompt) { errEl.textContent = 'Title and prompt are required.'; return; }
  try {
    if (editId) {
      await apiFetch(`/admin/eval-questions/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
      adminToast('Question updated.');
    } else {
      await apiFetch('/admin/eval-questions', { method: 'POST', body: JSON.stringify(body) });
      adminToast('Question created.');
    }
    closeModal('eval-q-modal');
    await loadEvalQuestions();
  } catch(e) { errEl.textContent = e.message; }
}

function confirmDeleteEvalQ(id, title) {
  document.getElementById('confirm-title').textContent = 'Delete eval question';
  document.getElementById('confirm-body').innerHTML = `Permanently delete <strong>"${escHtml(title)}"</strong>?`;
  document.getElementById('confirm-ok-btn').onclick = async () => {
    try {
      await apiFetch(`/admin/eval-questions/${id}`, { method: 'DELETE' });
      adminToast('Question deleted.');
      closeModal('confirm-modal');
      await loadEvalQuestions();
    } catch(e) { closeModal('confirm-modal'); adminToast(e.message, 'error'); }
  };
  openModal('confirm-modal');
}


async function loadAnalytics() {
  // Each section fetches and renders independently.
  // A failure in one section shows an inline error without blanking the rest.

  // ── Overview stat cards + activity charts ────────────────────────────
  try {
    const stats = await apiFetch('/admin/stats');
    const o = stats.overview;
    document.getElementById('analytics-stats').innerHTML = `
      ${statCard('Registered Users', o.total_users, 'total accounts created')}
      ${statCard('Total Submissions', o.total_submissions, o.anonymous_submissions + ' were anonymous')}
      ${statCard('Lessons Completed', o.total_lesson_completions, 'across all users')}
      ${statCard('Quiz Attempts', o.total_quiz_attempts, 'questions answered')}
      ${statCard('Lesson Read Rate', o.lesson_read_rate_pct + '%', 'of lessons triggered were read')}
      ${statCard('Admin Accounts', o.total_admins, 'active administrators')}
    `;
    renderBarChart('dau-chart', 'dau-labels', stats.dau_7d.map(r => r.active_users), stats.dau_7d.map(r => r.day.slice(5)), '#4f8ef7');
    renderBarChart('reg-chart', 'reg-labels', stats.registrations_14d.map(r => r.new_users), stats.registrations_14d.map(r => r.day.slice(5)), '#34d399');
  } catch(e) {
    document.getElementById('analytics-stats').innerHTML = `<div style="color:var(--red);font-size:.8rem;padding:.5rem">Could not load overview: ${escHtml(e.message)}</div>`;
  }

  // ── Skill distribution ───────────────────────────────────────────────
  try {
    const skills = await apiFetch('/admin/analytics/skills');
    const topics = [...new Set(skills.skill_distribution.map(r => r.topic))];
    const levelColors = { beginner: '#34d399', intermediate: '#fbbf24', advanced: '#f87171' };
    document.getElementById('skill-dist').innerHTML = topics.map(t => {
      const rows = skills.skill_distribution.filter(r => r.topic === t);
      const total = rows.reduce((s, r) => s + r.user_count, 0) || 1;
      return `<div style="margin-bottom:.9rem"><div style="font-size:.78rem;margin-bottom:.35rem;color:var(--text)">${topicLabel(t)}</div>
        <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;gap:1px">
          ${['beginner','intermediate','advanced'].map(lvl => {
            const row = rows.find(r => r.current_level === lvl);
            const pct = row ? Math.round(row.user_count / total * 100) : 0;
            return pct ? `<div style="width:${pct}%;background:${levelColors[lvl]};opacity:.8" title="${lvl}: ${row?.user_count || 0} users"></div>` : '';
          }).join('')}
        </div>
        <div style="display:flex;gap:.75rem;margin-top:.3rem">
          ${['beginner','intermediate','advanced'].map(lvl => {
            const row = rows.find(r => r.current_level === lvl);
            return `<span style="font-family:'DM Mono',monospace;font-size:.63rem;color:${levelColors[lvl]}">${lvl[0].toUpperCase()}: ${row?.user_count || 0}</span>`;
          }).join('')}
        </div></div>`;
    }).join('') || '<div class="empty-state" style="padding:1rem"><p>No skill data yet</p></div>';
  } catch(e) {
    document.getElementById('skill-dist').innerHTML = `<div style="color:var(--red);font-size:.8rem;padding:.5rem">Could not load skill data: ${escHtml(e.message)}</div>`;
  }

  // ── Lesson heatmap ───────────────────────────────────────────────────
  try {
    const heatmap = await apiFetch('/admin/analytics/lessons-heatmap');
    const hData = heatmap.by_lesson.slice(0, 8);
    const hMax = Math.max(...hData.map(r => r.trigger_count), 1);
    document.getElementById('lesson-heatmap').innerHTML = hData.length ? hData.map(r => `
      <div class="topic-row">
        <div class="topic-name" style="font-size:.75rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.title)}">${escHtml(r.title)}</div>
        <div class="topic-bar-wrap"><div class="topic-bar-fill fill-blue" style="width:${Math.round(r.trigger_count / hMax * 100)}%"></div></div>
        <div class="topic-pct">${r.trigger_count}</div>
      </div>`).join('')
    : '<div class="empty-state" style="padding:1rem"><p>No trigger data yet</p></div>';
  } catch(e) {
    document.getElementById('lesson-heatmap').innerHTML = `<div style="color:var(--red);font-size:.8rem;padding:.5rem">Could not load heatmap: ${escHtml(e.message)}</div>`;
  }

  // ── Quiz analytics ───────────────────────────────────────────────────
  try {
    const quiz = await apiFetch('/admin/analytics/quiz');
    document.getElementById('quiz-by-topic').innerHTML = quiz.by_topic.length ? quiz.by_topic.map(r => {
      const pct = r.accuracy_pct || 0;
      const col = pct >= 70 ? '#34d399' : pct >= 40 ? '#fbbf24' : '#f87171';
      return `<div class="topic-row">
        <div class="topic-name" style="font-size:.75rem">${topicLabel(r.topic)}</div>
        <div class="topic-bar-wrap"><div class="topic-bar-fill" style="width:${pct}%;background:${col}"></div></div>
        <div class="topic-pct">${pct}%</div>
      </div>`;
    }).join('') : '<div class="empty-state" style="padding:1rem"><p>No attempts yet</p></div>';

    document.getElementById('quiz-by-diff').innerHTML = quiz.by_difficulty.length ? quiz.by_difficulty.map(r => {
      const pct = r.accuracy_pct || 0;
      const col = pct >= 70 ? '#34d399' : pct >= 40 ? '#fbbf24' : '#f87171';
      return `<div class="topic-row">
        <div class="topic-name" style="font-size:.75rem">${r.difficulty}</div>
        <div class="topic-bar-wrap"><div class="topic-bar-fill" style="width:${pct}%;background:${col}"></div></div>
        <div class="topic-pct">${pct}%</div>
      </div>`;
    }).join('') : '<div class="empty-state" style="padding:1rem"><p>No attempts yet</p></div>';

    document.getElementById('hardest-questions').innerHTML = quiz.hardest_questions.length
      ? `<table style="width:100%;border-collapse:collapse">${quiz.hardest_questions.map(q => `
          <tr style="border-bottom:1px solid var(--border)">
            <td style="padding:.5rem .25rem;font-size:.78rem;color:var(--text)">${escHtml(q.question_text.slice(0,80))}${q.question_text.length > 80 ? '…' : ''}</td>
            <td style="padding:.5rem .5rem;white-space:nowrap">${topicBadge(q.topic)}</td>
            <td style="padding:.5rem .25rem;white-space:nowrap;font-family:'DM Mono',monospace;font-size:.72rem;color:${(q.accuracy_pct||0)<40?'var(--red)':'var(--yellow)'}">
              ${q.accuracy_pct}% (${q.attempts} attempts)
            </td>
            <td style="padding:.5rem .25rem;white-space:nowrap">
              <button class="btn btn-sm" onclick="viewQuestionStats(${q.id})" style="font-size:.7rem;padding:.2rem .55rem;">View stats</button>
            </td>
          </tr>`).join('')}</table>`
      : '<div class="empty-state" style="padding:1rem"><p>Need at least 5 attempts per question</p></div>';
  } catch(e) {
    const msg = `<div style="color:var(--red);font-size:.8rem;padding:.5rem">Could not load quiz data: ${escHtml(e.message)}</div>`;
    ['quiz-by-topic','quiz-by-diff','hardest-questions'].forEach(id => {
      document.getElementById(id).innerHTML = msg;
    });
  }

  // ── Pre/post-test ────────────────────────────────────────────────────
  try {
    const pretest = await apiFetch('/admin/analytics/pretest');
    const byPhase = pretest.by_phase || [];
    const pre  = byPhase.find(r => r.phase === 'pretest');
    const post = byPhase.find(r => r.phase === 'posttest');
    document.getElementById('pretest-data').innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
        ${statCard('Pre-test avg', pre  ? pre.avg_score_pct  + '%' : '—', pre  ? pre.total_submissions  + ' submissions' : '')}
        ${statCard('Post-test avg', post ? post.avg_score_pct + '%' : '—', post ? post.total_submissions + ' submissions' : '')}
        ${statCard('Paired users', pretest.paired_users ?? 0, 'completed both')}
        ${statCard('Avg improvement', pretest.avg_improvement_pct != null ? '+' + pretest.avg_improvement_pct + '%' : '—', '')}
      </div>`;
  } catch(e) {
    document.getElementById('pretest-data').innerHTML = `<div style="color:var(--red);font-size:.8rem;padding:.5rem">Could not load pre/post-test data: ${escHtml(e.message)}</div>`;
  }
}

// ── CORPUS ─────────────────────────────────────────────────────────────────
async function loadCorpusStats() {
  try {
    const d = await apiFetch('/admin/corpus/stats');
    document.getElementById('corpus-stats').innerHTML = `
      ${statCard('Total sentences', d.total_sentences, '')}
      ${statCard('Unique sources', d.sources, '')}
      ${statCard('Pipelines', d.pipelines || '—', '')}`;
  } catch(e) { document.getElementById('corpus-stats').innerHTML = `<div style="color:var(--red);font-size:.8rem">${e.message}</div>`; }
}

async function ingestCorpus() {
  const domain    = document.getElementById('corpus-domain').value.trim();
  const name      = document.getElementById('corpus-name').value.trim();
  const pipeline  = document.getElementById('corpus-pipeline').value;
  const rawText   = document.getElementById('corpus-sentences').value;
  const sentences = rawText.split('\n').map(s => s.trim()).filter(Boolean);
  const resultEl  = document.getElementById('corpus-result');
  if (!domain || !name) { resultEl.style.color = 'var(--red)'; resultEl.textContent = 'Domain and name are required.'; return; }
  if (!sentences.length){ resultEl.style.color = 'var(--red)'; resultEl.textContent = 'No sentences provided.'; return; }
  try {
    const r = await apiFetch('/admin/corpus/ingest', { method: 'POST', body: JSON.stringify({ sentences, source_domain: domain, source_name: name, pipeline }) });
    resultEl.style.color = 'var(--green)';
    resultEl.textContent = `Inserted ${r.inserted}, skipped ${r.skipped} duplicates.`;
    await loadCorpusStats();
  } catch(e) { resultEl.style.color = 'var(--red)'; resultEl.textContent = e.message; }
}

async function cleanupOrphanedUploads() {
  const btn    = document.getElementById('cleanup-orphans-btn');
  const result = document.getElementById('cleanup-orphans-result');
  if (!btn || !result) return;
  if (!confirm('This will permanently delete all uploaded files that are no longer attached to any quiz question or lesson. Continue?')) return;
  btn.disabled = true;
  result.style.color = 'var(--muted)';
  result.textContent = 'Scanning…';
  try {
    const d = await apiFetch('/admin/upload/cleanup-orphans', { method: 'DELETE' });
    result.style.color = 'var(--green)';
    result.textContent = `Done — ${d.deleted_count} file(s) deleted, ${d.retained_count} retained.`;
    if (d.deleted_count && d.deleted_files.length) {
      result.textContent += ' Removed: ' + d.deleted_files.join(', ');
    }
  } catch(e) {
    result.style.color = 'var(--red)';
    result.textContent = 'Cleanup failed: ' + e.message;
  }
  btn.disabled = false;
}

// ── HELPERS ────────────────────────────────────────────────────────────────
function topicBadge(t) {
  return `<span class="badge" style="${_topicStyle(t)}border:none;">${_topicLabel(t)}</span>`;
}

// ── TOPICS MANAGEMENT ─────────────────────────────────────────────────────────

function filterTopics() {
  const q = (document.getElementById('topic-search')?.value || '').toLowerCase().trim();
  const tbody = document.getElementById('topics-table-body');
  if (!tbody) return;
  const allTopics = Object.values(_dashTopicRegistry).sort((a,b) => a.sort_order - b.sort_order || a.key.localeCompare(b.key));
  const filtered = !q ? allTopics : allTopics.filter(t =>
    (t.key||'').toLowerCase().includes(q) || (t.label||'').toLowerCase().includes(q)
  );
  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:1.5rem;font-size:.8rem;">No topics match "${escHtml(q)}".</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map(t => `
    <tr>
      <td style="padding:.5rem .6rem;font-family:'DM Mono',monospace;font-size:.75rem;">${escHtml(t.key)}</td>
      <td style="padding:.5rem .6rem;">
        <span style="${_topicStyle(t.key)};padding:.2rem .7rem;border-radius:20px;font-size:.72rem;font-family:'DM Mono',monospace;">
          ${escHtml(t.icon)} ${escHtml(t.label)}
        </span>
      </td>
      <td style="padding:.5rem .6rem;text-align:center;">
        <div style="display:inline-block;width:22px;height:22px;border-radius:50%;background:hsl(${t.color_hue},60%,55%);" title="Hue ${t.color_hue}"></div>
      </td>
      <td style="padding:.5rem .6rem;font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted);">${t.sort_order}</td>
      <td style="padding:.5rem .6rem;font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted);text-align:center;">${t.quiz_limit != null ? t.quiz_limit : '<span style="opacity:.4">—</span>'}</td>
      <td style="padding:.5rem .6rem;font-size:.72rem;color:var(--muted);text-align:center;">${Array.isArray(t.linked_quiz_ids) ? t.linked_quiz_ids.length : '<span style="opacity:.4">auto</span>'}</td>
      <td style="padding:.5rem .6rem;">
        <div style="display:flex;gap:.4rem;">
          <button class="btn btn-sm" onclick="openTopicModal('${escAttr(t.key)}')" style="font-size:.7rem;padding:.25rem .6rem;">Edit</button>
          <button class="btn btn-sm" onclick="deleteTopic('${escAttr(t.key)}')" style="font-size:.7rem;padding:.25rem .6rem;background:rgba(248,113,113,.12);color:var(--red);border-color:rgba(248,113,113,.3);">Delete</button>
        </div>
      </td>
    </tr>`).join('');
}

function _renderTopicsTable() {
  const tbody = document.getElementById('topics-table-body');
  if (!tbody) return;
  const topics = Object.values(_dashTopicRegistry).sort((a,b) => a.sort_order - b.sort_order || a.key.localeCompare(b.key));
  if (!topics.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:1.5rem;font-size:.8rem;">No topics yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = topics.map(t => `
    <tr>
      <td style="padding:.5rem .6rem;font-family:'DM Mono',monospace;font-size:.75rem;">${escHtml(t.key)}</td>
      <td style="padding:.5rem .6rem;">
        <span style="${_topicStyle(t.key)};padding:.2rem .7rem;border-radius:20px;font-size:.72rem;font-family:'DM Mono',monospace;">
          ${escHtml(t.icon)} ${escHtml(t.label)}
        </span>
      </td>
      <td style="padding:.5rem .6rem;text-align:center;">
        <div style="display:inline-block;width:22px;height:22px;border-radius:50%;background:hsl(${t.color_hue},60%,55%);" title="Hue ${t.color_hue}"></div>
      </td>
      <td style="padding:.5rem .6rem;font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted);">${t.sort_order}</td>
      <td style="padding:.5rem .6rem;font-family:'DM Mono',monospace;font-size:.72rem;color:var(--muted);text-align:center;">${t.quiz_limit != null ? t.quiz_limit : '<span style=\"opacity:.4\">—</span>'}</td>
      <td style="padding:.5rem .6rem;font-size:.72rem;color:var(--muted);text-align:center;">${Array.isArray(t.linked_quiz_ids) ? t.linked_quiz_ids.length : '<span style=\"opacity:.4\">auto</span>'}</td>
      <td style="padding:.5rem .6rem;">
        <div style="display:flex;gap:.4rem;">
          <button class="btn btn-sm" onclick="openTopicModal('${escAttr(t.key)}')" style="font-size:.7rem;padding:.25rem .6rem;">Edit</button>
          <button class="btn btn-sm" onclick="deleteTopic('${escAttr(t.key)}')" style="font-size:.7rem;padding:.25rem .6rem;background:rgba(248,113,113,.12);color:var(--red);border-color:rgba(248,113,113,.3);">Delete</button>
        </div>
      </td>
    </tr>`).join('');
}

// ── Topic modal state ──────────────────────────────────────────────────────
let _tmAllQuizQuestions = [];
let _tmLinkedQuizIds    = new Set();
let _tmCurrentTopicKey  = null;

async function openTopicModal(key) {
  const t = key ? _dashTopicRegistry[key] : null;
  _tmCurrentTopicKey = key || null;

  document.getElementById('topic-modal-title').textContent = t ? 'Edit Topic' : 'New Topic';
  document.getElementById('tm-key').value        = t ? t.key        : '';
  document.getElementById('tm-key').disabled     = !!t;
  document.getElementById('tm-label').value      = t ? t.label      : '';
  document.getElementById('tm-icon').value       = t ? t.icon       : '📄';
  document.getElementById('tm-hue').value        = t ? t.color_hue  : 220;
  document.getElementById('tm-hue-preview').style.background = `hsl(${t ? t.color_hue : 220},60%,55%)`;
  document.getElementById('tm-hue-num').textContent = t ? t.color_hue : 220;
  document.getElementById('tm-sort').value       = t ? t.sort_order : 0;
  document.getElementById('tm-quiz-limit').value = (t && t.quiz_limit) ? t.quiz_limit : '';
  document.getElementById('tm-existing-key').value = t ? t.key : '';
  document.getElementById('topic-modal-error').textContent = '';

  // Reset search/filter controls
  const searchEl = document.getElementById('tm-quiz-search');
  const diffEl   = document.getElementById('tm-quiz-filter-diff');
  const topicEl  = document.getElementById('tm-quiz-filter-topic');
  if (searchEl) searchEl.value = '';
  if (diffEl)   diffEl.value   = '';
  if (topicEl)  topicEl.value  = '';

  // Populate topic filter options in quiz linker
  if (topicEl) {
    const allTopics = Object.values(_dashTopicRegistry).sort((a,b) => a.sort_order - b.sort_order);
    topicEl.innerHTML = '<option value="">All topics</option>' +
      allTopics.map(tp => `<option value="${escAttr(tp.key)}">${escHtml(tp.icon)} ${escHtml(tp.label)}</option>`).join('');
  }

  openModal('topic-modal');

  // Load quiz questions async — show spinner while loading
  document.getElementById('tm-quiz-checklist').innerHTML =
    '<div style="color:var(--muted);font-size:.8rem;text-align:center;padding:1.25rem">Loading questions…</div>';
  document.getElementById('tm-quiz-count-bar').textContent = '';

  try {
    _tmAllQuizQuestions = await apiFetch('/admin/quiz/questions');
    // Determine initial checked set
    if (t && Array.isArray(t.linked_quiz_ids) && t.linked_quiz_ids.length > 0) {
      _tmLinkedQuizIds = new Set(t.linked_quiz_ids);
    } else {
      // Auto-check questions whose topic matches this topic key
      _tmLinkedQuizIds = new Set(
        _tmAllQuizQuestions
          .filter(q => t && q.topic === t.key)
          .map(q => q.id)
      );
    }
  } catch(_) {
    _tmAllQuizQuestions = [];
    _tmLinkedQuizIds    = new Set();
  }
  renderTopicQuizChecklist();
}

function filterTopicQuizList() {
  renderTopicQuizChecklist();
}

function toggleTopicQuizLink(id) {
  if (_tmLinkedQuizIds.has(id)) {
    _tmLinkedQuizIds.delete(id);
  } else {
    _tmLinkedQuizIds.add(id);
  }
  const cb = document.getElementById(`tm-q-${id}`);
  if (cb) cb.checked = _tmLinkedQuizIds.has(id);
  _updateTopicQuizCountBar();
}

function _updateTopicQuizCountBar() {
  const el = document.getElementById('tm-quiz-count-bar');
  if (!el) return;
  const total   = _tmAllQuizQuestions.length;
  const checked = _tmLinkedQuizIds.size;
  el.textContent = `${checked} of ${total} question${total !== 1 ? 's' : ''} linked`;
}

function renderTopicQuizChecklist() {
  const container = document.getElementById('tm-quiz-checklist');
  if (!container) return;

  const search      = (document.getElementById('tm-quiz-search')?.value || '').toLowerCase();
  const diffFilter  = document.getElementById('tm-quiz-filter-diff')?.value || '';
  const topicFilter = document.getElementById('tm-quiz-filter-topic')?.value || '';

  let filtered = _tmAllQuizQuestions;
  if (diffFilter)  filtered = filtered.filter(q => q.difficulty === diffFilter);
  if (topicFilter) filtered = filtered.filter(q => q.topic === topicFilter);
  if (search)      filtered = filtered.filter(q =>
    (q.question_text || '').toLowerCase().includes(search) ||
    (q.topic || '').toLowerCase().includes(search)
  );

  _updateTopicQuizCountBar();

  if (!filtered.length) {
    container.innerHTML = '<div style="color:var(--muted);font-size:.8rem;text-align:center;padding:1.25rem">No questions match this filter.</div>';
    return;
  }

  const diffColors = {
    beginner:     'background:rgba(16,185,129,.12);color:#34d399',
    intermediate: 'background:rgba(245,158,11,.12);color:#fbbf24',
    advanced:     'background:rgba(248,113,113,.12);color:var(--red)',
  };

  container.innerHTML = filtered.map(q => {
    const checked    = _tmLinkedQuizIds.has(q.id);
    const topicReg   = _dashTopicRegistry[q.topic];
    const topicLabel = topicReg ? `${topicReg.icon} ${topicReg.label}` : q.topic || '—';
    const diffStyle  = diffColors[q.difficulty] || 'background:rgba(120,120,120,.12);color:var(--muted)';
    const preview    = (q.question_text || '').slice(0, 90) + ((q.question_text || '').length > 90 ? '…' : '');
    return `<label style="display:flex;align-items:flex-start;gap:.6rem;padding:.55rem .7rem;cursor:pointer;border-bottom:1px solid var(--border);transition:background .12s" onmouseover="this.style.background='rgba(255,255,255,.03)'" onmouseout="this.style.background=''">
      <input type="checkbox" id="tm-q-${q.id}" ${checked ? 'checked' : ''} onchange="toggleTopicQuizLink(${q.id})" style="margin-top:.2rem;flex-shrink:0;accent-color:var(--accent)">
      <span style="flex:1;min-width:0">
        <span style="font-size:.8rem;line-height:1.4;display:block">${escHtml(preview)}</span>
        <span style="font-size:.7rem;color:var(--muted);display:inline-flex;gap:.4rem;margin-top:.2rem;flex-wrap:wrap;align-items:center">
          <span style="padding:.1rem .45rem;border-radius:20px;font-size:.67rem;${diffStyle}">${q.difficulty || '—'}</span>
          <span style="padding:.1rem .45rem;border-radius:20px;font-size:.67rem;background:rgba(79,142,247,.1);color:#60a5fa">${escHtml(topicLabel)}</span>
          <span style="opacity:.55">#${q.id}</span>
        </span>
      </span>
    </label>`;
  }).join('');
}

async function saveTopicModal() {
  const errEl       = document.getElementById('topic-modal-error');
  const existingKey = document.getElementById('tm-existing-key').value;
  const key         = document.getElementById('tm-key').value.trim();
  const label       = document.getElementById('tm-label').value.trim();
  const icon        = document.getElementById('tm-icon').value.trim() || '📄';
  const color_hue   = parseInt(document.getElementById('tm-hue').value) || 220;
  const sort_order  = parseInt(document.getElementById('tm-sort').value) || 0;
  const quizLimitRaw = document.getElementById('tm-quiz-limit').value.trim();
  const quiz_limit  = quizLimitRaw ? parseInt(quizLimitRaw) : null;
  const linked_quiz_ids = [..._tmLinkedQuizIds];

  if (!key)   { errEl.textContent = 'Key is required.';   return; }
  if (!label) { errEl.textContent = 'Label is required.'; return; }
  if (!/^[a-z][a-z0-9_]*$/.test(key)) {
    errEl.textContent = 'Key must be lowercase snake_case (e.g. critical_thinking).'; return;
  }
  errEl.textContent = '';
  try {
    const payload = { label, icon, color_hue, sort_order, linked_quiz_ids };
    if (quiz_limit !== null) payload.quiz_limit = quiz_limit;
    else                     payload.clear_quiz_limit = true;

    if (existingKey) {
      await apiFetch(`/admin/topics/${existingKey}`, 'PUT', payload);
    } else {
      await apiFetch('/admin/topics', 'POST', { key, ...payload });
    }
    closeModal('topic-modal');
    await _refreshTopics();
    adminToast(existingKey ? 'Topic updated.' : 'Topic created.');
  } catch(e) {
    errEl.textContent = e.message || 'Save failed.';
  }
}

async function deleteTopic(key) {
  if (!confirm(`Delete topic "${key}"?\n\nLessons/questions using this topic key are NOT deleted — they just won't match a registry entry until reassigned.`)) return;
  try {
    await apiFetch(`/admin/topics/${key}`, 'DELETE');
    await _refreshTopics();
    adminToast('Topic deleted.');
  } catch(e) {
    adminToast(e.message || 'Delete failed.', 'error');
  }
}


function diffBadge(d) {
  const map = { beginner:'badge-beginner', intermediate:'badge-intermediate', advanced:'badge-advanced' };
  return `<span class="badge ${map[d] || ''}">${d}</span>`;
}
// topicLabel already defined above
function populateLessonDropdown(id, lessons) {
  const sel = document.getElementById(id);
  const cur = sel.value;
  sel.innerHTML = '<option value="">— no lesson —</option>' + lessons.map(l => `<option value="${l.id}">${escHtml(l.title)}</option>`).join('');
  if (cur) sel.value = cur;
}
function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }



let _adminToastTimer;
function adminToast(msg, type = 'success') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + (type === 'error' ? 'show' : 'show');
  t.style.borderColor = type === 'error' ? 'rgba(248,113,113,.4)' : '';
  t.style.color = type === 'error' ? 'var(--red)' : '';
  clearTimeout(_adminToastTimer);
  _adminToastTimer = setTimeout(() => t.classList.remove('show'), 3500);
}



// ── Admin image helpers (URL preview + clear) ─────────────────────────────────
function adminImgUrlPreview(inputId, thumbId, previewId) {
  const val  = (document.getElementById(inputId)?.value || '').trim();
  const prev  = document.getElementById(previewId);
  const thumb = document.getElementById(thumbId);
  if (!prev || !thumb) return;
  if (!val) { prev.style.display = 'none'; thumb.src = ''; thumb.style.display = ''; return; }

  const lower = val.toLowerCase();
  if (lower.endsWith('.pdf')) {
    // Show a PDF badge, hide the <img>
    thumb.style.display = 'none';
    let badge = prev.querySelector('.pdf-badge');
    if (!badge) {
      badge = document.createElement('div');
      badge.className = 'pdf-badge';
      badge.style.cssText = 'font-size:.78rem;color:var(--accent);padding:.35rem .6rem;background:rgba(79,142,247,.12);border-radius:6px;display:inline-flex;align-items:center;gap:.4rem;';
      prev.insertBefore(badge, prev.firstChild);
    }
    badge.innerHTML = '📄 ' + val.split('/').pop();
    prev.style.display = '';
  } else if (lower.match(/\.(mp4|webm|mov|avi)$/)) {
    thumb.style.display = 'none';
    let badge = prev.querySelector('.pdf-badge');
    if (!badge) {
      badge = document.createElement('div');
      badge.className = 'pdf-badge';
      badge.style.cssText = 'font-size:.78rem;color:var(--accent);padding:.35rem .6rem;background:rgba(79,142,247,.12);border-radius:6px;display:inline-flex;align-items:center;gap:.4rem;';
      prev.insertBefore(badge, prev.firstChild);
    }
    badge.innerHTML = '🎬 ' + val.split('/').pop();
    prev.style.display = '';
  } else {
    // Image — remove any leftover badge, show thumb
    const badge = prev.querySelector('.pdf-badge');
    if (badge) badge.remove();
    thumb.style.display = '';
    thumb.onerror = () => { thumb.style.display = 'none'; };
    thumb.src = val;
    prev.style.display = '';
  }
}

function adminImgClear(inputId, thumbId, previewId) {
  const inp = document.getElementById(inputId);
  const prev = document.getElementById(previewId);
  const thumb = document.getElementById(thumbId);
  if (inp)  inp.value = '';
  if (thumb) thumb.src = '';
  if (prev)  prev.style.display = 'none';
}

// ── Admin image upload + crop/resize ─────────────────────────────────────────
// State shared across the modal lifecycle
let _cropState = {
  targetInputId:   null,   // text input to write final URL into
  targetThumbId:   null,   // preview <img>
  targetPreviewId: null,   // preview wrapper
  fileInputId:     null,   // hidden <input type=file>
  img:             null,   // HTMLImageElement (original)
  natW:            0,
  natH:            0,
  // canvas display scale
  scale:           1,
  // crop rect in image-native coords
  cx: 0, cy: 0, cw: 0, ch: 0,
  // drag state
  dragging:        false,
  dragStartX:      0,
  dragStartY:      0,
  aspectRatio:     null,
};

function adminUploadCrop(inputId, thumbId, previewId) {
  // Derive the hidden file-input id by appending '-file'
  const fileInputId = inputId + '-file';
  const fileInput = document.getElementById(fileInputId);
  if (!fileInput) return;

  _cropState.targetInputId   = inputId;
  _cropState.targetThumbId   = thumbId;
  _cropState.targetPreviewId = previewId;
  _cropState.fileInputId     = fileInputId;

  fileInput.value = '';
  fileInput.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // PDFs and videos can't go through the image crop canvas — upload directly
    const isPdf   = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
    const isVideo = file.type.startsWith('video/');
    if (isPdf || isVideo) {
      await _uploadFileDirect(file);
      return;
    }

    // Images → crop modal as before
    const reader = new FileReader();
    reader.onload = (ev) => _openCropModal(ev.target.result);
    reader.readAsDataURL(file);
  };
  fileInput.click();
}

// Upload a non-image file (PDF, video) directly without the crop step
async function _uploadFileDirect(file) {
  const inp   = document.getElementById(_cropState.targetInputId);
  const thumb = document.getElementById(_cropState.targetThumbId);
  const prev  = document.getElementById(_cropState.targetPreviewId);

  // Show a simple in-place status while uploading
  if (prev) { prev.style.display = ''; }
  if (thumb) { thumb.style.display = 'none'; } // hide img; we'll show a placeholder

  const token = localStorage.getItem('sp_access_token') || sessionStorage.getItem('sp_access_token') || '';
  const fd = new FormData();
  fd.append('file', file, file.name);
  try {
    const res  = await fetch('/admin/upload', {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: fd,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const url  = data.url || '';
    if (!url) throw new Error('No URL in response');

    if (inp) inp.value = url;

    // For PDFs show a file icon placeholder instead of broken <img>
    const isPdf = file.name.toLowerCase().endsWith('.pdf');
    if (prev) {
      if (isPdf) {
        prev.style.display = '';
        // Replace thumb with a PDF badge if thumb exists
        if (thumb) {
          thumb.style.display = 'none';
          // Insert a PDF label next to the clear button if not already there
          let badge = prev.querySelector('.pdf-badge');
          if (!badge) {
            badge = document.createElement('div');
            badge.className = 'pdf-badge';
            badge.style.cssText = 'font-size:.78rem;color:var(--accent);padding:.35rem .6rem;background:rgba(79,142,247,.12);border-radius:6px;display:inline-flex;align-items:center;gap:.4rem;margin-top:.25rem;';
            badge.innerHTML = '📄 ' + file.name;
            prev.insertBefore(badge, prev.firstChild);
          } else {
            badge.innerHTML = '📄 ' + file.name;
          }
        }
      } else {
        // Video — show thumb if possible
        if (thumb) { thumb.style.display = ''; thumb.src = url; }
      }
    }
    adminToast('Uploaded: ' + file.name);
  } catch (err) {
    adminToast('Upload failed: ' + err.message, 'error');
    if (prev && !inp?.value) prev.style.display = 'none';
    if (thumb) thumb.style.display = '';
  }
}

function _openCropModal(dataUrl) {
  const overlay = document.getElementById('img-crop-overlay');
  const canvas  = document.getElementById('img-crop-canvas');
  const wInput  = document.getElementById('img-crop-w');
  const hInput  = document.getElementById('img-crop-h');
  const lockCb  = document.getElementById('img-crop-lock');
  const qualEl  = document.getElementById('img-crop-quality');
  const qualVal = document.getElementById('img-crop-quality-val');
  const status  = document.getElementById('img-crop-upload-status');
  if (!overlay || !canvas) return;

  status.textContent = '';
  qualEl.oninput = () => { qualVal.textContent = qualEl.value + '%'; };

  const img = new Image();
  img.onload = () => {
    _cropState.img  = img;
    _cropState.natW = img.naturalWidth;
    _cropState.natH = img.naturalHeight;
    _cropState.aspectRatio = img.naturalWidth / img.naturalHeight;

    // Fit canvas to container (max 580×340)
    const maxW = Math.min(580, window.innerWidth - 80);
    const maxH = 340;
    _cropState.scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);
    canvas.width  = Math.round(img.naturalWidth  * _cropState.scale);
    canvas.height = Math.round(img.naturalHeight * _cropState.scale);

    // Default crop = full image
    _cropState.cx = 0; _cropState.cy = 0;
    _cropState.cw = img.naturalWidth;
    _cropState.ch = img.naturalHeight;

    wInput.value = img.naturalWidth;
    hInput.value = img.naturalHeight;

    _cropDrawCanvas();

    // W/H input sync
    wInput.oninput = () => {
      const nw = Math.max(10, parseInt(wInput.value) || 10);
      if (lockCb.checked) {
        const nh = Math.round(nw / _cropState.aspectRatio);
        hInput.value = nh;
        _cropState.ch = Math.min(nh, _cropState.natH);
      }
      _cropState.cw = Math.min(nw, _cropState.natW);
      _cropDrawCanvas();
    };
    hInput.oninput = () => {
      const nh = Math.max(10, parseInt(hInput.value) || 10);
      if (lockCb.checked) {
        const nw = Math.round(nh * _cropState.aspectRatio);
        wInput.value = nw;
        _cropState.cw = Math.min(nw, _cropState.natW);
      }
      _cropState.ch = Math.min(nh, _cropState.natH);
      _cropDrawCanvas();
    };

    // Drag to select crop area
    canvas.onmousedown  = (e) => _cropDragStart(e, canvas);
    canvas.onmousemove  = (e) => _cropDragMove(e, canvas, wInput, hInput);
    canvas.onmouseup    = ()  => { _cropState.dragging = false; };
    canvas.onmouseleave = ()  => { _cropState.dragging = false; };
    canvas.ontouchstart = (e) => { e.preventDefault(); _cropDragStart(e.touches[0], canvas); };
    canvas.ontouchmove  = (e) => { e.preventDefault(); _cropDragMove(e.touches[0], canvas, wInput, hInput); };
    canvas.ontouchend   = ()  => { _cropState.dragging = false; };

    overlay.style.display = 'flex';
  };
  img.src = dataUrl;
}

function _cropDrawCanvas() {
  const canvas = document.getElementById('img-crop-canvas');
  if (!canvas || !_cropState.img) return;
  const ctx = canvas.getContext('2d');
  const s   = _cropState.scale;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(_cropState.img, 0, 0, canvas.width, canvas.height);

  // Dim outside crop
  ctx.fillStyle = 'rgba(0,0,0,0.5)';
  const rx = Math.round(_cropState.cx * s);
  const ry = Math.round(_cropState.cy * s);
  const rw = Math.round(_cropState.cw * s);
  const rh = Math.round(_cropState.ch * s);
  ctx.fillRect(0, 0, canvas.width, ry);                         // top
  ctx.fillRect(0, ry + rh, canvas.width, canvas.height - ry - rh); // bottom
  ctx.fillRect(0, ry, rx, rh);                                  // left
  ctx.fillRect(rx + rw, ry, canvas.width - rx - rw, rh);        // right

  // Crop border + handles
  ctx.strokeStyle = '#6c63ff';
  ctx.lineWidth   = 1.5;
  ctx.strokeRect(rx, ry, rw, rh);
  const handles = [[rx, ry],[rx+rw, ry],[rx, ry+rh],[rx+rw, ry+rh]];
  ctx.fillStyle = '#fff';
  handles.forEach(([hx, hy]) => {
    ctx.beginPath(); ctx.arc(hx, hy, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  });
  // Rule-of-thirds grid
  ctx.strokeStyle = 'rgba(255,255,255,0.2)';
  ctx.lineWidth   = 0.5;
  for (let i = 1; i < 3; i++) {
    ctx.beginPath(); ctx.moveTo(rx + (rw/3)*i, ry); ctx.lineTo(rx + (rw/3)*i, ry+rh); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(rx, ry + (rh/3)*i); ctx.lineTo(rx+rw, ry + (rh/3)*i); ctx.stroke();
  }
}

function _cropCanvasXY(e, canvas) {
  const r  = canvas.getBoundingClientRect();
  const cx = (e.clientX - r.left) / _cropState.scale;
  const cy = (e.clientY - r.top)  / _cropState.scale;
  return [
    Math.max(0, Math.min(cx, _cropState.natW)),
    Math.max(0, Math.min(cy, _cropState.natH)),
  ];
}
function _cropDragStart(e, canvas) {
  const [x, y] = _cropCanvasXY(e, canvas);
  _cropState.dragging = true;
  _cropState.dragStartX = x;
  _cropState.dragStartY = y;
  _cropState.cx = x; _cropState.cy = y;
  _cropState.cw = 0; _cropState.ch = 0;
}
function _cropDragMove(e, canvas, wInput, hInput) {
  if (!_cropState.dragging) return;
  const [x, y] = _cropCanvasXY(e, canvas);
  const rawW = x - _cropState.dragStartX;
  const rawH = y - _cropState.dragStartY;
  _cropState.cx = rawW >= 0 ? _cropState.dragStartX : x;
  _cropState.cy = rawH >= 0 ? _cropState.dragStartY : y;
  _cropState.cw = Math.abs(rawW);
  _cropState.ch = Math.abs(rawH);
  if (document.getElementById('img-crop-lock')?.checked && _cropState.cw > 1) {
    _cropState.ch = _cropState.cw / _cropState.aspectRatio;
  }
  wInput.value = Math.round(_cropState.cw);
  hInput.value = Math.round(_cropState.ch);
  _cropDrawCanvas();
}

function imgCropReset() {
  if (!_cropState.img) return;
  _cropState.cx = 0; _cropState.cy = 0;
  _cropState.cw = _cropState.natW;
  _cropState.ch = _cropState.natH;
  document.getElementById('img-crop-w').value = _cropState.natW;
  document.getElementById('img-crop-h').value = _cropState.natH;
  _cropDrawCanvas();
}

function imgCropCancel() {
  const overlay = document.getElementById('img-crop-overlay');
  if (overlay) overlay.style.display = 'none';
}

async function imgCropApply() {
  const btn = document.getElementById('img-crop-apply-btn');
  const status = document.getElementById('img-crop-upload-status');
  if (!_cropState.img) return;

  const fmt     = document.getElementById('img-crop-fmt').value;
  const quality = parseInt(document.getElementById('img-crop-quality').value) / 100;
  const ext     = fmt === 'image/png' ? 'png' : fmt === 'image/webp' ? 'webp' : 'jpg';

  // Render the cropped region at output size
  const outW = Math.max(1, Math.round(_cropState.cw));
  const outH = Math.max(1, Math.round(_cropState.ch));
  const offscreen = document.createElement('canvas');
  offscreen.width  = outW;
  offscreen.height = outH;
  const ctx = offscreen.getContext('2d');
  ctx.drawImage(
    _cropState.img,
    _cropState.cx, _cropState.cy, _cropState.cw, _cropState.ch,
    0, 0, outW, outH
  );

  btn.disabled    = true;
  status.textContent = 'Uploading…';

  offscreen.toBlob(async (blob) => {
    if (!blob) { status.textContent = 'Failed to encode image.'; btn.disabled = false; return; }
    try {
      const fd = new FormData();
      fd.append('file', blob, `upload.${ext}`);
      const token = localStorage.getItem('sp_access_token') || sessionStorage.getItem('sp_access_token') || '';
      const res   = await fetch('/admin/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const url  = data.url || data.path || '';
      if (!url) throw new Error('No URL in response');

      // Set value + preview
      const inp   = document.getElementById(_cropState.targetInputId);
      const thumb = document.getElementById(_cropState.targetThumbId);
      const prev  = document.getElementById(_cropState.targetPreviewId);
      if (inp)   inp.value  = url;
      if (thumb) {
        // Always reset display — a previous onerror or PDF-state may have hidden the element
        thumb.style.display = '';
        thumb.onerror = null;
        thumb.src  = url;
      }
      if (prev)  prev.style.display = '';

      status.textContent = '✓ Uploaded';
      setTimeout(imgCropCancel, 600);
    } catch (err) {
      status.textContent = 'Upload failed: ' + err.message;
    }
    btn.disabled = false;
  }, fmt, fmt === 'image/png' ? undefined : quality);
}

// ── Admin sidebar group toggle (used in admin-dashboard.html sidebar) ─────────
function toggleAdminSidebarGroup(groupId) {
  const toggle  = document.getElementById('nav-' + groupId + '-toggle');
  const sub     = document.getElementById('nav-sub-' + groupId + '-group');
  if (!toggle || !sub) return;
  const isOpen = toggle.classList.toggle('open');
  sub.classList.toggle('open', isOpen);
}
