<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "./Icon.svelte";
  import type { PageName } from "../types";

  let { page, navigate, engineOnline, activeJobCount = 0 }: { page: PageName; navigate: (page: string) => void; engineOnline: boolean; activeJobCount?: number } = $props();

  const meta: Record<PageName, { title: string }> = {
    process: { title: "创建笔记" },
    tasks: { title: "任务中心" },
    notes: { title: "笔记库" },
    collections: { title: "合集" },
    settings: { title: "设置" },
  };

  // Command palette state
  let commandOpen = $state(false);
  let commandQuery = $state("");

  const commandItems: Array<{ id: PageName; label: string; icon: string }> = [
    { id: "process", label: "创建笔记", icon: "sparkles" },
    { id: "tasks", label: "任务中心", icon: "tasks" },
    { id: "notes", label: "笔记库", icon: "note" },
    { id: "collections", label: "合集", icon: "folder" },
    { id: "settings", label: "设置", icon: "settings" },
  ];

  function handleKeydown(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      commandOpen = !commandOpen;
    }
  }

  let darkMode = $state(false);
  onMount(() => {
    document.addEventListener("keydown", handleKeydown);
    const saved = localStorage.getItem("video-notes-theme");
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    darkMode = saved ? saved === "dark" : Boolean(prefersDark);
    return () => document.removeEventListener("keydown", handleKeydown);
  });
  function toggleTheme() {
    darkMode = !darkMode;
    document.documentElement.classList.toggle("dark", darkMode);
    localStorage.setItem("video-notes-theme", darkMode ? "dark" : "light");
  }
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

    <button class="icon-btn theme-btn" onclick={toggleTheme} title={darkMode ? "切换到浅色模式" : "切换到深色模式"}>
      <Icon name={darkMode ? "sun" : "moon"} size={16} />
    </button>

    <button class="command-chip" type="button" onclick={() => commandOpen = true} title="搜索 (Ctrl+K)">
      <Icon name="search" size={16} />
      <span>搜索命令…</span>
      <kbd>Ctrl K</kbd>
    </button>

    {#if commandOpen}
      <!-- Command palette overlay -->
      <div class="command-overlay" role="presentation" onclick={() => commandOpen = false}>
        <div class="command-palette" role="dialog" aria-modal="true" tabindex="-1" onclick={(e) => e.stopPropagation()} onkeydown={(e) => { if (e.key === "Escape") commandOpen = false; }}>
          <div class="command-input-wrap">
            <Icon name="search" size={18} />
            <input type="text" class="command-input" placeholder="搜索页面、任务、笔记…" onkeydown={(e) => { if (e.key === "Escape") commandOpen = false; }} bind:value={commandQuery} />
          </div>
          <div class="command-results">
            {#if commandQuery}
              <div class="command-group">
                <p class="command-group-label">页面</p>
                {#each commandItems.filter(item => item.label.includes(commandQuery)) as item}
                  <button class="command-item" onclick={() => { navigate(item.id); commandOpen = false; }}>
                    <Icon name={item.icon} size={16} />{item.label}
                  </button>
                {/each}
              </div>
            {:else}
              <p class="command-hint">输入关键词搜索页面、任务或笔记</p>
            {/if}
          </div>
        </div>
      </div>
    {/if}
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

  .theme-btn { width: 38px; height: 38px; }

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

  .command-overlay {
    position: fixed; inset: 0; z-index: 2000;
    display: grid; place-items: start center; padding-top: 15vh;
    background: rgba(14, 17, 28, .64); backdrop-filter: blur(10px);
    animation: fade-in .12s ease;
  }
  .command-palette {
    width: min(580px, calc(100vw - 48px));
    display: flex; flex-direction: column;
    border: 1px solid var(--border-color);
    border-radius: 16px;
    background: var(--bg-elevated);
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    animation: modal-in .15s ease;
  }
  .command-input-wrap {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-color);
    color: var(--text-tertiary);
  }
  .command-input {
    flex: 1; border: 0; outline: 0;
    background: transparent;
    color: var(--text-primary);
    font-size: 16px;
  }
  .command-input::placeholder { color: var(--text-tertiary); }
  .command-results { padding: 8px; max-height: 360px; overflow-y: auto; }
  .command-group { display: flex; flex-direction: column; gap: 2px; }
  .command-group-label { padding: 6px 10px; color: var(--text-tertiary); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
  .command-item {
    display: flex; align-items: center; gap: 10px;
    width: 100%; padding: 9px 10px;
    border: 0; border-radius: 8px;
    color: var(--text-primary); background: transparent;
    cursor: pointer; font-size: 14px; text-align: left;
  }
  .command-item:hover { background: var(--bg-hover); }
  .command-hint { padding: 24px 16px; color: var(--text-tertiary); font-size: 14px; text-align: center; }

  @keyframes fade-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  @keyframes modal-in {
    from { opacity: 0; transform: translateY(-8px) scale(.98); }
    to { opacity: 1; transform: translateY(0) scale(1); }
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
