// One ordering boundary for polling, explicit writes and service replacement.
export class DesignWorkflowClient {
  constructor(api, emit) {
    this.api = api;
    this.emit = emit;
    this.epoch = 0;
    this.pending = false;
    this.active = true;
    this.current = null;
    this.saved = null;
  }
  reset(instance) {
    if (this.instance === instance) return;
    this.instance = instance;
    this.epoch++;
    this.current = null;
    this.saved = null;
    this.pending = false;
    this.emit({ view: null, saved: null, error: "", busy: false });
  }
  stop() {
    this.active = false;
    this.epoch++;
  }
  async refresh() {
    if (this.pending || !this.active) return;
    const generation = ++this.epoch;
    try {
      const v = await this.api.workflow("view");
      if (
        !this.active ||
        generation !== this.epoch ||
        (this.instance && v.service_instance_id !== this.instance)
      )
        return;
      this.current = v;
      this.emit({ view: v, saved: this.saved, error: "" });
    } catch (e) {
      if (this.active && generation === this.epoch)
        this.emit({ error: e.message });
    }
  }
  async act(
    action,
    payload = {},
    project = this.current?.project_name,
    review = null,
  ) {
    if (this.pending || !this.active)
      throw new Error("已有操作进行中，请等待结果");
    const expected = review || this.current;
    if (!expected) throw new Error("请先读取工程");
    const submitted = action === "save" ? fieldsFor(payload) : null;
    this.emit({ busy: true, error: "" });
    const generation = ++this.epoch;
    this.pending = generation;
    try {
      const v = await this.api.workflow(
        action,
        project,
        payload,
        expected.context,
        expected.service_instance_id,
      );
      if (
        !this.active ||
        generation !== this.epoch ||
        (this.instance && v.service_instance_id !== this.instance)
      )
        throw new Error(
          "服务已变化，操作结果待核对；请重新读取，不要自动重复提交",
        );
      this.current = v;
      if (action === "save")
        this.saved = { before: expected, after: v, fields: submitted };
      this.emit({ view: v, saved: this.saved });
      return v;
    } catch (e) {
      if (this.active && generation === this.epoch)
        this.emit({ error: e.message });
      throw e;
    } finally {
      if (this.pending === generation) {
        this.pending = false;
        if (this.active) this.emit({ busy: false });
      }
    }
  }
}

export function fieldsFor(d) {
  return {
    system_type: d.system_type,
    specs: { ...d.specs },
    sources: { ...d.sources },
    user_prompt: d.user_prompt,
    constraints: d.constraints,
  };
}

// Workflow data is JSON: object key order is irrelevant; types, keys and array
// order remain significant. Use the same comparison for editing and syncing.
export function sameContent(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object")
    return false;
  if (Array.isArray(a) || Array.isArray(b))
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((item, i) => sameContent(item, b[i]))
    );
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every((key) => Object.hasOwn(b, key) && sameContent(a[key], b[key]))
  );
}

