<script lang="ts">
  import { engineCall, onEngineEvent } from "../lib/api";
  import type { ProviderProfile } from "../lib/types";
  import Icon from "../lib/components/Icon.svelte";
  import PageHeader from "../lib/components/PageHeader.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import StatusPill from "../lib/components/StatusPill.svelte";
  import ProvidersPanel from "../lib/components/settings/ProvidersPanel.svelte";
  import ProviderFormDialog from "../lib/components/settings/ProviderFormDialog.svelte";
  import SettingsGeneral from "../lib/components/settings/SettingsGeneral.svelte";

  interface ProviderForm {
    name: string;
    provider: string;
    api_key: string;
    base_url: string;
    model: string;
    vision_model: string;
    audio_input?: boolean;
    video_input: boolean;
  }

  interface TemplateInfo {
    id: string;
    name: string;
    description: string;
    path: string;
  }

  interface CheckResult {
    name: string;
    status: "pass" | "fail" | "warn";
    detail: string;
  }

  interface StorageStatus {
    export_dir: string;
    jobs_root: string;
    legacy_jobs_root: string;
    downloads_root: string;
    capsule_root: string;
    playback_cache: string;
    vault_path: string;
    sizes: Record<string, number>;
    counts: Record<string, { dirs: number; files: number }>;
    tasks?: {
      total: number;
      running: number;
      completed: number;
      failed: number;
    };
  }

  interface RuntimeComponent {
    component: string;
    version: string;
    description: string;
    installed: boolean;
    installed_version?: string | null;
    status: string;
    size_mb?: number;
    latest_version?: string | null;
    update_available?: boolean;
    component_path?: string;
    provides?: string[];
    missing_files?: string[];
    downloadable?: boolean;
  }

  interface SettingsBag {
    output_dir: string;
    vault_path: string;
    template: string;
    active_provider: string;
    bilibili_cookie_file: string;
    compile_concurrency: number;
    effective_compile_concurrency: number;
  }

  const emptyProviderForm = (): ProviderForm => ({
    name: "",
    provider: "openai_compat",
    api_key: "",
    base_url: "",
    model: "",
    vision_model: "",
    video_input: false,
  });

  const tabs = [
    { id: "general", label: "通用设置", icon: "settings", hint: "目录与编译" },
    { id: "providers", label: "AI 供应商", icon: "bot", hint: "文本与视觉模型" },
    { id: "templates", label: "笔记模板", icon: "template", hint: "输出结构与场景" },
    { id: "plugins", label: "插件", icon: "package", hint: "播放、分析与下载组件" },
    { id: "storage", label: "存储管理", icon: "database", hint: "缓存、工作区与导出" },
    { id: "diagnostics", label: "系统诊断", icon: "stethoscope", hint: "依赖与运行环境" },
  ];

  let activeTab = $state("general");
  let settings = $state<SettingsBag>({
    output_dir: "./output",
    vault_path: "",
    template: "default",
    active_provider: "",
    bilibili_cookie_file: "",
    compile_concurrency: 0,
    effective_compile_concurrency: 2,
  });
  let providers = $state<ProviderProfile[]>([]);
  let templates = $state<TemplateInfo[]>([]);
  let checkResults = $state<CheckResult[]>([]);
  let loading = $state(true);
  let saving = $state(false);
  let testingProvider = $state<string | null>(null);
  let doctorRunning = $state(false);
  let bundlingDiagnostics = $state(false);
  let storageLoading = $state(false);
  let storageStatus = $state<StorageStatus | null>(null);
  let componentsLoading = $state(false);
  let runtimeComponents = $state<RuntimeComponent[]>([]);
  let componentAction = $state<string | null>(null);
  let downloadProgress = $state<Record<string, number>>({});
  let checkingUpdates = $state(false);
  let toast = $state<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  let dirty = $state(false);

  let graphCacheInfo = $state<{ count: number; bytes: number }>({ count: 0, bytes: 0 });

  function refreshGraphCacheInfo() {
    const info = (window as any).__graphCacheInfo?.();
    graphCacheInfo = info ?? { count: 0, bytes: 0 };
  }

  let totalStorageBytes = $derived(
    storageStatus
      ? Object.values(storageStatus.sizes).reduce((a: number, b: number) => a + b, 0)
      : 0
  );

  interface StorageCategory {
    key: string;
    label: string;
    icon: string;
    bytes: number;
    path: string;
    danger: "safe" | "caution" | "danger";
    actionLabel: string | null;
    action: (() => Promise<void>) | null;
    confirmMsg: string | null;
    hint: string;
  }

  let storageCategories = $derived.by((): StorageCategory[] => {
    if (!storageStatus) return [];
    const s = storageStatus;
    return [
      {
        key: "exports",
        label: "笔记导出",
        icon: "file-text",
        bytes: s.sizes.exports ?? 0,
        path: s.export_dir,
        danger: "safe",
        actionLabel: null,
        action: null,
        confirmMsg: null,
        hint: "已导出的 Markdown 笔记和资源",
      },
      {
        key: "jobs",
        label: "工作区缓存",
        icon: "briefcase",
        bytes: (s.sizes.jobs ?? 0) + (s.sizes.legacy_jobs ?? 0),
        path: s.jobs_root,
        danger: "safe",
        actionLabel: "清理已完成",
        action: cleanupCompletedWorkspaces,
        confirmMsg: null,
        hint: `${s.tasks?.total ?? 0} 个任务记录 · 可清理已完成或已失效的任务`,
      },
      {
        key: "capsules",
        label: "编译缓存",
        icon: "database",
        bytes: s.sizes.capsules ?? 0,
        path: s.capsule_root,
        danger: "caution",
        actionLabel: "清空缓存",
        action: cleanupCapsules,
        confirmMsg: "确定清理所有编译缓存吗？已导出的笔记不受影响，但历史记录将无法回放。",
        hint: "清理后历史版本无法回放，已导出笔记不受影响",
      },
      {
        key: "playback_cache",
        label: "视频播放缓存",
        icon: "play",
        bytes: s.sizes.playback_cache ?? 0,
        path: s.playback_cache,
        danger: "safe",
        actionLabel: "清空缓存",
        action: cleanupPlaybackCache,
        confirmMsg: "确定清理视频播放缓存吗？下次播放 HEVC 视频时会重新转码。",
        hint: "HEVC→H.264 转码缓存，清理后下次播放时重建",
      },
      {
        key: "downloads",
        label: "下载缓存",
        icon: "download",
        bytes: s.sizes.downloads ?? 0,
        path: s.downloads_root,
        danger: "safe",
        actionLabel: null,
        action: null,
        confirmMsg: null,
        hint: "视频下载过程中的临时文件",
      },
      {
        key: "runtime",
        label: "运行时组件",
        icon: "package",
        bytes: s.sizes.runtime ?? 0,
        path: "",
        danger: "safe",
        actionLabel: null,
        action: null,
        confirmMsg: null,
        hint: "FFmpeg、yt-dlp 等外部工具",
      },
    ];
  });

  function clearGraphCache() {
    (window as any).__graphCacheClear?.();
    refreshGraphCacheInfo();
    showToast("笔记图谱缓存已清理", "success");
  }

  let showProviderModal = $state(false);
  let editingProviderName = $state<string | null>(null);
  let providerForm = $state<ProviderForm>(emptyProviderForm());
  let providerSaving = $state(false);
  let providerSearch = $state("");
  let providerModelOptions = $state<string[]>([]);
  let discoveringProviderModels = $state(false);

  let selectedTemplate = $derived(templates.find((template) => template.id === settings.template));
  let passedChecks = $derived(checkResults.filter((item) => item.status === "pass").length);
  let toolComponents = $derived([...runtimeComponents].sort((left, right) => {
    const rank = (component: string) => component === "mpv-tools" ? 0 : component === "ffmpeg-tools" ? 1 : component === "download-tools" ? 2 : 3;
    return rank(left.component) - rank(right.component) || left.component.localeCompare(right.component);
  }));

  const componentLabels: Record<string, { name: string; description: string; icon: string }> = {
    "mpv-tools": {
      name: "本地视频播放器",
      description: "直接读取本地视频，复用播放器窗口并支持证据时间戳跳转",
      icon: "play",
    },
    "ffmpeg-tools": {
      name: "媒体分析工具",
      description: "用于媒体探测和内容分析，不承担笔记页面的交互播放",
      icon: "video",
    },
    "download-tools": {
      name: "在线视频下载工具",
      description: "用于下载受支持的公开视频来源",
      icon: "download",
    },
  };

  const capabilityLabels: Record<string, string> = {
    "video-playback": "本地播放",
    "timestamp-seek": "时间戳跳转",
    ffmpeg: "媒体分析",
    download: "视频下载",
  };

  function componentLabel(component: RuntimeComponent) {
    return componentLabels[component.component] ?? {
      name: component.component,
      description: component.description || "本机运行时组件",
      icon: "package",
    };
  }

  function componentCapabilities(component: RuntimeComponent) {
    return (component.provides ?? []).map((capability) => capabilityLabels[capability] ?? capability).join(" / ") || "运行时支持";
  }

  interface ComponentDownloadProgress {
    component: string;
    downloaded_bytes: number;
    total_bytes: number;
    stage: string;
  }

  $effect(() => {
    const promise = onEngineEvent<ComponentDownloadProgress>("component.download-progress", (payload) => {
      downloadProgress = {
        ...downloadProgress,
        [payload.component]: Math.round((payload.downloaded_bytes / payload.total_bytes) * 100),
      };
    });
    return () => { promise.then((fn) => fn()); };
  });

  function showToast(msg: string, type: "success" | "error" | "info" = "info") {
    toast = { msg, type };
    setTimeout(() => (toast = null), 3500);
  }

  function markDirty() { dirty = true; }

  async function loadAll() {
    loading = true;
    dirty = false;
    try {
      const [s, provs, tmpls] = await Promise.all([
        engineCall<SettingsBag>("settings.get"),
        engineCall<ProviderProfile[]>("settings.providers.list"),
        engineCall<TemplateInfo[]>("settings.templates.list"),
      ]);
      settings = {
        ...s,
        vault_path: s.vault_path ?? "",
        compile_concurrency: s.compile_concurrency ?? 0,
        effective_compile_concurrency: s.effective_compile_concurrency || 2,
      };
      providers = provs;
      templates = tmpls;
      refreshStorageStatus();
      refreshComponents();
      runDoctor();
    } catch (e: any) {
      showToast(`加载设置失败：${e?.message ?? e}`, "error");
    } finally { loading = false; }
  }

  async function handleSave() {
    saving = true;
    try {
      await engineCall("settings.update", {
        patches: {
          output_dir: settings.output_dir,
          vault_path: settings.vault_path,
          template: settings.template,
          bilibili_cookie_file: settings.bilibili_cookie_file,
          compile_concurrency: settings.compile_concurrency,
        },
      });
      dirty = false;
      settings.effective_compile_concurrency = settings.compile_concurrency || 2;
      showToast("设置已保存，任务并发上限已生效", "success");
    } catch (e: any) { showToast(`保存失败：${e?.message ?? e}`, "error"); }
    finally { saving = false; }
  }

  function openAddProvider() {
    editingProviderName = null;
    providerForm = emptyProviderForm();
    providerModelOptions = [];
    showProviderModal = true;
  }

  function openEditProvider(p: ProviderProfile) {
    editingProviderName = p.name;
    providerForm = {
      name: p.name,
      provider: p.provider === "llama_cpp" ? "openai_compat" : p.provider,
      api_key: "",
      base_url: p.base_url,
      model: p.model,
      vision_model: p.vision_model ?? p.model,
      video_input: p.video_input ?? false,
    };
    providerModelOptions = [];
    showProviderModal = true;
  }

  function closeProviderModal() {
    showProviderModal = false;
    providerForm = emptyProviderForm();
    providerModelOptions = [];
    editingProviderName = null;
  }

  function providerDiscoveryName() {
    if (!editingProviderName) return undefined;
    const saved = providers.find((item) => item.name === editingProviderName);
    if (!saved) return undefined;
    if (providerForm.api_key.trim()) return undefined;
    if ((providerForm.provider || "").trim() !== (saved.provider || "").trim()) return undefined;
    if ((providerForm.base_url || "").trim() !== (saved.base_url || "").trim()) return undefined;
    return editingProviderName;
  }

  async function discoverProviderModels() {
    discoveringProviderModels = true;
    try {
      providerModelOptions = await engineCall<string[]>("settings.providers.models", {
        name: providerDiscoveryName(),
        provider: providerForm.provider,
        base_url: providerForm.base_url,
        api_key: providerForm.api_key || undefined,
        model: providerForm.model,
        vision_model: providerForm.vision_model,
      });
      showToast(`读取到 ${providerModelOptions.length} 个模型`, "success");
    } catch (e: any) {
      providerModelOptions = [];
      showToast(`读取模型列表失败：${e?.message ?? e}`, "error");
    } finally {
      discoveringProviderModels = false;
    }
  }

  function chooseProviderModel(event: Event, field: "model" | "vision_model") {
    const select = event.currentTarget as HTMLSelectElement;
    if (!select.value) return;
    providerForm[field] = select.value;
    select.value = "";
  }

  async function saveProvider() {
    if (!providerForm.name.trim()) { showToast("供应商名称不能为空", "error"); return; }
    if (!providerForm.model.trim()) { showToast("文本模型不能为空", "error"); return; }
    providerSaving = true;
    try {
      if (editingProviderName) {
        await engineCall("settings.providers.update", {
          name: editingProviderName,
          provider: providerForm.provider,
          base_url: providerForm.base_url,
          model: providerForm.model,
          vision_model: providerForm.vision_model,
          video_input: providerForm.video_input,
        });
      } else {
        await engineCall("settings.providers.create", {
          name: providerForm.name,
          provider: providerForm.provider,
          api_key: providerForm.api_key || undefined,
          base_url: providerForm.base_url,
          model: providerForm.model,
          vision_model: providerForm.vision_model,
          video_input: providerForm.video_input,
        });
      }
      if (providerForm.api_key) {
        await engineCall("settings.secret.set", { provider: editingProviderName ?? providerForm.name, key: providerForm.api_key });
      }
      showToast(editingProviderName ? "供应商配置已更新" : "供应商已创建", "success");
      closeProviderModal();
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) { showToast(`保存供应商失败：${e?.message ?? e}`, "error"); }
    finally { providerSaving = false; }
  }

  async function deleteProvider(name: string) {
    if (!confirm(`确定要删除供应商“${name}”吗？`)) return;
    try {
      await engineCall("settings.providers.delete", { name });
      showToast(`供应商“${name}”已删除`, "success");
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) { showToast(`删除失败：${e?.message ?? e}`, "error"); }
  }

  async function setActiveProvider(name: string) {
    try {
      await engineCall("settings.providers.set_active", { name });
      settings.active_provider = name;
      providers = providers.map((provider) => ({ ...provider, active: provider.name === name }));
      showToast(`已将“${name}”设为活动供应商`, "success");
    } catch (e: any) { showToast(`激活供应商失败：${e?.message ?? e}`, "error"); }
  }

  async function deleteApiKey(name: string) {
    if (!confirm(`确定删除“${name}”的 API Key 吗？`)) return;
    try {
      await engineCall("settings.secret.delete", { provider: name });
      showToast("API Key 已删除", "success");
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) { showToast(`删除 API Key 失败：${e?.message ?? e}`, "error"); }
  }

  async function testConnection(p: ProviderProfile) {
    testingProvider = p.name;
    try {
      const result: any = await engineCall("settings.providers.test", { name: p.name, provider: p.provider, base_url: p.base_url, model: p.model });
      const cacheNote = result?.capability_cache_saved === false ? "；能力缓存未写入" : "";
      showToast(result?.success ? `连接成功：${result.message ?? "服务可用"}${cacheNote}` : `连接失败：${result?.message ?? "未知错误"}${cacheNote}`, result?.success ? "success" : "error");
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) { showToast(`测试连接异常：${e?.message ?? e}`, "error"); }
    finally { testingProvider = null; }
  }

  async function runDoctor() {
    doctorRunning = true;
    checkResults = [];
    try {
      const results: CheckResult[] = await engineCall("doctor.run");
      checkResults = results;
    } catch (e: any) { /* silently degrade */ }
    finally { doctorRunning = false; }
  }

  async function bundleDiagnostics() {
    bundlingDiagnostics = true;
    try {
      const path: string = await engineCall("diagnostics.bundle");
      showToast(`诊断报告已生成：${path}`, "success");
    } catch (e: any) { showToast(`生成诊断报告失败：${e?.message ?? e}`, "error"); }
    finally { bundlingDiagnostics = false; }
  }

  function formatBytes(bytes = 0) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
  }

  async function refreshStorageStatus() {
    storageLoading = true;
    try {
      storageStatus = await engineCall<StorageStatus>("storage.status");
      refreshGraphCacheInfo();
    } catch (e: any) {
      showToast(`读取存储状态失败：${e?.message ?? e}`, "error");
    } finally {
      storageLoading = false;
    }
  }

  function componentStatusLabel(component: RuntimeComponent) {
    if (component.installed && component.status === "ok") return "已安装";
    if (component.installed) return "需修复";
    return "未安装";
  }

  function componentStatusType(component: RuntimeComponent) {
    if (component.installed && component.status === "ok") return "pass";
    if (component.installed) return "warn";
    return "pending";
  }

  async function openExternalUrl(url: string) {
    try {
      await engineCall("system.open_url", { url });
    } catch (e: any) {
      showToast(`打开链接失败：${e?.message ?? e}`, "error");
    }
  }

  async function refreshComponents() {
    componentsLoading = true;
    try {
      runtimeComponents = await engineCall<RuntimeComponent[]>("components.list");
    } catch (e: any) {
      showToast(`读取插件状态失败：${e?.message ?? e}`, "error");
    } finally {
      componentsLoading = false;
    }
  }

  async function checkAllUpdates() {
    checkingUpdates = true;
    try {
      const results = await engineCall<Array<{ component: string; installed_version: string; latest_version: string; update_available: boolean }>>("components.check_updates");
      // Merge update info into runtimeComponents
      const updateMap = new Map(results.map(r => [r.component, r]));
      runtimeComponents = runtimeComponents.map(c => {
        const u = updateMap.get(c.component);
        return u ? { ...c, latest_version: u.latest_version, update_available: u.update_available } : c;
      });
      const updatesAvailable = results.filter(r => r.update_available);
      if (updatesAvailable.length > 0) {
        showToast(`发现 ${updatesAvailable.length} 个组件与当前应用内置版本不一致：${updatesAvailable.map(u => u.component).join(", ")}`, "info");
      } else {
        showToast("所有组件均与当前应用内置版本一致", "success");
      }
    } catch (e: any) {
      showToast(`检查组件版本失败：${e?.message ?? e}`, "error");
    } finally {
      checkingUpdates = false;
    }
  }

  async function installComponent(component: RuntimeComponent, mode: "install" | "update" | "repair" = "install") {
    const actionLabel = mode === "update" ? "同步" : mode === "repair" ? "修复" : "安装";
    componentAction = `install:${component.component}`;
    try {
      await engineCall("components.install", { component: component.component });
      showToast(`${component.component} ${actionLabel}完成`, "success");
      await refreshComponents();
    } catch (e: any) {
      showToast(`${actionLabel} ${component.component} 失败：${e?.message ?? e}`, "error");
    } finally {
      componentAction = null;
      // Clear progress bar when done
      const { [component.component]: _, ...rest } = downloadProgress;
      downloadProgress = rest;
    }
  }

  async function verifyComponent(component: RuntimeComponent) {
    componentAction = `verify:${component.component}`;
    try {
      const result = await engineCall<{ ok: boolean }>("components.verify", { component: component.component });
      showToast(result.ok ? `${component.component} 验证通过` : `${component.component} 验证未通过`, result.ok ? "success" : "error");
      await refreshComponents();
    } catch (e: any) {
      showToast(`验证 ${component.component} 失败：${e?.message ?? e}`, "error");
    } finally {
      componentAction = null;
    }
  }

  async function removeComponent(component: RuntimeComponent) {
    if (!confirm(`确定卸载 ${component.component} 吗？`)) return;
    componentAction = `remove:${component.component}`;
    try {
      await engineCall("components.remove", { component: component.component });
      showToast(`${component.component} 已卸载`, "success");
      await refreshComponents();
    } catch (e: any) {
      showToast(`卸载 ${component.component} 失败：${e?.message ?? e}`, "error");
    } finally {
      componentAction = null;
    }
  }

  async function cleanupOrphanWorkspaces() {
    storageLoading = true;
    try {
      const result = await engineCall<{ removed: number }>("storage.cleanup_orphans", { min_age_hours: 0 });
      showToast(`已清理 ${result.removed} 个孤儿任务缓存`, "success");
      await refreshStorageStatus();
    } catch (e: any) {
      showToast(`清理孤儿任务缓存失败：${e?.message ?? e}`, "error");
    } finally {
      storageLoading = false;
    }
  }

  async function cleanupCompletedWorkspaces() {
    storageLoading = true;
    try {
      const result = await engineCall<{ removed: number }>("storage.cleanup_completed");
      showToast(`已清理 ${result.removed} 个已完成任务缓存`, "success");
      await refreshStorageStatus();
    } catch (e: any) {
      showToast(`清理已完成任务缓存失败：${e?.message ?? e}`, "error");
    } finally {
      storageLoading = false;
    }
  }

  async function cleanupCapsules() {
    if (!confirm("确定清理所有编译缓存吗？已导出的笔记不受影响，但历史记录将无法回放。")) return;
    storageLoading = true;
    try {
      const result = await engineCall<{ removed: number }>("storage.cleanup_capsules");
      showToast(`已清理编译缓存（${result.removed} 个源）`, "success");
      await refreshStorageStatus();
    } catch (e: any) {
      showToast(`清理编译缓存失败：${e?.message ?? e}`, "error");
    } finally {
      storageLoading = false;
    }
  }

  async function cleanupPlaybackCache() {
    if (!confirm("确定清理视频播放缓存吗？下次播放 HEVC 视频时会重新转码。")) return;
    storageLoading = true;
    try {
      const result = await engineCall<{ removed: { dirs: number; files: number }; size: number }>("storage.cleanup_playback_cache");
      const sizeStr = formatBytes(result.size ?? 0);
      showToast(`已清理视频播放缓存（${result.removed?.files ?? 0} 个文件，${sizeStr}）`, "success");
      await refreshStorageStatus();
    } catch (e: any) {
      showToast(`清理视频播放缓存失败：${e?.message ?? e}`, "error");
    } finally {
      storageLoading = false;
    }
  }

  $effect(() => { loadAll(); });
