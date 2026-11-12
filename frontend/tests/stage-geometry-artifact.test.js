import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { createWorkbenchApi } from '../src/workbench-api.js';
import { MILESTONE_STAGES, parseMilestones } from '../src/stage-milestones.js';
import { createStageGeometryArtifactController, geometryArtifactKey, milestoneArtifactRequest,
  validateGeometryArtifactReference, validateMilestoneArtifact } from '../src/stage-geometry-artifact.js';

const clone = value => structuredClone(value);
const scope = { service_instance_id: 'reading-service', project_name: 'project-a' };
const hash = value => createHash('sha256').update(value).digest('hex');
const bytes = value => Buffer.byteLength(value, 'utf8');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function fixture() {
  const optical = { service_instance_id: 'original-service', model_id: 'original-model', model_revision: 3,
    active_project: 'project-a', model_project: 'project-a', configuration: 1, source_state_version: 8 };
  const source = { project_name: 'project-a', requirements_revision: 2, proposal_hash: 'a'.repeat(64),
    proposal_revision: 1, authorization_revision: 1, ...optical };
  const layout = { status: 'ready', plane: 'YZ', units: 'mm', rays: [], issues: [],
    surfaces: [1, 2, 3].map(index => ({ index, vertex_z: index, is_image: index === 3, is_stop: index === 1,
      points: [[index, -3], [index, 0], [index, 3]] })),
    elements: [{ front: 1, back: 2, material: 'N-BK7', points: [[1, -3], [1, 3], [2, 3], [2, -3]] }] };
  const payload = { entry: { id: '00000000-0000-4000-8000-000000000001', recorded_at: '2026-11-04T06:00:00Z',
    operation_id: 'operation-a', tool: 'zemax_run_optimization', label: '历史 <script>literal</script>', source: optical },
  snapshot: { status: 'success', schema_version: 1,
    sync: { service_instance_id: optical.service_instance_id, model_id: optical.model_id, model_revision: optical.model_revision,
      active_project: optical.active_project, model_project: optical.model_project, state_version: optical.source_state_version },
    model: { name: '归档模型', project: 'project-a', configuration: 1, units: 'mm', surfaces: [0, 1, 2, 3].map(index =>
      ({ index, radius: index ? 20 : 'Infinity', thickness: index ? 2 : 'Infinity', material: index === 1 ? 'N-BK7' : '' })) },
    layout, display_layout: clone(layout) } };
  const payloadJson = canonical(payload);
  const reference = { id: '0123456789abcdef0123456789abcdef', kind: 'geometry', schema_version: 1, project_name: 'project-a',
    task_id: 'task-a', attempt_id: 'attempt-a', stage_id: 'stage_1', operation_id: 'operation-a',
    created_at: '2026-11-04T06:01:00Z', sha256: hash(payloadJson), bytes: bytes(payloadJson), source };
  const envelope = { protocol_version: 2, context: { service_instance_id: scope.service_instance_id, active_project: scope.project_name },
    operation_id: 'read-a', result: { status: 'success', stage_artifact_schema_version: 1, storage: 'persistent', source: clone(scope),
      artifact: clone(reference), payload_json: payloadJson, limits: { max_artifact_bytes: 8388608, max_project_bytes: 268435456,
        max_workspace_bytes: 536870912, max_project_artifacts: 64, max_workspace_artifacts: 256 } } };
  const operation = { operation_id: 'operation-a', tool: 'zemax_run_optimization', queued_at: '2026-11-04T05:59:30Z',
    finished_at: '2026-11-04T06:00:00Z', status: 'succeeded', result_status: 'success', observed_source: clone(optical) };
  const attempt = { id: 'attempt-a', task_id: 'task-a', stage_id: 'stage_1', declared_status: 'needs_review', status: 'needs_review',
    stale_reasons: [], started_at: '2026-11-04T05:59:00Z', reported_at: '2026-11-04T06:01:00Z',
    source: { ...source, model_revision: 2, source_state_version: 6 }, end_source: { ...source, source_state_version: 12 },
    worker_id: 'worker-a', operations: [operation], note: '历史阶段说明', geometry_artifact: clone(reference) };
  return { payload, reference, envelope, attempt };
}
function repack(value, payload = JSON.parse(value.result.payload_json)) {
  value.result.payload_json = canonical(payload);
  value.result.artifact.sha256 = hash(value.result.payload_json); value.result.artifact.bytes = bytes(value.result.payload_json);
  return value;
}
function milestoneEnvelope(attempt) {
  return { schema_version: 1, storage: 'project', service_instance_id: scope.service_instance_id, project_name: scope.project_name,
    stages: MILESTONE_STAGES.map(stage => ({ ...stage, status: stage.id === attempt.stage_id ? attempt.status : 'not_started',
      attempts: stage.id === attempt.stage_id ? [attempt] : [] })),
    limits: { max_attempts_per_task: 16, max_operations_per_report: 12, max_visible_attempts: 32 } };
}
function harness() {
  const calls = [], changes = [], { reference } = fixture();
  const api = { readMilestoneArtifact(source, options, signal) { const wait = deferred(); calls.push({ source, options, signal, ...wait }); return wait.promise; } };
  return { calls, changes, reference, controller: createStageGeometryArtifactController({ api, scope, reference, onChange: value => changes.push(value) }) };
}

