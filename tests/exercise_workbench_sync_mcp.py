"""Drive the SIMULATED acceptance service through real stdio MCP.

Refuses every write unless the snapshot explicitly identifies the fixture engine.
This is not native optical acceptance. Default fixture: port 8766.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.workbench_client import WorkbenchClient


async def exercise(args):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    destination = ROOT / "output" / "workbench_sync_acceptance" / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    parameters = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "server.py")], cwd=ROOT,
        env={**os.environ, "OPTIFORGE_SERVICE_URL": args.url, "OPTIFORGE_SERVICE_FILE": str(args.service_file)})
    with (destination / "fixture-mcp.log").open("w", encoding="utf-8") as log:
        async with stdio_client(parameters, errlog=log) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                read = await session.call_tool("zemax_get_workbench_snapshot", {})
                snapshot = json.loads(read.content[0].text)
                if snapshot.get("connection", {}).get("engine") != "fixture":
                    raise RuntimeError("Refusing to write: target is not the simulated acceptance engine")
                results = []
                if args.load_path:
                    calls = [("zemax_load_file", {"filepath": args.load_path})]
                else:
                    calls = [("zemax_surface_operations", {"surface_index": args.surface, "radius": radius,
                              **({"comment": args.comment} if args.comment else {})}) for radius in args.radius or [24.]]
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    results.append(json.loads(result.content[0].text))
    client = WorkbenchClient(args.url, args.service_file)
    credentials = json.loads(args.service_file.read_text(encoding="utf-8"))
    published = client._request("/api/state", credentials)
    task = published["tasks"]["recent"][0]
    report = {"kind": "simulated-stdio-MCP", "results": results, "operation_id": task["operation_id"],
              "finished_at": task["finished_at"], "state_version": published["state_version"],
              "model_revision": published["model_revision"], "service_instance_id": published["service_instance_id"],
              "model_id": published["model_id"], "limits": "No native model writes, tracing or analysis."}
    (destination / "fixture-mcp-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--service-file", type=Path, default=ROOT / "output/workbench_sync_acceptance/fixtures/service.json")
    parser.add_argument("--surface", type=int, default=1)
    parser.add_argument("--radius", type=float, action="append")
    parser.add_argument("--comment")
    parser.add_argument("--load-path")
    asyncio.run(exercise(parser.parse_args()))
