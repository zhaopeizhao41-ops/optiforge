import React from "react";
import { parseStructureDiagram } from "./proposal-structure.js";

// Display-only labels for the fixed node kinds. All user text is rendered as
// React text nodes, never as markup.
const KIND_LABELS = {
  object: "物方",
  lens_group: "透镜组",
  stop: "光阑",
  mirror: "反射镜",
  grating: "光栅",
  module: "模块",
  image: "像方",
};

export function ProposalStructure({ proposal, proposalBound }) {
  const parsed = parseStructureDiagram(proposal?.structure_diagram);
  if (parsed.status === "missing") {
    return (
      <p className="proposal-structure-empty">未提供结构示意，按文字方案审阅</p>
    );
  }
  return (
    <section
      className="proposal-structure"
      aria-labelledby="proposal-structure-heading"
    >
      <div className="proposal-structure-head">
        <h3 id="proposal-structure-heading">方案结构示意</h3>
        {proposalBound === false && (
          <p className="proposal-structure-warning" role="alert">
            旧版示意，不能作为本次审批依据
          </p>
        )}
      </div>
      {parsed.status === "invalid" ? (
        <p className="proposal-structure-empty">
          结构示意格式不受支持，按文字方案审阅
        </p>
      ) : (
        <>
          <ol className="proposal-structure-flow" aria-label="结构节点顺序">
            {parsed.diagram.nodes.map((node, index) => (
              <li
                className="proposal-structure-node"
                key={`${index}-${node.kind}`}
              >
                <span className="proposal-structure-kind">
                  {KIND_LABELS[node.kind] ?? node.kind}
                </span>
                <span className="proposal-structure-label">{node.label}</span>
                {node.element_count != null && (
                  <span className="proposal-structure-count">
                    {node.element_count} 片
                  </span>
                )}
                {node.note ? (
                  <span className="proposal-structure-note">{node.note}</span>
                ) : null}
              </li>
            ))}
          </ol>
          {parsed.diagram.caption ? (
            <p className="proposal-structure-caption">
              {parsed.diagram.caption}
            </p>
          ) : null}
        </>
      )}
      <p className="proposal-structure-disclaimer">
        非比例示意 · 未仿真；不代表真实光路、通光或像质
      </p>
    </section>
  );
}
