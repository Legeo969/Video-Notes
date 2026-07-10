<script lang="ts">
  import Icon from "../Icon.svelte";
  import type { KnowledgeNode } from "../../types";

  let { nodes, depth = 0 }: { nodes: KnowledgeNode[]; depth?: number } = $props();

  let expanded = $state(new Set<string>());

  function toggle(id: string) {
    if (expanded.has(id)) {
      expanded.delete(id);
      expanded = expanded;
    } else {
      expanded.add(id);
      expanded = expanded;
    }
  }
</script>

{#each nodes as node (node.id)}
  <div class="tree-node" style="padding-left: {depth * 20}px">
    <button class="tree-toggle" class:leaf={node.children.length === 0} onclick={() => toggle(node.id)}>
      {#if node.children.length > 0}
        <Icon name="chevron-right" size={14} className={expanded.has(node.id) ? "expanded" : ""} />
      {:else}
        <span class="tree-dot" class:chapter={node.kind === "chapter"} class:section={node.kind === "section"} class:concept={node.kind === "concept"}></span>
      {/if}
    </button>
    <span class="tree-label" class:chapter={node.kind === "chapter"} class:section={node.kind === "section"} class:concept={node.kind === "concept"}>
      {node.label}
    </span>
  </div>
  {#if expanded.has(node.id) && node.children.length > 0}
    <svelte:self nodes={node.children} depth={depth + 1} />
  {/if}
{/each}

<style>
  .tree-node { display: flex; align-items: center; gap: 6px; padding: 4px 0; }
  .tree-toggle { display: grid; place-items: center; width: 20px; height: 20px; flex: 0 0 auto; border: 0; background: transparent; cursor: pointer; color: var(--text-tertiary); padding: 0; border-radius: 4px; transition: transform .15s; }
  .tree-toggle:hover { background: var(--bg-hover); }
  .tree-toggle.leaf { cursor: default; }
  .tree-toggle.leaf:hover { background: transparent; }
  .tree-toggle :global(svg) { transition: transform .15s; }
  .tree-toggle :global(svg.expanded) { transform: rotate(90deg); }
  .tree-dot { width: 8px; height: 8px; border-radius: 50%; }
  .tree-dot.chapter { background: var(--accent-color); }
  .tree-dot.section { background: var(--warning-color); }
  .tree-dot.concept { background: var(--success-color); }
  .tree-label { font-size: 13px; line-height: 1.4; color: var(--text-primary); }
  .tree-label.chapter { font-weight: 700; font-size: 14px; }
  .tree-label.section { font-weight: 600; }
  .tree-label.concept { font-weight: 400; color: var(--text-secondary); }
</style>
