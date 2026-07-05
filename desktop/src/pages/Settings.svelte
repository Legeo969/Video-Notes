<script lang="ts">
  import { engineCall } from "../lib/api";
  import type { ProviderProfile } from "../lib/types";

  /* ───────── types ───────── */
  interface ProviderForm {
    name: string;
    provider: string;
    api_key: string;
    base_url: string;
    model: string;
    vision_model: string;
  }

  interface TemplateInfo {
    name: string;
    path: string;
  }

  interface CheckResult {
    name: string;
    status: "pass" | "fail" | "warn";
    detail: string;
  }

  interface SettingsBag {
    output_dir: string;
    whisper_model: string;
    whisper_model_dir: string;
    ocr_enabled: boolean;
    template: string;
    active_provider: string;
  }

  const emptyProviderForm = (): ProviderForm => ({
    name: "",
    provider: "openai",
    api_key: "",
    base_url: "",
    model: "gpt-4o",
    vision_model: "gpt-4o",
  });

  /* ───────── state ───────── */
  let settings = $state<SettingsBag>({
    output_dir: "./output",
    whisper_model: "large-v3",
    whisper_model_dir: "",
    ocr_enabled: false,
    template: "default",
    active_provider: "",
  });
  let providers = $state<ProviderProfile[]>([]);
  let templates = $state<TemplateInfo[]>([]);
  let availableModels = $state<string[]>([]);
  let checkResults = $state<CheckResult[]>([]);
  let loading = $state(true);
  let saving = $state(false);
  let scanning = $state(false);
  let testingProvider = $state<string | null>(null);
  let doctorRunning = $state(false);
  let bundlingDiagnostics = $state(false);
  let toast = $state<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  let dirty = $state(false);

  /* provider modal */
  let showProviderModal = $state(false);
  let editingProviderName = $state<string | null>(null);
  let providerForm = $state<ProviderForm>(emptyProviderForm());
  let providerSaving = $state(false);

  /* api key reveal */
  let revealedKeys = $state<Record<string, boolean>>({});

  /* confirm save */
  let confirmSave = $state(false);

  /* ───────── helpers ───────── */
  function showToast(msg: string, type: "success" | "error" | "info" = "info") {
    toast = { msg, type };
    setTimeout(() => (toast = null), 3500);
  }

  function markDirty() { dirty = true; }

  /* ───────── data loading ───────── */
  async function loadAll() {
    loading = true;
    try {
      const [s, provs, tmpls] = await Promise.all([
        engineCall<SettingsBag>("settings.get"),
        engineCall<ProviderProfile[]>("settings.providers.list"),
        engineCall<TemplateInfo[]>("settings.templates.list"),
      ]);
      settings = s;
      providers = provs;
      templates = tmpls;
    } catch (e: any) {
      showToast(`加载设置失败: ${e?.message ?? e}`, "error");
    } finally {
      loading = false;
    }
  }

  async function scanModels() {
    scanning = true;
    try {
      availableModels = await engineCall<string[]>("settings.models.scan");
      showToast(`扫描到 ${availableModels.length} 个 Whisper 模型`, "success");
    } catch (e: any) {
      showToast(`模型扫描失败: ${e?.message ?? e}`, "error");
    } finally {
      scanning = false;
    }
  }

  /* ───────── save ───────── */
  async function handleSave() {
    confirmSave = false;
    saving = true;
    try {
      await engineCall("settings.update", {
        patches: {
          output_dir: settings.output_dir,
          whisper_model: settings.whisper_model,
          whisper_model_dir: settings.whisper_model_dir,
          ocr_enabled: settings.ocr_enabled,
          template: settings.template,
        },
      });
      dirty = false;
      showToast("设置已保存", "success");
    } catch (e: any) {
      showToast(`保存失败: ${e?.message ?? e}`, "error");
    } finally {
      saving = false;
    }
  }

  /* ───────── provider CRUD ───────── */
  function openAddProvider() {
    editingProviderName = null;
    providerForm = emptyProviderForm();
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
      vision_model: (p as any).vision_model ?? p.model,
    };
    showProviderModal = true;
  }

  function closeProviderModal() {
    showProviderModal = false;
    providerForm = emptyProviderForm();
  }

  async function saveProvider() {
    if (!providerForm.name.trim()) {
      showToast("供应商名称不能为空", "error");
      return;
    }
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
        await engineCall("settings.secret.set", {
          provider: editingProviderName ?? providerForm.name,
          key: providerForm.api_key,
        });
      }
      showToast(
        editingProviderName ? "供应商已更新" : "供应商已创建",
        "success"
      );
      closeProviderModal();
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) {
      showToast(`保存供应商失败: ${e?.message ?? e}`, "error");
    } finally {
      providerSaving = false;
    }
  }

  async function deleteProvider(name: string) {
    if (!confirm(`确定要删除供应商 "${name}" 吗？`)) return;
    try {
      await engineCall("settings.providers.delete", { name });
      showToast(`供应商 "${name}" 已删除`, "success");
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) {
      showToast(`删除失败: ${e?.message ?? e}`, "error");
    }
  }

  async function setActiveProvider(name: string) {
    try {
      await engineCall("settings.providers.set_active", { name });
      settings.active_provider = name;
      showToast(`已将 "${name}" 设为活动供应商`, "success");
    } catch (e: any) {
      showToast(`设置活动供应商失败: ${e?.message ?? e}`, "error");
    }
  }

  /* ───────── API key per-provider ───────── */
  async function deleteApiKey(name: string) {
    if (!confirm(`确定要删除 "${name}" 的 API Key 吗？`)) return;
    try {
      await engineCall("settings.secret.delete", { provider: name });
      showToast("API Key 已删除", "success");
      providers = await engineCall<ProviderProfile[]>("settings.providers.list");
    } catch (e: any) {
      showToast(`删除 API Key 失败: ${e?.message ?? e}`, "error");
    }
  }

  function toggleKeyReveal(name: string) {
    revealedKeys[name] = !revealedKeys[name];
  }

  /* ───────── test connection ───────── */
  async function testConnection(p: ProviderProfile) {
    testingProvider = p.name;
    try {
      const result: any = await engineCall("settings.providers.test", {
        name: p.name,
        provider: p.provider,
        base_url: p.base_url,
        model: p.model,
      });
      showToast(
        result?.success ? `连接测试成功: ${result.message ?? ""}` : `连接测试失败: ${result?.message ?? "未知错误"}`,
        result?.success ? "success" : "error"
      );
    } catch (e: any) {
      showToast(`测试连接异常: ${e?.message ?? e}`, "error");
    } finally {
      testingProvider = null;
    }
  }

  /* ───────── diagnostics ───────── */
  async function runDoctor() {
    doctorRunning = true;
    checkResults = [];
    try {
      const results: CheckResult[] = await engineCall("doctor.run");
      checkResults = results;
      showToast(`环境检查完成 — ${results.filter(r => r.status === "pass").length} 项通过`, "info");
    } catch (e: any) {
      showToast(`环境检查失败: ${e?.message ?? e}`, "error");
    } finally {
      doctorRunning = false;
    }
  }

  async function bundleDiagnostics() {
    bundlingDiagnostics = true;
    try {
      const path: string = await engineCall("diagnostics.bundle");
      showToast(`诊断报告已生成: ${path}`, "success");
    } catch (e: any) {
      showToast(`生成诊断报告失败: ${e?.message ?? e}`, "error");
    } finally {
      bundlingDiagnostics = false;
    }
  }

  /* ───────── init ───────── */
  $effect(() => { loadAll(); });
