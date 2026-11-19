import React, { useEffect, useRef, useState } from 'react';
import { Button } from './ui.jsx';
import TaskProvenance from './TaskProvenance.jsx';
import { taskMessage, taskPane } from './task-provenance.js';
import { createDesignTaskHistoryController } from './design-task-history.js';

const statusNames = { completed: '已完成', failed: '失败', superseded: '已归档 · 引用过期' };
const intentNames = { initial_proposal: '请求初始方案', proposal_revision: '请求修改方案',
  surface_question: 'Agent 提问', model_handoff: '建模 / 仿真交接' };

function HistoryPages({ scope, api, sync }) {
  const controller = useRef(null);
  const [view, setView] = useState(null);
  const [copy, setCopy] = useState(null);
  const [copyError, setCopyError] = useState('');
  useEffect(() => {
    const reader = createDesignTaskHistoryController({ api, scope, onChange: setView });
    controller.current = reader; setView(reader.view);
    return () => { reader.stop(); if (controller.current === reader) controller.current = null; };
  }, [api, scope.service_instance_id, scope.project_name]);
  const state = view || { tasks: [], status: 'idle', pending: false, loaded: false, hasMore: false, error: '' };
  async function copyText(task, kind) {
    try {
      await navigator.clipboard.writeText(kind === 'result' ? task.result_text : task.request_id);
      setCopy({ id: task.request_id, kind }); setCopyError('');
    } catch { setCopyError('剪贴板不可用，请选择记录中的文本手动复制'); }
  }
  return <>
    <p className="workflow-id">读取来源：{scope.project_name} · {scope.service_instance_id}</p>
    <div className="workflow-actions">
      {!state.loaded && <Button disabled={state.pending} onClick={() => controller.current?.load()}>
        读取较早终态任务
      </Button>}
      {state.loaded && <Button disabled={state.pending} onClick={() => controller.current?.refresh()}>重新读取历史</Button>}
      {state.loaded && state.hasMore && <Button disabled={state.pending} onClick={() => controller.current?.more()}>读取更多</Button>}
    </div>
    <p role="status">{state.pending ? state.loaded ? '正在读取更多历史…' : '正在读取较早终态任务…'
      : !state.loaded ? '历史尚未读取，按需显式加载。'
        : `已读取历史 ${state.tasks.length} 条${state.hasMore ? ' · 可继续读取' : ' · 已到本次历史末尾'}`}</p>
    {state.error && <p role="alert">{state.error}{state.loaded ? '；已读取记录和续读位置保留，可再次读取更多。' : ''}</p>}
    {copyError && <p role="alert">{copyError}</p>}
    {state.loaded && !state.tasks.length && <p>当前工程没有位于最近 100 条任务之前的终态记录。</p>}
    <div className="workflow-tasks" aria-busy={state.pending}>
      {state.tasks.map(task => {
        const message = taskMessage(task);
        return <article key={task.request_id} className="workflow-task" data-terminal-task-id={task.request_id}>
          <div><h3>{intentNames[task.kind]}</h3><span>{statusNames[task.status]}</span></div>
          <p>{task.user_text}</p>
          <TaskProvenance task={task} sync={sync} project={scope.project_name}/>
          <div className="workflow-actions">
            {task.result_text.trim() && <Button onClick={() => copyText(task, 'result')}>
              {copy?.id === task.request_id && copy.kind === 'result' ? '已复制结果' : '复制结果'}
            </Button>}
            <Button onClick={() => copyText(task, 'id')}>
              {copy?.id === task.request_id && copy.kind === 'id' ? '已复制任务 ID' : '复制任务 ID'}
            </Button>
          </div>
          <details><summary>任务身份与原始文本</summary>
            <p className="workflow-id">{task.request_id}</p>
            <p>需求 v{task.requirements_revision} · 任务 v{task.version}</p>
            {task.progress && task.progress !== message?.text && <><h4>原进度</h4><p>{task.progress}</p></>}
            {task.result_text && task.result_text !== message?.text && <><h4>原结果</h4><p>{task.result_text}</p></>}
          </details>
        </article>;
      })}
    </div>
  </>;
}

export default function TerminalTaskHistory({ flow, sync }) {
  const pane = taskPane(flow, sync);
  const ready = pane.status === 'ready' && typeof sync?.api?.readDesignTaskHistory === 'function';
  const scope = ready ? { service_instance_id: sync.engine.service_instance_id, project_name: sync.engine.active_project } : null;
  return <section aria-label="较早终态任务（只读）">
    <h3>较早终态任务（只读）</h3>
    <p>读取首次加载时最近 100 条任务之前的已完成、失败与已归档记录；与当前任务列表分别计数。</p>
    <p>此范围按插入位置固定，终态成员可能变化，需重新读取历史以更新。历史完成状态不表示当前光学验收或仿真授权。</p>
    {ready ? <HistoryPages key={JSON.stringify([scope.service_instance_id, scope.project_name])}
      scope={scope} api={sync.api} sync={sync}/>
      : <p role="status">{pane.status === 'ready' ? '历史读取接口不可用，请更新工作台。' : pane.message}</p>}
  </section>;
}
