"""
Zemax OpticStudio Session Manager
Singleton managing the ZOS-API connection and application lifecycle.
"""

import atexit
import os
import sys
import threading
import winreg
from typing import Any, Optional


class ZOSSession:
    _instance: Optional["ZOSSession"] = None
    _lock = threading.Lock()

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
        self._initialize()

    @classmethod
    def get_instance(cls, opticstudio_path: Optional[str] = None) -> "ZOSSession":
        with cls._lock:
            if cls._instance is None:
                cls._instance = ZOSSession(opticstudio_path)
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
            raise RuntimeError("Zemax license is not valid for ZOS-API usage.")

        self.system = self.application.PrimarySystem
        if self.system is None:
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
        if not self.is_connected or self.system is None:
            self._connect_standalone()
        self.system.New(save_changes)
        self.current_filepath = None

    def load_file(self, filepath: str, save_changes: bool = False):
        """Load an existing .zos or .zmx file."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        if not self.is_connected or self.system is None:
            self._connect_standalone()
        self.system.LoadFile(filepath, save_changes)
        self.current_filepath = filepath

    def save_file(self, filepath: Optional[str] = None):
        """Save current system to file."""
        if not self.is_connected or self.system is None:
            raise RuntimeError("No active Zemax system to save.")
        if filepath:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            self.system.SaveAs(filepath)
            self.current_filepath = filepath
        elif self.current_filepath:
            self.system.Save()
        else:
            raise ValueError("Filepath must be provided for unsaved new designs.")

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
