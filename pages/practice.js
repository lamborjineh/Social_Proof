// ─────────────────────────────────────────────────────────────────────────────
// SocialProof — index.js  v6.0
//
// Changes in v6.0:
//   - Reasoning Journal: three Bloom's L4–5 prompts shown after post_evidence
//     and after post_verdict stages, saved to /analyze/reasoning-journal
//   - Confidence Before/After: slider shown at Step 1 (before retrieval)
//     and after the evidence section. Delta visualised in Section 1.
//   - Source Diversity Panel: shown in Section 2 (Content Retrieval),
//     displays gov/academic/news/factcheck/international breakdown.
//   - MBFC badge on evidence cards: colour-coded, links to MBFC page.
//   - Retrieval Reason chip: one-line explanation under each evidence card.
//   - "What You Missed" feedback: rendered with a distinct yellow chip style.
//   - Calibration gap feedback: rendered with a distinct amber chip style.
//   - Confidence snapshot saved at /analyze/confidence-snapshot.
// ─────────────────────────────────────────────────────────────────────────────

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────────────────────
const API_BASE    = window._SP_API_BASE || '';
let   USER_ID     = window._SP_USER_ID  || null;
const TOTAL_STEPS = 8;

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
const state = {
  inputType:     'text',
  content:       '',
  imageData:     null,
  fileData:      null,
  fileName:      null,
  currentStep:   0,
  totalSteps:    TOTAL_STEPS,
  skippedSteps:  [],
  answeredSteps: [],
  userClaim:     '',
  sourceRating:  null,
  biasRating:    null,
  evidenceRating: null,
  purposeRating: null,
  audienceRating: null,
  logicRating:   null,
  corroboration: null,
  userScore:     50,
  confidence:    'medium',
  evaluationId:  null,
  userEvaluationId: null,
  systemResult:  null,
  comparisonResult: null,
  _submissionEvidenceRendered: false,
  _loaderInterval: null,
  _pendingUserClaim: null,
  _sourceDiversity: null,       // v6.0: SourceDiversityInfo from API
};

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────
function show(id) {
  ['phase-input','phase-eval','phase-loading','phase-results','phase-no-claims']
    .forEach(p => { const el = document.getElementById(p); if (el) el.style.display = p === id ? '' : 'none'; });
  // Hide hero when not on input phase
  document.body.dataset.hideHero = id === 'phase-input' ? '0' : '1';
}

function showToast(msg, duration = 3500) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), duration);
}

function _authHeaders() {
  const token = localStorage.getItem('sp_jwt');
  return token
    ? { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }
    : { 'Content-Type': 'application/json' };
}

function getSessionToken() {
  let tok = localStorage.getItem('sp_session');
  if (!tok || tok.length < 32) {
    tok = Array.from(crypto.getRandomValues(new Uint8Array(32)))
               .map(b => b.toString(16).padStart(2,'0')).join('');
    localStorage.setItem('sp_session', tok);
  }
  return tok;
}

async function initSessionToken() {
  getSessionToken();
}

// ─────────────────────────────────────────────────────────────────────────────
// Input phase
// ─────────────────────────────────────────────────────────────────────────────
function setInputType(type, el) {
  state.inputType = type;
  document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
  if (el) el.classList.add('active');
  document.getElementById('content-input').style.display    = type === 'text'  ? '' : 'none';
  document.getElementById('image-upload-area').style.display = type === 'image' ? '' : 'none';
  document.getElementById('file-upload-area').style.display  = type === 'file'  ? '' : 'none';
  updateCharCount();
}

function updateCharCount() {
  const el = document.getElementById('char-count');
  if (!el) return;
  if (state.inputType === 'image') {
    el.textContent = state.imageData ? '1 image selected' : '0 images selected';
  } else if (state.inputType === 'file') {
    el.textContent = state.fileData ? (state.fileName || '1 file selected') : '0 files selected';
  } else {
    const val = (document.getElementById('content-input')?.value || '');
    el.textContent = val.length + ' characters';
  }
}

function handleImageUpload(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    state.imageData = e.target.result.split(',')[1];
    // Show preview
    const previewWrap = document.getElementById('image-preview-wrap');
    const previewImg  = document.getElementById('image-preview');
    const previewName = document.getElementById('image-filename');
    if (previewWrap) previewWrap.style.display = '';
    if (previewImg)  previewImg.src = e.target.result;
    if (previewName) previewName.textContent = file.name;
    // Update drop label
    const label = document.getElementById('image-drop-label');
    if (label) label.style.display = 'none';
    updateCharCount();
  };
  reader.readAsDataURL(file);
}

function clearImageUpload() {
  state.imageData = null;
  const previewWrap = document.getElementById('image-preview-wrap');
  const previewImg  = document.getElementById('image-preview');
  const label       = document.getElementById('image-drop-label');
  const input       = document.getElementById('image-file-input');
  if (previewWrap) previewWrap.style.display = 'none';
  if (previewImg)  previewImg.src = '';
  if (label)       label.style.display = '';
  if (input)       input.value = '';
}

function handleFileUpload(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    state.fileData = e.target.result.split(',')[1];
    state.fileName = file.name;
    // Show file preview
    const previewWrap = document.getElementById('file-preview-wrap');
    const nameEl      = document.getElementById('file-filename');
    const sizeEl      = document.getElementById('file-filesize');
    const label       = document.getElementById('file-drop-label');
    if (previewWrap) previewWrap.style.display = '';
    if (nameEl)      nameEl.textContent = file.name;
    if (sizeEl)      sizeEl.textContent = (file.size / 1024 < 1024)
                       ? (file.size / 1024).toFixed(1) + ' KB'
                       : (file.size / 1048576).toFixed(1) + ' MB';
    if (label)       label.style.display = 'none';
    updateCharCount();
  };
  reader.readAsDataURL(file);
}

function clearFileUpload() {
  state.fileData = null;
  state.fileName = null;
  const previewWrap = document.getElementById('file-preview-wrap');
  const label       = document.getElementById('file-drop-label');
  const input       = document.getElementById('file-file-input');
  if (previewWrap) previewWrap.style.display = 'none';
  if (label)       label.style.display = '';
  if (input)       input.value = '';
}

function goToEval() {
  let content = '';
  if (state.inputType === 'text') {
    content = (document.getElementById('content-input')?.value || '').trim();
  } else if (state.inputType === 'image') {
    content = '[image]';
  } else if (state.inputType === 'file') {
    content = '[file]';
  }

  if (state.inputType === 'text' && content.length < 15) {
    showToast('Please enter at least 15 characters.'); return;
  } else if (state.inputType === 'image' && !state.imageData) {
    showToast('Please upload an image first.'); return;
  } else if (state.inputType === 'file' && !state.fileData) {
    showToast('Please upload a file first.'); return;
  }

  state.content = content;
  state.skippedSteps  = [];
  state.answeredSteps = [];
  state.currentStep   = 0;
  state._submissionEvidenceRendered = false;
  state._sourceDiversity = null;

  show('phase-eval');
  goToStep(0);
}

// ─────────────────────────────────────────────────────────────────────────────
// Evaluation steps
// ─────────────────────────────────────────────────────────────────────────────
let _evalQuestions = null;

async function loadEvalQuestions() {
  try {
    const res = await fetch(`${API_BASE}/eval-questions`, { headers: _authHeaders() });
    if (res.ok) {
      _evalQuestions = await res.json();
      _renderDynamicSteps(_evalQuestions);
    } else {
      // API endpoint not available (e.g. 404) — render the built-in 8 steps
      _renderDynamicSteps(null);
    }
  } catch (e) {
    // Network error or JSON parse failure — render the built-in 8 steps
    _renderDynamicSteps(null);
  }
}

function _renderDynamicSteps(questions) {
  // Dynamic step rendering hook — implemented by script.js if present
  if (window._renderDynamicStepsFn) window._renderDynamicStepsFn(questions);
}

