// Multi-anchor question context: places marked across the workbench (surfaces, one
// analysis view, an optimization stage), each with an optional comment, collected as
// drafts and sent with one question to the Agent. Adapted from the ContextRequest
// anchors and visual-region drafts of Agentero (poco-ai/Agentero, MIT, 820f5a8):
// drafts are ephemeral, bound to what they point at, and consumed on submit.
// Limits and shapes mirror core/design_workflow.validate_question_target.
import { ANALYSIS_FOCUS_TABS } from './workbench-ui.js';
import { analysisNames } from './workbench-analysis.js';

export const MAX_QUESTION_ANCHORS = 12;
export const MAX_ANCHOR_COMMENT = 2000;
// Tabs that show one field at a time; overview and field curvature span all fields.
export const FIELD_TABS = Object.freeze(['mtf', 'wavefront', 'spot', 'ray_fan']);
export const QUESTION_STAGES = Object.freeze(['stage_1', 'stage_2', 'stage_3', 'stage_4']);
const RESULT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/;
const MODEL_KEYS = ['service_instance_id', 'model_id', 'model_revision', 'model_project', 'configuration'];
const SCOPE_KEYS = ['service_instance_id', 'model_project'];
const KEYS = { surface: ['kind', 'index'], analysis: ['kind', 'tab', 'result_id', 'field'], stage: ['kind', 'stage'] };

const object = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const index = (value, minimum) => Number.isSafeInteger(value) && value >= minimum;

/** An anchor without its comment, exactly as the backend accepts it. */
export function validAnchor(anchor) {
  if (!object(anchor) || !Object.hasOwn(KEYS, anchor.kind)) return false;
  const allowed = [...KEYS[anchor.kind], 'comment'];
  if (!Object.keys(anchor).every(key => allowed.includes(key))) return false;
  if ('comment' in anchor && !(typeof anchor.comment === 'string' && anchor.comment.trim()
      && anchor.comment.length <= MAX_ANCHOR_COMMENT)) return false;
  if (anchor.kind === 'surface') return index(anchor.index, 0);
  if (anchor.kind === 'stage') return QUESTION_STAGES.includes(anchor.stage);
  return ANALYSIS_FOCUS_TABS.includes(anchor.tab) && typeof anchor.result_id === 'string' && RESULT_ID.test(anchor.result_id)
    && (!('field' in anchor) || FIELD_TABS.includes(anchor.tab) && index(anchor.field, 1));
}

/** Identity without the comment: two anchors at one place are duplicates. */
export function anchorKey(anchor) {
  return JSON.stringify(KEYS[anchor.kind].filter(key => key in anchor).map(key => [key, anchor[key]]));
}

export function anchorLabel(anchor) {
  if (anchor.kind === 'surface') return `S${anchor.index}`;
  if (anchor.kind === 'stage') return `阶段 ${QUESTION_STAGES.indexOf(anchor.stage) + 1}`;
  const name = anchor.tab === 'overview' ? '分析概览' : analysisNames[anchor.tab] || anchor.tab;
  return 'field' in anchor ? `${name} · F${anchor.field}` : name;
}

/** Short label for a list of anchors: the first three, then a count. */
export function anchorsLabel(anchors) {
  const shown = anchors.slice(0, 3).map(anchorLabel).join(' · ');
  return anchors.length > 3 ? `${shown} 等 ${anchors.length} 处` : shown;
}

export function sameReference(a, b, keys = MODEL_KEYS) {
  return object(a) && object(b) && keys.every(key => a[key] !== undefined && a[key] === b[key]);
}

// A stage belongs to the project's workflow, not to one model revision: optimization
// rounds change the revision while the stage record stays the same.
const bindingKeys = anchor => anchor.kind === 'stage' ? SCOPE_KEYS : MODEL_KEYS;

export const emptyQuestionContext = Object.freeze({ drafts: Object.freeze([]), dropped: 0, sequence: 0 });

