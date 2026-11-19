import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import {
  addDraft, clearDropped, consumeDrafts, draftsFor, emptyQuestionContext, reconcileDrafts, removeDraft, setDraftComment,
} from './question-context.js';

function reducer(state, action) {
  switch (action.type) {
    case 'add': return action.next;
    case 'remove': return removeDraft(state, action.id);
    case 'comment': return setDraftComment(state, action.id, action.comment);
    case 'consume': return consumeDrafts(state, action.ids);
    case 'reconcile': return reconcileDrafts(state, action.reference);
    case 'clear-dropped': return clearDropped(state);
    default: return state;
  }
}

const referenceKey = reference => reference ? JSON.stringify(reference) : '';

/**
 * Question-context drafts for the whole workbench. Lives in the always-mounted
 * Workbench so drafts survive page switches; never persisted. `reference` is the
 * observed model (task-provenance.observedReference) or null while unknown.
 */
export function useQuestionContext(reference) {
  const [state, dispatch] = useReducer(reducer, emptyQuestionContext);
  const [notice, setNotice] = useState('');
  const key = referenceKey(reference);
  useEffect(() => { dispatch({ type: 'reconcile', reference }); }, [key]);
  useEffect(() => {
    if (!state.dropped) return;
    setNotice(`模型已变化，已移除 ${state.dropped} 处旧的提问上下文`);
    dispatch({ type: 'clear-dropped' });
  }, [state.dropped]);
  const add = useCallback((anchor) => {
    const { state: next, error } = addDraft(state, anchor, reference);
    setNotice(error || '');
    if (!error) dispatch({ type: 'add', next });
    return !error;
  }, [state, key]);
  const drafts = useMemo(() => reference ? draftsFor(state, reference) : state.drafts, [state, key]);
  return {
    drafts,
    reference,
    canAdd: Boolean(reference),
    notice,
    dismissNotice: useCallback(() => setNotice(''), []),
    add,
    remove: useCallback((id) => dispatch({ type: 'remove', id }), []),
    comment: useCallback((id, comment) => dispatch({ type: 'comment', id, comment }), []),
    consume: useCallback((ids) => dispatch({ type: 'consume', ids }), []),
  };
}
