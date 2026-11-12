"""Bounded immutable geometry store tests using temporary workflow SQLite only."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import milestone_geometry_artifacts as artifacts
from core import design_workflow as flow
from tools import project_manager as projects


PROJECT = "stage_geometry_test"
SOURCE = {"project_name": PROJECT, "requirements_revision": 1, "proposal_hash": "a" * 64,
          "proposal_revision": 1, "authorization_revision": 0, "service_instance_id": "original-service",
          "model_id": "original-model", "model_revision": 4, "active_project": PROJECT,
          "model_project": PROJECT, "configuration": 1, "source_state_version": 8}


def geometry():
    optical = {key: SOURCE[key] for key in artifacts.OPTICAL_KEYS}
    sync = {key: optical[key] for key in artifacts.OPTICAL_KEYS
            if key not in {"source_state_version", "configuration"}}
    sync["state_version"] = optical["source_state_version"]
    return {"entry": {"id": "cache-entry", "recorded_at": "2026-11-04T02:00:00+00:00", "operation_id": "op-1",
                      "tool": "zemax_run_optimization", "label": "合成几何", "source": optical},
            "snapshot": {"status": "success", "schema_version": 1, "sync": sync,
                         "model": {"project": PROJECT, "configuration": 1, "units": "mm", "name": "合成 <script>纯文本</script>",
                                   "surfaces": [{"index": 0, "radius": "Infinity", "thickness": "Infinity", "material": ""}]},
                         "layout": {"status": "unsupported", "plane": "YZ", "units": "mm", "surfaces": [],
                                    "elements": [], "issues": ["合成不可用轮廓说明"], "rays": []}}}


class GeometryArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="geometry-artifact-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "output"
        self.path = self.root / ".design-workflow.sqlite3"
        self.active = Path(self.temp.name) / "active.json"
        self.active.write_text(json.dumps({"active_project": PROJECT}), encoding="utf-8")
        for name, value in (("OUTPUT_BASE_DIR", str(self.root)), ("ACTIVE_PROJECT_FILE", str(self.active))):
            p = patch.object(projects, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.payload = geometry()
        self.task = {"request_id": "handoff", "project_name": PROJECT, "milestone_attempts": []}
        self.attempt = {"id": "attempt-1", "task_id": "handoff", "stage_id": "stage_1", "declared_status": "needs_review",
                        "end_source": {**SOURCE, "source_state_version": 12}, "operations": [
                            {"operation_id": "op-1", "status": "succeeded", "result_status": "success",
                             "observed_source": {key: SOURCE[key] for key in artifacts.OPTICAL_KEYS}}]}
        self.operation = self.attempt["operations"][-1]
        self.scope = {"service_instance_id": "reading-new-service", "project_name": PROJECT}
        self.observation = {"service_instance_id": self.scope["service_instance_id"],
                            "context": {"service_instance_id": self.scope["service_instance_id"], "active_project": PROJECT}}
        token = artifacts.GEOMETRY_READER.set(lambda operation_id, source: deepcopy(self.payload))
        self.addCleanup(artifacts.GEOMETRY_READER.reset, token)

    def create(self):
        with flow.transaction() as db:
            metadata = artifacts.archive(db, PROJECT, self.task, self.attempt, self.operation, self.attempt["end_source"])
            self.attempt["geometry_artifact"] = metadata
            self.task["milestone_attempts"] = [self.attempt]
            flow._put_task(db, self.task)
        self.ref = metadata
        return metadata

    def read(self):
        return artifacts.read(PROJECT, {"source": self.scope, "artifact_id": self.ref["id"]}, self.observation)

    def assert_code(self, code, function, *args):
        with self.assertRaises(flow.WorkflowError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(self.temp.name, str(raised.exception))
        return raised.exception

    def mutate(self, sql, values=()):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(sql, values)

    def test_canonical_payload_hash_ref_and_old_service_source_preserved(self):
        ref = self.create()
        result = self.read()
        self.assertEqual(result["artifact"], ref)
        self.assertEqual(result["source"], self.scope)
        self.assertEqual(result["artifact"]["source"], SOURCE)
        self.assertEqual(set(result), {"status", "stage_artifact_schema_version", "storage", "source", "artifact", "payload_json", "limits"})
        raw = artifacts.canonical(self.payload)
        self.assertEqual(result["payload_json"], raw)
        self.assertEqual(ref["sha256"], hashlib.sha256(raw.encode("utf-8")).hexdigest())
        self.assertEqual(ref["bytes"], len(raw.encode("utf-8")))
        self.assertEqual(result["limits"], {"max_artifact_bytes": 8388608, "max_project_bytes": 268435456,
                         "max_workspace_bytes": 536870912, "max_project_artifacts": 64, "max_workspace_artifacts": 256})

    def test_identical_binding_is_idempotent_but_changed_content_or_source_conflicts(self):
        ref = self.create()
        with flow.transaction() as db:
            again = artifacts.archive(db, PROJECT, self.task, self.attempt, self.operation, self.attempt["end_source"])
            self.assertEqual(ref, again)
        self.payload["snapshot"]["model"]["name"] = "different"
        with flow.transaction() as db:
            self.assert_code("MILESTONE_ARTIFACT_CONFLICT", artifacts.archive, db, PROJECT, self.task,
                             self.attempt, self.operation, self.attempt["end_source"])
        self.assertEqual(self.read()["artifact"], ref)

    def test_recursive_private_keys_removed_without_editing_user_text(self):
        self.payload["snapshot"]["extra"] = {"ToKeN": "canary", "LEASE_token": "canary", "Token_HASH": "canary",
                                            "milestone_ATTEMPTS": ["canary"], "keep": [{"tOKen": "canary", "text": "token 原文"}]}
        self.create()
        result = self.read()
        self.assertNotIn("canary", result["payload_json"])
        self.assertEqual(json.loads(result["payload_json"])["snapshot"]["extra"], {"keep": [{"text": "token 原文"}]})
        self.assertEqual(self.payload["snapshot"]["extra"]["ToKeN"], "canary")

    def test_exact_payload_budget_and_each_capacity_limit_reject_without_eviction(self):
        size = len(artifacts.canonical(self.payload).encode())
        with patch.object(artifacts, "MAX_ARTIFACT_BYTES", size - 1):
            with flow.transaction() as db:
                self.assert_code("MILESTONE_ARTIFACT_TOO_LARGE", artifacts.archive, db, PROJECT, self.task,
                                 self.attempt, self.operation, self.attempt["end_source"])
        with patch.object(artifacts, "MAX_ARTIFACT_BYTES", size):
            ref = self.create()
        for key, value in (("MAX_PROJECT_ARTIFACTS", 1), ("MAX_WORKSPACE_ARTIFACTS", 1),
                           ("MAX_PROJECT_BYTES", size), ("MAX_WORKSPACE_BYTES", size)):
            with self.subTest(capacity=key), patch.object(artifacts, key, value):
                second = {**self.attempt, "id": "attempt-2"}
                with flow.transaction() as db:
                    self.assert_code("MILESTONE_ARTIFACT_CAPACITY", artifacts.archive, db, PROJECT,
                                     self.task, second, self.operation, self.attempt["end_source"])
            self.assertEqual(self.read()["artifact"], ref)

    def test_missing_database_table_or_id_does_not_create_storage(self):
        self.ref = {"id": "0" * 32}
        self.assert_code("MILESTONE_ARTIFACT_NOT_FOUND", self.read)
        self.assertFalse(self.root.exists())
        with flow.transaction():
            pass
        original = self.path.read_bytes()
        self.assert_code("MILESTONE_ARTIFACT_NOT_FOUND", self.read)
        self.assertEqual(original, self.path.read_bytes())
        self.create()
        self.ref = {"id": "0" * 32}
        self.assert_code("MILESTONE_ARTIFACT_NOT_FOUND", self.read)

    def test_read_denies_writes_and_preserves_database_bytes_and_closes_connection(self):
        self.create()
        before = self.path.read_bytes()
        connect = sqlite3.connect
        statements, connections, forbidden = [], [], []
        deny = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}

        def authorizer(action, first, second, _database, _trigger):
            if action in deny:
                forbidden.append((action, first, second))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def traced(*args, **kwargs):
            self.assertIn("?mode=ro", args[0])
            self.assertTrue(kwargs["uri"])
            db = connect(*args, **kwargs)
            db.set_authorizer(authorizer)
            db.set_trace_callback(statements.append)
            connections.append(db)
            return db

        with patch.object(artifacts.sqlite3, "connect", side_effect=traced), \
             patch.object(flow, "transaction", side_effect=AssertionError("write transaction forbidden")):
            self.read()
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(forbidden, [])
        self.assertIn("PRAGMA query_only=ON", statements)
        self.assertIn("BEGIN", statements)
        for db in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_corrupt_hash_json_metadata_reference_and_schema_are_explicit_errors(self):
        self.create()
        with closing(sqlite3.connect(self.path)) as db:
            original = db.execute(f"SELECT metadata_json,payload_json,bytes FROM {artifacts.TABLE}").fetchone()
        for value in ("{}", '{"entry":1,"entry":2}', "invalid-private-data"):
            self.mutate(f"UPDATE {artifacts.TABLE} SET payload_json=?", (value,))
            self.assert_code("MILESTONE_ARTIFACT_INVALID", self.read)
        self.mutate(f"UPDATE {artifacts.TABLE} SET payload_json=?", (original[1],))
        metadata = json.loads(original[0])
        metadata["source"]["model_revision"] += 1
        self.mutate(f"UPDATE {artifacts.TABLE} SET metadata_json=?", (json.dumps(metadata),))
        self.assert_code("MILESTONE_ARTIFACT_INVALID", self.read)
        self.mutate(f"UPDATE {artifacts.TABLE} SET metadata_json=?", (original[0],))
        self.mutate("UPDATE tasks SET data=?", ('{"request_id":"handoff","project_name":"stage_geometry_test","milestone_attempts":[]}',))
        self.assert_code("MILESTONE_ARTIFACT_INVALID", self.read)
        self.mutate(f"ALTER TABLE {artifacts.TABLE} ADD COLUMN incompatible TEXT")
        self.assert_code("MILESTONE_ARTIFACT_UNAVAILABLE", self.read)

    def test_bad_large_storage_row_is_rejected_before_payload_select(self):
        self.create()
        self.mutate(f"UPDATE {artifacts.TABLE} SET payload_json=?", ("x" * 2000,))
        sql = []
        connect = sqlite3.connect

        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(sql.append)
            return db

        with patch.object(artifacts, "MAX_ARTIFACT_BYTES", 1500), patch.object(artifacts.sqlite3, "connect", side_effect=traced):
            self.assert_code("MILESTONE_ARTIFACT_TOO_LARGE", self.read)
        self.assertTrue(any("length(CAST(payload_json AS BLOB))" in line for line in sql))
        self.assertFalse(any(line.startswith("SELECT *") for line in sql))

    def test_valid_hash_cannot_hide_duplicate_json_or_invalid_payload_schema(self):
        self.create()
        original_ref = deepcopy(self.ref)
        invalid = [artifacts.canonical(self.payload)[:-1] + ',"entry":{}}',
                   '{"entry":1,"snapshot":{}}', '{"entry":NaN,"snapshot":{}}']
        for raw in invalid:
            metadata = {**original_ref, "sha256": hashlib.sha256(raw.encode()).hexdigest(), "bytes": len(raw.encode())}
            self.mutate(f"UPDATE {artifacts.TABLE} SET metadata_json=?,payload_json=?,bytes=?",
                        (artifacts.canonical(metadata), raw, metadata["bytes"]))
            self.assert_code("MILESTONE_ARTIFACT_INVALID", self.read)

    def test_task_receipt_boolean_cannot_equal_valid_integer_source_with_unchanged_payload_hash(self):
        for key in ("configuration", "model_revision", "source_state_version"):
            self.payload["entry"]["source"][key] = 1
            self.operation["observed_source"][key] = 1
            self.attempt["end_source"][key] = 1
        self.payload["snapshot"]["sync"].update(model_revision=1, state_version=1)
        self.create()
        original_payload = self.read()["payload_json"]
        for key in ("configuration", "model_revision", "source_state_version"):
            with self.subTest(receipt_key=key):
                corrupted = deepcopy(self.task)
                corrupted["milestone_attempts"][0]["operations"][-1]["observed_source"][key] = True
                self.mutate("UPDATE tasks SET data=?", (json.dumps(corrupted),))
                self.assert_code("MILESTONE_ARTIFACT_INVALID", self.read)
        self.mutate("UPDATE tasks SET data=?", (json.dumps(self.task),))
        self.assertEqual(self.read()["payload_json"], original_payload)

    def test_layout_snapshot_schema_and_source_validation_rejects_unusable_geometry(self):
        mutations = [lambda p: p["entry"]["source"].update(configuration=True),
                     lambda p: p["snapshot"]["sync"].update(state_version=999),
                     lambda p: p["snapshot"]["model"].update(project="other"),
                     lambda p: p["snapshot"]["model"]["surfaces"][0].update(radius=None),
                     lambda p: p["snapshot"]["model"].update(surfaces=[]),
                     lambda p: p["snapshot"]["layout"].update(issues=[]),
                     lambda p: p["snapshot"]["layout"].update(status="ready"),
                     lambda p: p["snapshot"]["layout"].update(rays=[{}]),
                     lambda p: p["snapshot"]["layout"].update(plane="XZ")]
        for mutate in mutations:
            value = geometry()
            mutate(value)
            self.assert_code("MILESTONE_ARTIFACT_INVALID", artifacts.validate_payload, value)

    def test_schema_io_errors_are_fixed_safe_messages(self):
        self.create()
        with patch.object(artifacts.sqlite3, "connect", side_effect=OSError("private-path-sensitive-sql")):
            error = self.assert_code("MILESTONE_ARTIFACT_UNAVAILABLE", self.read)
        self.assertNotIn("private-path", str(error))


if __name__ == "__main__":
    unittest.main()
