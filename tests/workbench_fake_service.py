"""Manual/browser acceptance fixture. Simulated optics ONLY; never opens Zemax.

Run: python -B tests/workbench_fake_service.py --port 8766
Point stdio MCP at server.py with OPTIFORGE_SERVICE_URL and OPTIFORGE_SERVICE_FILE
from the printed paths. Stop/restart this process to test browser recovery.
"""
import argparse
from copy import deepcopy
import math
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.workbench_service import EngineWorker, WorkbenchHTTPServer, write_service_file
from core.workbench_snapshot import build_layout, json_safe
from core.workbench_state import now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--trace", action="store_true", help="Enable simulated-only ray trace fixture")
    args = parser.parse_args()
    model = {"units": "mm", "lens_units": "Millimeters", "system_mode": "Sequential", "name": "同步验收 · 模拟引擎",
             "filepath": "fixture.zmx", "project": "同步验收", "configuration": 1, "configuration_count": 1,
             "settings": {"aperture_type": "EPD", "aperture_value": 10, "field_type": "Angle", "ray_aiming": "Off"},
             "fields": [{"index": 1, "x": 0, "y": 0, "weight": 1}],
             "wavelengths": [{"index": 1, "wavelength_um": .55, "weight": 1, "is_primary": True}]}
    def row(index, radius=0., thickness=0., material="", half=5.):
        return {"index": index, "radius": radius, "thickness": thickness, "material": material, "semi_diameter": half,
                "conic": 0., "type": "Standard", "comment": "", "is_stop": index == 1, "radius_solve": "Fixed", "thickness_solve": "Fixed"}
    model["surfaces"] = [row(0, math.inf, math.inf), row(1, 20., 4., "N-BK7"), row(2, -20., 30.), row(3, 0., 0., half=2.)]
    if args.trace:
        model["name"] = "光线验收 · 模拟引擎"
        model["settings"]["field_normalization"] = "Radial"
        model["fields"] = [{"index": i+1, "x": 0, "y": y, "weight": 1} for i,y in enumerate([0,3,5])]
        model["wavelengths"] = [{"index":i+1,"wavelength_um":w,"weight":1,"is_primary":i==0} for i,w in enumerate([.55,.65])]
    session = object()
    project = ["同步验收"]
    def capture():
        return {"status": "success", "schema_version": 1, "revision": str(model["surfaces"][1]["radius"]),
                "observed_at": now(), "connection": {"state": "connected", "engine": "fixture"},
                "model": json_safe(deepcopy(model)), "layout": build_layout(model),
                "capabilities": {"ray_trace": args.trace, "analysis": False, "editing": False}}
    def trace(m, settings):
        from core.workbench_trace import assemble
        time.sleep(.35)
        mode=m["surfaces"][1]["comment"]
        def batch(surface, rays):
            output=[]
            for number, ray in enumerate(rays,1):
                error=9 if mode=="fixture-trace-fail" or mode=="fixture-trace-partial" and surface>=2 and number%3==0 else 0
                vig=2 if mode=="fixture-trace-partial" and surface>=2 and number%3==1 else 0
                z=[0,0,4,34][surface]
                y=ray["py"]*4*(1-z/35)+ray["field"]["hy"]*z*.02
                output.append((True,number,error,vig,0.,y,z,0.,0.,1.,0.,0.,1.,0.,1.))
            return output
        start=time.monotonic()
        result=assemble(m,settings,batch,lambda s:([1,0,0,0,1,0,0,0,1],[0,0,0]))
        result["elapsed_ms"]=(time.monotonic()-start+.35)*1000
        return result
    def write(surface_index, **arguments):
        comment = arguments.get("comment", "")
        if comment == "fixture-fail":
            return {"status": "error", "message": "模拟操作失败，已回滚", "rolled_back": True}
        if comment == "fixture-slow": time.sleep(2)
        if not 0 <= surface_index < len(model["surfaces"]):
            return {"status": "error", "message": "fixture index out of range", "rolled_back": True}
        model["surfaces"][surface_index].update({key: value for key, value in arguments.items() if value is not None})
        for key in ("radius", "thickness", "semi_diameter", "conic"):
            model["surfaces"][surface_index][key] = float(model["surfaces"][surface_index][key])
        return {"status": "success", "surface": deepcopy(model["surfaces"][surface_index])}
    def load(filepath):
        project[0] = Path(filepath).stem
        model.update(name=Path(filepath).stem + " · 模拟引擎", filepath=filepath, project=project[0])
        return {"status": "success", "message": "fixture model loaded"}
    registry = {"zemax_get_workbench_snapshot": capture, "zemax_surface_operations": write,
                "zemax_load_file": load, "zemax_get_project": lambda: {"status": "success", "project_name": project[0]},
                "get_current_design_proposal": lambda: {"user_confirmed_to_simulate": False}}
    # Confirmation here is fixture metadata only; the real native gate is never opened.
    engine = EngineWorker(registry, metadata=lambda: {"active_project": project[0], "model_project": project[0],
                                                     "proposal_confirmed": args.trace, "session_identity": session}, trace=trace if args.trace else None)
    server = WorkbenchHTTPServer(("127.0.0.1", args.port), engine)
    credentials = ROOT / "output" / ("workbench_raytrace_acceptance" if args.trace else "workbench_sync_acceptance") / "fixtures" / "service.json"
    write_service_file(server, credentials)
    engine.call("zemax_get_workbench_snapshot", source="startup")
    print(f"SIMULATED engine: http://127.0.0.1:{server.server_port}\nService file: {credentials}", flush=True)
    try: server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); engine.executor.shutdown()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    main()
