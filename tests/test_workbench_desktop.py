"""Isolated adapter tests: all HTTP and collectors are doubles, no desktop read."""
from copy import deepcopy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import workbench_desktop as desktop
from core.workbench_control import WorkbenchControlClient, WorkbenchControlError


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="optiforge-desktop-mcp-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "output" / "workbench_service" / "service.json"
        self.file.parent.mkdir(parents=True)
        self.url = "http://127.0.0.1:43210"
        self.instance = "test-instance"
        self.token = "SECRET_TOKEN_DO_NOT_DISCLOSE"
        self.credential = {"url": self.url, "protocol_version": 2,
                           "service_instance_id": self.instance, "token": self.token}
        self.file.write_text(json.dumps(self.credential), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "OPTIFORGE_SERVICE_URL": self.url, "OPTIFORGE_SERVICE_FILE": str(self.file)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.platform = patch.object(desktop.sys, "platform", "win32")
        self.platform.start()
        self.addCleanup(self.platform.stop)
        self.http = patch.object(WorkbenchControlClient, "_open", side_effect=self.session)
        self.http_mock = self.http.start()
        self.addCleanup(self.http.stop)
        self.collector = patch.object(desktop, "_collect", side_effect=lambda *_: self.encode(self.payload()))
        self.collect_mock = self.collector.start()
        self.addCleanup(self.collector.stop)

    def session(self, request, timeout):
        self.assertEqual(request.full_url, self.url + "/api/session")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("X-optiforge-token"), self.token)
        return {"protocol_version": 2, "service_instance_id": self.instance, "token": self.token}

    def payload(self, status="unique_candidate"):
        row = {"hwnd": "9007199254740993", "process_id": 200,
               "process_created_at": "2026-11-03T00:00:01.1234567Z", "browser": "Chrome",
               "visible": True, "minimized": False, "class_name": "SECRET_CLASS",
               "bounds": {"left": -10, "top": 20, "right": 1430, "bottom": 980,
                          "coordinates": desktop.COORDINATES}, "dpi": 144, "url_binding": "unverified"}
        rows = [row] if status == "unique_candidate" else []
        if status == "ambiguous":
            rows = [row, {**deepcopy(row), "hwnd": "100"}]
        return {"schema_version": 1, "status": status,
                "reason": "diagnostic_unavailable" if status == "unavailable" else "stable_profile_observation",
                "url_binding": "unverified", "observation": desktop.CLI_OBSERVATION,
                "observed_at": "2026-11-03T00:00:02.1234567Z",
                "service": {"endpoint": self.url + "/", "instance_id": self.instance, "listener_pid": 123},
                "profiles": [{"browser": "Chrome", "profile_path": str(self.root / "output" / "desktop_profile" / "chrome")}],
                "candidates": rows}

    @staticmethod
    def encode(payload):
        return json.dumps(payload).encode("utf-8")

    def call(self, payload=None, browser="Auto"):
        if payload is not None:
            self.collect_mock.side_effect = None
            self.collect_mock.return_value = self.encode(payload)
        return desktop.desktop_diagnostics(self.instance, browser)

    def assert_unavailable(self, result, reason=None):
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["url_binding"], "unverified")
        self.assertIs(result["action_supported"], False)
        if reason:
            self.assertEqual(result["reason"], reason)
        self.assertNotIn(self.token, json.dumps(result))

    def test_verified_candidates_are_whitelisted_not_url_or_action_authority(self):
        result = self.call()
        self.assertEqual(result["status"], "unique_candidate")
        self.assertIs(result["action_supported"], False)
        self.assertEqual(result["url_binding"], "unverified")
        self.assertEqual(result["service"], {"service_instance_id": self.instance, "listener_pid": 123})
        self.assertEqual(result["candidates"][0]["hwnd"], "9007199254740993")
        self.assertEqual(result["candidates"][0]["bounds"]["left"], -10)
        self.assertEqual(self.http_mock.call_count, 2)
        self.collect_mock.assert_called_once_with(self.root, "Auto")
        text = json.dumps(result)
        for private in (self.token, str(self.root), "SECRET_CLASS", "profile_path", "CommandLine", "selected", "endpoint"):
            self.assertNotIn(private, text)

    def test_each_cli_status_is_retained_with_consistent_cardinality(self):
        for status in ("not_found", "ambiguous", "unavailable"):
            with self.subTest(status=status):
                result = self.call(self.payload(status))
                self.assertEqual(result["status"], status)
                self.assertEqual(len(result["candidates"]), 2 if status == "ambiguous" else 0)

    def test_supported_browser_scope_rejects_foreign_profile(self):
        self.assertEqual(self.call(browser="Chrome")["status"], "unique_candidate")
        self.assert_unavailable(self.call(browser="Edge"), "collector_result_invalid")

    def test_invalid_direct_argument_types_are_rejected_without_reads(self):
        for instance, browser in ((None, "Auto"), (True, "Auto"), (1, "Auto"), ("", "Auto"),
                                  ("SECRET\nINSTANCE", "Auto"), (self.instance, True),
                                  (self.instance, "chrome"), (self.instance, {"shell": "x"})):
            with self.subTest(instance=instance, browser=browser):
                self.assert_unavailable(desktop.desktop_diagnostics(instance, browser), "invalid_arguments")
        self.http_mock.assert_not_called()
        self.collect_mock.assert_not_called()

    def test_unsupported_platform_returns_before_configuration_or_collection(self):
        with patch.object(desktop.sys, "platform", "linux"), patch.object(desktop, "_read_binding", side_effect=AssertionError("no read")):
            self.assert_unavailable(self.call(), "unsupported_platform")
        self.collect_mock.assert_not_called()

    def test_wrong_instance_does_not_run_collector_or_query_http(self):
        self.assert_unavailable(desktop.desktop_diagnostics("different-instance"), "instance_mismatch")
        self.collect_mock.assert_not_called()
        self.http_mock.assert_not_called()

    def test_localhost_and_noncanonical_endpoints_are_not_silently_normalized(self):
        for endpoint in ("http://localhost:43210", "http://127.0.0.1:043210", "http://127.0.0.1:0", "http://127.0.0.1:65536",
                         "https://127.0.0.1:43210", self.url + "/?x=secret", "http://user@127.0.0.1:43210", self.url + "/path"):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": endpoint}):
                self.assert_unavailable(self.call(), "unsupported_endpoint")
        self.collect_mock.assert_not_called()

    def test_strict_absolute_standard_path_and_alias_rejection(self):
        bad = ("output\\workbench_service\\service.json", "C:relative\\service.json", "\\\\server\\share\\service.json",
               "\\\\?\\C:\\service.json", "C:\\root\\..\\output\\workbench_service\\service.json",
               "C:\\root\\.\\output\\workbench_service\\service.json", "C:\\root \\output\\workbench_service\\service.json",
               "C:\\root.\\output\\workbench_service\\service.json", "C:\\PROGRA~1\\output\\workbench_service\\service.json",
               "C:\\root\\output\\workbench_service\\service.json:stream", str(self.file).replace("\\", "/"))
        for path in bad:
            with self.subTest(path=path), patch.dict(os.environ, {"OPTIFORGE_SERVICE_FILE": path}):
                self.assert_unavailable(self.call(), "unsupported_path")
        other = self.root / "service.json"
        other.write_text(json.dumps(self.credential), encoding="utf-8")
        with patch.dict(os.environ, {"OPTIFORGE_SERVICE_FILE": str(other)}):
            self.assert_unavailable(self.call(), "unsupported_path")
        self.collect_mock.assert_not_called()

    def test_existing_reparse_ancestor_is_rejected(self):
        real_lstat = Path.lstat
        class Reparse:
            st_mode = 0
            st_file_attributes = 0x400
        def flagged(path):
            return Reparse() if path == self.file.parent else real_lstat(path)
        with patch.object(Path, "lstat", flagged):
            self.assert_unavailable(self.call(), "unsupported_path")
        self.collect_mock.assert_not_called()

    def test_resolved_alias_is_rejected_instead_of_retargeted(self):
        with patch.object(Path, "resolve", return_value=self.root / "another-binding"):
            self.assert_unavailable(self.call(), "unsupported_path")

    def test_credential_duplicate_keys_protocol_types_url_and_missing_token_fail_closed(self):
        samples = [b'{"token":"one","token":"two"}']
        for field, value in (("token", None), ("service_instance_id", "bad\ninstance"), ("protocol_version", True), ("url", self.url + "/")):
            data = {**self.credential, field: value}
            samples.append(self.encode(data))
        for raw in samples:
            with self.subTest(raw=raw):
                self.file.write_bytes(raw)
                self.assert_unavailable(self.call())
        self.collect_mock.assert_not_called()

    def test_http_error_payload_never_reaches_response(self):
        self.http_mock.side_effect = WorkbenchControlError("SECRET_CODE", "SECRET_PATH " + self.token)
        result = self.call()
        self.assert_unavailable(result, "service_unavailable")
        self.assertNotIn("SECRET", json.dumps(result))
        self.collect_mock.assert_not_called()

    def test_http_session_swap_at_either_verification_blocks_candidates(self):
        good = self.session_from_fields()
        for replacement in ({**good, "service_instance_id": "other-instance"}, {**good, "token": "different-token"},
                            {**good, "protocol_version": 1}, []):
            for sequence in ([replacement], [good, replacement]):
                with self.subTest(sequence=sequence):
                    self.http_mock.side_effect = sequence
                    self.assert_unavailable(self.call(), "service_unavailable")

    def session_from_fields(self):
        return {"protocol_version": 2, "service_instance_id": self.instance, "token": self.token}

    def test_collector_cannot_swap_config_credentials_or_same_content_file_identity(self):
        for mutation in ("env_url", "env_file", "token", "instance", "same_content_replace"):
            with self.subTest(mutation=mutation):
                self.file.write_text(json.dumps(self.credential), encoding="utf-8")
                def collect(*_):
                    if mutation == "env_url":
                        os.environ["OPTIFORGE_SERVICE_URL"] = self.url + "/"
                    elif mutation == "env_file":
                        os.environ["OPTIFORGE_SERVICE_FILE"] = str(self.root / "unknown.json")
                    elif mutation == "same_content_replace":
                        replacement = self.file.with_name("replacement.json")
                        replacement.write_bytes(self.file.read_bytes())
                        replacement.replace(self.file)
                    else:
                        self.file.write_text(json.dumps({**self.credential, mutation if mutation == "token" else "service_instance_id": "changed"}), encoding="utf-8")
                    return self.encode(self.payload())
                self.collect_mock.side_effect = collect
                self.assert_unavailable(self.call())
                os.environ["OPTIFORGE_SERVICE_URL"] = self.url
                os.environ["OPTIFORGE_SERVICE_FILE"] = str(self.file)

    def test_config_changed_during_http_verification_is_rechecked(self):
        calls = 0
        def verify(request, timeout):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.file.write_text(json.dumps({**self.credential, "token": "changed"}), encoding="utf-8")
            return self.session(request, timeout)
        self.http_mock.side_effect = verify
        self.assert_unavailable(self.call(), "configuration_changed")

    def test_raw_output_duplicate_keys_constants_encoding_and_size_are_rejected(self):
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"status":NaN}', b'[]', b'\xff\x00', b'{} trailing', b'x' * (desktop.OUTPUT_LIMIT + 1)):
            with self.subTest(length=len(raw)):
                self.collect_mock.side_effect = None
                self.collect_mock.return_value = raw
                self.assert_unavailable(self.call())

    def test_output_schema_and_status_cardinality_conflicts(self):
        changes = (("schema_version", True), ("schema_version", 2), ("status", "done"), ("status", []),
                   ("reason", "SECRET_RAW_ERROR"), ("reason", []), ("url_binding", "verified"),
                   ("action_supported", True), ("selected", "9007199254740993"), ("observation", "SECRET"),
                   ("observed_at", "yesterday"), ("service", None), ("profiles", None), ("candidates", {}))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                payload = self.payload(); payload[key] = value
                self.assert_unavailable(self.call(payload), "collector_result_invalid")
        for status, count in (("unique_candidate", 0), ("ambiguous", 1), ("not_found", 1), ("unavailable", 1)):
            payload = self.payload(); payload["status"] = status
            payload["reason"] = "diagnostic_unavailable" if status == "unavailable" else "stable_profile_observation"
            payload["candidates"] = payload["candidates"][:count]
            self.assert_unavailable(self.call(payload), "collector_result_invalid")

    def test_cli_service_endpoint_instance_and_pid_must_be_valid_not_invented(self):
        for key, value in (("endpoint", self.url + "/other"), ("instance_id", "other"), ("listener_pid", True),
                           ("listener_pid", 0), ("listener_pid", 2**32), ("listener_pid", "123")):
            payload = self.payload(); payload["service"][key] = value
            self.assert_unavailable(self.call(payload), "collector_result_invalid")
        payload = self.payload(); payload["service"]["listener_pid"] = 777
        self.assertEqual(self.call(payload)["service"]["listener_pid"], 777)

    def test_profiles_only_match_exact_expected_scope_and_are_never_returned(self):
        for change in ("suffix", "dot", "duplicate", "unknown_browser", "unhashable"):
            payload = self.payload()
            if change == "suffix": payload["profiles"][0]["profile_path"] += "-other"
            elif change == "dot": payload["profiles"][0]["profile_path"] += "\\..\\chrome"
            elif change == "duplicate": payload["profiles"] *= 2
            elif change == "unknown_browser": payload["profiles"][0]["browser"] = "Other"
            else: payload["profiles"][0]["browser"] = []
            self.assert_unavailable(self.call(payload), "collector_result_invalid")

    def test_candidate_numeric_boolean_handle_and_date_boundaries(self):
        changes = (("hwnd", 123), ("hwnd", "01"), ("hwnd", "0"), ("hwnd", str(2**64)),
                   ("process_id", True), ("process_id", "200"), ("process_id", 0), ("visible", False),
                   ("minimized", 1), ("dpi", True), ("dpi", 0), ("dpi", "144"),
                   ("process_created_at", "2026-99-01T00:00:00Z"), ("url_binding", "verified"),
                   ("browser", "Edge"), ("browser", []), ("action_supported", True), ("selected", "123"))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                payload = self.payload(); payload["candidates"][0][key] = value
                self.assert_unavailable(self.call(payload), "collector_result_invalid")
        payload = self.payload(); payload["candidates"][0]["dpi"] = None
        payload["candidates"][0]["minimized"] = True
        self.assertIsNone(self.call(payload)["candidates"][0]["dpi"])

    def test_bounds_and_duplicate_handle_process_conflicts(self):
        for key, value in (("left", True), ("right", 2**31), ("bottom", -9999), ("coordinates", "SECRET_RAW")):
            payload = self.payload(); payload["candidates"][0]["bounds"][key] = value
            self.assert_unavailable(self.call(payload), "collector_result_invalid")
        payload = self.payload("ambiguous"); payload["candidates"][1]["hwnd"] = payload["candidates"][0]["hwnd"]
        self.assert_unavailable(self.call(payload), "collector_result_invalid")
        payload = self.payload("ambiguous"); payload["candidates"][1]["process_created_at"] = "2026-11-03T00:00:03Z"
        self.assert_unavailable(self.call(payload), "collector_result_invalid")

    def test_unknown_private_payload_fields_are_not_serialized(self):
        payload = self.payload()
        payload.update({"token": self.token, "Credential": {"password": "SECRET"}, "CommandLine": "SECRET_COMMAND"})
        payload["service"]["private"] = "SECRET"
        payload["profiles"][0]["executable_path"] = "SECRET"
        payload["candidates"][0].update({"title": "SECRET", "CommandLine": "SECRET", "profile": "SECRET"})
        text = json.dumps(self.call(payload))
        self.assertNotIn("SECRET", text)
        self.assertNotIn(str(self.root), text)

    def test_utf8_unicode_profile_input_is_validated_then_omitted(self):
        binding = desktop._read_binding()
        workspace = self.root / "中文工作台"
        binding = replace(binding, workspace=workspace)
        payload = self.payload()
        payload["profiles"][0]["profile_path"] = str(workspace / "output" / "desktop_profile" / "chrome")
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result = desktop._sanitize(raw, binding, "Auto")
        self.assertEqual(result["status"], "unique_candidate")
        self.assertNotIn("中文工作台", json.dumps(result, ensure_ascii=False))

    def test_collection_errors_are_fixed_and_no_candidate_escapes(self):
        for exception in (desktop._Failure("collector_timeout"), desktop._Failure("collector_output_too_large"),
                          RuntimeError("SECRET_EXCEPTION")):
            self.collect_mock.side_effect = exception
            result = self.call()
            self.assert_unavailable(result)
            self.assertNotIn("SECRET", json.dumps(result))

    def test_mcp_wrapper_is_independent_and_does_not_accept_paths(self):
        import inspect
        import server
        signature = inspect.signature(server.optiforge_workbench_desktop_diagnostics)
        self.assertEqual(list(signature.parameters), ["service_instance_id", "browser"])
        result = json.loads(server.optiforge_workbench_desktop_diagnostics(self.instance))
        self.assertEqual(result["status"], "unique_candidate")
        with patch.object(server, "_control_result", side_effect=AssertionError("must not reuse raw errors")):
            self.http_mock.side_effect = WorkbenchControlError("SECRET", "SECRET")
            self.assert_unavailable(json.loads(server.optiforge_workbench_desktop_diagnostics(self.instance)))


