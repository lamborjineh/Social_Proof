const API_BASE  = '';
let LESSONS     = [];   // loaded from /lessons API
let currentFilter = 'all';
let completedKeys = new Set(JSON.parse(localStorage.getItem('sp_read_lessons') || '[]'));

// ── Frontend metadata map (icon + preview + takeaway by lesson_key) ───────────
// New lessons added to the DB without entries here get auto-generated previews.
const LESSON_META = {
  identify_claims:    { icon:'🎯', takeaway:'Ask yourself: "Can this be independently verified?" If yes — it\'s a claim that deserves fact-checking.' },
  multiple_claims:    { icon:'📋', takeaway:'Break compound sentences into individual claims and investigate each one separately.' },
  source_verification:{ icon:'🔍', takeaway:'Before trusting any content, take 60 seconds to check: Who published this? When? Can I find their editorial standards?' },
  primary_vs_secondary:{ icon:'🏛️', takeaway:'"Studies show…" tells you nothing. Always ask: Which study? Where is the link? What did it actually say?' },
  recognize_bias:     { icon:'🔥', takeaway:'The more emotional a post makes you feel, the more carefully you should read it.' },
  clickbait_headlines:{ icon:'🪤', takeaway:'Always read past the headline. A sensational headline that matches a measured article is rare.' },
  investigate_evidence:  { icon:'📊', takeaway:'"Studies show" is not evidence. The citation, sample size, methodology, and publication venue are what make evidence credible.' },
  correlation_causation:{ icon:'🔗', takeaway:'When a post says "X is linked to Y," that\'s correlation — not proof that X causes Y.' },
  general_mil:        { icon:'📖', takeaway:'MIL doesn\'t make you a cynic — it makes you a more informed, empowered consumer of information.' },
  confirmation_bias:  { icon:'🧠', takeaway:'The content you agree with most strongly deserves the most scrutiny — not the least.' },
};

// ── Dynamic topic system — works for ANY topic added via the admin dashboard ───
// Seeded hints for known topics; unknown topics auto-generate from their key.
const TOPIC_HINTS = {
  claim_detection:    { label:'Claim Detection',    icon:'🎯', h:220 },
  source_verification:{ label:'Source Verification', icon:'🔍', h:158 },
  bias_detection:     { label:'Bias Detection',      icon:'⚡', h: 38 },
  evidence_evaluation:{ label:'Evidence Evaluation', icon:'📊', h:340 },
  general:            { label:'General MIL',         icon:'📖', h:260 },
};

// Auto-assigns a stable hue to any unknown topic by hashing its string
function _topicHue(topic) {
  let h = 0;
  for (let i = 0; i < topic.length; i++) h = (h * 31 + topic.charCodeAt(i)) & 0xffff;
  return h % 360;
}

// Returns { label, icon, cls, style } for any topic key
function getTopicMeta(topic) {
  const hint = TOPIC_HINTS[topic];
  const hue  = hint ? hint.h : _topicHue(topic);
  const label = hint ? hint.label
                     : topic.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
  const icon  = hint ? hint.icon : '📄';
  // Inline style replaces CSS class — works for any topic without touching styles.css
  const tagStyle = `background:hsla(${hue},70%,65%,.13);color:hsl(${hue},70%,72%);`;
  return { label, icon, hue, tagStyle };
}

// Legacy shims so existing renderGrid / renderQuizTopicCards code keeps working
const TOPIC_ICONS = new Proxy({}, { get: (_, t) => getTopicMeta(t).icon });
const TOPIC_META  = new Proxy({}, { get: (_, t) => ({ ...getTopicMeta(t), cls: 'tag-dynamic' }) });

// ── Media renderer for quiz questions ────────────────────────────────────────
// Renders the correct element based on the file extension of image_url.
// Supports images, PDFs (embedded via <iframe>), and videos.
function _renderQuestionMedia(q) {
  const url = q && (q.image_url || q.video_url);
  if (!url) return '';
  const lower = url.toLowerCase();

  // PDF — embed in an iframe so it renders inline
  if (lower.endsWith('.pdf')) {
    return `<iframe src="${url}" style="width:100%;height:320px;border:1px solid var(--border);border-radius:10px;margin-bottom:.85rem;" loading="lazy" title="PDF attachment"></iframe>`;
  }

  // Video
  if (lower.match(/\.(mp4|webm|mov|avi)$/)) {
    return `<video controls style="width:100%;max-height:240px;border-radius:10px;margin-bottom:.85rem;border:1px solid var(--border);">
      <source src="${url}">
      <p style="font-size:.8rem;color:var(--muted);">Your browser doesn't support embedded video. <a href="${url}" target="_blank">Download</a></p>
    </video>`;
  }

  // Image (default)
  return `<img src="${url}" alt="question media" style="width:100%;max-height:220px;object-fit:cover;border-radius:10px;margin-bottom:.85rem;border:1px solid var(--border);" onerror="this.style.display='none'">`;
}

// Strip HTML tags for preview generation
function stripHtml(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return tmp.textContent || tmp.innerText || '';
}

// Estimate reading time in minutes
function readTime(content) {
  const words = stripHtml(content).split(/\s+/).length;
  const mins  = Math.max(1, Math.round(words / 200));
  return `${mins} min read`;
}

// Render lesson content: HTML lessons render as-is; plain-text lessons
// get their newlines converted to paragraphs so they display correctly.
function _formatLessonContent(content) {
  if (!content) return '';
  const trimmed = content.trim();
  if (trimmed.startsWith('<')) return trimmed; // already HTML
  return trimmed
    .split(/\n\n+/)
    .map(p => `<p style="margin-bottom:.75rem;">${p.replace(/\n/g, '<br>')}</p>`)
    .join('');
}

// ── Fetch lessons from API ─────────────────────────────────────────────────────
async function loadLessons() {
  try {
    const isAdmin = localStorage.getItem('sp_role') === 'admin';
    const url = isAdmin ? `${API_BASE}/lessons?include_unpublished=1` : `${API_BASE}/lessons`;
    const res  = await fetch(url, { credentials: 'include' });
    const data = await res.json();
    if (!Array.isArray(data) || !data.length) throw new Error('empty');
    LESSONS = data.map(l => ({
      ...l,
      key:     l.lesson_key || l.key || String(l.id),
      icon:    (LESSON_META[l.lesson_key] || {}).icon || TOPIC_ICONS[l.topic] || '📄',
      preview: stripHtml(l.content || '').slice(0, 140) + '…',
      takeaway:(LESSON_META[l.lesson_key] || {}).takeaway || '',
    }));
  } catch(e) {
    // Graceful fallback — show friendly error
    document.getElementById('lessons-grid').innerHTML =
      '<p style="color:var(--muted);font-size:.88rem;">Could not load lessons — make sure the API is running.</p>';
    return;
  }
  updateProgress();
  renderGrid(getFiltered());
  renderFilterBar();
  renderQuizTopicCards();
}

// ── Render grid ────────────────────────────────────────────────────────────────
function renderGrid(list) {
  const grid  = document.getElementById('lessons-grid');
  const empty = document.getElementById('empty-state');
  if (!list.length) { grid.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  grid.innerHTML = list.map(l => {
    const tm     = getTopicMeta(l.topic);
    const done   = completedKeys.has(l.key);
    const rt     = readTime(l.content || l.preview || '');
    const unpub  = l.is_published === 0 || l.is_published === false;
    return `
      <div class="lesson-card grid-card ${done?'completed':''} ${unpub?'lesson-card--unpublished':''}"
           onclick="openLesson('${l.key}')"
           data-lesson-id="${l.id}"
           data-lesson-key="${l.key}"
           data-topic="${l.topic}"
           style="${unpub ? 'opacity:.6;' : ''}">
        <div class="lesson-topic-tag" style="${tm.tagStyle}">${tm.label}</div>
        ${unpub ? '<div style="font-size:.65rem;color:var(--muted);margin-bottom:.25rem;font-family:\'DM Mono\',monospace;">⏸ DEACTIVATED</div>' : ''}
        <span class="lesson-icon">${l.icon}</span>
        <div class="lesson-title">${l.title}</div>
        <div class="lesson-preview">${l.preview}</div>
        <div class="lesson-footer">
          <span class="lesson-diff diff-${l.difficulty}">
            <span class="diff-dot"></span>${l.difficulty.charAt(0).toUpperCase()+l.difficulty.slice(1)}
          </span>
          <span class="read-time">⏱ ${rt}</span>
        </div>
      </div>`;
  }).join('');
}

function getFiltered() {
  const q = (document.getElementById('search').value || '').toLowerCase();
  return LESSONS.filter(l => {
    const matchTopic  = currentFilter === 'all' || l.topic === currentFilter;
    const haystack    = (l.title + ' ' + (l.content || '') + ' ' + l.preview).toLowerCase();
    const matchSearch = !q || haystack.includes(q);
    return matchTopic && matchSearch;
  });
}

// Debounce handle for backend FTS — avoids a round-trip on every keystroke.
let _searchDebounce = null;

async function _backendSearch(q) {
  try {
    const params = new URLSearchParams({ q });
    if (currentFilter !== 'all') params.set('topic', currentFilter);
    const res  = await fetch(`${API_BASE}/lessons?${params}`, { credentials: 'include' });
    const data = await res.json();
    if (!Array.isArray(data)) return;
    // Merge backend results — add any lessons not already in LESSONS
    const existing = new Set(LESSONS.map(l => l.id));
    data.forEach(l => {
      if (!existing.has(l.id)) {
        LESSONS.push({
          ...l,
          key:     l.lesson_key || l.key || String(l.id),
          icon:    (LESSON_META[l.lesson_key] || {}).icon || TOPIC_ICONS[l.topic] || '📄',
          preview: stripHtml(l.content || '').slice(0, 140) + '…',
          takeaway:(LESSON_META[l.lesson_key] || {}).takeaway || '',
        });
      }
    });
    renderGrid(getFiltered());
  } catch(e) {
    // Backend search failed — client-side filter is already shown
  }
}

function filterLessons() {
  renderGrid(getFiltered());  // immediate client-side pass
  const q = (document.getElementById('search').value || '').trim();
  if (!q) return;
  clearTimeout(_searchDebounce);
  _searchDebounce = setTimeout(() => _backendSearch(q), 350);
}

function setFilter(btn, filter) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentFilter = filter;
  filterLessons();
}

