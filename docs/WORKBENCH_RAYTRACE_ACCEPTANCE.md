# 3A 原生几何追迹交接

2026-10-28。本轮完成软件实现，提交后交规划聊天复审及用户验收；不代表阶段一整体通过。稳定原工作区不变、不 push。既存 `frontend/src/styles.css` 差异保留且不纳入本轮提交。

## 实现范围

- 原生 Sequential、当前配置、非偏振实光线，从 S1 到像面；3/5/9 条确定性子午 pupil，每条路径保留视场、波长、原生输入、单位、以 S1 为基准的坐标和终止原因。
- 真实视场/主波长默认选择、完整表 Radial/Rectangular 归一化、243 路径 / 200 面 / 48600 点上限、30 秒预算。拒绝 GRIN、非序列、未知内部曲线/多路径面型，以及未核实的 ignored fields 模式。
- 单引擎队列、受上下文保护的只读计算；前后原生观察、缓存命中和合并请求均需门禁。追迹本身不改工程/模型版本，也不解除已有 uncertain。
- POST `/api/trace` 接受任务；GET `/api/traces/<id>` 只读结果。任务摘要按 operation_id 保留，物理缓存按 key 复用；最多 8 结果、32 MiB，Future 和事件不保存交点副本。
- 前端 400ms 合并；最多一个在途请求，模型变更后隐藏旧结果并重算，模型/实例切换重置开启状态。POST 网络错误不重放，已接受任务可从摘要恢复。
- 独立绘图组件，YZ/XZ 本地等比例投影、视场图例、本地隐藏、波长线型、错误/截光详情。D2 初版严格拒绝完整轮廓；用户反馈后的原生显示修复见文末。
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

## 用户反馈修复：完整轮廓与隐藏详情（2026-10-28）

基于用户四条浏览器意见修复，不改外部 `D:\mcp gemini zemax\plan.md`：

- 新增 `display_layout`，采用原生 `LDE.GetSag` 的主矢高、`MechanicalSemiDiameter` 和绘图标志生成交互 SVG。严格 `layout` 的实数定义域、碰撞与材料边界检查保留，作为详情中的诊断，不再因诊断而清空原生显示轮廓。不改半径、口径、厚度、求解或原件。
- 原生 D2 的球面超定义域主矢高饱和为半径；机械口径处保留平直边缘。water 文件最后一段材料直到像面也可明确显示为材料区域（`ends_at_image`），不将其宣称为独立可制造镜片。
- 主画布移除常驻计数、长错误段和重跑行；“光路详情”唤出浮窗，背景 blur(8px)，统计、重跑和几何诊断放在浮窗内。按钮使用既有 Motion 弹簧交互，弹窗使用既有进入动画。设置、投影仍就近保留。
- 画布采用视口高度预算，场景允许 flex 收缩，工具栏、图例和底部控件保留空间；320px 自动换行、场景最小高度 280px。全图按横纵同尺度适配，放大仍允许局部查看。
- 该显示适配限定同轴 Sequential Standard、Squared 边缘、无隐藏面/自定义孔径/局部变换/反射；最多 200 个目标面、97 个常规高度采样并加精确圆锥边界和机械边缘，5 秒协作式预算。原生属性或矢高读取失败只禁用此显示层，不发布局部轮廓。单次原生调用本身不可由该预算中断。

使用公开 ZOS-API 与官方示例，无新增开源内核或产品依赖、未反编译 Zemax。官方 `CrossSectionExport` 两份 PNG 作为对照，未将位图当成交互绘图层。Windows 原生窗口捕获在恢复重试后仍超时，因此使用成功的官方布局导出；未操作或改变用户 Zemax 窗口。原生证据目录为 `output/workbench_raytrace_acceptance/layout-review/`，含两份 `*-zemax.png`、`*-probe.json`。

最终代码的只读复核（每文件 45 条、3 波长、5 pupil）：

- **coaxial_dermatoscope_35mm**：990 个逐面光线交点，最大差 0；22 个显示面、13 个材料区域。110 个轮廓采样点另以 GetSag + GetGlobalMatrix 独立复核，最大差 2.8422e-14 mm。首次完整刷新约 1563ms，原生追迹约 766ms。
- **water_objective_na09_475mm_portable**：945 个逐面光线交点，最大差 0；21 个显示面、13 个材料区域。105 个轮廓采样点独立复核，最大差 0。最终预览复跑首次完整刷新约 1422ms，原生追迹约 625ms。源 SHA256 `0d11ba20f393743a2c538ebd3d50237910b125f8986cb578f3993f85b1715c09`。
- 两文件、各自提案与 project.json 字节、前后原生处方、state_version/model_revision 均保持不变。water 原提案 confirmed=true、缺 confirmed_at，报告如实保留，没有补写确认。运行记录为两项目的 `native-acceptance.json` / `native-result.json`；不与初版点数重复累计。

