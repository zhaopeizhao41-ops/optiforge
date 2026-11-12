import test from 'node:test';
import assert from 'node:assert/strict';
import { AnalysisHistoryController, analysisHistoryScope, analysisHistoryConnected, canonical,
  validateAnalysisHistory, validateAnalysisHistoryEntry, analysisComparisonReason, historicalMetrics } from '../src/workbench-analysis-history.js';
import { createWorkbenchApi } from '../src/workbench-api.js';
import { WorkbenchSync } from '../src/workbench-sync.js';

const clone = value => structuredClone(value);
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; };
const state = (extra = {}) => ({ service_instance_id: 'service-a', active_project: 'project-a', model_id: 'model-a',
  model_revision: 20, state_version: 50, analysis_history_revision: 1, proposal_confirmed: false, ...extra });
const scope = analysisHistoryScope(state());
const settings = () => ({ analyses: ['mtf', 'wavefront'], fields: [1], sample_size: '256x256', max_frequency: 100,
  wavefront_size: 128, spot_rings: 6, mtf_min_at_30lp_mm: null, distortion_max_pct: null, item_timeout_s: 120, suite_timeout_s: 600 });
const model = () => ({ units: 'mm', lens_units: 'Millimeters', system_mode: 'Sequential', configuration: 1,
  settings: { field_type: 'Angle', field_normalization: 'Radial', aperture_value: 10 },
  fields: [{ index: 1, x: 0, y: 0, weight: 1 }],
  trace_fields: [{ index: 1, x: 0, y: 0, weight: 1, is_active: true }],
  wavelengths: [{ index: 1, wavelength_um: .55, weight: 1, is_primary: true }] });
function result(id = 'a') {
  const m = model();
  return { protocol_version: 2, analysis_schema_version: 1, result_id: id, key: `cache-${id}`, status: 'success', engine: 'SIMULATED test adapter',
    finished_at: '2026-11-03T08:00:01Z', source: { service_instance_id: 'service-a', active_project: 'project-a', model_project: 'project-a',
      model_id: 'model-a', model_revision: 2, configuration: 1, source_state_version: 10 }, settings: settings(),
    comparison_context: { schema_version: 1, model: m }, model_metadata: { units: m.units, configuration: m.configuration,
      fields: clone(m.fields), wavelengths: clone(m.wavelengths), working_f_number: 3, airy_radius_um: 2 }, overall: 'INFO',
    items: { mtf: { status: 'success', coverage: { fields: [1], wavelengths: 'all' }, effective_settings: [{ field_index: 1,
      sample_size: 'S_256x256', max_frequency: 100, wavelengths: 'all native system wavelengths and weights', polarization: false, type: 'Modulation', surface: 'image' }] },
    wavefront: { status: 'success', coverage: { fields: [1], wavelengths: [1] }, fields: [{ field_index: 1, wavelength_index: 1, wavelength_um: .55,
      effective_settings: { sampling: 'S_128x128', remove_tilt: false, reference_to_primary: false, use_exit_pupil: true, polarization: 'None', surface: 'image' },
      grid: { nx: 3, ny: 3, min_x: -1, min_y: -1, dx: 1, dy: 1, unit: 'waves', coordinates: 'native row/column; pupil unit unverified',
        orientation: 'increasing row upward', statistics_source: 'native grid; piston removed; tilt retained', mask_source: 'native_nan',
        strehl_method: 'Marechal estimated', rms_waves: 99, strehl_estimate: .001 } }] } },
    verdicts: [{ analysis: 'mtf', verdict: 'INFO', criteria: 'stored MTF diagnostic', evidence: { minimum: .4, threshold: null } },
      { analysis: 'wavefront', verdict: 'PASS', criteria: 'stored wavefront verdict', evidence: { rms_waves: .03, strehl_estimate: .96,
        worst_covered_field: 1, full_field_table: true, native_statistics_verified: false } }] };
}
function entry(r = result()) {
  return { id: r.result_id, ...Object.fromEntries(['result_id', 'status', 'engine', 'finished_at', 'source', 'settings', 'overall'].map(key => [key, clone(r[key])])) };
}
const envelope = (extra = {}) => ({ protocol_version: 2, analysis_history_schema_version: 1, storage: 'session', service_instance_id: 'service-a',
  project_name: 'project-a', analysis_history_revision: 1, limits: { max_entries: 8, max_entry_bytes: 8388608, max_total_bytes: 67108864 }, ...extra });
