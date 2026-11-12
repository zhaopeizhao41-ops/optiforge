# OptiForge 本地可视化工作台

自然语言对话继续在外部 Agent 中进行。界面显示当前本地工作台服务中的模型、光路、表面属性和像质分析。

```powershell
cd frontend
npm ci
npm run build
cd ..
python -B workbench.py
```

默认入口为 http://127.0.0.1:8765/ 。也可通过 `--port` 选择端口、通过 `--model <已有文件>` 载入模型。`start-workbench.ps1` 可构建并启动。开发入口 `npm run dev` 将 `/api` 代理到 8765；`npm run preview` 仅提供静态页面。

## 独立应用窗口（可选）

`start-workbench.ps1` 默认行为不变：构建后在当前控制台前台运行 `workbench.py`，`-Model`、`-Port`、`-SkipBuild` 与旧版一致；默认前台不会复用或覆盖在线服务的凭据。

加 `-App` 时使用 Chrome/Edge 的 `--app` 窗口（仍由现有本地服务提供页面，不引入 Electron 或新后端）：

- `-Browser Auto|Edge|Chrome` 仅与 `-App` 搭配，启动前先检查浏览器；找不到时直接失败，不会启动服务，也不会静默改用默认浏览器。
- 自动复用本工作树中端口、协议、token、实例身份与能力都匹配的在线服务；在线但不匹配、凭据无法验证或无法读取 `service.json` 时拒绝并保留原文件。
- 没有可复用实例时，以隐藏后台进程启动服务（日志在 `output/workbench_service/`；`-Port 0` 自动选择空闲端口），新凭据核对就绪后才替换正式 `service.json`。
- 关闭窗口不会停止服务。输出会给出 URL、PID、实例 ID；先核对 PID，再 `Stop-Process -Id <PID>`。
- 浏览器使用专用持久 profile `output/desktop_profile/<chrome|edge>`，与日常浏览器的登录、扩展和存储隔离；同 origin 的 localStorage 只存在该 profile 内。
- 不保证单实例窗口（重复 `-App` 会复用服务并再开一个窗口），不禁用 F5，也不置顶。
- `-WorkflowOnly` 透传 `--workflow-only`，与 `-Model` 互斥。`--service-file` 仅供启动器写入临时凭据，正常使用无需直接传入。

从仓库根目录运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\get-workbench-window.ps1`（或 `pwsh -NoProfile -File .\get-workbench-window.ps1`）可输出只读窗口诊断 JSON。示例的执行策略只作用于该次受信脚本进程，不修改系统策略。`-WorkspacePath <工作树绝对路径>` 默认是脚本目录，`-Browser Auto|Chrome|Edge` 限定浏览器；诊断的 `Auto` 汇总已安装的 Chrome 和 Edge，启动器的 `Auto` 则选择其中一个。诊断复用启动器核对在线 loopback 服务的身份，不启动服务或浏览器、不覆盖凭据，不改变窗口、进程或光学模型。

`unique_candidate` 只表示两次观察中恰好一个可见顶层窗口属于该工作树的专用浏览器 profile（含最小化窗口）；`ambiguous` 表示多个候选且不选定目标，`not_found` 表示完整且稳定的观察中没有匹配，服务未核实、相关元数据缺失或观察发生变化时为 `unavailable`。候选按精确可执行文件、原生解析的 `--user-data-dir`、窗口/进程关联与创建时间核对，标题不参与判断。输出保留句柄字符串、窗口类、最小化状态、原始窗口矩形和可取得的 DPI；矩形是 `GetWindowRect` 的 DPI 虚拟化屏幕坐标，DPI 缺失为 `null`，均不表示已完成多 DPI 验收。

同一个 profile 可以有多个页面或来源，所有结果始终为 `url_binding: "unverified"`；即使只有一个候选，也没有证明它显示工作台 URL、是 App 窗口或可安全执行后续动作。诊断只描述读取时刻，不提供后续句柄仍有效的保证，不输出 token、凭据、标题或完整进程命令行。Windows 平台接口依据 Microsoft Learn 的 [EnumWindows](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumwindows)、[窗口进程](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid)、[CommandLineToArgvW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-commandlinetoargvw)、[LocalFree](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-localfree)、[Win32_Process](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-process)、[窗口矩形](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect)及 [DPI](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdpiforwindow)原文核对（2026-11-03；`GetDpiForWindow` 最低 Windows 10 1607，缺少接口时保留未知）。

Agent 可调用独立 MCP 工具 `optiforge_workbench_desktop_diagnostics(service_instance_id="当前服务实例", browser="Auto")` 读取上述候选；`browser` 仅接受 `Auto`、`Chrome`、`Edge`，不接受工作树、脚本或可执行文件路径。目标工作树只从已配置且可核实的绝对 `OPTIFORGE_SERVICE_FILE`（标准后缀 `output/workbench_service/service.json`）推导；不支持别名、重解析路径及 `localhost` 端点，需现有规范 `http://127.0.0.1:<端口>` 配置。实际执行的诊断脚本和启动器来自受信 MCP 代码树，服务凭据及 HTTP 身份在采集前后核对；配置、内容或实例变化时不返回候选。

