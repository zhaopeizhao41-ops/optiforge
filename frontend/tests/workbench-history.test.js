import test from 'node:test';
import assert from 'node:assert/strict';
import { HistoryController, historyScope, historyComparisonReason, validateHistoryList, validateHistoryEntry } from '../src/workbench-history.js';
import { createWorkbenchApi } from '../src/workbench-api.js';

const tick = () => new Promise(resolve=>setTimeout(resolve,0));
const deferred = () => { let resolve,reject; const promise=new Promise((r,j)=>{resolve=r;reject=j;}); return {promise,resolve,reject}; };
const state = (extra={}) => ({service_instance_id:'service-a',active_project:'project-a',state_version:5,history_revision:1,...extra});
const scope = historyScope(state());
function entry(id='entry-a',extra={}) {
  return {id,operation_id:`operation-${id}`,recorded_at:'2026-11-03T10:00:00Z',tool:'zemax_run_optimization',label:'优化模型',
    source:{service_instance_id:'service-a',active_project:'project-a',model_project:'project-a',model_id:'model-a',
      model_revision:2,configuration:1,source_state_version:5},...extra};
}
const envelope = () => ({protocol_version:2,service_instance_id:'service-a',history_schema_version:1,storage:'session',project_name:'project-a'});
const list = (entries=[entry()],extra={}) => ({...envelope(),history_revision:1,entries,recording:{status:'recorded',code:null},...extra});
function detail(item=entry()) {
  return {...envelope(),entry:item,snapshot:{schema_version:1,model:{surfaces:[],configuration:item.source.configuration},layout:{units:'mm'},
    sync:{...item.source,state_version:item.source.source_state_version}}};
}
function setup(entries=[entry()]) {
  const calls=[];
  const api={readHistory:async(s,signal)=>{calls.push(['list',s,signal]);return list(entries);},
    readHistoryEntry:async(id,s,signal)=>{calls.push(['detail',id,s,signal]);return detail(entries.find(item=>item.id===id));}};
  const c=new HistoryController(api);
  return {c,api,calls};
}

test('history envelope/source validation rejects foreign scope, unknown version and unsafe revisions',()=>{
  assert.equal(validateHistoryList(list(),scope).entries.length,1);
  for(const bad of [list(undefined,{history_schema_version:2}),list(undefined,{storage:'disk'}),
    list(undefined,{service_instance_id:'other'}),list(undefined,{project_name:'other'}),list([entry(),entry()])]) {
    assert.throws(()=>validateHistoryList(bad,scope));
  }
  const invalid=entry();invalid.source.configuration=0;
  assert.throws(()=>validateHistoryList(list([invalid]),scope));
});

test('detail geometry identity must match both immutable summary and snapshot sync',()=>{
  const expected=entry();assert.equal(validateHistoryEntry(detail(),scope,expected).entry.id,expected.id);
  const wrong=detail();wrong.snapshot.sync.model_revision=1;
  assert.throws(()=>validateHistoryEntry(wrong,scope,expected),/来源/);
  const other=detail(entry('other'));
  assert.throws(()=>validateHistoryEntry(other,scope,expected),/条目/);
  const config=detail();config.snapshot.model.configuration=2;
  assert.throws(()=>validateHistoryEntry(config,scope,expected),/来源/);
});

test('comparison permits two different revisions only in the same model and configuration',()=>{
  const a=entry('a'),b=entry('b');b.source.model_revision=3;
  assert.equal(historyComparisonReason(a,b),'');
  assert.match(historyComparisonReason(a,a),/另一条/);
  for(const [key,value,reason] of [['service_instance_id','other',/服务/],['model_project','other',/工程/],
    ['model_id','other',/模型/],['configuration',2,/配置/]]) {
    const changed=entry('other');changed.source[key]=value;
    assert.match(historyComparisonReason(a,changed),reason);
  }
});

test('initial list/detail are GET-only adoption and ordinary event updates do not refetch',async()=>{
  const {c,calls}=setup();c.update(state(),'synced');await tick();
  assert.equal(c.view.left.entry.id,'entry-a');
  assert.deepEqual(calls.map(call=>call[0]),['list','detail']);
  for(let seq=1;seq<50;seq++)c.update(state({event_seq:seq}),'synced');
  await tick();assert.equal(calls.length,2);c.stop();
});

test('new history revision refreshes summaries while current optical revision can change independently',async()=>{
  const {c,calls}=setup();c.update(state(),'synced');await tick();
  c.update(state({state_version:6,model_revision:10,history_revision:2}),'synced');await tick();
  assert.equal(calls.filter(call=>call[0]==='list').length,2);
  assert.equal(c.view.left.entry.id,'entry-a');c.stop();
});

test('same filename or old model never bypasses a project/service scope change',async()=>{
  for(const change of [{active_project:'project-b'},{service_instance_id:'service-b'}]) {
    const {c,api}=setup(),pending=deferred();api.readHistoryEntry=()=>pending.promise;
    c.update(state(),'synced');await tick();
    api.readHistory=async()=>new Promise(()=>{});
    c.update(state(change),'synced');
    assert.equal(c.view.left,null);assert.deepEqual(c.view.entries,[]);
    pending.resolve(detail());await tick();assert.equal(c.view.left,null);c.stop();
  }
});

