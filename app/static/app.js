'use strict';
/* HaphazardTasks client. Holds no task data while locked; the server enforces every rule. */

const $ = s => document.querySelector(s);
const SCREENS = ['loading', 'setup', 'unlock', 'newpass', 'app'];
const MIN_PASS = 12;
const POLL_MS = 15000;
const BEAT_MS = 20000;

let csrf = null;
let idleLeft = 0;
let lastBeat = 0;
let pollTimer = null;
let tickTimer = null;
let setupPass = null;

function show(name) {
  SCREENS.forEach(s => { $('#screen-' + s).hidden = s !== name; });
  if (name === 'app') return;
  const first = $('#screen-' + name).querySelector('form:not([hidden]) .field:not([hidden]) input, form:not([hidden]) > input');
  if (first) first.focus();
}

function setErr(sel, msg) {
  const el = $(sel);
  el.textContent = msg || '';
  el.hidden = !msg;
}

let toastTimer;
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: 'same-origin', cache: 'no-store' };
  if (method !== 'GET') {
    opts.headers['Content-Type'] = 'application/json';
    if (csrf) opts.headers['X-CSRF-Token'] = csrf;
    opts.body = JSON.stringify(body || {});
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch {
    return { ok: false, status: 0, data: { error: 'The HaphazardTasks server is not running. Start it from the desktop icon.' } };
  }
  let data = {};
  try { data = await resp.json(); } catch { /* empty body */ }
  if (resp.status === 401 && data.locked && !$('#screen-app').hidden) onLocked('The vault locked. Enter your passphrase to continue.');
  return { ok: resp.ok, status: resp.status, data };
}

/* ---- lock state ---------------------------------------------------- */

function wipe() {
  resetDashboard();
  $('#vault-info').replaceChildren();
  document.querySelectorAll('input').forEach(i => { i.value = ''; });
  csrf = null;
  setupPass = null;
}

function stopTimers() {
  clearInterval(pollTimer);
  clearInterval(tickTimer);
  pollTimer = tickTimer = null;
}

function onLocked(reason) {
  stopTimers();
  wipe();
  $('#unlock-reason').textContent = reason || 'Enter your passphrase to decrypt your tasks.';
  setUnlockMode(false);
  show('unlock');
}

function renderIdle() {
  const m = Math.floor(idleLeft / 60), s = idleLeft % 60;
  $('#idle').textContent = `${m}:${String(s).padStart(2, '0')}`;
  $('#vault-pill').classList.toggle('warn', idleLeft <= 60);
}

async function poll() {
  const { data } = await api('GET', '/api/session');
  if (!data.unlocked) {
    if (!$('#screen-app').hidden) onLocked('The vault locked after a period without activity. Enter your passphrase to continue.');
    return;
  }
  idleLeft = data.idle_seconds_left;
  renderIdle();
}

function startTimers() {
  stopTimers();
  pollTimer = setInterval(poll, POLL_MS);
  tickTimer = setInterval(() => {
    idleLeft = Math.max(0, idleLeft - 1);
    renderIdle();
    if (idleLeft === 0) poll();
    const now = HTRings.dayNum(new Date());
    if (now !== today) { today = now; renderAll(); }  // midnight rolls the day rings
  }, 1000);
}

async function heartbeat() {
  if ($('#screen-app').hidden || Date.now() - lastBeat < BEAT_MS) return;
  lastBeat = Date.now();
  const { ok, data } = await api('POST', '/api/activity');
  if (ok) { idleLeft = data.idle_seconds_left; renderIdle(); }
}
['pointerdown', 'pointermove', 'keydown', 'wheel'].forEach(ev =>
  addEventListener(ev, heartbeat, { passive: true }));
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !$('#screen-app').hidden) poll();
});

/* ---- app ----------------------------------------------------------- */

