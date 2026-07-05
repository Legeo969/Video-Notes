<script lang="ts">
  import { invoke } from "../lib/api";
  import type { JobInfo } from "../lib/types";

  let url = $state("");
  let title = $state("");
  let whisperModel = $state("large-v3");
  let ocrEnabled = $state(false);
  let visionEnabled = $state(false);
  let isProcessing = $state(false);
  let progress = $state(0);
  let stageMessage = $state("");

  async function startProcess() {
    isProcessing = true;
    progress = 0;
    try {
      const result = await invoke("process.start", {
        input: url,
        title: title || undefined,
        whisper_model: whisperModel,
        ocr_enabled: ocrEnabled,
        vision_enabled: visionEnabled,
      });
      console.log("Process started:", result);
    } catch (e) {
      console.error("Process failed:", e);
    } finally {
      isProcessing = false;
    }
  }

  function handleFileSelect() {
    // Tauri dialog plugin
    alert("文件选择器 - Tauri dialog 集成后可用");
  }
</script>

<div class="page">
  <h2 class="page-title">视频处理</h2>

  <div class="form-card">
    <div class="form-group">
      <label for="input">视频链接或文件路径</label>
      <div class="input-row">
        <input id="input" type="text" bind:value={url} placeholder="YouTube / Bilibili 链接，或本地文件路径" />
        <button class="btn btn-secondary" onclick={handleFileSelect}>选择文件</button>
      </div>
    </div>

    <div class="form-group">
      <label for="title">标题（可选）</label>
      <input id="title" type="text" bind:value={title} placeholder="自动从视频元数据提取" />
    </div>

    <div class="form-row">
      <div class="form-group">
        <label for="model">Whisper 模型</label>
        <select id="model" bind:value={whisperModel}>
          <option value="tiny">tiny</option>
          <option value="base">base</option>
          <option value="small">small</option>
          <option value="medium">medium</option>
          <option value="large-v3">large-v3</option>
        </select>
      </div>

      <div class="form-group checkbox-group">
        <label>
          <input type="checkbox" bind:checked={ocrEnabled} />
          OCR 文字识别
        </label>
        <label>
          <input type="checkbox" bind:checked={visionEnabled} />
          视觉理解
        </label>
      </div>
    </div>

    <button class="btn btn-primary" onclick={startProcess} disabled={!url || isProcessing}>
      {isProcessing ? "处理中..." : "开始处理"}
    </button>
  </div>

  {#if isProcessing}
    <div class="progress-section">
      <div class="progress-bar">
        <div class="progress-fill" style="width: {progress * 100}%"></div>
      </div>
      <p class="stage-message">{stageMessage}</p>
    </div>
  {/if}
</div>

<style>
  .form-card {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 24px;
    display: flex;
    flex-direction: column;
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

  .input-row {
    display: flex;
    gap: 8px;
  }

  .input-row input { flex: 1; }

  input, select {
    padding: 8px 12px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background: var(--bg-input);
    color: var(--text-primary);
    font-size: 14px;
  }

  .form-row {
    display: flex;
    gap: 16px;
    align-items: flex-end;
  }

  .checkbox-group {
    display: flex;
    flex-direction: row;
    gap: 16px;
    align-items: center;
  }

  .checkbox-group label {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
  }

  .progress-section {
    margin-top: 20px;
  }

  .progress-bar {
    height: 8px;
    background: var(--bg-progress);
    border-radius: 4px;
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent-color);
    transition: width 0.3s;
  }

  .stage-message {
    margin-top: 8px;
    font-size: 13px;
    color: var(--text-secondary);
  }
</style>