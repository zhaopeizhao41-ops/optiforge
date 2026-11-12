"""Agent-facing workbench views, read-only control access, prompts and host configs.

Runs the SIMULATED demo service in a temporary workspace; never opens Zemax.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import connect_agent  # noqa: E402
import server  # noqa: E402
import workbench_demo_service as demo_service  # noqa: E402
from core.workbench_control import WorkbenchControlClient, WorkbenchControlError  # noqa: E402
from core.workbench_service import write_service_file  # noqa: E402
from core.workbench_view import ViewError, render  # noqa: E402
from mcp.server.mcpserver import Image  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


class WorkbenchViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = demo_service.Demo(0, pause=False)
        cls.demo.start_server()
        cls.demo.requirements()
        cls.demo.proposal()
        cls.demo.confirm()
        cls.demo.stages()
        engine = cls.demo.engine
        accepted = engine.request_trace({"fields": [1, 2, 3], "pupil_count": 5}, expected_context=engine.state.context())
        engine.trace_pending[(accepted["key"], engine.state.read()["state_version"])][1].result(timeout=60)
        cls.url = f"http://127.0.0.1:{cls.demo.server.server_port}"
        cls.instance = engine.state.context()["service_instance_id"]
        cls.client = WorkbenchControlClient(cls.url, {"token": cls.demo.server.token,
                                                      "service_instance_id": cls.instance, "protocol_version": 2})
        cls.credentials = Path(tempfile.mkdtemp(prefix="view-credentials-")) / "service.json"
        write_service_file(cls.demo.server, cls.credentials)

    @classmethod
    def tearDownClass(cls):
        cls.demo.server.shutdown()
        cls.demo.server.server_close()
        cls.demo.engine.executor.shutdown()
        cls.demo.close(keep=False)

    def test_layout_view_has_lens_rays_and_link(self):
        summary, image = render(self.client, self.instance, "layout")
        self.assertTrue(image.startswith(PNG))
        self.assertEqual(summary["workbench_url"], self.url + "/")
        self.assertEqual(summary["model"]["name"], "Cooke 三片式 · SIMULATED")
        self.assertEqual(summary["trace"]["fields"], [1, 2, 3])
        self.assertIn("不代表像质验收", summary["note"])

    def test_stages_view_reads_all_archives_with_original_verdicts(self):
        summary, image = render(self.client, self.instance, "stages")
        self.assertTrue(image.startswith(PNG))
        stages = summary["stages"]
        self.assertEqual([row["number"] for row in stages], [1, 2, 3, 4])
        for row in stages:
            self.assertEqual(row["errors"], [])
            self.assertEqual(row["metrics"]["fields"], [1, 2, 3])
            self.assertIn("SIMULATED", row["metrics"]["engine"])
            self.assertEqual(next(v for v in row["verdicts"] if v["analysis"] == "spot")["verdict"], "INFO")
        self.assertLess(stages[-1]["metrics"]["worst_spot_rms_um"], stages[0]["metrics"]["worst_spot_rms_um"])

    def test_analysis_view_marks_current_result(self):
        summary, image = render(self.client, self.instance, "analysis")
        self.assertTrue(image.startswith(PNG))
        self.assertTrue(summary["current"])
        self.assertIn(summary["overall"], {"PASS", "WARN", "FAIL", "ERROR", "INFO"})
        self.assertEqual(summary["metrics"]["mtf_threshold"], 0.3)

    def test_views_do_not_queue_native_work_or_change_state(self):
        before = self.demo.engine.state.read()
        seen = {task["operation_id"] for task in before["tasks"]["recent"]}
        for view in ("layout", "stages", "analysis"):
            render(self.client, self.instance, view)
        after = self.demo.engine.state.read()
        self.assertEqual(after["state_version"], before["state_version"])
        self.assertEqual(after["model_revision"], before["model_revision"])
        new = [task["tool"] for task in after["tasks"]["recent"] if task["operation_id"] not in seen]
        self.assertTrue(new and all(tool == "zemax_design_workflow" for tool in new), new)
        self.assertEqual(after["tasks"]["pending"], [])
        self.assertEqual(after["trace_summary"], before["trace_summary"])
        self.assertEqual(after["analysis_summary"], before["analysis_summary"])

    def test_read_only_whitelist_and_identity_checks(self):
        for action in ("confirm", "save", "milestone_report", "submit"):
            with self.assertRaises(WorkbenchControlError):
                self.client.read_tool(demo_service.PROJECT, action, {})
        for path in ("/api/refresh", "/api/analyses/../state", "/api/session", "/api/tool"):
            with self.assertRaises(WorkbenchControlError):
                self.client.read_json(path)
        with self.assertRaises(ViewError) as wrong:
            render(self.client, "another-instance", "layout")
        self.assertEqual(wrong.exception.code, "WORKBENCH_INSTANCE_MISMATCH")
        with self.assertRaises(ViewError) as project:
            render(self.client, self.instance, "stages", "other_project")
        self.assertEqual(project.exception.code, "WORKFLOW_PROJECT_MISMATCH")
        with self.assertRaises(ViewError):
            render(self.client, self.instance, "spreadsheet")

    def test_mcp_tool_returns_text_and_png_content(self):
        environment = {"OPTIFORGE_SERVICE_URL": self.url, "OPTIFORGE_SERVICE_FILE": str(self.credentials)}
        with patch.dict(os.environ, environment):
            started = time.monotonic()
            result = server.optiforge_workbench_view(self.instance, "stages")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(len(result), 2)
        self.assertEqual(json.loads(result[0])["status"], "success")
        self.assertIsInstance(result[1], Image)
        self.assertEqual(result[1].to_image_content().mime_type, "image/png")
        self.assertNotIn(self.demo.server.token, result[0])
        with patch.dict(os.environ, environment):
            error = server.optiforge_workbench_view("another-instance", "layout")
        self.assertEqual(len(error), 1)
        self.assertEqual(json.loads(error[0])["code"], "WORKBENCH_INSTANCE_MISMATCH")
        with patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": "", "OPTIFORGE_SERVICE_FILE": ""}):
            missing = server.optiforge_workbench_view(self.instance)
        self.assertEqual(json.loads(missing[0])["code"], "WORKBENCH_CONFIG_MISSING")


class PromptAndConfigTests(unittest.TestCase):
    def test_prompts_name_the_read_only_and_claim_tools(self):
        show = server.optiforge_show_workbench_prompt("stages")
        self.assertIn("optiforge_workbench_view", show)
        self.assertIn('view="stages"', show)
        pick = server.optiforge_pick_up_task_prompt()
        for tool in ("optiforge_workbench_wait_for_task", "zemax_claim_design_task", "zemax_register_design_proposal"):
            self.assertIn(tool, pick)
        self.assertIn("never confirm a proposal yourself", pick)

    def test_every_host_config_parses_and_carries_no_token(self):
        url = "http://127.0.0.1:8765"
        for host in connect_agent.HOSTS:
            where, text = connect_agent.render(host, url, python="python")
            self.assertTrue(where)
            self.assertNotIn("token", text.lower())
            self.assertIn("server.py", text)
            self.assertIn("OPTIFORGE_SERVICE_FILE", text)
        vscode = json.loads(connect_agent.render("vscode", url)[1])
        self.assertEqual(vscode["servers"]["optiforge"]["type"], "stdio")
        desktop = json.loads(connect_agent.render("claude-desktop", url)[1])
        self.assertEqual(desktop["mcpServers"]["optiforge"]["env"]["OPTIFORGE_SERVICE_URL"], url)
        codex = tomllib.loads(connect_agent.render("codex", url, python="C:\\Python\\python.exe")[1])
        self.assertEqual(codex["mcp_servers"]["optiforge"]["command"], "C:\\Python\\python.exe")
        self.assertEqual(codex["mcp_servers"]["optiforge"]["env"]["OPTIFORGE_SERVICE_URL"], url)
        command = connect_agent.render("claude-code", url)[1]
        self.assertTrue(command.startswith("claude mcp add optiforge --scope user --env "))
        self.assertIn(" -- ", command)


if __name__ == "__main__":
    unittest.main()