const list = (entries = [entry()], extra = {}) => ({ ...envelope(), entries, ...extra });
const detail = (r = result(), extra = {}) => ({ ...envelope(), entry: entry(r), result: r, ...extra });
function setup(results = [result()]) {
  const calls = [], entries = results.map(entry);
  const api = { readAnalysisHistory: async (s, signal) => { calls.push(['list', s, signal]); return list(entries); },
    readAnalysisHistoryEntry: async (id, s, signal) => { calls.push(['detail', id, s, signal]); return detail(results.find(r => r.result_id === id)); } };
  return { c: new AnalysisHistoryController(api), api, calls, entries };
}

test('scoped directory rejects foreign source, incompatible envelopes, missing normalized settings and duplicates', () => {
  assert.equal(validateAnalysisHistory(list(), scope).entries.length, 1);
  for (const extra of [{ protocol_version: 1 }, { analysis_history_schema_version: 2 }, { storage: 'disk' },
    { service_instance_id: 'foreign' }, { project_name: 'foreign' }, { analysis_history_revision: -1 },
    { limits: { max_entries: 9, max_entry_bytes: 8388608, max_total_bytes: 67108864 } }]) assert.throws(() => validateAnalysisHistory(list(undefined, extra), scope));
  for (const change of [e => e.source.model_project = 'foreign', e => e.source.configuration = 0,
    e => e.source.model_revision = 0, e => e.source.source_state_version = Infinity,
    e => e.finished_at = '2026-11-03', e => e.overall = 'CURRENT_PASS', e => delete e.settings.item_timeout_s,
    e => e.settings.unrecognized = true, e => e.settings.fields = [2, 1], e => e.id = 'not-result-id']) {
    const bad = entry(); change(bad); assert.throws(() => validateAnalysisHistory(list([bad]), scope));
  }
  assert.throws(() => validateAnalysisHistory(list([entry(), entry()]), scope), /重复/);
  assert.throws(() => validateAnalysisHistory(list(Array.from({ length: 9 }, (_, i) => entry(result(String(i))))), scope));
});

test('detail identity binds immutable summary, original result, exact scope and original verdicts', () => {
  const expected = entry(); assert.equal(validateAnalysisHistoryEntry(detail(), scope, expected).entry.id, 'a');
  for (const change of [d => d.entry.finished_at = '2026-11-03T08:01:00Z', d => d.result.result_id = 'foreign',
    d => d.result.source.model_revision++, d => d.result.settings.mtf_min_at_30lp_mm = .5,
    d => d.result.engine = 'other', d => d.result.status = 'partial', d => d.result.overall = 'FAIL',
    d => d.result.verdicts[0].verdict = 'UNKNOWN', d => d.result.verdicts.push(clone(d.result.verdicts[0]))]) {
    const bad = detail(); change(bad); assert.throws(() => validateAnalysisHistoryEntry(bad, scope, expected));
  }
  const legacy = result(); delete legacy.comparison_context; legacy.status = 'partial'; legacy.items.wavefront = null;
  assert.equal(validateAnalysisHistoryEntry(detail(legacy), scope).result.status, 'partial');
});