// ─────────────────────────────────────────────────────────────────────────────
// Dynamic step renderer — turns API eval-questions into interactive step cards
// Falls back to the built-in 8 steps if the API returns nothing.
// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// Metacognitive guardrail engine
// Called inside nextStep() before advancing. Returns a guardrail message
// (string) if the answer needs a nudge, or null to proceed.
// Works for ANY question — uses the question text + answer heuristically.
// ─────────────────────────────────────────────────────────────────────────────
function _checkGuardrail(q, answer, stepIndex) {
  if (answer === null || answer === undefined) return null;
  const val    = String(answer).trim();
  const valLow = val.toLowerCase();
  const prompt = (q.prompt || q.question_text || '').toLowerCase();

  // Normalise input_type the same way the renderer does
  const _itype     = (q.input_type || '').toLowerCase();
  const isTextarea = _itype === 'text' || _itype === 'textarea' || _itype === '';
  const isStructured = !isTextarea; // radio, multiple_choice, yes_no, yesno, scale, checkbox, multiple_answer

  // ── 1. DB-driven branches (admin-configured follow-up questions) ───────────
  // trigger_value in the DB is the full label string; val is also the full label.
  // Comparison is case-insensitive and trimmed.
  const branches = Array.isArray(q.branches) ? q.branches : [];
  for (const b of branches) {
    if (b.is_active === 0 || b.is_active === false) continue;
    if (!b.followup_prompt) continue;
    const condition  = (b.trigger_condition || '').toLowerCase();
    const triggerVal = (b.trigger_value || '').toLowerCase().trim();
    let matched = false;

    if (condition === 'equals') {
      matched = valLow === triggerVal;
    } else if (condition === 'includes') {
      matched = valLow.includes(triggerVal);
    } else if (condition === 'skipped') {
      matched = (val === '' || valLow === 'skipped');
    }

    if (matched) return b; // full branch object → caller renders rich follow-up
  }

  // ── 2. Heuristic fallbacks — TEXTAREA ONLY, never for structured inputs ───
  if (isStructured) return null;

  // Very short free-text answer
  const words = val.split(/\s+/).filter(Boolean).length;
  if (words < 4) {
    return `That's very brief. Can you say a bit more? Even one extra detail strengthens your analysis.`;
  }

  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Guardrail UI — injects a prompt below the answer area and blocks Next
// ─────────────────────────────────────────────────────────────────────────────
function _showGuardrail(stepIndex, message) {
  const existingId = `guardrail-${stepIndex}`;
  if (document.getElementById(existingId)) return false; // already showing

  const card = document.getElementById(`eval-step-${stepIndex}`);
  if (!card) return false;

  const nav = card.querySelector('.eval-step-nav');
  if (!nav) return false;

  const box = document.createElement('div');
  box.id = existingId;
  box.style.cssText = `
    margin-top:.75rem;padding:.85rem 1rem;
    background:rgba(251,191,36,.08);
    border:1px solid rgba(251,191,36,.35);
    border-left:3px solid rgba(251,191,36,.8);
    border-radius:8px;font-size:.85rem;
    color:var(--text);line-height:1.6;
  `;
  box.innerHTML = `
    <div style="font-weight:700;color:rgba(251,191,36,1);font-size:.72rem;
                font-family:'DM Mono',monospace;letter-spacing:.06em;margin-bottom:.35rem;">
      💡 THINK AGAIN
    </div>
    <div>${message}</div>
    <div style="display:flex;gap:.6rem;margin-top:.65rem;">
      <button class="btn btn-sm" onclick="_dismissGuardrail(${stepIndex}, true)"
        style="font-size:.8rem;padding:.3rem .8rem;background:rgba(251,191,36,.15);
               color:rgba(200,150,0,1);border:1px solid rgba(251,191,36,.4);">
        I've reconsidered — continue →
      </button>
      <button class="btn btn-ghost btn-sm" onclick="_dismissGuardrail(${stepIndex}, false)"
        style="font-size:.8rem;padding:.3rem .8rem;">
        Update my answer
      </button>
    </div>
  `;

  nav.insertAdjacentElement('beforebegin', box);
  return true; // guardrail was shown, block advancement
}

function _dismissGuardrail(stepIndex, proceed) {
  const box = document.getElementById(`guardrail-${stepIndex}`);
  if (box) box.remove();
  if (proceed) {
    _advanceStep(stepIndex);
  }
  // "Update my answer" just removes the box so they can change the input
}

window._renderDynamicStepsFn = function(questions) {
  // Use API questions if available and non-empty; otherwise fall back
  const qs = (questions && questions.length > 0)
    ? questions.filter(q => q.is_active !== 0 && q.is_active !== false)
    : _defaultEvalSteps();

  if (!qs.length) return;

  // Update total step count
  state.totalSteps = qs.length;

  // Keep the "Here's how you did across the N steps" heading in sync
  const stepCountEl = document.getElementById('ur-step-count');
  if (stepCountEl) stepCountEl.textContent = qs.length;

  const container = document.getElementById('eval-steps-container');
  const tracker   = document.getElementById('eval-steps-tracker');
  if (!container) return;

  container.innerHTML = '';
  if (tracker) tracker.innerHTML = '';

  // ── Progress slider header (replaces dot-stepper when > 8 questions) ────────
  const COMPACT_THRESHOLD = 8;
  const isCompact = qs.length > COMPACT_THRESHOLD;

  if (tracker) {
    if (isCompact) {
      // Compact mode: thin progress bar + step counter, no overflow issue
      tracker.style.cssText = 'display:block;margin-bottom:1rem;';
      tracker.innerHTML = `
        <div id="eval-step-counter" style="display:flex;justify-content:space-between;align-items:center;
             font-family:'DM Mono',monospace;font-size:.68rem;color:var(--muted);margin-bottom:.5rem;">
          <span id="eval-step-counter-label">Step 1 of ${qs.length}</span>
          <div style="display:flex;gap:4px;align-items:center;overflow-x:auto;max-width:60%;padding-bottom:2px;scrollbar-width:none;">
            ${qs.map((_, si) => `<div id="tracker-chip-${si}" title="${qs[si]?.title || 'Step '+(si+1)}"
              onclick="(()=>{if(${si}<=state.currentStep||state.answeredSteps.includes(${si})||state.skippedSteps.includes(${si}))goToStep(${si});})()"
              style="flex-shrink:0;width:8px;height:8px;border-radius:50%;cursor:pointer;
                     transition:all .2s;background:var(--border);"></div>`).join('')}
          </div>
        </div>
        <div style="height:4px;background:var(--border);border-radius:2px;overflow:hidden;">
          <div id="eval-step-progress-fill" style="height:100%;width:${Math.round(1/qs.length*100)}%;
               background:var(--accent);border-radius:2px;transition:width .3s ease;"></div>
        </div>`;
    } else {
      // Normal mode: horizontal dot-node stepper (existing behaviour)
      tracker.style.cssText = 'display:flex;align-items:flex-start;overflow-x:auto;padding-bottom:4px;';
    }
  }

  // Store questions for guardrail access
  window._currentEvalQuestions = qs;

  // State key mapping — for built-in steps keep legacy keys; custom steps use index
  const legacyKeys = ['userClaim','sourceRating','biasRating','evidenceRating',
                      'purposeRating','audienceRating','logicRating','corroboration'];

  qs.forEach((q, i) => {
    const stateKey = legacyKeys[i] || `customStep_${i}`;

    // ── Stepper node (only injected in normal mode) ─────────────────────────
    if (tracker && !isCompact) {
      const shortLabel = q.step_label || q.title ||
        (q.prompt || q.question_text || `Step ${i+1}`)
          .replace(/^(what|how|does|do|is|are|who|why|have you)\s+/i,'')
          .split(/\s+/).slice(0,3).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

      // Connector line between nodes (skip before first)
      if (i > 0) {
        const connector = document.createElement('div');
        connector.className = 'stepper-connector';
        connector.id = `stepper-conn-${i}`;
        tracker.appendChild(connector);
      }

      const node = document.createElement('div');
      node.className = 'stepper-node';
      node.id = `tracker-chip-${i}`;
      node.setAttribute('title', q.prompt || q.question_text || '');
      node.onclick = () => {
        if (state.answeredSteps.includes(i) || state.skippedSteps.includes(i) || i <= state.currentStep) {
          goToStep(i);
        }
      };
      node.innerHTML = `
        <div class="stepper-circle" id="stepper-circle-${i}">
          <span class="stepper-num">${i+1}</span>
          <svg class="stepper-check" viewBox="0 0 12 12" fill="none"><polyline points="2,6 5,9 10,3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <span class="stepper-skip-mark">–</span>
        </div>
        <div class="stepper-label">${shortLabel}</div>
      `;
      tracker.appendChild(node);
    }

    // ── Step card ────────────────────────────────────────────────────────────
    const card = document.createElement('div');
    card.className = 'eval-step card';
    card.id = `eval-step-${i}`;
    card.style.display = 'none';

    // Normalize input_type aliases from admin (radio→multiple_choice, yesno→yes_no, checkbox→multiple_answer)
    const _itype = (q.input_type || '').toLowerCase();
    const isTextarea   = _itype === 'text' || _itype === 'textarea' || !q.input_type;
    const isMulti      = _itype === 'multiple_choice' || _itype === 'radio';
    const isCheckbox   = _itype === 'checkbox' || _itype === 'multiple_answer';
    const isScale      = _itype === 'scale';
    const isYesNo      = _itype === 'yes_no' || _itype === 'yesno';

    let inputHtml = '';

    if (isTextarea) {
      inputHtml = `
        <textarea id="eq-ans-${i}" rows="3"
          placeholder="Type your answer…"
          onchange="answerStep(${i}, this.value)"
          style="width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;
                 padding:.8rem 1rem;color:var(--text);font-family:'DM Sans',sans-serif;
                 font-size:.9rem;resize:vertical;line-height:1.6;box-sizing:border-box;margin-top:.75rem;">
        </textarea>`;
    } else if (isMulti && q.options && q.options.length > 0) {
      // options may be plain strings (from API) or {value,label} objects (from built-in steps)
      // Always use the full label text as the stored value so DB branch trigger_value matching works
      const choices = q.options.map((opt) =>
        (typeof opt === 'object' && opt !== null) ? opt : { value: opt, label: opt }
      );
      inputHtml = `<div style="display:flex;flex-direction:column;gap:.6rem;margin-top:.75rem;">
        ${choices.map(ch => `
          <label class="option-label" style="cursor:pointer;display:flex;align-items:center;gap:.75rem;
                 padding:.7rem 1rem;background:var(--surface);border:1px solid var(--border);border-radius:10px;
                 transition:border-color .15s,background .15s;">
            <input type="radio" name="eq-radio-${i}" value="${ch.value.replace(/'/g, '&#39;')}"
              onchange="answerStep(${i}, this.value); _highlightOption(this);"
              style="accent-color:var(--accent);flex-shrink:0;">
            <span style="font-size:.9rem;color:var(--text);">${ch.label}</span>
          </label>`).join('')}
      </div>`;
    } else if (isYesNo) {
      // Use q.options if the DB provided them; fall back to the generic Yes/No/Unsure trio
      const yesNoChoices = (q.options && q.options.length > 0)
        ? q.options.map(opt => (typeof opt === 'object' && opt !== null) ? opt : { value: opt, label: opt })
        : [
            { value: 'Yes',   label: '✅ Yes' },
            { value: 'No',    label: '❌ No' },
            { value: 'Unsure', label: '🤔 Unsure' },
          ];
      inputHtml = `<div style="display:flex;flex-direction:column;gap:.6rem;margin-top:.75rem;">
        ${yesNoChoices.map(ch => `
          <label class="option-label" style="cursor:pointer;display:flex;align-items:center;gap:.75rem;
                 padding:.7rem 1rem;background:var(--surface);border:1px solid var(--border);border-radius:10px;
                 transition:border-color .15s,background .15s;">
            <input type="radio" name="eq-radio-${i}" value="${ch.value.replace(/'/g, '&#39;')}"
              onchange="answerStep(${i}, this.value); _highlightOption(this);"
              style="accent-color:var(--accent);flex-shrink:0;">
            <span style="font-size:.9rem;color:var(--text);">${ch.label}</span>
          </label>`).join('')}
      </div>`;
    } else if (isCheckbox && q.options && q.options.length > 0) {
      const choices = q.options.map((opt, idx) =>
        (typeof opt === 'object' && opt !== null) ? opt : { value: String(idx), label: opt }
      );
      inputHtml = `<div style="display:flex;flex-direction:column;gap:.6rem;margin-top:.75rem;" id="eq-cb-group-${i}">
        ${choices.map(ch => `
          <label class="option-label" style="cursor:pointer;display:flex;align-items:center;gap:.75rem;
                 padding:.7rem 1rem;background:var(--surface);border:1px solid var(--border);border-radius:10px;">
            <input type="checkbox" name="eq-cb-${i}" value="${ch.value}"
              onchange="_collectCheckboxAnswer(${i})"
              style="accent-color:var(--accent);flex-shrink:0;">
            <span style="font-size:.9rem;color:var(--text);">${ch.label}</span>
          </label>`).join('')}
      </div>`;
    } else if (isScale) {
      const scaleMin = q.scale_min_label || 'Not at all';
      const scaleMax = q.scale_max_label || 'Completely';
      inputHtml = `<div style="margin-top:.75rem;">
        <input type="range" id="eq-scale-${i}" min="1" max="5" value="3"
          style="width:100%;accent-color:var(--accent);"
          oninput="document.getElementById('eq-scale-lbl-${i}').textContent=this.value; answerStep(${i}, this.value);">
        <div style="display:flex;justify-content:space-between;font-size:.72rem;color:var(--muted);margin-top:.35rem;">
          <span style="max-width:40%;line-height:1.3;">1 — ${scaleMin}</span>
          <span id="eq-scale-lbl-${i}" style="color:var(--accent);font-weight:700;font-size:.9rem;">3</span>
          <span style="max-width:40%;text-align:right;line-height:1.3;">5 — ${scaleMax}</span>
        </div>
      </div>`;
    } else {
      // fallback: textarea
      inputHtml = `<textarea id="eq-ans-${i}" rows="3"
          placeholder="Your answer…"
          onchange="answerStep(${i}, this.value)"
          style="width:100%;background:var(--surface);border:1px solid var(--border);border-radius:10px;
                 padding:.8rem 1rem;color:var(--text);font-family:'DM Sans',sans-serif;
                 font-size:.9rem;resize:vertical;line-height:1.6;box-sizing:border-box;margin-top:.75rem;">
        </textarea>`;
    }

    card.innerHTML = `
      <div id="eval-step-label" style="font-family:'DM Mono',monospace;font-size:.68rem;color:var(--accent);letter-spacing:.07em;margin-bottom:.4rem;"></div>
      <p style="font-size:1rem;font-weight:600;color:var(--text);line-height:1.5;">${q.prompt || q.question_text || ''}</p>
      ${(q.hint || q.hint_text) ? `
        <div style="margin-top:.5rem;">
          <button onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.textContent=this.textContent.includes('Show')?'▲ Hide hint':'💡 Show hint'"
            style="background:none;border:none;font-size:.78rem;color:var(--muted);cursor:pointer;padding:0;font-family:'DM Mono',monospace;letter-spacing:.04em;">
            💡 Show hint
          </button>
          <div style="display:none;margin-top:.5rem;padding:.65rem .9rem;background:rgba(79,142,247,.07);border-left:3px solid var(--accent);border-radius:0 8px 8px 0;font-size:.83rem;color:var(--text);line-height:1.6;">
            ${q.hint || q.hint_text}
          </div>
        </div>` : ''}
      ${inputHtml}
      <div class="eval-step-nav">
        <div class="eval-step-nav-left">
          <button class="btn btn-ghost btn-sm" onclick="cancelEval()">✕ Cancel</button>
          <button class="btn btn-ghost btn-sm" onclick="confirmSkipStep(${i})">Skip step</button>
        </div>
        <div class="eval-step-nav-right">
          ${i > 0 ? `<button class="btn btn-ghost btn-sm" onclick="prevStep(${i})">← Back</button>` : ''}
          <button class="btn btn-primary btn-sm" onclick="nextStep(${i})">
            ${i < qs.length - 1 ? 'Next →' : 'Finish & Analyze →'}
          </button>
        </div>
      </div>
      <div id="skip-confirm-${i}" style="display:none;margin-top:.75rem;padding:.75rem 1rem;background:rgba(251,146,60,.06);border:1px solid rgba(251,146,60,.25);border-radius:10px;font-size:.85rem;color:var(--text);line-height:1.6;">
        <div style="font-weight:600;color:var(--orange);margin-bottom:.4rem;font-size:.82rem;">⚠ Skip this step?</div>
        Skipping won't count toward your assessment score.
        <div class="eval-step-nav-left" style="margin-top:.65rem;">
          <button class="btn btn-ghost btn-sm" onclick="document.getElementById('skip-confirm-${i}').style.display='none'">← Keep answering</button>
          <button class="btn btn-sm" onclick="skipStep(${i})" style="background:rgba(251,146,60,.15);color:var(--orange);border:1px solid rgba(251,146,60,.35);">Skip anyway →</button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });
};

// Highlight the selected radio's parent label
function _highlightOption(radio) {
  const name = radio.name;
  document.querySelectorAll(`input[name="${name}"]`).forEach(r => {
    const lbl = r.closest('label');
    if (!lbl) return;
    lbl.style.borderColor = r.checked ? 'var(--accent)' : 'var(--border)';
    lbl.style.background  = r.checked ? 'rgba(79,142,247,.08)' : 'var(--surface)';
  });
}

// Built-in fallback steps (used when API returns nothing)
function _defaultEvalSteps() {
  return [
    { id: 'claim', question_text: 'What is the main factual claim being made in this content?', question_type: 'textarea', is_enabled: true,
      hint: 'A claim is a statement presented as fact — e.g. "Study shows X causes Y." Look for specific numbers, named causes/effects, or absolute statements. Ignore opinions and predictions.' },
    { id: 'source', question_text: 'How credible does the source appear to be?', question_type: 'multiple_choice', is_enabled: true,
      hint: 'Check: Is the author or outlet named? Do they have a track record? Is there a "About" page? Unknown social accounts or anonymous blogs score lower than established news organisations or academic institutions.',
      choices: [{value:'yes',label:'✅ Credible — known, verifiable source'},{value:'no',label:'❌ Unreliable or unknown source'},{value:'unsure',label:'🤔 Not sure'},{value:'none_mentioned',label:'⬜ No source mentioned'}] },
    { id: 'bias', question_text: 'Does the content use emotional or biased language?', question_type: 'multiple_choice', is_enabled: true,
      hint: 'Watch for loaded words ("shocking," "devastating," "hero"), all-caps emphasis, sweeping generalisations ("everyone knows…"), or language that only frames one side. Neutral content tends to use measured, factual phrasing.',
      choices: [{value:'1',label:'Yes — emotional or one-sided language'},{value:'0',label:'No — neutral and balanced'},{value:'2',label:'Somewhat — mixed signals'}] },
    { id: 'evidence', question_text: 'What kind of evidence does the content provide?', question_type: 'multiple_choice', is_enabled: true,
      hint: 'Strong evidence includes linked studies, named experts with credentials, or official statistics. Weak evidence is "experts say…" without names, personal stories, or claims with no citation at all.',
      choices: [{value:'1',label:'Strong — specific data, citations, or studies'},{value:'2',label:'Weak — vague claims, anecdotes only'},{value:'0',label:'None — no supporting evidence'}] },
    { id: 'purpose', question_text: "What do you think is the main purpose of this content?", question_type: 'multiple_choice', is_enabled: true,
      hint: 'Ask yourself: does this content primarily want me to learn something, feel something, buy something, or vote/act a certain way? Most content has a mix, but one usually dominates.',
      choices: [{value:'inform',label:'📰 Inform'},{value:'persuade',label:'💬 Persuade'},{value:'entertain',label:'🎭 Entertain'},{value:'sell',label:'💰 Sell/Advertise'},{value:'unknown',label:'❓ Unclear'}] },
    { id: 'audience', question_text: 'Who do you think this content is aimed at?', question_type: 'multiple_choice', is_enabled: true,
      hint: 'Look at vocabulary level, assumed knowledge, and the platform it appeared on. Content with jargon targets insiders; content that uses very simplified language may be targeting those already agreeing with its viewpoint.',
      choices: [{value:'general',label:'🌐 General public'},{value:'partisan',label:'🎯 A specific group or community'},{value:'professional',label:'🎓 Experts or professionals'},{value:'hard_to_tell',label:'🤔 Hard to tell'}] },
    { id: 'logic', question_text: 'Does the reasoning in the content seem logically sound?', question_type: 'multiple_choice', is_enabled: true,
      hint: 'Common fallacies: "Everyone shares this, so it must be true" (bandwagon), "A happened, then B happened, so A caused B" (false cause), "If you disagree you must be X" (false dichotomy). Does the evidence actually support the conclusion drawn?',
      choices: [{value:'valid',label:'✅ Logically sound'},{value:'fallacy',label:'⚠️ Contains logical fallacies'},{value:'unclear',label:'🤔 Unclear or hard to tell'}] },
    { id: 'corroboration', question_text: 'Have you seen this claim reported by other independent sources?', question_type: 'multiple_choice', is_enabled: true,
      hint: 'Try a quick search of the headline or key phrase. If only one outlet is reporting something big, be cautious. Independent corroboration means outlets that aren\'t all citing the same single original source.',
      choices: [{value:'confirmed',label:'✅ Confirmed by other sources'},{value:'contradicted',label:'❌ Contradicted by others'},{value:'partial',label:'⚠️ Partially confirmed'},{value:'only_one',label:'📰 Only one source found'},{value:'not_found',label:'🔍 Not found elsewhere'},{value:'unchecked',label:'⬜ Not checked yet'}] },
  ];
}

function goToStep(stepIndex) {
  state.currentStep = stepIndex;
  document.querySelectorAll('.eval-step').forEach((el, i) => {
    el.style.display = i === stepIndex ? 'block' : 'none';
  });

  // Update stepper nodes
  const qs = window._currentEvalQuestions || [];
  document.querySelectorAll('.stepper-node').forEach((node, i) => {
    const isAnswered = state.answeredSteps.includes(i);
    const isSkipped  = state.skippedSteps.includes(i);
    const isCurrent  = i === stepIndex;
    const isPast     = i < stepIndex;

    node.classList.remove('active','done','skipped');
    if (isCurrent)       node.classList.add('active');
    else if (isSkipped)  node.classList.add('skipped');
    else if (isAnswered) node.classList.add('done');

    // Update connector fill (the line leading INTO this node)
    const conn = document.getElementById(`stepper-conn-${i}`);
    if (conn) {
      conn.classList.toggle('filled', isAnswered || isSkipped || isPast);
    }
  });

  const prog = document.getElementById('eval-progress');
  if (prog) {
    prog.style.width = `${((stepIndex + 1) / state.totalSteps) * 100}%`;
  }
  const progLabel = document.getElementById('eval-step-label');
  if (progLabel) {
    progLabel.textContent = `Step ${stepIndex + 1} of ${state.totalSteps}`;
  }

  // Compact mode: update the slim progress fill + counter + dot colours
  const compactFill = document.getElementById('eval-step-progress-fill');
  if (compactFill) {
    compactFill.style.width = `${Math.round((stepIndex + 1) / state.totalSteps * 100)}%`;
  }
  const compactCounter = document.getElementById('eval-step-counter-label');
  if (compactCounter) {
    compactCounter.textContent = `Step ${stepIndex + 1} of ${state.totalSteps}`;
  }
  // Colour compact dots
  for (let ci = 0; ci < state.totalSteps; ci++) {
    const dot = document.getElementById(`tracker-chip-${ci}`);
    if (!dot || !dot.style.borderRadius) continue; // skip non-dot nodes
    const isAns  = state.answeredSteps.includes(ci);
    const isSkip = state.skippedSteps.includes(ci);
    const isCur  = ci === stepIndex;
    dot.style.background = isAns  ? 'var(--green)'
                         : isSkip ? 'var(--orange)'
                         : isCur  ? 'var(--accent)'
                         : ci < stepIndex ? 'rgba(79,142,247,.35)'
                         : 'var(--border)';
    dot.style.transform  = isCur ? 'scale(1.4)' : 'scale(1)';
  }
}



function answerStep(stepIndex, value) {
  const legacyKeys = ['userClaim','sourceRating','biasRating','evidenceRating',
                      'purposeRating','audienceRating','logicRating','corroboration'];
  const key = legacyKeys[stepIndex] || `customStep_${stepIndex}`;
  state[key] = value;
  if (!state.answeredSteps.includes(stepIndex)) state.answeredSteps.push(stepIndex);
  state.skippedSteps = state.skippedSteps.filter(s => s !== stepIndex);
}

function _collectCheckboxAnswer(stepIndex) {
  const checked = Array.from(document.querySelectorAll(`input[name="eq-cb-${stepIndex}"]:checked`))
    .map(el => el.value);
  answerStep(stepIndex, checked.join(','));
  document.querySelectorAll(`input[name="eq-cb-${stepIndex}"]`).forEach(r => {
    const lbl = r.closest('label');
    if (!lbl) return;
    lbl.style.borderColor = r.checked ? 'var(--accent)' : 'var(--border)';
    lbl.style.background  = r.checked ? 'rgba(79,142,247,.08)' : 'var(--surface)';
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Follow-up branch engine — full question-type + content-type parity with
// main eval steps. Supports recursive nesting: each branch can carry
// nested_branches[] that fire based on the user's answer (option A + C).
// ─────────────────────────────────────────────────────────────────────────────
function _showFollowUpBranch(stepIndex, branch, nestDepth) {
  nestDepth = nestDepth || 0;
  const existingId = `followup-branch-${stepIndex}`;

  // Replace any existing follow-up box (handles chaining/recursion)
  const existing = document.getElementById(existingId);
  if (existing) existing.remove();

  const card = document.getElementById(`eval-step-${stepIndex}`);
  if (!card) return;
  const nav = card.querySelector('.eval-step-nav');
  if (!nav) return;

  const isBlock   = branch.followup_type === 'block';
  const accentClr = isBlock ? 'rgba(239,68,68,.8)' : 'rgba(251,191,36,1)';
  const bgClr     = isBlock ? 'rgba(239,68,68,.06)' : 'rgba(251,191,36,.07)';
  const bdClr     = isBlock ? 'rgba(239,68,68,.3)'  : 'rgba(251,191,36,.3)';
  const label     = isBlock ? '🚫 STOP & REVIEW' : '❓ FOLLOW-UP QUESTION';

  // ── Input type: does this follow-up collect an answer? ──────────────────────
  const rawItype   = (branch.input_type || '').toLowerCase();
  const hasInput   = rawItype && rawItype !== 'none';
  const hasNested  = Array.isArray(branch.nested_branches) && branch.nested_branches.length > 0;
  const isInteractive = hasInput || hasNested;
  const fuId       = `fu-ans-${stepIndex}-${nestDepth}`;
  const radioName  = `fu-radio-${stepIndex}-${nestDepth}`;
  const cbName     = `fu-cb-${stepIndex}-${nestDepth}`;

  let inputHtml = '';
  if (hasInput) {
    const isTA  = rawItype === 'text' || rawItype === 'textarea';
    const isMC  = rawItype === 'multiple_choice' || rawItype === 'radio';
    const isYN  = rawItype === 'yes_no' || rawItype === 'yesno';
    const isSC  = rawItype === 'scale';
    const isCB  = rawItype === 'checkbox' || rawItype === 'multiple_answer';
    const opts  = (() => {
      if (!branch.options) return [];
      if (Array.isArray(branch.options)) return branch.options;
      try { return JSON.parse(branch.options); } catch { return []; }
    })().map(o => (typeof o === 'object' && o !== null) ? o : { value: String(o), label: String(o) });

    if (isTA) {
      inputHtml = `<textarea id="${fuId}" rows="2" placeholder="Your answer…"
        style="width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;
               padding:.65rem .85rem;color:var(--text);font-family:'DM Sans',sans-serif;
               font-size:.87rem;resize:vertical;line-height:1.55;box-sizing:border-box;margin-top:.6rem;"></textarea>`;

    } else if ((isMC || isYN) && opts.length) {
      inputHtml = `<div style="display:flex;flex-direction:column;gap:.4rem;margin-top:.6rem;">
        ${opts.map(ch => `
          <label style="cursor:pointer;display:flex;align-items:center;gap:.6rem;
                 padding:.5rem .85rem;background:var(--surface);border:1px solid var(--border);
                 border-radius:8px;transition:border-color .15s,background .15s;">
            <input type="radio" name="${radioName}" value="${ch.value.replace(/"/g,'&quot;')}"
              style="accent-color:var(--accent);flex-shrink:0;"
              onchange="this.closest('div').querySelectorAll('label').forEach(l=>{
                l.style.borderColor='var(--border)';l.style.background='var(--surface)';});
                this.closest('label').style.borderColor='var(--accent)';
                this.closest('label').style.background='rgba(79,142,247,.08)';">
            <span style="font-size:.87rem;color:var(--text);">${ch.label}</span>
          </label>`).join('')}
      </div>`;

    } else if (isYN) {
      // Fallback yes/no without DB options
      const dflt = [{value:'Yes',label:'✅ Yes'},{value:'No',label:'❌ No'},{value:'Unsure',label:'🤔 Unsure'}];
      inputHtml = `<div style="display:flex;flex-direction:column;gap:.4rem;margin-top:.6rem;">
        ${dflt.map(ch => `
          <label style="cursor:pointer;display:flex;align-items:center;gap:.6rem;
                 padding:.5rem .85rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;">
            <input type="radio" name="${radioName}" value="${ch.value}" style="accent-color:var(--accent);flex-shrink:0;">
            <span style="font-size:.87rem;color:var(--text);">${ch.label}</span>
          </label>`).join('')}
      </div>`;

    } else if (isSC) {
      const sMin = branch.scale_min_label || 'Not at all';
      const sMax = branch.scale_max_label || 'Completely';
      inputHtml = `<div style="margin-top:.6rem;">
        <input type="range" id="${fuId}" min="1" max="5" value="3"
          style="width:100%;accent-color:var(--accent);"
          oninput="document.getElementById('${fuId}-lbl').textContent=this.value;">
        <div style="display:flex;justify-content:space-between;font-size:.7rem;color:var(--muted);margin-top:.3rem;">
          <span style="max-width:40%;line-height:1.3;">1 — ${sMin}</span>
          <span id="${fuId}-lbl" style="color:var(--accent);font-weight:700;">3</span>
          <span style="max-width:40%;text-align:right;line-height:1.3;">5 — ${sMax}</span>
        </div>
      </div>`;

    } else if (isCB && opts.length) {
      inputHtml = `<div style="display:flex;flex-direction:column;gap:.4rem;margin-top:.6rem;" id="fu-cbg-${stepIndex}-${nestDepth}">
        ${opts.map(ch => `
          <label style="cursor:pointer;display:flex;align-items:center;gap:.6rem;
                 padding:.5rem .85rem;background:var(--surface);border:1px solid var(--border);border-radius:8px;">
            <input type="checkbox" name="${cbName}" value="${ch.value.replace(/"/g,'&quot;')}"
              style="accent-color:var(--accent);flex-shrink:0;"
              onchange="this.closest('label').style.borderColor=this.checked?'var(--accent)':'var(--border)';
                        this.closest('label').style.background=this.checked?'rgba(79,142,247,.08)':'var(--surface)';">
            <span style="font-size:.87rem;color:var(--text);">${ch.label}</span>
          </label>`).join('')}
      </div>`;
    }
  }

  // ── Content block: lesson | image | text | quiz | url | file ────────────────
  let contentHtml = '';
  const ct = branch.content_type;

  if (ct === 'lesson' && branch.lesson_id) {
    contentHtml = `<div style="margin-top:.7rem;padding:.6rem .9rem;
      background:rgba(79,142,247,.07);border:1px solid rgba(79,142,247,.25);border-radius:8px;">
      <div style="font-size:.65rem;color:var(--accent);font-family:'DM Mono',monospace;
           letter-spacing:.05em;margin-bottom:.3rem;">📖 SUGGESTED LESSON</div>
      <a onclick="ptHideOverlay?.('sp-pretest-overlay');openLesson?.('${branch.lesson_key||''}');return false;"
         href="#" style="font-size:.85rem;color:var(--accent);font-weight:600;text-decoration:none;">
        ${branch.lesson_title || 'View Lesson →'}
      </a>
    </div>`;

  } else if (ct === 'image') {
    // Support both content_url and dedicated image_url field
    const imgSrc = branch.image_url || branch.content_url || '';
    if (imgSrc) {
      contentHtml = `<div style="margin-top:.7rem;">
        <img src="${imgSrc}" alt="Reference image" loading="lazy"
          style="width:100%;max-height:260px;object-fit:contain;border-radius:8px;
                 border:1px solid var(--border);background:rgba(0,0,0,.15);display:block;"
          onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
        <div style="display:none;margin-top:.4rem;padding:.5rem .75rem;background:rgba(255,255,255,.04);
             border:1px solid var(--border);border-radius:8px;align-items:center;gap:.5rem;
             font-size:.78rem;color:var(--muted);">
          ⚠ Image could not load
          <a href="${imgSrc}" target="_blank" rel="noopener"
             style="color:var(--accent);margin-left:.3rem;">Open in new tab ↗</a>
        </div>
      </div>`;
    }

  } else if (ct === 'text' && branch.content_url) {
    contentHtml = `<div style="margin-top:.7rem;padding:.65rem .9rem;
      background:rgba(255,255,255,.03);border-left:3px solid ${accentClr};
      border-radius:0 8px 8px 0;font-size:.83rem;color:var(--text);line-height:1.6;">
      ${branch.content_url}
    </div>`;

  } else if (ct === 'quiz' && branch.quiz_question_id) {
    const qbId = `fu-quiz-${stepIndex}-${nestDepth}`;
    fetch(`/quiz/question/${branch.quiz_question_id}`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(q => {
        if (!q) return;
        const placeholder = document.getElementById(qbId);
        if (!placeholder) return;
        const opts = Array.isArray(q.options) ? q.options : JSON.parse(q.options || '[]');
        placeholder.outerHTML = `
          <div style="margin-top:.7rem;padding:.7rem .9rem;background:rgba(79,142,247,.05);
               border:1px solid rgba(79,142,247,.2);border-radius:8px;">
            <div style="font-size:.65rem;color:var(--accent);font-family:'DM Mono',monospace;
                 letter-spacing:.05em;margin-bottom:.4rem;">🧩 QUICK QUIZ</div>
            <div style="font-size:.88rem;font-weight:600;margin-bottom:.55rem;">${q.question_text}</div>
            <div style="display:flex;flex-direction:column;gap:.35rem;">
              ${opts.map((o, oi) => `
                <button onclick="this.closest('div').querySelectorAll('button').forEach(b=>{b.disabled=true;b.style.opacity='.55';});
                  this.style.opacity='1';this.style.borderColor='${oi===q.correct_index?'rgba(52,211,153,.7)':'rgba(239,68,68,.5)'}';"
                  style="text-align:left;padding:.45rem .7rem;border:1px solid var(--border);border-radius:7px;
                         background:var(--surface);color:var(--text);font-size:.82rem;cursor:pointer;transition:border-color .15s;">
                  ${String.fromCharCode(65+oi)}. ${o}
                </button>`).join('')}
            </div>
          </div>`;
      }).catch(() => {});
    contentHtml = `<div id="${qbId}" style="margin-top:.5rem;color:var(--muted);font-size:.8rem;">Loading quiz…</div>`;

  } else if (ct === 'url' && branch.content_url) {
    // Nicely formatted URL card
    let displayUrl = '';
    try { displayUrl = new URL(branch.content_url).hostname; } catch { displayUrl = branch.content_url; }
    contentHtml = `<div style="margin-top:.7rem;">
      <a href="${branch.content_url}" target="_blank" rel="noopener"
         style="display:flex;align-items:center;gap:.6rem;padding:.55rem .85rem;
                background:rgba(79,142,247,.06);border:1px solid rgba(79,142,247,.2);
                border-radius:8px;text-decoration:none;transition:background .15s;"
         onmouseenter="this.style.background='rgba(79,142,247,.12)';"
         onmouseleave="this.style.background='rgba(79,142,247,.06)';">
        <span style="font-size:1rem;flex-shrink:0;">🔗</span>
        <div style="min-width:0;flex:1;">
          <div style="font-size:.72rem;color:var(--muted);font-family:'DM Mono',monospace;
               white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${displayUrl}</div>
          <div style="font-size:.82rem;color:var(--accent);font-weight:600;white-space:nowrap;
               overflow:hidden;text-overflow:ellipsis;">${branch.link_label || 'Open resource ↗'}</div>
        </div>
        <span style="font-size:.75rem;color:var(--muted);flex-shrink:0;">↗</span>
      </a>
    </div>`;

  } else if (ct === 'file') {
    const fileUrl  = branch.file_url || branch.content_url || '';
    const fileName = branch.file_name || (fileUrl ? fileUrl.split('/').pop() : 'Download file');
    const fileExt  = fileName.split('.').pop().toUpperCase();
    const fileIcon = { PDF:'📄', DOC:'📝', DOCX:'📝', XLS:'📊', XLSX:'📊',
                       PPT:'📊', PPTX:'📊', ZIP:'🗜', PNG:'🖼', JPG:'🖼',
                       MP4:'🎬', MP3:'🎵' }[fileExt] || '📎';
    if (fileUrl) {
      contentHtml = `<div style="margin-top:.7rem;">
        <a href="${fileUrl}" target="_blank" rel="noopener" download
           style="display:flex;align-items:center;gap:.6rem;padding:.55rem .85rem;
                  background:rgba(52,211,153,.05);border:1px solid rgba(52,211,153,.2);
                  border-radius:8px;text-decoration:none;transition:background .15s;"
           onmouseenter="this.style.background='rgba(52,211,153,.1)';"
           onmouseleave="this.style.background='rgba(52,211,153,.05)';">
          <span style="font-size:1.1rem;flex-shrink:0;">${fileIcon}</span>
          <div style="min-width:0;flex:1;">
            <div style="font-size:.82rem;color:var(--text);font-weight:600;white-space:nowrap;
                 overflow:hidden;text-overflow:ellipsis;">${fileName}</div>
            <div style="font-size:.68rem;color:var(--muted);font-family:'DM Mono',monospace;
                 letter-spacing:.03em;">${fileExt} · Click to download</div>
          </div>
          <span style="font-size:.75rem;color:rgba(52,211,153,.8);flex-shrink:0;">⬇</span>
        </a>
      </div>`;
    }
  }

  // ── Action buttons ──────────────────────────────────────────────────────────
  const submitOrContinue = isInteractive
    ? `<button class="btn btn-sm" onclick="_submitFollowUp(${stepIndex}, ${nestDepth})"
         style="font-size:.8rem;padding:.3rem .9rem;background:${bgClr};
                color:${accentClr};border:1px solid ${bdClr};">Submit →</button>`
    : `<button class="btn btn-sm" onclick="_dismissFollowUp(${stepIndex}, true)"
         style="font-size:.8rem;padding:.3rem .9rem;background:${bgClr};
                color:${isBlock?'var(--red)':accentClr};border:1px solid ${bdClr};">
         ${isBlock ? 'I understand — continue →' : 'Got it — continue →'}
       </button>`;

  const updateBtn = !isBlock
    ? `<button class="btn btn-ghost btn-sm" onclick="_dismissFollowUp(${stepIndex}, false)"
         style="font-size:.8rem;padding:.3rem .8rem;">Update my answer</button>`
    : '';

  // ── Build and inject box ────────────────────────────────────────────────────
  const box = document.createElement('div');
  box.id = existingId;
  // Store branch data on the element for _submitFollowUp to read
  box._branchData    = branch;
  box._nestDepth     = nestDepth;
  box.style.cssText  = `margin-top:.75rem;padding:.85rem 1rem;background:${bgClr};
    border:1px solid ${bdClr};border-left:3px solid ${accentClr};border-radius:8px;
    font-size:.85rem;color:var(--text);line-height:1.6;`;
  box.innerHTML = `
    <div style="font-weight:700;color:${accentClr};font-size:.72rem;
         font-family:'DM Mono',monospace;letter-spacing:.06em;margin-bottom:.35rem;">${label}</div>
    <div style="font-size:.9rem;font-weight:600;color:var(--text);margin-bottom:.25rem;">${branch.followup_prompt}</div>
    ${inputHtml}
    ${contentHtml}
    <div style="display:flex;gap:.6rem;margin-top:.65rem;flex-wrap:wrap;">
      ${submitOrContinue}
      ${updateBtn}
    </div>`;

  nav.insertAdjacentElement('beforebegin', box);
}

