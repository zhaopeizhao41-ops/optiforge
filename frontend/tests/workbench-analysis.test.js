import test from 'node:test';
import assert from 'node:assert/strict';
import { AnalysisController,defaultAnalysisSettings,matchesAnalysis,analysisIsCurrent,analysisBlocked,formatAxisTick } from '../src/workbench-analysis.js';
import { createWorkbenchApi } from '../src/workbench-api.js';
import { modelBusy } from '../src/workbench-trace.js';

const tick=()=>new Promise(resolve=>setTimeout(resolve,0));

test('axis labels compact extreme magnitudes and negative zero without losing a small-scale axis',()=>{
  assert.equal(formatAxisTick(9.8835e-10,.67),'0');
  assert.equal(formatAxisTick(-9.8835e-10,.67),'0');
  assert.equal(formatAxisTick(-0,1),'0');
  assert.equal(formatAxisTick(9.8835e-10,1e-10),'9.88e-10');
  assert.equal(formatAxisTick(-1.23456e-120,1e-121),'-1.23e-120');
  assert.equal(formatAxisTick(1.23456e120,1e119),'1.23e120');
  assert.equal(formatAxisTick(Number.MIN_VALUE,Number.MIN_VALUE),'4.94e-324');
  assert.equal(formatAxisTick(-Number.MAX_VALUE,1e307),'-1.8e308');
  assert.equal(formatAxisTick(1.23456,.5),'1.23');
  assert.equal(formatAxisTick(-.000999,.0001),'-9.99e-4');
  for(const value of [null,undefined,NaN,Infinity,-Infinity])assert.equal(formatAxisTick(value,1),'—');
});
function state(extra={}) {
  return {protocol_version:2,service_instance_id:'instance-a',model_id:'model-a',model_revision:1,state_version:1,
    active_project:'a',model_project:'a',proposal_confirmed:true,validity:'valid',context_valid:true,
    snapshot:{model:{configuration:1,fields:Array.from({length:7},(_,i)=>({index:i+1,x:0,y:i})),
      wavelengths:[{index:1,is_primary:true,wavelength_um:.6328}]}},tasks:{pending:[],recent:[]},analysis_summary:[],...extra};
}
const source=s=>Object.fromEntries(['service_instance_id','model_id','model_revision','active_project','model_project'].map(k=>[k,s[k]]).concat([['configuration',s.snapshot.model.configuration]]));
const result=s=>({analysis_schema_version:1,result_id:'result-a',key:'key-a',source:source(s),status:'success',items:{},settings:defaultAnalysisSettings(s.snapshot.model),verdicts:[]});
function setup() {
  let posts=0,gets=0;
  const s=state();
  const api={analysis:async()=>{posts++;return {operation_id:'operation-a',key:'key-a'};},readAnalysis:async()=>{gets++;return result(s);}};
  const controller=new AnalysisController(api);controller.update(s,'synced',true);
  return {controller,s,api,counts:()=>({posts,gets})};
}
function completed(s,extra={}) {
  return state({...s,analysis_summary:[{operation_id:'operation-a',key:'key-a',result_id:'result-a',status:'succeeded',source:source(s),...extra}]});
}