test('comparability allows historical revisions and key ordering while rejecting same result, engine, source and settings differences', () => {
  const a = result('a'), b = result('b'); b.source.model_revision++; b.source.source_state_version++;
  b.model_metadata.working_f_number = 4; b.model_metadata.airy_radius_um = 3;
  b.settings = Object.fromEntries(Object.entries(b.settings).reverse());
  assert.equal(analysisComparisonReason(a, b), '');
  assert.equal(canonical({ a: 1, b: 2 }), canonical({ b: 2, a: 1 }));
  assert.match(analysisComparisonReason(a, a), /同一缓存/);
  for (const change of [r => r.source.model_id = 'other', r => r.source.configuration = 2, r => r.source.active_project = 'other',
    r => r.source.service_instance_id = 'other', r => r.engine = 'other', r => r.settings.mtf_min_at_30lp_mm = .5,
    r => r.settings.item_timeout_s = 60, r => r.settings.wavefront_size = 256]) {
    const other = result('b'); change(other); assert.ok(analysisComparisonReason(a, other));
  }
});

test('pair requires complete physical fields, units, wavelength weights/primary flags and normalization, with no fabricated defaults', () => {
  for (const change of [r => delete r.comparison_context, r => delete r.comparison_context.model.settings.field_type,
    r => delete r.comparison_context.model.settings.field_normalization, r => delete r.comparison_context.model.lens_units,
    r => r.comparison_context.model.settings.field_type = 'unknown', r => r.comparison_context.model.units = 'unknown',
    r => delete r.model_metadata, r => delete r.comparison_context.model.fields[0].weight,
    r => delete r.comparison_context.model.wavelengths[0].weight, r => r.comparison_context.model.wavelengths[0].is_primary = false]) {
    const a = result('a'), b = result('b'); change(a); change(b);
    assert.ok(analysisComparisonReason(a, b));
    assert.doesNotThrow(() => validateAnalysisHistoryEntry(detail(a), scope));
  }
  for (const change of [r => r.comparison_context.model.settings.field_normalization = 'Rectangular',
    r => r.comparison_context.model.units = 'cm', r => r.comparison_context.model.fields[0].y = 5,
    r => r.comparison_context.model.wavelengths[0].weight = 2, r => r.comparison_context.model.trace_fields[0].is_active = false,
    r => r.model_metadata.wavelengths[0].wavelength_um = .6]) {
    const b = result('b'); change(b); assert.ok(analysisComparisonReason(result('a'), b));
  }
});

test('effective coverage and per-field wavefront conventions must exist on both sides and match', () => {
  for (const change of [r => r.items.mtf.coverage = {}, r => r.items.mtf.effective_settings = {},
    r => r.items.mtf.effective_settings = [], r => r.items.mtf.effective_settings = '',
    r => delete r.items.mtf.effective_settings[0].polarization, r => delete r.items.wavefront.coverage.wavelengths,
    r => r.items.wavefront.fields = [], r => r.items.wavefront.fields[0].effective_settings = {},
    r => delete r.items.wavefront.fields[0].effective_settings.remove_tilt,
    r => delete r.items.wavefront.fields[0].grid.orientation, r => r.items.wavefront.fields[0].grid.dx = 0,
    r => r.items.wavefront.fields[0].wavelength_index = 2]) {
    const a = result('a'), b = result('b'); change(a); change(b); assert.ok(analysisComparisonReason(a, b));
  }
  for (const change of [r => r.items.mtf.effective_settings[0].sample_size = 'S_512x512',
    r => r.items.mtf.coverage.fields = [2], r => r.items.wavefront.fields[0].effective_settings.remove_tilt = true,
    r => r.items.wavefront.fields[0].grid.statistics_source = 'other', r => r.items.wavefront.fields[0].grid.coordinates = 'pupil',
    r => r.items.wavefront.fields[0].grid.nx = 4]) {
    const b = result('b'); change(b); assert.ok(analysisComparisonReason(result('a'), b));
  }
  const b = result('b'); b.status = 'partial'; assert.match(analysisComparisonReason(result('a'), b), /未完成/);
  b.status = 'success'; b.items.wavefront.status = 'error'; assert.match(analysisComparisonReason(result('a'), b), /未完成/);
});

