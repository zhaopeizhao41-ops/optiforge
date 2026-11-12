import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveFocus, validCommand, validFocus } from '../src/workbench-ui.js';

test('focus schema mirrors the service: per-page keys, known tabs, integer targets', () => {
  for (const [page, focus] of [['design', { surface: 0 }], ['analysis', { tab: 'spot', field: 2 }], ['analysis', { tab: 'overview' }],
    ['workflow', { tab: 'milestones', stage: 'stage_3' }], ['workflow', { tab: 'tasks' }], ['design', null], ['analysis', undefined]]) {
    assert.equal(validFocus(page, focus), true, JSON.stringify([page, focus]));
  }
  for (const [page, focus] of [['design', {}], ['design', { surface: -1 }], ['design', { surface: 1.5 }], ['design', { surface: '1' }],
    ['analysis', { field: 1 }], ['analysis', { tab: 'zernike' }], ['analysis', { tab: 'mtf', field: 0 }],
    ['workflow', { tab: 'tasks', stage: 'stage_1' }], ['workflow', { tab: 'milestones', stage: 'stage_9' }], ['design', [1]]]) {
    assert.equal(validFocus(page, focus), false, JSON.stringify([page, focus]));
  }
});

test('commands with malformed focus are ignored rather than half-applied', () => {
  const base = { command_id: 'ui_1', request_id: 'r', page: 'analysis' };
  assert.equal(validCommand({ ...base, focus: { tab: 'spot' } }), true);
  assert.equal(validCommand({ ...base, focus: { surface: 1 } }), false);
  assert.equal(validCommand(base), true);
});

test('resolver honours only what exists and never substitutes another target', () => {
  assert.deepEqual(resolveFocus('design', { surface: 4 }, { surfaces: [0, 1, 4, 7] }), { actual: { surface: 4 }, reason: null });
  const missing = resolveFocus('design', { surface: 99 }, { surfaces: [0, 1, 4] });
  assert.equal(missing.actual, null);
  assert.match(missing.reason, /S99/);
  assert.deepEqual(resolveFocus('analysis', { tab: 'spot', field: 2 }, { fields: [1, 2, 3] }).actual, { tab: 'spot', field: 2 });
  const noField = resolveFocus('analysis', { tab: 'mtf', field: 9 }, { fields: [1, 2, 3] });
  assert.deepEqual(noField.actual, { tab: 'mtf' });
  assert.match(noField.reason, /F9/);
  assert.match(resolveFocus('analysis', { tab: 'mtf', field: 1 }, { fields: null }).reason, /没有分析结果/);
  assert.deepEqual(resolveFocus('workflow', { tab: 'milestones', stage: 'stage_2' }, { stageAvailable: true }).actual,
    { tab: 'milestones', stage: 'stage_2' });
  assert.deepEqual(resolveFocus('workflow', { tab: 'milestones', stage: 'stage_2' }, { stageAvailable: false }).actual,
    { tab: 'milestones' });
  assert.deepEqual(resolveFocus('workflow', { tab: 'proposal' }), { actual: { tab: 'proposal' }, reason: null });
  assert.deepEqual(resolveFocus('design', null), { actual: null, reason: null });
});
