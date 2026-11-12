"""MCP stdio entry that also joins the local workbench when one is running.

The Agent app starts this file instead of server.py. If the workbench left a
credential file in ``output/workbench_service/service.json`` (written by
workbench.py / start-workbench.ps1), its loopback URL is exported as
OPTIFORGE_SERVICE_URL / OPTIFORGE_SERVICE_FILE before server.py imports, so
every tool runs against the same engine the browser shows. Without a credential
file the process runs exactly like server.py: an independent native session with
no workbench views (optiforge_* tools report WORKBENCH_CONFIG_MISSING).

This only *reads* the credential file. It prints no token and never starts,
stops or reconfigures a service.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SERVICE_FILE = ROOT / "output" / "workbench_service" / "service.json"


HEALTH_TIMEOUT_S = 2.0


def _alive(url: str) -> bool:
    """True when the credential's service answers /api/health as the workbench.

    A credential file outlives its service (crash, reboot, closed window). Routing
    every tool to a dead URL turns each call into WORKBENCH_UNAVAILABLE, so a
    stale file must fall back to the independent native session instead.
    """
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url.rstrip("/") + "/api/health", timeout=HEALTH_TIMEOUT_S) as response:
            health = json.load(response)
    except Exception:
        return False
    return isinstance(health, dict) and health.get("service") == "optiforge-workbench"


def configure(probe=_alive):
    """Export the running workbench's loopback URL when its credentials are live.

    Returns (connected, reason) where reason is a short diagnostic for stderr.
    """
    if os.environ.get("OPTIFORGE_SERVICE_URL"):
        return True, "environment"
    try:
        credentials = json.loads(SERVICE_FILE.read_text(encoding="utf-8"))
        url = credentials["url"]
    except (OSError, ValueError, KeyError, TypeError):
        return False, "no credential file"
    if not isinstance(url, str) or not url.startswith(("http://127.0.0.1:", "http://localhost:")):
        return False, "credential URL is not loopback"
    if not probe(url):
        return False, f"credential file is stale: {url} did not answer /api/health"
    os.environ["OPTIFORGE_SERVICE_URL"] = url
    os.environ.setdefault("OPTIFORGE_SERVICE_FILE", str(SERVICE_FILE))
    return True, url


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # Diagnostic only: stderr is not part of the MCP stdio protocol.
    connected, reason = configure()
    if not connected:
        os.environ["OPTIFORGE_STANDALONE_REASON"] = reason
    print(f"[optiforge] workbench bridge: {'connected' if connected else 'standalone native'} ({reason})",
          file=sys.stderr, flush=True)
    sys.path.insert(0, str(ROOT))
    runpy_path = ROOT / "server.py"
    import runpy
    runpy.run_path(str(runpy_path), run_name="__main__")


if __name__ == "__main__":
    main()
