"""Print the MCP configuration that connects an Agent app to the local workbench.

    python -B connect_agent.py                 # every supported host
    python -B connect_agent.py --host cursor   # one host
    python -B connect_agent.py --check         # also check the running service

The Agent app starts server.py over stdio; server.py forwards every tool to the
running workbench service, so the app and the visual workbench share one engine.
Nothing is written to any config file and no token is printed: the token stays
in the service credential file, which only server.py reads.

Config formats checked 2026-11-11 against code.visualstudio.com (MCP configuration
reference: VS Code `servers` with type stdio; Claude Desktop and Cursor locations),
code.claude.com (`claude mcp add ... --env KEY=value -- command`), Codex config.toml
`[mcp_servers.<name>]` tables and Gemini CLI `mcpServers` in settings.json.
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
SERVICE_FILE = ROOT / "output" / "workbench_service" / "service.json"
NAME = "optiforge"
HOSTS = ("claude-desktop", "claude-code", "cursor", "vscode", "codex", "gemini")


def server_entry(url, python=None):
    return {"command": python or sys.executable, "args": ["-B", str(ROOT / "server.py")],
            "env": {"OPTIFORGE_SERVICE_URL": url, "OPTIFORGE_SERVICE_FILE": str(SERVICE_FILE)}}


def _toml_string(value):
    return json.dumps(value, ensure_ascii=False)


def render(host, url, python=None):
    """Return (where, text) for one host. Pure; used by tests."""
    entry = server_entry(url, python)
    if host in {"claude-desktop", "cursor", "gemini"}:
        where = {"claude-desktop": r"%APPDATA%\Claude\claude_desktop_config.json（macOS: ~/Library/Application Support/Claude/）",
                 "cursor": "~/.cursor/mcp.json（全局）或 <工作区>/.cursor/mcp.json",
                 "gemini": "~/.gemini/settings.json（或 <工作区>/.gemini/settings.json）"}[host]
        return where, json.dumps({"mcpServers": {NAME: entry}}, ensure_ascii=False, indent=2)
    if host == "vscode":
        return "<工作区>/.vscode/mcp.json（或用户配置 MCP: Open User Configuration）", json.dumps(
            {"servers": {NAME: {"type": "stdio", **entry}}}, ensure_ascii=False, indent=2)
    if host == "claude-code":
        env = " ".join(f'--env {key}="{value}"' for key, value in entry["env"].items())
        args = " ".join(f'"{arg}"' for arg in entry["args"])
        return "终端执行（--scope user 对所有项目生效）", f'claude mcp add {NAME} --scope user {env} -- "{entry["command"]}" {args}'
    if host == "codex":
        lines = [f"[mcp_servers.{NAME}]", f"command = {_toml_string(entry['command'])}",
                 f"args = [{', '.join(_toml_string(arg) for arg in entry['args'])}]", "",
                 f"[mcp_servers.{NAME}.env]"]
        lines += [f"{key} = {_toml_string(value)}" for key, value in entry["env"].items()]
        return "~/.codex/config.toml", "\n".join(lines)
    raise ValueError(f"unknown host {host}")


def service_url():
    """URL of the running service from its credential file, else the default port."""
    try:
        return json.loads(SERVICE_FILE.read_text(encoding="utf-8"))["url"]
    except (OSError, ValueError, KeyError):
        return "http://127.0.0.1:8765"


def check(url):
    lines = [f"凭据文件：{'存在' if SERVICE_FILE.is_file() else '缺失（先运行 workbench.py 或 start-workbench.ps1）'}"]
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url.rstrip("/") + "/api/health", timeout=3) as response:
            health = json.load(response)
        lines.append(f"工作台服务：在线（{url}，schema {health.get('schema_version')}）")
    except Exception:
        lines.append(f"工作台服务：未连接（{url}）。Agent 可以先配置；用到工作台时再启动服务。")
    return lines


def main():
    parser = argparse.ArgumentParser(description="打印连接 OptiForge 工作台的 Agent MCP 配置")
    parser.add_argument("--host", choices=HOSTS + ("all",), default="all")
    parser.add_argument("--url", help="工作台服务地址（默认读取凭据文件，否则 http://127.0.0.1:8765）")
    parser.add_argument("--python", help="启动 server.py 的 Python（默认当前解释器）")
    parser.add_argument("--check", action="store_true", help="同时检查凭据文件与服务是否在线")
    args = parser.parse_args()
    url = args.url or service_url()
    for host in HOSTS if args.host == "all" else (args.host,):
        where, text = render(host, url, args.python)
        print(f"## {host}\n位置：{where}\n{text}\n")
    print("连接后在 Agent 里可用：optiforge_workbench_status / optiforge_workbench_view；"
          "支持 MCP prompts 的应用另有 /optiforge_show_workbench 与 /optiforge_pick_up_task。")
    if args.check:
        print("\n".join(check(url)))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
