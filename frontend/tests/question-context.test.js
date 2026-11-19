import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_ANCHOR_COMMENT, MAX_QUESTION_ANCHORS, addDraft, anchorKey, anchorLabel, anchorsLabel, buildTarget, consumeDrafts,
  draftsFor, emptyQuestionContext, parseTarget, reconcileDrafts, removeDraft, setDraftComment, validAnchor,
} from '../src/question-context.js';

const reference = (extra = {}) => ({ service_instance_id: 'service-a', model_id: 'model-a', model_revision: 2,
  model_project: 'project-a', configuration: 1, ...extra });
const add = (state, anchor, ref = reference()) => addDraft(state, anchor, ref);
const fill = (anchors, ref = reference()) => anchors.reduce((state, anchor) => add(state, anchor, ref).state, emptyQuestionContext);

test('anchors mirror the backend shapes', () => {
  for (const anchor of [{ kind: 'surface', index: 0 }, { kind: 'stage', stage: 'stage_4' },
    { kind: 'analysis', tab: 'overview', result_id: 'r-1' }, { kind: 'analysis', tab: 'mtf', result_id: 'r-1', field: 2 },
    { kind: 'surface', index: 3, comment: 'x'.repeat(MAX_ANCHOR_COMMENT) }]) assert.equal(validAnchor(anchor), true, JSON.stringify(anchor));
  for (const anchor of [null, [], { kind: 'lens' }, { kind: 'surface', index: -1 }, { kind: 'surface', index: 1.5 },
    { kind: 'surface', index: 1, extra: true }, { kind: 'stage', stage: 'stage_5' },
    { kind: 'analysis', tab: 'psf', result_id: 'r-1' }, { kind: 'analysis', tab: 'mtf' },
    { kind: 'analysis', tab: 'mtf', result_id: '-bad' }, { kind: 'analysis', tab: 'overview', result_id: 'r', field: 1 },
    { kind: 'analysis', tab: 'mtf', result_id: 'r', field: 0 }, { kind: 'surface', index: 1, comment: '  ' },
    { kind: 'surface', index: 1, comment: 'x'.repeat(MAX_ANCHOR_COMMENT + 1) }]) assert.equal(validAnchor(anchor), false, JSON.stringify(anchor));
  assert.equal(anchorKey({ kind: 'surface', index: 2, comment: 'a' }), anchorKey({ comment: 'b', index: 2, kind: 'surface' }));
  assert.deepEqual([{ kind: 'surface', index: 3 }, { kind: 'stage', stage: 'stage_2' }, { kind: 'analysis', tab: 'mtf', result_id: 'r', field: 2 },
    { kind: 'analysis', tab: 'overview', result_id: 'r' }].map(anchorLabel), ['S3', '阶段 2', 'MTF · F2', '分析概览']);
  const many = [0, 1, 2, 3, 4].map(index => ({ kind: 'surface', index }));
  assert.equal(anchorsLabel(many), 'S0 · S1 · S2 等 5 处');
  assert.equal(anchorsLabel(many.slice(0, 2)), 'S0 · S1');
});

test('adding refuses without a model, duplicates, comments on add and the thirteenth anchor', () => {
  assert.match(addDraft(emptyQuestionContext, { kind: 'surface', index: 1 }, null).error, /没有可提问的模型/);
  assert.match(addDraft(emptyQuestionContext, { kind: 'surface', index: 1 }, reference({ model_id: null })).error, /没有可提问的模型/);
  const one = add(emptyQuestionContext, { kind: 'surface', index: 1 });
  assert.equal(one.error, null);
  assert.deepEqual(one.state.drafts[0].binding, reference());
  assert.match(add(one.state, { kind: 'surface', index: 1 }).error, /已在提问上下文中/);
  assert.equal(add(one.state, { kind: 'surface', index: 2, comment: 'pre-filled' }).error, '无法加入这一项');
  const full = fill(Array.from({ length: MAX_QUESTION_ANCHORS }, (_, index) => ({ kind: 'surface', index })));
  assert.equal(full.drafts.length, 12);
  const refused = add(full, { kind: 'stage', stage: 'stage_1' });
  assert.match(refused.error, /最多 12 处/);
  assert.equal(refused.state, full);
});

test('comments are refused beyond the limit, and remove and consume act on ids only', () => {
  let state = fill([{ kind: 'surface', index: 1 }, { kind: 'stage', stage: 'stage_1' }, { kind: 'surface', index: 2 }]);
  const [a, b, c] = state.drafts.map(draft => draft.id);
  state = setDraftComment(state, a, '入射角最大');
  assert.equal(state.drafts[0].comment, '入射角最大');
  assert.equal(setDraftComment(state, a, 'x'.repeat(MAX_ANCHOR_COMMENT + 1)), state);
  assert.equal(removeDraft(state, 'missing'), state);
  assert.deepEqual(removeDraft(state, b).drafts.map(draft => draft.id), [a, c]);
  assert.deepEqual(consumeDrafts(state, [a, c]).drafts.map(draft => draft.id), [b]);
});

test('a known model change drops model-bound drafts, a missing observation keeps all, stages follow the project', () => {
  const state = fill([{ kind: 'surface', index: 1 }, { kind: 'analysis', tab: 'mtf', result_id: 'r', field: 1 }, { kind: 'stage', stage: 'stage_2' }]);
  assert.equal(reconcileDrafts(state, null), state);
  assert.equal(reconcileDrafts(state, reference()), state);
  const revised = reconcileDrafts(state, reference({ model_revision: 3 }));
  assert.deepEqual(revised.drafts.map(draft => draft.anchor.kind), ['stage']);
  assert.equal(revised.dropped, 2);
  const otherProject = reconcileDrafts(state, reference({ model_project: 'project-b' }));
  assert.deepEqual(otherProject.drafts, []);
  assert.equal(otherProject.dropped, 3);
  assert.equal(draftsFor(state, reference({ configuration: 2 })).length, 1);
});

test('the submit target keeps order, trims comments and falls back to the original surface shape', () => {
  let state = fill([{ kind: 'stage', stage: 'stage_2' }, { kind: 'surface', index: 4 }]);
  state = setDraftComment(state, state.drafts[1].id, '  这里  ');
  state = setDraftComment(state, state.drafts[0].id, '   ');
  assert.deepEqual(buildTarget(state.drafts, 1), { kind: 'context', anchors: [
    { kind: 'stage', stage: 'stage_2' }, { kind: 'surface', index: 4, comment: '这里' }] });
  assert.deepEqual(buildTarget([], 3), { kind: 'surface', index: 3 });
  assert.equal(buildTarget([], undefined), null);
});

test('stored targets of both shapes parse, malformed ones do not', () => {
  assert.deepEqual(parseTarget({ kind: 'surface', index: 0 }), { kind: 'surface', anchors: [{ kind: 'surface', index: 0 }] });
  const context = { kind: 'context', anchors: [{ kind: 'surface', index: 2, comment: '看这里' }, { kind: 'stage', stage: 'stage_1' }] };
  assert.deepEqual(parseTarget(context), context);
  for (const target of [null, 'S1', { kind: 'surface' }, { kind: 'surface', index: 1, comment: 'x' }, { kind: 'context' },
    { kind: 'context', anchors: [] }, { kind: 'context', anchors: [{ kind: 'lens' }] },
    { kind: 'context', anchors: Array.from({ length: 13 }, (_, index) => ({ kind: 'surface', index })) }])
    assert.equal(parseTarget(target), null, JSON.stringify(target));
});
