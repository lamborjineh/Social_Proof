// script.js — Shared utilities for all SocialProof pages
// Loaded via <script src="/pages/script.js" defer> in every HTML file.
// Uses var (not const/let) for globals so page-specific JS can safely re-use
// the same names without SyntaxError: Identifier already declared.

// ─────────────────────────────────────────────────────────────────────────────
// Sidebar + Layout
// ─────────────────────────────────────────────────────────────────────────────
var sidebar    = document.getElementById('sidebar');
var mainEl     = document.getElementById('main') || document.getElementById('page-main');
var burgerBtn  = document.getElementById('burger-btn');
var overlay    = document.getElementById('sidebar-overlay');
var sidebarOpen = window.innerWidth >= 900;

function initSidebar() {
  if (!sidebar || !burgerBtn) return;
  if (window.innerWidth < 900) {
    sidebar.classList.add('collapsed');
    if (mainEl) mainEl.classList.remove('expanded');
    burgerBtn.classList.add('visible');
    sidebarOpen = false;
  } else {
    sidebar.classList.remove('collapsed');
    if (mainEl) mainEl.classList.remove('expanded');
    if (overlay) overlay.classList.remove('visible');
    burgerBtn.classList.remove('visible');
    sidebarOpen = true;
  }
}

function toggleSidebar() {
  // Pages with a topnav drawer (login, lessons, mindmap, dashboard)
  var drawer  = document.getElementById('topnav-drawer');
  var topOverlay = document.getElementById('sidebar-overlay');
  if (drawer) {
    var open = drawer.classList.toggle('open');
    if (topOverlay) topOverlay.classList.toggle('open', open);
    document.body.style.overflow = open ? 'hidden' : '';
    return;
  }
  // Pages with a sidebar (admin-dashboard — has its own override, but guard here anyway)
  if (!sidebar || !burgerBtn) return;
  sidebarOpen = !sidebarOpen;
  if (sidebarOpen) {
    sidebar.classList.remove('collapsed');
    sidebar.classList.add('open');
    if (window.innerWidth >= 900) { if (mainEl) mainEl.classList.remove('expanded'); }
    else { if (overlay) overlay.classList.add('visible'); }
    burgerBtn.classList.remove('visible');
  } else {
    sidebar.classList.add('collapsed');
    sidebar.classList.remove('open');
    if (mainEl) mainEl.classList.add('expanded');
    if (overlay) overlay.classList.remove('visible');
    burgerBtn.classList.add('visible');
  }
}

window.addEventListener('resize', initSidebar);
initSidebar();

// ─────────────────────────────────────────────────────────────────────────────
// Nav Group Toggle (shared — used by Lessons dropdown on every page)
// ─────────────────────────────────────────────────────────────────────────────
function toggleNavGroup(id) {
  var toggle = document.getElementById('nav-' + id + '-toggle');
  var sub    = document.getElementById('nav-sub-' + id);
  if (!toggle || !sub) return;

  // If clicking Lessons from a non-lessons page, navigate to lessons.html
  if (id === 'lessons') {
    var isOnLessons = window.location.pathname.indexOf('lessons.html') !== -1;
    if (!isOnLessons) {
      window.location.href = 'lessons.html';
      return;
    }
  }

  var isOpen = toggle.classList.toggle('open');
  sub.classList.toggle('open', isOpen);
}

// ─────────────────────────────────────────────────────────────────────────────
// Age Mode
// ─────────────────────────────────────────────────────────────────────────────
var AGE_COPY = {
  youth: {
    pageTitle:      'Is This Real?',
    pageSub:        "DON'T GET FOOLED · CHECK IT FAST",
    h2Input:        'Drop the post, message, or story here 👇',
    placeholder:    'Paste or type the thing you want to check…',
    beginBtn:       "Let's Go! →",
    hintClaim:      '⚡ A claim is when someone says something is true — like a fact, not an opinion.',
    verdictPrefix:  '🔍 Here\'s what we found:',
    confidenceLabel:'How sure are we?',
    sharePrompt:    'Show a friend!',
    lessonCTA:      'Level up your skills →',
  },
  adult: {
    pageTitle:      'Review Content',
    pageSub:        'CHECK · THINK · DECIDE',
    h2Input:        'What would you like to review?',
    placeholder:    'Paste or type here',
    beginBtn:       'Start →',
    hintClaim:      '💡 A claim is a statement presented as fact — something that can be verified.',
    verdictPrefix:  'Analysis result:',
    confidenceLabel:'Confidence score',
    sharePrompt:    'Share this result',
    lessonCTA:      'Improve your media literacy →',
  },
  older: {
    pageTitle:      'Check the Facts',
    pageSub:        "IS THIS TRUE? · LET'S FIND OUT",
    h2Input:        'Paste the text you want to check below.',
    placeholder:    'Type or paste the content here, then press Start.',
    beginBtn:       'Start →',
    hintClaim:      '💡 A claim is a statement presented as a fact. We will help you check if it is true.',
    verdictPrefix:  'Here is what we found:',
    confidenceLabel:'How reliable is this?',
    sharePrompt:    'Share with family',
    lessonCTA:      'Learn more at your pace →',
  },
};

