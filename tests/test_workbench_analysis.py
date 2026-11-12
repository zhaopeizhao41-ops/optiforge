"""Trust boundaries, native data contracts and bounded analysis scheduling; offline."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.analysis_data import (AnalysisError, settings_for, validate_settings, interpolate, wavefront_grid,
    primary_wave, length_to_um, suite_verdict, native_wavefront_statistics, compare_wavefront_statistics, RMS_LIMIT)
from core.analysis_native import columns, native_unit, NativeSuite
from core.analysis_runner import run_analysis, close_analysis, configuration, quarantine, NativeOwnershipError
from core.workbench_engine import EngineWorker
from core.workbench_service import WorkbenchHTTPServer
from core.workbench_effects import ToolEffect
from core.zos_session import ZOSSession


def model():
    return {"units":"mm","configuration":1,"name":"same-name","system_mode":"Sequential",
            "fields":[{"index":i+1,"x":0.,"y":float(i)} for i in range(7)],
            "wavelengths":[{"index":1,"wavelength_um":.6328,"is_primary":True,"weight":1.}],
            "settings":{"field_normalization":"Radial"},"surfaces":[{"radius":10},{"radius":20},{"radius":0}]}


def data(m,s,progress=None):
    if progress:progress(s["analyses"][0],s["fields"][0])
    items={k:{"status":"error","message":"fixture no data"} for k in s["analyses"]}
    return {"analysis_schema_version":1,"status":"partial","items":items,"settings":s,"elapsed_ms":1,
            "overall":"ERROR","verdicts":[],"model_metadata":m}


class ContractTests(unittest.TestCase):
    def test_zero_opd_is_valid_and_ideal_wavefront_not_missing(self):
        g=wavefront_grid([[math.nan,0.,math.nan],[0.,0.,0.],[math.nan,0.,math.nan]])
        self.assertEqual(g["valid_count"],5)
        self.assertEqual((g["pv_waves"],g["rms_waves"],g["strehl_estimate"]),(0.,0.,1.))
        self.assertIsNone(g["values"][0][0]);self.assertTrue(g["valid_mask"][0][1])
        self.assertEqual(json.loads(json.dumps(g,allow_nan=False))["values"][0][1],0.)

    def test_no_mask_and_no_valid_data_fail_instead_of_zero_success(self):
        for rows in ([],[[0,0],[0,0]],[[math.nan]*2]*2,[[math.nan,math.inf],[0,1]]):
            with self.subTest(rows=rows),self.assertRaises(AnalysisError):wavefront_grid(rows)
        g=wavefront_grid([[0,0],[0,0]],explicit_mask=[[True,False],[False,True]])
        self.assertEqual(g["valid_count"],2)

    def test_native_header_science_localization_precision_and_mismatch(self):
        for line in ('Peak to valley = 1.000E-3 waves, RMS = 2.50E-4 waves.',
                     '波峰到波谷 = 1,000E-3 波, RMS = 2,50E-4 波.'):
            n=native_wavefront_statistics([line]);self.assertEqual(n["rms_waves"],.00025)
            self.assertAlmostEqual(n["rms_rounding_step"],.000001)
            self.assertTrue(compare_wavefront_statistics({"pv_waves":.001,"rms_waves":.0002504},n)["verified"])
            self.assertFalse(compare_wavefront_statistics({"pv_waves":.001,"rms_waves":.00026},n)["verified"])
        self.assertIsNone(native_wavefront_statistics(['RMS unavailable']))
        self.assertFalse(compare_wavefront_statistics({},None)["verified"])

    def test_exact_threshold_and_unverified_wavefront_cannot_pass(self):
        m=model();s=settings_for(m,{"analyses":["wavefront"],"fields":[1]})
        def verdict(rms,verified):
            g=wavefront_grid([[math.nan,0],[0,rms]])
            g.update(rms_waves=rms,strehl_estimate=math.exp(-(2*math.pi*rms)**2))
            return suite_verdict({"wavefront":{"status":"success","fields":[{"field_index":1,"grid":g,"statistics_comparison":{"verified":verified}}]}},s,m)
        self.assertEqual(verdict(.0719,True)["overall"],"FAIL")
        self.assertEqual(verdict(0,False)["overall"],"WARN")
        self.assertEqual(verdict(0,True)["overall"],"WARN") # only 1/7 fields
        self.assertEqual(RMS_LIMIT,1/14)

    def test_interpolation_is_bracketed_no_nearest_30(self):
        p=interpolate([20.,40.],[.9,.5],30.)
        self.assertEqual(p,{"value":.7,"method":"linear_interpolation","bracket":[20.,40.]})
        self.assertEqual(interpolate([30.,40.],[.8,.5],30.)["method"],"native_exact")
        for x,y in (([0,29],[1,.8]),([31,40],[.8,.7]),([40,20],[.5,.9]),([20,40],[None,.5])):
            self.assertIsNone(interpolate(x,y,30.)["value"])

    def test_missing_mtf_coverage_with_threshold_cannot_pass(self):
        m=model();s=settings_for(m,{"analyses":["mtf"],"mtf_min_at_30lp_mm":.1})
        items={"mtf":{"status":"success","curves":[{"x":[0,20],"y":[1.,.9]}]}}
        self.assertEqual(suite_verdict(items,s,m)["overall"],"ERROR")
        s["mtf_min_at_30lp_mm"]=None
        self.assertEqual(suite_verdict(items,s,m)["overall"],"INFO")

    def test_partial_field_mtf_and_gapped_distortion_never_pass(self):
        m=model();s=settings_for(m,{'analyses':['mtf'],'fields':[1],'mtf_min_at_30lp_mm':.1})
        items={'mtf':{'status':'success','curves':[{'x':[0,40],'y':[1.,.9]}]}}
        self.assertEqual(suite_verdict(items,s,m)['overall'],'WARN')
        s=settings_for(m,{'analyses':['field_curvature'],'distortion_max_pct':1.})
        items={'field_curvature':{'status':'success','curves':[{'kind':'distortion','x':[0,1,2],'y':[0,None,.2]}]}}
        self.assertEqual(suite_verdict(items,s,m)['overall'],'ERROR')

    def test_legacy_aliases_are_normalized_before_native(self):
        s=validate_settings({'analyses':['OPD','fft_mtf','rayfan','distortion']})
        self.assertEqual(s['analyses'],['mtf','wavefront','ray_fan','field_curvature'])

    def test_units_and_primary_not_defaulted(self):
        self.assertEqual([length_to_um(u) for u in ('mm','cm','m','in')],[1000,10000,1000000,25400])
        with self.assertRaises(AnalysisError):length_to_um('unknown')
        m=model();m["wavelengths"][0]["is_primary"]=False
        with self.assertRaises(AnalysisError):primary_wave(m)
        self.assertEqual(native_unit('Units are µm.','transverse'),'µm')
        self.assertEqual(native_unit('空间频率:周期/mm.','frequency'),'lp/mm')
        with self.assertRaises(AnalysisError):native_unit('units unknown','frequency')

    def test_defaults_cover_magnitude_endpoints_and_budget_before_allocation(self):
        m=model();s=settings_for(m)
        self.assertEqual(s["fields"],[1,3,4,6,7])
        self.assertEqual(len(s["analyses"]),5)
        for bad in ({"fields":[0]},{"fields":[]},{"fields":[1,1]},{"fields":[True]},
                    {"sample_size":"9000"},{"max_frequency":math.nan},{"max_frequency":True},
                    {"analyses":[]},{"analyses":["fake"]},{"wavefront_size":512},{"item_timeout_s":999},
                    {"foo":1},{"mtf_min_at_30lp_mm":1.01}):
            with self.subTest(bad=bad),self.assertRaises((AnalysisError,ValueError)):settings_for(m,bad)
        with self.assertRaises(AnalysisError):settings_for(m,{"wavefront_size":256})

    def test_columns_no_empty_or_ambiguous_data(self):
        class Array:
            Rank=2
            def GetLength(self,axis):return (2,1)[axis]
            def __getitem__(self,index):return math.nan
        s=NS(YData=NS(Data=Array()),XData=NS(Data=[0.,1.]),SeriesLabels=['T'])
        with self.assertRaises(AnalysisError):columns(s)
        s.SeriesLabels=[]
        with self.assertRaises(AnalysisError):columns(s)


class LifecycleTests(unittest.TestCase):
    def test_configuration_never_restores_or_reads_after_quarantine(self):
        session=NS(native_cleanup_error=None)
        access=[]
        class MCE:
            current=1
            @property
            def NumberOfConfigurations(self):
                access.append(('total',bool(session.native_cleanup_error)));return 3
            @property
            def CurrentConfiguration(self):
                access.append(('current',bool(session.native_cleanup_error)));return self.current
            def SetCurrentConfiguration(self,value):
                access.append(('set',value,bool(session.native_cleanup_error)));self.current=value
        system=NS(MCE=MCE())
        with patch.object(ZOSSession,'_instance',session):
            with self.assertRaisesRegex(NativeOwnershipError,'original cleanup failure'):
                with configuration(system,2):
                    # serialized_operation can turn the raised error into data;
                    # ownership must be checked even on a normal context exit.
                    quarantine('original cleanup failure')
            self.assertEqual(system.MCE.current,2)
            self.assertEqual([v for v in access if v[0]=='set'],[('set',2,False)])
            self.assertTrue(all(v[-1] is False for v in access))
            before=list(access)
            with self.assertRaises(NativeOwnershipError),configuration(system,3):pass
            self.assertEqual(access,before)

    def test_safe_configuration_restores_on_success_and_ordinary_failure(self):
        for fails in (False,True):
            with self.subTest(fails=fails),patch.object(ZOSSession,'_instance',NS(native_cleanup_error=None)):
                history=[]
                mce=NS(NumberOfConfigurations=3,CurrentConfiguration=1)
                def set_config(value):history.append(value);mce.CurrentConfiguration=value
                mce.SetCurrentConfiguration=set_config
                try:
                    with configuration(NS(MCE=mce),2):
                        self.assertEqual(mce.CurrentConfiguration,2)
                        if fails:raise ValueError('data failure')
                except ValueError:
                    self.assertTrue(fails)
                self.assertEqual(mce.CurrentConfiguration,1);self.assertEqual(history,[2,1])

    def test_no_unbounded_wait_and_normal_close(self):
        class A:
            def Apply(self):self.polls=2
            def IsRunning(self):self.polls-=1;return self.polls>=0
            def WaitForCompletion(self):raise AssertionError('unbounded wait forbidden')
            def GetResults(self):return 'native'
            def Close(self):self.closed=True
        a=A();self.assertEqual(run_analysis(a,.2),'native');close_analysis(a);self.assertTrue(a.closed)

    def test_stuck_termination_quarantines_and_never_closes_running_analysis(self):
        class A:
            def Apply(self):pass
            def IsRunning(self):return True
            def Terminate(self):pass
            def Close(self):raise AssertionError('running close')
        session=NS()
        with patch.object(ZOSSession,'_instance',session),patch('core.analysis_runner._STOP_GRACE_S',.005),patch('core.analysis_runner._POLL_S',.001):
            with self.assertRaises(NativeOwnershipError):run_analysis(A(),.001)
            self.assertTrue(session.native_cleanup_error)
            with self.assertRaises(NativeOwnershipError):close_analysis(A())

    def test_cleanup_failure_stops_suite_instead_of_continuing_other_items(self):
        s=settings_for(model(),{"analyses":["mtf","spot"]})
        runner=NativeSuite(NS(system=NS(Mode='Sequential'),ZOSAPI=None),model(),s)
        calls=[]
        def fail():calls.append('mtf');raise NativeOwnershipError('close failed')
        runner.mtf=fail;runner.spot=lambda:calls.append('spot')
        with self.assertRaises(NativeOwnershipError):runner.execute()
        self.assertEqual(calls,['mtf'])

    def test_ordinary_data_failure_continues_and_reports_partial(self):
        s=settings_for(model(),{"analyses":["mtf","spot"]})
        runner=NativeSuite(NS(system=NS(Mode='Sequential'),ZOSAPI=None),model(),s)
        runner.mtf=lambda:(_ for _ in ()).throw(AnalysisError('ANALYSIS_NO_DATA','no data'))
        runner.spot=lambda:{"fields":[]}
        r=runner.execute();self.assertEqual(r["status"],'partial');self.assertEqual(r["overall"],'ERROR')


class AnalysisCase(unittest.TestCase):
    def setUp(self):
        self.model=model();self.session=object();self.confirmed=True;self.project='a';self.calls=0
        self.engine=EngineWorker({},capture=self.capture,metadata=self.metadata,analysis=self.analyze)
        self.addCleanup(self.engine.executor.shutdown)
        self.engine.refresh();self.engine.refresh_future.result(timeout=2)

    def capture(self):return {"status":"success","schema_version":1,"model":deepcopy(self.model)}
    def metadata(self):return {"active_project":self.project,"model_project":self.project,"proposal_confirmed":self.confirmed,"session_identity":self.session}
    def analyze(self,m,s,p):self.calls+=1;return data(m,s,p)
    def request(self,settings=None):
        reply=self.engine.request_analysis(settings,expected_context=self.engine.state.context())
        if reply["status"]!='accepted':return reply,reply
        result=self.engine.analysis_pending[reply["key"]][1].result(timeout=3)["result"]
        return reply,self.engine.state.get_analysis(result["result_id"]) if result.get('result_id') else result

class EngineTests(AnalysisCase):
    def test_immutable_cache_no_arrays_in_state_or_events_no_revision_change(self):
        before=self.engine.state.read();_,a=self.request();_,b=self.request()
        self.assertEqual(a["result_id"],b["result_id"]);self.assertEqual(self.calls,1)
        b["items"].clear();self.assertTrue(self.engine.state.get_analysis(a['result_id'])['items'])
        after=self.engine.state.read();self.assertEqual(after['state_version'],before['state_version'])
        self.assertEqual(after['model_revision'],before['model_revision'])
        self.assertNotIn('items',after['analysis_summary'][0])
        self.assertTrue(all('items' not in e for e in self.engine.state.events))

    def test_reject_missing_stale_context_and_revoked_authorization_including_cache(self):
        self.request();self.confirmed=False;self.engine.refresh();self.engine.refresh_future.result()
        self.assertEqual(self.request()[0]['code'],'PROPOSAL_NOT_CONFIRMED');self.assertEqual(self.calls,1)
        self.assertEqual(self.engine.request_analysis(expected_context={})['code'],'WORKBENCH_CONTEXT_REQUIRED')

    def test_concurrent_requests_merge_and_state_reads_do_not_wait(self):
        started=threading.Event();release=threading.Event()
        def slow(m,s,p):started.set();release.wait(2);return self.analyze(m,s,p)
        self.engine.analysis_adapter=slow
        ctx=self.engine.state.context();a=self.engine.request_analysis(expected_context=ctx)
        self.assertTrue(started.wait(1));b=self.engine.request_analysis(expected_context=ctx)
        self.assertEqual(a['operation_id'],b['operation_id'])
        self.assertEqual(self.engine.state.read()['analysis_summary'][0]['status'],'running')
        release.set();self.engine.analysis_pending[a['key']][1].result(timeout=2);self.assertEqual(self.calls,1)

    def test_revoke_while_queued_prevents_native_invocation(self):
        started=threading.Event();release=threading.Event()
        future=self.engine.executor.submit(lambda:(started.set(),release.wait(2)))
        self.assertTrue(started.wait(1));r=self.engine.request_analysis(expected_context=self.engine.state.context())
        self.confirmed=False;release.set();future.result()
        result=self.engine.analysis_pending[r['key']][1].result(timeout=2)['result']
        self.assertEqual(result['status'],'error');self.assertEqual(self.calls,0)

    def test_late_model_change_or_revocation_cannot_publish_current_result(self):
        for change in ('model','authorization'):
            with self.subTest(change=change):
                self.confirmed=True;self.engine.refresh();self.engine.refresh_future.result()
                self.engine.state.analysis_results.clear()
                def changed(m,s,p):
                    if change=='model':self.model['surfaces'][0]['radius']+=1
                    else:self.confirmed=False
                    return self.analyze(m,s,p)
                self.engine.analysis_adapter=changed
                _,r=self.request();self.assertEqual(r['code'],'ANALYSIS_RESULT_STALE')
                self.assertFalse(self.engine.state.analysis_results)

    def test_observation_failure_not_hidden_by_later_read(self):
        calls=[];original=self.capture
        def once():
            calls.append(1)
            if len(calls)==2:raise RuntimeError('read failed')
            return original()
        self.engine.capture=once
        _,r=self.request();self.assertEqual(r['status'],'error');self.assertEqual(len(calls),2)
        self.assertFalse(self.engine.state.read()['context_valid'])
        self.assertFalse(self.engine.state.analysis_results)

    def test_same_name_cross_project_never_rebinds_cache(self):
        _,a=self.request();self.project='b';self.engine.refresh();self.engine.refresh_future.result()
        _,b=self.request();self.assertNotEqual(a['key'],b['key']);self.assertEqual(self.calls,2)
        self.assertEqual(self.engine.state.get_analysis(a['result_id'])['source']['model_project'],'a')

    def test_cache_count_byte_budget_and_eviction(self):
        for i in range(9):
            _,r=self.request({'max_frequency':50.+i})
            if not i:first=r['result_id']
        self.assertEqual(len(self.engine.state.analysis_results),8)
        self.assertIsNone(self.engine.state.get_analysis(first))
        _,r=self.request({'max_frequency':1000})
        with patch('core.analysis_data.MAX_RESULT_BYTES',10),self.assertRaises(AnalysisError):self.engine.state.store_analysis(r)

    def test_total_cache_byte_budget_evicts_before_count_limit(self):
        _,first=self.request()
        size=len(json.dumps(first,ensure_ascii=False,allow_nan=False).encode('utf-8'))
        with patch('core.analysis_data.MAX_CACHE_BYTES',size+50):
            _,second=self.request({'max_frequency':105.})
        self.assertIsNone(self.engine.state.get_analysis(first['result_id']))
        self.assertIsNotNone(self.engine.state.get_analysis(second['result_id']))
        summaries=self.engine.state.read()['analysis_summary']
        self.assertEqual(summaries[0]['status'],'evicted')

    def test_cleanup_failure_blocks_trace_reads_and_writes_but_cached_status_readable(self):
        def unsafe(m,s,p):raise NativeOwnershipError('native still running')
        self.engine.analysis_adapter=unsafe
        _,r=self.request();self.assertEqual(r['code'],'ANALYSIS_CLEANUP_FAILED')
        self.assertEqual(self.engine.state.read()['validity'],'uncertain')
        self.engine.registry['write']=lambda:(_ for _ in ()).throw(AssertionError('must not run'))
        self.engine.effects={'write':ToolEffect('modify',True)}
        r=self.engine.call('write',expected_context=self.engine.state.context())
        self.assertEqual(r['status'],'error');self.assertTrue(self.engine.trace_blocked)

    def test_mcp_suite_publishes_detailed_result_with_compact_response(self):
        args={'analyses':['mtf','spot'],'max_frequency':100.,'sample_size':'256x256','max_wavefront_fields':5,
              'mtf_min_at_30lp_mm':None,'distortion_max_pct':None,'config':None,'include_details':False}
        reply=self.engine.call_envelope('zemax_run_analysis_suite',args,expected_context=self.engine.state.context())
        self.assertNotIn('items',reply['result']);self.assertEqual(self.calls,1)
        self.assertTrue(self.engine.state.get_analysis(reply['result']['result_id'])['items'])

    def test_mcp_rejection_preserves_authorization_code(self):
        self.confirmed=False;self.engine.refresh();self.engine.refresh_future.result()
        reply=self.engine.call_envelope('zemax_run_analysis_suite',{},expected_context=self.engine.state.context())
        self.assertEqual(reply['result']['code'],'PROPOSAL_NOT_CONFIRMED');self.assertEqual(self.calls,0)

    def test_mcp_config_and_unrecognized_arguments_checked_before_native(self):
        for args in ({'config':True},{'analyses':[]},{'unrecognized':1},{'max_wavefront_fields':6}):
            reply=self.engine.call_envelope('zemax_run_analysis_suite',args,expected_context=self.engine.state.context())
            self.assertEqual(reply['result']['status'],'error')
        self.assertEqual(self.calls,0)


class GuardTests(unittest.TestCase):
    def test_quarantined_direct_tools_allow_only_pure_knowledge(self):
        from core.operation_guard import serialized_operation
        calls=[]
        def knowledge():calls.append('knowledge');return {'text':'cached'}
        knowledge.__name__='zemax_lookup_manual'
        def native():calls.append('native');return {'status':'success'}
        native.__name__='zemax_system_info'
        with patch.object(ZOSSession,'_instance',NS(native_cleanup_error='not stopped')):
            self.assertEqual(serialized_operation(knowledge)()['text'],'cached')
            self.assertEqual(serialized_operation(native)()['code'],'ANALYSIS_CLEANUP_FAILED')
        self.assertEqual(calls,['knowledge'])


class SingleToolOwnershipTests(unittest.TestCase):
    def test_single_mcp_cleanup_failure_blocks_observation_and_configuration_restore(self):
        from tools import analysis_tools as analysis
        for config in (None,2):
            with self.subTest(config=config):
                session=NS(native_cleanup_error=None,is_connected=True,model_project='a')
                access=[];writes=[];reads=[]
                mce=NS(NumberOfConfigurations=3,CurrentConfiguration=1)
                def set_config(value):
                    writes.append((value,bool(session.native_cleanup_error)));mce.CurrentConfiguration=value
                mce.SetCurrentConfiguration=set_config;session.system=NS(MCE=mce)
                def capture():
                    access.append(bool(session.native_cleanup_error))
                    return {'status':'success','model':{**model(),'configuration':mce.CurrentConfiguration}}
                def metadata():
                    reads.append(bool(session.native_cleanup_error))
                    return {'active_project':'a','model_project':'a','proposal_confirmed':True,'session_identity':session}
                def cleanup(_settings):raise quarantine('single analysis still running')
                with patch.object(ZOSSession,'_instance',session),patch.object(ZOSSession,'get_instance',return_value=session), \
                     patch('tools.project_manager.get_active_project_name',return_value='a'), \
                     patch('tools.system_tools.get_current_design_proposal',return_value={'user_confirmed_to_simulate':True}), \
                     patch.object(analysis,'_suite',side_effect=cleanup):
                    engine=EngineWorker({'zemax_run_field_curvature_distortion':analysis.zemax_run_field_curvature_distortion,
                                         'zemax_lookup_manual':lambda:{'text':'cached manual'}},capture=capture,metadata=metadata)
                    server=WorkbenchHTTPServer(('127.0.0.1',0),engine)
                    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
                    try:
                        engine.refresh();engine.refresh_future.result(timeout=2)
                        before=engine.state.read()
                        req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/tool',method='POST',
                            headers={'X-OptiForge-Token':server.token,'Content-Type':'application/json'},
                            data=json.dumps({'protocol_version':2,'name':'zemax_run_field_curvature_distortion',
                                             'arguments':{'config':config},'expected_context':engine.state.context()}).encode())
                        with urllib.request.urlopen(req,timeout=3) as response:reply=json.load(response)
                        self.assertEqual(reply['result']['code'],'ANALYSIS_CLEANUP_FAILED')
                        state=engine.state.read()
                        self.assertEqual(state['validity'],'uncertain');self.assertFalse(state['context_valid'])
                        self.assertIsNone(state['snapshot']);self.assertEqual(state['error'],'single analysis still running')
                        self.assertEqual(state['model_revision'],before['model_revision'])
                        self.assertEqual(access,[False]);self.assertEqual(reads,[False])
                        self.assertEqual(writes,[] if config is None else [(2,False)])
                        engine.refresh();engine.refresh_future.result(timeout=2)
                        self.assertEqual(engine.call('zemax_lookup_manual')['text'],'cached manual')
                        self.assertEqual(access,[False]);self.assertEqual(reads,[False])
                        self.assertEqual(engine.state.read()['error'],state['error'])
                        self.assertTrue(engine.state.wait_events(state['service_instance_id'],before['event_seq'],0)['events'])
                    finally:
                        server.shutdown();server.server_close();thread.join(2);engine.executor.shutdown()


class HTTPTests(AnalysisCase):
    def test_async_endpoint_and_fixed_trace_service_capability(self):
        server=WorkbenchHTTPServer(('127.0.0.1',0),self.engine)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            req=urllib.request.Request(url+path,headers={'X-OptiForge-Token':server.token})
            return json.load(urllib.request.urlopen(req,timeout=2))
        self.assertTrue(get('/api/session')['capabilities']['analysis'])
        request=urllib.request.Request(url+'/api/analysis',method='POST',headers={'X-OptiForge-Token':server.token,'Content-Type':'application/json'},
            data=json.dumps({'protocol_version':2,'expected_context':self.engine.state.context(),'settings':{}}).encode())
        with urllib.request.urlopen(request,timeout=2) as response:
            self.assertEqual(response.status,202);reply=json.load(response)
        self.engine.analysis_pending[reply['key']][1].result(timeout=2)
        summary=get('/api/state')['analysis_summary'][-1]
        result=get('/api/analyses/'+summary['result_id']);self.assertTrue(result['items'])
        self.engine.analysis_enabled=False;self.assertFalse(get('/api/session')['capabilities']['analysis'])


if __name__=='__main__':unittest.main()
