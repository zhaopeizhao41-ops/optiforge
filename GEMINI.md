# Zemax OpticStudio MCP - Workspace Operating Guidelines

## ⚠️ 强制执行五步闭环光学设计工作流 (Mandatory 5-Stage Protocol)

在当前工程中，当用户提出任何光学系统设计、镜头建模、像差优化或结构改进任务时，智能体**必须强制执行**以下五步流程，严禁跳过：

---

### Step 0: 需求完备性审查与交互追问 (Specification Completeness Audit & Requirements Refinement)
- **核心原则**：当用户提出初始需求时，往往缺少关键参数（如只说“设计一个50mm镜头”而未指定光圈、像面大小、像元尺寸、工作谱段、后截距或机械包络）。**严禁在参数模糊或缺失的情况下自行凭空盲目假设参数启动设计**！
- **强制调用审查工具**：必须首先调用 `zemax_audit_requirements`（传入当前识别到的参数或用户原始描述），核对光学系统关键参数完整度。
- **主动追问与行业默认值**：
  - 若审查结果判定为 `NEEDS_CLARIFICATION`，智能体**必须立即停下来向用户提出针对性追问**；
  - 提问必须清晰专业，并**显式附带行业经典推荐默认值 (Recommended Defaults)**（例如：F/# 默认 2.8；传感器默认 2/3" 靶面对角线 11mm；像元默认 3.45μm；工作谱段默认可见光 400-700nm）；
  - 告知用户：“若您对某项参数没有特殊限定，可回复'按推荐默认值'，我们将采用行业基准值启动设计”；
  - 与用户多轮确认，直至核心规格指标形成闭环后，方可进入 Step 1。

---

### Step 1: 联网检索成熟初始结构 (Web Search Initial Structure)
- **禁止盲目设计**：光学设计高度依赖优质初始架构，严禁随意凭空捏造曲率或盲设参数。
- **强制联网检索**：必须使用网络搜索工具（检索 USPTO、Google Patents、知网/万方、光学手册如 Smith *Modern Lens Design*, Kingslake *Lens Design Fundamentals*, Fischer *Optical System Design* 等）。
- **对齐指标**：寻找在焦距 (EFL)、F 数或数值孔径 (NA)、视场角 (FOV) 及工作波长上最为接近的经典成熟专利或文献初始结构（如 Cooke Triplet, Double Gauss, Petzval, 远摄/反远摄, 显微物镜, 扫描平场透镜等）。

---

### Step 2: 深度光学推演与专家工程规则 (Deep Optical Thinking & Engineering Principles)
- **一阶高斯光学计算**：
  - 计算入瞳直径 $EPD = EFL / F\#$
  - 拉格朗日不变量 $H = n \cdot u \cdot y$
  - 透镜组光焦度分配 $\Phi = \sum \phi_i$
- **初级与高级像差平衡分析**：
  - 球差与彗差平衡：利用带区负球差平衡边缘正球差（$W_{040} = -W_{060}$，边缘双零点平衡）；
  - 场曲与像散平衡：当 Petzval 和无法拉平时，合理引入像散分离子午/弧矢像面，使最小弥散斑落在平直传感器上。
- **工业首选玻璃与二级光谱消除**：
  - 优先从 CDGM / Schott 常用优选目录中选型（H-K9L/N-BK7, H-ZF4A/N-SF11, H-LaK53A/N-LAK9, H-ZLaF50D/N-LAF21, H-FK61/N-PK52A）；
  - 严禁在裸露外表面使用耐酸耐候性差的软玻璃；
  - 复消色差（APO）反常相对部分色散玻璃（如 H-FK61, CaF2）**必须置于轴上边缘光线高度 $y$ 最大的正透镜上**，以最大化校正力臂。
