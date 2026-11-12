import test from 'node:test';
import assert from 'node:assert/strict';
import { MILESTONE_STAGES, parseMilestones, milestonePane } from '../src/stage-milestones.js';

const clone = value => structuredClone(value);
const context = () => ({ project_name:'project-a', active_project:'project-a', requirements_revision:2,
  proposal_hash:'proposal-a', proposal_revision:1, authorization_revision:1 });
const nativeSource = (extra={}) => ({ service_instance_id:'service-a', model_id:'model-a', model_revision:3,
  active_project:'project-a', model_project:'project-a', configuration:1, source_state_version:8, ...extra });
const source = (extra={}) => ({ ...context(), ...nativeSource(), ...extra });
function operation(extra={}) {
  return { operation_id:'operation-a', tool:'zemax_run_optimization', queued_at:'2026-11-03T08:01:00+00:00',
    finished_at:'2026-11-03T08:02:00+00:00', status:'succeeded', result_status:'success', observed_source:nativeSource(), ...extra };
}
function attempt(extra={}) {
  return { id:'attempt-a', stage_id:'stage_1', task_id:'task-a', declared_status:'needs_review', status:'needs_review',
    stale_reasons:[], started_at:'2026-11-03T08:00:00Z', reported_at:'2026-11-03T08:03:00Z',
    source:source({model_revision:2,source_state_version:6}), end_source:source(), worker_id:'agent-a',
    operations:[operation()], note:'冻结厚度，显式记录本次曲率搜索范围。', ...extra };
}
const running = (extra={}) => attempt({declared_status:'running',status:'running',reported_at:null,end_source:null,operations:[],...extra});
const failed = (extra={}) => attempt({declared_status:'failed',status:'failed',operations:[],...extra});
function envelope(attempts=[]) {
  return { schema_version:1, storage:'project', service_instance_id:'service-a', project_name:'project-a',
    stages:MILESTONE_STAGES.map(stage => { const list=attempts.filter(item=>item.stage_id===stage.id);
      return {...stage,status:list[0]?.status ?? 'not_started',attempts:list}; }),
    limits:{max_attempts_per_task:16,max_operations_per_report:12,max_visible_attempts:32} };
}
function sync(extra={}) {
  const current=nativeSource(extra), state_version=current.source_state_version;
  const observed=Object.fromEntries(['service_instance_id','model_id','model_revision','active_project','model_project'].map(key=>[key,current[key]]));
  const exact={...observed,state_version};
  return { canCollaborate:true, phase:'synced', error:'', engine:{...observed,state_version,validity:'valid',context_valid:true,error:null,
    snapshot:{status:'success',schema_version:1,sync:clone(exact),model:{project:current.model_project,configuration:current.configuration}},
    snapshot_context:clone(exact)} };
}
const flow = (data=envelope([attempt()])) => ({error:'',view:{service_instance_id:'service-a',project_name:'project-a',
  context:context(),proposal_bound:true,proposal:{user_confirmed_to_simulate:true},optimization_milestones:data}});
const status = (f=flow(),s=sync()) => milestonePane(f,s).stages[0]?.status;

test('four empty stages stay not_started; missing data is never an implied plan',()=>{
  assert.equal(parseMilestones(envelope()).status,'ok');
  assert.deepEqual(milestonePane(flow(envelope()),sync()).stages.map(stage=>stage.status),Array(4).fill('not_started'));
  for(const missing of [null,undefined]) {
    assert.equal(parseMilestones(missing).status,'missing');
    const f=flow(); f.view.optimization_milestones=missing;
    assert.deepEqual(milestonePane(f,sync()),{status:'unavailable',stages:[]});
  }
});

test('explicit running, needs_review and failed records retain their distinct meanings',()=>{
  for(const record of [running(),attempt(),failed()]) {
    assert.equal(parseMilestones(envelope([record])).status,'ok');
    assert.equal(status(flow(envelope([record]))),record.declared_status);
  }
  const data=envelope([attempt(),failed({id:'b',stage_id:'stage_2'})]);
  assert.deepEqual(milestonePane(flow(data),sync()).stages.map(stage=>stage.status),['needs_review','failed','not_started','not_started']);
});

test('unknown schema, incomplete stage sets and invented passing states fail the whole panel',()=>{
  const invalids=[[],{...envelope(),schema_version:'1'},{...envelope(),schema_version:2},
    {...envelope(),storage:'session'},{...envelope(),service_instance_id:null},{...envelope(),stages:[]}];
  for(const mutate of [data=>data.stages.pop(),data=>data.stages.reverse(),data=>data.stages[0].label='已通过',
    data=>data.stages[0].status='passed',data=>data.stages[0].attempts[0].status='verified',
    data=>data.stages[0].attempts[0].declared_status='completed']) {
    const data=envelope([attempt()]); mutate(data); invalids.push(data);
  }
  for(const data of invalids) {
    assert.equal(parseMilestones(data).status,'invalid');
    assert.deepEqual(milestonePane(flow(data),sync()),{status:'unavailable',stages:[]});
  }
});

