const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && value.trim().length > 0;
const boundedText = (value, max) => text(value) && Array.from(value).length <= max;
const count = (value, min = 0) => Number.isSafeInteger(value) && value >= min;
const timestamp = value => boundedText(value, 100) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const fail = message => { throw new Error(message); };
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const scopeKeys = ['service_instance_id', 'project_name'];
const opticalKeys = ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project', 'configuration', 'source_state_version'];
const semanticKeys = ['project_name', 'requirements_revision', 'proposal_hash', 'proposal_revision', 'authorization_revision'];
const sourceKeys = [...semanticKeys, ...opticalKeys];
const metadataKeys = ['id', 'kind', 'schema_version', 'project_name', 'task_id', 'attempt_id', 'stage_id', 'operation_id', 'created_at', 'sha256', 'bytes', 'source'];
const privateKeys = new Set(['token', 'lease_token', 'token_hash', 'milestone_attempts']);
const same = (a, b, keys) => Boolean(a && b && keys.every(key => a[key] === b[key]));
const validScope = scope => exact(scope, scopeKeys) && boundedText(scope.service_instance_id, 512) && boundedText(scope.project_name, 80);
const LIMITS = Object.freeze({ max_artifact_bytes: 8388608, max_project_bytes: 268435456,
  max_workspace_bytes: 536870912, max_project_artifacts: 64, max_workspace_artifacts: 256 });

function validSource(source, project) {
  return exact(source, sourceKeys) && ['service_instance_id', 'model_id'].every(key => boundedText(source[key], 512))
    && source.project_name === project && source.active_project === project && source.model_project === project
    && ['model_revision', 'configuration', 'requirements_revision', 'proposal_revision'].every(key => count(source[key], 1))
    && count(source.authorization_revision) && count(source.source_state_version)
    && typeof source.proposal_hash === 'string' && /^[a-f0-9]{64}$/.test(source.proposal_hash);
}

export function validateGeometryArtifactReference(reference, attempt = null, project = reference?.project_name) {
  const ref = reference;
  if (!exact(ref, metadataKeys) || typeof ref.id !== 'string' || !/^[a-f0-9]{32}$/.test(ref.id) || ref.kind !== 'geometry' || ref.schema_version !== 1
      || !boundedText(project, 80) || ref.project_name !== project
      || !['task_id', 'attempt_id', 'operation_id'].every(key => boundedText(ref[key], 100))
      || !['stage_1', 'stage_2', 'stage_3', 'stage_4'].includes(ref.stage_id)
      || !timestamp(ref.created_at) || typeof ref.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(ref.sha256)
      || !count(ref.bytes, 1) || ref.bytes > LIMITS.max_artifact_bytes || !validSource(ref.source, project)) {
    fail('阶段几何归档引用不兼容');
  }
  if (attempt) {
    const last = attempt.operations?.at(-1);
    if (attempt.declared_status !== 'needs_review' || ref.task_id !== attempt.task_id || ref.attempt_id !== attempt.id
        || ref.stage_id !== attempt.stage_id || ref.operation_id !== last?.operation_id
        || !same(ref.source, attempt.end_source, sourceKeys.filter(key => key !== 'source_state_version'))
        || !same(ref.source, last?.observed_source, opticalKeys)) fail('阶段几何归档与记录来源不一致');
  }
  return ref;
}

export const geometryArtifactKey = reference => {
  validateGeometryArtifactReference(reference);
  return JSON.stringify(metadataKeys.map(key => key === 'source' ? sourceKeys.map(name => reference.source[name]) : reference[key]));
};

export function milestoneArtifactRequest(scope, { artifactId } = {}) {
  if (!validScope(scope) || typeof artifactId !== 'string' || !/^[a-f0-9]{32}$/.test(artifactId)) fail('阶段几何读取来源或归档 ID 不完整');
  return { source: { ...scope }, artifact_id: artifactId };
}

export function publicJson(value) {
  const pending = [value];
  while (pending.length) {
    const item = pending.pop();
    if (item === null || ['string', 'boolean'].includes(typeof item)) continue;
    if (typeof item === 'number' && Number.isFinite(item)) continue;
    if (typeof item !== 'object') fail('阶段几何正文格式不兼容');
    for (const [key, child] of Object.entries(item)) {
      if (privateKeys.has(key.toLowerCase())) fail('阶段几何正文含非公开字段');
      pending.push(child);
    }
  }
}