// ── Open lesson modal ──────────────────────────────────────────────────────────
async function openLesson(key) {
  // Pretest is non-negotiable — block lesson access until completed
  if (!localStorage.getItem('sp_pretest_done')) {
    initPretest();
    return;
  }
  const l = LESSONS.find(x => x.key === key);
  if (!l) return;
  const tm  = TOPIC_META[l.topic] || { label: l.topic, cls: 'tag-general' };
  const done = completedKeys.has(key);
  const diffColor = { beginner:'var(--green)', intermediate:'var(--yellow)', advanced:'var(--red)' }[l.difficulty] || 'var(--muted)';
  const rt = readTime(l.content || '');

  document.getElementById('modal-body').innerHTML = `
    <div class="lesson-topic-tag ${tm.cls}" style="margin-bottom:1.2rem;">${tm.label}</div>
    ${l.image_url ? `<img src="${l.image_url}" alt="${l.title}" style="width:100%;max-height:220px;object-fit:cover;border-radius:12px;margin-bottom:1.1rem;border:1px solid var(--border);" onerror="this.style.display='none'">` : ''}
    <div style="font-size:2rem;margin-bottom:.6rem;">${l.icon}</div>
    <div class="modal-title">${l.title}</div>
    <div style="display:flex;gap:.9rem;align-items:center;margin-bottom:1.2rem;flex-wrap:wrap;">
      <span class="lesson-diff diff-${l.difficulty}" style="font-family:'DM Mono',monospace;font-size:.68rem;letter-spacing:.06em;color:var(--muted);display:flex;align-items:center;gap:.4rem;">
        <span class="diff-dot" style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${diffColor}"></span>
        ${l.difficulty.toUpperCase()}
      </span>
      ${l.mil_skill ? `<span style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--accent2);">MIL: ${l.mil_skill}</span>` : ''}
    </div>
    <div class="modal-content">${_formatLessonContent(l.content || l.preview || '')}</div>
    ${l.takeaway ? `<div class="takeaway"><div class="takeaway-label">KEY TAKEAWAY</div><div class="takeaway-text">${l.takeaway}</div></div>` : ''}

    <!-- Micro-quiz section -->
    <div class="micro-quiz">
      <div class="micro-quiz-label">⚡ Quick Check — answer to complete this lesson</div>
      <div id="micro-quiz-area"><div class="micro-loading">Loading question…</div></div>
    </div>

    <!-- Mark complete button (shown if no quiz available or after attempting) -->
    <div id="mark-area" style="display:none;">
      <button class="mark-complete-btn ${done?'done':''}" id="mark-btn" onclick="markComplete('${key}')"
        ${done?'disabled':''}>
        ${done ? '✓ Completed' : '✓ Mark as Complete'}
      </button>
    </div>
  `;

  document.getElementById('modal-overlay').classList.add('open');
  loadMicroQuiz(l.topic, key, done);
}

// ── Load micro-quiz for lesson modal ──────────────────────────────────────────
async function loadMicroQuiz(topic, lessonKey, alreadyDone) {
  const area = document.getElementById('micro-quiz-area');
  if (!area) return;
  try {
    const res  = await fetch(`${API_BASE}/quiz?topic=${topic}&limit=1`, { credentials: 'include' });
    const data = await res.json();
    if (!data || !data.length) throw new Error('no q');
    const q       = data[0];
    const options = Array.isArray(q.options) ? q.options : JSON.parse(q.options || '[]');
    const letters = ['A','B','C','D'];

    area.innerHTML = `
      ${q.scenario_text ? `<div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:.65rem .85rem;margin-bottom:.75rem;font-size:.82rem;line-height:1.6;color:var(--muted);">${q.scenario_text}</div>` : ''}
      ${_renderQuestionMedia(q)}
      <div class="micro-quiz-q">${q.question_text}</div>
      <div class="micro-options" id="micro-options">
        ${options.map((opt,i) => `
          <button class="micro-opt" data-idx="${i}"
            onclick="answerMicro(${i},${q.correct_index},'${(q.explanation||'').replace(/'/g,"\\'")}','${lessonKey}')">
            <span class="micro-opt-letter">${letters[i]||i}</span>
            <span>${opt}</span>
          </button>`).join('')}
      </div>
      <div class="micro-feedback" id="micro-feedback"></div>
    `;
  } catch(e) {
    // No quiz available — just show the manual mark button
    area.innerHTML = '<p style="font-size:.8rem;color:var(--muted);">No check available for this lesson.</p>';
    document.getElementById('mark-area').style.display = 'block';
  }
}

// Skill metadata for micro-quiz chips (mirrors backend _TOPIC_SKILL map)
const _MICRO_SKILL = {
  claim_detection:    { label:'Claim Detection',          desc:'Spotting specific, checkable assertions in everyday language' },
  source_verification:{ label:'Source Verification',      desc:'Tracing where information comes from and whether that origin is trustworthy' },
  bias_detection:     { label:'Bias Detection',           desc:'Recognising emotional framing, loaded language, and selective emphasis' },
  evidence_evaluation:{ label:'Evidence Evaluation',      desc:'Judging whether sources actually prove what a claim asserts' },
  general:            { label:'Media & Information Literacy', desc:'Applying critical thinking across the full fact-checking workflow' },
};

function answerMicro(selected, correct, explanation, lessonKey) {
  const opts = document.querySelectorAll('.micro-opt');
  const fb   = document.getElementById('micro-feedback');
  opts.forEach((btn, idx) => {
    btn.disabled = true;
    if (idx === correct) btn.classList.add('correct');
    else if (idx === selected && selected !== correct) btn.classList.add('wrong');
    else if (idx === correct && selected !== correct) btn.classList.add('reveal');
  });

  const isCorrect = selected === correct;
  fb.className = `micro-feedback show ${isCorrect ? 'correct' : 'wrong'}`;
  fb.innerHTML = `<strong>${isCorrect ? '\u2713 Correct!' : '\u2717 Not quite.'}</strong>${explanation ? `<br><span style="color:var(--text);font-size:.82rem;">${explanation}</span>` : ''}`;

  // Skill chip — read topic from the nearest lesson card's data-topic attribute
  const lessonCard = fb.closest('[data-topic]') || document.querySelector(`[data-key="${lessonKey}"]`);
  const topic = lessonCard ? (lessonCard.dataset.topic || 'general') : 'general';
  const skill = _MICRO_SKILL[topic] || _MICRO_SKILL.general;
  fb.innerHTML += `<div style="margin-top:.5rem"><span class="skill-chip"><span class="skill-chip-icon">&#x1F9E0;</span> Skill practiced: <strong>${skill.label}</strong></span><div class="skill-chip-desc">${skill.desc}</div></div>`;

  // Show mark-complete after answering
  const markArea = document.getElementById('mark-area');
  if (markArea) markArea.style.display = 'block';

  // Auto-mark complete if correct
  if (isCorrect && !completedKeys.has(lessonKey)) {
    setTimeout(() => markComplete(lessonKey, true), 600);
  }
}

// ── Mark lesson complete ───────────────────────────────────────────────────────
function markComplete(key, auto = false) {
  if (completedKeys.has(key)) return;
  completedKeys.add(key);
  localStorage.setItem('sp_read_lessons', JSON.stringify([...completedKeys]));

  // Update button state
  const btn = document.getElementById('mark-btn');
  if (btn) { btn.textContent = '✓ Completed'; btn.classList.add('done'); btn.disabled = true; }

  updateProgress();
  renderGrid(getFiltered());

  // XP popup
  if (!auto) showXpPopup('+10 XP');
  else showXpPopup('✓ +10 XP');

  // Sync to server (best-effort)
  const lessonId = (LESSONS.find(l => l.key === key) || {}).id;
  if (lessonId) {
    fetch(`${API_BASE}/lessons/${lessonId}/complete`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    }).catch(() => {});
  }
}

function showXpPopup(text) {
  const el = document.createElement('div');
  el.className = 'xp-popup';
  el.textContent = text;
  el.style.cssText = `left:50%;top:40%;margin-left:-40px;`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 950);
}

function updateProgress() {
  const total = LESSONS.length;
  const done  = completedKeys.size;
  document.getElementById('read-count').textContent = `${done} / ${total}`;
  document.getElementById('progress-fill').style.width = total ? `${(done/total)*100}%` : '0%';
}

function closeModal() { document.getElementById('modal-overlay').classList.remove('open'); }
function closeModalOutside(e) { if (e.target.id === 'modal-overlay') closeModal(); }

// ── Init ───────────────────────────────────────────────────────────────────────
loadLessons();


