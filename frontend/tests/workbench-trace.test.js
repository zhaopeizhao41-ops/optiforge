import test from 'node:test';
import assert from 'node:assert/strict';
import { TraceController, defaultTraceSettings, matchesTrace, projection, settingsId } from '../src/workbench-trace.js';
import { createWorkbenchApi } from '../src/workbench-api.js';

const settings = {fields:[1],wavelengths:[1],pupil_count:5,pupil_axis:'Y'};
const state = () => ({ service_instance_id:'i',model_id:'m',model_revision:1,state_version:1,context_valid:true,validity:'valid',
  active_project:'a',model_project:'a',proposal_confirmed:true,tasks:{pending:[],recent:[]},trace_summary:[],
  snapshot:{model:{configuration:1,fields:[{index:1,x:0,y:0},{index:2,x:-3,y:4},{index:3,x:6,y:8}],wavelengths:[{index:1,is_primary:true}]}} });
const result = (s=state(),cfg=settings) => ({source:{...s,configuration:1},settings:cfg,settings_hash:'h',key:'key',result_id:'r',rays:[]});
const tick = () => new Promise(r=>setTimeout(r,10));
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(api={}) {
  const calls=[];
  const c=new TraceController({trace:async (...args)=>{calls.push(args);return {key:'key',operation_id:'op'};},readTrace:async()=>result(),...api},{debounce:1});
  c.update(state(),'synced',settings);return {c,calls};
}

test('defaults select actual min/middle/max and primary; subset order is canonical',()=>{
  assert.deepEqual(defaultTraceSettings(state().snapshot.model),{...settings,fields:[1,2,3]});
  assert.equal(settingsId({...settings,fields:[3,1]}),settingsId({...settings,fields:[1,3]}));
});
test('orthogonal view is part of result identity and uses only symmetric contours',()=>{
  assert.notEqual(settingsId({...settings,pupil_axis:'X'}),settingsId(settings));
  const contour={status:'ready',symmetric_planes:['YZ','XZ'],surfaces:[{points:[[0,-2],[10,2]]}]};
  assert.equal(projection(contour,{rays:[]},'XZ').contours,contour);
  assert.equal(projection({status:'ready',surfaces:[]},{rays:[]},'XZ').contours,null);
  const xz=projection(contour,{rays:[{px:1,py:0,points:[{xyz:[1,0,0]},{xyz:[2,0,5]}]}]},'XZ');
  assert.deepEqual(xz.rays[0].projected,[[0,1],[5,2]]);
});
test('source/configuration/context/authorization/settings/connection all guard display',()=>{
  const s=state(),r=result();assert.equal(matchesTrace(r,s,settings,'synced'),true);
  for(const [k,v] of Object.entries({service_instance_id:'x',model_id:'x',model_revision:2,active_project:'x',proposal_confirmed:false,context_valid:false,validity:'uncertain'}))
    assert.equal(matchesTrace(r,{...s,[k]:v},settings,'synced'),false,k);
  assert.equal(matchesTrace(r,s,{...settings,pupil_count:3},'synced'),false);
  assert.equal(matchesTrace(r,s,settings,'reconnecting'),false);
  assert.equal(matchesTrace(r,{...s,snapshot:{model:{configuration:2}}},settings,'synced'),false);
});
test('no automatic trace before enable; coalesces changes and waits for writes',async()=>{
  const {c,calls}=fixture();await tick();assert.equal(calls.length,0);
  c.enable(true);c.update(state(),'synced',{...settings,pupil_count:3});c.update(state(),'synced',{...settings,pupil_count:9});
  await tick();assert.equal(calls.length,1);assert.equal(calls[0][0].pupil_count,9);c.stop();
  const f=fixture();const s=state();s.tasks.pending=[{tool:'zemax_surface_operations'}];f.c.update(s,'running',settings);f.c.enable(true);
  await tick();assert.equal(f.calls.length,0);f.c.update(state(),'synced',settings);await tick();assert.equal(f.calls.length,1);f.c.stop();
});
test('completion fetches immutable result and never makes another POST',async()=>{
  const {c,calls}=fixture();c.enable(true);await tick();
  const s=state();s.trace_summary=[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}];
  c.update(s,'synced',settings);await tick();assert.equal(c.view.result.result_id,'r');
  c.update(s,'synced',settings);await tick();assert.equal(calls.length,1);c.stop();
});
test('settings change during running keeps one in flight and submits only latest after completion',async()=>{
  const {c,calls}=fixture();c.enable(true);await tick();
  c.update(state(),'running',{...settings,pupil_count:3});c.update(state(),'running',{...settings,pupil_count:9});await tick();assert.equal(calls.length,1);
  const s=state();s.trace_summary=[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}];c.update(s,'synced',{...settings,pupil_count:9});await tick();await tick();
  assert.equal(calls.length,2);assert.equal(calls[1][0].pupil_count,9);assert.equal(c.view.result,null);c.stop();
});

