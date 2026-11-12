const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && value.trim().length > 0;
const boundedText = (value, maximum, required = false) => typeof value === 'string'
  && [...value].length <= maximum && (!required || text(value));
const timestamp = value => Number.isFinite(value) && value >= 0;
const counter = (value, minimum = 0) => Number.isSafeInteger(value) && value >= minimum;
const scopeKeys = ['service_instance_id', 'project_name'];
const kinds = ['initial_proposal', 'proposal_revision', 'surface_question', 'model_handoff'];
const terminalStatuses = ['completed', 'failed', 'superseded'];
const privateKeys = new Set(['token', 'lease_token', 'token_hash', 'milestone_attempts']);
const RANGE = 'older_than_current_100_at_first_read';
const MAX_LIMIT = 50;
const MAX_BYTES = 1048576;
const rawResultSizes = new WeakMap();
const fail = message => { throw new Error(message); };
const exactKeys = (value, keys) => object(value) && Object.keys(value).length === keys.length
  && keys.every(key => Object.hasOwn(value, key));
const validScope = scope => exactKeys(scope, scopeKeys) && scopeKeys.every(key => text(scope[key]));
const sameScope = (a, b) => Boolean(a && b && scopeKeys.every(key => a[key] === b[key]));
const opaqueCursor = value => typeof value === 'string' && value.length > 0 && value.length <= 1024
  && /^[A-Za-z0-9_-]+$/.test(value);

export function designTaskHistoryRequest(scope, { limit = 20, cursor = null } = {}) {
  if (!validScope(scope)) fail('历史读取来源不完整，请重新读取当前工程');
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT || cursor !== null && !opaqueCursor(cursor)) {
    fail('历史分页参数不兼容');
  }
  return { source: { ...scope }, limit, cursor };
}

function validatePublicJson(value) {
  const pending = [value], seen = new Set();
  while (pending.length) {
    const item = pending.pop();
    if (item === null || ['string', 'boolean'].includes(typeof item)) continue;
    if (typeof item === 'number' && Number.isFinite(item)) continue;
    if (typeof item !== 'object' || seen.has(item)) fail('历史任务数据格式不兼容');
    seen.add(item);
    for (const [key, child] of Object.entries(item)) {
      if (privateKeys.has(key.toLowerCase())) fail('历史响应含非公开任务字段，已停止显示');
      pending.push(child);
    }
  }
}

function resultBytes(value) {
  const encoded = JSON.stringify(value);
  let separators = 0, quoted = false, escaped = false;
  for (const character of encoded) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
    } else if (character === '"') quoted = true;
    else if (character === ',' || character === ':') separators++;
  }
  // The server uses Python's default comma/colon separators, each followed by
  // one space, and ensure_ascii=False. Punctuation inside text is untouched.
  return new TextEncoder().encode(encoded).byteLength + separators;
}

function stringEnd(raw, start) {
  let escaped = false;
  for (let index = start + 1; index < raw.length; index++) {
    if (escaped) escaped = false;
    else if (raw[index] === '\\') escaped = true;
    else if (raw[index] === '"') return index + 1;
  }
  fail('任务历史响应格式不兼容');
}

function valueEnd(raw, start) {
  let depth = 0;
  for (let index = start; index < raw.length; index++) {
    const character = raw[index];
    if (character === '"') index = stringEnd(raw, index) - 1;
    else if (character === '{' || character === '[') depth++;
    else if (character === '}' || character === ']') {
      if (depth === 0) return index;
      depth--;
    } else if (character === ',' && depth === 0) return index;
  }
  fail('任务历史响应格式不兼容');
}

// Only this read needs exact result bytes: parsing then re-serializing numbers
// loses Python's original float spelling (for example 1e+20 and 1.0).
export async function decodeDesignTaskHistoryResponse(response) {
  const raw = await response.text(), value = JSON.parse(raw);
  if (!object(value)) fail('任务历史响应格式不兼容');
  const skipSpace = start => { while (/\s/.test(raw[start] || '') && start < raw.length) start++; return start; };
  let index = skipSpace(0) + 1, size = null;
  while (true) {
    index = skipSpace(index);
    if (raw[index] === '}') break;
    const end = stringEnd(raw, index), key = JSON.parse(raw.slice(index, end));
    const start = skipSpace(skipSpace(end) + 1), stop = valueEnd(raw, start);
    if (key === 'result') {
      if (size !== null) fail('任务历史响应含重复 result');
      size = new TextEncoder().encode(raw.slice(start, stop).trimEnd()).byteLength;
    }
    index = skipSpace(stop);
    if (raw[index] === '}') break;
    index++;
  }
  if (size === null) fail('任务历史响应缺少 result');
  rawResultSizes.set(value, size);
  return value;
}

