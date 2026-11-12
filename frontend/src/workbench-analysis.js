import { modelId, traceBlocked, modelBusy } from './workbench-trace.js';

export const analysisNames = { mtf:'MTF',wavefront:'波前',spot:'点列',ray_fan:'光线像差',field_curvature:'场曲 / 畸变' };

// Axis labels have a fixed gutter. Keep readouts and original arrays separate
// from this display-only rounding; a residual tiny relative to a tick is zero.
export function formatAxisTick(value,step) {
  if (!Number.isFinite(value)) return '—';
  if (value===0 || Number.isFinite(step) && step!==0 && Math.abs(value/step)<1e-6) return '0';
  if (Math.abs(value)<.001 || Math.abs(value)>=1e4) {
    const [mantissa,exponent]=value.toExponential(2).split('e');
    return `${Number(mantissa)}e${Number(exponent)}`;
  }
  return String(Number(value.toPrecision(3)));
}

export function defaultAnalysisSettings(model) {
  const f=[...(model.trace_fields || model.fields)].filter(f=>!f.ignore && f.is_active!==false)
    .sort((a,b)=>Math.hypot(a.x,a.y)-Math.hypot(b.x,b.y) || a.index-b.index);
  const n=Math.min(5,f.length);
  return { analyses:Object.keys(analysisNames),fields:Array.from({length:n},(_,i)=>f[Math.round(i*(f.length-1)/Math.max(1,n-1))].index).sort((a,b)=>a-b),
    sample_size:'256x256',max_frequency:100,wavefront_size:128,spot_rings:6,mtf_min_at_30lp_mm:null,distortion_max_pct:null };
}
export function matchesAnalysis(result,state) {
  return Boolean(result && state?.snapshot && ['service_instance_id','model_id','model_revision','active_project','model_project']
    .every(k=>result.source?.[k]===state[k]) && result.source.configuration===state.snapshot.model.configuration);
}
export function analysisBlocked(state,phase,capable) {
  if (!capable) return '当前服务未启用像质分析，请使用新版工作台服务';
  return traceBlocked(state,phase) || (modelBusy(state)?'等待模型操作完成':'');
}
export const analysisIsCurrent=(result,state,phase)=>matchesAnalysis(result,state) && !traceBlocked(state,phase) && !modelBusy(state);

export class AnalysisController {
  constructor(api) {
    this.api=api;this.active=true;this.listeners=new Set();this.generation=0;this.readEpoch=0;this.abort=new AbortController();
    this.handled=new Set();this.view={status:'idle',result:null,error:'',summary:null};
  }
  subscribe(f) { this.listeners.add(f);return ()=>this.listeners.delete(f); }
  emit(v) { if(this.active) { this.view={...this.view,...v};this.listeners.forEach(f=>f(this.view)); } }
  update(state,phase,capable) {
    const id=modelId(state);
    if (this.identity!==undefined && id!==this.identity) {
      this.generation++;this.readEpoch++;this.abort.abort();this.abort=new AbortController();this.pending=null;this.reading=null;this.posting=false;
      this.handled.clear();this.emit({status:'idle',result:null,error:'',summary:null});
    }
    this.identity=id;this.state=state;this.phase=phase;this.capable=capable;
    if (!this.active || ['connecting','reconnecting','unavailable'].includes(phase)) return;
    const summaries=state?.analysis_summary || [];
    let summary=this.pending ? summaries.find(s=>s.operation_id===this.pending.operation_id) : null;
    if (!summary && !this.pending) summary=[...summaries].reverse().find(s=>matchesAnalysis(s,state));
    if (!summary) return;
    if (['queued','running'].includes(summary.status)) { this.emit({status:summary.status,summary,error:''});return; }
    if (this.handled.has(summary.operation_id)) return;
    if (summary.result_id && ['succeeded','partial'].includes(summary.status)) this.read(summary);
    else {
      this.handled.add(summary.operation_id);this.pending=null;
      this.emit({status:'failed',summary,error:summary.message || '分析结果已淘汰或不可用，请重新运行'});
    }
  }
  async run(settings) {
    if (!this.active || this.pending || this.posting || analysisBlocked(this.state,this.phase,this.capable)) return;
    const generation=this.generation;this.readEpoch++;this.reading=null;this.posting=true;this.emit({status:'queued',error:'',summary:null});
    try {
      const accepted=await this.api.analysis(settings,this.state,this.abort.signal);
      if (!this.active || generation!==this.generation) return;
      this.pending=accepted;this.update(this.state,this.phase,this.capable);
    } catch(e) {
      if (this.active && generation===this.generation) this.emit({status:'unknown',error:e.message});
      // An unknown POST is never replayed. Published summaries can recover it.
    } finally { if(generation===this.generation) this.posting=false; }
  }
  async read(summary) {
    if (this.reading===summary.operation_id) return;
    const generation=this.generation,epoch=++this.readEpoch;this.reading=summary.operation_id;
    try {
      let result;
      for(let attempt=0;attempt<2;attempt++) {
        try { result=await this.api.readAnalysis(summary.result_id,this.abort.signal);break; }
        catch(e) {
          if(attempt || e.status===404 || e.status===400 || this.abort.signal.aborted) throw e;
        }
      }
      if (!this.active || generation!==this.generation || epoch!==this.readEpoch) return;
      if(result.key!==summary.key || result.result_id!==summary.result_id || modelId(result.source)!==this.identity)
        throw new Error('分析来源不匹配');
      this.emit({result,status:result.status==='partial'?'partial':'ready',summary,error:''});
    } catch(e) { if(this.active && generation===this.generation && epoch===this.readEpoch) this.emit({status:'failed',error:e.message,summary}); }
    finally {
      if(this.active && generation===this.generation && epoch===this.readEpoch) {this.handled.add(summary.operation_id);this.pending=null;this.reading=null;}
    }
  }
  stop() { this.active=false;this.generation++;this.abort.abort(); }
}
