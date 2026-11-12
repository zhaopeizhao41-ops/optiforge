import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { MILESTONE_STAGES, milestoneStatusLabel, parseMilestones } from '../src/stage-milestones.js';
import { convergenceAttempts, interpolateAt, loadConvergence, niceTicks, qualityArtifactKey, qualityArtifactRequest, qualityMetrics,
  spreadLabels, validateQualityArtifact, validateQualityArtifactReference } from '../src/stage-quality-artifact.js';

if (!globalThis.crypto?.subtle) globalThis.crypto = webcrypto;
const clone = value => structuredClone(value);
const scope = { service_instance_id: 'reading-service', project_name: 'project-a' };
const hash = value => createHash('sha256').update(value).digest('hex');
const bytes = value => Buffer.byteLength(value, 'utf8');
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}` : JSON.stringify(value);

function fixture() {
  const optical = { service_instance_id: 'original-service', model_id: 'original-model', model_revision: 3,
    active_project: 'project-a', model_project: 'project-a', configuration: 1, source_state_version: 9 };
  const source = { project_name: 'project-a', requirements_revision: 2, proposal_hash: 'a'.repeat(64),
    proposal_revision: 1, authorization_revision: 1, ...optical };
  const result = { analysis_schema_version: 1, status: 'partial', overall: 'ERROR', result_id: 'result-a', key: 'key-a',
    engine: 'SIMULATED <b>engine</b>', source: optical, started_at: '2026-11-04T06:00:00Z', finished_at: '2026-11-04T06:00:02Z',
    settings: { fields: [1, 2], analyses: ['mtf', 'wavefront', 'spot', 'field_curvature'], mtf_min_at_30lp_mm: 0.3 },
    items: {
      mtf: { status: 'success', curves: [
        { field_index: 1, kind: 'tangential', x: [0, 20, 40], y: [1, 0.8, 0.4] },
        { field_index: 1, kind: 'sagittal', x: [0, 20, 40], y: [1, 0.9, 0.5] },
        { field_index: 2, kind: 'tangential', x: [0, 20, 40], y: [1, 0.6, null] },
        { field_index: 2, kind: 'sagittal', x: [0, 20, 40], y: [1, 0.7, 0.3] }] },
      wavefront: { status: 'success', fields: [{ field_index: 1, grid: { rms_waves: 0.05, strehl_estimate: 0.9 } },
        { field_index: 2, grid: { rms_waves: 0.12, strehl_estimate: 0.56 } }] },
      spot: { status: 'error', message: '未完成' },
      field_curvature: { status: 'success', curves: [] } },
    verdicts: [{ analysis: 'mtf', verdict: 'FAIL', criteria: '30 lp/mm' }, { analysis: 'wavefront', verdict: 'FAIL', criteria: 'λ/14' },
      { analysis: 'spot', verdict: 'ERROR', criteria: '诊断数据' },
      { analysis: 'field_curvature', verdict: 'INFO', criteria: '畸变', evidence: { maximum: 1.25, threshold: null } }],
    model_metadata: { airy_radius_um: 3.2 } };
  const payloadJson = canonical(result);
  const reference = { id: 'fedcba9876543210fedcba9876543210', kind: 'quality', schema_version: 1, project_name: 'project-a',
    task_id: 'task-a', attempt_id: 'attempt-a', stage_id: 'stage_2', result_id: 'result-a', created_at: '2026-11-04T06:01:00Z',
    sha256: hash(payloadJson), bytes: bytes(payloadJson), source,
    analysis: { status: 'partial', overall: 'ERROR', started_at: result.started_at, finished_at: result.finished_at } };
  const envelope = { protocol_version: 2, context: { service_instance_id: scope.service_instance_id, active_project: scope.project_name },
    result: { status: 'success', stage_artifact_schema_version: 1, storage: 'persistent', source: clone(scope),
      artifact: clone(reference), payload_json: payloadJson, limits: {} } };
  const attempt = { id: 'attempt-a', task_id: 'task-a', stage_id: 'stage_2', declared_status: 'needs_review', status: 'needs_review',
    stale_reasons: [], end_source: { ...source, source_state_version: 12 }, quality_artifact: clone(reference) };
  return { result, reference, envelope, attempt };
}
const repack = (envelope, payload) => {
  envelope.result.payload_json = canonical(payload);
  envelope.result.artifact.sha256 = hash(envelope.result.payload_json); envelope.result.artifact.bytes = bytes(envelope.result.payload_json);
  return envelope;
};

test('reference validation binds kind, stage, attempt and end source but keeps each state version', () => {
  const { reference, attempt } = fixture();
  assert.equal(validateQualityArtifactReference(reference, attempt, 'project-a'), reference);
  for (const change of [r => { r.kind = 'geometry'; }, r => { r.analysis.overall = 'OK'; }, r => { r.analysis.status = 'error'; },
    r => { r.result_id = ''; }, r => { r.extra = 1; }, r => { r.bytes = 8388609; }, r => { r.source.model_revision = 0; }]) {
    const value = clone(reference); change(value);
    assert.throws(() => validateQualityArtifactReference(value, attempt, 'project-a'));
  }
  for (const change of [a => { a.stage_id = 'stage_3'; }, a => { a.declared_status = 'failed'; }, a => { a.end_source.model_revision = 4; }]) {
    const value = clone(attempt); change(value);
    assert.throws(() => validateQualityArtifactReference(reference, value, 'project-a'), /记录来源不一致/);
  }
  assert.equal(qualityArtifactKey(reference), qualityArtifactKey(clone(reference)));
  assert.throws(() => qualityArtifactRequest(scope, { artifactId: '../x' }));
});

test('archived body is hash-checked and returned verbatim, never re-judged', async () => {
  const { envelope, reference, result } = fixture();
  const value = await validateQualityArtifact(clone(envelope), scope, reference);
  assert.deepEqual(value.payload, result);
  assert.equal(value.payload.overall, 'ERROR');
  const tampered = clone(envelope); tampered.result.payload_json = tampered.result.payload_json.replace('"FAIL"', '"PASS"');
  await assert.rejects(validateQualityArtifact(tampered, scope, reference), /大小|校验/);
  const mismatched = fixture(); mismatched.result.overall = 'PASS';
  await assert.rejects(validateQualityArtifact(repack(clone(envelope), mismatched.result), scope), /归档来源不一致/);
  const leaked = fixture(); leaked.result.items.mtf.token = 'secret';
  const leakEnvelope = repack(clone(envelope), leaked.result);
  await assert.rejects(validateQualityArtifact(leakEnvelope, scope), /非公开|格式/);
  await assert.rejects(validateQualityArtifact({ ...clone(envelope), context: { service_instance_id: 'other', active_project: 'project-a' } }, scope), /服务已变化/);
  const failed = clone(envelope); failed.result = { status: 'error', code: 'MILESTONE_QUALITY_INVALID', message: '阶段像质数据不一致' };
  await assert.rejects(validateQualityArtifact(failed, scope), error => error.code === 'MILESTONE_QUALITY_INVALID');
});

test('metrics: exact or bracketed MTF only, missing data stays null, worst values per measure', () => {
  assert.equal(interpolateAt([0, 20, 40], [1, 0.8, 0.4], 30), 0.6000000000000001);
  assert.equal(interpolateAt([0, 30], [1, 0.5], 30), 0.5);
  assert.equal(interpolateAt([0, 20], [1, 0.8], 30), null);
  assert.equal(interpolateAt([0, 20, 40], [1, null, 0.4], 30), null);
  assert.equal(interpolateAt([0, 40, 20], [1, 0.5, 0.8], 30), null);
  const metrics = qualityMetrics(fixture().result);
  assert.ok(Math.abs(metrics.perField[1].mtf30 - 0.6) < 1e-12);
  assert.equal(metrics.perField[2].mtf30, null);
  assert.equal(metrics.minMtf30, null, 'one field without coverage must not yield a minimum');
  assert.equal(metrics.worstWavefrontRms, 0.12);
  assert.equal(metrics.worstSpotRms, null);
  assert.equal(metrics.maxDistortion, 1.25);
  assert.equal(metrics.mtfThreshold, 0.3);
  assert.equal(metrics.overall, 'ERROR');
});

test('attempts with quality archives validate in the milestone envelope; superseded stages read as such', () => {
  const { attempt } = fixture();
  const full = { ...attempt, source: { ...attempt.end_source, model_revision: 2, source_state_version: 6 },
    started_at: '2026-11-04T05:59:00Z', reported_at: '2026-11-04T06:01:00Z', worker_id: 'w', note: 'n',
    operations: [{ operation_id: 'op', tool: 'zemax_run_optimization', queued_at: '2026-11-04T05:59:30Z', finished_at: '2026-11-04T06:00:00Z',
      status: 'succeeded', result_status: 'success', observed_source: { ...attempt.end_source, source_state_version: 9 } }] };
  for (const key of ['project_name', 'requirements_revision', 'proposal_hash', 'proposal_revision', 'authorization_revision'])
    delete full.operations[0].observed_source[key];
  const envelope = value => ({ schema_version: 1, storage: 'project', service_instance_id: scope.service_instance_id, project_name: 'project-a',
    stages: MILESTONE_STAGES.map(stage => ({ ...stage, status: stage.id === 'stage_2' ? value.status : 'not_started',
      attempts: stage.id === 'stage_2' ? [value] : [] })), limits: { max_attempts_per_task: 16, max_operations_per_report: 12, max_visible_attempts: 32 } });
  assert.equal(parseMilestones(envelope(full)).status, 'ok');
  const broken = clone(full); broken.quality_artifact.stage_id = 'stage_1';
  assert.equal(parseMilestones(envelope(broken)).status, 'invalid');
  assert.equal(milestoneStatusLabel({ ...full, status: 'stale', stale_reasons: ['model_revision_changed'] }), '待核对 · 已被后续修改');
  assert.equal(milestoneStatusLabel({ ...full, status: 'stale', stale_reasons: ['model_revision_changed', 'approval_revoked'] }), '来源过期');
  assert.equal(milestoneStatusLabel(full), '待核对');
});

test('convergence reads each archive once, in stage order, and keeps per-stage errors', async () => {
  const { envelope, attempt } = fixture();
  const stages = MILESTONE_STAGES.map(stage => ({ ...stage, attempts: stage.id === 'stage_2' ? [attempt]
    : stage.id === 'stage_3' ? [{ ...clone(attempt), id: 'running', declared_status: 'running' }] : [] }));
  const rows = convergenceAttempts(stages);
  assert.deepEqual(rows.map(row => row.stage.id), ['stage_2']);
  const calls = [];
  const api = { readMilestoneQualityArtifact: async (source, options) => { calls.push(options.artifactId); return clone(envelope); } };
  const loaded = await loadConvergence(api, scope, rows);
  assert.deepEqual(calls, [attempt.quality_artifact.id]);
  assert.equal(loaded[0].number, 2);
  assert.equal(loaded[0].quality.payload.result_id, 'result-a');
  const failing = { readMilestoneQualityArtifact: async () => { throw new Error('断开'); } };
  const partial = await loadConvergence(failing, scope, rows);
  assert.equal(partial[0].quality, null);
  assert.match(partial[0].errors[0], /断开/);
});

test('chart helpers: round ticks cover the data, end labels never overlap', () => {
  assert.deepEqual(niceTicks(0, 4.17), [0, 2, 4, 6]);
  assert.deepEqual(niceTicks(0, 0.3), [0, 0.1, 0.2, 0.3]);
  assert.deepEqual(niceTicks(0, 38.4), [0, 20, 40]);
  const spread = spreadLabels([100, 104, NaN, 60]);
  assert.equal(spread[3], 60);
  assert.ok(spread[1] - spread[0] >= 12);
  assert.ok(Number.isNaN(spread[2]));
});
