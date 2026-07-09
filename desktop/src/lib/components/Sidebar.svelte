<script lang="ts">
  import { onMount } from "svelte";
  import type { PageName } from "../types";
  import Icon from "./Icon.svelte";

  let { navigate, active }: { navigate: (page: string) => void; active: string } = $props();
  const pages: { id: PageName; label: string; icon: string }[] = [
    { id: "process", label: "创建笔记", icon: "sparkles" },
    { id: "tasks", label: "任务中心", icon: "tasks" },
    { id: "notes", label: "笔记库", icon: "note" },
    { id: "collections", label: "合集", icon: "folder" },
  ];
  const appVersion = __APP_VERSION__;
  onMount(() => {
    const saved = localStorage.getItem("video-notes-theme");
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const isDark = saved ? saved === "dark" : Boolean(prefersDark);
    document.documentElement.classList.toggle("dark", isDark);
  });
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
        <li><button class="nav-item" class:active={active === page.id} onclick={() => navigate(page.id)} aria-current={active === page.id ? "page" : undefined}><span class="nav-icon"><Icon name={page.icon} size={17} /></span><span>{page.label}</span></button></li>
      {/each}
    </ul>
    <p class="nav-label second">系统</p>
    <ul><li><button class="nav-item" class:active={active === "settings"} onclick={() => navigate("settings")}><span class="nav-icon"><Icon name="settings" size={17} /></span><span>设置</span></button></li></ul>
  </nav>
  <div class="sidebar-spacer"></div>
  <footer><span class="version">{appVersion}</span></footer>
</aside>

<style>
  .sidebar { width: 248px; min-width: 248px; height: 100vh; display: flex; flex-direction: column; padding: 18px 14px 14px; color: var(--text-primary); background: var(--bg-sidebar); border-right: 1px solid var(--border-color); overflow: hidden; }
  .brand { display: flex; align-items: center; gap: 12px; height: 56px; padding: 0 8px; }
  .brand-mark { position: relative; display: grid; place-items: center; width: 40px; height: 40px; flex: 0 0 auto; overflow: hidden; border-radius: 11px; color: #fff; background: var(--accent-color); box-shadow: 0 8px 20px var(--accent-glow); }
  .brand-mark i { position: absolute; right: -5px; width: 18px; height: 2px; border-radius: 99px; background: rgba(255,255,255,.35); transform: rotate(-36deg); }
  .brand-mark i:nth-of-type(1) { top: 8px; } .brand-mark i:nth-of-type(2) { top: 15px; } .brand-mark i:nth-of-type(3) { top: 22px; }
  .brand-copy { min-width: 0; overflow: hidden; display: flex; flex-direction: column; }
  .brand-copy strong { font-size: 17px; line-height: 1.2; letter-spacing: -.015em; }
  .brand-copy span { margin-top: 4px; color: var(--text-tertiary); font-size: 11px; font-weight: 760; letter-spacing: .13em; }
  nav ul { display: flex; flex-direction: column; gap: 3px; list-style: none; }
  .nav-label { padding: 0 12px 9px; color: var(--text-tertiary); font-size: 12px; font-weight: 760; letter-spacing: .11em; text-transform: uppercase; }
  .nav-label.second { margin-top: 19px; }
  .nav-item { position: relative; width: 100%; min-height: 44px; display: grid; grid-template-columns: 32px minmax(0,1fr) auto; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid transparent; border-radius: 10px; color: var(--text-secondary); background: transparent; cursor: pointer; text-align: left; overflow: hidden; transition: color .14s, background .14s, border-color .14s; }
  .nav-item:hover { color: var(--text-primary); background: var(--bg-hover); }
  .nav-item.active { color: var(--accent-color); background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent-color) 16%, var(--border-color)); }
  .nav-item.active::before { content: ""; position: absolute; left: -7px; width: 3px; height: 18px; border-radius: 99px; background: var(--accent-color); }
  .nav-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; color: var(--text-tertiary); }
  .nav-item.active .nav-icon { color: var(--accent-color); }
  .nav-item > span:nth-child(2) { font-size: 14px; font-weight: 620; }
  .sidebar-spacer { flex: 1; min-height: 18px; }
  footer { display: flex; align-items: center; justify-content: center; padding: 9px 2px 0; border-top: 1px solid var(--border-color); }
  .version { color: var(--text-tertiary); font-size: 12px; }
  @media (max-width: 1180px) { .sidebar { width: 220px; min-width: 220px; } }
  @media (max-width: 1050px) {
    .sidebar { width: 64px; min-width: 64px; padding: 18px 8px 14px; }
    .brand-copy, nav p.nav-label, .nav-item > span:nth-child(2), .version { display: none; }
    .brand { justify-content: center; padding: 0; }
    .nav-item { grid-template-columns: 1fr; justify-items: center; padding: 8px; min-height: 42px; }
    .nav-item.active::before { left: -4px; height: 14px; }
  }
</style>