// ── Auth state → sidebar ────────────────────────────────────
(function() {
  const username  = localStorage.getItem('sp_username');
  const role      = localStorage.getItem('sp_role');
  const loginLink  = document.getElementById('sidebar-login-link');
  const loginLinkM = document.getElementById('sidebar-login-link-m');

  if (username) {
    const logoutHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
      <polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
    </svg> Log out`;
    const doLogout = async e => {
      e.preventDefault();
      await fetch('/auth/cookie-logout',{method:'POST',credentials:'include'}).catch(()=>{});
      localStorage.clear(); window.location.href = 'login.html';
    };
    if (loginLink)  { loginLink.innerHTML  = logoutHTML; loginLink.href  = '#'; loginLink.style.color  = 'var(--red)'; loginLink.onclick  = doLogout; }
    if (loginLinkM) { loginLinkM.innerHTML = logoutHTML; loginLinkM.href = '#'; loginLinkM.style.color = 'var(--red)'; loginLinkM.onclick = doLogout; }
  }

})();

// ── Embedded Practice Quiz ─────────────────────────────────────────────────────
const QUIZ_API = '';
const QUIZ_TOPIC_NAMES = {
  claim_detection:'Claim Detection', source_verification:'Source Verification',
  bias_detection:'Bias Detection', evidence_evaluation:'Evidence Evaluation', general:'General'
};
const QUIZ_TOPIC_COLORS = {
  claim_detection:'var(--blue)', source_verification:'var(--accent2)',
  bias_detection:'var(--orange)', evidence_evaluation:'var(--green)', general:'var(--accent)'
};

// ── Build filter bar from whatever topics are actually in the DB ───────────────
function renderFilterBar() {
  const bar = document.getElementById('filter-bar');
  if (!bar) return;
  const topics = [...new Set(LESSONS.map(l => l.topic).filter(Boolean))];
  bar.innerHTML = `<button class="filter-btn active" data-filter="all" onclick="setFilter(this,'all')">📚 All</button>`;
  topics.forEach(topic => {
    const tm  = getTopicMeta(topic);
    const btn = document.createElement('button');
    btn.className = 'filter-btn';
    btn.dataset.filter = topic;
    btn.onclick = function(){ setFilter(this, topic); };
    btn.textContent = `${tm.icon} ${tm.label}`;
    bar.appendChild(btn);
  });
}

// ── Build quiz topic cards — one card per unique topic ─────────────────────────
function renderQuizTopicCards() {
  const grid = document.getElementById('quiz-topic-grid');
  if (!grid) return;

  // Build topic set: start from the known MIL topics, then add any extra topics from LESSONS
  const knownTopics = Object.keys(QUIZ_TOPIC_NAMES);
  const lessonTopics = [...new Set(LESSONS.map(l => l.topic).filter(Boolean))];
  const allTopics = [...new Set([...knownTopics, ...lessonTopics])];

  const allCard = `
    <div class="topic-card" data-topic="all" onclick="quizSelectTopic(this,'all')"
         style="padding:1rem;border-radius:12px;border:1px solid var(--accent);background:rgba(79,142,247,.08);cursor:pointer;transition:all .2s;">
      <div style="font-size:1.4rem;margin-bottom:.4rem;">📚</div>
      <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:.9rem;margin-bottom:.2rem;">All Topics</div>
      <div style="font-family:'DM Mono',monospace;font-size:.7rem;color:var(--muted);">Mixed practice</div>
    </div>`;

  const topicCards = allTopics.map(topic => {
    const tm = getTopicMeta(topic);
    return `
      <div class="topic-card" data-topic="${topic}"
           onclick="quizSelectTopic(this,'${topic}')"
           style="padding:1rem;border-radius:12px;border:1px solid var(--border);background:var(--surface);cursor:pointer;transition:all .2s;"
           onmouseenter="if(qState.selectedTopic!=='${topic}'){this.style.borderColor='hsl(${tm.hue},60%,55%)';this.style.background='hsla(${tm.hue},60%,55%,.08)'}"
           onmouseleave="if(qState.selectedTopic!=='${topic}'){this.style.borderColor='var(--border)';this.style.background='var(--surface)'}">
        <div style="font-size:1.4rem;margin-bottom:.4rem;">${tm.icon}</div>
        <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:.9rem;margin-bottom:.2rem;">${tm.label}</div>
      </div>`;
  }).join('');

  grid.innerHTML = allCard + topicCards;
}

function quizSelectTopic(el, topic) {
  qState.selectedTopic = topic; // set FIRST so hover guards see updated value
  document.querySelectorAll('#quiz-topic-grid .topic-card').forEach(c => {
    c.style.borderColor = 'var(--border)'; c.style.background = 'var(--surface)';
  });
  el.style.borderColor = 'var(--accent)'; el.style.background = 'rgba(79,142,247,.08)';
  qState.selectedLessonId = null;
  qState._selectedEl = el;
}

function _shuffleArray(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

const qState = { questions:[], currentIndex:0, score:0, topicStats:{}, selectedTopic:'all', selectedLessonId:null, answered:{}, _selectedEl:null };

// Session limit removed — quiz loads all available questions for the selected topic.

async function quizStart() {
  qState.score = 0; qState.currentIndex = 0; qState.topicStats = {}; qState.answered = {}; qState._shuffled = {}; qState._selIdx = {};
  const topic = qState.selectedTopic;
  let url;
  if (topic && topic !== 'all') {
    url = `${QUIZ_API}/quiz?topic=${encodeURIComponent(topic)}&limit=50`;
  } else {
    url = `${QUIZ_API}/quiz?limit=50`;
  }
  try {
    const res = await fetch(url, { credentials:'include' });
    const qs = await res.json() || [];
    // Shuffle question order for each new session
    qState.questions = _shuffleArray(qs);
  } catch(e) { qState.questions = []; }
  if (!qState.questions.length) {
    alert('No quiz questions found in the database. Ask an admin to seed questions, or import seed_quiz_questions.sql.');
    return;
  }
  document.getElementById('quiz-screen-start').style.display = 'none';
  document.getElementById('quiz-screen-quiz').style.display = '';
  document.getElementById('quiz-screen-results').style.display = 'none';
  localStorage.setItem('sp_quiz_last_date', new Date().toISOString().slice(0,10));
  quizRender();
}

function quizBackToTopics() {
  // Cancel current quiz and go back to topic selection
  document.getElementById('quiz-screen-quiz').style.display = 'none';
  document.getElementById('quiz-screen-results').style.display = 'none';
  document.getElementById('quiz-screen-start').style.display = '';
  // Re-apply topic highlight if one was selected
  if (qState._selectedEl) {
    document.querySelectorAll('#quiz-topic-grid .topic-card').forEach(c => {
      c.style.borderColor = 'var(--border)'; c.style.background = 'var(--surface)';
    });
    qState._selectedEl.style.borderColor = 'var(--accent)';
    qState._selectedEl.style.background = 'rgba(79,142,247,.08)';
  }
}

function _shuffleOptions(q) {
  // Reshuffle answer options on every render so position can't be memorised.
  // Works for multiple_choice and scenario_based. Skips true_false + identification.
  const qt = q.question_type || 'multiple_choice';
  if (qt === 'true_false' || qt === 'identification') return q;
  const rawOptions = Array.isArray(q.options) ? q.options : JSON.parse(q.options || '[]');
  if (!rawOptions.length) return q;
  const indexed = rawOptions.map((text, i) => ({ text, orig: i }));
  for (let i = indexed.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [indexed[i], indexed[j]] = [indexed[j], indexed[i]];
  }
  const origToNew = {};
  indexed.forEach((item, newIdx) => { origToNew[item.orig] = newIdx; });
  const shuffled = { ...q, options: indexed.map(item => item.text) };
  if (qt === 'multiple_answer') {
    const ci = Array.isArray(q.correct_indices) ? q.correct_indices : [];
    shuffled.correct_indices = ci.map(i => origToNew[i]).filter(i => i !== undefined);
    shuffled.correct_index = 0;
  } else {
    shuffled.correct_index = origToNew[q.correct_index] ?? q.correct_index;
  }
  return shuffled;
}

function quizRender() {
  const raw = qState.questions[qState.currentIndex];
  if (!raw) { quizShowResults(); return; }
  // Persist shuffled options per question index so going prev/next shows same order
  if (!qState._shuffled) qState._shuffled = {};
  if (!qState._shuffled[qState.currentIndex]) {
    qState._shuffled[qState.currentIndex] = _shuffleOptions(raw);
  }
  const q = qState._shuffled[qState.currentIndex];
  const total = qState.questions.length;
  const options = Array.isArray(q.options) ? q.options : JSON.parse(q.options || '[]');
  const letters = ['A','B','C','D'];
  const topic = q.topic || 'general';
  document.getElementById('quiz-progress-label').textContent = `Question ${qState.currentIndex+1} of ${total}`;
  document.getElementById('quiz-score-label').innerHTML = `Score: <b>${qState.score}</b>`;
  document.getElementById('quiz-progress-fill').style.width = `${(qState.currentIndex/total)*100}%`;
  document.getElementById('quiz-feedback-box').style.display = 'none';

  const answeredVal = qState.answered[qState.currentIndex];
  const alreadyAnswered = !!answeredVal && answeredVal !== 'skipped';
  // Show/hide navigation buttons
  const prevBtn = document.getElementById('quiz-prev-btn');
  if (prevBtn) prevBtn.style.display = qState.currentIndex > 0 ? '' : 'none';
  document.getElementById('quiz-skip-btn').style.display = alreadyAnswered ? 'none' : '';
  document.getElementById('quiz-next-btn').style.display = alreadyAnswered ? '' : 'none';

  const qContainer = document.getElementById('quiz-question-container');
  qContainer.dataset.explanation = (q.explanation||'');
  qContainer.dataset.hint = (q.hint||'');
  qContainer.innerHTML = `
    <div style="display:inline-flex;align-items:center;gap:.4rem;font-family:'DM Mono',monospace;font-size:.68rem;letter-spacing:.08em;text-transform:uppercase;padding:.3rem .7rem;border-radius:20px;margin-bottom:1rem;background:rgba(79,142,247,.15);color:var(--accent);">${QUIZ_TOPIC_NAMES[topic]||topic}</div>
    ${q.scenario_text ? `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.85rem 1rem;margin-bottom:1rem;font-size:.88rem;line-height:1.65;color:var(--muted);">${q.scenario_text}</div>` : ''}
    ${_renderQuestionMedia(q)}
    <div style="font-size:1.05rem;font-weight:500;line-height:1.6;margin-bottom:1.5rem;">${q.question_text}</div>
    ${q.hint ? `<div style="margin-bottom:1rem;">
      <button id="quiz-hint-btn" onclick="const h=document.getElementById('quiz-hint-box');const show=h.style.display==='none';h.style.display=show?'block':'none';this.textContent=show?'\ud83d\udca1 Hide hint':'\ud83d\udca1 Show hint';"
        style="background:none;border:1px solid rgba(251,191,36,.4);border-radius:20px;color:#f59e0b;font-size:.75rem;padding:.3rem .75rem;cursor:pointer;font-family:'DM Sans',sans-serif;">
        \ud83d\udca1 Show hint
      </button>
      <div id="quiz-hint-box" style="display:none;margin-top:.5rem;padding:.65rem .9rem;background:rgba(251,191,36,.07);border:1px solid rgba(251,191,36,.25);border-radius:9px;font-size:.85rem;line-height:1.6;color:var(--text);">
        ${q.hint}
      </div>
    </div>` : ''}
    <div style="display:flex;flex-direction:column;gap:.65rem;">
      ${options.map((opt,i) => {
        // Restore highlight for already-answered questions
        let btnStyle = 'width:100%;text-align:left;padding:.85rem 1.1rem;border-radius:12px;border:1px solid var(--border);background:var(--surface);color:var(--text);font-family:\'DM Sans\',sans-serif;font-size:.92rem;line-height:1.5;cursor:pointer;transition:all .2s;display:flex;align-items:center;gap:.75rem;';
        let circleStyle = 'width:22px;height:22px;border-radius:50%;border:1.5px solid var(--border);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-family:\'DM Mono\',monospace;font-size:.7rem;color:var(--muted);';
        const savedSel = alreadyAnswered ? qState._selIdx[qState.currentIndex] : undefined;
        if (alreadyAnswered) {
          if (i === q.correct_index) { btnStyle = btnStyle.replace('var(--border)','var(--green)').replace('var(--surface)','rgba(52,211,153,.1)').replace('var(--text)','var(--green)'); circleStyle = circleStyle.replace('var(--border)','var(--green)').replace('var(--muted)','var(--green)'); }
          else if (savedSel !== undefined && i === savedSel && savedSel !== q.correct_index) { btnStyle = btnStyle.replace('var(--border)','var(--red)').replace('var(--surface)','rgba(248,113,113,.1)').replace('var(--text)','var(--red)'); circleStyle = circleStyle.replace('var(--border)','var(--red)').replace('var(--muted)','var(--red)'); }
        }
        const disabled = alreadyAnswered ? 'disabled' : '';
        return `<button style="${btnStyle}" ${disabled}
          data-idx="${i}" onclick="quizAnswer(${i},${q.correct_index},this.closest('#quiz-question-container').dataset.explanation||'',this.closest('#quiz-question-container').dataset.hint||'',${q.id},'${topic}')">
          <span style="${circleStyle}">${letters[i]||i}</span>
          <span>${opt}</span>
        </button>`;
      }).join('')}
    </div>`;

  // Restore feedback box for already-answered questions
  if (alreadyAnswered) {
    const savedSel = qState._selIdx ? qState._selIdx[qState.currentIndex] : undefined;
    const isCorrect = savedSel === q.correct_index;
    const fb = document.getElementById('quiz-feedback-box');
    fb.style.display = 'block';
    fb.style.background = isCorrect ? 'rgba(52,211,153,.1)' : 'rgba(248,113,113,.1)';
    fb.style.border = isCorrect ? '1px solid rgba(52,211,153,.25)' : '1px solid rgba(248,113,113,.25)';
    fb.innerHTML = `<strong style="color:${isCorrect?'var(--green)':'var(--red)'}">${isCorrect?'✓ Correct!':'✗ Incorrect'}</strong>` + (q.explanation ? `<br><span style="font-size:.88rem;">${q.explanation}</span>` : '');
  }
}

async function quizAnswer(sel, correct, explanation, hint, qid, topic) {
  if (qState.answered[qState.currentIndex]) return;
  qState.answered[qState.currentIndex] = true;
  if (!qState._selIdx) qState._selIdx = {};
  qState._selIdx[qState.currentIndex] = sel; // save selected index for prev navigation
  const isCorrect = sel === correct;
  // Hide hint toggle once answered
  const hintBtn = document.getElementById('quiz-hint-btn');
  if (hintBtn) hintBtn.style.display = 'none';
  document.querySelectorAll('#quiz-question-container button[data-idx]').forEach((btn, i) => {
    btn.disabled = true;
    if (i === correct) { btn.style.borderColor='var(--green)'; btn.style.background='rgba(52,211,153,.1)'; btn.style.color='var(--green)'; }
    else if (i === sel && !isCorrect) { btn.style.borderColor='var(--red)'; btn.style.background='rgba(248,113,113,.1)'; btn.style.color='var(--red)'; }
  });
  if (isCorrect) qState.score += 10;
  if (!qState.topicStats[topic]) qState.topicStats[topic] = {correct:0,total:0};
  qState.topicStats[topic].total++;
  if (isCorrect) qState.topicStats[topic].correct++;
  const fb = document.getElementById('quiz-feedback-box');
  fb.style.display = 'block';
  fb.style.background = isCorrect ? 'rgba(52,211,153,.1)' : 'rgba(248,113,113,.1)';
  fb.style.border = isCorrect ? '1px solid rgba(52,211,153,.25)' : '1px solid rgba(248,113,113,.25)';
  fb.innerHTML = `<strong style="color:${isCorrect?'var(--green)':'var(--red)'}">${isCorrect?'✓ Correct!':'✗ Incorrect'}</strong>`
    + (explanation ? `<br><span style="font-size:.88rem;">${explanation}</span>` : '')
    + (!isCorrect && hint ? `<div style="margin-top:.55rem;padding:.55rem .8rem;background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.25);border-radius:8px;font-size:.83rem;line-height:1.55;color:var(--text);"><span style="color:#f59e0b;font-weight:600;">💡 Hint:</span> ${hint}</div>` : '')
    + (!isCorrect ? `<br><a href="lessons.html" style="display:inline-flex;align-items:center;gap:.4rem;margin-top:.5rem;font-family:'DM Mono',monospace;font-size:.72rem;color:var(--accent);text-decoration:none;border:1px solid rgba(79,142,247,.3);border-radius:8px;padding:.3rem .8rem;">📖 Review ${QUIZ_TOPIC_NAMES[topic]||topic} lessons →</a>` : '');
  document.getElementById('quiz-score-label').innerHTML = `Score: <b>${qState.score}</b>`;
  document.getElementById('quiz-skip-btn').style.display = 'none';
  document.getElementById('quiz-next-btn').style.display = '';
  // Record attempt + surface skill chip (feature ⑨)
  try {
    const res = await fetch(`${QUIZ_API}/quiz/attempt`,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:parseInt(localStorage.getItem('sp_user_id')),question_id:qid,selected_index:sel})});
    if (res.ok) {
      const data = await res.json();
      if (data.skill_label) {
        const chipEl = document.createElement('div');
        chipEl.innerHTML = `<span class="skill-chip"><span class="skill-chip-icon">&#x1F9E0;</span> Skill practiced: <strong>${data.skill_label}</strong></span>${data.skill_description ? `<div class="skill-chip-desc">${data.skill_description}</div>` : ''}`;
        fb.appendChild(chipEl);
      }
    }
  } catch(e){}
}

