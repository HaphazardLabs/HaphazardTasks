'use strict';
/* Dashboard: overview, task grid, archive, focus panel, status menu, task editor, settings.
   Loaded before app.js, which provides api(), toast(), setErr() and loadVault(); those are
   only called after boot. app.js calls startDashboard() on unlock and resetDashboard() on lock. */

const byId = id => document.getElementById(id);
const esc = HTRings.esc;
const STATUS_ORDER = ['pending', 'underway', 'done', 'blocked', 'deferred'];
const PRI_RANK = { High: 0, Medium: 1, Low: 2 };
const MAX_PHASES = 4;
const OVERVIEW_RINGS = 8;

let tasks = [];        // active
let archived = [];
let view = 'active';
let sortBy = 'due';
let query = '';
let tagFilter = '';
let focusId = null;
let editorReturnsToFocus = null;
let editorReturnsToTemplates = false;
let templates = [];
let today = HTRings.dayNum(new Date());

/* ---- theme (needed on the lock screen too, so it lives in localStorage as well) ---- */

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme;
  else delete root.dataset.theme;
  try { localStorage.setItem('ht-theme', theme || 'system'); } catch { /* storage unavailable */ }
}
try { applyTheme(localStorage.getItem('ht-theme') || 'system'); } catch { /* storage unavailable */ }

/* ---- lookups ---------------------------------------------------------- */

const findTask = id => tasks.find(t => t.id === id) || archived.find(t => t.id === id);
const isArchived = t => Boolean(t && t.archived_at);
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function upsert(t) {
  tasks = tasks.filter(x => x.id !== t.id);
  archived = archived.filter(x => x.id !== t.id);
  (t.archived_at ? archived : tasks).push(t);
}

function removeTask(id) {
  tasks = tasks.filter(x => x.id !== id);
  archived = archived.filter(x => x.id !== id);
}

/* ---- small renderers --------------------------------------------------- */

function statusIcon(s, size = 13) {
  const body = {
    done: '<circle cx="7" cy="7" r="6" fill="currentColor"/><path class="ico-done-check" d="M4 7.2l2 2 4-4.2" stroke-width="1.7" fill="none"/>',
    underway: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M7 1.5a5.5 5.5 0 0 1 0 11z" fill="currentColor"/>',
    pending: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/>',
    blocked: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3.2 10.8l7.6-7.6" stroke="currentColor" stroke-width="1.5"/>',
    deferred: '<circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.2 1.8"/>',
  }[s];
  return `<svg width="${size}" height="${size}" viewBox="0 0 14 14" aria-hidden="true">${body}</svg>`;
}

function metaHTML(t) {
  const pr = HTRings.progress(t);
  const dm = HTRings.dayModel(t.start_date, t.due_date, today, pr.p >= 1);
  const chips = [`<span class="chip pri-${esc(t.priority)}">${esc(t.priority)}</span>`];
  if (t.archived_at) chips.push(`<span class="chip">Archived ${HTRings.fmt(t.archived_at.slice(0, 10))}</span>`);
  if (dm.late) chips.push(`<span class="chip late">Overdue ${plural(-dm.daysLeft, 'day')}</span>`);
  else if (pr.p >= 1) chips.push('<span class="chip complete">Complete</span>');
  else if (dm.daysLeft === 0) chips.push('<span class="chip">Due today</span>');
  else chips.push(`<span class="chip">Due ${HTRings.fmt(t.due_date)} · ${plural(dm.daysLeft, 'day')}</span>`);
  if (dm.startsIn) chips.push(`<span class="chip mono">Starts in ${plural(dm.startsIn, 'day')}</span>`);
  else if (dm.late) chips.push(`<span class="chip mono">Day ${dm.day} · due day ${dm.total}</span>`);
  else if (dm.day <= dm.total) chips.push(`<span class="chip mono">Day ${dm.day} of ${dm.total}</span>`);
  if (t.recurrence_label) chips.push(`<span class="chip recur">↻ ${esc(t.recurrence_label.replace('Repeats ', ''))}</span>`);
  t.tags.forEach(tag => chips.push(`<button type="button" class="chip tag" data-tag="${esc(tag)}" title="Show only “${esc(tag)}”">${esc(tag)}</button>`));
  return `<div class="meta">${chips.join('')}</div>`;
}

function checklistHTML(t) {
  const n = t.phases.length, live = !isArchived(t);
  return t.phases.map((ph, p) => {
    const counted = ph.items.filter(i => i.status !== 'deferred');
    const done = counted.filter(i => i.status === 'done').length;
    return `<div class="phase"><div class="phase-h">${HTRings.ringKeyIcon(n, p)}<span>${esc(ph.name)}</span><span class="cnt">${done}/${counted.length}</span></div>
      <ul class="items">${ph.items.map((it, i) => {
        const chip = live
          ? `<button type="button" class="st c-${it.status}" aria-haspopup="menu" aria-label="${esc(it.text)}: ${HTRings.LABEL[it.status]}. Change status">${statusIcon(it.status)}${HTRings.LABEL[it.status]}</button>`
          : `<span class="st c-${it.status}">${statusIcon(it.status)}${HTRings.LABEL[it.status]}</span>`;
        return `<li class="item is-${it.status}" data-p="${p}" data-i="${i}">${chip}<span class="txt">${esc(it.text)}</span></li>`;
      }).join('')}</ul></div>`;
  }).join('');
}

function cardHTML(t) {
  const actions = isArchived(t)
    ? `<div class="card-actions"><button type="button" class="btn" data-restore="${esc(t.id)}">Restore</button></div>` : '';
  return `<article class="card task-view${isArchived(t) ? ' archived' : ''}" data-id="${esc(t.id)}">
    <div class="card-ring">${HTRings.ringSVG(t, today)}</div>
    <div class="card-body"><div class="card-head"><h3><button type="button" class="title-btn" data-open="${esc(t.id)}">${esc(t.title)}</button></h3>${metaHTML(t)}</div>
    ${checklistHTML(t)}${actions}</div></article>`;
}

/* ---- filtering and sorting ---------------------------------------------- */

function haystack(t) {
  return [t.title, t.notes_md, ...t.tags, ...t.phases.flatMap(p => [p.name, ...p.items.map(i => i.text)])]
    .join('\n').toLowerCase();
}

function visibleTasks() {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const list = (view === 'active' ? tasks : archived).filter(t =>
    (!tagFilter || t.tags.includes(tagFilter)) && words.every(w => haystack(t).includes(w)));
  const due = (a, b) => a.due_date.localeCompare(b.due_date) || a.title.localeCompare(b.title);
  if (sortBy === 'priority') list.sort((a, b) => PRI_RANK[a.priority] - PRI_RANK[b.priority] || due(a, b));
  else if (sortBy === 'pct') list.sort((a, b) => HTRings.progress(a).p - HTRings.progress(b).p || due(a, b));
  else list.sort(due);
  return list;
}

/* ---- rendering ---------------------------------------------------------- */