差异复核结论：本轮没有用新的数学近似修正追迹，已有实光线逐点对照一致。主要差异来自轮廓被整图拒绝、机械口径漏读，以及比较时的视场/波长/采样设置不同。UI 默认主波长、实际最小/中间/最大三个视场、5 pupil，与 Zemax 对比时应设成相同参数；当前路径从 S1 开始，不画物面到 S1 的入射段。

浏览器最终构建实测：1254×892，D2 22 面/13 区域/15 光线，底部控件 bottom=819、面板 bottom=836，未被面板裁切；water 21 面/13 区域/15 光线，深浅主题均可见。320×780 的 innerWidth=320、scrollWidth=305、sceneHeight=280，无横向溢出；详情宽273、x=16，blur(8px)，Escape 关闭并恢复到“光路详情”按钮。截图位于 reports：`coaxial-layout-final.png`、`water-layout-light.png`、`water-layout-dark.png`、`water-layout-320-final.png`、`layout-details-blur.png`。用户对美观、顺滑的验收仍由用户决定。

软件回归 **243 项通过**（工作台124、旧第二批33、旧第三批33、前端53），生产构建和 `git diff --check` 通过。新增9个显示测试覆盖原生读取异常、超域显示与严格检查并存、机械边缘、像面材料区域、坐标拒绝、采样预算、重复观察稳定性。计时仅进入验收报告，不进入状态快照，以免每次读取都增加 state_version、使追迹失效。

本轮本地修复提交以 `fix: render native lens outlines and simplify trace controls` 标识；未 push，未触碰并行 `styles.css` 内容（SHA256仍为 `0F5EEE8725E82CDBA37A228C095E1FF4527254D00F384BD557D43275FE3B67A8`）。新预览 water 为 `http://127.0.0.1:8770/`、D2 为 `http://127.0.0.1:8771/`；旧8767进程加载旧Python模块，不代表本轮版本。展示策略差异供规划复审，见问题文档Q4。

## 继续验收修复：打开模型与 XZ 截面（2026-10-28）

用户发现“打开模型”不可用，原因是上次交付的8770其实是固定模型的只读原生验收程序，注册表只有快照读取，UI却仍显示导入按钮，导致未知工具错误；不是ZMX格式或原文件损坏。8765普通工作台仍是旧Python进程，前端构建更新不会重载其原生适配代码。

**当前入口已调整**：8770运行最新 `workbench.py` 普通工作台，支持真实文件选择与导入；8771运行water原工程的只读验收预览，不能换文件。服务握手新增 `capabilities.model_import`，新版固定预览明确返回false、UI隐藏按钮、控制器拒绝提交，HTTP在建立导入目录前返回 `MODEL_IMPORT_UNAVAILABLE`。普通服务默认为true，未提供该字段的旧普通服务保持兼容。验收预览不写服务发现文件，普通8770写入本工作区的服务发现供MCP桥接使用。

实际浏览器测试：点击8770“打开模型”，通过系统文件选择器选择 `D:\mcp gemini zemax\output\water_objective_na09\water_objective_na09_475mm_portable.zmx`，读取完成后标题、水镜轮廓和表面参数切换成功，状态已同步。内容存于独立本地 `output/import_water_objective_na09_475mm_portable_c4a3a74f/source/`，新项目提案仍为false，没有复制原项目授权；原文件不改。截图 `reports/model-import-restored.png`。打开/看轮廓不受提案确认限制，原生追迹继续遵守现有项目确认规则。

XZ旧行为是将 `Px=0, Py=-1..1` 的同一组子午光线改投影轴，X方向可能几乎完全重叠；且轮廓被固定排除。这符合旧计划的“纯本地投影”约定，但不满足用户期望的X方向光束截面。本轮按反馈改为：