此工具保持四态，并固定返回 `url_binding: "unverified"`、`action_supported: false`；不输出 profile/凭据/可执行文件路径、窗口标题、类名、完整命令行或原始服务错误。既有 `optiforge_workbench_status` 响应不变。Windows 采集使用系统目录 API 定位固定 PowerShell、独立隐藏子进程及固定参数；CLI 执行入口明确输出无 BOM 的 UTF-8 JSON，dot-source 入口不改变调用方编码。已创建的采集进程最多读取 20 秒，stdout/stderr 合计超过 256 KiB 时终止该子进程，进程创建阶段不承诺可中断。缺少平台、凭据或采集能力时明确 `unavailable`；软件测试或模拟候选不等于真实桌面采集、URL/HWND 绑定或原生窗口操作验收。

## 模型与光路

“打开模型”读取 `.zmx/.zos` 的本地工作副本，不覆盖原文件。主页面展示真实二维轮廓和表面属性，支持滚轮缩放、拖动和一键复位。光线来自当前原生模型的实光线追迹；不支持的面型或状态会给出原因。

Agent 经同一服务修改模型后，页面按模型修订更新。其他 Zemax 窗口或磁盘文件的更改需要重新载入。导入副本不会继承原工程的提案确认；原生追迹和分析均要求当前工程与模型匹配、提案已确认。

## 像质分析

在“分析”页设置项目后点击“运行分析”，提交一个异步套件。支持完整 FFT MTF、主波长 OPD 热图、原生像面点列、光线像差、场曲和畸变。切页、主题、图例和图表缩放只影响显示；模型变化后旧结果标记为历史，需要显式重新运行。服务重启或切换模型后不展示另一模型的结果。

默认最多选择五个实际视场，按视场幅值覆盖首、中、末位置；MTF 默认 256×256、100 lp/mm，OPD 默认 128×128。设置表示计算采样，不是镜头像质目标。结果同时记录实际覆盖、波长、配置、来源和有效设置。

- 波前用未舍入的 RMS ≤ 1/14 waves 和估算 Strehl ≥ 0.8 判定；原生统计无法核实时不能判通过，部分视场不能代表全视场通过。Strehl 使用 Maréchal 估算，大像差时仅供参考。
- MTF 的 30 lp/mm 数值只取精确频点或包围点线性插值。未提供 MTF / 畸变阈值时为 INFO。
- 点列采用同一次原生 hexapolar 批追迹，参考主波长主光线，统计按实际波长权重、仅剩余有效光线计算；与 Standard Spot 的其他采样或参考不可混用。点列半径只作诊断。
- OPD 孔径外使用原生 NaN 掩码，有效零值保留。热图和读点按原生行列索引显示；Min/Dx 元数据尚不作为经标定的入口/出口光瞳坐标。
- 场曲/畸变采用原生 +Y 连续扫描，来源中标明其覆盖，不冒充离散视场子集。

服务缓存最多八套、总计 64 MiB，单套最多 8 MiB。单项默认 120 秒、整套 600 秒；若无法确认原生任务停止或关闭，则阻止后续原生操作，保留状态读取，不自动杀进程或重建会话。

## 工程协作

“工程”页保存需求、运行共用规格审查、审阅 Agent 登记的真实提案，并查看持久交接任务。无模型时默认进入此页；已有模型仍使用“光路”和“分析”页。推荐值须点击采用后才会写入，需求编辑会使旧审查失效；保存的草稿可在刷新后恢复。字段填写或删除补填的顺序不影响保存状态；保存期间继续输入的内容会保留，需再次保存。多页面旧版本保存会被拒绝，保留本页编辑供手动合并。

