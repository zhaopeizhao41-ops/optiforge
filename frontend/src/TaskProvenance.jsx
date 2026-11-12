import React from 'react';
import { taskMessage, taskProvenance } from './task-provenance.js';
import './task-provenance.css';

const missing = value => value ?? '未记录';
export default function TaskProvenance({ task, sync, project }) {
  const provenance = taskProvenance(task, sync, project), message = taskMessage(task);
  const relation = provenance.relation;
  const historical = relation.status === 'historical' || message?.historical;
  return <>
    <section className="task-provenance" aria-label="任务来源" data-task-relation={relation.status}>
      <dl className="task-provenance-summary">
        <div><dt>目标</dt><dd>{provenance.target}</dd></div>
        <div><dt>工程</dt><dd>{provenance.project}</dd></div>
        <div><dt>记录修订</dt><dd>{provenance.revision === null ? '未记录' : `修订 ${provenance.revision}`}</dd></div>
        <div><dt>配置</dt><dd>{missing(provenance.configuration)}</dd></div>
      </dl>
      <p className="task-provenance-relation">{relation.label}{relation.reasons.length ? ` · ${relation.reasons.join('；')}` : ''}</p>
      {relation.status === 'unbound' && <p className="task-provenance-note">此任务未绑定具体模型，按任务内容交接。</p>}
      {relation.status === 'unknown' && <p className="task-provenance-note">来源未完整记录或当前模型观察不可用。</p>}
      {provenance.validity && <p className="task-provenance-note">{provenance.validity} · 任务有效性与模型引用关系分别记录。</p>}
      <details className="task-provenance-details"><summary>完整模型引用</summary>
        <dl aria-label="已记录模型引用">{[['服务实例', provenance.source.service_instance_id], ['模型身份', provenance.source.model_id],
          ['模型工程', provenance.source.model_project], ['模型修订', provenance.revision], ['配置', provenance.configuration]].map(([key, value]) =>
          <div key={key}><dt>{key}</dt><dd>{missing(value)}</dd></div>)}</dl>
        <p className="task-provenance-note">来源仅用于核对身份，不表示光学验收、仿真授权或 Agent 已执行。</p>
      </details>
    </section>
    {message && <div className="task-message" data-task-message={message.label}>
      <h4>{message.label}</h4><p>{message.text}</p>
      {historical && <p className="task-provenance-note">历史任务记录 · 不代表当前模型验证。</p>}
      {!historical && task.status === 'completed' && <p className="task-provenance-note">任务回复不代表当前像质或优化验收。</p>}
    </div>}
  </>;
}
