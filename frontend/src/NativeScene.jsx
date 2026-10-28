import React, { useMemo, useState, useLayoutEffect } from 'react';
import { motion } from 'motion/react';
import { Button, Modal, useBox } from './ui.jsx';
import { SurfacePicker } from './SurfacePicker.jsx';
import { projection } from './workbench-trace.js';
import './workbench-trace.css';

const colors = ['#c33f46', '#198868', '#bf841a', '#3974c5', '#bd5291', '#268f9c', '#9a722e', '#64852e', '#9a5e49'];
export function TraceControls({ trace, model, plane, setPlane }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const settings = trace.settings;
  if (!settings || !model) return null;
  const toggle = (name, index, max) => {
    const values = settings[name];
    const next = values.includes(index) ? values.filter(v=>v!==index) : values.length < max ? [...values,index] : values;
    if (next.length) trace.setSettings({ ...settings, [name]: next });
  };
  const labels = { waiting: '准备追迹', queued: '追迹排队中', running: '正在追迹', unknown: '结果待核对', failed: '追迹未完成' };
  const count = trace.result?.counts;
  return <>
    <div className="trace-toolbar">
      <Button className="trace-toggle" aria-pressed={trace.enabled} onClick={()=>trace.enable(!trace.enabled)}>
        <span className="trace-switch"><motion.span animate={{ x: trace.enabled ? 14 : 0 }} /></span>显示光线
      </Button>
      {trace.enabled && <>
        <Button onClick={()=>setSettingsOpen(true)}>追迹设置</Button>
        <div className="trace-projections" aria-label="光线投影">{['YZ','XZ'].map(p=><Button key={p} aria-pressed={plane===p} onClick={()=>setPlane(p)}>{p}</Button>)}</div>
      </>}
    </div>
    {trace.enabled && <div className="trace-status" role="status" aria-live="polite">
      {count ? <Button onClick={()=>setDetailsOpen(true)}>到达像面 {count.complete} · 截光 {count.vignetted} · 错误 {count.error}</Button>
        : <span>{trace.blocked || trace.error || (!trace.result && trace.status === 'ready' ? '结果已过期' : labels[trace.status]) || '等待可追迹模型'}</span>}
      <Button onClick={trace.retry} disabled={['running','queued'].includes(trace.status)}>重新追迹</Button>
    </div>}
    {settingsOpen && <Modal title="光线追迹" className="trace-settings-dialog" onClose={()=>setSettingsOpen(false)}>
      <h3>视场</h3><div className="trace-options">{(model.trace_fields || model.fields).filter(f=>!f.ignore&&f.is_active!==false).map(f=><Button key={f.index}
        aria-pressed={settings.fields.includes(f.index)} onClick={()=>toggle('fields',f.index,9)}>F{f.index} · {f.x}, {f.y}</Button>)}</div>
      <h3>波长</h3><div className="trace-options">{model.wavelengths.map(w=><Button key={w.index} aria-pressed={settings.wavelengths.includes(w.index)} onClick={()=>toggle('wavelengths',w.index,3)}>{w.wavelength_um} μm{w.is_primary ? ' · 主' : ''}</Button>)}</div>
      <h3>子午光瞳采样</h3><SurfacePicker label="光瞳采样数量" value={settings.pupil_count} onChange={n=>trace.setSettings({...settings,pupil_count:n})} options={[3,5,9].map(value=>({value,label:`${value} 条 / 视场 / 波长`}))} />
      <p>Px = 0 · Py = −1 至 1 · S1 → 像面</p>
      <Button className="studio-modal-done" onClick={()=>setSettingsOpen(false)}>完成</Button>
    </Modal>}
    {detailsOpen && trace.result && <Modal title="追迹结果" onClose={()=>setDetailsOpen(false)}>
      <p>{trace.result.elapsed_ms.toFixed(0)} ms · {trace.result.counts.total} 条 · {trace.result.units}</p>
      {trace.result.counts.both > 0 && <p>{trace.result.counts.both} 条同时报告错误和截光，两项统计重叠</p>}
      <div className="trace-diagnostics">{trace.result.rays.filter(r=>r.termination.kind!=='complete').map(r=><p key={r.id}>
        F{r.field.index} · {r.wavelength_um} μm · Py {r.py}：{r.termination.kind==='vignetted'?'截光':'追迹错误'}，首见 S{r.termination.first_observed_surface}，错误码 {r.termination.error_code} / 渐晕码 {r.termination.vignette_code}
      </p>)}{trace.result.counts.complete===trace.result.counts.total && <p>全部采样光线到达像面</p>}</div>
    </Modal>}
  </>;
}