test('switching YZ to XZ while pending rejects the old pupil fan and submits the new axis once',async(t)=>{
  const pending=deferred(),calls=[],xz={...settings,pupil_axis:'X'};
  const c=new TraceController({
    trace:async cfg=>{calls.push(cfg);return {key:cfg.pupil_axis,operation_id:cfg.pupil_axis};},
    readTrace:id=>id==='Y'?pending.promise:Promise.resolve({...result(state(),xz),key:'X'}),
  },{debounce:1});t.after(()=>c.stop());
  c.update(state(),'synced',settings);c.enable(true);await tick();
  const complete={...state(),trace_summary:[{operation_id:'Y',key:'Y',status:'succeeded',result_id:'Y'}]};
  c.update(complete,'synced',settings);await tick();c.update(complete,'synced',xz);
  pending.resolve(result());await tick();await tick();
  assert.equal(c.view.result,null);assert.equal(calls.length,2);assert.equal(calls[1].pupil_axis,'X');
  assert.equal(matchesTrace(result(),state(),xz,'synced'),false);
  c.update({...state(),trace_summary:[{operation_id:'X',key:'X',status:'succeeded',result_id:'X'}]},'synced',xz);await tick();
  assert.equal(c.view.result.settings.pupil_axis,'X');assert.equal(calls.length,2);
});
test('late result cannot render after model change and enabling resets on new model/service',async()=>{
  const pending=deferred();const {c}=fixture({readTrace:()=>pending.promise});c.enable(true);await tick();
  const s=state();s.trace_summary=[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}];c.update(s,'synced',settings);
  c.update({...state(),model_id:'different'},'synced',settings);pending.resolve(result());await tick();
  assert.equal(c.view.enabled,false);assert.equal(c.view.result,null);c.stop();
});

for (const identity of ['service_instance_id', 'model_id']) {
  for (const stage of ['accepted', 'post', 'get']) {
    for (const outcome of stage === 'accepted' ? ['none'] : ['resolve', 'reject']) {
      test(`${identity} switch after ${stage}: old ${outcome} cannot occupy or clear the new task`, async (t) => {
        const old = deferred(), fresh = deferred(), requests = [], reads = [];
        const oldReply = {key:'old-key',operation_id:'old-op'}, newReply = {key:'new-key',operation_id:'new-op'};
        const {c} = fixture({
          trace: (cfg, s, signal) => {
            requests.push({s, signal});
            return requests.length === 1 ? stage === 'post' ? old.promise : Promise.resolve(oldReply) : Promise.resolve(newReply);
          },
          readTrace: (id, signal) => { reads.push({id, signal}); return id === 'old-r' ? old.promise : fresh.promise; },
        });
        t.after(()=>c.stop());
        c.enable(true); await tick();
        if (stage === 'get') {
          c.update({...state(),trace_summary:[{...oldReply,status:'succeeded',result_id:'old-r'}]},'synced',settings);
          assert.equal(reads.length,1);
        }
        const next = {...state(),[identity]:'new-identity'};
        c.update(next,'synced',settings);
        assert.equal(c.view.enabled,false);
        assert.equal(Boolean(c.pending),false); assert.equal(c.inflight,false); assert.equal(c.reading,false);
        assert.equal(requests[0].signal.aborted,true);
        c.enable(true); await tick();
        assert.equal(requests.length,2);
        const complete = {...next,trace_summary:[{...newReply,status:'succeeded',result_id:'new-r'}]};
        c.update(complete,'synced',settings);
        assert.equal(reads.at(-1).id,'new-r'); assert.equal(reads.at(-1).signal.aborted,false);
        const view = {...c.view};
        if (outcome === 'resolve') old.resolve(stage === 'post' ? oldReply : {...result(),key:'old-key',result_id:'old-r'});
        if (outcome === 'reject') old.reject(new Error('old transport failed'));
        await tick();
        assert.deepEqual(c.view,view); assert.equal(c.pending.operation_id,'new-op');
        assert.equal(c.reading,true); assert.equal(c.inflight,true); assert.equal(requests.length,2);
        fresh.resolve({...result(next),key:'new-key',result_id:'new-r'}); await tick();
        assert.equal(c.view.result.result_id,'new-r'); assert.equal(c.view.status,'ready');
        assert.equal(c.pending,null); assert.equal(c.reading,false); assert.equal(c.inflight,false);
        c.update(complete,'synced',settings); await tick(); assert.equal(requests.length,2);
      });
    }
  }
}

