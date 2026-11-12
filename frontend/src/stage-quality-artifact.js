import { parsePayload, publicJson, sha256Hex, validateMilestoneArtifact } from './stage-geometry-artifact.js';

const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && value.trim().length > 0;
const boundedText = (value, max) => text(value) && Array.from(value).length <= max;
const count = (value, min = 0) => Number.isSafeInteger(value) && value >= min;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const timestamp = value => boundedText(value, 100) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const fail = message => { throw new Error(message); };
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const same = (a, b, keys) => Boolean(a && b && keys.every(key => a[key] === b[key]));
const scopeKeys = ['service_instance_id', 'project_name'];
const opticalKeys = ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project', 'configuration', 'source_state_version'];
const semanticKeys = ['project_name', 'requirements_revision', 'proposal_hash', 'proposal_revision', 'authorization_revision'];
const sourceKeys = [...semanticKeys, ...opticalKeys];
const metadataKeys = ['id', 'kind', 'schema_version', 'project_name', 'task_id', 'attempt_id', 'stage_id', 'result_id',
  'created_at', 'sha256', 'bytes', 'source', 'analysis'];
const analysisKeys = ['status', 'overall', 'started_at', 'finished_at'];
const verdicts = ['PASS', 'WARN', 'FAIL', 'ERROR', 'INFO'];
const MAX_BYTES = 8388608;
const validScope = scope => exact(scope, scopeKeys) && boundedText(scope.service_instance_id, 512) && boundedText(scope.project_name, 80);

function validSource(source, project) {
  return exact(source, sourceKeys) && ['service_instance_id', 'model_id'].every(key => boundedText(source[key], 512))
    && source.project_name === project && source.active_project === project && source.model_project === project
    && ['model_revision', 'configuration', 'requirements_revision', 'proposal_revision'].every(key => count(source[key], 1))
    && count(source.authorization_revision) && count(source.source_state_version)
    && typeof source.proposal_hash === 'string' && /^[a-f0-9]{64}$/.test(source.proposal_hash);
}

export function validateQualityArtifactReference(reference, attempt = null, project = reference?.project_name) {
  const ref = reference;
  if (!exact(ref, metadataKeys) || typeof ref.id !== 'string' || !/^[a-f0-9]{32}$/.test(ref.id) || ref.kind !== 'quality'
      || ref.schema_version !== 1 || !boundedText(project, 80) || ref.project_name !== project
      || !['task_id', 'attempt_id', 'result_id'].every(key => boundedText(ref[key], 100))
      || !['stage_1', 'stage_2', 'stage_3', 'stage_4'].includes(ref.stage_id)
      || !timestamp(ref.created_at) || typeof ref.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(ref.sha256)
      || !count(ref.bytes, 1) || ref.bytes > MAX_BYTES || !validSource(ref.source, project)
      || !exact(ref.analysis, analysisKeys) || !['success', 'partial'].includes(ref.analysis.status)
      || !verdicts.includes(ref.analysis.overall) || !timestamp(ref.analysis.started_at) || !timestamp(ref.analysis.finished_at)) {
    fail('阶段像质归档引用不兼容');
  }
  if (attempt && (attempt.declared_status !== 'needs_review' || ref.task_id !== attempt.task_id || ref.attempt_id !== attempt.id
      || ref.stage_id !== attempt.stage_id
      || !same(ref.source, attempt.end_source, sourceKeys.filter(key => key !== 'source_state_version')))) {
    fail('阶段像质归档与记录来源不一致');
  }
  return ref;
}

export const qualityArtifactKey = reference => {
  validateQualityArtifactReference(reference);
  return JSON.stringify(metadataKeys.map(key => key === 'source' ? sourceKeys.map(name => reference.source[name])
    : key === 'analysis' ? analysisKeys.map(name => reference.analysis[name]) : reference[key]));
};

export function qualityArtifactRequest(scope, { artifactId } = {}) {
  if (!validScope(scope) || typeof artifactId !== 'string' || !/^[a-f0-9]{32}$/.test(artifactId)) fail('阶段像质读取来源或归档 ID 不完整');
  return { source: { ...scope }, artifact_id: artifactId };
}

function validateResult(result, artifact) {
  if (!object(result) || result.analysis_schema_version !== 1 || result.result_id !== artifact.result_id
      || !exact(result.source, opticalKeys) || !same(result.source, artifact.source, opticalKeys)
      || !analysisKeys.every(key => result[key] === artifact.analysis[key])
      || !object(result.settings) || !Array.isArray(result.settings.fields) || !object(result.items)
      || !Array.isArray(result.verdicts) || !result.verdicts.every(row => object(row) && verdicts.includes(row.verdict) && text(row.analysis))) {
    fail('阶段像质正文与归档来源不一致');
  }
  return result;
}

