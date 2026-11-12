import React,{useEffect,useMemo,useRef,useState} from 'react';
import { motion } from 'motion/react';
import { ArrowLeft,Play,RefreshCw,Settings2,Focus,Info,ChevronDown } from 'lucide-react';
import { Button,Modal } from './ui.jsx';
import { SurfacePicker } from './SurfacePicker.jsx';
import { analysisNames,defaultAnalysisSettings,formatAxisTick } from './workbench-analysis.js';
import './workbench-analysis.css';
import AnalysisHistory from './AnalysisHistory.jsx';
import { qualityMetrics } from './stage-quality-artifact.js';
import { resolveFocus } from './workbench-ui.js';

const colors=['#cc4e4e','#279478','#d1902e','#35949b','#a76841','#b75c70','#7a8e31','#545454'];
const fmt=v=>v===null || v===undefined?'—':Number(v).toLocaleString(undefined,{maximumSignificantDigits:5});
const labelOf=c=>[c.field_index && `F${c.field_index}`,c.wavelength_um && `${c.wavelength_um} μm`,
  {tangential:'T',sagittal:'S',distortion:'畸变'}[c.kind]].filter(Boolean).join(' · ');
const statusText={idle:'尚未计算',queued:'排队中',running:'正在计算',ready:'计算完成',partial:'存在未完成项目',failed:'计算未完成',unknown:'请求结果待核对'};

