import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from './ui.jsx';
import { convergenceAttempts, loadConvergence, niceTicks, qualityMetrics, spreadLabels } from './stage-quality-artifact.js';
import { QualityTiles } from './AnalysisWorkspace.jsx';
import './stage-convergence.css';

const fmt = value => value === null || value === undefined ? '—' : Number(value).toLocaleString(undefined, { maximumSignificantDigits: 4 });
const markers = ['circle', 'square', 'diamond'];

function Marker({ shape, x, y }) {
  if (shape === 'square') return <rect x={x - 4} y={y - 4} width="8" height="8" rx="1.5" />;
  if (shape === 'diamond') return <path d={`M${x} ${y - 5}L${x + 5} ${y}L${x} ${y + 5}L${x - 5} ${y}Z`} />;
  return <circle cx={x} cy={y} r="4" />;
}

// One measure per chart (small multiples); fields are series in fixed order.
function StageLineChart({ title, unit, stages, series, reference, note }) {
  const [hover, setHover] = useState(null);
  const width = 360, height = 220, left = 52, right = 34, top = 18, bottom = 34;
  const pw = width - left - right, ph = height - top - bottom;
  const values = series.flatMap(s => s.values).filter(Number.isFinite).concat(reference ? [reference.value] : []);
  if (!values.length) return <figure className="convergence-chart is-empty"><figcaption>{title}</figcaption><p>归档中没有此项数据</p></figure>;
  const ticks = niceTicks(Math.min(0, ...values), Math.max(...values) || 1);
  const lo = ticks[0], hi = ticks.at(-1);
  const px = i => left + (stages.length === 1 ? pw / 2 : i / (stages.length - 1) * pw);
  const py = v => top + (hi - v) / (hi - lo) * ph;
  const labels = spreadLabels(series.map(s => { const i = s.values.findLastIndex(Number.isFinite); return i >= 0 ? py(s.values[i]) : NaN; }));
  const move = event => {
    const box = event.currentTarget.getBoundingClientRect(), x = (event.clientX - box.left) / box.width * width;
    const index = stages.reduce((best, _, i) => Math.abs(px(i) - x) < Math.abs(px(best) - x) ? i : best, 0);
    setHover(index);
  };
  return <figure className="convergence-chart">
    <figcaption>{title}{unit && <span> / {unit}</span>}</figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" onPointerMove={move} onPointerLeave={() => setHover(null)}
      aria-label={`${title}，按阶段变化；${series.map(s => `${s.label}: ${s.values.map(fmt).join(' → ')}`).join('；')}`}>
      {ticks.map((tick, i) => <g key={i} className="convergence-grid"><line x1={left} x2={left + pw} y1={py(tick)} y2={py(tick)} />
        <text x={left - 8} y={py(tick) + 4} textAnchor="end">{fmt(tick)}</text></g>)}
      {stages.map((stage, i) => <text key={stage.stageId} className="convergence-axis" x={px(i)} y={height - 10} textAnchor="middle">阶段 {stage.number}</text>)}
      {reference && Number.isFinite(reference.value) && <g className="convergence-reference">
        <line x1={left} x2={left + pw} y1={py(reference.value)} y2={py(reference.value)} />
        <text x={left + pw} y={py(reference.value) - 5} textAnchor="end">{reference.label}</text></g>}
      {hover !== null && <line className="convergence-crosshair" x1={px(hover)} x2={px(hover)} y1={top} y2={top + ph} />}
      {series.map((s, index) => {
        const points = s.values.map((v, i) => Number.isFinite(v) ? [px(i), py(v)] : null);
        const labelY = labels[index];
        const path = points.reduce((d, p, i) => p ? `${d}${d && points[i - 1] ? 'L' : 'M'}${p[0]},${p[1]} ` : d, '');
        const last = points.findLastIndex(Boolean);
        return <g key={s.label} className={`convergence-series is-${index + 1}`}>
          <path d={path} fill="none" />
          {points.map((p, i) => p && <Marker key={i} shape={markers[index % markers.length]} x={p[0]} y={p[1]} />)}
          {last >= 0 && <text className="convergence-label" x={points[last][0] + 8} y={labelY + 4}>{s.label}</text>}
        </g>;
      })}
    </svg>
    {hover !== null && <div className={`convergence-tooltip ${px(hover) > width / 2 ? 'is-left' : ''}`} role="status">
      <strong>阶段 {stages[hover].number} · {stages[hover].label}</strong>
      {series.map((s, index) => <span key={s.label}><i className={`is-${index + 1}`} />{s.label} {fmt(s.values[hover])}{unit ? ` ${unit}` : ''}</span>)}
    </div>}
    <div className="convergence-legend">{series.map((s, index) => <span key={s.label}><i className={`is-${index + 1}`} />{s.label}</span>)}</div>
    {note && <p className="convergence-note">{note}</p>}
  </figure>;
}

