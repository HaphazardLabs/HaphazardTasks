// Day-ring and progress edge cases. Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../app/static/rings.js');

const T = R.dayNum('2026-10-26');
const count = (m, cls) => m.segments.filter(s => s.cls === cls).length;

test('normal task mid-way: elapsed, today, ahead', () => {
  const m = R.dayModel('2026-10-01', '2026-11-14', T, false);
  assert.equal(m.total, 45);
  assert.equal(m.day, 26);
  assert.equal(m.unit, 1);
  assert.equal(m.segments.length, 45);
  assert.equal(count(m, 'past'), 25);
  assert.equal(count(m, 'today'), 1);
  assert.equal(count(m, 'ahead'), 19);
  assert.equal(m.daysLeft, 19);
  assert.equal(m.late, false);
});

test('start in the future: all ahead, startsIn counted', () => {
  const m = R.dayModel('2026-10-29', '2026-11-05', T, false);
  assert.equal(m.startsIn, 3);
  assert.equal(count(m, 'ahead'), m.segments.length);
  assert.equal(count(m, 'today'), 0);
});

test('due today: today is the last segment, not late', () => {
  const m = R.dayModel('2026-10-20', '2026-10-26', T, false);
  assert.equal(m.late, false);
  assert.equal(m.daysLeft, 0);
  assert.equal(m.segments.at(-1).cls, 'today');
  assert.ok(m.segments.at(-1).isDue);
});

test('overdue: ring stretches to today, past-due days striped, due mark placed', () => {
  const m = R.dayModel('2026-09-20', '2026-10-24', T, false);
  assert.equal(m.total, 35);
  assert.equal(m.day, 37);
  assert.equal(m.late, true);
  assert.equal(m.segments.length, 37);
  assert.equal(count(m, 'late'), 35);
  assert.equal(count(m, 'late-past'), 1);
  assert.equal(m.segments.at(-1).cls, 'today-late');
  assert.equal(m.daysLeft, -2);
  assert.ok(Math.abs(m.dueFraction - 35 / 37) < 1e-9);
});

test('finished after the due date is not overdue', () => {
  const m = R.dayModel('2026-09-20', '2026-10-24', T, true);
  assert.equal(m.late, false);
  assert.equal(m.segments.length, 35);
  assert.equal(count(m, 'past'), 35);
});

test('over 120 days switches to week segments', () => {
  const m = R.dayModel('2026-06-01', '2026-12-31', T, false);
  assert.equal(m.total, 214);
  assert.equal(m.unit, 7);
  assert.equal(m.segments.length, Math.ceil(214 / 7));
  assert.equal(count(m, 'today'), 1);
  const today = m.segments.find(s => s.cls === 'today');
  assert.ok(today.first <= m.day && m.day <= today.last);
});

test('exactly 120 days stays daily', () => {
  const m = R.dayModel('2026-10-01', R.isoFromDayNum(R.dayNum('2026-10-01') + 119), T, false);
  assert.equal(m.total, 120);
  assert.equal(m.unit, 1);
});

test('single-day task', () => {
  const m = R.dayModel('2026-10-26', '2026-10-26', T, false);
  assert.equal(m.segments.length, 1);
  assert.equal(m.segments[0].cls, 'today');
});

test('day numbers ignore DST changes', () => {
  // US DST ends 2026-11-01; the span must still count whole days.
  const m = R.dayModel('2026-10-31', '2026-11-02', R.dayNum('2026-11-02'), false);
  assert.equal(m.total, 3);
  assert.equal(m.day, 3);
});

test('progress excludes deferred items', () => {
  const task = { phases: [{ items: [{ status: 'done' }, { status: 'done' }, { status: 'pending' }, { status: 'deferred' }] }] };
  assert.deepEqual(R.progress(task), { done: 2, total: 3, p: 2 / 3 });
});

test('phase radii: rings never overlap and stay inside the day ring', () => {
  for (let n = 1; n <= 4; n++) {
    const rs = R.phaseRadii(n);
    assert.ok(rs[0].r + rs[0].th / 2 <= 85.0001);
    for (let k = 1; k < n; k++) assert.ok(rs[k - 1].r - rs[k - 1].th / 2 > rs[k].r + rs[k].th / 2);
    assert.ok(rs.at(-1).r - rs.at(-1).th / 2 >= 49.99);
  }
});

test('ringSVG escapes item text', () => {
  const task = { title: '<b>x</b>', start_date: '2026-10-01', due_date: '2026-11-01',
    phases: [{ name: 'P', items: [{ text: '<img src=x onerror=alert(1)>', status: 'done' }] }] };
  const svg = R.ringSVG(task, T);
  assert.ok(!svg.includes('<img'));
  assert.ok(!svg.includes('<b>'));
});

const mk = (start, due, statuses) => ({ title: 't', start_date: start, due_date: due,
  phases: [{ name: 'P', items: statuses.map(status => ({ text: 'x', status })) }] });

test('health: overdue, behind, on pace, complete', () => {
  assert.equal(R.health(mk('2026-09-20', '2026-10-24', ['done', 'pending']), T), 'late');
  // Day 26 of 45 is ~57% of the schedule; 3 of 8 done (38%) trails by more than 12 points.
  assert.equal(R.health(mk('2026-10-01', '2026-11-14', ['done', 'done', 'done', 'pending', 'pending', 'pending', 'pending', 'pending']), T), 'behind');
  assert.equal(R.health(mk('2026-10-01', '2026-11-14', ['done', 'done', 'done', 'done', 'done', 'pending', 'pending', 'pending']), T), 'ontrack');
  assert.equal(R.health(mk('2026-09-20', '2026-10-24', ['done', 'deferred']), T), 'complete');
  assert.equal(R.health(mk('2026-10-29', '2026-11-05', ['pending']), T), 'ontrack');
});

test('pace is clamped to 0..1', () => {
  assert.equal(R.pace(mk('2026-10-29', '2026-11-05', ['pending']), T), 0);
  assert.equal(R.pace(mk('2026-09-01', '2026-09-30', ['pending']), T), 1);
});

test('overviewSVG draws one track per task and escapes titles', () => {
  const list = [mk('2026-10-01', '2026-11-14', ['done', 'pending']), { ...mk('2026-10-01', '2026-11-14', ['pending']), title: '<script>' }];
  const svg = R.overviewSVG(list, T);
  assert.equal((svg.match(/class="track"/g) || []).length, 2);
  assert.ok(!svg.includes('<script>'));
  assert.ok(svg.includes('33%'));
});
