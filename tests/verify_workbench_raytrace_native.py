"""Read-only native comparison in an EXISTING confirmed project, never confirms it.

python -B tests/verify_workbench_raytrace_native.py --model <existing.zmx> [--serve --port 8767]
Authorization is read on every operation from the model's own design_proposal.json.
This dedicated validation process does not change the main service's import project.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.workbench_engine import EngineWorker
from core.workbench_service import WorkbenchHTTPServer
from core.workbench_snapshot import capture_snapshot
from core.workbench_trace import TraceError, trace_session, settings_for
from core.zos_session import ZOSSession


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=8767)
    parser.add_argument('--pupil-axis',choices=['X','Y'],default='Y')
    args=parser.parse_args()
    source=args.model.resolve();folder=source.parent;proposal_path=folder/'design_proposal.json'
    def proposal():
        p=json.loads(proposal_path.read_text(encoding='utf-8'))
        if p.get('project_name')!=folder.name or p.get('user_confirmed_to_simulate') is not True:
            raise TraceError('PROPOSAL_NOT_CONFIRMED','原模型所在工程没有匹配的已确认提案')
        return p
    try:authorization=proposal()
    except Exception as e:
        print(json.dumps({'status':'blocked','message':str(e)},ensure_ascii=False));return 2
    before_bytes={str(p):digest(p) for p in (source,proposal_path,folder/'project.json') if p.exists()}
    session=ZOSSession.get_instance()
    # Open the original model in a separate owned, headless application; no Save,
    # no global set_project and no writes to original project metadata.
    session.load_file(str(source),save_changes=False)
    session.model_project=folder.name
    def metadata():
        proposal()
        return {'active_project':folder.name,'model_project':session.model_project,
                'proposal_confirmed':True,'session_identity':session,'recovery_error':session.last_recovery_error}
    def trace(m,s):
        proposal()
        if session.model_project != folder.name or Path(session.current_filepath).resolve()!=source:
            raise TraceError('MODEL_PROJECT_MISMATCH','验证进程的实际模型已改变')
        with ZOSSession.operation_lock:return trace_session(session,m,s)
    # Registry exposes no mutations. Metadata comes from the ORIGINAL proposal,
    # never a fixture confirmed flag or a copied import authorization.
    engine=EngineWorker({'zemax_get_workbench_snapshot':capture_snapshot},capture=capture_snapshot,metadata=metadata,trace=trace)
    try:
        capture_started=time.monotonic()
        engine.refresh();engine.refresh_future.result(timeout=30)
        initial_refresh_ms=(time.monotonic()-capture_started)*1000
        before=engine.state.read()
        m=before['snapshot']['model'];settings=settings_for(m,{'wavelengths':[w['index'] for w in m['wavelengths'][:3]],'pupil_axis':args.pupil_axis})
        reply=engine.request_trace(settings,expected_context=engine.state.context(),source='native-validation')
        if reply['status']!='accepted':raise RuntimeError(reply)
        result=next(v[1] for v in engine.trace_pending.values() if v[0]==reply['operation_id']).result(timeout=120)['result']
        if result['status']!='success':raise RuntimeError(result)
        result=engine.state.get_trace(result['result_id'])
        def compare():
            from System import Double, Int32
            import ZOSAPI
            started=time.monotonic();tool=session.open_tool('OpenBatchRayTrace');checked=0;worst=0.
            try:
                reference=session.system.LDE.GetGlobalMatrix(1,*([Double(0)]*12))
                assert reference[0] and len(reference)==13
                for ray in result['rays']:
                    for point in ray['points']:
                        surface=point['surface']
                        native=tool.SingleRayNormUnpol(ZOSAPI.Tools.RayTrace.RaysType.Real,surface,ray['wave_index'],
                            ray['field']['hx'],ray['field']['hy'],ray['px'],ray['py'],False,
                            *([Int32(0)]*2+[Double(0)]*11))
                        assert len(native)==14 and native[0],native
                        assert native[1]==0,native
                        matrix=session.system.LDE.GetGlobalMatrix(surface,*([Double(0)]*12))
                        assert matrix[0] and len(matrix)==13
                        global_xyz=[sum(float(matrix[1+3*i+j])*float(native[3+j]) for j in range(3))+float(matrix[10+i]) for i in range(3)]
                        xyz=[sum(float(reference[1+3*j+i])*(global_xyz[j]-float(reference[10+j])) for j in range(3)) for i in range(3)]
                        for actual,expected in zip(point['xyz'],xyz):
                            assert math.isclose(actual,expected,abs_tol=1e-8,rel_tol=1e-8),(actual,expected)
                            worst=max(worst,abs(actual-expected))
                        checked+=1
                    term=ray['termination']
                    if term['kind']!='complete':
                        native=tool.SingleRayNormUnpol(ZOSAPI.Tools.RayTrace.RaysType.Real,term['first_observed_surface'],ray['wave_index'],
                            ray['field']['hx'],ray['field']['hy'],ray['px'],ray['py'],False,*([Int32(0)]*2+[Double(0)]*11))
                        assert native[1]==term['error_code'] and native[2]==term['vignette_code']
                return {'independent_points':checked,'max_absolute_error':worst,'comparison_ms':(time.monotonic()-started)*1000}
            finally:
                assert not tool.IsRunning
                assert tool.Close()
        comparison=engine.executor.submit(compare).result(timeout=120)
        def compare_display():
            from System import Double
            layout=before['snapshot'].get('display_layout',{})
            if layout.get('status')!='ready':return {'status':'unsupported','issues':layout.get('issues',[])}
            reference=session.system.LDE.GetGlobalMatrix(1,*([Double(0)]*12))
            assert reference[0] and len(reference)==13
            checked=0;worst=0.
            for contour in layout['surfaces']:
                i=contour['index'];native_surface=session.system.LDE.GetSurfaceAt(i)
                assert contour['clear_semi_diameter']==float(native_surface.SemiDiameter)
                assert contour['mechanical_semi_diameter']==float(native_surface.MechanicalSemiDiameter)
                matrix=session.system.LDE.GetGlobalMatrix(i,*([Double(0)]*12))
                assert matrix[0] and len(matrix)==13
                points=contour['points']
                for point in (points[0],points[1],points[len(points)//2],points[-2],points[-1]):
                    # Mechanical lands use the native clear-edge sag. Compare
                    # independently in global coordinates, rather than summing
                    # thicknesses as the axial display adapter does.
                    height=max(-contour['clear_semi_diameter'],min(contour['clear_semi_diameter'],point[1]))
                    sag=session.system.LDE.GetSag(i,height if args.pupil_axis=='X' else 0.,height if args.pupil_axis=='Y' else 0.,Double(0),Double(0))
                    assert sag[0] and math.isfinite(float(sag[1]))
                    local=[point[1],0.,float(sag[1])] if args.pupil_axis=='X' else [0.,point[1],float(sag[1])]
                    world=[sum(float(matrix[1+3*a+b])*local[b] for b in range(3))+float(matrix[10+a]) for a in range(3)]
                    relative=[sum(float(reference[1+3*b+a])*(world[b]-float(reference[10+b])) for b in range(3)) for a in range(3)]
                    for actual,expected in zip(point,[relative[2],relative[0 if args.pupil_axis=='X' else 1]]):
                        assert math.isclose(actual,expected,abs_tol=1e-8,rel_tol=1e-8),(i,actual,expected)
                        worst=max(worst,abs(actual-expected))
                    checked+=1
            return {'status':'success','surfaces':len(layout['surfaces']),'elements':len(layout['elements']),
                    'independent_points':checked,'max_absolute_error':worst}
        display_comparison=engine.executor.submit(compare_display).result(timeout=30)
        engine.refresh();engine.refresh_future.result(timeout=30)
        after=engine.state.read()
        assert before['snapshot']['model']==after['snapshot']['model']
        assert before['state_version']==after['state_version'] and before['model_revision']==after['model_revision']
        assert all(digest(Path(p))==v for p,v in before_bytes.items())
        report={'status':'success','at':datetime.now(timezone.utc).isoformat(),'source':str(source),'source_sha256':digest(source),
                'authorization_path':str(proposal_path),'confirmed_at':authorization.get('confirmed_at'),
                'before_bytes':before_bytes,'source_identity':result['source'],'settings':settings,
                'counts':result['counts'],'elapsed_ms':result['elapsed_ms'],'comparison':comparison,
                'initial_refresh_ms':initial_refresh_ms,
                'layout':after['snapshot']['layout'],'display_layout':after['snapshot'].get('display_layout'),
                'display_comparison':display_comparison,'result_id':result['result_id'],
                'versions_unchanged':True,'source_bytes_and_proposal_unchanged':True}
        out=ROOT/'output/workbench_raytrace_acceptance/reports'/folder.name;out.mkdir(parents=True,exist_ok=True)
        suffix='-xz' if args.pupil_axis=='X' else ''
        (out/f'native-acceptance{suffix}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/f'native-result{suffix}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in {'layout','display_layout'}},ensure_ascii=False),flush=True)
        if args.serve:
            server=WorkbenchHTTPServer(('127.0.0.1',args.port),engine,model_import=False)
            print(f'READ-ONLY original-project validation UI: http://127.0.0.1:{args.port}',flush=True)
            try:server.serve_forever(poll_interval=.1)
            except KeyboardInterrupt:pass
            finally:server.server_close()
        assert all(digest(Path(p))==v for p,v in before_bytes.items())
    finally:
        engine.executor.shutdown(wait=True);session.close()
    return 0


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
