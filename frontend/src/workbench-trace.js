export const settingsId = (s) => JSON.stringify(s ? { fields: [...s.fields].sort((a,b)=>a-b), wavelengths: [...s.wavelengths].sort((a,b)=>a-b), pupil_count: s.pupil_count, pupil_axis: s.pupil_axis || 'Y' } : null);
export const modelId = (s) => s ? `${s.service_instance_id}:${s.model_id}` : '';
export function defaultTraceSettings(model) {
  const fields = [...(model.trace_fields || model.fields)].filter(f => !f.ignore && f.is_active !== false)
    .sort((a,b) => Math.hypot(a.x,a.y) - Math.hypot(b.x,b.y) || a.index-b.index);
  return { fields: [...new Set([fields[0]?.index, fields[Math.floor(fields.length/2)]?.index, fields.at(-1)?.index].filter(Number.isInteger))],
    wavelengths: model.wavelengths.filter(w => w.is_primary).slice(0,1).map(w => w.index), pupil_count: 5, pupil_axis: 'Y' };
}
const reads = new Set(['__refresh__', '__trace__', 'zemax_get_workbench_snapshot', 'zemax_system_info', 'zemax_get_system_data',
  'zemax_get_project', 'zemax_list_projects', 'get_current_design_proposal', 'zemax_audit_requirements', 'zemax_lookup_manual']);
export const modelBusy = (s) => s?.tasks.pending.some(t => !reads.has(t.tool));
export function traceAllowed(s, phase) {
  return Boolean(s?.context_valid && s.validity === 'valid' && s.snapshot && s.model_id && s.proposal_confirmed
    && s.active_project && s.active_project === s.model_project && !modelBusy(s) && !['reconnecting','unavailable','connecting'].includes(phase));
}
export function matchesTrace(result, s, settings, phase) {
  return Boolean(result && traceAllowed(s, phase) && ['service_instance_id','model_id','model_revision','active_project','model_project'].every(k => result.source[k] === s[k])
    && result.source.configuration === s.snapshot.model.configuration && settingsId(result.settings) === settingsId(settings));
}
const signature = (s, settings) => s?.snapshot && `${modelId(s)}:${s.model_revision}:${s.snapshot.model.configuration}:${s.active_project}:${s.model_project}:${s.proposal_confirmed}:${settingsId(settings)}`;
const disconnected = phase => ['reconnecting','unavailable','connecting'].includes(phase);
const transientReadError = error => error.code !== 'TRACE_NOT_FOUND'
  && (!Number.isInteger(error.status) || [408,429].includes(error.status) || error.status >= 500);
const maxResultReads = 2;