test('late list response cannot restore another project after rebinding',async()=>{
  const {c,api}=setup(),pending=deferred();api.readHistory=()=>pending.promise;
  c.update(state(),'synced');api.readHistory=()=>new Promise(()=>{});
  c.update(state({active_project:'project-b'}),'synced');
  pending.resolve(list());await tick();assert.deepEqual(c.view.entries,[]);c.stop();
});

test('rapid snapshot selection rejects late detail even when transport ignores abort',async()=>{
  const entries=[entry('a'),entry('b')],{c,api}=setup(entries),a=deferred(),b=deferred();
  api.readHistoryEntry=id=>id==='a'?a.promise:b.promise;
  c.update(state(),'synced');await tick();c.select('b');b.resolve(detail(entries[1]));await tick();
  a.resolve(detail(entries[0]));await tick();
  assert.equal(c.view.selected,'b');assert.equal(c.view.left.entry.id,'b');c.stop();
});

test('older refresh cannot overwrite newest authoritative list',async()=>{
  const {c,api}=setup(),first=deferred(),second=deferred();let n=0;
  api.readHistory=()=>++n===1?first.promise:second.promise;
  c.update(state(),'synced');c.refresh();
  second.resolve(list([]));await tick();first.resolve(list());await tick();
  assert.deepEqual(c.view.entries,[]);assert.equal(c.view.left,null);c.stop();
});

test('eviction hides selected geometry and late detail immediately',async()=>{
  const {c,api}=setup();c.update(state(),'synced');await tick();assert.ok(c.view.left);
  api.readHistory=async()=>list([]);await c.refresh();await tick();
  assert.equal(c.view.left,null);assert.equal(c.view.selected,null);assert.deepEqual(c.view.entries,[]);c.stop();
});

test('detail 404 clears old geometry and exposes eviction without replay or optical calls',async()=>{
  const {c,api,calls}=setup();c.update(state(),'synced');await tick();
  api.readHistoryEntry=async()=>{throw Object.assign(new Error('missing'),{status:404});};
  c.select('entry-a');await tick();
  assert.equal(c.view.left,null);assert.equal(c.view.leftStatus,'failed');assert.match(c.view.leftError,/淘汰/);
  assert.equal(calls.filter(call=>call[0]==='list').length,1);c.stop();
});

test('disconnect clears visible snapshots and cancelled reads cannot publish',async()=>{
  const {c,api}=setup();c.update(state(),'synced');await tick();assert.ok(c.view.left);
  const pending=deferred();api.readHistoryEntry=()=>pending.promise;c.select('entry-a');
  c.update(state(),'reconnecting','offline');assert.equal(c.view.left,null);assert.deepEqual(c.view.entries,[]);
  pending.resolve(detail());await tick();assert.equal(c.view.status,'offline');assert.equal(c.view.left,null);c.stop();
});

test('list read failure clears cached history and explicit refresh can recover',async()=>{
  const {c,api}=setup();c.update(state(),'synced');await tick();
  api.readHistory=async()=>{throw new Error('offline');};await c.refresh();
  assert.equal(c.view.left,null);assert.deepEqual(c.view.entries,[]);assert.equal(c.view.status,'failed');
  api.readHistory=async()=>list();await c.refresh();await tick();assert.ok(c.view.left);c.stop();
});

test('compare slot rejects incompatible entry and selection clears incompatible prior comparison',async()=>{
  const a=entry('a'),b=entry('b'),other=entry('other');other.source.model_id='other-model';
  const {c,calls}=setup([a,b,other]);c.update(state(),'synced');await tick();
  c.compare('other');assert.equal(c.view.compare,null);
  c.compare('b');await tick();assert.equal(c.view.right.entry.id,'b');
  c.select('other');assert.equal(c.view.compare,null);assert.equal(c.view.right,null);
  await tick();assert.equal(calls.filter(call=>call[0]==='detail'&&call[1]==='b').length,1);c.stop();
});

test('unmount aborts in-flight request and blocks subsequent result emission',async()=>{
  const {c,api}=setup(),pending=deferred();let signal;
  api.readHistory=(_scope,s)=>{signal=s;return pending.promise;};
  c.update(state(),'synced');c.stop();assert.equal(signal.aborted,true);
  pending.resolve(list());await tick();assert.deepEqual(c.view.entries,[]);
});

test('history transport binds both query fields and uses only GET; foreign schema/detail ID rejected',async()=>{
  const calls=[];let response=list();
  const api=createWorkbenchApi(async(path,options)=>{calls.push([path,options]);return {ok:true,status:200,json:async()=>response};});
  await api.readHistory(scope);
  assert.equal(calls[0][0],'/api/history?instance_id=service-a&project_name=project-a');
  response=detail();await api.readHistoryEntry('entry-a',scope);
  assert.equal(calls[1][0],'/api/history/entry-a?instance_id=service-a&project_name=project-a');
  assert.ok(calls.every(([,options])=>!options.method && !options.body && options.cache==='no-store'));
  response=list(undefined,{history_schema_version:2});await assert.rejects(api.readHistory(scope),/版本/);
  response=detail(entry('foreign'));await assert.rejects(api.readHistoryEntry('entry-a',scope),/请求/);
});
