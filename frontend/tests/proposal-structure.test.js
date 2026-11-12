// Contract/parse tests for the non-proportional proposal structure diagram.
// Pure module: no DOM, no React, no network.
import test from "node:test";
import assert from "node:assert/strict";
import {
  STRUCTURE_LIMITS,
  parseStructureDiagram,
  structureSummary,
} from "../src/proposal-structure.js";

const valid = {
  schema_version: 1,
  nodes: [
    { kind: "object", label: "物方" },
    { kind: "lens_group", label: "前组", element_count: 2 },
    { kind: "stop", label: "光阑" },
    { kind: "image", label: "像方" },
  ],
  caption: "示意，非比例",
};

const node = (kind, extra = {}) => ({ kind, label: kind, ...extra });

test("accepts a valid diagram and summarizes element counts", () => {
  const parsed = parseStructureDiagram(valid);
  assert.equal(parsed.status, "ok");
  assert.deepEqual(parsed.diagram, valid);
  assert.equal(structureSummary(parsed.diagram), "物方 → 前组（2 片） → 光阑 → 像方");
});

test("missing diagrams degrade without inventing a node", () => {
  assert.equal(parseStructureDiagram(undefined).status, "missing");
  assert.equal(parseStructureDiagram(null).status, "missing");
  assert.equal(parseStructureDiagram({}).status, "invalid");
});

test("accepts every documented boundary exactly", () => {
  const smallest = {
    schema_version: 1,
    nodes: [node("object"), node("image")],
  };
  assert.equal(parseStructureDiagram(smallest).status, "ok");

  const widest = {
    schema_version: 1,
    nodes: [
      { ...node("object"), label: "o".repeat(STRUCTURE_LIMITS.maxLabel) },
      {
        ...node("lens_group"),
        note: "n".repeat(STRUCTURE_LIMITS.maxNote),
        element_count: STRUCTURE_LIMITS.maxElements,
      },
      ...Array.from({ length: 21 }, () => node("module")),
      node("image"),
    ],
    caption: "c".repeat(STRUCTURE_LIMITS.maxCaption),
  };
  assert.equal(widest.nodes.length, STRUCTURE_LIMITS.maxNodes);
  assert.equal(parseStructureDiagram(widest).status, "ok");
  assert.equal(parseStructureDiagram({ ...widest, nodes: widest.nodes.slice(0, -1) }).status, "invalid");
});

