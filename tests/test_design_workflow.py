"""File-only collaboration contracts. Never starts a native optical session."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import threading
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import design_workflow as f
from core.workbench_engine import EngineWorker
from core.workbench_client import WorkbenchClient
from core.workbench_service import WorkbenchHTTPServer, write_service_file
from core.workbench_effects import KNOWLEDGE, CONTEXT_READS, CONTEXT_WRITES
from tools import project_manager as projects
from tools.system_tools import (
    zemax_register_design_proposal,
    zemax_confirm_design_proposal,
    get_current_design_proposal,
    zemax_audit_requirements,
)
import tools

SPECS = {
    "efl_mm": 50,
    "f_number": 2.8,
    "fov_or_sensor": "像面直径 11 mm",
    "wavelength_range": "400–700 nm",
}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in [
            ("OUTPUT_BASE_DIR", self.temp.name),
            ("ACTIVE_PROJECT_FILE", str(Path(self.temp.name) / ".active.json")),
        ]:
            p = patch.object(projects, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch(
            "core.zos_session.ZOSSession.get_instance",
            side_effect=AssertionError("unexpected native session"),
        )
        self.native = p.start()
        self.addCleanup(p.stop)
        projects.set_active_project("test_a")

    def view(self):
        return f.view()

    def save(self, specs=None, **extra):
        specs = deepcopy(SPECS if specs is None else specs)
        return f.save_requirements(
            "test_a",
            self.view()["context"],
            "imaging_lens",
            specs,
            {k: "user" for k in specs},
            **extra,
        )

    def research(self, request_id="research"):
        self.save()
        v = f.submit_intent(
            "test_a",
            self.view()["context"],
            request_id,
            "initial_proposal",
            "隔离软件测试，非真实镜头设计",
        )
        self.assertTrue(v["draft"]["audit"]["result"]["is_complete"])
        c = f.claim("test_a", request_id, 1, "simulated-test-agent", 300)
        return c

    def register(self, c):
        return zemax_register_design_proposal(
            project_name="test_a",
            target_specs=self.view()["draft"]["audit"]["result"]["recognized_specs"],
            initial_structure_source="测试文献引用（模拟Agent，不宣称已做设计研究）",
            optical_theory_analysis="测试推演",
            glass_selection_rationale="测试选型",
            merit_function_strategy="测试策略",
            mechanical_constraints="测试预算",
            internal_air_spacing_budget="测试空气隙",
            topology="方案示意 / 未仿真",
            risks="仅软件测试，禁止实际仿真",
            workflow_context=self.view()["context"],
            task_claim={
                k: v
                for k, v in {
                    "request_id": c["task"]["request_id"],
                    "version": c["task"]["version"],
                    "lease_token": c["lease_token"],
                }.items()
            },
        )

    def complete(self, c, p):
        return f.update_task(
            "test_a",
            c["task"]["request_id"],
            c["task"]["version"],
            c["lease_token"],
            "completed",
            result_proposal_hash=p["proposal_hash"],
        )

    def test_restore_sources_no_implicit_defaults_and_cas(self):
        original = self.view()
        self.assertEqual(original["draft"]["specs"], {})
        v = self.save(user_prompt="<script>not executable</script>")
        self.assertEqual(self.view()["draft"], v["draft"])
        self.assertEqual(v["draft"]["sources"]["f_number"], "user")
        with self.assertRaisesRegex(f.WorkflowError, "已变化"):
            f.save_requirements("test_a", original["context"], "imaging_lens", {}, {})
        rec = v["templates"]["imaging_lens"]["mandatory"][1]
        with self.assertRaises(f.WorkflowError):
            f.save_requirements(
                "test_a",
                v["context"],
                "imaging_lens",
                {"f_number": 999},
                {"f_number": "recommended"},
            )
        adopted = f.save_requirements(
            "test_a",
            v["context"],
            "imaging_lens",
            {"f_number": rec["recommended_value"]},
            {"f_number": "recommended"},
        )
        self.assertEqual(adopted["draft"]["sources"]["f_number"], "recommended")
        self.native.assert_not_called()

    def test_shared_audit_ambiguities_conflicts_and_invalidation(self):
        for key, value in [
            ("fov_or_sensor", "25 deg"),
            ("wavelength_range", "400-700"),
            ("f_number", 0),
        ]:
            specs = {**SPECS, key: value}
            self.save(specs)
            v = f.audit("test_a", self.view()["context"])
            self.assertFalse(v["draft"]["audit"]["result"]["is_complete"])
            self.assertEqual(
                v["draft"]["audit"]["result"]["recognized_specs"],
                zemax_audit_requirements(specs=specs)["recognized_specs"],
            )
        self.save({**SPECS, "max_total_track_mm": 10, "min_back_focal_length_mm": 20})
        self.assertTrue(
            f.audit("test_a", self.view()["context"])["draft"]["audit"]["result"][
                "conflicts"
            ]
        )
        self.save()
        f.audit("test_a", self.view()["context"])
        v = self.save({**SPECS, "efl_mm": 60})
        self.assertIsNone(v["draft"]["audit"])

    def test_numeric_specs_accept_only_their_own_unit_suffix(self):
        base = {"f_number": 2.8, "fov_or_sensor": "像面直径 11 mm", "wavelength_range": "486.1–656.3 nm"}
        for value in ("50 mm", "50mm", "50 毫米", " 50 MM ", 50, "50"):
            audit = zemax_audit_requirements(system_type="imaging_lens", specs={**base, "efl_mm": value})
            self.assertEqual(audit["status"], "READY_FOR_DESIGN", value)
        for value in ("50 cm", "50 in", "mm", "50 mm 60 mm", "-0 mm", "0 mm", "inf mm"):
            audit = zemax_audit_requirements(system_type="imaging_lens", specs={**base, "efl_mm": value})
            self.assertIn("efl_mm", [item["key"] for item in audit["missing_mandatory"]], value)
        pitch = zemax_audit_requirements(system_type="imaging_lens",
                                         specs={**base, "efl_mm": 50, "pixel_pitch_um": "3.45 μm"})
        self.assertIn("pixel_pitch_um", pitch["recognized_specs"])
        self.assertNotIn("pixel_pitch_um", zemax_audit_requirements(
            system_type="imaging_lens", specs={**base, "efl_mm": 50, "pixel_pitch_um": "3.45 mm"})["recognized_specs"])

    def test_field_dimensions_and_angles_require_complete_positive_semantics(self):
        invalid = [
            "0 mm",
            "-11 mm",
            "−11 mm",
            "全视场 -25 deg",
            "全视场 +25 deg",
            "半视场 ±12.5 deg",
            "全视场 -12.5 到 +12.5 deg",
            "25 deg",
            "全视场 0°",
            "全视场 半视场 25 deg",
            "全视场 25 deg half field",
            "NaN mm",
            "inf mm",
            "1e999 mm",
            '2/0"',
            '-2/3"',
            '2/-3"',
            '0/3"',
            "11 mm -2 mm",
            "11 x 0 mm",
            "11 x -8 mm",
            "传感器 11mm (0mm)",
            "0毫米",
            "11 cm",
        ]
        for value in invalid:
            with self.subTest(value=value):
                result = zemax_audit_requirements(
                    specs={**SPECS, "fov_or_sensor": value}
                )
                self.assertFalse(result["is_complete"])
                self.assertNotIn("fov_or_sensor", result["recognized_specs"])
                issue = next(
                    i for i in result["invalid_specs"] if i["key"] == "fov_or_sensor"
                )
                self.assertIn("正", issue["message"])
                self.save({**SPECS, "fov_or_sensor": value})
                view = f.submit_intent(
                    "test_a",
                    self.view()["context"],
                    "invalid",
                    "initial_proposal",
                    "invalid field",
                )
                self.assertEqual(view["tasks"], [])
        valid = [
            "11 mm",
            "像面直径 11 mm",
            "传感器对角线 11 毫米",
            "11x8 mm",
            "传感器 11mm×8mm",
            '2/3"',
            "1/1.8″",
            "1/2.3 英寸",
            "1 inch",
            'sensor 2/3"',
            "全视场 25 deg",
            "半视场 12.5°",
            "24 deg full field",
            "half fov 12.5 degrees",
            "11 ＭＭ",
        ]
        for value in valid:
            with self.subTest(value=value):
                result = zemax_audit_requirements(
                    specs={**SPECS, "fov_or_sensor": value}
                )
                self.assertTrue(result["is_complete"], result["invalid_specs"])
        self.native.assert_not_called()

    def test_requirement_changes_archive_history_and_recover_51st_submission(self):
        for i in range(60):
            self.save({**SPECS, "efl_mm": 50 + i})
            view = f.submit_intent(
                "test_a",
                self.view()["context"],
                f"job-{i}",
                "initial_proposal",
                "versioned request",
            )
            self.assertEqual(
                sum(t["status"] in ("pending", "claimed") for t in view["tasks"]), 1
            )
        self.assertEqual(len(view["tasks"]), 60)
        archived = [t for t in view["tasks"] if t["status"] == "superseded"]
        self.assertEqual(len(archived), 59)
        self.assertTrue(all(t["stale"] for t in archived))
        self.assertEqual(self.view()["tasks"], view["tasks"])
        self.assertEqual(
            f.claim("test_a", "job-59", 1, "worker", 30)["status"], "success"
        )
        with self.assertRaises(f.WorkflowError):
            f.claim("test_a", "job-0", archived[-1]["version"], "worker", 30)

    def test_archived_claims_reject_late_results_and_old_tokens(self):
        c = self.research()
        self.save({**SPECS, "efl_mm": 60})
        archived = self.view()["tasks"][0]
        self.assertEqual(archived["status"], "superseded")
        self.assertEqual(archived["version"], c["task"]["version"] + 1)
        for version in (c["task"]["version"], archived["version"]):
            with self.assertRaises(f.WorkflowError):
                f.update_task(
                    "test_a", "research", version, c["lease_token"], "failed", "late"
                )
        with self.assertRaises(f.WorkflowError):
            f.retry_task(
                "test_a", self.view()["context"], "research", archived["version"]
            )
        f.audit("test_a", self.view()["context"])
        self.assertEqual(self.register(c)["status"], "error")

    def test_proposal_changes_archive_other_research_and_old_handoffs(self):
        old = self.research()
        for i in range(48):
            f.submit_intent(
                "test_a",
                self.view()["context"],
                f"other-{i}",
                "initial_proposal",
                "old research",
            )
        f.submit_intent(
            "test_a",
            self.view()["context"],
            "new",
            "initial_proposal",
            "another proposal",
        )
        newer = f.claim("test_a", "new", 1, "new-worker", 300)
        p = self.register(newer)
        self.assertEqual(p["status"], "success")
        tasks = {t["request_id"]: t for t in self.view()["tasks"]}
        self.assertEqual(sum(t["status"] == "superseded" for t in tasks.values()), 49)
        self.assertEqual(tasks["research"]["status"], "superseded")
        self.assertEqual(tasks["new"]["status"], "claimed")
        self.complete(newer, p)
        f.confirm("test_a", self.view()["context"], "handoff")
        f.submit_intent(
            "test_a",
            self.view()["context"],
            "revision",
            "proposal_revision",
            "needs changes",
        )
        self.assertEqual(
            next(t for t in self.view()["tasks"] if t["request_id"] == "handoff")[
                "status"
            ],
            "superseded",
        )
        c = f.claim("test_a", "revision", 1, "worker", 30)
        self.assertEqual(self.register(c)["status"], "success")

    def test_known_model_changes_archive_work_but_absent_observation_does_not(self):
        model = {
            "model_project": "test_a",
            "model_id": "id",
            "model_revision": 1,
            "service_instance_id": "s",
            "configuration": 1,
        }
        self.save()
        for i in range(50):
            f.submit_intent(
                "test_a",
                self.view()["context"],
                f"q-{i}",
                "surface_question",
                "explain",
                model,
                {"kind": "surface", "index": 1},
                model,
            )
        c = f.claim("test_a", "q-0", 1, "worker", 30, model)
        self.assertEqual(self.view()["tasks"][0]["status"], "pending")
        listed = f.task_list("test_a", {**model, "model_revision": 2})["tasks"]
        self.assertTrue(all(t["status"] == "superseded" for t in listed))
        newer = {**model, "model_revision": 2}
        view = f.submit_intent(
            "test_a",
            self.view()["context"],
            "new-q",
            "surface_question",
            "new question",
            newer,
            {"kind": "surface", "index": 1},
            newer,
        )
        self.assertEqual(view["tasks"][0]["status"], "pending")
        self.assertEqual(len(view["tasks"]), 51)
        with self.assertRaises(f.WorkflowError):
            f.update_task(
                "test_a",
                "q-0",
                c["task"]["version"],
                c["lease_token"],
                "completed",
                "late",
                model=model,
            )
        current = newer
        for i, changed in enumerate(
            (
                {**newer, "model_id": "new"},
                {**newer, "model_id": "new", "configuration": 2},
            )
        ):
            f.task_list("test_a", current)
            request_id = f"switch-{i}"
            f.submit_intent(
                "test_a",
                self.view()["context"],
                request_id,
                "surface_question",
                "question",
                current,
                {"kind": "surface", "index": 1},
                current,
            )
            listed = f.task_list("test_a", changed)["tasks"]
            task = next(t for t in listed if t["request_id"] == request_id)
            self.assertEqual(task["status"], "superseded")
            current = changed

    def test_effective_queue_limit_includes_claims_and_failed_task_retry(self):
        self.save()
        ctx = self.view()["context"]
        for i in range(50):
            f.submit_intent("test_a", ctx, f"active-{i}", "initial_proposal", "valid")
        claim = f.claim("test_a", "active-0", 1, "worker", 30)
        with self.assertRaises(f.WorkflowError) as e:
            f.submit_intent("test_a", ctx, "overflow", "initial_proposal", "valid")
        self.assertEqual(e.exception.code, "WORKFLOW_QUEUE_FULL")
        f.update_task(
            "test_a", "active-0", 2, claim["lease_token"], "failed", "test failure"
        )
        f.submit_intent("test_a", ctx, "replacement", "initial_proposal", "valid")
        with self.assertRaises(f.WorkflowError) as e:
            f.retry_task("test_a", ctx, "active-0", 3)
        self.assertEqual(e.exception.code, "WORKFLOW_QUEUE_FULL")
        tasks = self.view()["tasks"]
        self.assertEqual(sum(t["status"] in ("pending", "claimed") for t in tasks), 50)
        self.assertTrue(all(t["status"] != "superseded" for t in tasks))

    def test_incomplete_submit_does_not_create_task(self):
        self.save({})
        v = f.submit_intent(
            "test_a", self.view()["context"], "id", "initial_proposal", "test"
        )
        self.assertEqual(v["tasks"], [])
        self.assertEqual(v["stage"], "needs_requirements")

    def test_real_registration_confirmation_and_revocation_share_gate(self):
        c = self.research()
        p = self.register(c)
        self.assertEqual(p["status"], "success", p)
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        self.complete(c, p)
        review = self.view()
        v = f.confirm("test_a", review["context"], "handoff")
        self.assertTrue(get_current_design_proposal()["user_confirmed_to_simulate"])
        self.assertEqual(v["tasks"][0]["kind"], "model_handoff")
        self.assertFalse(v["tasks"][0]["stale"])
        self.save({**SPECS, "efl_mm": 60})
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        self.assertTrue(self.view()["tasks"][0]["stale"])
        self.native.assert_not_called()

    def test_old_confirmation_new_proposal_and_modification_request(self):
        c = self.research()
        p = self.register(c)
        self.complete(c, p)
        old = self.view()
        p2 = self.register(c)
        self.assertEqual(p2["status"], "error")  # completed lease
        v = f.submit_intent(
            "test_a", old["context"], "revision", "proposal_revision", "请修改测试方案"
        )
        rejected = zemax_confirm_design_proposal(
            "test_a", workflow_context=v["context"]
        )
        self.assertEqual(rejected["code"], "WORKFLOW_CHANGES_PENDING")
        new = f.claim("test_a", "revision", 1, "test", 300)
        p2 = self.register(new)
        self.assertEqual(p2["proposal_revision"], 2)
        with self.assertRaises(f.WorkflowError):
            f.confirm("test_a", old["context"], "old-confirm")
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])

    def test_confirmation_failure_never_leaves_authorized_json(self):
        c = self.research()
        p = self.register(c)
        self.complete(c, p)
        with self.assertRaises(f.WorkflowError):
            f.confirm("test_a", self.view()["context"], "")
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        # Simulate a crash after JSON write but before the DB evidence committed.
        path = Path(self.temp.name) / "test_a/design_proposal.json"
        data = json.loads(path.read_text("utf-8"))
        data["user_confirmed_to_simulate"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        self.assertFalse(self.view()["proposal"]["user_confirmed_to_simulate"])

    def test_idempotency_tokens_results_expiry_and_explicit_retry(self):
        self.save()
        ctx = self.view()["context"]
        for _ in range(2):
            f.submit_intent("test_a", ctx, "id", "initial_proposal", "test")
        self.assertEqual(len(self.view()["tasks"]), 1)
        with self.assertRaises(f.WorkflowError):
            f.submit_intent("test_a", ctx, "id", "initial_proposal", "changed")
        c = f.claim("test_a", "id", 1, "worker", 30)
        self.assertNotIn("token_hash", c["task"])
        with self.assertRaises(f.WorkflowError):
            f.update_task("test_a", "id", 2, "wrong", "progress", "test")
        with self.assertRaises(f.WorkflowError):
            f.update_task(
                "test_a", "id", 2, c["lease_token"], "completed", "invented success"
            )
        now = time.time()
        with patch("core.design_workflow.time.time", return_value=now + 60):
            self.assertTrue(self.view()["tasks"][0]["lease_expired"])
            with self.assertRaises(f.WorkflowError):
                f.update_task("test_a", "id", 2, c["lease_token"], "progress", "late")
            v = f.retry_task("test_a", self.view()["context"], "id", 2)
        new = f.claim("test_a", "id", 3, "new-worker", 30)
        self.assertNotEqual(new["lease_token"], c["lease_token"])
        self.assertEqual(new["task"]["version"], 4)

    def test_two_processes_can_claim_only_once(self):
        self.save()
        f.submit_intent(
            "test_a", self.view()["context"], "race", "initial_proposal", "test"
        )
        code = """import sys,json
