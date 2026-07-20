<script lang="ts">
  import Icon from "../Icon.svelte";

  interface SettingsBag {
    output_dir: string;
    vault_path: string;
    template: string;
    active_provider: string;
    bilibili_cookie_file: string;
    compile_concurrency: number;
    effective_compile_concurrency: number;
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

  let effectiveConcurrency = $derived(
    settings.compile_concurrency || settings.effective_compile_concurrency || 2
  );
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
      <strong>云端精确编译</strong>
      <small>编译模式</small>
    </div>
    <div class="summary-card">
      <span class="summary-icon concurrency"><Icon name="tasks" size={18} /></span>
      <strong class="tabular-number">{effectiveConcurrency} 个任务</strong>
      <small>{settings.compile_concurrency === 0 ? "智能并发" : "固定并发上限"}</small>
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
      <label class="field-label" for="bilibili_cookie_file">Cookie 文件路径 <small>可选</small></label>
      <div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="bilibili_cookie_file" type="text" bind:value={settings.bilibili_cookie_file} oninput={onMarkDirty} placeholder="例如：C:\Users\你\cookies.txt" /></div>
      <small class="field-help">只建议填写 cookies.txt 路径。该字段不会写入任务快照，但会保存在本机设置中；需要登录、会员、年龄验证或风控的视频通常必须配置。</small>
    </div>
  </div>

  <div class="setting-group settings-card-section">
    <div class="group-head"><div class="group-icon"><Icon name="tasks" size={18} /></div><div><h3>并行任务</h3><p>限制同时执行媒体处理和云端编译的任务数量。</p></div></div>
    <div class="concurrency-row">
      <div class="concurrency-copy">
        <label for="compile_concurrency">编译任务并发数</label>
        <p>超出上限的任务会自动排队；任务完成、失败或取消后，下一个任务自动开始。</p>
      </div>
      <div class="concurrency-control">
        <select id="compile_concurrency" bind:value={settings.compile_concurrency} onchange={onMarkDirty} aria-describedby="compile_concurrency_help">
          <option value={0}>智能推荐（2）</option>
          <option value={1}>1 个任务</option>
          <option value={2}>2 个任务</option>
          <option value={3}>3 个任务</option>
          <option value={4}>4 个任务</option>
        </select>
        <small id="compile_concurrency_help">当前最多同时运行 <strong class="tabular-number">{effectiveConcurrency}</strong> 个任务</small>
      </div>
    </div>
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
  .summary-icon.concurrency { color: var(--info-color); background: var(--info-soft); }
  .summary-card strong { overflow: hidden; color: var(--text-primary); font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
  .summary-card small { overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

  .settings-card-section { margin-top: 14px; padding: 18px; border: 1px solid var(--border-color); border-radius: 13px; background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .settings-card-section + .settings-card-section { margin-top: 12px; }
  .setting-group.settings-card-section { border-bottom: 0; }
  .settings-card-section .group-head { margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); }
  .online-video-settings .input-wrap input { min-height: 38px; }
  .field-help { display: block; margin-top: 4px; color: var(--text-tertiary); font-size: 11px; line-height: 1.5; }

  .concurrency-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(190px, 230px); align-items: center; gap: 18px; }
  .concurrency-copy { min-width: 0; }
  .concurrency-copy label { color: var(--text-primary); font-size: 14px; font-weight: 700; }
  .concurrency-copy p { max-width: 620px; margin-top: 5px; color: var(--text-secondary); font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; text-wrap: pretty; }
  .concurrency-control { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
  .concurrency-control select { min-height: 44px; font-variant-numeric: tabular-nums; }
  .concurrency-control small { color: var(--text-tertiary); font-size: 11px; line-height: 1.45; text-wrap: pretty; }
  .concurrency-control strong { color: var(--accent-color); }
  .tabular-number { font-variant-numeric: tabular-nums; }


  @media (max-width: 760px) {
    .settings-summary-grid, .concurrency-row { grid-template-columns: minmax(0, 1fr); }
    .concurrency-row { align-items: stretch; gap: 12px; }
  }
  @media (max-width: 1180px) { .settings-pane { padding: 24px; } }
  @media (max-width: 960px) { .settings-pane { padding: 18px 14px 24px; } }
  @media (max-width: 900px) { .settings-pane { padding: 16px 12px 20px; } }
</style>