test('valid original bytes and reference preserve old service and cache state while end metadata has advanced', async () => {
  const { envelope, reference, attempt } = fixture(), before = clone(envelope);
  assert.equal(validateGeometryArtifactReference(reference, attempt, 'project-a'), reference);
  const value = await validateMilestoneArtifact(envelope, scope, reference);
  assert.equal(value.artifact.source.service_instance_id, 'original-service');
  assert.equal(value.payload.snapshot.sync.state_version, 8); assert.equal(attempt.end_source.source_state_version, 12);
  assert.equal(value.payloadJson, envelope.result.payload_json); assert.deepEqual(envelope, before);
  assert.equal(parseMilestones(milestoneEnvelope(attempt)).status, 'ok');
});

test('legacy attempts without artifact remain valid; optional malformed references fail the panel', () => {
  const { attempt } = fixture(); delete attempt.geometry_artifact;
  assert.equal(parseMilestones(milestoneEnvelope(attempt)).status, 'ok');
  for (const geometry_artifact of [null, {}, [], { ...fixture().reference, bytes: 0 }]) {
    assert.equal(parseMilestones(milestoneEnvelope({ ...attempt, geometry_artifact })).status, 'invalid');
  }
});

test('stage and artifact timestamps accept explicit local offsets from trusted engine receipts', () => {
  const { attempt } = fixture();
  Object.assign(attempt, { started_at: '2026-11-04T13:59:00+08:00', reported_at: '2026-11-04T14:01:00+08:00' });
  Object.assign(attempt.operations[0], { queued_at: '2026-11-04T13:59:30+08:00', finished_at: '2026-11-04T14:00:00+08:00' });
  attempt.geometry_artifact.created_at = '2026-11-04T14:01:00+08:00';
  assert.equal(parseMilestones(milestoneEnvelope(attempt)).status, 'ok');
  attempt.started_at = '2026-11-04T13:59:00';
  assert.equal(parseMilestones(milestoneEnvelope(attempt)).status, 'invalid');
});