export function taskInstruction(t, project) {
  const step = (n, text) => `${n}. ${text}`;
  const head = [
    `任务 ${t.request_id}（页面版本 v${t.version}），工程 ${project}。以下步骤由已接入 OptiForge 的 Agent 执行；粘贴本指令不等于领取或授权。`,
    '1. 先调用 zemax_design_workflow(action="view") 读取工程与精确 context；context 过期时先重新 view 核对。',
    `2. 调用 zemax_list_design_tasks(project_name="${project}")，核对任务 ID、当前版本与 target 未被替换。`,
    step(
      3,
      "用 zemax_claim_design_task 领取：按预算显式选择 lease_seconds（30–3600 秒），保管 lease_token；领取不授权光学仿真。",
    ),
  ];
  const research = t.kind === "initial_proposal" || t.kind === "proposal_revision";
  const work = research
    ? [
        step(
          4,
          "以 requirements_snapshot.audit.result.recognized_specs 原样作为 target_specs，按冻结需求联网检索原文并记录来源、时间及版本，数值计算运行代码留存输入、单位和输出，不自行改写规格。",
        ),
        step(
          5,
          '调用 zemax_update_design_task(status="progress") 报进度，之后一律使用返回的新 version。',
        ),
        step(
          6,
          "调用 zemax_register_design_proposal，传 workflow_context、task_claim={request_id, version（用最新返回值）, lease_token} 与完整方案（含 topology、risks 等）。",
        ),
        step(
          7,
          '以登记返回的 proposal_hash 调用 zemax_update_design_task(status="completed", result_proposal_hash=...)（version 同样用最新值）。',
        ),
      ]
    : t.kind === "model_handoff"
      ? [
          step(
            4,
            "执行前重新核对当前提案版本、绑定授权和目标身份；已有模型须与工程匹配，尚无模型则只按已确认提案建模。领取不增加授权。",
          ),
          step(
            5,
            '按既有五步流程建模/仿真，完成后调用 zemax_update_design_task(status="completed") 附文字结果。',
          ),
        ]
      : [
          step(
            4,
            "只读解释或建议该表面，不修改光学参数；重新核对模型身份、修订、配置与 target 表面。",
          ),
          step(
            5,
            '完成后调用 zemax_update_design_task(status="completed") 附文字结果。',
          ),
        ];
  return [...head, ...work, "租约到期需用户显式重试；progress 不自动续租。"].join("\n");
}

export function reconcileDraft(base, local, incoming, saved = null) {
  if (!incoming) return { base, draft: local };
  const next = {
    project: incoming.project_name,
    revision: incoming.draft.revision,
    fields: fieldsFor(incoming.draft),
    review: incoming,
  };
  const savedBaseMatches = Boolean(
    base &&
    saved &&
    base.project === saved.before.project_name &&
    base.revision === saved.before.draft.revision &&
    base.review?.service_instance_id === saved.before.service_instance_id &&
    sameContent(base.review?.context, saved.before.context) &&
    sameContent(base.fields, fieldsFor(saved.before.draft)) &&
    saved.after.project_name === base.project &&
    saved.after.service_instance_id === saved.before.service_instance_id &&
    sameContent(fieldsFor(saved.after.draft), saved.fields),
  );
  // A successful save and any later poll may arrive in the same render. First
  // acknowledge only our own exact receipt, independently of that later view.
  // This preserves typing and deliberate reverts made while the save ran.
  if (savedBaseMatches)
    base = {
      project: saved.after.project_name,
      revision: saved.after.draft.revision,
      fields: saved.fields,
      review: saved.after,
    };
  // Remote content matching our edits is not a save receipt or permission to
  // adopt its context. A dirty baseline changes only through explicit recovery
  // or our own successful save; clean editors follow the active project.
  if (!base || sameContent(local, base.fields))
    return { base: next, draft: next.fields };
  return { base, draft: local };
}

export function isDraftDirty(base, draft) {
  return Boolean(base && draft) && !sameContent(draft, base.fields);
}

// Only an explicit successful return may refresh the selection identity. Any
// requirements, proposal, authorization or service change remains a conflict.
export function reconcileReturnedDraft(editor, incoming) {
  const { base } = editor;
  if (
    !base?.review?.context ||
    !incoming?.context ||
    base.project !== incoming.project_name ||
    base.revision !== incoming.draft.revision ||
    base.review.service_instance_id !== incoming.service_instance_id ||
    !sameContent(base.fields, fieldsFor(incoming.draft))
  )
    return editor;
  const { selection_revision: before, ...previous } = base.review.context;
  const { selection_revision: after, ...current } = incoming.context;
  if (
    !Number.isSafeInteger(before) ||
    !Number.isSafeInteger(after) ||
    after <= before ||
    previous.project_name !== base.project ||
    previous.active_project !== base.project ||
    !sameContent(previous, current)
  )
    return editor;
  return { ...editor, base: { ...base, review: incoming } };
}