export function parsePayload(raw) {
  let value;
  try { value = JSON.parse(raw); } catch { fail('阶段几何正文不是有效 JSON'); }
  // JSON.parse accepts duplicate keys. Scan the original tokens so duplicates,
  // including escaped spellings, cannot silently replace archived fields.
  const stack = [];
  for (let index = 0; index < raw.length; index++) {
    const character = raw[index];
    if (character === '{') stack.push({ keys: new Set(), key: true });
    else if (character === '[') stack.push(null);
    else if (character === '}' || character === ']') stack.pop();
    else if (character === ',' && stack.at(-1)) stack.at(-1).key = true;
    else if (character === '"') {
      const start = index;
      while (++index < raw.length) {
        if (raw[index] === '\\') index++;
        else if (raw[index] === '"') break;
      }
      const container = stack.at(-1);
      if (container?.key) {
        const key = JSON.parse(raw.slice(start, index + 1));
        if (container.keys.has(key)) fail('阶段几何正文含重复字段');
        container.keys.add(key); container.key = false;
      }
    }
  }
  publicJson(value);
  return value;
}

const opticalNumber = value => Number.isFinite(value) || value === 'Infinity' || value === '-Infinity';
const point = value => Array.isArray(value) && value.length === 2 && value.every(Number.isFinite);
function validateLayout(layout, model) {
  if (!object(layout) || !['ready', 'unsupported'].includes(layout.status) || layout.plane !== 'YZ'
      || layout.units !== model.units || !Array.isArray(layout.surfaces) || !Array.isArray(layout.elements)
      || !Array.isArray(layout.issues) || !layout.issues.every(item => typeof item === 'string')
      || layout.validation_issues != null && (!Array.isArray(layout.validation_issues) || !layout.validation_issues.every(item => typeof item === 'string'))
      || !Array.isArray(layout.rays) || layout.rays.length) fail('阶段几何轮廓格式不兼容');
  if (layout.status === 'unsupported') {
    if (layout.surfaces.length || layout.elements.length || !layout.issues.length) fail('阶段几何轮廓不可用说明缺失');
    return;
  }
  const modelIndices = new Set(model.surfaces.map(surface => surface.index));
  const indices = new Set();
  for (const surface of layout.surfaces) {
    if (!object(surface) || !count(surface.index) || !modelIndices.has(surface.index) || indices.has(surface.index)
        || !Number.isFinite(surface.vertex_z) || typeof surface.is_image !== 'boolean' || typeof surface.is_stop !== 'boolean'
        || !Array.isArray(surface.points) || surface.points.length < 2 || !surface.points.every(point)) fail('阶段几何表面轮廓不兼容');
    indices.add(surface.index);
  }
  const fronts = new Set();
  for (const element of layout.elements) {
    if (!object(element) || !indices.has(element.front) || !indices.has(element.back) || fronts.has(element.front)
        || typeof element.material !== 'string' || !Array.isArray(element.points) || element.points.length < 3
        || !element.points.every(point)) fail('阶段几何镜片轮廓不兼容');
    fronts.add(element.front);
  }
  if (!layout.surfaces.length) fail('阶段几何轮廓为空');
}