test('same-instance reconnect while POST is unresolved does not abort or replay it',async(t)=>{
  const post=deferred(),requests=[];
  const {c}=fixture({trace:(cfg,s,signal)=>{requests.push(signal);return post.promise;}});t.after(()=>c.stop());
  c.enable(true);await tick();c.update(state(),'reconnecting',settings);c.update(state(),'synced',settings);await tick();
  assert.equal(requests.length,1);assert.equal(requests[0].aborted,false);
  post.resolve({key:'key',operation_id:'op'});await tick();
  c.update({...state(),trace_summary:[{key:'key',operation_id:'op',status:'succeeded',result_id:'r'}]},'synced',settings);await tick();
  assert.equal(c.view.status,'ready');assert.equal(requests.length,1);
});

test('project migration with unchanged physical key displays a new result and preserves the old source',async(t)=>{
  const a=result(),b={...result({...state(),active_project:'b',model_project:'b'}),result_id:'b-r'};
  const replies=[{key:'key',operation_id:'op'},{key:'key',operation_id:'b-op'}];let posts=0;
  const {c}=fixture({trace:async()=>replies[posts++],readTrace:async id=>id==='r'?a:b});t.after(()=>c.stop());
  c.enable(true);await tick();
  c.update({...state(),trace_summary:[{...replies[0],status:'succeeded',result_id:'r'}]},'synced',settings);await tick();
  assert.equal(c.view.result,a);
  const next={...state(),active_project:'b',model_project:'b',state_version:2};c.update(next,'synced',settings);await tick();
  assert.equal(matchesTrace(a,next,settings,'synced'),false);assert.equal(posts,2);
  c.update({...next,trace_summary:[{...replies[1],status:'succeeded',result_id:'b-r'}]},'synced',settings);await tick();
  assert.equal(c.view.status,'ready');assert.equal(c.view.result,b);
  assert.equal(a.source.active_project,'a');assert.equal(a.source.model_project,'a');
});

test('transient result GET retries once after reconnect, never from repeated events or by POST',async(t)=>{
  const recovered=deferred();let reads=0;
  const {c,calls}=fixture({readTrace:()=>++reads===1?Promise.reject(new Error('network interrupted')):recovered.promise});t.after(()=>c.stop());
  c.enable(true);await tick();const complete={...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]};
  c.update(complete,'synced',settings);await tick();assert.equal(c.view.status,'failed');
  assert.equal(c.pending.operation_id,'op');
  for(let i=0;i<20;i++)c.update(complete,'synced',settings);
  await tick();assert.equal(reads,1);assert.equal(calls.length,1);
  c.update(complete,'reconnecting',settings);assert.equal(reads,1);
  c.update(complete,'synced',settings);
  for(let i=0;i<20;i++)c.update(complete,'synced',settings);
  assert.equal(reads,2);assert.equal(calls.length,1);
  c.retry();c.retry();assert.equal(reads,2);
  recovered.resolve(result());await tick();assert.equal(c.view.status,'ready');assert.equal(c.view.result.result_id,'r');
  c.update(complete,'reconnecting',settings);c.update(complete,'synced',settings);await tick();
  assert.equal(reads,2);assert.equal(calls.length,1);
});

