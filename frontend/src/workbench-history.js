const isCounter = value => Number.isSafeInteger(value) && value >= 0;
const text = value => typeof value === 'string' && value.length > 0;
const sourceKeys = ['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project', 'configuration', 'source_state_version'];
const sourceEquals = (a, b) => Boolean(a && b && sourceKeys.every(key => a[key] === b[key]));
const fail = message => { throw new Error(message); };

export function historyScope(state) {
  return text(state?.service_instance_id) && text(state?.active_project)
    ? { service_instance_id: state.service_instance_id, project_name: state.active_project } : null;
}
export const sameHistoryScope = (a, b) => Boolean(a && b && a.service_instance_id === b.service_instance_id && a.project_name === b.project_name);
export const historyConnected = (state, phase, error = '') => Boolean(historyScope(state)
  && !['connecting', 'reconnecting'].includes(phase) && !(phase === 'unavailable' && error));

function validateEnvelope(value, scope) {
  if (value?.protocol_version !== 2 || value.history_schema_version !== 1 || value.storage !== 'session') fail('历史数据版本不兼容，请更新工作台');
  if (!sameHistoryScope(value, scope)) fail('历史来源已变化，请重新读取当前工程');
}
function validateSummary(entry, scope) {
  const s = entry?.source;
  if (!text(entry?.id) || !text(entry.operation_id) || !text(entry.tool) || !text(entry.label)
      || !text(entry.recorded_at) || !Number.isFinite(Date.parse(entry.recorded_at))
      || !s || s.service_instance_id !== scope.service_instance_id
      || s.active_project !== scope.project_name || s.model_project !== scope.project_name
      || !text(s.model_id) || !isCounter(s.model_revision) || s.model_revision < 1
      || !isCounter(s.configuration) || s.configuration < 1 || !isCounter(s.source_state_version)) fail('历史快照来源不完整或不属于当前工程');
  return entry;
}
export function validateHistoryList(value, scope) {
  validateEnvelope(value, scope);
  if (!isCounter(value.history_revision) || !Array.isArray(value.entries) || value.entries.length > 12
      || new Set(value.entries.map(entry => entry?.id)).size !== value.entries.length) fail('历史列表格式不兼容');
  value.entries.forEach(entry => validateSummary(entry, scope));
  if (!value.recording || !['empty', 'recorded', 'skipped'].includes(value.recording.status)) fail('历史记录状态不完整');
  return value;
}
export function validateHistoryEntry(value, scope, expectedEntry) {
  validateEnvelope(value, scope);
  const entry = validateSummary(value.entry, scope), snapshot = value.snapshot, s = entry.source;
  if (expectedEntry && (entry.id !== expectedEntry.id || !sourceEquals(s, expectedEntry.source)
      || entry.operation_id !== expectedEntry.operation_id || entry.tool !== expectedEntry.tool
      || entry.recorded_at !== expectedEntry.recorded_at)) fail('历史条目已变化，请刷新列表');
  if (snapshot?.schema_version !== 1 || !Array.isArray(snapshot.model?.surfaces)
      || snapshot.model.configuration !== s.configuration || !snapshot.sync
      || !['service_instance_id', 'model_id', 'model_revision', 'active_project', 'model_project'].every(key => snapshot.sync[key] === s[key])
      || snapshot.sync.state_version !== s.source_state_version
      || !(snapshot.display_layout || snapshot.layout)) fail('历史光路与快照来源不一致');
  return value;
}
export function historyComparisonReason(left, right) {
  if (!left || !right) return '请选择两个历史快照';
  if (left.id === right.id) return '请选择另一条快照';
  const a = left.source, b = right.source;
  if (!a || !b || a.service_instance_id !== b.service_instance_id || a.active_project !== b.active_project || a.model_project !== b.model_project) return '快照来自不同服务或工程';
  if (a.model_id !== b.model_id) return '模型不同，无法直接对比';
  if (a.configuration !== b.configuration) return '配置不同，无法直接对比';
  return '';
}
const blank = scope => ({scope, status:'idle', entries:[], recording:null, selected:null, compare:null,
  left:null, right:null, leftStatus:'idle', rightStatus:'idle', error:'', leftError:'', rightError:''});

