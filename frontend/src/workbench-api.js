import { validateHistoryList, validateHistoryEntry } from './workbench-history.js';
import { validateAnalysisHistory, validateAnalysisHistoryEntry } from './workbench-analysis-history.js';
import { decodeDesignTaskHistoryResponse, designTaskHistoryRequest, validateDesignTaskHistory } from './design-task-history.js';
import { milestoneArtifactRequest, validateMilestoneArtifact } from './stage-geometry-artifact.js';
import { qualityArtifactRequest, validateQualityArtifact } from './stage-quality-artifact.js';

const VERSION = 2;
const contextKeys = ['service_instance_id', 'state_version', 'model_id', 'active_project', 'model_project'];
export const contextOf = (value) => Object.fromEntries(contextKeys.map((key) => [key, value[key]]));

function check(condition, message = '本地工程服务协议不兼容，请更新并重启') {
  if (!condition) throw new Error(message);
}
function version(value) { check(value?.protocol_version === VERSION); return value; }
const counter = (value) => Number.isSafeInteger(value) && value >= 0;

export function validateState(value) {
  version(value);
  check(typeof value.service_instance_id === 'string' && counter(value.state_version) && counter(value.event_seq)
    && typeof value.context_valid === 'boolean'
    && counter(value.model_revision) && ['initializing', 'valid', 'uncertain', 'unavailable'].includes(value.validity)
    && Array.isArray(value.tasks?.pending) && Array.isArray(value.tasks?.recent));
  if (value.snapshot) {
    check(value.snapshot.schema_version === 1 && Array.isArray(value.snapshot.model?.surfaces)
      && value.snapshot_context?.service_instance_id === value.service_instance_id
      && value.snapshot_context.model_id === value.model_id && value.snapshot_context.state_version === value.state_version
      && value.snapshot_context.model_revision === value.model_revision);
  }
  check(value.validity !== 'valid' || Boolean(value.snapshot) && value.context_valid);
  return value;
}