// ─────────────────────────────────────────────────────────────────────────────
// Collect the follow-up's answer, check nested branches, chain or advance
// ─────────────────────────────────────────────────────────────────────────────
function _submitFollowUp(stepIndex, nestDepth) {
  const box = document.getElementById(`followup-branch-${stepIndex}`);
  if (!box) return;
  const branch = box._branchData;
  if (!branch) { _dismissFollowUp(stepIndex, true); return; }

  const rawItype  = (branch.input_type || '').toLowerCase();
  const fuId      = `fu-ans-${stepIndex}-${nestDepth}`;
  const radioName = `fu-radio-${stepIndex}-${nestDepth}`;
  const cbName    = `fu-cb-${stepIndex}-${nestDepth}`;

  // ── Read answer ─────────────────────────────────────────────────────────────
  let answer = '';
  if (rawItype === 'scale') {
    const el = document.getElementById(fuId);
    answer = el ? el.value : '3';
  } else if (rawItype === 'checkbox' || rawItype === 'multiple_answer') {
    const checked = document.querySelectorAll(`input[name="${cbName}"]:checked`);
    answer = Array.from(checked).map(c => c.value).join(', ');
  } else if (rawItype === 'text' || rawItype === 'textarea') {
    const el = document.getElementById(fuId);
    answer = el ? el.value.trim() : '';
  } else {
    // radio types (multiple_choice, yes_no, etc.)
    const radio = document.querySelector(`input[name="${radioName}"]:checked`);
    answer = radio ? radio.value : '';
  }

  // ── Match nested branches ───────────────────────────────────────────────────
  const nestedBranches = Array.isArray(branch.nested_branches) ? branch.nested_branches : [];
  const ansLow = answer.toLowerCase().trim();
  let matched  = null;

  for (const nb of nestedBranches) {
    if (nb.is_active === 0 || nb.is_active === false) continue;
    const cond     = (nb.trigger_condition || '').toLowerCase();
    const trigVal  = (nb.trigger_value   || '').toLowerCase().trim();
    if      (cond === 'equals'  && ansLow === trigVal)         { matched = nb; break; }
    else if (cond === 'includes'&& ansLow.includes(trigVal))   { matched = nb; break; }
    else if (cond === 'skipped' && !answer)                    { matched = nb; break; }
    else if (cond === 'any')                                   { matched = nb; break; }
  }

  if (matched) {
    // Chain into the next follow-up level
    _showFollowUpBranch(stepIndex, matched, nestDepth + 1);
  } else {
    _dismissFollowUp(stepIndex, true);
  }
}