编辑未保存的需求时，浏览器关闭、刷新或离开当前页面会触发标准 `beforeunload` 原生提醒；页内切换“光路”/“分析”不会被阻止，也不会自动保存或创建任务。保存成功且编辑内容回到已保存状态后会解除提醒；保存进行中、保存失败、冲突或还原前仍保持保护；组件卸载时移除监听。浏览器可自行忽略该提示，因此这只是尽力提醒，不能承诺绝对防丢；需要保留的内容请先保存。

另一页面或 Agent 切换活动工程时，本页未保存的草稿会保留，并显示工程切换恢复面板。可返回原工程继续编辑，或明确放弃草稿并读取新工程；处理前不显示新工程的需求、方案或任务操作。仅当返回后的原工程保存内容、需求版本、提案、授权和服务身份均未变化时，才能继续保存；发生真实远程变化仍按版本冲突处理，保留草稿供手动合并。断连或服务重连期间保留本页草稿和离开提醒，不自动保存或重放操作；关闭或刷新页面仍可能丢失未保存内容。

无需原生软件的机器可在仓库根目录运行 `python -B workbench.py --workflow-only --port 8765`。该模式不提供模型导入、原生刷新、追迹或优化。正常模式使用 `python -B workbench.py`，仅显式模型操作才建立原生会话。

对话继续在外部 Agent。将它的 MCP 配置指向本仓库 `server.py`，并设置 `OPTIFORGE_SERVICE_URL=http://127.0.0.1:8765`、`OPTIFORGE_SERVICE_FILE` 为此仓库 `output/workbench_service/service.json` 的绝对路径。凭据留在本机。隔离服务须使用其自己生成的匹配凭据文件，不能混用。首轮接入需显式领取或由宿主轮询，不提供所有宿主自动唤醒。

工具交接顺序：

1. `zemax_design_workflow(action="view")` 读取当前工程与精确 `context`；这是上下文过期后的恢复入口。
2. `zemax_list_design_tasks` 找到待领取任务，再以任务 ID、版本和领取者调用 `zemax_claim_design_task`。保管返回的 `lease_token`；领取不授权光学仿真。
3. 按冻结的需求版本检索、推演。用领取令牌和任务版本调用 `zemax_update_design_task(status="progress")`，并使用返回的新任务版本。进度更新不自动续租。
4. `zemax_register_design_proposal` 传入当前 `workflow_context`、`task_claim={request_id,version,lease_token}`、与审查一致的 `target_specs` 和完整方案内容（含 `topology`、`risks`）。登记后使用返回的 `proposal_hash` 完成研究任务。
5. 用户在界面明确确认具体需求及提案版本后，共用 JSON 授权源才放行，并创建待领取的建模交接任务。当前版本不内置自动优化执行器。修改需求或重新登记方案撤销旧审批；领取者执行前须重读当前授权及目标身份。

界面“复制任务指令”会按任务类型（研究、建模交接、表面提问）生成上述顺序，携带工程、任务 ID 和页面版本，并要求核对当前 target。复制只写入剪贴板，不等于送达、领取或授权；领取时请按任务预算显式选择 `lease_seconds`（30–3600 秒）。

过期租约或失败任务需由用户显式重试，令牌会更换；引用过期应重新提交任务，不能对新模型同索引表面继续操作。未知结果不会自动重放写入。针对表面的任务只用于解释或建议，绑定真实模型 ID、修订和配置，不自动修改参数。

需求、提案、授权或明确观察到的模型版本变更会将失效待办归档为历史，撤销旧令牌并释放队列名额；历史仍可查看，不能重新领取或用迟到结果恢复。每个工程最多保留 50 条有效待领取/已领取任务，失败任务重试也受此限制。暂时缺少模型观察不会永久归档任务。

像面规格须为正尺寸并带单位（如 `11 mm`、`11×8 mm` 或传感器格式 `2/3"`）；视场须明确正的全角或半角幅度（如 `全视场 25 deg`）。零/负尺寸、有符号方向、范围或全半角不明的表达会要求补充，不能直接创建研究任务。

