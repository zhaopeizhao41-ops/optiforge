import React, { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  Plus,
  FolderOpen,
  ArrowRight,
  Check,
  Copy,
  RefreshCw,
  MessageSquare,
  FileText,
} from "lucide-react";
import { Button, Modal } from "./ui.jsx";
import "./design-workflow.css";
import {
  isDraftDirty,
  reconcileDraft,
  reconcileReturnedDraft,
  sameContent,
  taskInstruction,
} from "./design-workflow.js";
import { ProposalStructure } from "./ProposalStructure.jsx";
import StageMilestones from "./StageMilestones.jsx";
import { resolveFocus } from "./workbench-ui.js";
import { SurfacePicker } from "./SurfacePicker.jsx";
import { createUnsavedChangesGuard, guardNeeded } from "./unsaved-changes.js";
import TaskProvenance from "./TaskProvenance.jsx";
import { taskPane } from "./task-provenance.js";
import { TASK_FILTERS, taskFilterView } from "./task-filters.js";
import TerminalTaskHistory from "./TerminalTaskHistory.jsx";
import { anchorLabel, anchorsLabel, buildTarget, draftsFor } from "./question-context.js";

export const stageNames = {
  draft: "需求草稿",
  needs_requirements: "待补需求",
  awaiting_agent: "待 Agent 领取",
  researching: "研究中",
  proposal_review: "待审阅方案",
  changes_requested: "待修改",
  confirmed: "已确认",
  handoff: "后续任务交接",
  handoff_failed: "任务失败",
  lease_expired: "领取已超时",
};
const taskNames = {
  pending: "待领取",
  claimed: "已领取",
  completed: "已完成",
  failed: "失败",
  superseded: "已归档 · 引用过期",
};
const intentNames = {
  initial_proposal: "请求初始方案",
  proposal_revision: "请求修改方案",
  surface_question: "Agent 提问",
  model_handoff: "建模 / 仿真交接",
};
const newId = () => crypto.randomUUID();

function TaskListFilters({ pane, children }) {
  const [selected, setSelected] = useState("all");
  const filtered = taskFilterView(pane, selected);
  return (
    <>
      <div className="workflow-task-filters" role="group" aria-label="任务状态筛选">
        {TASK_FILTERS.map(({ value, label }) => (
          <Button
            key={value}
            aria-pressed={filtered.selected === value}
            onClick={() => setSelected(value)}
          >
            {label}<span className="workflow-task-filter-count">{filtered.counts[value]}</span>
          </Button>
        ))}
      </div>
      <p className="workflow-task-filter-summary" role="status">
        本次已读取 {filtered.total} 条 · 显示 {filtered.tasks.length} 条
      </p>
      {filtered.total === 0 ? (
        <div className="workflow-empty">暂无交接任务</div>
      ) : !filtered.tasks.length ? (
        <div className="workflow-empty workflow-task-filter-empty">
          <p>本次读取的任务中暂无此状态的记录</p>
          <Button onClick={() => setSelected("all")}>查看全部任务</Button>
        </div>
      ) : null}
      <div className="workflow-tasks">
        {filtered.tasks.map(children)}
      </div>
    </>
  );
}

export function WorkflowStrip({ flow, sync, onOpen }) {
  const v = flow.view,
    m = sync.engine?.snapshot?.model;
  return (
    <div className="workflow-strip">
      <Button onClick={onOpen} className="workflow-project">
        <FolderOpen size={16} />
        <span>
          {v?.project_name || "工程"}
          <span>
            {flow.error
              ? "流程未连接 · 显示上次读取"
              : v
                ? stageNames[v.stage]
                : "连接流程服务"}
          </span>
        </span>
        <ArrowRight size={15} />
      </Button>
      <span className="workflow-model">
        {m
          ? `${m.name} · r${sync.engine.model_revision}${m.project?.startsWith("import_") ? " · 导入副本" : " · 原工程模型"}`
          : "未载入模型"}{" "}
        ·{" "}
        {sync.canImportModel === false
          ? flow.available
            ? "协作模式"
            : "只读预览"
          : sync.engine?.validity === "valid"
            ? "原生已连接"
            : "原生未连接"}
        {m && v?.project_name !== sync.engine.model_project
          ? " · 模型属于其他工程"
          : ""}
      </span>
    </div>
  );
}