function renderGrid() {
  const list = visibleTasks();
  byId('grid').innerHTML = list.map(cardHTML).join('');
  byId('empty').hidden = !(view === 'active' && tasks.length === 0);
  const source = view === 'active' ? tasks : archived;
  const noMatch = byId('no-match');
  noMatch.hidden = !(source.length && !list.length) && !(view === 'archive' && !archived.length);
  noMatch.textContent = view === 'archive' && !archived.length
    ? 'Nothing archived yet. Archive a finished task from its detail panel.'
    : 'No tasks match the search or tag filter.';
  byId('count-active').textContent = tasks.length;
  byId('count-archive').textContent = archived.length;
}

function renderTagOptions() {
  const all = [...new Set([...tasks, ...archived].flatMap(t => t.tags))].sort((a, b) => a.localeCompare(b));
  if (tagFilter && !all.includes(tagFilter)) tagFilter = '';
  const sel = byId('tag-sel');
  sel.replaceChildren(new Option('All tags', ''), ...all.map(t => new Option(t, t)));
  sel.value = tagFilter;
  byId('tag-list').replaceChildren(...all.map(t => new Option(t)));
}

function renderOverview() {
  const box = byId('overview');
  box.hidden = view !== 'active' || tasks.length === 0;
  if (box.hidden) return;
  const list = [...tasks].sort((a, b) => a.due_date.localeCompare(b.due_date) || a.title.localeCompare(b.title));
  const shown = list.slice(0, OVERVIEW_RINGS);
  byId('ov-ring').innerHTML = HTRings.overviewSVG(shown, today);
  const keys = shown.map((t, k) => {
    const h = HTRings.health(t, today);
    return `<li><button type="button" data-open="${esc(t.id)}">${HTRings.ringKeyIcon(shown.length, k)}<span class="nm">${esc(t.title)}</span>` +
      `<span class="health h-${h}">${HTRings.HEALTH_LABEL[h]}</span><span class="pc">${Math.round(HTRings.progress(t).p * 100)}%</span></button></li>`;
  });
  if (list.length > shown.length) keys.push(`<li class="more">+${list.length - shown.length} more due later, not drawn in the ring</li>`);
  byId('ov-key').innerHTML = keys.join('');
  const items = tasks.flatMap(HTRings.allItems);
  const count = s => items.filter(i => i.status === s).length;
  const due7 = tasks.filter(t => {
    const d = HTRings.dayModel(t.start_date, t.due_date, today, false).daysLeft;
    return d >= 0 && d <= 7 && HTRings.progress(t).p < 1;
  }).length;
  const late = tasks.filter(t => HTRings.health(t, today) === 'late').length;
  byId('stats').innerHTML = [
    ['', items.filter(i => i.status !== 'done' && i.status !== 'deferred').length, 'Open items'],
    ['st-blocked', count('blocked'), 'Blocked items'],
    ['st-due', due7, 'Due in 7 days'],
    ['st-late', late, 'Overdue tasks'],
  ].map(([c, v, l]) => `<div class="stat ${c}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
}

function renderLegend() {
  byId('legend').innerHTML =
    ['done', 'underway', 'pending', 'blocked', 'deferred'].map(s =>
      `<span class="c-${s}">${statusIcon(s)}<span class="muted">${HTRings.LABEL[s]}</span></span>`).join('') +
    '<span class="muted">Day ring:</span>' +
    [['past', 'Days gone'], ['today', 'Today'], ['ahead', 'Days left'], ['late', 'Overdue']].map(([c, l]) =>
      `<span><i class="sw sw-${c}"></i>${l}</span>`).join('');
}

function renderAll() {
  renderTagOptions();
  renderOverview();
  renderGrid();
  if (focusId) renderFocus();
}

/** Replace one card in place so the grid doesn't reshuffle under the pointer. */
function refreshTask(t) {
  upsert(t);
  const el = document.querySelector(`.card[data-id="${CSS.escape(t.id)}"]`);
  if (el) el.outerHTML = cardHTML(t); else renderGrid();
  renderOverview();
  if (focusId === t.id) renderFocus();
}

async function loadTasks() {
  const [a, b] = await Promise.all([api('GET', '/api/tasks'), api('GET', '/api/tasks?archived=1')]);
  if (!a.ok || !b.ok) return;
  tasks = a.data;
  archived = b.data;
  today = HTRings.dayNum(new Date());
  renderAll();
}

async function startDashboard() {
  const { ok, data } = await api('GET', '/api/settings');
  if (ok) applyTheme(data.theme);
  await loadTasks();
}

function resetDashboard() {
  closeMenu();
  closeDialogs();
  tasks = [];
  archived = [];
  focusId = null;
  query = '';
  tagFilter = '';
  byId('grid').replaceChildren();
  ['ov-ring', 'ov-key', 'stats', 'focus-ring', 'focus-title', 'focus-meta', 'focus-notes', 'focus-list', 'focus-history', 'tag-list']
    .forEach(id => byId(id).replaceChildren());
  byId('overview').hidden = true;
}

/* ---- view switch, search, tags ------------------------------------------ */

function setView(v) {
  view = v;
  byId('view-active').setAttribute('aria-pressed', v === 'active');
  byId('view-archive').setAttribute('aria-pressed', v === 'archive');
  renderOverview();
  renderGrid();
}
byId('view-active').addEventListener('click', () => setView('active'));
byId('view-archive').addEventListener('click', () => setView('archive'));
byId('search').addEventListener('input', e => { query = e.target.value; renderGrid(); });
byId('tag-sel').addEventListener('change', e => { tagFilter = e.target.value; renderGrid(); });
byId('sort-sel').addEventListener('change', e => { sortBy = e.target.value; renderGrid(); });

/* ---- safe Markdown for notes (builds DOM nodes; never parses HTML) ------- */

function inlineMarkdown(text, parent) {
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const tok = m[0];
    const el = document.createElement(tok.startsWith('`') ? 'code' : tok.startsWith('**') ? 'strong' : 'em');
    el.textContent = tok.startsWith('**') ? tok.slice(2, -2) : tok.slice(1, -1);
    parent.append(el);
    last = m.index + tok.length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

function renderMarkdown(src) {
  const frag = document.createDocumentFragment();
  let list = null, para = null;
  const end = () => { list = null; para = null; };
  for (const raw of src.split('\n')) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { end(); continue; }
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      end();
      const h = document.createElement(m[1].length === 1 ? 'h3' : 'h4');
      inlineMarkdown(m[2], h);
      frag.append(h);
    } else if ((m = line.match(/^\s*([-*]|\d+[.)])\s+(.*)$/))) {
      const tag = /\d/.test(m[1]) ? 'OL' : 'UL';
      if (!list || list.tagName !== tag) { list = document.createElement(tag); frag.append(list); }
      para = null;
      const li = document.createElement('li');
      inlineMarkdown(m[2], li);
      list.append(li);
    } else {
      list = null;
      if (!para) { para = document.createElement('p'); frag.append(para); } else para.append(document.createElement('br'));
      inlineMarkdown(line, para);
    }
  }
  return frag;
}

