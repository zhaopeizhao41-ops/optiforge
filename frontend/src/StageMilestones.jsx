import React from 'react';
import { MILESTONE_REASONS, MILESTONE_STATUS, milestonePane, milestoneStatusLabel } from './stage-milestones.js';
import StageGeometryArtifact from './StageGeometryArtifact.jsx';
import StageQualityArtifact from './StageQualityArtifact.jsx';
import StageConvergence from './StageConvergence.jsx';
import './stage-milestones.css';

const time = value => value ? new Date(value).toLocaleString() : '未报告';
const keyFor = attempt => JSON.stringify([attempt.task_id, attempt.id]);

function SourceDetails({ source, label }) {
  if (!source) return <p className="milestone-muted">{label}：无可用模型观察</p>;
  const rows = [
    ['工程', source.project_name ?? source.model_project], ['服务实例', source.service_instance_id],
    ['模型', source.model_id], ['模型修订', source.model_revision], ['配置', source.configuration],
    ['观察状态版本', source.source_state_version],
    ...(source.project_name ? [['需求版本', source.requirements_revision], ['提案版本', source.proposal_revision],
      ['提案引用', source.proposal_hash], ['授权版本', source.authorization_revision]] : []),
  ];
  return <div className="milestone-source"><h4>{label}</h4><dl aria-label={label}>
    {rows.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}
  </dl></div>;
}

function MilestoneAttempt({ attempt, sync, scope, historical = false }) {
  return <article className="milestone-attempt" data-milestone-attempt-id={attempt.id}
    data-milestone-task-id={attempt.task_id} data-milestone-status={attempt.status}>
    {historical && <div className="milestone-attempt-heading"><span>{time(attempt.reported_at || attempt.started_at)}</span>
      <span className="milestone-status">{milestoneStatusLabel(attempt)}</span></div>}
    <p className="milestone-note">{attempt.note}</p>
    <p className="milestone-meta">{attempt.worker_id} · 开始于 {time(attempt.started_at)}{attempt.reported_at ? ` · 报告于 ${time(attempt.reported_at)}` : ''}</p>
    {attempt.status === 'stale' && <div className="milestone-stale" role="status">
      <p>原记录状态：{MILESTONE_STATUS[attempt.declared_status]}</p>
      <ul>{attempt.stale_reasons.map(reason => <li key={reason}>{MILESTONE_REASONS[reason] || `来源状态已变化（${reason}）`}</li>)}</ul>
    </div>}
    <details className="milestone-details"><summary>来源与操作明细</summary>
      <dl className="milestone-identifiers"><div><dt>任务</dt><dd>{attempt.task_id}</dd></div><div><dt>记录</dt><dd>{attempt.id}</dd></div></dl>
      <SourceDetails source={attempt.source} label="开始时来源"/>
      {attempt.declared_status !== 'running' && <SourceDetails source={attempt.end_source} label="报告时来源"/>}
      <h4>关联操作</h4>
      {!attempt.operations.length ? <p className="milestone-muted">{attempt.declared_status === 'failed'
        ? 'Agent 失败报告；未附原生操作证据。' : '尚未关联操作证据。'}</p>
        : <ol className="milestone-operations" aria-label="关联操作">{attempt.operations.map(operation => <li key={operation.operation_id}>
          <p>{operation.tool}</p><dl><div><dt>操作</dt><dd>{operation.operation_id}</dd></div>
            <div><dt>执行状态</dt><dd>{operation.status}</dd></div><div><dt>工具原始状态</dt><dd>{operation.result_status ?? '未提供'}</dd></div>
            <div><dt>提交时间</dt><dd>{time(operation.queued_at)}</dd></div><div><dt>结束时间</dt><dd>{time(operation.finished_at)}</dd></div>
            {operation.code && <div><dt>状态码</dt><dd>{operation.code}</dd></div>}
            {operation.rolled_back !== undefined && <div><dt>回滚</dt><dd>{operation.rolled_back ? '是' : '否'}</dd></div>}
            {operation.view_error && <div><dt>观察错误</dt><dd>{operation.view_error}</dd></div>}
          </dl>
          {operation.observed_source ? <SourceDetails source={operation.observed_source} label="操作观察来源"/>
            : <p className="milestone-muted">无可用几何观察；本项仅为操作元数据。</p>}
        </li>)}</ol>}
      <p className="milestone-muted">操作状态仅供核对，不是原生边界、像质或公差的验收结论。</p>
    </details>
    <StageGeometryArtifact attempt={attempt} sync={sync} scope={scope}/>
    <StageQualityArtifact attempt={attempt} sync={sync} scope={scope}/>
  </article>;
}

export default function StageMilestones({ flow, sync, focusStage = null }) {
  const pane = milestonePane(flow, sync);
  const scope = { service_instance_id: sync?.engine?.service_instance_id, project_name: sync?.engine?.active_project };
  return <section className="workflow-card stage-milestones" aria-label="四阶段记录">
    <div className="workflow-card-title"><h2>四阶段记录</h2><span>显式执行记录 · 只读</span></div>
    <p className="milestone-disclaimer">阶段记录不代表像质验收通过。</p>
    <p className="milestone-persistence">“记录中”表示 Agent 已显式开始本阶段记录，不证明原生优化器正在运行。</p>
    <p className="milestone-persistence">阶段记录作为工程元数据保留；旧服务记录只供历史核对，不会自动恢复执行。</p>
    {pane.status !== 'ready' ? <p className="milestone-empty" role="status">{pane.status === 'offline'
      ? '连接或工程来源已变化，阶段记录暂不显示。' : '阶段记录不可用，请等待当前流程重新读取。'}</p>
      : <div className="milestone-grid">{pane.stages.map((stage, index) => <article key={stage.id}
        className={`milestone-card${focusStage === stage.id ? ' is-focused' : ''}`} data-milestone-stage={stage.id} data-milestone-status={stage.status}
        aria-label={`阶段 ${index + 1} · ${stage.label}`}>
        <div className="milestone-card-heading"><div><span className="milestone-number">阶段 {index + 1}</span><h3>{stage.label}</h3></div>
          <span className="milestone-status">{stage.attempts[0] ? milestoneStatusLabel(stage.attempts[0]) : MILESTONE_STATUS[stage.status]}</span></div>
        {stage.attempts.length ? <>
          <MilestoneAttempt key={keyFor(stage.attempts[0])} attempt={stage.attempts[0]} sync={sync} scope={scope}/>
          {stage.attempts.length > 1 && <details className="milestone-history"><summary>历史记录（{stage.attempts.length - 1}）</summary>
            {stage.attempts.slice(1).map(attempt => <MilestoneAttempt key={keyFor(attempt)} attempt={attempt} sync={sync} scope={scope} historical/>)}
          </details>}
        </> : <p className="milestone-empty">尚无显式执行记录</p>}
      </article>)}</div>}
    {pane.status === 'ready' && <StageConvergence stages={pane.stages} sync={sync} scope={scope}/>}
  </section>;
}
