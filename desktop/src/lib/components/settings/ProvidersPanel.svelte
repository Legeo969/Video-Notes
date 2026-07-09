<script lang="ts">
  import type { ProviderProfile } from "../../types";
  import Icon from "../Icon.svelte";
  import EmptyState from "../EmptyState.svelte";
  import StatusPill from "../StatusPill.svelte";

  const providerTypeLabels: Record<string, string> = {
    openai_compat: "OpenAI Compatible",
    google_gemini: "Google Gemini",
    anthropic_messages: "Anthropic Messages",
    openai_responses: "OpenAI Responses",
    mimo: "OpenAI Compatible",
    dashscope: "OpenAI Compatible",
    openai: "OpenAI Compatible",
    llama_cpp: "OpenAI Compatible",
  };

  let {
    providers,
    activeProvider,
    testingProvider,
    clearingCapabilities,
    providerSearch = $bindable(""),
    onSetActive,
    onTestConnection,
    onClearCapabilities,
    onOpenAddProvider,
    onOpenEditProvider,
    onDeleteProvider,
    onDeleteApiKey,
  }: {
    providers: ProviderProfile[];
    activeProvider: string;
    testingProvider: string | null;
    clearingCapabilities: string | null;
    providerSearch?: string;
    onSetActive: (name: string) => void;
    onTestConnection: (p: ProviderProfile) => void;
    onClearCapabilities: (name: string) => void;
    onOpenAddProvider: () => void;
    onOpenEditProvider: (p: ProviderProfile) => void;
    onDeleteProvider: (name: string) => void;
    onDeleteApiKey: (name: string) => void;
  } = $props();

  let filteredProviders = $derived.by(() => {
    const q = providerSearch.trim().toLowerCase();
    if (!q) return providers;
    return providers.filter((p) =>
      [p.name, p.provider, p.model, p.vision_model || ""]
        .some((v) => v.toLowerCase().includes(q))
    );
  });

  function providerVisionModel(p: ProviderProfile) {
    return (p.vision_model || p.model || "").trim();
  }

  function providerVisionCapability(p: ProviderProfile) {
    const model = providerVisionModel(p);
    return model ? p.capabilities?.[model] : undefined;
  }

  function providerVisionStatus(p: ProviderProfile) {
    return providerVisionCapability(p)?.vision ?? "unknown";
  }

  function providerVisionStatusLabel(p: ProviderProfile) {
    const status = providerVisionStatus(p);
    return status === "pass" ? "pass" : status === "fail" ? "fail" : "unknown";
  }

  function providerVisionDetail(p: ProviderProfile) {
    const capability = providerVisionCapability(p);
    if (!capability) return "尚未测试";
    const summary = capability.error || capability.message || "无摘要";
    const time = capability.last_tested_at
      ? new Date(capability.last_tested_at).toLocaleString("zh-CN")
      : "未知时间";
    return `${time} · ${summary}`;
  }
</script>