function LensOverlay({ stages }) {
  const [focus, setFocus] = useState(null);
  const shapes = stages.filter(stage => stage.geometry).map(stage => {
    const snapshot = stage.geometry.payload.snapshot, layout = snapshot.display_layout || snapshot.layout;
    return { stage, layout, units: snapshot.model.units };
  }).filter(item => item.layout.status === 'ready');
  if (!shapes.length) return <p className="convergence-note">没有可绘制的阶段几何归档</p>;
  // Crop to the glass: the image plane sits far behind and would squeeze the lenses.
  const points = shapes.flatMap(item => item.layout.elements.flatMap(e => e.points));
  if (!points.length) return <p className="convergence-note">阶段几何中没有镜片轮廓</p>;
  const image = shapes.map(({ stage, layout }) => {
    const glass = layout.elements.flatMap(e => e.points).map(p => p[0]), plane = layout.surfaces.find(s => s.is_image);
    return { number: stage.number, back: plane && glass.length ? plane.vertex_z - Math.max(...glass) : null, total: plane?.vertex_z ?? null };
  });
  const minZ = Math.min(...points.map(p => p[0])), maxZ = Math.max(...points.map(p => p[0]));
  const maxY = Math.max(...points.map(p => Math.abs(p[1])));
  const width = 720, height = 240, pad = 28, scale = Math.min((width - 2 * pad) / (maxZ - minZ || 1), (height - 2 * pad) / (2 * maxY || 1));
  const x = z => pad + (z - minZ) * scale, y = v => height / 2 - v * scale;
  const outline = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join('') + 'Z';
  return <figure className="convergence-lens">
    <figcaption>镜片截面演变（YZ，等比例，单位 {shapes[0].units}）</figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`阶段 ${shapes.map(s => s.stage.number).join('、')} 的镜片截面叠加，颜色由浅到深表示阶段先后`}>
      <line className="convergence-axis-line" x1={pad / 2} x2={width - pad / 2} y1={height / 2} y2={height / 2} />
      {shapes.map(({ stage, layout }) => <g key={stage.stageId} className={`convergence-stage is-stage-${stage.number}${focus === null || focus === stage.number ? '' : ' is-dimmed'}`}>
        {layout.elements.map(element => <path key={element.front} d={outline(element.points)} />)}
      </g>)}
    </svg>
    <p className="convergence-note">截面仅含镜片；像面距末片：{image.map(row => `阶段 ${row.number} ${fmt(row.back)}`).join(' · ')}（{shapes[0].units}）。</p>
    <div className="convergence-legend">{shapes.map(({ stage }) => <Button key={stage.stageId} aria-pressed={focus === stage.number}
      onClick={() => setFocus(value => value === stage.number ? null : stage.number)}><i className={`is-stage-${stage.number}`} />阶段 {stage.number} · {stage.label}</Button>)}</div>
  </figure>;
}