function quizSkip() {
  if (qState.answered[qState.currentIndex]) return;
  const q = qState.questions[qState.currentIndex];
  if (q && q.topic) { if (!qState.topicStats[q.topic]) qState.topicStats[q.topic]={correct:0,total:0}; qState.topicStats[q.topic].total++; }
  qState.answered[qState.currentIndex] = 'skipped';
  qState.currentIndex++;
  if (qState.currentIndex >= qState.questions.length) quizShowResults();
  else quizRender();
}

function quizNext() {
  qState.currentIndex++;
  if (qState.currentIndex >= qState.questions.length) quizShowResults();
  else quizRender();
}

function quizPrev() {
  if (qState.currentIndex <= 0) return;
  qState.currentIndex--;
  // If the previous question was skipped, clear it so user can re-answer
  if (qState.answered[qState.currentIndex] === 'skipped') {
    delete qState.answered[qState.currentIndex];
  }
  quizRender();
}

function quizShowResults() {
  document.getElementById('quiz-screen-quiz').style.display = 'none';
  document.getElementById('quiz-screen-results').style.display = '';
  const total = qState.questions.length;
  const correctCount = Math.round(qState.score / 10);
  const pct = total > 0 ? Math.round((correctCount/total)*100) : 0;
  document.getElementById('quiz-result-num').textContent = `${correctCount}/${total}`;
  const ring = document.getElementById('quiz-result-ring');
  ring.style.background = pct >= 80 ? `conic-gradient(var(--green) ${pct}%, var(--surface) 0%)` : pct >= 50 ? `conic-gradient(var(--yellow) ${pct}%, var(--surface) 0%)` : `conic-gradient(var(--red) ${pct}%, var(--surface) 0%)`;
  const headlines = [[80,'Excellent work! 🎉','Your media literacy skills are strong.'],[60,'Good effort! 📈','Review weak topics to improve.'],[40,'Keep practising! 💪','Focus on topics where you struggled.'],[0,"Let's learn together 📖",'Check the lessons — they build skills step by step.']];
  const [,headline,subline] = headlines.find(([t]) => pct >= t);
  document.getElementById('quiz-result-headline').textContent = headline;
  document.getElementById('quiz-result-subline').textContent = subline;
}

function quizRetry() {
  document.getElementById('quiz-screen-results').style.display = 'none';
  document.getElementById('quiz-screen-start').style.display = '';
  // Re-highlight selected topic if any
  if (qState._selectedEl) {
    document.querySelectorAll('#quiz-topic-grid .topic-card').forEach(c => {
      c.style.borderColor = 'var(--border)'; c.style.background = 'var(--surface)';
    });
    qState._selectedEl.style.borderColor = 'var(--accent)';
    qState._selectedEl.style.background = 'rgba(79,142,247,.08)';
  }
}


let _pbqQuestions = [];
let _pbqActive    = [];
let _pbqIdx       = 0;
let _pbqScore     = 0;
let _pbqMissed    = [];
let _pbqAnswered  = false;

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchLessonsTab(tab) {
  const isLessons    = tab === 'lessons';
  const lessonsEl    = document.getElementById('tab-lessons');
  const navLessons   = document.getElementById('nav-sub-lessons-lessons');

  if (lessonsEl)  lessonsEl.style.display = isLessons ? '' : 'none';
  if (navLessons) navLessons.classList.toggle('active', isLessons);
  window.scrollTo({ top: 0, behavior: 'smooth' });

  // Update hash for bookmarking/back-button
  window.location.hash = tab;

  // Refresh relevant stats panel on tab switch
  if (tab === 'lessons') {
    // stats are shown in the user dashboard
  }
}

// Hash-based routing on load: #quiz scrolls to quiz within lessons tab
(function() {
  const hash = window.location.hash.replace('#', '');
  if (hash === 'quiz') {
    switchLessonsTab('lessons');
    setTimeout(() => { document.getElementById('quiz')?.scrollIntoView({behavior: 'smooth'}); }, 300);
  }
})();




// ══ PRE-TEST / POST-TEST ══════════════════════════════════════════════════════

const PT_CLAIMS = []; // populated from GET /quiz/pretest

// ── Shared helpers ────────────────────────────────────────────────────────────
function ptGetSession() {
  return sessionStorage.getItem('sp_session') || 'anonymous';
}
function ptGetUserId() {
  const id = localStorage.getItem('sp_user_id');
  return id ? parseInt(id) : null;
}
function ptShowOverlay(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'flex';
}
function ptHideOverlay(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}

// ══ PRETEST ═══════════════════════════════════════════════════════════════════

async function initPretest() {
  if (localStorage.getItem('sp_pretest_done'))    return; // already completed
  // Pretest is non-negotiable — cannot be skipped

  let claims = [];
  try {
    const r = await fetch(`${API_BASE}/quiz/pretest`, { credentials: 'include' });
    if (!r.ok) return;
    const d = await r.json();
    claims = d.claims || [];
  } catch(e) { return; }

  if (!claims.length) return;
  PT_CLAIMS.length = 0;
  PT_CLAIMS.push(...claims.slice(0, 5)); // cap at 5 — keep it frictionless

  renderPretestModal();
  ptShowOverlay('sp-pretest-overlay');
}

let _ptStep = 0; // current stepper index for pretest

function renderPretestModal() {
  _ptStep = 0;
  const body = document.getElementById('sp-pretest-modal-body');
  if (!body) return;
  _renderPretestStep(body);
}

