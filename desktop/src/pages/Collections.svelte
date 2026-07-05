<script lang="ts">
  import { engineCall } from "../lib/api";
  import type { CollectionInfo } from "../lib/types";

  // ── Types ─────────────────────────────────────────────

  interface CollectionItem {
    id: number;
    input: string;
    status: string;
    title?: string;
    progress?: number;
  }

  interface CollectionDetail {
    id: number;
    name: string;
    status: string;
    item_count: number;
    items: CollectionItem[];
  }

  // ── State ─────────────────────────────────────────────

  let collections = $state<CollectionInfo[]>([]);
  let selectedId = $state<number | null>(null);
  let detail = $state<CollectionDetail | null>(null);
  let loading = $state(false);
  let detailLoading = $state(false);
  let error = $state<string | null>(null);

  // Create dialog
  let showCreateDialog = $state(false);
  let createName = $state("");
  let createItems = $state("");
  let creating = $state(false);

  // Batch processing
  let processing = $state(false);
  let batchJobId = $state<string | null>(null);
  let batchProgress = $state<string>("");

  // Export
  let exportPath = $state<string | null>(null);

  // Confirm delete
  let confirmDeleteId = $state<number | null>(null);

  // Add items to existing collection
  let showAddItems = $state(false);
  let addItemsText = $state("");

  // ── Load collections ──────────────────────────────────

  async function loadCollections() {
    loading = true;
    error = null;
    try {
      collections = await engineCall<CollectionInfo[]>("collection.list");
    } catch (e) {
      error = `加载合集列表失败: ${e}`;
      console.error(e);
    } finally {
      loading = false;
    }
  }

  // ── Select collection ─────────────────────────────────

  async function selectCollection(id: number) {
    if (selectedId === id) return;
    selectedId = id;
    detailLoading = true;
    detail = null;
    exportPath = null;
    batchJobId = null;
    batchProgress = "";
    try {
      detail = await engineCall<CollectionDetail>("collection.get", { id });
    } catch (e) {
      error = `加载合集详情失败: ${e}`;
      console.error(e);
    } finally {
      detailLoading = false;
    }
  }

  // ── Create collection ─────────────────────────────────

  async function createCollection() {
    if (!createName.trim() || !createItems.trim()) return;
    creating = true;
    try {
      const items = createItems
        .split("\n")
        .map(l => l.trim())
        .filter(l => l.length > 0);
      await engineCall("collection.create", { name: createName.trim(), items });
      showCreateDialog = false;
      createName = "";
      createItems = "";
      await loadCollections();
    } catch (e) {
      error = `创建合集失败: ${e}`;
      console.error(e);
    } finally {
      creating = false;
    }
  }

  // ── Delete collection ─────────────────────────────────

  async function deleteCollection(id: number) {
    try {
      await engineCall("collection.delete", { id });
      if (selectedId === id) {
        selectedId = null;
        detail = null;
      }
      confirmDeleteId = null;
      await loadCollections();
    } catch (e) {
      error = `删除合集失败: ${e}`;
      console.error(e);
    }
  }

  // ── Add items to existing collection ──────────────────

  async function addItems() {
    if (!selectedId || !addItemsText.trim()) return;
    try {
      const items = addItemsText
        .split("\n")
        .map(l => l.trim())
        .filter(l => l.length > 0);
      await engineCall("collection.add_items", { id: selectedId, items });
      addItemsText = "";
      showAddItems = false;
      await selectCollection(selectedId);
    } catch (e) {
      error = `添加条目失败: ${e}`;
      console.error(e);
    }
  }

  // ── Remove item ───────────────────────────────────────

  async function removeItem(itemId: number) {
    if (!selectedId) return;
    try {
      await engineCall("collection.remove_items", {
        id: selectedId,
        item_ids: [itemId],
      });
      await selectCollection(selectedId);
    } catch (e) {
      error = `移除条目失败: ${e}`;
      console.error(e);
    }
  }

  // ── Batch process ─────────────────────────────────────

  async function batchProcess() {
    if (!selectedId) return;
    processing = true;
    batchJobId = null;
    batchProgress = "正在提交批量处理...";
    try {
      const result = await engineCall<{ batch_job_id: string }>(
        "collection.batch_process",
        { id: selectedId, opts: {} }
      );
      batchJobId = result.batch_job_id;
      batchProgress = "批量处理已提交，请在任务页面查看进度";
      await selectCollection(selectedId);
    } catch (e) {
      batchProgress = `批量处理失败: ${e}`;
      console.error(e);
    } finally {
      processing = false;
    }
  }

  // ── Export ────────────────────────────────────────────

  async function exportCollection() {
    if (!selectedId) return;
    try {
      const result = await engineCall<{ path: string }>("collection.export", {
        id: selectedId,
      });
      exportPath = result.path;
    } catch (e) {
      error = `导出失败: ${e}`;
      console.error(e);
    }
  }

  // ── Import folder ─────────────────────────────────────

  async function importFolder() {
    // Placeholder — Tauri dialog integration needed for real folder picker
    const path = prompt("输入文件夹路径：");
    if (!path || !path.trim()) return;
    try {
      await engineCall("collection.import_folder", { path: path.trim() });
      await loadCollections();
    } catch (e) {
      error = `导入文件夹失败: ${e}`;
      console.error(e);
    }
  }

  // ── Helpers ───────────────────────────────────────────

  function statusLabel(status: string): string {
    const map: Record<string, string> = {
      completed: "已完成",
      processing: "处理中",
      pending: "等待中",
      failed: "失败",
      paused: "已暂停",
      cancelled: "已取消",
    };
    return map[status] || status;
  }

  function statusClass(status: string): string {
    return "status-" + status;
  }

  function itemStatusLabel(status: string): string {
    return statusLabel(status);
  }

  // ── Init ──────────────────────────────────────────────

  $effect(() => { loadCollections(); });