/* ---- focus panel ---------------------------------------------------------- */

function historyLine(e) {
  const L = HTRings.LABEL;
  switch (e.kind) {
    case 'status': return `${e.item || 'Deleted item'}: ${L[e.from] || e.from} → ${L[e.to] || e.to}`;
    case 'created': return 'Task created';
    case 'edited': return 'Task edited';
    case 'archived': return 'Archived';
    case 'restored': return 'Restored from the archive';
    case 'cycle': return `Cycle ${e.from === 'complete' ? 'completed' : 'closed incomplete'} · next due ${HTRings.fmt(e.to)}`;
    default: return e.kind;
  }
}

async function loadHistory(id) {
  const { ok, data } = await api('GET', `/api/tasks/${encodeURIComponent(id)}/history`);
  if (!ok || focusId !== id) return;
  const list = byId('focus-history');
  if (!data.length) {
    const li = document.createElement('li'); li.className = 'empty-h'; li.textContent = 'No changes yet.';
    return list.replaceChildren(li);
  }
  list.replaceChildren(...data.map(e => {
    const li = document.createElement('li');
    const time = document.createElement('time');
    time.dateTime = e.at;
    time.textContent = new Date(e.at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    li.append(time, historyLine(e));
    return li;
  }));
}

async function loadCycles(id) {
  const t = findTask(id);
  const wrap = byId('focus-cycles-wrap');
  const { ok, data } = await api('GET', `/api/tasks/${encodeURIComponent(id)}/cycles`);
  if (!ok || focusId !== id) return;
  wrap.hidden = !(data.length || (t && t.recurrence));
  if (!data.length) {
    const li = document.createElement('li'); li.className = 'muted'; li.textContent = 'The first cycle is still running.';
    return byId('focus-cycles').replaceChildren(li);
  }
  byId('focus-cycles').replaceChildren(...data.map(c => {
    const li = document.createElement('li');
    const dates = document.createElement('span'); dates.className = 'dates';
    dates.textContent = `${HTRings.fmt(c.start_date)} – ${HTRings.fmt(c.due_date)}`;
    const badge = document.createElement('span');
    badge.className = `health ${c.outcome === 'complete' ? 'h-complete' : 'h-late'}`;
    badge.textContent = c.outcome === 'complete' ? 'Complete' : 'Incomplete';
    const cnt = document.createElement('span'); cnt.className = 'cnt'; cnt.textContent = `${c.done} of ${c.total}`;
    li.append(dates, badge, cnt);
    return li;
  }));
}

function renderFocus() {
  const t = findTask(focusId);
  if (!t) return closeDialogs();
  const archivedTask = isArchived(t);
  byId('focus-view').dataset.id = t.id;
  byId('focus-kind').textContent = archivedTask ? 'Archived task'
    : `${plural(t.phases.length, 'phase')} · ${plural(t.phases.length, 'ring')}`;
  byId('focus-ring').innerHTML = HTRings.ringSVG(t, today);
  byId('focus-title').textContent = t.title;
  byId('focus-meta').innerHTML = metaHTML(t);
  byId('focus-notes').replaceChildren(renderMarkdown(t.notes_md || ''));
  byId('focus-list').innerHTML = checklistHTML(t);
  byId('focus-archive').textContent = archivedTask ? 'Restore' : 'Archive';
  byId('focus-edit').hidden = archivedTask;
}

function openFocus(id, opener) {
  focusId = id;
  byId('focus-confirm').hidden = true;
  byId('focus-history').replaceChildren();
  renderFocus();
  openDialog('focus', opener);
  byId('focus-close').focus();
  loadHistory(id);
  loadCycles(id);
}

byId('focus-close').addEventListener('click', closeDialogs);
byId('focus-edit').addEventListener('click', () => {
  const t = findTask(focusId);
  editorReturnsToFocus = t.id;
  const opener = dialogOpener;
  closeDialogs({ keepFocusId: true, restoreFocus: false });
  openEditor(t, opener);
});
byId('focus-archive').addEventListener('click', async () => {
  const t = findTask(focusId);
  const action = isArchived(t) ? 'restore' : 'archive';
  const { ok, data } = await api('POST', `/api/tasks/${encodeURIComponent(t.id)}/${action}`);
  if (!ok) return toast(data.error || `Could not ${action} the task.`);
  upsert(data);
  closeDialogs();
  renderAll();
  toast(action === 'archive' ? `Archived “${data.title}”` : `Restored “${data.title}”`);
});
byId('focus-template').addEventListener('click', async () => {
  const { ok, data } = await api('POST', '/api/templates', { from_task: focusId });
  if (!ok) return toast(data.error || 'Could not save the template.');
  toast(`Saved “${data.name}” as a template`);
});
byId('focus-delete').addEventListener('click', () => { byId('focus-confirm').hidden = false; byId('focus-confirm-no').focus(); });
byId('focus-confirm-no').addEventListener('click', () => { byId('focus-confirm').hidden = true; byId('focus-delete').focus(); });
byId('focus-confirm-yes').addEventListener('click', () => deleteTask(focusId));

async function deleteTask(id) {
  const t = findTask(id);
  const { ok, data } = await api('DELETE', `/api/tasks/${encodeURIComponent(id)}`);
  if (!ok) return toast(data.error || 'Could not delete the task.');
  removeTask(id);
  dialogOpener = byId('new-btn');
  closeDialogs();
  renderAll();
  toast(`Deleted “${t.title}”`);
}

/* ---- status menu ---------------------------------------------------------- */

const menu = byId('menu');
let menuCtx = null;

function closeMenu() {
  if (menu.hidden) return;
  menu.hidden = true;
  const back = menuCtx && menuCtx.anchor;
  menuCtx = null;
  if (back && document.contains(back)) back.focus();
}

function placeMenu(anchor) {
  menu.hidden = false;
  const r = anchor.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
  let y = r.bottom + 4;
  if (y + h > innerHeight - 8) y = Math.max(8, r.top - h - 4);
  menu.style.top = y + 'px';
  menu.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
}

async function openNewMenu(anchor) {
  const { ok, data } = await api('GET', '/api/templates');
  templates = ok ? data : [];
  menuCtx = { kind: 'new', anchor };
  menu.innerHTML = '<button type="button" role="menuitem" data-new="blank">Blank task <span class="cur">N</span></button>' +
    '<div class="menu-sep"></div><div class="mh">From template</div>' +
    (templates.length
      ? templates.map(t => `<button type="button" role="menuitem" data-new-tpl="${esc(t.id)}">${esc(t.name)}</button>`).join('')
      : '<div class="empty-note">No templates yet. Open a task and choose Save as template.</div>') +
    '<div class="menu-sep"></div><button type="button" role="menuitem" data-manage-templates="1">Manage templates…</button>';
  placeMenu(anchor);
  menu.querySelector('button').focus();
}

function openStatusMenu(taskId, p, i, anchor) {
  const t = tasks.find(x => x.id === taskId);
  if (!t) return;
  const item = t.phases[p].items[i];
  const where = anchor.closest('.task-view').id || 'grid';
  menuCtx = { kind: 'status', taskId, itemId: item.id, p, i, where, anchor };
  menu.innerHTML = '<div class="mh">Set status</div>' + STATUS_ORDER.map((s, k) =>
    `<button type="button" role="menuitem" data-set="${s}" class="c-${s}">${statusIcon(s)}<span class="lbl">${HTRings.LABEL[s]}</span><span class="cur">${s === item.status ? 'current' : k + 1}</span></button>`).join('');
  placeMenu(anchor);
  (menu.querySelector(`[data-set="${item.status}"]`) || menu.querySelector('button')).focus();
}

/** Change one item's status. `where` names the view that should keep keyboard focus. */
async function setStatus(taskId, p, i, status, where) {
  const t = tasks.find(x => x.id === taskId);
  if (!t) return;
  const item = t.phases[p].items[i];
  if (item.status === status) return;
  const { ok, data } = await api('PATCH', `/api/items/${encodeURIComponent(item.id)}`, { status });
  if (!ok) return toast(data.error || 'Could not change the status.');
  refreshTask(data);
  if (focusId === taskId) { loadHistory(taskId); loadCycles(taskId); }
  if (data.cycle_closed) toast(`Cycle complete. The checklist has reset; next due ${HTRings.fmt(data.due_date)}.`);
  else toast(`${item.text} → ${HTRings.LABEL[status]}`);
  const scope = where === 'focus-view' ? byId('focus-view') : document.querySelector(`.card[data-id="${CSS.escape(taskId)}"]`);
  const btn = scope && scope.querySelector(`.item[data-p="${p}"][data-i="${i}"] .st`);
  if (btn) btn.focus();
}

menu.addEventListener('keydown', e => {
  const items = [...menu.querySelectorAll('button')];
  const i = items.indexOf(document.activeElement);
  if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  else if (/^[1-5]$/.test(e.key) && menuCtx && menuCtx.kind === 'status') { e.preventDefault(); items[+e.key - 1].click(); }
  else if (e.key === 'Tab') closeMenu();
});

/* ---- clicks anywhere in the dashboard -------------------------------------- */

document.addEventListener('click', e => {
  const tgt = e.target;
  if (!menu.hidden && !menu.contains(tgt) && !tgt.closest('.st, .seg, #new-btn')) closeMenu();

  if (tgt.closest('#new-btn')) {
    if (!menu.hidden && menuCtx && menuCtx.kind === 'new') return closeMenu();
    return openNewMenu(byId('new-btn'));
  }
  if (tgt.closest('[data-new]')) { menuCtx.anchor = null; closeMenu(); return openEditor(null, byId('new-btn')); }
  const fromTpl = tgt.closest('[data-new-tpl]');
  if (fromTpl) {
    menuCtx.anchor = null; closeMenu();
    return openEditor(null, byId('new-btn'), templates.find(t => t.id === fromTpl.dataset.newTpl));
  }
  if (tgt.closest('[data-manage-templates]')) { menuCtx.anchor = null; closeMenu(); return openTemplates(byId('new-btn')); }

  const set = tgt.closest('[data-set]');
  if (set && menuCtx && menuCtx.kind === 'status') {
    const { taskId, p, i, where } = menuCtx;
    menuCtx.anchor = null;
    closeMenu();
    return setStatus(taskId, p, i, set.dataset.set, where);
  }
  const tv = tgt.closest('.task-view');
  if (tv && !isArchived(findTask(tv.dataset.id))) {
    const st = tgt.closest('button.st');
    if (st) {
      const li = st.closest('.item');
      if (!menu.hidden && menuCtx && menuCtx.anchor === st) return closeMenu();
      return openStatusMenu(tv.dataset.id, +li.dataset.p, +li.dataset.i, st);
    }
    const seg = tgt.closest('.seg');
    if (seg) {
      const btn = tv.querySelector(`.item[data-p="${seg.dataset.p}"][data-i="${seg.dataset.i}"] .st`);
      btn.scrollIntoView({ block: 'nearest' });
      return openStatusMenu(tv.dataset.id, +seg.dataset.p, +seg.dataset.i, btn);
    }
  }
  const tag = tgt.closest('[data-tag]');
  if (tag) {
    tagFilter = tag.dataset.tag;
    byId('tag-sel').value = tagFilter;
    if (!byId('focus').hidden) closeDialogs();
    return renderGrid();
  }
  const open = tgt.closest('[data-open]');
  if (open) return openFocus(open.dataset.open, open);
  const restore = tgt.closest('[data-restore]');
  if (restore) {
    api('POST', `/api/tasks/${encodeURIComponent(restore.dataset.restore)}/restore`).then(({ ok, data }) => {
      if (!ok) return toast(data.error || 'Could not restore the task.');
      upsert(data);
      renderAll();
      toast(`Restored “${data.title}”`);
    });
  }
});

/* ---- cross-highlighting ----------------------------------------------------- */

function clearHighlight() {
  document.querySelectorAll('.item.hl, .seg.hl').forEach(el => el.classList.remove('hl'));
  document.querySelectorAll('svg.dim').forEach(s => s.classList.remove('dim'));
}
function highlightFrom(target) {
  clearHighlight();
  const src = target.closest('.item, .seg');
  const view = target.closest('.task-view');
  if (!src || !view || src.dataset.p === undefined) return;
  const sel = `[data-p="${src.dataset.p}"][data-i="${src.dataset.i}"]`;
  const seg = view.querySelector('.seg' + sel), row = view.querySelector('.item' + sel);
  if (seg) { seg.classList.add('hl'); seg.closest('svg').classList.add('dim'); }
  if (row) row.classList.add('hl');
}
['grid', 'focus-view'].forEach(id => {
  byId(id).addEventListener('mouseover', e => highlightFrom(e.target));
  byId(id).addEventListener('mouseleave', clearHighlight);
  byId(id).addEventListener('focusin', e => highlightFrom(e.target));
});

/* ---- dialogs ------------------------------------------------------------------ */

let dialogOpener = null;
const DIALOGS = ['focus', 'editor', 'settings', 'templates'];

function openDialog(id, opener) {
  closeMenu();
  if (opener !== undefined) dialogOpener = opener || document.activeElement;
  DIALOGS.forEach(d => { byId(d).hidden = d !== id; });
  byId('scrim').hidden = false;
  byId('screen-app').inert = true;
}

function closeDialogs({ keepFocusId = false, restoreFocus = true } = {}) {
  DIALOGS.forEach(d => { byId(d).hidden = true; });
  byId('scrim').hidden = true;
  byId('screen-app').inert = false;
  if (!keepFocusId) focusId = null;
  if (restoreFocus && dialogOpener && document.contains(dialogOpener)) dialogOpener.focus();
  if (restoreFocus) dialogOpener = null;
}

const dialogOpen = () => DIALOGS.some(d => !byId(d).hidden);
byId('scrim').addEventListener('click', () => closeEditorOrDialogs());

function closeEditorOrDialogs() {
  if (!byId('editor').hidden && editorReturnsToTemplates) {
    editorReturnsToTemplates = false;
    setEditorMode('task');
    return openTemplates();
  }
  if (!byId('editor').hidden && editorReturnsToFocus) {
    const id = editorReturnsToFocus;
    editorReturnsToFocus = null;
    return openFocus(id);
  }
  editorReturnsToFocus = null;
  closeDialogs();
}

/* ---- keyboard ------------------------------------------------------------------- */

const typing = el => el && (el.closest('input, textarea, select, [contenteditable]'));

document.addEventListener('keydown', e => {
  if (byId('screen-app').hidden && !dialogOpen()) return;
  if (e.key === 'Escape') {
    if (!menu.hidden) return closeMenu();
    if (!byId('ed-confirm').hidden) { byId('ed-confirm').hidden = true; return; }
    if (!byId('focus-confirm').hidden) { byId('focus-confirm').hidden = true; return; }
    if (dialogOpen()) return closeEditorOrDialogs();
    if (e.target === byId('search') && query) { byId('search').value = ''; query = ''; return renderGrid(); }
    return;
  }
  // Checklist navigation and number keys, on a status chip in a card or the focus panel.
  const st = e.target.closest && e.target.closest('.task-view button.st');
  if (st && menu.hidden && !e.altKey && !e.ctrlKey && !e.metaKey) {
    const tv = st.closest('.task-view');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const all = [...tv.querySelectorAll('button.st')];
      const next = all[all.indexOf(st) + (e.key === 'ArrowDown' ? 1 : -1)];
      if (next) next.focus();
      return;
    }
    if (/^[1-5]$/.test(e.key)) {
      e.preventDefault();
      const li = st.closest('.item');
      return setStatus(tv.dataset.id, +li.dataset.p, +li.dataset.i, STATUS_ORDER[+e.key - 1], tv.id || 'grid');
    }
  }
  if (dialogOpen() || typing(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === 'n' || e.key === 'N') { e.preventDefault(); openEditor(null, byId('new-btn')); }
  else if (e.key === '/') { e.preventDefault(); byId('search').focus(); }
  else if (e.key === 'l' || e.key === 'L') { e.preventDefault(); byId('lock-btn').click(); }
});

/* ---- editor ---------------------------------------------------------------------- */

let editing = null;
let editorMode = 'task';      // 'task' | 'template'
let editingTemplate = null;

/* Repeat controls */
const monthSel = byId('ed-month-day');
monthSel.replaceChildren(...Array.from({ length: 31 }, (_, k) => new Option(`Day ${k + 1}`, String(k + 1))), new Option('Last day', '-1'));

function showRepeat(pattern) {
  byId('ed-repeat').value = pattern || '';
  byId('ed-rep-weekly').hidden = pattern !== 'weekly';
  monthSel.hidden = pattern !== 'monthly';
  byId('ed-rep-interval').hidden = pattern !== 'interval';
  byId('ed-repeat-hint').hidden = !pattern;
}

function fillRepeat(rec) {
  const due = byId('ed-due').value;
  const dueDate = due ? new Date(due + 'T12:00:00') : new Date();
  const wd = (dueDate.getDay() + 6) % 7; // JS Sunday=0 -> Monday=0
  byId('ed-rep-weekly').querySelectorAll('input').forEach(cb => {
    cb.checked = rec && rec.weekdays ? rec.weekdays.includes(+cb.value) : +cb.value === wd;
  });
  monthSel.value = String(rec && rec.month_day ? rec.month_day : dueDate.getDate());
  byId('ed-every').value = rec && rec.every_n_days ? rec.every_n_days : 7;
  showRepeat(rec ? rec.pattern : '');
}

byId('ed-repeat').addEventListener('change', e => {
  const cur = collectRepeat();
  fillRepeat(e.target.value ? { ...(cur || {}), pattern: e.target.value } : null);
});

function collectRepeat() {
  const pattern = byId('ed-repeat').value;
  if (!pattern) return null;
  if (pattern === 'weekly') return { pattern, weekdays: [...byId('ed-rep-weekly').querySelectorAll('input:checked')].map(cb => +cb.value) };
  if (pattern === 'monthly') return { pattern, month_day: +monthSel.value };
  if (pattern === 'interval') return { pattern, every_n_days: Math.round(+byId('ed-every').value) };
  return { pattern };
}

const isoPlus = days => HTRings.isoFromDayNum(HTRings.dayNum(new Date()) + days);

function iconButton(label, glyph, action) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'icon-btn';
  b.textContent = glyph;
  b.title = label;
  b.setAttribute('aria-label', label);
  b.dataset.action = action;
  return b;
}

