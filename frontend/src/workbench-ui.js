export const UI_PAGES = Object.freeze(['workflow', 'design', 'analysis']);
const PAGE_SET = new Set(UI_PAGES);

export function isUiPage(value) {
  return typeof value === 'string' && PAGE_SET.has(value);
}

export function normalizeUiPage(value) {
  return isUiPage(value) ? value : 'design';
}

export function newUiSessionId() {
  const crypto = globalThis.crypto;
  if (crypto?.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  const bytes = new Uint8Array(16);
  if (crypto?.getRandomValues) crypto.getRandomValues(bytes);
  else for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function validCommand(value) {
  return Boolean(value) && typeof value.command_id === 'string' && value.command_id.length > 0
    && isUiPage(value.page) && typeof value.request_id === 'string' && validFocus(value.page, value.focus);
}

export function commandReceipt(value) {
  if (!value || typeof value !== 'object') return null;
  if (typeof value.command_id !== 'string' || !isUiPage(value.page)) return null;
  return { command_id: value.command_id, page: value.page, request_id: value.request_id ?? '' };
}

export const AUTO_COMMAND_PREFIX = 'auto-';

// Auto commands follow Agent work; explicit agent navigation is never filtered.
export function isAutoCommand(command) {
  return typeof command?.request_id === 'string' && command.request_id.startsWith(AUTO_COMMAND_PREFIX);
}

export function pageAllowed(page, { canWorkflow = false } = {}) {
  if (page === 'workflow') return canWorkflow === true;
  // The analysis page always exists; capabilities only gate running analyses.
  return page === 'analysis' || page === 'design';
}

/**
 * Conservative local deadline from the request start and the server-reported
 * remaining time. The caller re-checks before setPage and before ACK.
 */
export function commandDeadline(command, requestStartMs) {
  const remaining = Number.isFinite(command?.remaining_ms) ? Math.max(0, command.remaining_ms) : 0;
  return requestStartMs + remaining;
}

export function commandExpired(deadlineMs, nowMs) {
  return !Number.isFinite(deadlineMs) || nowMs > deadlineMs;
}

// Mirrors core/workbench_ui.valid_focus: a page-specific deep-link target.
export const ANALYSIS_FOCUS_TABS = Object.freeze(['overview', 'mtf', 'wavefront', 'spot', 'ray_fan', 'field_curvature']);
export const WORKFLOW_FOCUS_TABS = Object.freeze(['requirements', 'proposal', 'tasks', 'milestones']);
const STAGES = ['stage_1', 'stage_2', 'stage_3', 'stage_4'];
const index = (value, minimum) => Number.isSafeInteger(value) && value >= minimum && value <= 100000;
export function validFocus(page, focus) {
  if (focus === null || focus === undefined) return true;
  if (!focus || typeof focus !== 'object' || Array.isArray(focus)) return false;
  const keys = Object.keys(focus);
  if (!keys.length) return false;
  if (page === 'design') return keys.length === 1 && keys[0] === 'surface' && index(focus.surface, 0);
  if (page === 'analysis') return keys.every(key => key === 'tab' || key === 'field') && ANALYSIS_FOCUS_TABS.includes(focus.tab)
    && (!('field' in focus) || index(focus.field, 1));
  if (page === 'workflow') return keys.every(key => key === 'tab' || key === 'stage') && WORKFLOW_FOCUS_TABS.includes(focus.tab)
    && (!('stage' in focus) || focus.tab === 'milestones' && STAGES.includes(focus.stage));
  return false;
}

/**
 * Decide which part of a focus the current page can honour. Never substitutes a
 * different target: an unavailable part is dropped and explained.
 * context: design {surfaces:[index]}, analysis {fields:[index]|null}, workflow {stageAvailable}.
 */
export function resolveFocus(page, focus, context = {}) {
  if (!validFocus(page, focus) || !focus) return { actual: null, reason: focus ? 'FOCUS_INVALID' : null };
  if (page === 'design') {
    return (context.surfaces || []).includes(focus.surface) ? { actual: { surface: focus.surface }, reason: null }
      : { actual: null, reason: `FOCUS_UNAVAILABLE: 当前模型没有 S${focus.surface}` };
  }
  if (page === 'analysis') {
    if (!('field' in focus)) return { actual: { tab: focus.tab }, reason: null };
    if (Array.isArray(context.fields) && context.fields.includes(focus.field)) return { actual: { ...focus }, reason: null };
    return { actual: { tab: focus.tab }, reason: context.fields ? `FOCUS_PARTIAL: 当前结果不含视场 F${focus.field}`
      : 'FOCUS_PARTIAL: 当前没有分析结果，未定位视场' };
  }
  if (!('stage' in focus)) return { actual: { tab: focus.tab }, reason: null };
  return context.stageAvailable ? { actual: { ...focus }, reason: null }
    : { actual: { tab: focus.tab }, reason: `FOCUS_PARTIAL: 阶段记录未显示，未定位 ${focus.stage}` };
}
