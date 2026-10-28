import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkbenchSync, abortableDelay, canAdopt } from '../src/workbench-sync.js';
import { contextOf, createWorkbenchApi, validateState } from '../src/workbench-api.js';

const flush = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function state(version = 1, instance = 'instance-a', model = 'model-a', seq = version * 4) {
  const context = { service_instance_id: instance, state_version: version, model_id: model, active_project: '中文项目', model_project: '中文项目' };
  return { protocol_version: 2, ...context, context_valid: true, event_seq: seq, model_revision: version, validity: 'valid', error: null,
    snapshot_context: { ...context, model_revision: version }, snapshot: { schema_version: 1, model: { surfaces: [{ index: 1 }], value: version }, sync: context },
    tasks: { pending: [], recent: [] } };
}
function fakeApi() {
  const api = { current: state(), waits: [], imports: 0, refreshes: 0, reads: 0, subscriptions: 0, maxSubscriptions: 0 };
  api.handshake = async () => ({ service_instance_id: api.current.service_instance_id });
  api.readState = async () => { api.reads++; return structuredClone(api.current); };
  api.events = (instance, after, signal) => {
    const wait = deferred(); api.waits.push({ ...wait, after, instance }); api.subscriptions++;
    api.maxSubscriptions = Math.max(api.subscriptions, api.maxSubscriptions);
    const abort = () => wait.reject(new Error('abort'));
    signal.addEventListener('abort', abort, { once: true });
    return wait.promise.finally(() => { api.subscriptions--; signal.removeEventListener('abort', abort); });
  };
  api.notify = (extra = {}) => api.waits.shift().resolve({ service_instance_id: api.current.service_instance_id,
    events: [{ seq: api.current.event_seq, kind: 'state_changed' }], next_seq: api.current.event_seq, reset_required: false, ...extra });
  api.refresh = async () => { api.refreshes++; return { status: 'accepted' }; };
  api.importModel = async () => { api.imports++; return { result: { status: 'success' }, state: structuredClone(api.current) }; };
  return api;
}
async function running(t, api = fakeApi(), options) {
  const sync = new WorkbenchSync(api, options); t.after(() => sync.stop()); sync.start(); await flush(); return { sync, api };
}

test('adoption uses instance, state version, and atomic event cursor', () => {
  assert.equal(canAdopt(state(3), state(2), 'instance-a'), false);
  assert.equal(canAdopt(state(3), state(3, 'instance-a', 'model-a', 11), 'instance-a'), false);
  assert.equal(canAdopt(state(3), state(1, 'instance-b'), 'instance-b'), true);
  assert.equal(canAdopt(state(1, 'instance-b'), state(99), 'instance-b'), false);
});

test('fixed preview hides and blocks import; normal handshake restores it after reconnect', async (t) => {
  const api=fakeApi();let available=false;
  api.handshake=async()=>({service_instance_id:api.current.service_instance_id,capabilities:{model_import:available}});
  const {sync}=await running(t,api);
  assert.equal(sync.view.canImportModel,false);
  await sync.importModel({name:'sample.zmx'});assert.equal(api.imports,0);
  available=true;sync.reconnect();await flush();
  assert.equal(sync.view.canImportModel,true);
  await sync.importModel({name:'sample.zmx'});assert.equal(api.imports,1);
});