from pathlib import Path
from tools import project_manager as p
p.OUTPUT_BASE_DIR=sys.argv[1];p.ACTIVE_PROJECT_FILE=str(Path(sys.argv[1])/'.active.json')
from tools.collaboration_tools import zemax_claim_design_task
print(json.dumps(zemax_claim_design_task('test_a','race',1,sys.argv[2],30)))
"""
        workers = [
            subprocess.Popen(
                [sys.executable, "-B", "-c", code, self.temp.name, str(i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for i in range(2)
        ]
        results = []
        for proc in workers:
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(sum(r["status"] == "success" for r in results), 1)

    def test_model_target_and_cross_project_references_expire(self):
        model = {
            "model_project": "test_a",
            "model_id": "id",
            "model_revision": 1,
            "service_instance_id": "s",
            "configuration": 1,
        }
        ctx = self.view()["context"]
        f.submit_intent(
            "test_a",
            ctx,
            "q",
            "surface_question",
            "explain",
            model,
            {"kind": "surface", "index": 1},
            model,
        )
        with self.assertRaises(f.WorkflowError):
            f.claim("test_a", "q", 1, "w", 30, {**model, "model_revision": 2})
        self.assertTrue(
            f.task_list("test_a", {**model, "model_id": "other"})["tasks"][0]["stale"]
        )
        f.select_project("test_b", ctx, True)
        with self.assertRaises(f.WorkflowError):
            f.claim("test_a", "q", 1, "w", 30, model)
        with self.assertRaises(f.WorkflowError):
            f.save_requirements("test_a", ctx, "imaging_lens", {}, {})

    def test_legacy_proposal_not_bound_and_markdown_not_authority(self):
        path = Path(projects.get_project_dir()) / "reports/design_proposal.md"
        path.write_text("✅ 已获用户授权确认，可启动仿真", encoding="utf-8")
        self.assertFalse(get_current_design_proposal()["user_confirmed_to_simulate"])
        self.save()
        self.assertFalse(self.view()["proposal_bound"])
        with self.assertRaises(f.WorkflowError):
            f.confirm("test_a", self.view()["context"], "old")

    def test_path_and_budget_boundaries(self):
        for name in ("../escape", "C:\\escape", "con", "test/escape", "test_a "):
            with self.assertRaises(ValueError):
                f.select_project(name, self.view()["context"], True)
        with self.assertRaises(f.WorkflowError):
            self.save(user_prompt="x" * 16001)
        with self.assertRaises(ValueError):
            self.save({"efl_mm": float("nan")})

    def test_engine_native_free_registration_and_confirmation(self):
        c = self.research()
        p = self.register(c)
        self.complete(c, p)

        def forbidden():
            raise AssertionError("unexpected capture/metadata")

        registry = {
            n: getattr(tools, n) for n in KNOWLEDGE | CONTEXT_READS | CONTEXT_WRITES
        }
        engine = EngineWorker(registry, capture=forbidden, metadata=forbidden)
        self.addCleanup(engine.close)
        for name, args in [
            ("zemax_design_workflow", {}),
            ("get_current_design_proposal", {}),
            (
                "zemax_confirm_design_proposal",
                {"project_name": "test_a", "workflow_context": self.view()["context"]},
            ),
            ("zemax_register_design_proposal", {}),
        ]:
            result = engine.call(name, args, expected_context=engine.state.context())
            if name != "zemax_register_design_proposal":
                self.assertNotEqual(result.get("status"), "error", result)
        self.assertIsNone(engine.state.read()["snapshot"])
        self.assertFalse(engine.state.read()["context_valid"])
        self.native.assert_not_called()

    def test_stale_bridge_can_observe_again_without_native_and_restart_writes_fail(
        self,
    ):
        registry = {
            n: getattr(tools, n) for n in KNOWLEDGE | CONTEXT_READS | CONTEXT_WRITES
        }
        engine = EngineWorker(registry, native_enabled=False)
        self.addCleanup(engine.close)
        server = WorkbenchHTTPServer(("127.0.0.1", 0), engine, model_import=False)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        path = Path(self.temp.name) / "service.json"
        write_service_file(server, path)
        bridge = WorkbenchClient("http://127.0.0.1:" + str(server.server_port), path)
        first = bridge.call("zemax_design_workflow", {})
        other = engine.call(
            "zemax_design_workflow",
            {
                "action": "select",
                "project_name": "test_b",
                "payload": {"create": True},
                "expected_workflow": first["context"],
            },
            expected_context=engine.state.context(),
        )
        self.assertEqual(other["status"], "success")
        fresh = bridge.call("zemax_design_workflow", {})
        self.assertEqual(fresh["project_name"], "test_b")
        stale = engine.state.context()
        stale["service_instance_id"] = "old"
        result = engine.call(
            "zemax_design_workflow",
            {
                "action": "audit",
                "project_name": "test_b",
                "expected_workflow": fresh["context"],
            },
            expected_context=stale,
        )
        self.assertEqual(result["code"], "WORKBENCH_CONTEXT_STALE")
        result = engine.call("__refresh__")
        self.assertEqual(result["code"], "NATIVE_UNAVAILABLE")
        self.native.assert_not_called()

    def test_old_research_cannot_overwrite_another_tasks_proposal(self):
        c = self.research()
        f.submit_intent(
            "test_a",
            self.view()["context"],
            "new",
            "initial_proposal",
            "second request",
        )
        newer = f.claim("test_a", "new", 1, "new-worker", 300)
        self.assertEqual(self.register(newer)["status"], "success")
        self.assertEqual(self.register(c)["code"], "WORKFLOW_TASK_CONFLICT")

    def test_revocation_invalidates_previously_open_confirm_context(self):
        c = self.research()
        p = self.register(c)
        self.complete(c, p)
        old = self.view()
        zemax_confirm_design_proposal("test_a", False)
        with self.assertRaises(f.WorkflowError):
            f.confirm("test_a", old["context"], "old-confirm")

    def test_legacy_project_round_trip_invalidates_old_workflow_context(self):
        old = self.view()["context"]
        projects.set_active_project("test_b")
        projects.set_active_project("test_a")
        with self.assertRaises(f.WorkflowError):
            f.audit("test_a", old)

    def test_loaded_snapshot_is_preserved_and_question_targets_are_versioned(self):
        def forbidden():
            raise AssertionError("workflow observed the native model")

        registry = {
            n: getattr(tools, n) for n in KNOWLEDGE | CONTEXT_READS | CONTEXT_WRITES
        }
        engine = EngineWorker(registry, capture=forbidden, metadata=forbidden)
        self.addCleanup(engine.close)
        engine.state.publish(
            metadata={
                "active_project": "test_a",
                "model_project": "test_a",
                "proposal_confirmed": False,
            },
            snapshot={"model": {"configuration": 1, "surfaces": [{"index": 1}]}},
            validity="valid",
            replace=True,
        )

        def call(args):
            return engine.call(
                "zemax_design_workflow", args, expected_context=engine.state.context()
            )

        v = call({})
        model = v["model_reference"]
        old = engine.state.read()
        v = call(
            {
                "action": "submit",
                "project_name": "test_a",
                "expected_workflow": v["context"],
                "payload": {
                    "request_id": "q",
                    "kind": "surface_question",
                    "user_text": "explain",
                    "target": {"kind": "surface", "index": 1},
                    "expected_model": model,
                },
            }
        )
        self.assertFalse(v["tasks"][0]["stale"])
        self.assertEqual(engine.state.read()["model_revision"], old["model_revision"])
        self.assertEqual(engine.state.read()["snapshot"], old["snapshot"])
        v = call(
            {
                "action": "select",
                "project_name": "test_b",
                "expected_workflow": v["context"],
                "payload": {"create": True},
            }
        )
        v = call(
            {
                "action": "select",
                "project_name": "test_a",
                "expected_workflow": v["context"],
                "payload": {"create": False},
            }
        )
        self.assertTrue(engine.state.read()["context_valid"])
        self.assertEqual(v["model_reference"], model)
        engine.state.publish(
            metadata={
                "active_project": "test_a",
                "model_project": "test_a",
                "proposal_confirmed": False,
            },
            snapshot=old["snapshot"],
            validity="valid",
            model_changed=True,
        )
        v = call({})
        self.assertTrue(v["tasks"][0]["stale"])
        self.native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
