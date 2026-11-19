import React, { useState } from 'react';
import { MessageSquare, MessageSquareText, X } from 'lucide-react';
import { Button } from './ui.jsx';
import { MAX_ANCHOR_COMMENT, MAX_QUESTION_ANCHORS, anchorLabel } from './question-context.js';
import './question-context.css';

/**
 * The marks collected for one Agent question, visible on every page. Each chip can
 * carry a comment; asking opens the question dialog on the workflow page, where the
 * drafts are sent and then removed. Nothing here is persisted or sent on its own.
 */
export default function QuestionContextTray({ context, canAsk, onAsk }) {
  const [editing, setEditing] = useState(null);
  const { drafts, notice } = context;
  if (!drafts.length && !notice) return null;
  const current = drafts.find((draft) => draft.id === editing);
  return <section className="question-tray" aria-label="提问上下文">
    {drafts.length > 0 && <div className="question-tray-row">
      <span className="question-tray-title">提问上下文 <span>{drafts.length}/{MAX_QUESTION_ANCHORS}</span></span>
      <ul className="question-tray-chips">{drafts.map((draft) => {
        const label = anchorLabel(draft.anchor), commented = Boolean(draft.comment.trim());
        return <li key={draft.id} className="question-chip" data-question-anchor={draft.anchor.kind}>
          <Button className="question-chip-label" aria-pressed={editing === draft.id}
            aria-label={`${label}${commented ? ' · 已有批注' : ''} · 编辑批注`} onClick={() => setEditing(editing === draft.id ? null : draft.id)}>
            <span>{label}</span>{commented && <MessageSquareText size={13} aria-hidden="true" />}
          </Button>
          <Button className="question-chip-remove" aria-label={`移除 ${label}`}
            onClick={() => { context.remove(draft.id); if (editing === draft.id) setEditing(null); }}><X size={13} /></Button>
        </li>;
      })}</ul>
      <Button className="question-tray-ask" disabled={!canAsk} onClick={onAsk}><MessageSquare size={16} />向 Agent 提问</Button>
    </div>}
    {current && <label className="question-tray-comment">
      <span>{anchorLabel(current.anchor)} 的批注 · 可选 · {current.comment.length}/{MAX_ANCHOR_COMMENT}</span>
      <textarea value={current.comment} maxLength={MAX_ANCHOR_COMMENT} placeholder="写下希望 Agent 关注的地方"
        onChange={(event) => context.comment(current.id, event.target.value)} />
    </label>}
    {notice && <p className="question-tray-notice" role="status">{notice}
      <Button className="question-chip-remove" aria-label="关闭提示" onClick={context.dismissNotice}><X size={13} /></Button></p>}
  </section>;
}

/** Small "add to question" control used on the design, analysis and stage views. */
export function AddToQuestion({ context, anchor, disabled = false, label = '加入提问' }) {
  return <Button className="question-add" disabled={disabled || !context?.canAdd || !anchor}
    title="加入提问上下文，和其他标记一起发给 Agent" onClick={() => context.add(anchor)}>
    <MessageSquare size={15} />{label}
  </Button>;
}
