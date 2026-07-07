<script lang="ts">
  import Icon from "./Icon.svelte";
  import type { PageName } from "../types";

  let { page, navigate, engineOnline = true, activeJobCount = 0 }: { page: PageName; navigate: (page: string) => void; engineOnline?: boolean; activeJobCount?: number } = $props();

  const meta: Record<PageName, { title: string }> = {
    process: { title: "创建笔记" },
    tasks: { title: "任务中心" },
    notes: { title: "笔记库" },
    collections: { title: "合集" },
    settings: { title: "设置" },
  };
</script>

<header class="topbar" data-tauri-drag-region>
  <div class="breadcrumb" data-tauri-drag-region>
    <span>工作台</span>
    <Icon name="chevron-right" size={15} />
    <strong>{meta[page].title}</strong>
  </div>

  <div class="topbar-actions">
    {#if activeJobCount > 0}
      <button class="activity-chip" type="button" onclick={() => navigate("tasks")} title="查看运行中的任务">
        <span class="activity-pulse"></span>
        <strong>{activeJobCount}</strong>
        <span>个任务运行中</span>
      </button>
    {/if}

    <div class="engine-chip" class:offline={!engineOnline}>
      <span class="engine-dot"></span>
      <span>{engineOnline ? "Native 引擎在线" : "引擎离线"}</span>
    </div>

    <button class="command-chip" type="button" title="前往笔记库搜索" onclick={() => navigate("notes")}>
      <Icon name="search" size={16} />
      <span>搜索笔记</span>
      <kbd>Ctrl K</kbd>
    </button>
  </div>
</header>

<style>
  .topbar {
    height: 68px;
    min-height: 68px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    padding: 0 30px;
    border-bottom: 1px solid var(--border-color);
    background: color-mix(in srgb, var(--bg-appbar) 96%, transparent);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
  }

  .breadcrumb {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-tertiary);
    font-size: 13px;
  }

  .breadcrumb strong {
    color: var(--text-primary);
    font-size: 14px;
    font-weight: 700;
  }

  .topbar-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }

  .activity-chip,
  .engine-chip,
  .command-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 38px;
    padding: 0 12px;
    border: 1px solid var(--border-color);
    border-radius: 10px;
    color: var(--text-secondary);
    background: var(--bg-card);
    box-shadow: var(--shadow-xs);
    font-size: 13px;
  }

  .activity-chip,
  .command-chip {
    cursor: pointer;
  }

  .activity-chip:hover,
  .command-chip:hover {
    border-color: var(--border-strong);
    background: var(--bg-hover);
  }

  .activity-chip strong {
    color: var(--text-primary);
    font-size: 14px;
  }

  .activity-pulse,
  .engine-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent-color);
  }

  .activity-pulse {
    box-shadow: 0 0 0 4px var(--accent-glow);
    animation: pulse 1.8s ease-in-out infinite;
  }

  .engine-dot {
    background: var(--success-color);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--success-color) 12%, transparent);
  }

  .engine-chip.offline .engine-dot {
    background: var(--danger-color);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--danger-color) 12%, transparent);
  }

  .command-chip {
    min-width: 170px;
    justify-content: flex-start;
  }

  .command-chip span {
    flex: 1;
    text-align: left;
  }

  kbd {
    min-width: 42px;
    padding: 3px 7px;
    border: 1px solid var(--border-color);
    border-bottom-color: var(--border-strong);
    border-radius: 6px;
    color: var(--text-tertiary);
    background: var(--bg-subtle);
    font-family: var(--font-sans);
    font-size: 11px;
    text-align: center;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: .45; }
  }

  @media (max-width: 1120px) {
    .topbar { padding: 0 22px; }
    .command-chip { min-width: 38px; width: 38px; padding: 0; justify-content: center; }
    .command-chip span, .command-chip kbd { display: none; }
    .activity-chip span:last-child { display: none; }
  }
  @media (max-width: 1050px) {
    .topbar { height: 56px; min-height: 56px; padding: 0 14px; gap: 10px; }
    .topbar-actions { gap: 6px; }
    .engine-chip { display: none; }
  }
</style>