export function NativeScene({ layout, trace, selected, onSelect, zoom, plane = 'YZ' }) {
  const [ref, { width, height }] = useBox();
  useLayoutEffect(()=>{ if(ref.current && trace) ref.current.dataset.traceRenderedAt=String(Date.now()); },[trace?.result_id]);
  const [hidden, setHidden] = useState([]);
  const view = useMemo(()=>projection(layout,trace,plane),[layout,trace,plane]);
  const fields = trace ? [...new Set(trace.rays.map(r=>r.field.index))] : [];
  const projected = trace?.rays.some(r=>r.points.some(p=>Math.abs(p.xyz[plane==='YZ'?0:1])>1e-8));
  const diagnostic = !view.contours;
  const bounds = view.bounds;
  let path = ()=>'';
  let x = ()=>0, y = ()=>0;
  if (bounds) {
    const [z0,y0,z1,y1] = bounds;
    const spanZ = Math.max(z1-z0,1e-9), spanY = Math.max(y1-y0,1e-9);
    const scale = Math.min(Math.max(1,width-76)/spanZ, Math.max(1,height-70)/spanY)*zoom/100;
    const centre = zoom>100 ? view.contours?.surfaces.find(s=>s.index===selected)?.vertex_z ?? z0/2+z1/2 : z0/2+z1/2;
    x = z=>width/2+(z-centre)*scale; y = v=>height/2-(v-y0/2-y1/2)*scale;
    path = points=>points.map(([z,v],i)=>`${i?'L':'M'}${x(z)} ${y(v)}`).join(' ');
  }
  return <>
    {diagnostic && <div className="trace-layout-diagnostic" role="status">
      <p>{trace?.counts.drawable ? '光线诊断视图 · 镜片轮廓未显示' : plane==='XZ' ? 'XZ 光线投影' : '二维轮廓暂不可用'}</p>
      {plane==='YZ' && <p>{layout.issues.join('；')}</p>}
    </div>}
    <div className="studio-scene trace-scene" ref={ref} data-result-id={trace?.result_id}>
      {view.issue && <div className="trace-no-rays" role="status">{view.issue}</div>}
      {bounds && <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${plane} ${projected?'三维路径投影':'光路'}，横纵同尺度，单位 ${layout.units}；${trace?'原生实光线 S1 至像面':'未绘制光线'}`}>
        <line x1="16" x2={width-16} y1={y(0)} y2={y(0)} className="studio-axis" strokeDasharray="3 7" />
        {view.contours?.elements.map(e=><path key={e.front} d={`${path(e.points)} Z`} className="studio-native-glass" />)}
        {view.contours?.surfaces.map(s=><g key={s.index}>
          <path d={path(s.points)} className={s.is_image?'studio-image-plane':'studio-native-surface'} style={{stroke:selected===s.index?'var(--st-text)':undefined,strokeWidth:selected===s.index?2.4:undefined}} />
          <path d={path(s.points)} className="studio-native-hit cursor-interaction" onClick={()=>onSelect(s.index)} />
          {s.is_stop&&!s.is_image&&<>{[s.points[0],s.points.at(-1)].map((p,i)=><line key={i} x1={x(s.vertex_z)} x2={x(s.vertex_z)} y1={y(p[1])} y2={y(p[1])+(i?-9:9)} className="studio-native-stop" />)}</>}
        </g>)}
        {view.rays.filter(r=>!hidden.includes(r.field.index)&&r.projected.length>=2).map(r=><g key={r.id} className="trace-ray" style={{color:colors[(r.field.index-1)%colors.length]}}>
          <path d={path(r.projected)} fill="none" stroke="currentColor" strokeWidth={r.py===0?1.9:1.1} strokeDasharray={trace.settings.wavelengths.indexOf(r.wave_index)%2?'5 3':undefined} opacity={r.py===0?1:.72} />
          {r.termination.kind!=='complete'&&<circle cx={x(r.projected.at(-1)[0])} cy={y(r.projected.at(-1)[1])} r="3" fill="var(--st-panel)" stroke="currentColor" />}
        </g>)}
      </svg>}
      {trace && !trace.counts.drawable && <div className="trace-no-rays" role="status">没有可绘制的连续光线，请查看追迹结果</div>}
    </div>
    {trace && <div className="trace-legend" aria-label="视场图例">{fields.map(f=><Button key={f} aria-pressed={!hidden.includes(f)} onClick={()=>setHidden(h=>h.includes(f)?h.filter(x=>x!==f):[...h,f])}>
      <span style={{background:colors[(f-1)%colors.length]}} />F{f}</Button>)}<span>{trace.settings.wavelengths.map(w=>trace.rays.find(r=>r.wave_index===w)?.wavelength_um).join(' / ')} μm · {projected?'三维投影 · ':''}{plane}</span></div>}
  </>;
}