function validateTask(task, scope) {
  if (!object(task) || !boundedText(task.request_id, 100, true) || task.project_name !== scope.project_name
      || !kinds.includes(task.kind) || !terminalStatuses.includes(task.status)
      || !boundedText(task.user_text, 16000, true)
      || !['progress', 'result_text'].every(key => boundedText(task[key], 16000))
      || !counter(task.version, 1) || !counter(task.requirements_revision)
      || !object(task.requirements_snapshot) || !counter(task.requirements_snapshot.revision)
      || !timestamp(task.created_at)
      || typeof task.stale !== 'boolean' || task.lease_expired !== false) fail('较早终态任务格式不兼容');
  for (const key of ['worker_id', 'proposal_hash', 'result_proposal_hash']) {
    if (!Object.hasOwn(task, key) || task[key] !== null && typeof task[key] !== 'string') fail('较早终态任务文本不兼容');
  }
  if (!Object.hasOwn(task, 'lease_until') || task.lease_until !== null && !timestamp(task.lease_until)
      || Object.hasOwn(task, 'finished_at') && !timestamp(task.finished_at)) fail('较早终态任务时间不兼容');
  if (Object.hasOwn(task, 'invalidated_reason') && typeof task.invalidated_reason !== 'string') fail('较早终态任务文本不兼容');
  for (const key of ['model_reference', 'target']) {
    // Keep original historical identities, including incomplete legacy records.
    if (!Object.hasOwn(task, key) || task[key] !== null && !object(task[key])) fail('历史模型引用或目标格式不兼容');
  }
}

// Return the complete v2 tool envelope, like the other workbench API validators.
export function validateDesignTaskHistory(value, scope, { limit = MAX_LIMIT } = {}) {
  designTaskHistoryRequest(scope, { limit });
  if (value?.protocol_version !== 2 || !object(value.context)
      || value.context.service_instance_id !== scope.service_instance_id) fail('历史读取服务已变化，请重新读取当前工程');
  const result = value.result;
  if (!object(result)) fail('任务历史响应格式不兼容');
  if (result.status !== 'success') {
    throw Object.assign(new Error(text(result.message) ? result.message : '无法读取较早终态任务'), { code: result.code });
  }
  if (result.task_history_schema_version !== 1 || result.storage !== 'persistent' || result.range !== RANGE
      || !validScope(result.source) || !sameScope(result.source, scope)
      || !exactKeys(result.limits, ['max_limit', 'max_response_bytes'])
      || result.limits.max_limit !== MAX_LIMIT || result.limits.max_response_bytes !== MAX_BYTES) fail('任务历史版本或来源不兼容');
  validatePublicJson(result);
  if ((rawResultSizes.get(value) ?? resultBytes(result)) > MAX_BYTES) fail('任务历史响应超过读取预算');
  if (!Array.isArray(result.tasks) || result.tasks.length > limit
      || new Set(result.tasks.map(task => task?.request_id)).size !== result.tasks.length
      || typeof result.has_more !== 'boolean'
      || (result.has_more ? !opaqueCursor(result.next_cursor) || !result.tasks.length : result.next_cursor !== null)) {
    fail('任务历史分页格式不兼容');
  }
  result.tasks.forEach(task => validateTask(task, scope));
  return value;
}

const blank = scope => ({ scope, status: scope ? 'idle' : 'offline', tasks: [], loaded: false,
  hasMore: false, nextCursor: null, pending: false, error: '' });

export function createDesignTaskHistoryController({ api, scope = null, onChange = () => {} }) {
  let active = true, epoch = 0, abort, pending = false;
  let current = validScope(scope) ? Object.freeze({ ...scope }) : null;
  let view = blank(current);
  const emit = changes => { if (active) { view = { ...view, ...changes }; onChange(view); } };
  const invalidate = () => { epoch++; abort?.abort(); pending = false; };
  const controller = {
    get view() { return view; },
    update(nextScope, available = true) {
      const next = available && validScope(nextScope) ? Object.freeze({ ...nextScope }) : null;
      if (sameScope(next, current) || next === null && current === null) return;
      invalidate(); current = next; emit(blank(current));
    },
    load() { return view.loaded ? Promise.resolve(false) : read(false); },
    refresh() { return read(false); },
    more() { return read(true); },
    stop() { invalidate(); active = false; },
  };
  async function read(append) {
    if (!active || !current || pending || append && (!view.loaded || !view.hasMore || !view.nextCursor)) return false;
    pending = true;
    const source = current, generation = ++epoch, cursor = append ? view.nextCursor : null;
    const request = new AbortController(); abort = request;
    emit(append ? { status: 'loading', pending: true, error: '' }
      : { ...blank(source), status: 'loading', pending: true });
    const accepted = () => active && !request.signal.aborted && epoch === generation && sameScope(source, current)
      && (!append || view.nextCursor === cursor);
    try {
      const response = await api.readDesignTaskHistory(source, { limit: 20, cursor }, request.signal);
      if (!accepted()) return false;
      const result = validateDesignTaskHistory(response, source, { limit: 20 }).result;
      if (append && (result.tasks.some(task => view.tasks.some(old => old.request_id === task.request_id))
          || result.has_more && result.next_cursor === cursor)) fail('历史分页记录重复或游标未推进，请重新读取历史');
      emit({ status: 'ready', tasks: append ? [...view.tasks, ...result.tasks] : result.tasks,
        loaded: true, hasMore: result.has_more, nextCursor: result.next_cursor, error: '' });
      return true;
    } catch (error) {
      if (accepted()) emit({ status: 'failed', error: error.message || '无法读取较早终态任务' });
      return false;
    } finally {
      if (active && epoch === generation) { pending = false; emit({ pending: false }); }
    }
  }
  return controller;
}