export function CurvePlot({curves,title,xLabel,yLabel,scatter=false,domain,reference}) {
  const [hidden,setHidden]=useState([]),[zoom,setZoom]=useState(1),[hover,setHover]=useState(null);
  const svg=useRef(null);
  const active=curves.filter((c,i)=>!hidden.includes(i));
  const ring=scatter && Number.isFinite(reference?.radius) && reference.radius>0 ? reference : null;
  const points=active.flatMap(c=>scatter?c.points:c.x.map((x,i)=>[x,c.y[i]])).filter(p=>p.every(Number.isFinite))
    .concat(ring?[[-ring.radius,-ring.radius],[ring.radius,ring.radius]]:[]);
  let xmin=Math.min(...points.map(p=>p[0])),xmax=Math.max(...points.map(p=>p[0]));
  let ymin=Math.min(...points.map(p=>p[1])),ymax=Math.max(...points.map(p=>p[1]));
  if(!Number.isFinite(xmin)) {xmin=-1;xmax=1;ymin=-1;ymax=1;}
  if(domain) [xmin,xmax,ymin,ymax]=domain;
  const cx=(xmin+xmax)/2,cy=(ymin+ymax)/2;
  let hx=(xmax-xmin || Math.max(Math.abs(cx)*.1,.01))*(domain?.5:.55)/zoom;
  let hy=(ymax-ymin || Math.max(Math.abs(cy)*.1,.01))*(domain?.5:.55)/zoom;
  const width=640,height=scatter?500:390,left=110,right=40,top=24,bottom=62;
  const pw=width-left-right,ph=height-top-bottom;
  if(scatter) {const scale=Math.max(hx/pw,hy/ph);hx=scale*pw;hy=scale*ph;}
  xmin=cx-hx;xmax=cx+hx;ymin=cy-hy;ymax=cy+hy;
  const px=x=>left+(x-xmin)/(xmax-xmin)*pw,py=y=>top+(ymax-y)/(ymax-ymin)*ph;
  const clip=React.useId().replaceAll(':','');
  const wheel=e=>{e.preventDefault();setZoom(z=>Math.min(16,Math.max(1,z*Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.005))))};
  useEffect(()=>{const el=svg.current;el?.addEventListener('wheel',wheel,{passive:false});return ()=>el?.removeEventListener('wheel',wheel);},[]);
  const nearest=e=>{
    if(scatter) return;
    const box=e.currentTarget.getBoundingClientRect(),x=xmin+((e.clientX-box.left)/box.width*width-left)/pw*(xmax-xmin);
    const found=active.map(c=>{
      const i=c.x.reduce((best,v,j)=>Math.abs(v-x)<Math.abs(c.x[best]-x)?j:best,0);
      return {...c,point:[c.x[i],c.y[i]]};
    }).filter(c=>Number.isFinite(c.point[1]));
    setHover(found);
  };
  return <div className="analysis-plot">
    <div className="analysis-plot-title"><h3>{title}</h3><Button className="studio-icon-button" aria-label={`复位${title}`} onClick={()=>{setZoom(1);setHidden([]);}}><Focus size={17}/></Button></div>
    <svg ref={svg} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}，${xLabel}，${yLabel}${scatter?'，横纵等比例':''}`}
      onPointerMove={nearest} onPointerLeave={()=>setHover(null)}>
      <defs><clipPath id={clip}><rect x={left} y={top} width={pw} height={ph}/></clipPath></defs>
      {Array.from({length:5},(_,i)=>{
        const x=xmin+(xmax-xmin)*i/4,y=ymin+(ymax-ymin)*i/4;
        return <g key={i} className="analysis-grid"><line x1={px(x)} y1={top} x2={px(x)} y2={top+ph}/><line x1={left} y1={py(y)} x2={left+pw} y2={py(y)}/>
          <text x={px(x)} y={height-36} textAnchor="middle">{formatAxisTick(x,(xmax-xmin)/4)}</text><text x={left-10} y={py(y)+4} textAnchor="end">{formatAxisTick(y,(ymax-ymin)/4)}</text></g>;
      })}
      <g clipPath={`url(#${clip})`}>{curves.map((c,i)=>hidden.includes(i)?null:scatter?
        <g key={i} fill={colors[i%colors.length]}>{c.points.filter(p=>p.every(Number.isFinite)).map((p,j)=><circle key={j} cx={px(p[0])} cy={py(p[1])} r="1.8"/>)}</g>:
        <path key={i} fill="none" stroke={colors[i%colors.length]} strokeWidth="1.8" strokeDasharray={c.kind==='sagittal'?'7 4':undefined}
          d={c.x.reduce((a,x,j)=>{const y=c.y[j];return {...a,d:a.d+(Number.isFinite(y)?`${a.valid?'L':'M'}${px(x)},${py(y)} `:''),valid:Number.isFinite(y)};},{d:'',valid:false}).d}/>)}</g>
      {ring && <g className="analysis-reference" clipPath={`url(#${clip})`}>
        <circle cx={px(0)} cy={py(0)} r={ring.radius/(xmax-xmin)*pw} fill="none"/>
        <text x={left+10} y={top+18}>◌ {ring.label}</text></g>}
      <text x={left+pw/2} y={height-7} textAnchor="middle" className="analysis-axis">{xLabel}</text>
      <text transform={`translate(17 ${top+ph/2}) rotate(-90)`} textAnchor="middle" className="analysis-axis">{yLabel}</text>
    </svg>
    <div className="analysis-legend">{curves.map((c,i)=><Button key={i} aria-pressed={!hidden.includes(i)} onClick={()=>setHidden(v=>v.includes(i)?v.filter(x=>x!==i):[...v,i])}>
      <span style={{background:colors[i%colors.length]}}/>{labelOf(c) || c.label}</Button>)}</div>
    {hover && <div className="analysis-readout" role="status">{hover.map((c,i)=><span key={i}>{labelOf(c)} · {fmt(c.point[0])} / {fmt(c.point[1])}</span>)}</div>}
  </div>;
}