test("rejects schema, structure and type violations as a whole", () => {
  const cases = {
    "not an object": [],
    "unknown root key": { ...valid, extra: 1 },
    "version zero": { ...valid, schema_version: 0 },
    "version two": { ...valid, schema_version: 2 },
    "version string": { ...valid, schema_version: "1" },
    "version bool": { ...valid, schema_version: true },
    "nodes not list": { ...valid, nodes: {} },
    "one node": { schema_version: 1, nodes: [node("object")] },
    "too many nodes": {
      schema_version: 1,
      nodes: [node("object"), ...Array.from({ length: 23 }, () => node("module")), node("image")],
    },
    "first not object": {
      schema_version: 1,
      nodes: [node("module"), node("image")],
    },
    "last not image": {
      schema_version: 1,
      nodes: [node("object"), node("module")],
    },
    "middle object": {
      schema_version: 1,
      nodes: [node("object"), node("object"), node("image")],
    },
    "two stops": {
      schema_version: 1,
      nodes: [node("object"), node("stop"), node("stop"), node("image")],
    },
    "count on stop": {
      schema_version: 1,
      nodes: [node("object"), node("stop", { element_count: 1 }), node("image")],
    },
    "count bool": {
      schema_version: 1,
      nodes: [node("object"), node("lens_group", { element_count: true }), node("image")],
    },
    "count zero": {
      schema_version: 1,
      nodes: [node("object"), node("lens_group", { element_count: 0 }), node("image")],
    },
    "count too big": {
      schema_version: 1,
      nodes: [node("object"), node("lens_group", { element_count: 65 }), node("image")],
    },
    "count float": {
      schema_version: 1,
      nodes: [node("object"), node("lens_group", { element_count: 2.5 }), node("image")],
    },
    "blank label": {
      schema_version: 1,
      nodes: [{ kind: "object", label: "  " }, node("image")],
    },
    "label too long": {
      schema_version: 1,
      nodes: [{ kind: "object", label: "o".repeat(81) }, node("image")],
    },
    "note too long": {
      schema_version: 1,
      nodes: [{ kind: "object", label: "o", note: "n".repeat(241) }, node("image")],
    },
    "caption too long": { ...valid, caption: "c".repeat(501) },
    "caption not text": { ...valid, caption: 1 },
    "note not text": {
      schema_version: 1,
      nodes: [{ kind: "object", label: "o", note: 1 }, node("image")],
    },
    "unknown node key": {
      schema_version: 1,
      nodes: [{ kind: "object", label: "o", url: "https://x" }, node("image")],
    },
    "caption null": { ...valid, caption: null },
    "note null": {
      schema_version: 1,
      nodes: [{ kind: "object", label: "o", note: null }, node("image")],
    },
    "lens count null": {
      schema_version: 1,
      nodes: [
        node("object"),
        node("lens_group", { element_count: null }),
        node("image"),
      ],
    },
    "stop count null": {
      schema_version: 1,
      nodes: [node("object"), node("stop", { element_count: null }), node("image")],
    },
  };
  for (const [name, value] of Object.entries(cases)) {
    assert.equal(parseStructureDiagram(value).status, "invalid", name);
  }
});

test("counts code points, not UTF-16 units, for every text limit", () => {
  const emoji = "😀"; // one code point, two UTF-16 units
  assert.equal(emoji.length, 2);

  const withLabel = (length) => ({
    schema_version: 1,
    nodes: [
      { kind: "object", label: emoji.repeat(length) },
      node("image"),
    ],
  });
  assert.equal(parseStructureDiagram(withLabel(80)).status, "ok");
  assert.equal(parseStructureDiagram(withLabel(81)).status, "invalid");

  const noteOf = (length) => ({
    schema_version: 1,
    nodes: [
      { kind: "object", label: "o", note: emoji.repeat(length) },
      node("image"),
    ],
  });
  assert.equal(parseStructureDiagram(noteOf(240)).status, "ok");
  assert.equal(parseStructureDiagram(noteOf(241)).status, "invalid");

  const captionOf = (length) => ({
    schema_version: 1,
    nodes: withLabel(2).nodes,
    caption: emoji.repeat(length),
  });
  assert.equal(parseStructureDiagram(captionOf(500)).status, "ok");
  assert.equal(parseStructureDiagram(captionOf(501)).status, "invalid");
});

test("keeps markup-like text as plain data, never as parsed structure", () => {
  const injected = {
    schema_version: 1,
    nodes: [
      { kind: "object", label: "<img src=x onerror=alert(1)>" },
      {
        kind: "lens_group",
        label: "<script>alert(1)</script>",
        note: "&\"><svg onload=alert(1)>",
        element_count: 1,
      },
      node("image"),
    ],
    caption: "<b>caption</b>",
  };
  const parsed = parseStructureDiagram(injected);
  assert.equal(parsed.status, "ok");
  assert.equal(parsed.diagram.nodes[0].label, "<img src=x onerror=alert(1)>");
  assert.equal(parsed.diagram.nodes[1].note, "&\"><svg onload=alert(1)>");
  assert.equal(parsed.diagram.caption, "<b>caption</b>");
  assert.equal(
    structureSummary(parsed.diagram),
    "<img src=x onerror=alert(1)> → <script>alert(1)</script>（1 片） → image",
  );
});

test("does not mutate its input", () => {
  const input = JSON.parse(JSON.stringify(valid));
  parseStructureDiagram(input);
  assert.deepEqual(input, valid);
});