test('both unknown mandatory effective/grid/engine strings block comparison while complete native coordinate caveats remain viewable', () => {
  const a = result('a'), b = result('b');
  assert.equal(analysisComparisonReason(a, b), '');
  const paths = [['engine'], ...['sample_size', 'wavelengths', 'type', 'surface'].map(key => ['items', 'mtf', 'effective_settings', 0, key]),
    ...['sampling', 'polarization', 'surface'].map(key => ['items', 'wavefront', 'fields', 0, 'effective_settings', key]),
    ...['unit', 'coordinates', 'orientation', 'statistics_source', 'mask_source', 'strehl_method'].map(key => ['items', 'wavefront', 'fields', 0, 'grid', key])];
  for (const path of paths) for (const sentinel of ['unknown', 'unavailable', 'unverified', 'n/a']) {
    const x = clone(a), y = clone(b);
    for (const r of [x, y]) { const target = path.slice(0, -1).reduce((node, key) => node[key], r); target[path.at(-1)] = sentinel; }
    assert.ok(analysisComparisonReason(x, y), `${path.join('.')}=${sentinel}`);
  }
});

test('other requested diagnostic kinds require their original key effective settings and coverage too', () => {
  const definitions = { spot: { reference: 'primary wavelength chief ray', sampling: 'hexapolar', rings: 6, rays_per_wavelength: 127,
    surface: 'image', delta_focus: 0, statistics: 'wavelength weighted valid rays', native_length_unit: 'mm' },
  ray_fan: { number_of_rays: 20, check_apertures: true, vignetted_pupil: true, reference: 'native chief ray' },
  field_curvature: { scan: '+Y continuous', distortion: 'F-Tan(theta)', reference_field: 1, ignore_vignette: false, native_length_unit: 'mm' } };
  for (const [kind, effective] of Object.entries(definitions)) {
    const a = result('a'), b = result('b');
    for (const r of [a, b]) { r.settings.analyses = [kind]; r.items = { [kind]: { status: 'success', effective_settings: clone(effective),
      coverage: { fields: kind === 'field_curvature' ? 'continuous +Y to native maximum field' : [1], wavelengths: 'all' } } }; }
    assert.equal(analysisComparisonReason(a, b), '');
    for (const field of Object.keys(effective)) {
      const x = clone(a), y = clone(b); delete x.items[kind].effective_settings[field]; delete y.items[kind].effective_settings[field];
      assert.ok(analysisComparisonReason(x, y), `${kind}.${field} absent on both sides`);
      if (typeof effective[field] === 'string') {
        x.items[kind].effective_settings[field] = 'unknown'; y.items[kind].effective_settings[field] = 'unknown';
        assert.ok(analysisComparisonReason(x, y), `${kind}.${field} unknown on both sides`);
      }
    }
    if (kind === 'field_curvature') {
      const x = clone(a), y = clone(b); x.items[kind].coverage.fields = 'unknown'; y.items[kind].coverage.fields = 'unknown';
      assert.ok(analysisComparisonReason(x, y));
    }
    a.items[kind].coverage = {}; b.items[kind].coverage = {}; assert.ok(analysisComparisonReason(a, b));
  }
});

