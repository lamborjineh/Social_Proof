// ─────────────────────────────────────────────────────────────────────────────
// SocialProof — dashboard.js  v6.0
//
// v6.0 Changes:
//   - renderSkillProgress(): adds sparkline history bars under each skill ring
//     (per-skill progress over time from user_skill_history). Also adds a
//     per-skill label: "improved" / "needs work" / "stable".
//   - renderReasoningJournal(): new section in the Learning panel that shows
//     the user's last 5 Reasoning Journal entries with Bloom's level badges.
//   - renderConfidenceTrend(): new section showing confidence_before vs
//     confidence_after across the user's last 10 sessions as a mini trend.
//   - renderSourceDiversitySummary(): new section summarising the source
//     diversity breakdown across all sessions (UNESCO MIL alignment).
//   - loadDashboard(): fetches skill_history, journal_entries,
//     confidence_trend, source_diversity_summary from the dashboard endpoint.
//   - All admin panel functions kept unchanged (lines 469+).
// ─────────────────────────────────────────────────────────────────────────────

// ── Age mode ──────────────────────────────────────────────────────────────────
(function restoreAgePill() {
  const saved = localStorage.getItem('sp_age_mode') || 'adult';
  document.querySelectorAll('.age-pill').forEach(el => el.classList.remove('active'));
  const pill = document.getElementById('age-' + saved);
  if (pill) pill.classList.add('active');
  // Brief flash to signal the mode switch
  document.body.classList.add('mode-switching');
  setTimeout(() => document.body.classList.remove('mode-switching'), 320);
  document.body.classList.toggle('mode-youth', saved === 'youth');
  document.body.classList.toggle('mode-older', saved === 'older');
})();

function setAgeMode(mode) {
  localStorage.setItem('sp_age_mode', mode);
  document.querySelectorAll('.age-pill').forEach(el => el.classList.remove('active'));
  const pill = document.getElementById('age-' + mode);
  if (pill) pill.classList.add('active');
  document.body.classList.toggle('mode-youth', mode === 'youth');
  document.body.classList.toggle('mode-older', mode === 'older');
}

// ── Nav group toggle ──────────────────────────────────────────────────────────
function toggleNavGroup(id) {
  const toggle = document.getElementById('nav-' + id + '-toggle');
  const sub    = document.getElementById('nav-sub-' + id);
  if (!toggle || !sub) return;
  const isOpen = toggle.classList.toggle('open');
  sub.classList.toggle('open', isOpen);
}

