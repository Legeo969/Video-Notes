<script lang="ts">
  import { onMount } from "svelte";
  import Sidebar from "./lib/components/Sidebar.svelte";
  import Topbar from "./lib/components/Topbar.svelte";
  import Icon from "./lib/components/Icon.svelte";
  import Process from "./pages/Process.svelte";
  import Tasks from "./pages/Tasks.svelte";
  import Notes from "./pages/Notes.svelte";
  import Collections from "./pages/Collections.svelte";
  import Settings from "./pages/Settings.svelte";
  import { activeJobs, initializeJobEvents, navigateTo, refreshJobs } from "./lib/stores/jobs";
  import { getEngineStatus, onEngineEvent, runningInTauri, toErrorMessage } from "./lib/api";
  import type { PageName } from "./lib/types";

  let currentPage = $state<PageName>("process");
  let engineError = $state("");
  let startupLog = $state("");
  let engineOnline = $state(true);
  let alertExpanded = $state(false);

  function navigate(page: string) {
    currentPage = page as PageName;
  }

  function setEngineFailure(payload: { error?: string | null; startup_log?: string }) {
    engineOnline = false;
    engineError = payload.error || "处理引擎未能启动。";
    startupLog = payload.startup_log || startupLog;
  }

  async function retryEngine() {
    engineError = "";
    engineOnline = false;
    try {
      await refreshJobs();
      const status = await getEngineStatus();
      engineOnline = status.running;
      if (!status.running) setEngineFailure(status);
    } catch (error) {
      const status = await getEngineStatus().catch(() => null);
      setEngineFailure({
        error: status?.error || toErrorMessage(error),
        startup_log: status?.startup_log,
      });
    }
  }

  $effect(() => {
    const target = $navigateTo;
    if (target) {
      currentPage = target;
      navigateTo.set(null);
    }
  });

  onMount(() => {
    const disposers: Array<() => void> = [];
    let disposed = false;

    async function initialize() {
      if (runningInTauri()) {
        const stopFailed = await onEngineEvent<{ error?: string; startup_log?: string }>(
          "engine.start_failed",
          setEngineFailure
        );
        const stopStarted = await onEngineEvent("engine.started", () => {
          engineOnline = true;
          engineError = "";
        });
        const stopDisconnected = await onEngineEvent("engine.disconnected", () => {
          setEngineFailure({ error: "旧版处理引擎连接已断开；native engine 仍可用于设置和插件管理。" });
        });
        if (disposed) {
          stopFailed(); stopStarted(); stopDisconnected();
        } else {
          disposers.push(stopFailed, stopStarted, stopDisconnected);
        }

        const status = await getEngineStatus().catch(() => null);
        if (status) {
          engineOnline = status.running;
          startupLog = status.startup_log || "";
          if (!status.running && status.error) setEngineFailure(status);
        }
      }

      try {
        await refreshJobs();
        engineOnline = true;
      } catch (error) {
        const status = await getEngineStatus().catch(() => null);
        setEngineFailure({
          error: status?.error || toErrorMessage(error),
          startup_log: status?.startup_log,
        });
      }

      const stopJobs = await initializeJobEvents();
      if (disposed) stopJobs();
      else disposers.push(stopJobs);
    }

    initialize().catch((error) => {
      setEngineFailure({ error: toErrorMessage(error) });
    });

    return () => {
      disposed = true;
      for (const dispose of disposers) dispose();
    };
  });
</script>

<div class="app-layout">
  <Sidebar {navigate} active={currentPage} {engineOnline} activeJobCount={$activeJobs.length} />
  <section class="workspace-shell">
    <Topbar page={currentPage} {navigate} {engineOnline} activeJobCount={$activeJobs.length} />
    <main class="content-area">
      {#if engineError}
        <section class="engine-alert" role="alert">
          <div class="alert-symbol"><Icon name="alert" size={18} /></div>
          <div class="alert-copy">
            <div class="alert-title-row"><strong>处理能力暂不可用</strong><span>界面仍可浏览</span></div>
            <p>{engineError}</p>
            {#if alertExpanded && startupLog}<code>{startupLog}</code>{/if}
            {#if startupLog}<button class="detail-link" onclick={() => alertExpanded = !alertExpanded}>{alertExpanded ? "收起诊断信息" : "查看诊断信息"}</button>{/if}
          </div>
          <button class="btn btn-secondary btn-sm" onclick={retryEngine}><Icon name="refresh" size={14} />重新连接</button>
        </section>
      {/if}
      <div class="page-host" class:full-bleed={currentPage === "notes"}>
        {#if currentPage === "process"}<Process />
        {:else if currentPage === "tasks"}<Tasks />
        {:else if currentPage === "notes"}<Notes />
        {:else if currentPage === "collections"}<Collections />
        {:else if currentPage === "settings"}<Settings />{/if}
      </div>
    </main>
  </section>
</div>

<style>
  .app-layout { display: flex; width: 100%; height: 100vh; overflow: hidden; background: var(--bg-app); }
  .workspace-shell { flex: 1; min-width: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  .content-area { flex: 1; min-height: 0; overflow: hidden; background: var(--bg-app); }
  .page-host { height: 100%; min-height: 0; overflow-y: auto; padding: 30px 34px 48px; }
  .page-host.full-bleed { height: 100%; min-height: 0; padding: 0; overflow: hidden; }
  .engine-alert { position: sticky; top: 10px; z-index: 30; display: flex; align-items: flex-start; gap: 11px; width: calc(100% - 68px); max-width: 1240px; margin: 10px auto 0; padding: 11px 12px; border: 1px solid color-mix(in srgb, var(--danger-color) 22%, var(--border-color)); border-radius: 12px; background: color-mix(in srgb, var(--danger-soft) 94%, var(--bg-card)); box-shadow: var(--shadow-sm); }
  .alert-symbol { display: grid; place-items: center; flex: 0 0 32px; width: 32px; height: 32px; border-radius: 9px; color: var(--danger-color); background: color-mix(in srgb, var(--danger-color) 10%, transparent); }
  .alert-copy { flex: 1; min-width: 0; }
  .alert-title-row { display: flex; align-items: center; gap: 8px; }
  .alert-title-row strong { font-size: 15px; }
  .alert-title-row span { padding: 3px 8px; border-radius: 99px; color: var(--text-secondary); background: var(--bg-card); font-size: 12px; font-weight: 650; }
  .alert-copy p { margin-top: 5px; color: var(--text-secondary); font-size: 14px; overflow-wrap: anywhere; }
  .alert-copy code { display: block; margin-top: 7px; padding: 7px 9px; border-radius: 7px; background: var(--bg-card); color: var(--text-secondary); font-size: 13px; overflow-wrap: anywhere; }
  .detail-link { margin-top: 5px; border: 0; color: var(--danger-color); background: transparent; cursor: pointer; font-size: 13px; font-weight: 650; }
  @media (max-width: 1100px) { .page-host { padding: 24px 24px 38px; } .engine-alert { width: calc(100% - 40px); } }
  @media (max-width: 960px) { .page-host { padding: 16px 12px 28px; } .engine-alert { width: calc(100% - 24px); } }
</style>
