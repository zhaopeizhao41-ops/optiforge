import React, { useState } from 'react';
import { Focus, Minus, Plus, RefreshCw } from 'lucide-react';
import { Button } from './ui.jsx';
import { SurfacePicker } from './SurfacePicker.jsx';
import { NativeScene } from './NativeScene.jsx';
import { homeCamera, zoomCamera, MIN_ZOOM, MAX_ZOOM } from './workbench-camera.js';
import { useWorkbenchHistory } from './use-workbench-history.js';
import { historyComparisonReason } from './workbench-history.js';
import './workbench-history.css';

const time = value => new Date(value).toLocaleString();
const number = value => typeof value === 'number' ? value.toLocaleString('en-US',{useGrouping:false,maximumFractionDigits:6}) : value==='Infinity'?'∞':value==='-Infinity'?'−∞':'—';

function HistoryScene({ value, side }) {
  const [camera,setCamera] = useState(homeCamera);
  const [selected,setSelected] = useState(null);
  const {entry,snapshot} = value;
  const surface = snapshot.model.surfaces.find(item => item.index===selected);
  return <figure className="history-scene-card" data-history-entry-id={entry.id}>
    <figcaption><strong>{side} · {entry.label}</strong><span>{time(entry.recorded_at)}</span></figcaption>
    <p className="history-source">修订 {entry.source.model_revision} · 配置 {entry.source.configuration} · {snapshot.model.units}</p>
    <div className="history-scene-stage">
      <NativeScene layout={snapshot.display_layout || snapshot.layout} trace={null} selected={selected}
        onSelect={setSelected} camera={camera} onCameraChange={setCamera} />
    </div>
    <div className="history-scene-tools">
      <Button className="studio-icon-button" aria-label={`${side}缩小历史光路`} disabled={camera.zoom<=MIN_ZOOM} onClick={()=>setCamera(c=>zoomCamera(c,c.zoom-25))}><Minus size={16}/></Button>
      <span>{Math.round(camera.zoom)}%</span>
      <Button className="studio-icon-button" aria-label={`${side}放大历史光路`} disabled={camera.zoom>=MAX_ZOOM} onClick={()=>setCamera(c=>zoomCamera(c,c.zoom+25))}><Plus size={16}/></Button>
      <Button className="studio-icon-button" aria-label={`${side}复位历史光路`} onClick={()=>setCamera(homeCamera())}><Focus size={16}/></Button>
    </div>
    {surface && <dl className="history-surface-readout" aria-label={`${side}历史表面属性`}>
      <div><dt>表面</dt><dd>S{surface.index}</dd></div>
      <div><dt>曲率半径</dt><dd>{number(surface.radius)} {snapshot.model.units}</dd></div>
      <div><dt>面后厚度</dt><dd>{number(surface.thickness)} {snapshot.model.units}</dd></div>
      <div><dt>材料</dt><dd>{surface.material || 'AIR'}</dd></div>
    </dl>}
    <details className="history-source-details"><summary>快照来源</summary><dl className="studio-detail-list">
      <div><dt>工程</dt><dd>{entry.source.model_project}</dd></div>
      <div><dt>模型</dt><dd>{entry.source.model_id}</dd></div>
      <div><dt>服务实例</dt><dd>{entry.source.service_instance_id}</dd></div>
      <div><dt>状态版本</dt><dd>{entry.source.source_state_version}</dd></div>
    </dl></details>
  </figure>;
}

function HistoryPane({value,status,error,side}) {
  if (value) return <HistoryScene key={value.entry.id} value={value} side={side}/>;
  return <div className="history-detail-message" role={error?'alert':'status'}>{error || (status==='loading'?'正在读取历史光路':'选择一条快照以回看光路')}</div>;
}

export default function HistoryWorkspace({sync}) {
  const history = useWorkbenchHistory(sync);
  const selected = history.entries.find(entry=>entry.id===history.selected);
  return <section className="history-workspace" aria-label="历史光路回看">
    <div className="history-heading"><div><p className="history-session">本次服务会话 · 历史快照只读</p>
      <p>记录成功操作后的已观察光路。重启服务后清空；达到缓存上限时会淘汰较早记录。</p></div>
      <Button aria-label="刷新操作历史" disabled={history.status==='loading'||history.status==='offline'} onClick={history.refresh}><RefreshCw size={16}/>刷新</Button>
    </div>
    <p className="history-geometry-note">仅比较几何，不包含像质结果或实光线。操作记录不代表四阶段优化或像质验收已完成。</p>
    {history.recording?.status==='skipped' && <p className="history-notice" role="status">最近一次操作未能记录历史快照，已有光学操作的结果保持不变。</p>}
    {history.error && history.status==='failed' && <p className="history-notice" role="alert">{history.error}</p>}
    {history.status==='offline' ? <div className="history-empty" role="status">正在等待当前工程连接，历史光路暂不显示。</div>
      : !history.entries.length ? <div className="history-empty" role="status">{history.status==='loading'?'正在读取操作历史':'本次服务会话暂无可用快照。后续成功的模型操作会在此留存已观察的光路。'}</div>
      : <div className="history-layout">
        <ol className="history-timeline" aria-label="操作时间轴">{history.entries.map(entry=><li key={entry.id}>
          <Button aria-label={`查看历史快照 ${entry.id}`} aria-pressed={history.selected===entry.id} onClick={()=>history.select(entry.id)}>
            <span className="history-timeline-mark" aria-hidden="true"/>
            <span className="history-timeline-copy"><strong>{entry.label}</strong><time dateTime={entry.recorded_at}>{time(entry.recorded_at)}</time>
              <span>修订 {entry.source.model_revision} · 配置 {entry.source.configuration}</span></span>
          </Button>
        </li>)}</ol>
        <div className="history-detail">
          <div className="history-selectors">
            <div className="history-selector"><span>查看快照</span><SurfacePicker label="查看快照" value={history.selected || ''} onChange={history.select}
              options={history.entries.map(entry=>({ value:entry.id, label:`${entry.label} · 修订 ${entry.source.model_revision} · ${time(entry.recorded_at)}` }))}/></div>
            <div className="history-selector"><span>对比快照</span><SurfacePicker label="对比快照" value={history.compare || ''} onChange={history.compareWith}
              options={[{ value:'', label:'不对比' }, ...history.entries.map(entry=>{
                const reason=historyComparisonReason(selected,entry);
                return { value:entry.id, disabled:Boolean(reason), label:`${entry.label} · 修订 ${entry.source.model_revision}${reason?`（${reason}）`:''}` };
              })]}/></div>
          </div>
          <p className="history-compare-note">可比较同一模型、同一配置的两次历史记录；两侧各自适配视口，可独立缩放。</p>
          <div className={`history-scenes ${history.compare?'is-comparing':''}`}>
            <HistoryPane value={history.left} status={history.leftStatus} error={history.leftError} side="A"/>
            {history.compare && <HistoryPane value={history.right} status={history.rightStatus} error={history.rightError} side="B"/>}
          </div>
        </div>
      </div>}
  </section>;
}