`python -B tests/test_design_workflow.py` 运行隔离协作测试，`npm test` 包含草稿并发与迟到响应测试，均无需 OpticStudio。软件验收提案不代表用户真实项目获准仿真。

## 本机 MCP 页面联动

工作台页面通过只读 UI 会话向上层 Agent 暴露“当前实际页面”，并接受受限的切页命令。MCP 侧是独立的本机 HTTP 客户端，不经过光学工具注册表，也不需要 OpticStudio：

- `optiforge_workbench_status()`：返回协议、能力、上下文、在线前端（含实际 page）与命令回执；无浏览器时仍可读服务状态。不含 token 或路径。
- `optiforge_workbench_events(service_instance_id, after_seq, timeout_ms)`：复用 `/api/events` 读取既有任务/模型事件。工作流成功后，成功返回的公开任务会以 `kind="task_changed"`、嵌套 `design_task`（仅 `project_name/request_id/kind/status/version/stale/lease_expired`）提示变化；事件不能替代领取/授权检查，领取仍以当前版本、状态、租约和授权为准。事件必须由宿主主动读取/领取，工具不会自行唤醒任意 Agent。
- `optiforge_workbench_wait_for_task(service_instance_id, project_name, after_seq=0, timeout_ms=20000)`：宿主主动调用的只读等待入口，读取指定活动工程的既有任务队列。返回 `status="ready"` 或 `"idle"`、`tasks`（最多 50 项上述七字段摘要）、精确的工作流 `context`、`next_seq` 与 `reset_required`。仅 `pending` 且 `stale=false`、`lease_expired=false` 的合法快照可成为候选；当前已有待办会立即返回，无待办时等待通知，超时和事件截断后重新读取权威状态补漏。游标与事件均不表示已领取、已送达或保证不重复；候选可在返回前被另一宿主领取，必须继续用既有 list/read 重新核对并按精确版本原子 claim。
- `task_changed` 可能是变化提示，也可能是首次观察到的既有任务快照；收到后必须先检查 `status`、`stale`、`lease_expired`，历史 completed/stale 任务不是新的可领取任务。去重与重连是**有界内存**语义，不是 exactly-once：服务重启、事件历史截断或缓存淘汰后，宿主必须重新 list/read 当前任务状态恢复，事件不保证送达、不保证领取成功、不能当授权。
- `optiforge_workbench_navigate(service_instance_id, ui_session_id, page, request_id)`：只接受 `workflow` / `design` / `analysis`，返回 `accepted + command_id`，**不代表已切换**。用 `optiforge_workbench_status()` 的 `commands` 回执确认 `applied` / `rejected` / `expired`；`events` 只承载任务/模型事件，不含导航回执。

等待入口不会自动切换工程、领取、重试、确认方案或运行光学，也不会唤醒未调用它的宿主。必须提供明确的当前服务实例和活动工程；配置缺失、实例变化、活动工程切换或连接故障会明确报错，绝不退回本地或原生会话。模型交接候选仅作提示：执行前仍须重新读取提案绑定授权和模型身份。任务全文、模型引用及领取令牌通过原有 list/read/claim 流程获取，不在等待摘要中返回。

`after_seq` 必须为非负整数，`timeout_ms` 必须为 `0..20000` 整数；布尔、字符串及浮点数不接受。`next_seq` 可用于下一次等待，`reset_required=true` 表示本轮已重读权威队列。单次使用一个单调时钟截止时间，事件等待最多 20 秒；`waited_ms` 记录长轮询调用的累计实测耗时并封顶于请求等待预算，`elapsed_ms` 记录整轮耗时。状态与流程 HTTP 请求各有 5 秒网络超时，排队和这些普通请求开销可能使整轮超过事件等待预算；超时状态不自动重放写入。多个宿主等待使用同一既有队列，最终领取竞争仍由原子 claim 决定。

给运行中 Agent 宿主的最小操作顺序：先重连或重启 MCP，让宿主发现新工具；调用 `optiforge_workbench_status()` 读取实例与当前活动工程，再调用 `optiforge_workbench_wait_for_task(...)`。收到 `idle` 后可用返回的 `next_seq` 再等待；收到 `ready` 后先用 `zemax_design_workflow(action="view", project_name=...)` 和 `zemax_list_design_tasks(...)` 核对任务及精确上下文，再以当前任务版本调用 `zemax_claim_design_task`。后续 `zemax_update_design_task(status="progress")` 必须使用领取令牌和最新返回版本；它不自动续租。研究任务完成前须按既有流程登记真实提案，并携带返回的 `proposal_hash` 完成任务；租约过期或写入结果未知时不得自动重放或重试。此入口提供宿主主动等待能力，未配置调用它的宿主仍不会被自动唤醒。

