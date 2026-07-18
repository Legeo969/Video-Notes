<script lang="ts">
  import { onMount } from "svelte";
  import { open } from "@tauri-apps/plugin-dialog";
  import { engineCall, runningInTauri, toErrorMessage } from "../lib/api";
  import { jobs, refreshJobs, requestNavigate } from "../lib/stores/jobs";
  import Icon from "../lib/components/Icon.svelte";
  import PageHeader from "../lib/components/PageHeader.svelte";

  let sourceMode = $state<"file" | "link">("file");
  let fileSource = $state("");
  let linkSource = $state("");
  let title = $state("");
  let template = $state("default");
  let templates = $state<Array<{id: string, name: string, description: string}>>([]);
  let activeProvider = $state("");
  let submitting = $state(false);
  let errorMessage = $state("");
  let startedJobId = $state<number | null>(null);

  let source = $derived(sourceMode === "file" ? fileSource : linkSource);
  let publicUrlValid = $derived(sourceMode !== "link" || isSupportedPublicUrl(linkSource));
  let publicUrlMessage = $derived(sourceMode === "link" ? linkValidationMessage(linkSource) : "");
  let canSubmit = $derived(Boolean(source.trim()) && publicUrlValid && !submitting);

  let currentJob = $derived(
    startedJobId === null ? undefined : $jobs.find((job) => job.id === startedJobId)
  );

  onMount(async () => {
    try {
      const rawSettings = await engineCall<Record<string, any>>("settings.get");
      const settings = rawSettings as any;
      activeProvider = String(settings.active_provider || "");
      template = String(settings.template || "default");
    } catch (error) {
      errorMessage = toErrorMessage(error);
    }
    try {
      templates = await engineCall<Array<{id: string, name: string, description: string}>>("settings.templates.list");
      if (templates.length > 0 && !templates.some(t => t.id === template)) {
        template = templates[0].id;
      }
    } catch (error) {
      // templates list unavailable, use static fallback
      templates = [
        { id: "default", name: "默认学习笔记", description: "通用结构化笔记模板" },
        { id: "lecture", name: "课程讲义", description: "适合课程和讲座" },
        { id: "summary", name: "摘要", description: "短摘要与关键要点" },
        { id: "mindmap", name: "思维导图", description: "大纲式思维导图" },
      ];
    }
  });

  function isSupportedPublicUrl(value: string): boolean {
    const raw = value.trim();
    if (!raw) return false;
    try {
      const url = new URL(raw);
      if (!["http:", "https:"].includes(url.protocol)) return false;
      const host = url.hostname.toLowerCase();
      const path = url.pathname.toLowerCase();
      if (host.includes("bilibili.com")) {
        return path.includes("/video/") || path.includes("/bangumi/play/");
      }
      return Boolean(host.includes("youtube.com") || host.includes("youtu.be") || host.includes("bilibili.com") || host.includes("vimeo.com") || host.includes("x.com") || host.includes("twitter.com"));
    } catch {
      return false;
    }
  }

  function linkValidationMessage(value: string): string {
    const raw = value.trim();
    if (!raw) return "";
    try {
      const url = new URL(raw);
      if (!httpOrHttps(url.protocol)) return "仅支持 http:// 或 https:// 链接。";
      if (url.hostname.toLowerCase().includes("bilibili.com") && !url.pathname.toLowerCase().includes("/video/") && !url.pathname.toLowerCase().includes("/bangumi/play/")) {
        return "B站请粘贴完整视频详情页地址，例如 https://www.bilibili.com/video/BV...，不要粘贴首页、列表或被截断的 /vi 链接。";
      }
      return "";
    } catch {
      return "链接格式不完整，请粘贴完整的视频详情页地址。";
    }
  }

  function httpOrHttps(protocol: string): boolean {
    return protocol === "http:" || protocol === "https:";
  }

  function changeSourceMode(mode: "file" | "link") {
    sourceMode = mode;
    errorMessage = "";
  }

  async function startProcess() {
    const input = source.trim();
    if (!input || submitting) return;
    if (sourceMode === "link" && !isSupportedPublicUrl(input)) {
      errorMessage = linkValidationMessage(input) || "请粘贴完整、可公开访问的视频详情页链接。";
      return;
    }

    submitting = true;
    errorMessage = "";
    try {
      const result = await engineCall<{ job_id: number }>("compile.video", {
        input,
        title: title.trim() || undefined,
        mode: "precision",
        template,
      });
      startedJobId = Number(result.job_id);
      await refreshJobs();
    } catch (error) {
      errorMessage = toErrorMessage(error);
    } finally {
      submitting = false;
    }
  }

  async function handleFileSelect() {
    errorMessage = "";
    if (!runningInTauri()) {
      errorMessage = "浏览器预览模式不能读取本地文件，请粘贴测试路径或在桌面应用中运行。";
      return;
    }
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{
        name: "视频与音频",
        extensions: ["mp4", "mkv", "mov", "avi", "webm", "mp3", "wav", "m4a", "flac"],
      }],
    });
    if (typeof selected === "string") {
      fileSource = selected;
      sourceMode = "file";
    }
  }

  function resetForm() {
    fileSource = "";
    linkSource = "";
    sourceMode = "file";
    title = "";
    startedJobId = null;
    errorMessage = "";
  }

  function fileName(path: string) {
    return path.split(/[\\/]/).pop() || path;
  }