/**
 * Add one anchor bound to the currently observed model. Returns {state, error};
 * the state is unchanged on any error. A missing observation refuses instead of guessing.
 */
export function addDraft(state, anchor, reference) {
  if (!object(reference) || !MODEL_KEYS.every(key => reference[key] !== undefined && reference[key] !== null))
    return { state, error: '当前没有可提问的模型' };
  if (!validAnchor(anchor) || 'comment' in anchor) return { state, error: '无法加入这一项' };
  const key = anchorKey(anchor);
  if (state.drafts.some(draft => anchorKey(draft.anchor) === key)) return { state, error: `${anchorLabel(anchor)} 已在提问上下文中` };
  if (state.drafts.length >= MAX_QUESTION_ANCHORS) return { state, error: `提问上下文最多 ${MAX_QUESTION_ANCHORS} 处` };
  const binding = Object.fromEntries(MODEL_KEYS.map(name => [name, reference[name]]));
  const sequence = state.sequence + 1;
  const draft = { id: `anchor-${sequence}`, anchor: { ...anchor }, comment: '', binding };
  return { state: { ...state, sequence, drafts: [...state.drafts, draft] }, error: null };
}

export function removeDraft(state, id) {
  const drafts = state.drafts.filter(draft => draft.id !== id);
  return drafts.length === state.drafts.length ? state : { ...state, drafts };
}

/** Comments longer than the limit are refused rather than cut, like the backend. */
export function setDraftComment(state, id, comment) {
  if (typeof comment !== 'string' || comment.length > MAX_ANCHOR_COMMENT) return state;
  return { ...state, drafts: state.drafts.map(draft => draft.id === id ? { ...draft, comment } : draft) };
}

export function consumeDrafts(state, ids) {
  const used = new Set(ids);
  const drafts = state.drafts.filter(draft => !used.has(draft.id));
  return drafts.length === state.drafts.length ? state : { ...state, drafts };
}

/**
 * Keep drafts in step with the observed model. A known, different model drops the
 * drafts bound to the old one and counts them; a missing observation (null) only
 * pauses adding and asking -- it is not evidence that the model changed.
 */
export function reconcileDrafts(state, reference) {
  if (!object(reference)) return state;
  const drafts = state.drafts.filter(draft => sameReference(draft.binding, reference, bindingKeys(draft.anchor)));
  if (drafts.length === state.drafts.length) return state;
  return { ...state, drafts, dropped: state.dropped + state.drafts.length - drafts.length };
}

export function clearDropped(state) {
  return state.dropped ? { ...state, dropped: 0 } : state;
}

/** Drafts that may be sent with a question about the model `reference`. */
export function draftsFor(state, reference) {
  return state.drafts.filter(draft => sameReference(draft.binding, reference, bindingKeys(draft.anchor)));
}

/**
 * The submit target. With no drafts it is the original single-surface shape,
 * byte for byte, so the backend and older tasks keep their meaning.
 */
export function buildTarget(drafts, fallbackSurface) {
  if (!drafts.length) return index(fallbackSurface, 0) ? { kind: 'surface', index: fallbackSurface } : null;
  return {
    kind: 'context',
    anchors: drafts.map(draft => draft.comment.trim() ? { ...draft.anchor, comment: draft.comment.trim() } : { ...draft.anchor }),
  };
}

/** Read a stored target of either shape; null when it is missing or malformed. */
export function parseTarget(target) {
  if (!object(target)) return null;
  if (target.kind === 'surface') {
    return Object.keys(target).every(key => key === 'kind' || key === 'index') && index(target.index, 0)
      ? { kind: 'surface', anchors: [{ kind: 'surface', index: target.index }] } : null;
  }
  if (target.kind !== 'context' || !Array.isArray(target.anchors) || !target.anchors.length
      || target.anchors.length > MAX_QUESTION_ANCHORS || !target.anchors.every(validAnchor)) return null;
  return { kind: 'context', anchors: target.anchors };
}
