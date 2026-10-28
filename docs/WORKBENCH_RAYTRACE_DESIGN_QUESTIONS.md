# 3A 追迹：API 证据、决定与待复审项

2026-10-28；执行依据为 `D:\mcp gemini zemax\plan.md` 的 R1–R5。实现从 `64beb34` 开始。派发版 SHA-256 为 `E7493D9A51BC755E8525DADDC15B3952FF88E8037A6EFA93C06EC9DD853CEA45`；结束时计划仅追加了规划聊天的持续跟踪说明，SHA-256 为 `833542050E27C3AC9BAF3C788F87371157EF2B612F66996EA757A203514E531E`。未修改该计划。

## 已核实的 API 契约

证据来自本机 OpticStudio 2024 R1 官方 Python 示例 22/23、`ZOSAPI_Interfaces.dll` 和安装目录 Help 中的官方 CHM；没有反编译 Zemax 算法。CHM 阅读辅助包只装在被忽略的 `output/workbench_raytrace_acceptance/api-reader/`，不是产品依赖。

- `ReadNextResult` 在本机 pythonnet 返回 15 项：available、rayNumber、ErrorCode、vignetteCode、xyz、lmn、normal xyz、opd、intensity。available 仅表示有记录。
- `SingleRayNormUnpol` 返回 14 项；输入 bool 是 **calcOPD**。本轮传 False，绝不把它解释为渐晕开关。
- `GetGlobalMatrix` 返回 bool + 行序 3×3 旋转 + xyz 平移，共 13 项。先检查 bool，再执行局部→全局→S1 参考系；不累加厚度代替全局坐标。
- 官方 Normalized Field Coordinates 定义了 Radial 最大径向尺度、Rectangular 各轴最大绝对尺度，全部基于完整表。负视场和零轴在纯函数测试覆盖。
- 官方 Vignetting Factors 说明归一化 pupil 的缩放、平移与旋转由原生处理；本实现传原始 Px/Py，不再手工应用 VDX/VDY/VCX/VCY/VAN/TAN，完整记录实际数值。现有原生样例未覆盖所有非零因子组合，不能声称逐项已验证。
- 官方 API 帮助只将 vignetteCode 描述为渐晕码，未提供本机足够明确的通用整数→面号映射。实现按目标面依次追迹，记录**首个出现非零码的目标面**，保留原码；不使用 abs(error) 猜面号。截光交点可作为终点，其后交点不进入可信路径；错误交点不用。
- 官方说明超时返回后工具仍运行。实现取消并等待 IsRunning=false 后才 Close；不可取消时队列仍显示运行，不能假报已停止。清理失败以独立 trace_blocked 阻止后续原生保护操作，不给模型伪记一次修改。

## Q1：忽略/非活动视场尚缺原生归一化证据

官方 IField 暴露 Ignore / IsActive，但帮助没有充分说明这类字段是否参与归一化极值。未修改用户模型来试探，也未猜测。当前只要完整表含忽略/非活动字段，就返回 `TRACE_IGNORED_FIELDS_UNVERIFIED`，在打开追迹工具前拒绝；普通属性和轮廓读取继续可用。

提交规划聊天决定后续是否批准专用合成原生夹具，验证 ignored extrema、Rectangular 双轴及非零渐晕因子后扩展支持。本轮软件开发无需等待这一决定，也没有把未验证模式纳入成功路径。

## Q2：原生折叠证据的准确边界

已有已确认工程中，`compact_spectrometer_COMPLETE.zmx` 的可信前缀经过 S5 CoordinateBreak、S6 Binary2、S7 CoordinateBreak，共 210 个点与独立 SingleRay 一致；全部 30 条在请求 S8 时返回原码 2，未绘制其后路径。`czerny_turner_vis_spectrometer` 的 15 条均先在 S2 截光，不能算穿过后续折叠镜的验证。名称为 reflective_fixed 的样例实际是 Standard / BK7 折射处方，不能凭文件名算反射验证。

