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
