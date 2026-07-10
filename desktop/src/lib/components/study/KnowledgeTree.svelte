<script lang="ts">
  import type { KnowledgeGraph, GraphNode } from "../../types";

  let { graph }: { graph: KnowledgeGraph } = $props();

  // Build children map from part_of relations
  let children = $derived.by(() => {
    const nodeMap = new Map(graph.nodes.map(n => [n.id, n]));
    const map = new Map<string, GraphNode[]>();
    for (const rel of graph.relations) {
      if (rel.relationType === "part_of") {
        if (!map.has(rel.targetId)) map.set(rel.targetId, []);
        const child = nodeMap.get(rel.sourceId);
        if (child) map.get(rel.targetId)!.push(child);
      }
    }
    return map;
  });

  // Root = chapter types or nodes not listed as child in any part_of relation
  let treeRoots = $derived.by(() => {
    const hasParent = new Set(
      graph.relations
        .filter(r => r.relationType === "part_of")
        .map(r => r.sourceId)
    );
    return graph.nodes.filter(n =>
      n.nodeType === "chapter" || !hasParent.has(n.id)
    );
  });
</script>

{#snippet treeNode(node: GraphNode, depth: number)}
  <div class="tree-node" style="padding-left: {depth * 20}px">
    <span
      class="node-type-dot"
      class:concept={node.nodeType === "concept"}
      class:tool={node.nodeType === "tool"}
      class:method={node.nodeType === "method"}
      class:chapter={node.nodeType === "chapter"}
    ></span>
    <span class="node-name">{node.name}</span>
    {#if node.importance > 0}
      <span class="node-importance">{'★'.repeat(node.importance)}</span>
    {/if}
    {#if node.summary}
      <span class="node-summary">{node.summary}</span>
    {/if}
  </div>
  {#if children.has(node.id)}
    {#each children.get(node.id)! as child (child.id)}
      {@render treeNode(child, depth + 1)}
    {/each}
  {/if}
{/snippet}

{#each treeRoots as root (root.id)}
  <div class="graph-node" data-id={root.id}>
    {@render treeNode(root, 0)}
  </div>
{/each}

<style>
  .graph-node { margin-bottom: 2px; }
  .tree-node { display: flex; align-items: center; gap: 8px; padding: 5px 0; }
  .node-type-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
  .node-type-dot.chapter { background: var(--accent-color); }
  .node-type-dot.concept { background: var(--success-color); }
  .node-type-dot.tool { background: var(--warning-color); }
  .node-type-dot.method { background: var(--info-color); }
  .node-name { font-size: 13px; line-height: 1.4; color: var(--text-primary); }
  .tree-node:has(.node-type-dot.chapter) .node-name { font-weight: 700; font-size: 14px; }
  .node-importance { color: var(--warning-color); font-size: 11px; letter-spacing: 1px; }
  .node-summary { color: var(--text-tertiary); font-size: 11px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
