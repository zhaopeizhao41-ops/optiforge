"""Start the local workbench: python -B workbench.py [--model existing.zmx]."""
import argparse
import os
import sys

from core.workbench_service import EngineWorker, WorkbenchHTTPServer, write_service_file


def main():
    parser = argparse.ArgumentParser(description="OptiForge 本地工程工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", help="读取已有 .zmx/.zos，加载不会自动授权仿真")
    args = parser.parse_args()
    if os.environ.get("OPTIFORGE_SERVICE_URL"):
        raise SystemExit("启动工程服务时请取消 OPTIFORGE_SERVICE_URL；该变量仅供 MCP 桥接入口使用。")
    engine = EngineWorker()
    server = WorkbenchHTTPServer(("127.0.0.1", args.port), engine)
    write_service_file(server)
    print(f"OptiForge: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        if args.model:
            result = engine.call("zemax_load_file", {"filepath": os.path.abspath(args.model)})
            print(f"Model: {result.get('status')} {result.get('message', '')}", flush=True)
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