test('older normal services keep their existing import entry', async (t) => {
  const {sync,api}=await running(t);
  assert.equal(sync.view.canImportModel,true);
  await sync.importModel({name:'sample.zmx'});assert.equal(api.imports,1);
});
test('initial read cursor closes the read/subscribe race and live events update without refresh', async (t) => {
  const { sync, api } = await running(t);
  assert.equal(api.waits[0].after, 4);
  api.current = state(2); api.notify(); await flush();
  assert.equal(sync.view.snapshot.model.value, 2);
  assert.equal(sync.view.phase, 'synced'); assert.equal(api.refreshes, 0);
});
test('queued/running task marks the retained snapshot stale, then failure is visible', async (t) => {
  const { sync, api } = await running(t);
  api.current = state(1, 'instance-a', 'model-a', 6);
  api.current.tasks.pending = [{ operation_id: 'op', status: 'running' }];
  api.notify(); await flush(); assert.equal(sync.view.phase, 'running'); assert.equal(sync.view.stale, true);
  api.current = state(1, 'instance-a', 'model-a', 7);
  api.current.tasks.recent = [{ status: 'failed', message: 'rollback completed' }];
  api.notify(); await flush(); assert.equal(sync.view.error, 'rollback completed');
});
test('uncertain/unavailable state never becomes synced merely because a snapshot exists', async (t) => {
  const { sync, api } = await running(t);
  api.current = { ...state(2), validity: 'uncertain', error: '恢复未确认' };
  api.notify(); await flush(); assert.equal(sync.view.phase, 'unavailable'); assert.equal(sync.view.stale, true);
  api.current = { ...state(3), validity: 'unavailable', snapshot: null, snapshot_context: null };
  api.notify(); await flush(); assert.equal(sync.view.snapshot.model.value, 2); assert.equal(sync.view.stale, true);
});
test('overflow/reset fetches full cached state and advances subscription cursor', async (t) => {
  const { sync, api } = await running(t);
  api.current = state(40); api.notify({ events: [], reset_required: true }); await flush();
  assert.equal(sync.view.engine.state_version, 40); assert.equal(api.waits[0].after, 160);
});
test('disconnect preserves snapshot, then restart adopts new instance at a lower version', async (t) => {
  const retry = deferred(); const delays = [];
  const { sync, api } = await running(t, fakeApi(), { delay: (ms) => { delays.push(ms); return retry.promise; }, random: () => 0 });
  api.waits.shift().reject(new Error('offline')); await flush();
  assert.equal(sync.view.phase, 'reconnecting'); assert.equal(sync.view.snapshot.model.value, 1);
  assert.equal(sync.view.error, '连接已断开，显示上次读取');
  api.current = state(0, 'instance-b', 'model-b'); retry.resolve(); await flush();
  assert.deepEqual(delays, [1000]); assert.equal(sync.view.engine.service_instance_id, 'instance-b');
  assert.equal(sync.adopt(state(99), sync.epoch), false);
});
test('read reconnect backoff is bounded and deterministic under a controlled clock', async () => {
  const delays = []; let sync;
  sync = new WorkbenchSync({ handshake: async () => { throw new Error('offline'); } }, {
    random: () => 0, delay: async (ms) => { delays.push(ms); if (delays.length === 6) sync.stop(); },
  });
  await sync.start(); assert.deepEqual(delays, [1000, 2000, 4000, 8000, 10000, 10000]);
});
test('late response from old connection/model cannot overwrite new state', async (t) => {
  const { sync, api } = await running(t); const epoch = sync.epoch;
  api.current = state(3, 'instance-a', 'model-b'); api.notify(); await flush();
  assert.equal(sync.adopt(state(2), epoch), false);
  sync.reconnect(); await flush(); assert.equal(sync.adopt(state(9), epoch), false);
  assert.equal(sync.view.engine.model_id, 'model-b');
});
test('start is idempotent and stop/restart leaves exactly one subscription', async (t) => {
  const { sync, api } = await running(t); sync.start(); await flush();
  assert.equal(api.subscriptions, 1); sync.stop(); sync.start(); await flush();
  assert.equal(api.subscriptions, 1); assert.equal(api.maxSubscriptions, 1);
  sync.stop(); await flush(); assert.equal(api.subscriptions, 0);
});
test('resume during initial handshake reconnects even if transport returns after abort', async (t) => {
  const first = deferred(); const api = fakeApi(); let handshakes = 0;
  api.handshake = async () => ++handshakes === 1 ? first.promise : { service_instance_id: 'instance-a' };
  const { sync } = await running(t, api);
  sync.reconnect(); first.resolve({ service_instance_id: 'instance-a' }); await flush();
  assert.equal(handshakes, 2); assert.equal(api.subscriptions, 1);
  assert.equal(sync.view.phase, 'synced');
});
test('unmount ignores outstanding read even when a fake transport ignores abort', async () => {
  const read = deferred(); const api = fakeApi(); api.readState = () => read.promise;
  const sync = new WorkbenchSync(api); sync.start(); await flush(); sync.stop();
  read.resolve(state(3)); await flush(); assert.equal(sync.view.snapshot, null); assert.equal(api.subscriptions, 0);
});
test('import reply uses same adoption rule; a network failure never replays import', async (t) => {
  const { sync, api } = await running(t); const reply = deferred();
  api.importModel = async (file, context) => { api.imports++; assert.deepEqual(context, contextOf(state())); return reply.promise; };
  const importing = sync.importModel({ name: 'sample.zmx' });
  api.current = state(3); api.notify(); await flush();
  reply.resolve({ result: { status: 'success' }, state: state(2) }); await importing;
  assert.equal(sync.view.engine.state_version, 3);
  api.importModel = async () => { api.imports++; throw new Error('结果未知，请刷新核对'); };
  await sync.importModel({ name: 'sample.zmx' });
  assert.equal(api.imports, 2); assert.equal(sync.view.error, '结果未知，请刷新核对');
});
test('aborting retry delay cleans its timer promptly', async () => {
  const abort = new AbortController(); const waiting = abortableDelay(20000, abort.signal); abort.abort(new Error('unmounted'));
  await assert.rejects(waiting, /unmounted/);
});