使用前只在 **MCP 宿主为该 `server.py` 配置的子进程环境**中设置 `OPTIFORGE_SERVICE_URL` 与 `OPTIFORGE_SERVICE_FILE`，指向运行中的工作台和其凭据文件；不要在工作台启动 shell/系统环境中全局导出 `OPTIFORGE_SERVICE_URL`，否则 `workbench.py` 会拒绝启动。缺失时会明确报配置错误，不退回原生会话。每次调用都会核对响应中的 `service_instance_id` 与凭据一致。

最小 stdio MCP 配置示例（`command`/`args` 与现有 `python -B server.py` 启动方式一致；路径均为占位绝对路径，不含密钥）：

```json
{
  "mcpServers": {
    "optiforge": {
      "command": "python",
      "args": ["-B", "C:\\path\\to\\optiforge\\server.py"],
      "env": {
        "OPTIFORGE_SERVICE_URL": "http://127.0.0.1:8765",
        "OPTIFORGE_SERVICE_FILE": "C:\\path\\to\\optiforge\\output\\workbench_service\\service.json"
      }
    }
  }
}
```

升级本批接口时按以下顺序操作，避免旧进程/旧页面造成“工具存在但无回执”：

1. **先保存**页面中未保存的需求草稿（重启或刷新会丢失未保存编辑）。
2. 在同一更新后的 checkout 中构建前端：进入 `frontend` 目录执行 `npm run build`（依赖缺失时才 `npm ci`）。
3. 从同一 checkout 停止并重启工作台（`python -B workbench.py` 或 `start-workbench.ps1`），让 `/api/ui/*` 生效并重写 `service.json`。
4. 刷新或重新打开工作台页面，让新 hook 注册。
5. 最后重连/重启 MCP 宿主进程，使 `server.py` 重新导入并出现 `optiforge_workbench_*` 工具；再调用一次 `optiforge_workbench_status()` 验证协议、实例与在线 frontend。

仅重建前端不会让已运行的工作台进程获得新 API；仅重连 MCP 也不会让旧页面出现 hook。

页面 hook 在每次 mount 生成唯一会话 ID，约每秒 heartbeat 并上报实际页面；收到命令后先调用 `setPage`，等 React 提交后才 ACK `applied`（并报告 actual_page）。关闭/刷新页面会 abort 并停止会话，旧会话的命令不会作用到新会话；多个页面各自独立，不会广播误控。手动切页也会在下次 heartbeat 反映到 status。

分析页始终可打开并上报实际页面；是否可运行分析仍由服务的 analysis 能力决定。命令按 `ui_session_id` 定向，`request_id` 幂等（同 ID 改页面会冲突，已结算命令重复请求返回真实终态），每个会话最多一条待执行命令，命令 8 秒过期且过期后不会补执行；会话 10 秒无心跳即离线。服务端随响应给出 `expires_at_ms` 与 `remaining_ms`，页面用请求起点加 remaining 计算保守 deadline，过期/卸载/旧 epoch 的迟到响应不会 setPage 或 ACK。ACK 只在 React 提交后发送，并绑定当前会话/实例；`accepted` 必须由 status 的 `commands` 回执复核，服务端不会自动产生 applied 回执。

## 界面与开发

光路页“操作历史”可回看本次服务会话中成功模型操作后已观察到的几何快照。历史缓存最多 12 项、总计 32 MiB、单项 8 MiB；较早记录可能被淘汰，重启服务会清空。未能记录历史快照不改变原操作结果。它不表示四阶段优化已经完成，也不包含像质结果或实光线。

时间轴可选择单条快照，或选择同一工程、模型与配置的两条历史记录并排查看；每侧标注来源与修订，可独立缩放和读取表面属性。两侧分别适配视口，不能仅凭画面大小判断尺寸变化。历史视图只发读取请求，不恢复模型、不执行追迹、分析或参数修改。工程或服务实例变化、断连和条目淘汰时会隐藏旧光路；来源不匹配的迟到结果会被丢弃。

