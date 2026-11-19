import test from 'node:test';
import assert from 'node:assert/strict';
import { taskPane, taskProvenance, taskMessage } from '../src/task-provenance.js';
import { DesignWorkflowClient } from '../src/design-workflow.js';

const clone = value => structuredClone(value);
const source = (extra = {}) => ({ service_instance_id: 'service-a', model_id: 'model-a', model_revision: 2,
  model_project: 'project-a', configuration: 1, ...extra });
function sync() {
  const identity = { service_instance_id: 'service-a', model_id: 'model-a', model_revision: 2,
    model_project: 'project-a', active_project: 'project-a', state_version: 12 };
  return { transportConnected: true, canCollaborate: true, canAnalyze: false, phase: 'synced', error: '',
    engine: { ...identity, context_valid: true, validity: 'valid', proposal_confirmed: false,
      snapshot_context: clone(identity), snapshot: { status: 'success', schema_version: 1, sync: clone(identity),
        model: { project: 'project-a', configuration: 1, surfaces: [{ index: 0 }, { index: 1 }] } } } };
}
const task = (extra = {}) => ({ request_id: 'task-a', project_name: 'project-a', kind: 'surface_question',
  status: 'pending', stale: false, user_text: '请解释这一表面', model_reference: source(),
  target: { kind: 'surface', index: 0 }, progress: '', result_text: '', requirements_revision: 1, ...extra });
const flow = (tasks = [task()]) => ({ error: '', view: { service_instance_id: 'service-a', project_name: 'project-a',
  context: { project_name: 'project-a', active_project: 'project-a' }, tasks } });
const provenance = (t = task(), s = sync()) => taskProvenance(t, s, 'project-a');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('ready task pane retains actual immutable records and does not depend on analysis or optical authorization', () => {
  const f = flow(), s = sync(), before = clone(f);
  const result = taskPane(f, s);
  assert.equal(result.status, 'ready'); assert.equal(result.tasks, f.view.tasks); assert.deepEqual(f, before);
  assert.equal(taskPane(flow([]), s).status, 'ready'); assert.deepEqual(taskPane(flow([]), s).tasks, []);
  s.engine.proposal_confirmed = false; s.canAnalyze = false; assert.equal(taskPane(f, s).status, 'ready');
});

test('explicit transport, phase, capability and workflow error hide all task cards synchronously', () => {
  for (const change of [s => s.transportConnected = false, s => delete s.transportConnected, s => s.canCollaborate = false,
    s => s.phase = 'connecting', s => s.phase = 'reconnecting', s => s.phase = 'stopped', s => s.engine = null]) {
    const s = sync(); change(s); const pane = taskPane(flow(), s); assert.equal(pane.status, 'offline'); assert.deepEqual(pane.tasks, []);
  }
  assert.equal(taskPane({ ...flow(), error: 'read failed' }, sync()).status, 'unavailable');
  assert.deepEqual(taskPane({ ...flow(), error: 'read failed' }, sync()).tasks, []);
});

test('service/project/context changes and known foreign task cannot flash under a newer synchronous scope', () => {
  for (const change of [f => f.view.service_instance_id = 'other', f => f.view.project_name = 'other',
    f => f.view.context.project_name = 'other', f => f.view.context.active_project = 'other', f => delete f.view.context]) {
    const f = flow(); change(f); assert.equal(taskPane(f, sync()).status, 'offline'); assert.deepEqual(taskPane(f, sync()).tasks, []);
  }
  for (const change of [s => s.engine.service_instance_id = 'other', s => s.engine.active_project = 'other']) {
    const s = sync(); change(s); assert.equal(taskPane(flow(), s).status, 'offline');
  }
  assert.equal(taskPane(flow([task({ project_name: 'other' })]), sync()).status, 'unavailable');
});

test('missing malformed or duplicate task arrays are unavailable rather than manufactured empty/current', () => {
  for (const tasks of [undefined, null, {}, [null], [task(), task()], [task({ request_id: '' })],
    [task({ status: 'progress' })], [task({ kind: 'unrecognized' })], [task({ stale: undefined })],
    [task({ user_text: { html: 'not a string' } })]]) {
    const f = flow(); f.view.tasks = tasks;
    const result = taskPane(f, sync()); assert.equal(result.status, 'unavailable'); assert.deepEqual(result.tasks, []);
  }
  assert.equal(taskPane({ view: null, error: '' }, sync()).status, 'unavailable');
});