test('enforces bounded metadata and unique task plus attempt identity without requiring global attempt IDs',()=>{
  const shared=envelope([attempt(),attempt({task_id:'task-b',stage_id:'stage_2'})]);
  assert.equal(parseMilestones(shared).status,'ok');
  assert.equal(parseMilestones(envelope([attempt(),attempt({stage_id:'stage_2'})])).status,'invalid');
  assert.equal(parseMilestones({...envelope(),limits:{max_attempts_per_task:160,max_operations_per_report:12,max_visible_attempts:32}}).status,'invalid');
  assert.equal(parseMilestones(envelope(Array.from({length:16},(_,index)=>attempt({id:`a-${index}`})))).status,'ok');
  assert.equal(parseMilestones(envelope(Array.from({length:17},(_,index)=>attempt({id:`a-${index}`})))).status,'invalid');
  const many=Array.from({length:32},(_,index)=>attempt({id:`a-${index}`,task_id:`task-${index}`}));
  assert.equal(parseMilestones(envelope(many)).status,'ok');
  assert.equal(parseMilestones(envelope([...many,attempt({id:'extra',task_id:'extra'})])).status,'invalid');
});

test('needs_review requires successful observed operations and cannot accept rollback or raw errors',()=>{
  for(const operations of [[],[operation({status:'failed'})],[operation({result_status:'error'})],
    [operation({observed_source:null})],[operation({rolled_back:true})],[operation({view_error:'read failed'})],
    [operation({observed_source:nativeSource({model_id:'other'})})],[operation(),operation()]]) {
    assert.equal(parseMilestones(envelope([attempt({operations})])).status,'invalid');
  }
  assert.equal(parseMilestones(envelope([attempt({end_source:null})])).status,'invalid');
});

test('an Agent failure without native geometry remains metadata and becomes stale without an end observation',()=>{
  const record=failed({end_source:null});
  assert.equal(parseMilestones(envelope([record])).status,'ok');
  const projected=milestonePane(flow(envelope([record])),sync()).stages[0].attempts[0];
  assert.equal(projected.status,'stale'); assert.equal(projected.declared_status,'failed');
  assert.ok(projected.stale_reasons.includes('observation_unavailable'));
  assert.equal(parseMilestones(envelope([failed({operations:[operation({status:'failed',result_status:null,observed_source:null})]})])).status,'ok');
});

test('invalid provenance, dates, inconsistent states and oversized reports are unavailable',()=>{
  assert.equal(parseMilestones(envelope([attempt({note:'🔎'.repeat(2000)})])).status,'ok');
  for(const record of [attempt({source:source({model_revision:0})}),attempt({source:source({configuration:true})}),
    attempt({source:source({authorization_revision:-1})}),attempt({source:source({model_project:'other'})}),
    attempt({started_at:'not-a-date'}),attempt({reported_at:'2026-11-02T00:00:00Z'}),attempt({note:'x'.repeat(2001)}),
    attempt({operations:Array.from({length:13},(_,index)=>operation({operation_id:`op-${index}`}))}),
    attempt({status:'running'}),attempt({status:'stale',stale_reasons:[]}),running({reported_at:'2026-11-03T08:03:00Z'}),
    attempt({operations:[operation({queued_at:null})]})]) {
    assert.equal(parseMilestones(envelope([record])).status,'invalid');
  }
});

test('render guard hides old records immediately during disconnect, flow failure or lost capability',()=>{
  for(const phase of ['connecting','reconnecting','stopped']) {
    assert.deepEqual(milestonePane(flow(),{...sync(),phase}),{status:'offline',stages:[]});
  }
  assert.equal(milestonePane({...flow(),error:'flow disconnected'},sync()).status,'offline');
  assert.equal(milestonePane(flow(),{...sync(),canCollaborate:false}).status,'offline');
  assert.equal(milestonePane(flow(),{...sync(),phase:'unavailable',error:'network disconnected'}).status,'offline');
});

test('late old project or service response cannot be rendered under the current scope',()=>{
  const current=flow();
  assert.equal(milestonePane(current,sync({service_instance_id:'new-service'})).status,'offline');
  assert.equal(milestonePane(current,sync({active_project:'new-project',model_project:'new-project'})).status,'offline');
  for(const mutate of [view=>view.service_instance_id='other',view=>view.project_name='other',
    view=>view.context.active_project='other',view=>view.optimization_milestones.service_instance_id='other',
    view=>view.optimization_milestones.project_name='other']) {
    const late=clone(current); mutate(late.view);
    assert.equal(milestonePane(late,sync()).status,'offline');
    assert.deepEqual(milestonePane(late,sync()).stages,[]);
  }
});

