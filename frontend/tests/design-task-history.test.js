import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorkbenchApi } from '../src/workbench-api.js';
import { createDesignTaskHistoryController, decodeDesignTaskHistoryResponse, designTaskHistoryRequest,
  validateDesignTaskHistory } from '../src/design-task-history.js';

const scope = { service_instance_id: 'service-a', project_name: 'project-a' };
const clone = value => structuredClone(value);
const task = (id = 'old-a', changes = {}) => ({ request_id: id, project_name: 'project-a', kind: 'surface_question',
  status: 'completed', user_text: '历史问题', progress: '历史进度', result_text: '历史回复',
  requirements_revision: 1, requirements_snapshot: { revision: 1 }, version: 2,
  stale: true, lease_expired: false, created_at: 1700000000, finished_at: 1700000100,
  worker_id: 'old-worker', proposal_hash: null, result_proposal_hash: null, lease_until: null,
  model_reference: { service_instance_id: 'original-old-service', model_id: 'original-old-model',
    model_project: 'project-a', model_revision: 1, configuration: 1 }, target: { kind: 'surface', index: 0 }, ...changes });
const envelope = (tasks = [task()], cursor = null, source = scope) => ({ protocol_version: 2,
  operation_id: 'read-operation', context: { service_instance_id: source.service_instance_id },
  result: { status: 'success', task_history_schema_version: 1, storage: 'persistent', source: { ...source },
    range: 'older_than_current_100_at_first_read', tasks, has_more: cursor !== null, next_cursor: cursor,
    limits: { max_limit: 50, max_response_bytes: 1048576 } } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject }; };
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const wireJson = value => Array.isArray(value) ? `[${value.map(wireJson).join(', ')}]`
  : value && typeof value === 'object' ? `{${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)}: ${wireJson(item)}`).join(', ')}}`
    : JSON.stringify(value);
function harness() {
  const calls = [], changes = [];
  const api = { readDesignTaskHistory(source, options, signal) {
    const wait = deferred(); calls.push({ source, options, signal, ...wait }); return wait.promise;
  } };
  return { calls, changes, controller: createDesignTaskHistoryController({ api, scope, onChange: view => changes.push(view) }) };
}

test('terminal history validates all terminal kinds while keeping exact original model identity and text', () => {
  for (const status of ['completed', 'failed', 'superseded']) for (const kind of ['initial_proposal', 'proposal_revision', 'surface_question', 'model_handoff']) {
    const value = envelope([task('old', { status, kind, user_text: '  <script>literal</script>\n中文  ' })], 'opaque_9007199254740993123');
    const before = clone(value); assert.equal(validateDesignTaskHistory(value, scope), value);
    assert.deepEqual(value, before); assert.equal(value.result.tasks[0].model_reference.service_instance_id, 'original-old-service');
  }
  for (const model_reference of [null, {}, { service_instance_id: 'legacy' }]) {
    validateDesignTaskHistory(envelope([task('old', { model_reference })]), scope);
  }
});

test('unknown version, storage, range, source or limits fail closed', () => {
  for (const alter of [v => v.protocol_version = 1, v => delete v.context,
    v => v.context.service_instance_id = 'other', v => v.result.task_history_schema_version = 2,
    v => v.result.storage = 'session', v => v.result.range = 'all', v => v.result.source.project_name = 'other',
    v => v.result.source.service_instance_id = 'other', v => v.result.source.extra = 'other',
    v => v.result.limits.max_limit = 51, v => v.result.limits.max_response_bytes--,
    v => v.result.limits.extra = 1, v => delete v.result]) {
    const value = envelope(); alter(value); assert.throws(() => validateDesignTaskHistory(value, scope));
  }
});

test('required task fields cannot be missing or manufactured from the current source', () => {
  for (const key of ['request_id', 'project_name', 'kind', 'status', 'user_text', 'progress', 'result_text', 'version',
    'requirements_revision', 'requirements_snapshot', 'created_at', 'worker_id', 'proposal_hash', 'result_proposal_hash',
    'model_reference', 'target', 'lease_until', 'stale', 'lease_expired']) {
    const value = envelope(); delete value.result.tasks[0][key];
    assert.throws(() => validateDesignTaskHistory(value, scope), undefined, key);
  }
});

test('malformed task text, counters, status, timestamps and nullable objects are rejected', () => {
  for (const changes of [{ request_id: '' }, { request_id: 'a'.repeat(101) }, { project_name: 'foreign' }, { kind: 'unknown' },
    { status: 'pending' }, { status: 'claimed' }, { user_text: '  ' }, { user_text: '字'.repeat(16001) }, { progress: [] },
    { progress: 'p'.repeat(16001) }, { result_text: {} }, { result_text: 'r'.repeat(16001) }, { version: 0 },
    { version: true }, { version: 1.5 }, { requirements_revision: -1 }, { requirements_revision: Number.MAX_SAFE_INTEGER + 1 },
    { requirements_snapshot: {} }, { requirements_snapshot: { revision: true } }, { stale: 1 }, { lease_expired: true },
    { created_at: null }, { created_at: '1700000000' }, { created_at: -1 }, { created_at: Infinity },
    { finished_at: null }, { lease_until: false }, { worker_id: 42 }, { proposal_hash: [] }, { result_proposal_hash: {} },
    { model_reference: [] }, { target: 'S0' }, { invalidated_reason: null }]) {
    assert.throws(() => validateDesignTaskHistory(envelope([task('old', changes)]), scope), undefined, JSON.stringify(changes));
  }
  validateDesignTaskHistory(envelope([task('🙂'.repeat(100), { user_text: '🙂'.repeat(16000) })]), scope);
});

test('page length, duplicate IDs and has_more/cursor consistency are strict', () => {
  for (const alter of [v => v.result.tasks = {}, v => v.result.tasks.push(clone(v.result.tasks[0])),
    v => v.result.tasks = Array.from({ length: 51 }, (_, i) => task(String(i))), v => v.result.has_more = 1,
    v => v.result.next_cursor = 'cursor', v => v.result.next_cursor = '',
    v => Object.assign(v.result, { has_more: true, next_cursor: null }),
    v => Object.assign(v.result, { has_more: true, next_cursor: 'a=' }),
    v => Object.assign(v.result, { has_more: true, next_cursor: 'a'.repeat(1025) }),
    v => Object.assign(v.result, { tasks: [], has_more: true, next_cursor: 'opaque' })]) {
    const value = envelope(); alter(value); assert.throws(() => validateDesignTaskHistory(value, scope));
  }
  validateDesignTaskHistory(envelope([]), scope);
  const twentyOne = envelope(Array.from({ length: 21 }, (_, i) => task(String(i))));
  assert.throws(() => validateDesignTaskHistory(twentyOne, scope, { limit: 20 }));
});

test('recursive private fields are rejected regardless of casing or nesting; literal prose is unchanged', () => {
  for (const key of ['TOKEN', 'LeAsE_ToKeN', 'tOkEn_HaSh', 'milestone_attempts']) {
    const value = envelope(); value.result.tasks[0].requirements_snapshot.extra = [{ nested: { [key]: 'private' } }];
    assert.throws(() => validateDesignTaskHistory(value, scope), /非公开/);
  }
  validateDesignTaskHistory(envelope([task('old', { user_text: 'literal token, lease_token: prose is not a JSON key' })]), scope);
});

test('UTF-8 result budget includes default separator spaces and ignores quoted punctuation', () => {
  const value = envelope([task('old', { user_text: '\\",:中文'.repeat(900) })]); value.result.note = '';
  const used = new TextEncoder().encode(wireJson(value.result)).byteLength;
  value.result.note = 'n'.repeat(1048576 - used);
  assert.equal(new TextEncoder().encode(wireJson(value.result)).byteLength, 1048576);
  assert.ok(new TextEncoder().encode(JSON.stringify(value.result)).byteLength < 1048576);
  validateDesignTaskHistory(value, scope);
  value.result.note += 'x'; assert.throws(() => validateDesignTaskHistory(value, scope), /预算/);
  value.result.note = '中'.repeat(350000); assert.throws(() => validateDesignTaskHistory(value, scope), /预算/);
});

test('real raw Python float spelling accepts an exact 1 MiB result through API and controller validation', async () => {
  const value = envelope(Array.from({ length: 5 }, (_, index) => task(`old-${index}`, {
    requirements_snapshot: { revision: 1, extra: { n: 1e20, padding: 'x'.repeat(200000) } },
  })));
  const pythonResult = () => wireJson(value.result).replaceAll('"n": 100000000000000000000', '"n": 1e+20');
  const used = new TextEncoder().encode(pythonResult()).byteLength;
  value.result.tasks[0].requirements_snapshot.extra.padding += 'x'.repeat(1048576 - used);
  const resultRaw = pythonResult(); assert.equal(new TextEncoder().encode(resultRaw).byteLength, 1048576);
  const raw = `{"protocol_version": 2, "metadata": {"result": "ignore nested result, : \\\"quoted\\\""}, "result": ${resultRaw}, "context": {"service_instance_id": "service-a"}}`;
  const api = createWorkbenchApi(async () => new Response(raw));
  const result = await api.readDesignTaskHistory(scope);
  assert.equal(result.result.tasks[0].requirements_snapshot.extra.n, 1e20);
  validateDesignTaskHistory(result, scope);
  const controller = createDesignTaskHistoryController({ api, scope }); assert.equal(await controller.load(), true);
  assert.equal(controller.view.tasks.length, 5); controller.stop();
  const over = raw.replace('"result": {', '"result": { ');
  await assert.rejects(createWorkbenchApi(async () => new Response(over)).readDesignTaskHistory(scope), /预算/);
});

test('raw result location handles strings, nested containers and escaped keys but rejects duplicate top-level result', async () => {
  const resultRaw = wireJson(envelope().result);
  const raw = ` {"context": {"service_instance_id": "service-a", "nested": [{"result": "ignored"}]}, "result_text": "result: { } ,", "protocol_version": 2, "res\\u0075lt": ${resultRaw} } `;
  validateDesignTaskHistory(await decodeDesignTaskHistoryResponse(new Response(raw)), scope);
  const duplicate = raw.replace('"protocol_version": 2', `"result": ${resultRaw}, "protocol_version": 2`);
  await assert.rejects(decodeDesignTaskHistoryResponse(new Response(duplicate)), /重复 result/);
  for (const invalid of ['[]', 'null', '{"protocol_version":2}', '{"result":{} trailing}']) {
    await assert.rejects(decodeDesignTaskHistoryResponse(new Response(invalid)));
  }
});

test('read errors are preserved as errors instead of successful empty pages', () => {
  const value = envelope(); value.result = { status: 'error', code: 'WORKFLOW_HISTORY_UNAVAILABLE', message: '存储不可用' };
  assert.throws(() => validateDesignTaskHistory(value, scope), error => error.code === 'WORKFLOW_HISTORY_UNAVAILABLE' && error.message === '存储不可用');
});

test('request scope and paging arguments are strict and copied without interpreting opaque rowids', () => {
  assert.deepEqual(designTaskHistoryRequest(scope), { source: scope, limit: 20, cursor: null });
  const source = clone(scope), request = designTaskHistoryRequest(source, { limit: 50, cursor: 'opaque_9223372036854775807' });
  source.project_name = 'other'; assert.equal(request.source.project_name, 'project-a');
  for (const options of [{ limit: 0 }, { limit: 51 }, { limit: true }, { limit: 1.5 }, { limit: '20' },
    { cursor: '' }, { cursor: '=' }, { cursor: false }, { cursor: 'a'.repeat(1025) }]) {
    assert.throws(() => designTaskHistoryRequest(scope, options));
  }
  for (const source of [null, {}, { ...scope, service_instance_id: ' ' }, { ...scope, project_name: '' }, { ...scope, other: true }]) {
    assert.throws(() => designTaskHistoryRequest(source));
  }
});

test('controller never fetches automatically and double clicks share one pending lock', async () => {
  const { controller: c, calls } = harness();
  c.update(clone(scope)); assert.equal(calls.length, 0); assert.equal(c.view.status, 'idle');
  const first = c.load(); assert.equal(c.view.pending, true); assert.equal(calls.length, 1);
  assert.equal(await c.load(), false); assert.equal(await c.more(), false); assert.equal(await c.refresh(), false);
  calls[0].resolve(envelope([task()], 'page_2')); assert.equal(await first, true);
  assert.equal(c.view.tasks.length, 1); assert.equal(c.view.nextCursor, 'page_2');
  const prior = c.view; c.update(clone(scope)); assert.equal(c.view, prior); assert.equal(calls.length, 1);
  assert.equal(await c.load(), false); assert.equal(calls.length, 1); c.stop();
});

test('explicit more appends exact terminal records and explicit refresh replaces them from a null cursor', async () => {
  const { controller: c, calls } = harness();
  const first = c.load(); calls[0].resolve(envelope([task('first')], 'page_2')); await first;
  const next = c.more(); assert.equal(calls[1].options.cursor, 'page_2');
  calls[1].resolve(envelope([task('second')], 'page_3')); await next;
  assert.deepEqual(c.view.tasks.map(t => t.request_id), ['first', 'second']);
  const refresh = c.refresh(); assert.equal(calls[2].options.cursor, null); assert.deepEqual(c.view.tasks, []);
  calls[2].resolve(envelope([task('fresh')])); await refresh;
  assert.deepEqual(c.view.tasks.map(t => t.request_id), ['fresh']); assert.equal(await c.more(), false); c.stop();
});

test('initial failure stays failed and empty until an explicit retry; empty success is distinct', async () => {
  const { controller: c, calls } = harness();
  const first = c.load(); calls[0].reject(new Error('unavailable')); assert.equal(await first, false);
  assert.equal(c.view.status, 'failed'); assert.equal(c.view.loaded, false); assert.equal(c.view.error, 'unavailable');
  assert.deepEqual(c.view.tasks, []); assert.equal(c.view.nextCursor, null); c.update(clone(scope)); assert.equal(calls.length, 1);
  const retry = c.load(); calls[1].resolve(envelope([])); await retry;
  assert.equal(c.view.status, 'ready'); assert.equal(c.view.loaded, true); assert.equal(c.view.hasMore, false); c.stop();
});

test('same-source continuation failures keep rows and cursor for an explicit retry', async () => {
  const { controller: c, calls } = harness();
  const first = c.load(); calls[0].resolve(envelope([task('first')], 'page_2')); await first;
  const rows = c.view.tasks;
  const more = c.more(); calls[1].reject(new Error('transport failed')); assert.equal(await more, false);
  assert.equal(c.view.tasks, rows); assert.equal(c.view.nextCursor, 'page_2'); assert.equal(c.view.loaded, true);
  assert.equal(c.view.pending, false); assert.equal(calls.length, 2);
  const retry = c.more(); assert.equal(calls[2].options.cursor, 'page_2');
  calls[2].resolve(envelope([task('second')])); await retry;
  assert.deepEqual(c.view.tasks.map(t => t.request_id), ['first', 'second']); c.stop();
});

test('cross-page duplicate IDs, unchanged cursor and foreign source never merge into existing rows', async () => {
  const { controller: c, calls } = harness();
  const first = c.load(); calls[0].resolve(envelope([task('first')], 'page_2')); await first;
  for (const bad of [envelope([task('first')], 'page_3'), envelope([task('second')], 'page_2'),
    envelope([task('second')], null, { ...scope, service_instance_id: 'other' })]) {
    const index = calls.length, more = c.more(); calls[index].resolve(bad); assert.equal(await more, false);
    assert.deepEqual(c.view.tasks.map(t => t.request_id), ['first']); assert.equal(c.view.nextCursor, 'page_2');
    assert.equal(c.view.status, 'failed'); assert.equal(c.view.pending, false);
  }
  c.stop();
});

test('A to B to A invalidates old requests even when transport ignores abort', async () => {
  const { controller: c, calls } = harness();
  const oldA = c.load(); c.update({ ...scope, project_name: 'project-b' });
  assert.equal(calls[0].signal.aborted, true); assert.deepEqual(c.view.tasks, []);
  const oldB = c.load(); c.update(clone(scope)); const newA = c.load();
  calls[2].resolve(envelope([task('new-a')])); assert.equal(await newA, true);
  calls[0].resolve(envelope([task('late-a')])); calls[1].reject(new Error('late-b'));
  assert.equal(await oldA, false); assert.equal(await oldB, false);
  assert.deepEqual(c.view.tasks.map(t => t.request_id), ['new-a']); assert.equal(c.view.error, ''); c.stop();
});

test('disconnect, capability/unavailable loss and reconnect clear data without auto fetching or resurrecting requests', async () => {
  const { controller: c, calls } = harness();
  const first = c.load(); calls[0].resolve(envelope([task('first')], 'page_2')); await first;
  const more = c.more(); c.update(scope, false);
  assert.equal(c.view.status, 'offline'); assert.deepEqual(c.view.tasks, []); assert.equal(calls[1].signal.aborted, true);
  assert.equal(await c.more(), false); c.update(scope, true);
  assert.equal(c.view.status, 'idle'); assert.equal(c.view.loaded, false); assert.equal(calls.length, 2);
  calls[1].resolve(envelope([task('late')])); await more; assert.deepEqual(c.view.tasks, []); c.stop();
});

test('service replacement clears loaded pages; normal same-source updates retain them', async () => {
  const { controller: c, calls } = harness();
  const first = c.load(); calls[0].resolve(envelope([task('first')], 'page_2')); await first;
  const original = c.view; c.update(clone(scope)); assert.equal(c.view, original);
  c.update({ ...scope, service_instance_id: 'service-b' }); assert.deepEqual(c.view.tasks, []);
  assert.equal(c.view.nextCursor, null); assert.equal(c.view.status, 'idle'); assert.equal(calls.length, 1); c.stop();
});

test('stop aborts and prevents emissions or reads after an ignored cancellation', async () => {
  const { controller: c, calls, changes } = harness();
  const first = c.load(), count = changes.length; c.stop(); assert.equal(calls[0].signal.aborted, true);
  calls[0].resolve(envelope()); await first;
  assert.equal(changes.length, count); assert.equal(await c.refresh(), false); assert.equal(calls.length, 1);
});

test('API uses only semantic-read list POST with the frozen source and requested paging', async () => {
  const calls = [], abort = new AbortController();
  const api = createWorkbenchApi(async (path, options) => { calls.push({ path, options }); return response(envelope()); });
  const result = await api.readDesignTaskHistory(scope, { limit: 5, cursor: 'opaque_large_rowid' }, abort.signal);
  assert.equal(result.result.tasks[0].request_id, 'old-a'); assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/api/tool'); assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.cache, 'no-store'); assert.equal(calls[0].options.signal, abort.signal);
  assert.deepEqual(JSON.parse(calls[0].options.body), { protocol_version: 2, name: 'zemax_list_design_tasks',
    arguments: { project_name: 'project-a', history: { source: scope, limit: 5, cursor: 'opaque_large_rowid' } } });
});

test('API allows one 403 handshake retry with immutable original request source', async () => {
  const calls = [], source = clone(scope);
  const api = createWorkbenchApi(async (path, options) => {
    calls.push({ path, options });
    if (calls.length === 1) { source.project_name = 'mutated-after-send'; return response({ message: 'expired' }, 403); }
    if (path === '/api/session') return response({ protocol_version: 2, token: 'fixture-handshake-only', service_instance_id: 'service-a' });
    return response(envelope());
  });
  await api.readDesignTaskHistory(source);
  assert.deepEqual(calls.map(call => call.path), ['/api/tool', '/api/session', '/api/tool']);
  assert.equal(calls[0].options.body, calls[2].options.body);
  assert.equal(JSON.parse(calls[2].options.body).arguments.history.source.project_name, 'project-a');
  assert.equal(calls[2].options.headers['X-OptiForge-Token'], 'fixture-handshake-only');
});

test('restarted service is never adopted into retried history source', async () => {
  const calls = [];
  const api = createWorkbenchApi(async (path, options) => {
    calls.push({ path, options });
    if (calls.length === 1) return response({ message: 'expired' }, 403);
    if (path === '/api/session') return response({ protocol_version: 2, token: 'fixture-new-session', service_instance_id: 'restarted' });
    return response(envelope([task()], null, { ...scope, service_instance_id: 'restarted' }));
  });
  await assert.rejects(api.readDesignTaskHistory(scope), /服务已变化/);
  assert.equal(calls.length, 3); assert.equal(JSON.parse(calls[2].options.body).arguments.history.source.service_instance_id, 'service-a');
});

test('repeated 403 is bounded and 401/500 HTTP errors are not retried or mapped to empty pages', async () => {
  for (const status of [401, 403, 500]) {
    const calls = [];
    const api = createWorkbenchApi(async path => {
      calls.push(path);
      if (path === '/api/session') return response({ protocol_version: 2, token: 'fixture-only', service_instance_id: 'service-a' });
      return response({ message: 'denied', code: 'DENIED' }, status);
    });
    await assert.rejects(api.readDesignTaskHistory(scope), error => error.status === status && error.code === 'DENIED');
    assert.equal(calls.length, status === 403 ? 3 : 1);
  }
});

test('API rejects tool errors, protocol mismatch, malformed JSON and failed transport', async () => {
  const failed = envelope(); failed.result = { status: 'error', code: 'WORKFLOW_HISTORY_UNAVAILABLE', message: 'fixture unavailable' };
  await assert.rejects(createWorkbenchApi(async () => response(failed)).readDesignTaskHistory(scope), error => error.code === 'WORKFLOW_HISTORY_UNAVAILABLE');
  await assert.rejects(createWorkbenchApi(async () => response({ ...envelope(), protocol_version: 1 })).readDesignTaskHistory(scope));
  await assert.rejects(createWorkbenchApi(async () => new Response('not json')).readDesignTaskHistory(scope), /无法读取/);
  await assert.rejects(createWorkbenchApi(async () => { throw new Error('network'); }).readDesignTaskHistory(scope), /连接已断开/);
});

test('abort after fetch and invalid arguments cannot return successful history', async () => {
  const abort = new AbortController();
  const api = createWorkbenchApi(async () => { abort.abort(new Error('cancelled')); return response(envelope()); });
  await assert.rejects(api.readDesignTaskHistory(scope, {}, abort.signal), /cancelled/);
  let reads = 0;
  const noFetch = createWorkbenchApi(async () => { reads++; return response(envelope()); });
  await assert.rejects(noFetch.readDesignTaskHistory(scope, { limit: true })); assert.equal(reads, 0);
  const small = createWorkbenchApi(async () => response(envelope([task('one'), task('two')])));
  await assert.rejects(small.readDesignTaskHistory(scope, { limit: 1 }));
});
