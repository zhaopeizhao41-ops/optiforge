"""End-to-end SIMULATED workflow over real HTTP: requirements to four archived stages.

Uses tests/workbench_demo_service.py in a temporary workspace. Never opens Zemax;
the simulated optics are software-test data, not an optical acceptance.
"""
import hashlib
import json
from pathlib import Path
import sys
import unittest
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import demo_optics as optics  # noqa: E402
import workbench_demo_service as service  # noqa: E402


class DemoFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = service.Demo(0, pause=False)
        cls.demo.start_server()
        cls.demo.requirements()
        cls.demo.proposal()
        cls.demo.confirm()
        cls.demo.stages()
        cls.url = f"http://127.0.0.1:{cls.demo.server.server_port}"
        with urllib.request.urlopen(cls.url + "/api/session") as response:
            cls.token = json.load(response)["token"]

    @classmethod
    def tearDownClass(cls):
        cls.demo.server.shutdown()
        cls.demo.server.server_close()
        cls.demo.engine.executor.shutdown()
        cls.demo.close(keep=False)

    def post_tool(self, name, arguments):
        request = urllib.request.Request(self.url + "/api/tool", method="POST",
            data=json.dumps({"protocol_version": 2, "name": name, "arguments": arguments}).encode(),
            headers={"Content-Type": "application/json", "X-OptiForge-Token": self.token})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def view(self):
        return self.post_tool("zemax_design_workflow", {"action": "view", "project_name": service.PROJECT})["result"]

    def test_all_four_stages_report_with_geometry_and_quality_archives(self):
        stages = self.view()["optimization_milestones"]["stages"]
        self.assertEqual([stage["id"] for stage in stages], ["stage_1", "stage_2", "stage_3", "stage_4"])
        for stage in stages:
            attempt = stage["attempts"][0]
            self.assertEqual(attempt["declared_status"], "needs_review")
            self.assertEqual(attempt["geometry_artifact"]["kind"], "geometry")
            self.assertEqual(attempt["quality_artifact"]["kind"], "quality")
            # Same model revision as the reported geometry; each keeps its own state version.
            self.assertEqual(attempt["quality_artifact"]["source"]["model_revision"],
                             attempt["geometry_artifact"]["source"]["model_revision"])
        # Earlier stages are superseded only by later model changes.
        for stage in stages[:3]:
            self.assertEqual(stage["attempts"][0]["stale_reasons"], ["model_revision_changed"])
        self.assertEqual(stages[3]["attempts"][0]["stale_reasons"], [])

    def test_quality_archive_reads_over_http_with_matching_hash_and_original_verdicts(self):
        context = self.demo.engine.state.context()
        scope = {"service_instance_id": context["service_instance_id"], "project_name": service.PROJECT}
        for stage in self.view()["optimization_milestones"]["stages"]:
            reference = stage["attempts"][0]["quality_artifact"]
            reply = self.post_tool("zemax_design_workflow", {"action": "milestone_quality_artifact", "project_name": service.PROJECT,
                                                             "payload": {"source": scope, "artifact_id": reference["id"]}})["result"]
            self.assertEqual(reply["status"], "success", reply)
            self.assertEqual(hashlib.sha256(reply["payload_json"].encode()).hexdigest(), reference["sha256"])
            body = json.loads(reply["payload_json"])
            self.assertEqual(body["overall"], reference["analysis"]["overall"])
            self.assertEqual(body["engine"], optics.ENGINE)
            self.assertIn("SIMULATED", body["engine"])
            spot = next(row for row in body["verdicts"] if row["analysis"] == "spot")
            self.assertEqual(spot["verdict"], "INFO")

    def test_simulated_optimization_improves_and_keeps_boundaries(self):
        context = self.demo.engine.state.context()
        scope = {"service_instance_id": context["service_instance_id"], "project_name": service.PROJECT}
        spots = []
        for stage in self.view()["optimization_milestones"]["stages"]:
            reference = stage["attempts"][0]["quality_artifact"]
            body = json.loads(self.post_tool("zemax_design_workflow", {"action": "milestone_quality_artifact",
                "project_name": service.PROJECT, "payload": {"source": scope, "artifact_id": reference["id"]}})["result"]["payload_json"])
            spots.append(max(field["rms_radius_um"] for field in body["items"]["spot"]["fields"]))
        self.assertLess(spots[-1], spots[0])
        self.assertTrue(all(edge >= 1.19 if glass else edge >= 0.79 for _, glass, edge in optics.edge_thickness(self.demo.model)))
        self.assertAlmostEqual(optics.describe(self.demo.model)["efl_mm"], 50.0, delta=1.0)

    def test_demo_used_an_isolated_workspace(self):
        self.assertFalse(Path(self.demo.temp).resolve().is_relative_to((ROOT / "output").resolve()))
        self.assertTrue((Path(self.demo.temp) / ".design-workflow.sqlite3").is_file())


class OpticsSanityTests(unittest.TestCase):
    def test_glass_indices_match_schott_nd(self):
        # SCHOTT datasheets: N-SK16 nd 1.62041, N-F2 nd 1.62005 at 587.56 nm.
        self.assertAlmostEqual(optics.index("N-SK16", 0.5875618), 1.62041, places=4)
        self.assertAlmostEqual(optics.index("N-F2", 0.5875618), 1.62005, places=4)

    def test_cooke_template_paraxial_focal_length(self):
        facts = optics.describe(optics.cooke_model("x"))
        self.assertAlmostEqual(facts["efl_mm"], 50.0, delta=0.5)
        self.assertAlmostEqual(facts["working_f_number"], 4.5, delta=0.1)


if __name__ == "__main__":
    unittest.main()