test('reference enforces exact fields, scalar types, budgets, source shape and bounded text', () => {
  for (const alter of [r => delete r.id, r => r.extra = 1, r => r.id = [r.id], r => r.id = 'A'.repeat(32),
    r => r.kind = 'quality', r => r.schema_version = 2, r => r.bytes = 0, r => r.bytes = true,
    r => r.bytes = 8388609, r => r.sha256 = ['a'.repeat(64)], r => r.sha256 = 'A'.repeat(64),
    r => r.stage_id = 'stage_5', r => r.task_id = 't'.repeat(101), r => r.created_at = '2026-11-04',
    r => r.source.extra = true, r => r.source.proposal_hash = 'old-hash', r => r.source.service_instance_id = 's'.repeat(513),
    r => r.source.model_id = 'm'.repeat(513), r => r.source.configuration = 0, r => r.source.model_revision = true,
    r => r.source.authorization_revision = -1, r => r.source.active_project = 'foreign', r => r.source.model_project = 'foreign']) {
    const { reference } = fixture(); alter(reference); assert.throws(() => validateGeometryArtifactReference(reference));
  }
  const { reference } = fixture(); reference.source.authorization_revision = 0;
  reference.source.service_instance_id = 's'.repeat(512); reference.source.model_id = 'm'.repeat(512);
  validateGeometryArtifactReference(reference);
});

test('reference binds exact task, attempt, stage, operation and original source without current model replacement', () => {
  for (const alter of [a => a.id = 'other', a => a.task_id = 'other', a => a.stage_id = 'stage_2',
    a => a.declared_status = 'failed', a => a.operations[0].operation_id = 'other',
    a => a.operations[0].observed_source.source_state_version = 7, a => a.end_source.model_revision = 4,
    a => a.end_source.service_instance_id = 'reading-service', a => a.end_source.proposal_hash = 'b'.repeat(64)]) {
    const { reference, attempt } = fixture(); alter(attempt);
    assert.throws(() => validateGeometryArtifactReference(reference, attempt, 'project-a'));
  }
  const { reference } = fixture(), copied = clone(reference);
  copied.source = Object.fromEntries(Object.entries(copied.source).reverse());
  assert.equal(geometryArtifactKey(reference), geometryArtifactKey(copied));
  copied.source.source_state_version++; assert.notEqual(geometryArtifactKey(reference), geometryArtifactKey(copied));
});

test('v2 service and project envelope, persistent schema, result scope and limits all fail closed', async () => {
  for (const alter of [v => v.protocol_version = 1, v => delete v.context, v => v.context.service_instance_id = 'other',
    v => v.context.active_project = 'foreign', v => delete v.context.active_project,
    v => v.result.stage_artifact_schema_version = 2, v => v.result.storage = 'session',
    v => v.result.source.project_name = 'foreign', v => v.result.source.service_instance_id = 'other',
    v => v.result.source.extra = 1, v => v.result.limits.max_artifact_bytes++, v => v.result.limits.extra = 1]) {
    const { envelope } = fixture(); alter(envelope); await assert.rejects(validateMilestoneArtifact(envelope, scope));
  }
});

test('response errors retain their codes and never become a missing or current snapshot success', async () => {
  for (const code of ['MILESTONE_ARTIFACT_NOT_FOUND', 'MILESTONE_ARTIFACT_INVALID', 'MILESTONE_ARTIFACT_UNAVAILABLE']) {
    const { envelope } = fixture(); envelope.result = { status: 'error', code, message: '归档明确错误' };
    await assert.rejects(validateMilestoneArtifact(envelope, scope), error => error.code === code && error.message === '归档明确错误');
  }
});

test('immutable full metadata must match the requested reference', async () => {
  for (const alter of [r => r.id = 'f'.repeat(32), r => r.task_id = 'another-task', r => r.attempt_id = 'another-attempt',
    r => r.created_at = '2026-11-04T07:00:00Z', r => r.source.authorization_revision = 2,
    r => r.source.source_state_version = 9, r => r.sha256 = 'b'.repeat(64), r => r.bytes++]) {
    const { envelope, reference } = fixture(); alter(reference);
    await assert.rejects(validateMilestoneArtifact(envelope, scope, reference), /请求引用/);
  }
});

