import test from 'node:test';
import assert from 'node:assert/strict';
import { TASK_FILTERS, taskFilterView } from '../src/task-filters.js';
import { taskPane } from '../src/task-provenance.js';

const task = (request_id, status, extra = {}) => ({
  request_id, status, project_name: 'project-a', kind: 'surface_question',
  user_text: '已存储的任务', stale: false, ...extra,
});
const flow = tasks => ({ error: '', view: {
  service_instance_id: 'service-a', project_name: 'project-a',
  context: { project_name: 'project-a', active_project: 'project-a' }, tasks,
} });
const sync = () => ({
  transportConnected: true, phase: 'synced', canCollaborate: true, canAnalyze: false,
  engine: { service_instance_id: 'service-a', active_project: 'project-a', proposal_confirmed: false },
});
const freeze = value => {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
};

test('the six supported filters have stable labels and immutable definitions', () => {
  assert.deepEqual(TASK_FILTERS, [
    { value: 'all', label: '全部' },
    { value: 'pending', label: '待领取' },
    { value: 'claimed', label: '已领取' },
    { value: 'completed', label: '已完成' },
    { value: 'failed', label: '失败' },
    { value: 'superseded', label: '已归档' },
  ]);
  assert.ok(Object.isFrozen(TASK_FILTERS));
  assert.ok(TASK_FILTERS.every(Object.isFrozen));
});

test('counts partition only loaded ready records by exact stored status', () => {
  const rows = [
    task('p-old', 'pending', { stale: true }),
    task('claim-expired', 'claimed', { lease_expired: true }),
    task('done', 'completed'), task('fail', 'failed'), task('archive', 'superseded'),
    task('p-new', 'pending'), task('done-old', 'completed', { stale: true }),
  ];
  const pane = taskPane(flow(rows), sync());
  const result = taskFilterView(pane);
  assert.equal(result.selected, 'all');
  assert.equal(result.total, 7);
  assert.deepEqual(result.counts, { all: 7, pending: 2, claimed: 1, completed: 2, failed: 1, superseded: 1 });
  assert.equal(Object.entries(result.counts).filter(([status]) => status !== 'all')
    .reduce((sum, [, count]) => sum + count, 0), result.total);
  for (const { value } of TASK_FILTERS.slice(1)) {
    const visible = taskFilterView(pane, value);
    assert.deepEqual(visible.tasks, rows.filter(row => row.status === value));
    assert.deepEqual(visible.counts, result.counts);
    assert.equal(visible.tasks.length, result.counts[value]);
  }
  assert.equal(taskFilterView(pane, 'pending').tasks[0], rows[0]);
  assert.equal(taskFilterView(pane, 'claimed').tasks[0], rows[1]);
});

test('All retains the source array; subsets retain source ordering and record identity without mutation', () => {
  const rows = freeze([
    task('c1', 'completed'), task('p1', 'pending'), task('c2', 'completed'),
    task('f1', 'failed'), task('c3', 'completed'),
  ]);
  const f = freeze(flow(rows)), s = freeze(sync());
  const before = JSON.stringify({ f, s });
  const pane = taskPane(f, s);
  assert.equal(taskFilterView(pane).tasks, rows);
  const selected = taskFilterView(pane, 'completed').tasks;
  assert.deepEqual(selected.map(row => row.request_id), ['c1', 'c2', 'c3']);
  assert.equal(selected[0], rows[0]);
  assert.equal(selected[1], rows[2]);
  assert.equal(selected[2], rows[4]);
  assert.equal(JSON.stringify({ f, s }), before);
});

test('nonready panes never expose cached rows, counts, totals or the previous filter', () => {
  const rows = [task('stored', 'failed')];
  for (const pane of [undefined, null, {}, ...['offline', 'unavailable', 'loading', 'error']
    .map(status => ({ status, tasks: rows }))]) {
    assert.deepEqual(taskFilterView(pane, 'failed'), {
      selected: 'all', counts: null, total: null, tasks: [],
    });
  }
});