function dragHandle(label) {
  const h = document.createElement('span');
  h.className = 'drag';
  h.textContent = '⠿';
  h.draggable = true;
  h.title = label;
  h.setAttribute('aria-hidden', 'true');
  return h;
}

function itemRow(it) {
  const row = document.createElement('div');
  row.className = 'ed-row ed-item';
  row.dataset.id = it.id || '';
  row.dataset.status = it.status || 'pending';
  const dot = document.createElement('span');
  dot.className = `st-dot bg-${row.dataset.status}`;
  dot.title = HTRings.LABEL[row.dataset.status];
  const input = document.createElement('input');
  input.className = 'it-text';
  input.value = it.text || '';
  input.maxLength = 300;
  input.placeholder = 'Checklist item';
  input.setAttribute('aria-label', 'Checklist item');
  row.append(dragHandle('Drag to reorder or move to another phase'), dot, input, iconButton('Remove item', '×', 'remove-item'));
  return row;
}

function phaseBlock(ph) {
  const box = document.createElement('div');
  box.className = 'ed-phase';
  box.dataset.id = ph.id || '';
  const head = document.createElement('div');
  head.className = 'ed-row';
  const key = document.createElement('span');
  key.className = 'ph-key';
  const name = document.createElement('input');
  name.className = 'ph-name';
  name.value = ph.name || '';
  name.maxLength = 80;
  name.placeholder = 'Phase name';
  name.setAttribute('aria-label', 'Phase name');
  head.append(dragHandle('Drag to reorder phases'), key, name,
    iconButton('Move phase out (toward the outer ring)', '↑', 'up'),
    iconButton('Move phase in (toward the center)', '↓', 'down'), iconButton('Remove phase', '×', 'remove-phase'));
  const items = document.createElement('div');
  items.className = 'ed-items';
  (ph.items.length ? ph.items : [{}]).forEach(it => items.append(itemRow(it)));
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'btn add-item';
  add.textContent = 'Add item';
  add.dataset.action = 'add-item';
  box.append(head, items, add);
  return box;
}