export class HistoryController {
  constructor(api) {
    this.api = api; this.active = true; this.listeners = new Set(); this.generation = 0;
    this.listEpoch = 0; this.slotEpoch = {left:0, right:0}; this.slotAbort = {};
    this.view = blank(null);
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  emit(changes) { if (this.active) { this.view = {...this.view, ...changes}; this.listeners.forEach(listener => listener(this.view)); } }
  invalidate() {
    this.generation++; this.listEpoch++; this.listAbort?.abort();
    for (const slot of ['left','right']) { this.slotEpoch[slot]++; this.slotAbort[slot]?.abort(); }
    this.readKey = null;
  }
  update(state, phase, error = '') {
    const scope = historyScope(state), connected = historyConnected(state, phase, error);
    if (!sameHistoryScope(scope, this.view.scope) || !connected) {
      this.invalidate(); this.emit({...blank(scope), status:connected?'idle':'offline'});
    }
    this.state = state; this.connected = connected;
    if (!this.active || !connected) return;
    const revision = state.history_revision ?? state.state_version;
    const key = `${scope.service_instance_id}:${scope.project_name}:${revision}`;
    if (key !== this.readKey) { this.readKey = key; this.loadList(); }
  }
  async refresh() { if (this.active && this.connected) await this.loadList(); }
  async loadList() {
    const scope = this.view.scope, generation = this.generation, epoch = ++this.listEpoch;
    this.listAbort?.abort(); const abort = new AbortController(); this.listAbort = abort;
    this.emit({status:'loading', error:''});
    try {
      const result = validateHistoryList(await this.api.readHistory(scope, abort.signal), scope);
      if (!this.active || abort.signal.aborted || generation !== this.generation || epoch !== this.listEpoch) return;
      let selected = this.view.selected, compare = this.view.compare;
      if (!result.entries.some(entry => entry.id === selected)) selected = result.entries[0]?.id ?? null;
      if (!result.entries.some(entry => entry.id === compare)) compare = null;
      const left = result.entries.find(entry => entry.id === selected), right = result.entries.find(entry => entry.id === compare);
      if (compare && historyComparisonReason(left,right)) compare = null;
      const oldSelected = this.view.selected, oldCompare = this.view.compare;
      this.emit({entries:result.entries, recording:result.recording, selected, compare, status:'ready', error:'',
        ...(selected!==oldSelected?{left:null,leftStatus:'idle',leftError:''}:{}),
        ...(compare!==oldCompare?{right:null,rightStatus:'idle',rightError:''}:{})});
      if (selected !== oldSelected || !this.view.left && this.view.leftStatus !== 'loading') this.readSlot('left',selected);
      if (compare !== oldCompare || compare && !this.view.right && this.view.rightStatus !== 'loading') this.readSlot('right',compare);
      if (!selected) this.readSlot('left',null);
      if (!compare) this.readSlot('right',null);
    } catch (error) {
      if (this.active && !abort.signal.aborted && generation === this.generation && epoch === this.listEpoch) {
        for (const slot of ['left','right']) { this.slotEpoch[slot]++; this.slotAbort[slot]?.abort(); }
        this.emit({status:'failed', entries:[], selected:null, compare:null, left:null, right:null,
          leftStatus:'idle', rightStatus:'idle', error:error.message || '无法读取操作历史'});
      }
    }
  }
  select(id) {
    if (!this.active || !this.connected || !this.view.entries.some(entry => entry.id === id)) return;
    const left = this.view.entries.find(entry => entry.id === id), right = this.view.entries.find(entry => entry.id === this.view.compare);
    const compare = right && !historyComparisonReason(left,right) ? right.id : null;
    this.emit({selected:id, compare, left:null, leftStatus:'loading',leftError:'',
      ...(!compare?{right:null,rightStatus:'idle',rightError:''}:{})}); this.readSlot('left',id);
    if (!compare) this.readSlot('right',null);
  }
  compare(id) {
    if (!this.active || !this.connected) return;
    const left = this.view.entries.find(entry => entry.id === this.view.selected), right = this.view.entries.find(entry => entry.id === id);
    if (id && historyComparisonReason(left,right)) return;
    this.emit({compare:id || null,right:null,rightStatus:id?'loading':'idle',rightError:''}); this.readSlot('right',id || null);
  }
  async readSlot(slot, id) {
    const scope = this.view.scope, generation = this.generation, epoch = ++this.slotEpoch[slot];
    this.slotAbort[slot]?.abort(); const abort = new AbortController(); this.slotAbort[slot] = abort;
    const stateKey = `${slot}Status`, errorKey = `${slot}Error`;
    this.emit({[slot]:null, [stateKey]:id?'loading':'idle', [errorKey]:''});
    if (!id) return;
    const entry = this.view.entries.find(item => item.id === id);
    if (!entry) return;
    try {
      const result = validateHistoryEntry(await this.api.readHistoryEntry(id,scope,abort.signal),scope,entry);
      if (!this.active || abort.signal.aborted || generation !== this.generation || epoch !== this.slotEpoch[slot]) return;
      const current = this.view.entries.find(item => item.id === id);
      if (!current || !sourceEquals(current.source,result.entry.source)) return;
      this.emit({[slot]:result, [stateKey]:'ready', [errorKey]:''});
    } catch (error) {
      if (this.active && !abort.signal.aborted && generation === this.generation && epoch === this.slotEpoch[slot]) this.emit({[slot]:null,
        [stateKey]:'failed', [errorKey]:error.status===404?'快照已淘汰或不存在，请刷新历史列表':error.message || '无法读取历史快照'});
    }
  }
  stop() { this.invalidate(); this.active = false; }
}