- **严格内部空气间隔与紧凑装配预算**：
  - **严禁优化器逃逸**：内部镜片间空气间隔中心厚度必须严格限制在 $t_{\text{air}} \le 12.0\,\text{mm}$（极限 $\le 15.0\,\text{mm}$），杜绝优化器通过拉大空气间隙偷懒消除 Petzval 场曲；
  - 纯镜组叠层总长 $L_{\text{barrel}} = \sum(t_g + t_a) \le 0.35 \sim 0.6 \times EFL$；镜筒深径比 $L/D \le 2.5$；
  - 空气中心间隔 $MNCA \ge 0.5\,\text{mm}$，边缘净空间隔 $MNEA \ge 0.8\,\text{mm}$（预留隔圈 Flat Land 支撑面）。
- **可制造性 (DFM) 与公差低敏感设计**：
  - 玻璃中心厚度 $CT \ge 1.0\,\text{mm}$（且 $CT / D \ge 0.08$）；
  - 玻璃边缘厚度 $ET \ge 1.2 \sim 1.5\,\text{mm}$（严格杜绝刀口 Edge Knife 与崩边）；
  - 球面检验样板拟合陡度比 $|R| \ge 1.2 \sim 1.5 \times \text{Semi-Diameter}$（杜绝超半球深凹面）；
  - 入射角钝化：单表面主光线与边缘光线最大入射角控制在 $\le 30^\circ \sim 45^\circ$ 以内（使用 `RAID` 监控），若偏折角过大必须拆分透镜（Element Splitting）。
- **非球面使用纪律**：
  - 位置：靠近光阑校正球差/彗差，远离光阑校正像散/畸变/场曲；
  - 阶数释放：仅允许释放圆锥常数 $k$、4阶项与6阶项，**严禁开放8阶以上项**，杜绝中频空间波纹振荡。
- **递进式四阶段优化路径**：
  - Stage 1: 冻结厚度，RMS Spot 搜索基础拓扑曲率；
  - Stage 2: 预埋硬边界（`MNCA`, `MXCA`, `MNEA`, `TTHI`, `MNEG`），释放厚度；
  - Stage 3: Spot 接近 $1.5\times$ 艾里斑时切换 RMS Wavefront，代换优选玻璃；
  - Stage 4: 边界权重加倍，Hammer 深度寻优与公差钝化。

---

### Step 3: 输出《光学设计提案报告》并审阅 (Proposal Review)
- 将上述调研与推导整合成结构化《光学设计与仿真提案报告》，调用 `zemax_register_design_proposal` 登记，并以 Markdown 呈现给用户：
  1. **设计需求指标拆解表**（EFL, F/#, FOV, 谱段, TOTR, WD, 像元尺寸）
  2. **专利/文献初始结构来源与拓扑结构**（组数、片数、光阑 Stop 位置）
  3. **理论光焦度分配与像差平衡策略**
  4. **拟选用玻璃材料清单及选型依据**
  5. **紧凑度与光机装配边界预算**（最大单段空气隙、叠层厚度、镜筒深径比）
  6. **评价函数控制与分阶段优化路径**
  7. **公差敏感度与工艺风险预警**

---

### Step 4: 用户决策门禁 (User Confirmation Gate)
- **强制暂停**：呈报方案后，**必须停下来明确向用户询问**：
  > *"以上设计方案已根据文献检索与深度光学推演完成。请审阅该方案，是否同意以此方案启动 Zemax 仿真与自动优化？"*
- **严禁擅自调用**：在用户明确输入“同意”、“确认”、“开始仿真”或类似确认指令之前，**严禁私自调用** `zemax_new_file`, `zemax_load_template`, `zemax_surface_operations`, `zemax_setup_merit_function`, `zemax_run_optimization`, `zemax_run_hammer`。
- 若用户提出修改意见，返回 Step 2 调整方案并重新报审。

---

## 例外说明
查询当前已有设计状态与需求审查的只读工具（`zemax_system_info`, `zemax_get_system_data`, `zemax_lookup_manual`, `zemax_audit_requirements`）不受四步设计门禁限制。
详细工程手册可通过 MCP 资源 `zemax://manual/expert_design_guide` 或 `domain/optical_expert_manual.md` 随时查阅。