test('hash and byte budgets use the original payload UTF-8 string and accept wire float spelling', async () => {
  const { envelope } = fixture();
  const payload = JSON.parse(envelope.result.payload_json); payload.snapshot.extra = { large: 1e20, whole: 1 };
  repack(envelope, payload);
  envelope.result.payload_json = envelope.result.payload_json.replace('"large":100000000000000000000', '"large":1e+20').replace('"whole":1', '"whole":1.0');
  envelope.result.artifact.bytes = bytes(envelope.result.payload_json); envelope.result.artifact.sha256 = hash(envelope.result.payload_json);
  const value = await validateMilestoneArtifact(envelope, scope);
  assert.equal(value.payload.snapshot.extra.large, 1e20); assert.equal(value.payload.snapshot.extra.whole, 1);
  assert.ok(value.payloadJson.includes('1e+20')); assert.ok(value.payloadJson.includes('1.0'));
  const changed = clone(envelope); changed.result.payload_json = changed.result.payload_json.replace('历史', '改写');
  await assert.rejects(validateMilestoneArtifact(changed, scope), /校验失败/);
  changed.result.artifact.bytes++; await assert.rejects(validateMilestoneArtifact(changed, scope), /大小/);
});

test('exact 8 MiB payload passes and one more UTF-8 byte fails without numeric reserialization', async () => {
  const { envelope } = fixture(), payload = JSON.parse(envelope.result.payload_json);
  payload.snapshot.padding = ''; repack(envelope, payload);
  payload.snapshot.padding = 'x'.repeat(8388608 - bytes(envelope.result.payload_json)); repack(envelope, payload);
  assert.equal(envelope.result.artifact.bytes, 8388608); await validateMilestoneArtifact(envelope, scope);
  payload.snapshot.padding += 'x'; repack(envelope, payload);
  await assert.rejects(validateMilestoneArtifact(envelope, scope), /引用不兼容/);
});

test('duplicate JSON keys, escaped duplicate spellings and recursively private fields are refused', async () => {
  for (const duplicate of ['"extra":1,"extra":2,', '"extra":1,"ex\\u0074ra":2,']) {
    const { envelope } = fixture(); envelope.result.payload_json = envelope.result.payload_json.replace('"snapshot":{', `"snapshot":{${duplicate}`);
    envelope.result.artifact.bytes = bytes(envelope.result.payload_json); envelope.result.artifact.sha256 = hash(envelope.result.payload_json);
    await assert.rejects(validateMilestoneArtifact(envelope, scope), /重复字段/);
  }
  for (const key of ['TOKEN', 'Lease_Token', 'ToKeN_HaSh', 'Milestone_Attempts']) {
    const { envelope } = fixture(), payload = JSON.parse(envelope.result.payload_json);
    payload.snapshot.extra = [{ deeper: { [key]: 'private' } }]; repack(envelope, payload);
    await assert.rejects(validateMilestoneArtifact(envelope, scope), /非公开/);
  }
});

test('malformed JSON and nonfinite numeric overflow are explicit errors after correct raw hashing', async () => {
  for (const raw of ['not JSON', '{"entry":', '{"entry":{},"snapshot":{"bad":1e400}}']) {
    const { envelope } = fixture(); envelope.result.payload_json = raw;
    envelope.result.artifact.bytes = bytes(raw); envelope.result.artifact.sha256 = hash(raw);
    await assert.rejects(validateMilestoneArtifact(envelope, scope), /JSON|正文格式/);
  }
});

test('payload entry and snapshot identities are exact historical bindings', async () => {
  for (const alter of [p => delete p.entry, p => p.extra = 1, p => p.entry.extra = 1, p => p.entry.id = '',
    p => p.entry.label = 'a'.repeat(257), p => p.entry.tool = 'a'.repeat(101), p => p.entry.operation_id = 'other',
    p => p.entry.source.service_instance_id = 'reading-service', p => p.entry.source.source_state_version++,
    p => p.snapshot.status = 'error', p => p.snapshot.schema_version = 2, p => p.snapshot.model.project = 'other',
    p => p.snapshot.model.configuration = 2, p => p.snapshot.sync.state_version++, p => p.snapshot.sync.extra = 1,
    p => p.snapshot.model.units = 'a'.repeat(33), p => p.snapshot.model.name = {}, p => p.snapshot.model.surfaces = [],
    p => p.snapshot.model.surfaces[1].index = 0, p => p.snapshot.model.surfaces[0].radius = null,
    p => p.snapshot.model.surfaces[1].thickness = 'unknown', p => p.snapshot.model.surfaces[0].material = {}]) {
    const { envelope } = fixture(), payload = JSON.parse(envelope.result.payload_json); alter(payload); repack(envelope, payload);
    await assert.rejects(validateMilestoneArtifact(envelope, scope));
  }
});

