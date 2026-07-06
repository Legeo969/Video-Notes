<script lang="ts">
  import { onMount } from "svelte";
  import type { PageName } from "../types";
  import Icon from "./Icon.svelte";

  let { navigate, active, engineOnline = true, activeJobCount = 0 }: { navigate: (page: string) => void; active: string; engineOnline?: boolean; activeJobCount?: number } = $props();
  const pages: { id: PageName; label: string; icon: string }[] = [
    { id: "process", label: "创建笔记", icon: "sparkles" },
    { id: "tasks", label: "任务中心", icon: "tasks" },
    { id: "notes", label: "笔记库", icon: "note" },
    { id: "collections", label: "合集", icon: "folder" },
  ];
  const appVersion = __APP_VERSION__;
  let darkMode = $state(false);
  function applyTheme(isDark: boolean) { darkMode = isDark; document.documentElement.classList.toggle("dark", isDark); localStorage.setItem("video-notes-theme", isDark ? "dark" : "light"); }
  onMount(() => { const saved = localStorage.getItem("video-notes-theme"); const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches; applyTheme(saved ? saved === "dark" : Boolean(prefersDark)); });
</script>

<aside class="sidebar">
  <div class="brand" data-tauri-drag-region>
    <div class="brand-mark" aria-hidden="true"><Icon name="play" size={14} strokeWidth={2.3} /><i></i><i></i><i></i></div>
    <div class="brand-copy" data-tauri-drag-region><strong>Video Notes</strong><span>AI STUDY WORKSPACE</span></div>
  </div>
  <nav aria-label="主导航">
    <p class="nav-label">工作空间</p>
    <ul>
      {#each pages as page}
        <li><button class="nav-item" class:active={active === page.id} onclick={() => navigate(page.id)} aria-current={active === page.id ? "page" : undefined}><span class="nav-icon"><Icon name={page.icon} size={17} /></span><span>{page.label}</span>{#if page.id === "tasks" && activeJobCount > 0}<em>{activeJobCount}</em>{/if}</button></li>
      {/each}
    </ul>
    <p class="nav-label second">系统</p>
    <ul><li><button class="nav-item" class:active={active === "settings"} onclick={() => navigate("settings")}><span class="nav-icon"><Icon name="settings" size={17} /></span><span>设置</span></button></li></ul>
  </nav>
  <div class="sidebar-spacer"></div>
  <section class="engine-card" class:offline={!engineOnline}>
    <div class="engine-row"><span class="engine-symbol"><Icon name="activity" size={15} /></span><div><strong>{engineOnline ? "Native 引擎正常" : "处理引擎离线"}</strong><small>{engineOnline ? "设置和插件管理可用" : "查看页面顶部诊断"}</small></div><span class="engine-status-dot"></span></div>
    {#if activeJobCount > 0}<div class="engine-progress"><span style={`width:${Math.min(100, 28 + activeJobCount * 14)}%`}></span></div>{/if}
  </section>
  <footer><button class="theme-button" onclick={() => applyTheme(!darkMode)}><Icon name={darkMode ? "sun" : "moon"} size={15} /><span>{darkMode ? "浅色外观" : "深色外观"}</span></button><span class="version">{appVersion}</span></footer>
</aside>

<style>
  .sidebar { width: 248px; min-width: 248px; height: 100vh; display: flex; flex-direction: column; padding: 18px 14px 14px; color: var(--text-primary); background: var(--bg-sidebar); border-right: 1px solid var(--border-color); }
  .brand { display: flex; align-items: center; gap: 12px; height: 56px; padding: 0 8px; }
  .brand-mark { position: relative; display: grid; place-items: center; width: 40px; height: 40px; flex: 0 0 auto; overflow: hidden; border-radius: 11px; color: #fff; background: var(--accent-color); box-shadow: 0 8px 20px var(--accent-glow); }
  .brand-mark i { position: absolute; right: -5px; width: 18px; height: 2px; border-radius: 99px; background: rgba(255,255,255,.35); transform: rotate(-36deg); }
  .brand-mark i:nth-of-type(1) { top: 8px; } .brand-mark i:nth-of-type(2) { top: 15px; } .brand-mark i:nth-of-type(3) { top: 22px; }
  .brand-copy { min-width: 0; display: flex; flex-direction: column; }
  .brand-copy strong { font-size: 17px; line-height: 1.2; letter-spacing: -.015em; }
  .brand-copy span { margin-top: 4px; color: var(--text-tertiary); font-size: 11px; font-weight: 760; letter-spacing: .13em; }
  nav ul { display: flex; flex-direction: column; gap: 3px; list-style: none; }
  .nav-label { padding: 0 12px 9px; color: var(--text-tertiary); font-size: 12px; font-weight: 760; letter-spacing: .11em; text-transform: uppercase; }
  .nav-label.second { margin-top: 19px; }
  .nav-item { position: relative; width: 100%; min-height: 44px; display: grid; grid-template-columns: 32px minmax(0,1fr) auto; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid transparent; border-radius: 10px; color: var(--text-secondary); background: transparent; cursor: pointer; text-align: left; transition: color .14s, background .14s, border-color .14s; }
  .nav-item:hover { color: var(--text-primary); background: var(--bg-hover); }
  .nav-item.active { color: var(--accent-color); background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent-color) 16%, var(--border-color)); }
  .nav-item.active::before { content: ""; position: absolute; left: -7px; width: 3px; height: 18px; border-radius: 99px; background: var(--accent-color); }
  .nav-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; color: var(--text-tertiary); }
  .nav-item.active .nav-icon { color: var(--accent-color); }
  .nav-item > span:nth-child(2) { font-size: 14px; font-weight: 620; }
  .nav-item em { display: grid; place-items: center; min-width: 19px; height: 19px; padding: 0 5px; border-radius: 99px; color: #fff; background: var(--accent-color); font-size: 12px; font-style: normal; font-weight: 760; }
  .sidebar-spacer { flex: 1; min-height: 18px; }
  .engine-card { margin: 0 1px 12px; padding: 13px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card); }
  .engine-row { display: grid; grid-template-columns: 34px minmax(0,1fr) 8px; align-items: center; gap: 8px; }
  .engine-symbol { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 9px; color: var(--success-color); background: var(--success-soft); }
  .offline .engine-symbol { color: var(--danger-color); background: var(--danger-soft); }
  .engine-row div { min-width: 0; display: flex; flex-direction: column; }
  .engine-row strong { font-size: 13px; } .engine-row small { margin-top: 3px; color: var(--text-tertiary); font-size: 11px; }
  .engine-status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success-color); box-shadow: 0 0 0 4px color-mix(in srgb, var(--success-color) 12%, transparent); }
  .offline .engine-status-dot { background: var(--danger-color); box-shadow: 0 0 0 4px color-mix(in srgb, var(--danger-color) 12%, transparent); }
  .engine-progress { height: 3px; margin-top: 9px; overflow: hidden; border-radius: 99px; background: var(--bg-progress); }
  .engine-progress span { display: block; height: 100%; border-radius: inherit; background: var(--accent-color); }
  footer { display: flex; align-items: center; justify-content: space-between; padding: 9px 2px 0; border-top: 1px solid var(--border-color); }
  .theme-button { display: flex; align-items: center; gap: 8px; min-height: 36px; padding: 5px 7px; border: 0; border-radius: 8px; color: var(--text-tertiary); background: transparent; cursor: pointer; font-size: 13px; }
  .theme-button:hover { color: var(--text-primary); background: var(--bg-hover); }
  .version { color: var(--text-tertiary); font-size: 12px; }
  @media (max-width: 1180px) { .sidebar { width: 224px; min-width: 224px; } }
</style>
