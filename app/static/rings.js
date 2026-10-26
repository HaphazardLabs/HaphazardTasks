'use strict';
/* Ring geometry and day math. Pure functions: loaded by the page and by the Node tests. */
(function (root) {
  const DAY_LIMIT = 120; // beyond this the day ring switches to week segments
  const LABEL = { done: 'Done', underway: 'Underway', pending: 'Pending', blocked: 'Blocked', deferred: 'Deferred' };

  /** Calendar day number for 'YYYY-MM-DD' or a local Date, immune to DST and time zones. */
  function dayNum(d) {
    if (typeof d === 'string') {
      const [y, m, day] = d.split('-').map(Number);
      return Date.UTC(y, m - 1, day) / 864e5;
    }
    return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 864e5;
  }

  function isoFromDayNum(n) {
    return new Date(n * 864e5).toISOString().slice(0, 10);
  }

  function allItems(task) {
    return task.phases.flatMap(p => p.items);
  }

  /** Percent done ignores deferred items. */
  function progress(task) {
    const live = allItems(task).filter(i => i.status !== 'deferred');
    const done = live.filter(i => i.status === 'done').length;
    return { done, total: live.length, p: live.length ? done / live.length : 1 };
  }

  /**
   * Day-cycle ring model. day = today's day number counted from the start (start = day 1).
   * Overdue (past due and not complete) stretches the ring to today.
   */
  function dayModel(startIso, dueIso, today, complete) {
    const s = dayNum(startIso), d = dayNum(dueIso), t = typeof today === 'number' ? today : dayNum(today);
    const total = d - s + 1;
    const day = t - s + 1;
    const late = day > total && !complete;
    const span = late ? day : total;
    const unit = span > DAY_LIMIT ? 7 : 1;
    const n = Math.ceil(span / unit);
    const segments = [];
    for (let k = 0; k < n; k++) {
      const first = k * unit + 1, last = Math.min((k + 1) * unit, span);
      let cls;
      if (day >= first && day <= last) cls = late ? 'today-late' : 'today';
      else if (late) cls = first > total ? 'late-past' : 'late';
      else cls = last < day ? 'past' : 'ahead';
      segments.push({ first, last, cls, startIso: isoFromDayNum(s + first - 1), isDue: first <= total && total <= last });
    }
    return {
      total, day, late, unit, span, segments,
      daysLeft: d - t,                  // 0 = due today, negative = overdue
      startsIn: day < 1 ? 1 - day : 0,
      dueFraction: late ? total / span : 1,
    };
  }

  function polar(r, deg) {
    const rad = (deg - 90) * Math.PI / 180;
    return [100 + r * Math.cos(rad), 100 + r * Math.sin(rad)];
  }

  function arc(r, a0, a1) {
    if (a1 - a0 >= 359.9) a1 = a0 + 359.9;
    const [x0, y0] = polar(r, a0), [x1, y1] = polar(r, a1);
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }

  /** Radii for n phase rings, outermost first. Rings thin out so the center hole stays put. */
  function phaseRadii(n) {
    const outer = 85, innerMin = 50, gap = n > 1 ? 3.5 : 0;
    const th = Math.min(17, (outer - innerMin - gap * (n - 1)) / n);
    return Array.from({ length: n }, (_, k) => ({ r: outer - th / 2 - k * (th + gap), th }));
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  const fmt = iso => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

  /** Full ring set as an SVG string: phase rings, day-cycle ring, center readout. */
  function ringSVG(task, today) {
    const parts = [];
    const radii = phaseRadii(task.phases.length);
    task.phases.forEach((ph, p) => {
      const { r, th } = radii[p];
      parts.push(`<circle class="track" cx="100" cy="100" r="${r}" stroke-width="${th}"/>`);
      const m = ph.items.length, step = 360 / m, g = m > 1 ? Math.min(3, step * 0.18) : 0;
      ph.items.forEach((it, i) => {
        parts.push(`<path class="seg s-${it.status}" data-p="${p}" data-i="${i}" stroke-width="${th}" ` +
          `d="${arc(r, i * step + g / 2, (i + 1) * step - g / 2)}"><title>${esc(it.text)}: ${LABEL[it.status]}</title></path>`);
      });
    });

    const pr = progress(task);
    const dm = dayModel(task.start_date, task.due_date, today, pr.p >= 1);
    const step = 360 / dm.segments.length, g = Math.min(1.4, step * 0.28), R = 92.5;
    dm.segments.forEach((sg, k) => {
      const label = dm.unit === 1 ? `Day ${sg.first} · ${fmt(sg.startIso)}` : `Week ${k + 1} · from ${fmt(sg.startIso)}`;
      const tag = sg.cls.startsWith('today') ? ' (today)' : sg.cls === 'late-past' ? ' (past due)' : sg.isDue ? ' (due)' : '';
      parts.push(`<path class="day d-${sg.cls}" d="${arc(R, k * step + g / 2, (k + 1) * step - g / 2)}"><title>${label}${tag}</title></path>`);
    });
    if (dm.late) {
      const a = dm.dueFraction * 360, [x0, y0] = polar(R - 6, a), [x1, y1] = polar(R + 6, a);
      parts.push(`<line class="due-mark" x1="${x0.toFixed(2)}" y1="${y0.toFixed(2)}" x2="${x1.toFixed(2)}" y2="${y1.toFixed(2)}"><title>Due ${fmt(task.due_date)}</title></line>`);
    }
    parts.push(`<text class="c-pct" x="100" y="96">${Math.round(pr.p * 100)}%</text>`);
    parts.push(`<text class="c-sub" x="100" y="121">${pr.done} of ${pr.total}</text>`);
    return `<svg viewBox="0 0 200 200" role="img" aria-label="${esc(task.title)}: ${pr.done} of ${pr.total} items done">${parts.join('')}</svg>`;
  }

  const HEALTH_LABEL = { late: 'Overdue', behind: 'Behind', ontrack: 'On pace', complete: 'Complete' };
  const BEHIND_MARGIN = 0.12;

  /** Share of the schedule used so far, 0..1. Today counts as half used. */
  function pace(task, today) {
    const dm = dayModel(task.start_date, task.due_date, today, false);
    return Math.max(0, Math.min(1, (dm.day - 0.5) / dm.total));
  }

  /** complete | late | behind | ontrack. Behind = percent done trails time used by more than 12 points. */
  function health(task, today) {
    const pr = progress(task);
    if (pr.p >= 1) return 'complete';
    const dm = dayModel(task.start_date, task.due_date, today, false);
    if (dm.day > dm.total) return 'late';
    if (pr.p + BEHIND_MARGIN < pace(task, today)) return 'behind';
    return 'ontrack';
  }

  /** Overview: one ring per task (outermost = first), arc = percent done, tick = schedule used. */
  function overviewSVG(list, today) {
    const n = list.length;
    const outer = 88, inner = 42, gap = n > 1 ? 2.5 : 0;
    const th = n ? Math.min(10, (outer - inner - gap * (n - 1)) / n) : 0;
    let all = 0, done = 0;
    const parts = [];
    list.forEach((t, k) => {
      const r = outer - th / 2 - k * (th + gap), pr = progress(t), h = health(t, today);
      all += pr.total; done += pr.done;
      parts.push(`<circle class="track" cx="100" cy="100" r="${r}" stroke-width="${th}"/>`);
      if (pr.p > 0) parts.push(`<path class="ov-arc ov-${h}" stroke-width="${th}" d="${arc(r, 0, Math.max(pr.p * 360, 2))}"><title>${esc(t.title)}: ${Math.round(pr.p * 100)}%</title></path>`);
      const a = pace(t, today) * 360, [x0, y0] = polar(r - th / 2 - 1, a), [x1, y1] = polar(r + th / 2 + 1, a);
      parts.push(`<line class="ov-tick" x1="${x0.toFixed(2)}" y1="${y0.toFixed(2)}" x2="${x1.toFixed(2)}" y2="${y1.toFixed(2)}"/>`);
    });
    const pct = all ? Math.round(done / all * 100) : 0;
    parts.push(`<text class="c-pct" x="100" y="94">${pct}%</text><text class="c-sub" x="100" y="118">all items</text>`);
    return `<svg viewBox="0 0 200 200" role="img" aria-label="Overall progress: ${pct}% of items done across ${n} tasks">${parts.join('')}</svg>`;
  }

  /** Small icon showing which ring (k of n) a phase is drawn on. */
  function ringKeyIcon(n, k) {
    let out = '';
    for (let j = 0; j < n; j++) {
      const r = n === 1 ? 6 : 6.5 - j * (5 / (n - 1));
      out += `<circle cx="8" cy="8" r="${r.toFixed(2)}" class="${j === k ? 'on' : 'off'}" stroke-width="${j === k ? 1.8 : 1}"/>`;
    }
    return `<svg class="key-ico" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">${out}</svg>`;
  }

  const api = { DAY_LIMIT, LABEL, HEALTH_LABEL, pace, health, overviewSVG, dayNum, isoFromDayNum, allItems, progress, dayModel, phaseRadii, arc, polar, ringSVG, ringKeyIcon, esc, fmt };
  root.HTRings = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