function _dismissFollowUp(stepIndex, proceed) {
  const box = document.getElementById(`followup-branch-${stepIndex}`);
  if (box) box.remove();
  if (proceed) _advanceStep(stepIndex);
}

function cancelEval() {
  if (confirm('Cancel this evaluation and return to the start?')) {
    newEval();
  }
}

function confirmSkipStep(stepIndex) {
  const box = document.getElementById(`skip-confirm-${stepIndex}`);
  if (box) box.style.display = box.style.display === 'none' ? 'block' : 'none';
}

function skipStep(stepIndex) {
  const box = document.getElementById(`skip-confirm-${stepIndex}`);
  if (box) box.style.display = 'none';

  // Check for admin-configured 'skipped' branches first
  const qs = window._currentEvalQuestions || [];
  const q  = qs[stepIndex];
  if (q && Array.isArray(q.branches)) {
    const skippedBranch = q.branches.find(b =>
      b.is_active !== 0 && (b.trigger_condition || '').toLowerCase() === 'skipped'
    );
    if (skippedBranch) {
      if (!state.skippedSteps.includes(stepIndex)) state.skippedSteps.push(stepIndex);
      state.answeredSteps = state.answeredSteps.filter(s => s !== stepIndex);
      _showFollowUpBranch(stepIndex, skippedBranch);
      goToStep(stepIndex); // stay on step to show the followup
      return;
    }
  }

  if (!state.skippedSteps.includes(stepIndex)) state.skippedSteps.push(stepIndex);
  state.answeredSteps = state.answeredSteps.filter(s => s !== stepIndex);
  if (stepIndex < state.totalSteps - 1) goToStep(stepIndex + 1);
  else beginAnalysis();
}

function nextStep(stepIndex) {
  const qs = window._currentEvalQuestions || [];
  const q  = qs[stepIndex];

  // Read the answer using the same key mapping as answerStep
  const legacyKeys = ['userClaim','sourceRating','biasRating','evidenceRating',
                      'purposeRating','audienceRating','logicRating','corroboration'];
  const stateKey = legacyKeys[stepIndex] || `customStep_${stepIndex}`;
  const answer   = state[stateKey];

  // ── If no answer given, treat as an explicit skip (orange, counted) ────────
  if (answer === null || answer === undefined || answer === '') {
    if (!state.skippedSteps.includes(stepIndex)) state.skippedSteps.push(stepIndex);
    state.answeredSteps = state.answeredSteps.filter(s => s !== stepIndex);
    if (stepIndex < state.totalSteps - 1) goToStep(stepIndex + 1);
    else beginAnalysis();
    return;
  }
  // ──────────────────────────────────────────────────────────────────────────

  // Only trigger once per step (no existing follow-up/guardrail already showing)
  const alreadyShowing = document.getElementById(`followup-branch-${stepIndex}`) ||
                         document.getElementById(`guardrail-${stepIndex}`);
  if (q && !alreadyShowing) {
    const result = _checkGuardrail(q, answer, stepIndex);
    if (result) {
      if (typeof result === 'object' && result.followup_prompt) {
        _showFollowUpBranch(stepIndex, result);
      } else if (typeof result === 'string') {
        _showGuardrail(stepIndex, result);
      }
      return; // block advancement until dismissed
    }
  }

  _advanceStep(stepIndex);
}

