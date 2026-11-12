const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = value => typeof value === 'string' && value.trim().length > 0;
const known = value => text(value) && !['unknown', 'unavailable', 'unverified', 'n/a'].includes(value.trim().toLowerCase());
const counter = (value, min = 0) => Number.isSafeInteger(value) && value >= min;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const timestamp = value => text(value) && /T.*(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
const KINDS = ['mtf', 'wavefront', 'spot', 'ray_fan', 'field_curvature'];
const VERDICTS = ['PASS', 'WARN', 'FAIL', 'ERROR', 'INFO'];
const SOURCE_KEYS = ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project', 'configuration', 'source_state_version'];
const fail = message => { throw new Error(message); };

export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
const equal = (a, b) => canonical(a) === canonical(b);
export function analysisHistoryScope(state) {
  return text(state?.service_instance_id) && text(state?.active_project)
    ? { service_instance_id: state.service_instance_id, project_name: state.active_project } : null;
}
export const sameAnalysisHistoryScope = (a, b) => Boolean(a && b && a.service_instance_id === b.service_instance_id && a.project_name === b.project_name);
export const analysisHistoryConnected = (state, phase, transportConnected) => Boolean(analysisHistoryScope(state)
  && transportConnected === true && ['synced', 'running', 'unavailable'].includes(phase));
function validSource(source, scope) {
  return object(source) && source.service_instance_id === scope.service_instance_id
    && source.active_project === scope.project_name && source.model_project === scope.project_name
    && text(source.model_id) && counter(source.model_revision, 1) && counter(source.configuration, 1) && counter(source.source_state_version);
}
function validSettings(value) {
  const keys = ['analyses', 'fields', 'sample_size', 'max_frequency', 'wavefront_size', 'spot_rings',
    'mtf_min_at_30lp_mm', 'distortion_max_pct', 'item_timeout_s', 'suite_timeout_s'];
  return object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
    && Array.isArray(value.analyses) && value.analyses.length > 0 && value.analyses.length <= 5
    && new Set(value.analyses).size === value.analyses.length && value.analyses.every(kind => KINDS.includes(kind))
    && equal(value.analyses, KINDS.filter(kind => value.analyses.includes(kind)))
    && Array.isArray(value.fields) && value.fields.length > 0 && value.fields.length <= 5
    && new Set(value.fields).size === value.fields.length && value.fields.every(field => counter(field, 1))
    && equal(value.fields, [...value.fields].sort((a, b) => a - b))
    && ['128x128', '256x256', '512x512'].includes(value.sample_size) && finite(value.max_frequency) && value.max_frequency >= .001 && value.max_frequency <= 100000
    && [128, 256].includes(value.wavefront_size) && [3, 6, 9, 12].includes(value.spot_rings)
    && (value.mtf_min_at_30lp_mm === null || finite(value.mtf_min_at_30lp_mm) && value.mtf_min_at_30lp_mm >= 0 && value.mtf_min_at_30lp_mm <= 1)
    && (value.distortion_max_pct === null || finite(value.distortion_max_pct) && value.distortion_max_pct >= 0 && value.distortion_max_pct <= 10000)
    && finite(value.item_timeout_s) && value.item_timeout_s >= .1 && value.item_timeout_s <= 120
    && finite(value.suite_timeout_s) && value.suite_timeout_s >= .1 && value.suite_timeout_s <= 600;
}
function envelope(value, scope) {
  if (value?.protocol_version !== 2 || value.analysis_history_schema_version !== 1 || value.storage !== 'session'
      || !sameAnalysisHistoryScope(value, scope) || !counter(value.analysis_history_revision)
      || value.limits?.max_entries !== 8 || value.limits?.max_entry_bytes !== 8388608 || value.limits?.max_total_bytes !== 67108864) {
    fail('分析历史协议或工程来源不匹配');
  }
}
function entry(value, scope) {
  if (!object(value) || !text(value.id) || value.id !== value.result_id || !['success', 'partial'].includes(value.status)
      || !text(value.engine) || !timestamp(value.finished_at) || !validSource(value.source, scope)
      || !validSettings(value.settings) || !VERDICTS.includes(value.overall)) fail('分析历史条目格式或来源不完整');
  return value;
}
export function validateAnalysisHistory(value, scope) {
  envelope(value, scope);
  if (!Array.isArray(value.entries) || value.entries.length > 8 || new Set(value.entries.map(item => item?.id)).size !== value.entries.length) {
    fail('分析历史目录不完整或包含重复记录');
  }
  value.entries.forEach(item => entry(item, scope));
  return value;
}
export function validateAnalysisHistoryEntry(value, scope, expected) {
  envelope(value, scope);
  const selected = entry(value.entry, scope), result = value.result;
  if (expected && !equal(selected, expected)) fail('分析历史条目已变化，请刷新目录');
  if (!object(result) || result.analysis_schema_version !== 1 || result.result_id !== selected.id || !text(result.key)
      || result.status !== selected.status || result.engine !== selected.engine || result.finished_at !== selected.finished_at
      || result.overall !== selected.overall || !validSource(result.source, scope)
      || !SOURCE_KEYS.every(key => result.source[key] === selected.source[key]) || !equal(result.settings, selected.settings)
      || !object(result.items) || !Array.isArray(result.verdicts) || result.verdicts.length > 5
      || new Set(result.verdicts.map(row => row?.analysis)).size !== result.verdicts.length
      || !result.verdicts.every(row => object(row) && KINDS.includes(row.analysis) && VERDICTS.includes(row.verdict) && typeof row.criteria === 'string')) {
    fail('分析结果与所选历史条目不一致');
  }
  return value;
}

function completeFields(fields) {
  return Array.isArray(fields) && fields.length > 0 && fields.length <= 100
    && new Set(fields.map(field => field?.index)).size === fields.length
    && fields.every(field => object(field) && counter(field.index, 1) && finite(field.x) && finite(field.y)
      && finite(field.weight) && field.weight >= 0);
}
function completeWavelengths(waves) {
  return Array.isArray(waves) && waves.length > 0 && waves.length <= 12
    && new Set(waves.map(wave => wave?.index)).size === waves.length
    && waves.every(wave => object(wave) && counter(wave.index, 1) && finite(wave.wavelength_um) && wave.wavelength_um > 0
      && finite(wave.weight) && wave.weight >= 0 && typeof wave.is_primary === 'boolean')
    && waves.filter(wave => wave.is_primary).length === 1;
}
function comparisonModel(result) {
  const context = result.comparison_context, model = context?.model, metadata = result.model_metadata;
  if (context?.schema_version !== 1 || !object(model) || !known(model.units) || !known(model.lens_units) || !known(model.system_mode)
      || !counter(model.configuration, 1) || model.configuration !== result.source.configuration
      || !object(model.settings) || !known(model.settings.field_type) || !known(model.settings.field_normalization)
      || !completeFields(model.fields) || !completeWavelengths(model.wavelengths)
      || model.trace_fields !== undefined && !completeFields(model.trace_fields)
      || !object(metadata) || metadata.units !== model.units || metadata.configuration !== model.configuration
      || !completeFields(metadata.fields) || !completeWavelengths(metadata.wavelengths)
      || !equal(metadata.fields, model.fields) || !equal(metadata.wavelengths, model.wavelengths)) return null;
  return { context, metadata: { units: metadata.units, configuration: metadata.configuration, fields: metadata.fields, wavelengths: metadata.wavelengths } };
}
function effectiveMetadata(kind, item, result) {
  const coverage = item?.coverage, effective = item?.effective_settings;
  if (!object(coverage) || (kind === 'field_curvature'
      ? !known(coverage.fields) : !equal(coverage.fields, result.settings.fields))) return null;
  if (kind !== 'wavefront' && coverage.wavelengths !== 'all') return null;
  if (kind !== 'wavefront') {
    if (kind === 'mtf') {
      if (!Array.isArray(effective) || effective.length !== result.settings.fields.length
          || !equal(effective.map(row => row?.field_index).sort((a, b) => a - b), result.settings.fields)
          || !effective.every(row => object(row) && ['sample_size', 'wavelengths', 'type', 'surface'].every(key => known(row[key]))
            && finite(row.max_frequency) && row.max_frequency > 0 && typeof row.polarization === 'boolean')) return null;
    } else if (!object(effective)) return null;
    else if (kind === 'spot' && (!['reference', 'sampling', 'surface', 'statistics', 'native_length_unit'].every(key => known(effective[key]))
      || !counter(effective.rings, 1) || !counter(effective.rays_per_wavelength, 1) || !finite(effective.delta_focus))) return null;
    else if (kind === 'ray_fan' && (!counter(effective.number_of_rays, 1) || typeof effective.check_apertures !== 'boolean'
      || typeof effective.vignetted_pupil !== 'boolean' || !known(effective.reference))) return null;
    else if (kind === 'field_curvature' && (!['scan', 'distortion', 'native_length_unit'].every(key => known(effective[key]))
      || !counter(effective.reference_field, 1) || typeof effective.ignore_vignette !== 'boolean')) return null;
    return { coverage, effective_settings: effective };
  }
  const primary = result.model_metadata.wavelengths.find(wave => wave.is_primary);
  if (!equal(coverage.wavelengths, [primary.index]) || !Array.isArray(item.fields)
      || !equal(item.fields.map(field => field?.field_index).sort((a, b) => a - b), result.settings.fields)) return null;
  const fields = [];
  for (const field of item.fields) {
    const grid = field?.grid;
    const settings = field?.effective_settings;
    if (!counter(field?.field_index, 1) || field.wavelength_index !== primary.index || field.wavelength_um !== primary.wavelength_um
        || !object(settings) || !['sampling', 'polarization', 'surface'].every(key => known(settings[key]))
        || !['remove_tilt', 'reference_to_primary', 'use_exit_pupil'].every(key => typeof settings[key] === 'boolean') || !object(grid)
        || !['unit', 'coordinates', 'orientation', 'statistics_source', 'mask_source', 'strehl_method'].every(key => known(grid[key]))
        || !['nx', 'ny'].every(key => counter(grid[key], 1) && grid[key] <= 256)
        || !['min_x', 'min_y', 'dx', 'dy'].every(key => finite(grid[key])) || grid.dx === 0 || grid.dy === 0) return null;
    fields.push({ field_index: field.field_index, wavelength_index: field.wavelength_index, wavelength_um: field.wavelength_um,
      effective_settings: field.effective_settings, grid: Object.fromEntries(['unit', 'coordinates', 'orientation', 'statistics_source', 'mask_source',
        'strehl_method', 'nx', 'ny', 'min_x', 'min_y', 'dx', 'dy'].map(key => [key, grid[key]])) });
  }
  return { coverage: item.coverage, fields };
}
export function analysisComparisonReason(left, right) {
  if (!left || !right) return '请选择两条完整历史记录';
  if (left.result_id === right.result_id) return '同一缓存记录不能作为两次独立结果对比';
  if (!['service_instance_id', 'active_project', 'model_project', 'model_id', 'configuration'].every(key => left.source?.[key] === right.source?.[key])) return '服务、工程、模型或配置不同';
  if (!known(left.engine) || !known(right.engine) || left.engine !== right.engine) return '分析引擎不同或未知';
  if (!validSettings(left.settings) || !validSettings(right.settings) || !equal(left.settings, right.settings)) return '采样、视场、阈值或其他分析设置不同';
  const a = comparisonModel(left), b = comparisonModel(right);
  if (!a || !b) return '缺少可核对的视场、波长或单位元数据，可单独回看';
  if (!equal(a, b)) return '物理视场、波长权重、单位或模型统计口径不同';
  if (left.status !== 'success' || right.status !== 'success') return '存在未完成分析项目，可单独回看';
  for (const kind of left.settings.analyses) {
    if (left.items?.[kind]?.status !== 'success' || right.items?.[kind]?.status !== 'success') return '存在缺失或未完成分析项目，可单独回看';
    const first = effectiveMetadata(kind, left.items[kind], left), second = effectiveMetadata(kind, right.items[kind], right);
    if (!first || !second) return '缺少有效采样或统计口径，可单独回看';
    if (!equal(first, second)) return '覆盖范围、有效采样或统计口径不同';
  }
  return '';
}

export function historicalMetrics(result) {
  const mtf = result.verdicts.find(row => row.analysis === 'mtf'), wave = result.verdicts.find(row => row.analysis === 'wavefront');
  const usableMtf = result.items.mtf?.status === 'success' && object(mtf?.evidence), usableWave = result.items.wavefront?.status === 'success' && object(wave?.evidence);
  const normalized = value => finite(value) && value >= 0 && value <= 1;
  return { mtf: usableMtf && normalized(mtf.evidence.minimum) ? mtf.evidence.minimum : null,
    rms: usableWave && finite(wave.evidence.rms_waves) && wave.evidence.rms_waves >= 0 ? wave.evidence.rms_waves : null,
    strehl: usableWave && normalized(wave.evidence.strehl_estimate) ? wave.evidence.strehl_estimate : null,
    verified: usableWave && typeof wave.evidence.native_statistics_verified === 'boolean' ? wave.evidence.native_statistics_verified : null,
    mtfThreshold: usableMtf && normalized(mtf.evidence.threshold) ? mtf.evidence.threshold : null,
    worstField: usableWave && counter(wave.evidence.worst_covered_field, 1) ? wave.evidence.worst_covered_field : null };
}

const blank = scope => ({ scope, status: 'idle', entries: [], selected: null, compare: null, left: null, right: null,
  leftStatus: 'idle', rightStatus: 'idle', error: '', leftError: '', rightError: '' });
export class AnalysisHistoryController {
  constructor(api) {
    this.api = api; this.active = true; this.listeners = new Set(); this.generation = 0; this.listEpoch = 0;
    this.slotEpoch = { left: 0, right: 0 }; this.slotAbort = {}; this.view = blank(null);
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  emit(changes) { if (this.active) { this.view = { ...this.view, ...changes }; this.listeners.forEach(listener => listener(this.view)); } }
  invalidate() {
    this.generation++; this.listEpoch++; this.listAbort?.abort(); this.readKey = null;
    for (const slot of ['left', 'right']) { this.slotEpoch[slot]++; this.slotAbort[slot]?.abort(); }
  }
  update(state, phase, transportConnected) {
    const scope = analysisHistoryScope(state), connected = analysisHistoryConnected(state, phase, transportConnected), capable = counter(state?.analysis_history_revision);
    if (!sameAnalysisHistoryScope(scope, this.view.scope) || !connected || !capable) {
      this.invalidate(); this.emit({ ...blank(scope), status: !connected ? 'offline' : !capable ? 'unavailable' : 'idle' });
    }
    this.connected = connected && capable;
    if (!this.active || !this.connected) return;
    const key = `${scope.service_instance_id}:${scope.project_name}:${state.analysis_history_revision}`;
    if (key !== this.readKey) { this.readKey = key; this.loadList(); }
  }
  async refresh() { if (this.active && this.connected) await this.loadList(); }
  async loadList() {
    const scope = this.view.scope, generation = this.generation, epoch = ++this.listEpoch;
    this.listAbort?.abort(); const abort = new AbortController(); this.listAbort = abort;
    this.emit({ status: 'loading', error: '' });
    try {
      const result = validateAnalysisHistory(await this.api.readAnalysisHistory(scope, abort.signal), scope);
      if (!this.active || abort.signal.aborted || generation !== this.generation || epoch !== this.listEpoch) return;
      const selected = result.entries.some(item => item.id === this.view.selected) ? this.view.selected : result.entries[0]?.id ?? null;
      const compare = result.entries.some(item => item.id === this.view.compare && item.id !== selected) ? this.view.compare : null;
      const changes = { entries: result.entries, selected, compare, status: 'ready', error: '' }, reload = [];
      for (const [slot, id] of [['left', selected], ['right', compare]]) {
        const detail = this.view[slot], current = result.entries.find(item => item.id === id);
        if (!id || !detail || !equal(detail.entry, current)) {
          changes[slot] = null; changes[`${slot}Status`] = id ? 'loading' : 'idle'; changes[`${slot}Error`] = '';
          reload.push([slot, id]);
        }
      }
      this.emit(changes);
      for (const [slot, id] of reload) this.readSlot(slot, id);
    } catch (error) {
      if (this.active && !abort.signal.aborted && generation === this.generation && epoch === this.listEpoch) {
        for (const slot of ['left', 'right']) { this.slotEpoch[slot]++; this.slotAbort[slot]?.abort(); }
        this.emit({ ...blank(scope), status: 'failed', error: error.message || '无法读取分析历史' });
      }
    }
  }
  select(id) {
    if (!this.active || !this.connected || !this.view.entries.some(item => item.id === id)) return;
    const compare = this.view.compare === id ? null : this.view.compare;
    this.emit({ selected: id, compare, left: null, leftStatus: 'loading', leftError: '', ...(!compare ? { right: null, rightStatus: 'idle', rightError: '' } : {}) });
    this.readSlot('left', id); if (!compare) this.readSlot('right', null);
  }
  compare(id) {
    if (!this.active || !this.connected || id && (id === this.view.selected || !this.view.entries.some(item => item.id === id))) return;
    this.emit({ compare: id || null, right: null, rightStatus: id ? 'loading' : 'idle', rightError: '' }); this.readSlot('right', id || null);
  }
  async readSlot(slot, id) {
    const scope = this.view.scope, generation = this.generation, epoch = ++this.slotEpoch[slot];
    this.slotAbort[slot]?.abort(); const abort = new AbortController(); this.slotAbort[slot] = abort;
    this.emit({ [slot]: null, [`${slot}Status`]: id ? 'loading' : 'idle', [`${slot}Error`]: '' });
    if (!id) return;
    const expected = this.view.entries.find(item => item.id === id);
    if (!expected) return;
    try {
      const result = validateAnalysisHistoryEntry(await this.api.readAnalysisHistoryEntry(id, scope, abort.signal), scope, expected);
      if (!this.active || abort.signal.aborted || generation !== this.generation || epoch !== this.slotEpoch[slot]) return;
      if (!equal(this.view.entries.find(item => item.id === id), expected)) return;
      this.emit({ [slot]: result, [`${slot}Status`]: 'ready', [`${slot}Error`]: '' });
    } catch (error) {
      if (this.active && !abort.signal.aborted && generation === this.generation && epoch === this.slotEpoch[slot]) this.emit({ [slot]: null,
        [`${slot}Status`]: 'failed', [`${slot}Error`]: error.status === 404 ? '记录已淘汰或不可用，请刷新分析历史' : error.message || '无法读取分析记录' });
    }
  }
  stop() { this.invalidate(); this.active = false; }
}
