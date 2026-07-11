<script lang="ts">
  import Icon from "../Icon.svelte";
  // Whisper model imports removed with the whisper/OCR migration

  interface SettingsBag {
    output_dir: string;
    vault_path: string;
    compile_mode: string;
    vision_enabled: boolean;
    frame_mode: string;
    frame_interval: number;
    max_frames: number;
    template: string;
    active_provider: string;
    bilibili_cookie_file: string;
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
  <div class="pane-head"><div><span>GENERAL</span><h2>通用设置</h2><p>设置默认输出位置、编译模式与视觉理解能力。</p></div></div>

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
    <div class="summary-card">
      <span class="summary-icon enhance"><Icon name="eye" size={18} /></span>
      <strong>{settings.vision_enabled ? "视觉已启用" : "基础模式"}</strong>
      <small>Vision {settings.vision_enabled ? "开启" : "关闭"}</small>
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
  </div>

  <div class="setting-group settings-card-section">
    <div class="group-head"><div class="group-icon"><Icon name="image" size={18} /></div><div><h3>默认抽帧设置</h3><p>控制新任务与合集任务默认的帧抽取策略。创建任务页仍可单次覆盖。</p></div></div>
    <div class="runtime-settings-grid">
      <div class="field">
        <label class="field-label" for="settings_frame_mode">抽帧模式</label>
        <select id="settings_frame_mode" bind:value={settings.frame_mode} onchange={onMarkDirty}>
          <option value="fixed">固定间隔抽取</option>
          <option value="adaptive">自适应合并场景检测</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label" for="settings_max_frames">最大帧数</label>
        <input id="settings_max_frames" type="number" bind:value={settings.max_frames} min={1} max={10000} oninput={onMarkDirty} />
      </div>
      <div class="field field-full">
        <label class="field-label" for="settings_frame_interval">抽帧间隔（秒）</label>
        <input id="settings_frame_interval" type="number" bind:value={settings.frame_interval} min={0.1} max={600} step={0.1} oninput={onMarkDirty} disabled={settings.frame_mode === "adaptive"} />
        {#if settings.frame_mode === "adaptive"}<small style="color:var(--text-tertiary);font-size:10px;margin-top:2px;">自适应模式自动计算间隔</small>{/if}
      </div>
    </div>
    <div style="margin-top:8px;padding:8px 12px;border-radius:8px;background:var(--bg-subtle);font-size:12px;color:var(--text-tertiary);line-height:1.5;">
      <strong>固定间隔</strong>：按间隔秒数等距抽帧。<strong>自适应</strong>：场景检测 + 均匀采样 + 95% 相似度去重，自动合并相似帧。合集任务也会使用此默认值。
    </div>
  </div>

  <div class="setting-group settings-card-section">
    <div class="group-head"><div class="group-icon"><Icon name="eye" size={18} /></div><div><h3>视觉理解</h3><p>抽取关键帧并调用当前活动供应商的视觉模型，分析图表、界面和演示步骤。</p></div></div>
    <div class="enhancement-settings-grid">
      <button type="button" class="setting-toggle-card as-button" class:enabled={settings.vision_enabled} onclick={() => { settings.vision_enabled = !settings.vision_enabled; onMarkDirty(); }} aria-pressed={settings.vision_enabled}>
        <span class="toggle-feature-icon"><Icon name="eye" size={20} /></span>
        <span class="toggle-copy"><strong>视觉理解</strong><small>抽取关键帧并调用当前活动供应商的视觉模型，分析图表、界面和演示步骤。</small></span>
        <span class="switch" aria-hidden="true"><input type="checkbox" checked={settings.vision_enabled} tabindex="-1" /><span class="switch-track"></span></span>
      </button>
    </div>
    <div class="enhancement-explain"><Icon name="info" size={14} />视觉理解会抽取关键帧并调用当前活动 AI 供应商的视觉模型。首次真实任务建议先关闭，确认转录与笔记主链路后再开启。</div>
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
  .summary-icon.enhance { color: var(--warning-color); background: var(--warning-soft); }
  .summary-card strong { overflow: hidden; color: var(--text-primary); font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
  .summary-card small { overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

  .settings-card-section { margin-top: 14px; padding: 18px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .settings-card-section + .settings-card-section { margin-top: 12px; }
  .setting-group.settings-card-section { border-bottom: 0; }
  .settings-card-section .group-head { margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); }
  .settings-card-section .group-head.with-action { margin-bottom: 14px; }

  .online-video-settings .input-wrap input { min-height: 38px; }
  .field-help { display: block; margin-top: 4px; color: var(--text-tertiary); font-size: 11px; line-height: 1.5; }

  .compile-mode-switcher { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .compile-mode-option { display: flex; align-items: center; gap: 12px; width: 100%; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s; }
  .compile-mode-option:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .compile-mode-option.active { border-color: var(--accent-color); background: var(--accent-faint); }
  .compile-mode-icon { display: grid; place-items: center; width: 41px; height: 41px; border-radius: 12px; color: var(--text-secondary); background: var(--bg-muted); }
  .compile-mode-option.active .compile-mode-icon { color: var(--accent-color); background: var(--accent-soft); }
  .compile-mode-copy { display: flex; flex: 1; flex-direction: column; }
  .compile-mode-copy strong { font-size: 14px; }
  .compile-mode-copy small { margin-top: 3px; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }
  .compile-mode-check { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; color: #fff; background: var(--accent-color); }

  .runtime-settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
  .field-full { grid-column: 1 / -1; }

  .setting-toggle-card { display: flex; align-items: center; gap: 12px; width: 100%; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s; }
  .setting-toggle-card.as-button { appearance: none; font: inherit; }
  .enhancement-settings-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
  .enhancement-explain { display: flex; align-items: flex-start; gap: 8px; margin-top: 10px; padding: 10px 12px; border-radius: 10px; color: var(--text-secondary); background: var(--bg-subtle); border: 1px solid var(--border-color); font-size: 12px; line-height: 1.55; }
  .setting-toggle-card.enabled { border-color: color-mix(in srgb, var(--accent-color) 50%, var(--border-color)); background: var(--accent-faint); }
  .toggle-feature-icon { display: grid; place-items: center; width: 41px; height: 41px; border-radius: 12px; color: var(--text-secondary); background: var(--bg-muted); }
  .enabled .toggle-feature-icon { color: var(--accent-color); background: var(--accent-soft); }
  .toggle-copy { display: flex; flex: 1; flex-direction: column; }
  .toggle-copy strong { font-size: 14px; }
  .toggle-copy small { margin-top: 3px; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }

  @media (max-width: 760px) { .runtime-settings-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .enhancement-settings-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .compile-mode-switcher { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .settings-summary-grid { grid-template-columns: 1fr; } }
  @media (max-width: 1180px) { .settings-pane { padding: 24px; } }
  @media (max-width: 960px) { .settings-pane { padding: 18px 14px 24px; } }
  @media (max-width: 900px) { .settings-pane { padding: 16px 12px 20px; } }
</style>
