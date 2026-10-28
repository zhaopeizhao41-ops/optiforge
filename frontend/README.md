# OptiForge 本地可视化工作台

自然语言对话在外部 Agent 中进行，工作台显示共享本地服务中的当前模型。

## 启动

在项目根目录执行：

```powershell
cd frontend
npm ci
npm run build
cd ..
python -B workbench.py --model "<已有 .zmx/.zos 文件路径>"
```

打开 <http://127.0.0.1:8765/>。省略 `--model` 可先进入工作台再打开模型。也可运行根目录的 `start-workbench.ps1` 构建并启动。

## Agent 接入

先启动工作台服务，再让 MCP 客户端运行同一仓库的 `server.py`，只在该 MCP 子进程中设置 `OPTIFORGE_SERVICE_URL=http://127.0.0.1:8765`。桥接从本仓库 `output/workbench_service/service.json` 读取连接信息；服务和客户端必须使用匹配的协议版本。不要把该环境变量设置到工作台启动进程。

## 功能与范围

- 主页面显示二维轮廓及表面半径、厚度、材料；更多属性提供面型、求解、配置、孔径、视场、波长和会话来源。
- 开启“显示光线”后选择实际视场、波长和 3/5/9 条光瞳采样，显示从 S1 至像面的非偏振几何实光线。追迹需要 OpticStudio API 许可和当前项目已确认的提案。
- 画布支持滚轮缩放、左键拖动和一键复位；支持深浅主题及减少动效。
- 轮廓适配限定范围的同轴 Sequential Standard 系统；未知面型、特殊坐标、孔径或边缘不会猜测绘制。不支持的追迹模式会明确返回原因。
- 导入文件保存为独立本地工作副本。Agent 经同一服务完成的操作按模型版本更新显示；不监听原磁盘文件，也不接管其他 Zemax 窗口。断连和过期结果明确标示。
- UI 参数为只读，分析页尚未接入真实像质计算。二维显示不构成制造图纸或像质验收。

## 开发与测试

- `npm run dev`：启动5173开发服务，`/api`代理到8765工作台。
- `npm test`：执行前端离线回归测试。
- `npm run build`：构建生产静态资源并生成第三方许可说明。
- `npm run preview`：4173静态资源预览，不提供工程API。

React、Motion、Lucide React和Vite的固定版本见 `package-lock.json`；许可说明保留在 `THIRD_PARTY_NOTICES.md`。
