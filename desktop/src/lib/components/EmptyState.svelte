<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "./Icon.svelte";

  let {
    icon = "note",
    title,
    description = "",
    compact = false,
    action,
  }: {
    icon?: string;
    title: string;
    description?: string;
    compact?: boolean;
    action?: Snippet;
  } = $props();
</script>

<div class="empty-state-pro" class:compact>
  <div class="empty-icon-pro"><Icon name={icon} size={compact ? 20 : 26} /></div>
  <h3>{title}</h3>
  {#if description}<p>{description}</p>{/if}
  {#if action}<div class="empty-action">{@render action()}</div>{/if}
</div>