- 追迹设置增加 `pupil_axis`，默认Y保持兼容；YZ采样Px=0、Py=-1..1，XZ采样Py=0、Px=-1..1，真实视场与波长不变。不旋转或伪造视场坐标。
- 切换截面会选择该方向的采样/缓存，方向进入设置哈希与显示身份，旧方向结果立即隐藏；仍以400ms合并、单在途任务完成后只处理最新方向，未扩大243条/200面预算。
- 只有经过适配器确认的同轴旋转对称Standard轮廓声明 `symmetric_planes=[YZ,XZ]`，才在两截面共享；未知/局部变换/特殊面轮廓仍拒绝。离轴光线超出截面时继续标记“三维投影”。主光线判定同时检查Px与Py为0，避免XZ全部加粗。

新增X方向原生只读对照：water 45条/945交点、D2 45条/990交点，全部到达像面，独立SingleRayNormUnpol最大差0。轮廓另外沿GetSag(x,0)和全局矩阵对照，共215个点，最大差2.8422e-14 mm；不是以Y方向公式自比。源文件、各自提案、project.json、原生处方及版本保持不变。两工程的 `native-acceptance-xz.json` / `native-result-xz.json` 保留在reports下，不覆盖Y方向记录。命令在既有验证脚本追加 `--pupil-axis X`。

浏览器8771从YZ切XZ：旧线先消失，随后21面、13区域、15条主波长真实光线，图例标记三维投影/XZ；切回YZ再次先隐藏旧线。截图 `reports/water-xz-native-final.png`。只读页不再显示打开模型。新方向的几何显示已实测，未将两个模型外推为折叠/非旋转对称系统支持。

完整受影响回归 **250项通过**（工作台127、旧第二批33、旧第三批33、前端57），生产构建与diff检查通过。新增测试包括只读导入在落盘前拒绝、普通/旧服务兼容、X光瞳实际输入、对称轮廓标志、方向结果隔离及切换中的迟到结果。`styles.css`并行差异仍保持原SHA，不纳入提交。改动本地提交以 `fix: restore model import and trace orthogonal pupil fans` 标识，不push；方案差异见Q5/Q6，仍待用户体验验收。

## 六项反馈修复：单视图与鼠标相机（2026-10-28）

执行依据为唯一 `D:\mcp gemini zemax\plan.md` 顶部当前任务和规划聊天裁决；从 a6c1a02 开始，未改该计划，未更改设计或授权，未推送。交付以 `fix: add single-view camera controls and explain trace eligibility` 标识，本轮等待规划聊天从25457dd起整体复审与用户体验验收。

- 删除双视图控件与轴向文字，内部固定 Y 扇面；API X 能力保留。顶部单位变成普通文本，侧栏“更多属性”继续提供系统信息。
- 鼠标滚轮按指针锚点等比缩放（10%–1600%，处理 pixel/line/page delta）；左键拖动平移，4px阈值、pointer capture、up/cancel/lost capture收尾。单击镜面选面，拖动不误选。“一键复位”恢复100%、零平移并适配当前轮廓与真实光线联合范围。百分比按钮只回100%，保留其原有缩放语义。
- 同模型读取/新结果保留相机；一旦用户移动相机，追迹范围变化不会拉走已观察的内容。模型或服务身份切换重置相机与显示意图。缩放不再围绕当前选中表面自动跳转。容器调整维持横纵同尺度，尺寸恢复后对应镜面坐标恢复。
- 开启光线后直接解释未确认、归属不同、断连/失信、过期、排队、运行或失败，零可绘制路径在画布显示原因。详细统计仍在模态。未确认时重跑禁用，不自动解除授权；离线覆盖实际确认后恢复计算。
- 同模型的视场/波长表改变时清除无效或已忽略索引，无剩余项时回退真实可用默认项，在设置中告知；清理后的选择持久保留，不在旧索引后来重现时偷偷恢复。配置、孔径、归一化、瞄准与完整表来自同一原生快照，模型修订与设置共同约束结果。
- 来源范围在底部和详情明确：工作台当前会话、当前文件/副本路径、Standalone模式、实例和修订；不监听磁盘原件或其他Zemax窗口。浏览器不提供源完整路径，未伪造。Q7保留当前导入项目的实际确认缺口。
- 修复未保存空白会话的标题元数据API错误：使用已核实的TitleNotes.Title，空/缺失可选标题回退未命名模型，保留其他原生错误失效规则。生产核心变更仅此一行；不需要重复无关光线计算。

### 软件与原生证据

软件合计 **260项通过**：前端66、工作台128、旧第二批33、旧第三批33；生产build与diff检查通过。初次误用pytest因当前解释器未装该包未执行，随后采用仓库实际unittest入口，以上是成功执行的准确计数。最新可选标题fallback追加后重跑受影响test_workbench.py的35项通过；其余已通过部分未重复累计。