</script>

<div class="page process-page">
  <PageHeader
    eyebrow="AI 笔记工作台"
    title="创建视频笔记"
    description="导入本地媒体或公开视频链接，AI 将自动分析画面与音频，生成结构化笔记。"
    icon="sparkles"
  />

  <div class="workspace-grid">
    <section class="builder-card surface-raised">
      <div class="workflow-steps" aria-label="任务创建步骤">
        <div class="workflow-step active"><span>1</span><div><strong>选择媒体</strong><small>文件或链接</small></div></div>
        <div class="step-line"></div>
        <div class="workflow-step active"><span>2</span><div><strong>编译模式</strong><small>云端精确编译</small></div></div>
        <div class="step-line"></div>
        <div class="workflow-step"><span>3</span><div><strong>后台生成</strong><small>实时同步进度</small></div></div>
      </div>

      <div class="builder-section">
        <div class="section-heading-pro">
          <div class="section-number">01</div>
          <div><h2>选择媒体来源</h2><p>支持视频、音频文件及常见公开平台链接。</p></div>
        </div>

        <div class="source-mode-tabs" role="tablist" aria-label="媒体来源类型">
          <button type="button" class:active={sourceMode === "file"} onclick={() => changeSourceMode("file")}><Icon name="folder-open" size={16} />本地文件</button>
          <button type="button" class:active={sourceMode === "link"} onclick={() => changeSourceMode("link")}><Icon name="link" size={16} />公开视频链接</button>
        </div>

        {#if sourceMode === "file"}
          <button class="media-picker" class:has-source={Boolean(fileSource.trim())} onclick={handleFileSelect} type="button">
            <div class="picker-icon"><Icon name={fileSource ? "video" : "upload"} size={25} /></div>
            {#if fileSource.trim()}
              <div class="picker-copy"><strong>{fileName(fileSource)}</strong><span>{fileSource}</span></div>
              <span class="replace-link">更换文件</span>
            {:else}
              <div class="picker-copy"><strong>选择本地视频或音频</strong><span>MP4、MKV、MOV、WebM、MP3、WAV 等</span></div>
              <span class="browse-chip">浏览文件</span>
            {/if}
          </button>
        {:else}
          <div class="link-source-card">
            <span class="link-source-icon"><Icon name="link" size={20} /></span>
            <div class="field">
              <label class="field-label" for="public-url">公开视频链接</label>
              <div class="input-wrap">
                <input id="public-url" class="url-input" type="url" bind:value={linkSource} placeholder="https://www.bilibili.com/video/BV... 或 https://www.youtube.com/watch?v=..." onkeydown={(event) => event.key === "Enter" && canSubmit && startProcess()} />
                {#if linkSource}<button class="input-clear" type="button" aria-label="清空链接" onclick={() => linkSource = ""}><Icon name="x" size={14} /></button>{/if}
              </div>
              <small class="field-help">粘贴完整视频详情页地址。B站不要粘贴首页、列表页或被截断的 /vi 地址；需要登录的视频请先在设置里配置 Cookie 文件。</small>
              {#if publicUrlMessage}<div class="model-scan-notice"><Icon name="alert" size={14} /><span>{publicUrlMessage}</span></div>{/if}
            </div>
          </div>
        {/if}

        <div class="field">
          <label class="field-label" for="title">笔记标题 <small>可选，留空时自动识别</small></label>
          <input id="title" type="text" bind:value={title} placeholder="例如：产品设计系统课程 · 第三讲" />
        </div>
      </div>

      <div class="builder-divider"></div>

      <div class="builder-section">
        <div class="section-heading-pro">
          <div class="section-number">02</div>
          <div><h2>配置编译方式</h2><p>选择编译模式，并按需要启用视觉理解。</p></div>
        </div>

        <div class="enhancement-grid" aria-label="内容增强开关">
          <div class="enhancement-card enabled">
            <div class="enhance-icon"><Icon name="cloud" size={20} /></div>
            <div class="enhance-copy"><strong>云端精确编译</strong><span>调用多模态 AI 分析视频帧和音频，输出结构化笔记</span></div>
          </div>
        </div>

        <div class="template-selector">
          <span class="field-label">笔记模板</span>
          <div class="template-grid">
            {#each templates as tpl}
              <button type="button" class="template-card" class:active={template === tpl.id} onclick={() => template = tpl.id} aria-pressed={template === tpl.id}>
                <div class="template-icon">
                  {#if tpl.id === "default"}<Icon name="note" size={18} />
                  {:else if tpl.id === "lecture"}<Icon name="book" size={18} />
                  {:else if tpl.id === "summary"}<Icon name="list" size={18} />
                  {:else if tpl.id === "mindmap"}<Icon name="compass" size={18} />
                  {:else}<Icon name="file" size={18} />
                  {/if}
                </div>
                <div class="template-copy">
                  <strong>{tpl.name}</strong>
                  <span>{tpl.description}</span>
                </div>
              </button>
            {/each}
          </div>
        </div>

        <div class="task-preflight">
          <Icon name="info" size={15} />
          <span>编译链路：本地媒体/公开视频 → 多模态 AI 分析 → 结构化笔记。</span>
        </div>

        </div>

      {#if errorMessage}
        <div class="alert alert-error"><Icon name="alert" size={17} /><span>{errorMessage}</span></div>
      {/if}

      <div class="submit-bar">
        <div class="submit-summary">
          <span class="summary-icon"><Icon name="sparkles" size={16} /></span>
          <div><strong>{!source.trim() ? "请先选择媒体来源" : !publicUrlValid ? "请修正公开视频链接" : "已准备好创建任务"}</strong><small>云端精确编译 · {activeProvider ? `AI：${activeProvider}` : "未设置 AI 供应商"}</small></div>
        </div>
        <div class="submit-actions">
          {#if startedJobId !== null}<button class="btn btn-secondary" type="button" onclick={resetForm}>新建另一个</button>{/if}
          <button class="btn btn-primary btn-lg" onclick={startProcess} disabled={!canSubmit}>
            {#if submitting}<span class="spinner"></span>正在提交{:else}<Icon name="play" size={16} />开始处理{/if}
          </button>
        </div>
      </div>

      {#if startedJobId !== null && currentJob}
        <div class="submit-success">
          <div class="success-icon"><Icon name="check" size={24} /></div>
          <div class="success-copy">
            <strong>任务已提交</strong>
            <p>任务 #{currentJob.id} 已进入后台处理，可在<button class="link-btn" onclick={() => requestNavigate("tasks")}>任务中心</button>查看实时进度。</p>
          </div>
        </div>
      {/if}
    </section>


  </div>
</div>

<style>
.process-page { max-width: 1380px; }

  .workspace-grid { display: flex; flex-direction: column; gap: 0; }
  .builder-card { overflow: hidden; }
  .workflow-steps { display: flex; align-items: center; padding: 18px 24px; border-bottom: 1px solid var(--border-color); background: var(--bg-subtle); }
  .workflow-step { display: flex; align-items: center; gap: 9px; min-width: 0; color: var(--text-tertiary); }
  .workflow-step > span { display: grid; place-items: center; width: 27px; height: 27px; border-radius: 9px; background: var(--bg-muted); font-size: 14px; font-weight: 750; }
  .workflow-step.active > span { color: var(--accent-color); background: var(--accent-soft); }
  .workflow-step div { display: flex; min-width: 0; flex-direction: column; }
  .workflow-step strong { color: var(--text-secondary); font-size: 14px; }
  .workflow-step.active strong { color: var(--text-primary); }
  .workflow-step small { margin-top: 1px; font-size: 13px; overflow-wrap: anywhere; }
  .step-line { flex: 1; min-width: 16px; height: 1px; margin: 0 13px; background: var(--border-color); }

  .builder-section { display: flex; flex-direction: column; gap: 17px; padding: 25px 26px; }
  .section-heading-pro { display: flex; align-items: flex-start; gap: 12px; min-width: 0; }
  .section-heading-pro > div:last-child { min-width: 0; }
  .section-number { display: grid; place-items: center; width: 31px; height: 31px; flex: 0 0 auto; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); font-size: 14px; font-weight: 800; letter-spacing: .04em; }
  .section-heading-pro h2 { font-size: 18px; font-weight: 730; text-wrap: balance; }
  .section-heading-pro p { margin-top: 3px; color: var(--text-secondary); font-size: 14px; overflow-wrap: anywhere; text-wrap: pretty; }
  .builder-divider { height: 1px; margin: 0 26px; background: var(--border-color); }

  .media-picker { width: 100%; min-height: 98px; display: flex; align-items: center; gap: 14px; padding: 18px; border: 1.5px dashed var(--border-strong); border-radius: 14px; color: var(--text-primary); background: linear-gradient(145deg, var(--bg-subtle), var(--bg-card)); cursor: pointer; text-align: left; transition: border-color .16s, background .16s, transform .16s; }
  .media-picker:hover { transform: translateY(-1px); border-color: var(--accent-color); background: var(--accent-faint); }
  .media-picker.has-source { border-style: solid; border-color: color-mix(in srgb, var(--success-color) 38%, var(--border-color)); background: color-mix(in srgb, var(--success-soft) 55%, var(--bg-card)); }
  .picker-icon { display: grid; place-items: center; width: 52px; height: 52px; flex: 0 0 auto; border-radius: 15px; color: var(--accent-color); background: var(--accent-soft); }
  .has-source .picker-icon { color: var(--success-color); background: var(--success-soft); }
  .picker-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; }
  .picker-copy strong { font-size: 16px; font-weight: 700; }
  .picker-copy span { max-width: 100%; margin-top: 4px; overflow: hidden; color: var(--text-secondary); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .browse-chip, .replace-link { flex: 0 0 auto; padding: 7px 10px; border-radius: 8px; color: var(--accent-color); background: var(--accent-soft); font-size: 14px; font-weight: 700; }
  .replace-link { color: var(--success-color); background: var(--success-soft); }
  .input-clear { position: absolute; right: 1px; display: grid; place-items: center; width: 40px; height: 40px; border: 0; border-radius: 7px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
  .input-clear:hover { color: var(--text-primary); background: var(--bg-hover); }


  .enhancement-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .enhancement-card { display: flex; align-items: center; gap: 11px; min-height: 76px; padding: 13px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .15s, background .15s; appearance: none; font: inherit; }
  .enhancement-card:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .enhancement-card.enabled { border-color: color-mix(in srgb, var(--accent-color) 55%, var(--border-color)); background: var(--accent-faint); }
  .task-preflight { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: 11px; color: var(--text-secondary); background: var(--bg-subtle); font-size: 13px; line-height: 1.55; }
  .template-selector { display: flex; flex-direction: column; gap: 8px; }
  .template-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; }
  .template-card { display: flex; align-items: center; gap: 10px; min-height: 54px; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: 10px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .15s, background .15s; }
  .template-card:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .template-card.active { border-color: var(--accent-color); background: var(--accent-faint); }
  .template-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto; border-radius: 8px; color: var(--text-secondary); background: var(--bg-muted); }
  .template-card.active .template-icon { color: var(--accent-color); background: var(--accent-soft); }
  .template-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; }
  .template-copy strong { font-size: 12px; }
  .template-copy span { margin-top: 2px; color: var(--text-secondary); font-size: 11px; line-height: 1.35; }
  .enhance-icon { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 auto; border-radius: 11px; color: var(--text-secondary); background: var(--bg-muted); }
  .enabled .enhance-icon { color: var(--accent-color); background: var(--accent-soft); }
  .enhance-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; }
  .enhance-copy strong { font-size: 14px; }
  .enhance-copy span { margin-top: 3px; color: var(--text-secondary); font-size: 13px; line-height: 1.45; }

  .submit-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 17px 26px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .submit-summary { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .summary-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .submit-summary div { display: flex; min-width: 0; flex-direction: column; }
  .submit-summary strong { font-size: 14px; }
  .submit-summary small { margin-top: 2px; color: var(--text-tertiary); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .submit-actions { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; }
  .spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.4); border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .workspace-grid { gap: 16px; }
  .step-line { margin: 0 10px; }
  .media-picker:hover { background: var(--accent-faint); }

  @media (max-width: 1180px) {
    .workspace-grid { gap: 14px; }
  }

  @media (max-width: 1100px) {
    .workspace-grid { gap: 14px; }
    .builder-section { padding: 20px; }
    .builder-divider { margin: 0 20px; }
    .submit-bar { padding: 14px 20px; }
  }

  @media (max-width: 1050px) {
    .enhancement-grid { grid-template-columns: 1fr; }
  }


  /* UI v7 — usable creator workspace */
  .process-page { max-width: 1240px; }
  .builder-card { border-radius: 16px; box-shadow: var(--shadow-sm); }
  .workflow-steps { padding: 16px 24px; }
  .workflow-step > span { width: 30px; height: 30px; }
  .workflow-step strong { font-size: 13px; }
  .workflow-step small { font-size: 11px; color: var(--text-tertiary); }
  .builder-section { gap: 18px; padding: 26px 30px; }
  .builder-divider { margin: 0 30px; }
  .section-number { width: 32px; height: 32px; }
  .section-heading-pro h2 { font-size: 17px; font-weight: 720; }
  .section-heading-pro p { margin-top: 4px; color: var(--text-tertiary); font-size: 12px; }
  .media-picker { min-height: 108px; padding: 20px; border-radius: 14px; }
  .picker-icon { width: 50px; height: 50px; }
  .picker-copy strong { font-size: 15px; }
  .picker-copy span { font-size: 12px; color: var(--text-tertiary); }
  .enhancement-card { min-height: 80px; padding: 14px; border-radius: 12px; }
  .enhance-copy strong { font-size: 13px; }
  .enhance-copy span { font-size: 11px; color: var(--text-tertiary); }
  .submit-bar { padding: 16px 30px; }
  .submit-summary strong { font-size: 13px; }
  .submit-summary small { font-size: 11px; color: var(--text-tertiary); }
  .task-preflight { font-size: 11px; color: var(--text-tertiary); }

  @media (max-width: 1280px) {
    .workspace-grid { gap: 16px; }
  }

  @media (max-width: 960px) {
    .enhancement-grid { grid-template-columns: 1fr; }
    .template-grid { grid-template-columns: repeat(2, 1fr); }
    .submit-bar { flex-direction: column; align-items: stretch; gap: 12px; }
    .submit-summary { min-width: 0; }
    .submit-summary small { white-space: normal; }
    .submit-actions { justify-content: flex-end; }
    .workflow-step small { display: none; }
    .picker-copy span { white-space: normal; }
  }

  @media (max-width: 900px) {
    .builder-section { padding: 16px 14px; }
    .builder-divider { margin: 0 14px; }
    .submit-bar { padding: 12px 14px; }
    .section-heading-pro h2 { font-size: 17px; }
    .section-heading-pro p { font-size: 12px; }
    .media-picker { min-height: 90px; padding: 14px; }
    .picker-icon { width: 44px; height: 44px; }
    .picker-copy strong { font-size: 15px; }
    .enhancement-card { min-height: 72px; padding: 12px; }
    .source-mode-tabs { width: 100%; justify-content: center; }
    .source-mode-tabs button { flex: 1; justify-content: center; }
  }


  /* Task creation interaction — one source mode, one real model selector, one primary action. */
  .source-mode-tabs { display: inline-flex; align-self: flex-start; gap: 3px; padding: 3px; border: 1px solid var(--border-color); border-radius: 10px; background: var(--bg-subtle); }
  .source-mode-tabs button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 40px; padding: 6px 10px; border: 0; border-radius: 7px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 12px; font-weight: 650; }
  .source-mode-tabs button:hover { color: var(--text-primary); background: var(--bg-card); }
  .source-mode-tabs button.active { color: var(--accent-color); background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .link-source-card { display: grid; grid-template-columns: 40px minmax(0,1fr); gap: 11px; padding: 15px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .link-source-icon { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .link-source-card .field { gap: 6px; }
  .field-help { color: var(--text-tertiary); font-size: 11px; line-height: 1.5; }

  .model-scan-notice { display: flex; align-items: flex-start; gap: 6px; padding: 8px 10px; border-radius: 8px; color: var(--danger-color); background: var(--danger-soft); font-size: 11px; line-height: 1.45; }
  
  .url-input { width: 100%; min-width: 0; padding-right: 42px; }
  .link-source-card { align-items: start; }
  @media (max-width: 1050px) {
    .enhancement-grid { grid-template-columns: 1fr; }
  }

  /* Submit success state */
  .submit-success {
    display: flex; align-items: flex-start; gap: 14px;
    margin-top: 18px; padding: 16px 18px;
    border: 1px solid color-mix(in srgb, var(--success-color) 25%, var(--border-color));
    border-radius: 12px;
    background: var(--success-soft);
  }
  .success-icon {
    display: grid; place-items: center;
    width: 42px; height: 42px; flex: 0 0 auto;
    border-radius: 11px;
    color: var(--success-color);
    background: color-mix(in srgb, var(--success-color) 12%, transparent);
  }
  .success-copy { display: flex; flex-direction: column; gap: 4px; }
  .success-copy strong { font-size: 15px; }
  .success-copy p { color: var(--text-secondary); font-size: 13px; line-height: 1.5; }
  .link-btn { border: 0; color: var(--accent-color); background: transparent; cursor: pointer; font-size: 13px; font-weight: 650; padding: 0; text-decoration: underline; }
  .link-btn:hover { color: var(--accent-strong); }
</style>
