import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorkbenchApi } from '../src/workbench-api.js';
import { commandDeadline, commandExpired, commandReceipt, isUiPage, newUiSessionId,
  normalizeUiPage, pageAllowed, validCommand, UI_PAGES } from '../src/workbench-ui.js';

const response = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });

test('UI page whitelist and session ids are bounded', () => {
  assert.deepEqual([...UI_PAGES], ['workflow', 'design', 'analysis']);
  assert.equal(isUiPage('analysis'), true);
  assert.equal(isUiPage('settings'), false);
  assert.equal(normalizeUiPage('workflow'), 'workflow');
  assert.equal(normalizeUiPage('settings'), 'design');
  const first = newUiSessionId();
  const second = newUiSessionId();
  assert.match(first, /^[0-9a-f]{32}$/);
  assert.notEqual(first, second);
});

test('command validation distinguishes accepted, rejected and expired receipts', () => {
  assert.equal(validCommand({ command_id: 'c1', page: 'analysis', request_id: 'r1' }), true);
  assert.equal(validCommand({ command_id: 'c1', page: 'settings', request_id: 'r1' }), false);
  assert.equal(validCommand({ command_id: '', page: 'analysis', request_id: 'r1' }), false);
  assert.deepEqual(commandReceipt({ command_id: 'c1', page: 'design', request_id: 'r1' }),
    { command_id: 'c1', page: 'design', request_id: 'r1' });
  assert.equal(commandReceipt({ command_id: 'c1', page: 'settings' }), null);
});

test('deadline is conservative and expired commands are rejected', () => {
  const command = { command_id: 'c1', page: 'analysis', request_id: 'r1', remaining_ms: 1200 };
  const start = 100;
  assert.equal(commandDeadline(command, start), 1300);
  assert.equal(commandExpired(1300, 1299), false);
  assert.equal(commandExpired(1300, 1301), true);
  assert.equal(commandDeadline({ remaining_ms: -5 }, 100), 100);
  assert.equal(commandExpired(NaN, 1), true);
});

test('workflow page is gated; analysis page stays addressable', () => {
  assert.equal(pageAllowed('design', { canWorkflow: false, canAnalyze: false }), true);
  assert.equal(pageAllowed('workflow', { canWorkflow: false, canAnalyze: true }), false);
  assert.equal(pageAllowed('workflow', { canWorkflow: true }), true);
  assert.equal(pageAllowed('analysis', { canWorkflow: true, canAnalyze: false }), true);
  assert.equal(pageAllowed('analysis', { canAnalyze: true }), true);
});

test('workbench UI API uses the authenticated loopback endpoints and payloads', async () => {
  const calls = [];
  const fetcher = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === '/api/session') {
      return response({ protocol_version: 2, token: 'token-1', service_instance_id: 'instance-1' });
    }
    if (path === '/api/ui/session') {
      return response({ protocol_version: 2, service_instance_id: 'instance-1',
        ui_session_id: 'session-1', page: 'design', online: true, lease_ms: 10000 });
    }
    if (path === '/api/ui/heartbeat') {
      return response({ protocol_version: 2, service_instance_id: 'instance-1',
        ui_session_id: 'session-1', page: 'design',
        command: { command_id: 'cmd-1', page: 'analysis', request_id: 'request-1', remaining_ms: 8000 } });
    }
    if (path === '/api/ui/ack') {
      return response({ protocol_version: 2, service_instance_id: 'instance-1',
        receipt: { command_id: 'cmd-1', status: 'applied', page: 'analysis', actual_page: 'analysis' } });
    }
    throw new Error(`unexpected ${path}`);
  };
  const api = createWorkbenchApi(fetcher);
  await api.handshake();
  await api.uiRegister('instance-1', 'session-1', 'design');
  const heartbeat = await api.uiHeartbeat('instance-1', 'session-1', 'design');
  assert.equal(heartbeat.command.command_id, 'cmd-1');
  assert.equal(validCommand(heartbeat.command), true);
  await api.uiAck('instance-1', 'session-1', 'cmd-1', 'applied', 'analysis');
  const posts = calls.filter((call) => call.path.startsWith('/api/ui/'));
  assert.deepEqual(posts.map((call) => call.path), ['/api/ui/session', '/api/ui/heartbeat', '/api/ui/ack']);
  assert.deepEqual(JSON.parse(posts[0].options.body), {
    protocol_version: 2, service_instance_id: 'instance-1', ui_session_id: 'session-1', page: 'design',
  });
  assert.deepEqual(JSON.parse(posts[2].options.body), {
    protocol_version: 2, service_instance_id: 'instance-1', ui_session_id: 'session-1',
    command_id: 'cmd-1', status: 'applied', actual_page: 'analysis', reason: null,
  });
  for (const call of posts) assert.equal(call.options.headers['X-OptiForge-Token'], 'token-1');
});

test('ACK is rejected when the service did not accept the command', async () => {
  const fetcher = async (path) => {
    if (path === '/api/session') {
      return response({ protocol_version: 2, token: 'token-1', service_instance_id: 'instance-1' });
    }
    return response({ protocol_version: 2, service_instance_id: 'instance-1',
      receipt: { command_id: 'other', status: 'applied' } });
  };
  const api = createWorkbenchApi(fetcher);
  await api.handshake();
  await assert.rejects(api.uiAck('instance-1', 'session-1', 'cmd-1', 'applied', 'analysis'),
    /确认未被工作台接受/);
});

test('UI responses from another service instance are rejected', async () => {
  const fetcher = async (path) => {
    if (path === '/api/session') {
      return response({ protocol_version: 2, token: 'token-1', service_instance_id: 'instance-1' });
    }
    return response({ protocol_version: 2, service_instance_id: 'instance-2',
      receipt: { command_id: 'cmd-1', status: 'applied' } });
  };
  const api = createWorkbenchApi(fetcher);
  await api.handshake();
  await assert.rejects(api.uiAck('instance-1', 'session-1', 'cmd-1', 'applied', 'analysis'),
    /确认未被工作台接受/);
});

test('helper gates used by the hook: receive -> deadline -> page gate -> receipt', () => {
  const command = { command_id: 'cmd-1', page: 'analysis', request_id: 'r1', remaining_ms: 100 };
  const start = 50;
  const deadline = commandDeadline(command, start);
  assert.equal(commandExpired(deadline, 100), false);
  assert.equal(pageAllowed(command.page, { canAnalyze: true }), true);
  // After commit, ACK is sent with the same command/session/instance and actual page.
  const receipt = commandReceipt(command);
  assert.deepEqual(receipt, { command_id: 'cmd-1', page: 'analysis', request_id: 'r1' });
  assert.equal(commandExpired(deadline, 151), true);
});
