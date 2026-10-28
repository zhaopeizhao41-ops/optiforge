# 3A 原生几何追迹交接

2026-10-28。本轮完成软件实现，提交后交规划聊天复审及用户验收；不代表阶段一整体通过。稳定原工作区不变、不 push。既存 `frontend/src/styles.css` 差异保留且不纳入本轮提交。

## 实现范围

- 原生 Sequential、当前配置、非偏振实光线，从 S1 到像面；3/5/9 条确定性子午 pupil，每条路径保留视场、波长、原生输入、单位、以 S1 为基准的坐标和终止原因。
- 真实视场/主波长默认选择、完整表 Radial/Rectangular 归一化、243 路径 / 200 面 / 48600 点上限、30 秒预算。拒绝 GRIN、非序列、未知内部曲线/多路径面型，以及未核实的 ignored fields 模式。
- 单引擎队列、受上下文保护的只读计算；前后原生观察、缓存命中和合并请求均需门禁。追迹本身不改工程/模型版本，也不解除已有 uncertain。
- POST `/api/trace` 接受任务；GET `/api/traces/<id>` 只读结果。任务摘要按 operation_id 保留，物理缓存按 key 复用；最多 8 结果、32 MiB，Future 和事件不保存交点副本。
- 前端 400ms 合并；最多一个在途请求，模型变更后隐藏旧结果并重算，模型/实例切换重置开启状态。POST 网络错误不重放，已接受任务可从摘要恢复。
- 独立绘图组件，YZ/XZ 本地等比例投影、视场图例、本地隐藏、波长线型、错误/截光详情。D2 严格拒绝完整轮廓，同时显示真实光线诊断。
- 沿用 Motion / React / Lucide 和自定义 SurfacePicker；没有新增前端依赖。未接入点列图、MTF、波前、优化或独立计算内核。

## 开发验证

初版提交 `6d2d811` 的受影响自动化合计 **203 项通过**：工作台 / 同步 / HTTP / 新追迹 105，旧第二批离线回归 33，旧第三批离线回归 33，前端 32。生产构建通过。新增光线适配、矩阵、失败前缀、缓存内存/队列边界、授权、前后观察、版本、迟到响应、合并、断连、卸载与纯投影测试。

运行：

```powershell
python -B -m unittest discover -s tests -p 'test_workbench*.py'
python -B tests/regression_stage_two.py
python -B tests/regression_stage_three.py
cd frontend
npm test
npm run build
```

上述均为软件/模拟测试，不包含原生交点计数。新矩阵和错误语义测试不以复制生产输出作为唯一断言。

### 首轮复审修复：R3A-1–5（2026-10-28）

按唯一计划的五项复审问题修复，交规划聊天再次审查：

- **R3A-1**：追迹计算与缓存命中的前后观察、计算报错后的观察，遇到元数据或快照读取失败均清空当前快照并设置 `context_valid=False`。本任务不再次读取来掩盖失败，不增加只读光学修订，不清除既存 uncertainty。测试同时验证旧上下文和新读取的失效上下文均不能继续写入或追迹。
- **R3A-2**：服务实例或模型身份切换时终止旧传输并释放占用；POST、GET 及 finally 以生命周期代次隔离。覆盖已接受、POST 未返回、GET 未返回三种切换，以及忽略 abort 的旧响应成功、失败。新模型重新开启后可完成追迹；同实例未知 POST 保持不自动重放。
- **R3A-3**：物理 key 相同但工程来源不同的缓存重新计算，生成新 result_id；旧结果及 source 不改写。工程 A→B 迁移后可命中 B 的新缓存，前端能展示新结果。
- **R3A-4**：瞬时结果读取失败保留已接受任务。恢复连接时最多追加一次 GET（每任务初次加恢复共两次），连续状态事件不触发请求循环；显式重试优先读取该已接受结果。404/410 或 `TRACE_NOT_FOUND` 直接结束等待，不自动 POST。另覆盖重连已完成后旧 GET 才报错，以及 GET 成功返回时仍处于断连阶段的时序；断连中的结果仍由显示门禁隐藏。
- **R3A-5**：清理失败的阻塞前移至调度入口。阻止 native read、计算、修改，以及会在发布状态时读取原生快照的上下文操作与刷新；缓存状态、已存结果、事件和知识读取继续可用。不会通过只读名称绕过阻塞，也不在本批自动解除清理阻塞。

修复后运行上述完整命令：**234 项通过**（工作台 115、第二批回归 33、第三批回归 33、前端 53），生产构建与 `git diff --check` 通过。相对初版增加 31 次测试执行；参数化子场景未另计，HTTP 测试类沿用继承执行。此轮仅进行软件/模拟回归，未重新运行原生追迹或浏览器体验验收，后文 1455 个原生交点和截图仍为初版证据。

## 原生只读证据

本机 OpticStudio 2024 R1；独立调用 `SingleRayNormUnpol`，abs=1e-8、rel=1e-8，单位沿原生。以下每个工程均读取原目录自己的已确认提案；不自动确认、修改处方或 Save。源模型、提案、project.json 字节及前后原生处方、配置、模型/工程版本均核对不变。