function _advanceStep(stepIndex) {
  if (stepIndex < state.totalSteps - 1) goToStep(stepIndex + 1);
  else beginAnalysis();
}

function prevStep(stepIndex) {
  if (stepIndex > 0) goToStep(stepIndex - 1);
}

function setConfidence(val) {
  state.confidence = val;
  document.querySelectorAll('.conf-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.val === val);
  });
}

function updateSlider(val) {
  state.userScore = parseInt(val);
  const disp = document.getElementById('score-display');
  if (disp) disp.textContent = val;
}

// ─────────────────────────────────────────────────────────────────────────────
// Main analysis flow
// ─────────────────────────────────────────────────────────────────────────────
async function beginAnalysis() {
  show('phase-loading');
  animateLoader();

  let analysisResult;
  try {
    const res = await fetch(`${API_BASE}/analyze`, {
      method:  'POST',
      credentials: 'include',
      headers: _authHeaders(),
      body:    JSON.stringify({
        text:             state.inputType === 'text' ? state.content : null,
        image_data:       state.inputType === 'image' ? state.imageData : null,
        file_data:        state.inputType === 'file'  ? state.fileData : null,
        file_name:        state.inputType === 'file'  ? state.fileName : null,
        input_type:       state.inputType,
        session_token:    getSessionToken(),
        user_id:          USER_ID,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `API error ${res.status}`);
    }
    analysisResult = await res.json();
    state.evaluationId = analysisResult.submission_id ?? analysisResult.evaluation_id;
    state.systemResult  = analysisResult;
    state._sourceDiversity = analysisResult.source_diversity || null;   // v6.0
  } catch (err) {
    show('phase-eval');
    showToast(`Analysis failed: ${err.message}`);
    return;
  }

  if (analysisResult.no_claims_detected) { _showNoClaimsPhase(); return; }

  let comparisonResult = null;
  try {
    const skippedNames = state.skippedSteps
      .map(s => ['claims','source','bias','evidence','purpose','audience','logic','corroboration'][s])
      .filter(Boolean);
    const res2 = await fetch(`${API_BASE}/user-evaluation`, {
      method:  'POST',
      credentials: 'include',
      headers: _authHeaders(),
      body:    JSON.stringify({
        evaluation_id:     state.evaluationId,
        user_id:           USER_ID,
        session_token:     getSessionToken(),
        identified_claims: state.userClaim ? [state.userClaim] : [],
        source_credible:   state.sourceRating,
        bias_detected:     state.biasRating === '1',
        evidence_assessed: state.evidenceRating === '1' || state.evidenceRating === '2',
        user_score:        state.userScore,
        user_label:        state.userScore >= 60 ? 'Likely Credible' : state.userScore >= 40 ? 'Uncertain' : 'Likely Misleading',
        confidence_level:  state.confidence,
        skipped_steps:     skippedNames,
        purpose_rating:    state.purposeRating    || null,
        audience_rating:   state.audienceRating   || null,
        logic_rating:      state.logicRating      || null,
        corroboration:     state.corroboration    || null,
        total_steps:       state.totalSteps,
      }),
    });
    if (res2.ok) {
      const data2 = await res2.json();
      comparisonResult = data2.comparison;
      state.userEvaluationId = data2.user_evaluation_id;
      state.comparisonResult = comparisonResult;
    }
  } catch (err) {
    console.warn('User evaluation save failed:', err);
  }

  renderResults(analysisResult, comparisonResult);
}

// ─────────────────────────────────────────────────────────────────────────────
// No-Claims flow (unchanged)
// ─────────────────────────────────────────────────────────────────────────────
function _showNoClaimsPhase() {
  _gatherBackgroundEvidence(state.content, state.inputType);
  if (state.userClaim && state.userClaim.trim().length >= 5) {
    show('phase-loading');
    document.getElementById('loading-text').textContent = 'Using your identified claim…';
    _runUserClaimPipeline(state.userClaim.trim());
    return;
  }
  if (state.inputType === 'text' && state.content && state.content.trim().length >= 5) {
    show('phase-loading');
    document.getElementById('loading-text').textContent = 'Re-analyzing your content…';
    _runUserClaimPipeline(state.content.trim().slice(0, 500));
    return;
  }
  document.getElementById('no-claims-input-card').style.display = 'block';
  document.getElementById('no-claims-validating').style.display = 'none';
  document.getElementById('no-claims-warning').style.display    = 'none';
  document.getElementById('no-claims-claim-input').value        = '';
  const existingNotice = document.getElementById('no-claims-soft-notice');
  if (!existingNotice) {
    const noticeEl = document.createElement('div');
    noticeEl.id = 'no-claims-soft-notice';
    noticeEl.style.cssText = 'padding:.85rem 1.1rem;background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.25);border-radius:10px;font-size:.88rem;color:var(--yellow);margin-bottom:1rem;line-height:1.6;';
    noticeEl.innerHTML = '<strong>No clear claim was identified.</strong> The system will still retrieve related sources based on the provided text.';
    const inputCard = document.getElementById('no-claims-input-card');
    inputCard.parentNode.insertBefore(noticeEl, inputCard);
  }
  show('phase-no-claims');
}

async function submitNoClaimsInput() {
  const claimText = document.getElementById('no-claims-claim-input').value.trim();
  if (claimText.length < 5) { showToast('Please type what you think the claim is (at least 5 characters).'); return; }
  document.getElementById('no-claims-input-card').style.display  = 'none';
  document.getElementById('no-claims-warning').style.display     = 'none';
  document.getElementById('no-claims-validating').style.display  = 'block';
  let validationResult;
  try {
    const res = await fetch(`${API_BASE}/analyze/validate-claim`, {
      method: 'POST', credentials: 'include', headers: _authHeaders(),
      body:   JSON.stringify({ claim_text: claimText, submission_id: state.evaluationId }),
    });
    if (res.status === 503) { await _runUserClaimPipeline(claimText); return; }
    if (!res.ok) throw new Error(`Validation error ${res.status}`);
    validationResult = await res.json();
  } catch (err) {
    await _runUserClaimPipeline(claimText); return;
  }
  document.getElementById('no-claims-validating').style.display = 'none';
  if (validationResult.is_claim) {
    await _runUserClaimPipeline(claimText);
  } else {
    state._pendingUserClaim = claimText;
    document.getElementById('no-claims-warning-reason').textContent =
      validationResult.reason || "The input doesn't appear to be a specific, verifiable factual statement.";
    document.getElementById('no-claims-warning').style.display = 'block';
  }
}

function retryClaimInput() {
  document.getElementById('no-claims-warning').style.display    = 'none';
  document.getElementById('no-claims-input-card').style.display = 'block';
}

async function forceSubmitUserClaim() {
  const claimText = state._pendingUserClaim;
  if (!claimText) return;
  document.getElementById('no-claims-warning').style.display    = 'none';
  document.getElementById('no-claims-validating').style.display = 'block';
  await _runUserClaimPipeline(claimText);
}

async function _runUserClaimPipeline(claimText) {
  try {
    const res = await fetch(`${API_BASE}/analyze/user-claim`, {
      method: 'POST', credentials: 'include', headers: _authHeaders(),
      body:   JSON.stringify({ submission_id: state.evaluationId, claim_text: claimText, session_token: getSessionToken(), user_id: USER_ID }),
    });
    if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || `Pipeline error ${res.status}`); }
    const result = await res.json();
    const normalised = { ...state.systemResult, ...result, no_claims_detected: false };
    if (result.evidence && result.evidence.length > 0) {
      const wrapEl = document.getElementById('evidence-claim-wrap');
      if (wrapEl) wrapEl.style.display = 'block';
      _renderEvidenceInto('evidence-claim-list', result.evidence);
    }
    state._submissionEvidenceRendered = true;
    renderResults(normalised, state.comparisonResult);
  } catch (err) {
    show('phase-no-claims');
    document.getElementById('no-claims-validating').style.display = 'none';
    document.getElementById('no-claims-input-card').style.display = 'block';
    showToast(`Pipeline error: ${err.message}`);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Loader
// ─────────────────────────────────────────────────────────────────────────────
function animateLoader() {
  const messages = [
    'Reading your content…',
    'Retrieving related articles…',
    'Comparing perspectives…',
    'Ranking sources by relevance…',
    'Pulling together what we found…',
    'Almost there…',
  ];
  let i = 0;
  const el = document.getElementById('loading-text');
  const iv = setInterval(() => {
    el.style.opacity = 0;
    setTimeout(() => { el.textContent = messages[i++ % messages.length]; el.style.opacity = 1; }, 200);
  }, 600);
  setTimeout(() => clearInterval(iv), 30000);
  state._loaderInterval = iv;
}

// ─────────────────────────────────────────────────────────────────────────────
// Render results
// ─────────────────────────────────────────────────────────────────────────────
function renderResults(r, comparison) {
  if (state._loaderInterval) clearInterval(state._loaderInterval);
  show('phase-results');

  const isInconclusive = r.is_inconclusive === true;
  const lb = document.getElementById('sys-label');
  if (lb) {
    lb.textContent = r.label;
    lb.className = 'label-badge ' + (isInconclusive ? 'badge-yellow' :
      r.label === 'Credible' ? 'badge-green' : r.label === 'Misleading' ? 'badge-red' : 'badge-yellow');
  }
  const sysExp = document.getElementById('sys-explanation');
  if (sysExp) sysExp.textContent = r.explanation;

  const covEl = document.getElementById('eq-coverage');
  if (covEl) {
    const covPct = ((r.evidence_coverage ?? 0) * 100).toFixed(0);
    covEl.textContent  = covPct + '%';
    covEl.style.color  = covPct >= 60 ? 'var(--green)' : covPct >= 30 ? 'var(--yellow)' : 'var(--red)';
  }
  const modeEl = document.getElementById('eq-mode');
  if (modeEl) modeEl.textContent = r.live_search_used ? '🌐 Live Search (FAISS had no results)' : '📚 FAISS Corpus';

  const partEl = document.getElementById('eq-partial');
  if (partEl) { partEl.textContent = r.is_partial ? 'Yes — verdict based on partial evidence' : 'No'; partEl.style.color = r.is_partial ? 'var(--yellow)' : 'var(--green)'; }

  const warnEl = document.getElementById('eq-warning');
  if (warnEl) warnEl.style.display = 'none';

  const expSrc = document.getElementById('explanation-source');
  if (expSrc) expSrc.textContent = r.explanation_source === 'ollama' ? '✦ Explanation generated by local AI (Ollama)' : '✦ Rule-based explanation';

  const stepsCompleted = state.totalSteps - state.skippedSteps.length;
  const compUser = document.getElementById('comp-user');
  if (compUser) compUser.textContent = `${stepsCompleted} / ${state.totalSteps}`;
  const skippedAreaNames = state.skippedSteps
    .map(s => {
      const names = ['Claim','Source','Bias','Evidence','Purpose','Audience','Logic','Corroboration'];
      const qs = window._currentEvalQuestions || [];
      return names[s] || (qs[s] && qs[s].title) || `Step ${s + 1}`;
    })
    .filter(Boolean);
  const compConf = document.getElementById('comp-user-conf');
  if (compConf) compConf.textContent = '';

  // ── Feedback area ─────────────────────────────────────────────────────────
  const fb = document.getElementById('feedback-area');
  if (fb) {
    fb.innerHTML = '';
    if (comparison && comparison.feedback_items) {
      comparison.feedback_items.forEach(item => {
        fb.innerHTML += _renderFeedbackItem(item);
      });
    }
  }

  // ── Annotation stubs ──────────────────────────────────────────────────────
  const at = document.getElementById('annotated-text');
  const annSection = document.getElementById('section-annotation');
  const annTip = document.getElementById('ann-tip');
  const annTipType = document.getElementById('ann-tip-type');
  const annTipDesc = document.getElementById('ann-tip-desc');
  const annClaimSummary = document.getElementById('ann-claim-summary');
  const annClaimList = document.getElementById('ann-claim-list');
  const annCounts = document.getElementById('ann-counts');

  if (r.annotated && r.annotated.length > 0 && at) {
    // Annotation rendering (unchanged from v5.0)
    let _claimCount = 0, _opinionCount = 0, _ctxCount = 0;
    const _claimsForSummary = [];
    at.innerHTML = r.annotated.map((seg, i) => {
      let cls = 'ann-context';
      if (seg.type === 'claim') { cls = 'ann-claim' + (seg.status === 'contradict' ? ' ann-contra' : seg.status === 'support' ? ' ann-support' : ''); _claimCount++; _claimsForSummary.push({ text: seg.text, status: seg.status }); }
      else if (seg.type === 'opinion') { cls = 'ann-opinion'; _opinionCount++; }
      else { _ctxCount++; }
      return `<span class="${cls}" data-type="${seg.type}" data-status="${seg.status||''}" data-idx="${i}">${seg.text} </span>`;
    }).join('');
    if (annSection) annSection.style.display = '';
    if (annCounts) annCounts.textContent = `${_claimCount} claim${_claimCount!==1?'s':''} · ${_opinionCount} opinion${_opinionCount!==1?'s':''} · ${_ctxCount} context`;
    if (_claimsForSummary.length > 0 && annClaimList) {
      annClaimList.innerHTML = _claimsForSummary.map(c => {
        const icon = c.status === 'support' ? '✅' : c.status === 'contradict' ? '❌' : '⬜';
        const col  = c.status === 'support' ? 'var(--green)' : c.status === 'contradict' ? 'var(--red)' : 'var(--muted)';
        return `<div style="display:flex;gap:.6rem;align-items:flex-start;"><span>${icon}</span><span style="color:${col};flex:1">${c.text}</span></div>`;
      }).join('');
      if (annClaimSummary) annClaimSummary.style.display = 'block';
    }
  }

  // ── Evidence ──────────────────────────────────────────────────────────────
  if (!state._submissionEvidenceRendered) {
    state._submissionEvidenceRendered = true;
    _renderEvidenceInto('evidence-submission-list', r.evidence || r.articles || []);
  }

  // ── Lessons ───────────────────────────────────────────────────────────────
  const la = document.getElementById('lessons-area');
  if (la) {
    la.innerHTML = '';
    const triggeredLessons = comparison?.triggered_lessons || [];
    if (triggeredLessons.length > 0) {
      const topicIcons = { claim_detection:'🎯', source_verification:'🔍', bias_detection:'⚡', evidence_evaluation:'🧪', general:'📚' };
      triggeredLessons.forEach(lesson => {
        la.innerHTML += `<div class="lesson-card" onclick="markLessonRead('${lesson.key}')">
          <div class="lesson-icon">${topicIcons[lesson.topic] || '📖'}</div>
          <div class="lesson-title">${lesson.title || lesson.key}</div>
          <div class="lesson-text">${lesson.trigger_reason || ''}</div>
          <a href="lessons.html#${lesson.key}" class="lesson-link">Read full lesson →</a>
        </div>`;
      });
    } else {
      la.innerHTML = '<div class="no-lessons">Great job — no specific lesson gaps detected. Keep practicing with the <a href="lessons.html#quiz" style="color:var(--accent)">Quiz</a>.</div>';
    }
  }

  const reeval = document.getElementById('reeval-score');
  if (reeval) reeval.value = state.userScore;
  const reevalDisp = document.getElementById('reeval-score-display');
  if (reevalDisp) reevalDisp.textContent = state.userScore;

  startPostAnalysisFlow(r);

  try {
    localStorage.setItem('sp_last_result', JSON.stringify({
      result: r, comparison,
      state: {
        content: state.content, inputType: state.inputType, userClaim: state.userClaim,
        skippedSteps: state.skippedSteps, answeredSteps: state.answeredSteps,
        totalSteps: state.totalSteps, userScore: state.userScore, evaluationId: state.evaluationId,
        sourceRating: state.sourceRating, biasRating: state.biasRating,
        evidenceRating: state.evidenceRating, purposeRating: state.purposeRating,
        audienceRating: state.audienceRating, logicRating: state.logicRating,
        corroboration: state.corroboration,
      },
      savedAt: Date.now(),
    }));
  } catch(e) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// v6.0: Feedback item renderer
// Distinct visual styles per feedback type:
//   good         → green left-border
//   warn / bad   → orange / red
//   calibration  → amber background (Dunning-Kruger warning)
//   missed       → yellow chip with "What you missed:" header
//   diversity    → blue/teal chip
// ─────────────────────────────────────────────────────────────────────────────
function _renderFeedbackItem(item) {
  if (item.type === 'calibration') {
    return `<div style="background:rgba(251,191,36,.1);border-left:3px solid var(--yellow);border-radius:8px;padding:.8rem 1rem;margin-bottom:.5rem;line-height:1.6;">
      <div style="font-weight:700;color:var(--yellow);font-size:.82rem;margin-bottom:.25rem;">🧠 CONFIDENCE VS. THOROUGHNESS MISMATCH</div>
      <div style="font-size:.85rem;color:var(--text);">${item.text}</div>
      ${item.learn_more ? `<a href="lessons.html#${item.learn_more}" style="display:inline-block;margin-top:.5rem;font-size:.78rem;color:var(--accent);">→ Learn about metacognition</a>` : ''}
    </div>`;
  }
  if (item.type === 'missed') {
    const step = item.step_name ? item.step_name.charAt(0).toUpperCase() + item.step_name.slice(1) : '';
    return `<div style="background:rgba(251,191,36,.06);border-left:3px solid rgba(251,191,36,.5);border-radius:8px;padding:.75rem 1rem;margin-bottom:.4rem;line-height:1.6;">
      <div style="font-weight:700;color:var(--yellow);font-size:.75rem;margin-bottom:.2rem;">
        ⬜ WHAT YOU MISSED${step ? `: ${step.toUpperCase()}` : ''}
      </div>
      <div style="font-size:.84rem;color:var(--text);">${item.text.replace(/^You skipped checking [^:]+:\s*/,'')}</div>
      ${item.learn_more ? `<a href="lessons.html#${item.learn_more}" style="display:inline-block;margin-top:.4rem;font-size:.78rem;color:var(--accent);">→ Learn ${item.step_name || 'more'}</a>` : ''}
    </div>`;
  }
  const cls = item.type === 'good' ? 'feedback-good' : item.type === 'warn' ? 'feedback-warn' : 'feedback-bad';
  return `<div class="${cls}">${item.type === 'good' ? '✓' : '✗'} ${item.text}</div>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// v6.0: Source Diversity Panel
// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// Post-analysis phases
// ─────────────────────────────────────────────────────────────────────────────
const reflectionState = { position: null, reasoning: '', saved: false };

function startPostAnalysisFlow(analysisResult) {
  _fetchExploration(analysisResult);
  _renderUserResult(analysisResult);
  _renderContentRetrieval(analysisResult);
  _initUserInput(analysisResult);
  const ta = document.getElementById('r6-reasoning-input');
  if (ta) ta.addEventListener('input', () => {
    const cc = document.getElementById('r6-char-count');
    if (cc) cc.textContent = ta.value.length + ' characters';
  });
}

// ── SECTION 1: User Result ────────────────────────────────────────────────────
async function _renderUserResult(analysisResult) {
  document.getElementById('ur-loading').style.display = 'flex';
  document.getElementById('ur-content').style.display = 'none';

  // Always fetch the current eval questions directly so names match the admin dashboard,
  // regardless of boot order or cached in-memory state.
  let _activeQuestions = window._currentEvalQuestions || _evalQuestions || null;
  try {
    const _eqRes = await fetch(`${API_BASE}/eval-questions`, { headers: _authHeaders() });
    if (_eqRes.ok) {
      const _eqData = await _eqRes.json();
      if (_eqData && _eqData.length) _activeQuestions = _eqData;
    }
  } catch (_eqErr) { /* fall through to cached or default */ }
  const stepNames = (_activeQuestions && _activeQuestions.length)
    ? _activeQuestions.map(q => q.title || q.step_label || q.step_name || q.name || 'Step')
    : ['Claim','Source','Bias','Evidence','Purpose','Audience','Logic','Corroboration'];
  // Sync both the heading count and totalSteps so the summary figure matches
  const stepCountEl = document.getElementById('ur-step-count');
  if (stepCountEl) stepCountEl.textContent = stepNames.length;
  if (stepNames.length !== 8) state.totalSteps = stepNames.length;
  const unsureVals = ['unsure','none_mentioned'];
  const stepAnswerLabels = {
    yes:'Credible', no:'Unreliable/Unknown', unsure:'Not sure', none_mentioned:'No source mentioned',
    1:'Yes — emotional/biased language', 0:'No — neutral language', 2:'Somewhat',
    inform:'Inform', persuade:'Persuade', entertain:'Entertain', sell:'Sell', unknown:'Unknown',
    general:'General public', partisan:'A specific group', professional:'Experts/Professionals',
    hard_to_tell:'Hard to tell', none:'None of the above',
    valid:'Logically sound', fallacy:'Contains fallacies', unclear:'Unclear/hard to tell',
    confirmed:'Confirmed by other sources', contradicted:'Contradicted by others',
    partial:'Partially confirmed', only_one:'Only one source found',
    not_found:'Not found elsewhere', unchecked:'Not checked',
  };
  const lessonMap = {
    'Claim':         { key:'claim_detection',      title:'Claim Detection',       desc:'How to spot verifiable factual claims vs opinions.' },
    'Source':        { key:'source_verification',  title:'Source Verification',   desc:'How to assess credibility of sources.' },
    'Bias':          { key:'bias_detection',        title:'Bias Detection',        desc:'How to spot emotional language and bias.' },
    'Evidence':      { key:'evidence_evaluation',  title:'Evidence Evaluation',   desc:'How to assess the quality of evidence.' },
    'Purpose':       { key:'media_purpose',        title:'Media Purpose',         desc:'Understanding why content is created.' },
    'Audience':      { key:'audience_awareness',   title:'Audience Awareness',    desc:'How targeting shapes content.' },
    'Logic':         { key:'logical_reasoning',    title:'Logical Reasoning',     desc:'Spotting logical fallacies and sound arguments.' },
    'Corroboration': { key:'corroboration',        title:'Cross-Checking Sources',desc:'Why multiple independent sources matter.' },
  };
  const _legacyStateKeys = ['userClaim','sourceRating','biasRating','evidenceRating',
                             'purposeRating','audienceRating','logicRating','corroboration'];
  const stateAnswers = stepNames.map((_, i) => {
    const key = _legacyStateKeys[i] || `customStep_${i}`;
    return (key === 'userClaim') ? (state[key] || null) : state[key];
  });

  const recapEl = document.getElementById('ur-steps-recap');
  const skippedNames = [], unsureNames = [];
  const gridRows = stepNames.map((name, i) => {
    const raw       = stateAnswers[i];
    const hasValue  = raw !== null && raw !== undefined && raw !== '';
    const isSkipped = state.skippedSteps.includes(i) || (!state.answeredSteps.includes(i) && !hasValue);
    const isAnswered= state.answeredSteps.includes(i) && hasValue;
    const isUnsure  = !isSkipped && (unsureVals.includes(String(raw)) || raw === 'unsure');
    const icon      = isSkipped ? '⬜' : isUnsure ? '🤔' : isAnswered ? '✅' : '⬜';
    const valText   = isSkipped
      ? '<span style="color:var(--muted);font-style:italic;">Skipped</span>'
      : hasValue ? (stepAnswerLabels[raw] || raw)
      : '<span style="color:var(--muted);font-style:italic;">Skipped</span>';
    const borderColor = isSkipped ? 'rgba(107,115,148,.25)' : isUnsure ? 'rgba(251,191,36,.3)' : 'rgba(52,211,153,.3)';
    const bg = isSkipped ? 'transparent' : isUnsure ? 'rgba(251,191,36,.04)' : 'rgba(52,211,153,.04)';
    if (isSkipped) skippedNames.push(name);
    if (isUnsure)  unsureNames.push(name);

    // ── Resolve lesson action ───────────────────────────────────────────────
    const q = (_evalQuestions && _evalQuestions[i]) || null;
    const ltype = q?.step_link_type || '';
    const lval  = q?.step_link_value || '';
    // Auto-map lesson from the step name if no explicit link configured
    const _autoLesson = lessonMap[name];
    let lessonAction = '';
    let lessonLabel  = '📖 Lesson';
    if (ltype && lval) {
      if (ltype === 'lesson') {
        lessonAction = `window.open('lessons.html#${lval}','_blank','noopener');`;
        lessonLabel  = '📖 Lesson';
      } else if (ltype === 'url') {
        lessonAction = `window.open(${JSON.stringify(lval)},'_blank','noopener');`;
        lessonLabel  = '↗ Link';
      } else if (ltype === 'quiz') {
        lessonAction = `window.open('lessons.html?quiz=${lval}','_blank','noopener');`;
        lessonLabel  = '❓ Quiz';
      } else if (ltype === 'mindmap') {
        const mm = lval ? `mindmap.html#${lval}` : 'mindmap.html';
        lessonAction = `window.open('${mm}','_blank','noopener');`;
        lessonLabel  = '🗺 Map';
      } else if (ltype === 'dashboard') {
        lessonAction = `window.open('dashboard.html#${lval}','_blank','noopener');`;
        lessonLabel  = '📊 Dashboard';
      }
    } else if (_autoLesson) {
      lessonAction = `window.open('lessons.html#${_autoLesson.key}','_blank','noopener');`;
      lessonLabel  = '📖 Lesson';
    }

    // ── Revisit action — always goes back to that eval step ─────────────────
    const revisitAction = `show('phase-eval');goToStep(${i});`;

    // ── Lesson button HTML (hidden if no lesson configured) ─────────────────
    const lessonBtnHtml = lessonAction
      ? `<button onclick="event.stopPropagation();${lessonAction}" title="${lessonLabel}"
           style="font-size:.65rem;padding:.22rem .5rem;border-radius:5px;border:1px solid var(--border);
                  background:rgba(79,142,247,.08);color:var(--accent);cursor:pointer;white-space:nowrap;
                  font-family:'DM Mono',monospace;letter-spacing:.03em;transition:background .15s;"
           onmouseenter="this.style.background='rgba(79,142,247,.18)';"
           onmouseleave="this.style.background='rgba(79,142,247,.08)';">
           ${lessonLabel}
         </button>`
      : '';

    const revisitBtnHtml = `<button onclick="event.stopPropagation();${revisitAction}" title="Revisit this step"
         style="font-size:.65rem;padding:.22rem .5rem;border-radius:5px;border:1px solid var(--border);
                background:var(--surface);color:var(--muted);cursor:pointer;white-space:nowrap;
                font-family:'DM Mono',monospace;letter-spacing:.03em;transition:background .15s,color .15s;"
         onmouseenter="this.style.background='var(--card)';this.style.color='var(--text)';"
         onmouseleave="this.style.background='var(--surface)';this.style.color='var(--muted)';">
         ↩ Revisit
       </button>`;

    return `<div style="display:flex;flex-direction:column;gap:.35rem;padding:.5rem .7rem;border-radius:7px;
              border:1px solid ${borderColor};background:${bg};min-width:0;
              transition:filter .15s,transform .1s;"
              onmouseenter="this.style.filter='brightness(1.1)';this.style.transform='scale(1.01)';"
              onmouseleave="this.style.filter='';this.style.transform='';">
      <div style="display:flex;align-items:center;gap:.5rem;min-width:0;">
        <span style="font-size:.85rem;flex-shrink:0;">${icon}</span>
        <div style="min-width:0;overflow:hidden;flex:1;">
          <div style="font-family:'DM Mono',monospace;font-size:.6rem;color:var(--muted);letter-spacing:.07em;white-space:nowrap;">${name.toUpperCase()}</div>
          <div style="font-size:.78rem;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${valText}</div>
        </div>
      </div>
      <div style="display:flex;gap:.3rem;justify-content:flex-end;flex-wrap:wrap;">
        ${lessonBtnHtml}
        ${revisitBtnHtml}
      </div>
    </div>`;
  });
  recapEl.style.display = 'grid';
  recapEl.style.gridTemplateColumns = 'repeat(2, 1fr)';
  recapEl.style.gap = '.4rem';
  recapEl.innerHTML = gridRows.join('');

  // Lesson suggestion buttons removed — the step cards above are directly clickable.
  const lessonEl = document.getElementById('ur-lesson-suggestions');
  if (lessonEl) lessonEl.innerHTML = '';

  const totalQ = stepNames.length;
  const stepsCompleted2 = totalQ - state.skippedSteps.filter(i => i < totalQ).length;
  let summary = '';
  if (skippedNames.length === 0 && unsureNames.length === 0) {
    summary = `Great work — you completed all ${stepsCompleted2} evaluation steps. Keep applying these habits every time you encounter content online.`;
  } else {
    summary = '';
  }
  const aiSum = document.getElementById('ur-ai-summary');
  if (aiSum) {
    if (summary) {
      aiSum.textContent = summary;
      aiSum.style.display = '';
    } else {
      aiSum.style.display = 'none';
    }
  }

  // ── Verdict comparison on recap grid (once systemResult is ready) ──────────
  if (state.systemResult?.label) {
    _overlayVerdictOnRecap(state.systemResult, stateAnswers, stepNames);
  }

  document.getElementById('ur-loading').style.display = 'none';
  document.getElementById('ur-content').style.display = 'block';
}

