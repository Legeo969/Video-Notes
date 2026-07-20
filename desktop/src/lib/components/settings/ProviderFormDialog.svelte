<script lang="ts">
  import Icon from "../Icon.svelte";

  interface ProviderForm {
    name: string;
    provider: string;
    api_key: string;
    base_url: string;
    model: string;
    vision_model: string;
    video_input: boolean;
  }

  const providerTypeOptions = [
    {
      id: "openai_compat",
      label: "OpenAI Compatible",
      hint: "Chat Completions 兼容接口，例如 OpenAI 兼容网关、阿里云百炼兼容模式。",
      defaultBaseUrl: "https://api.openai.com/v1",
      defaultModel: "gpt-4o-mini",
      defaultVisionModel: "gpt-4o-mini",
      supported: true,
    },
    {
      id: "google_gemini",
      label: "Google Gemini",
      hint: "Google Generative Language API，使用 gemini-* 模型。",
      defaultBaseUrl: "https://generativelanguage.googleapis.com/v1beta",
      defaultModel: "gemini-2.5-flash",
      defaultVisionModel: "gemini-2.5-flash",
      supported: false,
    },
    {
      id: "anthropic_messages",
      label: "Anthropic Messages",
      hint: "Anthropic /v1/messages 接口（兼容 MiniMax M3 等扩展支持视频的端点）。",
      defaultBaseUrl: "https://api.anthropic.com/v1",
      defaultModel: "claude-sonnet-4-5",
      defaultVisionModel: "claude-sonnet-4-5",
      supported: true,
    },
    {
      id: "openai_responses",
      label: "OpenAI Responses",
      hint: "OpenAI /v1/responses 接口，适合新版 OpenAI API 调用。",
      defaultBaseUrl: "https://api.openai.com/v1",
      defaultModel: "gpt-5.5",
      defaultVisionModel: "gpt-5.5",
      supported: false,
    },
  ];

  let {
    show,
    editingProviderName,
    providerForm = $bindable(),
    providerSaving,
    providerModelOptions,
    discoveringProviderModels,
    onClose,
    onSave,
    onDiscoverModels,
    onChooseModel,
  }: {
    show: boolean;
    editingProviderName: string | null;
    providerForm: ProviderForm;
    providerSaving: boolean;
    providerModelOptions: string[];
    discoveringProviderModels: boolean;
    onClose: () => void;
    onSave: () => void;
    onDiscoverModels: () => void;
    onChooseModel: (event: Event, field: "model" | "vision_model") => void;
  } = $props();

  let selectedProviderType = $derived(
    providerTypeOptions.find((item) => item.id === providerForm.provider) ?? providerTypeOptions[0]
  );
  let providerTypeUnsupported = $derived(!selectedProviderType.supported);

  function handleProviderTypeChange() {
    providerForm.model = "";
    providerForm.vision_model = "";
    providerForm.video_input = false;
  }
</script>

