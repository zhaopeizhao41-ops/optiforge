# 阶段一 · 第二批：Agent 自动同步与断连恢复

日期：2026-10-28（Asia/Shanghai）。状态：S1–S5 软件实现、自动化测试与真实只读复核完成，交规划聊天复审与用户交互验收。**原生写入同步仅经模拟验证，尚未完成真实写入验收。**

## 版本与范围

- 实现工作区：`C:\Users\Kernel\.codex\worktrees\optiforge-visual-workbench\mcp gemini zemax`。
- 分支：`codex/visual-workbench-stage-one`；本批起点：`4cb2ae41213c747dd725444966e6024e10da6b6a`。
- 唯一执行计划：`D:\mcp gemini zemax\plan.md`，由规划聊天维护，本聊天没有修改它。最后读取的 SHA-256 为 `88C380A3872B8AADD43B9096BA8B2445E2CB7EEB7717690C79B029C1A66FA6EE`。
- 稳定原工作区仍在 `main` 的 `69bdb51`；本批仅本地提交，不 push、不部署、不创建远程 PR。将来上传仍用 SSH。

自然语言对话继续在外部 Agent。UI 仍以二维轮廓和表面属性为主，自动显示经同一服务执行的操作结果、任务状态与断连提示。未接入真实光线、分析结果面板、UI 参数编辑、聊天入口或独立光学内核，也未进入第三批。

## 实现与契约

`core/workbench_effects.py` 显式登记所有 `tools.__all__` 入口，区分读取、模型替换、模型修改、工程上下文修改与可能临时改变模型的计算。CAD 导出可能切换活动项目，也执行上下文保护。覆盖测试阻止新增工具漏登记。

`core/workbench_engine.py` 统一接入 MCP、启动加载、UI 导入及刷新。任务登记和入队顺序一致；唯一引擎线程在真正执行前校验目标，执行原有工具及其装饰器，再在同一队列项内观察元数据、读取快照、一次发布状态，最后发布完成事件。显示读取失败不会把已成功的原生操作改报为失败，也不会重放写入。

`core/workbench_state.py` 只持有内存数据，不导入 ZOS。默认保留 256 条事件、50 条完成任务、最多 32 条未完成任务；队满明确返回 `WORKBENCH_BUSY`。状态及事件游标在同一锁下复制，长轮询等待条件变量，心跳和缓存查询不访问原生引擎。

身份和计数分别表达：

- `service_instance_id`：每次服务启动的新 UUID。
- `model_id`：成功 New/Load/Template 或观察到不同原生会话后更新；同路径重载也更新，保存/另存为保留。
- `model_revision`：持久修改及保守计算失效计数；特殊参数、求解、MCE 等不可见改动也推进。刷新观察到可见光学内容变化同样推进。
- `state_version`：已发布工程状态版本，包含身份、项目、提案、有效性和快照变化；排队、开始及无变化读取不推进。
- `event_seq`：任务及状态通知序号，独立于光学版本。
- 快照 v1 的 `revision`：继续保留，仍只是已读取内容的哈希，不代表全部原生状态。

快照 `sync`、外层 `snapshot_context` 与发布版本一致。`active_project` 和 `model_project` 分别表达；模型的名称、路径和归属变化不被误作可见光学内容变化。

### 目标保护与错误恢复

受保护请求携带五字段 `expected_context`：`service_instance_id`、`state_version`、`model_id`、`active_project`、`model_project`。桥接首次只读握手，之后保留本客户端真正观察过的上下文；显式模型/项目读取或本客户端成功修改后才采用新上下文。查询手册不会隐式确认其他 Agent 切换后的目标。迟到响应不能回退已观察版本。

引擎线程拒绝缺失或过时目标，分别返回 `WORKBENCH_CONTEXT_REQUIRED` / `WORKBENCH_CONTEXT_STALE`，且不调用写入函数。错误结果不自动采用新目标、重试或重放。原提案确认和模型项目门禁继续执行。

元数据读取失败时发布 `context_valid=false`；旧项目字段仅供诊断，后续针对当前模型的修改、计算、保存和工程上下文写入被 `WORKBENCH_STATE_UNCERTAIN` 阻止。显式读取及 New/Load/Template 恢复路径保留。成功重读真实身份后版本推进，旧目标仍须重新观察。纯视图读取失败与身份不可确认分开处理。

模型恢复风险独立持久记录：原生部分写入与元数据同时失败时，之后的元数据读取恢复不能消除模型的 `uncertain`；后续操作回滚也只能恢复到该次操作的开始前，不能消除历史不确定性。成功替换模型或可证明不同的新会话才能解除。首次观察到某会话不视为证明它干净。明确写入前拒绝（含 `QUALITY_GATE_FAILURE`）不使已知模型失效。相关方案问题和规划决定见 [问题记录](WORKBENCH_SYNC_DESIGN_QUESTIONS.md)。

### HTTP 与前端

