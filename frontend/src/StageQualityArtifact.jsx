import React, { useEffect, useRef, useState } from 'react';
import { Button } from './ui.jsx';
import { QualityTiles } from './AnalysisWorkspace.jsx';
import { createStageQualityArtifactController, qualityArtifactKey, qualityMetrics, validateQualityArtifactReference } from './stage-quality-artifact.js';

const fmt = value => value === null || value === undefined ? '—' : Number(value).toLocaleString(undefined, { maximumSignificantDigits: 4 });
const names = { mtf: 'MTF', wavefront: '波前', spot: '点列', ray_fan: '光线像差', field_curvature: '场曲 / 畸变' };

function ArchivedQuality({ value }) {
  const { artifact, payload } = value, metrics = qualityMetrics(payload), source = artifact.source;
  return <figure className="stage-quality-figure" data-stage-quality-id={artifact.id}>
    <figcaption><strong>当时像质 · <span className={`analysis-verdict verdict-${payload.overall.toLowerCase()}`}>{payload.overall}</span></strong>
      <span>{new Date(payload.finished_at).toLocaleString()} · {payload.engine}</span></figcaption>
    <QualityTiles metrics={metrics} />
    <div className="analysis-result-list stage-quality-verdicts">{payload.verdicts.map(row => <div key={row.analysis}>
      <span>{names[row.analysis] || row.analysis}</span><span className={`analysis-verdict verdict-${row.verdict.toLowerCase()}`}>{row.verdict}</span>
      <span>{payload.items[row.analysis]?.status === 'error' ? payload.items[row.analysis].message : row.criteria}</span></div>)}</div>
    <div className="studio-data-table"><table><thead><tr><th>视场</th><th>波前 RMS / λ</th><th>Strehl 估算</th><th>MTF@30</th><th>点列 RMS / μm</th></tr></thead>
      <tbody>{metrics.fields.map(field => { const row = metrics.perField[field]; return <tr key={field}><td>F{field}</td>
        <td>{fmt(row.wavefrontRms)}</td><td>{fmt(row.strehl)}</td><td>{fmt(row.mtf30)}</td><td>{fmt(row.spotRms)}</td></tr>; })}</tbody></table></div>
    <details><summary>归档原始来源与校验</summary><dl className="stage-geometry-source" aria-label="阶段像质原始来源">
      {[['工程', source.project_name], ['原服务实例', source.service_instance_id], ['模型修订', source.model_revision], ['配置', source.configuration],
        ['原观察状态版本', source.source_state_version], ['提案版本', source.proposal_revision], ['授权版本', source.authorization_revision],
        ['分析结果', artifact.result_id], ['计算开始', payload.started_at], ['计算结束', payload.finished_at],
        ['归档 ID', artifact.id], ['正文大小 / B', artifact.bytes], ['SHA-256', artifact.sha256]].map(([label, detail]) =>
        <div key={label}><dt>{label}</dt><dd>{detail}</dd></div>)}
    </dl></details>
  </figure>;
}

function QualityReader({ reference, scope, api }) {
  const controller = useRef(null);
  const [view, setView] = useState({ status: 'idle', value: null, pending: false, error: '' });
  useEffect(() => {
    const reader = createStageQualityArtifactController({ api, scope, reference, onChange: setView });
    controller.current = reader; setView(reader.view);
    return () => { reader.stop(); if (controller.current === reader) controller.current = null; };
  }, [api, scope.service_instance_id, scope.project_name, qualityArtifactKey(reference)]);
  return <>
    <div className="stage-geometry-actions">
      <Button disabled={view.pending} onClick={() => controller.current?.load()}>{view.value ? '重新读取' : '读取阶段像质归档'}</Button>
      {(view.value || view.pending || view.error) && <Button onClick={() => controller.current?.close()}>收起</Button>}
    </div>
    {view.pending && <p role="status">正在读取并核验阶段像质归档…</p>}
    {view.error && <p role="alert">{view.error}</p>}
    {view.value && <ArchivedQuality key={view.value.artifact.id} value={view.value} />}
  </>;
}

export default function StageQualityArtifact({ attempt, sync, scope }) {
  const reference = attempt.quality_artifact;
  if (reference === undefined) return null;
  let invalid = false;
  try { validateQualityArtifactReference(reference, attempt, scope?.project_name); } catch { invalid = true; }
  const available = sync?.transportConnected === true && sync.canCollaborate === true
    && scope?.service_instance_id === sync.engine?.service_instance_id && scope?.project_name === sync.engine?.active_project
    && typeof sync?.api?.readMilestoneQualityArtifact === 'function';
  return <section className="stage-geometry-artifact stage-quality-artifact" aria-label="阶段像质归档">
    <h4>阶段像质归档 {!invalid && <span className={`analysis-verdict verdict-${reference.analysis.overall.toLowerCase()}`}>{reference.analysis.overall}</span>}</h4>
    <p>本阶段内计算的原始分析结果，按原判据保存；归档不代表验收通过。</p>
    {invalid ? <p role="alert">阶段像质归档引用不可用，请核对阶段记录。</p>
      : !available ? <p role="status">连接或工程来源不可用，阶段像质暂不显示。</p>
        : <QualityReader reference={reference} scope={scope} api={sync.api} />}
  </section>;
}