test('default actual fields span magnitude and primary wavelengths remain actual',()=>{
  const s=defaultAnalysisSettings(state().snapshot.model);
  assert.deepEqual(s.fields,[1,3,4,6,7]);assert.equal(s.analyses.length,5);
});
test('reading state and display-only updates submit zero native tasks',async()=>{
  const {controller:c,s,counts}=setup();
  for(let i=0;i<20;i++) c.update({...s,event_seq:i},'synced',true);
  await tick();assert.deepEqual(counts(),{posts:0,gets:0});c.stop();
});
test('explicit run posts once and repeated clicks merge locally',async()=>{
  const {controller:c,s,counts}=setup();
  const settings=defaultAnalysisSettings(s.snapshot.model);
  await Promise.all([c.run(settings),c.run(settings)]);
  assert.equal(counts().posts,1);
  c.update(completed(s),'synced',true);await tick();
  assert.equal(c.view.status,'ready');assert.equal(counts().gets,1);
  for(let i=0;i<10;i++)c.update(completed(s),'synced',true);
  await tick();assert.deepEqual(counts(),{posts:1,gets:1});c.stop();
});
test('Agent published result is adopted through GET only',async()=>{
  const {controller:c,s,counts}=setup();c.update(completed(s),'synced',true);await tick();
  assert.deepEqual(counts(),{posts:0,gets:1});assert.ok(c.view.result);c.stop();
});
test('model revision or authorization change makes cached results historical, no automatic rerun',async()=>{
  const {controller:c,s,counts}=setup();c.update(completed(s),'synced',true);await tick();
  const changed=state({...s,model_revision:2});c.update(changed,'synced',true);
  assert.ok(c.view.result);assert.equal(analysisIsCurrent(c.view.result,changed,'synced'),false);
  assert.equal(analysisIsCurrent(c.view.result,state({...s,proposal_confirmed:false}),'synced'),false);
  assert.equal(analysisIsCurrent(c.view.result,s,'reconnecting'),false);
  assert.equal(counts().posts,0);c.stop();
});
test('same filename in another model and restart clear cards; late GET cannot overwrite',async()=>{
  for(const change of [{model_id:'other'},{service_instance_id:'restarted'},{model_project:'b',active_project:'b',model_id:'b'}]) {
    const {controller:c,s,api}=setup();let resolve;
    api.readAnalysis=()=>new Promise(r=>resolve=r);
    c.update(completed(s),'synced',true);
    c.update(state({...s,...change}),'synced',true);resolve(result(s));await tick();
    assert.equal(c.view.result,null);c.stop();
  }
});
test('unknown POST is never replayed; published event can recover result',async()=>{
  const {controller:c,s,api}=setup();let posts=0;
  api.analysis=async()=>{posts++;throw new Error('unknown');};
  await c.run(defaultAnalysisSettings(s.snapshot.model));assert.equal(c.view.status,'unknown');
  for(let i=0;i<10;i++)c.update(s,'synced',true);
  assert.equal(posts,1);c.update(completed(s),'synced',true);await tick();assert.equal(c.view.status,'ready');c.stop();
});
test('evicted or partial results show explicit states',async()=>{
  const {controller:c,s}=setup();c.update(completed(s,{status:'evicted',result_id:null}),'synced',true);
  assert.equal(c.view.status,'failed');assert.match(c.view.error,/淘汰/);c.stop();
  const other=setup();other.api.readAnalysis=async()=>({...result(other.s),status:'partial'});
  other.controller.update(completed(other.s,{status:'partial'}),'synced',true);await tick();
  assert.equal(other.controller.view.status,'partial');other.controller.stop();
});
test('transient result GET retries are bounded, not native POST retries',async()=>{
  const {controller:c,s,api}=setup();let gets=0;
  api.readAnalysis=async()=>{gets++;throw new Error('offline');};
  c.update(completed(s),'synced',true);await tick();
  assert.equal(gets,2);assert.equal(c.view.status,'failed');c.update(completed(s),'synced',true);await tick();assert.equal(gets,2);c.stop();
});
test('missing capability blocks before submitting and analysis tasks do not dirty optical model',()=>{
  assert.match(analysisBlocked(state(),'synced',false),/新版/);
  assert.equal(modelBusy(state({tasks:{pending:[{tool:'__analysis__'}],recent:[]}})),false);
  assert.equal(modelBusy(state({tasks:{pending:[{tool:'zemax_run_analysis_suite'}],recent:[]}})),false);
  assert.equal(matchesAnalysis(result(state()),state({active_project:'b',model_project:'b'})),false);
});
test('unmount abort and late POST/GET settle without publishing or retrying',async()=>{
  const {controller:c,s,api}=setup();let resolve;
  api.analysis=()=>new Promise(r=>resolve=r);
  const running=c.run(defaultAnalysisSettings(s.snapshot.model));c.stop();resolve({operation_id:'o',key:'k'});await running;
  assert.equal(c.pending,undefined);
});
test('transport rejects wrong result schema and POST errors are never replayed',async()=>{
  const calls=[];
  const api=createWorkbenchApi(async(path,options)=>{
    calls.push([path,options]);return {ok:true,status:200,json:async()=>({protocol_version:2,analysis_schema_version:0})};
  });
  await assert.rejects(api.readAnalysis('a'),/不兼容/);
  await assert.rejects(api.analysis({},state()),/未接受/);
  assert.equal(calls.length,2);
});

test('switching model during POST allows a new explicit run without waiting for old POST',async()=>{
  const {controller:c,s,api}=setup();let resolve,posts=0;
  api.analysis=()=>{posts++;return posts===1?new Promise(r=>resolve=r):Promise.resolve({operation_id:'new',key:'new'});};
  const old=c.run(defaultAnalysisSettings(s.snapshot.model));
  c.update(state({...s,model_id:'new'}),'synced',true);
  await c.run(defaultAnalysisSettings(s.snapshot.model));assert.equal(posts,2);
  resolve({operation_id:'old',key:'old'});await old;
  assert.equal(c.pending.operation_id,'new');c.stop();
});

test('late result from older revision cannot overwrite a newer completed result',async()=>{
  const {controller:c,s,api}=setup();const deferred={};
  api.readAnalysis=id=>new Promise(resolve=>deferred[id]=resolve);
  c.update(completed(s),'synced',true);
  const next=state({...s,model_revision:2});
  c.update(completed(next,{operation_id:'new',result_id:'new',key:'new'}),'synced',true);
  deferred.new({...result(next),result_id:'new',key:'new'});await tick();
  deferred['result-a'](result(s));await tick();
  assert.equal(c.view.result.result_id,'new');c.stop();
});
