<script lang="ts">
  import type { PageName } from "../lib/types";

  let { navigate, active }: { navigate: (page: string) => void; active: string } = $props();

  const pages: { id: PageName; label: string; icon: string }[] = [
    { id: "process", label: "处理", icon: "▶" },
    { id: "tasks", label: "任务", icon: "☰" },
    { id: "notes", label: "笔记", icon: "📝" },
    { id: "collections", label: "合集", icon: "📁" },
    { id: "settings", label: "设置", icon: "⚙" },
  ];
</script>

<nav class="sidebar">
  <div class="sidebar-header">
    <h1 class="app-title">Video Notes AI</h1>
  </div>
  <ul class="nav-list">
    {#each pages as page}
      <li>
        <button
          class="nav-item"
          class:active={active === page.id}
          onclick={() => navigate(page.id)}
        >
          <span class="nav-icon">{page.icon}</span>
          <span class="nav-label">{page.label}</span>
        </button>
      </li>
    {/each}
  </ul>
</nav>

<style>
  .sidebar {
    width: 200px;
    min-width: 200px;
    background: var(--bg-sidebar);
    border-right: 1px solid var(--border-color);
    display: flex;
    flex-direction: column;
  }

  .sidebar-header {
    padding: 20px 16px;
    border-bottom: 1px solid var(--border-color);
  }

  .app-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
    margin: 0;
  }

  .nav-list {
    list-style: none;
    padding: 8px;
    margin: 0;
    flex: 1;
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 12px;
    border: none;
    background: none;
    border-radius: 6px;
    color: var(--text-secondary);
    cursor: pointer;
    font-size: 14px;
    transition: all 0.15s;
  }

  .nav-item:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }

  .nav-item.active {
    background: var(--accent-bg);
    color: var(--accent-color);
    font-weight: 500;
  }

  .nav-icon {
    font-size: 16px;
    width: 20px;
    text-align: center;
  }
</style>