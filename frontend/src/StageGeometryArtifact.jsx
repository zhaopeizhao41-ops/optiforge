import React, { useEffect, useRef, useState } from 'react';
import { Focus, Minus, Plus } from 'lucide-react';
import { Button } from './ui.jsx';
import { NativeScene } from './NativeScene.jsx';
import { homeCamera, zoomCamera, MIN_ZOOM, MAX_ZOOM } from './workbench-camera.js';
import { createStageGeometryArtifactController, geometryArtifactKey, validateGeometryArtifactReference } from './stage-geometry-artifact.js';
import './stage-geometry-artifact.css';

const number = value => typeof value === 'number' ? value.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 6 })
  : value === 'Infinity' ? '∞' : value === '-Infinity' ? '−∞' : '—';

function ArchivedGeometry({ value }) {
  const [camera, setCamera] = useState(homeCamera), [selected, setSelected] = useState(null);
  const { artifact, payload: { entry, snapshot } } = value, model = snapshot.model, source = artifact.source;
  const layout = snapshot.display_layout || snapshot.layout, surface = model.surfaces.find(row => row.index === selected);
  return <figure className="stage-geometry-figure" data-stage-geometry-id={artifact.id}>
    <figcaption><strong>当时几何 · {entry.label}</strong><span>{new Date(entry.recorded_at).toLocaleString()}</span></figcaption>
    <p>{model.name || '归档模型'} · 修订 {source.model_revision} · 配置 {source.configuration} · {model.surfaces.length} 个表面 · {model.units}</p>
    <div className="stage-geometry-canvas"><NativeScene layout={layout} trace={null} selected={selected}
      onSelect={setSelected} camera={camera} onCameraChange={setCamera}/></div>
    {layout.issues.length > 0 && <div className="stage-geometry-issues" role="status">{layout.issues.map((issue, index) => <p key={index}>{issue}</p>)}</div>}
    <div className="stage-geometry-camera" aria-label="归档几何视口">
      <Button className="studio-icon-button" aria-label="缩小阶段几何" disabled={camera.zoom <= MIN_ZOOM} onClick={() => setCamera(value => zoomCamera(value, value.zoom - 25))}><Minus size={16}/></Button>
      <span>{Math.round(camera.zoom)}%</span>
      <Button className="studio-icon-button" aria-label="放大阶段几何" disabled={camera.zoom >= MAX_ZOOM} onClick={() => setCamera(value => zoomCamera(value, value.zoom + 25))}><Plus size={16}/></Button>
      <Button className="studio-icon-button" aria-label="复位阶段几何" onClick={() => setCamera(homeCamera())}><Focus size={16}/></Button>
    </div>
    {surface && <dl className="stage-geometry-source" aria-label="归档表面属性">
      <div><dt>表面</dt><dd>S{surface.index}</dd></div>
      <div><dt>曲率半径</dt><dd>{number(surface.radius)} {model.units}</dd></div>
      <div><dt>面后厚度</dt><dd>{number(surface.thickness)} {model.units}</dd></div>
      <div><dt>材料</dt><dd>{surface.material || 'AIR'}</dd></div>
    </dl>}
    <details><summary>归档原始来源与校验</summary><dl className="stage-geometry-source" aria-label="阶段几何原始来源">
      {[['工程', source.project_name], ['原服务实例', source.service_instance_id], ['模型', source.model_id], ['模型修订', source.model_revision],
        ['配置', source.configuration], ['原观察状态版本', source.source_state_version], ['需求版本', source.requirements_revision],
        ['提案版本', source.proposal_revision], ['提案引用', source.proposal_hash], ['授权版本', source.authorization_revision],
        ['任务', artifact.task_id], ['阶段记录', artifact.attempt_id], ['关联操作', artifact.operation_id], ['归档 ID', artifact.id],
        ['归档时间', artifact.created_at], ['正文大小 / B', artifact.bytes], ['SHA-256', artifact.sha256]].map(([label, detail]) =>
        <div key={label}><dt>{label}</dt><dd>{detail}</dd></div>)}
    </dl></details>
  </figure>;
}

function ArtifactReader({ reference, scope, api }) {
  const controller = useRef(null);
  const [view, setView] = useState({ status: 'idle', value: null, pending: false, error: '' });
  useEffect(() => {
    const reader = createStageGeometryArtifactController({ api, scope, reference, onChange: setView });
    controller.current = reader; setView(reader.view);
    return () => { reader.stop(); if (controller.current === reader) controller.current = null; };
  }, [api, scope.service_instance_id, scope.project_name, geometryArtifactKey(reference)]);
  return <>
    <div className="stage-geometry-actions">
      <Button disabled={view.pending} onClick={() => controller.current?.load()}>{view.value ? '重新读取' : '读取阶段几何快照'}</Button>
      {(view.value || view.pending || view.error) && <Button onClick={() => controller.current?.close()}>收起</Button>}
    </div>
    {view.pending && <p role="status">正在读取并核验阶段几何归档…</p>}
    {view.error && <p role="alert">{view.error}</p>}
    {view.value && <ArchivedGeometry key={view.value.artifact.id} value={view.value}/>}
  </>;
}

export default function StageGeometryArtifact({ attempt, sync, scope, ready = true }) {
  const reference = attempt.geometry_artifact;
  let referenceKey = null, invalid = false;
  if (reference !== undefined) {
    try { validateGeometryArtifactReference(reference, attempt, scope?.project_name); referenceKey = geometryArtifactKey(reference); }
    catch { invalid = true; }
  }
  const available = ready === true && sync?.transportConnected === true && sync.canCollaborate === true
    && ['synced', 'running', 'unavailable'].includes(sync.phase) && scope?.service_instance_id === sync.engine?.service_instance_id
    && scope?.project_name === sync.engine?.active_project && typeof sync?.api?.readMilestoneArtifact === 'function';
  return <section className="stage-geometry-artifact" aria-label="阶段几何归档">
    <h4>阶段几何归档</h4>
    <p>当时几何，不含像质结果或实光线；不表示当前模型验收或仿真授权。</p>
    {invalid ? <p role="alert">阶段几何归档引用不可用，请核对阶段记录。</p>
      : !reference ? <p>本阶段未归档几何快照。</p>
        : !available ? <p role="status">连接或工程来源不可用，阶段几何暂不显示。</p>
          : <ArtifactReader key={JSON.stringify([scope.service_instance_id, scope.project_name, referenceKey])}
            reference={reference} scope={scope} api={sync.api}/>}
  </section>;
}