function _renderPretestStep(body) {
  const total = PT_CLAIMS.length;
  const c = PT_CLAIMS[_ptStep];
  if (!c) return;
  const isLast = _ptStep === total - 1;
  const allAnswered = PT_CLAIMS.every(cl => ptAnswers[cl.id] !== undefined);
  const qtype = c.question_type || 'true_false';

  body.innerHTML = `
    <div style="text-align:center;margin-bottom:1.25rem;">
      <div style="font-size:2rem;margin-bottom:.4rem;">🧭</div>
      <div style="font-family:'Syne',sans-serif;font-weight:800;font-size:1.3rem;margin-bottom:.3rem;">Help us help you</div>
      <div style="color:var(--muted);font-size:.82rem;line-height:1.5;">Answer as best you can — we track your progress over time.</div>
    </div>

    <!-- Stepper dots -->
    <div style="display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:1.5rem;">
      ${PT_CLAIMS.map((cl, i) => `
        <div style="width:${i === _ptStep ? '24px' : '8px'};height:8px;border-radius:4px;
             background:${ptAnswers[cl.id] !== undefined ? 'var(--green)' : i === _ptStep ? 'var(--accent)' : 'var(--border)'};
             transition:all .25s;"></div>
      `).join('')}
    </div>

    <div style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--accent);letter-spacing:.08em;margin-bottom:.5rem;">
      QUESTION ${_ptStep + 1} OF ${total}
    </div>

    <div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1.25rem 1.3rem;margin-bottom:1.25rem;">
      ${_renderQuestionMedia(c)}
      <div style="font-size:.95rem;line-height:1.65;color:var(--text);">${c.text}</div>
    </div>

    ${_ptRenderInput(c)}

    <!-- Navigation -->
    <div style="display:flex;gap:.75rem;align-items:stretch;margin-top:1.25rem;">
      ${_ptStep > 0 ? `
        <button onclick="_ptBack()"
          style="flex:1;padding:.75rem;border-radius:10px;border:1px solid var(--border);background:transparent;
                 color:var(--muted);font-family:'Syne',sans-serif;font-weight:600;font-size:.88rem;cursor:pointer;text-align:center;">
          ← Back
        </button>` : '<div style="flex:1;"></div>'}
      ${isLast ? `
        <button id="pt-submit-btn" onclick="submitPretest()" ${allAnswered ? '' : 'disabled'}
          style="flex:1;padding:.75rem;border-radius:10px;border:none;background:var(--accent);color:#fff;
                 font-family:'Syne',sans-serif;font-weight:700;font-size:.88rem;
                 opacity:${allAnswered ? '1' : '.4'};cursor:${allAnswered ? 'pointer' : 'not-allowed'};transition:all .2s;text-align:center;">
          Submit Answers →
        </button>` : `
        <button onclick="_ptNext()" ${ptAnswers[c.id] !== undefined ? '' : 'disabled'}
          style="flex:1;padding:.75rem;border-radius:10px;border:none;
                 background:${ptAnswers[c.id] !== undefined ? 'var(--accent)' : 'var(--surface)'};
                 color:${ptAnswers[c.id] !== undefined ? '#fff' : 'var(--muted)'};
                 font-family:'Syne',sans-serif;font-weight:700;font-size:.88rem;
                 cursor:${ptAnswers[c.id] !== undefined ? 'pointer' : 'not-allowed'};transition:all .2s;
                 border:1px solid ${ptAnswers[c.id] !== undefined ? 'transparent' : 'var(--border)'};text-align:center;">
          Next →
        </button>`}
    </div>
    <div style="text-align:center;margin-top:.75rem;color:var(--muted);font-size:.72rem;font-family:'DM Mono',monospace;letter-spacing:.03em;">
      Complete the pretest to unlock lessons
    </div>`;
}

function _ptRenderInput(c) {
  const qtype = c.question_type || 'true_false';
  const ans   = ptAnswers[c.id];

  if (qtype === 'true_false') {
    return `<div style="display:flex;gap:.75rem;">
      <button onclick="ptSelectStepper(${c.id},'True')"
        style="flex:1;padding:.75rem .5rem;border-radius:10px;border:1.5px solid ${ans==='True'?'var(--accent)':'var(--border)'};
               background:${ans==='True'?'rgba(79,142,247,.15)':'transparent'};
               color:${ans==='True'?'var(--accent)':'var(--text)'};
               font-family:'DM Mono',monospace;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .2s;box-sizing:border-box;text-align:center;">
        ✓ True
      </button>
      <button onclick="ptSelectStepper(${c.id},'False')"
        style="flex:1;padding:.75rem .5rem;border-radius:10px;border:1.5px solid ${ans==='False'?'var(--accent)':'var(--border)'};
               background:${ans==='False'?'rgba(79,142,247,.15)':'transparent'};
               color:${ans==='False'?'var(--accent)':'var(--text)'};
               font-family:'DM Mono',monospace;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .2s;box-sizing:border-box;text-align:center;">
        ✗ False
      </button>
    </div>`;
  }

  if (qtype === 'yes_no') {
    return `<div style="display:flex;gap:.5rem;">
      ${['Yes','No','Unsure'].map(v => `
        <button onclick="ptSelectStepper(${c.id},'${v}')"
          style="flex:1;padding:.7rem .4rem;border-radius:10px;border:1.5px solid ${ans===v?'var(--accent)':'var(--border)'};
                 background:${ans===v?'rgba(79,142,247,.15)':'transparent'};
                 color:${ans===v?'var(--accent)':'var(--text)'};
                 font-family:'DM Mono',monospace;font-size:.8rem;font-weight:600;cursor:pointer;transition:all .2s;">
          ${v}
        </button>`).join('')}
    </div>`;
  }

  if (qtype === 'multiple_choice') {
    let opts = [];
    try { opts = JSON.parse(c.options || '[]'); } catch(_) {}
    return `<div style="display:flex;flex-direction:column;gap:.5rem;">
      ${opts.map((opt, idx) => `
        <button onclick="ptSelectStepper(${c.id},'${idx}')"
          style="padding:.7rem .9rem;border-radius:10px;text-align:left;border:1.5px solid ${String(ans)===String(idx)?'var(--accent)':'var(--border)'};
                 background:${String(ans)===String(idx)?'rgba(79,142,247,.1)':'transparent'};
                 color:${String(ans)===String(idx)?'var(--accent)':'var(--text)'};font-size:.88rem;cursor:pointer;transition:all .2s;">
          <span style="font-family:'DM Mono',monospace;font-size:.7rem;opacity:.6;margin-right:.5rem;">${String.fromCharCode(65+idx)}.</span>
          ${opt}
        </button>`).join('')}
    </div>`;
  }

  if (qtype === 'scale') {
    const cur = ans !== undefined ? ans : '3';
    return `<div>
      <input type="range" min="1" max="5" value="${cur}" id="pt-scale-${c.id}"
        oninput="ptSelectStepper(${c.id}, this.value); document.getElementById('pt-scale-lbl-${c.id}').textContent=this.value"
        style="width:100%;accent-color:var(--accent);">
      <div style="display:flex;justify-content:space-between;font-size:.72rem;color:var(--muted);margin-top:.35rem;">
        <span>1 — Strongly Disagree</span>
        <span id="pt-scale-lbl-${c.id}" style="color:var(--accent);font-weight:700">${cur}</span>
        <span>5 — Strongly Agree</span>
      </div>
    </div>`;
  }

  // open-ended
  return `<textarea id="pt-open-${c.id}" rows="3"
    placeholder="Type your answer…"
    oninput="ptSelectStepper(${c.id}, this.value)"
    style="width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;
           padding:.65rem .85rem;color:var(--text);font-size:.88rem;resize:vertical;">${ans||''}</textarea>`;
}

function ptSelectStepper(id, value) {
  ptAnswers[id] = value;
  const body = document.getElementById('sp-pretest-modal-body');
  if (body) _renderPretestStep(body);
}

function _ptNext() {
  if (_ptStep < PT_CLAIMS.length - 1) {
    _ptStep++;
    const body = document.getElementById('sp-pretest-modal-body');
    if (body) _renderPretestStep(body);
  }
}

function _ptBack() {
  if (_ptStep > 0) {
    _ptStep--;
    const body = document.getElementById('sp-pretest-modal-body');
    if (body) _renderPretestStep(body);
  }
}

const ptAnswers = {}; // { claim_id: answer }

function ptSelect(id, value) {
  ptSelectStepper(id, value);
}

