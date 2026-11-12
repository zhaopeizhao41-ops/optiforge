"""Minimal MCP Apps host for testing the workbench panel. SIMULATED optics only.

Starts the demo workbench service, connects to server.py over a real stdio MCP
connection, and serves a host page that loads ui://optiforge/workbench-view.html
into a sandboxed iframe and speaks the ext-apps postMessage protocol
(ui/initialize, tool-input, tool-result, tools/call, ui/open-link,
ui/request-display-mode, size-changed, host-context-changed).

Run: python -B tests/mcp_app_host.py --port 8768, then open http://127.0.0.1:8768/
"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
from anyio.from_thread import start_blocking_portal

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import workbench_demo_service as demo_service  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402

APP_URI = "ui://optiforge/workbench-view.html"
HOST_PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>MCP Apps 测试宿主</title>
<style>body{font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#eef0f2;color:#202429}
.wrap{max-width:980px;margin:0 auto;padding:16px;display:grid;gap:12px}
.chat{background:#fff;border-radius:14px;padding:14px;box-shadow:0 1px 4px #0001}
iframe{width:100%;border:1px solid #dfe3e6;border-radius:12px;height:420px;background:#fff;display:block}
#log{font:12px/1.4 Consolas,monospace;white-space:pre-wrap;max-height:260px;overflow:auto;background:#1d2125;color:#cfd6dc;border-radius:10px;padding:10px}
body.dark{background:#111417;color:#edf0f2}body.dark .chat{background:#1d2125}</style></head>
<body><div class="wrap"><div class="chat"><b>用户：</b>看一下四阶段收敛。<br><b>Agent：</b>调用 <code>optiforge_workbench_view(view="stages")</code></div>
<iframe id="app" sandbox="allow-scripts" title="OptiForge 工作台视图"></iframe>
<div><button id="theme">切换主题（host-context-changed）</button></div><div id="log"></div></div>
<script>
const frame=document.getElementById('app'),logBox=document.getElementById('log');
// ?inspect: same-origin only so automated tests can click inside the panel; real hosts keep the strict sandbox.
if(location.search.includes('inspect'))frame.sandbox.add('allow-same-origin');let dark=false,instance=null,mode='inline';
window.__protocol=[];window.__openedLinks=[];
const log=(dir,m)=>{window.__protocol.push({dir,method:m.method||null,id:m.id??null,error:m.error||null});
  logBox.textContent+=dir+' '+(m.method||('result #'+m.id))+'\n';logBox.scrollTop=1e9;};
const post=m=>{log('host→app',m);frame.contentWindow.postMessage({jsonrpc:'2.0',...m},'*');};
const call=async(name,args)=>{const r=await fetch('/call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,arguments:args})});return r.json();};
window.addEventListener('message',async e=>{if(e.source!==frame.contentWindow)return;const m=e.data;if(!m||m.jsonrpc!=='2.0')return;log('app→host',m);
  const reply=(result)=>post({id:m.id,result}),fail=(message)=>post({id:m.id,error:{code:-32000,message}});
  switch(m.method){
    case 'ui/initialize': reply({protocolVersion:'2026-01-26',hostInfo:{name:'optiforge-test-host',version:'1'},
      hostCapabilities:{openLinks:{},serverTools:{}},hostContext:{theme:dark?'dark':'light',displayMode:mode,availableDisplayModes:['inline','fullscreen'],
      platform:'desktop',toolInfo:{tool:{name:'optiforge_workbench_view'}}}});break;
    case 'ui/notifications/initialized':{const args={service_instance_id:instance,view:'stages'};post({method:'ui/notifications/tool-input',params:{arguments:args}});
      post({method:'ui/notifications/tool-result',params:await call('optiforge_workbench_view',args)});break;}
    case 'tools/call': try{reply(await call(m.params.name,m.params.arguments));}catch(err){fail(String(err));}break;
    case 'ui/open-link': window.__openedLinks.push(m.params.url);reply({});break;
    case 'ui/request-display-mode': mode=m.params.mode==='fullscreen'?'fullscreen':'inline';frame.style.height=mode==='fullscreen'?'90vh':'';reply({mode});break;
    case 'ui/notifications/size-changed': if(mode==='inline')frame.style.height=Math.min(1400,Math.max(200,m.params.height))+'px';break;
  }});
document.getElementById('theme').onclick=()=>{dark=!dark;document.body.classList.toggle('dark',dark);post({method:'ui/notifications/host-context-changed',params:{theme:dark?'dark':'light'}});};
(async()=>{const status=await call('optiforge_workbench_status',{});instance=JSON.parse(status.content[0].text).service_instance_id;
  const app=await (await fetch('/app')).json();window.__appMime=app.mimeType;window.__appMeta=app.meta;frame.srcdoc=app.text;})();
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    demo = demo_service.Demo(0, pause=False)
    demo.start_server(); demo.requirements(); demo.proposal(); demo.confirm(); demo.stages()
    env = {**os.environ, "OPTIFORGE_SERVICE_URL": f"http://127.0.0.1:{demo.server.server_port}",
           "OPTIFORGE_SERVICE_FILE": str(demo.service_file)}
    with start_blocking_portal() as portal:
        params = StdioServerParameters(command=sys.executable, args=["-B", "server.py"], env=env, cwd=str(ROOT))
        with portal.wrap_async_context_manager(Client(params)) as client:
            serve(portal, client, args.port, demo)


def serve(portal, client, port, demo):
    if True:

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, value, status=200):
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.split("?", 1)[0] == "/":
                    body = HOST_PAGE.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/app":
                    content = portal.call(client.read_resource, APP_URI).contents[0]
                    self._json(content.model_dump(by_alias=True, exclude_none=True))
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if self.path != "/call":
                    return self._json({"error": "not found"}, 404)
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                result = portal.call(client.call_tool, request["name"], request.get("arguments") or {})
                self._json(result.model_dump(by_alias=True, exclude_none=True, mode="json"))

        http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        print(f"[host] MCP Apps 测试宿主：http://127.0.0.1:{http.server_port}/", flush=True)
        try:
            http.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            http.server_close()
            demo.server.shutdown(); demo.engine.executor.shutdown(wait=False); demo.close(False)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
