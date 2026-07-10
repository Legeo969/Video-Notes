<script lang="ts">
  import { engineCall } from "../lib/api";
  import type { ProviderProfile } from "../lib/types";
  import Icon from "../lib/components/Icon.svelte";
  import PageHeader from "../lib/components/PageHeader.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import StatusPill from "../lib/components/StatusPill.svelte";
  import ProvidersPanel from "../lib/components/settings/ProvidersPanel.svelte";
  import ProviderFormDialog from "../lib/components/settings/ProviderFormDialog.svelte";
  import SettingsGeneral from "../lib/components/settings/SettingsGeneral.svelte";
  import {
    normalizeLocalWhisperModels,
    normalizeWhisperModelId,
    type LocalWhisperModel,
  } from "../lib/whisperModels";

  interface ProviderForm {
    name: string;
    provider: string;
    api_key: string;
    base_url: string;
    model: string;
    vision_model: string;
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
    transcription_backend: string;
    whisper_model: string;
    whisper_model_dir: string;
    whisper_device: string;
    ocr_enabled: boolean;
    ocr_backend: string;
    ocr_http_endpoint: string;
    ocr_http_api_key: string;
    ocr_api_key_configured: boolean;
    ocr_model: string;
    vision_enabled: boolean;
    frame_mode: string;
    frame_interval: number;
    max_frames: number;
    template: string;
    active_provider: string;
    bilibili_cookie_file: string;
  }

  const emptyProviderForm = (): ProviderForm => ({
    name: "",
    provider: "openai_compat",
    api_key: "",
    base_url: "",
    model: "",
    vision_model: "",
  });

  const officialPaddleOcrModelOptions = [
    "PaddleOCR-VL-1.6",
    "PaddleOCR-VL-1.5",
    "PaddleOCR-VL",
    "PP-StructureV3",
    "PP-OCRv6",
    "PP-OCRv5",
    "PP-OCRv5-latin",
  ];

  const tabs = [
    { id: "general", label: "通用与转录", icon: "settings", hint: "目录、模型和 OCR" },
    { id: "providers", label: "AI 供应商", icon: "bot", hint: "文本与视觉模型" },
    { id: "templates", label: "笔记模板", icon: "template", hint: "输出结构与场景" },
    { id: "plugins", label: "插件", icon: "package", hint: "转写 / OCR 可选组件" },
    { id: "storage", label: "存储管理", icon: "database", hint: "缓存、工作区与导出" },
    { id: "diagnostics", label: "系统诊断", icon: "stethoscope", hint: "依赖与运行环境" },
  ];

  let activeTab = $state("general");
  let settings = $state<SettingsBag>({
    output_dir: "./output",
    vault_path: "",
    transcription_backend: "whisper_cpp",
    whisper_model: "large-v3",
    whisper_model_dir: "",
    whisper_device: "auto",
    ocr_enabled: false,
    ocr_backend: "tesseract",
    ocr_http_endpoint: "",
    ocr_http_api_key: "",
    ocr_api_key_configured: false,
    ocr_model: "PaddleOCR-VL-1.6",
    vision_enabled: false,
    frame_mode: "fixed",
    frame_interval: 30,
    max_frames: 30,
    template: "default",
    active_provider: "",
    bilibili_cookie_file: "",
  });
  let providers = $state<ProviderProfile[]>([]);
  let templates = $state<TemplateInfo[]>([]);
  let localWhisperModels = $state<LocalWhisperModel[]>([]);
  let checkResults = $state<CheckResult[]>([]);
  let loading = $state(true);
  let saving = $state(false);
  let scanning = $state(false);
  let testingProvider = $state<string | null>(null);
  let testingOcr = $state(false);
  let refreshingOcrModels = $state(false);
  let testingVision = $state(false);
  let clearingCapabilities = $state<string | null>(null);
  let doctorRunning = $state(false);
  let bundlingDiagnostics = $state(false);
  let storageLoading = $state(false);
  let storageStatus = $state<StorageStatus | null>(null);
  let componentsLoading = $state(false);
  let runtimeComponents = $state<RuntimeComponent[]>([]);
  let componentAction = $state<string | null>(null);
  let toast = $state<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  let dirty = $state(false);
  let ocrKeyDirty = $state(false);

  let showProviderModal = $state(false);
  let editingProviderName = $state<string | null>(null);
  let providerForm = $state<ProviderForm>(emptyProviderForm());
  let providerSaving = $state(false);
  let providerSearch = $state("");
  let providerModelOptions = $state<string[]>([]);
  let discoveringProviderModels = $state(false);
  let paddleOcrModelOptions = $state<string[]>([...officialPaddleOcrModelOptions]);

  let selectedTemplate = $derived(templates.find((template) => template.id === settings.template));
  let passedChecks = $derived(checkResults.filter((item) => item.status === "pass").length);
  let toolComponents = $derived(runtimeComponents.filter((item) => ["download-tools", "ffmpeg-tools"].includes(item.component) || (item.provides ?? []).some((cap) => ["download", "ffmpeg"].includes(cap))));
  let transcriptionComponents = $derived(runtimeComponents.filter((item) => item.component === "whisper-cpp-tools" || item.component === "whisper-cpp-cuda-tools" || (item.provides ?? []).includes("transcription-native")));
  let ocrComponents = $derived(runtimeComponents.filter((item) => item.component === "tesseract-ocr-tools" || (item.provides ?? []).includes("ocr-native")));

  function showToast(msg: string, type: "success" | "error" | "info" = "info") {
    toast = { msg, type };
    setTimeout(() => (toast = null), 3500);
  }

  function markDirty() { dirty = true; }

  async function loadAll() {
    loading = true;
    ocrKeyDirty = false;
    try {
      const [s, provs, tmpls, localModels] = await Promise.all([
        engineCall<SettingsBag>("settings.get"),
        engineCall<ProviderProfile[]>("settings.providers.list"),
        engineCall<TemplateInfo[]>("settings.templates.list"),
        engineCall<Array<string | LocalWhisperModel>>("settings.models.local").catch(() => []),
      ]);
      settings = {
        ...s,
        vault_path: s.vault_path ?? "",
        transcription_backend: "whisper_cpp",
        frame_mode: s.frame_mode ?? "fixed",
        frame_interval: s.frame_interval ?? 30,
        max_frames: s.max_frames ?? 30,
        ocr_backend: s.ocr_backend || "tesseract",
        ocr_http_endpoint: s.ocr_http_endpoint ?? "",
        ocr_http_api_key: s.ocr_http_api_key ?? "",
        ocr_model: s.ocr_model || "PaddleOCR-VL-1.6",
        whisper_model: normalizeWhisperModelId(s.whisper_model) || "large-v3",
      };
      providers = provs;
      templates = tmpls;
      localWhisperModels = normalizeLocalWhisperModels(localModels);
      refreshStorageStatus();
      refreshComponents();
    } catch (e: any) {
      showToast(`加载设置失败：${e?.message ?? e}`, "error");
    } finally { loading = false; }
  }

  async function scanModels() {
    scanning = true;
    try {
      const discovered = await engineCall<Array<string | LocalWhisperModel>>("settings.models.local");
      localWhisperModels = normalizeLocalWhisperModels(discovered);
      if (localWhisperModels.length === 0) {
        showToast("没有找到可直接运行的本地 Whisper 模型，请检查模型目录。", "error");
      } else {
        showToast(`扫描到 ${localWhisperModels.length} 个可选 Whisper 模型`, "success");
      }
    } catch (e: any) { showToast(`模型扫描失败：${e?.message ?? e}`, "error"); }
    finally { scanning = false; }
  }

  function chooseWhisperModel(modelId: string) {
    const normalized = normalizeWhisperModelId(modelId);
    const installed = localWhisperModels.some((m) => m.id === normalized);
    if (!installed) {
      showToast(`模型“${normalized}”未在本地模型目录中找到，不能设为默认模型。`, "error");
      return;
    }
    settings.whisper_model = normalized;
    markDirty();
  }

  async function handleSave() {
    settings.whisper_model = normalizeWhisperModelId(settings.whisper_model) || "large-v3";
    saving = true;
    try {
      await engineCall("settings.update", {
        patches: {
          output_dir: settings.output_dir,
          vault_path: settings.vault_path,
          transcription_backend: "whisper_cpp",
          whisper_model: settings.whisper_model,
          whisper_model_dir: settings.whisper_model_dir,
          whisper_device: settings.whisper_device,
          ocr_enabled: settings.ocr_enabled,
          ocr_backend: settings.ocr_backend,
          ocr_http_endpoint: settings.ocr_http_endpoint,
          ...(ocrKeyDirty ? { ocr_http_api_key: settings.ocr_http_api_key } : {}),
          ocr_model: settings.ocr_model,
          vision_enabled: settings.vision_enabled,
          frame_mode: settings.frame_mode,
          frame_interval: settings.frame_interval,
          max_frames: settings.max_frames,
          template: settings.template,
          bilibili_cookie_file: settings.bilibili_cookie_file,
        },
      });
      dirty = false;
      ocrKeyDirty = false;
      const whisperAvailable = localWhisperModels.some((m) => m.id === normalizeWhisperModelId(settings.whisper_model));
      showToast(whisperAvailable ? "设置已保存并将在后续任务中生效" : "设置已保存；当前 Whisper 模型未检测到，运行任务前请安装或重新扫描模型。", whisperAvailable ? "success" : "info");
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
        });
      } else {
        await engineCall("settings.providers.create", {
          name: providerForm.name,
          provider: providerForm.provider,
          api_key: providerForm.api_key || undefined,
          base_url: providerForm.base_url,
          model: providerForm.model,
          vision_model: providerForm.vision_model,
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

  async function testOcrConnection() {
    testingOcr = true;
    try {
      const result: any = await engineCall("settings.ocr.test", {
        ocr_backend: settings.ocr_backend,
        ocr_http_endpoint: settings.ocr_http_endpoint,
        ocr_http_api_key: settings.ocr_http_api_key,
        ocr_model: settings.ocr_model,
      });
      showToast(result?.success ? `OCR 连接成功：${result.message ?? "服务可用"}` : `OCR 连接失败：${result?.message ?? "未知错误"}`, result?.success ? "success" : "error");
    } catch (e: any) {
      showToast(`OCR 测试异常：${e?.message ?? e}`, "error");
    } finally {
      testingOcr = false;
    }
  }

  async function refreshOcrModels() {
    refreshingOcrModels = true;
    try {
      paddleOcrModelOptions = [...officialPaddleOcrModelOptions];
      showToast("官方 PaddleOCR API 未提供模型发现接口；已刷新内置官方模型列表。", "info");
    } finally {
      refreshingOcrModels = false;
    }
  }

  async function testVisionConnection() {
    if (!settings.active_provider) {
      showToast("没有活动供应商，请先设置并激活 AI 供应商", "error");
      return;
    }
    testingVision = true;
    try {
      const result: any = await engineCall("settings.vision.test", {
        name: settings.active_provider,
      });
      const cacheNote = result?.capability_cache_saved === false ? "；能力缓存未写入" : "";
      if (result?.success) {
        showToast(`视觉模型 [${result.model}] 可用：${result?.result ?? ""}${cacheNote}`, "success");
      } else {
        showToast(`视觉模型测试失败：${result?.message ?? "未知错误"}${result?.error ? `（${result.error}）` : ""}${cacheNote}`, "error");
      }
    } catch (e: any) {
      showToast(`视觉模型测试异常：${e?.message ?? e}`, "error");
    } finally {
      try {
        providers = await engineCall<ProviderProfile[]>("settings.providers.list");
        settings = { ...settings, ...(await engineCall<SettingsBag>("settings.get")) };
      } catch (_e) {}
      testingVision = false;
    }
  }

  async function clearProviderCapabilities(provider?: string) {
    clearingCapabilities = provider || "__all__";
    try {
      await engineCall("settings.providers.capabilities.clear", provider ? { provider } : {});
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
      showToast(provider ? "能力缓存已清除" : "全部供应商能力缓存已清除", "success");
    } catch (e: any) {
      showToast(`清除能力缓存失败：${e?.message ?? e}`, "error");
    } finally {
      clearingCapabilities = null;
    }
  }

  async function runDoctor() {
    doctorRunning = true;
    checkResults = [];
    try {
      const results: CheckResult[] = await engineCall("doctor.run");
      checkResults = results;
      showToast(`环境检查完成，${results.filter((r) => r.status === "pass").length} 项通过`, "info");
    } catch (e: any) { showToast(`环境检查失败：${e?.message ?? e}`, "error"); }
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

  function componentHelpUrl(component: RuntimeComponent) {
    if (component.component === "tesseract-ocr-tools") return "https://github.com/UB-Mannheim/tesseract/wiki";
    return "";
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

  async function installComponent(component: RuntimeComponent, updating = false) {
    componentAction = `install:${component.component}`;
    try {
      await engineCall("components.install", { component: component.component });
      showToast(`${component.component} ${updating ? "已更新" : "已安装"}`, "success");
      await refreshComponents();
    } catch (e: any) {
      showToast(`${updating ? "更新" : "安装"} ${component.component} 失败：${e?.message ?? e}`, "error");
    } finally {
      componentAction = null;
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
            <Icon name="chevron-right" size={14} />
          </button>
        {/each}
        <div class="security-note"><Icon name="shield" size={17} /><div><strong>本地安全存储</strong><p>敏感凭据不会写入任务快照或诊断日志。</p></div></div>
      </aside>

      <main class="settings-content">
        {#if activeTab === "general"}
          <SettingsGeneral
            bind:settings
            bind:localWhisperModels
            bind:scanning
            bind:testingOcr
            bind:refreshingOcrModels
            bind:paddleOcrModelOptions
            bind:ocrKeyDirty
            onMarkDirty={markDirty}
            onChooseWhisperModel={chooseWhisperModel}
            onScanModels={scanModels}
            onOpenExternalUrl={openExternalUrl}
            onTestOcrConnection={testOcrConnection}
            onRefreshOcrModels={refreshOcrModels}
          />

        {:else if activeTab === "providers"}
          <ProvidersPanel
            {providers}
            activeProvider={settings.active_provider}
            {testingProvider}
            {testingVision}
            {clearingCapabilities}
            bind:providerSearch
            onSetActive={setActiveProvider}
            onTestConnection={testConnection}
            onTestVisionConnection={testVisionConnection}
            onClearCapabilities={clearProviderCapabilities}
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
              <div><span>PLUGINS</span><h2>插件</h2><p>按需安装转写和 OCR 运行时组件；主程序保持轻量，重依赖放在本机 runtime。</p></div>
              <button class="btn btn-secondary" type="button" onclick={refreshComponents} disabled={componentsLoading}><Icon name="refresh" size={15} />{componentsLoading ? "刷新中" : "刷新"}</button>
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="download" size={18} /></div><div><h3>外部工具</h3><p>yt-dlp 和 FFmpeg 以 standalone executable 运行。</p></div></div>
              {#if componentsLoading && toolComponents.length === 0}
                <div class="plugin-empty-state"><span class="loading-ring compact"></span><div><strong>正在读取插件状态</strong><small>检查本机 runtime 组件清单与已安装目录。</small></div></div>
              {:else if toolComponents.length === 0}
                <div class="plugin-empty-state"><Icon name="package" size={20} /><div><strong>未找到工具组件清单</strong><small>请确认 runtime/manifests/download-tools.json 或 ffmpeg-tools.json 存在。</small></div></div>
              {:else}
                <div class="plugin-grid">
                  {#each toolComponents as component}
                    <article class="plugin-card" class:installed={component.installed}>
                      <div class="plugin-card-head">
                        <div class="plugin-icon"><Icon name={component.component === "download-tools" ? "download" : "video"} size={20} /></div>
                        <div class="plugin-title"><strong>{component.component}</strong><small>{component.description}</small></div>
                        <StatusPill status={componentStatusType(component)} label={componentStatusLabel(component)} />
                      </div>
                      <div class="plugin-meta">
                        <div><span>工具版本</span><strong>{component.installed_version || "未安装"}</strong></div>
                        <div><span>体积</span><strong>{component.size_mb ? `${component.size_mb} MB` : "未知"}</strong></div>
                        <div><span>能力</span><strong>{(component.provides ?? []).join(" / ") || "runtime"}</strong></div>
                      </div>
                      <div class="plugin-path"><span>安装位置</span><code>{component.component_path || "尚未安装"}</code></div>
                      {#if component.missing_files?.length}
                        <div class="plugin-warning"><Icon name="alert" size={14} /><span>缺少 {component.missing_files.length} 个组件文件，建议重新安装。</span></div>
                      {/if}
                      <div class="plugin-actions">
                        {#if component.installed}
                          <button class="btn btn-secondary" type="button" onclick={() => verifyComponent(component)} disabled={componentAction !== null}><Icon name="check" size={14} />{componentAction === `verify:${component.component}` ? "验证中" : "验证"}</button>
                          {#if component.update_available}
                            <button class="btn btn-primary" type="button" onclick={() => installComponent(component, true)} disabled={componentAction !== null}><Icon name="refresh" size={14} />{componentAction === `install:${component.component}` ? "更新中" : "更新"}</button>
                          {/if}
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else if component.downloadable}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component)} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
                        {:else if componentHelpUrl(component)}
                          <button class="btn btn-secondary" type="button" onclick={() => openExternalUrl(componentHelpUrl(component))}><Icon name="external" size={14} />安装说明</button>
                        {/if}
                      </div>
                    </article>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="audio" size={18} /></div><div><h3>转写引擎</h3><p>whisper.cpp CPU / CUDA 组件按需安装；模型文件单独放在模型目录。</p></div></div>
              {#if componentsLoading && transcriptionComponents.length === 0}
                <div class="plugin-empty-state"><span class="loading-ring compact"></span><div><strong>正在读取插件状态</strong><small>检查本机 runtime 组件清单与已安装目录。</small></div></div>
              {:else if transcriptionComponents.length === 0}
                <div class="plugin-empty-state"><Icon name="package" size={20} /><div><strong>未找到转写插件清单</strong><small>请确认 runtime/manifests/whisper-cpp-tools.json 存在。</small></div></div>
              {:else}
                <div class="plugin-grid">
                  {#each transcriptionComponents as component}
                    <article class="plugin-card" class:installed={component.installed}>
                      <div class="plugin-card-head">
                        <div class="plugin-icon"><Icon name="audio" size={20} /></div>
                        <div class="plugin-title"><strong>{component.component}</strong><small>{component.description}</small></div>
                        <StatusPill status={componentStatusType(component)} label={componentStatusLabel(component)} />
                      </div>
                      <div class="plugin-meta">
                        <div><span>工具版本</span><strong>{component.installed_version || "未安装"}</strong></div>
                        <div><span>体积</span><strong>{component.size_mb ? `${component.size_mb} MB` : "未知"}</strong></div>
                        <div><span>能力</span><strong>{(component.provides ?? []).join(" / ") || "runtime"}</strong></div>
                      </div>
                      <div class="plugin-path"><span>安装位置</span><code>{component.component_path || "尚未安装"}</code></div>
                      {#if component.missing_files?.length}
                        <div class="plugin-warning"><Icon name="alert" size={14} /><span>缺少 {component.missing_files.length} 个组件文件，建议重新安装。</span></div>
                      {/if}
                      <div class="plugin-actions">
                        {#if component.installed}
                          <button class="btn btn-secondary" type="button" onclick={() => verifyComponent(component)} disabled={componentAction !== null}><Icon name="check" size={14} />{componentAction === `verify:${component.component}` ? "验证中" : "验证"}</button>
                          {#if component.update_available}
                            <button class="btn btn-primary" type="button" onclick={() => installComponent(component, true)} disabled={componentAction !== null}><Icon name="refresh" size={14} />{componentAction === `install:${component.component}` ? "更新中" : "更新"}</button>
                          {/if}
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else if component.downloadable}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component)} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
                        {:else if componentHelpUrl(component)}
                          <button class="btn btn-secondary" type="button" onclick={() => openExternalUrl(componentHelpUrl(component))}><Icon name="external" size={14} />安装说明</button>
                        {/if}
                      </div>
                    </article>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="package" size={18} /></div><div><h3>OCR 插件</h3><p>PaddleOCR HTTP 不需要安装；仅 Tesseract 本地后端需要。</p></div></div>
              {#if componentsLoading && ocrComponents.length === 0}
                <div class="plugin-empty-state"><span class="loading-ring compact"></span><div><strong>正在读取插件状态</strong><small>检查本机 runtime 组件清单与已安装目录。</small></div></div>
              {:else if ocrComponents.length === 0}
                <div class="plugin-empty-state"><Icon name="package" size={20} /><div><strong>未找到 OCR 插件清单</strong><small>请确认 runtime/manifests/tesseract-ocr-tools.json 存在。</small></div></div>
              {:else}
                <div class="plugin-grid">
                  {#each ocrComponents as component}
                    <article class="plugin-card" class:installed={component.installed}>
                      <div class="plugin-card-head">
                        <div class="plugin-icon"><Icon name="ocr" size={20} /></div>
                        <div class="plugin-title"><strong>{component.component}</strong><small>{component.description}</small></div>
                        <StatusPill status={componentStatusType(component)} label={componentStatusLabel(component)} />
                      </div>
                      <div class="plugin-meta">
                        <div><span>工具版本</span><strong>{component.installed_version || "未安装"}</strong></div>
                        <div><span>体积</span><strong>{component.size_mb ? `${component.size_mb} MB` : "未知"}</strong></div>
                        <div><span>能力</span><strong>{(component.provides ?? []).join(" / ") || "runtime"}</strong></div>
                      </div>
                      <div class="plugin-path"><span>安装位置</span><code>{component.component_path || "尚未安装"}</code></div>
                      {#if component.missing_files?.length}
                        <div class="plugin-warning"><Icon name="alert" size={14} /><span>缺少 {component.missing_files.length} 个组件文件，建议重新安装。</span></div>
                      {/if}
                      {#if component.component === "tesseract-ocr-tools" && !component.installed}
                        <div class="plugin-warning plugin-note"><Icon name="info" size={14} /><span>使用 PaddleOCR HTTP 时可忽略；只有选择 Tesseract 本地后端才需要系统 Tesseract。</span></div>
                      {/if}
                      <div class="plugin-actions">
                        {#if component.installed}
                          <button class="btn btn-secondary" type="button" onclick={() => verifyComponent(component)} disabled={componentAction !== null}><Icon name="check" size={14} />{componentAction === `verify:${component.component}` ? "验证中" : "验证"}</button>
                          {#if component.update_available}
                            <button class="btn btn-primary" type="button" onclick={() => installComponent(component, true)} disabled={componentAction !== null}><Icon name="refresh" size={14} />{componentAction === `install:${component.component}` ? "更新中" : "更新"}</button>
                          {/if}
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else if component.downloadable}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component)} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
                        {:else if componentHelpUrl(component)}
                          <button class="btn btn-secondary" type="button" onclick={() => openExternalUrl(componentHelpUrl(component))}><Icon name="external" size={14} />安装说明</button>
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
              <div><span>STORAGE</span><h2>存储管理</h2><p>查看 AppData 状态、本机任务记录、工作区缓存和用户可见导出目录。</p></div>
              <button class="btn btn-secondary" type="button" onclick={refreshStorageStatus} disabled={storageLoading}><Icon name="refresh" size={15} />{storageLoading ? "刷新中" : "刷新"}</button>
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="database" size={18} /></div><div><h3>本机存储概览</h3><p>任务中心显示的是当前 Native 任务记录；工作区缓存只是可清理的临时目录。</p></div></div>
              {#if storageStatus}
                <div class="storage-grid">
                  <div><span>导出目录</span><strong>{formatBytes(storageStatus.sizes.exports)}</strong><small>{storageStatus.export_dir}</small></div>
                  <div><span>本机任务记录</span><strong>{storageStatus.tasks?.total ?? 0} 个</strong><small>活动 {storageStatus.tasks?.running ?? 0} · 已完成 {storageStatus.tasks?.completed ?? 0} · 异常 {storageStatus.tasks?.failed ?? 0}</small></div>
                  <div><span>工作区缓存</span><strong>{formatBytes((storageStatus.sizes.jobs ?? 0) + (storageStatus.sizes.legacy_jobs ?? 0))}</strong><small>{storageStatus.jobs_root}{(storageStatus.sizes.legacy_jobs ?? 0) > 0 ? ` · legacy: ${storageStatus.legacy_jobs_root}` : ""}</small></div>
                  <div><span>运行时组件</span><strong>{formatBytes(storageStatus.sizes.runtime)}</strong><small>插件、外部工具和已安装组件</small></div>
                  <div><span>Obsidian Vault</span><strong>{storageStatus.vault_path ? "已配置" : "未配置"}</strong><small>{storageStatus.vault_path || "未配置"}</small></div>
                </div>
              {:else}
                <div class="storage-empty-state"><span class="loading-ring compact"></span><div><strong>尚未读取存储状态</strong><small>点击刷新以查看本机缓存、工作区和导出目录占用。</small></div></div>
              {/if}
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="trash" size={18} /></div><div><h3>工作区缓存清理</h3><p>只清理 AppData 中的临时工作区，不会删除当前任务记录、已经导出的笔记或 assets 图片。</p></div></div>
              <div class="storage-actions">
                <button class="btn btn-secondary" type="button" onclick={cleanupOrphanWorkspaces} disabled={storageLoading}><Icon name="trash" size={14} />清理孤儿任务缓存</button>
                <button class="btn btn-secondary" type="button" onclick={cleanupCompletedWorkspaces} disabled={storageLoading}><Icon name="check" size={14} />清理已完成任务缓存</button>
              </div>
            </div>
          </section>

        {:else if activeTab === "diagnostics"}
          <section class="settings-pane">
            <div class="pane-head actions-head">
              <div><span>SYSTEM HEALTH</span><h2>系统诊断</h2><p>检测 FFmpeg、转录引擎、OCR、GPU 和本地存储状态。</p></div>
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
  .unsaved-badge { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; padding: 6px 10px; border-radius: 9px; color: var(--warning-color); background: var(--warning-soft); font-size: 13px; font-weight: 650; }
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
  .group-actions .btn { min-height: 36px; white-space: nowrap; }
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

  .floating-save-bar { position: fixed; z-index: 50; right: 28px; bottom: 22px; display: flex; align-items: center; gap: 8px; padding: 9px 10px 9px 13px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-elevated); box-shadow: var(--shadow-md); }
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
  .plugin-warning { display: flex; align-items: center; gap: 6px; margin: 0 14px; padding: 7px 10px; border-radius: 8px; color: var(--warning-color); background: var(--warning-soft); font-size: 12px; }
  .plugin-note { color: var(--text-secondary); background: var(--bg-subtle); }
  .plugin-actions { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 14px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .plugin-actions .btn { min-height: 34px; padding: 6px 10px; font-size: 13px; }
  .plugin-empty-state { display: flex; align-items: center; gap: 12px; min-height: 78px; margin-top: 14px; padding: 14px 15px; border: 1px dashed var(--border-strong); border-radius: 12px; color: var(--text-secondary); background: var(--bg-subtle); }
  .plugin-empty-state > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  .plugin-empty-state strong { color: var(--text-primary); font-size: 14px; }
  .plugin-empty-state small { font-size: 12px; }
  .storage-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
  .storage-grid > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; padding: 12px; border-radius: 10px; background: var(--bg-subtle); }
  .storage-grid span { color: var(--text-tertiary); font-size: 12px; font-weight: 600; }
  .storage-grid strong { color: var(--text-primary); font-size: 14px; }
  .storage-grid small { overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .storage-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .storage-empty-state { display: flex; align-items: center; gap: 12px; min-height: 72px; margin-top: 14px; padding: 14px 15px; border: 1px dashed var(--border-strong); border-radius: 12px; color: var(--text-secondary); background: var(--bg-subtle); }
  .storage-empty-state > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  .storage-empty-state strong { color: var(--text-primary); font-size: 14px; }
  .storage-empty-state small { font-size: 12px; }
  @media (max-width: 760px) { .plugin-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .storage-grid { grid-template-columns: 1fr; } }

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
    .storage-grid { grid-template-columns: 1fr; }
    .check-head, .check-row { grid-template-columns: minmax(100px, .5fr) 70px minmax(140px, 1fr); gap: 8px; }
    .settings-pane { padding: 18px 14px 24px; }
    .pane-head { flex-direction: column; gap: 10px; }
    .pane-head h2 { font-size: 20px; }
    .diagnostic-actions { flex-wrap: wrap; }
    .group-head.with-action { grid-template-columns: 1fr; gap: 10px; }
    .group-actions { grid-column: 1; justify-content: flex-start; }
  }
</style>
