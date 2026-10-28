"""Optical trust, task boundaries and HTTP cache tests; no native session."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.workbench_trace import (TraceError, assemble, display_point, eligibility, matrix_from_native,
                                  normalize_fields, records, settings_for, trace_session)
from core.workbench_engine import EngineWorker
from core.workbench_effects import ToolEffect
from core.workbench_service import WorkbenchHTTPServer
from core.workbench_snapshot import build_layout

I = ([1.,0.,0.,0.,1.,0.,0.,0.,1.], [0.,0.,0.])


def model():
    return {"units": "mm", "system_mode": "Sequential", "configuration": 2,
            "settings": {"field_normalization": "Radial", "field_type": "Angle", "ray_aiming": "Real"},
            "fields": [{"index":i+1,"x":x,"y":y} for i,(x,y) in enumerate([(0,0),(-3,4),(6,8)])],
            "wavelengths": [{"index":i,"is_primary":i==2,"wavelength_um":w} for i,w in enumerate([.48,.55,.65],1)],
            "surfaces": [{"index":i,"type":"Standard","radius":20.,"conic":0.,"thickness":3.,
                          "semi_diameter":5.,"material":"","is_stop":i==1} for i in range(4)]}


def row(number, surface=1, *, error=0, vig=0, xyz=None):
    return (True,number,error,vig,*(xyz or (number*.1,number*.2,surface*2.)),0.,0.,1.,0.,0.,1.,0.,1.)


def fake_trace(m, settings):
    result = assemble(m, settings, lambda s,r:[row(i,s) for i in range(1,len(r)+1)], lambda s:I)
    result["elapsed_ms"] = 1.
    return result


class MathTests(unittest.TestCase):
    def test_radial_rectangular_complete_table_negative_and_zero(self):
        fields=model()["fields"]
        radial=normalize_fields(fields,"Radial"); rect=normalize_fields(fields,"Rectangular")
        self.assertEqual((radial[1]["hx"],radial[1]["hy"]),(-.3,.4))
        self.assertEqual((rect[1]["hx"],rect[1]["hy"]),(-.5,.5))
        self.assertEqual(normalize_fields([{"x":0,"y":0}],"Radial")[0]["hy"],0)
        self.assertEqual(normalize_fields([{"x":0,"y":-2}],"Rectangular")[0]["hx"],0)

    def test_defaults_primary_existing_fields_and_limits(self):
        m=model(); s=settings_for(m)
        self.assertEqual(s,{"fields":[1,2,3],"wavelengths":[2],"pupil_count":5})
        for invalid in ({"fields":[0]}, {"wavelengths":[9]}, {"pupil_count":True}, {"pupil_count":7}, {"fields":[]}, {"fields":[1,1]}, {"plane":"XZ"}):
            with self.subTest(invalid=invalid),self.assertRaises(TraceError):settings_for(m,invalid)
        m["surfaces"]*=60
        with self.assertRaises(TraceError):settings_for(m)

    def test_subset_field_normalization_does_not_change(self):
        m=model();r=fake_trace(m,settings_for(m,{"fields":[2]}))
        self.assertEqual(r["rays"][0]["field"]["hx"],-.3)
        self.assertEqual([x["py"] for x in r["rays"]],[-1,-.5,0,.5,1])
        self.assertTrue(all(x["px"]==0 for x in r["rays"]))

    def test_matrices_rotation_translation_and_s1_reference(self):
        rotated=([0,-1,0,1,0,0,0,0,1],[10,20,30])
        self.assertEqual(display_point([1,2,3],rotated,I),[8,21,33])
        self.assertEqual(display_point([1,2,3],rotated,rotated),[1,2,3])
        with self.assertRaises(TraceError):matrix_from_native((False,*([0]*12)))

    def test_propagation_order_in_fold_and_native_point_not_sag(self):
        m=model();r=assemble(m,settings_for(m),lambda s,rs:[row(i,s,xyz=(0,0,10-s)) for i in range(1,len(rs)+1)],lambda s:I)
        self.assertEqual([p["xyz"][2] for p in r["rays"][0]["points"]],[9,8,7])
        self.assertEqual([p["surface"] for p in r["rays"][0]["points"]],[1,2,3])
        m["units"]="in";m["surfaces"][1]["type"]="EvenAspheric"
        self.assertEqual(fake_trace(m,settings_for(m))["units"],"in")

    def test_record_order_missing_duplicate_invalid_and_nonfinite(self):
        self.assertEqual(records([row(2),row(1)],2)[1][1],1)
        for rows in ([row(1)],[],[row(1),row(1)],[row(0),row(2)],[row(1),row(3)],[(False,*row(1)[1:]),row(2)]):
            with self.subTest(rows=rows),self.assertRaises(TraceError):records(rows,2)
        for value in (float('nan'),float('inf')):
            with self.assertRaises(TraceError):
                assemble(model(),settings_for(model()),lambda s,rs:[row(i,s,xyz=(value,0,0)) for i in range(1,len(rs)+1)],lambda s:I)
        def invalid_direction(s, rs):
            return [(*row(i,s)[:7],float('nan'),*row(i,s)[8:]) for i in range(1,len(rs)+1)]
        with self.assertRaises(TraceError):assemble(model(),settings_for(model()),invalid_direction,lambda s:I)

    def test_error_and_vignetting_stop_prefix_no_code_surface_guess(self):
        m=model()
        def batch(s,rs):
            return [row(i,s,error=-819 if s>=2 and i%2 else 0,vig=77 if s>=2 and i%2==0 else 0) for i in range(1,len(rs)+1)]
        r=assemble(m,settings_for(m),batch,lambda s:I)
        a,b=r["rays"][:2]
        self.assertEqual(len(a["points"]),1);self.assertEqual(len(b["points"]),2)
        self.assertEqual(a["termination"]["error_code"],-819)
        self.assertEqual(b["termination"]["first_observed_surface"],2)
        self.assertEqual(b["termination"]["vignette_code"],77)
        self.assertEqual(r["counts"]["complete"],0)

    def test_all_failed_no_fake_paths_and_d2_outline_unchanged(self):
        m=model();m["surfaces"][1].update(radius=-1.82,semi_diameter=7.5)
        before=deepcopy(m)
        self.assertNotEqual(build_layout(m)["status"],"ready")
        result=assemble(m,settings_for(m),lambda s,rs:[row(i,s,error=31) for i in range(1,len(rs)+1)],lambda s:I)
        self.assertEqual(result["counts"]["drawable"],0);self.assertIsNone(result["bounds_xyz"])
        self.assertGreater(fake_trace(m,settings_for(m))["counts"]["drawable"],0)
        self.assertEqual(m,before)

    def test_error_and_vignetting_codes_can_coexist_without_losing_either(self):
        m=model();r=assemble(m,settings_for(m),lambda s,rs:[row(i,s,error=-42,vig=8) for i in range(1,len(rs)+1)],lambda s:I)
        self.assertEqual(r['counts']['error'],15);self.assertEqual(r['counts']['vignetted'],15);self.assertEqual(r['counts']['both'],15)
        self.assertEqual(r['counts']['drawable'],0)

    def test_ignored_field_ambiguity_is_explicitly_rejected(self):
        m=model();m["fields"][-1]["ignore"]=True
        with self.assertRaisesRegex(TraceError,'归一化'):fake_trace(m,settings_for(m))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.model=model();self.session=object();self.confirmed=True;self.calls=0
        self.engine=EngineWorker({},capture=self.capture,metadata=self.metadata,trace=self.trace)
        self.engine.refresh();self.engine.refresh_future.result(timeout=2)
        self.addCleanup(self.engine.executor.shutdown)

    def capture(self):
        return {"status":"success","schema_version":1,"model":deepcopy(self.model),"layout":{}}
    def metadata(self):
        return {"active_project":"a","model_project":"a","proposal_confirmed":self.confirmed,"session_identity":self.session}
    def trace(self,m,s):
        self.calls+=1;return fake_trace(m,s)
    def request(self,s=None):
        reply=self.engine.request_trace(s,expected_context=self.engine.state.context())
        if reply["status"]=="accepted":
            future=next(v[1] for v in self.engine.trace_pending.values() if v[0]==reply["operation_id"])
            result=future.result(timeout=3)["result"]
            return reply,self.engine.state.get_trace(result["result_id"]) if result.get("result_id") else result
        return reply,reply

    def test_success_cache_hit_versions_immutable_and_no_points_in_state(self):
        before=self.engine.state.read();reply,result=self.request();after=self.engine.state.read()
        for k in ('state_version','model_revision'):self.assertEqual(before[k],after[k])
        self.assertNotIn('rays',after['trace_summary'][0])
        _,cached=self.request();self.assertEqual(result['result_id'],cached['result_id']);self.assertEqual(self.calls,1)
        cached['rays'].clear();self.assertTrue(self.engine.state.get_trace(result['result_id'])['rays'])
        self.assertEqual(result['source']['source_state_version'],before['state_version'])
        self.assertFalse(any('rays' in e for e in self.engine.state.events))

    def test_all_gates_before_adapter_and_cache(self):
        self.request();self.confirmed=False;self.engine.refresh();self.engine.refresh_future.result()
        self.assertEqual(self.request()[0]['code'],'PROPOSAL_NOT_CONFIRMED');self.assertEqual(self.calls,1)
        self.engine.uncertainty='old failure';self.engine.refresh();self.engine.refresh_future.result()
        self.assertEqual(self.request()[0]['code'],'WORKBENCH_STATE_UNCERTAIN');self.assertEqual(self.engine.uncertainty,'old failure')
        reply=self.engine.request_trace(expected_context={})
        self.assertEqual(reply['code'],'WORKBENCH_CONTEXT_REQUIRED')

    def test_failure_does_not_advance_or_mark_model_modified(self):
        def fail(m,s):raise TraceError('TRACE_TIMEOUT','timeout')
        self.engine.trace_adapter=fail;before=self.engine.state.read();_,r=self.request();after=self.engine.state.read()
        self.assertEqual(r['code'],'TRACE_TIMEOUT')
        self.assertEqual(before['state_version'],after['state_version']);self.assertEqual(after['model_revision'],before['model_revision'])
        self.assertIsNone(self.engine.uncertainty)

    def assert_observation_invalidated(self, before):
        after = self.engine.state.read()
        self.assertFalse(after['context_valid'])
        self.assertNotEqual(after['validity'], 'valid')
        self.assertIsNone(after['snapshot'])
        self.assertIsNone(after['snapshot_context'])
        self.assertEqual(after['model_revision'], before['model_revision'])
        self.assertGreater(after['state_version'], before['state_version'])
        writes = []
        self.engine.registry['protected_write'] = lambda: writes.append('write')
        self.engine.effects = {'protected_write': ToolEffect('modify', True)}
        old = {k: before[k] for k in self.engine.state.context()}
        self.assertEqual(self.engine.call('protected_write', expected_context=old)['code'], 'WORKBENCH_CONTEXT_STALE')
        self.assertEqual(self.engine.call('protected_write', expected_context=self.engine.state.context())['code'], 'WORKBENCH_STATE_UNCERTAIN')
        self.assertEqual(self.request()[1]['code'], 'WORKBENCH_STATE_UNCERTAIN')
        self.assertEqual(writes, [])

    def test_observation_failures_invalidate_before_after_compute_and_cache(self):
        for cached in (False, True):
            for boundary in (1, 2):
                for reader in ('metadata', 'capture'):
                    with self.subTest(cached=cached, boundary=boundary, reader=reader):
                        self.engine.metadata = self.metadata
                        self.engine.capture = self.capture
                        self.engine.refresh(); self.engine.refresh_future.result(timeout=2)
                        self.engine.state.trace_results.clear()
                        if cached:
                            self.request()
                        before = self.engine.state.read()
                        calls_before = self.calls
                        read = getattr(self, reader)
                        observations = []
                        def fail_once():
                            observations.append(reader)
                            if len(observations) == boundary:
                                raise RuntimeError(f'{reader} unavailable')
                            return read()
                        setattr(self.engine, reader, fail_once)
                        _, result = self.request()
                        self.assertEqual(result['status'], 'error')
                        self.assert_observation_invalidated(before)
                        # A later successful retry must not hide the failed boundary.
                        self.assertEqual(len(observations), boundary)
                        self.assertEqual(self.calls - calls_before, int(not cached and boundary == 2))

    def test_failed_computation_observation_also_invalidates(self):
        before = self.engine.state.read()
        def fail(m, s):
            self.engine.metadata = lambda: (_ for _ in ()).throw(RuntimeError('metadata unavailable'))
            raise TraceError('TRACE_TIMEOUT', 'timeout')
        self.engine.trace_adapter = fail
        _, result = self.request()
        self.assertEqual(result['code'], 'TRACE_TIMEOUT')
        self.assert_observation_invalidated(before)

    def test_observation_error_preserves_existing_uncertainty(self):
        before = self.engine.state.read()
        self.engine.uncertainty = 'unresolved previous write'
        self.engine.metadata = lambda: (_ for _ in ()).throw(RuntimeError('metadata unavailable'))
        self.request()
        self.assert_observation_invalidated(before)
        self.assertEqual(self.engine.uncertainty, 'unresolved previous write')

    def test_same_physical_key_recomputes_for_changed_project_without_rebinding_old_source(self):
        reply_a, result_a = self.request()
        before = self.engine.state.read()
        self.engine.metadata = lambda: {**self.metadata(), 'active_project': 'b', 'model_project': 'b'}
        self.engine.refresh(); self.engine.refresh_future.result(timeout=2)
        reply_b, result_b = self.request()
        self.assertEqual(reply_a['key'], reply_b['key'])
        self.assertNotEqual(result_a['result_id'], result_b['result_id'])
        self.assertEqual(self.calls, 2)
        self.assertEqual(result_b['source']['active_project'], 'b')
        self.assertEqual(result_b['source']['model_project'], 'b')
        self.assertEqual(self.engine.state.get_trace(result_a['result_id']), result_a)
        after = self.engine.state.read()
        self.assertEqual(after['model_revision'], before['model_revision'])
        self.assertEqual(after['model_id'], before['model_id'])
        _, again = self.request()
        self.assertEqual(again['result_id'], result_b['result_id'])
        self.assertEqual(self.calls, 2)

    def test_concurrent_same_key_merges_and_other_settings_do_not(self):
        started=threading.Event();release=threading.Event()
        def slow(m,s):started.set();release.wait(2);return self.trace(m,s)
        self.engine.trace_adapter=slow
        context=self.engine.state.context();a=self.engine.request_trace(expected_context=context);self.assertTrue(started.wait(1))
        b=self.engine.request_trace(expected_context=context);self.assertEqual(a,b)
        c=self.engine.request_trace({'pupil_count':3},expected_context=context);self.assertNotEqual(a['operation_id'],c['operation_id'])
        release.set();[f.result(timeout=3) for _,f in self.engine.trace_pending.values()]
        self.assertEqual(self.calls,2)

    def test_pre_and_post_observation_rejects_invisible_change_and_late_result(self):
        self.model['surfaces'][1]['radius']=25
        _,r=self.request();self.assertEqual(r['code'],'WORKBENCH_CONTEXT_STALE');self.assertEqual(self.calls,0)
        def changed(m,s):self.model['surfaces'][1]['radius']=30;return self.trace(m,s)
        self.engine.trace_adapter=changed
        _,r=self.request();self.assertEqual(r['code'],'TRACE_RESULT_STALE');self.assertEqual(len(self.engine.state.trace_results),0)

    def test_session_change_and_queued_context_rejected(self):
        self.session=object();_,r=self.request();self.assertEqual(r['code'],'WORKBENCH_CONTEXT_STALE');self.assertEqual(self.calls,0)

    def test_bounded_cache_and_results_evicted(self):
        for n in range(9):
            fields=[1] if n<3 else [2] if n<6 else [3]
            _,r=self.request({'fields':fields,'pupil_count':(3,5,9)[n%3]})
            if n==0:first=r['result_id']
        self.assertEqual(len(self.engine.state.trace_results),8);self.assertIsNone(self.engine.state.get_trace(first))
        self.assertLessEqual(len(self.engine.state.read()['trace_summary']),8)

    def test_two_finished_same_key_requests_keep_both_operation_summaries(self):
        a,_=self.request();b,_=self.request()
        summaries=self.engine.state.read()['trace_summary']
        self.assertEqual({s['operation_id'] for s in summaries},{a['operation_id'],b['operation_id']})
        self.assertEqual(len({s['result_id'] for s in summaries}),1)

    def test_queued_old_context_rejected_before_native(self):
        blocker=threading.Event()
        gate=self.engine.executor.submit(lambda:blocker.wait(2))
        context=self.engine.state.context();a=self.engine.request_trace(expected_context=context)
        self.engine.state.publish(metadata={'active_project':'b','model_project':'b','proposal_confirmed':True},snapshot=self.capture(),validity='valid')
        blocker.set();gate.result(timeout=3)
        result=next(v[1] for v in self.engine.trace_pending.values() if v[0]==a['operation_id']).result(timeout=3)['result']
        self.assertEqual(result['code'],'WORKBENCH_CONTEXT_STALE');self.assertEqual(self.calls,0)

    def test_byte_budget_is_enforced_without_publishing_oversized_result(self):
        with patch('core.workbench_trace.MAX_BYTES',20):
            _,result=self.request()
        self.assertEqual(result['code'],'TRACE_RESULT_TOO_LARGE');self.assertEqual(len(self.engine.state.trace_results),0)

    def test_cleanup_failure_blocks_future_native_computation_without_model_mutation(self):
        def fail(m,s):raise TraceError('TRACE_CLEANUP_FAILED','tool open')
        self.engine.trace_adapter=fail;before=self.engine.state.read();_,r=self.request()
        self.assertEqual(r['code'],'TRACE_CLEANUP_FAILED');self.assertEqual(self.engine.state.read()['model_revision'],before['model_revision'])
        self.engine.trace_adapter=self.trace;self.assertEqual(self.request()[1]['code'],'TRACE_CLEANUP_FAILED');self.assertEqual(self.calls,0)

    def test_cleanup_failure_blocks_native_reads_and_observation_but_not_knowledge_or_cached_state(self):
        self.request()
        cached = next(iter(self.engine.state.trace_results))
        before = self.engine.state.read()
        calls = []
        self.engine.registry.update({name: lambda: calls.append('native') or {'status': 'success'}
                                     for name in ('zemax_generate_folded_drawing', 'zemax_audit_retroreflection',
                                                  'zemax_system_info', 'zemax_get_project', 'zemax_set_project',
                                                  'zemax_run_analysis_suite', 'zemax_surface_operations')})
        self.engine.registry['zemax_lookup_manual'] = lambda: {'status': 'success', 'text': 'cached manual'}
        self.engine.trace_blocked = 'tool stop/close unknown'
        with patch.object(self.engine, 'capture', side_effect=AssertionError('must not observe native model')):
            for name in ('zemax_generate_folded_drawing', 'zemax_audit_retroreflection', 'zemax_system_info',
                         'zemax_get_project', 'zemax_set_project', 'zemax_run_analysis_suite',
                         'zemax_surface_operations', '__refresh__'):
                with self.subTest(name=name):
                    result = self.engine.call(name, expected_context=self.engine.state.context())
                    self.assertEqual(result['code'], 'TRACE_CLEANUP_FAILED')
            self.assertEqual(self.engine.call('zemax_lookup_manual')['text'], 'cached manual')
            self.assertIsNotNone(self.engine.state.get_trace(cached))
            self.assertTrue(self.engine.state.wait_events(before['service_instance_id'], before['event_seq'], 0)['events'])
        self.assertEqual(calls, [])
        after = self.engine.state.read()
        for key in ('model_revision', 'state_version', 'snapshot'):
            self.assertEqual(after[key], before[key])


class LifecycleTests(unittest.TestCase):
    def run_adapter(self, *, timeout=False, close=True, occupied=False):
        calls=[];tool=NS(IsRunning=False,CanCancel=True,Succeeded=True,ErrorMessage='')
        def run(seconds):calls.append('run');tool.IsRunning=timeout
        def cancel():calls.append('cancel');return True
        def wait(seconds):calls.append('wait');tool.IsRunning=False
        def closing():calls.append('close');return close
        tool.RunAndWaitWithTimeout=run;tool.Cancel=cancel;tool.WaitWithTimeout=wait;tool.Close=closing
        def create(count,kind,surface):
            iterator=iter([row(i,surface) for i in range(1,count+1)]+[(False,*([0]*14))])
            return NS(AddRay=lambda *a:None,StartReadingResults=lambda:None,ReadNextResult=lambda *a:next(iterator))
        tool.CreateNormUnpol=create
        def opener(name):
            if occupied:raise RuntimeError('already open')
            return tool
        session=NS(system=NS(Mode='Sequential',LDE=NS(GetGlobalMatrix=lambda *a:(True,*I[0],*I[1]))),open_tool=opener)
        api=NS(Tools=NS(RayTrace=NS(RaysType=NS(Real=1),OPDMode=NS(**{'None':0}))))
        with patch.dict(sys.modules,{'System':NS(Double=float,Int32=int),'ZOSAPI':api}):
            try:return trace_session(session,model(),settings_for(model())),calls
            except Exception as e:return e,calls
    def test_timeout_waits_for_native_stop_before_close(self):
        error,calls=self.run_adapter(timeout=True);self.assertEqual(error.code,'TRACE_TIMEOUT')
        self.assertEqual(calls,['run','cancel','wait','close'])
    def test_cleanup_failure_and_existing_tool(self):
        error,calls=self.run_adapter(close=False);self.assertEqual(error.code,'TRACE_CLEANUP_FAILED')
        error,calls=self.run_adapter(occupied=True);self.assertIn('already open',str(error));self.assertEqual(calls,[])
    def test_normal_closes_each_target(self):
        result,calls=self.run_adapter();self.assertEqual(result['counts']['complete'],15);self.assertEqual(calls.count('close'),3)
    def test_grin_rejected_without_opening_tool(self):
        m=model();m['surfaces'][1]['type']='Gradient3'
        with patch.dict(sys.modules,{'System':NS(Double=float,Int32=int),'ZOSAPI':NS()}),self.assertRaises(TraceError):
            trace_session(NS(system=NS(Mode='Sequential')),m,settings_for(m))


class HTTPTests(EngineTests):
    def test_post_trace_and_get_result_token_and_no_recompute(self):
        server=WorkbenchHTTPServer(('127.0.0.1',0),self.engine)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}'
        def request(path,payload=None,token=True):
            req=urllib.request.Request(url+path,data=json.dumps(payload).encode() if payload else None,
                headers={'X-OptiForge-Token':server.token if token else '', 'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as response:return json.load(response)
        reply=request('/api/trace',{'protocol_version':2,'expected_context':self.engine.state.context(),'settings':{'fields':[1]}})
        self.assertEqual(reply['status'],'accepted')
        for _,future in self.engine.trace_pending.values():future.result(timeout=2)
        summary=request('/api/state')['trace_summary'][0]
        result=request('/api/traces/'+summary['result_id']);self.assertEqual(result['key'],reply['key'])
        request('/api/traces/'+summary['result_id']);self.assertEqual(self.calls,1)
        with self.assertRaises(urllib.error.HTTPError):request('/api/traces/'+summary['result_id'],token=False)
        with self.assertRaises(urllib.error.HTTPError):request('/api/traces/missing')


if __name__=='__main__':unittest.main()