- `coaxial_dermatoscope_35mm`：3 个实际视场、0.83/0.81/0.85 μm、5 pupil；45 条全部到像面，990 个逐面点，最大差 0。原始文件 SHA-256 为 `ea57669b697aec8d766b01980cc45be6c9f9f843d22ad89db9e313de75f48f7f`；确认时间 2026-10-26 13:30:41。22 个目标面，完整轮廓继续拒绝 S1/S2。
- `test_doublet_lens`：2 视场、3 波长、5 pupil；30 条全部到像面，120 个点，最大差 0，原生计算约 141ms。源目录存有确认标志，旧提案缺少 confirmed_at；报告保留这一缺项。
- `czerny_turner_reflective_fixed`：15 条成功、105 点、差 0，约 312ms；实际处方为折射 Standard 面，**不算反射证据**。
- `czerny_turner_vis_spectrometer`：15 条均先截光，30 个可信前缀点、差 0，约 234ms；未把被挡后的原生计算点绘为穿透光线。
- `compact_spectrometer_COMPLETE`：30 条均在目标 S8 首见 error=2，210 个可信前缀点、差 0，约 343ms；覆盖 S5/S7 CoordinateBreak 及 S6 Binary2。只验证了失败前的实际路径，不宣称完整系统设计有效。

每个模型的 JSON 在被 Git 忽略的 `output/workbench_raytrace_acceptance/reports/<project>/native-acceptance.json` 与 `native-result.json`。D2 最终复跑记录覆盖该项目同名文件；报告中的实际 elapsed_ms 为准。原生点数总计 1455，与 203 项软件测试分别计数。

原生验证命令：

```powershell
python -B tests/verify_workbench_raytrace_native.py --model 'D:\mcp gemini zemax\output\coaxial_dermatoscope_35mm\coaxial_dermatoscope_35mm.zmx' --serve --port 8767
```

该命令是只读原工程验收入口，不是给导入副本开门禁的工具。普通产品服务仍使用 `python -B workbench.py --model <path>`；未确认的本地工程须在外部 Agent 中按原流程确认。

## 浏览器证据

真实 D2 页：默认 15 条/主波长，设置后 81 条/三波长/9 pupil；深浅主题、YZ/XZ、图例和自定义菜单、End/Enter/Escape 实测。原生预览明确显示“光线诊断视图 · 镜片轮廓未显示”及 S1/S2 问题；没有修改原件来获得轮廓。

模拟页：`python -B tests/workbench_fake_service.py --port 8768 --trace`。两个页面同时开启，模拟半径 20→24 后都显示 revision=2、同一 result_id=`5f7649e2-7859-4f2e-9d10-420b5b8ab48b`，部分失败为到达 5 / 截光 5 / 错误 5。完成时间 `2026-10-28T05:17:55.722815+00:00`，两个 DOM 绘制时间戳分别为 `1793164675752` / `1793164675751`，延迟 **29.185ms / 28.185ms**；这是模拟小样本的完成到绘制延迟，不包括原生计算，也不是性能保证。

全部失败模拟：0 条到达、15 条错误、光线 path 数 0。切换模型后两个页面的显示光线开关重置；停止夹具后保留处方并显示断连，恢复后采用新实例。自动化另覆盖未知 POST 恢复和断连不重放。最终在 320×780 下以键盘选 9 条采样，显示 27 条真实光线；innerWidth=320、scrollWidth=305，无横向溢出。截图 `native-320.png`，详见问题文档 Q3。

截图在 `output/workbench_raytrace_acceptance/reports/`：`native-d2-light.png`、`native-d2-dark-picker.png`、最终构建 `native-d2-final.png`、`native-320.png`，及模拟截图。浏览器证据与原生数值对照分开；用户交互验收未代勾选。

## 待复审及限制

1. Ignore/IsActive 归一化、非零渐晕因子组合的原生证据需专用受控夹具；当前明确拒绝有忽略/非活动字段的模型。
2. 完整、成功的反射折叠路径尚未原生验收；已有特殊面型/坐标变换有效前缀和离线矩阵/反向传播测试。
3. 320px 已有最终构建真实交互证据；减少动效开关与用户对顺滑程度的最终体验验收仍需复验。
4. 当前 8765 导入项目未确认，不能因另一原工程已确认就运行计算。8767 是直接读取原授权的只读验收预览，不影响该门禁。
5. 第二批真实写入自动同步尚未验收；这次只读追迹不能补签该项。更改、优化、像质和公差均未在本轮原生验收中运行。

具体 API 与方案问题见 [WORKBENCH_RAYTRACE_DESIGN_QUESTIONS.md](WORKBENCH_RAYTRACE_DESIGN_QUESTIONS.md)。本轮交付后停止扩展，等待规划复审和用户验收。

本地交付分支为 `codex/visual-workbench-stage-one`；初版提交为 `6d2d811`（`feat: add native workbench ray tracing and diagnostic views`），首轮复审修复以 `fix: isolate trace lifecycles and invalidate failed observations` 标识。均不含并行 styles.css 修改，修复提交 SHA 随交接回复提供。停在规划复审与用户验收点。
