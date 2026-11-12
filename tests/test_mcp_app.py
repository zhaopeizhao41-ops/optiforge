"""MCP Apps panel for optiforge_workbench_view: registration, resource and HTML contract.

The interactive protocol is exercised in a browser with tests/mcp_app_host.py.
"""
from pathlib import Path
import re
import sys
import unittest

import anyio

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID  # noqa: E402

HTML = (ROOT / "core" / "mcp_apps" / "workbench_view.html").read_text(encoding="utf-8")


class AppRegistrationTests(unittest.TestCase):
    def test_tool_resource_and_capability_over_an_in_process_client(self):
        async def main():
            async with Client(server.app) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                view = tools["optiforge_workbench_view"]
                content = (await client.read_resource(server.WORKBENCH_VIEW_APP)).contents[0]
                resources = {str(r.uri): r for r in (await client.list_resources()).resources}
                return view, content, resources
        view, content, resources = anyio.run(main)
        self.assertEqual(view.meta["ui"]["resourceUri"], "ui://optiforge/workbench-view.html")
        self.assertIn("service_instance_id", view.input_schema["properties"])
        self.assertEqual(content.mime_type, APP_MIME_TYPE)
        self.assertEqual(content.text, HTML)
        self.assertEqual(resources[server.WORKBENCH_VIEW_APP].mime_type, APP_MIME_TYPE)
        self.assertIn(EXTENSION_ID, server.app._lowlevel_server.extensions)

    def test_tool_still_degrades_to_text_without_a_workbench(self):
        import os
        from unittest.mock import patch
        import json
        with patch.dict(os.environ, {"OPTIFORGE_SERVICE_URL": "", "OPTIFORGE_SERVICE_FILE": ""}):
            result = server.optiforge_workbench_view("any")
        self.assertEqual(json.loads(result[0])["code"], "WORKBENCH_CONFIG_MISSING")


class AppHtmlContractTests(unittest.TestCase):
    def test_self_contained_under_the_default_csp(self):
        self.assertTrue(HTML.lstrip().lower().startswith("<!doctype html>"))
        self.assertIsNone(re.search(r"https?://", HTML), "no external URLs: default CSP blocks them")
        self.assertIsNone(re.search(r"<(?:script|link|img)[^>]+(?:src|href)=", HTML), "no external scripts/styles/images")
        self.assertNotIn("fetch(", HTML)
        self.assertNotIn("XMLHttpRequest", HTML)

    def test_untrusted_text_is_never_parsed_as_html(self):
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(sink, HTML)
        self.assertIn("event.source !== window.parent", HTML)

    def test_speaks_the_ext_apps_protocol(self):
        for method in ("ui/initialize", "ui/notifications/initialized", "ui/notifications/tool-input",
                       "ui/notifications/tool-result", "ui/notifications/host-context-changed",
                       "ui/notifications/size-changed", "ui/open-link", "ui/request-display-mode",
                       "ui/resource-teardown", "tools/call"):
            self.assertIn(f'"{method}"', HTML)
        self.assertIn('protocolVersion: "2026-01-26"', HTML)
        self.assertIn("appInfo", HTML)
        self.assertIn("appCapabilities", HTML)
        self.assertIn('const TOOL = "optiforge_workbench_view"', HTML)

    def test_states_it_is_read_only(self):
        self.assertIn("不代表像质验收或仿真授权", HTML)
        self.assertNotIn("zemax_confirm_design_proposal", HTML)
        self.assertNotIn("zemax_run_", HTML)


if __name__ == "__main__":
    unittest.main()
