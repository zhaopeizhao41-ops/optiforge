"""Pure validation for the optional, non-proportional proposal structure diagram.

The diagram is a short ordered list of labelled nodes for the proposal review
page. It carries no coordinates, radii, thicknesses, rays, markup or links, and
nothing here reads files, projects, the network or the native OpticStudio API.
Anything outside the fixed schema is rejected as a whole.

Only the whole ``structure_diagram`` may be absent (``None``) for compatibility
with older proposals and callers. Optional fields inside it are validated by
presence: once ``caption`` / ``note`` / ``element_count`` appears, an explicit
JSON ``null`` is a type error, not "missing". Length limits count Unicode code
points (matching JavaScript code-point counting on the review page).
"""

SCHEMA_VERSION = 1
KINDS = ("object", "lens_group", "stop", "mirror", "grating", "module", "image")
MIN_NODES = 2
MAX_NODES = 24
MAX_LABEL = 80
MAX_NOTE = 240
MAX_CAPTION = 500
MAX_ELEMENT_COUNT = 64

ROOT_KEYS = frozenset({"schema_version", "nodes", "caption"})
NODE_KEYS = frozenset({"kind", "label", "note", "element_count"})
EDGE_KINDS = frozenset({"object", "image"})


class StructureDiagramError(ValueError):
    """Rejected structure diagram; the caller must not write anything."""

    code = "STRUCTURE_DIAGRAM_INVALID"

    def __init__(self, message):
        super().__init__(message)
        self.code = "STRUCTURE_DIAGRAM_INVALID"


def _fail(message):
    raise StructureDiagramError(message)


def _is_int(value):
    # bool is an int subclass; the schema requires a real integer.
    return type(value) is int


def validate_structure_diagram(value):
    """Return a normalized diagram, ``None`` for absent, or raise.

    ``None`` is legal and means "no diagram was supplied": older proposals and
    callers that predate this field keep working unchanged.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        _fail("结构示意必须是对象")
    unknown = set(value) - ROOT_KEYS
    if unknown:
        _fail("结构示意包含未知字段")
    if not _is_int(value.get("schema_version")) or value["schema_version"] != SCHEMA_VERSION:
        _fail("结构示意版本必须为 1")
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        _fail("结构示意缺少节点列表")
    if not MIN_NODES <= len(nodes) <= MAX_NODES:
        _fail(f"结构示意节点数量必须在 {MIN_NODES}–{MAX_NODES} 之间")
    caption = None
    if "caption" in value:
        caption = value["caption"]
        if not isinstance(caption, str):
            _fail("结构示意说明必须是文本；不需要时请省略该字段")
        if len(caption) > MAX_CAPTION:
            _fail(f"结构示意说明不能超过 {MAX_CAPTION} 字符")

    normalized = []
    stops = 0
    last = len(nodes) - 1
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            _fail("结构示意节点必须是对象")
        if set(node) - NODE_KEYS:
            _fail("结构示意节点包含未知字段")
        kind = node.get("kind")
        if kind not in KINDS:
            _fail("结构示意节点类型不受支持")
        label = node.get("label")
        if not isinstance(label, str) or not label.strip():
            _fail("结构示意节点必须有名称")
        if len(label) > MAX_LABEL:
            _fail(f"结构示意节点名称不能超过 {MAX_LABEL} 字符")
        note = None
        if "note" in node:
            note = node["note"]
            if not isinstance(note, str):
                _fail("结构示意节点备注必须是文本；不需要时请省略该字段")
            if len(note) > MAX_NOTE:
                _fail(f"结构示意节点备注不能超过 {MAX_NOTE} 字符")
        element_count = None
        if "element_count" in node:
            element_count = node["element_count"]
            if kind != "lens_group":
                _fail("只有透镜组可以标注镜片数量")
            if not _is_int(element_count):
                _fail("镜片数量必须是整数")
            if not 1 <= element_count <= MAX_ELEMENT_COUNT:
                _fail(f"镜片数量必须在 1–{MAX_ELEMENT_COUNT} 之间")
        if kind == "stop":
            stops += 1
            if stops > 1:
                _fail("结构示意最多只能有一个光阑")
        if index == 0 and kind != "object":
            _fail("结构示意必须以物方开始")
        if index == last and kind != "image":
            _fail("结构示意必须以像方结束")
        if 0 < index < last and kind in EDGE_KINDS:
            _fail("结构示意中间节点不能是物方或像方")

        item = {"kind": kind, "label": label}
        if note is not None:
            item["note"] = note
        if element_count is not None:
            item["element_count"] = element_count
        normalized.append(item)

    result = {"schema_version": SCHEMA_VERSION, "nodes": normalized}
    if caption is not None:
        result["caption"] = caption
    return result


def describe_structure_diagram(diagram):
    """Plain-text node order for the Markdown proposal report (``""`` if absent)."""
    if not diagram:
        return ""
    parts = []
    for node in diagram.get("nodes", []):
        label = node.get("label", "")
        if node.get("kind") == "lens_group" and "element_count" in node:
            label = f"{label}（{node['element_count']} 片）"
        parts.append(label)
    text = " → ".join(parts)
    if diagram.get("caption"):
        text += f"\n\n说明：{diagram['caption']}"
    return text