export default function StageConvergence({ stages, sync, scope }) {
  const rows = useMemo(() => convergenceAttempts(stages), [stages]);
  const key = JSON.stringify(rows.map(row => [row.attempt.id, row.attempt.quality_artifact?.id, row.attempt.geometry_artifact?.id]));
  const [view, setView] = useState({ status: 'idle', stages: [], error: '' });
  const abort = useRef(null);
  useEffect(() => { abort.current?.abort(); setView({ status: 'idle', stages: [], error: '' }); }, [key, scope.service_instance_id, scope.project_name]);
  useEffect(() => () => abort.current?.abort(), []);
  const available = sync?.transportConnected === true && sync.canCollaborate === true && typeof sync?.api?.readMilestoneQualityArtifact === 'function'
    && scope.service_instance_id === sync.engine?.service_instance_id && scope.project_name === sync.engine?.active_project;
  if (!rows.length) return null;
  const load = async () => {
    abort.current?.abort();
    const controller = new AbortController(); abort.current = controller;
    setView({ status: 'loading', stages: [], error: '' });
    try {
      const loaded = await loadConvergence(sync.api, scope, rows, controller.signal);
      if (!controller.signal.aborted) setView({ status: 'ready', stages: loaded, error: '' });
    } catch (error) {
      if (!controller.signal.aborted) setView({ status: 'failed', stages: [], error: error.message || '读取阶段归档失败' });
    }
  };
  const loaded = view.stages, measured = loaded.map(stage => stage.quality ? qualityMetrics(stage.quality.payload) : null);
  const fields = [...new Set(measured.flatMap(m => m?.fields || []))].sort((a, b) => a - b).slice(0, 3);
  const series = key => fields.map(field => ({ label: `F${field}`, values: measured.map(m => m?.perField[field]?.[key] ?? null) }));
  const last = measured.findLast(Boolean);
  const engines = [...new Set(measured.filter(Boolean).map(m => m.engine))];
  return <section className="stage-convergence" aria-label="阶段收敛">
    <div className="convergence-heading"><div><h3>阶段收敛</h3>
      <p>读取各阶段归档的当时几何与像质，按阶段比较。只读，不运行分析；归档不代表验收通过。</p></div>
      <Button disabled={!available || view.status === 'loading'} onClick={load}>{view.status === 'ready' ? '重新读取' : `读取 ${rows.length} 个阶段归档`}</Button></div>
    {!available && <p className="convergence-note" role="status">连接或工程来源不可用，暂不能读取归档。</p>}
    {view.status === 'loading' && <p role="status">正在读取并逐条核验阶段归档…</p>}
    {view.error && <p role="alert">{view.error}</p>}
    {view.status === 'ready' && <>
      {engines.length > 0 && <p className="convergence-engine">计算引擎：{engines.join('；')}</p>}
      {loaded.some(stage => stage.errors.length) && <div className="convergence-errors" role="status">
        {loaded.filter(stage => stage.errors.length).map(stage => <p key={stage.stageId}>阶段 {stage.number}：{stage.errors.join('；')}</p>)}</div>}
      {last && <QualityTiles metrics={last} />}
      <LensOverlay stages={loaded} />
      {fields.length > 0 && <div className="convergence-grid-charts">
        <StageLineChart title="波前 RMS" unit="λ" stages={loaded} series={series('wavefrontRms')}
          reference={{ value: 1 / 14, label: '门禁 λ/14' }} />
        <StageLineChart title="30 lp/mm MTF（T/S 较小值）" stages={loaded} series={series('mtf30')}
          reference={last?.mtfThreshold !== null && last?.mtfThreshold !== undefined ? { value: last.mtfThreshold, label: `门禁 ${fmt(last.mtfThreshold)}` } : null} />
        <StageLineChart title="点列 RMS 半径" unit="μm" stages={loaded} series={series('spotRms')}
          reference={last?.airyRadius ? { value: last.airyRadius, label: `艾里半径 ${fmt(last.airyRadius)}` } : null}
          note="点列与艾里半径仅为诊断（INFO），不作验收依据。" />
      </div>}
      <details className="convergence-table"><summary>数据表</summary><div className="studio-data-table"><table>
        <thead><tr><th>阶段</th><th>判定</th>{fields.map(f => <th key={`w${f}`}>F{f} 波前 / λ</th>)}{fields.map(f => <th key={`m${f}`}>F{f} MTF@30</th>)}{fields.map(f => <th key={`s${f}`}>F{f} 点列 / μm</th>)}</tr></thead>
        <tbody>{loaded.map((stage, i) => <tr key={stage.stageId}><td>阶段 {stage.number} · {stage.label}</td><td>{measured[i]?.overall ?? '—'}</td>
          {fields.map(f => <td key={`w${f}`}>{fmt(measured[i]?.perField[f]?.wavefrontRms)}</td>)}
          {fields.map(f => <td key={`m${f}`}>{fmt(measured[i]?.perField[f]?.mtf30)}</td>)}
          {fields.map(f => <td key={`s${f}`}>{fmt(measured[i]?.perField[f]?.spotRms)}</td>)}</tr>)}</tbody>
      </table></div></details>
    </>}
  </section>;
}