function refreshPhaseControls() {
  const blocks = [...byId('ed-phases').children];
  blocks.forEach((b, k) => {
    b.querySelector('.ph-key').innerHTML = HTRings.ringKeyIcon(blocks.length, k);
    b.querySelector('[data-action="up"]').disabled = k === 0;
    b.querySelector('[data-action="down"]').disabled = k === blocks.length - 1;
    b.querySelector('[data-action="remove-phase"]').disabled = blocks.length === 1;
    const list = b.querySelector('.ed-items');
    if (!list.children.length) list.append(itemRow({}));
  });
  byId('ed-add-phase').disabled = blocks.length >= MAX_PHASES;
}

function setEditorMode(mode) {
  editorMode = mode;
  byId('editor').classList.toggle('template-mode', mode === 'template');
  byId('ed-title-label').textContent = mode === 'template' ? 'Template name' : 'Title';
  byId('ed-save').textContent = mode === 'template' ? 'Save template' : 'Save task';
  byId('ed-as-template').hidden = mode === 'template';
}

function openEditor(task, opener, fromTemplate) {
  setEditorMode('task');
  editing = task || null;
  editorReturnsToTemplates = false;
  if (!task) editorReturnsToFocus = null;
  byId('editor-title').textContent = task ? 'Edit task' : fromTemplate ? `New task from “${fromTemplate.name}”` : 'New task';
  byId('ed-title').value = task ? task.title : fromTemplate ? fromTemplate.name : '';
  byId('ed-priority').value = task ? task.priority : 'Medium';
  byId('ed-start').value = task ? task.start_date : isoPlus(0);
  byId('ed-due').value = task ? task.due_date : isoPlus(14);
  byId('ed-tags').value = task ? task.tags.join(', ') : '';
  byId('ed-notes').value = task ? task.notes_md : '';
  const phases = task ? task.phases
    : fromTemplate ? fromTemplate.phases.map(p => ({ name: p.name, items: p.items.map(text => ({ text })) }))
      : [{ name: 'Checklist', items: [] }];
  byId('ed-phases').replaceChildren(...phases.map(phaseBlock));
  refreshPhaseControls();
  fillRepeat(task ? task.recurrence : null);
  byId('ed-delete').hidden = !task;
  byId('ed-confirm').hidden = true;
  setErr('#ed-err', null);
  openDialog('editor', opener);
  byId('ed-title').focus();
}

