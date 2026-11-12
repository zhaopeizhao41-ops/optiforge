// The pane comes from taskPane; counts describe its loaded records only.
export const TASK_FILTERS = Object.freeze([
  Object.freeze({ value: "all", label: "全部" }),
  Object.freeze({ value: "pending", label: "待领取" }),
  Object.freeze({ value: "claimed", label: "已领取" }),
  Object.freeze({ value: "completed", label: "已完成" }),
  Object.freeze({ value: "failed", label: "失败" }),
  Object.freeze({ value: "superseded", label: "已归档" }),
]);

const statuses = TASK_FILTERS.slice(1).map(({ value }) => value);

export function taskFilterView(pane, selected = "all") {
  if (pane?.status !== "ready" || !Array.isArray(pane.tasks)
      || !pane.tasks.every(task => task && statuses.includes(task.status))) {
    return { selected: "all", counts: null, total: null, tasks: [] };
  }
  const filter = TASK_FILTERS.some(({ value }) => value === selected) ? selected : "all";
  const counts = Object.fromEntries(TASK_FILTERS.map(({ value }) => [value, 0]));
  for (const task of pane.tasks) counts[task.status] += 1;
  counts.all = pane.tasks.length;
  return {
    selected: filter,
    counts,
    total: counts.all,
    tasks: filter === "all" ? pane.tasks : pane.tasks.filter(task => task.status === filter),
  };
}
