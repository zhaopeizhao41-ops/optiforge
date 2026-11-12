import test from 'node:test';
import assert from 'node:assert/strict';
import { AUTO_COMMAND_PREFIX, isAutoCommand } from '../src/workbench-ui.js';

test('only auto-prefixed commands are treated as follow-the-Agent jumps', () => {
  assert.equal(isAutoCommand({ request_id: `${AUTO_COMMAND_PREFIX}1a2b3c4d` }), true);
  assert.equal(isAutoCommand({ request_id: 'agent-request-01' }), false);
  for (const value of [null, {}, { request_id: 7 }]) assert.equal(isAutoCommand(value), false);
});
