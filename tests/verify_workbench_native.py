"""Read-only acceptance against an already running workbench service.

Loads no model, performs no optical analysis, and never authorizes simulation.
Run: python -B tests/verify_workbench_native.py [--source original.zmx]
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.workbench_client import WorkbenchClient
from core.workbench_snapshot import json_safe, sag


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def successful(result):
    if result.get("status") != "success":
        raise RuntimeError(result.get("message", "Native read failed"))
    return result


async def verify_stdio(url, expected, log):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    parameters = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "server.py")], cwd=ROOT,
        env={**os.environ, "OPTIFORGE_SERVICE_URL": url})
    async with stdio_client(parameters, errlog=log) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert "zemax_get_workbench_snapshot" in {tool.name for tool in listed.tools}
            result = await session.call_tool("zemax_get_workbench_snapshot", {}, read_timeout_seconds=30)
            assert not result.is_error
            snapshot = successful(json.loads(result.content[0].text))
            assert snapshot["revision"] == expected["revision"]
            assert snapshot["model"] == expected["model"]
            assert snapshot["sync"] == expected["sync"]
            data = await session.call_tool("zemax_get_system_data", {}, read_timeout_seconds=30)
            assert not data.is_error
            assert json_safe(json.loads(data.content[0].text))["surfaces"] == expected["model"]["surfaces"]
            proposal = await session.read_resource("zemax://proposal/current")
            assert json.loads(proposal.contents[0].text).get("user_confirmed_to_simulate") is not True
            return len(listed.tools)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--source", type=Path, help="Optional original file whose bytes must remain unchanged")
    args = parser.parse_args()
    client = WorkbenchClient(args.url)
    before_source = digest(args.source) if args.source else None
    snapshot = successful(client.call("zemax_get_workbench_snapshot", {}))
    native_path = Path(snapshot["model"]["filepath"])
    before_model = digest(native_path)
    report_dir = ROOT / "output" / "workbench_acceptance" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    native = successful(client.call("zemax_get_system_data", {}))
    checks = []
    for field, source in (("surfaces", "surfaces"), ("fields", "fields"), ("wavelengths", "wavelengths"), ("settings", "general")):
        assert all(snapshot["model"][field].get(k) == v for k, v in native[source].items()) if field == "settings" else snapshot["model"][field] == native[source], field
        checks.append(f"native_{field}_equal")
    second = successful(client.call("zemax_get_workbench_snapshot", {}))
    assert snapshot["revision"] == second["revision"]
    checks.append("repeat_read_revision_stable")
    assert snapshot["sync"] == second["sync"]
    checks.append("repeat_read_sync_versions_stable")
    credentials = json.loads(client.session_file.read_text(encoding="utf-8"))
    cached = client._request("/api/state", credentials)
    assert cached["protocol_version"] == 2
    assert cached["snapshot_context"] == second["sync"]
    assert cached["snapshot"]["model"] == second["model"]
    assert cached["validity"] == "valid"
    assert cached["model_project"] == second["model"]["project"]
    checks.append("published_cache_context_and_snapshot_consistent")
    events = client._request(f"/api/events?instance_id={cached['service_instance_id']}&after_seq={cached['event_seq']}&timeout_ms=0", credentials)
    assert events["events"] == [] and not events["reset_required"]
    after_heartbeat = client._request("/api/state", credentials)
    assert cached["state_version"] == after_heartbeat["state_version"]
    assert cached["model_revision"] == after_heartbeat["model_revision"]
    checks.append("cached_reads_and_heartbeat_do_not_advance_versions")
    assert snapshot["schema_version"] == 1
    assert snapshot["capabilities"] == {"ray_trace": True, "analysis": False, "editing": False}
    assert snapshot["layout"]["rays"] == []
    checks.append("no_fixture_rays_or_analysis")
    layout = snapshot["layout"]
    assert layout["status"] == "ready", layout["issues"]
    vertex = 0.
    for contour, row in zip(layout["surfaces"], snapshot["model"]["surfaces"][1:]):
        assert contour["index"] == row["index"]
        assert abs(contour["vertex_z"] - vertex) < 1e-10
        for z, y in contour["points"]:
            assert abs(z - vertex - sag(row["radius"], row["conic"], y)) < 1e-10
        if not contour["is_image"]:
            vertex += row["thickness"]
    checks.append("all_contours_match_prescription_sag_and_distances")
    with (report_dir / "mcp-stdio.log").open("w", encoding="utf-8") as log:
        tool_count = asyncio.run(verify_stdio(args.url, snapshot, log))
    checks.extend(["real_stdio_mcp_shared_model", "native_proposal_not_authorized"])
    assert digest(native_path) == before_model
    if args.source:
        assert digest(args.source) == before_source
        assert before_source == before_model
    checks.append("model_and_original_file_bytes_unchanged")
    (report_dir / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    report = {"status": "passed", "verified_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "revision": snapshot["revision"], "model": snapshot["model"]["name"],
              "protocol_version": 2, "sync": snapshot["sync"],
              "units": snapshot["model"]["units"], "surfaces": len(snapshot["model"]["surfaces"]),
              "mcp_tool_count": tool_count, "source_sha256": before_source, "model_sha256": before_model,
              "limits": "Read-only acceptance; no native modeling, optimization, ray trace or analysis was executed."}
    destination = report_dir / "native-acceptance.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: {destination}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
