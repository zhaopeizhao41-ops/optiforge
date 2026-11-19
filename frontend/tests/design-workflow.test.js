import test from "node:test";
import assert from "node:assert/strict";
import {
  DesignWorkflowClient,
  taskInstruction,
  reconcileDraft,
  fieldsFor,
  isDraftDirty,
  reconcileReturnedDraft,
  sameContent,
} from "../src/design-workflow.js";

const value = (rev = 1, project = "a", instance = "s") => ({
  project_name: project,
  service_instance_id: instance,
  context: { requirements_revision: rev },
  draft: {
    revision: rev,
    system_type: "imaging_lens",
    specs: { efl_mm: 50 },
    sources: { efl_mm: "user" },
    user_prompt: "",
    constraints: "",
  },
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};

test("question instructions ask the Agent to check every context anchor without changing parameters", () => {
  const text = taskInstruction({ request_id: "q-1", version: 1, kind: "surface_question" }, "project-a");
  assert.match(text, /逐项核对 target/);
  assert.match(text, /上下文锚点/);
  assert.match(text, /不修改光学参数/);
  assert.match(text, /缓存淘汰/);
});

test("late polling cannot replace an explicit saved draft", async () => {
  const old = deferred();
  const saved = value(2);
  let state = {};
  const c = new DesignWorkflowClient(
    { workflow: async (action) => (action === "view" ? old.promise : saved) },
    (v) => (state = { ...state, ...v }),
  );
  c.current = value();
  c.reset("s");
  c.current = value();
  const poll = c.refresh();
  const write = c.act("save", {});
  await write;
  old.resolve(value());
  await poll;
  assert.equal(state.view, saved);
});
test("confirmation sends the frozen reviewed context even after a poll update", async () => {
  const reviewed = value();
  let args;
  const c = new DesignWorkflowClient(
    {
      workflow: async (...a) => {
        args = a;
        return value(2);
      },
    },
    () => {},
  );
  c.current = value(2);
  await c.act("confirm", { request_id: "id" }, "a", reviewed);
  assert.equal(args[3], reviewed.context);
  assert.equal(args[4], "s");
});
test("service replacement rejects late write and never replays it", async () => {
  const d = deferred();
  let calls = 0;
  let state = {};
  const c = new DesignWorkflowClient(
    {
      workflow: () => {
        calls++;
        return d.promise;
      },
    },
    (v) => (state = { ...state, ...v }),
  );
  c.reset("s");
  c.current = value();
  const writing = c.act("save", {});
  c.reset("new");
  d.resolve(value(2));
  await assert.rejects(writing, /服务已变化/);
  assert.equal(calls, 1);
  assert.equal(state.view, null);
  assert.equal(state.saved, null);
});
test("disconnection does not replay a write; explicit reload can recover", async () => {
  let calls = 0;
  let state = {};
  const c = new DesignWorkflowClient(
    {
      workflow: async () => {
        calls++;
        throw new Error("断连");
      },
    },
    (v) => (state = { ...state, ...v }),
  );
  c.current = value();
  await assert.rejects(c.act("submit", {}), /断连/);
  assert.equal(calls, 1);
  assert.equal(state.busy, false);
  c.api.workflow = async () => value(2);
  await c.refresh();
  assert.equal(state.view.draft.revision, 2);
});
test("clean drafts adopt remote revision; dirty drafts retain conflict and project identity", () => {
  const first = value();
  let local = reconcileDraft(null, null, first);
  const newer = value(2);
  newer.draft.specs.efl_mm = 60;
  assert.equal(
    reconcileDraft(local.base, local.draft, newer).draft.specs.efl_mm,
    60,
  );
  const edited = { ...local.draft, user_prompt: "local edit" };
  const conflict = reconcileDraft(local.base, edited, newer);
  assert.equal(conflict.base.revision, 1);
  assert.equal(conflict.draft, edited);
  const switched = reconcileDraft(local.base, edited, value(0, "b"));
  assert.equal(switched.base.project, "a");
  assert.equal(switched.draft, edited);
  const cleanSwitch = reconcileDraft(local.base, local.draft, value(0, "b"));
  assert.equal(cleanSwitch.base.project, "b");
  assert.equal(cleanSwitch.draft.user_prompt, "");
});
test("unmounted controller drops late reads and all writes remain explicit", async () => {
  const d = deferred();
  let emitted = [];
  const c = new DesignWorkflowClient({ workflow: () => d.promise }, (v) =>
    emitted.push(v),
  );
  const reading = c.refresh();
  c.stop();
  d.resolve(value());
  await reading;
  assert.equal(emitted.length, 0);
});

test("old service write cannot block new context or clear its pending state", async () => {
  const old = deferred(),
    next = deferred();
  let state = {};
  let calls = 0;
  const c = new DesignWorkflowClient(
    { workflow: () => (++calls === 1 ? old.promise : next.promise) },
    (v) => (state = { ...state, ...v }),
  );
  c.reset("s");
  c.current = value();
  const a = c.act("save", {});
  c.reset("new");
  c.current = value(1, "a", "new");
  const b = c.act("save", {});
  old.resolve(value(2));
  await assert.rejects(a, /服务已变化/);
  assert.equal(state.busy, true);
  assert.equal(state.saved, null);
  next.resolve(value(2, "a", "new"));
  await b;
  assert.equal(state.busy, false);
});

test("JSON content comparison ignores nested object order but preserves data semantics", () => {
  assert(sameContent(
    { specs: { size: [11, 8], efl: 50 }, sources: { efl: "user" } },
    { sources: { efl: "user" }, specs: { efl: 50, size: [11, 8] } },
  ));
  for (const [a, b] of [
    [50, "50"], [null, {}], [false, 0], [{ x: null }, {}],
    [[11, 8], [8, 11]], [[50], { 0: 50 }],
    [{ source: "user" }, { source: "recommended" }],
    [{ nested: [{ x: 1 }] }, { nested: [{ x: 2 }] }],
  ]) assert.equal(sameContent(a, b), false);
});

const complete = () => ({
  ...value(0),
  context: { requirements_revision: 0, selection_revision: 1 },
  draft: { ...value(0).draft, specs: {}, sources: {} },
});
const filled = (order) => {
  const specs = { efl_mm: 50, f_number: 2.8, wavelength_range: "486–656 nm", fov_or_sensor: "11 mm" };
  return {
    ...fieldsFor(complete().draft),
    specs: Object.fromEntries(order.map(k => [k, specs[k]])),
    sources: Object.fromEntries(order.map(k => [k, "user"])),
  };
};
const savedView = (before, fields) => ({
  ...before,
  context: { ...before.context, requirements_revision: before.draft.revision + 1 },
  draft: {
    ...fields,
    specs: Object.fromEntries(Object.entries(fields.specs).sort()),
    sources: Object.fromEntries(Object.entries(fields.sources).sort()),
    revision: before.draft.revision + 1,
  },
});

test("all input orders and deleting then refilling accept our sorted save receipt as clean", () => {
  const keys = ["efl_mm", "f_number", "wavelength_range", "fov_or_sensor"];
  const permutations = (xs) => xs.length ? xs.flatMap((x, i) => permutations(xs.filter((_, j) => i !== j)).map(tail => [x, ...tail])) : [[]];
  for (const order of permutations(keys)) {
    const before = complete(), initial = reconcileDraft(null, null, before);
    const local = filled(order);
    const first = order[0], x = local.specs[first], source = local.sources[first];
    delete local.specs[first]; delete local.sources[first];
    local.specs[first] = x; local.sources[first] = source;
    const incoming = savedView(before, local);
    const result = reconcileDraft(initial.base, local, incoming, {
      before, after: incoming, fields: local,
    });
    assert.equal(result.base.revision, 1);
    assert(sameContent(result.draft, fieldsFor(incoming.draft)));
    const edited = { ...result.draft, user_prompt: "continue editing" };
    const polled = reconcileDraft(result.base, edited, structuredClone(incoming));
    assert.equal(polled.draft, edited);
    assert.equal(polled.base.revision, 1);
  }
});

test("refilling unchanged fields stays clean while actual type and source changes stay dirty", () => {
  const initial = reconcileDraft(null, null, value());
  const local = { ...initial.draft, specs: {}, sources: {} };
  local.specs.efl_mm = 50; local.sources.efl_mm = "user";
  assert(sameContent(local, initial.base.fields));
  for (const edited of [
    { ...local, specs: { efl_mm: "50" } },
    { ...local, sources: { efl_mm: "recommended" } },
  ]) {
    const next = value(2); next.draft.specs.efl_mm = 60;
    const result = reconcileDraft(initial.base, edited, next);
    assert.equal(result.draft, edited);
    assert.equal(result.base.revision, 1);
  }
});

test("own save acknowledgement advances baseline without overwriting in-flight edits", () => {
  const before = value(), initial = reconcileDraft(null, null, before);
  const submitted = { ...initial.draft, user_prompt: "submitted" };
  const after = savedView(before, submitted);
  const receipt = { before, after, fields: submitted };
  for (const local of [
    { ...submitted, user_prompt: "typed while saving" },
    initial.draft, // A user can intentionally revert while the save is pending.
  ]) {
    const result = reconcileDraft(initial.base, local, after, receipt);
    assert.equal(result.draft, local);
    assert.equal(result.base.revision, 2);
    assert(!sameContent(result.draft, result.base.fields));
    const poll = reconcileDraft(result.base, result.draft, structuredClone(after), receipt);
    assert.equal(poll.draft, local);
    assert.equal(poll.base.revision, 2);
    const next = savedView(after, local);
    const clean = reconcileDraft(poll.base, poll.draft, next, { before: after, after: next, fields: local });
    assert.equal(clean.base.revision, 3);
    assert(sameContent(clean.draft, fieldsFor(next.draft)));
  }
});

test("save receipts acknowledge only our saved baseline and cannot adopt a later remote context", () => {
  const before = value(), initial = reconcileDraft(null, null, before);
  const submitted = { ...initial.draft, user_prompt: "sent" };
  const after = savedView(before, submitted);
  const local = { ...submitted, user_prompt: "new local edit" };
  const receipt = { before, after, fields: submitted };
  const remote = savedView(after, { ...submitted, constraints: "other page changed" });
  for (const incoming of [
    remote,
    { ...after, service_instance_id: "new-service" },
    { ...after, context: { ...after.context, selection_revision: 2 } },
    { ...after, draft: { ...after.draft, sources: { efl_mm: "recommended" } } },
  ]) {
    const result = reconcileDraft(initial.base, local, incoming, receipt);
    assert.equal(result.base.revision, after.draft.revision);
    assert.equal(result.base.review, after);
    assert(sameContent(result.base.fields, submitted));
    assert.equal(result.draft, local);
  }
});

test("controller carries only its successful save receipt across polls and clears it on service change", async () => {
  const before = value(), submitted = fieldsFor(before.draft);
  submitted.user_prompt = "save payload";
  const after = savedView(before, submitted), pending = deferred();
  let state = {};
  const c = new DesignWorkflowClient({ workflow: (action) => action === "view" ? after : pending.promise }, v => state = { ...state, ...v });
  c.reset("s"); c.current = before;
  const write = c.act("save", submitted);
  assert.equal(state.saved, null);
  pending.resolve(after); await write;
  assert.equal(state.saved.before, before);
  assert.equal(state.saved.after, after);
  assert(sameContent(state.saved.fields, submitted));
  const receipt = state.saved;
  await c.refresh(); assert.equal(state.saved, receipt);
  c.reset("replacement"); assert.equal(state.saved, null);
});

const selectedView = (project = "a", selection = 1) => ({
  ...value(1, project),
  context: {
    project_name: project, active_project: project, selection_revision: selection,
    requirements_revision: 1, proposal_hash: null, proposal_revision: 0,
    authorization_revision: 0,
  },
});

test("dirty project identity survives another project even when its fields equal the local draft", () => {
  const original = selectedView();
  const initial = reconcileDraft(null, null, original);
  const local = { ...initial.draft, user_prompt: "keep a" };
  const incoming = selectedView("b", 2);
  incoming.draft = { ...incoming.draft, ...local };
  const kept = reconcileDraft(initial.base, local, incoming);
  assert.equal(kept.base, initial.base);
  assert.equal(kept.draft, local);
  assert(isDraftDirty(kept.base, kept.draft));
  const returned = reconcileDraft(kept.base, kept.draft, selectedView("a", 3));
  assert.equal(returned.base, initial.base); // polling alone never rebases context
  assert.equal(returned.draft, local);
  assert.notDeepEqual(returned.base.review.context, selectedView("a", 3).context);
});

test("disconnection preserves the dirty project and an explicit discard can adopt another project", () => {
  const initial = reconcileDraft(null, null, selectedView());
  const local = { ...initial.draft, constraints: "must remain available" };
  const disconnected = reconcileDraft(initial.base, local, null);
  assert.equal(disconnected.base, initial.base);
  assert.equal(disconnected.draft, local);
  assert(isDraftDirty(disconnected.base, disconnected.draft));
  const next = selectedView("b", 2);
  const discarded = reconcileDraft(null, null, next);
  assert.equal(discarded.base.project, "b");
  assert(!isDraftDirty(discarded.base, discarded.draft));
});

test("a batched save receipt and other-project poll retain later typing and deliberate reverts", () => {
  const before = selectedView(), initial = reconcileDraft(null, null, before);
  const submitted = { ...initial.draft, user_prompt: "saved a" };
  const after = savedView(before, submitted);
  const receipt = { before, after, fields: submitted };
  for (const local of [
    { ...submitted, user_prompt: "typing after save" }, initial.draft,
  ]) {
    const next = reconcileDraft(initial.base, local, selectedView("b", 2), receipt);
    assert.equal(next.base.project, "a");
    assert.equal(next.base.revision, after.draft.revision);
    assert.equal(next.base.review, after);
    assert.equal(next.draft, local);
    assert(isDraftDirty(next.base, next.draft));
  }
  const clean = reconcileDraft(initial.base, submitted, selectedView("b", 2), receipt);
  assert.equal(clean.base.project, "b");
  assert(!isDraftDirty(clean.base, clean.draft));
});

test("other-project polling cannot acknowledge a receipt from a different context or service", () => {
  const before = selectedView(), initial = reconcileDraft(null, null, before);
  const submitted = { ...initial.draft, user_prompt: "saved a" };
  const after = savedView(before, submitted);
  const local = { ...submitted, user_prompt: "later typing" };
  for (const receipt of [
    { before: { ...before, context: { ...before.context, proposal_revision: 2 } }, after, fields: submitted },
    { before, after: { ...after, service_instance_id: "other" }, fields: submitted },
    { before, after, fields: { ...submitted, constraints: "not the actual response" } },
  ]) {
    const result = reconcileDraft(initial.base, local, selectedView("b", 2), receipt);
    assert.equal(result.base, initial.base);
    assert.equal(result.draft, local);
  }
});

test("explicit successful return refreshes only selection identity and preserves the draft", () => {
  const initial = reconcileDraft(null, null, selectedView());
  const editor = { ...initial, draft: { ...initial.draft, user_prompt: "local a" } };
  const returned = selectedView("a", 3);
  const restored = reconcileReturnedDraft(editor, returned);
  assert.equal(restored.draft, editor.draft);
  assert.equal(restored.base.review, returned);
  assert.equal(restored.base.fields, initial.base.fields);
  assert(isDraftDirty(restored.base, restored.draft));
  assert.equal(reconcileDraft(restored.base, restored.draft, returned).base.review, returned);
});

test("explicit return never rebases real remote changes or an incomplete identity", () => {
  const initial = reconcileDraft(null, null, selectedView());
  const editor = { ...initial, draft: { ...initial.draft, user_prompt: "keep local" } };
  const good = selectedView("a", 3);
  for (const incoming of [
    null,
    selectedView("b", 3),
    { ...good, service_instance_id: "new-service" },
    { ...good, draft: { ...good.draft, revision: 2 } },
    { ...good, draft: { ...good.draft, user_prompt: "remote edit" } },
    ...["requirements_revision", "proposal_hash", "proposal_revision", "authorization_revision", "active_project"].map(key =>
      ({ ...good, context: { ...good.context, [key]: "changed" } })),
    { ...good, context: { ...good.context, unexpected: true } },
    { ...good, context: { ...good.context, selection_revision: 1 } },
    { ...good, context: { ...good.context, selection_revision: undefined } },
  ]) {
    assert.equal(reconcileReturnedDraft(editor, incoming), editor);
    assert.equal(editor.draft.user_prompt, "keep local");
  }
});

test("return rejection survives component reconciliation in either effect order even when remote fields match local edits", () => {
  const initial = reconcileDraft(null, null, selectedView());
  const edited = { ...initial, draft: { ...initial.draft, user_prompt: "my local change" } };
  const changedRequirements = savedView(selectedView("a", 3), edited.draft);
  const changedService = {
    ...changedRequirements, service_instance_id: "replacement",
  };
  const changedProposal = {
    ...selectedView("a", 3),
    context: { ...selectedView("a", 3).context, proposal_hash: "new proposal" },
  };
  for (const incoming of [changedRequirements, changedService, changedProposal]) {
    for (const effectFirst of [false, true]) {
      let editor = edited;
      if (effectFirst) editor = reconcileDraft(editor.base, editor.draft, incoming);
      editor = reconcileReturnedDraft(editor, incoming);
      editor = reconcileDraft(editor.base, editor.draft, incoming);
      assert.equal(editor.base, initial.base);
      assert.equal(editor.draft, edited.draft);
      assert(isDraftDirty(editor.base, editor.draft));
    }
  }
});

test("ordinary same-project matching fields cannot acknowledge an unsaved draft without a receipt", () => {
  const before = selectedView(), initial = reconcileDraft(null, null, before);
  const local = { ...initial.draft, user_prompt: "same remote and local" };
  const incoming = savedView(before, local);
  const result = reconcileDraft(initial.base, local, incoming);
  assert.equal(result.base, initial.base);
  assert.equal(result.draft, local);
  assert(isDraftDirty(result.base, result.draft));
});

test("a successful save followed by a same-project proposal poll before render preserves in-flight typing and reverts", async () => {
  for (const revert of [false, true]) {
    const before = selectedView(), initial = reconcileDraft(null, null, before);
    const submitted = { ...initial.draft, user_prompt: "submitted change" };
    const local = revert ? initial.draft : { ...submitted, user_prompt: "continued typing" };
    const after = savedView(before, submitted);
    const incoming = {
      ...after, context: { ...after.context, proposal_hash: "new proposal", proposal_revision: 1 },
    };
    let flow = {};
    const client = new DesignWorkflowClient({ workflow: async (action) => action === "save" ? after : incoming },
      changes => { flow = { ...flow, ...changes }; });
    client.reset("s"); client.current = before;
    await client.act("save", submitted, "a", before);
    await client.refresh(); // React need not render the intermediate save response.
    const editor = reconcileDraft(initial.base, local, flow.view, flow.saved);
    assert.equal(editor.base.review, after);
    assert.equal(editor.base.revision, after.draft.revision);
    assert.equal(editor.draft, local);
    assert(isDraftDirty(editor.base, editor.draft));
    assert.notDeepEqual(editor.base.review.context, incoming.context);
    const repeated = reconcileDraft(editor.base, editor.draft, incoming, flow.saved);
    assert.equal(repeated.base, editor.base); // the old receipt cannot be consumed twice
    assert.equal(repeated.draft, local);
  }
});

test("return selects with the current other-project context, then saves with the fresh original context", async () => {
  const original = selectedView(), initial = reconcileDraft(null, null, original);
  const editor = { ...initial, draft: { ...initial.draft, user_prompt: "save only a" } };
  const active = selectedView("b", 2), returned = selectedView("a", 3);
  const calls = [];
  const client = new DesignWorkflowClient({ workflow: async (...args) => {
    calls.push(args);
    return args[0] === "select" ? returned : savedView(returned, editor.draft);
  } }, () => {});
  client.reset("s"); client.current = active;
  const next = await client.act("select", {}, editor.base.project, active);
  const recovered = reconcileReturnedDraft(editor, next);
  await client.act("save", recovered.draft, recovered.base.project, recovered.base.review);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0], ["select", "a", {}, active.context, "s"]);
  assert.deepEqual(calls[1], ["save", "a", editor.draft, returned.context, "s"]);
});
