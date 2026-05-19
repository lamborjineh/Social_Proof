function switchTab(tab) {
  document.getElementById('form-login').style.display    = tab === 'login'    ? 'block' : 'none';
  document.getElementById('form-register').style.display = tab === 'register' ? 'block' : 'none';
  document.getElementById('form-forgot').style.display   = tab === 'forgot'   ? 'block' : 'none';
  document.getElementById('success-state').classList.remove('show');
  document.getElementById('tab-login').classList.toggle('active', tab === 'login');
  document.getElementById('tab-register').classList.toggle('active', tab === 'register');
}
function showForgot() { switchTab('forgot'); }

const EYE_OPEN  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
const EYE_SHUT  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

function togglePw(id, btn) {
  const el = document.getElementById(id);
  const hidden = el.type === 'password';
  el.type = hidden ? 'text' : 'password';
  btn.innerHTML = hidden ? EYE_SHUT : EYE_OPEN;
  btn.setAttribute('aria-label', hidden ? 'Hide password' : 'Show password');
}

function showErr(id, show) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('show', show);
}
function markInput(id, err) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('error', err);
}

// Username availability debounce
let _usernameTimer = null;
async function _checkUsernameAvailable(username) {
  try {
    const res = await fetch(`/auth/check-username?u=${encodeURIComponent(username)}`);
    if (!res.ok) return; // silent fail — server will catch on submit
    const data = await res.json();
    const taken = !data.available;
    markInput('reg-username', taken);
    const errEl = document.getElementById('err-reg-username');
    if (errEl) {
      errEl.textContent = taken
        ? 'That username is already taken. Please choose another.'
        : 'Username must be 3–50 characters, letters/numbers/underscores only.';
      errEl.classList.toggle('show', taken);
    }
  } catch (_) { /* network error — ignore, server validates on submit */ }
}

function validateUsername() {
  const v = document.getElementById('reg-username').value;
  const ok = /^[a-zA-Z0-9_]{3,50}$/.test(v);
  markInput('reg-username', !ok && v.length > 0);
  showErr('err-reg-username', !ok && v.length > 0);
  // Debounced availability check (only when format is valid)
  clearTimeout(_usernameTimer);
  if (ok) {
    _usernameTimer = setTimeout(() => _checkUsernameAvailable(v), 400);
  }
  return ok;
}
function validateEmail() {
  const v = document.getElementById('reg-email').value;
  // Require: local-part, @, domain of ≥2 chars, dot, TLD of ≥2 chars; max 254 chars total
  const ok = v.length <= 254 && /^[^\s@]{1,64}@[^\s@]{2,}\.[^\s@]{2,}$/.test(v);
  markInput('reg-email', !ok && v.length > 0);
  showErr('err-reg-email', !ok && v.length > 0);
  return ok;
}
function validateConfirm() {
  const pw = document.getElementById('reg-password').value;
  const cf = document.getElementById('reg-confirm').value;
  const ok = pw === cf && cf.length > 0;
  markInput('reg-confirm', !ok && cf.length > 0);
  showErr('err-reg-confirm', !ok && cf.length > 0);
  return ok;
}

// Password strength
function checkStrength() {
  const pw = document.getElementById('reg-password').value;
  const strip = document.getElementById('pw-strength');
  if (!pw) { strip.classList.remove('show'); return; }
  strip.classList.add('show');
  let score = 0;
  if (pw.length >= 8) score++;
  if (/[A-Z]/.test(pw)) score++;
  if (/[0-9]/.test(pw)) score++;
  if (/[^a-zA-Z0-9]/.test(pw)) score++;
  const labels = ['','Weak','Fair','Good','Strong'];
  const cls = score <= 1 ? 'filled-weak' : score <= 2 ? 'filled-fair' : 'filled-strong';
  for (let i = 1; i <= 4; i++) {
    const bar = document.getElementById('bar'+i);
    bar.className = 'pw-bar' + (i <= score ? ' ' + cls : '');
  }
  document.getElementById('pw-label').textContent = labels[score] || 'Weak';
}