export function createWorkbenchApi(fetcher = globalThis.fetch.bind(globalThis)) {
  let session;
  let handshakeGeneration = 0;
  async function request(path, { signal, read = false, decode, ...options } = {}, retry = true) {
    let response;
    try {
      response = await fetcher(path, { ...options, signal, cache: 'no-store',
        headers: { ...options.headers, ...(session ? { 'X-OptiForge-Token': session.token } : {}) } });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error(read ? '连接已断开，显示上次读取' : '结果未知，请刷新核对');
    }
    if (response.status === 403 && read && retry) {
      await handshake(signal);
      return request(path, { signal, read, decode, ...options }, false);
    }
    let result;
    try { result = decode && response.ok ? await decode(response) : await response.json(); }
    catch { throw Object.assign(new Error(read ? '无法读取工程服务响应' : '结果未知，请刷新核对'), { status: response.status }); }
    if (!response.ok) throw Object.assign(new Error(result.message || (read ? '工程服务暂不可用' : '结果未知，请刷新核对')),
      { status: response.status, code: result.code });
    if (signal?.aborted) throw signal.reason || new Error('Aborted');
    return version(result);
  }
  async function handshake(signal) {
    const generation = ++handshakeGeneration;
    const value = await request('/api/session', { signal, read: true }, false);
    check(typeof value.token === 'string' && typeof value.service_instance_id === 'string');
    if (generation === handshakeGeneration) session = value;
    return value;
  }
  return {
    handshake,
    async readMilestoneArtifact(scope, options, signal) {
      const payload = milestoneArtifactRequest(scope, options);
      const value = await request('/api/tool', { method: 'POST', read: true, signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, name: 'zemax_design_workflow',
          arguments: { action: 'milestone_artifact', project_name: payload.source.project_name, payload } }) });
      const validated = await validateMilestoneArtifact(value, payload.source);
      check(validated.artifact.id === payload.artifact_id, '阶段几何归档与请求 ID 不一致');
      if (signal?.aborted) throw signal.reason || new Error('Aborted');
      return value;
    },
    async readMilestoneQualityArtifact(scope, options, signal) {
      const payload = qualityArtifactRequest(scope, options);
      const value = await request('/api/tool', { method: 'POST', read: true, signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, name: 'zemax_design_workflow',
          arguments: { action: 'milestone_quality_artifact', project_name: payload.source.project_name, payload } }) });
      const validated = await validateQualityArtifact(value, payload.source);
      check(validated.artifact.id === payload.artifact_id, '阶段像质归档与请求 ID 不一致');
      if (signal?.aborted) throw signal.reason || new Error('Aborted');
      return value;
    },
    async readDesignTaskHistory(scope, options = {}, signal) {
      const history = designTaskHistoryRequest(scope, options);
      const value = await request('/api/tool', { method: 'POST', read: true, signal, decode: decodeDesignTaskHistoryResponse,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, name: 'zemax_list_design_tasks',
          arguments: { project_name: history.source.project_name, history } }) });
      return validateDesignTaskHistory(value, history.source, { limit: history.limit });
    },
    async readAnalysisHistory(scope, signal) {
      const query = new URLSearchParams({instance_id:scope.service_instance_id,project_name:scope.project_name});
      return validateAnalysisHistory(await request(`/api/analysis-history?${query}`,{read:true,signal}),scope);
    },
    async readAnalysisHistoryEntry(id, scope, signal) {
      const query = new URLSearchParams({instance_id:scope.service_instance_id,project_name:scope.project_name});
      const value = validateAnalysisHistoryEntry(await request(`/api/analysis-history/${encodeURIComponent(id)}?${query}`,{read:true,signal}),scope);
      check(value.entry.id===id,'分析记录与请求不一致');
      return value;
    },
    async readHistory(scope, signal) {
      const query = new URLSearchParams({instance_id:scope.service_instance_id,project_name:scope.project_name});
      return validateHistoryList(await request(`/api/history?${query}`,{read:true,signal}),scope);
    },
    async readHistoryEntry(id, scope, signal) {
      const query = new URLSearchParams({instance_id:scope.service_instance_id,project_name:scope.project_name});
      const value = validateHistoryEntry(await request(`/api/history/${encodeURIComponent(id)}?${query}`,{read:true,signal}),scope);
      check(value.entry.id===id,'历史条目与请求不一致');
      return value;
    },
    async workflow(action='view',project_name=null,payload={},expected_workflow=null,expectedInstance=null) {
      const state=await request('/api/state',{read:true});
      check(!expectedInstance || expectedInstance===state.service_instance_id,'服务实例已改变，请重新读取后核对');
      const value=await request('/api/tool',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({protocol_version:VERSION,name:'zemax_design_workflow',
          arguments:{action,project_name,payload,expected_workflow},expected_context:contextOf(state)})});
      const result=value.result;
      if(result?.status!=='success')throw Object.assign(new Error(result?.message || '流程操作未完成'),{code:result?.code});
      check(result.workflow_schema_version===1 && result.context && result.draft && result.tasks,'流程数据版本不兼容');
      check(result.service_instance_id===state.service_instance_id,'服务实例已改变，请重新读取后核对');
      return result;
    },
    async analysis(settings, context, signal) {
      const value = await request('/api/analysis', { method:'POST',signal,
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({protocol_version:VERSION,expected_context:contextOf(context),settings}) });
      check(value.status==='accepted' && typeof value.key==='string' && typeof value.operation_id==='string',value.message || '分析请求未接受');
      return value;
    },
    async readAnalysis(id, signal) {
      const value = await request(`/api/analyses/${encodeURIComponent(id)}`,{read:true,signal});
      check(value.analysis_schema_version===1 && value.source && value.items && value.settings
        && typeof value.result_id==='string' && typeof value.key==='string' && Array.isArray(value.verdicts),'分析结果格式不兼容');
      return value;
    },
    async trace(settings, context, signal) {
      const value = await request('/api/trace', { method: 'POST', signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, expected_context: contextOf(context), settings }) });
      check(value.status === 'accepted' && typeof value.operation_id === 'string' && typeof value.key === 'string', value.message || '追迹请求未接受');
      return value;
    },
    async readTrace(id, signal) {
      const value = await request(`/api/traces/${encodeURIComponent(id)}`, { read: true, signal });
      check(value.trace_schema_version === 1 && typeof value.settings_hash === 'string' && value.source
        && Array.isArray(value.rays) && value.rays.length <= 243 && value.units
        && value.rays.every((ray) => Array.isArray(ray.points) && ray.points.length <= 200
          && ray.points.every((p) => Number.isInteger(p.surface) && Array.isArray(p.xyz) && p.xyz.length === 3 && p.xyz.every(Number.isFinite))), '追迹结果格式不兼容');
      return value;
    },
    async readState(signal) { return validateState(await request('/api/state', { signal, read: true })); },
    async events(instance, after, signal) {
      const value = await request(`/api/events?instance_id=${encodeURIComponent(instance)}&after_seq=${after}&timeout_ms=20000`, { signal, read: true });
      check(typeof value.service_instance_id === 'string' && counter(value.next_seq) && typeof value.reset_required === 'boolean'
        && Array.isArray(value.events) && value.events.every((event) => counter(event.seq) && typeof event.kind === 'string'));
      return value;
    },
    async refresh(signal) {
      const value = await request('/api/refresh', { method: 'POST', read: true, signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ protocol_version: VERSION }) });
      check(value.status === 'accepted' && typeof value.operation_id === 'string', value.message || '刷新任务未接受');
      return value;
    },
    async importModel(file, context, signal) {
      check(/\.(zmx|zos)$/i.test(file.name), '请选择 .zmx 或 .zos 文件');
      check(file.size > 0 && file.size <= 32 * 1024 * 1024, '文件大小应在 0–32 MB 之间');
      const value = await request('/api/model/import', { method: 'POST', body: file, signal,
        headers: { 'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name),
          'X-OptiForge-Protocol': String(VERSION), 'X-Expected-Context': encodeURIComponent(JSON.stringify(contextOf(context))) } });
      check('result' in value && value.context && 'operation_id' in value);
      if (value.state) validateState(value.state);
      return value;
    },
    async uiRegister(instanceId, uiSessionId, page, signal) {
      const value = await request('/api/ui/session', { method: 'POST', signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, service_instance_id: instanceId,
          ui_session_id: uiSessionId, page }) });
      check(value.online === true && value.ui_session_id === uiSessionId && value.service_instance_id === instanceId, '前端会话注册失败');
      return value;
    },
    async uiHeartbeat(instanceId, uiSessionId, page, signal) {
      const value = await request('/api/ui/heartbeat', { method: 'POST', signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, service_instance_id: instanceId,
          ui_session_id: uiSessionId, page }) });
      check(value.protocol_version === VERSION && value.ui_session_id === uiSessionId && value.service_instance_id === instanceId, '前端心跳响应不兼容');
      return value;
    },
    async uiAck(instanceId, uiSessionId, commandId, status, actualPage, reason = null, signal, actualFocus = null) {
      const value = await request('/api/ui/ack', { method: 'POST', signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ protocol_version: VERSION, service_instance_id: instanceId,
          ui_session_id: uiSessionId, command_id: commandId, status, actual_page: actualPage, reason,
          ...(actualFocus ? { actual_focus: actualFocus } : {}) }) });
      check(value.receipt?.command_id === commandId && value.receipt?.status === status
        && value.service_instance_id === instanceId, '前端确认未被工作台接受');
      return value;
    },
  };
}