// Same envelope contract as the geometry archive; the body is an original analysis result.
export async function validateQualityArtifact(envelope, scope, expectedReference = null) {
  if (!validScope(scope) || envelope?.protocol_version !== 2 || envelope.context?.service_instance_id !== scope.service_instance_id
      || envelope.context?.active_project !== scope.project_name) fail('阶段像质读取服务已变化，请重新读取当前工程');
  const result = envelope.result;
  if (result?.status !== 'success') throw Object.assign(new Error(text(result?.message) ? result.message : '无法读取阶段像质归档'), { code: result?.code });
  if (result.stage_artifact_schema_version !== 1 || result.storage !== 'persistent' || !validScope(result.source)
      || !same(result.source, scope, scopeKeys)) fail('阶段像质归档版本或读取来源不兼容');
  try { publicJson(result); } catch { fail('阶段像质响应含非公开字段'); }
  const artifact = validateQualityArtifactReference(result.artifact, null, scope.project_name);
  if (expectedReference && qualityArtifactKey(artifact) !== qualityArtifactKey(expectedReference)) fail('阶段像质归档与请求引用不一致');
  if (typeof result.payload_json !== 'string') fail('阶段像质正文缺失');
  const bytes = new TextEncoder().encode(result.payload_json);
  if (bytes.byteLength !== artifact.bytes || bytes.byteLength > MAX_BYTES) fail('阶段像质正文大小与归档不一致');
  if (await sha256Hex(bytes, '当前环境无法核验阶段像质归档，请使用本地工作台') !== artifact.sha256) fail('阶段像质正文校验失败，请核对归档');
  let payload;
  try { payload = parsePayload(result.payload_json); } catch { fail('阶段像质正文格式不兼容'); }
  return { artifact, payload: validateResult(payload, artifact), source: result.source };
}

// Exact native value or bracketed linear interpolation; never the nearest point (core.analysis_data.interpolate).
export function interpolateAt(x, y, target) {
  if (!Array.isArray(x) || !Array.isArray(y) || x.length !== y.length || !x.length || !x.every(finite)) return null;
  if (x.some((value, i) => i && x[i - 1] >= value)) return null;
  for (let i = 0; i < x.length; i++) {
    if (x[i] === target) return finite(y[i]) ? y[i] : null;
    if (i && x[i - 1] < target && target < x[i]) {
      const a = y[i - 1], b = y[i];
      return finite(a) && finite(b) ? a + (b - a) * (target - x[i - 1]) / (x[i] - x[i - 1]) : null;
    }
  }
  return null;
}

const worst = values => values.length ? Math.max(...values) : null;

/** Display metrics read from the stored body. Missing data stays null; nothing is re-judged. */
export function qualityMetrics(result) {
  const items = result.items || {}, fields = result.settings.fields;
  const perField = {};
  for (const field of fields) perField[field] = { wavefrontRms: null, strehl: null, spotRms: null, mtf30: null };
  if (items.wavefront?.status === 'success') for (const row of items.wavefront.fields || []) {
    if (perField[row.field_index] && finite(row.grid?.rms_waves)) {
      perField[row.field_index].wavefrontRms = row.grid.rms_waves;
      perField[row.field_index].strehl = finite(row.grid.strehl_estimate) ? row.grid.strehl_estimate : null;
    }
  }
  if (items.spot?.status === 'success') for (const row of items.spot.fields || []) {
    if (perField[row.field_index] && finite(row.rms_radius_um)) perField[row.field_index].spotRms = row.rms_radius_um;
  }
  if (items.mtf?.status === 'success') for (const field of fields) {
    const values = (items.mtf.curves || []).filter(curve => curve.field_index === field).map(curve => interpolateAt(curve.x, curve.y, 30));
    if (values.length && values.every(finite)) perField[field].mtf30 = Math.min(...values);
  }
  const distortion = result.verdicts.find(row => row.analysis === 'field_curvature')?.evidence?.maximum;
  const threshold = result.settings.mtf_min_at_30lp_mm;
  const pick = key => fields.map(field => perField[field][key]).filter(finite);
  return {
    fields, perField, overall: result.overall,
    worstWavefrontRms: worst(pick('wavefrontRms')), worstSpotRms: worst(pick('spotRms')),
    minMtf30: pick('mtf30').length === fields.length ? Math.min(...pick('mtf30')) : null,
    maxDistortion: finite(distortion) ? distortion : null,
    mtfThreshold: finite(threshold) ? threshold : null,
    airyRadius: finite(result.model_metadata?.airy_radius_um) ? result.model_metadata.airy_radius_um : null,
    engine: result.engine,
  };
}

