# OptiForge 开源项目采用与参考计划

核对日期：2026-10-27。当前仅记录候选及采用方式，尚未安装新依赖、复制第三方代码或集成独立内核。项目自身的需求门禁、制造规则和工程流程继续保留。

## 阶段一拟采用

### 官方 MCP Python SDK

- 来源：https://github.com/modelcontextprotocol/python-sdk
- 许可：MIT。
- 用途：继续提供外部 Agent 工具、资源、提示及传输接入。
- 采用方式：包依赖和适配层。保持 `zemax_*` 工具接口兼容，逐步将业务执行交给共享本地服务。
- 现状：源码导入 `mcp.server.mcpserver.MCPServer`，当前机器安装 `mcp==2.2.0`；requirements 却列出 `fastmcp>=0.1.0`。阶段一须验证并明确锁定真实使用的 SDK，不能仅依赖机器上偶然安装的包。

### FastAPI

- 来源：https://github.com/fastapi/fastapi
- 许可：MIT。
- 用途：本地工程服务的状态接口和任务事件接口，供 UI 读取同一模型与计算状态。
- 采用方式：包依赖；工具业务逻辑保留在 OptiForge，避免经 HTTP 重新实现一套绕过门禁的业务流程。
- 初期默认本机访问；长期计算由统一执行队列持有，不能在每个 HTTP 请求中创建新的 Zemax 模型。

### React 与 Vite

- 来源：https://github.com/react/react 、https://github.com/vitejs/vite
- 许可：均为 MIT。
- 用途：本地可视化工作台与前端构建。
- 采用方式：标准依赖和自主界面实现，不引入完整第三方聊天产品或复制其他应用界面。
- 界面展示工程数据、图形和任务状态；自然语言对话继续在外部 Agent 应用进行。

### Apache ECharts

- 来源：https://github.com/apache/echarts
- 许可：Apache-2.0。
- 用途：显示 MTF、优化历史及适合交互呈现的分析序列；光路和二维几何可由 SVG 等原生绘图实现。
- 采用方式：前端依赖。图表必须注明单位、采样条件、引擎和模型修订，展示真实分析数据。

### Three.js（可选）

- 来源：https://github.com/mrdoob/three.js
- 许可：MIT。
- 用途：后续三维镜片、光线和包络查看。
- 采用方式：按需求增加依赖。显示几何或示意线不构成真实光线追迹，不能替代数值计算。

## 阶段二优先验证

### Optiland

- 来源：https://github.com/optiland/optiland
- 许可：MIT。
- 用途：独立序列追迹、分析、优化和 ZMX 互操作的首选候选；NumPy/PyTorch 能力按基准验证后启用。
- 采用方式：优先固定版本的包依赖，通过 `OptilandBackend` 适配，不将其内部对象变成整个应用的公共数据模型。
- 验收边界：项目验证页只覆盖有限系统，仍有多色 MTF 等未决差异，必须在 OptiForge 的基准集上复验。
- 参考：https://optiland.readthedocs.io/en/latest/validation.html

### RayOptics

- 来源：https://github.com/mjhoptics/ray-optics
- 许可：BSD-3-Clause。
- 用途：几何光学、近轴计算及 Zemax 导入行为的参考，也可用于部分指标的独立交叉核验。
- 采用方式：先作为开发期参考或测试依赖，不默认成为生产中的第二套主内核。
- 限制：多个开源实现结果一致不能单独证明物理正确，仍需解析解、明确基准和目标工具对照。

## 后续研究候选

### DeepLens

- 来源：https://github.com/vccimaging/DeepLens
- 许可：Apache-2.0。
- 用途：研究可微追迹、光学与成像算法联合优化。
- 采用方式：独立实验后评估算法或可选后端，不与阶段二主内核同时强行集成。
- DeepLens 将自动镜头设计研究引向 AutoLens；AutoLens 须单独核对具体版本、许可、数据和模型来源后再决定是否采用。

### REA

REA 作为需要时使用的开发分析工具，帮助研究接口、文件结构和有明确目标的行为差异。它不作为产品运行时的光学计算依赖，也不作为恢复完整商业光学内核的承诺。本轮未启动 Zemax 二进制分析。

## 第三方资产记录方式

正式采用时记录实际版本或提交、许可证、保留的版权声明、修改范围和验证结果。代码包许可与玻璃目录、镜头处方、专利内容、样例数据、模型权重的使用条件分别核对，不能混为一项授权。

优先使用公开接口和依赖适配。确有必要化用代码时保留来源及对应声明，避免无来源地搬运实现。只加入当前阶段需要且已验证的依赖。