中性浅深双主题、按钮和页面动效、减少动态效果选项沿用主界面。分析为二级页面；主页面保留光路与表面属性。

`npm test` 运行无需 OpticStudio 的前端契约测试。后端离线回归可在仓库根目录运行 `python -B -m unittest discover -s tests -p 'test_workbench*.py'`。

分析页“分析历史”通过带服务实例和活动工程范围的只读目录回看缓存结果，可并列查看两条不同记录的原始 MTF 30 lp/mm、最差覆盖视场波前 RMS 和估算 Strehl、原判据及来源设置。它只读取当前服务已有缓存，不运行分析、不恢复模型，也不声明当前像质或优化阶段通过。每个结果 ID 只有一条目录记录；缓存命中复用原记录身份和时间，不能作为两次独立计算对比。

历史分析最多缓存 8 项，单项 8 MiB、总计 64 MiB，淘汰或重启后无法回看。不同模型/配置、采样/阈值、物理视场、波长权重、单位、有效设置或统计口径，及缺失比较元数据/部分未完成结果，只能单条查看；选择这些组合时隐藏双栏数值并说明原因。原生统计核验未知或未通过、INFO/ERROR、估算 Strehl 与局部视场覆盖均保留原说明。页面不计算差值、改善率或新的验收判据。

弹窗仅在打开时读取目录及所选详情；目录修订改变或显式刷新才重新读取目录，不使用任务摘要猜测缓存目录。工程或服务切换、网络断连和条目淘汰时清除旧数据，迟到响应不能覆盖新选择。同步层以独立的传输连接状态区分 HTTP 断连和原生观察或任务错误，错误文字相同也不会混淆。模型暂时没有原生观察、当前修订变化、较近任务失败或提案撤权不自动授权新分析，也不阻止读取当前工程已有历史；缺少历史目录能力的旧服务明确显示不可用。

工程页“优化阶段”只读呈现基础曲率、厚度与边界、波前与材料、深度寻优与公差四阶段的显式执行记录。记录随现有流程读取，可展开每次尝试的开始/报告来源、关联操作及较早记录。未开始、记录中、待核对、失败、来源过期分别显示；“记录中”只表示 Agent 已显式开始记录，不证明原生优化器正在运行。成功操作、已完成交接任务或历史光路都不会自动成为阶段记录，也不代表像质或公差验收通过。

阶段记录按工程持久保存的是元数据；旧服务记录只供历史核对，不保留或恢复原生几何与执行。每任务最多 16 次尝试，每次报告最多 12 个操作，页面最多显示最近 32 次尝试；记录额满时由服务拒绝新增，不静默删除运行中记录。切换服务或工程、连接中断、读取失败时立即隐藏旧面板；当前模型/配置/最终修订与来源不一致会标示来源过期。普通流程读取和观察状态版本增长不等于模型修订变化。缺失或非法阶段数据明确显示不可用。

需求页的设计类型与工程选择复用已有下拉组件，支持方向键、Home/End、Enter/Space 选择及 Escape/Tab 关闭，不包含原生选择框的文字搜索行为。输入框与多行文本沿用浅深主题的柔和边框和可见键盘焦点；复选框、单选框和文件选择器保持各自交互。

任务页按实际记录显示目标表面、任务工程、模型修订与配置，完整服务/模型身份在“完整模型引用”中核对。来源与当前模型一致仅表示身份一致；历史来源、当前观察不可用和未绑定模型分别说明，任务“引用已过期”不自动证明模型已经变更。研究任务或尚无模型的建模交接可合法不绑定模型，S0 也可作为已记录目标。

任务消息沿用已存文字：领取中的文字标“Agent 进度”，表面问题的完成文字标“Agent 回复”，失败和归档分别标示；完成优先读取 result_text，兼容早期 progress，保留换行与纯文本。任务回复不代表像质或优化验收。切服务/工程、断连或任务读取失败时立即隐藏旧任务卡；有 HTTP 连接但原生观察不可用时仍可回看记录。来源展示不会领取任务、改写模型或触发分析。

## 开源依赖

React 19.3.1（MIT）、Motion 14.6.0（MIT）、Lucide React 1.62.0（ISC）、Vite 8.3.5（MIT）。完整版本见 `package-lock.json`，运行依赖许可由构建脚本保存到 `THIRD_PARTY_NOTICES.md`。