function Wavefront({item,field}) {
  const f=item.fields.find(f=>f.field_index===field)||item.fields[0];
  const canvas=useRef(null),[zoom,setZoom]=useState(1),[point,setPoint]=useState(null);
  const g=f.grid;
  const [minimum,maximum]=useMemo(()=>g.values.reduce(([lo,hi],row)=>row.reduce(([a,b],v)=>Number.isFinite(v)?[Math.min(a,v),Math.max(b,v)]:[a,b],[lo,hi]),[Infinity,-Infinity]),[g]);
  useEffect(()=>{
    const c=canvas.current,ctx=c.getContext('2d');c.width=g.nx;c.height=g.ny;
    const image=ctx.createImageData(g.nx,g.ny),min=minimum,max=maximum,span=max-min || 1;
    for(let row=0;row<g.ny;row++) for(let col=0;col<g.nx;col++) {
      const dest=((g.ny-1-row)*g.nx+col)*4; // native rows, matched to official text; no pupil coordinate calibration
      if(!g.valid_mask[row][col]) continue;
      const t=(g.values[row][col]-min)/span;
      const rgb=t<.5?[53+(234-53)*t*2,148+(239-148)*t*2,155+(235-155)*t*2]:[234+(205-234)*(t-.5)*2,239+(107-239)*(t-.5)*2,235+(63-235)*(t-.5)*2];
      rgb.forEach((v,i)=>image.data[dest+i]=Math.round(v));image.data[dest+3]=255;
    }
    ctx.putImageData(image,0,0);setZoom(1);setPoint(null);
  },[g,minimum,maximum]);
  return <div className="analysis-wavefront">
    <div className="analysis-plot-title"><h3>OPD · F{f.field_index}</h3><Button className="studio-icon-button" aria-label="复位波前" onClick={()=>setZoom(1)}><Focus size={17}/></Button></div>
    <div className="analysis-opd" onWheel={e=>{e.stopPropagation();setZoom(v=>Math.min(4,Math.max(1,v*(e.deltaY<0?1.12:.89))));}}>
      <canvas ref={canvas} style={{transform:`scale(${zoom})`}} aria-label={`F${f.field_index} 原生 OPD 热图，孔径外透明，单位 waves`}
        onPointerMove={e=>{const b=e.currentTarget.getBoundingClientRect(),col=Math.floor((e.clientX-b.left)/b.width*g.nx),row=g.ny-1-Math.floor((e.clientY-b.top)/b.height*g.ny);
          setPoint(g.valid_mask[row]?.[col]?{row,col,value:g.values[row][col]}:null);}} onPointerLeave={()=>setPoint(null)}/>
    </div>
    <div className="analysis-color-scale" aria-label="OPD 色标，单位 waves"><span>{fmt(minimum)}</span><span aria-hidden="true"/><span>{fmt(maximum)} λ</span></div>
    <p className="analysis-plot-meta">{f.wavelength_um} μm · {g.nx} × {g.ny} · waves {point && ` · 原生行 ${point.row+1} 列 ${point.col+1} · OPD ${fmt(point.value)}`}</p>
    <div className="analysis-metrics"><div><span>RMS</span><strong>{fmt(g.rms_waves)} λ</strong></div><div><span>PV</span><strong>{fmt(g.pv_waves)} λ</strong></div><div><span>Strehl 估算</span><strong>{fmt(g.strehl_estimate)}</strong></div></div>
    {!g.strehl_small_aberration && <p className="analysis-warning">像差超出小像差近似范围，Strehl 估算仅供参考</p>}
    <div className="studio-data-table"><table><thead><tr><th>视场</th><th>RMS / λ</th><th>PV / λ</th><th>Strehl 估算</th></tr></thead><tbody>{item.fields.map(f=><tr key={f.field_index}><td>F{f.field_index}</td><td>{fmt(f.grid.rms_waves)}</td><td>{fmt(f.grid.pv_waves)}</td><td>{fmt(f.grid.strehl_estimate)}</td></tr>)}</tbody></table></div>
  </div>;
}

// Headline numbers read from the stored result. Gate rows follow the verdict table;
// spot size is diagnostic INFO and labelled as such.
export function QualityTiles({metrics}) {
  const rows=[['最差波前 RMS',metrics.worstWavefrontRms,'λ','门禁 ≤ 0.0714'],['30 lp/mm 最低 MTF',metrics.minMtf30,'',metrics.mtfThreshold!==null?`门禁 ≥ ${fmt(metrics.mtfThreshold)}`:'未设阈值 · INFO'],
    ['最差点列 RMS',metrics.worstSpotRms,'μm','诊断 INFO'],['最大畸变',metrics.maxDistortion,'%','未设阈值时 INFO']];
  return <div className="analysis-tiles">{rows.map(([label,value,unit,note])=><div key={label}><span>{label}</span>
    <strong>{fmt(value)}{value!==null && unit && <small> {unit}</small>}</strong><em>{note}</em></div>)}</div>;
}