test('empty loaded records and an empty selected subset remain distinguishable', () => {
  const empty = taskFilterView(taskPane(flow([]), sync()), 'failed');
  assert.equal(empty.selected, 'failed');
  assert.equal(empty.total, 0);
  assert.deepEqual(empty.tasks, []);
  assert.deepEqual(empty.counts, { all: 0, pending: 0, claimed: 0, completed: 0, failed: 0, superseded: 0 });
  const rows = [task('pending', 'pending')], pane = taskPane(flow(rows), sync());
  const noMatch = taskFilterView(pane, 'failed');
  assert.equal(noMatch.total, 1);
  assert.equal(noMatch.counts.failed, 0);
  assert.deepEqual(noMatch.tasks, []);
  assert.equal(taskFilterView(pane, 'all').tasks, rows);
});

test('unsupported selection falls back to All, with no synthetic status buckets', () => {
  const rows = [task('done', 'completed')], pane = taskPane(flow(rows), sync());
  for (const selected of ['running', 'stale', 'lease_expired', '__proto__', null, {}, 0]) {
    const result = taskFilterView(pane, selected);
    assert.equal(result.selected, 'all');
    assert.equal(result.tasks, rows);
    assert.deepEqual(Object.keys(result.counts), TASK_FILTERS.map(({ value }) => value));
  }
});

test('malformed ready arrays fail closed rather than report fabricated counts', () => {
  for (const tasks of [null, {}, [null], [task('unknown', 'running')], [task('unset', undefined)]]) {
    assert.deepEqual(taskFilterView({ status: 'ready', tasks }, 'pending'), {
      selected: 'all', counts: null, total: null, tasks: [],
    });
  }
});

test('taskPane remains the authority for offline, error, scope and record validation', () => {
  const rows = [task('done', 'completed')];
  const cases = [
    [flow(rows), { ...sync(), transportConnected: false }],
    [{ ...flow(rows), error: 'refresh failed' }, sync()],
    [flow(rows), { ...sync(), canCollaborate: false }],
    [flow(rows), { ...sync(), engine: { ...sync().engine, service_instance_id: 'service-b' } }],
    [flow(rows), { ...sync(), engine: { ...sync().engine, active_project: 'project-b' } }],
    [flow([task('foreign', 'pending', { project_name: 'project-b' })]), sync()],
    [flow([task('duplicate', 'pending'), task('duplicate', 'failed')]), sync()],
  ];
  for (const [f, s] of cases) {
    const pane = taskPane(f, s);
    assert.notEqual(pane.status, 'ready');
    assert.deepEqual(taskFilterView(pane, 'completed'), {
      selected: 'all', counts: null, total: null, tasks: [],
    });
  }
});

test('native-unavailable HTTP collaboration can filter, without granting optical capabilities', () => {
  const rows = [task('pending', 'pending'), task('failed', 'failed')];
  const s = sync(); s.phase = 'unavailable'; s.error = 'native unavailable';
  s.engine.context_valid = false; s.engine.snapshot = null;
  const result = taskFilterView(taskPane(flow(rows), s), 'failed');
  assert.equal(result.total, 2);
  assert.equal(result.tasks[0], rows[1]);
  assert.equal(s.canAnalyze, false);
  assert.equal(s.engine.proposal_confirmed, false);
});

test('same-source refreshed data uses the selected filter and the newly loaded counts', () => {
  const s = sync();
  const before = taskFilterView(taskPane(flow([task('old', 'failed')]), s), 'failed');
  const refreshed = [task('old', 'completed'), task('new', 'failed'), task('pending', 'pending')];
  const after = taskFilterView(taskPane(flow(refreshed), s), before.selected);
  assert.equal(after.selected, 'failed');
  assert.deepEqual(after.tasks.map(row => row.request_id), ['new']);
  assert.equal(after.total, 3);
  assert.deepEqual(after.counts, { all: 3, pending: 1, claimed: 0, completed: 1, failed: 1, superseded: 0 });
});
