"""Start the local workbench: python -B workbench.py [--model existing.zmx]."""
import argparse
import os
from pathlib import Path
import sys

from core.workbench_service import EngineWorker, WorkbenchHTTPServer, write_service_file


def main():
    parser = argparse.ArgumentParser(description="OptiForge 本地工程工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", help="读取已有 .zmx/.zos，加载不会自动授权仿真")
    parser.add_argument("--workflow-only", action="store_true", help="只启动需求/提案/Agent协作，不连接原生软件")
    parser.add_argument("--service-file", help="服务凭据写入路径（默认 output/workbench_service/service.json；必须位于本工作树 output/ 下）")
    args = parser.parse_args()
    if os.environ.get("OPTIFORGE_SERVICE_URL"):
        raise SystemExit("启动工程服务时请取消 OPTIFORGE_SERVICE_URL；该变量仅供 MCP 桥接入口使用。")
    if args.workflow_only and args.model:
        parser.error("--workflow-only 不能与 --model 同用")
    credential_path = None
    if args.service_file:
        candidate = Path(args.service_file).resolve()
        output_root = (Path(__file__).resolve().parent / "output").resolve()
        if output_root != candidate and output_root not in candidate.parents:
            parser.error("--service-file 必须位于本工作树 output/ 目录下")
        credential_path = candidate
    if args.workflow_only:
        import tools
        from core.workbench_effects import KNOWLEDGE, CONTEXT_WRITES, CONTEXT_READS
        engine = EngineWorker({name:getattr(tools,name) for name in KNOWLEDGE | CONTEXT_WRITES | CONTEXT_READS},native_enabled=False)
    else:
        engine = EngineWorker()
    from tools.project_manager import get_active_project_name
    engine.state.publish(metadata={"active_project":get_active_project_name(),"model_project":None,"proposal_confirmed":False},
                         snapshot=None,validity="unavailable",context_valid=False,error=None)
    server = WorkbenchHTTPServer(("127.0.0.1", args.port), engine, model_import=not args.workflow_only)
    if credential_path:
        write_service_file(server, credential_path)
    else:
        write_service_file(server)
    print(f"OptiForge: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        if args.model:
            engine.submit("zemax_load_file", {"filepath": os.path.abspath(args.model)},
                          expected_context=engine.state.context(), source="startup")
        # Start native observation only on an explicit model action. Opening
        # requirements must also work on machines without an optical license.
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
