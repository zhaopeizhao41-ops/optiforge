"""
Zemax OpticStudio Session Manager
Singleton managing the ZOS-API connection and application lifecycle.
"""

import atexit
import os
import sys
import threading
import uuid
import winreg
from functools import wraps
from typing import Any, Optional


class ZOSSession:
    _instance: Optional["ZOSSession"] = None
    _lock = threading.Lock()
    operation_lock = threading.RLock()
    _recovery_model_state = None  # (recovery path, filepath, project) of the last snapshot

    def __init__(self, opticstudio_path: Optional[str] = None):
        self.opticstudio_path = opticstudio_path
        self.net_helper = None
        self.ZOSAPI = None
        self.connection = None
        self.application = None
        self.system = None
        self.is_connected = False
        self.mode = "Standalone"  # 'Standalone' or 'Interactive'
        self.current_filepath: Optional[str] = None
        self.model_project: Optional[str] = None
        self.last_recovery_file: Optional[str] = None
        self.last_recovery_error: Optional[str] = None
        self._initialize()

    @classmethod
    def get_instance(cls, opticstudio_path: Optional[str] = None) -> "ZOSSession":
        with cls._lock:
            if cls._instance is None or not cls._instance.is_connected:
                cls._instance = cls(opticstudio_path)
            return cls._instance

    def _initialize(self):
        """Locate Zemax installation and load .NET assemblies."""
        import clr

        # Determine Zemax root
        zemax_root = None
        try:
            with winreg.OpenKey(
                winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER),
                r"Software\Zemax",
                0,
                winreg.KEY_READ,
            ) as a_key:
                zemax_root = winreg.QueryValueEx(a_key, "ZemaxRoot")[0]
        except Exception:
            pass

        # Try default install paths if not found in registry
        default_paths = [
            r"C:\Program Files\Ansys Zemax OpticStudio 2024 R1.00",
            r"C:\Program Files\Zemax OpticStudio",
        ]
        
        target_dir = self.opticstudio_path
        if not target_dir:
            for p in default_paths:
                if os.path.exists(p):
                    target_dir = p
                    break

        if not target_dir and zemax_root:
            target_dir = zemax_root

        if not target_dir or not os.path.exists(target_dir):
            raise RuntimeError(
                f"Zemax OpticStudio directory not found. Checked: {default_paths}, reg: {zemax_root}"
            )

        # NetHelper is usually in install folder or ZOS-API\Libraries
        net_helper_candidates = [
            os.path.join(target_dir, "ZOSAPI_NetHelper.dll"),
            os.path.join(target_dir, r"ZOS-API\Libraries\ZOSAPI_NetHelper.dll"),
        ]
        net_helper_path = None
        for cand in net_helper_candidates:
            if os.path.exists(cand):
                net_helper_path = cand
                break

        if not net_helper_path:
            raise RuntimeError(f"ZOSAPI_NetHelper.dll not found in {target_dir}")

        clr.AddReference(net_helper_path)
        import ZOSAPI_NetHelper

        self.net_helper = ZOSAPI_NetHelper

        # Initialize via NetHelper
        is_init = ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize()
        if not is_init:
            is_init = ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize(target_dir)

        if not is_init:
            raise RuntimeError("ZOSAPI_Initializer failed to initialize.")

        actual_zemax_dir = ZOSAPI_NetHelper.ZOSAPI_Initializer.GetZemaxDirectory()
        clr.AddReference(os.path.join(actual_zemax_dir, "ZOSAPI.dll"))
        clr.AddReference(os.path.join(actual_zemax_dir, "ZOSAPI_Interfaces.dll"))
        import ZOSAPI

        self.ZOSAPI = ZOSAPI
        self.connection = ZOSAPI.ZOSAPI_Connection()
        self._connect_standalone()
        atexit.register(self.close)

    def _connect_standalone(self):
        """Create a new headless Zemax application instance."""
        if self.connection is None:
            raise RuntimeError("ZOSAPI Connection is not created.")

        self.application = self.connection.CreateNewApplication()
        if self.application is None:
            raise RuntimeError("Failed to acquire ZOSAPI Application.")

        if not self.application.IsValidLicenseForAPI:
            self.application.CloseApplication()
            self.application = None
            raise RuntimeError("Zemax license is not valid for ZOS-API usage.")

        self.system = self.application.PrimarySystem
        if self.system is None:
            try:
                self.application.CloseApplication()
            except Exception:
                pass
            self.application = None
            raise RuntimeError("Unable to acquire Primary Optical System.")

        self.is_connected = True
        self.mode = "Standalone"

    def connect_interactive(self) -> bool:
        """Attempt to connect to an existing running OpticStudio GUI instance."""
        try:
            app = self.connection.ConnectToApplication()
            if app and app.IsValidLicenseForAPI:
                if self.application and self.mode == "Standalone":
                    self.application.CloseApplication()
                self.application = app
                self.system = app.PrimarySystem
                self.mode = "Interactive"
                self.is_connected = True
                return True
        except Exception:
            pass
        return False

    def new_system(self, save_changes: bool = False):
        """Create a new blank sequential optical system."""
        self._replace_model(lambda: self.system.New(save_changes), None)

    def load_file(self, filepath: str, save_changes: bool = False):
        """Load an existing .zos or .zmx file."""
        # ZOS-API resolves relative paths against its own working directory and
        # silently keeps the old model, so always pass an absolute path.
        filepath = os.path.abspath(filepath)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        def replace():
            if self.system.LoadFile(filepath, save_changes) is False:
                raise RuntimeError(f"ZOS-API failed to load: {filepath}")

        self._replace_model(replace, filepath)

    def _replace_model(self, replace, filepath: Optional[str]) -> None:
        """Replace the active model, restoring the recovery copy on failure."""
        if not self.is_connected or self.system is None:
            self._connect_standalone()
        recovery = self._save_recovery_copy()
        try:
            replace()
        except Exception as error:
            try:
                self._restore_recovery(recovery)
            except Exception as restore_error:
                raise RuntimeError(f"Model replacement failed: {error}; {restore_error}") from error
            raise
        self.current_filepath = filepath
        self.model_project = None
        self.last_recovery_error = None

    def _save_recovery_copy(self) -> Optional[str]:
        """Keep the in-memory design before a destructive system replacement."""
        # An uncertain model must not replace the last usable recovery copy.
        # A successful explicit New/LoadFile can establish a fresh model instead.
        if self.last_recovery_error:
            return None
        copy = self.system.CopySystem()
        if copy is None:
            raise RuntimeError("ZOS-API could not copy the current model; replacement aborted.")
        try:
            return self._save_snapshot(copy)
        finally:
            try:
                copy.Close(False)
            except Exception:
                pass

    def _save_snapshot(self, snapshot) -> str:
        from tools.project_manager import get_project_dir
        folder = get_project_dir(self.model_project, "recovery")
        path = os.path.join(folder, f"recovery-{uuid.uuid4().hex}.zmx")
        snapshot.SaveAs(path)
        if not os.path.isfile(path):
            raise RuntimeError("ZOS-API did not create a recovery copy; model replacement aborted.")
        self.last_recovery_file = path
        self._recovery_model_state = (path, self.current_filepath, self.model_project)
        return path

    def _restore_recovery(self, path: Optional[str]) -> None:
        if not path:
            return
        try:
            self.system.LoadFile(path, False)
            self.last_recovery_error = None
            state = self._recovery_model_state
            if state is not None and state[0] == path:
                self.current_filepath, self.model_project = state[1:]
        except Exception as error:
            self.last_recovery_error = str(error)
            self.current_filepath = None
            self.model_project = None
            raise RuntimeError(f"Model replacement failed; recovery file is {path}; automatic restore failed: {error}") from error

    def restore_last_recovery(self) -> str:
        if not self.last_recovery_file:
            raise RuntimeError("No recovery copy is available.")
        self._restore_recovery(self.last_recovery_file)
        return self.last_recovery_file

    def save_file(self, filepath: Optional[str] = None):
        """Save current system to file."""
        if not self.is_connected or self.system is None:
            raise RuntimeError("No active Zemax system to save.")
        if self.last_recovery_error:
            raise RuntimeError(f"Cannot save an uncertain model after failed recovery: {self.last_recovery_error}")
        if filepath:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            self.system.SaveAs(filepath)
            self.current_filepath = filepath
        elif self.current_filepath:
            self.system.SaveAs(self.current_filepath)
        else:
            raise ValueError("Filepath must be provided for unsaved new designs.")

    def open_tool(self, opener_name: str):
        """
        Open a ZOS-API tool (e.g. 'OpenLocalOptimization') safely.
        ZOS-API allows only one open tool at a time; its owner must close it.
        """
        tools = self.system.Tools
        current = tools.CurrentTool
        if current is not None:
            raise RuntimeError("Another ZOS-API tool is already open; close it before starting a new operation.")
        tool = getattr(tools, opener_name)()
        if tool is None:
            raise RuntimeError(f"ZOS-API {opener_name}() returned None (another tool may still be running).")
        return tool

    def close(self):
        """Close connection and clean up application."""
        if self.application is not None and self.mode == "Standalone":
            try:
                self.application.CloseApplication()
            except Exception:
                pass
        self.application = None
        self.system = None
        self.connection = None
        self.is_connected = False
        self.current_filepath = None
        self.model_project = None
        self.last_recovery_error = None
        if type(self)._instance is self:
            type(self)._instance = None


def synchronized(fn):
    """Run fn under the shared ZOS operation lock."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with ZOSSession.operation_lock:
            return fn(*args, **kwargs)
    return wrapped


for _method_name in ("connect_interactive", "new_system", "load_file", "restore_last_recovery", "save_file", "open_tool", "close"):
    setattr(ZOSSession, _method_name, synchronized(getattr(ZOSSession, _method_name)))
