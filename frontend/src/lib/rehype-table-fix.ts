import type { Element, Root } from "hast";
import { visit } from "unist-util-visit";

const VALID_TABLE_SECTIONS = ["thead", "tbody", "tfoot", "colgroup", "caption"];

/** Wrap invalid table-section children (non-tr) in tr>td */
function fixTableSection(node: Element): void {
  const valid: Element["children"] = [];
  let orphans: Element["children"] = [];

  const flushOrphans = () => {
    if (orphans.length > 0) {
      valid.push({
        type: "element",
        tagName: "tr",
        properties: {},
        children: [
          { type: "element", tagName: "td", properties: { colSpan: 100 }, children: orphans },
        ],
      });
      orphans = [];
    }
  };

  for (const child of node.children) {
    if (child.type === "text") {
      orphans.push(child);
    } else if (child.type === "element") {
      if (child.tagName === "tr") {
        flushOrphans();
        valid.push(child);
      } else {
        orphans.push(child);
      }
    } else {
      orphans.push(child);
    }
  }
  flushOrphans();
  node.children = valid;
}

/** Wrap invalid tr children (non-td, non-th) in td */
function fixTableRow(node: Element): void {
  const valid: Element["children"] = [];
  let orphans: Element["children"] = [];

  const flushOrphans = () => {
    if (orphans.length > 0) {
      valid.push({ type: "element", tagName: "td", properties: {}, children: orphans });
      orphans = [];
    }
  };

  for (const child of node.children) {
    if (child.type === "text") {
      orphans.push(child);
    } else if (child.type === "element") {
      if (child.tagName === "td" || child.tagName === "th") {
        flushOrphans();
        valid.push(child);
      } else {
        orphans.push(child);
      }
    } else {
      orphans.push(child);
    }
  }
  flushOrphans();
  node.children = valid;
}

/**
 * Rehype plugin that fixes malformed table structure.
 * - table: only thead, tbody, tfoot, colgroup, caption; wrap text/orphan-tr
 * - thead/tbody/tfoot: only tr; wrap text/other in tr>td
 * - tr: only td, th; wrap text/other in td
 */
export function rehypeTableFix() {
  return (tree: Root) => {
    visit(tree, "element", (node) => {
      if (node.tagName === "table") {
        const valid: Element["children"] = [];
        let orphans: Element["children"] = [];

        const flushOrphans = () => {
          if (orphans.length > 0) {
            valid.push({
              type: "element",
              tagName: "tbody",
              properties: {},
              children: [
                {
                  type: "element",
                  tagName: "tr",
                  properties: {},
                  children: [
                    {
                      type: "element",
                      tagName: "td",
                      properties: { colSpan: 100 },
                      children: orphans,
                    },
                  ],
                },
              ],
            });
            orphans = [];
          }
        };

        for (const child of node.children) {
          if (child.type === "text") {
            orphans.push(child);
          } else if (child.type === "element") {
            if (VALID_TABLE_SECTIONS.includes(child.tagName)) {
              flushOrphans();
              valid.push(child);
            } else if (child.tagName === "tr") {
              flushOrphans();
              valid.push({
                type: "element",
                tagName: "tbody",
                properties: {},
                children: [child],
              });
            } else {
              orphans.push(child);
            }
          } else {
            orphans.push(child);
          }
        }
        flushOrphans();
        node.children = valid;
      } else if (node.tagName === "thead" || node.tagName === "tbody" || node.tagName === "tfoot") {
        fixTableSection(node);
      } else if (node.tagName === "tr") {
        fixTableRow(node);
      }
    });
  };
}