test('reconnect completed before the old result GET rejects still permits one bounded recovery read',async(t)=>{
  const old=deferred();let reads=0;
  const {c,calls}=fixture({readTrace:()=>++reads===1?old.promise:Promise.resolve(result())});t.after(()=>c.stop());
  c.enable(true);await tick();const complete={...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]};
  c.update(complete,'synced',settings);c.update(complete,'reconnecting',settings);c.update(complete,'synced',settings);
  old.reject(new Error('late network failure'));await tick();
  assert.equal(reads,2);assert.equal(calls.length,1);assert.equal(c.view.status,'ready');
});

test('result arriving during reconnect is hidden until sync returns and needs no new GET or POST',async(t)=>{
  const pending=deferred();let reads=0;
  const {c,calls}=fixture({readTrace:()=>{reads++;return pending.promise;}});t.after(()=>c.stop());
  c.enable(true);await tick();const complete={...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]};
  c.update(complete,'synced',settings);c.update(complete,'reconnecting',settings);pending.resolve(result());await tick();
  assert.equal(matchesTrace(c.view.result,complete,settings,'reconnecting'),false);
  c.update(complete,'synced',settings);await tick();
  assert.equal(matchesTrace(c.view.result,complete,settings,'synced'),true);assert.equal(reads,1);assert.equal(calls.length,1);
});

test('automatic result GET recovery has a fixed budget across repeated reconnects',async(t)=>{
  let reads=0;const {c,calls}=fixture({readTrace:async()=>{reads++;throw new Error('network interrupted');}});t.after(()=>c.stop());
  c.enable(true);await tick();const complete={...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]};
  c.update(complete,'synced',settings);await tick();
  for(let i=0;i<5;i++){c.update(complete,'reconnecting',settings);c.update(complete,'synced',settings);await tick();}
  assert.equal(reads,2);assert.equal(calls.length,1);assert.equal(c.view.status,'failed');
});

test('explicit retry after a transient result failure reads the accepted result without another POST',async(t)=>{
  let reads=0;const {c,calls}=fixture({readTrace:async()=>{if(++reads===1)throw new Error('network interrupted');return result();}});t.after(()=>c.stop());
  c.enable(true);await tick();const complete={...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]};
  c.update(complete,'synced',settings);await tick();c.retry();await tick();
  assert.equal(reads,2);assert.equal(calls.length,1);assert.equal(c.view.status,'ready');
});

test('obsolete result waiting for reconnect does not block tracing the latest revision',async(t)=>{
  const {c,calls}=fixture({readTrace:async()=>{throw new Error('network interrupted');}});t.after(()=>c.stop());
  c.enable(true);await tick();c.update({...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]},'synced',settings);await tick();
  c.update({...state(),model_revision:2},'synced',settings);await tick();
  assert.equal(calls.length,2);assert.equal(calls[1][1].model_revision,2);
});

for(const status of [404,410])test(`result GET ${status} is terminal and never loops on reconnect`,async(t)=>{
  let reads=0;const {c,calls}=fixture({readTrace:async()=>{reads++;throw Object.assign(new Error('结果已淘汰'),{status,code:'TRACE_NOT_FOUND'});}});t.after(()=>c.stop());
  c.enable(true);await tick();const complete={...state(),trace_summary:[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}]};
  c.update(complete,'synced',settings);await tick();
  for(let i=0;i<3;i++){c.update(complete,'reconnecting',settings);c.update(complete,'synced',settings);await tick();}
  assert.equal(reads,1);assert.equal(calls.length,1);assert.equal(c.pending,null);assert.equal(c.view.status,'failed');
});