</script>

<div class="page">
  <div class="page-header">
    <h2 class="page-title">设置</h2>
    {#if dirty}
      <button class="btn btn-warning" onclick={() => (confirmSave = true)} disabled={saving}>
        {saving ? "保存中..." : "有未保存的更改"}
      </button>
    {/if}
  </div>

  {#if loading}
    <div class="loading">加载中...</div>
  {:else}
    <!-- ════════════ General ════════════ -->
    <section class="settings-section">
      <h3>通用</h3>
      <div class="form-grid">
        <div class="form-group">
          <label for="output_dir">输出目录</label>
          <input
            id="output_dir"
            type="text"
            bind:value={settings.output_dir}
            oninput={markDirty}
            placeholder="./output"
          />
        </div>
        <div class="form-group">
          <label for="whisper_model">Whisper 模型</label>
          <div class="input-row">
            <select id="whisper_model" bind:value={settings.whisper_model} onchange={markDirty}>
              <option value="tiny">tiny</option>
              <option value="base">base</option>
              <option value="small">small</option>
              <option value="medium">medium</option>
              <option value="large-v3">large-v3</option>
            </select>
            <button class="btn btn-sm btn-secondary" onclick={scanModels} disabled={scanning}>
              {scanning ? "扫描中..." : "扫描模型"}
            </button>
          </div>
          {#if availableModels.length > 0}
            <div class="help-text">
              可用模型: {availableModels.join(", ")}
            </div>
          {/if}
        </div>
        <div class="form-group">
          <label for="whisper_model_dir">Whisper 模型目录（可选）</label>
          <input
            id="whisper_model_dir"
            type="text"
            bind:value={settings.whisper_model_dir}
            oninput={markDirty}
            placeholder="留空使用默认缓存目录"
          />
        </div>
        <div class="form-group checkbox-group">
          <label>
            <input type="checkbox" bind:checked={settings.ocr_enabled} onchange={markDirty} />
            OCR 文字识别
          </label>
        </div>
      </div>
    </section>

    <!-- ════════════ Providers ════════════ -->
    <section class="settings-section">
      <div class="section-header">
        <h3>供应商</h3>
        <button class="btn btn-primary btn-sm" onclick={openAddProvider}>+ 添加供应商</button>
      </div>

      {#if providers.length === 0}
        <p class="empty-hint">尚未配置任何供应商。点击"添加供应商"开始配置。</p>
      {/if}

      <div class="provider-list">
        {#each providers as p (p.name)}
          <div class="provider-card" class:active={p.name === settings.active_provider}>
            <div class="provider-head">
              <div class="provider-title">
                <strong>{p.name}</strong>
                <span class="badge badge-provider">{p.provider}</span>
                {#if p.name === settings.active_provider}
                  <span class="badge badge-active">活动</span>
                {/if}
              </div>
              <div class="provider-actions">
                {#if p.name !== settings.active_provider}
                  <button
                    class="btn btn-xs btn-ghost"
                    onclick={() => setActiveProvider(p.name)}
                    title="设为活动供应商"
                  >
                    激活
                  </button>
                {/if}
                <button class="btn btn-xs btn-ghost" onclick={() => openEditProvider(p)} title="编辑">
                  编辑
                </button>
                <button
                  class="btn btn-xs btn-ghost btn-danger-text"
                  onclick={() => deleteProvider(p.name)}
                  title="删除"
                >
                  删除
                </button>
              </div>
            </div>

            <div class="provider-details">
              <div class="detail-row">
                <span class="detail-label">模型</span>
                <span class="detail-value">{p.model}</span>
              </div>
              {#if (p as any).vision_model}
                <div class="detail-row">
                  <span class="detail-label">视觉模型</span>
                  <span class="detail-value">{(p as any).vision_model}</span>
                </div>
              {/if}
              <div class="detail-row">
                <span class="detail-label">Base URL</span>
                <span class="detail-value">{p.base_url || "(默认)"}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">API Key</span>
                <span class="detail-value key-preview">
                  {#if revealedKeys[p.name]}
                    <input
                      type="text"
                      class="api-key-input"
                      readonly
                      value={p.api_key_preview || "未配置"}
                    />
                    <button class="btn btn-xs btn-ghost" onclick={() => toggleKeyReveal(p.name)}>隐藏</button>
                  {:else}
                    <span class="key-masked">{p.api_key_preview || "未配置"}</span>
                    {#if p.api_key_configured}
                      <button class="btn btn-xs btn-ghost" onclick={() => toggleKeyReveal(p.name)}>显示</button>
                      <button class="btn btn-xs btn-ghost btn-danger-text" onclick={() => deleteApiKey(p.name)}>删除</button>
                    {:else}
                      <button class="btn btn-xs btn-ghost" onclick={() => openEditProvider(p)}>配置</button>
                    {/if}
                  {/if}
                </span>
              </div>
            </div>

            <div class="provider-footer">
              <button
                class="btn btn-xs btn-secondary"
                onclick={() => testConnection(p)}
                disabled={testingProvider === p.name}
              >
                {testingProvider === p.name ? "测试中..." : "测试连接"}
              </button>
            </div>
          </div>
        {/each}
      </div>
    </section>

    <!-- ════════════ Templates ════════════ -->
    <section class="settings-section">
      <h3>笔记模板</h3>
      {#if templates.length === 0}
        <p class="empty-hint">暂无可用模板。</p>
      {:else}
        <div class="template-list">
          <label class="template-option" for="template_sel">当前模板</label>
          <select id="template_sel" bind:value={settings.template} onchange={markDirty}>
            {#each templates as t}
              <option value={t.name}>{t.name}</option>
            {/each}
          </select>
          <ul class="template-detail-list">
            {#each templates as t}
              <li><code>{t.name}</code> — {t.path}</li>
            {/each}
          </ul>
        </div>
      {/if}
    </section>

    <!-- ════════════ Diagnostics ════════════ -->
    <section class="settings-section">
      <div class="section-header">
        <h3>诊断</h3>
        <div class="diag-actions">
          <button class="btn btn-secondary btn-sm" onclick={runDoctor} disabled={doctorRunning}>
            {doctorRunning ? "检查中..." : "运行环境检查"}
          </button>
          <button class="btn btn-secondary btn-sm" onclick={bundleDiagnostics} disabled={bundlingDiagnostics}>
            {bundlingDiagnostics ? "生成中..." : "导出诊断报告"}
          </button>
        </div>
      </div>

      {#if checkResults.length > 0}
        <table class="check-table">
          <thead>
            <tr>
              <th>检查项</th>
              <th>状态</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {#each checkResults as cr}
              <tr>
                <td>{cr.name}</td>
                <td>
                  <span class="badge badge-{cr.status}">
                    {cr.status === "pass" ? "通过" : cr.status === "fail" ? "失败" : "警告"}
                  </span>
                </td>
                <td class="detail-cell">{cr.detail}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {:else if !doctorRunning}
        <p class="empty-hint">尚未运行环境检查。点击"运行环境检查"检测 CUDA、FFmpeg、OCR 等依赖状态。</p>
      {/if}
    </section>

    <!-- ════════════ Save footer ════════════ -->
    <div class="save-bar">
      {#if confirmSave}
        <div class="confirm-dialog">
          <span>确定要保存更改吗？</span>
          <button class="btn btn-primary btn-sm" onclick={handleSave} disabled={saving}>确定</button>
          <button class="btn btn-sm" onclick={() => (confirmSave = false)}>取消</button>
        </div>
      {:else}
        <button
          class="btn btn-primary"
          onclick={() => (confirmSave = true)}
          disabled={saving || !dirty}
        >
          保存设置
        </button>
      {/if}
    </div>
  {/if}

  <!-- ════════════ Toast ════════════ -->
  {#if toast}
    <div class="toast toast-{toast.type}">
      {toast.msg}
    </div>
  {/if}
</div>

<!-- ════════════ Provider Modal ════════════ -->
{#if showProviderModal}
  <div class="modal-overlay" onclick={closeProviderModal}>
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <div class="modal-header">
        <h3>{editingProviderName ? "编辑供应商" : "添加供应商"}</h3>
        <button class="btn btn-xs btn-ghost" onclick={closeProviderModal}>✕</button>
      </div>

      <div class="modal-body">
        <div class="form-group">
          <label for="prov_name">名称</label>
          <input
            id="prov_name"
            type="text"
            bind:value={providerForm.name}
            disabled={!!editingProviderName}
            placeholder="my-provider"
          />
        </div>

        <div class="form-group">
          <label for="prov_type">供应商类型</label>
          <select id="prov_type" bind:value={providerForm.provider}>
            <option value="openai">OpenAI</option>
            <option value="azure">Azure OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="google">Google Gemini</option>
            <option value="ollama">Ollama (本地)</option>
            <option value="custom">自定义兼容</option>
          </select>
        </div>

        <div class="form-group">
          <label for="prov_base_url">Base URL</label>
          <input
            id="prov_base_url"
            type="text"
            bind:value={providerForm.base_url}
            placeholder="https://api.openai.com/v1"
          />
        </div>

        <div class="form-group">
          <label for="prov_model">模型</label>
          <input
            id="prov_model"
            type="text"
            bind:value={providerForm.model}
            placeholder="gpt-4o"
          />
        </div>

        <div class="form-group">
          <label for="prov_vision_model">视觉模型</label>
          <input
            id="prov_vision_model"
            type="text"
            bind:value={providerForm.vision_model}
            placeholder={providerForm.model}
          />
        </div>

        <div class="form-group">
          <label for="prov_api_key">
            API Key
            {#if editingProviderName}
              <span class="help-text">（留空则保留现有密钥）</span>
            {/if}
          </label>
          <div class="input-row">
            <input
              id="prov_api_key"
              type="password"
              bind:value={providerForm.api_key}
              placeholder={editingProviderName ? "输入新密钥以替换" : "sk-..."}
            />
          </div>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn btn-secondary" onclick={closeProviderModal}>取消</button>
        <button class="btn btn-primary" onclick={saveProvider} disabled={providerSaving}>
          {providerSaving ? "保存中..." : "保存"}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  /* ───── layout ───── */
  .page {
    padding: 24px 32px;
    max-width: 900px;
  }

  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
  }

  .page-title {
    font-size: 20px;
    font-weight: 700;
    margin: 0;
    color: var(--text-primary);
  }

  .loading {
    text-align: center;
    padding: 48px 0;
    color: var(--text-secondary);
    font-size: 15px;
  }

  /* ───── sections ───── */
  .settings-section {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 18px;
  }

  .settings-section h3 {
    font-size: 15px;
    font-weight: 600;
    margin: 0 0 16px;
    color: var(--text-primary);
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }

  .section-header h3 {
    margin: 0;
  }

  .diag-actions {
    display: flex;
    gap: 8px;
  }

  /* ───── form ───── */
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .form-group label {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  .form-group.checkbox-group {
    justify-content: flex-end;
  }

  .form-group.checkbox-group label {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    font-size: 14px;
    color: var(--text-primary);
  }

  .input-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .input-row select,
  .input-row input {
    flex: 1;
  }

  input,
  select {
    padding: 8px 12px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background: var(--bg-input);
    color: var(--text-primary);
    font-size: 14px;
    transition: border-color 0.15s;
  }

  input:focus,
  select:focus {
    outline: none;
    border-color: var(--accent-color);
  }

  input:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .help-text {
    font-size: 12px;
    color: var(--text-tertiary, #888);
    margin-top: 2px;
  }

  .empty-hint {
    color: var(--text-secondary);
    font-size: 13px;
    margin: 8px 0;
  }

  /* ───── buttons ───── */
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background: var(--bg-card);
    color: var(--text-primary);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    white-space: nowrap;
  }

  .btn:hover { background: var(--bg-hover); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .btn-primary {
    background: var(--accent-color);
    border-color: var(--accent-color);
    color: #fff;
  }
  .btn-primary:hover { filter: brightness(1.1); }

  .btn-secondary {
    background: var(--bg-input);
    border-color: var(--border-color);
  }

  .btn-warning {
    background: #f0ad4e;
    border-color: #f0ad4e;
    color: #fff;
  }

  .btn-sm { padding: 4px 10px; font-size: 12px; }
  .btn-xs { padding: 2px 8px; font-size: 11px; border-radius: 4px; }

  .btn-ghost {
    background: transparent;
    border-color: transparent;
  }
  .btn-ghost:hover { background: var(--bg-hover); }

  .btn-danger-text { color: var(--danger-color, #e74c3c); }
  .btn-danger-text:hover { background: rgba(231, 76, 60, 0.1); }

  /* ───── provider cards ───── */
  .provider-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .provider-card {
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 14px 16px;
    background: var(--bg-base);
    transition: border-color 0.15s;
  }

  .provider-card.active {
    border-color: var(--accent-color);
    box-shadow: 0 0 0 1px var(--accent-color);
  }

  .provider-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .provider-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .provider-title strong {
    font-size: 14px;
    font-weight: 600;
  }

  .provider-actions {
    display: flex;
    gap: 4px;
  }

  .provider-details {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px 16px;
    margin-bottom: 8px;
  }

  .detail-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }

  .detail-label {
    color: var(--text-secondary);
    min-width: 72px;
    flex-shrink: 0;
  }

  .detail-value {
    color: var(--text-primary);
    word-break: break-all;
  }

  .key-preview {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .api-key-input {
    font-family: monospace;
    font-size: 12px;
    padding: 2px 6px;
    max-width: 200px;
  }

  .key-masked {
    font-family: monospace;
    color: var(--text-secondary);
  }

  .provider-footer {
    display: flex;
    gap: 8px;
  }

  /* ───── badges ───── */
  .badge {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 500;
    line-height: 1.6;
  }

  .badge-provider {
    background: var(--bg-hover);
    color: var(--text-secondary);
  }

  .badge-active {
    background: #d4edda;
    color: #155724;
  }

  .badge-pass {
    background: #d4edda;
    color: #155724;
  }

  .badge-fail {
    background: #f8d7da;
    color: #721c24;
  }

  .badge-warn {
    background: #fff3cd;
    color: #856404;
  }

  /* ───── templates ───── */
  .template-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .template-list select {
    max-width: 320px;
  }

  .template-detail-list {
    margin: 4px 0 0;
    padding: 0;
    list-style: none;
    font-size: 13px;
    color: var(--text-secondary);
  }

  .template-detail-list li {
    padding: 2px 0;
  }

  .template-detail-list code {
    background: var(--bg-hover);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 12px;
  }

  .template-option {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
  }

  /* ───── diagnostics table ───── */
  .check-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin-top: 4px;
  }

  .check-table th {
    text-align: left;
    padding: 8px 12px;
    font-weight: 600;
    color: var(--text-secondary);
    border-bottom: 1px solid var(--border-color);
  }

  .check-table td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border-color);
    vertical-align: middle;
  }

  .check-table tbody tr:hover {
    background: var(--bg-hover);
  }

  .detail-cell {
    color: var(--text-secondary);
    word-break: break-word;
  }

  /* ───── save bar ───── */
  .save-bar {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid var(--border-color);
    display: flex;
    justify-content: flex-end;
  }

  .confirm-dialog {
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 14px;
    color: var(--text-primary);
  }

  /* ───── toast ───── */
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    padding: 12px 20px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    color: #fff;
    z-index: 9999;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    animation: slideIn 0.25s ease;
  }

  .toast-success { background: #28a745; }
  .toast-error { background: #e74c3c; }
  .toast-info { background: #3498db; }

  @keyframes slideIn {
    from { transform: translateY(16px); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }

  /* ───── modal ───── */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 5000;
    animation: fadeIn 0.15s ease;
  }

  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    width: 480px;
    max-height: 90vh;
    overflow-y: auto;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }

  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px 0;
  }

  .modal-header h3 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }

  .modal-body {
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px 16px;
    border-top: 1px solid var(--border-color);
    margin-top: 4px;
  }

  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  /* ───── responsive ───── */
  @media (max-width: 700px) {
    .page { padding: 16px; }
    .form-grid { grid-template-columns: 1fr; }
    .provider-details { grid-template-columns: 1fr; }
    .modal { width: calc(100% - 32px); }
    .section-header { flex-direction: column; align-items: flex-start; gap: 8px; }
    .diag-actions { flex-wrap: wrap; }
  }
</style>