2026-10-28T08:06:50Z与08:10:25Z重新读取两份原授权文件，默认Y方向/三个真实波长/5 pupil：D2 45条990交点、water45条945交点，独立SingleRay最大差均为0；原生轮廓另检110+105个点，最大差2.8422e-14 mm。源文件、各自提案/project.json字节、处方和工程版本保持不变。原生记录在 `reports/<原项目名>/native-acceptance.json` 与 `native-result.json`。这是已有授权的只读数值对照；没有授权8770导入副本，也没有进行优化或像质分析。

相机手势前后HTTP内存证据 `reports/single-view-camera-before.json` / `single-view-camera-invariance.json`：8770无新任务，8771只有一次主动refresh，无新trace任务、模型快照不变、model_revision均为1；相机不会发POST追迹。之后为了检验新结果到达，用户界面显式勾选第二波长触发一次新采样，单独计入浏览器试验，不混入相机零计算证据。规划聊天也在独立页执行默认Y追迹用于复审；该额外追迹不是本相机前后区间内的手势计算。

### 真实浏览器验收

- **664×672**：实测滚轮100%→161.60744%，缩放锚点(300,430)前后对应光学点不变，scrollY=0；拖动60×40像素得到同样平移。单击真实镜面选到S13，从镜面拖动选中项不变，拖出面板仍完成并释放；一键回100%/0/0。
- **同模型刷新与新结果**：water已移动相机zoom=161.60744、pan=(-3.50465,25.54467)，实际15→30条光线后相机不变，首镜面SVG路径起点及路径相同（M-55.75955,197.90778）；聚焦侧栏开→关后回到同一路径。容器尺寸变化只适配容器并保持等比例，不借相机操作改采样。
- **320×780**：innerWidth320、scrollWidth305（含竖滚动条），场景280px，无横向溢出；30条真实光线可绘，深浅主题与减少动效设置均实际操作。详情宽273、x16、背景blur(8px)，Escape恢复到唤起按钮。实际pointercancel/lostcapture由纯手势测试覆盖；浏览器实际覆盖drag出界和释放，不伪称发出了OS级cancel事件。
- **导入与模型切换**：临时8772以生产普通服务、隔离活动项目文件验收。浏览器真实选择D2，再在161.60744%/非零平移/显示光线开启时选择water，新模型identity改变，100%/0/0、显示开关关闭，未继承授权。修复空白标题后原生启动valid、标题未命名模型，浏览器空白→water再次成功；证据 `single-view-blank-session.json` 与 `single-view-blank-to-import.png`。正常8770会话未替换，临时实例结束后关闭。

最终截图：`single-view-d2-status.png`、`single-view-d2-dark.png`、`single-view-water-zoom-pan.png`、`single-view-water-reset.png`、`single-view-water-320-final.png`、`single-view-import-switch-final.png`、`single-view-blank-to-import.png` 及最终664px图（single-view-water-wide-final.png，文件名保留；实际DOM宽664、高672）。环境没有可调用的RecordScreen接口，因此使用实际浏览器截图及前后DOM/原生/HTTP记录，未宣称提供录屏。最终美观和顺滑程度由用户体验验收。

### 运行入口与限制

正式入口仍为 **http://127.0.0.1:8770/**，普通workbench.py、有导入能力，实例e8410dec-bbfa-4e15-a8c0-3e40dc8cf2eb；当前D2导入项目import_coaxial_dermatoscope_35mm_fbd2d8c8，model d4900fd9-d6a7-444b-96e7-60779195e07a，revision1/state3，proposal_confirmed=false。它运行a6c1a02时启动的Python后端，本轮提供新前端构建；没有为标题修复停止或重建用户会话。新标题代码在新启动的临时8772原生进程验证，不把旧8770称为已重启的最终后端。

**http://127.0.0.1:8771/** 是water原授权只读验收，实例a38ea2a5-2c1c-4a17-8d11-c0a9b6de28a0、model2164f32a-7a86-4375-a144-16f232b3c53c、revision1/state1，model_import=false；原Python为a6c1a02时启动，本轮只使用最终前端做相机验收。不是可导入主入口。

最终静态资源为index-D2j_amhh.js / index-DPZZDpAJ.css；保留并行styles.css原SHA `0F5EEE8725E82CDBA37A228C095E1FF4527254D00F384BD557D43275FE3B67A8`，不纳入提交。源/副本监听、多会话同步、真实写入原生验收与Q1/Q2限制继续保留；本轮不扩展下一批。