test('current model identity requires both matching published snapshot contexts, never a stale flow reference', () => {
  const s = sync(); assert.equal(provenance(task(), s).relation.status, 'matching');
  assert.equal(provenance().relation.label, '引用与当前模型一致');
  for (const invalidate of [s => s.engine.context_valid = false, s => s.engine.validity = 'uncertain',
    s => s.engine.snapshot = null, s => s.engine.snapshot.status = 'error', s => s.engine.snapshot.schema_version = 2,
    s => s.engine.snapshot_context.model_revision = 1, s => s.engine.snapshot.sync.model_revision = 1,
    s => s.engine.snapshot.sync.state_version = 11, s => s.engine.snapshot_context.state_version = 11,
    s => s.engine.snapshot_context.active_project = 'other', s => s.engine.snapshot.sync.service_instance_id = 'other',
    s => s.engine.snapshot.model.project = 'other', s => s.engine.model_revision = 0,
    s => s.engine.snapshot.model.configuration = 0, s => s.transportConnected = false]) {
    const s = sync(); invalidate(s); s.model_reference = source(); assert.equal(provenance(task(), s).relation.status, 'unknown');
  }
});

test('only observed differing source fields prove historical identity, with concrete reasons', () => {
  for (const [key, value, reason] of [['service_instance_id', 'old-service', '服务实例已变化'], ['model_id', 'old-model', '模型身份已变化'],
    ['model_revision', 1, '模型修订已变化'], ['model_project', 'old-project', '活动工程已变化'], ['configuration', 2, '模型配置已变化']]) {
    const result = provenance(task({ model_reference: source({ [key]: value }) }));
    assert.equal(result.relation.status, 'historical'); assert.equal(result.relation.label, '历史模型引用'); assert.ok(result.relation.reasons.includes(reason));
    assert.equal(result.source[key], value);
  }
  const s = sync(); s.engine.state_version = 1000; s.engine.snapshot_context.state_version = 1000; s.engine.snapshot.sync.state_version = 1000;
  assert.equal(provenance(task(), s).relation.status, 'matching');
});

test('task validity and source relation remain separate; stale does not prove a model change or revive a task', () => {
  for (const t of [task({ stale: true }), task({ status: 'superseded', stale: true })]) {
    const result = provenance(t); assert.equal(result.relation.status, 'matching'); assert.deepEqual(result.relation.reasons, []); assert.ok(result.validity);
  }
  assert.equal(provenance(task({ status: 'superseded', stale: true })).validity, '任务已归档');
  assert.equal(provenance(task({ stale: true })).validity, '任务引用已过期');
});

test('HTTP native-unavailable states still show tasks while unknown model observations cannot imply equality', () => {
  const s = sync(); s.phase = 'unavailable'; s.error = 'native unavailable'; s.engine.error = s.error;
  s.engine.context_valid = false; s.engine.validity = 'unavailable'; s.engine.snapshot = null; s.engine.snapshot_context = null;
  assert.equal(taskPane(flow(), s).status, 'ready'); assert.equal(provenance(task(), s).relation.status, 'unknown');
  assert.equal(provenance(task({ model_reference: source({ model_revision: 1 }) }), s).relation.status, 'unknown');
  assert.equal(provenance(task({ model_reference: source({ service_instance_id: 'old-service' }) }), s).relation.status, 'historical');
  assert.equal(provenance(task({ model_reference: source({ model_project: 'other-project' }) }), s).relation.status, 'historical');
  s.transportConnected = false; assert.equal(taskPane(flow(), s).status, 'offline');
});

test('source projection retains recorded S0 revision configuration and only five reference keys, with no selected/current substitution', () => {
  const t = task(), s = sync(); s.selected = 7;
  const result = provenance(t, s); assert.equal(result.target, 'S0'); assert.equal(result.project, 'project-a');
  assert.equal(result.revision, 2); assert.equal(result.configuration, 1);
  t.model_reference.filepath = 'C:/not-for-source-display.zmx'; t.model_reference.lease_token = 'private';
  t.model_reference.source_state_version = 5;
  assert.deepEqual(Object.keys(provenance(t).source).sort(), ['service_instance_id', 'model_id', 'model_revision', 'model_project', 'configuration'].sort());
  assert.equal(JSON.stringify(provenance(t)).includes('private'), false); assert.equal(JSON.stringify(provenance(t)).includes('filepath'), false);
  for (const target of [null, {}, { kind: 'lens', index: 1 }, { kind: 'surface', index: -1 }, { kind: 'surface', index: true },
    { kind: 'surface', index: .5 }, { kind: 'surface', index: Number.MAX_SAFE_INTEGER + 1 }]) {
    assert.equal(provenance(task({ target })).target, '目标表面未记录或无效');
    assert.equal(provenance(task({ target })).relation.status, 'unknown');
  }
});