{#if show}
  <div class="modal-overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && onClose()}>
    <div class="modal-shell provider-modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title">
      <header class="modal-header">
        <div class="modal-title-wrap">
          <div class="modal-provider-icon"><Icon name="bot" size={21} /></div>
          <div>
            <span>{editingProviderName ? "EDIT PROVIDER" : "NEW PROVIDER"}</span>
            <h2 id="provider-modal-title">{editingProviderName ? "编辑 AI 供应商" : "添加 AI 供应商"}</h2>
            <p>配置文本生成、视觉理解模型与安全访问凭据。</p>
          </div>
        </div>
        <button class="icon-btn" onclick={onClose} aria-label="关闭弹窗"><Icon name="x" size={15} /></button>
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
          <div class="form-section-head provider-model-head"><span>02</span><div><h3>服务端点与模型</h3><p>可从供应商真实 `/models` 接口读取模型；读取失败不会伪造列表，也不会覆盖手动输入。</p></div><button class="btn btn-secondary btn-sm" type="button" onclick={onDiscoverModels} disabled={discoveringProviderModels || providerTypeUnsupported || !providerForm.base_url.trim()}><Icon name="refresh" size={13} />{discoveringProviderModels ? "读取中" : "读取模型"}</button></div>
          {#if providerTypeUnsupported}
            <div class="provider-type-warning"><Icon name="alert" size={15} /><span>当前编译器尚未实现此 API 类型的视频请求适配器，因此不能用于自动视频笔记任务。</span></div>
          {:else if providerForm.provider === "anthropic_messages"}
            <div class="provider-type-hint"><Icon name="info" size={15} /><span>原生 Anthropic Claude 不支持视频；只有扩展端点（例如 MiniMax M3）支持。勾选"此端点支持视频输入"后才会发送视频，否则按文本路径处理。</span></div>
          {/if}
          <div class="field"><label class="field-label" for="prov_base_url">Base URL <small>{providerForm.provider === "google_gemini" ? "Gemini API 根地址" : providerForm.provider === "anthropic_messages" ? "Anthropic API 根地址" : providerForm.provider === "openai_responses" ? "OpenAI API 根地址" : "兼容接口通常以 /v1 结尾"}</small></label><div class="input-wrap has-icon"><span class="input-icon"><Icon name="server" size={15} /></span><input id="prov_base_url" type="text" bind:value={providerForm.base_url} placeholder={selectedProviderType.defaultBaseUrl || "由后续适配器提供"} disabled={providerTypeUnsupported} /></div></div>
          <div class="form-grid two-cols">
            <div class="field model-picker-field">
              <label class="field-label" for="prov_model">文本模型 <small>笔记生成</small></label>
              <select class="model-choice" aria-label="从真实读取的模型中选择文本模型" onchange={(event) => onChooseModel(event, "model")} disabled={providerTypeUnsupported || providerModelOptions.length === 0}>
                <option value="">{providerModelOptions.length ? "从读取列表选择…" : "读取成功后可选择"}</option>
                {#each providerModelOptions as model}<option value={model}>{model}</option>{/each}
              </select>
              <div class="input-wrap has-icon"><span class="input-icon"><Icon name="file-text" size={15} /></span><input id="prov_model" type="text" bind:value={providerForm.model} placeholder="输入精确模型 ID" disabled={providerTypeUnsupported} /></div>
              <small class="field-help">当前值会实际用于笔记生成，不要填写模型数量、菜单标题等展示文字。</small>
            </div>
            <div class="field model-picker-field">
              <label class="field-label" for="prov_vision_model">视觉模型 <small>画面理解</small></label>
              <select class="model-choice" aria-label="从真实读取的模型中选择视觉模型" onchange={(event) => onChooseModel(event, "vision_model")} disabled={providerTypeUnsupported || providerModelOptions.length === 0}>
                <option value="">{providerModelOptions.length ? "从读取列表选择…" : "读取成功后可选择"}</option>
                {#each providerModelOptions as model}<option value={model}>{model}</option>{/each}
              </select>
              <div class="input-wrap has-icon"><span class="input-icon"><Icon name="eye" size={15} /></span><input id="prov_vision_model" type="text" bind:value={providerForm.vision_model} placeholder={providerForm.model || "输入视觉模型 ID"} disabled={providerTypeUnsupported} /></div>
              <small class="field-help">服务没有独立视觉模型时，可与文本模型保持相同。</small>
            </div>
          </div>
          <label class="capability-toggle">
            <input type="checkbox" bind:checked={providerForm.video_input} disabled={providerTypeUnsupported} />
            <span><strong>此端点支持视频输入 (Anthropic-style base64 或 OpenAI-style video_url)</strong><small>原生 Anthropic Claude 不支持视频，请勿勾选；只有明确支持视频的端点（例如 MiniMax M3 等）才勾选。</small></span>
          </label>
        </section>

        <section class="form-section secure-section">
          <div class="form-section-head"><span>03</span><div><h3>安全凭据</h3><p>{editingProviderName ? "留空将保留现有 API Key。" : "凭据保存在本机配置中，不会写入任务快照或导出笔记。"}</p></div><span class="secure-badge"><Icon name="shield" size={13} />本机加密</span></div>
          <div class="field"><label class="field-label" for="prov_api_key">API Key {#if editingProviderName}<small>可选更新</small>{/if}</label><div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="prov_api_key" type="password" bind:value={providerForm.api_key} placeholder={editingProviderName ? "输入新密钥以替换，或留空保持不变" : "sk-..."} disabled={providerTypeUnsupported} /></div></div>
        </section>
      </div>

      <footer class="modal-footer provider-modal-footer">
        <span><Icon name="info" size={14} />保存后可在供应商卡片上测试连接。</span>
        <div>
          <button class="btn btn-secondary" onclick={onClose}>取消</button>
          <button class="btn btn-primary" onclick={onSave} disabled={providerSaving || providerTypeUnsupported || !providerForm.name.trim() || !providerForm.model.trim()}>
            <Icon name="save" size={15} />{providerSaving ? "正在保存" : "保存供应商"}
          </button>
        </div>
      </footer>
    </div>
  </div>
{/if}

<style>
  .provider-modal { width: min(780px, calc(100vw - 48px)); }
  .modal-title-wrap { display: flex; align-items: flex-start; gap: 11px; }
  .modal-provider-icon { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 13px; color: var(--accent-color); background: var(--accent-soft); }
  .modal-title-wrap > div:last-child { display: flex; flex-direction: column; }
  .modal-title-wrap > div:last-child > span { color: var(--accent-color); font-size: 11px; font-weight: 800; letter-spacing: .12em; }
  .provider-form { display: flex; flex-direction: column; gap: 18px; }
  .capability-toggle { display: flex; align-items: flex-start; gap: 10px; min-height: 44px; margin-top: 13px; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-subtle); cursor: pointer; }
  .capability-toggle input { width: 17px; height: 17px; margin-top: 1px; accent-color: var(--accent-color); }
  .capability-toggle span { display: flex; flex-direction: column; gap: 3px; }
  .capability-toggle strong { font-size: 12px; }
  .capability-toggle small { color: var(--text-tertiary); font-size: 11px; line-height: 1.45; text-wrap: pretty; }
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
  .provider-model-head { grid-template-columns: 30px minmax(0,1fr); align-items: start; }
  .provider-model-head .btn { grid-column: 2; justify-self: start; min-height: 34px; margin-top: 9px; white-space: nowrap; }
  .model-picker-field { gap: 8px; }
  .model-choice { width: 100%; min-height: 40px; padding: 0 36px 0 11px; border: 1px solid var(--border-color); border-radius: 9px; color: var(--text-primary); background: var(--bg-card); font: inherit; }
  .model-choice:disabled { color: var(--text-tertiary); background: var(--bg-subtle); cursor: not-allowed; }
  .provider-type-warning { display: flex; align-items: flex-start; gap: 8px; margin: 0; padding: 10px 12px; border-radius: 10px; color: var(--warning-color); background: var(--warning-soft); border: 1px solid color-mix(in srgb, var(--warning-color) 25%, var(--border-color)); font-size: 12px; line-height: 1.55; }
  .provider-type-hint { display: flex; align-items: flex-start; gap: 8px; margin: 0; padding: 10px 12px; border-radius: 10px; color: var(--info-color); background: var(--info-soft); border: 1px solid color-mix(in srgb, var(--info-color) 25%, var(--border-color)); font-size: 12px; line-height: 1.55; }

  .provider-modal { width: min(820px, calc(100vw - 48px)); }
  .form-section-head h3 { font-size: 15px; }
  .form-section-head p { font-size: 12px; }

  @media (max-width: 1120px) {
    .provider-modal { width: min(740px, calc(100vw - 48px)); }
    .form-section { border-radius: 13px; }
  }
</style>