// Submissions
async function submitLogin() {
  const id  = document.getElementById('login-identifier').value.trim();
  const pw  = document.getElementById('login-password').value;
  let valid = true;
  if (!id)  { markInput('login-identifier', true); showErr('err-login-identifier', true); valid = false; }
  else      { markInput('login-identifier', false); showErr('err-login-identifier', false); }
  if (!pw)  { markInput('login-password', true); showErr('err-login-password', true); valid = false; }
  else      { markInput('login-password', false); showErr('err-login-password', false); }
  if (!valid) {
    document.getElementById('form-login').classList.add('shake');
    setTimeout(() => document.getElementById('form-login').classList.remove('shake'), 300);
    return;
  }

  // Disable button while request is in flight
  const btn = document.getElementById('btn-login');
  if (btn) { btn.disabled = true; btn.textContent = 'Signing in…'; }

  try {
    const res = await fetch('/auth/cookie-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',   // C-8: required for HttpOnly cookie to be set
      body: JSON.stringify({ identifier: id, password: pw }),
    });

    if (!res.ok) {
      // 401 = wrong credentials, anything else = server error
      const err = await res.json().catch(() => ({}));
      const msg = err.detail || (res.status === 401 ? 'Incorrect email/username or password.'
                                                     : `Server error (${res.status}). Is the backend running?`);
      // Show inline error under password field
      let errEl = document.getElementById('err-login-server');
      if (!errEl) {
        errEl = document.createElement('div');
        errEl.id = 'err-login-server';
        errEl.className = 'field-err';
        const loginBtn = document.getElementById('btn-login');
        loginBtn.insertAdjacentElement('beforebegin', errEl);
      }
      errEl.textContent = msg;
      errEl.classList.add('show');
      document.getElementById('form-login').classList.add('shake');
      setTimeout(() => document.getElementById('form-login').classList.remove('shake'), 300);
      return;
    }

    const data = await res.json();

    // Persist token + user info so every page can read them
    // C-8: Token is now stored in an HttpOnly cookie set by the server.
    // Only non-sensitive display data is kept in localStorage.
    localStorage.setItem('sp_role',     data.role);
    localStorage.setItem('sp_user_id',  data.user_id);
    localStorage.setItem('sp_username', data.username);

    // Show success banner
    document.getElementById('form-login').style.display = 'none';
    const ss = document.getElementById('success-state');
    document.getElementById('success-title').textContent = 'Welcome back!';
    document.getElementById('success-sub').textContent   =
      `Signed in as ${data.username}. Redirecting…`;
    ss.classList.add('show');

    const dest = 'dashboard.html';
    setTimeout(() => { window.location.href = dest; }, 400);

  } catch (e) {
    // Network-level failure — backend probably not running
    let errEl = document.getElementById('err-login-server');
    if (!errEl) {
      errEl = document.createElement('div');
      errEl.id = 'err-login-server';
      errEl.className = 'field-err';
      const loginBtn = document.getElementById('btn-login');
      loginBtn.insertAdjacentElement('beforebegin', errEl);
    }
    errEl.textContent = 'Cannot reach the server. Make sure the backend is running.';
    errEl.classList.add('show');
  } finally {
    // Only re-enable if we didn't successfully log in (success redirects away)
    const ss = document.getElementById('success-state');
    if (!ss || !ss.classList.contains('show')) {
      if (btn) { btn.disabled = false; btn.textContent = 'Log In →'; }
    }
  }
}