const blank = () => ({ status: 'idle', value: null, pending: false, error: '' });
export function createStageQualityArtifactController({ api, scope, reference, onChange = () => {} }) {
  let active = true, epoch = 0, abort, pending = false, view = blank();
  const currentScope = validScope(scope) ? Object.freeze({ ...scope }) : null;
  const currentRef = reference ? structuredClone(validateQualityArtifactReference(reference, null, currentScope?.project_name)) : null;
  const emit = changes => { if (active) { view = { ...view, ...changes }; onChange(view); } };
  const invalidate = () => { epoch++; abort?.abort(); pending = false; };
  return {
    get view() { return view; },
    async load() {
      if (!active || pending || !currentScope || !currentRef) return false;
      pending = true;
      const generation = ++epoch, request = new AbortController(); abort = request;
      const accepted = () => active && !request.signal.aborted && epoch === generation;
      emit({ status: 'loading', pending: true, value: null, error: '' });
      try {
        const response = await api.readMilestoneQualityArtifact(currentScope, { artifactId: currentRef.id }, request.signal);
        if (!accepted()) return false;
        const value = await validateQualityArtifact(response, currentScope, currentRef);
        if (!accepted()) return false;
        emit({ status: 'ready', value, error: '' }); return true;
      } catch (error) {
        if (accepted()) emit({ status: 'failed', value: null, error: error.message || '无法读取阶段像质归档' });
        return false;
      } finally {
        if (active && generation === epoch) { pending = false; emit({ pending: false }); }
      }
    },
    close() { invalidate(); emit(blank()); },
    stop() { invalidate(); active = false; },
  };
}

/** Latest reported attempt per stage that carries archives, oldest stage first. */
export function convergenceAttempts(stages) {
  return stages.map((stage, index) => ({ stage, index, attempt: stage.attempts.find(attempt => attempt.declared_status === 'needs_review'
    && (attempt.quality_artifact || attempt.geometry_artifact)) || null })).filter(row => row.attempt);
}

/** Read every listed archive in order; each read is independently validated. Never runs analysis. */
export async function loadConvergence(api, scope, rows, signal) {
  const output = [];
  for (const { stage, index, attempt } of rows) {
    const entry = { stageId: stage.id, label: stage.label, number: index + 1, attemptId: attempt.id, quality: null, geometry: null, errors: [] };
    if (attempt.quality_artifact) {
      try {
        const reference = validateQualityArtifactReference(attempt.quality_artifact, attempt, scope.project_name);
        const response = await api.readMilestoneQualityArtifact(scope, { artifactId: reference.id }, signal);
        entry.quality = await validateQualityArtifact(response, scope, reference);
      } catch (error) { if (signal?.aborted) throw error; entry.errors.push(`像质：${error.message}`); }
    }
    if (attempt.geometry_artifact) {
      try {
        const response = await api.readMilestoneArtifact(scope, { artifactId: attempt.geometry_artifact.id }, signal);
        entry.geometry = await validateMilestoneArtifact(response, scope, attempt.geometry_artifact);
      } catch (error) { if (signal?.aborted) throw error; entry.errors.push(`几何：${error.message}`); }
    }
    output.push(entry);
  }
  return output;
}

// Round tick step (1/2/5 x 10^n) covering [lo, hi] in about `count` intervals.
export function niceTicks(lo, hi, count = 3) {
  const raw = (hi - lo) / count || 1, power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map(m => m * power).find(value => value >= raw);
  const start = Math.floor(lo / step) * step, ticks = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) ticks.push(Number(v.toPrecision(12)));
  if (ticks.at(-1) < hi) ticks.push(Number((ticks.at(-1) + step).toPrecision(12)));
  return ticks;
}

// Push end labels apart so they never overlap (minimum gap in px), keeping order.
export function spreadLabels(positions, gap = 12) {
  const order = positions.map((y, i) => [y, i]).filter(([y]) => Number.isFinite(y)).sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < order.length; i++) order[i][0] = Math.max(order[i][0], order[i - 1][0] + gap);
  const out = [...positions];
  for (const [y, i] of order) out[i] = y;
  return out;
}