</script>

<div class="page collections-page">
  <!-- ── Top bar ─────────────────────────────────────── -->
  <div class="top-bar">
    <h2 class="page-title">合集</h2>
    <div class="top-actions">
      <button class="btn btn-secondary" onclick={() => importFolder()}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
        导入文件夹
      </button>
      <button class="btn btn-primary" onclick={() => { showCreateDialog = true; }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        新建合集
      </button>
    </div>
  </div>

  <!-- ── Error banner ─────────────────────────────────── -->
  {#if error}
    <div class="error-banner">
      <span>{error}</span>
      <button class="error-close" onclick={() => error = null}>&times;</button>
    </div>
  {/if}

  <div class="layout-split">
    <!-- ── Left panel: Collection list ───────────────── -->
    <div class="list-panel">
      {#if loading}
        <div class="empty-state">加载中...</div>
      {:else if collections.length === 0}
        <div class="empty-state">
          <p>暂无合集</p>
          <p class="empty-hint">点击上方"新建合集"或"导入文件夹"开始</p>
        </div>
      {:else}
        <div class="collection-list">
          {#each collections as col (col.id)}
            <button
              class="collection-card"
              class:selected={selectedId === col.id}
              onclick={() => selectCollection(col.id)}
            >
              <div class="card-main">
                <span class="card-name">{col.name}</span>
                <span class={`status-badge ${statusClass(col.status)}`}>
                  {statusLabel(col.status)}
                </span>
              </div>
              <span class="card-count">{col.item_count} 条目</span>
            </button>
          {/each}
        </div>
      {/if}
    </div>

    <!-- ── Right panel: Detail view ─────────────────── -->
    <div class="detail-panel">
      {#if !selectedId}
        <div class="empty-state detail-empty">
          <p>选择一个合集查看详情</p>
        </div>
      {:else if detailLoading}
        <div class="empty-state">加载中...</div>
      {:else if detail}
        {@const d = detail}
        <div class="detail-header">
          <div class="detail-title-row">
            <h3>{d.name}</h3>
            <span class={`status-badge ${statusClass(d.status)}`}>
              {statusLabel(d.status)}
            </span>
          </div>
          <div class="detail-actions">
            <button
              class="btn btn-primary"
              disabled={processing || d.items.length === 0}
              onclick={batchProcess}
            >
              {#if processing}
                提交中...
              {:else}
                批量处理
              {/if}
            </button>
            <button class="btn btn-secondary" onclick={() => { showAddItems = true; }}>
              添加条目
            </button>
            <button class="btn btn-secondary" onclick={exportCollection}>
              导出
            </button>
            <button class="btn btn-danger btn-sm" onclick={() => { confirmDeleteId = d.id; }}>
              删除合集
            </button>
          </div>
        </div>

        {#if batchJobId}
          <div class="batch-info">
            <span>批量任务已提交：{batchJobId}</span>
            <span class="batch-hint">{batchProgress}</span>
          </div>
        {/if}

        {#if exportPath}
          <div class="export-info">
            <span>已导出至：{exportPath}</span>
          </div>
        {/if}

        <!-- Items table -->
        <div class="items-section">
          <h4>条目 ({d.items.length})</h4>
          {#if d.items.length === 0}
            <div class="empty-state">
              <p>合集为空</p>
              <p class="empty-hint">点击"添加条目"添加视频链接或文件路径</p>
            </div>
          {:else}
            <table class="item-table">
              <thead>
                <tr>
                  <th class="col-input">输入</th>
                  <th class="col-status">状态</th>
                  <th class="col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {#each d.items as item (item.id)}
                  <tr>
                    <td class="col-input" title={item.input}>
                      <span class="item-input">{item.input}</span>
                      {#if item.title}
                        <span class="item-title">{item.title}</span>
                      {/if}
                    </td>
                    <td class="col-status">
                      <span class={`status-badge ${statusClass(item.status)}`}>
                        {itemStatusLabel(item.status)}
                      </span>
                    </td>
                    <td class="col-actions">
                      <button
                        class="btn-sm btn-danger"
                        onclick={() => removeItem(item.id)}
                      >
                        移除
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {/if}
    </div>
  </div>
</div>

<!-- ── Create Dialog ─────────────────────────── -->
{#if showCreateDialog}
  <div class="modal-overlay" role="button" tabindex="0"
    onclick={() => { if (!creating) showCreateDialog = false; }}
    onkeydown={(e) => {
      if (e.key === 'Escape' && !creating) showCreateDialog = false;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!creating) showCreateDialog = false; }
    }}>
    <div class="modal" role="dialog" tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}>
      <h3>新建合集</h3>
      <div class="modal-body">
        <div class="form-group">
          <label for="create-name">合集名称</label>
          <input
            id="create-name"
            type="text"
            bind:value={createName}
            placeholder="例如：深度学习入门"
          />
        </div>
        <div class="form-group">
          <label for="create-items">视频链接或文件路径（一行一个）</label>
          <textarea
            id="create-items"
            bind:value={createItems}
            placeholder="https://youtube.com/watch?v=...&#10;https://bilibili.com/video/...&#10;C:\videos\lecture.mp4"
            rows="8"
          ></textarea>
        </div>
      </div>
      <div class="modal-footer">
        <button
          class="btn btn-secondary"
          disabled={creating}
          onclick={() => { showCreateDialog = false; }}
        >
          取消
        </button>
        <button
          class="btn btn-primary"
          disabled={!createName.trim() || !createItems.trim() || creating}
          onclick={createCollection}
        >
          {creating ? "创建中..." : "创建"}
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- ── Add Items Dialog ──────────────────────── -->
{#if showAddItems}
  <div class="modal-overlay" role="button" tabindex="0"
    onclick={() => { showAddItems = false; }}
    onkeydown={(e) => {
      if (e.key === 'Escape') showAddItems = false;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showAddItems = false; }
    }}>
    <div class="modal" role="dialog" tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}>
      <h3>添加条目</h3>
      <p class="modal-hint">添加到：{detail?.name}</p>
      <div class="modal-body">
        <div class="form-group">
          <label for="add-items">视频链接或文件路径（一行一个）</label>
          <textarea
            id="add-items"
            bind:value={addItemsText}
            placeholder="https://youtube.com/watch?v=...&#10;C:\videos\lecture.mp4"
            rows="8"
          ></textarea>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick={() => { showAddItems = false; }}>
          取消
        </button>
        <button
          class="btn btn-primary"
          disabled={!addItemsText.trim()}
          onclick={addItems}
        >
          添加
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- ── Confirm Delete Dialog ─────────────────── -->
{#if confirmDeleteId !== null}
  <div class="modal-overlay" role="button" tabindex="0"
    onclick={() => { confirmDeleteId = null; }}
    onkeydown={(e) => {
      if (e.key === 'Escape') confirmDeleteId = null;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); confirmDeleteId = null; }
    }}>
    <div class="modal modal-sm" role="dialog" tabindex="-1"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}>
      <h3>确认删除</h3>
      <p class="modal-hint">确定要删除此合集吗？此操作不可撤销。</p>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick={() => { confirmDeleteId = null; }}>
          取消
        </button>
        <button
          class="btn btn-danger"
          onclick={() => deleteCollection(confirmDeleteId!)}
        >
          删除
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  /* ── Layout ──────────────────────────────────────── */
  .collections-page {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    flex-shrink: 0;
  }

  .top-actions {
    display: flex;
    gap: 8px;
  }

  .top-actions .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }

  .layout-split {
    display: flex;
    gap: 16px;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }

  /* ── Error banner ────────────────────────────────── */
  .error-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #f8d7da;
    color: #721c24;
    padding: 8px 14px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 12px;
  }

  .error-close {
    background: none;
    border: none;
    color: #721c24;
    font-size: 18px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }

  /* ── Left panel: list ────────────────────────────── */
  .list-panel {
    width: 280px;
    flex-shrink: 0;
    overflow-y: auto;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background: var(--bg-card);
  }

  .collection-list {
    display: flex;
    flex-direction: column;
  }

  .collection-card {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 12px 14px;
    border: none;
    border-bottom: 1px solid var(--border-color);
    background: transparent;
    cursor: pointer;
    text-align: left;
    width: 100%;
    color: var(--text-primary);
    transition: background 0.1s;
    font-size: 14px;
  }

  .collection-card:last-child {
    border-bottom: none;
  }

  .collection-card:hover {
    background: var(--bg-hover);
  }

  .collection-card.selected {
    background: var(--accent-bg);
    border-left: 3px solid var(--accent-color);
    padding-left: 11px;
  }

  .card-main {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .card-name {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .card-count {
    font-size: 12px;
    color: var(--text-secondary);
  }

  /* ── Right panel: detail ─────────────────────────── */
  .detail-panel {
    flex: 1;
    overflow-y: auto;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background: var(--bg-card);
    padding: 20px;
  }

  .detail-empty {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .detail-header {
    margin-bottom: 16px;
  }

  .detail-title-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
  }

  .detail-title-row h3 {
    font-size: 18px;
    font-weight: 600;
  }

  .detail-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .detail-actions .btn-danger.btn-sm {
    margin-left: auto;
  }

  .batch-info,
  .export-info {
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .batch-info {
    background: #cce5ff;
    color: #004085;
  }

  .batch-hint {
    font-size: 12px;
    opacity: 0.8;
  }

  .export-info {
    background: #d4edda;
    color: #155724;
  }

  /* ── Items table ─────────────────────────────────── */
  .items-section {
    margin-top: 4px;
  }

  .items-section h4 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 10px;
    color: var(--text-secondary);
  }

  .item-table {
    width: 100%;
    border-collapse: collapse;
  }

  .item-table th {
    text-align: left;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    border-bottom: 2px solid var(--border-color);
  }

  .item-table td {
    padding: 8px 10px;
    border-bottom: 1px solid var(--border-color);
    font-size: 13px;
    vertical-align: middle;
  }

  .col-input { width: auto; }
  .col-status { width: 100px; }
  .col-actions { width: 70px; text-align: right; }

  .item-input {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 400px;
  }

  .item-title {
    display: block;
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 2px;
  }

  .item-table td.col-actions {
    text-align: right;
  }

  /* ── Status badges ───────────────────────────────── */
  .status-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
  }

  .status-completed { background: #d4edda; color: #155724; }
  .status-processing { background: #cce5ff; color: #004085; }
  .status-pending { background: #e2e3e5; color: #383d41; }
  .status-failed { background: #f8d7da; color: #721c24; }
  .status-paused { background: #fff3cd; color: #856404; }
  .status-cancelled { background: #e2e3e5; color: #383d41; }

  /* ── Empty state ─────────────────────────────────── */
  .empty-state {
    padding: 32px 16px;
    text-align: center;
    color: var(--text-secondary);
    font-size: 14px;
  }

  .empty-hint {
    font-size: 12px;
    margin-top: 6px;
    opacity: 0.7;
  }

  /* ── Modal ────────────────────────────────────────── */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }

  .modal {
    background: var(--bg-card);
    border-radius: 10px;
    padding: 24px;
    width: 520px;
    max-width: 90vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
  }

  .modal-sm {
    width: 380px;
  }

  .modal h3 {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 8px;
  }

  .modal-hint {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 12px;
  }

  .modal-body {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid var(--border-color);
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

  .form-group input,
  .form-group textarea {
    padding: 8px 12px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background: var(--bg-input);
    color: var(--text-primary);
    font-size: 14px;
    font-family: inherit;
    resize: vertical;
  }

  .form-group input:focus,
  .form-group textarea:focus {
    outline: none;
    border-color: var(--accent-color);
    box-shadow: 0 0 0 2px var(--accent-bg);
  }

  .form-group textarea {
    min-height: 120px;
  }
</style>