test('every NativeScene consumed contour field is validated before render', async () => {
  for (const alter of [l => l.status = 'unknown', l => l.plane = 'XZ', l => l.units = 'in', l => delete l.rays,
    l => l.rays.push({ id: 'not-geometric' }), l => l.issues = 'bad', l => l.validation_issues = {}, l => l.surfaces = [],
    l => l.surfaces[0].points = null, l => l.surfaces[0].points = [[0, 1]], l => l.surfaces[0].points[0] = [0, 'x'],
    l => l.surfaces[0].is_stop = 'yes', l => l.surfaces[0].is_image = null, l => l.surfaces[0].vertex_z = '0',
    l => l.surfaces[0].index = 100, l => l.surfaces.push(clone(l.surfaces[0])), l => l.elements = {},
    l => l.elements[0].front = 100, l => l.elements[0].back = 100, l => l.elements[0].points = [],
    l => l.elements[0].material = null, l => l.elements.push(clone(l.elements[0]))]) {
    const { envelope } = fixture(), payload = JSON.parse(envelope.result.payload_json);
    alter(payload.snapshot.display_layout); repack(envelope, payload);
    await assert.rejects(validateMilestoneArtifact(envelope, scope), /轮廓/);
  }
});

test('unsupported layouts retain their explanation and never fall back to a current model', async () => {
  const { envelope } = fixture(), payload = JSON.parse(envelope.result.payload_json);
  const unsupported = { status: 'unsupported', plane: 'YZ', units: 'mm', surfaces: [], elements: [], issues: ['原面型不支持二维截面'], rays: [] };
  payload.snapshot.layout = clone(unsupported); payload.snapshot.display_layout = clone(unsupported); repack(envelope, payload);
  const value = await validateMilestoneArtifact(envelope, scope); assert.equal(value.payload.snapshot.display_layout.status, 'unsupported');
  payload.snapshot.display_layout.issues = []; repack(envelope, payload); await assert.rejects(validateMilestoneArtifact(envelope, scope), /说明/);
});

test('unavailable secure digest fails explicitly rather than displaying unchecked geometry', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {} });
    await assert.rejects(validateMilestoneArtifact(fixture().envelope, scope), /无法核验/);
  } finally { Object.defineProperty(globalThis, 'crypto', previous); }
});

test('controller starts no reads on construction or same-scope updates; double clicks lock one request', async () => {
  const { controller: c, calls, reference } = harness(); c.update(clone(scope), clone(reference)); assert.equal(calls.length, 0);
  const reading = c.load(); assert.equal(calls.length, 1); assert.equal(c.view.pending, true); assert.equal(await c.load(), false);
  calls[0].resolve(fixture().envelope); assert.equal(await reading, true); assert.equal(c.view.status, 'ready');
  const view = c.view; c.update(clone(scope), clone(reference)); assert.equal(c.view, view); assert.equal(calls.length, 1); c.stop();
});

test('read failure stays explicit until user retries and successful reread remains local state', async () => {
  const { controller: c, calls } = harness(); const reading = c.load(); calls[0].reject(new Error('read failed')); await reading;
  assert.equal(c.view.status, 'failed'); assert.equal(c.view.value, null); assert.equal(c.view.error, 'read failed');
  const retry = c.load(); calls[1].resolve(fixture().envelope); await retry; assert.equal(c.view.status, 'ready');
  const reread = c.load(); assert.equal(c.view.value, null); calls[2].resolve(fixture().envelope); await reread;
  assert.equal(c.view.value.artifact.source.service_instance_id, 'original-service'); c.stop();
});