function moveItem(row, dir) {
  const list = row.parentElement;
  const sib = dir < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (sib) { dir < 0 ? sib.before(row) : sib.after(row); return true; }
  const box = list.closest('.ed-phase');
  const other = dir < 0 ? box.previousElementSibling : box.nextElementSibling;
  if (!other) return false;
  const target = other.querySelector('.ed-items');
  dir < 0 ? target.append(row) : target.prepend(row);
  refreshPhaseControls();
  return true;
}

function movePhase(box, dir) {
  const sib = dir < 0 ? box.previousElementSibling : box.nextElementSibling;
  if (!sib) return false;
  dir < 0 ? sib.before(box) : sib.after(box);
  refreshPhaseControls();
  return true;
}

byId('ed-phases').addEventListener('click', e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const box = btn.closest('.ed-phase');
  const action = btn.dataset.action;
  if (action === 'add-item') {
    const row = itemRow({});
    box.querySelector('.ed-items').append(row);
    row.querySelector('input').focus();
  } else if (action === 'remove-item') {
    btn.closest('.ed-item').remove();
  } else if (action === 'up' || action === 'down') {
    movePhase(box, action === 'up' ? -1 : 1);
    btn.focus();
  } else if (action === 'remove-phase' && byId('ed-phases').children.length > 1) {
    box.remove();
  }
  refreshPhaseControls();
});

byId('ed-phases').addEventListener('keydown', e => {
  if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
    const dir = e.key === 'ArrowUp' ? -1 : 1;
    const row = e.target.closest('.ed-item');
    const nameInput = e.target.closest('.ph-name');
    if (row) { e.preventDefault(); moveItem(row, dir); e.target.focus(); }
    else if (nameInput) { e.preventDefault(); movePhase(nameInput.closest('.ed-phase'), dir); nameInput.focus(); }
    return;
  }
  const input = e.target.closest('.it-text');
  if (!input) return;
  const row = input.closest('.ed-item');
  if (e.key === 'Enter') {
    e.preventDefault();
    const next = itemRow({});
    row.after(next);
    next.querySelector('input').focus();
  } else if (e.key === 'Backspace' && !input.value && row.parentElement.children.length > 1) {
    e.preventDefault();
    const prev = row.previousElementSibling || row.nextElementSibling;
    row.remove();
    prev.querySelector('input').focus();
  }
});

/* Drag and drop: items within and between phases, phases among themselves. */
let dragged = null;

byId('ed-phases').addEventListener('dragstart', e => {
  const handle = e.target.closest && e.target.closest('.drag');
  if (!handle) return;
  dragged = handle.closest('.ed-item') || handle.closest('.ed-phase');
  dragged.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
  if (e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(dragged, 12, 12);
});

function insertByPointer(container, selector, el, y) {
  const others = [...container.querySelectorAll(`:scope > ${selector}`)].filter(x => x !== el);
  const after = others.find(x => { const r = x.getBoundingClientRect(); return y < r.top + r.height / 2; });
  if (after) container.insertBefore(el, after); else container.append(el);
}

byId('ed-phases').addEventListener('dragover', e => {
  if (!dragged) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('.drop-target').forEach(x => x.classList.remove('drop-target'));
  if (dragged.classList.contains('ed-item')) {
    const box = e.target.closest('.ed-phase');
    if (!box) return;
    const list = box.querySelector('.ed-items');
    list.classList.add('drop-target');
    insertByPointer(list, '.ed-item', dragged, e.clientY);
  } else {
    insertByPointer(byId('ed-phases'), '.ed-phase', dragged, e.clientY);
  }
});

function endDrag() {
  if (!dragged) return;
  dragged.classList.remove('dragging');
  document.querySelectorAll('.drop-target').forEach(x => x.classList.remove('drop-target'));
  dragged = null;
  refreshPhaseControls();
}
byId('ed-phases').addEventListener('drop', e => { e.preventDefault(); endDrag(); });
byId('ed-phases').addEventListener('dragend', endDrag);

byId('ed-add-phase').addEventListener('click', () => {
  const blocks = byId('ed-phases');
  if (blocks.children.length >= MAX_PHASES) return;
  const box = phaseBlock({ name: `Phase ${blocks.children.length + 1}`, items: [] });
  blocks.append(box);
  refreshPhaseControls();
  box.querySelector('.ph-name').select();
  box.querySelector('.ph-name').focus();
});

function parseTags(text) {
  return [...new Set(text.split(',').map(t => t.trim()).filter(Boolean))];
}

function collectEditor() {
  const phases = [...byId('ed-phases').children].map(box => ({
    id: box.dataset.id || null,
    name: box.querySelector('.ph-name').value,
    items: [...box.querySelectorAll('.ed-item')]
      .filter(row => row.querySelector('input').value.trim())
      .map(row => ({ id: row.dataset.id || null, text: row.querySelector('input').value, status: row.dataset.status })),
  }));
  return {
    title: byId('ed-title').value,
    priority: byId('ed-priority').value,
    start_date: byId('ed-start').value,
    due_date: byId('ed-due').value,
    notes_md: byId('ed-notes').value,
    tags: parseTags(byId('ed-tags').value),
    recurrence: collectRepeat(),
    phases,
  };
}

function editorProblem(t) {
  if (editorMode === 'template') {
    if (!t.title.trim()) return 'Name the template.';
  } else {
  if (!t.title.trim()) return 'Give the task a title.';
  if (!t.start_date || !t.due_date) return 'Set a start date and a due date.';
  if (t.due_date < t.start_date) return 'The due date can’t be before the start date.';
  if (t.tags.some(tag => tag.length > 40)) return 'Keep each tag under 40 characters.';
  const r = t.recurrence;
  if (r && r.pattern === 'weekly' && !r.weekdays.length) return 'Pick at least one weekday to repeat on.';
  if (r && r.pattern === 'interval' && !(r.every_n_days >= 1 && r.every_n_days <= 365)) return 'Repeat every 1 to 365 days.';
  }
  const empty = t.phases.findIndex(p => !p.items.length);
  if (empty >= 0) return `Phase ${empty + 1} needs at least one item.`;
  const unnamed = t.phases.findIndex(p => !p.name.trim());
  if (unnamed >= 0) return `Name phase ${unnamed + 1}.`;
  return null;
}

byId('editor-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = collectEditor();
  const problem = editorProblem(body);
  setErr('#ed-err', problem);
  if (problem) return;
  const save = byId('ed-save');
  if (editorMode === 'template') return saveTemplateFromEditor(body);
  save.disabled = true;
  const wasEditing = editing;
  const { ok, data } = wasEditing
    ? await api('PUT', `/api/tasks/${encodeURIComponent(wasEditing.id)}`, body)
    : await api('POST', '/api/tasks', body);
  save.disabled = false;
  if (!ok) return setErr('#ed-err', data.error || 'Could not save the task.');
  upsert(data);
  editing = null;
  renderAll();
  toast(wasEditing ? 'Task saved' : 'Task created');
  if (editorReturnsToFocus) { editorReturnsToFocus = null; openFocus(data.id); }
  else closeDialogs();
});

