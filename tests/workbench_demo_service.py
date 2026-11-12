"""Full-workflow demo/acceptance service. SIMULATED optics ONLY; never opens Zemax.

Runs the real workflow tools, EngineWorker and HTTP server in an isolated
temporary workspace, then plays every step an Agent would take:
requirements -> audit -> research task -> proposal -> user confirmation (test
data) -> model handoff -> four milestone stages, each with a SIMULATED
optimization, analysis and geometry + quality archive.

Run: python -B tests/workbench_demo_service.py [--port 8767] [--keep] [--pause]
--pause stops before the confirmation so a person can confirm in the browser.
The user's real output/ workspace and any proposal are never touched.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import demo_optics as optics  # noqa: E402

PROJECT = "演示_cooke_triplet"
STAGES = [("stage_1", "curvatures", 15, "冻结厚度，曲率搜索基础拓扑（SIMULATED）"),
          ("stage_2", "all", 25, "释放厚度并预埋边界：玻璃 CT ≥ 1.5 mm、空气 ≥ 0.5 mm（SIMULATED）"),
          ("stage_3", "all", 15, "切换波前复核，材料维持 N-SK16 / N-F2（SIMULATED）"),
          ("stage_4", "curvatures", 10, "深度寻优收尾；公差分析未执行（SIMULATED）")]


def log(message):
    print(f"[demo] {message}", flush=True)


def require_success(result, step):
    if not isinstance(result, dict) or result.get("status") != "success":
        raise RuntimeError(f"{step} 失败：{json.dumps(result, ensure_ascii=False)[:600]}")
    return result


class Demo:
    def __init__(self, port, pause):
        from tools import project_manager as projects
        self.temp = Path(tempfile.mkdtemp(prefix="optiforge-demo-"))
        self.patches = [patch.object(projects, "OUTPUT_BASE_DIR", str(self.temp)),
                        patch.object(projects, "ACTIVE_PROJECT_FILE", str(self.temp / ".active_project.json")),
                        patch("core.zos_session.ZOSSession.get_instance",
                              side_effect=AssertionError("演示服务禁止打开原生 Zemax"))]
        for item in self.patches:
            item.start()
        projects.set_active_project(PROJECT)
        self.projects = projects
        self.model = optics.cooke_model(PROJECT)
        self.session = object()
        self.port, self.pause = port, pause
        self.confirmed = threading.Event()

    # ------------------------------------------------------------- engine
    def start_server(self):
        from core.workbench_service import EngineWorker, WorkbenchHTTPServer
        from tools.collaboration_tools import zemax_design_workflow, zemax_claim_design_task, zemax_update_design_task
        from tools.system_tools import get_current_design_proposal

        def mutate(variables="curvatures", iterations=10):
            return optics.optimize(self.model, variables, iterations)

        registry = {"zemax_design_workflow": zemax_design_workflow, "zemax_claim_design_task": zemax_claim_design_task,
                    "zemax_update_design_task": zemax_update_design_task,
                    "zemax_list_design_tasks": __import__("tools.collaboration_tools", fromlist=["x"]).zemax_list_design_tasks,
                    "zemax_get_workbench_snapshot": lambda: optics.snapshot(self.model),
                    "zemax_run_optimization": mutate, "zemax_load_file": lambda filepath: {"status": "success"},
                    "zemax_get_project": lambda: {"status": "success", "project_name": PROJECT},
                    "get_current_design_proposal": get_current_design_proposal}
        self.engine = EngineWorker(registry, metadata=lambda: {
            "active_project": self.projects.get_active_project_name(), "model_project": PROJECT,
            "proposal_confirmed": get_current_design_proposal().get("user_confirmed_to_simulate") is True,
            "session_identity": self.session}, trace=optics.trace_adapter, analysis=optics.analysis_adapter)
        self.server = WorkbenchHTTPServer(("127.0.0.1", self.port), self.engine)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True).start()
        # Credentials for an Agent's server.py (OPTIFORGE_SERVICE_URL / _FILE); temporary workspace only.
        from core.workbench_service import write_service_file
        self.service_file = self.temp / "service.json"
        write_service_file(self.server, self.service_file)
        log(f"SIMULATED 工作台：http://127.0.0.1:{self.server.server_port}/  （临时工作区 {self.temp}）")
        log(f"Agent 接入：OPTIFORGE_SERVICE_URL=http://127.0.0.1:{self.server.server_port} "
            f"OPTIFORGE_SERVICE_FILE={self.service_file}")

    def tool(self, name, arguments=None):
        envelope = self.engine.call_envelope(name, arguments or {}, expected_context=self.engine.state.context(), source="demo")
        return envelope["result"]

    def workflow(self, action="view", payload=None):
        from core import design_workflow as flow
        args = {"action": action, "project_name": PROJECT}
        if action != "view":
            args.update(payload=payload, expected_workflow=flow.view(PROJECT)["context"])
        return self.tool("zemax_design_workflow", args)

    def task(self, request_id):
        from core import design_workflow as flow
        return next(t for t in flow.task_list(PROJECT)["tasks"] if t["request_id"] == request_id)

    # ------------------------------------------------------------- steps
    def requirements(self):
        specs = {"efl_mm": "50 mm", "f_number": "4.5", "fov_or_sensor": "全视场 40 deg",
                 "wavelength_range": "486.1–656.3 nm（F-d-C）"}
        require_success(self.workflow("save", {"system_type": "imaging_lens", "specs": specs,
            "sources": {key: "user" for key in specs}, "user_prompt": "演示：经典三片式物镜（SIMULATED 软件测试数据）",
            "constraints": "演示数据，不是真实光学需求"}), "保存需求")
        audit = require_success(self.workflow("audit", {}), "需求审查")
        log(f"需求审查完成：{audit['draft']['audit']['result'].get('status')}")
        require_success(self.workflow("submit", {"request_id": "research", "kind": "initial_proposal",
            "user_text": "演示：请检索并提出初始结构（SIMULATED）"}), "提交研究任务")

    def proposal(self):
        from core import design_workflow as flow
        from tools.system_tools import zemax_register_design_proposal
        claim = require_success(self.tool("zemax_claim_design_task", {"project_name": PROJECT, "request_id": "research",
            "version": self.task("research")["version"], "worker_id": "demo-agent", "lease_seconds": 900}), "领取研究任务")
        specs = flow.view(PROJECT)["draft"]["audit"]["result"]["recognized_specs"]
        facts = optics.describe(self.model)
        result = zemax_register_design_proposal(
            project_name=PROJECT, target_specs=specs,
            initial_structure_source="演示：仓库模板 cooke_triplet（domain/design_templates.py）；Kingslake《Lens Design Fundamentals》三片式章节为参考方向。SIMULATED 数据。",
            optical_theory_analysis=f"SIMULATED 近轴：EFL {facts['efl_mm']:.2f} mm，工作 F/# {facts['working_f_number']:.2f}，入瞳位于 S1 后 {facts['entrance_pupil_z_mm']:.2f} mm。正-负-正光焦度分配，中央光阑平衡彗差与畸变。",
            glass_selection_rationale="N-SK16（nd 1.62041，νd 60.32）与 N-F2（nd 1.62005，νd 36.43）冕火石组合校正初级色差；色散取 SCHOTT 2017 目录 Sellmeier 系数。",
            merit_function_strategy="四阶段：曲率 → 释放厚度并加边界 → 波前复核 → 深度寻优；SIMULATED 阻尼最小二乘，非 Zemax 优化器。",
            mechanical_constraints="玻璃中心厚 ≥ 1.5 mm，空气间隔 ≥ 0.5 mm 且 ≤ 12 mm，后焦 30–60 mm。",
            internal_air_spacing_budget="单段空气间隔 ≤ 12 mm（演示约束）。", topology="物方 → L1 正 → L2 负（光阑后）→ L3 正 → 像面",
            risks="演示数据：无公差分析、无真实玻璃库核对；不可作为真实设计结论。", workflow_context=flow.view(PROJECT)["context"],
            task_claim={"request_id": "research", "version": claim["task"]["version"], "lease_token": claim["lease_token"]},
            structure_diagram={"schema_version": 1, "caption": "SIMULATED 演示结构示意，非等比例", "nodes": [
                {"kind": "object", "label": "无穷远物方"}, {"kind": "lens_group", "label": "L1 冕牌正透镜", "element_count": 1},
                {"kind": "lens_group", "label": "L2 火石负透镜", "element_count": 1}, {"kind": "stop", "label": "光阑"},
                {"kind": "lens_group", "label": "L3 冕牌正透镜", "element_count": 1}, {"kind": "image", "label": "像面"}]})
        require_success(result, "登记提案")
        require_success(self.tool("zemax_update_design_task", {"project_name": PROJECT, "request_id": "research",
            "version": claim["task"]["version"], "lease_token": claim["lease_token"], "status": "completed",
            "result_proposal_hash": result["proposal_hash"]}), "完成研究任务")
        log("提案已登记，等待确认")

    def confirm(self):
        if self.pause:
            log("已暂停：请在浏览器“方案审阅”中确认本版方案（仅隔离测试数据）")
            while self.task_or_none("handoff") is None:
                time.sleep(.5)
        else:
            require_success(self.workflow("confirm", {"request_id": "handoff"}), "演示确认（测试数据，非真实用户批准）")
        log("方案已确认，生成建模交接任务")

    def task_or_none(self, request_id):
        from core import design_workflow as flow
        return next((t for t in flow.task_list(PROJECT)["tasks"] if t["request_id"] == request_id), None)

    def stages(self):
        handoff = self.task("handoff")
        claim = require_success(self.tool("zemax_claim_design_task", {"project_name": PROJECT, "request_id": handoff["request_id"],
            "version": handoff["version"], "worker_id": "demo-optical-agent", "lease_seconds": 3600}), "领取建模任务")
        token = claim["lease_token"]
        self.tool("zemax_get_workbench_snapshot")
        settings = {"analyses": ["mtf", "wavefront", "spot", "ray_fan", "field_curvature"], "fields": [1, 2, 3],
                    "wavefront_size": 128, "spot_rings": 6, "max_frequency": 100, "mtf_min_at_30lp_mm": 0.3}
        baseline = self.analyze(settings)
        log(f"初始结构像质：{baseline['overall']}")
        for number, (stage, variables, iterations, note) in enumerate(STAGES, 1):
            version = self.task("handoff")["version"]
            require_success(self.workflow("milestone_start", {"request_id": "handoff", "version": version, "lease_token": token,
                "stage_id": stage, "attempt_id": f"attempt-{number}", "note": note}), f"开始阶段 {number}")
            operation = self.engine.call_envelope("zemax_run_optimization", {"variables": variables, "iterations": iterations},
                                                  expected_context=self.engine.state.context(), source="demo")
            require_success(operation["result"], f"阶段 {number} 优化")
            result = self.analyze(settings)
            reply = self.workflow("milestone_report", {"request_id": "handoff", "version": self.task("handoff")["version"],
                "lease_token": token, "attempt_id": f"attempt-{number}", "status": "needs_review",
                "operation_ids": [operation["operation_id"]], "archive_geometry": True,
                "quality_result_id": result["result_id"],
                "note": f"{operation['result']['message']}；像质 {result['overall']}（SIMULATED，待人工核对）"})
            require_success(reply, f"报告阶段 {number}")
            log(f"阶段 {number} 完成：{operation['result']['message']}，像质 {result['overall']}")
        require_success(self.tool("zemax_update_design_task", {"project_name": PROJECT, "request_id": "handoff",
            "version": self.task("handoff")["version"], "lease_token": token, "status": "progress",
            "result_text": "四阶段 SIMULATED 演示完成，等待人工核对；公差分析未执行。"}), "汇报进度")

    def analyze(self, settings):
        accepted = self.engine.request_analysis(settings, expected_context=self.engine.state.context(), source="demo")
        if accepted.get("status") != "accepted":
            raise RuntimeError(f"分析请求被拒绝：{accepted}")
        reply = self.engine.analysis_pending[accepted["key"]][1].result(timeout=120)
        result = reply["result"]
        if result.get("status") not in {"success", "partial"}:
            raise RuntimeError(f"分析失败：{result}")
        return result

    def close(self, keep):
        for item in reversed(self.patches):
            item.stop()
        if not keep:
            shutil.rmtree(self.temp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--pause", action="store_true", help="在确认前暂停，由浏览器确认")
    parser.add_argument("--exit", action="store_true", help="流程结束后退出（自动化验收）")
    args = parser.parse_args()
    demo = Demo(args.port, args.pause)
    try:
        demo.start_server()
        demo.requirements()
        demo.proposal()
        demo.confirm()
        demo.stages()
        log("全流程完成。浏览器打开上面的地址查看；Ctrl+C 退出。")
        if not args.exit:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        demo.server.shutdown()
        demo.engine.executor.shutdown(wait=False)
        demo.close(args.keep)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
