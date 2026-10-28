import { contextOf } from './workbench-api.js';

export function abortableDelay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const cleanup = () => signal.removeEventListener('abort', cancel);
    const timer = setTimeout(() => { cleanup(); resolve(); }, ms);
    const cancel = () => { clearTimeout(timer); cleanup(); reject(signal.reason); };
    signal.addEventListener('abort', cancel, { once: true });
  });
}

export function canAdopt(current, incoming, instance) {
  if (incoming.service_instance_id !== instance) return false;
  return !current || current.service_instance_id !== instance || incoming.state_version > current.state_version
    || incoming.state_version === current.state_version && incoming.event_seq >= current.event_seq;
}

export class WorkbenchSync {
  constructor(api, { delay = abortableDelay, random = Math.random } = {}) {
    this.api = api; this.delay = delay; this.random = random;
    this.listeners = new Set(); this.epoch = 0; this.active = false; this.runGeneration = 0;
    this.view = { phase: 'connecting', engine: null, snapshot: null, stale: true, error: '', importing: false, canImportModel: true };
  }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  emit(changes) { this.view = { ...this.view, ...changes }; for (const listener of this.listeners) listener(this.view); }
  adopt(incoming, epoch) {
    if (!this.active || epoch !== this.epoch || this.abort?.signal.aborted || !canAdopt(this.view.engine, incoming, this.instance)) return false;
    this.cursor = Math.max(this.cursor || 0, incoming.event_seq);
    const running = incoming.tasks.pending.length > 0;
    const current = incoming.validity === 'valid' && Boolean(incoming.snapshot);
    const recent = incoming.tasks.recent[0];
    const taskError = !running && recent && ['failed', 'rejected'].includes(recent.status) ? recent.message || '操作失败' : '';
    this.emit({ engine: incoming, snapshot: incoming.snapshot || this.view.snapshot,
      phase: running ? 'running' : current ? 'synced' : incoming.validity === 'initializing' ? 'connecting' : 'unavailable',
      stale: !current || running, error: incoming.error || taskError });
    return true;
  }
  start() {
    if (this.active) return this.loop;
    this.active = true; this.loop = this.run(++this.runGeneration); return this.loop;
  }
  stop() { this.active = false; this.epoch += 1; this.runGeneration += 1; this.abort?.abort(); }
  reconnect() { this.abort?.abort(); }
  async run(runGeneration) {
    let failures = 0;
    while (this.active && this.runGeneration === runGeneration) {
      const epoch = ++this.epoch;
      this.abort = new AbortController(); const signal = this.abort.signal;
      try {
        const session = await this.api.handshake(signal);
        if (!this.active || this.runGeneration !== runGeneration) return;
        if (epoch !== this.epoch || signal.aborted) continue;
        this.instance = session.service_instance_id; this.cursor = 0;
        this.emit({ canImportModel: session.capabilities?.model_import !== false });
        const initial = await this.api.readState(signal);
        if (initial.service_instance_id !== this.instance) throw new Error('服务已重启，正在重新连接');
        this.adopt(initial, epoch);
        while (this.active && !signal.aborted) {
          const events = await this.api.events(this.instance, this.cursor, signal);
          if (!this.active || epoch !== this.epoch || signal.aborted) break;
          if (events.closed || events.service_instance_id !== this.instance) throw new Error('服务已停止，正在重新连接');
          failures = 0;
          if (events.reset_required || events.events.length) {
            const state = await this.api.readState(signal);
            if (state.service_instance_id !== this.instance) throw new Error('服务已重启，正在重新连接');
            this.adopt(state, epoch);
          }
          this.cursor = Math.max(this.cursor, events.next_seq);
        }
      } catch (error) {
        if (!this.active || this.runGeneration !== runGeneration) break;
        if (signal.aborted) continue;
        this.emit({ phase: this.view.snapshot ? 'reconnecting' : 'unavailable', stale: true,
          error: this.view.snapshot ? '连接已断开，显示上次读取' : error.message || '本地工程服务未连接' });
        const ms = Math.min(10000, 1000 * 2 ** Math.min(failures++, 4)) + Math.floor(this.random() * 150);
        this.abort = new AbortController();
        try { await this.delay(ms, this.abort.signal); } catch { /* resume/unmount */ }
      }
    }
  }
  async refresh() {
    if (!this.active) return;
    const signal = this.abort.signal, epoch = this.epoch;
    try { await this.api.refresh(signal); }
    catch (error) { if (this.active && epoch === this.epoch && !signal.aborted) this.emit({ error: error.message }); }
  }
  async importModel(file) {
    if (!this.active || !this.view.engine || !this.view.canImportModel || this.view.importing || ['reconnecting', 'connecting'].includes(this.view.phase)) return;
    const epoch = this.epoch, signal = this.abort.signal, context = contextOf(this.view.engine);
    this.emit({ importing: true, error: '' });
    try {
      const reply = await this.api.importModel(file, context, signal);
      this.adopt(reply.state || this.view.engine, epoch);
      if (reply.result?.status === 'error' || reply.result?.status === 'rejected') throw new Error(reply.result.message || '打开模型失败');
    } catch (error) {
      if (this.active) this.emit({ error: signal.aborted || epoch !== this.epoch ? '结果未知，请刷新核对' : error.message });
    } finally { if (this.active) this.emit({ importing: false }); }
  }
}