内部协议升级到 `protocol_version=2`，光学快照仍为 `schema_version=1`。旧内部协议明确返回 409 `WORKBENCH_PROTOCOL_INCOMPATIBLE`；需一起重启新版服务和桥接。MCP 原工具名称、参数与业务结果保留。

- `GET /api/session`：握手、token、实例及协议版本。
- `GET /api/state`：读取缓存状态、快照、任务和原子游标。
- `GET /api/events`：最多等待 20 秒；事件过旧、游标越界或实例不同则要求全量恢复。
- `POST /api/refresh`：合并刷新，立即返回 accepted 和任务 ID。
- `POST /api/tool`：内部返回 `{protocol_version, operation_id, result, context}`；MCP 只取业务 result。
- `POST /api/model/import`：沿用文件隔离和校验，增加上下文保护，返回该导入自身操作边界的状态。中文上下文使用 URI 编码 JSON 请求头。

Host/Origin/token、静态路径和导入文件检查保留。凭据文件原子替换，不把 token、工具完整参数、镜头内容或大分析数组写进事件。

前端同步控制器、React hook 与 API 校验分别位于 `frontend/src/workbench-sync.js`、`use-workbench-sync.js` 和 `workbench-api.js`。页面只有一个有效订阅循环；按实例、状态版本和事件序号采用响应，拒绝过时数据。同一模型更新保留有效表面选择与缩放，模型身份变化则重置。页脚显示同步、任务名称与秒数；断连保留上次快照并明确过时，恢复后全量读取。重连按 1/2/4/8 秒退避、最高约 10 秒并加少量抖动；403 只重试读取握手。导入网络异常显示“结果未知，请刷新核对”，绝不自动重放。卸载、页面恢复和重启会清理等待及旧循环；握手阶段触发恢复也不会让循环提前退出。

## 运行与复现

在实现工作区运行，首次使用先在 `frontend` 执行 `npm ci`：

```powershell
.\start-workbench.ps1 -Model '.\output\workbench_acceptance\source\achromat_doublet_optimized.zmx'
```

访问 <http://127.0.0.1:8765/>。已有最新构建可加 `-SkipBuild`。验收副本是本机文件，不随 Git 分发；也可不传 `-Model`，在页面选择自己的已有模型。停止时在服务终端按 Ctrl+C，安全等待该服务持有的原生任务和会话结束。

外部 Agent 仍以本工作区 `server.py` 为 stdio 入口，环境变量 `OPTIFORGE_SERVICE_URL=http://127.0.0.1:8765`；凭据默认读取新版工作区 `output/workbench_service/service.json`，不需手填 token。该变量只能设置在 MCP 子进程，不能设置在服务进程。详细配置和回退步骤沿用 [运行说明](WORKBENCH_READONLY_ACCEPTANCE.md)。实际验证过 stdio MCP 协议，未逐个验收所有 Agent 产品。

模拟浏览器验收使用单独的纯几何夹具，**不创建 ZOS 会话**：

```powershell
python -B tests/workbench_fake_service.py --port 8766
```

打开 <http://127.0.0.1:8766/>，另一个终端运行真实 stdio MCP 客户端脚本：

```powershell
python -B tests/exercise_workbench_sync_mcp.py --radius 24
python -B tests/exercise_workbench_sync_mcp.py --radius 25 --radius 26 --radius 27
python -B tests/exercise_workbench_sync_mcp.py --radius 30 --comment fixture-slow
python -B tests/exercise_workbench_sync_mcp.py --radius 31 --comment fixture-fail
```

夹具页面及快照明确标为模拟引擎；客户端写入前强制检查引擎为 fixture，凭据使用单独的 `output/workbench_sync_acceptance/fixtures/service.json`。`fixture-slow` 仅延迟 2 秒，`fixture-fail` 仅返回模拟回滚错误。切模型可用 `--load-path`，夹具不解析其光学内容；UI 导入用的模拟 `.zmx` 不能拿到真实服务加载。停止/重启夹具可复现断连保留与新实例恢复。

## 自动化测试结果

最终代码于 2026-10-28 运行：

```powershell
python -B tests/test_workbench.py              # 31 通过
python -B tests/test_workbench_sync.py         # 24 通过
python -B tests/test_workbench_http_sync.py    # 12 通过
python -B tests/regression_stage_two.py       # 33 通过
python -B tests/regression_stage_three.py     # 33 通过
```

Python 合计 **133 项通过**，均为离线/假引擎测试。前端在 `frontend` 运行 `npm test`，**17 项通过**；共 **150 项**。规划聊天已独立复核此前的 149 项，本地随后补充一条握手中断恢复回归并通过；不代替对最终提交的复审。

`npm run build` 通过，未新增运行依赖；生产资源为 `index-CNKkbB1x.js` / `index-Dv2o-L0g.css`。主要覆盖任务/版本/身份、无原生读取的心跳、工具影响完整性、不可见改动失效、同路径重载、Save As、两客户端及排队目标冲突、长任务期间响应、事件丢失/游标竞态、重启 token、中文导入、历史恢复风险、原门禁与 R1/R2。前端覆盖乱序、重启、退避、卸载、单订阅、导入不重放及握手恢复。`git diff --check` 在提交前执行。

