<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "./Icon.svelte";
  let { eyebrow = "", title, description = "", icon = "", actions }: { eyebrow?: string; title: string; description?: string; icon?: string; actions?: Snippet } = $props();
</script>
<header class="page-header-pro">
  <div class="header-copy">{#if icon}<span class="header-icon"><Icon name={icon} size={18} /></span>{/if}<div>{#if eyebrow}<p class="page-eyebrow">{eyebrow}</p>{/if}<h1>{title}</h1>{#if description}<p class="page-description">{description}</p>{/if}</div></div>
  {#if actions}<div class="page-actions">{@render actions()}</div>{/if}
</header>
