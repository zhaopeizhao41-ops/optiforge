"""Scoped analysis history; in-memory routes and explicit fake optics only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.analysis_data import AnalysisError, validate_settings
from core.workbench_analysis_history import AnalysisHistoryError
from core.workbench_engine import EngineWorker
from core.workbench_service import WorkbenchHandler
from core.workbench_state import WorkbenchState


def model():
    return {"units": "mm", "lens_units": "Millimeters", "system_mode": "Sequential",
            "name": "SIMULATED", "filepath": "not-a-real-optical-file.zmx", "project": "a", "configuration": 1,
            "settings": {"field_type": "Angle", "field_normalization": "Radial", "aperture_value": 10},
            "fields": [{"index": 1, "x": 0., "y": 0., "weight": 1}],
            "trace_fields": [{"index": 1, "x": 0., "y": 0., "weight": 1, "is_active": True}],
            "wavelengths": [{"index": 1, "wavelength_um": .55, "weight": 1, "is_primary": True}],
            "surfaces": [{"index": 0, "radius": 0}, {"index": 1, "radius": 20}]}


def publish(state, *, project="a", replace=False, model_changed=False, snapshot=True):
    value = model()
    value["project"] = project
    return state.publish(metadata={"active_project": project, "model_project": project, "proposal_confirmed": True},
                         snapshot={"status": "success", "schema_version": 1, "model": value} if snapshot else None,
                         validity="valid" if snapshot else "unavailable", replace=replace, model_changed=model_changed)


def result(state, identifier="one", **extra):
    current = state.read()
    source = {key: current[key] for key in ("service_instance_id", "model_id", "model_revision", "active_project", "model_project")}
    source.update(configuration=1, source_state_version=current["state_version"])
    return {"analysis_schema_version": 1, "status": "success", "result_id": identifier,
            "key": "cache-key-" + identifier, "engine": "SIMULATED native adapter", "source": source,
            "started_at": "2026-11-03T00:00:00+00:00", "finished_at": "2026-11-03T00:00:01+00:00",
            "elapsed_ms": 1000, "settings": validate_settings({"fields": [1], "analyses": ["mtf"]}),
            "items": {"mtf": {"status": "success", "curves": [], "coverage": {"fields": [1], "wavelengths": "all"}}},
            "overall": "INFO", "verdicts": [{"analysis": "mtf", "verdict": "INFO", "criteria": "SIMULATED stored diagnostic",
                "evidence": {"minimum": .4, "threshold": None}}], "model_metadata": {"units": "mm", "configuration": 1,
                "fields": model()["fields"], "wavelengths": model()["wavelengths"]}, **extra}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkbenchState()
        publish(self.state, replace=True)
        self.instance = self.state.context()["service_instance_id"]

    def listing(self, project="a"):
        return self.state.analysis_history_list(self.instance, project)

    def detail(self, identifier="one", project="a"):
        return self.state.analysis_history_detail(self.instance, project, identifier)

    def test_empty_scope_and_state_revision(self):
        listing = self.listing()
        self.assertEqual(listing["entries"], [])
        self.assertEqual(listing["analysis_history_revision"], 0)
        self.assertEqual(listing["storage"], "session")
        self.assertEqual(listing["limits"], {"max_entries": 8, "max_entry_bytes": 8388608, "max_total_bytes": 67108864})
        self.assertEqual(self.state.read()["analysis_history_revision"], 0)

    def test_summary_has_exact_whitelist_and_no_payload_copy(self):
        stored = result(self.state)
        self.state.store_analysis(stored)
        class CannotCopy:
            def __deepcopy__(self, memo):
                raise AssertionError("list copied payload")
        self.state.analysis_results["one"][0]["items"]["mtf"]["canary"] = CannotCopy()
        entry = self.listing()["entries"][0]
        self.assertEqual(set(entry), {"id", "result_id", "status", "engine", "finished_at", "source", "settings", "overall"})
        self.assertEqual(entry["id"], entry["result_id"])
        self.assertEqual(entry["finished_at"], stored["finished_at"])
        self.assertNotIn("items", entry)
        self.assertNotIn("snapshot", entry)
        entry["source"]["model_id"] = "changed"
        entry["settings"]["fields"].append(9)
        self.assertEqual(self.listing()["entries"][0]["settings"]["fields"], [1])

    def test_detail_is_detached_and_private_tokens_are_recursively_removed(self):
        value = result(self.state)
        value.update(token="root-secret", Token_Hash="hash-secret")
        value["items"]["mtf"]["lease_token"] = "lease-secret"
        value["items"]["mtf"]["nested"] = [{"TOKEN": "nested-secret", "data": 3}]
        self.state.store_analysis(value)
        detail = self.detail()
        self.assertEqual(detail["entry"], self.listing()["entries"][0])
        self.assertNotIn("secret", json.dumps(detail))
        detail["result"]["items"]["mtf"]["nested"][0]["data"] = 99
        self.assertEqual(self.detail()["result"]["items"]["mtf"]["nested"][0]["data"], 3)
        self.assertEqual(self.state.get_analysis("one")["token"], "root-secret")

    def test_cache_hits_keep_first_result_time_order_and_revision(self):
        first = result(self.state)
        self.state.store_analysis(first)
        self.state.store_analysis(result(self.state, "two"))
        revision = self.listing()["analysis_history_revision"]
        self.state.store_analysis({**first, "finished_at": "2026-11-04T00:00:00+00:00"})
        self.assertEqual([e["id"] for e in self.listing()["entries"]], ["two", "one"])
        self.assertEqual(self.detail()["result"]["finished_at"], first["finished_at"])
        self.assertEqual(self.listing()["analysis_history_revision"], revision)
        self.assertEqual(len(self.state.analysis_results), 2)

    def test_read_does_not_advance_events_model_or_history(self):
        self.state.store_analysis(result(self.state))
        before = self.state.read()
        with patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native forbidden")):
            for _ in range(3):
                self.listing(); self.detail()
        self.assertEqual(self.state.read(), before)

    def test_old_revision_model_and_revoked_approval_remain_history(self):
        self.state.store_analysis(result(self.state))
        original = self.detail()["result"]["source"]
        publish(self.state, replace=True)
        self.state.data["proposal_confirmed"] = False
        self.assertNotEqual(self.state.data["model_id"], original["model_id"])
        self.assertEqual(self.detail()["result"]["source"], original)
        publish(self.state, snapshot=False)
        self.assertEqual(len(self.listing()["entries"]), 1)

    def test_scope_conflicts_and_known_foreign_result_are_rejected(self):
        self.state.store_analysis(result(self.state))
        for instance, project, code in (("old", "a", "WORKBENCH_INSTANCE_MISMATCH"),
                                         (self.instance, "b", "WORKFLOW_PROJECT_MISMATCH")):
            with self.assertRaises(AnalysisHistoryError) as error:
                self.state.analysis_history_list(instance, project)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(error.exception.status, 409)
        publish(self.state, project="b", replace=True)
        self.state.store_analysis(result(self.state, "b-result"))
        self.assertEqual([e["id"] for e in self.listing("b")["entries"]], ["b-result"])
        with self.assertRaises(AnalysisHistoryError) as error:
            self.detail(project="b")
        self.assertEqual((error.exception.code, error.exception.status), ("ANALYSIS_HISTORY_NOT_FOUND", 404))
        self.assertIsNotNone(self.state.get_analysis("one"))

    def test_malformed_source_status_timestamp_and_settings_are_excluded_not_filled(self):
        valid = result(self.state)
        mutations = [lambda r: r.pop("finished_at"), lambda r: r.update(finished_at="not-time"),
            lambda r: r.update(finished_at="2026-11-03T00:00:00"), lambda r: r.update(engine=""),
            lambda r: r.update(status="error"), lambda r: r.update(status=[]), lambda r: r.update(overall=[]),
            lambda r: r.update(analysis_schema_version=True), lambda r: r.update(settings={}),
            lambda r: r.update(settings={"fields": [0]}), lambda r: r.pop("key"),
            lambda r: r["source"].pop("model_id"), lambda r: r["source"].update(configuration=True),
            lambda r: r["source"].update(model_revision=0), lambda r: r["source"].update(model_project="foreign"),
            lambda r: r["source"].update(service_instance_id="foreign"), lambda r: r["source"].pop("source_state_version")]
        for index, mutate in enumerate(mutations):
            value = deepcopy(valid)
            value["result_id"] = "invalid-" + str(index)
            mutate(value)
            self.state.analysis_results.clear()
            self.state.store_analysis(value)
            self.assertEqual(self.listing()["entries"], [], index)
            with self.assertRaises(AnalysisHistoryError):
                self.detail(value["result_id"])
            self.assertIsNotNone(self.state.get_analysis(value["result_id"]))

    def test_missing_comparison_context_is_readable_and_original_partial_verdicts_stay(self):
        value = result(self.state, status="partial", overall="ERROR",
                       items={"mtf": {"status": "error", "message": "SIMULATED missing data"}})
        value["verdicts"] = [{"analysis": "spot", "verdict": "INFO", "criteria": "diagnostic only"},
                             {"analysis": "wavefront", "verdict": "WARN", "evidence": {"native_statistics_verified": False}}]
        self.state.store_analysis(value)
        self.assertEqual(self.listing()["entries"][0]["status"], "partial")
        self.assertEqual(self.listing()["entries"][0]["overall"], "ERROR")
        self.assertEqual(self.detail()["result"], value)
        self.assertNotIn("comparison_context", self.detail()["result"])

    def test_invalid_settings_do_not_hide_valid_entries_in_the_same_cache(self):
        good = result(self.state, "valid")
        bad = result(self.state, "invalid-settings", settings={"fields": [0]})
        self.state.store_analysis(good)
        self.state.store_analysis(bad)
        before = self.state.read()
        self.assertEqual([entry["id"] for entry in self.listing()["entries"]], ["valid"])
        self.assertEqual(self.detail("valid")["result"], good)
        with self.assertRaises(AnalysisHistoryError) as error:
            self.detail("invalid-settings")
        self.assertEqual((error.exception.code, error.exception.status), ("ANALYSIS_HISTORY_NOT_FOUND", 404))
        self.assertEqual(self.state.get_analysis("invalid-settings"), bad)
        self.assertEqual(self.state.read(), before)

    def test_count_eviction_and_original_byte_budget_still_apply(self):
        for index in range(9):
            self.state.store_analysis(result(self.state, "entry-" + str(index)))
        self.assertEqual(len(self.listing()["entries"]), 8)
        self.assertEqual(self.listing()["entries"][0]["id"], "entry-8")
        self.assertIsNone(self.state.get_analysis("entry-0"))
        with self.assertRaises(AnalysisHistoryError):
            self.detail("entry-0")
        self.assertEqual(self.listing()["analysis_history_revision"], 10)
        before = self.listing()
        with patch("core.analysis_data.MAX_RESULT_BYTES", 10), self.assertRaises(AnalysisError):
            self.state.store_analysis(result(self.state, "too-large"))
        self.assertEqual(self.listing(), before)

    def test_byte_eviction_advances_revision_without_result_payload_duplicate(self):
        first, second = result(self.state), result(self.state, "two")
        size = len(json.dumps(first, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        with patch("core.analysis_data.MAX_CACHE_BYTES", size + 30):
            self.state.store_analysis(first)
            self.state.store_analysis(second)
        self.assertEqual([e["id"] for e in self.listing()["entries"]], ["two"])
        self.assertEqual(self.listing()["analysis_history_revision"], 3)
        self.assertEqual(self.state.get_analysis("two"), second)

    def test_restart_has_no_prior_payload_and_rejects_old_service_identity(self):
        self.state.store_analysis(result(self.state))
        restarted = WorkbenchState()
        publish(restarted, replace=True)
        instance = restarted.context()["service_instance_id"]
        self.assertEqual(restarted.analysis_history_list(instance, "a")["entries"], [])
        self.assertEqual(restarted.read()["analysis_history_revision"], 0)
        with self.assertRaises(AnalysisHistoryError):
            restarted.analysis_history_list(self.instance, "a")
        with self.assertRaises(AnalysisHistoryError):
            restarted.analysis_history_detail(instance, "a", "one")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.native = patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native forbidden"))
        self.native.start()
        self.addCleanup(self.native.stop)
        self.optical, self.captures, self.calls = model(), 0, 0
        self.confirmed, self.session = True, object()
        self.mutate_after = False
        self.engine = EngineWorker({}, capture=self.capture, metadata=self.metadata, analysis=self.analyze)
        self.addCleanup(self.engine.executor.shutdown)
        self.engine.refresh(); self.engine.refresh_future.result(timeout=2)

    def capture(self):
        self.captures += 1
        return {"status": "success", "schema_version": 1, "model": deepcopy(self.optical)}

    def metadata(self):
        return {"active_project": "a", "model_project": "a", "proposal_confirmed": self.confirmed,
                "session_identity": self.session}

    def analyze(self, model_copy, settings, progress):
        self.calls += 1
        output = result(self.engine.state, settings=settings)
        if self.mutate_after:
            self.optical["surfaces"][1]["radius"] += 1
        return output

    def request(self, settings=None):
        accepted = self.engine.request_analysis(settings, expected_context=self.engine.state.context())
        self.assertEqual(accepted["status"], "accepted", accepted)
        envelope = self.engine.analysis_pending[accepted["key"]][1].result(timeout=3)
        return envelope["result"]

    def test_comparison_context_is_exact_whitelist_and_adds_no_capture(self):
        before = self.captures
        output = self.request()
        cached = self.engine.state.get_analysis(output["result_id"])
        context = cached["comparison_context"]
        self.assertEqual(context["schema_version"], 1)
        self.assertEqual(set(context["model"]), {"units", "lens_units", "system_mode", "configuration", "settings", "fields", "trace_fields", "wavelengths"})
        self.assertNotIn("surfaces", context["model"])
        self.assertNotIn("filepath", context["model"])
        self.assertNotIn("name", context["model"])
        self.assertEqual(self.captures, before + 2)
        self.assertEqual(self.calls, 1)
        cached["comparison_context"]["model"]["fields"][0]["x"] = 999
        self.assertEqual(self.engine.state.get_analysis(output["result_id"])["comparison_context"]["model"]["fields"][0]["x"], 0)

    def test_cache_hit_preserves_context_id_time_and_history_revision(self):
        first = self.request()
        stored = self.engine.state.get_analysis(first["result_id"])
        revision = self.engine.state.read()["analysis_history_revision"]
        again = self.request()
        self.assertEqual(first["result_id"], again["result_id"])
        self.assertEqual(self.engine.state.get_analysis(again["result_id"]), stored)
        self.assertEqual(self.engine.state.read()["analysis_history_revision"], revision)
        self.assertEqual(self.calls, 1)

    def test_missing_model_settings_are_not_synthesized(self):
        self.optical.pop("settings")
        self.optical.pop("trace_fields")
        self.engine.refresh(); self.engine.refresh_future.result(timeout=2)
        output = self.request()
        context = self.engine.state.get_analysis(output["result_id"])["comparison_context"]["model"]
        self.assertNotIn("settings", context)
        self.assertNotIn("trace_fields", context)

    def test_changes_during_analysis_do_not_publish_history(self):
        self.mutate_after = True
        output = self.request()
        self.assertEqual(output["code"], "ANALYSIS_RESULT_STALE")
        listing = self.engine.state.analysis_history_list(self.engine.state.context()["service_instance_id"], "a")
        self.assertEqual(listing["entries"], [])
        self.assertEqual(listing["analysis_history_revision"], 0)


class HandlerTests(unittest.TestCase):
    """Real HTTP handler routing without a listener or executor."""
    setUp = StoreTests.setUp

    def request(self, path, *, token="local-token", host="127.0.0.1:34567", origin=None):
        response = []
        handler = object.__new__(WorkbenchHandler)
        handler.path = path
        handler.server = SimpleNamespace(engine=SimpleNamespace(state=self.state), server_port=34567, token="local-token")
        handler.headers = {"Host": host, "X-OptiForge-Token": token}
        if origin is not None:
            handler.headers["Origin"] = origin
        handler._json = lambda payload, status=200: response.append((status, payload))
        handler.do_GET()
        return response[0]

    def url(self, identifier="", **scope):
        return "/api/analysis-history" + ("/" + identifier if identifier else "") + "?" + urlencode({
            "instance_id": self.instance, "project_name": "a", **scope})

    def test_handlers_list_detail_old_endpoint_and_read_side_effects(self):
        self.state.store_analysis(result(self.state))
        before = self.state.read()
        with patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native forbidden")):
            code, listing = self.request(self.url())
            self.assertEqual(code, 200)
            code, detail = self.request(self.url("one"))
            self.assertEqual(code, 200)
        self.assertEqual(detail["entry"], listing["entries"][0])
        self.assertEqual(self.request("/api/analyses/one")[1], self.state.get_analysis("one"))
        self.assertEqual(self.state.read(), before)

    def test_handlers_strict_scope_missing_duplicate_unknown_and_foreign(self):
        for path in ("/api/analysis-history", "/api/analysis-history?project_name=a", self.url() + "&instance_id=x",
                     self.url() + "&project_name=a", self.url(extra="x"),
                     "/api/analysis-history?instance_id=&project_name=a", self.url(project_name=" ")):
            code, error = self.request(path)
            self.assertEqual((code, error["code"]), (400, "ANALYSIS_HISTORY_SCOPE_REQUIRED"), path)
        for suffix in ("", "one"):
            self.assertEqual(self.request(self.url(suffix, instance_id="old"))[0], 409)
            self.assertEqual(self.request(self.url(suffix, project_name="b"))[0], 409)
        self.assertEqual(self.request(self.url("unknown"))[0], 404)
        self.assertEqual(self.request(self.url("../foreign"))[1]["code"], "ANALYSIS_HISTORY_NOT_FOUND")
        self.state.store_analysis(result(self.state))
        publish(self.state, project="b", replace=True)
        code, error = self.request(self.url("one", project_name="b"))
        self.assertEqual((code, error["code"]), (404, "ANALYSIS_HISTORY_NOT_FOUND"))

    def test_handlers_keep_token_host_origin_authentication(self):
        for headers in ({"token": "bad"}, {"host": "foreign:34567"}, {"origin": "https://foreign.example"}):
            self.assertEqual(self.request(self.url(), **headers)[0], 403)


if __name__ == "__main__":
    unittest.main()