因此本轮有原生坐标变换/特殊面型前缀证据，尚无完整、成功穿越反射折叠系统的原生证据。负轴向传播顺序由离线夹具覆盖，不冒充原生折叠验收；不为补验收修改这些处方。

## Q3：320px 浏览器复验（已补齐）

初次 viewport override 与页面重载/交互时序导致截图返回 1280，未把这些截图标成窄屏验收。后续在最终构建中，直接对当前真实 D2 页设置 320×780，打开设置、以 End/Enter 选 9 条采样、关闭设置并等待真实结果完成。

DOM 证据：innerWidth=320、scrollWidth=305（含竖向滚动条后无横向溢出），工具栏宽 237px 且自动换行，27 个真实 ray path，result_id=`f4da1eef-7263-4c8f-9939-166feb4c7f5d`；截图 `output/workbench_raytrace_acceptance/reports/native-320.png` 已保存。之后重置 viewport override。用户对动效的最终体验验收仍未代签。

## 门禁与本轮范围

当前主服务的 import 工程未确认，仍不允许追迹。原生验收程序直接读取原工程自己的 design_proposal.json，另外创建只读、独占的原生会话；没有复制 confirmed=true 到 import，也没有改原项目元数据。验证服务没有注册任何修改工具。新增 HTTP 追迹入口，未新增可选 MCP 工具；已有 MCP 名称和签名不变。

第二批真实参数修改→UI 自动同步仍未原生验收。本轮所有模拟写入只发生在无 Zemax 的夹具中；没有运行优化、公差或像质分析面板。

## 首轮复审反馈处理（2026-10-28）

已读取唯一计划追加的 R3A-1–5，完成软件修复并提交规划聊天复审，未改外部 plan.md：

1. **观察失败**：计算前后、缓存命中前后及计算失败后的元数据/快照读取均采用失效边界。失败任务不重复观察来重新信任旧上下文；保留既存 uncertainty，禁止后续保护操作使用失效快照。只读错误不虚增 model_revision。
2. **生命周期切换**：服务实例/模型身份变化时清除旧 pending、inflight 和 reading，以代次检查隔离迟到回调及 finally。Abort 仅帮助释放传输，正确性不依赖传输遵守 abort。同实例重连和未知 POST 继续不自动重放计算。
3. **跨工程缓存**：采用“来源不兼容则重算”，保留物理 key 定义和旧 source 不变；生成新结果后可按当前工程展示。没有将当前身份盖到旧结果上。
4. **结果恢复**：瞬时 GET 错误保留已接受身份，等待重连或用户重试，只读最多追加一次；超过预算停止自动恢复。404/410/TRACE_NOT_FOUND 为终态。连续事件不会消耗新的恢复次数；读回期间再次点击重试也不新建 POST。重连与 GET 迟到的两种顺序已覆盖。
5. **清理阻塞**：阻止所有声明 native 的操作，也阻止会触发原生快照发布的上下文操作/刷新，保留缓存与知识读取。当前不提供自动解除清理阻塞，恢复需安全停止并重启服务；未增设绕过生命周期的恢复接口。

Q1 按规划决定保留受限拒绝；Q2 保留完整折叠与非零渐晕组合的原生证据缺口，不修改用户处方补齐；Q3 的初版 320px 证据保持。此次完整软件回归为 234 项通过，生产构建通过；不把此次软件回归计作新的原生或浏览器验证。详细计数见交接文档。

## Q4：原生显示轮廓与严格处方几何分开（用户反馈后供规划复审）

用户明确指出 D2 能在 Zemax 显示，并授权使用 water_objective_na09_475mm_portable.zmx 复测。已先查明：D2 原生 GetSag 在球面实数定义域外仍返回成功、主矢高为 R；water 最后 S20 H-K9L 延伸到像面，原生仍显示该材料区域。两个模型的原生 CrossSectionExport 均成功，且机械半口径与 clear 半口径不同，旧轮廓既漏机械边缘又以严格诊断清空全部镜片。

