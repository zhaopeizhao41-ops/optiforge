import React, { useMemo, useRef, useState, useLayoutEffect } from 'react';
import { motion } from 'motion/react';
import { Activity, RefreshCw, SlidersHorizontal } from 'lucide-react';
import { Button, Modal, useBox } from './ui.jsx';
import { SurfacePicker } from './SurfacePicker.jsx';
import { projection } from './workbench-trace.js';
import { wheelZoom, SceneDrag, frameBounds } from './workbench-camera.js';
import './workbench-trace.css';

const colors = ['#c33f46', '#198868', '#bf841a', '#3974c5', '#bd5291', '#268f9c', '#9a722e', '#64852e', '#9a5e49'];
export function TraceControls({ trace, model, layout }) {
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
  const issues = [...new Set([...(layout?.issues || []), ...(layout?.validation_issues || [])])];
  return <>
    <div className="trace-toolbar">
      <Button className="trace-toggle" aria-pressed={trace.enabled} onClick={()=>trace.enable(!trace.enabled)}>
        <span className="trace-switch"><motion.span animate={{ x: trace.enabled ? 14 : 0 }} /></span>显示光线
      </Button>
      {trace.enabled && <>
        <Button onClick={()=>setSettingsOpen(true)} aria-label="追迹设置"><SlidersHorizontal size={16} /><span>设置</span></Button>
      </>}
      <Button className="trace-info-button" onClick={()=>setDetailsOpen(true)} aria-label="光路详情">
        <Activity size={16} /><span>光路详情</span>{(issues.length>0||trace.error)&&<span className="trace-info-dot" />}
      </Button>
    </div>
    {trace.enabled && trace.message && <div className="trace-status" role="status" aria-live="polite">{trace.message}</div>}
    {settingsOpen && <Modal title="光线追迹" className="trace-settings-dialog" onClose={()=>setSettingsOpen(false)}>
      <h3>视场</h3><div className="trace-options">{(model.trace_fields || model.fields).filter(f=>!f.ignore&&f.is_active!==false).map(f=><Button key={f.index}
        aria-pressed={settings.fields.includes(f.index)} onClick={()=>toggle('fields',f.index,9)}>F{f.index} · {f.x}, {f.y}</Button>)}</div>
      <h3>波长</h3><div className="trace-options">{model.wavelengths.map(w=><Button key={w.index} aria-pressed={settings.wavelengths.includes(w.index)} onClick={()=>toggle('wavelengths',w.index,3)}>{w.wavelength_um} μm{w.is_primary ? ' · 主' : ''}</Button>)}</div>
      <h3>光瞳采样</h3><SurfacePicker label="光瞳采样数量" value={settings.pupil_count} onChange={n=>trace.setSettings({...settings,pupil_count:n})} options={[3,5,9].map(value=>({value,label:`${value} 条 / 视场 / 波长`}))} />
      <p>从 S1 追迹至像面</p>
      {trace.settingsAdjusted && <p role="status">视场或波长表已更新，已采用当前模型中的可用项</p>}
      <Button className="studio-modal-done" onClick={()=>setSettingsOpen(false)}>完成</Button>
    </Modal>}
    {detailsOpen && <Modal title="光路详情" className="trace-details-dialog" onClose={()=>setDetailsOpen(false)}>
      {trace.enabled && <>
        {count ? <div className="trace-result-counts">{[['到达像面',count.complete],['截光',count.vignetted],['错误',count.error]].map(([label,value])=><div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
          : <p className="trace-dialog-status" role="status">{trace.blocked || trace.error || labels[trace.status] || '等待可追迹模型'}</p>}
        <Button className="trace-rerun" onClick={trace.retry} disabled={Boolean(trace.blocked)||['running','queued'].includes(trace.status)}><RefreshCw size={16} />重新追迹</Button>
      </>}
      {trace.result && <>
      <p>{trace.result.elapsed_ms.toFixed(0)} ms · {trace.result.counts.total} 条 · {trace.result.units}</p>
      {trace.result.counts.both > 0 && <p>{trace.result.counts.both} 条同时报告错误和截光，两项统计重叠</p>}
      <div className="trace-diagnostics">{trace.result.rays.filter(r=>r.termination.kind!=='complete').map(r=><p key={r.id}>
        F{r.field.index} · {r.wavelength_um} μm · Px {r.px}, Py {r.py}：{r.termination.kind==='vignetted'?'截光':'追迹错误'}，首见 S{r.termination.first_observed_surface}，错误码 {r.termination.error_code} / 渐晕码 {r.termination.vignette_code}
      </p>)}{trace.result.counts.complete===trace.result.counts.total && <p>全部采样光线到达像面</p>}</div></>}
      <h3>截面显示</h3><p>{layout?.status==='ready' ? layout.source==='zemax-primary-sag' ? 'Zemax 主矢高与机械半口径' : '处方截面' : '当前轮廓不可用'}</p>
      {issues.length>0&&<div className="trace-diagnostics"><h3>几何检查</h3>{issues.map((issue,i)=><p key={i}>{issue}</p>)}</div>}
      {!trace.enabled&&<p>开启“显示光线”后读取真实追迹结果</p>}
    </Modal>}
  </>;
}

export function NativeScene({ layout, trace, selected, onSelect, camera, onCameraChange }) {
  const [ref, { width, height }] = useBox();
  useLayoutEffect(()=>{ if(ref.current && trace) ref.current.dataset.traceRenderedAt=String(Date.now()); },[trace?.result_id]);
  const [hidden, setHidden] = useState([]);
  const view = useMemo(()=>projection(layout,trace,'YZ'),[layout,trace]);
  const fields = trace ? [...new Set(trace.rays.map(r=>r.field.index))] : [];
  const projected = trace?.rays.some(r=>r.points.some(p=>Math.abs(p.xyz[0])>1e-8));
  const bounds = view.bounds;
  // Once the user moves the camera, new trace bounds must not reframe the
  // optical content beneath it. Home always fits the latest complete content.
  const framing = useRef(null);
  framing.current=frameBounds(framing.current,bounds,camera);
  const [gesture] = useState(()=>new SceneDrag());
  const current = useRef(null);
  current.current = { bounds, onCameraChange };
  const [dragging, setDragging] = useState(false);
  useLayoutEffect(()=>{
    const scene = ref.current;
    const wheel = event=>{
      if (!current.current.bounds || !event.deltaY || !Number.isFinite(event.deltaY)) return;
      const box = scene.getBoundingClientRect();
      event.preventDefault();
      current.current.onCameraChange(c=>wheelZoom(c,event.deltaY,event.deltaMode,box.height,
        { x:event.clientX-box.left-box.width/2, y:event.clientY-box.top-box.height/2 }));
    };
    scene.addEventListener('wheel',wheel,{passive:false});
    return ()=>scene.removeEventListener('wheel',wheel);
  },[ref]);
  const finish = event=>{
    const ended = gesture.end(event.pointerId,event.clientX,event.clientY,event.type!=='pointerup');
    if (!ended) return;
    setDragging(false);
    if (Number.isInteger(ended.surface)) onSelect(ended.surface);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  let path = ()=>'';
  let x = ()=>0, y = ()=>0;
  if (bounds) {
    const [z0,y0,z1,y1] = framing.current;
    const spanZ = Math.max(z1-z0,1e-9), spanY = Math.max(y1-y0,1e-9);
    const scale = Math.min(Math.max(1,width-76)/spanZ, Math.max(1,height-70)/spanY)*camera.zoom/100;
    x = z=>width/2+camera.x+(z-z0/2-z1/2)*scale; y = v=>height/2+camera.y-(v-y0/2-y1/2)*scale;
    path = points=>points.map(([z,v],i)=>`${i?'L':'M'}${x(z)} ${y(v)}`).join(' ');
  }
  return <>
    <div className="studio-scene trace-scene" ref={ref} data-result-id={trace?.result_id}
      data-zoom={camera.zoom} data-pan-x={camera.x} data-pan-y={camera.y} data-dragging={dragging}
      onPointerDown={event=>{
        if (event.button!==0 || !event.isPrimary || !bounds) return;
        const hit = event.target.closest('[data-surface-index]');
        if (!gesture.begin(event.pointerId,event.clientX,event.clientY,camera,hit ? Number(hit.dataset.surfaceIndex) : null)) return;
        event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault();
      }}
      onPointerMove={event=>{
        const moved = gesture.move(event.pointerId,event.clientX,event.clientY);
        if (moved) { setDragging(true); onCameraChange(moved); }
      }}
      onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={finish}>
      {view.issue && <div className="trace-no-rays" role="status">{view.issue}</div>}
      {bounds && <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`2D ${projected?'三维路径投影':'光路'}，横纵同尺度，单位 ${layout.units}；${trace?'原生实光线 S1 至像面':'未绘制光线'}`}>
        <line x1="16" x2={width-16} y1={y(0)} y2={y(0)} className="studio-axis" strokeDasharray="3 7" />
        {view.contours?.elements.map(e=><path key={e.front} d={`${path(e.points)} Z`} className="studio-native-glass" />)}
        {view.contours?.surfaces.map(s=><g key={s.index}>
          <path d={path(s.points)} className={s.is_image?'studio-image-plane':'studio-native-surface'} style={{stroke:selected===s.index?'var(--st-text)':undefined,strokeWidth:selected===s.index?2.4:undefined}} />
          <path d={path(s.points)} className="studio-native-hit" data-surface-index={s.index} />
          {s.is_stop&&!s.is_image&&<>{[s.points[0],s.points.at(-1)].map((p,i)=><line key={i} x1={x(s.vertex_z)} x2={x(s.vertex_z)} y1={y(p[1])} y2={y(p[1])+(i?-9:9)} className="studio-native-stop" />)}</>}
        </g>)}
        {view.rays.filter(r=>!hidden.includes(r.field.index)&&r.projected.length>=2).map(r=><g key={r.id} className="trace-ray" style={{color:colors[(r.field.index-1)%colors.length]}}>
          <path d={path(r.projected)} fill="none" stroke="currentColor" strokeWidth={r.px===0&&r.py===0?1.9:1.1} strokeDasharray={trace.settings.wavelengths.indexOf(r.wave_index)%2?'5 3':undefined} opacity={r.px===0&&r.py===0?1:.72} />
          {r.termination.kind!=='complete'&&<circle cx={x(r.projected.at(-1)[0])} cy={y(r.projected.at(-1)[1])} r="3" fill="var(--st-panel)" stroke="currentColor" />}
        </g>)}
      </svg>}
      {trace && !trace.counts.drawable && <div className="trace-no-rays" role="status">没有可绘制的连续光线，请查看追迹结果</div>}
      {!bounds&&!trace&&<div className="trace-no-rays" role="status">暂无可用轮廓，请查看光路详情</div>}
    </div>
    {trace && <div className="trace-legend" aria-label="视场图例">{fields.map(f=><Button key={f} aria-pressed={!hidden.includes(f)} onClick={()=>setHidden(h=>h.includes(f)?h.filter(x=>x!==f):[...h,f])}>
      <span style={{background:colors[(f-1)%colors.length]}} />F{f}</Button>)}<span>{trace.settings.wavelengths.map(w=>trace.rays.find(r=>r.wave_index===w)?.wavelength_um).join(' / ')} μm{projected?' · 三维投影':''}</span></div>}
  </>;
}