## 浏览器端到端证据（模拟写入）

通过实际启动 `server.py` 的 stdio MCP 完成，下列修改不访问真实模型：

- 半径 20→24 自动显示，两个页面均收到；连续 25→26→27 最终版本一致，同模型的 125% 缩放保持。
- 选择 S2 后切换模型，选择重置 S1、缩放重置 100%；浏览器重新加载恢复当前模型。
- 通过页面文件选择器完成中文名称导入；失败操作提示“模拟操作失败，已回滚”，保留原值。
- 慢操作期间页脚实际显示“正在更新 · 修改表面 · 0 s”；深浅主题可用。
- 停止夹具后保留上次 32 mm 的值并标断连；重启的新 token/实例被自动采用，不刷新页面也可恢复到新夹具 20 mm，显示“已同步”。

操作完成时间取任务 `finished_at`，UI 时间取 `useLayoutEffect` 后 DOM `data-rendered-at`，均使用本机时钟：

1. 完成 `2026-10-28T03:10:13.713605+00:00`，DOM 时间戳 `1793157013728`，差 **14.395 ms**，状态版本 2。
2. 连续修改末次完成 `2026-10-28T03:10:53.284577+00:00`，DOM 时间戳 `1793157053297`，差 **12.423 ms**，状态版本 5。

两次均在计划的 1 秒目标内；这是模拟引擎的小样本交付延迟，不包含光学计算时间，也不是统计性能保证。这些交互在最后的恢复风险及握手边界补修前完成，补修已由对应行为测试覆盖；最新构建另做真实只读浏览器复核，不声称所有模拟交互在最终补修后全量重跑。

本机证据保存在被 Git 忽略的 `output/workbench_sync_acceptance/reports/`：`browser-acceptance.json`、`browser-failure-dark.png`、`browser-disconnected.png`、`browser-native-readonly.png` 和 stdio 客户端日志。`fixture-mcp-result.json` 每次运行覆盖；汇总文件保留上述两次计时记录。

## 真实 Zemax 只读复核

用最新代码重启 8765 服务，读取已有验收副本后执行：

```powershell
python -B tests/verify_workbench_native.py --source 'D:\mcp gemini zemax\output\achromat_doublet_optimized.zmx'
```

于 `2026-10-28T03:26:42.876617+00:00` **13 项通过**，单独计数，不并入前述 150 项。模型 `achromat_doublet_optimized`、6 面、mm，修订哈希 `bb81b0b35d0bdc7b`。最新服务实例 `18ae362a-72c7-4944-8a99-77add49ea64d`，模型身份 `8e8fa20b-b5d5-457d-b557-513bf4e6aff2`，状态版本和模型版本均为 1。

检查包括原生处方、视场、波长和设置相等，缓存/快照版本一致，重复读取和心跳不推进版本，二维轮廓符合 sag 与面间距离，真实 stdio MCP 与 UI 共用模型，提案未授权，原文件和副本字节不变。报告：`output/workbench_acceptance/reports/native-acceptance.json`；两份文件的 SHA-256 仍为 `074036cc30e712bf748c3036cb0ef2dd261d4fa2ef58a923f8f13428f7730dd6`。

最终前端构建的浏览器只读复核显示同一实例/模型、`synced`、`stale=false`；选择 S2、放大到 125%、手动刷新后均保持，属性为半径 -25.82505 mm、厚度 25.000106 mm、N-SF11。截图已保存。未重测 320px；第一批历史窄屏测试不能当作本批全量证据。

## 未验证项与验收边界

- **真实原生写入自动同步尚未验证**。本次没有具体光学验收提案的仿真确认，未调用真实表面修改、优化、追迹或像质分析。后续在独立副本上按 AGENTS.md 完成相应提案确认后，才能补一项“外部 MCP 修改一个参数→UI 自动更新”的原生证据；软件开发授权不替代该确认。
- 自动同步仅覆盖经本服务执行的操作。另一独立进程、原 standalone MCP 或其他 Zemax 实例的变化不自动成为本服务事件；不监控外部原生编辑器。
- 仍只有一个活动模型；多客户端共享同一上下文，无多独立设计会话。分析工具保留调用能力，仅呈现任务状态。
- 轮廓支持范围、文件副本隔离和原光学门禁沿用第一批；没有把旧快照或模拟数据当成真实光线/像质结果。
- 服务重启后事件/任务历史不持久化，依靠新实例和全量恢复。浏览器中止等待不代表取消原生任务；写入超时需显式读取核对。

当前没有等待规划决定的新方案问题；D1 和历史不确定性反馈已落实。下一步是规划聊天复审最终本地提交、用户交互验收和原生写入缺口安排，未提前进入第三批。
