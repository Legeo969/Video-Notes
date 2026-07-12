<script lang="ts">
  import Icon from "../Icon.svelte";
  // Whisper model imports removed with the whisper/OCR migration

  interface SettingsBag {
    output_dir: string;
    vault_path: string;
    compile_mode: string;
    template: string;
    active_provider: string;
    bilibili_cookie_file: string;
    draft_model_path: string;
  }

  let {
    settings = $bindable(),
    onMarkDirty,
    onOpenExternalUrl,
  }: {
    settings: SettingsBag;
    onMarkDirty: () => void;
    onOpenExternalUrl: (url: string) => void;
  } = $props();
</script>

<section class="settings-pane">
  <div class="pane-head"><div><span>GENERAL</span><h2>通用设置</h2><p>设置默认输出位置与编译模式。</p></div></div>

  <div class="settings-summary-grid">
    <div class="summary-card">
      <span class="summary-icon"><Icon name="folder" size={18} /></span>
      <strong>{settings.vault_path ? "Obsidian Vault" : "默认导出目录"}</strong>
      <small title={settings.vault_path || settings.output_dir}>{settings.vault_path || settings.output_dir || "未配置"}</small>
    </div>
    <div class="summary-card">
      <span class="summary-icon model"><Icon name="cpu" size={18} /></span>
      <strong>{settings.compile_mode === "precision" ? "云端精确编译" : "本地草稿模式"}</strong>
      <small>编译模式</small>
    </div>
  </div>

  <div class="setting-group settings-card-section">
    <div class="group-head"><div class="group-icon"><Icon name="folder" size={18} /></div><div><h3>文件目录</h3><p>配置笔记产物的存储位置。</p></div></div>
    <div class="form-grid two-cols">
      <div class="field"><label class="field-label" for="vault_path">Obsidian 笔记库 <small>可选，归档到 vault\video-notes</small></label><div class="input-wrap has-icon path-input"><span class="input-icon"><Icon name="folder" size={15} /></span><input id="vault_path" type="text" bind:value={settings.vault_path} title={settings.vault_path} oninput={onMarkDirty} placeholder="D:\Note_Obsidian" /></div></div>
    </div>
  </div>

  <div class="setting-group settings-card-section online-video-settings">
    <div class="group-head"><div class="group-icon"><Icon name="link" size={18} /></div><div><h3>在线视频下载与 Cookie</h3><p>用于 B站等需要登录态的视频下载。公开免费视频可留空。</p></div></div>
    <div class="field">
      <label class="field-label" for="bilibili_cookie_file">Cookie 文件或 Cookie 字符串 <small>可选</small></label>
      <div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="bilibili_cookie_file" type="text" bind:value={settings.bilibili_cookie_file} oninput={onMarkDirty} placeholder="例如：C:\Users\你\cookies.txt，或 SESSDATA=...; bili_jct=..." /></div>
      <small class="field-help">只建议填写 cookies.txt 路径。该字段不会写入任务快照，但会保存在本机设置中；需要登录、会员、年龄验证或风控的视频通常必须配置。</small>
    </div>
  </div>

  <div class="setting-group settings-card-section">
    <div class="group-head"><div class="group-icon"><Icon name="cpu" size={18} /></div><div><h3>编译模式</h3><p>选择默认视频编译模式，可在任务创建页临时切换。</p></div></div>
    <div class="compile-mode-switcher">
      <button
        type="button"
        class="compile-mode-option"
        class:active={settings.compile_mode === "precision"}
        onclick={() => { settings.compile_mode = "precision"; onMarkDirty(); }}
      >
        <span class="compile-mode-icon"><Icon name="cloud" size={20} /></span>
        <span class="compile-mode-copy"><strong>云端精确编译</strong><small>调用云端 AI 生成高质量结构化笔记</small></span>
        {#if settings.compile_mode === "precision"}<span class="compile-mode-check"><Icon name="check" size={14} /></span>{/if}
      </button>
      <button
        type="button"
        class="compile-mode-option"
        class:active={settings.compile_mode === "draft"}
        onclick={() => { settings.compile_mode = "draft"; onMarkDirty(); }}
      >
        <span class="compile-mode-icon"><Icon name="file-text" size={20} /></span>
        <span class="compile-mode-copy"><strong>本地草稿模式</strong><small>本地快速生成转录草稿，无需云端服务</small></span>
        {#if settings.compile_mode === "draft"}<span class="compile-mode-check"><Icon name="check" size={14} /></span>{/if}
      </button>
    </div>
    {#if settings.compile_mode === "draft"}
      <div class="draft-model-config">
        <label class="field-label" for="settings_draft_model">本地模型路径 (GGUF)</label>
        <div class="input-wrap has-icon">
          <span class="input-icon"><Icon name="cpu" size={15} /></span>
          <input id="settings_draft_model" type="text" bind:value={settings.draft_model_path} oninput={onMarkDirty} placeholder="可选，例如 C:\models\moondream-2b-int4.gguf" />
        </div>
        <small class="field-help">留空则使用内置画面分析（无需下载模型）。填入 GGUF 模型路径可使用本地 AI 分析帧内容。</small>
      </div>
    {/if}
  </div>

  </section>

<style>
  .settings-pane { padding: 30px 34px 42px; }
  .pane-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 20px; border-bottom: 1px solid var(--border-color); }
  .pane-head > div:first-child { display: flex; flex-direction: column; }
  .pane-head > div:first-child > span { color: var(--accent-color); font-size: 12px; font-weight: 800; letter-spacing: .12em; }
  .pane-head h2 { margin-top: 3px; font-size: 26px; letter-spacing: -.02em; text-wrap: balance; }
  .pane-head p { margin-top: 7px; color: var(--text-secondary); font-size: 13px; text-wrap: pretty; }

  .settings-summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 16px 0 18px; }
  .summary-card { display: grid; grid-template-columns: 38px minmax(0, 1fr); grid-template-rows: auto auto; align-items: center; column-gap: 10px; min-height: 78px; padding: 14px; border-radius: 12px; background: var(--bg-subtle); }
  .summary-icon { grid-row: 1 / 3; display: grid; place-items: center; width: 38px; height: 38px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .summary-icon.model { color: var(--success-color); background: var(--success-soft); }
  .summary-card strong { overflow: hidden; color: var(--text-primary); font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
  .summary-card small { overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

  .settings-card-section { margin-top: 14px; padding: 18px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .settings-card-section + .settings-card-section { margin-top: 12px; }
  .setting-group.settings-card-section { border-bottom: 0; }
  .settings-card-section .group-head { margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); }
  .online-video-settings .input-wrap input { min-height: 38px; }
  .field-help { display: block; margin-top: 4px; color: var(--text-tertiary); font-size: 11px; line-height: 1.5; }

  .compile-mode-switcher { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .compile-mode-option { display: flex; align-items: center; gap: 12px; width: 100%; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s; }
  .draft-model-config { margin-top: 12px; padding: 12px; border-radius: 10px; background: var(--bg-subtle); }
  .draft-model-config .field-help { margin-top: 6px; }
  .compile-mode-option:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .compile-mode-option.active { border-color: var(--accent-color); background: var(--accent-faint); }
  .compile-mode-icon { display: grid; place-items: center; width: 41px; height: 41px; border-radius: 12px; color: var(--text-secondary); background: var(--bg-muted); }
  .compile-mode-option.active .compile-mode-icon { color: var(--accent-color); background: var(--accent-soft); }
  .compile-mode-copy { display: flex; flex: 1; flex-direction: column; }
  .compile-mode-copy strong { font-size: 14px; }
  .compile-mode-copy small { margin-top: 3px; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }
  .compile-mode-check { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; color: #fff; background: var(--accent-color); }

  @media (max-width: 760px) { .compile-mode-switcher { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .settings-summary-grid { grid-template-columns: 1fr; } }
  @media (max-width: 1180px) { .settings-pane { padding: 24px; } }
  @media (max-width: 960px) { .settings-pane { padding: 18px 14px 24px; } }
  @media (max-width: 900px) { .settings-pane { padding: 16px 12px 20px; } }
</style>
