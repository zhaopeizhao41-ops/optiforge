import test from 'node:test';
import assert from 'node:assert/strict';
import { DesignWorkflowClient, fieldsFor, isDraftDirty, reconcileDraft } from '../src/design-workflow.js';
import { beforeUnload, createUnsavedChangesGuard, guardNeeded } from '../src/unsaved-changes.js';

function fakeTarget() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      listeners.get(type)?.delete(handler);
    },
    count(type) { return listeners.get(type)?.size ?? 0; },
  };
}

const initialView = () => ({
  project_name: 'p', service_instance_id: 's', context: { requirements_revision: 1 },
  draft: { revision: 1, system_type: 'imaging_lens', specs: { efl_mm: 50 },
    sources: { efl_mm: 'user' }, user_prompt: '', constraints: '' },
});
const nextView = (before, fields) => ({
  ...before,
  context: { ...before.context, requirements_revision: before.draft.revision + 1 },
  draft: { ...fields, revision: before.draft.revision + 1 },
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};

/** Uses the real client + reconcileDraft + the actual registered guard handler. */
async function harness() {
  const target = fakeTarget();
  const guard = createUnsavedChangesGuard(target);
  const save = deferred();
  let flow = {};
  let editor = { base: null, draft: null };
  let saving = false;
  const api = {
    async workflow(action) {
      if (action === 'view') return initialView();
      if (action === 'save') return save.promise;
      throw new Error(`unexpected action ${action}`);
    },
  };
  const client = new DesignWorkflowClient(api, (value) => {
    flow = { ...flow, ...value };
    if (flow.view) editor = reconcileDraft(editor.base, editor.draft, flow.view, flow.saved);
    const dirty = isDraftDirty(editor.base, editor.draft);
    guard.update(guardNeeded({ dirty, saving }));
  });
  await client.refresh();
  const dirty = () => isDraftDirty(editor.base, editor.draft);
  const edit = (draft) => {
    editor = { ...editor, draft };
    const dirtyNow = isDraftDirty(editor.base, editor.draft);
    guard.update(guardNeeded({ dirty: dirtyNow, saving }));
  };
  return {
    target, guard, client, save,
    start() { return client.refresh(); },
    get dirty() { return dirty(); },
    edit,
    discard(view) {
      editor = reconcileDraft(null, null, view);
      guard.update(guardNeeded({ dirty: dirty(), saving }));
    },
    beginSave() {
      saving = true;
      const promise = client.act('save', editor.draft);
      guard.update(guardNeeded({ dirty: dirty(), saving }));
      return promise;
    },
    finishSave(fields) {
      save.resolve(nextView(initialView(), fields));
    },
    failSave() { save.reject(new Error('save failed')); },
    settle() { saving = false; guard.update(guardNeeded({ dirty: dirty(), saving })); },
  };
}

test('initial clean draft registers no beforeunload listener', () => {
  const target = fakeTarget();
  const guard = createUnsavedChangesGuard(target);
  guard.update(guardNeeded({ dirty: false, saving: false }));
  assert.equal(target.count('beforeunload'), 0);
  assert.equal(guard.armed, false);
});

test('dirty and saving both arm exactly one beforeunload listener', () => {
  const target = fakeTarget();
  const guard = createUnsavedChangesGuard(target);
  assert.equal(guardNeeded({ dirty: true }), true);
  assert.equal(guardNeeded({ dirty: false, saving: true }), true);
  guard.update(true);
  guard.update(true);
  assert.equal(target.count('beforeunload'), 1);
  assert.equal(guard.armed, true);
});

test('the registered beforeunload handler requests browser confirmation', () => {
  const target = fakeTarget();
  const guard = createUnsavedChangesGuard(target);
  guard.update(true);
  const [handler] = target.listeners.get('beforeunload');
  const event = { prevented: false, returnValue: 'untouched',
    preventDefault() { this.prevented = true; } };
  assert.equal(handler(event), '');
  assert.equal(event.prevented, true);
  assert.equal(event.returnValue, '');
});

test('real successful save receipt clears and later edits re-arm', async () => {
  const h = await harness();
  await h.start();
  assert.equal(h.target.count('beforeunload'), 0);
  h.edit({ ...fieldsFor(initialView().draft), specs: { efl_mm: 60 } });
  assert.equal(h.target.count('beforeunload'), 1);
  const saving = h.beginSave();
  assert.equal(h.target.count('beforeunload'), 1);
  h.finishSave({ ...fieldsFor(initialView().draft), specs: { efl_mm: 60 } });
  await saving;
  h.settle();
  assert.equal(h.dirty, false);
  assert.equal(h.target.count('beforeunload'), 0);
  h.edit({ ...fieldsFor(initialView().draft), specs: { efl_mm: 55 } });
  assert.equal(h.target.count('beforeunload'), 1);
  h.edit({ ...fieldsFor(initialView().draft), specs: { efl_mm: 60 } });
  assert.equal(h.target.count('beforeunload'), 0);
});

test('real save failure keeps the guard armed', async () => {
  const h = await harness();
  await h.start();
  h.edit({ ...fieldsFor(initialView().draft), specs: { efl_mm: 60 } });
  const saving = h.beginSave();
  h.failSave();
  await assert.rejects(saving);
  h.settle();
  assert.equal(h.dirty, true);
  assert.equal(h.target.count('beforeunload'), 1);
});

test('reverting to saved content while saving keeps saving protection', async () => {
  const h = await harness();
  await h.start();
  h.edit({ ...fieldsFor(initialView().draft), specs: { efl_mm: 60 } });
  const saving = h.beginSave();
  h.edit(fieldsFor(initialView().draft)); // deliberate revert while request is pending
  assert.equal(h.dirty, false);
  assert.equal(h.target.count('beforeunload'), 1); // saving still protects
  h.finishSave({ ...fieldsFor(initialView().draft), specs: { efl_mm: 60 } });
  await saving;
  h.settle();
  assert.equal(h.target.count('beforeunload'), 1); // server differs from local revert
});

test('dispose removes the listener and tolerates a missing window target', () => {
  const target = fakeTarget();
  const guard = createUnsavedChangesGuard(target);
  guard.update(true);
  guard.dispose();
  assert.equal(target.count('beforeunload'), 0);
  assert.equal(guard.armed, false);
  const detached = createUnsavedChangesGuard(null);
  assert.equal(detached.update(true), false);
  detached.dispose();
});

test('project switching and service reset keep the registered guard until explicit discard', async () => {
  const h = await harness();
  h.edit({ ...fieldsFor(initialView().draft), user_prompt: 'unsaved p' });
  const other = { ...initialView(), project_name: 'other' };
  h.client.api.workflow = async () => other;
  await h.client.refresh();
  assert(h.dirty);
  assert.equal(h.target.count('beforeunload'), 1);
  h.client.reset('replacement-service');
  assert(h.dirty); // view=null must not make the existing editor clean
  assert.equal(h.target.count('beforeunload'), 1);
  const [handler] = h.target.listeners.get('beforeunload');
  const event = { preventDefault() { this.prevented = true; } };
  handler(event);
  assert.equal(event.prevented, true);
  h.discard(other);
  assert.equal(h.dirty, false);
  assert.equal(h.target.count('beforeunload'), 0);
});