按用户修复授权，本轮已添加独立 `display_layout`，优先用官方 GetSag + 机械半口径恢复同轴 Standard 的交互轮廓；旧严格 `layout` 与判据未放宽，问题只收进详情。原生显示成功不等于几何/制造性验收通过。此项取代旧计划“D2只画真实光线、不画轮廓”的显示策略；未改外部 plan.md，由规划聊天决定是否正式纳入后续基线。没有开启非球面、反射/折叠、特殊边缘或自定义孔径的猜测显示。

独立原生对照已通过：两个模型共1935个光线交点差0；215个轮廓采样点的最大差2.8422e-14 mm。源文件、提案、工程元数据及原生处方与版本保持不变。显示读取最多200面、5秒协作式预算，失败时仅关闭显示层；首次完整快照样本约1.4–1.6秒，后续若扩展复杂面型，应评估采样缓存/精度与原生读取成本。不能将这两个同轴样例外推为完整 Zemax 布局兼容。

本轮沿用 React/Motion/Lucide，没有引入开源光学内核或逆向依赖。公开证据：

- [CrossSectionExport API](https://developer.synopsys.com/docs/zos-api-interface-2024-r2/interface_z_o_s_a_p_i_1_1_tools_1_1_layouts_1_1_i_cross_section_export.xhtml)
- [官方社区 Python 布局导出示例](https://community.zemax.com/zos-api-12/api-layout-plot-python-4731)
- [官方机械边缘与口径说明](https://optics.ansys.com/hc/en-us/articles/42661783548563-Modeling-optics-with-realistic-edge-apertures)

主界面长诊断和统计改为按钮唤起的模态详情，背景虚化，默认只保留图与属性。该实现及243项软件回归、两文件原生只读证据均已交接；用户尚未签署体验验收。Q1/Q2与真实写入同步证据缺口继续保留。

## Q5：用户期望 XZ 方向光束截面，取代旧“同一组光线本地换轴”约定

用户补充XZ不对后，核实旧实现的所有光线都在Px=0的子午光瞳扇面取样。投影到XZ的数学换轴本身没错，但X范围可能几乎为零，且旧实现只允许YZ轮廓，因此不能提供用户期望的XZ光束图。

按用户纠错授权已实施 `pupil_axis=X/Y`：切到XZ改取Py=0、Px=-1..1，保留实际field/wave，方向进入请求设置、哈希和显示资格，切换会选择对应缓存或重新追迹，不继续复用Y扇面。只有确认同轴旋转对称且没有特殊坐标/孔径的原生Standard显示层声明两截面可用，其他面不复制轮廓。三维离轴路径仍标记投影，不声称所有光线都位于截面内。

此项改变唯一计划中的“YZ/XZ无需再次追迹”约定，已记录供规划聊天决定是否更新正式基线；外部plan.md未改。原生证据为两文件1935个X方向交点差0、215个X方向矢高/全局坐标轮廓点最大差2.8422e-14 mm。软件控制器覆盖切换时的迟到Y结果不显示、只提交一次X请求。不新增光学设计/优化，不改文件或提案。

## Q6：固定验收实例不能冒充完整工作台

此前8770固定原生样例服务没有载入工具，却沿用完整UI的“打开模型”，导致用户遇到UNKNOWN_TOOL。已把8770换为最新版正常工作台，浏览器实际ZMX导入成功；8771保留独立只读water验收。新握手通过model_import能力告诉UI隐藏只读实例的按钮，HTTP也在任何导入落盘前拒绝；旧未声明能力的普通服务维持兼容。验收程序不写MCP服务发现文件，避免将固定实例当作正常桥接目标。

仅恢复文件读取入口，不转移原项目授权给导入副本。UI导入建立隔离项目、confirmed=false；可显示处方和轮廓，追迹仍由现有门禁决定。原生写入→UI同步尚未补验收。用户对打开入口与XZ的反馈修复、250项回归和原生证据已完成，待规划复审与体验验收。