test('metrics use stored verdict evidence only, preserve unknown verification and reject missing or unphysical values without zeros', () => {
  const r = result(); assert.deepEqual(historicalMetrics(r), { mtf: .4, rms: .03, strehl: .96, verified: false, mtfThreshold: null, worstField: 1 });
  r.verdicts[1].evidence.native_statistics_verified = true; assert.equal(historicalMetrics(r).verified, true);
  delete r.verdicts[1].evidence.native_statistics_verified; assert.equal(historicalMetrics(r).verified, null);
  for (const value of [undefined, null, '0', NaN, Infinity, -.01, 1.01]) {
    r.verdicts[0].evidence.minimum = value; r.verdicts[1].evidence.strehl_estimate = value;
    assert.equal(historicalMetrics(r).mtf, null); assert.equal(historicalMetrics(r).strehl, null);
  }
  for (const value of [undefined, null, '0', NaN, Infinity, -.01]) { r.verdicts[1].evidence.rms_waves = value; assert.equal(historicalMetrics(r).rms, null); }
  r.verdicts = []; assert.deepEqual(historicalMetrics(r), { mtf: null, rms: null, strehl: null, verified: null, mtfThreshold: null, worstField: null });
  const failed = result(); failed.items.mtf = { status: 'error' }; failed.items.wavefront = null;
  assert.equal(historicalMetrics(failed).mtf, null); assert.equal(historicalMetrics(failed).rms, null);
  const zero = result(); zero.verdicts[0].evidence.minimum = 0; zero.verdicts[1].evidence.rms_waves = 0;
  assert.equal(historicalMetrics(zero).mtf, 0); assert.equal(historicalMetrics(zero).rms, 0);
});

test('capability absent is unavailable without fallback; ordinary state events, revision/model/authorization changes do not refetch', async () => {
  const { c, calls } = setup(); c.update(state({ analysis_history_revision: undefined }), 'synced', true);
  assert.equal(c.view.status, 'unavailable'); assert.equal(calls.length, 0);
  c.update(state(), 'synced', true); await tick(); assert.equal(c.view.left.result.result_id, 'a');
  for (let i = 0; i < 30; i++) c.update(state({ event_seq: i, state_version: 100 + i, model_id: 'new-live-model', model_revision: 100,
    proposal_confirmed: false, canAnalyze: false }), 'synced', true);
  assert.deepEqual(calls.map(call => call[0]), ['list', 'detail']);
  c.update(state({ analysis_history_revision: 2 }), 'synced', true); await tick();
  assert.equal(calls.filter(call => call[0] === 'list').length, 2); assert.equal(calls.filter(call => call[0] === 'detail').length, 1); c.stop();
});

test('native observation unavailable retains scoped reads using actual WorkbenchSync adopt semantics; HTTP failure clears', async () => {
  const { c, api, calls } = setup(); const sync = new WorkbenchSync(api); sync.active = true; sync.epoch = 1; sync.instance = 'service-a';
  sync.subscribe(view => c.update(view.engine, view.phase, view.transportConnected));
  sync.adopt(state({ event_seq: 1, validity: 'unavailable', snapshot: null, error: 'native observation unavailable', tasks: { pending: [], recent: [] } }), 1);
  assert.equal(sync.view.phase, 'unavailable'); assert.equal(sync.view.error, sync.view.engine.error);
  await tick(); assert.equal(c.view.left.result.result_id, 'a'); assert.equal(calls.length, 2);
  sync.emit({ transportConnected: false, phase: 'unavailable', error: '连接已断开，显示上次读取' });
  assert.equal(c.view.status, 'offline'); assert.equal(c.view.left, null); assert.deepEqual(c.view.entries, []);
  assert.equal(analysisHistoryConnected(state(), 'connecting'), false);
  assert.equal(analysisHistoryConnected(state(), 'reconnecting'), false); c.stop();
});

test('native or recent task error with healthy HTTP permits history, but an identically worded HTTP failure clears it', async () => {
  for (const taskError of [false, true]) {
    const { c } = setup(); const failed = deferred();
    const sync = new WorkbenchSync({ handshake: async () => ({ service_instance_id: 'service-a', capabilities: {} }),
      readState: async () => state({ validity: 'unavailable', snapshot: null, event_seq: 1,
        error: taskError ? '' : 'same error', tasks: { pending: [], recent: taskError ? [{ status: 'failed', message: 'same error' }] : [] } }),
      events: async () => failed.promise }, { delay: () => new Promise(() => {}) });
    sync.subscribe(view => c.update(view.engine, view.phase, view.transportConnected)); sync.start(); await tick(); assert.ok(c.view.left);
    assert.equal(sync.view.transportConnected, true); assert.equal(sync.view.error, 'same error');
    failed.reject(new Error('same error')); await tick(); assert.equal(sync.view.phase, 'unavailable'); assert.equal(sync.view.error, 'same error');
    assert.equal(sync.view.transportConnected, false); assert.equal(c.view.status, 'offline'); assert.equal(c.view.left, null); sync.stop(); c.stop();
  }
});