test('close aborts reads and ignores ignored-abort responses; reopen is explicit', async () => {
  const { controller: c, calls } = harness(); const reading = c.load(); c.close();
  assert.equal(calls[0].signal.aborted, true); assert.equal(c.view.value, null); assert.equal(c.view.status, 'idle');
  calls[0].resolve(fixture().envelope); assert.equal(await reading, false); assert.equal(c.view.value, null);
  const reopened = c.load(); calls[1].resolve(fixture().envelope); await reopened; assert.equal(c.view.status, 'ready'); c.stop();
});

test('scope A-B-A never resurrects a delayed historical response or late error', async () => {
  const { controller: c, calls, reference } = harness(); const oldA = c.load();
  c.update({ ...scope, service_instance_id: 'service-b' }, reference); const oldB = c.load();
  c.update(scope, reference); const current = c.load(); calls[2].resolve(fixture().envelope); await current;
  calls[0].resolve(fixture().envelope); calls[1].reject(new Error('late'));
  assert.equal(await oldA, false); assert.equal(await oldB, false); assert.equal(c.view.status, 'ready'); assert.equal(c.view.error, ''); c.stop();
});

test('disconnect and reconnect clear value and invalidate even abort-ignoring transport', async () => {
  const { controller: c, calls, reference } = harness(); const reading = c.load(); c.update(scope, reference, false);
  assert.equal(calls[0].signal.aborted, true); assert.equal(await c.load(), false); c.update(scope, reference, true);
  assert.equal(calls.length, 1); calls[0].resolve(fixture().envelope); await reading; assert.equal(c.view.value, null); c.stop();
});

test('changed reference invalidates old body immediately and requires a bound response', async () => {
  const { controller: c, calls, reference } = harness(); const first = c.load(); calls[0].resolve(fixture().envelope); await first;
  const next = { ...reference, id: 'f'.repeat(32) }; c.update(scope, next); assert.equal(c.view.value, null);
  const read = c.load(); calls[1].resolve(fixture().envelope); assert.equal(await read, false); assert.equal(c.view.status, 'failed');
  assert.equal(c.view.value, null); c.stop();
});

test('async SHA completion after close cannot publish the old geometry', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'crypto'), digest = deferred();
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { subtle: { digest: () => digest.promise } } });
    const { controller: c, calls } = harness(); const read = c.load(); calls[0].resolve(fixture().envelope);
    await new Promise(resolve => setImmediate(resolve)); assert.equal(c.view.pending, true); c.close();
    digest.resolve(await webcrypto.subtle.digest('SHA-256', Buffer.from(fixture().envelope.result.payload_json)));
    assert.equal(await read, false); assert.equal(c.view.value, null); assert.equal(c.view.status, 'idle'); c.stop();
  } finally { Object.defineProperty(globalThis, 'crypto', previous); }
});

test('stop aborts pending work, suppresses late emit and prevents new reads', async () => {
  const { controller: c, calls, changes } = harness(); const reading = c.load(), count = changes.length; c.stop();
  calls[0].resolve(fixture().envelope); await reading; assert.equal(calls[0].signal.aborted, true);
  assert.equal(changes.length, count); assert.equal(await c.load(), false); assert.equal(calls.length, 1);
});

test('API uses exactly the read-only action and immutable requested source without a state fetch', async () => {
  const calls = [], { reference, envelope } = fixture(), abort = new AbortController();
  const api = createWorkbenchApi(async (path, options) => { calls.push({ path, options }); return response(envelope); });
  assert.equal((await api.readMilestoneArtifact(scope, { artifactId: reference.id }, abort.signal)).result.artifact.id, reference.id);
  assert.equal(calls.length, 1); assert.equal(calls[0].path, '/api/tool'); assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.signal, abort.signal); assert.equal(calls[0].options.cache, 'no-store');
  assert.deepEqual(JSON.parse(calls[0].options.body), { protocol_version: 2, name: 'zemax_design_workflow', arguments: {
    action: 'milestone_artifact', project_name: 'project-a', payload: { source: scope, artifact_id: reference.id } } });
});

