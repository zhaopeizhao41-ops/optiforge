"""The workbench follows Agent work: mapping, UI fan-out, engine wiring, standalone note.

Never opens OpticStudio and never contacts a real service.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import workbench_client  # noqa: E402
from core.workbench_engine import EngineWorker  # noqa: E402
from core.workbench_follow import AUTO_PREFIX, follow_target  # noqa: E402
from core.workbench_ui import WorkbenchUIController  # noqa: E402

OK = {"status": "success"}


class FollowTargetTests(unittest.TestCase):
    def test_agent_operations_map_to_their_page(self):
        self.assertEqual(follow_target("zemax_apply_prescription", {}, OK, "succeeded", "mcp"), ("design", None))
        self.assertEqual(follow_target("zemax_run_optimization", {}, OK, "succeeded", "mcp"), ("design", None))
        self.assertEqual(follow_target("__analysis__", {}, OK, "succeeded", "mcp"),
                         ("analysis", {"tab": "overview"}))
        self.assertEqual(follow_target("zemax_confirm_design_proposal", {}, OK, "succeeded", "mcp"),
                         ("workflow", {"tab": "proposal"}))
        self.assertEqual(follow_target("zemax_design_workflow", {"action": "milestone_report"}, OK, "succeeded", "mcp"),
                         ("workflow", {"tab": "milestones"}))

    def test_user_clicks_failures_and_reads_never_move_the_page(self):
        self.assertIsNone(follow_target("__analysis__", {}, OK, "succeeded", "ui"))
        self.assertIsNone(follow_target("zemax_apply_prescription", {}, OK, "failed", "mcp"))
        self.assertIsNone(follow_target("zemax_apply_prescription", {}, {"status": "rejected"}, "succeeded", "mcp"))
        self.assertIsNone(follow_target("zemax_get_system_data", {}, OK, "succeeded", "mcp"))
        self.assertIsNone(follow_target("zemax_design_workflow", {"action": "view"}, OK, "succeeded", "mcp"))


class UIFollowTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.ui = WorkbenchUIController(clock=lambda: self.now[0], wall_clock=lambda: 1_000.0)

    def test_follow_sends_auto_commands_to_online_sessions_only(self):
        self.ui.register("session-on-design", "design")
        self.ui.register("session-on-workflow", "workflow")
        self.ui.register("session-offline-x", "workflow")
        self.ui._sessions["session-offline-x"]["last_seen"] = 0.0
        accepted = self.ui.follow("design")
        # Already on design with no focus: skipped; offline: skipped.
        self.assertEqual(len(accepted), 1)
        command = self.ui.heartbeat("session-on-workflow", "workflow")["command"]
        self.assertEqual(command["page"], "design")
        self.assertTrue(command["request_id"].startswith(AUTO_PREFIX))
        self.assertIsNone(self.ui.heartbeat("session-on-design", "design")["command"])

    def test_follow_never_stacks_on_a_pending_command(self):
        self.ui.register("session-busy-01", "design")
        self.assertEqual(len(self.ui.follow("analysis", {"tab": "overview"})), 1)
        self.assertEqual(self.ui.follow("workflow", {"tab": "milestones"}), [])

    def test_invalid_target_is_ignored(self):
        self.ui.register("session-valid-1", "design")
        self.assertEqual(self.ui.follow("spreadsheet"), [])
        self.assertEqual(self.ui.follow("analysis", {"tab": "nope"}), [])


class EngineFollowTests(unittest.TestCase):
    def test_engine_follows_agent_writes_but_not_user_reads(self):
        calls = []
        engine = EngineWorker({"zemax_apply_prescription": lambda: dict(OK),
                               "zemax_get_system_data": lambda: dict(OK)},
                              capture=lambda: {"status": "success", "model": {"surfaces": []}})
        self.addCleanup(engine.executor.shutdown)
        engine.navigator = lambda page, focus=None: calls.append((page, focus))
        context = engine.state.context()
        engine.call_envelope("zemax_get_system_data", {}, expected_context=context, source="mcp")
        engine.call_envelope("zemax_apply_prescription", {}, expected_context=engine.state.context(), source="ui")
        self.assertEqual(calls, [])
        engine.call_envelope("zemax_apply_prescription", {}, expected_context=engine.state.context(), source="mcp")
        self.assertEqual(calls, [("design", None)])

    def test_a_failing_navigator_never_changes_the_result(self):
        engine = EngineWorker({"zemax_apply_prescription": lambda: dict(OK),
                               "zemax_get_system_data": lambda: dict(OK)},
                              capture=lambda: {"status": "success", "model": {"surfaces": []}})
        self.addCleanup(engine.executor.shutdown)

        def broken(page, focus=None):
            raise RuntimeError("frontend gone")
        engine.navigator = broken
        # Writes need an observed model first, exactly as in the real engine.
        engine.call("zemax_get_system_data", {}, expected_context=engine.state.context())
        result = engine.call("zemax_apply_prescription", {}, expected_context=engine.state.context())
        self.assertEqual(result["status"], "success")


class StandaloneNoteTests(unittest.TestCase):
    def namespace(self):
        def zemax_apply_prescription():
            return {"status": "success"}

        def zemax_get_system_data():
            return {"status": "success"}
        for function in (zemax_apply_prescription, zemax_get_system_data):
            function.__module__ = "tools.fake"
        return {"_apply_prescription": zemax_apply_prescription, "_get_system_data": zemax_get_system_data}

    def test_bridge_standalone_marks_visible_tools_only(self):
        space = self.namespace()
        with patch.dict("os.environ", {"OPTIFORGE_STANDALONE_REASON": "credential file is stale"}, clear=False):
            import os
            os.environ.pop("OPTIFORGE_SERVICE_URL", None)
            workbench_client.route_tool_aliases(space)
        self.assertIn("未连接工作台", space["_apply_prescription"]()["workbench_connection"])
        self.assertIn("stale", space["_apply_prescription"]()["workbench_connection"])
        self.assertNotIn("workbench_connection", space["_get_system_data"]())

    def test_plain_server_runs_stay_silent(self):
        space = self.namespace()
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("OPTIFORGE_SERVICE_URL", None)
            os.environ.pop("OPTIFORGE_STANDALONE_REASON", None)
            workbench_client.route_tool_aliases(space)
        self.assertNotIn("workbench_connection", space["_apply_prescription"]())


if __name__ == "__main__":
    unittest.main()