test('service/project changes clear both slots synchronously and late detail/list responses cannot publish', async () => {
  for (const change of [{ active_project: 'other' }, { service_instance_id: 'other' }]) {
    const { c, api } = setup(), pending = deferred(); api.readAnalysisHistoryEntry = () => pending.promise;
    c.update(state(), 'synced', true); await tick(); api.readAnalysisHistory = () => new Promise(() => {});
    c.update(state(change), 'synced', true); assert.equal(c.view.left, null); assert.deepEqual(c.view.entries, []);
    pending.resolve(detail()); await tick(); assert.equal(c.view.left, null); c.stop();
  }
  const { c, api } = setup(), old = deferred(); api.readAnalysisHistory = () => old.promise;
  c.update(state(), 'synced', true); api.readAnalysisHistory = () => new Promise(() => {});
  c.update(state({ active_project: 'other' }), 'synced', true); old.resolve(list()); await tick(); assert.deepEqual(c.view.entries, []); c.stop();
});

test('rapid A/B selection and clearing B reject out-of-order details even when transport ignores abort', async () => {
  const { c, api } = setup([result('a'), result('b'), result('c')]), a = deferred(), b = deferred(), other = deferred();
  api.readAnalysisHistoryEntry = id => id === 'a' ? a.promise : id === 'b' ? b.promise : other.promise;
  c.update(state(), 'synced', true); await tick(); c.select('b'); b.resolve(detail(result('b'))); await tick();
  a.resolve(detail(result('a'))); await tick(); assert.equal(c.view.left.result.result_id, 'b');
  c.compare('b'); assert.equal(c.view.compare, null); c.compare('unknown'); assert.equal(c.view.compare, null);
  c.compare('c'); c.compare(null); other.resolve(detail(result('c'))); await tick(); assert.equal(c.view.right, null); assert.equal(c.view.compare, null); c.stop();
});

test('choosing current B for A clears comparison; incompatible records remain independently viewable', async () => {
  const other = result('b'); other.source.model_id = 'other-model'; const { c } = setup([result('a'), other]);
  c.update(state(), 'synced', true); await tick(); c.compare('b'); await tick();
  assert.equal(c.view.right.result.result_id, 'b'); assert.match(analysisComparisonReason(c.view.left.result, c.view.right.result), /模型/);
  c.select('b'); assert.equal(c.view.compare, null); assert.equal(c.view.right, null); await tick(); assert.equal(c.view.left.result.result_id, 'b'); c.stop();
});

test('older list cannot overwrite newest refresh and eviction clears mismatched detail in the SAME emitted view', async () => {
  const { c, api } = setup([result('a'), result('b')]); c.update(state(), 'synced', true); await tick(); c.compare('b'); await tick();
  const seen = []; c.subscribe(view => seen.push(clone(view)));
  api.readAnalysisHistory = async () => list([entry(result('b'))]); api.readAnalysisHistoryEntry = () => new Promise(() => {});
  await c.refresh();
  const adopted = seen.find(view => view.entries.length === 1 && view.selected === 'b');
  assert.ok(adopted); assert.equal(adopted.left, null); assert.equal(adopted.right, null); assert.equal(adopted.compare, null); c.stop();
  const next = setup(), first = deferred(), second = deferred(); let reads = 0;
  next.api.readAnalysisHistory = () => ++reads === 1 ? first.promise : second.promise;
  next.c.update(state(), 'synced', true); next.c.refresh(); second.resolve(list([])); await tick(); first.resolve(list()); await tick();
  assert.deepEqual(next.c.view.entries, []); assert.equal(next.c.view.left, null); next.c.stop();
});

