"""Stage-two regressions using fake ZOS-API objects.

Run: python -B tests/regression_stage_two.py

The suite deliberately avoids a native OpticStudio connection.  It exercises
validation, project isolation, operation rollback, and session lifecycle code
without writing to the repository's existing output directory.
"""
import inspect
import math
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import analysis_runner as runner
from core.zos_session import ZOSSession
from tools import analysis_tools as analysis
from tools import cad_export_tools as cad
from tools import optimization_tools as optimization
from tools import project_manager as projects
from tools import surface_tools as surfaces
from tools import system_tools as system


class FakeCell:
    def __init__(self, solve_type="Fixed"):
        self.solve_type = solve_type

    def GetSolveData(self):
        return SimpleNamespace(Type=self.solve_type)


class FakeSurface:
    def __init__(self, fail_radius=False):
        self._radius = 1.0
        self.fail_radius = fail_radius
        self.Thickness = 2.0
        self.Material = "N-BK7"
        self.SemiDiameter = 5.0
        self.Conic = 0.0
        self.Comment = ""
        self.IsStop = False
        self.RadiusCell = FakeCell()
        self.ThicknessCell = FakeCell()

    @property
    def Radius(self):
        return self._radius

    @Radius.setter
    def Radius(self, value):
        if self.fail_radius:
            raise RuntimeError("native surface write failed")
        self._radius = value


class FakeLDE:
    NumberOfSurfaces = 3

    def __init__(self, fail_radius=False):
        self.items = [FakeSurface(), FakeSurface(fail_radius), FakeSurface()]

    def GetSurfaceAt(self, index):
        return self.items[index]


class FakeSnapshot:
    def __init__(self, close_error=None):
        self.close_error = close_error
        self.close_calls = 0

    def Close(self, _save_changes):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error

    def SaveAs(self, path):
        Path(path).write_text("fixture snapshot", encoding="utf-8")


class FakeSystem:
    def __init__(self, snapshot=None, fail_radius=False, current_tool=None):
        self.LDE = FakeLDE(fail_radius=fail_radius)
        self.Tools = SimpleNamespace(CurrentTool=current_tool)
        self.snapshot = snapshot
        self.copy_calls = 0

    def CopySystem(self):
        self.copy_calls += 1
        return self.snapshot


class FakeSession:
    def __init__(self, project="model", snapshot=None, fail_radius=False, current_tool=None):
        self.system = FakeSystem(snapshot=snapshot, fail_radius=fail_radius, current_tool=current_tool)
        self.ZOSAPI = SimpleNamespace()
        self.model_project = project
        self.current_filepath = None
        self.last_recovery_file = None
        self.last_recovery_error = None
        self.save_snapshot_calls = 0
        self.restore_calls = []

    def _save_snapshot(self, _snapshot):
        self.save_snapshot_calls += 1
        self.last_recovery_file = "recovery-model.zmx"
        return self.last_recovery_file

    def _restore_recovery(self, path):
        self.restore_calls.append(path)


class FakeAnalysis:
    """Asynchronous IA_ stand-in: IsRunning() is True for `running_polls` polls (None = never finishes)."""
    def __init__(self, running_polls=0, results=None):
        self.running_polls = running_polls
        self.results = results
        self.GetAnalysisName = "FakeAnalysis"
        self.terminated = False
        self.closed = False

    def Apply(self):
        pass

    def IsRunning(self):
        if self.terminated:
            return False
        if self.running_polls is None:
            return True
        self.running_polls -= 1
        return self.running_polls >= 0

    def Terminate(self):
        self.terminated = True

    def WaitForCompletion(self):
        pass

    def GetResults(self):
        return self.results

    def Close(self):
        self.closed = True


class FakeMCE:
    def __init__(self, total=3, current=1):
        self.NumberOfConfigurations = total
        self.CurrentConfiguration = current
        self.history = []

    def SetCurrentConfiguration(self, n):
        self.history.append(n)
        self.CurrentConfiguration = n
        return True


class FakeOperandCell:
    def __init__(self, header, data_type):
        self.Header = header
        self.DataType = data_type
        self.IntegerValue = 0
        self.DoubleValue = 0.0


