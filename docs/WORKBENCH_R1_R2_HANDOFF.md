# R1/R2 修复交接记录

日期：2026-10-28。状态：实现与开发验证完成，待规划聊天复审和用户验收。

## 依据与范围

- 执行计划：`D:\mcp gemini zemax\plan.md`，本轮读取的 SHA-256 为 `6589bde426ece940a3a4e25edb0376fb391848d5af10d984afc2458dba09902a`；该文件由规划聊天维护，本轮未修改。
- 实现工作区：`C:\Users\Kernel\.codex\worktrees\optiforge-visual-workbench\mcp gemini zemax`。
- 分支：`codex/visual-workbench-stage-one`；修复基线：`c18c2138ac239c502f5fa0aa825b9bd6511224c4`。
- 生产代码仅涉及 `core/workbench_client.py` 和 `core/workbench_snapshot.py`，测试为 `tests/test_workbench.py`；另更新本交接记录及只读验收文档。
- 保留前端布局、MCP 工具名称与参数、光学设计门禁及原工作区。成果只保存本地，不 push、建远程 PR 或部署。

## R1：无穷远参数与错误分类

发送前递归编码浮点正负无穷为 `"Infinity"` / `"-Infinity"`，保持严格 JSON；有限数、普通字符串、布尔及空值保留原意，源参数不变。NaN 明确拒绝，不复用会把它变成 null 的快照编码逻辑。

接收端核对结果：`zemax_surface_operations` 的既有参数校验仅允许物面厚度为无穷远，写入前用 `float(thickness)` 恢复；`zemax_apply_prescription` 的嵌套 thickness 同样通过 `float()` 规范化，radius 经 `_coerce_radius()` 处理。桥接层不全局转换字符串，不修改这些接收端规则。

请求结构或编码错误返回 `INVALID_TOOL_ARGUMENTS`；连接与本地服务凭据错误继续返回 `WORKBENCH_UNAVAILABLE`。服务端已有校验错误和 `PROPOSAL_NOT_CONFIRMED` 保持原样。

测试实际启动 `server.py` 并完成 stdio MCP 握手，使用临时 HTTP 模拟引擎接收有限物距及正负无穷远；字符串 comment 保持文本，NaN 不到达引擎。另以真实工具包装器验证有限值校验及未确认提案门禁，并断言原生会话入口未被调用。嵌套处方的编码通过 HTTP echo 验证；未在真实模型中执行批量处方写入。

## R2：独立净空检查

在玻璃填充前，对相邻有效边界检查 `gap(y) = T + sag_back(y) - sag_front(y)`，使用共同有效半口径。中心与端点之外，有限非零同号半径、不同圆锥系数时还检查候选驻点：

```text
y² = (R_back² - R_front²) / (k_back - k_front)
```

实现先缩放半径再计算，避免直接平方溢出；只取共同口径内的实数候选。长度容差为 `1e-10 * max(共同半口径, abs(T))`，随单位同比缩放。空气净空小于负容差时拒绝；玻璃厚度不大于容差时拒绝。失败会清空 `layout.surfaces/elements`，保留 `model.surfaces`，不修正处方。

两侧均为空气的 Standard 行作为虚拟参考面/光阑保留显示，检查跨过它并累计间距，避免虚拟面误报或掩盖碰撞。共享胶合界面和同形零空气接触仍可显示。

独立球面公式确认计划中的 S2→S3 边缘净空为 `-1.170166537925831 mm`，修复后拒绝；改为 2 mm 空气隙后正常。内部驻点测试使用 R=20、k=-1 与 R=21、k=3，中心间距为 `0.00625 - 1e-8 mm`：97 个绘图采样位置均为正净空，但 `y=sqrt(10.25)` 的真实净空为 `-1e-8 mm`，修复后正确拒绝。

## 验证与证据

```powershell
python -B tests/test_workbench.py
python -B tests/regression_stage_two.py
python -B tests/regression_stage_three.py
git diff --check
python -B tests/verify_workbench_native.py --source 'D:\mcp gemini zemax\output\achromat_doublet_optimized.zmx'
```

三套离线回归分别通过 31、33、33 项，共 97 项；相比基线新增 10 项。差异空白检查通过。前端未变，本轮未重新构建或执行浏览器交互验收。

更新后的服务在 <http://127.0.0.1:8765/> 加载验收副本，原生只读复验通过 10 项检查。记录时间为 `2026-10-28T02:19:22.919439+00:00`，模型为 `achromat_doublet_optimized`，6 面、mm，修订号 `bb81b0b35d0bdc7b`。原生属性、视场、波长、设置与快照一致，轮廓点符合处方，实际 stdio MCP 共用同一模型，提案仍未授权。原文件与副本 SHA-256 均为 `074036cc30e712bf748c3036cb0ef2dd261d4fa2ef58a923f8f13428f7730dd6`，读取前后不变。

原生报告位于本工作区 `output/workbench_acceptance/reports/native-acceptance.json`，另有 `snapshot.json` 与 `mcp-stdio.log`；这些本地运行产物由 Git 忽略，不包含服务 token。运行环境为 Python 3.12.10、mcp 2.2.0、pythonnet 3.1.0、OpticStudio 2024 R1。

## 复审边界与遗留项

本轮未发现需要改变方案的新问题，也未扩展计划范围。R2 仍限于同轴 Standard 圆锥面在有效口径内的二维几何，不构成机械包络或可制造性验收。真实验证仅为只读，未执行原生建模、优化、追迹或像质分析；未逐个验证外部 Agent 客户端。

本记录供规划聊天复审 R1/R2，尚不代表其审查通过或用户验收通过。自动同步、断连恢复、真实光线、分析与独立内核继续等待后续具体计划。