test('directory summary change invalidates same-ID detail before adoption and late source cannot restore it', async () => {
  const { c, api } = setup(); c.update(state(), 'synced', true); await tick(); const newer = result(); newer.source.model_revision++;
  const seen = []; c.subscribe(view => seen.push(clone(view))); api.readAnalysisHistory = async () => list([entry(newer)]);
  api.readAnalysisHistoryEntry = async () => detail(); await c.refresh(); await tick();
  assert.equal(seen.find(view => view.entries[0]?.source.model_revision === 3).left, null);
  assert.equal(c.view.left, null); assert.equal(c.view.leftStatus, 'failed'); c.stop();
});

test('404 and failed directory clear stale data and recover only by explicit read, with no analysis submission', async () => {
  const { c, api, calls } = setup(); c.update(state(), 'synced', true); await tick();
  api.readAnalysisHistoryEntry = async () => { throw Object.assign(new Error('missing'), { status: 404 }); };
  c.select('a'); await tick(); assert.equal(c.view.left, null); assert.equal(c.view.leftStatus, 'failed'); assert.match(c.view.leftError, /淘汰/);
  api.readAnalysisHistory = async () => { throw new Error('offline'); }; await c.refresh(); assert.equal(c.view.status, 'failed'); assert.deepEqual(c.view.entries, []);
  api.readAnalysisHistory = async () => list(); api.readAnalysisHistoryEntry = async () => detail(); await c.refresh(); await tick(); assert.ok(c.view.left);
  assert.ok(calls.every(call => ['list', 'detail'].includes(call[0]))); c.stop();
});

test('unmount aborts requests and prevents any late published view', async () => {
  const { c, api } = setup(), pending = deferred(); let signal, emissions = 0;
  api.readAnalysisHistory = (_s, s) => { signal = s; return pending.promise; }; c.subscribe(() => emissions++);
  c.update(state(), 'synced', true); c.stop(); const frozen = emissions; assert.equal(signal.aborted, true);
  pending.resolve(list()); await tick(); assert.equal(emissions, frozen); assert.equal(c.view.left, null);
});

test('history API authenticates scoped GET only, encodes exact ID/scope and never falls back to live/unscoped analysis routes', async () => {
  const calls = []; let payload = list(); const api = createWorkbenchApi(async (path, options) => {
    calls.push([path, options]); return { ok: true, status: 200, json: async () => path === '/api/session'
      ? { protocol_version: 2, service_instance_id: 'service-a', token: 'SIMULATED-TOKEN' } : payload };
  });
  await api.handshake(); await api.readAnalysisHistory(scope); payload = detail(result('id/中文')); await api.readAnalysisHistoryEntry('id/中文', scope);
  assert.equal(calls[1][0], '/api/analysis-history?instance_id=service-a&project_name=project-a');
  assert.equal(calls[2][0], '/api/analysis-history/id%2F%E4%B8%AD%E6%96%87?instance_id=service-a&project_name=project-a');
  assert.ok(calls.every(([, options]) => !options.method && !options.body && options.cache === 'no-store'));
  assert.equal(calls[2][1].headers['X-OptiForge-Token'], 'SIMULATED-TOKEN');
  payload = detail(result('other')); await assert.rejects(api.readAnalysisHistoryEntry('id', scope), /请求/);
  const scoped = { service_instance_id: 'service a', project_name: '项目 / a' }; payload = list([], { ...scoped }); await api.readAnalysisHistory(scoped);
  const url = new URL(calls.at(-1)[0], 'http://localhost'); assert.equal(url.searchParams.get('project_name'), scoped.project_name);
  const failedCalls = [], failed = createWorkbenchApi(async path => { failedCalls.push(path); return { ok: false, status: 404,
    json: async () => ({ message: 'unsupported', code: 'NOT_FOUND' }) }; });
  await assert.rejects(failed.readAnalysisHistory(scope), /unsupported/); assert.equal(failedCalls.length, 1); assert.match(failedCalls[0], /^\/api\/analysis-history\?/);
});