test('403 handshake is bounded and never substitutes a new service into artifact scope', async () => {
  const calls = [], source = clone(scope), { reference, envelope } = fixture();
  const api = createWorkbenchApi(async (path, options) => {
    calls.push({ path, options });
    if (calls.length === 1) { source.service_instance_id = 'mutated'; return response({ message: 'expired' }, 403); }
    if (path === '/api/session') return response({ protocol_version: 2, token: 'fixture-only', service_instance_id: 'restarted' });
    return response(envelope);
  });
  await api.readMilestoneArtifact(source, { artifactId: reference.id });
  assert.deepEqual(calls.map(call => call.path), ['/api/tool', '/api/session', '/api/tool']);
  assert.equal(calls[0].options.body, calls[2].options.body);
  assert.equal(JSON.parse(calls[2].options.body).arguments.payload.source.service_instance_id, 'reading-service');
  const foreign = fixture(); foreign.envelope.context.service_instance_id = 'restarted'; foreign.envelope.result.source.service_instance_id = 'restarted';
  await assert.rejects(createWorkbenchApi(async () => response(foreign.envelope)).readMilestoneArtifact(scope, { artifactId: reference.id }), /服务已变化/);
});

test('401/403/500 HTTP failures, tool errors, wrong IDs and malformed JSON remain errors', async () => {
  const { reference } = fixture();
  for (const status of [401, 403, 500]) {
    const calls = [];
    const api = createWorkbenchApi(async path => { calls.push(path); return path === '/api/session'
      ? response({ protocol_version: 2, token: 'fixture', service_instance_id: scope.service_instance_id })
      : response({ code: 'DENIED', message: 'denied' }, status); });
    await assert.rejects(api.readMilestoneArtifact(scope, { artifactId: reference.id }), error => error.code === 'DENIED');
    assert.equal(calls.length, status === 403 ? 3 : 1);
  }
  const failed = fixture().envelope; failed.result = { status: 'error', code: 'MILESTONE_ARTIFACT_NOT_FOUND', message: 'not found' };
  await assert.rejects(createWorkbenchApi(async () => response(failed)).readMilestoneArtifact(scope, { artifactId: reference.id }), error => error.code === 'MILESTONE_ARTIFACT_NOT_FOUND');
  await assert.rejects(createWorkbenchApi(async () => response(fixture().envelope)).readMilestoneArtifact(scope, { artifactId: 'f'.repeat(32) }), /请求 ID/);
  await assert.rejects(createWorkbenchApi(async () => new Response('bad JSON')).readMilestoneArtifact(scope, { artifactId: reference.id }), /无法读取/);
});

test('bad arguments never fetch and abort after async verification cannot return success', async () => {
  let reads = 0; const api = createWorkbenchApi(async () => { reads++; return response(fixture().envelope); });
  for (const artifactId of ['', '../path', 'A'.repeat(32), [fixture().reference.id], null]) {
    await assert.rejects(api.readMilestoneArtifact(scope, { artifactId }));
  }
  assert.equal(reads, 0); assert.throws(() => milestoneArtifactRequest({ ...scope, extra: 1 }, { artifactId: fixture().reference.id }));
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'crypto'), digest = deferred(), abort = new AbortController();
  try {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { subtle: { digest: () => digest.promise } } });
    const reading = api.readMilestoneArtifact(scope, { artifactId: fixture().reference.id }, abort.signal);
    await new Promise(resolve => setImmediate(resolve)); abort.abort(new Error('cancelled'));
    digest.resolve(await webcrypto.subtle.digest('SHA-256', Buffer.from(fixture().envelope.result.payload_json)));
    await assert.rejects(reading, /cancelled/);
  } finally { Object.defineProperty(globalThis, 'crypto', previous); }
});