// ── Overlay verdict accuracy signal on recap grid cells ───────────────────────
function _overlayVerdictOnRecap(systemResult, stateAnswers, stepNames) {
  const verdictMap = {
    sourceRating:    { good: ['yes'],                   bad: ['none_mentioned'] },
    biasRating:      { good: ['0'],                     bad: ['1'] },
    evidenceRating:  { good: ['1'],                     bad: ['0'] },
    logicRating:     { good: ['valid'],                 bad: ['fallacy'] },
    corroboration:   { good: ['confirmed','partial'],   bad: ['not_found','only_one'] },
  };
  const stateKeys = ['userClaim','sourceRating','biasRating','evidenceRating',
                     'purposeRating','audienceRating','logicRating','corroboration'];
  const cells = document.querySelectorAll('#ur-steps-recap > div');
  cells.forEach((cell, i) => {
    const key = stateKeys[i];
    const map = verdictMap[key];
    const raw = stateAnswers[i];
    if (!map || !raw) return;
    const isGood = map.good.includes(String(raw));
    const isBad  = map.bad.includes(String(raw));
    if (isGood) {
      cell.style.borderColor = 'rgba(52,211,153,.5)';
      cell.style.background  = 'rgba(52,211,153,.06)';
    } else if (isBad) {
      cell.style.borderColor = 'rgba(248,113,113,.45)';
      cell.style.background  = 'rgba(248,113,113,.05)';
      // Add a small "review" chip
      const chip = document.createElement('span');
      chip.title = 'This answer may need a second look based on the AI verdict';
      chip.style.cssText = 'font-size:.65rem;color:var(--red);margin-left:auto;flex-shrink:0;';
      chip.textContent = '⚑';
      cell.style.display = 'flex';
      cell.appendChild(chip);
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Evidence rendering — v6.0: MBFC badge + retrieval reason chip
// ─────────────────────────────────────────────────────────────────────────────
function _renderEvidenceInto(listId, evidenceArr) {
  const el = document.getElementById(listId);
  if (!el) return;
  if (!evidenceArr || evidenceArr.length === 0) {
    el.innerHTML = '<div style="color:var(--muted);font-size:.85rem;padding:.5rem 0;">No evidence found.</div>';
    return;
  }
  const seen = new Set();
  const deduped = evidenceArr.filter(e => {
    const key = (e.source_url || '') + '::' + (e.source_label || '') + '::' + (e.evidence_text || '').slice(0, 60);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });

  let caveat = '';
  if (deduped.length === 1) {
    caveat = '<div style="margin-bottom:.75rem;padding:.7rem 1rem;background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.25);border-radius:8px;font-size:.82rem;color:var(--yellow);">⚠ Only one related source found — consider checking additional sources before deciding.</div>';
  } else if (deduped.length <= 3) {
    caveat = '<div style="margin-bottom:.75rem;padding:.7rem 1rem;background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.2);border-radius:8px;font-size:.82rem;color:var(--yellow);">⚠ Limited sources found — results may not show the full picture.</div>';
  } else {
    caveat = '';
  }

  // MBFC colour map
  const _mbfcColor = (factual) => {
    if (!factual) return 'var(--muted)';
    const f = factual.toUpperCase();
    if (f === 'HIGH' || f === 'VERY HIGH' || f === 'MOSTLY FACTUAL') return '#34d399';
    if (f === 'MIXED') return '#fbbf24';
    if (f === 'LOW' || f === 'VERY LOW' || f === 'SATIRE' || f === 'CONSPIRACY') return '#f87171';
    return 'var(--muted)';
  };

  el.innerHTML = caveat + deduped.map(e => {
    const title    = e.article_title || e.source_label || 'View Article';
    let   domain   = '';
    try { domain = e.source_url ? new URL(e.source_url).hostname.replace(/^www\./,'') : (e.source_label || ''); } catch(_) { domain = e.source_label || ''; }
    const date     = e.date_published || '';

    // MBFC badge (v6.0)
    const mbfcHtml = (e.mbfc_url)
      ? (() => {
          const color = _mbfcColor(e.mbfc_factual);
          const label = e.mbfc_factual ? `MBFC: ${e.mbfc_factual}` : 'MBFC';
          return `<a href="${e.mbfc_url}" target="_blank" rel="noopener noreferrer"
            title="Open Media Bias/Fact Check entry — ${e.mbfc_bias || 'bias unknown'}"
            style="font-size:.7rem;padding:.15rem .5rem;border-radius:4px;border:1px solid ${color};color:${color};
                   text-decoration:none;font-family:'DM Mono',monospace;white-space:nowrap;flex-shrink:0;">
            ${label} ↗
          </a>`;
        })()
      : '';

    const metaHtml = `<div style="display:flex;align-items:center;gap:.5rem;margin-top:.45rem;flex-wrap:wrap;">
      ${domain ? `<span style="font-size:.72rem;color:var(--muted);background:var(--border);padding:.15rem .55rem;border-radius:4px;font-family:'DM Mono',monospace;">${domain}</span>` : ''}
      ${date   ? `<span style="font-size:.72rem;color:var(--muted);">${date}</span>` : ''}
      ${mbfcHtml}
    </div>`;

    // Retrieval reason chip (v6.0)
    const reasonHtml = e.retrieval_reason
      ? `<div style="margin-top:.4rem;font-size:.72rem;color:var(--muted);font-style:italic;font-family:'DM Mono',monospace;">
           🔎 ${e.retrieval_reason}
         </div>`
      : '';

    const titleEl = e.source_url
      ? `<a href="${e.source_url}" target="_blank" rel="noopener noreferrer" class="ev-title-link">${title}</a>`
      : `<span class="ev-title-plain">${title}</span>`;

    return `<div class="evidence-item">
      ${titleEl}${metaHtml}${reasonHtml}
    </div>`;
  }).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Section 2: Content retrieval
// ─────────────────────────────────────────────────────────────────────────────
function _renderContentRetrieval(analysisResult) {
  _fetchExploration(analysisResult);
}

let _explorationReady = null;
async function _fetchExploration(analysisResult) {
  _explorationReady = [];
  _renderExplorationInline(_explorationReady);
}

function _renderExplorationInline(links) {
  document.getElementById('cr-explore-loading').style.display  = 'none';
  document.getElementById('cr-explore-content').style.display  = 'block';
  const el = document.getElementById('r3-links');
  if (!links || links.length === 0) {
    const exploreWrap = document.getElementById('cr-explore-content')?.closest('[style*="border-radius:14px"]') ||
      document.getElementById('cr-explore-content')?.parentElement;
    if (exploreWrap) exploreWrap.style.display = 'none';
    return;
  }
  el.innerHTML = links.map(l => `
    <a href="${l.url}" target="_blank" class="r3-source-card" style="text-decoration:none;">
      <div class="r3-source-title">${l.title}</div>
      <div class="r3-source-url">${l.url.length > 60 ? l.url.slice(0,60)+'…' : l.url}</div>
      <div class="r3-source-snippet">${l.description}</div>
    </a>`).join('');
}

function showPhaseR2() {}
function showPhaseR3() {}
function showPhaseR4() {}
function showPhaseR5() {}

// ─────────────────────────────────────────────────────────────────────────────
// Section 3: User Input  (prompts + verdict + Reasoning Journal)
// ─────────────────────────────────────────────────────────────────────────────
function _initUserInput(analysisResult) {
  // Bloom's L3–L6 + UNESCO MIL (Access → Evaluate → Create) aligned prompts
  const prompts = [
    {
      id:       'rj-noticed',
      label:    'Analyse',
      bloom:    'Bloom\'s L4',
      mil:      'Evaluate',
      icon:     '🔍',
      question: 'What patterns did you notice across the articles — in how they were written, what sources they cited, or what they left out?',
      placeholder: 'e.g. Most articles used anonymous sources and emotionally charged headlines, but none linked to original data…',
    },
    {
      id:       'rj-uncertain',
      label:    'Evaluate',
      bloom:    'Bloom\'s L5',
      mil:      'Evaluate',
      icon:     '⚖️',
      question: 'How reliable do you think this information is, and what makes you confident or uncertain about that judgement?',
      placeholder: 'e.g. I\'m unsure because two sources contradict each other and I couldn\'t find the original study they reference…',
    },
    {
      id:       'rj-next',
      label:    'Apply',
      bloom:    'Bloom\'s L3',
      mil:      'Access',
      icon:     '🧭',
      question: 'What would you do next — what source, tool, or person would you consult to verify or act on this?',
      placeholder: 'e.g. I\'d check the WHO site directly, search for the original report, or ask a subject expert…',
    },
    {
      id:       'rj-reflect',
      label:    'Create',
      bloom:    'Bloom\'s L6',
      mil:      'Create',
      icon:     '💡',
      question: 'In one sentence, what\'s the most important thing someone should know before sharing or acting on this content?',
      placeholder: 'e.g. Always check who funded the study before sharing health claims on social media…',
    },
  ];

  document.getElementById('r4-prompts').innerHTML = prompts.map((p, i) => `
    <div class="r4-prompt">
      <strong style="display:block;font-size:.72rem;font-family:'DM Mono',monospace;color:var(--accent);letter-spacing:.06em;margin-bottom:.35rem;">
        ${i + 1}. ${p.label.toUpperCase()}
      </strong>
      <p style="font-size:.87rem;color:var(--text);margin-bottom:.5rem;line-height:1.6;">${p.icon} ${p.question}</p>
      <textarea id="${p.id}" rows="2"
        placeholder="${p.placeholder}"
        style="width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;
               padding:.65rem .85rem;color:var(--text);font-family:'DM Sans',sans-serif;font-size:.85rem;
               resize:vertical;line-height:1.6;box-sizing:border-box;margin-top:.2rem;"></textarea>
    </div>
  `).join('');
}

function showPhaseR6() {}

// Alias called by the "Submit My Assessment" button in index.html
function _handleVerdictSubmit() { showPhaseR7(false); }

// ── R7: Reflection Summary + Save ─────────────────────────────────────────────
async function showPhaseR7(skipped) {
  const selected = document.querySelector('input[name="user_position"]:checked');
  if (!selected && !skipped) { showToast('Please select a position first.'); return; }
  reflectionState.position  = selected ? selected.value : 'uncertain';
  reflectionState.reasoning = skipped ? '' : (document.getElementById('r6-reasoning-input')?.value.trim() || '');

  // Collect Reasoning Journal fields (v7.0 — Bloom's + UNESCO MIL aligned)
  const whatNoticed     = document.getElementById('rj-noticed')?.value.trim()   || '';
  const stillUncertain  = document.getElementById('rj-uncertain')?.value.trim() || '';
  const wouldCheckNext  = document.getElementById('rj-next')?.value.trim()      || '';
  const keyTakeaway     = document.getElementById('rj-reflect')?.value.trim()   || '';

  const posMap = {
    supported:   { badge: '✅', label: 'Supported',    color: 'var(--green)' },
    unsupported: { badge: '❌', label: 'Unsupported',  color: 'var(--red)' },
    uncertain:   { badge: '🤔', label: 'Still Unsure', color: 'var(--yellow)' },
  };
  const pos = posMap[reflectionState.position] || posMap['uncertain'];

  document.getElementById('section-user-input').style.display = 'none';
  const r7 = document.getElementById('phase-r7-final');
  r7.style.display = 'block';
  r7.scrollIntoView({ behavior: 'smooth', block: 'start' });

  document.getElementById('r7-position-badge').textContent = pos.badge;
  document.getElementById('r7-position-label').textContent = pos.label;
  document.getElementById('r7-position-label').style.color  = pos.color;
  document.getElementById('r7-reasoning-display').textContent =
    reflectionState.reasoning || '(No reasoning provided)';

  // v6.0: show journal summary before save
  const journalHasContent = whatNoticed || stillUncertain || wouldCheckNext || keyTakeaway;
  if (journalHasContent) {
    const jSummary = document.createElement('div');
    jSummary.style.cssText = 'margin-top:.85rem;padding:.85rem 1rem;background:var(--surface);border-radius:10px;border-left:3px solid var(--accent);font-size:.83rem;line-height:1.7;';
    jSummary.innerHTML = `
      <div style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);letter-spacing:.08em;margin-bottom:.6rem;">YOUR REASONING JOURNAL</div>
      ${whatNoticed    ? `<p style="margin-bottom:.4rem;"><strong>Noticed:</strong> ${whatNoticed}</p>` : ''}
      ${stillUncertain ? `<p style="margin-bottom:.4rem;"><strong>Reliability:</strong> ${stillUncertain}</p>` : ''}
      ${wouldCheckNext ? `<p style="margin-bottom:.4rem;"><strong>Next step:</strong> ${wouldCheckNext}</p>` : ''}
      ${keyTakeaway    ? `<p style="margin-bottom:0;"><strong>Key takeaway:</strong> ${keyTakeaway}</p>` : ''}
    `;
    document.getElementById('r7-card').appendChild(jSummary);
  }

  const saveEl = document.getElementById('r7-save-status');
  saveEl.textContent = '⏳ Saving your reflection…';

  try {
    // v6.0: Save using the new /analyze/reasoning-journal endpoint
    const journalRes = await fetch(`${API_BASE}/analyze/reasoning-journal`, {
      method: 'POST', credentials: 'include', headers: _authHeaders(),
      body: JSON.stringify({
        submission_id:    state.evaluationId,
        user_id:          USER_ID,
        session_token:    getSessionToken(),
        stage:            'post_verdict',
        what_noticed:     whatNoticed     || null,
        still_uncertain:  stillUncertain  || null,
        would_check_next: wouldCheckNext  || null,
        key_takeaway:     keyTakeaway     || null,
        free_reasoning:   reflectionState.reasoning || null,
        verdict_position: reflectionState.position,
      }),
    });

    if (journalRes.ok) {
      const jData = await journalRes.json();
      const bloomLabel = ['','Remember','Understand','Apply','Analyze','Evaluate'][jData.bloom_level || 1] || '';
      const bloomDesc  = {
        Remember:    'You recalled key facts about the content.',
        Understand:  'You showed understanding of what the content means.',
        Apply:       'You applied media literacy criteria to evaluate the content.',
        Analyze:     'You broke down the content\'s structure, sources, and reasoning.',
        Evaluate:    'You made a well-supported judgement and questioned your own assumptions.',
      }[bloomLabel] || '';
      saveEl.innerHTML = `✅ Reflection saved${bloomLabel ? ` — <strong title="${bloomDesc}">${bloomLabel}</strong>${bloomDesc ? ` <span style="font-size:.78rem;color:var(--muted);">(${bloomDesc})</span>` : ''}` : '.'}.`;
    } else {
      saveEl.textContent = '✅ Reflection recorded.';
    }
    reflectionState.saved = true;

    // ── Verdict comparison: user verdict vs AI verdict ──────────────────────
    const sysLabel = state.systemResult?.label;
    const userPos  = reflectionState.position;
    if (sysLabel && userPos) {
      const sysNorm  = sysLabel.toLowerCase();
      const userNorm = userPos.toLowerCase();
      const match =
        (userNorm === 'supported'   && (sysNorm.includes('credible') || sysNorm.includes('support'))) ||
        (userNorm === 'unsupported' && (sysNorm.includes('mislead')  || sysNorm.includes('false') || sysNorm.includes('unsupport'))) ||
        (userNorm === 'uncertain'   && sysNorm.includes('inconclus'));
      const cmpColor = match ? 'var(--green)' : 'rgba(251,191,36,1)';
      const cmpIcon  = match ? '✅' : '⚡';
      const cmpMsg   = match
        ? 'Your verdict aligned with the AI analysis.'
        : `You chose <strong>${userPos}</strong> — the AI found this content <strong>${sysLabel}</strong>. Consider reviewing the evidence section to see why.`;
      const cmpEl = document.createElement('div');
      cmpEl.style.cssText = `margin-top:.9rem;padding:.75rem 1rem;background:rgba(0,0,0,.12);border-left:3px solid ${cmpColor};border-radius:0 8px 8px 0;font-size:.83rem;line-height:1.6;color:var(--text);`;
      cmpEl.innerHTML = `<span style="font-family:'DM Mono',monospace;font-size:.65rem;color:var(--muted);letter-spacing:.08em;display:block;margin-bottom:.35rem;">VERDICT COMPARISON</span>${cmpIcon} ${cmpMsg}`;
      saveEl.insertAdjacentElement('afterend', cmpEl);
    }

    // ── Share / export button ────────────────────────────────────────────────
    const existingShare = document.getElementById('r7-share-btn');
    if (!existingShare) {
      const shareBtn = document.createElement('button');
      shareBtn.id = 'r7-share-btn';
      shareBtn.className = 'btn btn-ghost btn-sm';
      shareBtn.style.cssText = 'margin-top:.9rem;font-size:.8rem;width:100%;';
      shareBtn.textContent = '📋 Copy summary to clipboard';
      shareBtn.onclick = () => {
        const lines = [
          `SocialProof Evaluation Summary`,
          `─────────────────────────────`,
          `My verdict: ${posMap[reflectionState.position]?.label || reflectionState.position}`,
          `AI verdict: ${state.systemResult?.label || '—'}`,
          `Steps completed: ${state.totalSteps - state.skippedSteps.length} / ${state.totalSteps}`,
          reflectionState.reasoning ? `My reasoning: ${reflectionState.reasoning}` : '',
          whatNoticed    ? `Noticed: ${whatNoticed}` : '',
          stillUncertain ? `Still uncertain: ${stillUncertain}` : '',
          wouldCheckNext ? `Would check next: ${wouldCheckNext}` : '',
        ].filter(Boolean).join('\n');
        navigator.clipboard.writeText(lines).then(() => {
          shareBtn.textContent = '✅ Copied!';
          setTimeout(() => { shareBtn.textContent = '📋 Copy summary to clipboard'; }, 2500);
        }).catch(() => { shareBtn.textContent = 'Copy failed — try manually'; });
      };
      document.getElementById('r7-card').appendChild(shareBtn);
    }
  } catch(e) {
    saveEl.textContent = '✅ Reflection noted (will sync when connected).';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Re-evaluation
// ─────────────────────────────────────────────────────────────────────────────
async function submitReeval() {
  const revised = parseInt(document.getElementById('reeval-score')?.value || '50');
  if (!state.userEvaluationId) { showToast('✅ Revised rating noted. (Log in to save it.)'); return; }
  try {
    const res = await fetch(`${API_BASE}/re-evaluation`, {
      method: 'POST', credentials: 'include', headers: _authHeaders(),
      body:   JSON.stringify({ user_evaluation_id: state.userEvaluationId, revised_score: revised,
        revised_label: revised >= 60 ? 'Likely Credible' : revised >= 40 ? 'Uncertain' : 'Likely Misleading',
        revised_confidence: state.confidence, revision_notes: null }),
    });
    if (res.ok) {
      const data  = await res.json();
      const shift = data.score_shift;
      const dir   = shift > 0 ? `↑ +${shift}` : shift < 0 ? `↓ ${shift}` : '→ unchanged';
      showToast(`✅ Revised rating (${revised}/100) saved. Shift: ${dir} pts`);
    } else { showToast(`✅ Revised rating (${revised}/100) recorded.`); }
  } catch (e) { showToast(`✅ Revised rating (${revised}/100) noted.`); }
}

// ─────────────────────────────────────────────────────────────────────────────
// New Evaluation
// ─────────────────────────────────────────────────────────────────────────────
function newEval() {
  Object.assign(state, {
    inputType: 'text', content: '', imageData: null, fileData: null, fileName: null,
    currentStep: 0, skippedSteps: [], answeredSteps: [],
    userClaim: '', sourceRating: null, biasRating: null, evidenceRating: null,
    purposeRating: null, audienceRating: null, logicRating: null, corroboration: null,
    userScore: 50, confidence: 'medium',
    evaluationId: null, userEvaluationId: null, systemResult: null, comparisonResult: null,
    _submissionEvidenceRendered: false, _loaderInterval: null, _pendingUserClaim: null,
    _sourceDiversity: null,
  });
  const ci = document.getElementById('content-input');
  if (ci) ci.value = '';
  const cc = document.getElementById('char-count');
  if (cc) cc.textContent = '0 characters';
  show('phase-input');
}

// ─────────────────────────────────────────────────────────────────────────────
// Lesson read tracking
// ─────────────────────────────────────────────────────────────────────────────
async function markLessonRead(lessonKey) {
  try {
    await fetch(`${API_BASE}/lessons/mark-read`, {
      method: 'POST', credentials: 'include', headers: _authHeaders(),
      body: JSON.stringify({ lesson_key: lessonKey, session_token: getSessionToken(), user_id: USER_ID }),
    });
  } catch (e) { /* non-critical */ }
}

// ─────────────────────────────────────────────────────────────────────────────
// Progress persistence
// ─────────────────────────────────────────────────────────────────────────────
function saveEvalProgress() {
  const snap = {
    phase: document.getElementById('phase-eval')?.style.display !== 'none' ? 'eval' : null,
    content: state.content, inputType: state.inputType,
    currentStep: state.currentStep, skippedSteps: state.skippedSteps,
    answeredSteps: state.answeredSteps, userClaim: state.userClaim,
    sourceRating: state.sourceRating, biasRating: state.biasRating,
    evidenceRating: state.evidenceRating, purposeRating: state.purposeRating,
    audienceRating: state.audienceRating, logicRating: state.logicRating,
    corroboration: state.corroboration,
  };
  sessionStorage.setItem('sp_eval_progress', JSON.stringify(snap));
}

function restoreEvalProgress() {
  const raw = sessionStorage.getItem('sp_eval_progress');
  if (!raw) return;
  try {
    const snap = JSON.parse(raw);
    if (snap.phase !== 'eval' || !snap.content) return;
    // Restore state silently without auto-navigating away from the landing page.
    // Removed show('phase-eval') + goToStep() calls which were bypassing phase-input on every reload.
    Object.assign(state, {
      content: snap.content, inputType: snap.inputType, currentStep: snap.currentStep || 0,
      skippedSteps: snap.skippedSteps || [], answeredSteps: snap.answeredSteps || [],
      userClaim: snap.userClaim || '', sourceRating: snap.sourceRating,
      biasRating: snap.biasRating, evidenceRating: snap.evidenceRating,
      purposeRating: snap.purposeRating, audienceRating: snap.audienceRating,
      logicRating: snap.logicRating, corroboration: snap.corroboration,

    });
    if (snap.inputType === 'text') {
      const ci = document.getElementById('content-input');
      if (ci) { ci.value = snap.content; updateCharCount(); }
    }
    sessionStorage.removeItem('sp_eval_progress');
  } catch(e) {}
}

function restoreStateFromPersist() {}
window.addEventListener('beforeunload', saveEvalProgress);

// ─────────────────────────────────────────────────────────────────────────────
// Restore last result after navigation
// ─────────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  try {
    const saved = localStorage.getItem('sp_last_result');
    if (!saved) return;
    const { result, comparison, state: s, savedAt } = JSON.parse(saved);
    if (!savedAt || Date.now() - savedAt > 2 * 60 * 60 * 1000) return;
    // Exclude totalSteps — it must come from the live API questions, not a stale save
    const { totalSteps: _ignored, ...restState } = s;
    Object.assign(state, restState);
    show('phase-results');
    renderResults(result, comparison);
  } catch(e) {}
});

// ─────────────────────────────────────────────────────────────────────────────
// Claim recommendations (stub)
// ─────────────────────────────────────────────────────────────────────────────
async function _fetchClaimRecommendations(claimText) {
  const banner = document.getElementById('claim-recommendations');
  if (banner) banner.style.display = 'none';
}

let _backgroundEvidenceCache = null;
async function _gatherBackgroundEvidence(content, inputType) {
  _backgroundEvidenceCache = [];
}

// ─────────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────────
async function _boot() {
  await initSessionToken();
  await loadEvalQuestions();
  restoreEvalProgress();
  restoreStateFromPersist();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _boot);
} else {
  _boot();
}
