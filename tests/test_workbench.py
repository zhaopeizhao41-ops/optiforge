"""Offline workbench contracts. Run: python -B tests/test_workbench.py.

Uses fake engine tools and temporary files; never connects to OpticStudio.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import http.client
import inspect
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.workbench_client import WorkbenchClient, route_tool_aliases
from core.workbench_service import EngineWorker, WorkbenchHTTPServer, write_service_file
from core.workbench_snapshot import build_layout, capture_snapshot, json_safe, native_layout_issues, sag


def surface(index, radius=0., thickness=0., material="", half=5., **extra):
    return dict(index=index, radius=radius, thickness=thickness, material=material,
                semi_diameter=half, conic=0., type="Standard", is_stop=False, **extra)


def model():
    return {"units": "mm", "system_mode": "Sequential", "surfaces": [
        surface(0, math.inf, math.inf, half=math.inf),
        surface(1, 20., 4., "N-BK7", is_test=True),
        surface(2, -20., 30.), surface(3, math.inf, math.inf, half=2.)]}


class GeometryTests(unittest.TestCase):
    def test_spherical_sag_matches_circle_and_sign(self):
        for radius in (20., -20.):
            for height in (0., 2., -5.):
                expected = math.copysign(abs(radius) - math.sqrt(radius ** 2 - height ** 2), radius)
                self.assertAlmostEqual(sag(radius, 0., height), expected, places=12)

    def test_planes_and_parabola(self):
        self.assertEqual(sag(0., 0., 100.), 0.)
        self.assertEqual(sag(math.inf, 0., 100.), 0.)
        self.assertEqual(sag(20., -1., 4.), .4)

    def test_invalid_sag_is_rejected(self):
        for values in ((2., 0., 3.), (math.nan, 0., 1.), (2., math.nan, 1.)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                sag(*values)

    def test_object_infinity_not_used_as_internal_distance(self):
        layout = build_layout(model())
        self.assertEqual(layout["status"], "ready")
        self.assertEqual([s["vertex_z"] for s in layout["surfaces"]], [0., 4., 34.])
        self.assertEqual([s["index"] for s in layout["surfaces"]], [1, 2, 3])
        self.assertEqual(layout["surfaces"][-1]["points"][0], [34., -2.])
        self.assertEqual(layout["rays"], [])

    def test_glass_uses_both_real_curves(self):
        layout = build_layout(model())
        element, = layout["elements"]
        self.assertEqual((element["front"], element["back"]), (1, 2))
        self.assertEqual(element["points"][:97], layout["surfaces"][0]["points"])
        self.assertEqual(element["points"][97:], list(reversed(layout["surfaces"][1]["points"])))

    def test_curved_image_uses_native_sag(self):
        data = model()
        data["surfaces"][-1]["radius"] = 10.
        image = build_layout(data)["surfaces"][-1]
        self.assertAlmostEqual(image["points"][0][0], 34. + sag(10., 0., 2.))

    def test_invalid_or_unsupported_layout_never_leaves_partial_shapes(self):
        for update in ({"type": "CoordinateBreak"}, {"type": "EvenAsphere"}, {"material": "MIRROR"},
                       {"thickness": -1.}, {"thickness": math.inf}, {"semi_diameter": 0.},
                       {"radius": 2.}, {"thickness": .1}):
            data = model()
            data["surfaces"][1].update(update)
            layout = build_layout(data)
            with self.subTest(update=update):
                self.assertEqual(layout["status"], "unsupported")
                self.assertTrue(layout["issues"])
                self.assertEqual(layout["surfaces"], [])
                self.assertEqual(layout["elements"], [])

    def test_native_units_and_nonsequential_rejection(self):
        data = model()
        data["units"] = "in"
        self.assertEqual(build_layout(data)["surfaces"][-1]["vertex_z"], 34.)
        self.assertEqual(build_layout(data)["units"], "in")
        data["system_mode"] = "NonSequential"
        self.assertEqual(build_layout(data)["status"], "unsupported")

    def test_optical_infinities_are_strict_json(self):
        safe = json_safe({"values": (math.inf, -math.inf, math.nan)})
        self.assertEqual(json.loads(json.dumps(safe, allow_nan=False)), {"values": ["Infinity", "-Infinity", None]})

    def test_native_transforms_and_custom_apertures_are_not_drawn_as_axial(self):
        for transformed, aperture in ((True, "None"), (False, "CircularObscuration")):
            tilt = SimpleNamespace(**{side + "Surface" + component: 0. for side in ("Before", "After")
                                     for component in ("DecenterX", "DecenterY", "TiltX", "TiltY", "TiltZ")},
                                   AfterSurfaceMode="Explicit", CoordinateReturn="None")
            tilt.BeforeSurfaceTiltX = 5. if transformed else 0.
            row = SimpleNamespace(TiltDecenterData=tilt,
                                  ApertureData=SimpleNamespace(CurrentType=aperture))
            system = SimpleNamespace(LDE=SimpleNamespace(NumberOfSurfaces=2, GetSurfaceAt=lambda index: row))
            data = model()
            data["layout_issues"] = native_layout_issues(system)
            self.assertEqual(build_layout(data)["status"], "unsupported")
            self.assertEqual(build_layout(data)["surfaces"], [])
        tilt.BeforeSurfaceTiltX = 0.
        row.ApertureData.CurrentType = "None"
        self.assertEqual(native_layout_issues(system), [])

    def test_snapshot_revision_is_stable_and_native_read_is_not_mutated(self):
        data = {"status": "success", "surfaces": model()["surfaces"], "fields": [], "wavelengths": [], "general": {}}
        original = deepcopy(data)
        fake = SimpleNamespace(last_recovery_error=None, current_filepath="example.zmx", model_project="example", mode="Standalone",
            system=SimpleNamespace(Mode="Sequential", MCE=SimpleNamespace(CurrentConfiguration=1, NumberOfConfigurations=2),
                                   SystemData=SimpleNamespace(Units=SimpleNamespace(LensUnits="Inches"))))
        with patch("core.zos_session.ZOSSession.get_instance", return_value=fake), patch("tools.system_tools.zemax_get_system_data", return_value=data), patch("core.workbench_snapshot.native_layout_issues", return_value=[]):
            first, second = capture_snapshot(), capture_snapshot()
        self.assertEqual(data, original)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(first["model"]["units"], "in")
        self.assertEqual(first["model"]["configuration_count"], 2)
        self.assertFalse(any(first["capabilities"].values()))
        json.dumps(first, allow_nan=False)


class WorkerTests(unittest.TestCase):
    def test_concurrent_calls_share_one_engine_thread(self):
        records = []
        def tool(value):
            records.append((threading.get_ident(), value, "start"))
            time.sleep(.001)
            records.append((threading.get_ident(), value, "end"))
            return {"status": "success", "value": value}
        engine = EngineWorker({"tool": tool})
        self.addCleanup(engine.executor.shutdown)
        with ThreadPoolExecutor(max_workers=8) as clients:
            values = list(clients.map(lambda n: engine.call("tool", {"value": n})["value"], range(24)))
        self.assertEqual(values, list(range(24)))
        self.assertEqual(len({row[0] for row in records}), 1)
        for start, end in zip(records[::2], records[1::2]):
            self.assertEqual(start[:2], end[:2])

    def test_import_snapshot_is_atomic_with_other_imports(self):
        state = {}
        entered, release = threading.Event(), threading.Event()
        def load(filepath):
            state["file"] = filepath
            if filepath == "a.zmx":
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test synchronization timed out")
            return {"status": "success"}
        engine = EngineWorker({"zemax_load_file": load, "zemax_get_workbench_snapshot": lambda: dict(state)})
        self.addCleanup(engine.executor.shutdown)
        with ThreadPoolExecutor(max_workers=2) as clients:
            a = clients.submit(engine.load_model, "a.zmx")
            self.assertTrue(entered.wait(2))
            b = clients.submit(engine.load_model, "b.zmx")
            release.set()
            self.assertEqual(a.result()["file"], "a.zmx")
            self.assertEqual(b.result()["file"], "b.zmx")

    def test_errors_return_data_instead_of_dropping_http_request(self):
        def fail():
            raise RuntimeError("test error")
        engine = EngineWorker({"fail": fail})
        self.addCleanup(engine.executor.shutdown)
        self.assertEqual(engine.call("missing")["code"], "UNKNOWN_TOOL")
        self.assertEqual(engine.call("fail")["message"], "test error")
        engine.registry = None
        with patch.dict(sys.modules, {"tools": None}):
            self.assertEqual(engine.call("fail")["status"], "error")


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="optiforge-workbench-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        dist = self.root / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<h1>workbench</h1>", encoding="utf-8")
        self.loaded = []
        self.engine = EngineWorker({
            "zemax_get_workbench_snapshot": lambda: {"status": "success", "native": True},
            "zemax_load_file": lambda filepath: (self.loaded.append(filepath) or {"status": "success"}),
            "echo": lambda value=7: {"status": "success", "value": value},
        })
        self.addCleanup(self.engine.executor.shutdown)
        self.server = WorkbenchHTTPServer(("127.0.0.1", 0), self.engine, dist)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.credentials = self.root / "service.json"
        write_service_file(self.server, self.credentials)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, method="GET", data=None, headers=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        merged = {"X-OptiForge-Token": self.server.token} if token else {}
        merged.update(headers or {})
        connection.request(method, path, data, merged)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_state_requires_token_and_external_origins_are_rejected(self):
        self.assertEqual(self.request("/api/state", token=False)[0], 403)
        self.assertEqual(self.request("/api/state")[0], 200)
        for headers in ({"Origin": "https://example.com"}, {"Host": "untrusted.test"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/session", headers=headers, token=False)[0], 403)

    def test_static_files_cannot_escape_distribution(self):
        (self.root / "private.txt").write_text("private", encoding="utf-8")
        self.assertEqual(self.request("/", token=False)[0], 200)
        self.assertEqual(self.request("/%2e%2e/private.txt", token=False)[0], 403)

    def test_bad_json_and_tool_parameters_are_reported(self):
        for body in (b"{", b'[]', b'{"name":"echo","arguments":[]}', b'{"name":"echo","arguments":{"value":NaN}}'):
            self.assertEqual(self.request("/api/tool", "POST", body, {"Content-Type": "application/json"})[0], 400)
        status, body = self.request("/api/tool", "POST", b'{"name":"missing"}', {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["code"], "UNKNOWN_TOOL")

    def test_file_import_uses_local_copy_and_atomic_read(self):
        with patch("core.workbench_service.ROOT", self.root):
            status, body = self.request("/api/model/import", "POST", b"local fixture",
                {"Content-Type": "application/octet-stream", "X-File-Name": "sample.zmx"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["native"])
        loaded = Path(self.loaded[0])
        self.assertTrue(loaded.is_relative_to(self.root / "output"))
        self.assertEqual(loaded.read_bytes(), b"local fixture")

    def test_invalid_import_names_never_reach_native_loader(self):
        for name in ("../sample.zmx", "CON.zmx", "sample.zmx.", "bad%3F.zmx", "file.txt"):
            self.assertEqual(self.request("/api/model/import", "POST", b"fixture",
                {"Content-Type": "application/octet-stream", "X-File-Name": name})[0], 400)
        self.assertEqual(self.loaded, [])

    def test_stdio_proxy_preserves_signature_and_forwards_arguments(self):
        def echo(value: int = 7):
            raise AssertionError("a proxy must not run a second local engine")
        echo.__module__ = "tools.fake"
        namespace = {"_echo": echo}
        with patch.dict("os.environ", {"OPTIFORGE_SERVICE_URL": self.url, "OPTIFORGE_SERVICE_FILE": str(self.credentials)}):
            route_tool_aliases(namespace)
        proxy = namespace["_echo"]
        self.assertEqual(inspect.signature(proxy), inspect.signature(echo))
        self.assertEqual(proxy(9), {"status": "success", "value": 9})
        self.assertEqual(proxy(), {"status": "success", "value": 7})
        with self.assertRaises(TypeError):
            proxy(1, 2)

    def test_unavailable_proxy_never_falls_back_to_native(self):
        client = WorkbenchClient(self.url, self.root / "missing.json")
        self.assertEqual(client.call("echo", {"value": 1})["code"], "WORKBENCH_UNAVAILABLE")
        for url in ("https://localhost", "http://example.com", "http://127.0.0.1/extra", "http://name@localhost"):
            with self.assertRaises(ValueError):
                WorkbenchClient(url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