class FakeOperand:
    """REAY layout as reported by a localized OpticStudio: cells 2..9 = 面, 波, Hx, Hy, Px, Py, blank, blank."""
    def __init__(self):
        layout = [("面", "Integer"), ("波", "Integer"), ("Hx", "Double"), ("Hy", "Double"),
                  ("Px", "Double"), ("Py", "Double"), (" ", "Double"), (" ", "Double")]
        self.cells = {col: FakeOperandCell(h, t) for col, (h, t) in enumerate(layout, start=2)}
        self.Target = 0.0
        self.Weight = 0.0
        self.Value = 0.0
        self.RowIndex = 0

    def ChangeType(self, _type):
        pass

    def GetCellAt(self, col):
        return self.cells[col]


class FakeMFE:
    NumberOfOperands = 0

    def __init__(self):
        self.operands = []

    def AddOperand(self):
        op = FakeOperand()
        self.operands.append(op)
        return op

    def CalculateMeritFunction(self):
        return 0.0


class StageTwoRegression(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="zemax-stage-two-"))
        self.old_project_paths = projects.OUTPUT_BASE_DIR, projects.ACTIVE_PROJECT_FILE
        projects.OUTPUT_BASE_DIR = str(self.temp_dir / "projects")
        projects.ACTIVE_PROJECT_FILE = str(self.temp_dir / "projects" / ".active_project.json")

    def tearDown(self):
        projects.OUTPUT_BASE_DIR, projects.ACTIVE_PROJECT_FILE = self.old_project_paths
        ZOSSession._instance = None

    def _proposal(self, project="model", confirmed=True):
        result = system.zemax_register_design_proposal(
            project_name=project,
            target_specs={"efl_mm": 50},
            initial_structure_source="fixture",
            optical_theory_analysis="fixture",
            glass_selection_rationale="fixture",
            merit_function_strategy="fixture",
            user_confirmed_to_simulate=confirmed,
        )
        self.assertEqual(result["status"], "success")
        return result

    def _load_session(self):
        """Exercise real session recovery against a controllable native boundary."""
        session = object.__new__(ZOSSession)
        session.application = None
        session.connection = None
        session.mode = "Standalone"
        session.is_connected = True
        session.current_filepath = str(self.temp_dir / "old.zmx")
        session.model_project = "old"
        session.last_recovery_file = None
        session.last_recovery_error = None
        session.ZOSAPI = SimpleNamespace()
        session.system = SimpleNamespace(
            CopySystem=Mock(side_effect=FakeSnapshot),
            LoadFile=Mock(), New=Mock(), SaveAs=Mock(),
            Tools=SimpleNamespace(CurrentTool=None),
            LDE=SimpleNamespace(NumberOfSurfaces=3),
        )
        return session

    def test_path_validation_rejects_traversal_reserved_ads_and_drive_relative(self):
        bad_paths = [
            "..\\escape.txt",
            "folder\\..\\escape.txt",
            "C:relative.txt",
            "C:\\temp\\file.txt:secret",
            "C:\\temp\\NUL\\file.txt",
            "\\\\?\\C:\\temp\\file.txt",
        ]
        for path in bad_paths:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    projects._validate_path(path)

        with self.assertRaises(ValueError):
            projects.sanitize_project_name("CON")
        with self.assertRaises(ValueError):
            projects.sanitize_project_name("project.")

    def test_relative_symlink_cannot_escape_project_workspace(self):
        projects.set_active_project("model")
        project_dir = Path(projects.get_project_dir("model"))
        outside = self.temp_dir / "outside"
        outside.mkdir()
        drawing_dir = project_dir / "drawings"
        link = drawing_dir / "linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaises(ValueError):
            projects.resolve_project_file_path("drawings/linked/escaped.json", "unused.json", project_name="model")
        with self.assertRaises(ValueError):
            projects.resolve_project_directory_path("linked/reports", project_name="model")

    def test_invalid_input_is_rejected_before_session_access(self):
        with patch.object(system.ZOSSession, "get_instance", side_effect=AssertionError("session accessed")):
            result = system.zemax_load_file("C:relative.zmx")
        self.assertEqual(result["status"], "error")
        self.assertIn("fully qualified", result["message"])

        with patch.object(analysis.ZOSSession, "get_instance", side_effect=AssertionError("session accessed")):
            result = analysis.zemax_run_fft_mtf(sample_size="invalid", max_frequency=10)
        self.assertEqual(result["status"], "error")
        self.assertIn("Unsupported sample_size", result["message"])

    def test_cad_parameters_are_validated_before_session_access(self):
        invalid_requests = (
            {"file_type": "unsupported"}, {"tolerance": math.nan},
            {"tolerance": 0}, {"num_rays": -1}, {"spline_segments": 1.5},
            {"dummy_thickness": -1}, {"field_index": -1},
            {"wavelength_index": -1}, {"first_surface": 0},
            {"first_surface": 3, "last_surface": 2},
        )
        for args in invalid_requests:
            with self.subTest(args=args), patch.object(
                cad.ZOSSession, "get_instance", side_effect=AssertionError("session accessed")
            ) as get_instance:
                result = cad.zemax_export_cad(**args)
                self.assertEqual(result["status"], "error")
                get_instance.assert_not_called()

    def test_proposal_confirmation_and_project_isolation_gate_model_writes(self):
        self._proposal("model", confirmed=False)
        fake = FakeSession(project="model", snapshot=FakeSnapshot())
        with patch.object(surfaces.ZOSSession, "get_instance", return_value=fake) as get_instance:
            blocked = surfaces.zemax_surface_operations(1, radius=4)
        self.assertEqual(blocked["code"], "PROPOSAL_NOT_CONFIRMED")
        get_instance.assert_not_called()

        self._proposal("target", confirmed=True)
        fake.model_project = "model"
        with patch.object(surfaces.ZOSSession, "get_instance", return_value=fake):
            mismatch = surfaces.zemax_surface_operations(1, radius=4)
        self.assertEqual(mismatch["code"], "MODEL_PROJECT_MISMATCH")
        self.assertEqual(fake.system.LDE.GetSurfaceAt(1).Radius, 1.0)

    def test_failed_model_write_restores_snapshot(self):
        self._proposal("model", confirmed=True)
        snapshot = FakeSnapshot()
        fake = FakeSession(snapshot=snapshot, fail_radius=True)
        with patch.object(surfaces.ZOSSession, "get_instance", return_value=fake):
            result = surfaces.zemax_surface_operations(1, radius=4)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(fake.save_snapshot_calls, 1)
        self.assertEqual(fake.restore_calls, ["recovery-model.zmx"])
        self.assertEqual(snapshot.close_calls, 1)

    def test_copy_failure_aborts_before_model_write(self):
        self._proposal("model", confirmed=True)
        fake = FakeSession(snapshot=None)
        with patch.object(surfaces.ZOSSession, "get_instance", return_value=fake):
            result = surfaces.zemax_surface_operations(1, radius=4)
        self.assertEqual(result["status"], "error")
        self.assertEqual(fake.system.LDE.GetSurfaceAt(1).Radius, 1.0)

    def test_snapshot_close_warning_does_not_replace_success(self):
        self._proposal("model", confirmed=True)
        snapshot = FakeSnapshot(close_error=RuntimeError("snapshot close failed"))
        fake = FakeSession(snapshot=snapshot)
        with patch.object(surfaces.ZOSSession, "get_instance", return_value=fake):
            result = surfaces.zemax_surface_operations(1, radius=4)
        self.assertEqual(result["status"], "success")
        self.assertIn("snapshot close failed", result["cleanup_warning"])
        self.assertEqual(fake.system.LDE.GetSurfaceAt(1).Radius, 4.0)

    def test_existing_zos_api_tool_is_left_for_its_owner_to_close(self):
        self._proposal("model", confirmed=True)
        open_tool = Mock()
        fake = FakeSession(snapshot=FakeSnapshot(), current_tool=open_tool)
        with patch.object(surfaces.ZOSSession, "get_instance", return_value=fake):
            result = surfaces.zemax_surface_operations(1, radius=4)
        self.assertEqual(result["status"], "error")
        open_tool.Close.assert_not_called()
        self.assertEqual(fake.system.copy_calls, 0)

    def test_load_failure_restores_file_and_active_project(self):
        projects.set_active_project("old")
        source = self.temp_dir / "incoming.zmx"
        source.write_text("fixture", encoding="utf-8")

        class LoadSession:
            current_filepath = str(self.temp_dir / "old.zmx")
            model_project = "old"
            last_recovery_file = "recovery-old.zmx"

            def load_file(self, path, save_changes=False):
                self.current_filepath = path

            def restore_last_recovery(self):
                self.current_filepath = self.last_recovery_file

        fake = LoadSession()
        set_calls = []

        def set_project(name, **_kwargs):
            set_calls.append(name)
            if len(set_calls) == 1:
                raise RuntimeError("project metadata failed")
            return {"status": "success"}

        with patch.object(system.ZOSSession, "get_instance", return_value=fake), patch.object(
            system, "set_active_project", side_effect=set_project
        ):
            result = system.zemax_load_file(str(source))
        self.assertEqual(result["status"], "error")
        self.assertEqual(fake.current_filepath, str(self.temp_dir / "old.zmx"))
        self.assertEqual(fake.model_project, "old")
        self.assertEqual(set_calls, ["incoming"])

    def test_loading_same_file_does_not_retry_a_failed_restore(self):
        projects.set_active_project("old")
        session = self._load_session()
        source = Path(session.current_filepath)
        source.write_text("fixture", encoding="utf-8")
        session.system.LoadFile.side_effect = [
            RuntimeError("incoming load failed"), RuntimeError("recovery load failed"), None,
        ]
        with patch.object(system.ZOSSession, "get_instance", return_value=session):
            result = system.zemax_load_file(str(source))
        self.assertEqual(result["status"], "error")
        self.assertIn("incoming load failed", result["message"])
        self.assertIn("recovery load failed", result["recovery_error"])
        self.assertEqual(session.system.LoadFile.call_count, 2)
        self.assertIsNone(session.current_filepath)
        self.assertIsNone(session.model_project)
        self.assertEqual(projects.get_active_project_name(), "old")

    def test_metadata_failure_restores_native_model_and_project(self):
        projects.set_active_project("old")
        session = self._load_session()
        previous_file = session.current_filepath
        source = self.temp_dir / "incoming.zmx"
        source.write_text("fixture", encoding="utf-8")
        original_set_project = projects.set_active_project

        def fail_after_switch(name, **kwargs):
            result = original_set_project(name, **kwargs)
            if name == "incoming":
                raise RuntimeError("project metadata failed")
            return result

        with patch.object(system.ZOSSession, "get_instance", return_value=session), patch.object(
            system, "set_active_project", side_effect=fail_after_switch
        ):
            result = system.zemax_load_file(str(source))
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(session.system.LoadFile.call_count, 2)
        self.assertEqual(session.current_filepath, previous_file)
        self.assertEqual(session.model_project, "old")
        self.assertEqual(projects.get_active_project_name(), "old")

    def test_metadata_failure_restores_project_even_if_model_restore_fails(self):
        projects.set_active_project("old")
        session = self._load_session()
        source = self.temp_dir / "incoming.zmx"
        source.write_text("fixture", encoding="utf-8")
        session.system.LoadFile.side_effect = [None, RuntimeError("recovery load failed")]
        original_set_project = projects.set_active_project

        def fail_after_switch(name, **kwargs):
            result = original_set_project(name, **kwargs)
            if name == "incoming":
                raise RuntimeError("project metadata failed")
            return result

        with patch.object(system.ZOSSession, "get_instance", return_value=session), patch.object(
            system, "set_active_project", side_effect=fail_after_switch
        ):
            result = system.zemax_load_file(str(source))
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["rolled_back"])
        self.assertIn("project metadata failed", result["message"])
        self.assertIn("recovery load failed", result["recovery_error"])
        self.assertEqual(projects.get_active_project_name(), "old")
        self.assertIsNone(session.current_filepath)
        self.assertIsNone(session.model_project)

    def test_template_recovery_failure_does_not_claim_previous_model(self):
        self._proposal("model")
        session = self._load_session()
        session.system.LoadFile.side_effect = RuntimeError("recovery load failed")
        with patch.object(system.ZOSSession, "get_instance", return_value=session):
            # This fake has no SystemData, so template construction fails after New.
            result = system.zemax_load_template("achromat_doublet")
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["rolled_back"])
        self.assertIn("SystemData", result["message"])
        self.assertIn("recovery load failed", result["recovery_error"])
        self.assertIsNone(session.current_filepath)
        self.assertIsNone(session.model_project)
        self.assertEqual(session.system.LoadFile.call_count, 1)

    def test_template_snapshot_failure_keeps_previous_recovery(self):
        self._proposal("model")
        session = self._load_session()
        session.last_recovery_file = "previous-recovery.zmx"
        previous_file = session.current_filepath
        session.system.CopySystem.side_effect = RuntimeError("copy failed")
        with patch.object(system.ZOSSession, "get_instance", return_value=session):
            result = system.zemax_load_template("achromat_doublet")
        self.assertEqual(result["status"], "error")
        self.assertIn("copy failed", result["message"])
        self.assertNotIn("No recovery copy", result["message"])
        self.assertEqual(session.last_recovery_file, "previous-recovery.zmx")
        self.assertEqual(session.current_filepath, previous_file)
        session.system.LoadFile.assert_not_called()
        session.system.New.assert_not_called()

    def test_session_replacement_preserves_original_and_restore_errors(self):
        for operation in ("load_file", "new_system"):
            with self.subTest(operation=operation):
                session = self._load_session()
                source = self.temp_dir / "incoming.zmx"
                source.write_text("fixture", encoding="utf-8")
                session.system.New.side_effect = RuntimeError("replacement failed")
                session.system.LoadFile.side_effect = (
                    [RuntimeError("replacement failed"), RuntimeError("restore failed")]
                    if operation == "load_file" else RuntimeError("restore failed")
                )
                with self.assertRaisesRegex(RuntimeError, "replacement failed.*restore failed"):
                    getattr(session, operation)(*([str(source)] if operation == "load_file" else []))
                self.assertEqual(session.last_recovery_error, "restore failed")
                self.assertIsNone(session.model_project)
                self.assertIsNone(session.current_filepath)

    def test_save_is_blocked_after_failed_recovery(self):
        session = self._load_session()
        session.last_recovery_error = "restore failed"
        with self.assertRaisesRegex(RuntimeError, "restore failed"):
            session.save_file(str(self.temp_dir / "uncertain.zmx"))
        session.system.SaveAs.assert_not_called()

    def test_session_close_clears_singleton_and_rebuilds(self):
        app = Mock()
        session = object.__new__(ZOSSession)
        session.application = app
        session.mode = "Standalone"
        session.system = object()
        session.connection = object()
        session.is_connected = True
        session.current_filepath = "file.zmx"
        session.model_project = "model"
        ZOSSession._instance = session
        session.close()
        app.CloseApplication.assert_called_once_with()
        self.assertIsNone(ZOSSession._instance)

        def fake_init(instance, _opticstudio_path=None):
            instance.is_connected = True

        with patch.object(ZOSSession, "__init__", fake_init):
            rebuilt = ZOSSession.get_instance()
        self.assertIsNot(rebuilt, session)

    def test_primary_system_initialization_failure_closes_application(self):
        app = Mock(IsValidLicenseForAPI=True, PrimarySystem=None)
        session = object.__new__(ZOSSession)
        session.connection = SimpleNamespace(CreateNewApplication=lambda: app)
        session.application = None
        session.system = None
        session.is_connected = False
        with self.assertRaises(RuntimeError):
            session._connect_standalone()
        app.CloseApplication.assert_called_once_with()
        self.assertIsNone(session.application)

    def test_recovery_failure_is_retained_for_callers(self):
        failing_system = SimpleNamespace(LoadFile=Mock(side_effect=RuntimeError("recovery load failed")))
        session = object.__new__(ZOSSession)
        session.system = failing_system
        session.last_recovery_error = None
        with self.assertRaisesRegex(RuntimeError, "automatic restore failed"):
            session._restore_recovery("recovery-model.zmx")
        self.assertEqual(session.last_recovery_error, "recovery load failed")

    def test_all_public_analysis_functions_are_serialized(self):
        public = (
            "zemax_run_spot_diagram", "zemax_run_fft_mtf", "zemax_run_ray_fan",
            "zemax_run_wavefront_map", "zemax_run_field_curvature_distortion",
            "zemax_export_spot_diagram_plot",
        )
        for name in public:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(analysis, name).__wrapped__)

    def test_requirements_audit_rejects_invalid_values_but_allows_negative_efl(self):
        base = {"efl_mm": -50, "f_number": 2.8, "fov_or_sensor": '2/3"', "wavelength_range": "400~700nm"}
        ready = system.zemax_audit_requirements(specs=base)
        self.assertEqual(ready["status"], "READY_FOR_DESIGN")
        self.assertEqual(ready["recognized_specs"]["efl_mm"], -50)

        for key, value in (("f_number", -1), ("wavelength_range", ""), ("efl_mm", math.nan)):
            with self.subTest(key=key):
                specs = dict(base)
                specs[key] = value
                result = system.zemax_audit_requirements(specs=specs)
                self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
                self.assertTrue(any(item["key"] == key for item in result["invalid_specs"]))
                self.assertIn(key, result["audit_report"])
                self.assertIn("非法的参数", result["audit_report"])

    def test_relative_drawing_output_cannot_escape_project(self):
        projects.set_active_project("model")
        with self.assertRaises(ValueError):
            projects.resolve_project_directory_path("..\\outside", project_name="model")
        with self.assertRaises(ValueError):
            projects.resolve_project_directory_path("output\\..\\outside", project_name="model")

    def test_run_analysis_terminates_on_timeout(self):
        stuck = FakeAnalysis(running_polls=None)
        with patch.object(runner, "_POLL_S", 0.001), self.assertRaises(TimeoutError):
            runner.run_analysis(stuck, timeout_s=0.02)
        self.assertTrue(stuck.terminated)

        done = FakeAnalysis(running_polls=2, results="results")
        self.assertEqual(runner.run_analysis(done, timeout_s=5), "results")
        self.assertFalse(done.terminated)

    def test_configuration_is_restored_and_validated(self):
        system_ = SimpleNamespace(MCE=FakeMCE(total=3, current=1))
        with self.assertRaises(RuntimeError), runner.configuration(system_, 2):
            self.assertEqual(system_.MCE.CurrentConfiguration, 2)
            raise RuntimeError("analysis failed")
        self.assertEqual(system_.MCE.CurrentConfiguration, 1)
        with self.assertRaises(ValueError), runner.configuration(system_, 4):
            pass
        self.assertEqual(system_.MCE.history, [2, 1])

    def test_analysis_config_is_validated_applied_and_restored(self):
        public = (
            "zemax_run_spot_diagram", "zemax_run_fft_mtf", "zemax_run_ray_fan",
            "zemax_run_wavefront_map", "zemax_run_field_curvature_distortion",
            "zemax_export_spot_diagram_plot",
        )
        for name in public:
            with self.subTest(name=name):
                self.assertIn("config", inspect.signature(getattr(analysis, name)).parameters)

        with patch.object(analysis.ZOSSession, "get_instance", side_effect=AssertionError("session accessed")):
            result = analysis.zemax_run_field_curvature_distortion(config=0)
        self.assertEqual(result["status"], "error")

        fcd = FakeAnalysis(results=SimpleNamespace(NumberOfDataSeries=0))
        mce = FakeMCE(total=3, current=1)
        fake = SimpleNamespace(system=SimpleNamespace(
            MCE=mce, Analyses=SimpleNamespace(New_FieldCurvatureAndDistortion=lambda: fcd)))
        with patch.object(analysis.ZOSSession, "get_instance", return_value=fake):
            result = analysis.zemax_run_field_curvature_distortion(config=2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["config"], 2)
        self.assertEqual(mce.history, [2, 1])
        self.assertTrue(fcd.closed)

    def test_add_operand_writes_named_double_columns_and_rejects_bad_cells(self):
        self._proposal("model")
        fake = FakeSession(project="model", snapshot=FakeSnapshot())
        fake.system.MFE = FakeMFE()
        fake.ZOSAPI = SimpleNamespace(Editors=SimpleNamespace(
            MFE=SimpleNamespace(MeritOperandType=SimpleNamespace(REAY="REAY"))))
        with patch.object(optimization.ZOSSession, "get_instance", return_value=fake):
            ok = optimization.zemax_add_operand(
                "REAY", 0.0, 1.0, param1=12, params={"param2": 1, "Hy": 1.0, "py": 0.7})
            self.assertEqual(ok["status"], "success")
            cells = fake.system.MFE.operands[0].cells
            self.assertEqual((cells[2].IntegerValue, cells[3].IntegerValue), (12, 1))
            self.assertEqual((cells[5].DoubleValue, cells[7].DoubleValue), (1.0, 0.7))
            self.assertEqual(ok["parameters"], {"param1": 12, "param2": 1, "param4": 1.0, "param6": 0.7})

            bad = optimization.zemax_add_operand("REAY", 0.0, 1.0, params={"param2": 1.5})
            self.assertEqual(bad["status"], "error")
            self.assertTrue(bad["rolled_back"])
            unknown = optimization.zemax_add_operand("REAY", 0.0, 1.0, params={"Qx": 1})
            self.assertIn("Unknown column", unknown["message"])

        with patch.object(optimization.ZOSSession, "get_instance", side_effect=AssertionError("session accessed")):
            invalid = optimization.zemax_add_operand("REAY", 0.0, 1.0, params=[1, 2])
        self.assertEqual(invalid["status"], "error")

    def test_mirror_surfaces_are_not_lens_elements(self):
        # Lens S1-S2 whose rear face is a back-surface mirror, then a standalone fold mirror at S4.
        rows = [("", 0, 10), ("N-BK7", 20, 3), ("MIRROR", -40, -3), ("", 0, -10), ("MIRROR", 0, 10), ("", 0, 0)]
        items = [SimpleNamespace(Material=m, Radius=r, Thickness=t, SemiDiameter=4.0, Conic=0.0, Type="Standard")
                 for m, r, t in rows]
        fake_sys = SimpleNamespace(LDE=SimpleNamespace(NumberOfSurfaces=len(items), GetSurfaceAt=lambda i: items[i]))
        elements = cad._extract_lens_elements(fake_sys)
        self.assertEqual([(e["surface_start"], e["surface_end"]) for e in elements], [(1, 2)])

    def test_mce_validation_rejects_bad_operand_type_and_mismatched_values(self):
        from core.input_validation import validate_arguments
        # operand_type must be non-empty string
        with self.assertRaises(ValueError):
            validate_arguments("zemax_mce_set_operand", {"operand_type": "", "values": [1], "param1": 1, "param2": 0, "param3": 0, "row": None, "variable": False})
        # values must be non-empty list
        with self.assertRaises(ValueError):
            validate_arguments("zemax_mce_set_operand", {"operand_type": "THIC", "values": [], "param1": 1, "param2": 0, "param3": 0, "row": None, "variable": False})
        # variable must be boolean
        with self.assertRaises(ValueError):
            validate_arguments("zemax_mce_set_operand", {"operand_type": "THIC", "values": [1.0], "param1": 1, "param2": 0, "param3": 0, "row": None, "variable": "yes"})

    def test_surface_type_and_params_validation(self):
        from core.input_validation import validate_arguments
        # surface_type must be non-empty
        with self.assertRaises(ValueError):
            validate_arguments("zemax_set_surface_type", {"surface_index": 1, "surface_type": ""})
        # params must be dict
        with self.assertRaises(ValueError):
            validate_arguments("zemax_set_surface_params", {"surface_index": 1, "params": [1, 2]})
        # params keys must be strings
        with self.assertRaises(ValueError):
            validate_arguments("zemax_set_surface_params", {"surface_index": 1, "params": {"": 1.0}})

    def test_fold_and_scan_mirror_validation(self):
        from core.input_validation import validate_arguments
        # axis must be x or y
        with self.assertRaises(ValueError):
            validate_arguments("zemax_add_fold_mirror", {"surface_index": 1, "reflect_angle_deg": 90, "axis": "z"})
        # scan_angles_deg must be non-empty list
        with self.assertRaises(ValueError):
            validate_arguments("zemax_add_scan_mirror", {"surface_index": 1, "scan_angles_deg": [], "reflect_angle_deg": 90, "axis": "x"})

    def test_tissue_stack_validation_rejects_bad_layers_and_depths(self):
        from core.input_validation import validate_arguments
        # tissue_layers must be non-empty list
        with self.assertRaises(ValueError):
            validate_arguments("zemax_setup_tissue_stack", {"gap_surface": 10, "tissue_layers": [], "depths_um": [0], "cover_layers": None})
        # depths_um must be non-empty list
        with self.assertRaises(ValueError):
            validate_arguments("zemax_setup_tissue_stack", {"gap_surface": 10, "tissue_layers": [{"n": 1.4}], "depths_um": [], "cover_layers": None})
        # layers must be dicts
        with self.assertRaises(ValueError):
            validate_arguments("zemax_setup_tissue_stack", {"gap_surface": 10, "tissue_layers": ["bad"], "depths_um": [0], "cover_layers": None})

    def test_envelope_validation_checks_surface_indices(self):
        from core.input_validation import validate_arguments
        # rim_points must be >= 3
        with self.assertRaises(ValueError):
            validate_arguments("zemax_get_envelope", {"first_surface": None, "last_surface": None, "frame_surface": None, "rim_points": 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)