class FakeProcess:
    def __init__(self, stdout=b'{}', stderr=b'', code=0):
        self.stdout = io.BytesIO(stdout) if isinstance(stdout, bytes) else stdout
        self.stderr = io.BytesIO(stderr) if isinstance(stderr, bytes) else stderr
        self.returncode = None
        self.code = code
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waited = True
        self.returncode = self.code if not self.killed else -1
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -1


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.system = Path(r'C:\Windows\System32')
        self.executable = self.system / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        self.script = Path(__file__).resolve().parents[1] / 'get-workbench-window.ps1'
        self.files = patch.object(desktop, '_collector_files', return_value=(self.executable, self.script, self.system))
        self.files.start()
        self.addCleanup(self.files.stop)

    def run_collect(self, process, **kwargs):
        with patch.object(desktop.subprocess, 'Popen', return_value=process) as spawn:
            result = desktop._collect(Path(r'C:\target work'), 'Chrome')
            return result, spawn.call_args

    def test_fixed_full_executable_argv_hidden_process_and_environment_override_removal(self):
        process = FakeProcess(b'{"ok":true}')
        poisoned = {'SystemRoot': r'C:\evil', 'WINDIR': r'C:\evil', 'PATH': r'C:\evil',
                    'OPTIFORGE_LAUNCHER_TEST_BROWSER': r'C:\evil\fake.exe',
                    'OPTIFORGE_LAUNCHER_TEST_BROWSER_ROOTS': r'C:\evil', 'OPTIFORGE_LAUNCHER_TEST_BROWSER_ONLY': '1',
                    'PSModulePath': r'C:\evil'}
        with patch.dict(os.environ, poisoned):
            result, call = self.run_collect(process)
        self.assertEqual(result, b'{"ok":true}')
        self.assertEqual(call.args[0], [str(self.executable), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                      '-File', str(self.script), '-WorkspacePath', r'C:\target work', '-Browser', 'Chrome'])
        self.assertIs(call.kwargs['shell'], False)
        self.assertEqual(call.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(call.kwargs['env']['SystemRoot'], str(self.system.parent))
        self.assertFalse(any(key.upper().startswith('OPTIFORGE_LAUNCHER_TEST_BROWSER') or key.upper() == 'PSMODULEPATH'
                             for key in call.kwargs['env']))
        self.assertFalse(process.killed)
        self.assertTrue(process.stdout.closed and process.stderr.closed)

    def test_exact_combined_limit_passes_and_overflow_kills_only_owned_process(self):
        output, _ = self.run_collect(FakeProcess(b'x' * desktop.OUTPUT_LIMIT))
        self.assertEqual(len(output), desktop.OUTPUT_LIMIT)
        process = FakeProcess(b'x' * (desktop.OUTPUT_LIMIT + 40960))
        before = {thread.ident for thread in threading.enumerate()}
        with self.assertRaises(desktop._Failure) as raised:
            self.run_collect(process)
        self.assertEqual(raised.exception.reason, 'collector_output_too_large')
        self.assertTrue(process.killed and process.waited)
        self.assertTrue(process.stdout.closed and process.stderr.closed)
        self.assertEqual({thread.ident for thread in threading.enumerate()} - before, set())

    def test_stderr_counts_towards_output_limit_and_is_never_returned(self):
        with self.assertRaises(desktop._Failure) as raised:
            self.run_collect(FakeProcess(b'x' * desktop.OUTPUT_LIMIT, b'SECRET'))
        self.assertEqual(raised.exception.reason, 'collector_output_too_large')
        with self.assertRaises(desktop._Failure) as raised:
            self.run_collect(FakeProcess(b'{}', b'SECRET'))
        self.assertEqual(raised.exception.reason, 'collector_failed')

    def test_timeout_cleans_up_child_and_pipe_readers(self):
        process = FakeProcess(b'{}')
        with patch.object(desktop.time, 'monotonic', side_effect=[0, 21]), self.assertRaises(desktop._Failure) as raised:
            self.run_collect(process)
        self.assertEqual(raised.exception.reason, 'collector_timeout')
        self.assertTrue(process.killed and process.waited)
        self.assertTrue(process.stdout.closed and process.stderr.closed)

    def test_nonzero_exit_and_read_error_after_valid_json_fail_closed(self):
        with self.assertRaises(desktop._Failure) as raised:
            self.run_collect(FakeProcess(b'{}', code=1))
        self.assertEqual(raised.exception.reason, 'collector_failed')
        class FailingRead(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError('SECRET_READ_ERROR')
                return super().read(size)
        with self.assertRaises(desktop._Failure) as raised:
            self.run_collect(FakeProcess(FailingRead(b'{}')))
        self.assertEqual(raised.exception.reason, 'collector_failed')

    def test_spawn_errors_have_fixed_reason_and_no_raw_text(self):
        with patch.object(desktop.subprocess, 'Popen', side_effect=OSError('SECRET_PATH')), self.assertRaises(desktop._Failure) as raised:
            desktop._collect(Path(r'C:\target work'), 'Auto')
        self.assertEqual(raised.exception.reason, 'collector_unavailable')

    def test_system_directory_uses_kernel_api_instead_of_environment(self):
        class Getter:
            def __call__(self, buffer, size):
                buffer.value = r'C:\Windows\System32'
                return len(buffer.value)
        class Kernel:
            GetSystemDirectoryW = Getter()
        with patch.dict(os.environ, {'SystemRoot': r'C:\evil', 'WINDIR': r'C:\evil'}), \
                patch.object(desktop.ctypes, 'WinDLL', return_value=Kernel()) as api, \
                patch.object(desktop, '_canonical_path', side_effect=lambda value, **_: Path(value)):
            self.assertEqual(desktop._system_directory(), self.system)
        api.assert_called_once_with('kernel32', use_last_error=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
