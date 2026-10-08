---
name: design-verification-workflow
description: Mandatory verification steps after building any optical design to prevent "builds but doesn't work" failures
metadata:
  type: project
---

# 设计验证工作流程

**目的**: 防止"设计脚本运行成功，但生成的 .zmx 文件在 Zemax 里打不开或光线追迹失败"的情况。

## 失败案例：Czerny-Turner 光谱仪（2026-10-08）

**症状**: 
- 设计脚本报告所有阶段 ✓ 成功
- `.zmx` 文件已保存
- 但用户在 Zemax 三维布局图里看到：曲面镜没有显示，光线是直线，镜面没被照亮

**根本原因**:
1. **CoordinateBreak 误用**: 只设置了 `tilt_x: 12°`（旋转坐标系），但**没有 decenter**（物理位移）。结果：坐标系旋转了，但光线仍然沿原光轴传播，根本打不到应该离轴的镜面位置。
2. **缺少验证步骤**: 设计脚本结束时没有检查：
   - 光线追迹状态（vignetted? blocked?）
   - 每个表面是否被光线照亮
   - 表面 semi-diameter 是否为 0（会阻挡光线）
3. **反射型 Czerny-Turner 的建模复杂性被低估**: 离轴镜面 + 折叠光路需要正确的 decenter + tilt 组合，不能只用简单的 tilt。

**为什么脚本报告"成功"但设计不能用**:
- ZOS-API 调用（`zemax_set_surface_type`, `zemax_surface_operations` 等）都返回 `status: success`，因为**表面参数确实被写入了 LDE**
- 但这些参数的**物理组合**导致光路不通，而工具层没有检查光学可行性

## 强制验证清单

**每个设计脚本在 `zemax_save_file()` 之前必须调用**:

```python
from core.design_verification import verify_design_usability, suggest_fixes
import json

# ... build design with zemax_surface_operations etc ...

# MANDATORY verification before save
verification = verify_design_usability()
print(f"\n[VERIFICATION]\n{json.dumps(verification, indent=2, ensure_ascii=False)}")

if verification.get("status") == "error":
    fixes = suggest_fixes(verification)
    print("\nSuggested fixes:")
    for fix in fixes:
        print(f"  - {fix}")
    sys.exit(1)  # Do NOT save unusable design

if verification.get("warnings"):
    print("\nWarnings (design may still work):")
    for w in verification["warnings"]:
        print(f"  - {w}")

# Only save if verification passed
zemax_save_file()
```

**检查项** (`verify_design_usability()` 实现在 `core/design_verification.py`):
1. ✓ 所有光学表面（非 OBJ/IMA）的 `semi_diameter > 0.01`
2. ✓ 注释里含 "mirror" 的表面，Material 应为 `MIRROR`
3. ✓ CoordinateBreak 表面至少有一个非零的 decenter 或 tilt 参数
4. ✓ 系统光阑已设置（EPD, F/#, etc.）
5. ✓ 至少定义了一个波长
6. ✓ Image 表面的 thickness = 0

**推荐但非强制**:
- 调用 `zemax_run_spot_diagram()` 检查是否有光线到达像面
- 读取系统数据，确认 EFL 或 back focal length 不是 `None` 或 `Infinity`

## CoordinateBreak 的正确使用

**错误** (只旋转坐标系，光线不偏离):
```python
zemax_set_surface_type(surface_index=1, surface_type="CoordinateBreak")
zemax_set_surface_params(surface_index=1, params={"tilt_x": 12.0})  # ✗ 光线仍在原光轴上
```

**正确** (物理偏移 + 旋转，光线打到离轴镜面):
```python
zemax_set_surface_type(surface_index=1, surface_type="CoordinateBreak")
zemax_set_surface_params(surface_index=1, params={
    "decenter_x": 50.0,   # ✓ 将后续表面物理移动 50mm
    "tilt_x": 12.0,       # ✓ 然后旋转 12°
    "order": 0            # Decenter then tilt
})
```

**或者使用 Zemax 的 fold mirror 工具**:
```python
# 自动插入 CB-before + MIRROR + CB-after，几何关系正确
zemax_add_fold_mirror(surface_index=N, reflect_angle_deg=90.0, axis="x")
```

## 反射型光谱仪的建模策略

**推荐顺序**:
1. **先建透射型原型**（用透镜代替镜面），验证光路和波长设置正确
2. **检查透射型的光线追迹**（spot diagram, layout）
3. **再转换为反射型**（替换透镜 → 镜面 + 折叠）

**不要**直接上来就建复杂的离轴反射系统，除非你已经验证过相同拓扑的设计。

## 工具层改进（已完成）

- ✓ 创建 `core/design_verification.py`，提供 `verify_design_usability()` 和 `suggest_fixes()`
- ✓ 文档化 CoordinateBreak 的 decenter 参数（之前只提到了 tilt）
- □ (TODO) 在 `zemax_save_file()` 内部自动调用验证，若失败则返回 error 而不是 success

## Why 这很重要

**代价**:
- 用户在 Zemax 里打开文件，发现不能用，**信任度下降**
- 需要手动调试 LDE，或者重新运行脚本
- 如果设计用于自动化流程（CI/CD, batch processing），不能用的文件会导致流程中断

**收益**:
- 在脚本层面就发现问题，立即给出 actionable fix
- 用户打开的 `.zmx` 文件**保证能追迹光线**
- 减少来回调试次数

**How to apply**:
每次写新的设计脚本（特别是光谱仪、离轴系统、折叠光路），在 save 前调用 `verify_design_usability()`。如果返回 error，**不要保存**，而是打印 issues 和 suggested fixes，让脚本以非零退出码结束。