// ── Build user dashboard sub-nav ──────────────────────────────────────────────
function buildDashboardSubNav(role) {
  const sub = document.getElementById('nav-sub-dashboard');
  if (!sub) return;

  const userItems = [
    { icon: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>', label: 'Overview', section: 'section-overview' },
    { icon: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>', label: 'Skills', section: 'section-skills' },
    { icon: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>', label: 'Learning', section: 'section-learning' },
    { icon: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>', label: 'History', section: 'section-history' },
  ];

  const items = userItems;
  sub.innerHTML = items.map(item => {
    if (item.children) {
      // Dropdown group
      const childBtns = item.children.map(c =>
        `<button class="nav-sub-child" id="nav-sub-${c.panel}" onclick="showAdminPanel('${c.panel}')">
          ${c.label}
        </button>`
      ).join('');
      return `<div class="nav-dropdown-group" id="nav-group-${item.group}">
        <button class="nav-sub-item nav-dropdown-toggle" onclick="toggleNavDropdown('${item.group}')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${item.icon}</svg>
          ${item.label}
          <svg class="nav-dropdown-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;margin-left:auto;transition:transform .2s"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div class="nav-dropdown-children">${childBtns}</div>
      </div>`;
    }
    if (item.panel) {
      return `<button class="nav-sub-item" id="nav-sub-${item.panel}" onclick="showAdminPanel('${item.panel}')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${item.icon}</svg>
        ${item.label}
      </button>`;
    }
    return `<button class="nav-sub-item" onclick="scrollToSection('${item.section}', this)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${item.icon}</svg>
      ${item.label}
    </button>`;
  }).join('');
}

function toggleNavDropdown(group) {
  const groupEl = document.getElementById('nav-group-' + group);
  if (!groupEl) return;
  groupEl.classList.toggle('open');
}

function scrollToSection(id, clickedBtn) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  document.querySelectorAll('#nav-sub-dashboard .nav-sub-item').forEach(n => n.classList.remove('active'));
  if (clickedBtn) clickedBtn.classList.add('active');
}

// ── Auth state ────────────────────────────────────────────────────────────────
const API = '';
(function initUserDashboard() {
  const username  = localStorage.getItem('sp_username');
  const userId    = localStorage.getItem('sp_user_id');
  const role      = localStorage.getItem('sp_role');
  const loginLink  = document.getElementById('sidebar-login-link');
  const loginLinkM = document.getElementById('sidebar-login-link-m');

  // Redirect admins to their own dashboard
  if (role === 'admin') {
    window.location.replace('admin-dashboard.html');
    return;
  }

  buildDashboardSubNav('user');
  setTimeout(function() {
    const firstSub = document.querySelector('#nav-sub-dashboard .nav-sub-item');
    if (firstSub) firstSub.classList.add('active');
  }, 0);

  if (username) {
    const _tuEl = document.getElementById('topbar-username');
    if (_tuEl) _tuEl.textContent = username;
    const logoutHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>Log out`;
    const doLogout = async e => {
      e.preventDefault();
      await fetch('/auth/cookie-logout', { method: 'POST', credentials: 'include' }).catch(() => {});
      localStorage.clear();
      window.location.href = 'login.html';
    };
    if (loginLink)  { loginLink.innerHTML  = logoutHTML; loginLink.href  = '#'; loginLink.style.color  = 'var(--red)'; loginLink.onclick  = doLogout; }
    if (loginLinkM) { loginLinkM.innerHTML = logoutHTML; loginLinkM.href = '#'; loginLinkM.style.color = 'var(--red)'; loginLinkM.onclick = doLogout; }
    document.getElementById('gate-section').style.display    = 'none';
    document.getElementById('dashboard-content').style.display = 'block';
    loadDashboard(userId);
  } else {
    // Not logged in — show gate
    document.getElementById('gate-section').style.display    = 'block';
    document.getElementById('dashboard-content').style.display = 'none';
  }
})();

// ── Dashboard loader ──────────────────────────────────────────────────────────
async function loadDashboard(userId) {
  if (!userId) return;
  try {
    const res = await fetch(`/dashboard/${userId}`, { credentials: 'include' });
    if (!res.ok) throw new Error('Could not load dashboard data.');
    const d = await res.json();

    document.getElementById('stat-evals').textContent   = d.stats.total_submissions   ?? '0';
    document.getElementById('stat-lessons').textContent = d.stats.lessons_completed   ?? '0';
    document.getElementById('stat-streak').textContent  = d.stats.quiz_streak         ?? '0';
    document.getElementById('stat-quiz').textContent    = d.stats.total_quiz_attempts ?? '0';

    checkNewUser(d.stats);

    if (d.activity_by_day) renderStreak(d.activity_by_day);
    // ── v6.0: Skill progress with history sparklines
    renderSkillProgress(d.skill_progress || [], d.skill_history || []);

    if ((d.behavior_cards || []).length > 0 || (d.lesson_triggers || []).length > 0) {
      document.getElementById('insights-wrap').style.display = 'block';
      renderBehaviorCards(d.behavior_cards || []);
      renderWeaknessBars(d.lesson_triggers || []);
    }

    renderRecommended(d.recommended || []);
    renderPretest(d.pretest);
    renderQuizHistory(d.quiz_history || []);
    renderHistory(d.history || []);

    // ── Quiz performance by topic (user-facing) ──
    loadDashQuizStats(userId);

    // ── v6.0: New sections
    if (d.journal_entries && d.journal_entries.length > 0) {
      renderReasoningJournal(d.journal_entries);
    }

    if (d.confidence_trend && d.confidence_trend.length > 0) {
      renderConfidenceTrend(d.confidence_trend);
    }

    if (d.source_diversity_summary) {
      renderSourceDiversitySummary(d.source_diversity_summary);
    }

  } catch (err) {
    console.warn('[Dashboard] load error:', err);
    showToast('Dashboard data unavailable.');
  }
}

// ── Render helpers ────────────────────────────────────────────────────────────

// ── v6.0: Skill progress with per-skill sparklines ───────────────────────────
function renderSkillProgress(skills, skillHistory) {
  const grid = document.getElementById('skill-grid');
  if (!skills.length) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><p>Complete a lesson to unlock skill tracking.</p></div>';
    return;
  }
  const levelPct = { beginner: 33, intermediate: 66, advanced: 100 };
  const levelCol = { beginner: '#34d399', intermediate: '#fbbf24', advanced: '#f87171' };
  const badgeSty = {
    beginner:     'background:rgba(52,211,153,.12);color:#34d399',
    intermediate: 'background:rgba(251,191,36,.12);color:#fbbf24',
    advanced:     'background:rgba(248,113,113,.12);color:#f87171',
  };

  // Build history lookup: topic → list of {level_to, changed_at}
  const historyByTopic = {};
  (skillHistory || []).forEach(h => {
    if (!historyByTopic[h.topic]) historyByTopic[h.topic] = [];
    historyByTopic[h.topic].push(h);
  });

  function ringPath(pct, col) {
    const r = 30, cx = 40, cy = 40, circ = 2 * Math.PI * r;
    const dash = (pct / 100) * circ;
    return `<svg class="skill-ring-svg" viewBox="0 0 80 80">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="7"/>
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${col}" stroke-width="7"
        stroke-dasharray="${dash} ${circ}" stroke-dashoffset="${circ * 0.25}"
        stroke-linecap="round" style="transition:stroke-dasharray .6s cubic-bezier(.4,0,.2,1)"/>
      <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central"
        font-family="Syne,sans-serif" font-weight="800" font-size="14" fill="${col}">${pct}%</text>
    </svg>`;
  }

  // Sparkline: mini bar chart of the last 6 history entries for a topic
  function sparkline(topic) {
    const hist = (historyByTopic[topic] || []).slice(-6);
    if (hist.length < 2) return '';
    const vals  = hist.map(h => levelPct[h.level_to] || 33);
    const maxV  = Math.max(...vals, 1);
    const bars  = vals.map((v, i) => {
      const h    = Math.max(3, Math.round((v / 100) * 24));
      const col  = levelCol[hist[i].level_to] || '#34d399';
      return `<div style="width:6px;height:${h}px;background:${col};border-radius:2px;opacity:.85;flex-shrink:0;"></div>`;
    }).join('');
    // Trend arrow
    const trend = vals[vals.length - 1] - vals[0];
    const trendEl = trend > 0
      ? `<span style="color:#34d399;font-size:.7rem;">↑</span>`
      : trend < 0
      ? `<span style="color:#f87171;font-size:.7rem;">↓</span>`
      : `<span style="color:var(--muted);font-size:.7rem;">→</span>`;
    return `<div style="display:flex;align-items:flex-end;gap:2px;margin-top:.5rem;height:28px;">
      ${bars}
      <div style="margin-left:3px;align-self:center;">${trendEl}</div>
    </div>
    <div style="font-size:.65rem;color:var(--muted);margin-top:.2rem;font-family:'DM Mono',monospace;">
      ${hist.length} level changes
    </div>`;
  }

  // Per-skill delta label
  function deltaLabel(topic) {
    const hist = (historyByTopic[topic] || []);
    if (hist.length < 2) return '';
    const first = hist[0].level_to;
    const last  = hist[hist.length - 1].level_to;
    const order = { beginner: 0, intermediate: 1, advanced: 2 };
    const delta = (order[last] || 0) - (order[first] || 0);
    if (delta > 0) return `<div style="font-size:.68rem;color:#34d399;margin-top:.25rem;">↑ Improving</div>`;
    if (delta < 0) return `<div style="font-size:.68rem;color:#f87171;margin-top:.25rem;">↓ Needs work</div>`;
    return `<div style="font-size:.68rem;color:var(--muted);margin-top:.25rem;">→ Stable</div>`;
  }

  grid.innerHTML = skills.map(s => {
    const pct = levelPct[s.current_level] || 33;
    const col = levelCol[s.current_level] || '#34d399';
    const sty = badgeSty[s.current_level] || badgeSty.beginner;
    return `<div class="skill-ring-card">
      ${ringPath(pct, col)}
      <div class="skill-ring-label">${escHtml(s.display_name)}</div>
      <span class="skill-ring-badge" style="${sty}">${s.current_level}</span>
      <div class="skill-ring-meta">${s.lessons_completed ?? 0} lesson${s.lessons_completed !== 1 ? 's' : ''} · ${s.quiz_accuracy_pct != null ? s.quiz_accuracy_pct + '% acc.' : 'no quiz'}</div>
      ${deltaLabel(s.topic)}
      ${sparkline(s.topic)}
    </div>`;
  }).join('');
}

// ── v6.0: Reasoning Journal viewer ───────────────────────────────────────────
// Shows the user's last 5 Reasoning Journal entries with Bloom's level badges.
// Surfaced in the Learning panel so the user can track how their reflective
// thinking has developed — primary qualitative data for Bloom's L4-5 analysis.
function renderReasoningJournal(entries) {
  const existingId = 'reasoning-journal-section';
  let section = document.getElementById(existingId);

  if (!section) {
    // Inject after the pretest section if it exists, else append to learning section
    const learningSection = document.getElementById('section-learning');
    if (!learningSection) return;
    section = document.createElement('div');
    section.id = existingId;
    section.style.cssText = 'margin-top:1.5rem;';
    learningSection.appendChild(section);
  }
  // Guard: clear before re-render so repeated loadDashboard calls don't duplicate content
  section.innerHTML = '';

  const bloomLabels = ['', 'Remember', 'Understand', 'Apply', 'Analyze', 'Evaluate'];
  const bloomColors = ['', 'var(--muted)', 'var(--muted)', '#93c5fd', '#fbbf24', '#34d399'];

  section.innerHTML = `
    <div style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.85rem;">
      REASONING JOURNAL — LAST ${entries.length} ENTRIES
    </div>
    ${entries.map(e => {
      const bl    = e.bloom_level || 1;
      const blLbl = bloomLabels[bl] || 'Recall';
      const blCol = bloomColors[bl] || 'var(--muted)';
      const date  = e.submitted_at ? new Date(e.submitted_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
      const wc    = e.total_word_count ? `${e.total_word_count} words` : '';
      const stageLabels = { post_eval: 'After Steps', post_evidence: 'After Evidence', post_verdict: 'After Verdict' };
      const stageLabel = stageLabels[e.stage] || e.stage;
      return `
        <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:.9rem 1.1rem;margin-bottom:.65rem;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.6rem;flex-wrap:wrap;gap:.4rem;">
            <div style="display:flex;gap:.45rem;align-items:center;">
              <span style="font-family:'DM Mono',monospace;font-size:.65rem;padding:.15rem .5rem;border-radius:4px;border:1px solid ${blCol};color:${blCol};">
                Bloom's L${bl}: ${blLbl}
              </span>
              <span style="font-size:.7rem;color:var(--muted);font-family:'DM Mono',monospace;">${stageLabel}</span>
            </div>
            <span style="font-size:.72rem;color:var(--muted);">${date}${wc ? ' · ' + wc : ''}</span>
          </div>
          ${e.what_noticed ? `
            <div style="margin-bottom:.4rem;">
              <div style="font-size:.68rem;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:.2rem;">NOTICED</div>
              <div style="font-size:.83rem;color:var(--text);line-height:1.6;">${escHtml(e.what_noticed)}</div>
            </div>` : ''}
          ${e.still_uncertain ? `
            <div style="margin-bottom:.4rem;">
              <div style="font-size:.68rem;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:.2rem;">STILL UNCERTAIN</div>
              <div style="font-size:.83rem;color:var(--text);line-height:1.6;">${escHtml(e.still_uncertain)}</div>
            </div>` : ''}
          ${e.would_check_next ? `
            <div>
              <div style="font-size:.68rem;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:.2rem;">WOULD CHECK NEXT</div>
              <div style="font-size:.83rem;color:var(--text);line-height:1.6;">${escHtml(e.would_check_next)}</div>
            </div>` : ''}
          ${!e.what_noticed && !e.still_uncertain && !e.would_check_next && e.free_reasoning ? `
            <div style="font-size:.83rem;color:var(--text);line-height:1.6;">${escHtml(e.free_reasoning)}</div>` : ''}
        </div>`;
    }).join('')}
  `;
}

// ── v6.0: Confidence trend panel ─────────────────────────────────────────────
// Shows confidence_before vs confidence_after across recent sessions.
// A user who consistently updates their confidence after seeing evidence
// is showing Kirkpatrick Level 3 behaviour change.
function renderConfidenceTrend(trend) {
  const existingId = 'confidence-trend-section';
  let section = document.getElementById(existingId);

  if (!section) {
    const skillsSection = document.getElementById('section-skills');
    if (!skillsSection) return;
    section = document.createElement('div');
    section.id = existingId;
    section.style.cssText = 'margin-top:1.5rem;';
    skillsSection.appendChild(section);
  }
  // Guard: clear before re-render so repeated loadDashboard calls don't duplicate content
  section.innerHTML = '';

  const confLabel = v => ['', 'Not confident', 'Slightly uncertain', 'Somewhat confident', 'Fairly confident', 'Very confident'][v] || '—';

  // Compute average delta
  const withDelta = trend.filter(t => t.confidence_delta !== null && t.confidence_delta !== undefined);
  const avgDelta  = withDelta.length
    ? (withDelta.reduce((sum, t) => sum + t.confidence_delta, 0) / withDelta.length).toFixed(1)
    : null;

  const calibrationFlags = trend.filter(t => t.calibration_flag).length;

  section.innerHTML = `
    <div style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.85rem;">
      CONFIDENCE BEFORE VS. AFTER — LAST ${trend.length} SESSIONS
    </div>
    ${avgDelta !== null ? `
      <div style="display:flex;gap:.85rem;margin-bottom:1rem;flex-wrap:wrap;">
        <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.7rem 1rem;flex:1;min-width:120px;">
          <div style="font-size:.7rem;color:var(--muted);font-family:'DM Mono',monospace;margin-bottom:.3rem;">AVG. CONFIDENCE SHIFT</div>
          <div style="font-size:1.3rem;font-weight:800;color:${parseFloat(avgDelta) < 0 ? '#34d399' : parseFloat(avgDelta) > 0.5 ? '#fbbf24' : 'var(--text)'};">
            ${parseFloat(avgDelta) > 0 ? '+' : ''}${avgDelta}
          </div>
          <div style="font-size:.72rem;color:var(--muted);margin-top:.2rem;">
            ${parseFloat(avgDelta) < -0.2 ? 'Evidence consistently reducing overconfidence ✓' :
              parseFloat(avgDelta) > 0.5  ? 'Confidence increasing after evidence — check thoroughness' :
              'Confidence roughly stable after reviewing evidence'}
          </div>
        </div>
        ${calibrationFlags > 0 ? `
          <div style="background:rgba(251,191,36,.07);border:1px solid rgba(251,191,36,.25);border-radius:10px;padding:.7rem 1rem;flex:1;min-width:120px;">
            <div style="font-size:.7rem;color:var(--yellow);font-family:'DM Mono',monospace;margin-bottom:.3rem;">🧠 CALIBRATION GAPS</div>
            <div style="font-size:1.3rem;font-weight:800;color:var(--yellow);">${calibrationFlags}</div>
            <div style="font-size:.72rem;color:var(--muted);margin-top:.2rem;">
              sessions where confidence was high but thoroughness was low
            </div>
          </div>` : ''}
      </div>` : ''}
    <div style="display:flex;flex-direction:column;gap:.45rem;">
      ${trend.map((t, i) => {
        const before = t.confidence_before;
        const after  = t.confidence_after;
        const delta  = t.confidence_delta;
        const date   = t.recorded_at ? new Date(t.recorded_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
        const flag   = t.calibration_flag;
        const deltaStr = delta !== null && delta !== undefined
          ? (delta > 0 ? `<span style="color:#fbbf24;">↑ +${delta}</span>`
           : delta < 0 ? `<span style="color:#34d399;">↓ ${delta}</span>`
           : `<span style="color:var(--muted);">→ 0</span>`)
          : '';
        return `
          <div style="background:var(--card);border:1px solid ${flag ? 'rgba(251,191,36,.3)' : 'var(--border)'};border-radius:8px;padding:.6rem .9rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.4rem;">
            <div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;">
              <span style="font-size:.75rem;color:var(--muted);">${date}</span>
              <span style="font-size:.8rem;">Before: <strong>${before != null ? before + '/5' : '—'}</strong></span>
              <span style="font-size:.8rem;">After: <strong>${after != null ? after + '/5' : '—'}</strong></span>
              ${deltaStr ? `<span style="font-size:.8rem;font-weight:700;">${deltaStr}</span>` : ''}
            </div>
            ${flag ? `<span style="font-size:.68rem;color:var(--yellow);font-family:'DM Mono',monospace;">🧠 calibration gap</span>` : ''}
          </div>`;
      }).join('')}
    </div>
  `;
}

// ── v6.0: Source Diversity Summary ───────────────────────────────────────────
// Aggregates source_diversity_log entries so the user can see their overall
// information ecosystem exposure — aligned to UNESCO MIL "Access and Evaluate".
function renderSourceDiversitySummary(summary) {
  const existingId = 'source-diversity-summary-section';
  let section = document.getElementById(existingId);

  if (!section) {
    const learningSection = document.getElementById('section-learning');
    if (!learningSection) return;
    section = document.createElement('div');
    section.id = existingId;
    section.style.cssText = 'margin-top:1.5rem;';
    learningSection.appendChild(section);
  }

  const avgScore = summary.avg_diversity_score != null
    ? Math.round(summary.avg_diversity_score * 100) + '%'
    : '—';

  const categories = [
    { key: 'total_government',    label: 'Government',    icon: '🏛️', color: '#93c5fd' },
    { key: 'total_academic',      label: 'Academic',      icon: '🎓', color: '#a78bfa' },
    { key: 'total_news',          label: 'News',          icon: '📰', color: 'var(--text)' },
    { key: 'total_factcheck',     label: 'Fact-Check',    icon: '✅', color: '#34d399' },
    { key: 'total_international', label: 'International', icon: '🌍', color: 'var(--accent)' },
    { key: 'total_other',         label: 'Other',         icon: '📄', color: 'var(--muted)' },
  ].filter(c => (summary[c.key] || 0) > 0);

  const total = categories.reduce((s, c) => s + (summary[c.key] || 0), 0) || 1;

  section.innerHTML = `
    <div style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.85rem;">
      SOURCE DIVERSITY — ACROSS ALL YOUR SESSIONS
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.1rem 1.25rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.85rem;flex-wrap:wrap;gap:.5rem;">
        <p style="font-size:.83rem;color:var(--muted);margin:0;line-height:1.6;flex:1;min-width:180px;">
          These are the types of sources you've encountered across all your evaluations.
          A varied diet of source types is a core UNESCO MIL competency.
        </p>
        <div style="text-align:right;">
          <div style="font-size:.65rem;font-family:'DM Mono',monospace;color:var(--muted);margin-bottom:.15rem;">AVG DIVERSITY</div>
          <div style="font-size:1.4rem;font-weight:800;color:var(--accent);">${avgScore}</div>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:.4rem;margin-bottom:.85rem;">
        ${categories.map(c => {
          const count = summary[c.key] || 0;
          const pct   = Math.round((count / total) * 100);
          return `
            <div style="display:flex;align-items:center;gap:.6rem;">
              <span style="font-size:.85rem;width:20px;">${c.icon}</span>
              <span style="font-size:.78rem;color:var(--text);width:90px;flex-shrink:0;">${c.label}</span>
              <div style="flex:1;background:var(--border);border-radius:4px;height:6px;overflow:hidden;">
                <div style="width:${pct}%;background:${c.color};height:100%;border-radius:4px;transition:width .5s;"></div>
              </div>
              <span style="font-size:.75rem;color:var(--muted);width:30px;text-align:right;">${count}</span>
            </div>`;
        }).join('')}
      </div>
      <p style="font-size:.75rem;color:var(--muted);margin:0;line-height:1.6;">
        ${(summary.avg_diversity_score || 0) < 0.33
          ? '⚠ Your retrieved sources are mostly from similar outlet types. Try using government or academic sources for higher-stakes claims.'
          : (summary.avg_diversity_score || 0) < 0.5
          ? 'Moderate variety. Adding more fact-check or international sources will strengthen your evidence base.'
          : '✓ Good source diversity across your sessions — keep comparing across source types.'}
      </p>
    </div>
  `;
}

function renderBehaviorCards(cards) {
  document.getElementById('behavior-cards').innerHTML = cards.length ? cards.map(c => `
    <div class="insight-card">
      <div class="insight-title">
        <svg class="insight-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        ${escHtml(c.title)}
      </div>
      <div class="insight-body">${escHtml(c.body)}</div>
      <div class="insight-footer">
        <span class="mil-tag">${escHtml(c.mil_skill)}</span>
        <a href="${escHtml(c.action)}" class="insight-link">${escHtml(c.action_label)}</a>
      </div>
    </div>`).join('') : '';
}

function renderWeaknessBars(triggers) {
  if (!triggers.length) { document.getElementById('weakness-bars').innerHTML = ''; return; }
  const max = Math.max(...triggers.map(t => t.trigger_count), 1);
  document.getElementById('weakness-bars').innerHTML = triggers.map(t => `
    <div class="weakness-row">
      <div class="weakness-label">${escHtml(t.display_name)}</div>
      <div class="weakness-bar-wrap"><div class="weakness-bar-fill" style="width:${Math.round(t.trigger_count / max * 100)}%"></div></div>
      <div class="weakness-count">${t.trigger_count}</div>
    </div>`).join('');
}

function renderStreak(activityByDay) {
  const today = new Date();
  const days  = 112;
  const dateMap = {};
  (activityByDay || []).forEach(r => { dateMap[r.date] = r.count; });
  const start = new Date(today);
  start.setDate(today.getDate() - (days - 1));
  const cols = [];
  let col = [];
  const cur = new Date(start);
  while (cur <= today) {
    const key = cur.toISOString().slice(0, 10);
    const cnt = dateMap[key] || 0;
    const lv  = cnt === 0 ? '' : cnt === 1 ? 'lv1' : cnt <= 3 ? 'lv2' : cnt <= 6 ? 'lv3' : 'lv4';
    col.push(`<div class="heatmap-cell ${lv}" title="${key}: ${cnt}"></div>`);
    if (col.length === 7) { cols.push([...col]); col = []; }
    cur.setDate(cur.getDate() + 1);
  }
  if (col.length) cols.push(col);
  document.getElementById('heatmap-grid').innerHTML = cols.map(c => `<div class="heatmap-col">${c.join('')}</div>`).join('');
  const totalDays = (activityByDay || []).filter(r => r.count > 0).length;
  document.getElementById('streak-label').textContent = totalDays + ' active day' + (totalDays !== 1 ? 's' : '') + ' in 16 weeks';
  document.getElementById('streak-wrap').style.display = 'block';
}

function checkNewUser(stats) {
  const isNew = !stats.total_submissions && !stats.lessons_completed && !stats.total_quiz_attempts;
  const banner = document.getElementById('new-user-banner');
  if (banner) banner.classList.toggle('visible', !!isNew);
}

function renderRecommended(lessons) {
  if (!lessons.length) {
    document.getElementById('recommended-lessons').innerHTML = `<div class="empty-state">No pending recommendations. <a href="lessons.html">Browse all lessons →</a></div>`;
    return;
  }
  document.getElementById('recommended-lessons').innerHTML = lessons.map(l => `
    <a href="lessons.html#${escHtml(l.lesson_key)}" class="lesson-rec">
      <div class="lesson-rec-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg></div>
      <div class="lesson-rec-text">
        <div class="lesson-rec-title">${escHtml(l.title)}</div>
        <div class="lesson-rec-meta">${escHtml(l.display_name)} · ${escHtml(l.difficulty)}</div>
      </div>
      <svg class="lesson-rec-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px"><polyline points="9 18 15 12 9 6"/></svg>
    </a>`).join('');
}

function renderPretest(data) {
  if (!data || (!data.pretest && !data.posttest)) {
    document.getElementById('pretest-section').innerHTML = `<div class="empty-state">Complete the pre-test on the lessons page to see your improvement here. <a href="lessons.html">Go to lessons →</a></div>`;
    return;
  }
  const pre   = data.pretest;
  const post  = data.posttest;
  const delta = data.delta;
  const deltaClass = delta == null ? 'delta-neutral' : delta > 0 ? 'delta-positive' : delta < 0 ? 'delta-negative' : 'delta-neutral';
  const deltaStr   = delta == null ? '—' : (delta > 0 ? '+' : '') + delta + '%';
  const deltaIcon  = delta == null ? '' : delta >= 0
    ? '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>'
    : '<polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/>';
  const deltaCardHtml = delta != null ? `
    <div class="delta-card">
      <div class="delta-icon ${delta < 0 ? 'negative' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${deltaIcon}</svg>
      </div>
      <div>
        <div class="delta-text-label">Score improvement</div>
        <div class="delta-text-val ${delta < 0 ? 'negative' : ''}">${deltaStr}</div>
        <div class="delta-text-sub">${delta > 0 ? 'You improved from pre to post-test — great work.' : delta < 0 ? 'Score dropped — keep practicing.' : 'No change between tests.'}</div>
      </div>
    </div>` : '';
  const pretestOnlyBanner = data.pretest_only ? `
    <div style="margin-top:1rem;padding:.85rem 1rem;background:rgba(79,142,247,.07);border:1px solid rgba(79,142,247,.2);border-radius:10px;font-size:.82rem;color:var(--muted);">
      ✅ Pre-test recorded. Come back after completing lessons to take the post-test and see your improvement.
      <a href="lessons.html" style="color:var(--accent);margin-left:.4rem;">Start lessons →</a>
    </div>` : '';
  document.getElementById('pretest-section').innerHTML = `
    <div class="pretest-grid">
      <div class="pretest-card">
        <div class="pretest-label">Pre-test</div>
        <div class="pretest-val">${pre ? pre.score_pct + '%' : '—'}</div>
        <div class="pretest-sub">${pre ? pre.correct + '/' + pre.total + ' correct' : 'not taken'}</div>
      </div>
      <div class="pretest-card">
        <div class="pretest-label">Post-test</div>
        <div class="pretest-val">${post ? post.score_pct + '%' : '—'}</div>
        <div class="pretest-sub">${post ? post.correct + '/' + post.total + ' correct' : 'not taken yet'}</div>
      </div>
      <div class="pretest-card">
        <div class="pretest-label">Improvement</div>
        <div class="pretest-val ${deltaClass}">${deltaStr}</div>
        <div class="pretest-sub">${delta != null ? (delta > 0 ? 'great progress' : delta < 0 ? 'keep practicing' : 'no change') : 'complete both tests'}</div>
      </div>
    </div>${deltaCardHtml}${pretestOnlyBanner}`;
}

function renderQuizHistory(attempts) {
  if (!attempts.length) {
    document.getElementById('quiz-history-list').innerHTML = `<div class="empty-state">No quiz attempts yet. <a href="lessons.html">Start a lesson →</a></div>`;
    return;
  }
  document.getElementById('quiz-history-list').innerHTML = `
    <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:.75rem 1.1rem">
      ${attempts.map(a => `
        <div class="quiz-row">
          <div class="quiz-dot ${a.is_correct ? 'quiz-dot-correct' : 'quiz-dot-wrong'}" title="${a.is_correct ? 'Correct' : 'Incorrect'}"></div>
          <div class="quiz-q-text">${escHtml(a.question_text)}${a.question_text.length >= 80 ? '…' : ''}</div>
          <span class="quiz-topic-tag" style="${_topicStyle(a.topic)}">${_topicLabel(a.topic)}</span>
          <div class="quiz-meta">${(a.attempted_at || '').slice(0, 10)}</div>
        </div>`).join('')}
    </div>`;
}

async function loadDashQuizStats(userId) {
  const container = document.getElementById('dash-quiz-stats');
  if (!container) return;
  try {
    const token = localStorage.getItem('sp_access_token') || sessionStorage.getItem('sp_access_token') || '';
    const authHeader = token ? { Authorization: `Bearer ${token}` } : {};
    const res = await fetch(`/quiz/stats/${userId}`, { credentials: 'include', headers: authHeader });
    if (!res.ok) throw new Error();
    const rows = await res.json();
    if (!rows || !rows.length) {
      container.innerHTML = '<div style="color:var(--muted);font-size:.82rem;text-align:center;padding:1rem 0;">Complete a quiz to see your stats here. <a href="lessons.html" style="color:var(--accent)">Go to Lessons →</a></div>';
      return;
    }
    container.innerHTML = rows.map(r => {
      const pct = r.accuracy_pct ?? 0;
      const col = pct >= 80 ? 'var(--green)' : pct >= 50 ? 'var(--yellow)' : 'var(--red)';
      const label = _topicLabel(r.topic);
      return `<div style="display:flex;align-items:center;gap:.85rem;margin-bottom:.7rem;">
        <span style="min-width:170px;font-size:.83rem;color:var(--text);">${label}</span>
        <div style="flex:1;height:6px;border-radius:3px;background:var(--border);overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:${col};border-radius:3px;transition:width .5s ease;"></div>
        </div>
        <span style="min-width:38px;text-align:right;font-family:'DM Mono',monospace;font-size:.78rem;font-weight:600;color:${col};">${pct}%</span>
        <span style="color:var(--muted);font-family:'DM Mono',monospace;font-size:.72rem;min-width:52px;text-align:right;">${r.topic_correct ?? 0}/${r.topic_attempts ?? 0}</span>
      </div>`;
    }).join('');
  } catch {
    container.innerHTML = '<div style="color:var(--muted);font-size:.82rem;text-align:center;padding:.5rem 0;">Could not load quiz stats.</div>';
  }
}

function renderHistory(history) {
  if (!history.length) {
    document.getElementById('history-list').innerHTML = `<div class="empty-state">No submissions yet. <a href="index.html">Try submitting some content →</a></div>`;
    return;
  }
  const typeIcons = {
    url:   '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    pdf:   '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    text:  '<line x1="17" y1="10" x2="3" y2="10"/><line x1="21" y1="6" x2="3" y2="6"/><line x1="21" y1="14" x2="3" y2="14"/><line x1="17" y1="18" x2="3" y2="18"/>',
  };
  document.getElementById('history-list').innerHTML = history.map(ev => {
    const icon = typeIcons[ev.input_type] || typeIcons.text;
    const date = ev.created_at ? new Date(ev.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
    return `<div class="history-item">
      <div class="history-type-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${icon}</svg></div>
      <div class="history-text">
        <div class="history-title">${escHtml(ev.content_preview || 'Evaluation #' + ev.eval_id)}</div>
        <div class="history-date">${date} · ${ev.input_type || 'text'}</div>
      </div>
    </div>`;
  }).join('');
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function switchTab(group, name) {
  document.querySelectorAll(`#learn-tab-${group} .tab-panel, [id^="learn-tab-"]`).forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById(`learn-tab-${name}`);
  if (panel) panel.classList.add('active');
  event.target.classList.add('active');
}

// ── Dynamic topic registry ─────────────────────────────────────────────────────
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
  return `<span class="badge" style="${_topicStyle(t)};border:none;">${_topicLabel(t)}</span>`;
}

function topicLabel(t) { return _topicLabel(t); }


function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3500);
}
