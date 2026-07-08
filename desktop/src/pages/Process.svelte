<script lang="ts">
  import { onMount } from "svelte";
  import { open } from "@tauri-apps/plugin-dialog";
  import { engineCall, runningInTauri } from "../lib/api";
  import { jobs, refreshJobs } from "../lib/stores/jobs";
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

  let sourceMode = $state<"file" | "link">("file");
  let fileSource = $state("");
  let linkSource = $state("");
  let title = $state("");
  let whisperModel = $state("large-v3");
  let transcriptionBackend = $state<"whisper_cpp">("whisper_cpp");
  let ocrEnabled = $state(false);
  let ocrBackend = $state<"tesseract" | "paddleocr_http" | "custom_http">("tesseract");
  let ocrModel = $state("PaddleOCR-VL-1.6");
  let visionEnabled = $state(false);
  let activeProvider = $state("");
  let whisperDevice = $state<"auto" | "cuda" | "cpu">("auto");
  let submitting = $state(false);
  let errorMessage = $state("");
  let startedJobId = $state<number | null>(null);
  let advancedOpen = $state(false);
  let localWhisperModels = $state<LocalWhisperModel[]>([]);
  let modelsLoading = $state(true);
  let modelScanError = $state("");
  const paddleOcrModelOptions = [
    "PaddleOCR-VL-1.6",
    "PaddleOCR-VL-1.5",
    "PaddleOCR-VL",
    "PP-StructureV3",
    "PP-OCRv6",
    "PP-OCRv5",
    "PP-OCRv5-latin",
  ];

  let source = $derived(sourceMode === "file" ? fileSource : linkSource);
  let publicUrlValid = $derived(sourceMode !== "link" || isSupportedPublicUrl(linkSource));
  let publicUrlMessage = $derived(sourceMode === "link" ? linkValidationMessage(linkSource) : "");
  let whisperCatalog = $derived(buildWhisperModelCatalog(localWhisperModels, whisperModel, false));
  let selectableWhisperModels = $derived(whisperCatalog.filter((model) => model.installed));
  let selectedWhisperModel = $derived(selectableWhisperModels.find((model) => model.id === normalizeWhisperModelId(whisperModel)));
  let modelReady = $derived(Boolean(selectedWhisperModel));
  let canSubmit = $derived(Boolean(source.trim()) && publicUrlValid && modelReady && !submitting);

  let currentJob = $derived(
    startedJobId === null ? undefined : $jobs.find((job) => job.id === startedJobId)
  );
  let activeCount = $derived($jobs.filter((job) => ["pending", "running", "pausing", "cancelling", "paused"].includes(job.status)).length);
  let completedCount = $derived($jobs.filter((job) => job.status === "completed").length);
  let recentJob = $derived($jobs[0]);

  onMount(async () => {
    modelsLoading = true;
    try {
      const [settings, discovered] = await Promise.all([
        engineCall<{
          whisper_model?: string;
          transcription_backend?: string;
          ocr_enabled?: boolean;
          ocr_backend?: string;
          ocr_model?: string;
          vision_enabled?: boolean;
          active_provider?: string;
          whisper_device?: string;
        }>("settings.get"),
        engineCall<Array<string | LocalWhisperModel>>("settings.models.local"),
      ]);
      const normalizedModels = normalizeLocalWhisperModels(discovered);
      localWhisperModels = normalizedModels;
      const preferred = normalizeWhisperModelId(settings.whisper_model) || "large-v3";
      transcriptionBackend = "whisper_cpp";
      whisperModel = normalizedModels.some((model) => model.id === preferred)
        ? preferred
        : normalizedModels[0]?.id || preferred;
      ocrEnabled = Boolean(settings.ocr_enabled);
      ocrBackend = (["tesseract", "paddleocr_http", "custom_http"].includes(String(settings.ocr_backend)) ? String(settings.ocr_backend) : "tesseract") as "tesseract" | "paddleocr_http" | "custom_http";
      ocrModel = String(settings.ocr_model || "PaddleOCR-VL-1.6");
      visionEnabled = Boolean(settings.vision_enabled);
      activeProvider = String(settings.active_provider || "");
      whisperDevice = (["auto", "cuda", "cpu"].includes(String(settings.whisper_device)) ? String(settings.whisper_device) : "auto") as "auto" | "cuda" | "cpu";
      if (visionEnabled && !activeProvider) {
        visionEnabled = false;
        modelScanError = "默认开启了视觉理解，但尚未配置活动 AI 供应商。本次任务已先关闭视觉理解。";
      }
      if (normalizedModels.length === 0) {
        modelScanError = "没有检测到可运行的本地 Whisper 模型。请先在设置中配置模型目录并扫描。";
      } else if (whisperModel !== preferred) {
        modelScanError = `默认模型“${preferred}”未安装，已为本次任务切换到“${whisperModel}”。`;
      }
    } catch (error) {
      modelScanError = error instanceof Error ? error.message : String(error);
    } finally {
      modelsLoading = false;
    }
  });

  async function scanLocalModels() {
    modelsLoading = true;
    modelScanError = "";
    try {
      const discovered = await engineCall<Array<string | LocalWhisperModel>>("settings.models.local");
      const normalized = normalizeLocalWhisperModels(discovered);
      localWhisperModels = normalized;
      const current = normalizeWhisperModelId(whisperModel);
      whisperModel = normalized.some((model) => model.id === current)
        ? current
        : normalized[0]?.id || current;
      if (normalized.length === 0) {
        modelScanError = "没有检测到可运行的本地 Whisper 模型。";
      }
    } catch (error) {
      modelScanError = error instanceof Error ? error.message : String(error);
    } finally {
      modelsLoading = false;
    }
  }



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
      const result = await engineCall<{ job_id: number }>("process.start", {
        input,
        title: title.trim() || undefined,
        transcription_backend: transcriptionBackend,
        whisper_model: normalizeWhisperModelId(whisperModel),
        ocr_enabled: ocrEnabled,
        ocr_backend: ocrBackend,
        ocr_model: ocrModel,
        vision_enabled: visionEnabled,
        whisper_device: whisperDevice,
      });
      startedJobId = result.job_id;
      await refreshJobs();
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : String(error);
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
    description="导入本地媒体或公开视频链接，AI 将自动完成转录、画面分析与结构化笔记生成。"
    icon="sparkles"
  >
    {#snippet actions()}
      <div class="header-stats">
        <div><strong>{activeCount}</strong><span>处理中</span></div>
        <div><strong>{completedCount}</strong><span>已完成</span></div>
      </div>
    {/snippet}
  </PageHeader>

  <div class="workspace-grid">
    <section class="builder-card surface-raised">
      <div class="workflow-steps" aria-label="任务创建步骤">
        <div class="workflow-step active"><span>1</span><div><strong>选择媒体</strong><small>文件或链接</small></div></div>
        <div class="step-line"></div>
        <div class="workflow-step active"><span>2</span><div><strong>配置处理</strong><small>模型与增强</small></div></div>
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
          <div><h2>配置处理方式</h2><p>选择转录模型，并按需要启用画面文字和视觉理解。</p></div>
        </div>

        <div class="field model-control-field">
          <div class="model-control-head">
            <div><div class="field-label">Whisper 转录模型 <small>仅展示当前机器实际可用的本地模型</small></div></div>
            <button class="btn btn-secondary btn-sm" type="button" onclick={scanLocalModels} disabled={modelsLoading}><Icon name="refresh" size={13} />{modelsLoading ? "扫描中" : "重新扫描"}</button>
          </div>

          {#if modelsLoading}
            <div class="model-loading"><span class="loading-ring compact"></span><div><strong>正在读取本地模型</strong><small>检查模型目录中的 ggml 模型文件…</small></div></div>
          {:else if selectableWhisperModels.length === 0}
            <div class="model-blocked">
              <span><Icon name="alert" size={18} /></span>
              <div><strong>没有可用的 Whisper 模型</strong><p>请前往“设置 → 通用与转录”，填写模型目录并点击“扫描本地模型”。</p></div>
            </div>
          {:else}
            <div class="model-picker-control">
              <div class="model-select-wrap">
                <label for="task-whisper-model">本次任务使用</label>
                <select id="task-whisper-model" bind:value={whisperModel}>
                  {#each selectableWhisperModels as model}<option value={model.id}>{model.label} · {model.id}</option>{/each}
                </select>
              </div>
              {#if selectedWhisperModel}
                <div class="selected-model-detail">
                  <span class="selected-model-icon"><Icon name="audio" size={19} /></span>
                  <div><strong>{selectedWhisperModel.label}</strong><small>{selectedWhisperModel.description} · {selectedWhisperModel.speed}</small><code>{selectedWhisperModel.path || selectedWhisperModel.id}</code></div>
                  <span class="ready-badge"><Icon name="check" size={12} />本地可用</span>
                </div>
              {/if}
            </div>
          {/if}

          {#if modelScanError}<div class="model-scan-notice" class:warning={selectableWhisperModels.length > 0}><Icon name={selectableWhisperModels.length > 0 ? "info" : "alert"} size={14} /><span>{modelScanError}</span></div>{/if}

          <div class="whisper-runtime-grid">
            <div class="field">
              <label class="field-label" for="task-transcription-backend">转写后端</label>
              <select id="task-transcription-backend" bind:value={transcriptionBackend}>
                <option value="whisper_cpp">whisper.cpp native CLI</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label" for="task-whisper-device">运行设备</label>
              <select id="task-whisper-device" bind:value={whisperDevice}>
                <option value="auto">自动：优先 CUDA，可降级 CPU</option>
                <option value="cuda">仅 CUDA / GPU：不可用则任务失败</option>
                <option value="cpu">CPU</option>
              </select>
            </div>
          </div>
        </div>

        <div class="enhancement-grid" aria-label="内容增强开关">
          <button type="button" class="enhancement-card" class:enabled={ocrEnabled} onclick={() => (ocrEnabled = !ocrEnabled)} aria-pressed={ocrEnabled}>
            <div class="enhance-icon"><Icon name="ocr" size={20} /></div>
            <div class="enhance-copy"><strong>OCR 文字识别</strong><span>提取幻灯片、字幕和画面中的文字信息</span></div>
            <span class="switch" aria-hidden="true">
              <input type="checkbox" checked={ocrEnabled} tabindex="-1" />
              <span class="switch-track"></span>
            </span>
          </button>

          <label class="enhancement-card ocr-backend-card" aria-disabled={!ocrEnabled}>
            <div class="enhance-icon"><Icon name="scan" size={20} /></div>
            <div class="enhance-copy"><strong>OCR 后端</strong><span>{ocrBackend === "tesseract" ? "native executable" : "HTTP API"}</span></div>
            <select bind:value={ocrBackend} disabled={!ocrEnabled}>
              <option value="tesseract">Tesseract native</option>
              <option value="paddleocr_http">PaddleOCR HTTP</option>
              <option value="custom_http">Custom HTTP OCR</option>
            </select>
          </label>

          {#if ocrBackend === "paddleocr_http"}
            <label class="enhancement-card ocr-backend-card" aria-disabled={!ocrEnabled}>
              <div class="enhance-icon"><Icon name="scan" size={20} /></div>
              <div class="enhance-copy"><strong>PaddleOCR 模型</strong><span>{ocrModel}</span></div>
              <select bind:value={ocrModel} disabled={!ocrEnabled}>
                {#if ocrModel && !paddleOcrModelOptions.includes(ocrModel)}
                  <option value={ocrModel}>{ocrModel}</option>
                {/if}
                {#each paddleOcrModelOptions as model}
                  <option value={model}>{model}</option>
                {/each}
              </select>
            </label>
          {/if}

          <button type="button" class="enhancement-card" class:enabled={visionEnabled} class:disabled={!activeProvider} onclick={() => activeProvider && (visionEnabled = !visionEnabled)} aria-pressed={visionEnabled} disabled={!activeProvider}>
            <div class="enhance-icon"><Icon name="eye" size={20} /></div>
            <div class="enhance-copy"><strong>视觉理解</strong><span>{activeProvider ? `调用活动供应商：${activeProvider}` : "请先在设置中配置活动 AI 供应商"}</span></div>
            <span class="switch" aria-hidden="true">
              <input type="checkbox" checked={visionEnabled} tabindex="-1" />
              <span class="switch-track"></span>
            </span>
          </button>
        </div>

        <div class="task-preflight">
          <Icon name="info" size={15} />
          <span>真实任务链路：本地媒体/公开视频 → Whisper 转录 → 可选 OCR/视觉理解 → 当前活动 AI 供应商生成笔记。</span>
        </div>

        <button type="button" class="advanced-toggle" onclick={() => advancedOpen = !advancedOpen}>
          <span><Icon name="settings" size={15} />高级选项</span>
          <Icon name="chevron-down" size={15} className={advancedOpen ? "rotate-icon" : ""} />
        </button>
        {#if advancedOpen}
          <div class="advanced-panel">
            <div><Icon name="shield" size={17} /><span><strong>实时阶段记录</strong><small>本机任务状态会同步处理阶段、进度和输出路径</small></span><span class="always-on">始终开启</span></div>
            <div><Icon name="database" size={17} /><span><strong>临时工作区</strong><small>中间产物写入 AppData，完成后可在设置中清理</small></span><span class="always-on">始终开启</span></div>
          </div>
        {/if}
      </div>

      {#if errorMessage}
        <div class="alert alert-error"><Icon name="alert" size={17} /><span>{errorMessage}</span></div>
      {/if}

      <div class="submit-bar">
        <div class="submit-summary">
          <span class="summary-icon"><Icon name="sparkles" size={16} /></span>
          <div><strong>{!source.trim() ? "请先选择媒体来源" : !publicUrlValid ? "请修正公开视频链接" : !modelReady ? "请先选择可用转录模型" : "已准备好创建任务"}</strong><small>{selectedWhisperModel?.label || whisperModel} · {whisperDevice === "cuda" ? "CUDA" : whisperDevice === "cpu" ? "CPU" : "自动设备"} · {ocrEnabled ? "OCR 开启" : "OCR 关闭"} · {visionEnabled ? "视觉理解开启" : "视觉理解关闭"} · {activeProvider ? `AI：${activeProvider}` : "未设置 AI 供应商"}</small></div>
        </div>
        <div class="submit-actions">
          {#if startedJobId !== null}<button class="btn btn-secondary" type="button" onclick={resetForm}>新建另一个</button>{/if}
          <button class="btn btn-primary btn-lg" onclick={startProcess} disabled={!canSubmit}>
            {#if submitting}<span class="spinner"></span>正在提交{:else}<Icon name="play" size={16} />开始处理{/if}
          </button>
        </div>
      </div>
    </section>

    <aside class="side-column">
      <section class="current-task surface">
        <div class="side-title"><div><span>LIVE STATUS</span><h2>当前任务</h2></div>{#if currentJob}<StatusPill status={currentJob.status} />{/if}</div>
        {#if currentJob}
          <div class="current-job-head">
            <div class="job-media-icon"><Icon name="video" size={19} /></div>
            <div><strong>{currentJob.title || fileName(currentJob.input) || "未命名任务"}</strong><small>任务 #{currentJob.id} · 第 {currentJob.attempt || 1} 次执行</small></div>
          </div>
          <div class="big-progress-number"><strong>{Math.round(currentJob.progress || 0)}</strong><span>%</span></div>
          <div class="progress-track"><div class="progress-bar" style={`width:${Math.max(1, currentJob.progress || 0)}%`}></div></div>
          <div class="progress-caption"><span>{currentJob.progress_message || currentJob.stage}</span><span>实时同步</span></div>
          {#if currentJob.error_message}<div class="alert alert-error mini-alert"><Icon name="alert" size={15} /><span>{currentJob.error_message}</span></div>{/if}
          <div class="persistence-note"><Icon name="shield" size={16} /><p>当前任务进度实时同步；导出的笔记和 assets 会保存在输出目录。</p></div>
        {:else}
          <EmptyState icon="activity" title="尚未提交任务" description="开始处理后，实时阶段和进度会显示在这里。" compact />
        {/if}
      </section>

      <section class="quick-overview surface">
        <div class="side-title"><div><span>WORKSPACE</span><h2>工作概览</h2></div></div>
        <div class="overview-grid">
          <div><span class="overview-icon active"><Icon name="activity" size={17} /></span><strong>{activeCount}</strong><small>活动任务</small></div>
          <div><span class="overview-icon done"><Icon name="check" size={17} /></span><strong>{completedCount}</strong><small>完成任务</small></div>
        </div>
        {#if recentJob}
          <div class="recent-row"><span>最近任务</span><strong>{recentJob.title || fileName(recentJob.input)}</strong><StatusPill status={recentJob.status} /></div>
        {:else}
          <div class="recent-row empty"><span>最近任务</span><strong>暂无任务记录</strong></div>
        {/if}
      </section>

      <section class="tips-card">
        <div class="tip-icon"><Icon name="info" size={17} /></div>
        <div><strong>首次运行建议</strong><p>先用 30–60 秒视频关闭 OCR 和视觉理解测试主链路，再逐项启用增强功能。</p></div>
      </section>
    </aside>
  </div>
</div>

<style>
.process-page { max-width: 1380px; }
  .header-stats { display: flex; align-items: center; gap: 6px; padding: 5px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .header-stats div { min-width: 70px; padding: 5px 11px; text-align: center; }
  .header-stats div + div { border-left: 1px solid var(--border-color); }
  .header-stats strong { display: block; font-size: 20px; line-height: 1.1; }
  .header-stats span { color: var(--text-tertiary); font-size: 13px; }

  .workspace-grid { display: grid; grid-template-columns: minmax(0, 1fr) 328px; gap: 20px; align-items: start; }
  .builder-card { overflow: hidden; }
  .workflow-steps { display: flex; align-items: center; padding: 18px 24px; border-bottom: 1px solid var(--border-color); background: var(--bg-subtle); }
  .workflow-step { display: flex; align-items: center; gap: 9px; color: var(--text-tertiary); }
  .workflow-step > span { display: grid; place-items: center; width: 27px; height: 27px; border-radius: 9px; background: var(--bg-muted); font-size: 14px; font-weight: 750; }
  .workflow-step.active > span { color: var(--accent-color); background: var(--accent-soft); }
  .workflow-step div { display: flex; flex-direction: column; }
  .workflow-step strong { color: var(--text-secondary); font-size: 14px; }
  .workflow-step.active strong { color: var(--text-primary); }
  .workflow-step small { margin-top: 1px; font-size: 13px; }
  .step-line { flex: 1; height: 1px; margin: 0 13px; background: var(--border-color); }

  .builder-section { display: flex; flex-direction: column; gap: 17px; padding: 25px 26px; }
  .section-heading-pro { display: flex; align-items: flex-start; gap: 12px; }
  .section-number { display: grid; place-items: center; width: 31px; height: 31px; flex: 0 0 auto; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); font-size: 14px; font-weight: 800; letter-spacing: .04em; }
  .section-heading-pro h2 { font-size: 18px; font-weight: 730; }
  .section-heading-pro p { margin-top: 3px; color: var(--text-secondary); font-size: 14px; }
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
  .input-clear { position: absolute; right: 7px; display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: 7px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
  .input-clear:hover { color: var(--text-primary); background: var(--bg-hover); }


  .enhancement-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .enhancement-card { display: flex; align-items: center; gap: 11px; min-height: 76px; padding: 13px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .15s, background .15s; appearance: none; font: inherit; }
  .enhancement-card:hover { border-color: var(--border-strong); background: var(--bg-subtle); }
  .enhancement-card.enabled { border-color: color-mix(in srgb, var(--accent-color) 55%, var(--border-color)); background: var(--accent-faint); }
  .enhancement-card.disabled { opacity: .62; cursor: not-allowed; }
  .ocr-backend-card { cursor: default; flex-wrap: wrap; }
  .ocr-backend-card[aria-disabled="true"] { opacity: .62; }
  .ocr-backend-card select { width: 100%; flex: 1 1 0; min-width: 0; }
  .task-preflight { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: 11px; color: var(--text-secondary); background: var(--bg-subtle); font-size: 13px; line-height: 1.55; }
  .enhance-icon { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 auto; border-radius: 11px; color: var(--text-secondary); background: var(--bg-muted); }
  .enabled .enhance-icon { color: var(--accent-color); background: var(--accent-soft); }
  .enhance-copy { display: flex; flex: 1; min-width: 0; flex-direction: column; }
  .enhance-copy strong { font-size: 14px; }
  .enhance-copy span { margin-top: 3px; color: var(--text-secondary); font-size: 13px; line-height: 1.45; }

  .advanced-toggle { display: flex; align-items: center; justify-content: space-between; width: 100%; padding: 8px 2px; border: 0; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 14px; font-weight: 650; }
  .advanced-toggle > span { display: flex; align-items: center; gap: 7px; }
  :global(.rotate-icon) { transform: rotate(180deg); }
  .advanced-panel { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 12px; border-radius: 12px; background: var(--bg-subtle); border: 1px solid var(--border-color); }
  .advanced-panel > div { display: flex; align-items: flex-start; gap: 8px; color: var(--text-secondary); }
  .advanced-panel > div > span { display: flex; flex: 1; flex-direction: column; }
  .advanced-panel strong { color: var(--text-primary); font-size: 14px; }
  .advanced-panel small { margin-top: 2px; font-size: 12px; line-height: 1.45; }
  .always-on { flex: 0 0 auto !important; color: var(--success-color); font-size: 12px; font-weight: 700; }

  .submit-bar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 17px 26px; border-top: 1px solid var(--border-color); background: var(--bg-subtle); }
  .submit-summary { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .summary-icon { display: grid; place-items: center; width: 34px; height: 34px; flex: 0 0 auto; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .submit-summary div { display: flex; min-width: 0; flex-direction: column; }
  .submit-summary strong { font-size: 14px; }
  .submit-summary small { margin-top: 2px; color: var(--text-tertiary); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .submit-actions { display: flex; gap: 8px; }
  .spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.4); border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .side-column { display: flex; flex-direction: column; gap: 14px; }
  .current-task, .quick-overview { padding: 19px; }
  .side-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 16px; }
  .side-title > div { display: flex; flex-direction: column; }
  .side-title span:first-child { color: var(--accent-color); font-size: 12px; font-weight: 800; letter-spacing: .12em; }
  .side-title h2 { margin-top: 3px; font-size: 16px; }
  .current-job-head { display: flex; align-items: center; gap: 10px; }
  .job-media-icon { display: grid; place-items: center; width: 40px; height: 40px; flex: 0 0 auto; border-radius: 12px; color: var(--accent-color); background: var(--accent-soft); }
  .current-job-head > div:last-child { display: flex; min-width: 0; flex-direction: column; }
  .current-job-head strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .current-job-head small { margin-top: 2px; color: var(--text-tertiary); font-size: 13px; }
  .big-progress-number { display: flex; align-items: baseline; margin: 20px 0 9px; }
  .big-progress-number strong { font-size: 40px; line-height: 1; font-weight: 760; letter-spacing: -.05em; }
  .big-progress-number span { margin-left: 3px; color: var(--text-tertiary); font-size: 15px; }
  .progress-caption { display: flex; justify-content: space-between; gap: 8px; margin-top: 7px; color: var(--text-secondary); font-size: 12px; }
  .progress-caption span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .progress-caption span:last-child { flex: 0 0 auto; color: var(--success-color); }
  .mini-alert { margin-top: 12px; }
  .persistence-note { display: flex; align-items: flex-start; gap: 8px; margin-top: 17px; padding-top: 13px; border-top: 1px solid var(--border-color); color: var(--text-tertiary); }
  .persistence-note p { font-size: 12px; line-height: 1.55; }

  .overview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .overview-grid > div { display: grid; grid-template-columns: 34px 1fr; grid-template-rows: auto auto; column-gap: 9px; padding: 11px; border-radius: 11px; background: var(--bg-subtle); border: 1px solid var(--border-color); }
  .overview-icon { grid-row: 1 / 3; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 10px; }
  .overview-icon.active { color: var(--info-color); background: var(--info-soft); }
  .overview-icon.done { color: var(--success-color); background: var(--success-soft); }
  .overview-grid strong { font-size: 18px; line-height: 1.1; }
  .overview-grid small { color: var(--text-tertiary); font-size: 12px; }
  .recent-row { display: grid; grid-template-columns: auto minmax(0,1fr) auto; align-items: center; gap: 8px; margin-top: 11px; padding-top: 11px; border-top: 1px solid var(--border-color); }
  .recent-row > span:first-child { color: var(--text-tertiary); font-size: 12px; }
  .recent-row > strong { min-width: 0; overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .recent-row.empty { grid-template-columns: auto 1fr; }

  .tips-card { display: flex; align-items: flex-start; gap: 10px; padding: 14px; border: 1px solid color-mix(in srgb, var(--info-color) 17%, var(--border-color)); border-radius: 13px; background: var(--info-soft); }
  .tip-icon { color: var(--info-color); }
  .tips-card div:last-child { display: flex; flex-direction: column; }
  .tips-card strong { color: var(--info-color); font-size: 13px; }
  .tips-card p { margin-top: 3px; color: var(--text-secondary); font-size: 12px; line-height: 1.55; }

  @media (max-width: 1180px) {
    .workspace-grid { grid-template-columns: 1fr; }
    .side-column { display: grid; grid-template-columns: 1fr 1fr; }
    .tips-card { grid-column: 1 / -1; }
  }

  @media (max-width: 1100px) {
    .side-column { grid-template-columns: 1fr; }
  }

  .process-page { max-width: 1280px; }
  .header-stats { padding: 3px; border-radius: 10px; box-shadow: none; }
  .header-stats div { min-width: 66px; padding: 4px 10px; }
  .workspace-grid { grid-template-columns: minmax(0, 1fr) 300px; gap: 16px; }
  .builder-card { border-radius: 18px; box-shadow: var(--shadow-sm); }
  .workflow-steps { padding: 13px 20px; background: var(--bg-card); }
  .workflow-step > span { width: 24px; height: 24px; border-radius: 8px; }
  .workflow-step small { display: none; }
  .step-line { margin: 0 10px; }
  .builder-section { gap: 15px; padding: 22px 23px; }
  .builder-divider { margin: 0 23px; }
  .section-number { width: 28px; height: 28px; border-radius: 9px; }
  .media-picker { min-height: 106px; border-radius: 13px; background: var(--bg-subtle); }
  .media-picker:hover { background: var(--accent-faint); }
  .enhancement-card { min-height: 70px; border-radius: 12px; }
  .submit-bar { padding: 14px 23px; }
  .side-column { position: sticky; top: 0; }
  .current-task, .quick-overview { padding: 17px; border-radius: 15px; }
  .tips-card { border-radius: 13px; }
  @media (max-width: 1180px) { .workspace-grid { grid-template-columns: 1fr; } .side-column { position: static; } }

  @media (max-width: 1100px) {
    .workspace-grid { gap: 14px; }
    .builder-section { padding: 20px; }
    .builder-divider { margin: 0 20px; }
    .submit-bar { padding: 14px 20px; }
  }

  @media (max-width: 1050px) {
    .enhancement-grid { grid-template-columns: 1fr; }
    .ocr-backend-card select { width: 100%; }
    .whisper-runtime-grid { grid-template-columns: 1fr; }
    .model-picker-control { grid-template-columns: 1fr; }
  }


  /* UI v7 — usable creator workspace */
  .process-page { max-width: 1240px; }
  .workspace-grid { grid-template-columns: minmax(0, 1fr) 340px; gap: 22px; }
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
  .advanced-toggle { font-size: 12px; color: var(--text-tertiary); }
  .advanced-panel strong { font-size: 12px; }
  .advanced-panel small, .always-on { font-size: 10px; }
  .submit-bar { padding: 16px 30px; }
  .submit-summary strong { font-size: 13px; }
  .submit-summary small { font-size: 11px; color: var(--text-tertiary); }
  .side-column { position: sticky; top: 0; gap: 14px; }
  .current-task, .quick-overview { padding: 18px; }
  .side-title span:first-child { font-size: 10px; }
  .side-title h2 { font-size: 15px; }
  .current-job-head strong { font-size: 13px; }
  .current-job-head small { font-size: 10px; color: var(--text-tertiary); }
  .progress-caption, .persistence-note p { font-size: 10px; }
  .overview-grid small, .recent-row > span:first-child { font-size: 10px; }
  .recent-row > strong, .tips-card strong { font-size: 11px; }
  .tips-card p { font-size: 10px; }
  .task-preflight { font-size: 11px; color: var(--text-tertiary); }

  @media (max-width: 1280px) {
    .workspace-grid { grid-template-columns: 1fr; }
    .side-column { position: static; display: grid; grid-template-columns: 1fr 1fr; }
    .tips-card { grid-column: 1 / -1; }
  }

  @media (max-width: 960px) {
    .workspace-grid { grid-template-columns: 1fr; }
    .side-column { position: static; display: flex; flex-direction: column; }
    .enhancement-grid { grid-template-columns: 1fr; }
    .model-picker-control { grid-template-columns: 1fr; }
    .whisper-runtime-grid { grid-template-columns: 1fr; }
    .submit-bar { flex-direction: column; align-items: stretch; gap: 12px; }
    .submit-summary { min-width: 0; }
    .submit-summary small { white-space: normal; }
    .submit-actions { justify-content: flex-end; }
    .header-stats { flex-wrap: wrap; }
    .workflow-step small { display: none; }
    .advanced-panel { grid-template-columns: 1fr; }
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
  .source-mode-tabs button { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; padding: 6px 10px; border: 0; border-radius: 7px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 12px; font-weight: 650; }
  .source-mode-tabs button:hover { color: var(--text-primary); background: var(--bg-card); }
  .source-mode-tabs button.active { color: var(--accent-color); background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .link-source-card { display: grid; grid-template-columns: 40px minmax(0,1fr); gap: 11px; padding: 15px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .link-source-icon { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .link-source-card .field { gap: 6px; }
  .field-help { color: var(--text-tertiary); font-size: 11px; line-height: 1.5; }

  .model-control-field { gap: 10px; }
  .model-control-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .model-loading,
  .model-blocked { display: flex; align-items: center; gap: 11px; min-height: 70px; padding: 12px 14px; border: 1px solid var(--border-color); border-radius: 11px; background: var(--bg-subtle); }
  .model-loading > div,
  .model-blocked > div { display: flex; flex: 1; flex-direction: column; }
  .model-loading strong,
  .model-blocked strong { font-size: 13px; }
  .model-loading small,
  .model-blocked p { margin-top: 3px; color: var(--text-tertiary); font-size: 11px; line-height: 1.5; }
  .model-blocked > span { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 9px; color: var(--danger-color); background: var(--danger-soft); }
  .model-picker-control { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(0, 1.3fr); gap: 10px; }
  .model-select-wrap { display: flex; flex-direction: column; gap: 6px; padding: 12px; border: 1px solid var(--border-color); border-radius: 11px; background: var(--bg-card); }
  .model-select-wrap label { color: var(--text-tertiary); font-size: 11px; font-weight: 680; }
  .model-select-wrap select { min-height: 40px; width: 100%; padding: 0 36px 0 10px; border: 1px solid var(--border-strong); border-radius: 8px; color: var(--text-primary); background: var(--bg-input); font-size: 12px; font-weight: 640; }
  .selected-model-detail { display: grid; grid-template-columns: 38px minmax(0,1fr) auto; align-items: center; gap: 10px; padding: 12px; border: 1px solid color-mix(in srgb, var(--success-color) 28%, var(--border-color)); border-radius: 11px; background: color-mix(in srgb, var(--success-soft) 56%, var(--bg-card)); }
  .selected-model-icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 10px; color: var(--success-color); background: var(--success-soft); }
  .selected-model-detail > div { min-width: 0; display: flex; flex-direction: column; }
  .selected-model-detail strong { font-size: 13px; }
  .selected-model-detail small { margin-top: 2px; color: var(--text-tertiary); font-size: 11px; }
  .selected-model-detail code { margin-top: 4px; overflow: hidden; color: var(--text-tertiary); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  .ready-badge { display: inline-flex; align-items: center; gap: 3px; padding: 4px 7px; border-radius: 99px; color: var(--success-color); background: var(--bg-card); font-size: 9px; font-weight: 740; white-space: nowrap; }
  .model-scan-notice { display: flex; align-items: flex-start; gap: 6px; padding: 8px 10px; border-radius: 8px; color: var(--danger-color); background: var(--danger-soft); font-size: 11px; line-height: 1.45; }
  .model-scan-notice.warning { color: var(--warning-color); background: var(--warning-soft); }
  @media (max-width: 900px) { .model-picker-control { grid-template-columns: 1fr; } }
.url-input { width: 100%; min-width: 0; }
  .link-source-card { align-items: start; }
  .whisper-runtime-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 2px; }
  @media (max-width: 980px) { .enhancement-grid { grid-template-columns: 1fr; } .ocr-backend-card select { width: 100%; } }
  @media (max-width: 760px) { .whisper-runtime-grid { grid-template-columns: 1fr; } }
</style>