function AnalysisSettings({model,settings,onChange,onClose}) {
  const [draft,setDraft]=useState(settings),[error,setError]=useState('');
  const put=(k,v)=>setDraft(s=>({...s,[k]:v}));
  const toggle=(key,value)=>put(key,draft[key].includes(value)?draft[key].filter(v=>v!==value):[...draft[key],value]);
  return <Modal title="分析设置" onClose={onClose} className="analysis-settings">
    <fieldset><legend>分析项目</legend><div className="analysis-checks">{Object.entries(analysisNames).map(([k,label])=><label key={k}><input type="checkbox" checked={draft.analyses.includes(k)} onChange={()=>toggle('analyses',k)}/>{label}</label>)}</div></fieldset>
    <fieldset><legend>实际视场 · 最多五个</legend><div className="analysis-checks">{(model.trace_fields || model.fields).filter(f=>!f.ignore && f.is_active!==false).map(f=><label key={f.index}><input type="checkbox" checked={draft.fields.includes(f.index)} onChange={()=>toggle('fields',f.index)}/>F{f.index} · {fmt(f.x)}, {fmt(f.y)}</label>)}</div></fieldset>
    <div className="analysis-setting-grid">
      <div className="analysis-setting-picker"><span>MTF 采样</span><SurfacePicker label="MTF 采样" value={draft.sample_size}
        options={[128,256,512].map(n=>({value:`${n}x${n}`,label:`${n}x${n}`}))} onChange={value=>put('sample_size',value)}/></div>
      <label>最高频率 / lp/mm<input type="number" min=".001" max="100000" value={draft.max_frequency} onChange={e=>put('max_frequency',Number(e.target.value))}/></label>
      <div className="analysis-setting-picker"><span>波前采样</span><SurfacePicker label="波前采样" value={draft.wavefront_size}
        options={[128,256].map(value=>({value,label:`${value} × ${value}`}))} onChange={value=>put('wavefront_size',value)}/></div>
      <div className="analysis-setting-picker"><span>点列环数</span><SurfacePicker label="点列环数" value={draft.spot_rings}
        options={[3,6,9,12].map(value=>({value,label:String(value)}))} onChange={value=>put('spot_rings',value)}/></div>
    </div>
    <details><summary>可选验收阈值<ChevronDown size={16}/></summary><div className="analysis-setting-grid">
      <label>30 lp/mm 最低 MTF<input type="number" min="0" max="1" step=".01" placeholder="未指定" value={draft.mtf_min_at_30lp_mm??''} onChange={e=>put('mtf_min_at_30lp_mm',e.target.value===''?null:Number(e.target.value))}/></label>
      <label>最大畸变 / %<input type="number" min="0" placeholder="未指定" value={draft.distortion_max_pct??''} onChange={e=>put('distortion_max_pct',e.target.value===''?null:Number(e.target.value))}/></label>
    </div></details>
    <p>MTF、点列和光线像差使用系统全部波长及权重；波前使用实际主波长。场曲为原生 +Y 连续扫描。</p>
    {error && <p role="alert" className="analysis-warning">{error}</p>}
    <Button className="analysis-run" onClick={()=>{
      if(!draft.fields.length || draft.fields.length>5 || !draft.analyses.length || !Number.isFinite(draft.max_frequency) || draft.max_frequency<=0) {setError('请选择分析项目、1–5 个视场及有效频率');return;}
      onChange(draft);onClose();
    }}>应用设置</Button>
  </Modal>;
}