export default function DesignWorkflow({ flow, sync, selected, onBack, focusRequest, onFocusReport,
  visible = true, questionContext = null, questionRequest = null }) {
  const { view: v, busy, error } = flow;
  const unsavedGuard = useRef(null);
  const observedProject = useRef(null);
  if (!unsavedGuard.current) unsavedGuard.current = createUnsavedChangesGuard();
  const [saving, setSaving] = useState(false);
  const [editor, setEditor] = useState({ base: null, draft: null });
  const { base, draft } = editor;
  const setDraft = (update) =>
    setEditor((current) => ({
      ...current,
      draft: typeof update === "function" ? update(current.draft) : update,
    }));
  const [tab, setTab] = useState("requirements"),
    [modal, setModal] = useState(null),
    [project, setProject] = useState(""),
    [create, setCreate] = useState(true),
    [message, setMessage] = useState(""),
    [copy, setCopy] = useState(""),
    [localError, setLocalError] = useState("");
  const [requestId, setRequestId] = useState(newId),
    [review, setReview] = useState(null);
  const [stageFocus, setStageFocus] = useState(null);
  const handledFocus = useRef(null);
  useEffect(() => {
    if (focusRequest?.page !== "workflow" || handledFocus.current === focusRequest.id) return;
    handledFocus.current = focusRequest.id;
    const { id, focus } = focusRequest;
    setTab(focus.tab);
    if (!focus.stage) {
      const result = resolveFocus("workflow", focus);
      onFocusReport?.(id, result.actual, result.reason);
      return;
    }
    // The stage card renders after the tab switch and the page becomes visible.
    let tries = 0, timer = null, highlight = null;
    const find = () => {
      const card = document.querySelector(`[data-milestone-stage="${focus.stage}"]`);
      if (card && card.offsetParent !== null) {
        card.scrollIntoView({ block: "center", behavior: "smooth" });
        setStageFocus(focus.stage);
        highlight = setTimeout(() => setStageFocus(null), 2600);
      } else if (tries++ < 40) {
        timer = setTimeout(find, 50);
        return;
      }
      const result = resolveFocus("workflow", focus, { stageAvailable: Boolean(card && card.offsetParent !== null) });
      onFocusReport?.(id, result.actual, result.reason);
    };
    timer = setTimeout(find, 0);
    return () => { clearTimeout(timer); };
  }, [focusRequest]);
  useEffect(() => {
    if (!v) return;
    setEditor((current) =>
      reconcileDraft(current.base, current.draft, v, flow.saved),
    );
    if (observedProject.current !== v.project_name) {
      observedProject.current = v.project_name;
      setLocalError("");
      setTab("requirements");
      setModal(null);
    }
  }, [v, flow.saved]);
  const dirty = isDraftDirty(base, draft);
  const projectChanged = Boolean(v && base && base.project !== v.project_name);
  const conflict =
    Boolean(v && base && draft) &&
    (projectChanged || base.revision !== v.draft.revision ||
      (dirty &&
        (base.review?.service_instance_id !== v.service_instance_id ||
          !sameContent(base.review?.context, v.context))));
  useEffect(() => {
    if (!unsavedGuard.current) return undefined;
    unsavedGuard.current.update(guardNeeded({ dirty, saving }));
    return () => unsavedGuard.current?.dispose();
  }, [dirty, saving]);
  const perform = async (action, payload = {}, name, expected = null) => {
    setLocalError("");
    try {
      return await flow.act(action, payload, name, expected);
    } catch (e) {
      setLocalError(e.message);
      return null;
    }
  };
  // The question dialog freezes the current context drafts; they are sent with the
  // question and removed only once the task exists.
  const handledQuestion = useRef(null);
  const submittedQuestion = useRef(null);
  const openQuestion = () => {
    setMessage("");
    setLocalError("");
    setReview({ ...v, selected, anchors: questionContext?.drafts ?? [] });
    setModal("question");
  };
  const questionDone = (ids) => {
    submittedQuestion.current = null;
    questionContext?.consume(ids);
    setRequestId(newId());
    setModal(null);
    setTab("tasks");
  };
  // "Ask" from the tray switches here first: a dialog cannot open inside a hidden page.
  useEffect(() => {
    if (!questionRequest || !visible || !v || handledQuestion.current === questionRequest.id) return;
    handledQuestion.current = questionRequest.id;
    if (v.model_reference?.model_project !== v.project_name) {
      setLocalError("当前模型不属于活动工程，暂不能提问");
      return;
    }
    if (dirty) {
      setLocalError("请先保存需求编辑，再向 Agent 提问");
      return;
    }
    openQuestion();
  }, [questionRequest, visible, v]);
  // A lost response still created the task: once it is listed, treat the question as sent.
  useEffect(() => {
    const sent = submittedQuestion.current;
    if (sent && modal === "question" && v?.tasks?.some((task) => task.request_id === sent.requestId)) {
      setLocalError("");
      questionDone(sent.ids);
    }
  }, [v, modal]);
  if (!v || !draft || (projectChanged && !dirty))
    return (
      <section className="design-workflow">
        <h1>工程协作</h1>
        <p role="status">{error || "正在读取工程流程"}</p>
        {dirty && <p>未保存的编辑仍保留在本页，请重新连接后继续处理。</p>}
        <Button onClick={flow.refresh}>重新连接</Button>
      </section>
    );
  if (projectChanged)
    return (
      <motion.main
        className="design-workflow"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <section className="workflow-card" aria-label="工程切换恢复">
          <div className="workflow-heading workflow-card-title">
            <h1>活动工程已切换</h1>
            <span>未保存</span>
          </div>
          <p role="status">
            {base.project} 的未保存编辑已保留。当前活动工程为 {v.project_name}，
            请先选择如何处理这份草稿。
          </p>
          {(error || localError) && <p role="alert">{error || localError}</p>}
          <div className="workflow-actions">
            <Button
              className="workflow-primary"
              disabled={busy}
              onClick={async () => {
                const next = await perform("select", {}, base.project, v);
                if (next)
                  setEditor((current) => reconcileReturnedDraft(current, next));
              }}
            >
              返回原工程继续编辑
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                setLocalError("");
                setEditor(reconcileDraft(null, null, v));
              }}
            >
              放弃当前编辑并读取新工程
            </Button>
          </div>
          <details>
            <summary>查看保留的草稿 · {base.project}</summary>
            <label className="workflow-description">
              保留的需求草稿
              <textarea
                aria-label="保留的需求草稿"
                readOnly
                value={[
                  `设计类型：${base.review.templates?.[draft.system_type]?.name || draft.system_type}`,
                  `设计目标：${draft.user_prompt}`,
                  ...Object.entries(draft.specs).map(([key, value]) => {
                    const keptTemplate = base.review.templates?.[draft.system_type];
                    const field = [
                      ...(keptTemplate?.mandatory || []),
                      ...(keptTemplate?.recommended || []),
                    ].find(
                      (item) => item.key === key,
                    );
                    return `${field?.name || key}：${value}`;
                  }),
                  `可选约束：${draft.constraints}`,
                ].join("\n")}
              />
            </label>
          </details>
        </section>
      </motion.main>
    );
  const template = v.templates[draft.system_type],
    p = v.proposal,
    audit = v.draft.audit,
    staleAudit = dirty || audit?.revision !== v.draft.revision;
  const change = (key, value, source = "user") =>
    setDraft((d) => {
      const specs = { ...d.specs },
        sources = { ...d.sources };
      if (value === "") {
        delete specs[key];
        delete sources[key];
      } else {
        specs[key] = value;
        sources[key] = source;
      }
      return { ...d, specs, sources };
    });
  const save = async () => {
    if (conflict) {
      setLocalError("另一页面已保存新版本。请复制未保存内容，重新读取后合并。");
      return null;
    }
    if (!dirty) return v;
    setSaving(true);
    try {
      return await perform("save", draft, base.project, base.review);
    } finally {
      setSaving(false);
    }
  };
  const submit = async (kind) => {
    let target = null;
    if (kind === "surface_question") {
      const anchors = review.anchors ?? [];
      if (draftsFor({ drafts: anchors }, review.model_reference).length !== anchors.length) {
        setLocalError("提问上下文所属的模型已变化，请移除后重新加入");
        return;
      }
      target = buildTarget(anchors, review.selected);
      if (!target) {
        setLocalError("请先选中表面或加入提问上下文");
        return;
      }
    }
    const current = await save();
    if (!current) return;
    if (kind === "surface_question")
      submittedQuestion.current = { requestId, ids: (review.anchors ?? []).map((draft) => draft.id) };
    const next = await perform(
      "submit",
      {
        kind,
        user_text:
          kind === "initial_proposal"
            ? draft.user_prompt ||
              "请检索成熟结构并推演，提交方案供我审阅；暂不授权建模仿真。"
            : message,
        request_id: requestId,
        ...(kind === "surface_question"
          ? {
              target,
              expected_model: review.model_reference,
            }
          : {}),
      },
      kind === "initial_proposal" ? undefined : review.project_name,
      kind === "initial_proposal" ? null : review,
    );
    if (next && kind === "surface_question") {
      questionDone(submittedQuestion.current?.ids ?? []);
      return;
    }
    if (next) {
      setRequestId(newId());
      if (kind !== "initial_proposal" || next.draft.audit?.result.is_complete) {
        setModal(null);
        setTab("tasks");
      }
    }
  };
  const issue = error || localError;
  const tasks = taskPane(flow, sync);
  return (
    <motion.main
      className="design-workflow"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="workflow-heading">
        <div>
          <h1>工程协作</h1>
          <p>
            {v.project_name} · {stageNames[v.stage]}
          </p>
        </div>
        <div className="workflow-actions">
          <Button
            onClick={() => {
              setProject("");
              setModal("project");
            }}
          >
            <FolderOpen size={17} />
            切换工程
          </Button>
          <Button onClick={onBack}>
            查看光路
            <ArrowRight size={17} />
          </Button>
        </div>
      </div>
      {issue && (
        <div className="analysis-warning" role="alert">
          {issue}
          <Button onClick={flow.refresh}>重新读取</Button>
        </div>
      )}
      <nav className="workflow-tabs" aria-label="工程流程">
        {[
          ["requirements", "需求"],
          ["proposal", "方案"],
          ["tasks", "任务"],
          ["milestones", "优化阶段"],
        ].map(([key, label]) => (
          <Button
            key={key}
            aria-pressed={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </Button>
        ))}
      </nav>
      {tab === "milestones" && <StageMilestones flow={flow} sync={sync} focusStage={stageFocus} questionContext={questionContext} />}
      {tab === "requirements" && (
        <section className="workflow-card">
          <div className="workflow-card-title">
            <h2>设计需求</h2>
            <span>{dirty ? "未保存" : `已保存 · v${v.draft.revision}`}</span>
          </div>
          {conflict && (
            <div className="analysis-warning" role="alert">
              需求已有新版本，当前编辑不会覆盖。
              <Button
                onClick={() => {
                  setEditor(reconcileDraft(null, null, v));
                }}
              >
                放弃当前编辑并读取新版本
              </Button>
            </div>
          )}
          <div className="workflow-description workflow-picker-field">
            <span>设计类型</span>
            <SurfacePicker
              label="设计类型"
              value={draft.system_type}
              options={Object.entries(v.templates).map(([value, t]) => ({ value, label: t.name }))}
              onChange={(value) => {
                if (value === draft.system_type) return;
                setDraft({
                  ...draft,
                  system_type: value,
                  specs: {},
                  sources: {},
                });
              }}
            />
          </div>
          <label className="workflow-description">
            设计目标
            <textarea
              aria-label="设计目标"
              value={draft.user_prompt}
              onChange={(e) =>
                setDraft({ ...draft, user_prompt: e.target.value })
              }
              placeholder="描述用途、期望效果或已有条件"
            />
          </label>
          {["mandatory", "recommended"].map((group) => (
            <React.Fragment key={group}>
              <h3>{group === "mandatory" ? "必需规格" : "推荐补充"}</h3>
              <div className="workflow-fields">
                {template[group].map((f) => (
                  <div className="workflow-field" key={f.key}>
                    <label>
                      {f.name}
                      <span>{f.unit}</span>
                      <input
                        aria-label={f.name}
                        value={draft.specs[f.key] ?? ""}
                        placeholder={
                          f.key === "fov_or_sensor"
                            ? "如全视场 25 deg / 半视场 12.5 deg / 像面直径 11 mm"
                            : f.key === "wavelength_range"
                              ? "如 400–700 nm"
                              : f.key === "scan_angle_deg"
                                ? "光学半角，单位 deg"
                                : ""
                        }
                        onChange={(e) => change(f.key, e.target.value)}
                      />
                    </label>
                    <div className="workflow-recommendation">
                      <span>{f.default}</span>
                      {f.recommended_value !== null && (
                        <Button
                          aria-label={`采用推荐值 ${f.name}`}
                          onClick={() =>
                            change(f.key, f.recommended_value, "recommended")
                          }
                        >
                          采用
                        </Button>
                      )}
                    </div>
                    {draft.sources[f.key] && (
                      <span className="workflow-origin">
                        {draft.sources[f.key] === "recommended"
                          ? "已采用推荐值"
                          : "用户填写"}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </React.Fragment>
          ))}
          <details>
            <summary>可选约束</summary>
            <label>
              机械、环境或其他限制
              <textarea
                aria-label="可选约束"
                value={draft.constraints}
                onChange={(e) =>
                  setDraft({ ...draft, constraints: e.target.value })
                }
              />
            </label>
          </details>
          <div className="workflow-actions">
            <Button disabled={busy || !dirty || conflict} onClick={save}>
              保存需求
            </Button>
            <Button
              disabled={busy || conflict}
              onClick={async () => {
                if (await save()) await perform("audit");
              }}
            >
              审查需求
            </Button>
            <Button
              className="workflow-primary"
              disabled={busy || conflict}
              onClick={() => submit("initial_proposal")}
            >
              提交需求并请求方案
              <ArrowRight size={16} />
            </Button>
          </div>
          {audit && (
            <div className="workflow-audit" role="status">
              <h3>
                {staleAudit
                  ? "审查已失效，请保存后重审"
                  : audit.result.is_complete
                    ? "规格审查通过"
                    : "请补充或修正规格"}
              </h3>
              {audit.result.missing_mandatory.map((f) => (
                <p key={f.key}>{f.question}</p>
              ))}
              {audit.result.invalid_specs.map((f) => (
                <p key={f.key}>
                  {f.key}：{f.message}
                </p>
              ))}
              {audit.result.conflicts?.map((f, i) => (
                <p key={i}>{f.message}</p>
              ))}
              <p>
                推荐值仅在点击采用后保存；审查通过允许研究，不代表授权仿真。
              </p>
            </div>
          )}
        </section>
      )}
      {tab === "proposal" && (
        <section className="workflow-card">
          <div className="workflow-card-title">
            <h2>方案审阅</h2>
            <span>
              {p ? `提案 v${p.proposal_revision ?? "旧版"}` : "待生成"}
            </span>
          </div>
          {!p ? (
            <div className="workflow-empty">
              <FileText size={32} />
              <p>Agent 尚未登记方案</p>
              <Button onClick={() => setTab("tasks")}>查看交接任务</Button>
            </div>
          ) : (
            <>
              <p className="workflow-proposal-state">
                {v.proposal_bound
                  ? "方案示意 / 未仿真"
                  : "旧提案 · 未绑定当前需求，不能作为本次审批依据"}
                {p.user_confirmed_to_simulate ? " · 已授权本版方案" : ""}
              </p>
              <ProposalStructure
                proposal={p}
                proposalBound={v.proposal_bound}
              />
              <h3>目标规格与来源</h3>
              <dl className="workflow-spec-list">
                {Object.entries(p.target_specs || {}).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>
                      {String(value)}
                      <span>
                        {p.requirements_snapshot?.sources[key] === "recommended"
                          ? "采用推荐值"
                          : p.requirements_snapshot?.sources[key] === "user"
                            ? "用户填写"
                            : p.requirements_snapshot
                              ? "需求描述提取"
                              : "旧提案，来源未绑定"}
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
              {[
                ["初始结构与来源", p.initial_structure_source],
                ["拓扑", p.topology],
                ["光学推演", p.optical_theory_analysis],
                ["玻璃选型", p.glass_selection_rationale],
                ["机械预算", p.mechanical_constraints],
                ["内部空气间隔", p.internal_air_spacing_budget],
                ["优化策略", p.merit_function_strategy],
                ["风险", p.risks],
              ].map(([title, body]) => (
                <section className="workflow-proposal-section" key={title}>
                  <h3>{title}</h3>
                  <p>{body || "未提供"}</p>
                </section>
              ))}
              <div className="workflow-actions">
                <Button
                  disabled={busy || !v.proposal_bound || dirty}
                  onClick={() => {
                    setMessage("");
                    setReview(v);
                    setModal("revision");
                  }}
                >
                  请求修改
                </Button>
                <Button
                  className="workflow-primary"
                  disabled={
                    busy ||
                    !v.proposal_bound ||
                    dirty ||
                    p.user_confirmed_to_simulate ||
                    v.stage === "changes_requested"
                  }
                  onClick={() => {
                    setReview(v);
                    setModal("confirm");
                  }}
                >
                  确认本版方案并授权建模 / 仿真
                </Button>
              </div>
            </>
          )}
        </section>
      )}
      {tab === "tasks" && (
        <section className="workflow-card">
          <div className="workflow-card-title">
            <h2>Agent 交接</h2>
            <Button
              className="studio-icon-button"
              aria-label="刷新任务"
              onClick={flow.refresh}
            >
              <RefreshCw size={17} />
            </Button>
          </div>
          <p>外部 Agent 需显式领取；此页面不会自动唤醒聊天或执行优化。</p>
          {tasks.status !== "ready" && <p role="status">{tasks.message}</p>}
          {tasks.status === "ready" && (
            <TaskListFilters
              key={JSON.stringify([v.service_instance_id, v.project_name])}
              pane={tasks}
            >
              {(t) => (
              <article key={t.request_id} className="workflow-task" data-task-id={t.request_id}>
                <div>
                  <h3>{intentNames[t.kind]}</h3>
                  <span>
                    {t.status === "superseded"
                      ? taskNames.superseded
                      : t.stale
                        ? "引用已过期"
                        : t.lease_expired
                          ? "租约已过期"
                          : taskNames[t.status]}
                  </span>
                </div>
                <p>{t.user_text}</p>
                {t.worker_id && (
                  <p>
                    领取者：{t.worker_id} ·{" "}
                    {new Date(t.lease_until * 1000).toLocaleString()}
                  </p>
                )}
                <TaskProvenance task={t} sync={sync} project={v.project_name}/>
                <div className="workflow-actions">
                  {t.status !== "superseded" && (
                    <Button
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(
                            taskInstruction(t, v.project_name),
                          );
                          setCopy(t.request_id);
                        } catch {
                          setLocalError("剪贴板不可用，请复制下方任务ID");
                        }
                      }}
                    >
                      <Copy size={15} />
                      {copy === t.request_id ? "已复制指令" : "复制任务指令"}
                    </Button>
                  )}
                  {!t.stale && (t.status === "failed" || t.lease_expired) && (
                    <Button
                      disabled={busy}
                      onClick={() =>
                        perform("retry", {
                          request_id: t.request_id,
                          version: t.version,
                        })
                      }
                    >
                      显式重试
                    </Button>
                  )}
                </div>
                <details>
                  <summary>任务身份与接入</summary>
                  <p className="workflow-id">{t.request_id}</p>
                  <p>
                    需求 v{t.requirements_revision} · 任务 v{t.version}
                  </p>
                  <p>
                    在 Agent 的 MCP 中使用同一仓库 server.py，并设置
                    OPTIFORGE_SERVICE_URL 为此服务、OPTIFORGE_SERVICE_FILE
                    为对应凭据文件。复制指令不代表已送达。
                  </p>
                </details>
              </article>
              )}
            </TaskListFilters>
          )}
          <TerminalTaskHistory flow={flow} sync={sync}/>
        </section>
      )}
      {v.model_reference?.model_project === v.project_name && (
        <Button
          className="workflow-question"
          disabled={dirty}
          onClick={openQuestion}
        >
          <MessageSquare size={16} />向 Agent 提问 ·{" "}
          {questionContext?.drafts.length ? `${questionContext.drafts.length} 处上下文` : `S${selected}`}
        </Button>
      )}
      {modal === "project" && (
        <Modal
          className="workflow-dialog"
          title="选择工程"
          onClose={() => setModal(null)}
        >
          <p>切换会改变活动工程；当前模型不自动重载。请先保存未保存的内容。</p>
          {issue && <p role="alert">{issue}</p>}
          <div className="workflow-picker-field">
            <span>已有工程</span>
            <SurfacePicker
              label="已有工程"
              value={create ? "" : project}
              options={[{ value: "", label: "请选择" }, ...v.projects.map(p => ({ value: p.project_name, label: p.project_name }))]}
              onChange={(value) => {
                if (value === (create ? "" : project)) return;
                setProject(value);
                setCreate(false);
              }}
            />
          </div>
          <label>
            新工程名称
            <input
              aria-label="新工程名称"
              value={create ? project : ""}
              onChange={(e) => {
                setProject(e.target.value);
                setCreate(true);
              }}
              placeholder="如 imaging_project"
            />
          </label>
          {dirty && <p>请先保存需求或关闭窗口继续编辑，再切换工程。</p>}
          <Button
            className="workflow-primary"
            disabled={!project || busy || dirty}
            onClick={async () => {
              if (await perform("select", { create }, project)) setModal(null);
            }}
          >
            <Plus size={16} />
            {create ? "创建并切换工程" : "切换活动工程"}
          </Button>
        </Modal>
      )}
      {(modal === "revision" || modal === "question") && (
        <Modal
          className="workflow-dialog"
          title={
            modal === "question"
              ? `向 Agent 提问 · ${review.anchors?.length ? anchorsLabel(review.anchors.map((d) => d.anchor)) : `S${review.selected}`}`
              : "请求修改方案"
          }
          onClose={() => setModal(null)}
        >
          <p>
            {modal === "question"
              ? review.anchors?.length
                ? `任务绑定当前模型及修订，以下 ${review.anchors.length} 处上下文随问题一起发送；Agent 仅分析解释或建议，不自动修改参数。`
                : "任务绑定当前模型及修订，仅分析解释或建议，不自动修改参数。"
              : "提交后撤销本版审批，等待 Agent 返回新提案。"}
          </p>
          {modal === "question" && review.anchors?.length > 0 && (
            <ol className="question-dialog-anchors" aria-label="随问题发送的上下文">
              {review.anchors.map((d) => (
                <li key={d.id}>
                  {anchorLabel(d.anchor)}
                  {d.comment.trim() && <p>{d.comment.trim()}</p>}
                </li>
              ))}
            </ol>
          )}
          {issue && <p role="alert">{issue}</p>}
          <textarea
            aria-label="任务内容"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <Button
            className="workflow-primary"
            disabled={busy || !message.trim()}
            onClick={() =>
              submit(
                modal === "question" ? "surface_question" : "proposal_revision",
              )
            }
          >
            创建待领取任务
          </Button>
        </Modal>
      )}
      {modal === "confirm" && (
        <Modal
          className="workflow-dialog"
          title="确认本版设计方案"
          onClose={() => setModal(null)}
        >
          <p>
            确认工程 {review.project_name} 的提案 v
            {review.proposal?.proposal_revision}、需求 v{review.draft.revision}
            ，授权后续建模和仿真，并创建待 Agent
            领取的交接任务。此操作不会立即运行优化。
          </p>
          {issue && <p role="alert">{issue}</p>}
          <Button
            className="workflow-primary"
            disabled={busy}
            onClick={async () => {
              const next = await perform(
                "confirm",
                { request_id: requestId },
                review.project_name,
                review,
              );
              if (next) {
                setModal(null);
                setTab("tasks");
                setRequestId(newId());
              }
            }}
          >
            <Check size={17} />
            确认并创建交接任务
          </Button>
        </Modal>
      )}
    </motion.main>
  );
}
