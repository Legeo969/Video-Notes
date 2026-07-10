<script lang="ts">
  import type { KnowledgeGraph, Entity } from "../../types";

  let { graph }: { graph: KnowledgeGraph } = $props();

  // Build children map from chapter/part_of relations
  let entityMap = $derived(new Map(graph.entities.map(e => [e.id, e])));

  // For each chapter, get its entities
  let treeRoots = $derived.by(() => {
    if (graph.chapters.length > 0) {
      return graph.chapters.map(ch => ({
        id: slug(ch.title),
        name: ch.title,
        entityType: "chapter" as const,
        summary: "",
        importance: 0,
        aliases: [],
        sourceRefs: [],
        children: ch.entityIds
          .map(eid => entityMap.get(eid))
          .filter((e): e is Entity => e !== undefined)
          .map(e => enrichEntity(e)),
      } as TreeNode));
    }
    // Fallback: if no chapters, show all entities flat
    return graph.entities.map(e => ({
      ...e,
      children: getRelatedEntities(e.id),
    }));
  });

  interface TreeNode extends Entity {
    children: TreeNode[];
  }

  function enrichEntity(entity: Entity): TreeNode {
    return {
      ...entity,
      children: getRelatedEntities(entity.id),
    };
  }

  function getRelatedEntities(entityId: string): TreeNode[] {
    const related: TreeNode[] = [];
    for (const rel of graph.relations) {
      let targetId: string | null = null;
      if (rel.source === entityId) targetId = rel.target;
      else if (rel.target === entityId) targetId = rel.source;
      if (!targetId) continue;

      const targetEntity = entityMap.get(targetId);
      if (!targetEntity) continue;

      const evidenceText = rel.evidence ? `: ${rel.evidence}` : "";
      related.push({
        ...targetEntity,
        children: [],
        // Override summary to show relation context
        summary: `${relationLabel(rel.relationType)} ${targetEntity.name}${evidenceText}`,
      });
    }
    return related;
  }

  function relationLabel(rt: string): string {
    const map: Record<string, string> = {
      uses: "使用",
      depends_on: "依赖",
      part_of: "属于",
      implements: "实现",
      improves: "改进",
      generates: "生成",
      imports: "导入",
      exports: "导出",
      related_to: "关联",
      similar_to: "类似",
      conflicts_with: "冲突",
      requires: "需要",
      produces: "产出",
      consumes: "消耗",
    };
    return map[rt] || "关联";
  }

  function slug(text: string): string {
    return text.toLowerCase().replace(/[^a-z0-9\-\_\u4e00-\u9fff]/g, "-").replace(/-+/g, "-");
  }

  // Color map for entity types
  function typeColor(et: string): string {
    const colors: Record<string, string> = {
      chapter: "var(--accent-color)",
      concept: "var(--success-color)",
      tool: "var(--warning-color)",
      method: "var(--info-color)",
      technology: "var(--primary-color)",
      workflow: "var(--accent-secondary)",
      asset: "var(--warning-color)",
      library: "var(--info-color)",
      person: "var(--danger-color)",
      organization: "var(--danger-color)",
      problem: "var(--danger-color)",
      solution: "var(--success-color)",
    };
    return colors[et] || "var(--text-tertiary)";
  }
</script>

{#snippet treeNode(node: TreeNode, depth: number)}
  <div class="tree-node" style="padding-left: {depth * 20}px">
    <span
      class="node-type-dot"
      style="background: {typeColor(node.entityType)}"
    ></span>
    <span class="node-name">{node.name}</span>
    {#if node.importance > 0}
      <span class="node-importance">{'★'.repeat(node.importance)}</span>
    {/if}
    {#if node.entityType !== "chapter" && node.summary}
      <span class="node-summary">{node.summary}</span>
    {/if}
    {#if node.aliases && node.aliases.length > 0}
      <span class="node-aliases">({node.aliases.join(", ")})</span>
    {/if}
  </div>
  {#if node.children.length > 0}
    {#each node.children as child (child.id)}
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
  .tree-node { display: flex; align-items: center; gap: 8px; padding: 5px 0; flex-wrap: wrap; }
  .node-type-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
  .node-name { font-size: 13px; line-height: 1.4; color: var(--text-primary); }
  .tree-node:has(.node-type-dot[style*="var(--accent-color)"]) .node-name { font-weight: 700; font-size: 14px; }
  .node-importance { color: var(--warning-color); font-size: 11px; letter-spacing: 1px; }
  .node-summary { color: var(--text-tertiary); font-size: 11px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
  .node-aliases { color: var(--text-tertiary); font-size: 10px; opacity: 0.7; }
</style>