async function submitRegister() {
  let valid = true;
  if (!validateUsername())  valid = false;
  if (!validateEmail())     valid = false;
  const pw = document.getElementById('reg-password').value;
  // bcrypt silently truncates at 72 bytes — enforce the cap client-side too
  const pwLen = new TextEncoder().encode(pw).length;
  const pwOk = pw.length >= 8 && pwLen <= 72 && /[A-Z]/.test(pw) && /[0-9]/.test(pw) && /[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]/.test(pw);
  if (!pwOk) {
    const errEl = document.getElementById('err-reg-password');
    if (errEl) errEl.textContent = pwLen > 72
      ? 'Password must be 72 characters or fewer.'
      : 'Password must be at least 8 characters with uppercase, number & special character.';
    markInput('reg-password', true); showErr('err-reg-password', true); valid = false;
  } else {
    markInput('reg-password', false); showErr('err-reg-password', false);
  }
  if (!validateConfirm()) valid = false;

  // Research consent is required
  const consentChecked = document.getElementById('research-consent')?.checked;
  if (!consentChecked) {
    showErr('err-research-consent', true);
    valid = false;
  } else {
    showErr('err-research-consent', false);
  }

  if (!valid) {
    document.getElementById('form-register').classList.add('shake');
    setTimeout(() => document.getElementById('form-register').classList.remove('shake'), 300);
    return;
  }

  const btn = document.getElementById('btn-register');
  if (btn) { btn.disabled = true; btn.textContent = 'Creating account…'; }

  try {
    const res = await fetch('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username:          document.getElementById('reg-username').value.trim(),
        email:             document.getElementById('reg-email').value.trim(),
        password:          pw,
        research_consent:  true,   // checkbox already verified above
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      // Pydantic 422 returns { detail: [{msg, loc}] }, plain errors return { detail: "string" }
      let msg;
      if (Array.isArray(err.detail)) {
        msg = err.detail.map(e => e.msg.replace('Value error, ', '')).join(' ');
      } else {
        msg = err.detail || `Registration failed (${res.status}).`;
      }
      let errEl = document.getElementById('err-reg-server');
      if (!errEl) {
        errEl = document.createElement('div');
        errEl.id = 'err-reg-server';
        errEl.className = 'form-error show';
        errEl.style.cssText = 'display:block;margin-bottom:.6rem;';
        document.getElementById('btn-register').insertAdjacentElement('beforebegin', errEl);
      }
      errEl.textContent = msg;
      errEl.style.display = 'block';
      document.getElementById('form-register').classList.add('shake');
      setTimeout(() => document.getElementById('form-register').classList.remove('shake'), 300);
      return;
    }

    // After registering, do a cookie-login so the HttpOnly cookie is set
    const regData = await res.json();
    const loginRes = await fetch('/auth/cookie-login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        identifier: regData.username,
        password: pw,
      }),
    });
    const data = loginRes.ok ? await loginRes.json() : regData;
    localStorage.setItem('sp_role',     data.role);
    localStorage.setItem('sp_user_id',  data.user_id);
    localStorage.setItem('sp_username', data.username);

    document.getElementById('form-register').style.display = 'none';
    const ss = document.getElementById('success-state');
    document.getElementById('success-title').textContent = 'Account created!';
    document.getElementById('success-sub').textContent =
      `Welcome, ${data.username}! Redirecting to your dashboard…`;
    ss.classList.add('show');
    setTimeout(() => { window.location.href = 'dashboard.html'; }, 1800);

  } catch (e) {
    let errEl = document.getElementById('err-reg-server');
    if (!errEl) {
      errEl = document.createElement('div');
      errEl.id = 'err-reg-server';
      errEl.className = 'field-err show';
      errEl.style.marginTop = '-.5rem';
      document.getElementById('btn-register').insertAdjacentElement('beforebegin', errEl);
    }
    errEl.textContent = 'Cannot reach the server. Make sure the backend is running on port 8000.';
    errEl.classList.add('show');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Create Account →'; }
  }
}

function submitForgot() {
  const email = document.getElementById('forgot-email').value;
  const ok = /^[^\s@]{1,64}@[^\s@]{2,}\.[^\s@]{2,}$/.test(email);
  markInput('forgot-email', !ok);
  showErr('err-forgot-email', !ok);
  if (!ok) return;
  document.getElementById('form-forgot').style.display = 'none';
  const ss = document.getElementById('success-state');
  document.getElementById('success-title').textContent = 'Reset link sent!';
  document.getElementById('success-sub').textContent = `If an account exists for ${email}, a password reset link has been sent.`;
  ss.classList.add('show');
}


// ── Auth state: show "Logout" if already logged in ───────────────────────────
(function checkAuth() {
  const username  = localStorage.getItem('sp_username');
  const role      = localStorage.getItem('sp_role');
  const loginLink  = document.getElementById('sidebar-login-link');
  const loginLinkM = document.getElementById('sidebar-login-link-m');

  if (username) {
    const logoutHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>Logout`;
    const doLogout = async (e) => { e.preventDefault(); await fetch('/auth/cookie-logout', { method: 'POST', credentials: 'include' }).catch(() => {}); localStorage.clear(); window.location.href = 'index.html'; };
    if (loginLink)  { loginLink.innerHTML  = logoutHTML; loginLink.onclick  = doLogout; loginLink.href  = '#'; }
    if (loginLinkM) { loginLinkM.innerHTML = logoutHTML; loginLinkM.onclick = doLogout; loginLinkM.href = '#'; }
  }

})();
