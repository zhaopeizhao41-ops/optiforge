// Live progress for a running optimization task, published by the engine once per
// round. Informational only: it never stands in for a quality verdict.
const finite = value => typeof value === 'number' && Number.isFinite(value);
const count = value => Number.isSafeInteger(value) && value > 0;
const merit = value => value.toLocaleString(undefined, { maximumSignificantDigits: 4 });

export function taskProgressLabel(task) {
  const progress = task?.progress;
  if (!progress || typeof progress !== 'object' || Array.isArray(progress) || !count(progress.round)) return '';
  const parts = [count(progress.rounds) ? `第 ${progress.round}/${progress.rounds} 轮` : `第 ${progress.round} 轮`];
  if (finite(progress.merit_after)) parts.push(`MF ${merit(progress.merit_after)}`);
  if (progress.timed_out === true) parts.push('本轮超时，正在取消');
  else if (progress.stop_reason === 'merit_function_non_finite') parts.push('评价函数非有限，正在停止');
  return parts.join(' · ');
}