async function submitPretest() {
  const payload = {
    session_token: ptGetSession(),
    user_id:       ptGetUserId(),
    answers:       {},
  };
  PT_CLAIMS.forEach(c => { payload.answers[c.id] = ptAnswers[c.id] || ''; });

  const btn = document.getElementById('pt-submit-btn');
  if (btn) { btn.textContent = 'Submitting…'; btn.disabled = true; }

  try {
    const r = await fetch(`${API_BASE}/quiz/pretest/submit`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error('server error');
    const data = await r.json();
    localStorage.setItem('sp_pretest_done',  'true');
    localStorage.setItem('sp_pretest_score', String(data.score_pct ?? ''));
    renderPretestResult(data);
  } catch(e) {
    // Fail gracefully — store flag and close so the user isn't blocked
    localStorage.setItem('sp_pretest_done', 'true');
    ptHideOverlay('sp-pretest-overlay');
  }
}

function renderPretestResult(data) {
  const body = document.getElementById('sp-pretest-modal-body');
  if (!body) return;
  body.innerHTML = `
    <div style="text-align:center;padding:.5rem 0 1.5rem;">
      <div style="font-size:2.5rem;margin-bottom:.6rem;">✅</div>
      <div style="font-family:'Syne',sans-serif;font-weight:800;font-size:1.4rem;margin-bottom:.3rem;">Baseline recorded!</div>
      <div style="color:var(--muted);font-size:.84rem;line-height:1.6;max-width:360px;margin:.75rem auto .5rem;">
        Thanks — that's all we need. Finish the lessons and we'll show you exactly how much you've improved.
      </div>
    </div>
    <button onclick="ptHideOverlay('sp-pretest-overlay')"
      style="width:100%;padding:.8rem;border-radius:12px;border:none;background:var(--accent);color:#fff;font-family:'Syne',sans-serif;font-weight:700;font-size:.95rem;cursor:pointer;">
      Start Learning →
    </button>`;
}

function ptSkip() {
  // Suppress for the rest of this browser session only; shows again on next visit
  sessionStorage.setItem('sp_pretest_skipped', 'true');
  ptHideOverlay('sp-pretest-overlay');
}

// ══ POSTTEST ══════════════════════════════════════════════════════════════════

let _posttestTriggered = false;

function checkPosttestTrigger() {
  if (_posttestTriggered)                         return;
  if (!localStorage.getItem('sp_pretest_done'))   return; // pretest must come first
  if (localStorage.getItem('sp_posttest_done'))   return; // already completed
  if (typeof TECHNIQUES === 'undefined') return;
  const allTried = TECHNIQUES.every(t => pbGetProgress(t.id).tried);
  if (!allTried) return;

  _posttestTriggered = true;
  injectPosttestBanner();
}

function injectPosttestBanner() {
  // Avoid duplicate banners
  if (document.getElementById('sp-posttest-banner')) return;

  const area = document.getElementById('pb-module-area');
  if (!area) return;

  const banner = document.createElement('div');
  banner.id = 'sp-posttest-banner';
  banner.style.cssText = [
    'margin-top:2rem',
    'padding:1.5rem 1.75rem',
    'background:linear-gradient(135deg,rgba(79,142,247,.08),rgba(139,92,246,.08))',
    'border:1px solid rgba(79,142,247,.3)',
    'border-radius:16px',
    'text-align:center',
  ].join(';');
  banner.innerHTML = `
    <div style="font-size:1.8rem;margin-bottom:.5rem;">🏆</div>
    <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:1.1rem;margin-bottom:.4rem;">
      You've completed all the lessons!
    </div>
    <div style="color:var(--muted);font-size:.88rem;line-height:1.6;margin-bottom:1.1rem;max-width:400px;margin-left:auto;margin-right:auto;">
      Ready to see how far you've come? Take the post-test and we'll show you your before vs. after score.
    </div>
    <button onclick="openPosttest()"
      style="padding:.7rem 1.8rem;border-radius:10px;border:none;background:var(--accent);color:#fff;font-family:'Syne',sans-serif;font-weight:700;font-size:.9rem;cursor:pointer;transition:all .2s;">
      Reveal my improvement →
    </button>`;
  area.appendChild(banner);
}

function renderPosttestLoginPrompt() {
  const body = document.getElementById('sp-posttest-modal-body');
  if (!body) return;
  body.innerHTML = `
    <div style="text-align:center;padding:.5rem 0 1.5rem;">
      <div style="font-size:2.5rem;margin-bottom:.6rem;">🏆</div>
      <div style="font-family:'Syne',sans-serif;font-weight:800;font-size:1.4rem;margin-bottom:.4rem;">See how much you've improved</div>
      <div style="color:var(--muted);font-size:.88rem;line-height:1.6;max-width:340px;margin:0 auto 1.25rem;">
        Create a free account to take the post-test and unlock your before vs. after score comparison.
      </div>
      <div style="display:flex;flex-direction:column;gap:.65rem;max-width:300px;margin:0 auto;">
        <a href="login.html#register"
          style="display:block;padding:.8rem;border-radius:12px;border:none;background:var(--accent);color:#fff;font-family:'Syne',sans-serif;font-weight:700;font-size:.95rem;text-decoration:none;text-align:center;">
          Create an account →
        </a>
        <a href="login.html"
          style="display:block;padding:.75rem;border-radius:12px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Syne',sans-serif;font-weight:600;font-size:.88rem;text-decoration:none;text-align:center;">
          Log in
        </a>
        <button onclick="ptHideOverlay('sp-posttest-overlay')"
          style="background:none;border:none;color:var(--muted);font-size:.75rem;cursor:pointer;font-family:'DM Mono',monospace;letter-spacing:.03em;padding:.4rem;">
          Maybe later
        </button>
      </div>
    </div>`;
}

async function openPosttest() {
  // Post-test requires login so scores can be linked and delta calculated
  const userId = localStorage.getItem('sp_user_id');
  if (!userId) {
    renderPosttestLoginPrompt();
    ptShowOverlay('sp-posttest-overlay');
    return;
  }
  if (!PT_CLAIMS.length) {
    try {
      const r = await fetch(`${API_BASE}/quiz/pretest`, { credentials: 'include' });
      if (!r.ok) return;
      const d = await r.json();
      PT_CLAIMS.push(...(d.claims || []));
    } catch(e) { return; }
  }
  renderPosttestModal();
  ptShowOverlay('sp-posttest-overlay');
}

const pttAnswers = {}; // { claim_id: 'True' | 'False' }

let _pttStep = 0; // current stepper index for posttest

function renderPosttestModal() {
  // Clear any stale answers from a previous attempt
  Object.keys(pttAnswers).forEach(k => delete pttAnswers[k]);
  _pttStep = 0;
  const body = document.getElementById('sp-posttest-modal-body');
  if (body) _renderPosttestStep(body);
}

function _renderPosttestStep(body) {
  const total = PT_CLAIMS.length;
  const c = PT_CLAIMS[_pttStep];
  const isLast = _pttStep === total - 1;
  const allAnswered = Object.keys(pttAnswers).length === total;
  const preScore = localStorage.getItem('sp_pretest_score');
  const preHint  = preScore ? `Your baseline was <strong>${preScore}%</strong>` : '';

  body.innerHTML = `
    <div style="text-align:center;margin-bottom:1.25rem;">
      <div style="font-size:2rem;margin-bottom:.4rem;">🧠</div>
      <div style="font-family:'Syne',sans-serif;font-weight:800;font-size:1.3rem;margin-bottom:.3rem;">Time to see your growth</div>
      ${preHint ? `<div style="color:var(--muted);font-size:.8rem;">${preHint}</div>` : ''}
    </div>

    <!-- Stepper dots -->
    <div style="display:flex;align-items:center;justify-content:center;gap:6px;margin-bottom:1.5rem;">
      ${PT_CLAIMS.map((cl, i) => `
        <div style="width:${i === _pttStep ? '24px' : '8px'};height:8px;border-radius:4px;
             background:${pttAnswers[cl.id] ? 'var(--green)' : i === _pttStep ? 'var(--accent)' : 'var(--border)'};
             transition:all .25s;"></div>
      `).join('')}
    </div>

    <!-- Question label -->
    <div style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--accent);letter-spacing:.08em;margin-bottom:.5rem;">
      QUESTION ${_pttStep + 1} OF ${total}
    </div>

    <!-- Question card -->
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:1.25rem 1.3rem;margin-bottom:1.25rem;min-height:90px;display:flex;align-items:center;">
      <div style="font-size:.95rem;line-height:1.65;color:var(--text);">${c.text}</div>
    </div>

    <!-- True / False buttons -->
    <div style="display:flex;gap:.75rem;margin-bottom:1.5rem;">
      <button id="ptt-true-${c.id}" onclick="pttSelectStepper(${c.id},'True')"
        style="flex:1;padding:.75rem .5rem;border-radius:10px;border:1.5px solid ${pttAnswers[c.id]==='True'?'var(--accent)':'var(--border)'};
               background:${pttAnswers[c.id]==='True'?'rgba(79,142,247,.15)':'transparent'};
               color:${pttAnswers[c.id]==='True'?'var(--accent)':'var(--text)'};
               font-family:'DM Mono',monospace;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .2s;box-sizing:border-box;text-align:center;">
        ✓ True
      </button>
      <button id="ptt-false-${c.id}" onclick="pttSelectStepper(${c.id},'False')"
        style="flex:1;padding:.75rem .5rem;border-radius:10px;border:1.5px solid ${pttAnswers[c.id]==='False'?'var(--accent)':'var(--border)'};
               background:${pttAnswers[c.id]==='False'?'rgba(79,142,247,.15)':'transparent'};
               color:${pttAnswers[c.id]==='False'?'var(--accent)':'var(--text)'};
               font-family:'DM Mono',monospace;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .2s;box-sizing:border-box;text-align:center;">
        ✗ False
      </button>
    </div>

    <!-- Navigation -->
    <div style="display:flex;gap:.75rem;align-items:center;">
      ${_pttStep > 0 ? `
        <button onclick="_pttBack()"
          style="flex:1;padding:.7rem;border-radius:10px;border:1px solid var(--border);background:transparent;
                 color:var(--muted);font-family:'Syne',sans-serif;font-weight:600;font-size:.88rem;cursor:pointer;">
          ← Back
        </button>` : '<div style="flex:1;"></div>'}
      ${isLast ? `
        <button id="ptt-submit-btn" onclick="submitPosttest()" ${allAnswered ? '' : 'disabled'}
          style="flex:2;padding:.75rem;border-radius:10px;border:none;background:var(--accent);color:#fff;
                 font-family:'Syne',sans-serif;font-weight:700;font-size:.9rem;
                 opacity:${allAnswered ? '1' : '.4'};cursor:${allAnswered ? 'pointer' : 'not-allowed'};transition:all .2s;">
          Submit & See Results →
        </button>` : `
        <button onclick="_pttNext()" ${pttAnswers[c.id] ? '' : 'disabled'}
          style="flex:2;padding:.75rem;border-radius:10px;border:none;
                 background:${pttAnswers[c.id] ? 'var(--accent)' : 'var(--surface)'};
                 color:${pttAnswers[c.id] ? '#fff' : 'var(--muted)'};
                 font-family:'Syne',sans-serif;font-weight:700;font-size:.9rem;
                 cursor:${pttAnswers[c.id] ? 'pointer' : 'not-allowed'};transition:all .2s;
                 border:1px solid ${pttAnswers[c.id] ? 'transparent' : 'var(--border)'}>
          Next →
        </button>`}
    </div>`;
}

function pttSelectStepper(id, value) {
  pttAnswers[id] = value;
  const body = document.getElementById('sp-posttest-modal-body');
  if (body) _renderPosttestStep(body);
}

function _pttNext() {
  if (_pttStep < PT_CLAIMS.length - 1) {
    _pttStep++;
    const body = document.getElementById('sp-posttest-modal-body');
    if (body) _renderPosttestStep(body);
  }
}

function _pttBack() {
  if (_pttStep > 0) {
    _pttStep--;
    const body = document.getElementById('sp-posttest-modal-body');
    if (body) _renderPosttestStep(body);
  }
}

function pttSelect(id, value) {
  pttAnswers[id] = value;

  const trueBtn  = document.getElementById(`ptt-true-${id}`);
  const falseBtn = document.getElementById(`ptt-false-${id}`);
  if (!trueBtn || !falseBtn) return;

  [trueBtn, falseBtn].forEach(btn => {
    btn.style.borderColor = 'var(--border)';
    btn.style.background  = 'transparent';
    btn.style.color       = 'var(--text)';
  });
  const picked = value === 'True' ? trueBtn : falseBtn;
  picked.style.borderColor = 'var(--accent)';
  picked.style.background  = 'rgba(79,142,247,.15)';
  picked.style.color       = 'var(--accent)';

  if (Object.keys(pttAnswers).length === PT_CLAIMS.length) {
    const btn = document.getElementById('ptt-submit-btn');
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.style.cursor = 'pointer'; }
  }
}