</script>

<div class="page settings-page">
  <PageHeader
    eyebrow="应用偏好设置"
    title="设置"
    description="管理转录模型、AI 供应商、笔记模板和本地运行环境。"
    icon="settings"
  >
    {#snippet actions()}
      {#if dirty}<span class="unsaved-badge"><span></span>有未保存的更改</span>{/if}
      <button class="btn btn-primary" onclick={handleSave} disabled={saving || !dirty}><Icon name="save" size={15} />{saving ? "正在保存" : "保存设置"}</button>
    {/snippet}
  </PageHeader>

  {#if loading}
    <section class="settings-loading surface"><span class="loading-ring"></span><h2>正在加载设置</h2><p>读取模型、供应商与模板配置…</p></section>
  {:else}
    <div class="settings-shell surface">
      <aside class="settings-nav">
        <div class="settings-nav-title">设置分类</div>
        {#each tabs as tab}
          <button class:active={activeTab === tab.id} onclick={() => activeTab = tab.id}>
            <span class="tab-icon"><Icon name={tab.icon} size={17} /></span>
            <span class="tab-copy"><strong>{tab.label}</strong><small>{tab.hint}</small></span>
            <span class="tab-chevron"><Icon name="chevron-right" size={14} /></span>
          </button>
        {/each}
        <div class="security-note"><Icon name="shield" size={17} /><div><strong>本地安全存储</strong><p>敏感凭据不会写入任务快照或诊断日志。</p></div></div>
      </aside>

      <main class="settings-content">
        {#if activeTab === "general"}
          <SettingsGeneral
            bind:settings
            onMarkDirty={markDirty}
            onOpenExternalUrl={openExternalUrl}
          />

        {:else if activeTab === "providers"}
          <ProvidersPanel
            {providers}
            activeProvider={settings.active_provider}
            {testingProvider}
            bind:providerSearch
            onSetActive={setActiveProvider}
            onTestConnection={testConnection}
            onOpenAddProvider={openAddProvider}
            onOpenEditProvider={openEditProvider}
            onDeleteProvider={deleteProvider}
            onDeleteApiKey={deleteApiKey}
          />

        {:else if activeTab === "templates"}
          <section class="settings-pane">
            <div class="pane-head"><div><span>NOTE TEMPLATES</span><h2>笔记模板</h2><p>选择 AI 输出的结构、章节侧重点和适用场景。</p></div></div>

            {#if selectedTemplate}
              <div class="selected-template-banner">
                <div class="template-hero-icon"><Icon name="template" size={24} /></div>
                <div><span>当前默认模板</span><h3>{selectedTemplate.name}</h3><p>{selectedTemplate.description}</p></div>
                <StatusPill status="completed" label="正在使用" />
              </div>
            {/if}

            {#if templates.length === 0}
              <EmptyState icon="template" title="暂无可用模板" description="请确认内置模板目录完整，或在诊断页面检查模板资源。" />
            {:else}
              <div class="template-grid">
                {#each templates as template}
                  <button class="template-card" class:selected={settings.template === template.id} onclick={() => { settings.template = template.id; markDirty(); }}>
                    <span class="template-card-icon"><Icon name={template.id.includes("code") ? "bot" : template.id.includes("meeting") ? "tasks" : template.id.includes("interview") ? "audio" : "file-text"} size={20} /></span>
                    <span class="template-card-copy"><span>{template.id}</span><strong>{template.name}</strong><p>{template.description || "结构化视频笔记模板"}</p></span>
                    <span class="template-selected">{#if settings.template === template.id}<Icon name="check" size={14} />{/if}</span>
                  </button>
                {/each}
              </div>
            {/if}
          </section>

        {:else if activeTab === "plugins"}
          <section class="settings-pane">
            <div class="pane-head actions-head">
              <div><span>PLUGINS</span><h2>插件</h2><p>按需安装运行时工具组件；主程序保持轻量，重依赖放在本机 runtime。</p></div>
              <div style="display:flex;gap:8px;">
                <button class="btn btn-secondary" type="button" onclick={checkAllUpdates} disabled={checkingUpdates || componentsLoading}><Icon name="refresh" size={15} />{checkingUpdates ? "检查中..." : "检查组件版本"}</button>
                <button class="btn btn-secondary" type="button" onclick={refreshComponents} disabled={componentsLoading}><Icon name="list" size={15} />{componentsLoading ? "刷新中" : "刷新"}</button>
              </div>
            </div>

            {#if checkResults.length > 0}
              <div class="plugin-system-check">
                <div class="system-check-icon"><Icon name="cpu" size={16} /></div>
                <div class="system-check-list">
                  {#each checkResults as check}
                    <span class="system-check-item">
                      <StatusPill status={check.status == "pass" ? "pass" : check.status == "warn" ? "warn" : "fail"} label={check.status === "pass" ? "可用" : check.status === "warn" ? "警告" : "缺失"} />
                      <strong>{check.name}</strong>
                      <small>{check.detail}</small>
                    </span>
                  {/each}
                </div>
                <button class="btn btn-secondary btn-sm" onclick={runDoctor} disabled={doctorRunning}><Icon name="refresh" size={13} />刷新检测</button>
              </div>
            {/if}

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="package" size={18} /></div><div><h3>运行组件</h3><p>按需安装本地播放、时间戳跳转、媒体分析与视频下载能力。</p></div></div>
              {#if componentsLoading && toolComponents.length === 0}
                <div class="plugin-empty-state"><span class="loading-ring compact"></span><div><strong>正在读取插件状态</strong><small>检查本机 runtime 组件清单与已安装目录。</small></div></div>
              {:else if toolComponents.length === 0}
                <div class="plugin-empty-state"><Icon name="package" size={20} /><div><strong>未找到运行组件清单</strong><small>请检查应用内置组件清单是否完整。</small></div></div>
              {:else}
                <div class="plugin-grid">
                  {#each toolComponents as component}
                    <article class="plugin-card" class:installed={component.installed} data-component={component.component}>
                      <div class="plugin-card-head">
                        <div class="plugin-icon"><Icon name={componentLabel(component).icon} size={20} /></div>
                        <div class="plugin-title"><strong>{componentLabel(component).name}</strong><small>{component.component} · {componentLabel(component).description}</small></div>
                        <StatusPill status={componentStatusType(component)} label={componentStatusLabel(component)} />
                      </div>
                      <div class="plugin-meta">
                        <div><span>工具版本</span><strong>{component.installed_version || "未安装"}</strong></div>
                        <div><span>体积</span><strong>{component.size_mb ? `${component.size_mb} MB` : "未知"}</strong></div>
                        <div><span>能力</span><strong>{componentCapabilities(component)}</strong></div>
                      </div>
                      {#if downloadProgress[component.component] !== undefined}
                        <div class="plugin-download-progress">
                          <div class="progress-bar"><div class="progress-fill" style="width: {downloadProgress[component.component]}%"></div></div>
                          <span class="progress-label">{downloadProgress[component.component]}%</span>
                        </div>
                      {/if}
                      <div class="plugin-path"><span>安装位置</span><code>{component.component_path || "尚未安装"}</code></div>
                      {#if component.component === "mpv-tools"}
                        <div class="plugin-warning plugin-note"><Icon name="play" size={14} /><span>笔记视频播放与证据时间戳跳转依赖此组件。</span></div>
                      {/if}
                      {#if component.missing_files?.length}
                        <div class="plugin-warning"><Icon name="alert" size={14} /><span>缺少 {component.missing_files.length} 个组件文件，建议重新安装。</span></div>
                      {:else if component.installed && component.status !== "ok"}
                        <div class="plugin-warning"><Icon name="alert" size={14} /><span>组件完整性校验未通过，请重新安装修复。</span></div>
                      {/if}
                      <div class="plugin-actions">
                        {#if component.installed}
                          {#if component.status !== "ok" && component.downloadable}
                            <button class="btn btn-primary" type="button" onclick={() => installComponent(component, "repair")} disabled={componentAction !== null}><Icon name="refresh" size={14} />{componentAction === `install:${component.component}` ? "修复中" : "重新安装修复"}</button>
                          {/if}
                          <button class="btn btn-secondary" type="button" onclick={() => verifyComponent(component)} disabled={componentAction !== null}><Icon name="check" size={14} />{componentAction === `verify:${component.component}` ? "验证中" : "验证"}</button>
                          {#if component.status === "ok" && component.update_available}
                            <button class="btn btn-primary" type="button" onclick={() => installComponent(component, "update")} disabled={componentAction !== null}><Icon name="refresh" size={14} />{componentAction === `install:${component.component}` ? "同步中" : "同步内置版本"}</button>
                          {/if}
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else if component.downloadable}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component, "install")} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
                        {/if}
                      </div>
                    </article>
                  {/each}
                </div>
              {/if}
            </div>

          </section>

        {:else if activeTab === "storage"}
          <section class="settings-pane">
            <div class="pane-head actions-head">
              <div><span>STORAGE</span><h2>存储管理</h2><p>各分类的磁盘占用、路径与清理操作。</p></div>
              <button class="btn btn-secondary" type="button" onclick={refreshStorageStatus} disabled={storageLoading}><Icon name="refresh" size={15} />{storageLoading ? "刷新中" : "刷新"}</button>
            </div>

            {#if storageStatus}
              <!-- ── Total usage bar ── -->
              <div class="storage-total-bar">
                <div class="storage-total-head">
                  <span class="storage-total-label">总占用</span>
                  <strong>{formatBytes(totalStorageBytes)}</strong>
                  <span class="storage-total-count">{storageCategories.filter(c => c.bytes > 0).length} 个分类</span>
                </div>
                <div class="storage-total-track">
                  {#each storageCategories as cat}
                    {#if cat.bytes > 0}
                      <div
                        class="storage-total-seg"
                        class:safe={cat.danger === "safe"}
                        class:caution={cat.danger === "caution"}
                        class:danger={cat.danger === "danger"}
                        style="width: {totalStorageBytes > 0 ? (cat.bytes / totalStorageBytes * 100).toFixed(1) : 0}%"
                        title="{cat.label}: {formatBytes(cat.bytes)}"
                      ></div>
                    {/if}
                  {/each}
                </div>
                <div class="storage-total-legend">
                  {#each storageCategories as cat}
                    {#if cat.bytes > 0}
                      <span><span class="legend-dot" class:safe={cat.danger === "safe"} class:caution={cat.danger === "caution"} class:danger={cat.danger === "danger"}></span>{cat.label}</span>
                    {/if}
                  {/each}
                </div>
              </div>

              <!-- ── Category cards ── -->
              <div class="storage-cards">
                {#each storageCategories as cat}
                  <div class="storage-card" class:safe={cat.danger === "safe"} class:caution={cat.danger === "caution"} class:danger={cat.danger === "danger"}>
                    <div class="storage-card-icon"><Icon name={cat.icon} size={20} /></div>
                    <div class="storage-card-body">
                      <div class="storage-card-head">
                        <strong>{cat.label}</strong>
                        <span class="storage-card-size">{formatBytes(cat.bytes)}</span>
                      </div>
                      <div class="storage-card-track">
                        <div class="storage-card-fill" style="width: {totalStorageBytes > 0 ? (cat.bytes / totalStorageBytes * 100).toFixed(1) : 0}%"></div>
                      </div>
                      <div class="storage-card-meta">
                        <code title={cat.path}>{cat.path || "—"}</code>
                        <span class="storage-card-hint">{cat.hint}</span>
                      </div>
                    </div>
                    {#if cat.action}
                      <button
                        class="btn btn-sm"
                        class:btn-secondary={cat.danger === "safe"}
                        class:btn-warning={cat.danger === "caution" || cat.danger === "danger"}
                        onclick={() => {
                          if (cat.confirmMsg && !confirm(cat.confirmMsg)) return;
                          cat.action!();
                        }}
                        disabled={storageLoading}
                      >
                        <Icon name={cat.danger === "safe" ? "trash" : "alert"} size={13} />
                        {cat.actionLabel}
                      </button>
                    {/if}
                  </div>
                {/each}
              </div>

              <!-- ── Batch actions ── -->
              <div class="storage-batch">
                <div class="group-head"><div class="group-icon"><Icon name="zap" size={18} /></div><div><h3>批量操作</h3><p>一次清理所有安全级别的缓存。</p></div></div>
                <div class="storage-batch-actions">
                  <button class="btn btn-secondary" onclick={cleanupOrphanWorkspaces} disabled={storageLoading}><Icon name="trash" size={14} />清理失效任务记录</button>
                  <button class="btn btn-warning" onclick={cleanupCapsules} disabled={storageLoading}><Icon name="alert" size={14} />清空编译缓存</button>
                  <button class="btn btn-secondary" onclick={clearGraphCache} disabled={storageLoading}><Icon name="refresh" size={14} />清理笔记图谱缓存</button>
                </div>
              </div>
            {:else}
              <div class="storage-empty-state"><span class="loading-ring compact"></span><div><strong>尚未读取存储状态</strong><small>点击刷新以查看本机缓存、工作区和导出目录占用。</small></div></div>
            {/if}
          </section>

        {:else if activeTab === "diagnostics"}
          <section class="settings-pane">
            <div class="pane-head actions-head">
              <div><span>SYSTEM HEALTH</span><h2>系统诊断</h2><p>检测 FFmpeg、在线下载、AI Provider 和本地存储状态。</p></div>
              <div class="diagnostic-actions"><button class="btn btn-secondary" onclick={bundleDiagnostics} disabled={bundlingDiagnostics}><Icon name="download" size={15} />{bundlingDiagnostics ? "生成中" : "导出诊断报告"}</button><button class="btn btn-primary" onclick={runDoctor} disabled={doctorRunning}><Icon name="stethoscope" size={15} />{doctorRunning ? "检查中" : "运行环境检查"}</button></div>
            </div>

            <div class="health-hero">
              <div class="health-ring" class:healthy={checkResults.length > 0 && passedChecks === checkResults.length}>
                {#if doctorRunning}<span class="loading-ring compact"></span>{:else}<Icon name={checkResults.length === 0 ? "stethoscope" : passedChecks === checkResults.length ? "check" : "alert"} size={30} />{/if}
              </div>
              <div><span>ENVIRONMENT STATUS</span><h3>{doctorRunning ? "正在检查运行环境" : checkResults.length === 0 ? "尚未运行环境检查" : `${passedChecks}/${checkResults.length} 项检查通过`}</h3><p>{doctorRunning ? "逐项验证本地依赖与服务可用性…" : checkResults.length === 0 ? "建议首次安装或更新后运行一次完整检查。" : "检查结果仅反映当前设备与已安装运行时。"}</p></div>
            </div>

            {#if checkResults.length > 0}
              <div class="check-list">
                <div class="check-head"><span>检查项</span><span>状态</span><span>详细信息</span></div>
                {#each checkResults as result}
                  <div class="check-row"><span class="check-name"><span class="check-icon status-{result.status}"><Icon name={result.status === "pass" ? "check" : result.status === "warn" ? "info" : "alert"} size={14} /></span><strong>{result.name}</strong></span><StatusPill status={result.status} /><p>{result.detail}</p></div>
                {/each}
              </div>
            {:else}
              <div class="diagnostic-placeholder"><EmptyState icon="stethoscope" title="准备检查系统环境" description="运行检查后，这里会展示每项依赖的状态、版本和修复建议。" compact /></div>
            {/if}
          </section>
        {/if}
      </main>
    </div>
  {/if}

  {#if dirty}
    <div class="floating-save-bar"><div><span class="save-dot"></span><p><strong>设置尚未保存</strong><small>离开应用前请保存更改。</small></p></div><button class="btn btn-secondary btn-sm" onclick={loadAll}>放弃更改</button><button class="btn btn-primary btn-sm" onclick={handleSave} disabled={saving}><Icon name="save" size={14} />{saving ? "保存中" : "保存更改"}</button></div>
  {/if}

  {#if toast}
    <div class="toast-pro {toast.type}"><Icon name={toast.type === "success" ? "check" : toast.type === "error" ? "alert" : "info"} size={16} /><span>{toast.msg}</span></div>
  {/if}
</div>

{#if showProviderModal}
  <ProviderFormDialog
    show={showProviderModal}
    {editingProviderName}
    bind:providerForm
    {providerSaving}
    {providerModelOptions}
    {discoveringProviderModels}
    onClose={closeProviderModal}
    onSave={saveProvider}
    onDiscoverModels={discoverProviderModels}
    onChooseModel={chooseProviderModel}
  />
{/if}

<style>
.settings-page { max-width: 1400px; padding-bottom: 80px; }
  .unsaved-badge { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 6px 10px; border-radius: 9px; color: var(--warning-color); background: var(--warning-soft); font-size: 13px; font-weight: 650; }
  .unsaved-badge span { width: 6px; height: 6px; border-radius: 50%; background: var(--warning-color); }
  .settings-loading { min-height: 480px; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  .loading-ring { width: 34px; height: 34px; margin-bottom: 13px; border: 3px solid var(--bg-progress); border-top-color: var(--accent-color); border-radius: 50%; animation: spin .8s linear infinite; }
  .loading-ring.compact { width: 28px; height: 28px; margin: 0; }
  .settings-loading h2 { font-size: 17px; }
  .settings-loading p { margin-top: 5px; color: var(--text-secondary); font-size: 13px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .settings-shell { display: grid; grid-template-columns: 245px minmax(0,1fr); min-height: 650px; overflow: hidden; }
  .settings-nav { display: flex; flex-direction: column; padding: 16px 11px; border-right: 1px solid var(--border-color); background: var(--bg-subtle); }
  .settings-nav-title { padding: 2px 11px 9px; color: var(--text-tertiary); font-size: 12px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
  .settings-nav > button { display: grid; grid-template-columns: 34px minmax(0,1fr) 15px; align-items: center; gap: 9px; width: 100%; margin-bottom: 4px; padding: 9px; border: 1px solid transparent; border-radius: 11px; color: var(--text-secondary); background: transparent; cursor: pointer; text-align: left; transition: background .14s, color .14s, border-color .14s; }
  .settings-nav > button:hover { color: var(--text-primary); background: var(--bg-hover); }
  .settings-nav > button.active { color: var(--accent-color); border-color: color-mix(in srgb, var(--accent-color) 20%, var(--border-color)); background: var(--accent-faint); box-shadow: inset 3px 0 0 var(--accent-color); }
  .tab-icon { display: grid; place-items: center; width: 33px; height: 33px; border-radius: 10px; color: var(--text-secondary); background: var(--bg-muted); }
  .active .tab-icon { color: var(--accent-color); background: var(--accent-soft); }
  .tab-copy { display: flex; min-width: 0; flex-direction: column; }
  .tab-copy strong { font-size: 14px; }
  .tab-copy small { margin-top: 2px; color: var(--text-tertiary); font-size: 12px; }
  .tab-chevron { display: grid; place-items: center; }
  .security-note { display: flex; align-items: flex-start; gap: 8px; margin: auto 3px 0; padding: 11px; border: 1px solid color-mix(in srgb, var(--success-color) 16%, var(--border-color)); border-radius: 11px; color: var(--success-color); background: var(--success-soft); }
  .security-note div { display: flex; flex-direction: column; }
  .security-note strong { font-size: 13px; }
  .security-note p { margin-top: 3px; color: var(--text-secondary); font-size: 11px; line-height: 1.5; }

  .settings-content { min-width: 0; background: var(--bg-card); }
  .settings-pane { padding: 24px 26px 32px; }
  .pane-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 20px; border-bottom: 1px solid var(--border-color); }
  .pane-head > div:first-child { display: flex; flex-direction: column; }
  .pane-head > div:first-child > span { color: var(--accent-color); font-size: 12px; font-weight: 800; letter-spacing: .12em; }
  .pane-head h2 { margin-top: 3px; font-size: 22px; letter-spacing: -.02em; }
  .pane-head p { margin-top: 5px; color: var(--text-secondary); font-size: 13px; }
  .actions-head { align-items: center; }
  .setting-group { padding: 22px 0; border-bottom: 1px solid var(--border-color); }
  .setting-group:last-child { border-bottom: 0; }
  .group-head { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .group-head.with-action { display: grid; grid-template-columns: 38px minmax(0,1fr) auto; align-items: center; }
  .group-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: max-content; }
  .group-actions .btn { min-height: 40px; }
  .group-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .group-head > div:nth-child(2) { display: flex; flex-direction: column; }
  .group-head h3 { font-size: 14px; }
  .group-head p { margin-top: 3px; color: var(--text-secondary); font-size: 12px; }
  .provider-type-warning { display: flex; align-items: flex-start; gap: 8px; margin-top: 10px; padding: 10px 12px; border-radius: 10px; color: var(--warning-color); background: var(--warning-soft); border: 1px solid color-mix(in srgb, var(--warning-color) 25%, var(--border-color)); font-size: 12px; line-height: 1.55; }

  .selected-template-banner { display: grid; grid-template-columns: 54px minmax(0,1fr) auto; align-items: center; gap: 13px; margin: 18px 0; padding: 15px; border: 1px solid color-mix(in srgb, var(--accent-color) 26%, var(--border-color)); border-radius: 14px; background: linear-gradient(145deg, var(--accent-faint), var(--bg-card)); }
  .template-hero-icon { display: grid; place-items: center; width: 54px; height: 54px; border-radius: 16px; color: var(--accent-color); background: var(--accent-soft); }
  .selected-template-banner > div:nth-child(2) { display: flex; min-width: 0; flex-direction: column; }
  .selected-template-banner > div:nth-child(2) > span { color: var(--accent-color); font-size: 11px; font-weight: 750; letter-spacing: .08em; }
  .selected-template-banner h3 { margin-top: 3px; font-size: 16px; }
  .selected-template-banner p { margin-top: 3px; color: var(--text-secondary); font-size: 12px; }
  .template-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; }
  .template-card { display: grid; grid-template-columns: 42px minmax(0,1fr) 22px; align-items: start; gap: 11px; min-height: 110px; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s, box-shadow .14s; }
  .template-card:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .template-card.selected { border-color: var(--accent-color); background: var(--accent-faint); box-shadow: 0 0 0 3px var(--accent-glow); }
  .template-card-icon { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 12px; color: var(--text-secondary); background: var(--bg-muted); }
  .selected .template-card-icon { color: var(--accent-color); background: var(--accent-soft); }
  .template-card-copy { display: flex; min-width: 0; flex-direction: column; }
  .template-card-copy > span { color: var(--text-tertiary); font-family: var(--font-mono); font-size: 11px; }
  .template-card-copy strong { margin-top: 4px; font-size: 14px; }
  .template-card-copy p { margin-top: 4px; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }
  .template-selected { display: grid; place-items: center; width: 21px; height: 21px; border: 1px solid var(--border-strong); border-radius: 50%; color: #fff; }
  .selected .template-selected { border-color: var(--accent-color); background: var(--accent-color); }

  .diagnostic-actions { display: flex; gap: 8px; }
  .health-hero { display: flex; align-items: center; gap: 15px; margin: 18px 0; padding: 17px; border: 1px solid var(--border-color); border-radius: 14px; background: var(--bg-subtle); }
  .health-ring { display: grid; place-items: center; width: 62px; height: 62px; flex: 0 0 auto; border: 5px solid var(--bg-progress); border-radius: 50%; color: var(--accent-color); background: var(--bg-card); }
  .health-ring.healthy { color: var(--success-color); border-color: color-mix(in srgb, var(--success-color) 30%, var(--bg-progress)); }
  .health-hero > div:last-child { display: flex; flex-direction: column; }
  .health-hero > div:last-child > span { color: var(--accent-color); font-size: 11px; font-weight: 800; letter-spacing: .1em; }
  .health-hero h3 { margin-top: 3px; font-size: 16px; }
  .health-hero p { margin-top: 4px; color: var(--text-secondary); font-size: 12px; }
  .check-list { overflow: hidden; border: 1px solid var(--border-color); border-radius: 12px; }
  .check-head, .check-row { display: grid; grid-template-columns: minmax(150px,.65fr) 80px minmax(220px,1fr); align-items: center; gap: 12px; }
  .check-head { padding: 9px 12px; color: var(--text-tertiary); background: var(--bg-subtle); font-size: 11px; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }
  .check-row { min-height: 52px; padding: 9px 12px; border-top: 1px solid var(--border-color); }
  .check-name { display: flex; align-items: center; gap: 8px; }
  .check-name strong { font-size: 13px; }
  .check-icon { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 9px; }
  .check-icon.status-pass { color: var(--success-color); background: var(--success-soft); }
  .check-icon.status-warn { color: var(--warning-color); background: var(--warning-soft); }
  .check-icon.status-fail { color: var(--danger-color); background: var(--danger-soft); }
  .check-row p { color: var(--text-secondary); font-size: 12px; line-height: 1.45; }
  .diagnostic-placeholder { border: 1px dashed var(--border-strong); border-radius: 13px; }

  .floating-save-bar { position: fixed; z-index: 50; right: 28px; bottom: 22px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; max-width: calc(100vw - 56px); padding: 9px 10px 9px 13px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-elevated); box-shadow: var(--shadow-md); }
  .floating-save-bar > div:first-child { display: flex; align-items: center; gap: 8px; margin-right: 5px; }
  .save-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--warning-color); }
  .floating-save-bar p { display: flex; flex-direction: column; }
  .floating-save-bar strong { font-size: 13px; }
  .floating-save-bar small { color: var(--text-tertiary); font-size: 11px; }

  @media (max-width: 1120px) {
    .settings-shell { grid-template-columns: minmax(190px, 210px) minmax(0,1fr); }
    .template-grid { grid-template-columns: 1fr; }
  }

  .settings-nav > button.active { box-shadow: none; }
  .pane-head { padding-bottom: 17px; }


  /* UI v7 — settings designed as a readable control center */
  .settings-page { max-width: 1240px; }
  .settings-shell { grid-template-columns: 250px minmax(0,1fr); min-height: 680px; border-radius: 16px; box-shadow: var(--shadow-sm); }
  .settings-nav { padding: 18px 12px; }
  .settings-nav-title { padding: 0 10px 12px; font-size: 12px; }
  .settings-nav > button { min-height: 52px; grid-template-columns: 36px minmax(0,1fr) 16px; padding: 9px 10px; }
  .tab-icon { width: 36px; height: 36px; }
  .tab-copy strong { font-size: 14px; }
  .tab-copy small { font-size: 11px; }
  .security-note { padding: 14px; }
  .security-note strong { font-size: 13px; }
  .security-note p { font-size: 11px; }
  .settings-pane { padding: 30px 34px 42px; }
  .pane-head h2 { font-size: 26px; }
  .pane-head p { margin-top: 7px; font-size: 13px; }
  .setting-group { padding: 26px 0; }
  .group-icon { width: 42px; height: 42px; }
  .group-head h3 { font-size: 16px; }
  .group-head p { font-size: 12px; }
  .template-card { min-height: 126px; padding: 16px; }
  .template-card-copy strong { font-size: 14px; }
  .template-card-copy p { font-size: 12px; }
  .check-row { min-height: 62px; padding: 12px 14px; }
  .check-name strong { font-size: 13px; }
  .check-row p { font-size: 12px; }

  @media (max-width: 920px) {
    .group-head.with-action { grid-template-columns: 38px minmax(0,1fr); }
    .group-actions { grid-column: 2; justify-content: flex-start; flex-wrap: wrap; min-width: 0; }
  }

  @media (max-width: 900px) {
    .settings-nav > button { min-height: 36px; padding: 4px 8px; }
    .tab-icon { width: 24px; height: 24px; }
    .settings-pane { padding: 16px 12px 20px; }
    .pane-head h2 { font-size: 18px; }
    .setting-group { padding: 16px 0; }
  }

  @media (max-width: 1180px) {
    .settings-shell { grid-template-columns: 220px minmax(0,1fr); }
    .settings-pane { padding: 24px; }
    .template-grid { grid-template-columns: 1fr; }
  }

  @media (max-width: 1050px) {
    .settings-shell { grid-template-columns: minmax(180px, 210px) minmax(0,1fr); }
  }

  .plugin-system-check { display: flex; align-items: center; gap: 12px; margin-top: 14px; padding: 12px 16px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .system-check-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto; border-radius: 8px; color: var(--accent-color); background: var(--accent-soft); }
  .system-check-list { display: flex; flex: 1; min-width: 0; flex-wrap: wrap; gap: 8px; }
  .system-check-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .system-check-item strong { color: var(--text-primary); }
  .system-check-item small { color: var(--text-tertiary); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
  .plugin-card { display: flex; min-width: 0; flex-direction: column; gap: 0; padding: 0; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-card); box-shadow: var(--shadow-xs); overflow: hidden; }
  .plugin-card.installed { border-color: color-mix(in srgb, var(--success-color) 25%, var(--border-color)); }
  .plugin-card-head { display: grid; grid-template-columns: 38px minmax(0,1fr) auto; align-items: center; gap: 10px; padding: 14px 14px 12px; }
  .plugin-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .plugin-title { display: flex; min-width: 0; flex-direction: column; }
  .plugin-title strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-title small { margin-top: 2px; overflow: hidden; color: var(--text-tertiary); font-size: 12px; line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-meta { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 14px 12px; }
  .plugin-meta > div { display: inline-flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: 7px; background: var(--bg-subtle); font-size: 12px; }
  .plugin-meta span { display: none; }
  .plugin-meta strong { overflow: hidden; color: var(--text-secondary); font-size: 12px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-path { display: flex; min-width: 0; flex-direction: column; gap: 0; margin: 0 14px; padding: 8px 10px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .plugin-path span { display: none; }
  .plugin-path code { overflow: hidden; color: var(--text-tertiary); font-family: var(--font-mono); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-download-progress { display: flex; align-items: center; gap: 8px; padding: 0 14px 10px; }
  .plugin-download-progress .progress-bar { flex: 1; height: 6px; border-radius: 3px; background: var(--bg-subtle); overflow: hidden; }
  .plugin-download-progress .progress-fill { height: 100%; border-radius: 3px; background: var(--accent-color); transition: width 0.3s ease; }
  .plugin-download-progress .progress-label { font-size: 11px; color: var(--text-tertiary); white-space: nowrap; min-width: 36px; text-align: right; }
  .plugin-warning { display: flex; align-items: center; gap: 6px; margin: 0 14px; padding: 7px 10px; border-radius: 8px; color: var(--warning-color); background: var(--warning-soft); font-size: 12px; }
  .plugin-note { color: var(--text-secondary); background: var(--bg-subtle); }
  .plugin-actions { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 14px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .plugin-actions .btn { min-height: 40px; padding: 6px 10px; font-size: 13px; }
  .plugin-empty-state { display: flex; align-items: center; gap: 12px; min-height: 78px; margin-top: 14px; padding: 14px 15px; border: 1px dashed var(--border-strong); border-radius: 12px; color: var(--text-secondary); background: var(--bg-subtle); }
  .plugin-empty-state > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  .plugin-empty-state strong { color: var(--text-primary); font-size: 14px; }
  .plugin-empty-state small { font-size: 12px; }
  .storage-empty-state { display: flex; align-items: center; gap: 12px; min-height: 72px; margin-top: 14px; padding: 14px 15px; border: 1px dashed var(--border-strong); border-radius: 12px; color: var(--text-secondary); background: var(--bg-subtle); }
  .storage-empty-state > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  .storage-empty-state strong { color: var(--text-primary); font-size: 14px; }
  .storage-empty-state small { font-size: 12px; }

  /* ── Storage redesign: total bar ── */
  .storage-total-bar { margin: 18px 0; padding: 16px 18px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-subtle); }
  .storage-total-head { display: flex; align-items: baseline; gap: 9px; margin-bottom: 8px; }
  .storage-total-label { color: var(--text-tertiary); font-size: 12px; font-weight: 650; }
  .storage-total-head strong { font-size: 22px; letter-spacing: -.02em; }
  .storage-total-count { margin-left: auto; color: var(--text-tertiary); font-size: 12px; }
  .storage-total-track { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--bg-progress); }
  .storage-total-seg { height: 100%; transition: width .3s ease; }
  .storage-total-seg.safe { background: var(--accent-color); }
  .storage-total-seg.caution { background: var(--warning-color); }
  .storage-total-seg.danger { background: var(--danger-color); }
  .storage-total-legend { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 8px; }
  .storage-total-legend > span { display: inline-flex; align-items: center; gap: 5px; color: var(--text-tertiary); font-size: 11px; }
  .legend-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
  .legend-dot.safe { background: var(--accent-color); }
  .legend-dot.caution { background: var(--warning-color); }
  .legend-dot.danger { background: var(--danger-color); }

  /* ── Storage category cards ── */
  .storage-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .storage-card { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: start; gap: 12px; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-card); box-shadow: var(--shadow-xs); transition: border-color .14s, box-shadow .14s; }
  .storage-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-sm); }
  .storage-card.caution { border-color: color-mix(in srgb, var(--warning-color) 28%, var(--border-color)); }
  .storage-card-icon { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); grid-row: 1 / 3; }
  .storage-card.caution .storage-card-icon { color: var(--warning-color); background: var(--warning-soft); }
  .storage-card-body { display: flex; min-width: 0; flex-direction: column; gap: 5px; }
  .storage-card-head { display: flex; align-items: baseline; gap: 8px; }
  .storage-card-head strong { font-size: 14px; }
  .storage-card-size { margin-left: auto; color: var(--text-primary); font-size: 14px; font-weight: 700; white-space: nowrap; }
  .storage-card-track { height: 4px; border-radius: 2px; background: var(--bg-progress); overflow: hidden; }
  .storage-card-fill { height: 100%; border-radius: 2px; background: var(--accent-color); transition: width .4s ease; }
  .storage-card.caution .storage-card-fill { background: var(--warning-color); }
  .storage-card-meta { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
  .storage-card-meta code { overflow: hidden; color: var(--text-tertiary); font-family: var(--font-mono); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .storage-card-hint { color: var(--text-tertiary); font-size: 11px; }
  .storage-card > .btn { align-self: center; grid-column: 3; grid-row: 1 / 3; white-space: nowrap; }

  /* ── Batch ops ── */
  .storage-batch { margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--border-color); }
  .storage-batch-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }

  .btn-warning { min-height: 40px; max-width: 100%; padding: 6px 11px; border: 1px solid color-mix(in srgb, var(--warning-color) 35%, var(--border-color)); border-radius: 9px; color: var(--warning-color); background: color-mix(in srgb, var(--warning-color) 8%, var(--bg-card)); cursor: pointer; font: inherit; font-size: 13px; font-weight: 650; display: inline-flex; align-items: center; justify-content: center; gap: 5px; transition: background .14s, border-color .14s, transform .14s; text-align: center; white-space: normal; overflow-wrap: anywhere; }
  .btn-warning:hover { background: color-mix(in srgb, var(--warning-color) 14%, var(--bg-subtle)); border-color: var(--warning-color); }

  /* Responsive: narrow settings */
  @media (max-width: 960px) {
    .settings-shell { grid-template-columns: 1fr; min-height: auto; }
    .settings-nav { flex-direction: row; flex-wrap: wrap; gap: 5px; padding: 10px 12px; border-right: 0; border-bottom: 1px solid var(--border-color); overflow-x: auto; }
    .settings-nav-title { display: none; }
    .settings-nav > button { grid-template-columns: 28px minmax(0, 1fr); min-height: 38px; flex: 0 0 auto; padding: 5px 9px; border-radius: 8px; }
    .settings-nav > button:last-child { margin-right: 0; }
    .settings-nav > button :last-child { display: none; }
    .tab-icon { width: 26px; height: 26px; border-radius: 7px; }
    .tab-copy strong { font-size: 13px; }
    .tab-copy small { display: none; }
    .security-note { display: none; }
    .template-grid { grid-template-columns: 1fr; }
    .plugin-grid { grid-template-columns: 1fr; }
    .storage-cards { grid-template-columns: 1fr; }
    .check-head, .check-row { grid-template-columns: minmax(100px, .5fr) 70px minmax(140px, 1fr); gap: 8px; }
    .settings-pane { padding: 18px 14px 24px; }
    .pane-head { flex-direction: column; gap: 10px; }
    .pane-head h2 { font-size: 20px; }
    .diagnostic-actions { flex-wrap: wrap; }
    .group-head.with-action { grid-template-columns: 1fr; gap: 10px; }
    .group-actions { grid-column: 1; justify-content: flex-start; }
  }

  /* Use the content width left after the app sidebar, not the window width. */
  @media (max-width: 1280px) {
    .settings-shell { grid-template-columns: minmax(0, 1fr); min-height: auto; }
    .settings-nav {
      flex-direction: row;
      flex-wrap: wrap;
      gap: 6px;
      padding: 10px 12px;
      border-right: 0;
      border-bottom: 1px solid var(--border-color);
      overflow: visible;
    }
    .settings-nav-title, .security-note { display: none; }
    .settings-nav > button {
      grid-template-columns: 32px minmax(0, 1fr);
      flex: 1 1 118px;
      min-width: 118px;
      min-height: 44px;
      margin: 0;
      padding: 6px 9px;
      border-radius: 9px;
    }
    .tab-chevron { display: none; }
    .tab-icon { width: 32px; height: 32px; border-radius: 8px; }
    .tab-copy strong { font-size: 13px; }
    .tab-copy small { display: none; }
    .pane-head { flex-wrap: wrap; }
    .actions-head > :last-child { display: flex !important; flex-wrap: wrap; gap: 8px; }
    .group-head.with-action { grid-template-columns: 42px minmax(0, 1fr); }
    .group-actions { grid-column: 2; justify-content: flex-start; flex-wrap: wrap; min-width: 0; }
  }

  @media (max-width: 1100px) {
    .template-grid, .plugin-grid, .storage-cards { grid-template-columns: minmax(0, 1fr); }
    .plugin-system-check { align-items: flex-start; flex-wrap: wrap; }
    .plugin-system-check > .btn { margin-left: 46px; }
    .storage-total-head { flex-wrap: wrap; }
    .storage-total-count { width: 100%; margin-left: 0; }
    .selected-template-banner { grid-template-columns: 54px minmax(0, 1fr); }
    .selected-template-banner > :last-child { grid-column: 2; justify-self: start; }
  }

  @media (max-width: 960px) {
    .floating-save-bar { right: 12px; bottom: 12px; left: 12px; max-width: none; }
    .floating-save-bar > div:first-child { flex: 1 1 220px; min-width: 0; }
  }
</style>