export class TraceController {
  constructor(api, { debounce = 400 } = {}) {
    this.api = api; this.debounce = debounce; this.listeners = new Set(); this.active = true;
    this.view = { enabled: false, result: null, status: 'off', error: '' }; this.abort = new AbortController();
    this.generation = 0; this.connectionGeneration = 0;
  }
  subscribe(f) { this.listeners.add(f); return () => this.listeners.delete(f); }
  emit(v) { if (!this.active) return; this.view = { ...this.view, ...v }; this.listeners.forEach(f=>f(this.view)); }
  update(state, phase, settings) {
    const id = modelId(state);
    const reconnected = disconnected(this.phase) && !disconnected(phase);
    if (reconnected) this.connectionGeneration++;
    if (this.identity !== undefined && id !== this.identity) {
      this.generation++; this.abort.abort(); this.abort = new AbortController(); clearTimeout(this.timer);
      this.pending = null; this.inflight = false; this.reading = false; this.attempted = null;
      this.emit({ enabled: false, result: null, status: 'off', error: '' });
    }
    this.identity = id; this.state = state; this.phase = phase; this.settings = settings;
    if (this.pending?.awaitReconnect && this.pending.signature !== signature(state, settings)) {
      this.pending = null; this.inflight = false;
    }
    if (this.pending?.awaitReconnect && reconnected) this.pending.awaitReconnect = false;
    if (!this.pending && this.view.status === 'unknown') {
      const recovered = [...(state?.trace_summary || [])].reverse().find(s=>s.settings && s.source
        && matchesTrace(s,state,settings,phase));
      if (recovered) { this.pending = { ...recovered, signature: signature(state, settings) }; this.inflight = true; }
    }
    if (this.pending) this.reconcile();
    this.schedule();
  }
  enable(enabled) { this.emit({ enabled, error: '', status: enabled ? 'waiting' : 'off' }); if (enabled) this.attempted = null; this.schedule(); }
  retry() {
    this.emit({ error: '' });
    if (this.pending) { this.pending.awaitReconnect = false; this.reconcile(); }
    else { this.attempted = null; this.schedule(); }
  }
  schedule() {
    clearTimeout(this.timer);
    if (!this.active || !this.view.enabled || this.inflight || !traceAllowed(this.state, this.phase)
      || this.state?.tasks.pending.some(t=>t.tool === '__trace__')) return;
    const sig = signature(this.state, this.settings);
    if (sig === this.attempted) return;
    this.emit({ status: 'waiting' });
    this.timer = setTimeout(()=>this.submit(sig), this.debounce);
  }
  async submit(sig) {
    if (!this.active || !this.view.enabled || sig !== signature(this.state, this.settings) || !traceAllowed(this.state,this.phase)) return;
    const generation = this.generation;
    this.inflight = true; this.attempted = sig; this.emit({ status: 'queued', error: '' });
    try {
      const reply = await this.api.trace(this.settings, this.state, this.abort.signal);
      if (!this.active || generation !== this.generation) return;
      this.pending = { ...reply, signature: sig }; this.reconcile();
    } catch(error) {
      if (!this.active || generation !== this.generation) return;
      this.inflight = false; this.emit({ status: 'unknown', error: error.message }); this.schedule();
    }
  }
  async reconcile() {
    const pending = this.pending;
    if (!pending || this.reading || disconnected(this.phase)) return;
    const generation = this.generation;
    const summary = this.state?.trace_summary?.find(t=>t.operation_id === pending.operation_id && t.key === pending.key);
    if (!summary) {
      if (this.state?.tasks.recent.some(t=>t.operation_id===pending.operation_id && ['succeeded','failed','rejected'].includes(t.status))) {
        this.pending = null; this.inflight = false;
        this.emit({ status: 'failed', error: '结果已淘汰，请重新追迹' }); this.schedule();
      }
      return;
    }
    if (['queued','running'].includes(summary.status)) { this.emit({ status: summary.status }); return; }
    if (pending.awaitReconnect && summary.status === 'succeeded' && summary.result_id) return;
    this.reading = true;
    const connectionGeneration = this.connectionGeneration;
    let readingResult = false, keepPending = false;
    try {
      if (summary.status !== 'succeeded' || !summary.result_id) throw new Error(summary.message || '追迹结果不可用');
      readingResult = true; pending.readAttempts = (pending.readAttempts || 0) + 1;
      const result = await this.api.readTrace(summary.result_id, this.abort.signal);
      // The hook still hides cached results during a disconnection. Keeping a
      // matching result here allows it to reappear after state synchronization.
      if (this.active && generation === this.generation && result.key === pending.key && matchesTrace(result,this.state,this.settings,'synced'))
        this.emit({ result, status: 'ready', error: '' });
    } catch(error) {
      if (this.active && generation === this.generation) {
        keepPending = readingResult && transientReadError(error) && pending.readAttempts < maxResultReads;
        if (keepPending) pending.awaitReconnect = connectionGeneration === this.connectionGeneration || disconnected(this.phase);
        this.emit({ status: 'failed', error: error.message });
      }
    } finally {
      // A retired request may settle even when its transport ignored abort.
      if (this.active && generation === this.generation) {
        this.reading = false; this.inflight = keepPending; this.pending = keepPending ? pending : null; this.schedule();
        if (keepPending && !pending.awaitReconnect) this.reconcile();
      }
    }
  }
  stop() { this.active = false; clearTimeout(this.timer); this.abort.abort(); }
}

export function projection(layout, trace, plane) {
  const contours = layout?.status === 'ready' && (plane === 'YZ' || layout.symmetric_planes?.includes(plane)) ? layout : null;
  const axis = plane === 'YZ' ? 1 : 0;
  const rays = (trace?.rays || []).map(ray=>({ ...ray, projected: ray.points.map(p=>[p.xyz[2],p.xyz[axis]]) }));
  const points = [...(contours?.surfaces.flatMap(s=>s.points) || []), ...rays.flatMap(r=>r.projected)];
  if (!points.length) return { contours, rays, bounds: null };
  const bounds = points.reduce((b,p)=>[Math.min(b[0],p[0]),Math.min(b[1],p[1]),Math.max(b[2],p[0]),Math.max(b[3],p[1])],[Infinity,Infinity,-Infinity,-Infinity]);
  if (!bounds.every(Number.isFinite) || !Number.isFinite(bounds[2]-bounds[0]) || !Number.isFinite(bounds[3]-bounds[1]))
    return { contours: null, rays: [], bounds: null, issue: '坐标范围超出安全绘图精度，请查看原生结果' };
  return { contours, rays, bounds };
}