function validatePayload(payload, artifact) {
  if (!exact(payload, ['entry', 'snapshot'])) fail('阶段几何正文结构不兼容');
  const { entry, snapshot } = payload, source = artifact.source, model = snapshot?.model;
  if (!exact(entry, ['id', 'recorded_at', 'operation_id', 'tool', 'label', 'source']) || !boundedText(entry.id, 100) || entry.operation_id !== artifact.operation_id
      || !boundedText(entry.tool, 100) || !boundedText(entry.label, 256) || !timestamp(entry.recorded_at)
      || !exact(entry.source, opticalKeys) || !same(entry.source, source, opticalKeys)
      || snapshot?.status !== 'success' || snapshot.schema_version !== 1 || !object(model)
      || model.project !== artifact.project_name || model.configuration !== source.configuration || !boundedText(model.units, 32)
      || !Array.isArray(model.surfaces) || !model.surfaces.length
      || new Set(model.surfaces.map(surface => surface?.index)).size !== model.surfaces.length
      || !model.surfaces.every(surface => object(surface) && count(surface.index) && opticalNumber(surface.radius)
        && opticalNumber(surface.thickness) && typeof surface.material === 'string')
      || model.name != null && typeof model.name !== 'string' || !exact(snapshot.sync, ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project', 'state_version'])
      || !same(snapshot.sync, source, opticalKeys.filter(key => !['configuration', 'source_state_version'].includes(key)))
      || snapshot.sync.state_version !== source.source_state_version) fail('阶段几何正文与归档来源不一致');
  if (!snapshot.display_layout && !snapshot.layout) fail('阶段几何轮廓缺失');
  if (snapshot.layout) validateLayout(snapshot.layout, model);
  if (snapshot.display_layout) validateLayout(snapshot.display_layout, model);
}

export async function sha256Hex(bytes, unavailable) {
  if (!globalThis.crypto?.subtle) fail(unavailable);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

export async function validateMilestoneArtifact(envelope, scope, expectedReference = null) {
  if (!validScope(scope) || envelope?.protocol_version !== 2 || envelope.context?.service_instance_id !== scope.service_instance_id
      || envelope.context?.active_project !== scope.project_name) {
    fail('阶段几何读取服务已变化，请重新读取当前工程');
  }
  const result = envelope.result;
  if (result?.status !== 'success') throw Object.assign(new Error(text(result?.message) ? result.message : '无法读取阶段几何快照'), { code: result?.code });
  if (result.stage_artifact_schema_version !== 1 || result.storage !== 'persistent'
      || !validScope(result.source) || !same(result.source, scope, scopeKeys)
      || !exact(result.limits, Object.keys(LIMITS)) || !Object.entries(LIMITS).every(([key, value]) => result.limits[key] === value)) {
    fail('阶段几何归档版本或读取来源不兼容');
  }
  publicJson(result);
  const artifact = validateGeometryArtifactReference(result.artifact, null, scope.project_name);
  if (expectedReference && geometryArtifactKey(artifact) !== geometryArtifactKey(expectedReference)) fail('阶段几何归档与请求引用不一致');
  if (typeof result.payload_json !== 'string') fail('阶段几何正文缺失');
  const bytes = new TextEncoder().encode(result.payload_json);
  if (bytes.byteLength !== artifact.bytes || bytes.byteLength > LIMITS.max_artifact_bytes) fail('阶段几何正文大小与归档不一致');
  if (await sha256Hex(bytes, '当前环境无法核验阶段几何归档，请使用本地工作台') !== artifact.sha256) fail('阶段几何正文校验失败，请核对归档');
  const payload = parsePayload(result.payload_json);
  validatePayload(payload, artifact);
  return { artifact, payload, payloadJson: result.payload_json, source: result.source };
}

const blank = () => ({ status: 'idle', value: null, pending: false, error: '' });
export function createStageGeometryArtifactController({ api, scope, reference, onChange = () => {} }) {
  let active = true, epoch = 0, abort, pending = false;
  let currentScope = validScope(scope) ? Object.freeze({ ...scope }) : null;
  let currentRef = reference ? structuredClone(validateGeometryArtifactReference(reference, null, currentScope?.project_name)) : null;
  let key = currentRef ? geometryArtifactKey(currentRef) : null, view = blank();
  const emit = changes => { if (active) { view = { ...view, ...changes }; onChange(view); } };
  const invalidate = () => { epoch++; abort?.abort(); pending = false; };
  return {
    get view() { return view; },
    update(scope, reference, available = true) {
      const nextScope = available && validScope(scope) ? Object.freeze({ ...scope }) : null;
      const nextRef = nextScope && reference ? structuredClone(validateGeometryArtifactReference(reference, null, nextScope.project_name)) : null;
      const nextKey = nextRef ? geometryArtifactKey(nextRef) : null;
      if (same(nextScope, currentScope, scopeKeys) && key === nextKey || !nextScope && !currentScope && key === nextKey) return;
      invalidate(); currentScope = nextScope; currentRef = nextRef; key = nextKey; emit(blank());
    },
    async load() {
      if (!active || pending || !currentScope || !currentRef) return false;
      pending = true;
      const source = currentScope, ref = currentRef, requestKey = key, generation = ++epoch;
      const request = new AbortController(); abort = request;
      const accepted = () => active && !request.signal.aborted && epoch === generation && key === requestKey && same(source, currentScope, scopeKeys);
      emit({ status: 'loading', pending: true, value: null, error: '' });
      try {
        const response = await api.readMilestoneArtifact(source, { artifactId: ref.id }, request.signal);
        if (!accepted()) return false;
        const value = await validateMilestoneArtifact(response, source, ref);
        if (!accepted()) return false;
        emit({ status: 'ready', value, error: '' }); return true;
      } catch (error) {
        if (accepted()) emit({ status: 'failed', value: null, error: error.message || '无法读取阶段几何快照' });
        return false;
      } finally {
        if (active && generation === epoch) { pending = false; emit({ pending: false }); }
      }
    },
    close() { invalidate(); emit(blank()); },
    stop() { invalidate(); active = false; },
  };
}