export default function AnalysisWorkspace({sync,analysis,onBack,focusRequest,onFocusReport}) {
  const model=sync.engine?.snapshot?.model;
  const identity=`${sync.engine?.service_instance_id}:${sync.engine?.model_id}`;
  const [draft,setDraft]=useState(null),[tab,setTab]=useState('overview'),[settingsOpen,setSettingsOpen]=useState(false),[sourceOpen,setSourceOpen]=useState(false),[field,setField]=useState(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const r=analysis.result;
  const settings=draft?.identity===identity?draft.settings:r?.settings || (model?defaultAnalysisSettings(model):null);
  const busy=['running','queued'].includes(analysis.status);
  const resultFields=r?.settings.fields || [];
  const currentField=resultFields.includes(field)?field:resultFields[0];
  const item=r?.items[tab];
  const curves=useMemo(()=>item?.curves?.filter(c=>!c.field_index || c.field_index===currentField) || [],[item,currentField]);
  const spot=useMemo(()=>r?.items.spot?.fields?.find(f=>f.field_index===currentField),[r,currentField]);
  // Only a different result or actual plot field resets local interaction.
  // Derived curve arrays can change identity during unrelated parent renders.
  const plotKey=`${r?.result_id}:${tab}:${tab==='field_curvature'?'continuous':currentField}`;
  const notice=analysis.blocked || analysis.error;
  const handledFocus=useRef(null);
  useEffect(()=>{
    if(focusRequest?.page!=='analysis' || handledFocus.current===focusRequest.id) return;
    handledFocus.current=focusRequest.id;
    const result=resolveFocus('analysis',focusRequest.focus,{fields:r?r.settings.fields:null});
    if(result.actual){setTab(result.actual.tab);if(result.actual.field)setField(result.actual.field);}
    onFocusReport?.(focusRequest.id,result.actual,result.reason);
  },[focusRequest,r]);
  return <motion.section className="studio-analysis-page analysis-workspace" initial={{opacity:0,y:10}} animate={{opacity:1,y:0}} exit={{opacity:0}}>
    <div className="studio-analysis-title"><Button className="studio-icon-button studio-back" aria-label="返回光路" onClick={onBack}><ArrowLeft size={19}/></Button><h1>像质分析</h1>
      <div className="analysis-actions"><Button onClick={()=>setHistoryOpen(true)}>分析历史</Button><Button aria-label="分析设置" className="studio-icon-button" disabled={!model || busy} onClick={()=>setSettingsOpen(true)}><Settings2 size={19}/></Button>
        <Button className="analysis-run" disabled={!settings || Boolean(analysis.blocked) || busy} onClick={()=>analysis.run(settings)}>{r?<RefreshCw size={17}/>:<Play size={17}/>}<span>{busy?'计算中':r?'重新分析':'运行分析'}</span></Button></div></div>
    <div className="analysis-summary"><span>{model?.name || '未连接模型'}</span><span>配置 {model?.configuration??'—'}</span>
      <span role="status">{statusText[analysis.status]}{busy && analysis.summary?.current_item && ` · ${analysisNames[analysis.summary.current_item]}${analysis.summary.current_field?` F${analysis.summary.current_field}`:''}`}</span>
      {r && <span>{(r.elapsed_ms/1000).toFixed(1)} s</span>}</div>
    {notice && <div className="analysis-warning" role="status">{notice}</div>}
    {r && !analysis.current && <div className="analysis-warning analysis-stale" role="status">历史结果 · 模型、授权或连接状态已变化，请重新核对并运行</div>}
    <nav className="analysis-tabs" aria-label="分析项目">{[['overview','总览'],...Object.entries(analysisNames)].map(([id,label])=><Button key={id} aria-pressed={tab===id} onClick={()=>setTab(id)}>{tab===id && <motion.span className="analysis-tab-highlight" layoutId="analysis-tab"/>}<span>{label}</span></Button>)}</nav>
    {!r ? <div className="studio-analysis-card analysis-empty"><div className="analysis-empty-mark" aria-hidden="true"/><h2>从真实模型开始</h2><p>选择设置，运行一次完整分析</p>
      {settings && <div className="analysis-coverage">视场 {settings.fields.map(i=>`F${i}`).join(' · ')}<br/>波长 {model.wavelengths.map(w=>`${w.wavelength_um} μm${w.is_primary?'（主）':''}`).join(' · ')}</div>}</div>
      : tab==='overview'?<div className="analysis-overview"><div className="analysis-result-heading"><h2>{analysis.current?'已覆盖范围':'历史记录'}</h2><span className={`analysis-verdict verdict-${r.overall.toLowerCase()}`}>{analysis.current?r.overall:'过期'}</span></div>
        <p>视场 {r.settings.fields.map(i=>`F${i}`).join(' · ')} · 波长 {r.model_metadata.wavelengths.map(w=>`${w.wavelength_um} μm`).join(' · ')}</p>
        <QualityTiles metrics={qualityMetrics(r)}/>
        <div className="analysis-result-list">{r.verdicts.map(v=><Button key={v.analysis} onClick={()=>setTab(v.analysis)}><span>{analysisNames[v.analysis]}</span><span className={`analysis-verdict verdict-${v.verdict.toLowerCase()}`}>{analysis.current?v.verdict:'历史'}</span><span>{r.items[v.analysis]?.status==='error'?r.items[v.analysis].message:v.criteria}</span></Button>)}</div>
        <p>点列和光线像差为诊断项。未指定 MTF 或畸变阈值时显示 INFO。</p>
      </div>:!item?<div className="analysis-empty studio-analysis-card"><p>本次未选择此项</p></div>:item.status!=='success'?<div className="analysis-empty studio-analysis-card"><h2>此项未完成</h2><p>{item.message}</p></div>:
        <div className="studio-analysis-card analysis-chart-card">
          {tab!=='field_curvature' && <div className="analysis-chart-toolbar"><div className="analysis-result-field"><span>视场</span>
            <SurfacePicker label="分析结果视场" value={currentField} options={resultFields.map(value=>({value,label:`F${value}`}))} onChange={setField}/></div></div>}
          {tab==='mtf' && <CurvePlot key={plotKey} curves={curves} title="多色 FFT MTF" xLabel="空间频率 / lp/mm" yLabel="MTF" domain={[0,r.settings.max_frequency,0,1]}/>}
          {tab==='wavefront' && <Wavefront item={item} field={currentField}/>}
          {tab==='spot' && spot && <><CurvePlot key={plotKey} curves={spot.series.map(s=>({...s,field_index:spot.field_index}))} title="真实像面点列" xLabel="X / μm" yLabel="Y / μm" scatter
            reference={r.model_metadata?.airy_radius_um?{radius:r.model_metadata.airy_radius_um,label:`艾里半径 ${fmt(r.model_metadata.airy_radius_um)} μm · 诊断`}:null}/>
            <div className="analysis-metrics"><div><span>RMS 半径 · INFO</span><strong>{fmt(spot.rms_radius_um)} μm</strong></div><div><span>有效 / 总数</span><strong>{spot.counts.valid} / {spot.counts.total}</strong></div><div><span>截光 / 失败</span><strong>{spot.counts.vignetted} / {spot.counts.failed}</strong></div></div>
            <p>主波长主光线参考 · 波长加权 · 仅剩余有效光线</p></>}
          {tab==='ray_fan' && <><CurvePlot key={`${plotKey}:t`} curves={curves.filter(c=>c.kind==='tangential')} title="子午 · Ey(Py)" xLabel="Py / 归一化光瞳" yLabel="横向像差 / μm"/><CurvePlot key={`${plotKey}:s`} curves={curves.filter(c=>c.kind==='sagittal')} title="弧矢 · Ex(Px)" xLabel="Px / 归一化光瞳" yLabel="横向像差 / μm"/></>}
          {tab==='field_curvature' && <><CurvePlot key={`${plotKey}:curvature`} curves={curves.filter(c=>c.kind!=='distortion')} title="场曲 · T / S" xLabel={`+Y 视场 / ${curves[0]?.x_unit}`} yLabel="焦面位移 / mm"/><CurvePlot key={`${plotKey}:distortion`} curves={curves.filter(c=>c.kind==='distortion')} title="畸变 · F–Tan(θ)" xLabel={`+Y 视场 / ${curves[0]?.x_unit}`} yLabel="畸变 / %"/></>}
        </div>}
    {r && <Button className="analysis-source-button" onClick={()=>setSourceOpen(true)}><Info size={17}/>计算来源与设置</Button>}
    {settingsOpen && model && <AnalysisSettings model={model} settings={settings} onChange={s=>setDraft({identity,settings:s})} onClose={()=>setSettingsOpen(false)}/>}
    {historyOpen && <Modal title="分析历史" className="analysis-history-dialog" onClose={()=>setHistoryOpen(false)}><AnalysisHistory sync={sync}/></Modal>}
    {sourceOpen && r && <Modal title="计算来源" onClose={()=>setSourceOpen(false)} className="analysis-source"><dl className="studio-detail-list">
      <div><dt>引擎</dt><dd>{r.engine}</dd></div><div><dt>工程</dt><dd>{r.source.model_project}</dd></div><div><dt>模型修订</dt><dd>{r.source.model_revision}</dd></div>
      <div><dt>服务实例</dt><dd>{r.source.service_instance_id}</dd></div><div><dt>计算时间</dt><dd>{new Date(r.finished_at).toLocaleString()}</dd></div></dl>
      <details><summary>有效设置与统计口径</summary><pre>{JSON.stringify({settings:r.settings,items:Object.fromEntries(Object.entries(r.items).map(([k,v])=>[k,{status:v.status,coverage:v.coverage,effective_settings:v.effective_settings,
        fields:v.fields?.map(f=>({field_index:f.field_index,effective_settings:f.effective_settings,grid:f.grid?{statistics_source:f.grid.statistics_source,mask_source:f.grid.mask_source,coordinates:f.grid.coordinates,native_statistics:f.native_statistics,comparison:f.statistics_comparison}:undefined}))}]))},null,2)}</pre></details>
    </Modal>}
  </motion.section>;
}
