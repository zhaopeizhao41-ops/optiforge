"""Deep-link focus on navigation commands: schema, honest receipts, MCP client boundary."""
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_control import WorkbenchControlClient, WorkbenchControlError  # noqa: E402
from core.workbench_ui import WorkbenchUIController, WorkbenchUIError, valid_focus  # noqa: E402


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class FocusSchemaTests(unittest.TestCase):
    def test_page_specific_focus_shapes(self):
        valid = [("design", {"surface": 0}), ("design", {"surface": 4}),
                 ("analysis", {"tab": "overview"}), ("analysis", {"tab": "spot", "field": 2}),
                 ("workflow", {"tab": "proposal"}), ("workflow", {"tab": "milestones", "stage": "stage_4"}),
                 ("design", None), ("analysis", None)]
        for page, focus in valid:
            self.assertTrue(valid_focus(page, focus), (page, focus))
        invalid = [("design", {}), ("design", {"surface": -1}), ("design", {"surface": True}),
                   ("design", {"surface": 1.0}), ("design", {"surface": 1, "tab": "mtf"}),
                   ("analysis", {"field": 2}), ("analysis", {"tab": "zernike"}), ("analysis", {"tab": "mtf", "field": 0}),
                   ("workflow", {"tab": "tasks", "stage": "stage_1"}), ("workflow", {"tab": "milestones", "stage": "stage_5"}),
                   ("workflow", {"stage": "stage_1"}), ("design", ["surface", 1]), ("design", "S1"), ("unknown", {"tab": "mtf"})]
        for page, focus in invalid:
            self.assertFalse(valid_focus(page, focus), (page, focus))


class FocusCommandTests(unittest.TestCase):
    def setUp(self):
        self.ui = WorkbenchUIController(clock=Clock())
        self.session = "ui_session_0001"
        self.ui.register(self.session, "design")

    def command(self, page="analysis", focus=None, request="request_0001"):
        result = self.ui.navigate(self.session, page, request, focus)
        self.assertEqual(result["focus"], focus)
        pending = self.ui.heartbeat(self.session, "design")["command"]
        self.assertEqual(pending["focus"], focus)
        return result["command_id"]

    def code(self, function, *args):
        with self.assertRaises(WorkbenchUIError) as raised:
            function(*args)
        return raised.exception.code

    def test_full_focus_receipt(self):
        command = self.command(focus={"tab": "spot", "field": 2})
        receipt = self.ui.ack(self.session, command, "applied", "analysis", None, {"tab": "spot", "field": 2})["receipt"]
        self.assertEqual((receipt["status"], receipt["actual_focus"], receipt["reason"]),
                         ("applied", {"tab": "spot", "field": 2}, None))
        self.assertEqual(self.ui.status()["commands"][0]["focus"], {"tab": "spot", "field": 2})

    def test_partial_focus_needs_a_reason_and_cannot_substitute(self):
        command = self.command(focus={"tab": "mtf", "field": 9})
        self.assertEqual(self.code(self.ui.ack, self.session, command, "applied", "analysis", None, {"tab": "mtf"}), "FOCUS_MISMATCH")
        self.assertEqual(self.code(self.ui.ack, self.session, command, "applied", "analysis", "x", {"tab": "mtf", "field": 3}),
                         "FOCUS_MISMATCH")
        self.assertEqual(self.code(self.ui.ack, self.session, command, "applied", "analysis", "x", {"tab": "spot"}), "FOCUS_MISMATCH")
        receipt = self.ui.ack(self.session, command, "applied", "analysis", "FOCUS_PARTIAL: 当前结果不含视场 F9", {"tab": "mtf"})["receipt"]
        self.assertEqual(receipt["actual_focus"], {"tab": "mtf"})
        # Idempotent replay must repeat the same focus.
        self.assertTrue(self.ui.ack(self.session, command, "applied", "analysis", "FOCUS_PARTIAL: 当前结果不含视场 F9",
                                    {"tab": "mtf"})["idempotent"])
        self.assertEqual(self.code(self.ui.ack, self.session, command, "applied", "analysis", None, None), "COMMAND_ALREADY_SETTLED")

    def test_unavailable_target_reports_none_with_reason(self):
        command = self.command("design", {"surface": 99})
        receipt = self.ui.ack(self.session, command, "applied", "design", "FOCUS_UNAVAILABLE: 当前模型没有 S99", None)["receipt"]
        self.assertIsNone(receipt["actual_focus"])

    def test_focus_free_commands_keep_old_behaviour(self):
        command = self.command()
        receipt = self.ui.ack(self.session, command, "applied", "analysis")["receipt"]
        self.assertIsNone(receipt["focus"])
        self.assertIsNone(receipt["actual_focus"])
        other = self.command("design", request="request_0002")
        self.assertEqual(self.code(self.ui.ack, self.session, other, "applied", "design", None, {"surface": 1}), "FOCUS_MISMATCH")

    def test_rejected_ack_cannot_claim_focus_and_bad_focus_is_refused(self):
        command = self.command(focus={"tab": "spot"})
        self.assertEqual(self.code(self.ui.ack, self.session, command, "rejected", "design", "x", {"surface": 1}), "INVALID_FOCUS")
        self.assertEqual(self.code(self.ui.ack, self.session, command, "applied", "analysis", None, {"surface": 1}), "INVALID_FOCUS")
        self.assertEqual(self.code(self.ui.navigate, self.session, "design", "request_0009", {"tab": "mtf"}), "INVALID_FOCUS")

    def test_request_id_bound_to_its_focus(self):
        self.command(focus={"tab": "spot"})
        self.assertEqual(self.ui.navigate(self.session, "analysis", "request_0001", {"tab": "spot"})["idempotent"], True)
        self.assertEqual(self.code(self.ui.navigate, self.session, "analysis", "request_0001", {"tab": "mtf"}),
                         "REQUEST_ID_CONFLICT")


class ClientBoundaryTests(unittest.TestCase):
    def test_client_rejects_bad_focus_before_any_http(self):
        client = WorkbenchControlClient("http://127.0.0.1:9", {"token": "t", "service_instance_id": "i", "protocol_version": 2})
        with self.assertRaises(WorkbenchControlError) as raised:
            client.navigate("i", "ui_session_0001", "design", "request_0001", {"surface": "S1"})
        self.assertEqual(raised.exception.code, "INVALID_FOCUS")


if __name__ == "__main__":
    unittest.main()