async function submitPosttest() {
  const preScore = parseInt(localStorage.getItem('sp_pretest_score') || '0') || null;
  const payload  = {
    session_token: ptGetSession(),
    user_id:       ptGetUserId(),
    answers:       {},
    pretest_score: preScore,
  };
  PT_CLAIMS.forEach(c => { payload.answers[c.id] = pttAnswers[c.id] || ''; });

  const btn = document.getElementById('ptt-submit-btn');
  if (btn) { btn.textContent = 'Submitting…'; btn.disabled = true; }

  try {
    const r = await fetch(`${API_BASE}/quiz/posttest/submit`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error('server error');
    const data = await r.json();
    localStorage.setItem('sp_posttest_done',  'true');
    localStorage.setItem('sp_posttest_score', String(data.score_pct ?? ''));
    renderPosttestResult(data);
  } catch(e) {
    localStorage.setItem('sp_posttest_done', 'true');
    ptHideOverlay('sp-posttest-overlay');
  }
}

function renderPosttestResult(data) {
  const body = document.getElementById('sp-posttest-modal-body');
  if (!body) return;

  const pct      = data.score_pct ?? 0;
  const delta    = data.delta;      // may be null if no pretest on record server-side
  const improved = typeof delta === 'number' && delta > 0;
  const prePct   = parseInt(localStorage.getItem('sp_pretest_score') || '0');

  // Delta comparison strip — only shown when the server returns a delta
  let deltaHtml = '';
  if (typeof delta === 'number') {
    const dColor = delta > 0 ? 'var(--green)' : delta < 0 ? 'var(--red)' : 'var(--muted)';
    const dSign  = delta > 0 ? '+' : '';
    const dMsg   = delta > 0
      ? '🎉 You improved — great work!'
      : delta < 0
        ? '📖 Keep practising — the lessons are always here.'
        : '➡️ Same score — keep building with the quiz!';

    deltaHtml = `
      <div style="display:flex;gap:.75rem;justify-content:center;align-items:stretch;margin:.85rem 0 .5rem;flex-wrap:wrap;">
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.6rem 1.2rem;text-align:center;min-width:80px;">
          <div style="font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.25rem;">PRE-TEST</div>
          <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:1.25rem;">${prePct}%</div>
        </div>
        <div style="display:flex;align-items:center;font-size:1.2rem;color:var(--muted);">→</div>
        <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:.6rem 1.2rem;text-align:center;min-width:80px;">
          <div style="font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.25rem;">POST-TEST</div>
          <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:1.25rem;">${pct}%</div>
        </div>
        <div style="display:flex;align-items:center;font-size:1.2rem;color:var(--muted);">→</div>
        <div style="background:${dColor}22;border:1px solid ${dColor}55;border-radius:10px;padding:.6rem 1.2rem;text-align:center;min-width:80px;">
          <div style="font-family:'DM Mono',monospace;font-size:.62rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.25rem;">CHANGE</div>
          <div style="font-family:'Syne',sans-serif;font-weight:700;font-size:1.25rem;color:${dColor};">${dSign}${delta}%</div>
        </div>
      </div>
      <div style="color:var(--muted);font-size:.83rem;margin-bottom:.85rem;">${dMsg}</div>`;
  }

  body.innerHTML = `
    <div style="text-align:center;padding:.5rem 0 .75rem;">
      <div style="font-size:2.5rem;margin-bottom:.5rem;">${improved ? '🏆' : '📊'}</div>
      <div style="font-family:'Syne',sans-serif;font-weight:800;font-size:1.4rem;margin-bottom:.3rem;">Post-test complete!</div>
      ${deltaHtml}
      <div style="color:var(--muted);font-size:.83rem;line-height:1.6;max-width:360px;margin:0 auto .9rem;">
        Your full results are on the dashboard. Keep practising with the quiz to keep building your skills.
      </div>
    </div>
    <div style="display:flex;gap:.75rem;justify-content:center;flex-wrap:wrap;">
      <button onclick="ptHideOverlay('sp-posttest-overlay')"
        style="padding:.7rem 1.4rem;border-radius:10px;border:none;background:var(--accent);color:#fff;font-family:'Syne',sans-serif;font-weight:700;font-size:.9rem;cursor:pointer;">
        Done
      </button>
      <a href="dashboard.html"
        style="display:inline-flex;align-items:center;gap:.35rem;padding:.7rem 1.4rem;border-radius:10px;border:1px solid var(--border);background:transparent;color:var(--text);font-family:'Syne',sans-serif;font-weight:600;font-size:.9rem;text-decoration:none;">
        View Dashboard →
      </a>
    </div>`;
  const banner = document.getElementById('sp-posttest-banner');
  if (banner) {
    banner.style.background   = 'rgba(52,211,153,.06)';
    banner.style.borderColor  = 'rgba(52,211,153,.3)';
    const dLabel = typeof delta === 'number'
      ? ` (${delta >= 0 ? '+' : ''}${delta}% vs baseline)`
      : '';
    banner.innerHTML = `
      <div style="color:var(--green);font-family:'Syne',sans-serif;font-weight:600;font-size:.95rem;">
        ✓ Post-test completed — ${pct}% score${dLabel}
      </div>`;
  }
}

// ── Patch pbRenderProgress so posttest is checked after every progress update ─
if (typeof pbRenderProgress === 'function') {
  const _origPbRenderProgress = pbRenderProgress;
  pbRenderProgress = function () {
    _origPbRenderProgress.call(this);
    checkPosttestTrigger();
  };
}

// ── Boot — small delay so page paint completes before the modal appears ────────
setTimeout(initPretest, 450);
// ══ END PRE-TEST / POST-TEST ══════════════════════════════════════════════════

async function _loadQuizResultsStats() {
  const container = document.getElementById('quiz-results-stats-container');
  if (!container) return;
  const userId = parseInt(localStorage.getItem('sp_user_id'));
  if (!userId) {
    container.innerHTML = '<p style="color:var(--muted);font-size:.8rem;text-align:center;">Log in to track your stats.</p>';
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/quiz/stats/${userId}`, { credentials: 'include' });
    if (!res.ok) throw new Error();
    const rows = await res.json();
    if (!rows.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:.8rem;text-align:center;">No stats yet.</p>';
      return;
    }
    container.innerHTML = rows.map(r => {
      const pct = r.accuracy_pct ?? 0;
      const c = pct >= 80 ? 'var(--green,#22c55e)' : pct >= 50 ? 'var(--yellow,#fbbf24)' : 'var(--red,#ef4444)';
      const label = (r.topic || 'general').replace(/_/g,' ').replace(/\b\w/g, l => l.toUpperCase());
      return `<div style="display:flex;align-items:center;gap:.75rem;font-size:.8rem;margin-bottom:.5rem;">
        <span style="min-width:155px;color:var(--text);">${label}</span>
        <div style="flex:1;height:6px;border-radius:3px;background:var(--border);overflow:hidden;"><div style="height:100%;width:${pct}%;background:${c};border-radius:3px;"></div></div>
        <span style="min-width:40px;text-align:right;font-family:'DM Mono',monospace;color:${c};font-weight:600;">${pct}%</span>
        <span style="color:var(--muted);min-width:48px;text-align:right;">${r.topic_correct}/${r.topic_attempts}</span>
      </div>`;
    }).join('');
  } catch {
    container.innerHTML = '<p style="color:var(--muted);font-size:.8rem;text-align:center;">Could not load stats.</p>';
  }
}

function quizRetry() {
  document.getElementById('quiz-screen-results').style.display = 'none';
  document.getElementById('quiz-screen-start').style.display = '';
}


// ══ LESSONS ADMIN PANEL ══════════════════════════════════════════════════════
// Injected when the user is an admin. Adds: edit, deactivate, delete controls
// on each lesson card, plus a floating "Add Lesson" button.

const _isAdmin = localStorage.getItem('sp_role') === 'admin';

function _getAuthHeader() {
  const token = document.cookie.split(';').map(c => c.trim())
    .find(c => c.startsWith('sp_jwt='));
  if (token) return { Authorization: `Bearer ${token.split('=')[1]}` };
  const ls = localStorage.getItem('sp_token');
  if (ls) return { Authorization: `Bearer ${ls}` };
  return {};
}

// ── Inject admin controls into lesson cards ──────────────────────────────────
const _origRenderGrid = renderGrid;
renderGrid = function(list) {
  _origRenderGrid(list);
  if (!_isAdmin) return;
  // Add admin badge + controls to each card — read lesson id from data attribute
  document.querySelectorAll('.lesson-card').forEach(card => {
    const lessonId = parseInt(card.dataset.lessonId);
    const lessonKey = card.dataset.lessonKey;
    if (!lessonId) return;
    const lesson = LESSONS.find(l => l.id === lessonId || l.key === lessonKey);
    if (!lesson) return;
    if (card.querySelector('.admin-lesson-controls')) return; // already injected

    const isPub = lesson.is_published !== 0 && lesson.is_published !== false;
    const bar = document.createElement('div');
    bar.className = 'admin-lesson-controls';
    bar.style.cssText = 'display:flex;gap:.35rem;justify-content:flex-end;margin-top:.6rem;padding-top:.5rem;border-top:1px solid var(--border)';
    bar.innerHTML = `
      <button class="btn btn-sm btn-icon" title="Edit" onclick="event.stopPropagation();adminEditLesson(${lesson.id})">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
      </button>
      <button class="btn btn-sm btn-icon" title="${isPub ? 'Deactivate' : 'Activate'}"
        onclick="event.stopPropagation();adminToggleLesson(${lesson.id})"
        style="color:${isPub ? 'var(--muted)' : 'var(--green)'}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px">
          <path d="${isPub ? 'M18 6L6 18M6 6l12 12' : 'M20 6L9 17l-5-5'}"/>
        </svg>
      </button>
      <button class="btn btn-sm btn-icon btn-danger" title="Delete" onclick="event.stopPropagation();adminDeleteLesson(${lesson.id},'${(lesson.title||'').replace(/'/g,"\\'").replace(/"/g,'\\"')}')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
      </button>`;
    card.appendChild(bar);
  });
  // Inject floating "Add" button if not present
  if (!document.getElementById('admin-add-lesson-btn')) {
    const fab = document.createElement('button');
    fab.id = 'admin-add-lesson-btn';
    fab.title = 'Add Lesson';
    fab.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:22px;height:22px"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`;
    fab.style.cssText = `position:fixed;bottom:2rem;right:2rem;z-index:900;background:var(--accent);color:#fff;border:none;border-radius:50%;width:52px;height:52px;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.35)`;
    fab.onclick = () => adminOpenLessonModal();
    document.body.appendChild(fab);
  }
};

// ── Admin modal helpers ──────────────────────────────────────────────────────
function _ensureAdminLessonModal() {
  if (document.getElementById('admin-lesson-modal')) return;
  const m = document.createElement('div');
  m.id = 'admin-lesson-modal';
  m.style.cssText = 'display:none;position:fixed;inset:0;z-index:1200;background:rgba(0,0,0,.6);align-items:center;justify-content:center;';
  m.innerHTML = `
    <div style="background:var(--bg-card,#1a1a2e);border:1px solid var(--border);border-radius:12px;padding:1.75rem;max-width:540px;width:94%;max-height:88vh;overflow-y:auto;">
      <h3 id="alm-title" style="margin:0 0 1.2rem;font-size:1rem;font-family:'Syne',sans-serif;">New Lesson</h3>
      <input type="hidden" id="alm-edit-id">
      <label style="font-size:.75rem;color:var(--muted);">Lesson Key *</label>
      <input id="alm-key" style="width:100%;box-sizing:border-box;margin:.3rem 0 .8rem;padding:.55rem .75rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-family:'DM Mono',monospace;font-size:.82rem;">
      <label style="font-size:.75rem;color:var(--muted);">Title *</label>
      <input id="alm-title" style="width:100%;box-sizing:border-box;margin:.3rem 0 .8rem;padding:.55rem .75rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:.82rem;">
      <label style="font-size:.75rem;color:var(--muted);">Content * (HTML supported)</label>
      <textarea id="alm-content" rows="6" style="width:100%;box-sizing:border-box;margin:.3rem 0 .8rem;padding:.55rem .75rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:.82rem;resize:vertical;"></textarea>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin-bottom:.8rem;">
        <div>
          <label style="font-size:.75rem;color:var(--muted);">Topic *</label>
          <select id="alm-topic" style="width:100%;margin-top:.3rem;padding:.5rem .6rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:.8rem;">
            <option value="claim_detection">Claim Detection</option>
            <option value="source_verification">Source Verification</option>
            <option value="bias_detection">Bias Detection</option>
            <option value="evidence_evaluation">Evidence Evaluation</option>
            <option value="general">General MIL</option>
          </select>
        </div>
        <div>
          <label style="font-size:.75rem;color:var(--muted);">Difficulty</label>
          <select id="alm-difficulty" style="width:100%;margin-top:.3rem;padding:.5rem .6rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:.8rem;">
            <option value="beginner">Beginner</option>
            <option value="intermediate">Intermediate</option>
            <option value="advanced">Advanced</option>
          </select>
        </div>
      </div>
      <label style="font-size:.75rem;color:var(--muted);">MIL Skill (optional)</label>
      <input id="alm-milskill" style="width:100%;box-sizing:border-box;margin:.3rem 0 .8rem;padding:.55rem .75rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:.82rem;">
      <label style="font-size:.75rem;color:var(--muted);">Sort Order</label>
      <input id="alm-sort" type="number" style="width:100%;box-sizing:border-box;margin:.3rem 0 .8rem;padding:.55rem .75rem;background:var(--bg,#0f0f1a);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:.82rem;">
      <label style="display:flex;align-items:center;gap:.5rem;font-size:.8rem;margin-bottom:1rem;cursor:pointer;">
        <input type="checkbox" id="alm-published" checked> Published (visible to users)
      </label>
      <div id="alm-error" style="color:var(--red,#ef4444);font-size:.78rem;margin-bottom:.6rem;min-height:1rem;"></div>
      <div style="display:flex;gap:.6rem;justify-content:flex-end;">
        <button onclick="adminCloseLessonModal()" style="background:none;border:1px solid var(--border);border-radius:7px;padding:.5rem 1rem;color:var(--muted);cursor:pointer;font-size:.82rem;">Cancel</button>
        <button onclick="adminSaveLesson()" style="background:var(--accent);border:none;border-radius:7px;padding:.5rem 1.2rem;color:#fff;cursor:pointer;font-size:.82rem;font-weight:600;">Save</button>
      </div>
    </div>`;
  document.body.appendChild(m);
}

function adminOpenLessonModal(data = null) {
  _ensureAdminLessonModal();
  const m = document.getElementById('admin-lesson-modal');
  document.getElementById('alm-edit-id').value   = '';
  document.getElementById('alm-title').textContent = data ? 'Edit Lesson' : 'New Lesson';
  document.getElementById('alm-title-el') && (document.getElementById('alm-title-el').textContent = data ? 'Edit Lesson' : 'New Lesson');
  document.getElementById('alm-title').value  = '';
  ['alm-key','alm-title','alm-content','alm-milskill','alm-sort'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  document.getElementById('alm-topic').value      = 'claim_detection';
  document.getElementById('alm-difficulty').value = 'beginner';
  document.getElementById('alm-published').checked = true;
  document.getElementById('alm-error').textContent = '';
  document.getElementById('alm-title-text') && (document.getElementById('alm-title-text').textContent = data ? 'Edit Lesson' : 'New Lesson');

  if (data) {
    document.getElementById('alm-edit-id').value    = data.id;
    document.getElementById('alm-key').value        = data.lesson_key || '';
    document.getElementById('alm-key').disabled     = true;
    document.getElementById('alm-title').value      = data.title || '';
    document.getElementById('alm-content').value    = data.content || '';
    document.getElementById('alm-topic').value      = data.topic || 'general';
    document.getElementById('alm-difficulty').value = data.difficulty || 'beginner';
    document.getElementById('alm-milskill').value   = data.mil_skill || '';
    document.getElementById('alm-sort').value       = data.sort_order ?? '';
    document.getElementById('alm-published').checked = data.is_published !== 0 && data.is_published !== false;
    document.getElementById('alm-title').setAttribute('data-heading', 'Edit Lesson');
  } else {
    document.getElementById('alm-key').disabled = false;
    document.getElementById('alm-title').setAttribute('data-heading', 'New Lesson');
  }
  // Update heading
  const h = m.querySelector('#alm-title-node') || m.querySelector('h3');
  if (h) h.textContent = data ? 'Edit Lesson' : 'New Lesson';

  m.style.display = 'flex';
}

function adminEditLesson(id) {
  const l = LESSONS.find(x => x.id === id);
  if (l) adminOpenLessonModal(l);
}

function adminCloseLessonModal() {
  const m = document.getElementById('admin-lesson-modal');
  if (m) m.style.display = 'none';
}

async function adminSaveLesson() {
  const errEl  = document.getElementById('alm-error');
  errEl.textContent = '';
  const editId = document.getElementById('alm-edit-id').value;
  const body   = {
    lesson_key:  document.getElementById('alm-key').value.trim(),
    title:       document.getElementById('alm-title').value.trim(),
    content:     document.getElementById('alm-content').value.trim(),
    topic:       document.getElementById('alm-topic').value,
    difficulty:  document.getElementById('alm-difficulty').value,
    mil_skill:   document.getElementById('alm-milskill').value.trim() || null,
    sort_order:  parseInt(document.getElementById('alm-sort').value) || null,
    is_published: document.getElementById('alm-published').checked,
  };
  if (!body.title || !body.content)           { errEl.textContent = 'Title and content are required.'; return; }
  if (!editId && !body.lesson_key)             { errEl.textContent = 'Lesson key is required.'; return; }

  const url    = editId ? `${API_BASE}/lessons/${editId}` : `${API_BASE}/lessons`;
  const method = editId ? 'PUT' : 'POST';
  try {
    const res = await fetch(url, {
      method, credentials: 'include',
      headers: { 'Content-Type': 'application/json', ..._getAuthHeader() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      errEl.textContent = err.detail || `Error ${res.status}`;
      return;
    }
    adminCloseLessonModal();
    await loadLessons();
    _adminToast(editId ? 'Lesson updated.' : 'Lesson created.');
  } catch(e) {
    errEl.textContent = e.message || 'Network error.';
  }
}

async function adminToggleLesson(id) {
  try {
    const res = await fetch(`${API_BASE}/lessons/${id}/toggle-published`, {
      method: 'PATCH', credentials: 'include',
      headers: { ..._getAuthHeader() },
    });
    if (!res.ok) throw new Error(`Error ${res.status}`);
    await loadLessons();
    _adminToast('Lesson status updated.');
  } catch(e) {
    _adminToast(e.message || 'Could not toggle lesson.', 'error');
  }
}

async function adminDeleteLesson(id, title) {
  if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
  try {
    const res = await fetch(`${API_BASE}/lessons/${id}`, {
      method: 'DELETE', credentials: 'include',
      headers: { ..._getAuthHeader() },
    });
    if (!res.ok && res.status !== 204) throw new Error(`Error ${res.status}`);
    await loadLessons();
    _adminToast('Lesson deleted.');
  } catch(e) {
    _adminToast(e.message || 'Could not delete lesson.', 'error');
  }
}

function _adminToast(msg, type = 'ok') {
  const t = document.createElement('div');
  t.style.cssText = `position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);z-index:9999;
    background:${type==='error'?'var(--red,#ef4444)':'var(--green,#22c55e)'};color:#fff;
    padding:.55rem 1.25rem;border-radius:8px;font-size:.82rem;font-weight:600;pointer-events:none;
    box-shadow:0 4px 16px rgba(0,0,0,.35)`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2800);
}

// Close admin modal on outside click
document.addEventListener('click', e => {
  const m = document.getElementById('admin-lesson-modal');
  if (m && e.target === m) adminCloseLessonModal();
});

// ══ LESSONS STATS SIDEBAR PANEL ═══════════════════════════════════════════════
// Shows per-topic quiz accuracy for logged-in users (same data as quiz panel,
// surfaced directly in the Lessons page sidebar/panel).

async function loadLessonsStatsPanel() {
  const wrapper   = document.getElementById('lessons-stats-panel-wrapper');
  const container = document.getElementById('lessons-stats-panel');
  if (!container) return;

  const userId = parseInt(localStorage.getItem('sp_user_id'));
  if (!userId) {
    // Not logged in — hide the whole panel entirely
    if (wrapper) wrapper.style.display = 'none';
    return;
  }

  // Show the panel now that we know the user is logged in
  if (wrapper) wrapper.style.display = '';

  container.innerHTML = '<div style="color:var(--muted);font-size:.78rem;text-align:center;">Loading…</div>';
  try {
    const [statsRes, progressRes] = await Promise.all([
      fetch(`${API_BASE}/quiz/stats/${userId}`, { credentials: 'include', headers: _getAuthHeader() }),
      fetch(`${API_BASE}/lessons/completions`, { credentials: 'include', headers: _getAuthHeader() }),
    ]);

    const statsRows   = statsRes.ok   ? await statsRes.json()    : [];
    const completions = progressRes.ok ? await progressRes.json() : [];
    const totalCompleted = completions.length;

    if (!statsRows.length && !totalCompleted) {
      container.innerHTML = `<p style="color:var(--muted);font-size:.8rem;text-align:center;">
        Complete some lessons and quizzes to see your stats here.</p>`;
      return;
    }

    // Build topic order dynamically from whatever topics came back from the API,
    // plus anything already in LESSONS so we never miss a category.
    const seenTopics = new Set([
      ...statsRows.map(r => r.topic),
      ...LESSONS.map(l => l.topic),
    ].filter(Boolean));
    const topicOrder = [...seenTopics];

    const statsByTopic = {};
    statsRows.forEach(r => { statsByTopic[r.topic] = r; });

    const html = topicOrder.map(topic => {
      const r = statsByTopic[topic];
      if (!r) return '';
      const pct = r.accuracy_pct ?? 0;
      const c = pct >= 80 ? 'var(--green,#22c55e)' : pct >= 50 ? 'var(--yellow,#fbbf24)' : 'var(--red,#ef4444)';
      const label = (TOPIC_META[topic] || {}).label ||
                    topic.replace(/_/g,' ').replace(/\b\w/g, l => l.toUpperCase());
      return `<div style="margin-bottom:.65rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.2rem;">
          <span style="font-size:.75rem;color:var(--text);">${label}</span>
          <span style="font-family:'DM Mono',monospace;font-size:.72rem;color:${c};font-weight:600;">${pct}%</span>
        </div>
        <div style="height:5px;border-radius:3px;background:var(--border);overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:${c};border-radius:3px;transition:width .4s;"></div>
        </div>
        <div style="font-size:.68rem;color:var(--muted);margin-top:.15rem;">${r.topic_correct}/${r.topic_attempts} correct</div>
      </div>`;
    }).filter(Boolean).join('');

    container.innerHTML = `
      <div style="font-size:.7rem;color:var(--muted);margin-bottom:.75rem;display:flex;justify-content:space-between;">
        <span>📚 ${totalCompleted} lesson${totalCompleted !== 1 ? 's' : ''} completed</span>
        <a href="#" onclick="loadLessonsStatsPanel();return false" style="color:var(--accent);text-decoration:none;font-size:.68rem;">↺ Refresh</a>
      </div>
      ${html || '<p style="color:var(--muted);font-size:.78rem;">No quiz attempts yet.</p>'}
    `;
  } catch {
    container.innerHTML = '<p style="color:var(--muted);font-size:.8rem;text-align:center;">Could not load stats.</p>';
  }
}

// Auto-load stats panel on page load
document.addEventListener('DOMContentLoaded', loadLessonsStatsPanel);
// Also call right away in case DOMContentLoaded already fired
if (document.readyState !== 'loading') loadLessonsStatsPanel();

