"""Bounded native optimization: deadlines, stagnation, and progress reporting.

Uses fake ZOS-API objects; never opens OpticStudio and never runs a real
optimizer. Verifies the three failure modes measured on 2026-11-12: a round
that never returns, a non-finite merit function that defeats the stagnation
guard, and an unbounded hammer tail wait.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import optimization_progress  # noqa: E402
from tools import optimization_tools  # noqa: E402


class FakeOperand:
    def __init__(self, value=1.0, weight=1.0):
        self.Type = "EFFL"
        self.Value = value
        self.Weight = weight


class FakeMFE:
    def __init__(self, values=None):
        self.values = values or [1.0]
        self.NumberOfOperands = len(self.values)

    def CalculateMeritFunction(self):
        pass

    def GetOperandAt(self, index):
        return FakeOperand(self.values[index - 1])


class FakeOptimizationTool:
    """IsRunning stays True after the deadline, as a diverging DLS run does."""

    def __init__(self, mf, *, honours_deadline=True, initial_mf=1.0):
        self.CurrentMeritFunction = mf
        self.honours_deadline = honours_deadline
        self.IsRunning = False
        self.CanCancel = True
        self.cancelled = 0
        self.closed = False
        self.Variables = 3
        self.Targets = 4
        self.InitialMeritFunction = initial_mf

    def RunAndWaitWithTimeout(self, seconds):
        self.IsRunning = not self.honours_deadline

    def Cancel(self):
        self.cancelled += 1
        self.IsRunning = False

    def Close(self):
        self.closed = True
        self.IsRunning = False


class FakeSession:
    def __init__(self, merit_after_round, initial_mf=1.0, stubborn=False):
        self.system = type("Sys", (), {})()
        self.system.MFE = FakeMFE()
        self.ZOSAPI = type("Zos", (), {"Tools": type("Tools", (), {
            "Optimization": type("Opt", (), {
                "OptimizationAlgorithm": type("Algo", (), {
                    "DampedLeastSquares": "DLS",
                    "OrthogonalDescent": "OD",
                })(),
                "OptimizationCycles": type("Cycles", (), {
                    "Fixed_10_Cycles": "10",
                    "Fixed_1_Cycle": "1",
                    "Fixed_5_Cycles": "5",
                    "Fixed_50_Cycles": "50",
                })(),
            })(),
        })(), })()
        self.merit_after_round = merit_after_round
        self.initial_mf = initial_mf
        self.stubborn = stubborn
        self.tools = []
        self.opens = 0
        self.rounds = 0
        self.copies = []
        self.restored = []
        self.restore_fails = False
        self.last_recovery_file = None
        self.system.CopySystem = self._copy

    def _copy(self):
        copy = type("Copy", (), {"closed": False})()
        copy.Close = lambda save: setattr(copy, "closed", True)
        self.copies.append(copy)
        return copy

    def _save_snapshot(self, snapshot):
        self.last_recovery_file = f"recovery-{len(self.restored)}.zmx"
        return self.last_recovery_file

    def _restore_recovery(self, path):
        if self.restore_fails:
            raise RuntimeError("LoadFile failed")
        self.restored.append(path)

    def open_tool(self, name):
        # The first open only inspects Variables/Targets/InitialMeritFunction.
        self.opens += 1
        if self.opens == 1:
            mf = self.initial_mf
        else:
            self.rounds += 1
            mf = self.merit_after_round(self.rounds)
        tool = FakeOptimizationTool(mf, honours_deadline=not self.stubborn,
                                    initial_mf=self.initial_mf)
        if name == "OpenHammerOptimization":
            tool.honours_deadline = False
        self.tools.append(tool)
        return tool


_RAW = getattr(getattr(optimization_tools.zemax_run_optimization, "__wrapped__", None),
               "__wrapped__", optimization_tools.zemax_run_optimization)


def run(merit_after_round, **kwargs):
    session = FakeSession(merit_after_round)
    with patch.object(optimization_tools.ZOSSession, "get_instance", return_value=session):
        return _RAW(**kwargs)


class OptimizationBoundsTests(unittest.TestCase):
    def test_round_timeout_returns_partial_and_stops_the_loop(self):
        session = FakeSession(lambda n: 1.0, stubborn=True)
        with patch.object(optimization_tools.ZOSSession, "get_instance", return_value=session):
            result = _RAW(max_rounds=5, round_timeout_seconds=0.01)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stop_reason"], "round_timeout")
        # One inspection open, then a single timed-out round.
        self.assertEqual(session.rounds, 1)
        self.assertIn("message", result)
        # The cancelled round is undone from the copy taken before it.
        self.assertTrue(result["round_rolled_back"])
        self.assertEqual(session.restored, ["recovery-0.zmx"])
        self.assertIn("恢复到被取消那一轮开始前", result["message"])
        self.assertTrue(all(copy.closed for copy in session.copies))

    def test_failed_rollback_fails_closed(self):
        session = FakeSession(lambda n: 1.0, stubborn=True)
        session.restore_fails = True
        with patch.object(optimization_tools.ZOSSession, "get_instance", return_value=session):
            result = _RAW(max_rounds=5, round_timeout_seconds=0.01)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "OPTIMIZATION_ROLLBACK_FAILED")
        self.assertFalse(result["round_rolled_back"])
        self.assertIn("恢复失败", result["message"])

    def test_non_finite_merit_function_ends_the_run_readably(self):
        merit = iter([0.9, float("nan")])
        result = run(lambda n: next(merit), max_rounds=5)
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["stop_reason"], "merit_function_non_finite")
        self.assertEqual(len(result["history"]), 1)
        self.assertIn("非有限", result["message"])

    def test_non_finite_initial_merit_function_is_rejected_before_running(self):
        session = FakeSession(lambda n: 1.0, initial_mf=float("inf"))
        with patch.object(optimization_tools.ZOSSession, "get_instance", return_value=session):
            result = _RAW(max_rounds=3)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "MERIT_FUNCTION_NON_FINITE")
        self.assertEqual(session.rounds, 0)

    def test_stagnation_stops_and_progress_is_reported(self):
        notes = []
        token = optimization_progress.SINK.set(notes.append)
        try:
            result = run(lambda n: 0.9 if n == 1 else 0.89, max_rounds=6, stagnation_threshold=0.005)
        finally:
            optimization_progress.SINK.reset(token)
        self.assertTrue(result["early_stop_triggered"])
        self.assertEqual(result["stop_reason"], "stagnated")
        # Round 1 improves 10%, round 2 still improves ~1.1% (>0.5% threshold),
        # so stagnation is only detected once a round stops improving (round 3).
        self.assertEqual([note["round"] for note in notes], [1, 2, 3])
        self.assertEqual(notes[1]["merit_after"], 0.89)
        self.assertEqual(notes[-1]["improvement_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()


class StageReminderTests(unittest.TestCase):
    def test_unrecorded_run_with_a_handoff_names_it(self):
        status = {"stage": None, "handoff": {"request_id": "handoff-abc", "version": 2}}
        with patch("core.design_milestones.stage_status", return_value=status):
            result = optimization_tools._stage_reminder({"status": "success"})
        self.assertEqual(result["status"], "success")
        self.assertIn("milestone_start", result["stage_record_hint"])
        self.assertIn("handoff-abc", result["stage_record_hint"])

    def test_unrecorded_run_without_a_handoff_does_not_suggest_a_failing_call(self):
        with patch("core.design_milestones.stage_status", return_value={"stage": None, "handoff": None}):
            hint = optimization_tools._stage_reminder({"status": "success"})["stage_record_hint"]
        self.assertNotIn("milestone_start", hint)
        self.assertIn("无法登记", hint)

    def test_recorded_stage_is_named_and_errors_are_untouched(self):
        with patch("core.design_milestones.stage_status", return_value={"stage": "stage_2", "handoff": None}):
            self.assertEqual(optimization_tools._stage_reminder({"status": "partial"})["recorded_stage"], "stage_2")
            self.assertNotIn("recorded_stage", optimization_tools._stage_reminder({"status": "error"}))

    def test_running_stage_reads_only_claimed_running_attempts(self):
        import json
        import sqlite3
        import tempfile
        from core import design_milestones
        folder = Path(tempfile.mkdtemp(prefix="stage-"))
        db = sqlite3.connect(folder / ".design-workflow.sqlite3")
        db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, project TEXT NOT NULL, data TEXT NOT NULL)")
        import time
        live = time.time() + 3600
        rows = [("a", "p", {"status": "claimed", "lease_until": live,
                            "milestone_attempts": [{"stage_id": "stage_1", "declared_status": "needs_review"},
                                                   {"stage_id": "stage_2", "declared_status": "running"}]}),
                ("d", "expired", {"status": "claimed", "lease_until": time.time() - 1,
                                  "milestone_attempts": [{"stage_id": "stage_1", "declared_status": "running"}]}),
                ("b", "p", {"status": "completed", "milestone_attempts": [{"stage_id": "stage_4", "declared_status": "running"}]}),
                ("c", "other", {"status": "claimed", "lease_until": live, "milestone_attempts": [{"stage_id": "stage_3", "declared_status": "running"}]})]
        db.executemany("INSERT INTO tasks VALUES (?,?,?)", [(i, p, json.dumps(d)) for i, p, d in rows])
        db.commit()
        db.close()
        with patch("tools.project_manager.get_output_base_dir", return_value=str(folder)):
            self.assertEqual(design_milestones.running_stage("p"), "stage_2")
            self.assertIsNone(design_milestones.running_stage("nobody"))
            self.assertIsNone(design_milestones.running_stage("expired"))
        with patch("tools.project_manager.get_output_base_dir", return_value=str(folder / "missing")):
            self.assertIsNone(design_milestones.running_stage("p"))


_RAW_HAMMER = getattr(getattr(optimization_tools.zemax_run_hammer, "__wrapped__", None),
                      "__wrapped__", optimization_tools.zemax_run_hammer)


class HammerTests(unittest.TestCase):
    def test_stopping_at_budget_is_success_so_history_records_it(self):
        # The engine records history (and stage geometry) only for status "success".
        session = FakeSession(lambda n: 0.4)
        with patch.object(optimization_tools.ZOSSession, "get_instance", return_value=session),                 patch("core.design_milestones.stage_status", return_value={"stage": "stage_4", "handoff": None}):
            result = _RAW_HAMMER(timeout_seconds=1)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["stopped_at_budget"])
        self.assertEqual(result["recorded_stage"], "stage_4")
        self.assertTrue(session.tools[-1].closed)

    def test_non_finite_hammer_merit_is_an_error(self):
        session = FakeSession(lambda n: 1.0, initial_mf=float("nan"))
        with patch.object(optimization_tools.ZOSSession, "get_instance", return_value=session):
            result = _RAW_HAMMER(timeout_seconds=1)
        self.assertEqual(result["code"], "MERIT_FUNCTION_NON_FINITE")