test('trace result HTTP errors preserve status and code for terminal versus transient recovery',async()=>{
  for(const status of [404,410,503]){
    const api=createWorkbenchApi(async()=>({ok:false,status,json:async()=>({message:'unavailable',code:status===503?'SERVICE_UNAVAILABLE':'TRACE_NOT_FOUND'})}));
    await assert.rejects(api.readTrace('r'),error=>error.status===status&&error.code===(status===503?'SERVICE_UNAVAILABLE':'TRACE_NOT_FOUND'));
  }
});
test('network failure and reconnect do not replay POST; explicit retry can',async()=>{
  let count=0;const {c}=fixture({trace:async()=>{count++;throw new Error('结果未知，请刷新核对');}});
  c.enable(true);await tick();assert.equal(c.view.status,'unknown');c.update(state(),'reconnecting',settings);c.update(state(),'synced',settings);await tick();
  assert.equal(count,1);c.retry();await tick();assert.equal(count,2);c.stop();
});
test('known accepted task recovers from state after reconnect',async()=>{
  const {c,calls}=fixture();c.enable(true);await tick();c.update(state(),'reconnecting',settings);
  const s=state();s.trace_summary=[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}];c.update(s,'synced',settings);await tick();
  assert.equal(c.view.result.result_id,'r');assert.equal(calls.length,1);c.stop();
});
test('lost POST response recovers matching result from state without replay',async()=>{
  let count=0;const {c}=fixture({trace:async()=>{count++;throw new Error('unknown');}});
  c.enable(true);await tick();const s=state();s.trace_summary=[{...result(),operation_id:'op',status:'succeeded'}];
  c.update(s,'synced',settings);await tick();assert.equal(c.view.result.result_id,'r');assert.equal(count,1);c.stop();
});
test('bounded history eviction finishes waiting state without reissuing POST',async()=>{
  const {c,calls}=fixture();c.enable(true);await tick();const s=state();s.tasks.recent=[{operation_id:'op',status:'succeeded'}];
  c.update(s,'synced',settings);await tick();assert.equal(c.view.status,'failed');assert.equal(calls.length,1);c.stop();
});
test('ordinary version update recalculates once; model reads do not hide same result',async()=>{
  const {c,calls}=fixture();c.enable(true);await tick();const s=state();s.trace_summary=[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}];c.update(s,'synced',settings);await tick();
  s.tasks.pending=[{tool:'zemax_lookup_manual'}];assert.equal(matchesTrace(c.view.result,s,settings,'running'),true);
  c.update({...state(),model_revision:2},'synced',settings);await tick();assert.equal(calls.length,2);c.stop();
});
test('unmount aborts waiting reads and ignores late transport',async()=>{
  const pending=deferred();const {c}=fixture({trace:()=>pending.promise});c.enable(true);await tick();c.stop();pending.resolve({key:'key',operation_id:'op'});await tick();assert.equal(c.pending,undefined);
});
test('YZ/XZ local projection preserves propagation order, bounds and no YZ outline in XZ',()=>{
  const layout={status:'ready',surfaces:[{points:[[0,-2],[10,2]]}]};
  const r={rays:[{points:[{xyz:[3,4,10]},{xyz:[6,8,5]}]}]};
  const yz=projection(layout,r,'YZ'),xz=projection(layout,r,'XZ');
  assert.deepEqual(yz.rays[0].projected,[[10,4],[5,8]]);assert.deepEqual(xz.rays[0].projected,[[10,3],[5,6]]);assert.equal(xz.contours,null);
  assert.deepEqual(yz.bounds,[0,-2,10,8]);assert.deepEqual(xz.bounds,[5,3,10,6]);
  assert.equal(projection({status:'unsupported'},null,'YZ').bounds,null);
});
test('D2 rays project independently of strict unavailable outline; all failed has no invented path',()=>{
  const layout={status:'unsupported',surfaces:[]};const r={rays:[{points:[{xyz:[0,0,1]},{xyz:[0,2,3]}]}]};
  assert.deepEqual(projection(layout,r,'YZ').bounds,[1,0,3,2]);
  assert.equal(projection(layout,{rays:[{points:[]}]},'YZ').bounds,null);
});
test('trace POST 403 or broken transport is never retried; result GET can renew token',async()=>{
  const requests=[];
  const fetcher=async(path,options)=>{requests.push(path);return {status:path==='/api/trace'?403:200,ok:path!=='/api/trace',json:async()=>path==='/api/session'?{protocol_version:2,token:'t',service_instance_id:'i'}:{protocol_version:2,message:'denied'}};};
  const api=createWorkbenchApi(fetcher);await api.handshake();await assert.rejects(api.trace(settings,state()));assert.equal(requests.filter(p=>p==='/api/trace').length,1);
});
