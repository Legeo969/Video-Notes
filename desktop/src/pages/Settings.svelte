<script lang="ts">
  import { engineCall } from "../lib/api";
  import type { ProviderProfile } from "../lib/types";
  import Icon from "../lib/components/Icon.svelte";
  import PageHeader from "../lib/components/PageHeader.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import StatusPill from "../lib/components/StatusPill.svelte";
  import {
    buildWhisperModelCatalog,
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
    state_dir: string;
    db_path: string;
    jobs_root: string;
    legacy_jobs_root: string;
    vault_path: string;
    sizes: Record<string, number>;
    counts: Record<string, { dirs: number; files: number }>;
  }

  interface RuntimeComponent {
    component: string;
    version: string;
    description: string;
    installed: boolean;
    installed_version?: string | null;
    status: string;
    size_mb?: number;
    component_path?: string;
    provides?: string[];
    missing_files?: string[];
  }

  interface SettingsBag {
    output_dir: string;
    vault_path: string;
    transcription_backend: string;
    whisper_model: string;
    whisper_model_dir: string;
    whisper_device: string;
    whisper_compute_type: string;
    ocr_enabled: boolean;
    ocr_backend: string;
    ocr_http_endpoint: string;
    ocr_http_api_key: string;
    vision_enabled: boolean;
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

  const providerPresetModels = new Set(["gpt-4o-mini", "gpt-4o", "gemini-2.5-flash", "claude-sonnet-4-5", "gpt-5.5"]);

  const providerTypeOptions = [
    {
      id: "openai_compat",
      label: "OpenAI Compatible",
      hint: "Chat Completions 兼容接口，例如 OpenAI 兼容网关、阿里云百炼兼容模式。",
      defaultBaseUrl: "https://api.openai.com/v1",
      supported: true,
    },
    {
      id: "google_gemini",
      label: "Google Gemini",
      hint: "Google Generative Language API，使用 gemini-* 模型。",
      defaultBaseUrl: "https://generativelanguage.googleapis.com/v1beta",
      supported: true,
    },
    {
      id: "anthropic_messages",
      label: "Anthropic Messages",
      hint: "Anthropic /v1/messages 接口，使用 claude-* 模型。",
      defaultBaseUrl: "https://api.anthropic.com/v1",
      supported: true,
    },
    {
      id: "openai_responses",
      label: "OpenAI Responses",
      hint: "OpenAI /v1/responses 接口，适合新版 OpenAI API 调用。",
      defaultBaseUrl: "https://api.openai.com/v1",
      supported: true,
    },
    {
      id: "chatgpt_codex",
      label: "ChatGPT Codex (Plus/Pro)",
      hint: "ChatGPT 计划中的 Codex 交互能力，不是普通 API Key 端点；此项仅做配置占位，不能用于自动笔记任务。",
      defaultBaseUrl: "",
      supported: false,
    },
  ];

  const providerTypeLabels: Record<string, string> = Object.fromEntries(providerTypeOptions.map((item) => [item.id, item.label]));
  providerTypeLabels.mimo = "OpenAI Compatible";
  providerTypeLabels.dashscope = "OpenAI Compatible";
  providerTypeLabels.openai = "OpenAI Compatible";

  const tabs = [
    { id: "general", label: "通用与转录", icon: "settings", hint: "目录、模型和 OCR" },
    { id: "providers", label: "AI 供应商", icon: "bot", hint: "文本与视觉模型" },
    { id: "templates", label: "笔记模板", icon: "template", hint: "输出结构与场景" },
    { id: "plugins", label: "插件", icon: "package", hint: "转写 / OCR 可选组件" },
    { id: "storage", label: "存储管理", icon: "database", hint: "缓存、数据库与导出" },
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
    whisper_compute_type: "auto",
    ocr_enabled: false,
    ocr_backend: "tesseract",
    ocr_http_endpoint: "",
    ocr_http_api_key: "",
    vision_enabled: false,
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
  let doctorRunning = $state(false);
  let bundlingDiagnostics = $state(false);
  let storageLoading = $state(false);
  let storageStatus = $state<StorageStatus | null>(null);
  let componentsLoading = $state(false);
  let runtimeComponents = $state<RuntimeComponent[]>([]);
  let componentAction = $state<string | null>(null);
  let toast = $state<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  let dirty = $state(false);

  let showProviderModal = $state(false);
  let editingProviderName = $state<string | null>(null);
  let providerForm = $state<ProviderForm>(emptyProviderForm());
  let providerSaving = $state(false);
  let providerSearch = $state("");
  let providerModelOptions = $state<string[]>([]);
  let discoveringProviderModels = $state(false);

  let filteredProviders = $derived.by(() => {
    const q = providerSearch.trim().toLowerCase();
    if (!q) return providers;
    return providers.filter((p) => [p.name, p.provider, p.model, p.vision_model || ""].some((v) => v.toLowerCase().includes(q)));
  });
  let selectedTemplate = $derived(templates.find((template) => template.id === settings.template));
  let passedChecks = $derived(checkResults.filter((item) => item.status === "pass").length);
  let whisperCatalog = $derived(buildWhisperModelCatalog(localWhisperModels, settings.whisper_model, true));
  let selectedWhisperModel = $derived(whisperCatalog.find((model) => model.id === normalizeWhisperModelId(settings.whisper_model)));
  let selectedWhisperAvailable = $derived(Boolean(selectedWhisperModel?.installed));
  let toolComponents = $derived(runtimeComponents.filter((item) => ["download-tools", "ffmpeg-tools"].includes(item.component) || (item.provides ?? []).some((cap) => ["download", "ffmpeg"].includes(cap))));
  let transcriptionComponents = $derived(runtimeComponents.filter((item) => item.component === "whisper-cpp-tools" || (item.provides ?? []).includes("transcription-native")));
  let ocrComponents = $derived(runtimeComponents.filter((item) => item.component === "tesseract-ocr-tools" || (item.provides ?? []).includes("ocr-native")));

  function showToast(msg: string, type: "success" | "error" | "info" = "info") {
    toast = { msg, type };
    setTimeout(() => (toast = null), 3500);
  }

  function markDirty() { dirty = true; }

  async function loadAll() {
    loading = true;
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
        ocr_backend: s.ocr_backend || "tesseract",
        ocr_http_endpoint: s.ocr_http_endpoint ?? "",
        ocr_http_api_key: s.ocr_http_api_key ?? "",
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
    const model = whisperCatalog.find((item) => item.id === normalized);
    if (!model?.installed) {
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
          whisper_compute_type: settings.whisper_compute_type,
          ocr_enabled: settings.ocr_enabled,
          ocr_backend: settings.ocr_backend,
          ocr_http_endpoint: settings.ocr_http_endpoint,
          ocr_http_api_key: settings.ocr_http_api_key,
          vision_enabled: settings.vision_enabled,
          template: settings.template,
          bilibili_cookie_file: settings.bilibili_cookie_file,
        },
      });
      dirty = false;
      showToast(selectedWhisperAvailable ? "设置已保存并将在后续任务中生效" : "设置已保存；当前 Whisper 模型未检测到，运行任务前请安装或重新扫描模型。", selectedWhisperAvailable ? "success" : "info");
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
      provider: p.provider,
      api_key: "",
      base_url: p.base_url,
      model: p.model,
      vision_model: p.vision_model ?? p.model,
    };
    providerModelOptions = Array.from(new Set([...(p.models ?? []), p.model, p.vision_model ?? ""].filter(Boolean)));
    showProviderModal = true;
  }

  function closeProviderModal() {
    showProviderModal = false;
    providerForm = emptyProviderForm();
    providerModelOptions = [];
    editingProviderName = null;
  }

  const selectedProviderType = $derived(providerTypeOptions.find((item) => item.id === providerForm.provider) ?? providerTypeOptions[0]);
  let providerTypeUnsupported = $derived(!selectedProviderType.supported);

  function handleProviderTypeChange() {
    const option = providerTypeOptions.find((item) => item.id === providerForm.provider);
    if (!option) return;
    providerModelOptions = [];
    if (!providerForm.base_url.trim() && option.defaultBaseUrl) {
      providerForm.base_url = option.defaultBaseUrl;
    }
    const nextModel =
      option.id === "google_gemini" ? "gemini-2.5-flash" :
      option.id === "anthropic_messages" ? "claude-sonnet-4-5" :
      option.id === "openai_responses" ? "gpt-5.5" :
      "";
    if (!providerForm.model.trim() || providerPresetModels.has(providerForm.model.trim())) {
      providerForm.model = nextModel;
    }
    if (!providerForm.vision_model.trim() || providerPresetModels.has(providerForm.vision_model.trim())) {
      providerForm.vision_model = nextModel;
    }
  }

  async function discoverProviderModels() {
    discoveringProviderModels = true;
    try {
      providerModelOptions = await engineCall<string[]>("settings.providers.models", {
        name: editingProviderName ?? undefined,
        provider: providerForm.provider,
        base_url: providerForm.base_url,
        api_key: providerForm.api_key || undefined,
        model: providerForm.model,
        vision_model: providerForm.vision_model,
      });
      showToast(`读取到 ${providerModelOptions.length} 个模型`, "success");
    } catch (e: any) {
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
    if (providerTypeUnsupported) { showToast("ChatGPT Codex (Plus/Pro) 不是可直接调用的 API 端点，不能保存为自动笔记供应商。", "error"); return; }
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
      showToast(result?.success ? `连接成功：${result.message ?? "服务可用"}` : `连接失败：${result?.message ?? "未知错误"}`, result?.success ? "success" : "error");
    } catch (e: any) { showToast(`测试连接异常：${e?.message ?? e}`, "error"); }
    finally { testingProvider = null; }
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

  async function installComponent(component: RuntimeComponent) {
    componentAction = `install:${component.component}`;
    try {
      await engineCall("components.install", { component: component.component });
      showToast(`${component.component} 已安装`, "success");
      await refreshComponents();
    } catch (e: any) {
      showToast(`安装 ${component.component} 失败：${e?.message ?? e}`, "error");
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
    description="管理转录模型、AI 供应商、笔记模板和本地运行环境。所有 API Key 均保存在本机安全存储中。"
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
          <section class="settings-pane">
            <div class="pane-head"><div><span>GENERAL & TRANSCRIPTION</span><h2>通用与转录</h2><p>设置默认输出位置、Whisper 模型与文字识别能力。</p></div></div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="folder" size={18} /></div><div><h3>文件与模型目录</h3><p>配置笔记产物与本地模型的存储位置。</p></div></div>
              <div class="form-grid two-cols">
                <div class="field"><label class="field-label" for="vault_path">Obsidian 笔记库 <small>可选，归档到 vault\video-notes</small></label><div class="input-wrap has-icon"><span class="input-icon"><Icon name="folder" size={15} /></span><input id="vault_path" type="text" bind:value={settings.vault_path} oninput={markDirty} placeholder="D:\Note_Obsidian" /></div></div>
                <div class="field"><label class="field-label" for="whisper_model_dir">Whisper 模型目录 <small>可选</small></label><div class="input-wrap has-icon"><span class="input-icon"><Icon name="database" size={15} /></span><input id="whisper_model_dir" type="text" bind:value={settings.whisper_model_dir} oninput={markDirty} placeholder="留空使用默认缓存目录" /></div></div>
              </div>
            </div>

            <div class="setting-group online-video-settings">
              <div class="group-head"><div class="group-icon"><Icon name="link" size={18} /></div><div><h3>在线视频下载与 Cookie</h3><p>用于 B站等需要登录态的视频下载。公开免费视频可留空。</p></div></div>
              <div class="field">
                <label class="field-label" for="bilibili_cookie_file">Cookie 文件或 Cookie 字符串 <small>可选</small></label>
                <div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="bilibili_cookie_file" type="text" bind:value={settings.bilibili_cookie_file} oninput={markDirty} placeholder="例如：C:\Users\你\cookies.txt，或 SESSDATA=...; bili_jct=..." /></div>
                <small class="field-help">只建议填写 cookies.txt 路径。该字段不会写入任务快照，但会保存在本机设置中；需要登录、会员、年龄验证或风控的视频通常必须配置。</small>
              </div>
            </div>

            <div class="setting-group">
              <div class="group-head with-action"><div class="group-icon"><Icon name="audio" size={18} /></div><div><h3>默认 Whisper 模型</h3><p>选择新任务默认使用的语音转录模型。</p></div><button class="btn btn-secondary btn-sm" onclick={scanModels} disabled={scanning}><Icon name="refresh" size={13} />{scanning ? "扫描中" : "扫描本地模型"}</button></div>
              {#if scanning}
                <div class="model-scan-state"><span class="loading-ring compact"></span><div><strong>正在扫描本地模型</strong><small>检查配置目录和应用默认模型目录…</small></div></div>
              {:else if localWhisperModels.length === 0}
                <div class="model-empty-state">
                  <span class="model-empty-icon"><Icon name="database" size={22} /></span>
                  <div><strong>尚未检测到可用模型</strong><p>模型目录中需要存在 <code>ggml-模型ID.bin</code> 或 <code>ggml-模型ID.gguf</code> 文件。</p></div>
                  <button class="btn btn-secondary btn-sm" type="button" onclick={scanModels}><Icon name="refresh" size={13} />重新扫描</button>
                </div>
              {:else}
                <div class="model-selection-summary" class:warning={!selectedWhisperAvailable}>
                  <span class="selection-status"><Icon name={selectedWhisperAvailable ? "check" : "alert"} size={15} /></span>
                  <div>
                    <strong>{selectedWhisperAvailable ? `当前默认：${selectedWhisperModel?.label}` : `当前配置不可用：${settings.whisper_model}`}</strong>
                    <small>{selectedWhisperAvailable ? selectedWhisperModel?.path || "本地模型已就绪" : "请在下方选择一个标记为“已安装”的模型"}</small>
                  </div>
                  <span class="installed-count">{localWhisperModels.length} 个已安装</span>
                </div>

                <div class="model-cards interactive-model-cards">
                  {#each whisperCatalog as model}
                    <button
                      type="button"
                      class="model-card"
                      class:selected={normalizeWhisperModelId(settings.whisper_model) === model.id}
                      class:unavailable={!model.installed}
                      disabled={!model.installed}
                      onclick={() => chooseWhisperModel(model.id)}
                      title={model.installed ? model.path || model.id : "本地未安装"}
                    >
                      <span class="model-check">{#if normalizeWhisperModelId(settings.whisper_model) === model.id}<Icon name="check" size={13} />{/if}</span>
                      <span class="model-availability" class:installed={model.installed}>{model.installed ? "已安装" : "未安装"}</span>
                      <strong>{model.label}</strong><small>{model.description}</small>
                      <span class="model-id">{model.id}</span>
                      <span class="quality-bars">{#each [1,2,3,4,5] as bar}<i class:active={bar <= model.quality}></i>{/each}</span>
                    </button>
                  {/each}
                </div>
              {/if}
              <div class="runtime-settings-grid">
                <div class="field">
                  <label class="field-label" for="transcription_backend">转写后端</label>
                  <select id="transcription_backend" bind:value={settings.transcription_backend} onchange={markDirty}>
                    <option value="whisper_cpp">whisper.cpp native CLI</option>
                  </select>
                </div>
                <div class="field">
                  <label class="field-label" for="whisper_device">Whisper 运行设备</label>
                  <select id="whisper_device" bind:value={settings.whisper_device} onchange={markDirty} disabled={settings.transcription_backend === "whisper_cpp"}>
                    <option value="auto">自动：优先 CUDA，可降级 CPU</option>
                    <option value="cuda">CUDA / GPU：不可用时报错</option>
                    <option value="cpu">CPU</option>
                  </select>
                </div>
                <div class="field">
                  <label class="field-label" for="whisper_compute_type">计算精度</label>
                  <select id="whisper_compute_type" bind:value={settings.whisper_compute_type} onchange={markDirty} disabled={settings.transcription_backend === "whisper_cpp"}>
                    <option value="auto">自动：CUDA=float16，CPU=int8</option>
                    <option value="float16">float16（CUDA 推荐）</option>
                    <option value="int8_float16">int8_float16（CUDA 低显存）</option>
                    <option value="int8">int8（CPU 推荐）</option>
                    <option value="float32">float32</option>
                  </select>
                </div>
              </div>
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="ocr" size={18} /></div><div><h3>内容增强</h3><p>控制新任务默认启用的 OCR 与视觉理解。这里保存的是默认值，创建任务页仍可单次覆盖。</p></div></div>
              <div class="enhancement-settings-grid">
                <button type="button" class="setting-toggle-card as-button" class:enabled={settings.ocr_enabled} onclick={() => { settings.ocr_enabled = !settings.ocr_enabled; markDirty(); }} aria-pressed={settings.ocr_enabled}>
                  <span class="toggle-feature-icon"><Icon name="scan" size={20} /></span>
                  <span class="toggle-copy"><strong>OCR 文字识别</strong><small>识别幻灯片、字幕和画面文字。未安装 OCR 后端时任务会给出明确错误，不会静默忽略。</small></span>
                  <span class="switch" aria-hidden="true"><input type="checkbox" checked={settings.ocr_enabled} tabindex="-1" /><span class="switch-track"></span></span>
                </button>
                <button type="button" class="setting-toggle-card as-button" class:enabled={settings.vision_enabled} onclick={() => { settings.vision_enabled = !settings.vision_enabled; markDirty(); }} aria-pressed={settings.vision_enabled}>
                  <span class="toggle-feature-icon"><Icon name="eye" size={20} /></span>
                  <span class="toggle-copy"><strong>视觉理解</strong><small>对关键帧、图表和演示内容做语义分析。需要活动 AI 供应商和视觉模型。</small></span>
                  <span class="switch" aria-hidden="true"><input type="checkbox" checked={settings.vision_enabled} tabindex="-1" /><span class="switch-track"></span></span>
                </button>
              </div>
              <div class="runtime-settings-grid">
                <div class="field">
                  <label class="field-label" for="ocr_backend">OCR 后端</label>
                  <select id="ocr_backend" bind:value={settings.ocr_backend} onchange={markDirty}>
                    <option value="tesseract">Tesseract native executable</option>
                    <option value="paddleocr_http">PaddleOCR HTTP service</option>
                    <option value="custom_http">Custom OCR HTTP API</option>
                  </select>
                </div>
                {#if settings.ocr_backend === "paddleocr_http" || settings.ocr_backend === "custom_http"}
                  <div class="field">
                    <label class="field-label" for="ocr_http_endpoint">OCR HTTP Endpoint <small>本地或远程</small></label>
                    <div class="input-wrap has-icon"><span class="input-icon"><Icon name="server" size={15} /></span><input id="ocr_http_endpoint" type="text" bind:value={settings.ocr_http_endpoint} oninput={markDirty} placeholder="http://127.0.0.1:8868/ocr" /></div>
                  </div>
                  <div class="field">
                    <label class="field-label" for="ocr_http_api_key">OCR API Key <small>可选</small></label>
                    <div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="ocr_http_api_key" type="password" bind:value={settings.ocr_http_api_key} oninput={markDirty} placeholder="Bearer token，可留空" /></div>
                  </div>
                {/if}
              </div>
              <div class="enhancement-explain"><Icon name="info" size={14} />OCR 是本地能力；视觉理解会调用当前活动 AI 供应商。首次真实任务建议先关闭两项，确认转录与笔记主链路，再逐项打开。</div>
            </div>
          </section>

        {:else if activeTab === "providers"}
          <section class="settings-pane">
            <div class="pane-head actions-head">
              <div><span>AI PROVIDERS</span><h2>AI 供应商</h2><p>管理笔记生成与视觉理解使用的模型服务。</p></div>
              <button class="btn btn-primary" onclick={openAddProvider}><Icon name="plus" size={15} />添加供应商</button>
            </div>

            <div class="provider-overview">
              <div><span class="overview-icon"><Icon name="server" size={18} /></span><strong>{providers.length}</strong><small>已配置供应商</small></div>
              <div><span class="overview-icon active"><Icon name="activity" size={18} /></span><strong>{settings.active_provider || "未设置"}</strong><small>当前活动供应商</small></div>
              <div><span class="overview-icon secure"><Icon name="key" size={18} /></span><strong>{providers.filter((p) => p.api_key_configured).length}</strong><small>已配置安全凭据</small></div>
            </div>

            <div class="provider-toolbar">
              <div class="provider-search input-wrap has-icon"><span class="input-icon"><Icon name="search" size={15} /></span><input type="search" bind:value={providerSearch} placeholder="搜索供应商、模型或类型" /></div>
              <span>{filteredProviders.length} 个配置</span>
            </div>

            {#if filteredProviders.length === 0}
              <EmptyState icon="bot" title={providerSearch ? "没有匹配的供应商" : "尚未配置 AI 供应商"} description={providerSearch ? "尝试更换搜索关键词。" : "添加 OpenAI 兼容、阿里云百炼或 MiMo 服务，开始生成 AI 笔记。"}>
                {#snippet action()}<button class="btn btn-primary btn-sm" onclick={openAddProvider}><Icon name="plus" size={14} />添加第一个供应商</button>{/snippet}
              </EmptyState>
            {:else}
              <div class="provider-grid">
                {#each filteredProviders as p (p.name)}
                  <article class="provider-card" class:active-provider={p.name === settings.active_provider}>
                    <header class="provider-head">
                      <div class="provider-avatar"><Icon name="bot" size={20} /></div>
                      <div class="provider-name"><div><h3>{p.name}</h3>{#if p.name === settings.active_provider}<StatusPill status="completed" label="活动" />{/if}</div><span>{providerTypeLabels[p.provider] || p.provider}</span></div>
                      <button class="icon-btn" onclick={() => openEditProvider(p)} title="编辑供应商"><Icon name="edit" size={14} /></button>
                    </header>

                    <div class="provider-models">
                      <div><span><Icon name="file-text" size={13} />文本模型</span><strong>{p.model || "未配置"}</strong></div>
                      <div><span><Icon name="eye" size={13} />视觉模型</span><strong>{p.vision_model || "跟随文本模型"}</strong></div>
                    </div>

                    <div class="provider-endpoint"><span>API ENDPOINT</span><code>{p.base_url || "供应商默认地址"}</code></div>

                    <div class="provider-key">
                      <span class="key-status" class:configured={p.api_key_configured}><Icon name="key" size={14} /><span><strong>{p.api_key_configured ? "API Key 已配置" : "尚未配置 API Key"}</strong><small>{p.api_key_preview || "凭据保存在本机安全存储"}</small></span></span>
                      {#if p.api_key_configured}<button class="text-danger" onclick={() => deleteApiKey(p.name)}>删除密钥</button>{/if}
                    </div>

                    <footer class="provider-footer">
                      <button class="btn btn-secondary btn-sm" onclick={() => testConnection(p)} disabled={testingProvider === p.name}><Icon name="activity" size={13} />{testingProvider === p.name ? "正在测试" : "测试连接"}</button>
                      {#if p.name !== settings.active_provider}<button class="btn btn-primary btn-sm" onclick={() => setActiveProvider(p.name)}><Icon name="check" size={13} />设为活动</button>{:else}<span class="active-note"><Icon name="check" size={13} />当前默认</span>{/if}
                      <button class="icon-btn delete-provider" onclick={() => deleteProvider(p.name)} title="删除供应商"><Icon name="trash" size={14} /></button>
                    </footer>
                  </article>
                {/each}
              </div>
            {/if}
          </section>

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
                        <div><span>版本</span><strong>{component.installed_version || component.version}</strong></div>
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
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component)} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
                        {/if}
                      </div>
                    </article>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="audio" size={18} /></div><div><h3>转写引擎</h3><p>使用 whisper.cpp native executable。</p></div></div>
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
                        <div><span>版本</span><strong>{component.installed_version || component.version}</strong></div>
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
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component)} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
                        {/if}
                      </div>
                    </article>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="package" size={18} /></div><div><h3>OCR 插件</h3><p>使用 Tesseract native executable。</p></div></div>
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
                        <div><span>版本</span><strong>{component.installed_version || component.version}</strong></div>
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
                          <button class="btn btn-secondary" type="button" onclick={() => removeComponent(component)} disabled={componentAction !== null}><Icon name="trash" size={14} />{componentAction === `remove:${component.component}` ? "卸载中" : "卸载"}</button>
                        {:else}
                          <button class="btn btn-primary" type="button" onclick={() => installComponent(component)} disabled={componentAction !== null}><Icon name="download" size={14} />{componentAction === `install:${component.component}` ? "安装中" : "安装"}</button>
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
              <div><span>STORAGE</span><h2>存储管理</h2><p>查看 AppData 状态、任务缓存、数据库和用户可见导出目录。</p></div>
              <button class="btn btn-secondary" type="button" onclick={refreshStorageStatus} disabled={storageLoading}><Icon name="refresh" size={15} />{storageLoading ? "刷新中" : "刷新"}</button>
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="database" size={18} /></div><div><h3>本机存储概览</h3><p>应用内部状态保存在 AppData；导出目录只保存最终笔记、转录、字幕和帧图。</p></div></div>
              {#if storageStatus}
                <div class="storage-grid">
                  <div><span>导出目录</span><strong>{formatBytes(storageStatus.sizes.export_bytes)}</strong><small>{storageStatus.export_dir}</small></div>
                  <div><span>应用数据库</span><strong>{formatBytes(storageStatus.sizes.db_bytes)}</strong><small>{storageStatus.db_path}</small></div>
                  <div><span>任务缓存</span><strong>{formatBytes(storageStatus.sizes.jobs_bytes + storageStatus.sizes.legacy_jobs_bytes)}</strong><small>{storageStatus.jobs_root}</small></div>
                  <div><span>Obsidian Vault</span><strong>{storageStatus.vault_path ? "已配置" : "未配置"}</strong><small>{storageStatus.vault_path || "未配置"}</small></div>
                </div>
              {:else}
                <div class="storage-empty-state"><span class="loading-ring compact"></span><div><strong>尚未读取存储状态</strong><small>点击刷新以查看本机缓存、数据库和导出目录占用。</small></div></div>
              {/if}
            </div>

            <div class="setting-group">
              <div class="group-head"><div class="group-icon"><Icon name="trash" size={18} /></div><div><h3>缓存清理</h3><p>只清理任务工作区缓存，不会删除已经导出的笔记文件。</p></div></div>
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
  <div class="modal-overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && closeProviderModal()}>
    <div class="modal-shell provider-modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title">
      <header class="modal-header">
        <div class="modal-title-wrap"><div class="modal-provider-icon"><Icon name="bot" size={21} /></div><div><span>{editingProviderName ? "EDIT PROVIDER" : "NEW PROVIDER"}</span><h2 id="provider-modal-title">{editingProviderName ? "编辑 AI 供应商" : "添加 AI 供应商"}</h2><p>配置文本生成、视觉理解模型与安全访问凭据。</p></div></div>
        <button class="icon-btn" onclick={closeProviderModal} aria-label="关闭弹窗"><Icon name="x" size={15} /></button>
      </header>

      <div class="modal-body provider-form">
        <section class="form-section">
          <div class="form-section-head"><span>01</span><div><h3>基础信息</h3><p>用于在应用内识别和管理此服务。</p></div></div>
          <div class="form-grid two-cols">
            <div class="field"><label class="field-label" for="prov_name">配置名称 <small>必填</small></label><input id="prov_name" type="text" bind:value={providerForm.name} disabled={!!editingProviderName} placeholder="例如：阿里云百炼 / Gemini / Claude" /></div>
            <div class="field"><label class="field-label" for="prov_type">API 类型</label><select id="prov_type" bind:value={providerForm.provider} onchange={handleProviderTypeChange}>{#each providerTypeOptions as option}<option value={option.id} disabled={!option.supported}>{option.label}{option.supported ? "" : "（暂不可用于自动任务）"}</option>{/each}</select><small class="field-help">{selectedProviderType.hint}</small></div>
          </div>
        </section>

        <section class="form-section">
          <div class="form-section-head provider-model-head"><span>02</span><div><h3>服务端点与模型</h3><p>先读取服务端模型列表，再选择文本与视觉模型；也可以手动输入模型 ID。</p></div><button class="btn btn-secondary btn-sm" type="button" onclick={discoverProviderModels} disabled={discoveringProviderModels}><Icon name="refresh" size={13} />{discoveringProviderModels ? "读取中" : "读取模型列表"}</button></div>
          {#if providerTypeUnsupported}
            <div class="provider-type-warning"><Icon name="alert" size={15} /><span>ChatGPT Codex (Plus/Pro) 属于 ChatGPT 账号内的 Codex 产品能力，不是可直接用 API Key 调用的服务端点；本应用当前不会把它作为自动笔记生成供应商。</span></div>
          {/if}
          <div class="field"><label class="field-label" for="prov_base_url">Base URL <small>{providerForm.provider === "google_gemini" ? "Gemini API 根地址" : providerForm.provider === "anthropic_messages" ? "Anthropic API 根地址" : providerForm.provider === "openai_responses" ? "OpenAI API 根地址" : "兼容接口通常以 /v1 结尾"}</small></label><div class="input-wrap has-icon"><span class="input-icon"><Icon name="server" size={15} /></span><input id="prov_base_url" type="text" bind:value={providerForm.base_url} placeholder={selectedProviderType.defaultBaseUrl || "由后续适配器提供"} disabled={providerTypeUnsupported} /></div></div>
          <div class="form-grid two-cols">
            <div class="field model-picker-field">
              <label class="field-label" for="prov_model">文本模型 <small>笔记生成</small></label>
              <select class="model-choice" aria-label="从服务端模型中选择文本模型" onchange={(event) => chooseProviderModel(event, "model")} disabled={providerTypeUnsupported || providerModelOptions.length === 0}>
                <option value="">{providerModelOptions.length ? "从已读取列表选择…" : "请先读取模型列表"}</option>
                {#each providerModelOptions as model}<option value={model}>{model}</option>{/each}
              </select>
              <div class="input-wrap has-icon"><span class="input-icon"><Icon name="file-text" size={15} /></span><input id="prov_model" type="text" bind:value={providerForm.model} placeholder="输入精确模型 ID" disabled={providerTypeUnsupported} /></div>
              <small class="field-help">当前值会实际用于笔记生成，不要填写模型数量、菜单标题等展示文字。</small>
            </div>
            <div class="field model-picker-field">
              <label class="field-label" for="prov_vision_model">视觉模型 <small>画面理解</small></label>
              <select class="model-choice" aria-label="从服务端模型中选择视觉模型" onchange={(event) => chooseProviderModel(event, "vision_model")} disabled={providerTypeUnsupported || providerModelOptions.length === 0}>
                <option value="">{providerModelOptions.length ? "从已读取列表选择…" : "请先读取模型列表"}</option>
                {#each providerModelOptions as model}<option value={model}>{model}</option>{/each}
              </select>
              <div class="input-wrap has-icon"><span class="input-icon"><Icon name="eye" size={15} /></span><input id="prov_vision_model" type="text" bind:value={providerForm.vision_model} placeholder={providerForm.model || "输入视觉模型 ID"} disabled={providerTypeUnsupported} /></div>
              <small class="field-help">服务没有独立视觉模型时，可与文本模型保持相同。</small>
            </div>
          </div>
        </section>

        <section class="form-section secure-section">
          <div class="form-section-head"><span>03</span><div><h3>安全凭据</h3><p>{editingProviderName ? "留空将保留现有 API Key。" : "凭据只保存在本机安全存储，不会进入任务快照。"}</p></div><span class="secure-badge"><Icon name="shield" size={13} />本机加密</span></div>
          <div class="field"><label class="field-label" for="prov_api_key">API Key {#if editingProviderName}<small>可选更新</small>{/if}</label><div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="prov_api_key" type="password" bind:value={providerForm.api_key} placeholder={editingProviderName ? "输入新密钥以替换，或留空保持不变" : "sk-..."} disabled={providerTypeUnsupported} /></div></div>
        </section>
      </div>

      <footer class="modal-footer provider-modal-footer"><span><Icon name="info" size={14} />保存后可在供应商卡片上测试连接。</span><div><button class="btn btn-secondary" onclick={closeProviderModal}>取消</button><button class="btn btn-primary" onclick={saveProvider} disabled={providerSaving || providerTypeUnsupported || !providerForm.name.trim() || !providerForm.model.trim()}><Icon name="save" size={15} />{providerSaving ? "正在保存" : "保存供应商"}</button></div></footer>
    </div>
  </div>
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
  .group-head.with-action { display: grid; grid-template-columns: 38px minmax(0,1fr) auto; }
  .group-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .group-head > div:nth-child(2) { display: flex; flex-direction: column; }
  .group-head h3 { font-size: 14px; }
  .group-head p { margin-top: 3px; color: var(--text-secondary); font-size: 12px; }
  .form-grid { display: grid; gap: 14px; }
  .form-grid.two-cols { grid-template-columns: 1fr 1fr; }

  .model-cards { display: grid; grid-template-columns: repeat(5,1fr); gap: 8px; }
  .model-card { position: relative; display: flex; min-height: 105px; flex-direction: column; align-items: flex-start; padding: 12px; border: 1px solid var(--border-color); border-radius: 12px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s, box-shadow .14s; }
  .model-card:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .model-card.selected { border-color: var(--accent-color); background: var(--accent-faint); box-shadow: 0 0 0 3px var(--accent-glow); }
  .model-check { display: grid; place-items: center; width: 17px; height: 17px; margin-bottom: 11px; border: 1.5px solid var(--border-strong); border-radius: 50%; color: #fff; }
  .selected .model-check { border-color: var(--accent-color); background: var(--accent-color); }
  .model-card strong { font-size: 14px; }
  .model-card small { margin-top: 2px; color: var(--text-tertiary); font-size: 12px; }
  .quality-bars { display: flex; gap: 3px; margin-top: auto; }
  .quality-bars i { width: 10px; height: 3px; border-radius: 99px; background: var(--bg-progress); }
  .quality-bars i.active { background: var(--accent-color); }
  .scan-results { display: flex; align-items: center; gap: 6px; margin-top: 11px; padding: 8px 10px; border-radius: 8px; color: var(--success-color); background: var(--success-soft); font-size: 12px; }
  .setting-toggle-card { display: flex; align-items: center; gap: 12px; width: 100%; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s; }
  .setting-toggle-card.as-button { appearance: none; font: inherit; }
  .enhancement-settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .enhancement-explain, .provider-type-warning { display: flex; align-items: flex-start; gap: 8px; margin-top: 10px; padding: 10px 12px; border-radius: 10px; color: var(--text-secondary); background: var(--bg-subtle); border: 1px solid var(--border-color); font-size: 12px; line-height: 1.55; }
  .provider-type-warning { margin: 0; color: var(--warning-color); background: var(--warning-soft); border-color: color-mix(in srgb, var(--warning-color) 25%, var(--border-color)); }
  .setting-toggle-card.enabled { border-color: color-mix(in srgb, var(--accent-color) 50%, var(--border-color)); background: var(--accent-faint); }
  .toggle-feature-icon { display: grid; place-items: center; width: 41px; height: 41px; border-radius: 12px; color: var(--text-secondary); background: var(--bg-muted); }
  .enabled .toggle-feature-icon { color: var(--accent-color); background: var(--accent-soft); }
  .toggle-copy { display: flex; flex: 1; flex-direction: column; }
  .toggle-copy strong { font-size: 14px; }
  .toggle-copy small { margin-top: 3px; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }

  .provider-overview { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin: 18px 0; }
  .provider-overview > div { display: grid; grid-template-columns: 38px minmax(0,1fr); grid-template-rows: auto auto; align-items: center; column-gap: 10px; padding: 12px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .overview-icon { grid-row: 1 / 3; display: grid; place-items: center; width: 38px; height: 38px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .overview-icon.active { color: var(--success-color); background: var(--success-soft); }
  .overview-icon.secure { color: var(--warning-color); background: var(--warning-soft); }
  .provider-overview strong { overflow: hidden; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-overview small { color: var(--text-tertiary); font-size: 12px; }
  .provider-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
  .provider-search { width: 280px; }
  .provider-search input { min-height: 35px; font-size: 13px; }
  .provider-toolbar > span { color: var(--text-tertiary); font-size: 12px; }
  .provider-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px; }
  .provider-card { display: flex; min-width: 0; flex-direction: column; overflow: hidden; border: 1px solid var(--border-color); border-radius: 14px; background: var(--bg-card); box-shadow: var(--shadow-xs); transition: border-color .14s, box-shadow .14s, transform .14s; }
  .provider-card:hover { transform: translateY(-1px); border-color: var(--border-strong); box-shadow: var(--shadow-sm); }
  .provider-card.active-provider { border-color: color-mix(in srgb, var(--accent-color) 55%, var(--border-color)); box-shadow: 0 0 0 3px var(--accent-glow); }
  .provider-head { display: grid; grid-template-columns: 42px minmax(0,1fr) 36px; align-items: center; gap: 10px; padding: 14px; }
  .provider-avatar { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 13px; color: var(--accent-color); background: var(--accent-soft); }
  .provider-name { display: flex; min-width: 0; flex-direction: column; }
  .provider-name > div { display: flex; align-items: center; gap: 7px; min-width: 0; }
  .provider-name h3 { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-name > span { margin-top: 3px; color: var(--text-tertiary); font-size: 12px; }

  .provider-model-head { grid-template-columns: 30px minmax(0,1fr); align-items: start; }
  .provider-model-head .btn { grid-column: 2; justify-self: start; min-height: 34px; margin-top: 9px; white-space: nowrap; }
  .model-picker-field { gap: 8px; }
  .model-choice { width: 100%; min-height: 40px; padding: 0 36px 0 11px; border: 1px solid var(--border-color); border-radius: 9px; color: var(--text-primary); background: var(--bg-card); font: inherit; }
  .model-choice:disabled { color: var(--text-tertiary); background: var(--bg-subtle); cursor: not-allowed; }
  .field-help { display: block; color: var(--text-tertiary); font-size: 12px; line-height: 1.5; }
  .provider-models { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 0 14px 12px; }
  .provider-models > div { display: flex; min-width: 0; flex-direction: column; padding: 9px; border-radius: 9px; background: var(--bg-subtle); }
  .provider-models span { display: flex; align-items: center; gap: 5px; color: var(--text-tertiary); font-size: 11px; }
  .provider-models strong { margin-top: 4px; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-endpoint { display: flex; flex-direction: column; margin: 0 14px 12px; padding: 9px; border: 1px solid var(--border-color); border-radius: 9px; }
  .provider-endpoint span { color: var(--text-tertiary); font-size: 11px; font-weight: 750; letter-spacing: .08em; }
  .provider-endpoint code { margin-top: 4px; overflow: hidden; color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-key { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 14px 13px; }
  .key-status { display: flex; align-items: center; gap: 7px; color: var(--text-tertiary); }
  .key-status.configured { color: var(--success-color); }
  .key-status > span { display: flex; flex-direction: column; }
  .key-status strong { color: var(--text-secondary); font-size: 12px; }
  .key-status small { margin-top: 2px; color: var(--text-tertiary); font-size: 11px; }
  .text-danger { border: 0; color: var(--danger-color); background: transparent; cursor: pointer; font-size: 11px; }
  .provider-footer { display: flex; align-items: center; gap: 7px; margin-top: auto; padding: 10px 14px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .active-note { display: flex; align-items: center; gap: 5px; margin-left: auto; color: var(--success-color); font-size: 12px; font-weight: 650; }
  .delete-provider { width: 32px; height: 32px; margin-left: auto; color: var(--text-tertiary); }
  .delete-provider:hover { color: var(--danger-color); background: var(--danger-soft); }

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

  .provider-modal { width: min(780px, calc(100vw - 48px)); }
  .modal-title-wrap { display: flex; align-items: flex-start; gap: 11px; }
  .modal-provider-icon { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 13px; color: var(--accent-color); background: var(--accent-soft); }
  .modal-title-wrap > div:last-child { display: flex; flex-direction: column; }
  .modal-title-wrap > div:last-child > span { color: var(--accent-color); font-size: 11px; font-weight: 800; letter-spacing: .12em; }
  .provider-form { display: flex; flex-direction: column; gap: 18px; }
  .form-section { padding-bottom: 18px; border-bottom: 1px solid var(--border-color); }
  .form-section:last-child { padding-bottom: 0; border-bottom: 0; }
  .form-section-head { display: grid; grid-template-columns: 30px minmax(0,1fr); align-items: start; gap: 10px; margin-bottom: 13px; }
  .form-section-head > span:first-child { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 9px; color: var(--accent-color); background: var(--accent-soft); font-size: 12px; font-weight: 800; }
  .form-section-head > div { display: flex; flex-direction: column; }
  .form-section-head h3 { font-size: 14px; }
  .form-section-head p { margin-top: 2px; color: var(--text-secondary); font-size: 12px; }
  .secure-section .form-section-head { grid-template-columns: 30px minmax(0,1fr) auto; }
  .secure-badge { display: flex !important; align-items: center; gap: 5px; width: auto !important; height: 26px !important; padding: 0 8px; border-radius: 8px !important; color: var(--success-color) !important; background: var(--success-soft) !important; font-size: 11px !important; letter-spacing: 0 !important; }
  .provider-modal-footer { justify-content: space-between; }
  .provider-modal-footer > span { display: flex; align-items: center; gap: 6px; color: var(--text-tertiary); font-size: 12px; }
  .provider-modal-footer > div { display: flex; gap: 8px; }

  @media (max-width: 1120px) {
    .settings-shell { grid-template-columns: 210px minmax(0,1fr); }
    .provider-grid, .template-grid { grid-template-columns: 1fr; }
    .model-cards { grid-template-columns: repeat(3,1fr); }
  }

  .settings-page { max-width: 1280px; }
  .settings-shell { grid-template-columns: 220px minmax(0,1fr); min-height: 620px; border-radius: 16px; box-shadow: var(--shadow-sm); }
  .settings-nav { padding: 13px 9px; background: var(--bg-sidebar); }
  .settings-nav > button { grid-template-columns: 31px minmax(0,1fr) 14px; padding: 8px; border-radius: 10px; }
  .settings-nav > button.active { box-shadow: none; }
  .tab-icon { width: 30px; height: 30px; border-radius: 9px; }
  .settings-pane { padding: 21px 23px 29px; }
  .pane-head { padding-bottom: 17px; }
  .pane-head h2 { font-size: 21px; }
  .setting-group { padding: 19px 0; }
  .group-icon { width: 35px; height: 35px; border-radius: 10px; }
  .model-cards { padding: 4px; gap: 4px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-subtle); }
  .model-card { min-height: 94px; padding: 10px; border-color: transparent; border-radius: 9px; background: transparent; }
  .model-card.selected { border-color: var(--border-color); background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .provider-modal { width: min(740px, calc(100vw - 48px)); }
  .form-section { border-radius: 13px; }


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
  .model-cards { gap: 9px; }
  .model-card { min-height: 110px; padding: 14px; }
  .model-card strong { font-size: 14px; }
  .model-card small { font-size: 11px; }
  .setting-toggle-card { min-height: 76px; padding: 16px; }
  .toggle-copy strong { font-size: 14px; }
  .toggle-copy small { font-size: 12px; }
  .provider-overview > div { min-height: 76px; padding: 14px; }
  .provider-overview strong { font-size: 18px; }
  .provider-overview small { font-size: 11px; }
  .provider-search input { min-height: 42px; font-size: 13px; }
  .provider-card { border-radius: 14px; }
  .provider-head { padding: 17px; }
  .provider-name h3 { font-size: 15px; }
  .provider-name > span { font-size: 11px; }
  .provider-models { padding: 0 17px 14px; }
  .provider-models span { font-size: 11px; }
  .provider-models strong { font-size: 13px; }
  .provider-endpoint { margin: 0 17px 14px; padding: 11px; }
  .provider-endpoint span { font-size: 10px; }
  .provider-endpoint code { font-size: 11px; }
  .provider-key { margin: 0 17px 15px; }
  .provider-footer { padding: 12px 17px; }
  .template-card { min-height: 126px; padding: 16px; }
  .template-card-copy strong { font-size: 14px; }
  .template-card-copy p { font-size: 12px; }
  .check-row { min-height: 62px; padding: 12px 14px; }
  .check-name strong { font-size: 13px; }
  .check-row p { font-size: 12px; }
  .provider-modal { width: min(820px, calc(100vw - 48px)); }
  .form-section-head h3 { font-size: 15px; }
  .form-section-head p { font-size: 12px; }

  @media (max-width: 1180px) {
    .settings-shell { grid-template-columns: 220px minmax(0,1fr); }
    .settings-pane { padding: 26px; }
    .provider-grid, .template-grid { grid-template-columns: 1fr; }
  }


  /* Local model interaction — scanned results are real controls, not status text. */
  .model-scan-state,
  .model-empty-state,
  .model-selection-summary { display: flex; align-items: center; gap: 12px; padding: 14px 15px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .model-scan-state > div,
  .model-empty-state > div,
  .model-selection-summary > div { display: flex; min-width: 0; flex: 1; flex-direction: column; }
  .model-scan-state strong,
  .model-empty-state strong,
  .model-selection-summary strong { font-size: 14px; }
  .model-scan-state small,
  .model-selection-summary small { margin-top: 3px; overflow: hidden; color: var(--text-secondary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .model-empty-state p { margin-top: 4px; color: var(--text-secondary); font-size: 12px; }
  .model-empty-state code { padding: 2px 5px; border-radius: 5px; color: var(--accent-color); background: var(--accent-soft); }
  .model-empty-icon,
  .selection-status { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 auto; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .model-selection-summary .selection-status { color: var(--success-color); background: var(--success-soft); }
  .model-selection-summary.warning { border-color: color-mix(in srgb, var(--warning-color) 35%, var(--border-color)); background: var(--warning-soft); }
  .model-selection-summary.warning .selection-status { color: var(--warning-color); background: color-mix(in srgb, var(--warning-color) 12%, transparent); }
  .installed-count { flex: 0 0 auto; padding: 5px 8px; border-radius: 99px; color: var(--success-color); background: var(--success-soft); font-size: 11px; font-weight: 750; }
  .interactive-model-cards { grid-template-columns: repeat(3, minmax(0,1fr)); padding: 0; border: 0; background: transparent; }
  .interactive-model-cards .model-card { position: relative; min-height: 132px; padding: 15px; border: 1px solid var(--border-color); background: var(--bg-card); }
  .interactive-model-cards .model-card:hover:not(:disabled) { border-color: var(--accent-color); background: var(--accent-faint); }
  .interactive-model-cards .model-card.selected { border-color: var(--accent-color); background: var(--accent-faint); box-shadow: 0 0 0 3px var(--accent-glow); }
  .interactive-model-cards .model-card.unavailable { opacity: .46; cursor: not-allowed; }
  .model-availability { position: absolute; top: 12px; right: 12px; padding: 3px 7px; border-radius: 99px; color: var(--text-tertiary); background: var(--bg-muted); font-size: 10px; font-weight: 750; }
  .model-availability.installed { color: var(--success-color); background: var(--success-soft); }
  .model-id { margin-top: 6px; overflow: hidden; color: var(--text-tertiary); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 1180px) { .interactive-model-cards { grid-template-columns: repeat(2, minmax(0,1fr)); } }
  .runtime-settings-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
  .plugin-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
  .plugin-card { display: flex; min-width: 0; flex-direction: column; gap: 13px; padding: 15px; border: 1px solid var(--border-color); border-radius: 14px; background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .plugin-card.installed { border-color: color-mix(in srgb, var(--success-color) 28%, var(--border-color)); }
  .plugin-card-head { display: grid; grid-template-columns: 42px minmax(0,1fr) auto; align-items: center; gap: 11px; }
  .plugin-icon { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 12px; color: var(--accent-color); background: var(--accent-soft); }
  .plugin-title { display: flex; min-width: 0; flex-direction: column; }
  .plugin-title strong { overflow: hidden; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-title small { margin-top: 3px; overflow: hidden; color: var(--text-secondary); font-size: 12px; line-height: 1.45; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .plugin-meta > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; padding: 10px; border-radius: 10px; background: var(--bg-subtle); }
  .plugin-meta span, .plugin-path span { color: var(--text-tertiary); font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; }
  .plugin-meta strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-path { display: flex; min-width: 0; flex-direction: column; gap: 5px; padding: 10px; border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-subtle); }
  .plugin-path code { overflow: hidden; color: var(--text-secondary); font-family: var(--font-mono); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .plugin-warning { display: flex; align-items: center; gap: 7px; padding: 9px 10px; border-radius: 10px; color: var(--warning-color); background: var(--warning-soft); font-size: 12px; }
  .plugin-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: auto; }
  .plugin-actions .btn { min-height: 40px; }
  .plugin-empty-state { display: flex; align-items: center; gap: 12px; min-height: 78px; margin-top: 14px; padding: 14px 15px; border: 1px dashed var(--border-strong); border-radius: 12px; color: var(--text-secondary); background: var(--bg-subtle); }
  .plugin-empty-state > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  .plugin-empty-state strong { color: var(--text-primary); font-size: 14px; }
  .plugin-empty-state small { font-size: 12px; }
  .storage-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
  .storage-grid > div { display: flex; min-width: 0; flex-direction: column; gap: 4px; padding: 12px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .storage-grid span { color: var(--text-tertiary); font-size: 11px; font-weight: 750; }
  .storage-grid strong { font-size: 15px; }
  .storage-grid small { overflow: hidden; color: var(--text-secondary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .storage-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .storage-empty-state { display: flex; align-items: center; gap: 12px; min-height: 72px; margin-top: 14px; padding: 14px 15px; border: 1px dashed var(--border-strong); border-radius: 12px; color: var(--text-secondary); background: var(--bg-subtle); }
  .storage-empty-state > div { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
  .storage-empty-state strong { color: var(--text-primary); font-size: 14px; }
  .storage-empty-state small { font-size: 12px; }
  @media (max-width: 760px) { .runtime-settings-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .plugin-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .storage-grid { grid-template-columns: 1fr; } }
</style>