byId('ed-cancel').addEventListener('click', closeEditorOrDialogs);
byId('ed-delete').addEventListener('click', () => { byId('ed-confirm').hidden = false; byId('ed-confirm-no').focus(); });
byId('ed-confirm-no').addEventListener('click', () => { byId('ed-confirm').hidden = true; byId('ed-delete').focus(); });
byId('ed-confirm-yes').addEventListener('click', () => { editorReturnsToFocus = null; deleteTask(editing.id); editing = null; });

byId('ed-as-template').addEventListener('click', async () => {
  const body = collectEditor();
  const named = body.phases.filter(p => p.items.length);
  if (!body.title.trim() || !named.length) return setErr('#ed-err', 'Add a title and at least one item first.');
  const { ok, data } = await api('POST', '/api/templates',
    { name: body.title, phases: named.map(p => ({ name: p.name, items: p.items.map(i => i.text) })) });
  if (!ok) return setErr('#ed-err', data.error || 'Could not save the template.');
  setErr('#ed-err', null);
  toast(`Saved “${data.name}” as a template`);
});

/* ---- templates ---------------------------------------------------------------- */

async function openTemplates(opener) {
  openDialog('templates', opener);
  byId('templates-close').focus();
  await renderTemplates();
}

async function renderTemplates() {
  const { ok, data } = await api('GET', '/api/templates');
  templates = ok ? data : [];
  byId('tpl-empty').hidden = templates.length > 0;
  byId('tpl-list').replaceChildren(...templates.map(t => {
    const li = document.createElement('li');
    li.dataset.id = t.id;
    const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = t.name;
    const sub = document.createElement('span'); sub.className = 'sub';
    sub.textContent = `${plural(t.phases.length, 'phase')} · ${plural(t.phases.reduce((n, p) => n + p.items.length, 0), 'item')}`;
    nm.append(sub);
    const mk = (label, action, cls = 'btn') => {
      const b = document.createElement('button'); b.type = 'button'; b.className = cls; b.textContent = label; b.dataset.tplAction = action;
      return b;
    };
    li.append(nm, mk('Use', 'use', 'btn primary'), mk('Edit', 'edit'), mk('Delete…', 'delete', 'btn danger-text'));
    return li;
  }));
}

byId('tpl-list').addEventListener('click', async e => {
  const b = e.target.closest('[data-tpl-action]');
  if (!b) return;
  const li = b.closest('li');
  const t = templates.find(x => x.id === li.dataset.id);
  const action = b.dataset.tplAction;
  if (action === 'use') return openEditor(null, byId('new-btn'), t);
  if (action === 'edit') return openTemplateEditor(t);
  if (action === 'delete') {
    const bar = document.createElement('div');
    bar.className = 'confirm';
    bar.append(`Delete “${t.name}”? Tasks made from it are not affected.`);
    const yes = document.createElement('button'); yes.type = 'button'; yes.className = 'btn danger'; yes.textContent = 'Delete template'; yes.dataset.tplAction = 'confirm-delete';
    const no = document.createElement('button'); no.type = 'button'; no.className = 'btn'; no.textContent = 'Keep it'; no.dataset.tplAction = 'cancel-delete';
    bar.append(yes, no);
    li.querySelectorAll('.confirm').forEach(x => x.remove());
    li.append(bar);
    return no.focus();
  }
  if (action === 'cancel-delete') return b.closest('.confirm').remove();
  if (action === 'confirm-delete') {
    const { ok, data } = await api('DELETE', `/api/templates/${encodeURIComponent(t.id)}`);
    if (!ok) return toast(data.error || 'Could not delete the template.');
    toast(`Deleted template “${t.name}”`);
    await renderTemplates();
    byId('templates-close').focus();
  }
});

function openTemplateEditor(tpl) {
  openEditor(null, byId('templates-btn'));
  setEditorMode('template');
  editingTemplate = tpl || null;
  editorReturnsToTemplates = true;
  byId('editor-title').textContent = tpl ? 'Edit template' : 'New template';
  byId('ed-title').value = tpl ? tpl.name : '';
  const phases = tpl ? tpl.phases.map(p => ({ name: p.name, items: p.items.map(text => ({ text })) })) : [{ name: 'Checklist', items: [] }];
  byId('ed-phases').replaceChildren(...phases.map(phaseBlock));
  refreshPhaseControls();
  byId('ed-title').focus();
}

async function saveTemplateFromEditor(body) {
  const payload = { name: body.title, phases: body.phases.map(p => ({ name: p.name, items: p.items.map(i => i.text) })) };
  const { ok, data } = editingTemplate
    ? await api('PUT', `/api/templates/${encodeURIComponent(editingTemplate.id)}`, payload)
    : await api('POST', '/api/templates', payload);
  if (!ok) return setErr('#ed-err', data.error || 'Could not save the template.');
  toast(editingTemplate ? 'Template saved' : 'Template created');
  editingTemplate = null;
  editorReturnsToTemplates = false;
  setEditorMode('task');
  openTemplates();
}

