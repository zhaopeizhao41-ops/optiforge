"""Public control failures never echo service or exception text.

HTTP responses and workflow observations are synthetic; no native or optical
engine is invoked. Existing HTTP control tests cover normal navigation receipts.
"""
import io
import json
from http.client import BadStatusLine, IncompleteRead
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_control import (ERROR_BODY_LIMIT, PUBLIC_ERROR_MESSAGES, REMOTE_ERROR_CODES,
    WorkbenchControlClient, WorkbenchControlError, public_control_error)

SECRET = "FAKE_SECRET_CANARY_82b123c"
PRIVATE_PATH = r"C:\FAKE_PRIVATE_PATH_CANARY\service.json"
INSTANCE = "error-test-instance"
PROJECT = "error_test_project"
URL = "http://127.0.0.1:12345"


class TrackingBody(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.requested = []
        self.was_closed = False

    def read(self, size=-1):
        self.requested.append(size)
        return super().read(size)

    def close(self):
        self.was_closed = True
        super().close()


class BrokenBody(TrackingBody):
    def read(self, size=-1):
        self.requested.append(size)
        raise OSError(SECRET + PRIVATE_PATH)


class ControlErrorTests(unittest.TestCase):
    def setUp(self):
        self.client = WorkbenchControlClient(URL, {
            "token": "fake-test-token", "service_instance_id": INSTANCE, "protocol_version": 2})
        self.request = urllib.request.Request(URL + "/api/ui/status")

    def assert_public(self, value, code="WORKBENCH_HTTP_ERROR"):
        self.assertEqual(value, {"status": "error", "code": code,
                                 "message": PUBLIC_ERROR_MESSAGES[code]})
        wire = json.dumps(value, ensure_ascii=False)
        self.assertNotIn(SECRET, wire)
        self.assertNotIn(PRIVATE_PATH, wire)

    def failed_open(self, raw, *, body_class=TrackingBody):
        body = body_class(raw)
        error = urllib.error.HTTPError(URL + "/" + SECRET, 409,
                                       PRIVATE_PATH + SECRET, {}, body)
        opener = Mock()
        opener.open.side_effect = error
        with patch("core.workbench_control.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(WorkbenchControlError) as caught:
                self.client._open(self.request, 3)
        self.assertTrue(body.was_closed)
        self.assertEqual(body.requested, [ERROR_BODY_LIMIT + 1])
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(SECRET, str(caught.exception))
        return public_control_error(caught.exception)

    def test_known_codes_have_fixed_messages_and_immutable_registry(self):
        for code in PUBLIC_ERROR_MESSAGES:
            with self.subTest(code=code):
                self.assert_public(public_control_error(WorkbenchControlError(code, SECRET + PRIVATE_PATH)), code)
        with self.assertRaises(TypeError):
            PUBLIC_ERROR_MESSAGES["SECRET"] = SECRET

    def test_unknown_or_non_string_codes_never_echo_or_crash(self):
        for code in (None, True, 7, [SECRET], {SECRET: PRIVATE_PATH}, "", SECRET,
                     "UI_SESSION_UNKNOWN " + SECRET, "x" * 20000):
            with self.subTest(type=type(code).__name__):
                self.assert_public(public_control_error(WorkbenchControlError(code, SECRET)))

    def test_projection_does_not_read_exception_message(self):
        class WithPrivateMessage:
            code = "UI_SESSION_OFFLINE"
            @property
            def message(self):
                raise AssertionError("message must never be read")
        self.assert_public(public_control_error(WithPrivateMessage()), "UI_SESSION_OFFLINE")

    def test_known_http_codes_keep_only_local_fixed_description(self):
        for code in ("UI_SESSION_UNKNOWN", "REQUEST_ID_CONFLICT", "PAGE_UNAVAILABLE",
                     "WORKBENCH_PROTOCOL_INCOMPATIBLE"):
            with self.subTest(code=code):
                raw = json.dumps({"code": code, "message": SECRET, "path": PRIVATE_PATH,
                                  "token": SECRET}).encode()
                self.assert_public(self.failed_open(raw), code)

    def test_http_payload_codes_of_wrong_types_are_fixed_fallback(self):
        for code in (None, True, 1, [], {}, SECRET):
            with self.subTest(type=type(code).__name__):
                self.assert_public(self.failed_open(json.dumps({"code": code, "message": SECRET}).encode()))

    def test_remote_cannot_claim_local_only_configuration_or_waiter_failures(self):
        self.assertLessEqual(REMOTE_ERROR_CODES, set(PUBLIC_ERROR_MESSAGES))
        for code in set(PUBLIC_ERROR_MESSAGES) - REMOTE_ERROR_CODES:
            with self.subTest(code=code):
                self.assert_public(self.failed_open(json.dumps({"code": code, "message": SECRET}).encode()))
                value = {"protocol_version": 2, "service_instance_id": INSTANCE,
                         "status": "error", "code": code, "message": SECRET}
                with patch.object(self.client, "_verify_instance"), patch.object(self.client, "_open", return_value=value):
                    with self.assertRaises(WorkbenchControlError) as caught:
                        self.client.status()
                self.assert_public(public_control_error(caught.exception))

    def test_http_scalar_array_empty_and_invalid_bodies_fail_closed(self):
        for raw in (b"[]", b"null", b"true", b"12", b'"text"', b"", b"<html>", b"\xff",
                    b'{"code":', b'{"code":"UI_SESSION_UNKNOWN", "x": NaN}',
                    b'{"code":"UI_SESSION_UNKNOWN", "x": Infinity}'):
            with self.subTest(raw=raw):
                self.assert_public(self.failed_open(raw))

    def test_duplicate_keys_at_any_depth_and_recursive_json_fail_closed(self):
        for raw in (b'{"code":"UI_SESSION_UNKNOWN","code":"REQUEST_ID_CONFLICT"}',
                    b'{"code":"UI_SESSION_UNKNOWN","nested":{"a":1,"a":2}}',
                    b'{"code":"UI_SESSION_UNKNOWN","deep":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"):
            with self.subTest(length=len(raw)):
                self.assert_public(self.failed_open(raw))

    def test_error_body_limit_is_enforced_before_json_code_is_trusted(self):
        core = b'{"code":"UI_SESSION_UNKNOWN"}'
        self.assert_public(self.failed_open(core + b" " * (ERROR_BODY_LIMIT - len(core))), "UI_SESSION_UNKNOWN")
        self.assert_public(self.failed_open(core + b" " * (ERROR_BODY_LIMIT + 1 - len(core))))

    def test_error_body_read_failure_is_closed_and_redacted(self):
        self.assert_public(self.failed_open(b"ignored", body_class=BrokenBody))

    def test_network_error_reason_is_not_chained_or_serialized(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(SECRET + PRIVATE_PATH)
        with patch("core.workbench_control.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(WorkbenchControlError) as caught:
                self.client._open(self.request, 3)
        self.assert_public(public_control_error(caught.exception), "WORKBENCH_UNAVAILABLE")
        self.assertTrue(caught.exception.__suppress_context__)

    def test_http_protocol_read_and_parser_errors_have_safe_connection_failure(self):
        for error in (BadStatusLine(SECRET + PRIVATE_PATH), IncompleteRead(SECRET.encode(), 100),
                      RecursionError(SECRET + PRIVATE_PATH)):
            with self.subTest(type=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                with patch("core.workbench_control.urllib.request.build_opener", return_value=opener):
                    with self.assertRaises(WorkbenchControlError) as caught:
                        self.client._open(self.request, 3)
                self.assert_public(public_control_error(caught.exception), "WORKBENCH_UNAVAILABLE")
                self.assertTrue(caught.exception.__suppress_context__)

    def test_http_200_explicit_error_is_projected_before_status_processing(self):
        for code, expected in ((SECRET, "WORKBENCH_HTTP_ERROR"), ({SECRET: PRIVATE_PATH}, "WORKBENCH_HTTP_ERROR"),
                               ("UI_SESSION_OFFLINE", "UI_SESSION_OFFLINE")):
            with self.subTest(expected=expected):
                value = {"protocol_version": 2, "service_instance_id": INSTANCE, "status": "error",
                         "code": code, "message": SECRET, "private_path": PRIVATE_PATH}
                with patch.object(self.client, "_verify_instance"), patch.object(self.client, "_open", return_value=value):
                    with self.assertRaises(WorkbenchControlError) as caught:
                        self.client.status()
                self.assert_public(public_control_error(caught.exception), expected)

    def test_error_envelope_does_not_override_protocol_or_instance_verification(self):
        for changes, expected in (({"protocol_version": 1}, "WORKBENCH_PROTOCOL_INCOMPATIBLE"),
                                  ({"service_instance_id": "other"}, "WORKBENCH_INSTANCE_MISMATCH")):
            value = {"protocol_version": 2, "service_instance_id": INSTANCE,
                     "status": "error", "code": "UI_SESSION_OFFLINE", "message": SECRET, **changes}
            with patch.object(self.client, "_verify_instance"), patch.object(self.client, "_open", return_value=value):
                with self.assertRaises(WorkbenchControlError) as caught:
                    self.client.status()
            self.assert_public(public_control_error(caught.exception), expected)

    def test_success_response_fields_remain_unchanged_and_token_is_still_removed(self):
        value = {"protocol_version": 2, "service_instance_id": INSTANCE, "status": "accepted",
                 "command_id": "command-known", "remaining_ms": 5000, "nested": {"token": "private", "data": 3}}
        with patch.object(self.client, "_verify_instance"), patch.object(self.client, "_open", return_value=value):
            result = self.client.navigate(INSTANCE, "session_known", "analysis", "request_known")
        self.assertEqual(result, {**value, "nested": {"data": 3}})
        self.assertNotIn("token", json.dumps(result))

    def test_server_common_wrapper_projects_all_control_entry_errors(self):
        import server
        for code in (SECRET, [], "UI_SESSION_UNKNOWN"):
            for method, arguments in (("status", ()), ("events", (INSTANCE, 0, 0)),
                                      ("navigate", (INSTANCE, "session_known", "analysis", "request_known"))):
                with self.subTest(method=method, type=type(code).__name__):
                    control = Mock()
                    getattr(control, method).side_effect = WorkbenchControlError(code, SECRET + PRIVATE_PATH)
                    with patch.object(server.WorkbenchControlClient, "from_environment", return_value=control):
                        result = json.loads(getattr(server, "optiforge_workbench_" + method)(*arguments))
                    self.assert_public(result, code if type(code) is str and code in PUBLIC_ERROR_MESSAGES else "WORKBENCH_HTTP_ERROR")

    def test_server_wait_wrapper_projects_exception_code_and_message_independently(self):
        import server
        for code in (SECRET, {SECRET: PRIVATE_PATH}, "WORKFLOW_READ_FAILED", "WORKFLOW_PROJECT_MISMATCH"):
            with self.subTest(type=type(code).__name__):
                waiter = Mock()
                waiter.wait_for_task.side_effect = WorkbenchControlError(code, SECRET + PRIVATE_PATH)
                with patch.object(server.WorkbenchControlClient, "from_environment", return_value=Mock()), \
                     patch.object(server, "WorkbenchTaskWaiter", return_value=waiter):
                    result = json.loads(server.optiforge_workbench_wait_for_task(INSTANCE, PROJECT, 0, 0))
                self.assert_public(result, code if type(code) is str and code in PUBLIC_ERROR_MESSAGES else "WORKBENCH_HTTP_ERROR")

    def test_real_waiter_read_error_code_cannot_escape_through_second_wrapper(self):
        import server
        context = {"service_instance_id": INSTANCE, "active_project": PROJECT}
        control = SimpleNamespace(instance=INSTANCE,
            status=lambda: {"protocol_version": 2, "service_instance_id": INSTANCE, "context": context,
                            "capabilities": {"collaboration": True, "workflow_schema_version": 1}},
            events=lambda *args: {"protocol_version": 2, "service_instance_id": INSTANCE,
                                  "events": [], "next_seq": 0, "reset_required": False})
        for code in (SECRET, {SECRET: PRIVATE_PATH}, "WORKFLOW_PROJECT_MISMATCH"):
            envelope = {"protocol_version": 2, "context": context,
                        "result": {"status": "error", "code": code, "message": SECRET}}
            with patch.object(server.WorkbenchControlClient, "from_environment", return_value=control), \
                 patch.object(server.WorkbenchTaskWaiter, "_observe", return_value=envelope):
                result = json.loads(server.optiforge_workbench_wait_for_task(INSTANCE, PROJECT, 0, 0))
            self.assert_public(result, code if type(code) is str and code in PUBLIC_ERROR_MESSAGES else "WORKBENCH_HTTP_ERROR")


if __name__ == "__main__":
    unittest.main()
