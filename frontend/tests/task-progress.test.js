import test from 'node:test';
import assert from 'node:assert/strict';
import { taskProgressLabel } from '../src/task-progress.js';

test('round, total and merit are shown for a running optimization', () => {
  assert.equal(taskProgressLabel({ progress: { round: 3, rounds: 6, merit_after: 0.023417 } }), '第 3/6 轮 · MF 0.02342');
});

test('timeouts and non-finite merit are announced, not shown as progress numbers', () => {
  assert.equal(taskProgressLabel({ progress: { round: 2, rounds: 6, timed_out: true } }), '第 2/6 轮 · 本轮超时，正在取消');
  assert.equal(taskProgressLabel({ progress: { round: 4, stop_reason: 'merit_function_non_finite' } }), '第 4 轮 · 评价函数非有限，正在停止');
});

test('missing or malformed progress yields no label', () => {
  for (const task of [null, {}, { progress: null }, { progress: [] }, { progress: { round: 0 } },
    { progress: { round: 1.5 } }, { progress: { round: '2' } }]) {
    assert.equal(taskProgressLabel(task), '');
  }
  assert.equal(taskProgressLabel({ progress: { round: 1, merit_after: Number.NaN } }), '第 1 轮');
});