byId('templates-btn').addEventListener('click', e => openTemplates(e.currentTarget));
byId('templates-close').addEventListener('click', closeDialogs);
byId('tpl-create').addEventListener('click', () => openTemplateEditor(null));
byId('empty-new').addEventListener('click', e => openEditor(null, e.currentTarget));

/* ---- settings ---------------------------------------------------------------------- */

byId('settings-btn').addEventListener('click', async e => {
  openDialog('settings', e.currentTarget);
  byId('settings-close').focus();
  setErr('#prefs-err', null);
  setErr('#import-err', null);
  byId('import-confirm').hidden = true;
  byId('import-start').hidden = false;
  loadVault();
  const { ok, data } = await api('GET', '/api/settings');
  if (ok) {
    byId('set-autolock').value = data.auto_lock_minutes;
    byId('set-theme').value = data.theme;
  }
});
byId('settings-close').addEventListener('click', closeDialogs);

/* Backup export and import */
byId('backup-export').addEventListener('click', async () => {
  const resp = await fetch('/api/backup/export', {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: '{}',
  });
  if (!resp.ok) return toast('Could not export the backup.');
  const name = (resp.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name ? name[1] : 'HaphazardTasks-backup.htv';
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast(`Exported ${a.download}`);
  loadVault();
});

byId('import-start').addEventListener('click', () => {
  if (!byId('import-file').files.length) return setErr('#import-err', 'Choose a backup file first.');
  if (!byId('import-pass').value) return setErr('#import-err', 'Enter the passphrase for that backup.');
  setErr('#import-err', null);
  byId('import-confirm').hidden = false;
  byId('import-start').hidden = true;
  byId('import-cancel').focus();
});
byId('import-cancel').addEventListener('click', () => {
  byId('import-confirm').hidden = true;
  byId('import-start').hidden = false;
});

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

byId('import-form').addEventListener('submit', async e => {
  e.preventDefault();
  const file = byId('import-file').files[0];
  if (!file) return;
  const { ok, data } = await api('POST', '/api/backup/import',
    { file_b64: await fileToBase64(file), passphrase: byId('import-pass').value });
  byId('import-confirm').hidden = true;
  byId('import-start').hidden = false;
  if (!ok) return setErr('#import-err', data.error || 'Could not restore the backup.');
  csrf = data.csrf;
  byId('import-pass').value = '';
  byId('import-file').value = '';
  setErr('#import-err', null);
  closeDialogs();
  await startDashboard();
  toast('Backup restored. The previous vault was kept as a saved version.');
});
byId('set-theme').addEventListener('change', e => applyTheme(e.target.value)); // preview at once
byId('prefs-form').addEventListener('submit', async e => {
  e.preventDefault();
  const minutes = Number(byId('set-autolock').value);
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 120) return setErr('#prefs-err', 'Auto-lock must be 1 to 120 minutes.');
  const { ok, data } = await api('PUT', '/api/settings', { auto_lock_minutes: minutes, theme: byId('set-theme').value });
  if (!ok) return setErr('#prefs-err', data.error || 'Could not save preferences.');
  setErr('#prefs-err', null);
  applyTheme(data.theme);
  poll();
  toast(`Saved. Auto-lock after ${plural(data.auto_lock_minutes, 'minute')}.`);
});

/* ---- example tasks ----------------------------------------------------------------- */

function exampleTasks() {
  const P = (name, ...items) => ({ name, items: items.map(([text, status]) => ({ text, status })) });
  const ex = (title, priority, start, due, notes, phases, recurrence = null) =>
    ({ title, priority, start_date: isoPlus(start), due_date: isoPlus(due), notes_md: notes, tags: ['example'], phases, recurrence });
  const dueDay = days => new Date(isoPlus(days) + 'T12:00:00');
  return [
    ex('Q4 vendor security review', 'High', -25, 19, 'Three vendors in scope.\n\n- Pen-test report is **waiting on the vendor**\n- Follow up Thursday', [
      P('Discovery', ['Collect SOC 2 reports', 'done'], ['Map data flows', 'done'], ['Inventory vendor access', 'done']),
      P('Assessment', ['Draft risk register', 'underway'], ['Interview vendor point of contact', 'underway'], ['Review pen-test results', 'blocked']),
      P('Close-out', ['Legal review', 'pending'], ['Executive summary', 'pending'])]),
    ex('Weekly status report', 'Medium', -3, 4, 'Resets every week. Finished weeks are kept under Past cycles.', [
      P('This week', ['Pull metrics', 'done'], ['Update risk log', 'underway'], ['Draft summary', 'pending'], ['Send to leadership', 'pending'])],
      { pattern: 'weekly', weekdays: [(dueDay(4).getDay() + 6) % 7] }),
    ex('Contract renewal, Site B', 'High', -36, -2, 'Pricing call slipped twice. Signatures wait on pricing.', [
      P('Prep', ['Gather usage data', 'done'], ['Compare quotes', 'done']),
      P('Negotiate', ['Redline terms', 'done'], ['Pricing call', 'underway']),
      P('Execute', ['Collect signatures', 'pending'], ['File in records', 'pending'])]),
    ex('Network segmentation plan', 'High', -55, 53, 'Pilot is blocked on the `change-window` approval.', [
      P('Survey', ['Asset inventory', 'done'], ['Traffic capture', 'done'], ['Trust-zone draft', 'done']),
      P('Design', ['VLAN plan', 'done'], ['Firewall rule set', 'done'], ['Rollback plan', 'underway']),
      P('Pilot', ['Lab build', 'pending'], ['Pilot site cutover', 'pending'], ['Change-window approval', 'blocked']),
      P('Rollout', ['Site 1', 'pending'], ['Site 2', 'pending'], ['Legacy subnet', 'deferred'])]),
    ex('Onboard new analyst', 'Medium', -13, 11, 'Shadowing starts once training clears.', [
      P('Accounts', ['Laptop issued', 'done'], ['Directory account', 'done'], ['Badge access', 'done']),
      P('Training', ['Security awareness', 'done'], ['Tooling walkthrough', 'underway'], ['Data handling policy', 'pending']),
      P('Shadowing', ['Shadow two reviews', 'pending'], ['Lead one review', 'pending'])]),
    ex('Monthly access audit', 'Low', -25, 5, 'Contractor accounts deferred to next cycle.', [
      P('This month', ['Export user list', 'done'], ['Compare to HR roster', 'done'], ['Disable stale accounts', 'done'], ['Review admin groups', 'done'], ['Review contractor accounts', 'pending'])],
      { pattern: 'monthly', month_day: dueDay(5).getDate() }),
  ];
}

byId('load-examples').addEventListener('click', async e => {
  const btn = e.currentTarget;
  btn.disabled = true;
  for (const t of exampleTasks()) {
    const { ok, data } = await api('POST', '/api/tasks', t);
    if (!ok) { btn.disabled = false; return toast(data.error || 'Could not add the examples.'); }
  }
  btn.disabled = false;
  await loadTasks();
  toast('Added 6 example tasks');
});

renderLegend();