function fmtBytes(n) {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

async function loadVault() {
  const { ok, data } = await api('GET', '/api/vault');
  if (!ok) return;
  const rows = [
    ['Active tasks', data.tasks],
    ['Archived tasks', data.archived],
    ['Templates', data.templates],
    ['Created', new Date(data.created_at).toLocaleString()],
    ['Vault file', `${fmtBytes(data.file_bytes)}, encrypted`],
    ['Saved versions', `${data.versions} of 5`],
    ['Location', data.location],
  ];
  $('#vault-info').replaceChildren(...rows.flatMap(([k, v]) => {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = String(v);
    return [dt, dd];
  }));
}

async function enterApp() {
  const { data } = await api('GET', '/api/session');
  if (!data.unlocked) return onLocked();
  csrf = data.csrf;
  if (data.must_change_passphrase) return show('newpass');
  idleLeft = data.idle_seconds_left;
  lastBeat = Date.now();
  renderIdle();
  show('app');
  startTimers();
  startDashboard();
}

/* ---- setup --------------------------------------------------------- */

function passProblem(a, b) {
  if (a.length < MIN_PASS) return `Use at least ${MIN_PASS} characters.`;
  if (a !== b) return 'The two passphrases don’t match.';
  return null;
}

$('#setup-pass').addEventListener('input', e => {
  const n = e.target.value.length;
  $('#setup-hint').textContent = n < MIN_PASS
    ? `${n} of ${MIN_PASS} characters minimum. Four or five unrelated words work well.`
    : `${n} characters. Good.`;
});

$('#setup-1').addEventListener('submit', async e => {
  e.preventDefault();
  const a = $('#setup-pass').value, b = $('#setup-pass2').value;
  const problem = passProblem(a, b);
  setErr('#setup-err', problem);
  if (problem) return;
  const { ok, data } = await api('POST', '/api/setup/begin');
  if (!ok) return setErr('#setup-err', data.error || 'Setup failed.');
  setupPass = a;
  $('#rkey').replaceChildren(...data.recovery_key.split('-').map(g => {
    const span = document.createElement('span'); span.textContent = g; return span;
  }));
  $('#setup-1').hidden = true;
  $('#setup-2').hidden = false;
  $('#rkey-check').focus();
});

$('#setup-back').addEventListener('click', () => {
  $('#setup-2').hidden = true;
  $('#setup-1').hidden = false;
  $('#rkey').replaceChildren();
  setupPass = null;
});

$('#rkey-print').addEventListener('click', () => window.print());

$('#setup-2').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('#setup-2-btn');
  btn.disabled = true; btn.textContent = 'Creating vault…';
  const { ok, data } = await api('POST', '/api/setup/finish',
    { passphrase: setupPass, recovery_check: $('#rkey-check').value });
  btn.disabled = false; btn.textContent = 'Create vault';
  if (!ok) return setErr('#setup2-err', data.error || 'Setup failed.');
  setupPass = null;
  $('#rkey').replaceChildren();
  document.querySelectorAll('#screen-setup input').forEach(i => { i.value = ''; });
  csrf = data.csrf;
  await enterApp();
  toast('Vault created and unlocked');
});

/* ---- unlock -------------------------------------------------------- */

let useRecovery = false;
function setUnlockMode(recovery) {
  useRecovery = recovery;
  $('#unlock-pass-row').hidden = recovery;
  $('#unlock-rec-row').hidden = !recovery;
  $('#unlock-toggle').textContent = recovery ? 'Use passphrase instead' : 'Use recovery key instead';
  setErr('#unlock-err', null);
}
$('#unlock-toggle').addEventListener('click', () => {
  setUnlockMode(!useRecovery);
  (useRecovery ? $('#unlock-rec') : $('#unlock-pass')).focus();
});

$('#unlock-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('#unlock-btn');
  const body = useRecovery ? { recovery_key: $('#unlock-rec').value } : { passphrase: $('#unlock-pass').value };
  if (!(body.recovery_key || body.passphrase)) return setErr('#unlock-err', useRecovery ? 'Enter your recovery key.' : 'Enter your passphrase.');
  btn.disabled = true; btn.textContent = 'Unlocking…';
  const { ok, status, data } = await api('POST', '/api/unlock', body);
  btn.disabled = false; btn.textContent = 'Unlock';
  if (!ok) {
    const msg = status === 429 ? `Too many attempts. Try again in ${data.retry_after} seconds.` : (data.error || 'Unlock failed.');
    setErr('#unlock-err', msg);
    return;
  }
  $('#unlock-pass').value = ''; $('#unlock-rec').value = '';
  setErr('#unlock-err', null);
  csrf = data.csrf;
  await enterApp();
});

/* ---- passphrase changes ------------------------------------------- */

$('#newpass-form').addEventListener('submit', async e => {
  e.preventDefault();
  const a = $('#newpass-1').value, b = $('#newpass-2').value;
  const problem = passProblem(a, b);
  setErr('#newpass-err', problem);
  if (problem) return;
  const { ok, data } = await api('POST', '/api/passphrase', { new_passphrase: a });
  if (!ok) return setErr('#newpass-err', data.error || 'Could not save the passphrase.');
  $('#newpass-1').value = ''; $('#newpass-2').value = '';
  await enterApp();
  toast('New passphrase saved');
});

$('#chpass-form').addEventListener('submit', async e => {
  e.preventDefault();
  const a = $('#chpass-new').value, b = $('#chpass-new2').value;
  const problem = passProblem(a, b);
  setErr('#chpass-err', problem);
  if (problem) return;
  const { ok, data } = await api('POST', '/api/passphrase',
    { current_passphrase: $('#chpass-cur').value, new_passphrase: a });
  if (!ok) return setErr('#chpass-err', data.error || 'Could not change the passphrase.');
  document.querySelectorAll('#chpass-form input').forEach(i => { i.value = ''; });
  toast('Passphrase changed');
  loadVault();
});

$('#lock-btn').addEventListener('click', async () => {
  await api('POST', '/api/lock');
  onLocked('Locked.');
});

/* ---- boot ---------------------------------------------------------- */

(async function boot() {
  const { status, data } = await api('GET', '/api/session');
  if (status === 0) {
    $('#screen-loading').querySelector('p').textContent = data.error;
    return;
  }
  if (!data.vault_exists) return show('setup');
  if (!data.unlocked) return onLocked();
  await enterApp();
})();
