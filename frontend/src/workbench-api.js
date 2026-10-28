let token;

async function localFetch(path, options) {
  try { return await fetch(path, options); }
  catch { throw new Error('本地工程服务未连接，请启动工作台后重试'); }
}

async function request(path, options = {}, retry = true) {
  if (!token) {
    const response = await localFetch('/api/session', { cache: 'no-store' });
    if (!response.ok) throw new Error('请通过本地工程服务打开工作台');
    token = (await response.json()).token;
  }
  const response = await localFetch(path, { ...options, cache: 'no-store',
    headers: { ...options.headers, 'X-OptiForge-Token': token } });
  if (response.status === 403 && retry) { token = undefined; return request(path, options, false); }
  const result = await response.json();
  if (!response.ok || result.status !== 'success') throw new Error(result.message || '无法读取模型');
  if (result.schema_version !== 1 || !Array.isArray(result.model?.surfaces)) throw new Error('模型数据版本不兼容');
  return result;
}

export function readSnapshot() { return request('/api/state'); }

export function importModel(file) {
  if (!/\.(zmx|zos)$/i.test(file.name)) return Promise.reject(new Error('请选择 .zmx 或 .zos 文件'));
  if (file.size > 32 * 1024 * 1024) return Promise.reject(new Error('文件不能超过 32 MB'));
  return request('/api/model/import', { method: 'POST', body: file,
    headers: { 'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name) } });
}