test('running accepts ordinary revision advances while finalized source must match current revision',()=>{
  assert.equal(status(flow(envelope([running()])),sync({model_revision:7})),'running');
  assert.equal(status(flow(envelope([running()])),sync({model_revision:1})),'stale');
  const projected=milestonePane(flow(),sync({model_revision:4})).stages[0].attempts[0];
  assert.equal(projected.status,'stale');
  assert.ok(projected.stale_reasons.includes('model_revision_changed'));
  assert.equal(projected.declared_status,'needs_review');
});

test('ordinary view or state version changes do not invalidate an unchanged model revision',()=>{
  assert.equal(status(flow(),sync({source_state_version:999})),'needs_review');
  const f=flow(); f.view.context.selection_revision=999;
  assert.equal(status(f,sync()),'needs_review');
});

test('old service metadata remains visible only as stale history in a current envelope',()=>{
  const record=attempt({source:source({service_instance_id:'old-service'}),end_source:source({service_instance_id:'old-service'}),
    operations:[operation({observed_source:nativeSource({service_instance_id:'old-service'})})],status:'stale',stale_reasons:['service_instance_changed']});
  const projected=milestonePane(flow(envelope([record])),sync());
  assert.equal(projected.status,'ready'); assert.equal(projected.stages[0].status,'stale');
  assert.equal(projected.stages[0].attempts[0].source.service_instance_id,'old-service');
});

test('model/configuration changes synchronously mark records stale before a flow poll catches up',()=>{
  for(const [change,reason] of [[{model_id:'model-b'},'model_changed'],[{configuration:2},'configuration_changed']]) {
    const projected=milestonePane(flow(),sync(change)).stages[0].attempts[0];
    assert.equal(projected.status,'stale'); assert.ok(projected.stale_reasons.includes(reason));
  }
});

test('changed requirements, proposal and approval cannot retain a current-looking state',()=>{
  for(const [key,value,reason] of [['requirements_revision',3,'requirements_changed'],['proposal_hash','other','proposal_changed'],
    ['proposal_revision',2,'proposal_changed'],['authorization_revision',2,'authorization_changed']]) {
    const f=flow(); f.view.context[key]=value;
    assert.ok(milestonePane(f,sync()).stages[0].attempts[0].stale_reasons.includes(reason));
  }
  const f=flow(); f.view.proposal.user_confirmed_to_simulate=false;
  assert.ok(milestonePane(f,sync()).stages[0].attempts[0].stale_reasons.includes('approval_revoked'));
});

test('server stale classification is never revived by a matching local source',()=>{
  const record=attempt({status:'stale',stale_reasons:['claim_changed','lease_expired']});
  const result=milestonePane(flow(envelope([record])),sync()).stages[0].attempts[0];
  assert.equal(result.status,'stale'); assert.deepEqual(result.stale_reasons,record.stale_reasons);
});

test('missing or imprecise native observations cannot make a stage look current',()=>{
  for(const invalidate of [s=>s.engine.snapshot=null,s=>s.engine.validity='uncertain',s=>s.engine.context_valid=false,
    s=>s.engine.snapshot.sync.state_version=4,s=>s.engine.snapshot_context.model_revision=2,
    s=>s.engine.snapshot.model.project='other',s=>s.engine.snapshot.status='error']) {
    const s=sync(); invalidate(s);
    assert.ok(milestonePane(flow(),s).stages[0].attempts[0].stale_reasons.includes('observation_unavailable'));
  }
  const s=sync(); s.phase='unavailable'; s.error='native unavailable'; s.engine.error=s.error; s.engine.snapshot=null;
  assert.equal(milestonePane(flow(),s).status,'ready'); assert.equal(status(flow(),s),'stale');
});

test('projection preserves immutable records and latest/older ordering independently of stage number',()=>{
  const newest=failed({id:'new',reported_at:'2026-11-03T08:04:00Z'}),older=attempt({id:'old'});
  const f=flow(envelope([newest,older,running({id:'stage4',stage_id:'stage_4'})])),before=clone(f);
  const pane=milestonePane(f,sync({configuration:2}));
  assert.deepEqual(f,before);
  assert.deepEqual(pane.stages[0].attempts.map(item=>item.id),['new','old']);
  assert.equal(pane.stages[3].attempts[0].declared_status,'running');
  assert.equal(status(f,sync()),'failed');
});
