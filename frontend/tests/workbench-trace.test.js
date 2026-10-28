import test from 'node:test';
import assert from 'node:assert/strict';
import { TraceController, defaultTraceSettings, matchesTrace, projection, settingsId } from '../src/workbench-trace.js';
import { createWorkbenchApi } from '../src/workbench-api.js';

const settings = {fields:[1],wavelengths:[1],pupil_count:5};
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
test('late result cannot render after model change and enabling resets on new model/service',async()=>{
  const pending=deferred();const {c}=fixture({readTrace:()=>pending.promise});c.enable(true);await tick();
  const s=state();s.trace_summary=[{operation_id:'op',key:'key',status:'succeeded',result_id:'r'}];c.update(s,'synced',settings);
  c.update({...state(),model_id:'different'},'synced',settings);pending.resolve(result());await tick();
  assert.equal(c.view.enabled,false);assert.equal(c.view.result,null);c.stop();
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
