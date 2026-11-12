import React from 'react';
import { RefreshCw } from 'lucide-react';
import { Button } from './ui.jsx';
import { SurfacePicker } from './SurfacePicker.jsx';
import { analysisNames } from './workbench-analysis.js';
import { analysisComparisonReason, historicalMetrics } from './workbench-analysis-history.js';
import { useWorkbenchAnalysisHistory } from './use-workbench-analysis-history.js';
import './analysis-history.css';

const time = value => new Date(value).toLocaleString();
const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumSignificantDigits: 6 }) : '未提供';
const optionLabel = entry => `修订 ${entry.source.model_revision} · 配置 ${entry.source.configuration} · ${time(entry.finished_at)}${entry.status === 'partial' ? ' · 部分结果' : ''}`;
function SourceSettings({ result }) {
  const source = result.source;
  return <details className="quality-history-source"><summary>来源与设置</summary>
    <dl aria-label="分析记录来源">{[['结果', result.result_id], ['引擎', result.engine], ['工程', source.model_project],
      ['服务实例', source.service_instance_id], ['模型', source.model_id], ['模型修订', source.model_revision],
      ['配置', source.configuration], ['观察状态版本', source.source_state_version], ['完成时间', time(result.finished_at)]].map(([key, value]) =>
      <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
    <h4>记录时设置与物理覆盖范围</h4><pre>{JSON.stringify({ settings: result.settings,
      comparison_context: result.comparison_context ?? '缺失，不能直接比较',
      model_metadata: result.model_metadata ? { units: result.model_metadata.units, configuration: result.model_metadata.configuration,
        fields: result.model_metadata.fields, wavelengths: result.model_metadata.wavelengths } : '未提供',
      items: Object.fromEntries(Object.entries(result.items).map(([kind, item]) => [kind, { status: item?.status,
        coverage: item?.coverage, effective_settings: item?.effective_settings,
        fields: Array.isArray(item?.fields) ? item.fields.filter(field => field && typeof field === 'object').map(field => ({
          field_index: field.field_index, effective_settings: field.effective_settings,
          statistics_comparison: field.statistics_comparison, grid: field.grid ? { unit: field.grid.unit,
            coordinates: field.grid.coordinates, orientation: field.grid.orientation, statistics_source: field.grid.statistics_source,
            mask_source: field.grid.mask_source, strehl_method: field.grid.strehl_method } : undefined })) : undefined }])) }, null, 2)}</pre>
  </details>;
}
function HistoryResult({ value, side, numeric }) {
  const result = value.result, metrics = historicalMetrics(result);
  return <article className="quality-history-card" data-analysis-history-id={result.result_id} data-analysis-history-side={side}>
    <div className="quality-history-card-heading"><h3>{side} · 历史记录</h3><span className="quality-history-verdict">原判据 {result.overall}</span></div>
    <p className="quality-history-meta">修订 {result.source.model_revision} · 配置 {result.source.configuration} · {time(result.finished_at)}</p>
    {result.status === 'partial' && <p className="quality-history-warning" role="status">部分结果 · 存在未完成分析项目</p>}
    {numeric ? <>
      <dl className="quality-history-metrics" data-analysis-history-numeric>
        <div><dt>最低 MTF · 30 lp/mm</dt><dd>{number(metrics.mtf)}</dd></div>
        <div><dt>最差覆盖视场 RMS / λ</dt><dd>{number(metrics.rms)}</dd></div>
        <div><dt>Strehl · 估算</dt><dd>{number(metrics.strehl)}</dd></div>
      </dl>
      <p className="quality-history-meta">原生统计核验：{metrics.verified === true ? '已核对' : metrics.verified === false ? '未通过' : '未知'}
        {metrics.worstField ? ` · 最差覆盖视场 F${metrics.worstField}` : ''}</p>
      <p className="quality-history-meta">MTF 阈值：{metrics.mtfThreshold === null ? '未指定或未提供，保留原判据' : number(metrics.mtfThreshold)}</p>
      <ol className="quality-history-verdicts" aria-label="原始分析判据">{result.settings.analyses.map(kind => {
        const row = result.verdicts.find(item => item.analysis === kind), item = result.items[kind];
        return <li key={kind}><div><strong>{analysisNames[kind]}</strong><span>{row?.verdict ?? '未提供'}</span></div>
          <p>{item?.status === 'error' ? typeof item.message === 'string' && item.message || '此项未完成' : item?.status !== 'success' ? '此项未计算或未完成' : row?.criteria || '未提供判据'}</p>
          {typeof row?.coverage_warning === 'string' && row.coverage_warning && <p>{row.coverage_warning}</p>}
          {row?.evidence?.full_field_table === false && <p>仅覆盖所选视场</p>}
          {row?.evidence !== undefined && <details><summary>原始判据证据</summary><pre>{JSON.stringify(row.evidence, null, 2)}</pre></details>}
        </li>;
      })}</ol>
      <p className="quality-history-meta">点列与光线像差保持诊断项；INFO 不代表验收通过。Strehl 数值为估算。</p>
    </> : <p className="quality-history-meta">当前组合不显示数值对比，可取消对比后单独回看。</p>}
    <SourceSettings result={result}/>
  </article>;
}
function ResultSlot({ value, side, status, error, numeric }) {
  return value ? <HistoryResult key={value.result.result_id} value={value} side={side} numeric={numeric}/>
    : <div className="quality-history-empty" role={error ? 'alert' : 'status'}>{error || (status === 'loading' ? '正在读取分析记录' : '请选择分析记录')}</div>;
}
export default function AnalysisHistory({ sync }) {
  const history = useWorkbenchAnalysisHistory(sync);
  const comparing = Boolean(history.compare), reason = comparing ? analysisComparisonReason(history.left?.result, history.right?.result) : '';
  const numeric = !comparing || !reason;
  return <section className="quality-history" aria-label="历史像质回看">
    <div className="quality-history-heading"><div><p>本次服务会话 · 历史分析只读</p>
      <p>历史记录只对应当时模型与设置，不代表当前像质或阶段验收。</p></div>
      <Button aria-label="刷新分析历史" disabled={['loading', 'offline', 'unavailable'].includes(history.status)} onClick={history.refresh}><RefreshCw size={16}/>刷新</Button></div>
    <p className="quality-history-note">本会话最多缓存 8 项；较早结果可能被淘汰，重启服务后清空。回看不会运行分析或恢复模型。</p>
    {history.status === 'offline' ? <p className="quality-history-empty" role="status">连接或工程来源已变化，分析历史暂不显示。</p>
      : history.status === 'unavailable' ? <p className="quality-history-empty" role="status">当前服务未提供分析历史目录，请更新服务后重新读取。</p>
      : history.error ? <p className="quality-history-empty" role="alert">{history.error}</p>
      : !history.entries.length ? <p className="quality-history-empty" role="status">{history.status === 'loading' ? '正在读取分析历史' : '本次服务会话暂无可用分析记录。'}</p>
      : <>
        <div className="quality-history-selectors"><div><span>查看分析记录</span><SurfacePicker label="查看分析记录" value={history.selected}
          options={history.entries.map(entry => ({ value: entry.id, label: optionLabel(entry) }))} onChange={history.select}/></div>
          <div><span>对比分析记录</span><SurfacePicker label="对比分析记录" value={history.compare || ''}
            options={[{ value: '', label: '不对比' }, ...history.entries.filter(entry => entry.id !== history.selected).map(entry => ({ value: entry.id, label: optionLabel(entry) }))]}
            onChange={history.compareWith}/></div></div>
        {comparing && <p className="quality-history-comparison" role="status">{reason || '来源与统计口径一致 · 并列显示原始历史记录。'}</p>}
        <div className={`quality-history-results ${comparing ? 'is-comparing' : ''}`}>
          <ResultSlot value={history.left} side="A" status={history.leftStatus} error={history.leftError} numeric={numeric}/>
          {comparing && <ResultSlot value={history.right} side="B" status={history.rightStatus} error={history.rightError} numeric={numeric}/>}
        </div>
      </>}
  </section>;
}
