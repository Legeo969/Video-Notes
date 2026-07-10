<script lang="ts">
  import Icon from "../Icon.svelte";
  import {
    buildWhisperModelCatalog,
    normalizeWhisperModelId,
  } from "../../whisperModels";
  import type { LocalWhisperModel } from "../../whisperModels";

  interface SettingsBag {
    output_dir: string;
    vault_path: string;
    transcription_backend: string;
    whisper_model: string;
    whisper_model_dir: string;
    whisper_device: string;
    language: string;
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

  let {
    settings = $bindable(),
    localWhisperModels = $bindable([]),
    scanning = $bindable(false),
    testingOcr = $bindable(false),
    refreshingOcrModels = $bindable(false),
    paddleOcrModelOptions = $bindable([]),
    ocrKeyDirty = $bindable(false),
    onMarkDirty,
    onChooseWhisperModel,
    onScanModels,
    onOpenExternalUrl,
    onTestOcrConnection,
    onRefreshOcrModels,
  }: {
    settings: SettingsBag;
    localWhisperModels?: LocalWhisperModel[];
    scanning?: boolean;
    testingOcr?: boolean;
    refreshingOcrModels?: boolean;
    paddleOcrModelOptions?: string[];
    ocrKeyDirty?: boolean;
    onMarkDirty: () => void;
    onChooseWhisperModel: (id: string) => void;
    onScanModels: () => void;
    onOpenExternalUrl: (url: string) => void;
    onTestOcrConnection: () => void;
    onRefreshOcrModels: () => void;
  } = $props();

  const whisperModelDownloadUrl = "https://huggingface.co/ggerganov/whisper.cpp/tree/main";
  const officialPaddleOcrModelOptions = [
    "PaddleOCR-VL-1.6",
    "PaddleOCR-VL-1.5",
    "PaddleOCR-VL",
    "PP-StructureV3",
    "PP-OCRv6",
    "PP-OCRv5",
    "PP-OCRv5-latin",
  ];

  let whisperCatalog = $derived(buildWhisperModelCatalog(localWhisperModels, settings.whisper_model, true));
  let selectedWhisperModel = $derived(whisperCatalog.find((model) => model.id === normalizeWhisperModelId(settings.whisper_model)));
  let selectedWhisperAvailable = $derived(Boolean(selectedWhisperModel?.installed));
</script>

<section class="settings-pane">
  <div class="pane-head"><div><span>GENERAL & TRANSCRIPTION</span><h2>通用与转录</h2><p>设置默认输出位置、Whisper 模型与文字识别能力。</p></div></div>

  <div class="settings-summary-grid">
    <div class="summary-card">
      <span class="summary-icon"><Icon name="folder" size={18} /></span>
      <strong>{settings.vault_path ? "Obsidian Vault" : "默认导出目录"}</strong>
      <small title={settings.vault_path || settings.output_dir}>{settings.vault_path || settings.output_dir || "未配置"}</small>
    </div>
    <div class="summary-card">
      <span class="summary-icon model"><Icon name="audio" size={18} /></span>
      <strong>{selectedWhisperModel?.label || settings.whisper_model}</strong>
      <small>{selectedWhisperAvailable ? "本地模型已就绪" : "等待扫描或安装"}</small>
    </div>
    <div class="summary-card">
      <span class="summary-icon enhance"><Icon name="scan" size={18} /></span>
      <strong>{settings.ocr_enabled || settings.vision_enabled ? "增强已启用" : "基础转录"}</strong>
      <small>OCR {settings.ocr_enabled ? "开启" : "关闭"} · Vision {settings.vision_enabled ? "开启" : "关闭"}</small>
    </div>
  </div>

  <div class="setting-group settings-card-section">
    <div class="group-head"><div class="group-icon"><Icon name="folder" size={18} /></div><div><h3>文件与模型目录</h3><p>配置笔记产物与本地模型的存储位置。</p></div></div>
    <div class="form-grid two-cols">
      <div class="field"><label class="field-label" for="vault_path">Obsidian 笔记库 <small>可选，归档到 vault\video-notes</small></label><div class="input-wrap has-icon path-input"><span class="input-icon"><Icon name="folder" size={15} /></span><input id="vault_path" type="text" bind:value={settings.vault_path} title={settings.vault_path} oninput={onMarkDirty} placeholder="D:\Note_Obsidian" /></div></div>
      <div class="field"><label class="field-label" for="whisper_model_dir">Whisper 模型目录 <small>可选</small></label><div class="input-wrap has-icon path-input"><span class="input-icon"><Icon name="database" size={15} /></span><input id="whisper_model_dir" type="text" bind:value={settings.whisper_model_dir} title={settings.whisper_model_dir} oninput={onMarkDirty} placeholder="留空使用默认缓存目录" /></div></div>
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
    <div class="group-head with-action">
      <div class="group-icon"><Icon name="audio" size={18} /></div>
      <div><h3>默认 Whisper 模型</h3><p>选择新任务默认使用的语音转录模型。</p></div>
      <div class="group-actions">
        <button class="btn btn-secondary btn-sm" type="button" onclick={() => onOpenExternalUrl(whisperModelDownloadUrl)}><Icon name="external" size={13} />模型下载页</button>
        <button class="btn btn-secondary btn-sm" type="button" onclick={onScanModels} disabled={scanning}><Icon name="refresh" size={13} />{scanning ? "扫描中" : "扫描本地模型"}</button>
      </div>
    </div>
    {#if scanning}
      <div class="model-scan-state"><span class="loading-ring compact"></span><div><strong>正在扫描本地模型</strong><small>检查配置目录和应用默认模型目录…</small></div></div>
    {:else if localWhisperModels.length === 0}
      <div class="model-empty-state">
        <span class="model-empty-icon"><Icon name="database" size={22} /></span>
        <div><strong>尚未检测到可用模型</strong><p>模型目录中需要存在 <code>ggml-模型ID.bin</code> 或 <code>ggml-模型ID.gguf</code> 文件。</p></div>
        <button class="btn btn-secondary btn-sm" type="button" onclick={onScanModels}><Icon name="refresh" size={13} />重新扫描</button>
      </div>
    {:else}
      <div class="model-selection-summary" class:warning={!selectedWhisperAvailable}>
        <span class="selection-status"><Icon name={selectedWhisperAvailable ? "check" : "alert"} size={15} /></span>
        <div>
          <strong>{selectedWhisperAvailable ? `当前默认：${selectedWhisperModel?.label}` : `当前配置不可用：${settings.whisper_model}`}</strong>
          <small class="model-path" title={selectedWhisperModel?.path || ""}>{selectedWhisperAvailable ? selectedWhisperModel?.path || "本地模型已就绪" : "请在下方选择一个标记为“已安装”的模型"}</small>
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
            onclick={() => onChooseWhisperModel(model.id)}
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
        <select id="transcription_backend" bind:value={settings.transcription_backend} onchange={onMarkDirty}>
          <option value="whisper_cpp">whisper.cpp native CLI</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label" for="whisper_device">Whisper 运行设备</label>
        <select id="whisper_device" bind:value={settings.whisper_device} onchange={onMarkDirty}>
          <option value="auto">自动：优先 CUDA，可降级 CPU</option>
          <option value="cuda">仅 CUDA / GPU：不可用则任务失败</option>
          <option value="cpu">CPU</option>
        </select>
      </div>
      <div class="field">
        <label class="field-label" for="whisper_language">转录语言</label>
        <select id="whisper_language" bind:value={settings.language} onchange={onMarkDirty}>
          <option value="">自动检测（中文视频可能误判为英文）</option>
          <option value="zh">中文（zh）</option>
          <option value="en">英文（en）</option>
          <option value="ja">日语（ja）</option>
          <option value="ko">韩语（ko）</option>
        </select>
      </div>
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
    <div class="group-head"><div class="group-icon"><Icon name="ocr" size={18} /></div><div><h3>内容增强</h3><p>控制新任务默认启用的 OCR 与视觉理解。这里保存的是默认值，创建任务页仍可单次覆盖。</p></div></div>
    <div class="enhancement-settings-grid">
      <button type="button" class="setting-toggle-card as-button" class:enabled={settings.ocr_enabled} onclick={() => { settings.ocr_enabled = !settings.ocr_enabled; onMarkDirty(); }} aria-pressed={settings.ocr_enabled}>
        <span class="toggle-feature-icon"><Icon name="scan" size={20} /></span>
        <span class="toggle-copy"><strong>OCR 文字识别</strong><small>识别幻灯片、字幕和画面文字。OCR 后端未配置或不可用时任务会给出明确错误，不会静默忽略。</small></span>
        <span class="switch" aria-hidden="true"><input type="checkbox" checked={settings.ocr_enabled} tabindex="-1" /><span class="switch-track"></span></span>
      </button>
      <button type="button" class="setting-toggle-card as-button" class:enabled={settings.vision_enabled} onclick={() => { settings.vision_enabled = !settings.vision_enabled; onMarkDirty(); }} aria-pressed={settings.vision_enabled}>
        <span class="toggle-feature-icon"><Icon name="eye" size={20} /></span>
        <span class="toggle-copy"><strong>视觉理解</strong><small>抽取关键帧并调用当前活动供应商的视觉模型，分析图表、界面和演示步骤。</small></span>
        <span class="switch" aria-hidden="true"><input type="checkbox" checked={settings.vision_enabled} tabindex="-1" /><span class="switch-track"></span></span>
      </button>
    </div>
    <div class="runtime-settings-grid">
      <div class="field">
        <label class="field-label" for="ocr_backend">OCR 引擎选择</label>
        <select id="ocr_backend" bind:value={settings.ocr_backend} onchange={onMarkDirty}>
          <option value="tesseract">Tesseract native executable</option>
          <option value="paddleocr_http">PaddleOCR HTTP service</option>
        </select>
      </div>
      {#if settings.ocr_backend === "paddleocr_http"}
        <div class="field">
            <div class="field-label-row">
              <label class="field-label" for="ocr_model">PaddleOCR Model</label>
              <button type="button" class="btn btn-secondary btn-xs" onclick={onRefreshOcrModels} disabled={refreshingOcrModels} title="重新加载内置官方模型列表（不调用远程 API）">
                <Icon name="refresh" size={12} />{refreshingOcrModels ? "刷新中" : "重置模型列表"}
              </button>
            </div>
            <select id="ocr_model" bind:value={settings.ocr_model} onchange={onMarkDirty}>
              {#if settings.ocr_model && !paddleOcrModelOptions.includes(settings.ocr_model)}
                <option value={settings.ocr_model}>{settings.ocr_model}</option>
              {/if}
              {#each paddleOcrModelOptions as model}
                <option value={model}>{model}</option>
              {/each}
            </select>
            <span class="field-hint">PaddleOCR hosted API 使用官方静态模型列表；手动输入的非官方模型名会保留为当前选项。</span>
          </div>
        <div class="field">
          <label class="field-label" for="ocr_http_endpoint">OCR HTTP Endpoint <small>本地或远程</small></label>
          <div class="input-wrap has-icon"><span class="input-icon"><Icon name="server" size={15} /></span><input id="ocr_http_endpoint" type="text" bind:value={settings.ocr_http_endpoint} oninput={onMarkDirty} placeholder="https://paddleocr.aistudio-app.com/api/v2/ocr/jobs" /></div>
        </div>
        <div class="field">
          <label class="field-label" for="ocr_http_api_key">OCR API Key <small style="color:var(--danger,#e53e3e)">必填</small></label>
          <div class="input-wrap has-icon"><span class="input-icon"><Icon name="key" size={15} /></span><input id="ocr_http_api_key" type="password" bind:value={settings.ocr_http_api_key} oninput={() => { onMarkDirty(); ocrKeyDirty = true; }} placeholder={settings.ocr_api_key_configured && !settings.ocr_http_api_key ? "API Key 已配置" : "填官方 TOKEN，不用写 bearer"} /></div>
        </div>
      {/if}
      <div class="field ocr-test-field">
        <span class="field-label">OCR 连接测试</span>
        <button
          type="button"
          class="btn btn-secondary ocr-test-btn"
          onclick={onTestOcrConnection}
          disabled={testingOcr || (settings.ocr_backend === "paddleocr_http" && !settings.ocr_http_endpoint.trim())}
        >
          <Icon name="activity" size={15} />{testingOcr ? "测试中" : "测试 OCR"}
        </button>
      </div>
            
    </div>
    <div class="enhancement-explain"><Icon name="info" size={14} />OCR 使用配置的 OCR 后端；视觉理解会抽取关键帧并调用当前活动 AI 供应商的视觉模型。首次真实任务建议先关闭两项，确认转录与笔记主链路，再逐项打开。</div>
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

  .interactive-model-cards { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); padding: 0; border: 0; background: transparent; }
  .interactive-model-cards .model-card { position: relative; min-height: 118px; padding: 15px; border: 1px solid var(--border-color); background: var(--bg-card); }
  .interactive-model-cards .model-card:hover:not(:disabled) { border-color: var(--accent-color); background: var(--accent-faint); }
  .interactive-model-cards .model-card.selected { border-color: var(--accent-color); background: var(--accent-faint); box-shadow: 0 0 0 3px var(--accent-glow); }
  .interactive-model-cards .model-card.unavailable { opacity: .58; cursor: not-allowed; }
  .model-availability { position: absolute; top: 12px; right: 12px; padding: 3px 7px; border-radius: 99px; color: var(--text-tertiary); background: var(--bg-muted); font-size: 10px; font-weight: 750; }
  .model-availability.installed { color: var(--success-color); background: var(--success-soft); }
  .model-id { margin-top: 6px; overflow: hidden; color: var(--text-tertiary); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }

  .runtime-settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
  .field-full { grid-column: 1 / -1; }
  .field-label-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .btn-xs { min-height: 26px; padding: 4px 8px; font-size: 12px; }
  .ocr-test-field { justify-content: end; }
  .ocr-test-btn { width: fit-content; min-width: 118px; }

  .setting-toggle-card { display: flex; align-items: center; gap: 12px; width: 100%; padding: 14px; border: 1px solid var(--border-color); border-radius: 13px; color: var(--text-primary); background: var(--bg-card); cursor: pointer; text-align: left; transition: border-color .14s, background .14s; }
  .setting-toggle-card.as-button { appearance: none; font: inherit; }
  .enhancement-settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .enhancement-explain { display: flex; align-items: flex-start; gap: 8px; margin-top: 10px; padding: 10px 12px; border-radius: 10px; color: var(--text-secondary); background: var(--bg-subtle); border: 1px solid var(--border-color); font-size: 12px; line-height: 1.55; }
  .setting-toggle-card.enabled { border-color: color-mix(in srgb, var(--accent-color) 50%, var(--border-color)); background: var(--accent-faint); }
  .toggle-feature-icon { display: grid; place-items: center; width: 41px; height: 41px; border-radius: 12px; color: var(--text-secondary); background: var(--bg-muted); }
  .enabled .toggle-feature-icon { color: var(--accent-color); background: var(--accent-soft); }
  .toggle-copy { display: flex; flex: 1; flex-direction: column; }
  .toggle-copy strong { font-size: 14px; }
  .toggle-copy small { margin-top: 3px; color: var(--text-secondary); font-size: 12px; line-height: 1.5; }

  @media (max-width: 760px) { .runtime-settings-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .enhancement-settings-grid { grid-template-columns: 1fr; } }
  @media (max-width: 760px) { .model-cards { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); } }
  @media (max-width: 760px) { .settings-summary-grid { grid-template-columns: 1fr; } }
  @media (max-width: 1180px) { .settings-pane { padding: 24px; } }
  @media (max-width: 960px) { .settings-pane { padding: 18px 14px 24px; } }
  @media (max-width: 900px) { .settings-pane { padding: 16px 12px 20px; } }
</style>