// TODO: remove this migration shim after 2026-06 (users holding old 'kids'/'teen' values will have migrated by then)
(function() {
  var raw = localStorage.getItem('sp_age_mode');
  if (raw === 'kids' || raw === 'teen') localStorage.setItem('sp_age_mode', 'youth');
})();

var currentMode = localStorage.getItem('sp_age_mode') || 'adult';

function applyAgeCopy(mode) {
  var copy = AGE_COPY[mode] || AGE_COPY.adult;
  var setTxt  = function(id, txt) { var el = document.getElementById(id); if (el) el.textContent = txt; };
  var setAttr = function(id, attr, val) { var el = document.getElementById(id); if (el) el[attr] = val; };
  setTxt('page-title',       copy.pageTitle);
  setTxt('page-sub',         copy.pageSub);
  setTxt('h2-input',         copy.h2Input);
  setAttr('content-input', 'placeholder', copy.placeholder);
  setTxt('begin-btn',        copy.beginBtn);
  setTxt('hint-claim',       copy.hintClaim);
  setTxt('verdict-prefix',   copy.verdictPrefix);
  setTxt('confidence-label', copy.confidenceLabel);
  setTxt('share-prompt',     copy.sharePrompt);
  setTxt('lesson-cta',       copy.lessonCTA);
  document.body.classList.toggle('mode-youth', mode === 'youth');
  document.body.classList.toggle('mode-older', mode === 'older');
}

function setAgeMode(mode) {
  currentMode = mode;
  localStorage.setItem('sp_age_mode', mode);
  document.querySelectorAll('.age-pill').forEach(function(el) { el.classList.remove('active'); });
  var pill = document.getElementById('age-' + mode);
  if (pill) pill.classList.add('active');
  // Brief flash to signal the mode switch
  document.body.classList.add('mode-switching');
  setTimeout(function() { document.body.classList.remove('mode-switching'); }, 320);
  applyAgeCopy(mode);
}

// Restore pill state on page load
(function restoreAgePill() {
  var saved = localStorage.getItem('sp_age_mode') || 'adult';
  if (saved === 'kids' || saved === 'teen') { saved = 'youth'; localStorage.setItem('sp_age_mode', 'youth'); }
  document.querySelectorAll('.age-pill').forEach(function(el) { el.classList.remove('active'); });
  var pill = document.getElementById('age-' + saved);
  if (pill) pill.classList.add('active');
  applyAgeCopy(saved);
})();

// ─────────────────────────────────────────────────────────────────────────────
// Shared utilities
// ─────────────────────────────────────────────────────────────────────────────
function markLessonRead(lessonKey) {
  var read = JSON.parse(localStorage.getItem('sp_read_lessons') || '[]');
  if (!read.includes(lessonKey)) { read.push(lessonKey); localStorage.setItem('sp_read_lessons', JSON.stringify(read)); }
}

function showToast(msg) {
  var t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(function() { t.classList.remove('show'); }, 3500);
}

// ─────────────────────────────────────────────────────────────────────────────
// Role-aware sidebar: hide user-only nav items from admin accounts.
// Pages mark user-only items with class "user-only-nav".
// Admin sees only: Dashboard panel.
// Users see everything: Practice, Lessons, My Map, Dashboard.
// ─────────────────────────────────────────────────────────────────────────────
(function applyRoleNav() {
  var role = localStorage.getItem('sp_role');
  if (role !== 'admin') return; // non-admins see full nav

  // Hide all elements tagged as user-only
  document.querySelectorAll('.user-only-nav').forEach(function(el) {
    el.style.display = 'none';
  });
})();

(function() {
  var username   = localStorage.getItem('sp_username');
  var loginLink  = document.getElementById('sidebar-login-link');
  var loginLinkM = document.getElementById('sidebar-login-link-m');
  if (!username) return;

  var logoutHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg> Log out';

  async function doLogout(e) {
    e.preventDefault();
    await fetch('/auth/cookie-logout', { method: 'POST', credentials: 'include' }).catch(function() {});
    localStorage.clear();
    window.location.href = 'login.html';
  }

  if (loginLink) {
    loginLink.innerHTML = logoutHTML;
    loginLink.href = '#';
    loginLink.style.color = 'var(--red)';
    loginLink.onclick = doLogout;
  }
  if (loginLinkM) {
    loginLinkM.innerHTML = logoutHTML;
    loginLinkM.href = '#';
    loginLinkM.style.color = 'var(--red)';
    loginLinkM.onclick = doLogout;
  }
})();
