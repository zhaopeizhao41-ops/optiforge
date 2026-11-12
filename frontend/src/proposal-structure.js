// Non-proportional proposal structure diagram: pure parsing, no geometry.
// Mirrors the server contract in core/proposal_diagram.py. Anything outside the
// fixed schema returns { status: "invalid" } so the UI can degrade to the text
// proposal instead of rendering partially trusted data.

export const STRUCTURE_KINDS = [
  "object",
  "lens_group",
  "stop",
  "mirror",
  "grating",
  "module",
  "image",
];

export const STRUCTURE_LIMITS = {
  minNodes: 2,
  maxNodes: 24,
  maxLabel: 80,
  maxNote: 240,
  maxCaption: 500,
  maxElements: 64,
};

const ROOT_KEYS = new Set(["schema_version", "nodes", "caption"]);
const NODE_KEYS = new Set(["kind", "label", "note", "element_count"]);
const EDGE_KINDS = new Set(["object", "image"]);

const invalid = (reason) => ({ status: "invalid", reason });
const isInteger = (value) =>
  typeof value === "number" && Number.isFinite(value) && Number.isInteger(value);
const isText = (value) => typeof value === "string";
const hasOwn = (object, key) => Object.hasOwn(object, key);
// Python len() counts code points; JS string.length counts UTF-16 units. Count
// code points so the same 80 emoji are accepted or rejected on both sides.
const codePointLength = (value) => [...value].length;

export function parseStructureDiagram(value) {
  if (value === null || value === undefined) return { status: "missing" };
  if (typeof value !== "object" || Array.isArray(value))
    return invalid("结构示意必须是对象");
  if (Object.keys(value).some((key) => !ROOT_KEYS.has(key)))
    return invalid("结构示意包含未知字段");
  if (!isInteger(value.schema_version) || value.schema_version !== 1)
    return invalid("结构示意版本必须为 1");
  if (!Array.isArray(value.nodes)) return invalid("结构示意缺少节点列表");

  const { minNodes, maxNodes, maxLabel, maxNote, maxCaption, maxElements } =
    STRUCTURE_LIMITS;
  const total = value.nodes.length;
  if (total < minNodes || total > maxNodes)
    return invalid(`结构示意节点数量必须在 ${minNodes}–${maxNodes} 之间`);

  // Optional fields are validated by presence: an explicit null is a type
  // error, not "missing" (only the whole diagram may be null/absent).
  const hasCaption = hasOwn(value, "caption");
  if (hasCaption) {
    if (!isText(value.caption))
      return invalid("结构示意说明必须是文本；不需要时请省略该字段");
    if (codePointLength(value.caption) > maxCaption)
      return invalid(`结构示意说明不能超过 ${maxCaption} 字符`);
  }

  const nodes = [];
  let stops = 0;
  for (let index = 0; index < total; index += 1) {
    const node = value.nodes[index];
    if (!node || typeof node !== "object" || Array.isArray(node))
      return invalid("结构示意节点必须是对象");
    if (Object.keys(node).some((key) => !NODE_KEYS.has(key)))
      return invalid("结构示意节点包含未知字段");
    if (!STRUCTURE_KINDS.includes(node.kind))
      return invalid("结构示意节点类型不受支持");
    if (!isText(node.label) || !node.label.trim())
      return invalid("结构示意节点必须有名称");
    if (codePointLength(node.label) > maxLabel)
      return invalid(`结构示意节点名称不能超过 ${maxLabel} 字符`);

    const hasNote = hasOwn(node, "note");
    if (hasNote) {
      if (!isText(node.note))
        return invalid("结构示意节点备注必须是文本；不需要时请省略该字段");
      if (codePointLength(node.note) > maxNote)
        return invalid(`结构示意节点备注不能超过 ${maxNote} 字符`);
    }

    const hasCount = hasOwn(node, "element_count");
    if (hasCount) {
      if (node.kind !== "lens_group")
        return invalid("只有透镜组可以标注镜片数量");
      const count = node.element_count;
      if (!isInteger(count) || count < 1 || count > maxElements)
        return invalid(`镜片数量必须是 1–${maxElements} 的整数`);
    }

    if (node.kind === "stop") {
      stops += 1;
      if (stops > 1) return invalid("结构示意最多只能有一个光阑");
    }
    if (index === 0 && node.kind !== "object")
      return invalid("结构示意必须以物方开始");
    if (index === total - 1 && node.kind !== "image")
      return invalid("结构示意必须以像方结束");
    if (index > 0 && index < total - 1 && EDGE_KINDS.has(node.kind))
      return invalid("结构示意中间节点不能是物方或像方");

    nodes.push({
      kind: node.kind,
      label: node.label,
      ...(hasNote ? { note: node.note } : {}),
      ...(hasCount ? { element_count: node.element_count } : {}),
    });
  }

  return {
    status: "ok",
    diagram: {
      schema_version: 1,
      nodes,
      ...(hasCaption ? { caption: value.caption } : {}),
    },
  };
}

export function structureSummary(diagram) {
  if (!diagram || !Array.isArray(diagram.nodes)) return "";
  return diagram.nodes
    .map((node) =>
      node.kind === "lens_group" && node.element_count != null
        ? `${node.label}（${node.element_count} 片）`
        : node.label,
    )
    .join(" → ");
}
