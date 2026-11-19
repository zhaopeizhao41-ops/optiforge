"""Isolated SQLite history tests; never opens native optical software or user data."""

import base64
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import design_task_history as history
from core import design_workflow as flow
from core.design_milestones import OBSERVATION
from tools import project_manager as projects
from tools.collaboration_tools import zemax_list_design_tasks


PROJECT = "history_a"
SOURCE = {"service_instance_id": "isolated-history-service", "project_name": PROJECT}
SCHEMA = """
CREATE TABLE state (project TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE tasks (id TEXT PRIMARY KEY, project TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), selection INTEGER NOT NULL);
INSERT INTO meta VALUES (1,0);
"""


def task(request_id, project=PROJECT, status="completed", **extra):
    return {
        "request_id": request_id, "project_name": project, "kind": "surface_question",
        "user_text": "隔离软件测试 <script>纯文本</script>", "status": status,
        "version": 2, "requirements_revision": 1, "requirements_snapshot": {"revision": 1},
        "proposal_hash": None, "model_reference": None, "target": None,
        "created_at": 1700000000.0, "worker_id": "synthetic-worker", "lease_until": 1700000300.0,
        "progress": "历史进度", "result_text": "历史结果", "result_proposal_hash": None,
        "finished_at": 1700000100.0, **extra,
    }


def encode_cursor(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value):
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))


class TaskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "output"
        self.db_path = self.root / ".design-workflow.sqlite3"
        self.active_path = Path(self.temp.name) / "active.json"
        self.active_path.write_text(json.dumps({"active_project": PROJECT}), encoding="utf-8")
        for name, value in (("OUTPUT_BASE_DIR", str(self.root)), ("ACTIVE_PROJECT_FILE", str(self.active_path))):
            p = patch.object(projects, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("core.zos_session.ZOSSession.get_instance", side_effect=AssertionError("native is forbidden"))
        self.native = p.start()
        self.addCleanup(p.stop)
        observed = {"service_instance_id": SOURCE["service_instance_id"],
                    "context": {"service_instance_id": SOURCE["service_instance_id"], "active_project": PROJECT}}
        token = OBSERVATION.set(observed)
        self.addCleanup(OBSERVATION.reset, token)
        token = flow.MODEL_REFERENCE.set(None)
        self.addCleanup(flow.MODEL_REFERENCE.reset, token)

    def seed(self, count=125, *, project=PROJECT, offset=0, rowid_base=None, extra=None):
        self.root.mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            if not db.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone():
                db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", (project, '{"revision":1}'))
            for index in range(1, count + 1):
                value = task(f"{project}-{index + offset}", project=project)
                if extra:
                    value.update(extra(index))
                data = json.dumps(value, ensure_ascii=False)
                if rowid_base is None:
                    db.execute("INSERT INTO tasks VALUES (?,?,?)", (value["request_id"], project, data))
                else:
                    db.execute("INSERT INTO tasks(rowid,id,project,data) VALUES (?,?,?,?)",
                               (rowid_base + index, value["request_id"], project, data))

    def read(self, **options):
        return zemax_list_design_tasks(PROJECT, {"source": dict(SOURCE), **options})

    def assert_error(self, value, code):
        self.assertEqual(value.get("status"), "error", value)
        self.assertEqual(value.get("code"), code, value)
        self.assertNotIn(self.temp.name, value.get("message", ""))

    def replace(self, request_id, transform):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            old = json.loads(db.execute("SELECT data FROM tasks WHERE id=?", (request_id,)).fetchone()[0])
            value = transform(old)
            db.execute("UPDATE tasks SET data=? WHERE id=?", (json.dumps(value, ensure_ascii=False), request_id))

    def test_default_page_and_probe_cursor_do_not_skip_rows(self):
        self.seed()
        first = self.read()
        self.assertEqual(set(first), {"status", "task_history_schema_version", "storage", "source",
                                     "range", "tasks", "has_more", "next_cursor", "limits"})
        self.assertEqual(first["source"], SOURCE)
        self.assertEqual(first["range"], "older_than_current_100_at_first_read")
        self.assertEqual(first["limits"], {"max_limit": 50, "max_response_bytes": 1048576})
        self.assertEqual([t["request_id"] for t in first["tasks"]], [f"{PROJECT}-{i}" for i in range(25, 5, -1)])
        self.assertTrue(first["has_more"])
        self.assertEqual(decode_cursor(first["next_cursor"])["before"], "6")
        second = self.read(cursor=first["next_cursor"])
        self.assertEqual([t["request_id"] for t in second["tasks"]], [f"{PROJECT}-{i}" for i in range(5, 0, -1)])
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])

    def test_context_question_target_reads_back_verbatim(self):
        target = {"kind": "context", "anchors": [
            {"kind": "surface", "index": 3, "comment": "边缘光线偏折大"},
            {"kind": "analysis", "tab": "mtf", "result_id": "r-1", "field": 2},
            {"kind": "stage", "stage": "stage_2"}]}
        self.seed(101, extra=lambda index: {"target": target} if index == 1 else {})
        self.assertEqual([t["target"] for t in self.read()["tasks"]], [target])

    def test_fewer_than_101_rows_are_empty_and_count_all_statuses(self):
        self.seed(100)
        self.assertEqual(self.read()["tasks"], [])
        self.seed(1, offset=100, extra=lambda _: {"status": "claimed"})
        self.assertEqual([t["request_id"] for t in self.read()["tasks"]], [f"{PROJECT}-1"])
        self.replace(f"{PROJECT}-1", lambda t: {**t, "status": "pending"})
        self.assertEqual(self.read()["tasks"], [])

    def test_project_boundary_ignores_interleaved_other_project(self):
        self.seed(130)
        self.seed(200, project="history_b")
        self.seed(5, offset=130)
        result = self.read(limit=50)
        self.assertEqual(len(result["tasks"]), 35)
        self.assertEqual(result["tasks"][0]["request_id"], f"{PROJECT}-35")
        self.assertTrue(all(t["project_name"] == PROJECT for t in result["tasks"]))

    def test_live_terminal_membership_and_fixed_insertion_upper(self):
        self.seed(125, extra=lambda i: {"status": "claimed" if i == 23 else ("superseded" if i == 25 else "failed")})
        first = self.read(limit=2)
        self.assertEqual([t["status"] for t in first["tasks"]], ["superseded", "failed"])
        self.replace(f"{PROJECT}-23", lambda t: {**t, "status": "completed"})
        self.seed(15, offset=125)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("UPDATE state SET data=? WHERE project=?", ('{"revision":2}', PROJECT))
        second = self.read(limit=50, cursor=first["next_cursor"])
        self.assertEqual(second["tasks"][0]["request_id"], f"{PROJECT}-23")
        self.assertTrue(second["tasks"][0]["stale"])
        self.assertEqual(self.read(limit=50)["tasks"][0]["request_id"], f"{PROJECT}-40")

    def test_rowids_above_javascript_integer_precision_roundtrip_as_strings(self):
        self.seed(125, rowid_base=history.MAX_ROWID - 125)
        first = self.read(limit=3)
        cursor = decode_cursor(first["next_cursor"])
        self.assertEqual(cursor["upper"], str(history.MAX_ROWID - 100))
        self.assertEqual(cursor["before"], str(history.MAX_ROWID - 102))
        self.assertEqual(self.read(limit=50, cursor=first["next_cursor"])["tasks"][0]["request_id"], f"{PROJECT}-22")

    def test_limits_are_strict_without_coercion(self):
        self.seed(102)
        for value in (True, False, 0, -1, 51, 1.0, "2", None, [], {}):
            with self.subTest(limit=value):
                self.assert_error(self.read(limit=value), "WORKFLOW_INPUT_INVALID")
        for value in (1, 50):
            self.assertEqual(self.read(limit=value)["status"], "success")

    def test_history_shape_and_source_fail_closed(self):
        for value in ({}, {"source": {}}, {"source": SOURCE, "unexpected": 1}, [], False, "history"):
            with self.subTest(history=value):
                result = zemax_list_design_tasks(PROJECT, value)
                self.assertEqual(result["status"], "error")
        for source in ({**SOURCE, "other": 1}, {**SOURCE, "service_instance_id": ""}, None):
            self.assert_error(self.read(source=source), "WORKFLOW_HISTORY_SOURCE_REQUIRED")
        self.assert_error(self.read(source={**SOURCE, "service_instance_id": "old-service"}), "WORKBENCH_INSTANCE_MISMATCH")
        self.assert_error(self.read(source={**SOURCE, "project_name": "history_b"}), "WORKFLOW_PROJECT_MISMATCH")
        self.active_path.write_text('{"active_project":"history_b"}', encoding="utf-8")
        self.assert_error(self.read(), "WORKFLOW_PROJECT_MISMATCH")
        self.assertFalse(self.root.exists())

    def test_missing_or_inconsistent_trusted_observation_is_rejected(self):
        for observed in (None, {}, {"service_instance_id": SOURCE["service_instance_id"], "context": {}},
                         {"service_instance_id": SOURCE["service_instance_id"],
                          "context": {"service_instance_id": "other", "active_project": PROJECT}}):
            token = OBSERVATION.set(observed)
            try:
                self.assert_error(self.read(), "WORKFLOW_HISTORY_SOURCE_REQUIRED")
            finally:
                OBSERVATION.reset(token)
        self.assertFalse(self.root.exists())

    def test_cursor_validates_exact_schema_scope_and_decimal_strings(self):
        base = {"v": 1, **SOURCE, "upper": "25", "before": "6"}
        invalid = ["", "a", "not+base64", "A" * 1025, 1, {}, True,
                   encode_cursor(b"[]"), encode_cursor(b"\xff"), encode_cursor(b"{")]
        for update in ({"v": True}, {"v": 2}, {"other": 1}, {"upper": 25}, {"before": "0"},
                       {"before": "06"}, {"before": "-1"}, {"before": "26"},
                       {"upper": str(history.MAX_ROWID + 1)}, {"upper": "1e2"},
                       {"project_name": "history_b"}, {"service_instance_id": "old-service"}):
            invalid.append(encode_cursor({**base, **update}))
        invalid.append(encode_cursor(base) + "=")
        invalid.append(encode_cursor(json.dumps(base)[:-1].encode() + b',"v":1}'))
        for value in invalid:
            with self.subTest(cursor=value):
                self.assert_error(self.read(cursor=value), "WORKFLOW_HISTORY_CURSOR_INVALID")
        self.assertFalse(self.root.exists())

    def test_byte_budget_shrinks_whole_tasks_and_preserves_traversal(self):
        self.seed(108, extra=lambda i: {"diagnostic": "证" * 80000} if i <= 8 else {})
        first = self.read(limit=8)
        self.assertEqual(first["status"], "success", first)
        self.assertGreater(len(first["tasks"]), 0)
        self.assertLess(len(first["tasks"]), 8)
        pages, current = [first], first
        while current["has_more"]:
            current = self.read(limit=8, cursor=current["next_cursor"])
            pages.append(current)
        all_tasks = [t for page in pages for t in page["tasks"]]
        self.assertEqual([t["request_id"] for t in all_tasks], [f"{PROJECT}-{i}" for i in range(8, 0, -1)])
        for page in pages:
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False).encode("utf-8")), 1048576)
            for item in page["tasks"]:
                self.assertEqual(item["diagnostic"], "证" * 80000)
            if page["has_more"]:
                last_index = page["tasks"][-1]["request_id"].rsplit("-", 1)[-1]
                self.assertEqual(decode_cursor(page["next_cursor"])["before"], last_index)

    def test_single_oversize_task_is_an_error_and_probe_is_not_skipped(self):
        self.seed(103, extra=lambda i: {"diagnostic": "文" * 400000} if i == 1 else {})
        first = self.read(limit=2)
        self.assertEqual([t["request_id"] for t in first["tasks"]], [f"{PROJECT}-3", f"{PROJECT}-2"])
        self.assert_error(self.read(limit=2, cursor=first["next_cursor"]), "WORKFLOW_TOO_LARGE")

    def test_recursive_case_insensitive_redaction_preserves_original_model_and_text(self):
        old_model = {"service_instance_id": "old-model-service", "model_id": "old-model", "model_revision": 7}
        secret = {"ToKeN": "do-not-return", "lease_TOKEN": "private", "TOKEN_HASH": "private",
                  "milestone_attempts": [{"note": "private"}], "safe": [{"Token": "private", "label": "kept"}]}
        self.seed(101, extra=lambda i: {"model_reference": old_model, "nested": deepcopy(secret),
                                       "milestone_attempts": [{"note": "private"}], "Token": "private",
                                       "result_text": "token 字样是自然语言，保留原文"} if i == 1 else {})
        result = self.read()
        item = result["tasks"][0]
        self.assertEqual(item["model_reference"], old_model)
        self.assertEqual(item["nested"], {"safe": [{"label": "kept"}]})
        self.assertNotIn("Token", item)
        self.assertNotIn("milestone_attempts", item)
        self.assertEqual(item["result_text"], "token 字样是自然语言，保留原文")
        self.assertTrue(item["stale"])
        token = flow.MODEL_REFERENCE.set(old_model)
        try:
            self.assertFalse(self.read()["tasks"][0]["stale"])
        finally:
            flow.MODEL_REFERENCE.reset(token)

    def test_missing_database_does_not_create_any_directory_or_file(self):
        self.assertEqual(self.read()["tasks"], [])
        self.assertFalse(self.root.exists())
        self.native.assert_not_called()

    def test_bad_json_and_invalid_rows_are_reported_without_record_details(self):
        self.seed(101)
        variants = ["{not-json-private-detail", '{"status":"completed","status":"failed"}', "[]",
                    json.dumps(task(f"{PROJECT}-1", version=True)),
                    json.dumps(task(f"{PROJECT}-1", project="history_b")),
                    json.dumps(task(f"{PROJECT}-1", kind="unknown")),
                    json.dumps(task(f"{PROJECT}-1", status="unknown")),
                    json.dumps(task(f"{PROJECT}-1", stale=1)),
                    json.dumps(task("different-id")),
                    json.dumps(task(f"{PROJECT}-1", result_text=12)),
                    json.dumps(task(f"{PROJECT}-1", requirements_revision=-1))]
        for value in variants:
            with self.subTest(value=value[:120]):
                with closing(sqlite3.connect(self.db_path)) as db, db:
                    db.execute("UPDATE tasks SET data=? WHERE id=?", (value, f"{PROJECT}-1"))
                result = self.read()
                self.assert_error(result, "WORKFLOW_HISTORY_UNAVAILABLE")
                self.assertNotIn("private-detail", result["message"])

    def test_corrupt_draft_or_proposal_is_safe_failure(self):
        self.seed(101)
        for value in ('{"revision":true}', '{"revision":1,"revision":2}', "invalid-private", "[]"):
            with closing(sqlite3.connect(self.db_path)) as db, db:
                db.execute("UPDATE state SET data=? WHERE project=?", (value, PROJECT))
            self.assert_error(self.read(), "WORKFLOW_HISTORY_UNAVAILABLE")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("UPDATE state SET data=? WHERE project=?", ('{"revision":1}', PROJECT))
        proposal = self.root / PROJECT / "design_proposal.json"
        proposal.parent.mkdir()
        for value in ("private-invalid-json", "[]"):
            proposal.write_text(value, encoding="utf-8")
            self.assert_error(self.read(), "WORKFLOW_HISTORY_UNAVAILABLE")

    def test_corrupt_incompatible_and_unreadable_storage_are_not_empty_success(self):
        self.root.mkdir()
        self.db_path.write_bytes(b"this-is-not-a-sqlite-database-private-path")
        self.assert_error(self.read(), "WORKFLOW_HISTORY_UNAVAILABLE")
        self.db_path.unlink()
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("CREATE TABLE tasks(id TEXT)")
        self.assert_error(self.read(), "WORKFLOW_HISTORY_UNAVAILABLE")
        with patch.object(history.sqlite3, "connect", side_effect=OSError("private-path-and-error")):
            result = self.read()
        self.assert_error(result, "WORKFLOW_HISTORY_UNAVAILABLE")
        self.assertNotIn("private-path", result["message"])

    def test_read_connection_and_sql_cannot_write_or_archive(self):
        self.seed(125, extra=lambda i: {"status": "claimed", "requirements_revision": 0} if i == 3 else {})
        before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        statements, attempts, connections = [], [], []
        original_connect = sqlite3.connect
        deny = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE,
                sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX}

        def authorizer(action, arg1, arg2, _db, _trigger):
            if action in deny:
                attempts.append((action, arg1, arg2))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def readonly_connect(*args, **kwargs):
            self.assertTrue(args[0].endswith("?mode=ro"))
            self.assertTrue(kwargs.get("uri"))
            db = original_connect(*args, **kwargs)
            db.set_authorizer(authorizer)
            db.set_trace_callback(statements.append)
            connections.append(db)
            return db

        guards = [patch.object(flow, name, side_effect=AssertionError(f"forbidden workflow helper {name}"))
                  for name in ("transaction", "view", "task_list", "_archive_invalid_tasks", "_save", "_put_task", "revoke")]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)
        with patch.object(history.sqlite3, "connect", side_effect=readonly_connect):
            result = self.read()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(attempts, [])
        self.assertIn("PRAGMA query_only=ON", statements)
        self.assertIn("BEGIN", statements)
        self.assertEqual(before, hashlib.sha256(self.db_path.read_bytes()).hexdigest())
        self.assertFalse(any("IMMEDIATE" in statement or "journal_mode" in statement.lower() for statement in statements))
        with closing(original_connect(self.db_path)) as db, db:
            stored = json.loads(db.execute("SELECT data FROM tasks WHERE id=?", (f"{PROJECT}-3",)).fetchone()[0])
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(stored["version"], 2)
        self.native.assert_not_called()
        for db in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_none_preserves_original_list_path_and_empty_object_never_falls_back(self):
        sentinel = {"status": "success", "project_name": PROJECT, "tasks": ["old-path"]}
        model = {"model_id": "current-model"}
        token = flow.MODEL_REFERENCE.set(model)
        try:
            with patch.object(flow, "task_list", return_value=sentinel) as current:
                self.assertIs(zemax_list_design_tasks(PROJECT), sentinel)
                self.assertIs(zemax_list_design_tasks(PROJECT, None), sentinel)
                current.assert_called_with(PROJECT, model)
                self.assertEqual(current.call_count, 2)
                self.assert_error(zemax_list_design_tasks(PROJECT, {}), "WORKFLOW_HISTORY_SOURCE_REQUIRED")
                self.assertEqual(current.call_count, 2)
        finally:
            flow.MODEL_REFERENCE.reset(token)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