test('context targets show every anchor label and comment while malformed context stays unknown', () => {
  const target = { kind: 'context', anchors: [{ kind: 'surface', index: 3, comment: '入射角最大' },
    { kind: 'analysis', tab: 'mtf', result_id: 'r-1', field: 2 }, { kind: 'stage', stage: 'stage_2' }, { kind: 'surface', index: 5 }] };
  const result = provenance(task({ target }));
  assert.equal(result.target, 'S3 · MTF · F2 · 阶段 2 等 4 处');
  assert.equal(result.relation.status, 'matching');
  assert.deepEqual(result.anchors, [{ label: 'S3', comment: '入射角最大', result_id: null },
    { label: 'MTF · F2', comment: null, result_id: 'r-1' }, { label: '阶段 2', comment: null, result_id: null },
    { label: 'S5', comment: null, result_id: null }]);
  assert.deepEqual(provenance(task()).anchors, []);
  for (const bad of [{ kind: 'context', anchors: [] }, { kind: 'context', anchors: [{ kind: 'stage', stage: 'stage_9' }] }]) {
    assert.equal(provenance(task({ target: bad })).target, '目标表面未记录或无效');
    assert.equal(provenance(task({ target: bad })).relation.status, 'unknown');
  }
});

test('research and pre-model handoff intentionally unbound references are valid while surface gaps remain unknown', () => {
  for (const kind of ['initial_proposal', 'proposal_revision', 'model_handoff']) {
    const t = task({ kind, model_reference: null, target: null }); const result = provenance(t);
    assert.equal(taskPane(flow([t]), sync()).status, 'ready'); assert.equal(result.relation.status, 'unbound');
    assert.equal(result.relation.label, '未绑定模型'); assert.equal(result.target, '未绑定具体表面'); assert.equal(result.revision, null);
  }
  for (const reference of [undefined, null, {}, [], source({ model_id: '' }), source({ model_revision: true }), source({ configuration: '1' })]) {
    const result = provenance(task({ model_reference: reference })); assert.equal(result.relation.status, 'unknown');
    assert.equal(result.relation.label, '当前无法核对模型引用');
  }
});

test('one primary message reflects actual task state, using terminal result then legacy progress and preserving exact text', () => {
  const stored = '  第一行\n<script>alert("literal")</script>\n第二行  ';
  assert.equal(taskMessage(task({ progress: 'not yet claimed' })), null);
  assert.deepEqual(taskMessage(task({ status: 'claimed', progress: stored, result_text: 'ignore old terminal' })),
    { label: 'Agent 进度', text: stored, historical: false });
  for (const [status, kind, label] of [['completed', 'surface_question', 'Agent 回复'], ['completed', 'initial_proposal', '任务结果'],
    ['completed', 'model_handoff', '任务结果'], ['failed', 'surface_question', 'Agent 失败报告']]) {
    assert.deepEqual(taskMessage(task({ status, kind, result_text: stored, progress: 'duplicated progress' })), { label, text: stored, historical: false });
    assert.equal(taskMessage(task({ status, kind, result_text: '  ', progress: stored })).text, stored);
  }
  assert.deepEqual(taskMessage(task({ status: 'superseded', stale: true, result_text: 'old answer', progress: stored })),
    { label: '归档记录', text: stored, historical: true });
  assert.equal(taskMessage(task({ status: 'completed', stale: true, result_text: stored })).historical, true);
  for (const status of ['pending', 'claimed', 'completed', 'failed', 'superseded', 'progress']) {
    assert.equal(taskMessage(task({ status, result_text: ' ', progress: '' })), null);
    assert.equal(taskMessage(task({ status, result_text: {}, progress: [] })), null);
  }
});

test('helpers do not mutate frozen task/sync/view objects or infer authorization, completion or native acceptance', () => {
  const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
  const f = freeze(flow([task({ status: 'completed', stale: true, result_text: 'stored answer' })])), s = freeze(sync());
  const before = JSON.stringify({ f, s }); taskPane(f, s); taskProvenance(f.view.tasks[0], s, 'project-a'); taskMessage(f.view.tasks[0]);
  assert.equal(JSON.stringify({ f, s }), before);
});

test('late workflow result retained by existing client cannot render across newer project/service or disconnected transport', async () => {
  const pending = deferred(); let state = flow();
  const client = new DesignWorkflowClient({ workflow: () => pending.promise }, changes => state = { ...state, ...changes });
  client.reset('service-a'); const reading = client.refresh();
  const newer = sync(); newer.engine.active_project = 'project-b';
  pending.resolve(flow().view); await reading; assert.equal(state.view.project_name, 'project-a');
  assert.deepEqual(taskPane(state, newer).tasks, []);
  const disconnected = sync(); disconnected.transportConnected = false; assert.deepEqual(taskPane(state, disconnected).tasks, []);
  const replaced = sync(); replaced.engine.service_instance_id = 'service-b'; assert.deepEqual(taskPane(state, replaced).tasks, []); client.stop();
});
