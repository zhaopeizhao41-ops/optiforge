# OptiForge 本地可视化工作台

真实工作台从本地工程服务打开：<http://127.0.0.1:8765/>。自然语言对话继续在外部 Agent 中进行。

```powershell
cd frontend
npm ci
npm run build
cd ..
python -B workbench.py
```

在页面点“打开模型”读取本地 `.zmx/.zos` 副本，或在启动时传入 `--model <已有文件>`。根目录的 `start-workbench.ps1` 可构建并启动。完整连接配置、验收与支持范围见 [第一批验收说明](../docs/WORKBENCH_READONLY_ACCEPTANCE.md)。

## 当前功能

主页面显示真实处方生成的二维轮廓，以及选中表面的半径、厚度、材料；更多属性包含面型、光阑、视场、波长、单位和求解。当前支持限定范围的同轴 Standard 面型，其他情况明确提示。刷新按钮手动读取服务的当前模型，自动同步留待下一批。

中性白与石墨灰双主题、按钮与页面动效、减少动态效果选项沿用已验收 UI。此批不绘制光线，分析页等待真实计算接入。

## 开发和演示

- `npm run dev`：5173 开发入口，`/api` 代理到 8765 工程服务。
- `npm run preview`：4173 纯静态构建预览，不提供 API。
- URL 加 `?preview`：保留的 UI 演示，包括样例光线、分析和同步动效；所有数据均为示例。
- `npm run preview:inline`：将演示打包为聊天内交互预览，不产生另一套页面代码。

## 开源依赖

React 19.3.1（MIT）、Motion 14.6.0（MIT）、Lucide React 1.62.0（ISC）、Vite 8.3.5（MIT）。完整版本见 `package-lock.json`，运行依赖许可由构建脚本保存到 `THIRD_PARTY_NOTICES.md`。