const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => value });
test('API validates independent contracts and rejects mismatched snapshot versions', () => {
  validateState(state());
  assert.throws(() => validateState({ ...state(), protocol_version: 1 }));
  assert.throws(() => validateState({ ...state(), snapshot_context: { state_version: 99 } }));
});
test('API safely encodes Chinese context header and transmits context fields only', async () => {
  const calls = [];
  const api = createWorkbenchApi(async (path, options) => {
    calls.push({ path, options });
    // Construct real Headers: any accidental non-ASCII header throws in Node/browser.
    new Headers(options.headers);
    if (path === '/api/session') return response({ protocol_version: 2, token: 'token', service_instance_id: 'instance-a' });
    return response({ protocol_version: 2, operation_id: 'op', result: { status: 'success' }, context: contextOf(state()), state: state(2) });
  });
  await api.handshake(); await api.importModel({ name: '中文模型.zmx', size: 9 }, state());
  const headers = calls[1].options.headers;
  assert.equal(JSON.parse(decodeURIComponent(headers['X-Expected-Context'])).active_project, '中文项目');
  assert.deepEqual(Object.keys(JSON.parse(decodeURIComponent(headers['X-Expected-Context']))), Object.keys(contextOf(state())));
});
test('403 renews read credentials once, but import/transport errors are never retried', async () => {
  const counts = { session: 0, read: 0, import: 0 };
  const api = createWorkbenchApi(async (path) => {
    if (path === '/api/session') { counts.session++; return response({ protocol_version: 2, token: 'token', service_instance_id: 'instance-a' }); }
    if (path === '/api/state') return ++counts.read === 1 ? response({}, 403) : response(state());
    counts.import++; return response({}, 403);
  });
  await api.handshake(); await api.readState();
  await assert.rejects(api.importModel({ name: 's.zmx', size: 1 }, state()), /结果未知/);
  assert.deepEqual(counts, { session: 2, read: 2, import: 1 });
});
test('API import with malformed result is reported without replay', async () => {
  let count = 0;
  const api = createWorkbenchApi(async () => { count++; return response({ protocol_version: 2, status: 'success' }); });
  await assert.rejects(api.importModel({ name: 's.zmx', size: 1 }, state())); assert.equal(count, 1);
});
