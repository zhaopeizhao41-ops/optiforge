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

## 🧩 复杂结构与多镜组/模块化设计强制工作流规范 (Mandatory Multi-Group & Modular Protocol)

当设计任务涉及多镜组、中继系统（如激光扫描显微镜：振镜 + 扫描镜 + 筒镜 + 物镜，内窥镜中继系统，投影光机等）时，智能体**必须严格执行模块化解耦工作流，严禁进行“跨模块一锅端盲目优化”**：

### 1. 三大接口解耦契约 (The 3 Interface Decoupling Contracts)
- **契约一：光瞳共轭契约 (Pupil Conjugation Contract)**：
  - 振镜偏转中心（Galvo Pivot / 系统光阑）必须与显微物镜后焦面（BFP / 入瞳）**严格光学共轭**；
  - 必须通过光瞳放大率 $M_{\text{pupil}} = \frac{f_{\text{tube}}}{f_{\text{scan}}} = \frac{D_{\text{BFP}}}{D_{\text{galvo}}}$ 精准匹配口径，确保物镜后焦面达到 100% 满瞳照明（Overfill 1.0~1.1x）；
  - 严禁光瞳轴向脱节，彻底杜绝光瞳走位（Pupil Walking）引发的边缘视场剧烈渐晕与荧光衰减。
- **契约二：中间像面与双远心契约 (Intermediate Image & Double Telecentricity Contract)**：
  - **扫描透镜必须像方远心**：全视场主光线在中间像面的入射倾角（CRA）必须 $\le 0.5^\circ$；
  - **筒镜必须物方远心**：筒镜以平行主光线接收中间像面的发散光束；
  - **中间像面自洽性**：中间像面必须是一个**独立的平场、低像差像面（RMS 波前像差 $\le 0.04\lambda$）**，严禁在中间像面遗留巨大场曲/像散并指望后组反向抵消！
- **契约三：无限远准直光束契约 (Infinity Space Contract)**：
  - 筒镜出射光束必须是**绝对平行准直光（倾角 $\theta \le 0.001^\circ$）**，杜绝汇聚/发散光破坏物镜固有的球差平衡。

### 2. 标准化五阶段闭环工作流 (Standard 5-Stage Modular Workflow)
1. **Stage 1: 顶层高斯光学计算与拉格朗日不变量切分 (Paraxial Layout)**：
   - 严禁直接盲目插镜片！必须先计算物镜入瞳 $D_{\text{BFP}} = 2 \cdot f_{\text{obj}} \cdot NA$、振镜口径 $D_{\text{galvo}}$、中继放大比 $M_{\text{pupil}}$，以及独立焦距 $f_{\text{scan}}$ 与 $f_{\text{tube}}$。
2. **Stage 2: 像差预算按方和根 (RSS) 切分 (Aberration Budget Allocation)**：
   - 全系统衍射极限 $\sigma_{\text{total}} \le 0.070\lambda$ 分解为：物镜 $\le 0.045\lambda$、扫描镜 $\le 0.035\lambda$、筒镜 $\le 0.030\lambda$、装配对准 $\le 0.030\lambda$。
3. **Stage 3: 子模块独立离线自洽设计 (Offline Isolated Design)**：
   - 扫描透镜、筒镜、物镜必须在**各自独立的 `.zmx` 文件中单独优化设计**，在其各自边界契约内达到指标。
4. **Stage 4: 理想近轴透镜 (Paraxial Lens) 隔离替代测试法**：
   - 联调前必须进行近轴隔离验证：用理想近轴透镜替换其他镜组，测试待测单模块（如真实扫描镜 + 理想筒镜 + 理想物镜），100% 隔离像差来源，精准定位缺陷。
5. **Stage 5: 阶梯式四步联调释放法 (Progressive Staged Release)**：
   - 第 1 步：全系统所有透镜曲率与厚度 **100% 冻结（Fixed）**，写入操作数硬屏障；
   - 第 2 步：**仅释放模块间的机械空气间隙**，微调消除共轭位置匹配引起的初级离焦（$W_{020} \to 0$）；
   - 第 3 步：限制透镜曲率浮动在原值 $\pm 5\%$ 以内，阻尼微调平滑高级残差；
   - 第 4 步：切换 RMS Wavefront，锁定公差钝化态。

### 3. 跨模块像差防代偿红线与评价函数“铁幕硬屏障” (The Operand Barrier Matrix)
- **严禁跨模块“幽灵代偿”**：严禁后组拿负球差去抵消前组的正球差！这会导致离轴视场激发巨额三阶彗差，且使装配公差敏感度暴增数千倍。
- **评价函数必须预埋操作数硬屏障**：
  - `EFLA Surf_start Surf_end`：分别锁死扫描透镜和筒镜的独立有效焦距（Target = 标称值，Weight $\ge 100$）；
  - `REAA Surf_TL_Exit`：锁定筒镜出射边缘光线倾角为 $0.000^\circ$（Weight $\ge 1000$）；
  - `REAY Surf_Obj_Entrance`：锁定物镜入瞳光束半口径为 $D_{\text{BFP}}/2$（Weight $\ge 1000$）；
  - `RAID Surf_IIP`：锁定中间像面主光线入射角 $\le 0.5^\circ$（Weight $\ge 200$）；
  - `REAY Surf_BFP Field=Max Py=0`：锁定边缘视场主光线在物镜光瞳高度归零（消除光瞳走位，Weight $\ge 500$）；
  - **`MXCA`**：**强制单段空气间隙上限 $\le 8.0 \sim 12.0\,\text{mm}$（权重 500，红线）**，彻底扼杀优化器通过拉长轴向空气间隙偷懒消除场曲的逃逸企图；
  - **`MNEG`**：**强制玻璃边缘厚度 $ET \ge 1.2 \sim 1.5\,\text{mm}$（权重 500，红线）**，严禁刀口尖角与胶合面矢高崩穿（Sag Crossover）；
  - **`MNEA`**：**强制边缘空气净空 $\ge 0.8 \sim 1.0\,\text{mm}$（权重 200）**，预留机械隔圈支撑台阶。

### 4. DFM 面向制造与机械装配纪律 (DFM & Drop-in Assembly)
- **单镜筒深径比**：单段镜筒深径比严格控制在 $L/D \le 2.0 \sim 2.5 : 1$。总长超标时必须分段独立制造，采用精密定位止口法兰（配合间隙 $< 5\,\mu\text{m}$）螺栓对接。
- **边缘平直台阶 (Flat Land)**：透镜机械外径必须满足 $D_{\text{mech}} \ge \text{CA} + 2.0 \sim 3.0\,\text{mm}$，预留平直圆柱支撑面（宽度 $W \ge 0.8 \sim 1.5\,\text{mm}$）与 $0.3\text{mm}\times 45^\circ$ 倒角，**严禁曲面边缘与金属隔圈产生线接触**。
- **模数化外径与单向直通装配**：同一镜筒内透镜统一采用标准系列外径（如 $\Phi 16.0\text{mm}, \Phi 25.4\text{mm}, \Phi 30.0\text{mm}$），采用单向直通精密落入式装配（Drop-in Assembly），保证装配同轴度 $< 1.5\,\mu\text{m}$。

---

## 例外说明
查询当前已有设计状态与需求审查的只读工具（`zemax_system_info`, `zemax_get_system_data`, `zemax_lookup_manual`, `zemax_audit_requirements`）不受四步设计门禁限制。
详细工程手册可通过 MCP 资源 `zemax://manual/expert_design_guide` 或 `domain/optical_expert_manual.md` 随时查阅。