<section class="settings-pane">
  <div class="pane-head actions-head">
    <div>
      <span>AI PROVIDERS</span>
      <h2>AI 供应商</h2>
      <p>管理笔记生成与视觉理解使用的模型服务。</p>
    </div>
    <button class="btn btn-primary" onclick={onOpenAddProvider}>
      <Icon name="plus" size={15} />添加供应商
    </button>
  </div>

  <div class="provider-overview">
    <div>
      <span class="overview-icon"><Icon name="server" size={18} /></span>
      <strong>{providers.length}</strong>
      <small>已配置供应商</small>
    </div>
    <div>
      <span class="overview-icon active"><Icon name="activity" size={18} /></span>
      <strong>{activeProvider || "未设置"}</strong>
      <small>当前活动供应商</small>
    </div>
    <div>
      <span class="overview-icon secure"><Icon name="key" size={18} /></span>
      <strong>{providers.filter((p) => p.api_key_configured).length}</strong>
      <small>已配置安全凭据</small>
    </div>
  </div>

  <div class="provider-toolbar">
    <div class="provider-search input-wrap has-icon">
      <span class="input-icon"><Icon name="search" size={15} /></span>
      <input type="search" bind:value={providerSearch} placeholder="搜索供应商、模型或类型" />
    </div>
    <span>{filteredProviders.length} 个配置</span>
  </div>

  {#if filteredProviders.length === 0}
    <EmptyState
      icon="bot"
      title={providerSearch ? "没有匹配的供应商" : "尚未配置 AI 供应商"}
      description={providerSearch ? "尝试更换搜索关键词。" : "添加 OpenAI 兼容、阿里云百炼或 MiMo 服务，开始生成 AI 笔记。"}
    >
      {#snippet action()}
        <button class="btn btn-primary btn-sm" onclick={onOpenAddProvider}>
          <Icon name="plus" size={14} />添加第一个供应商
        </button>
      {/snippet}
    </EmptyState>
  {:else}
    <div class="provider-grid">
      {#each filteredProviders as p (p.name)}
        <article class="provider-card" class:active-provider={p.name === activeProvider}>
          <header class="provider-head">
            <div class="provider-avatar"><Icon name="bot" size={20} /></div>
            <div class="provider-name">
              <div>
                <h3>{p.name}</h3>
                {#if p.name === activeProvider}
                  <StatusPill status="completed" label="活动" />
                {/if}
              </div>
              <span>{providerTypeLabels[p.provider] || p.provider}</span>
            </div>
            <button class="icon-btn" onclick={() => onOpenEditProvider(p)} title="编辑供应商">
              <Icon name="edit" size={14} />
            </button>
          </header>

          <div class="provider-models">
            <div>
              <span><Icon name="file-text" size={13} />文本模型</span>
              <strong>{p.model || "未配置"}</strong>
            </div>
            <div>
              <span><Icon name="eye" size={13} />视觉模型</span>
              <strong>{p.vision_model || "跟随文本模型"}</strong>
            </div>
          </div>

          <div class="provider-capability" class:pass={providerVisionStatus(p) === "pass"} class:fail={providerVisionStatus(p) === "fail"}>
            <span>vision capability</span>
            <strong>{providerVisionStatusLabel(p)}</strong>
            <small>{providerVisionDetail(p)}</small>
          </div>

          <div class="provider-endpoint">
            <span>API ENDPOINT</span>
            <code>{p.base_url || "供应商默认地址"}</code>
          </div>

          <div class="provider-key">
            <span class="key-status" class:configured={p.api_key_configured}>
              <Icon name="key" size={14} />
              <span>
                <strong>{p.api_key_configured ? "API Key 已配置" : "尚未配置 API Key"}</strong>
                <small>{p.api_key_preview || "凭据保存在本机安全存储"}</small>
              </span>
            </span>
            {#if p.api_key_configured}
              <button class="text-danger" onclick={() => onDeleteApiKey(p.name)}>删除密钥</button>
            {/if}
          </div>

          <footer class="provider-footer">
            <button class="btn btn-secondary btn-sm" onclick={() => onTestConnection(p)} disabled={testingProvider === p.name}>
              <Icon name="activity" size={13} />{testingProvider === p.name ? "正在测试" : "测试连接"}
            </button>
            <button class="btn btn-secondary btn-sm" onclick={() => onClearCapabilities(p.name)} disabled={clearingCapabilities === p.name}>
              <Icon name="refresh" size={13} />{clearingCapabilities === p.name ? "清除中" : "清除能力缓存"}
            </button>
            {#if p.name !== activeProvider}
              <button class="btn btn-primary btn-sm" onclick={() => onSetActive(p.name)}>
                <Icon name="check" size={13} />设为活动
              </button>
            {:else}
              <span class="active-note"><Icon name="check" size={13} />当前默认</span>
            {/if}
            <button class="icon-btn delete-provider" onclick={() => onDeleteProvider(p.name)} title="删除供应商">
              <Icon name="trash" size={14} />
            </button>
          </footer>
        </article>
      {/each}
    </div>
  {/if}
</section>

<style>
  .provider-overview { display: grid; grid-template-columns: repeat(3,1fr); gap: 8px; margin: 16px 0; }
  .provider-overview > div { display: grid; grid-template-columns: 34px minmax(0,1fr); grid-template-rows: auto auto; align-items: center; column-gap: 9px; padding: 10px; border-radius: 10px; background: var(--bg-subtle); }
  .overview-icon { grid-row: 1 / 3; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .overview-icon.active { color: var(--success-color); background: var(--success-soft); }
  .overview-icon.secure { color: var(--warning-color); background: var(--warning-soft); }
  .provider-overview strong { overflow: hidden; color: var(--text-primary); font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-overview small { color: var(--text-tertiary); font-size: 11px; }
  .provider-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
  .provider-search { width: 280px; }
  .provider-search input { min-height: 35px; font-size: 13px; }
  .provider-toolbar > span { color: var(--text-tertiary); font-size: 12px; }
  .provider-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; }
  .provider-card { display: flex; min-width: 0; flex-direction: column; overflow: hidden; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-card); box-shadow: var(--shadow-xs); transition: border-color .14s, box-shadow .14s; }
  .provider-card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-sm); }
  .provider-card.active-provider { border-color: color-mix(in srgb, var(--accent-color) 45%, var(--border-color)); box-shadow: 0 0 0 3px var(--accent-glow); }
  .provider-head { display: grid; grid-template-columns: 40px minmax(0,1fr) 34px; align-items: center; gap: 10px; padding: 14px 14px 0; }
  .provider-avatar { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .provider-name { display: flex; min-width: 0; flex-direction: column; }
  .provider-name > div { display: flex; align-items: center; gap: 7px; min-width: 0; }
  .provider-name h3 { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-name > span { margin-top: 2px; color: var(--text-tertiary); font-size: 11px; }
  .provider-models { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding: 10px 14px 0; }
  .provider-models > div { display: flex; min-width: 0; flex-direction: column; padding: 8px; border-radius: 8px; background: var(--bg-subtle); }
  .provider-models span { display: flex; align-items: center; gap: 4px; color: var(--text-tertiary); font-size: 11px; }
  .provider-models strong { margin-top: 3px; overflow: hidden; color: var(--text-primary); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-capability { display: grid; grid-template-columns: auto auto; gap: 3px 8px; align-items: center; margin: 10px 14px 0; padding: 8px 10px; border-radius: 8px; color: var(--text-tertiary); background: var(--bg-subtle); font-size: 11px; }
  .provider-capability strong { justify-self: start; padding: 2px 7px; border-radius: 999px; color: var(--text-tertiary); background: var(--bg-hover); font-size: 11px; }
  .provider-capability small { grid-column: 1 / -1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .provider-capability.pass strong { color: var(--success-color); background: var(--success-soft); }
  .provider-capability.fail strong { color: var(--danger-color); background: var(--danger-soft); }
  .provider-endpoint { display: flex; flex-direction: column; margin: 10px 14px 0; padding: 8px 10px; border-radius: 8px; background: var(--bg-subtle); }
  .provider-endpoint span { color: var(--text-tertiary); font-size: 10px; font-weight: 750; letter-spacing: .08em; }
  .provider-endpoint code { margin-top: 3px; overflow: hidden; color: var(--text-tertiary); font-family: var(--font-mono); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .provider-key { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 10px 14px 0; }
  .key-status { display: flex; align-items: center; gap: 6px; color: var(--text-tertiary); }
  .key-status.configured { color: var(--success-color); }
  .key-status > span { display: flex; flex-direction: column; }
  .key-status strong { color: var(--text-secondary); font-size: 12px; }
  .key-status small { margin-top: 1px; color: var(--text-tertiary); font-size: 11px; }
  .text-danger { border: 0; color: var(--danger-color); background: transparent; cursor: pointer; font-size: 11px; }
  .provider-footer { display: flex; align-items: center; gap: 6px; margin-top: auto; padding: 10px 14px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .active-note { display: flex; align-items: center; gap: 4px; margin-left: auto; color: var(--success-color); font-size: 12px; font-weight: 650; }
  .delete-provider { width: 32px; height: 32px; margin-left: auto; color: var(--text-tertiary); }
  .delete-provider:hover { color: var(--danger-color); background: var(--danger-soft); }

  .provider-overview > div { min-height: 76px; padding: 14px; }
  .provider-overview strong { font-size: 18px; }
  .provider-overview small { font-size: 11px; }
  .provider-search input { min-height: 42px; font-size: 13px; }
  .provider-card { border-radius: 13px; }
  .provider-head { padding: 14px 14px 0; }
  .provider-name h3 { font-size: 14px; }
  .provider-name > span { font-size: 11px; }
  .provider-models { padding: 10px 14px 0; gap: 6px; }
  .provider-models span { font-size: 11px; }
  .provider-models strong { font-size: 13px; }
  .provider-endpoint { margin: 10px 14px 0; padding: 8px 10px; }
  .provider-endpoint span { font-size: 10px; }
  .provider-endpoint code { font-size: 11px; }
  .provider-key { margin: 10px 14px 0; }
  .provider-footer { padding: 10px 14px; }

  @media (max-width: 1180px) {
    .provider-grid { grid-template-columns: 1fr; }
  }

  @media (max-width: 960px) {
    .provider-grid { grid-template-columns: 1fr; }
    .provider-overview { grid-template-columns: 1fr; }
    .provider-toolbar { flex-direction: column; gap: 8px; }
    .provider-search { width: 100%; }
  }
</style>
