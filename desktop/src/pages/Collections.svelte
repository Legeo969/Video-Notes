<script lang="ts">
  import { onMount } from "svelte";
  import { open } from "@tauri-apps/plugin-dialog";
  import { engineCall, runningInTauri } from "../lib/api";
  import type { CollectionInfo } from "../lib/types";
  import Icon from "../lib/components/Icon.svelte";
  import { requestNavigate } from "../lib/stores/jobs";
  import PageHeader from "../lib/components/PageHeader.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import StatusPill from "../lib/components/StatusPill.svelte";

  interface CollectionItem {
    id: number;
    input: string;
    status: string;
    title?: string;
    progress?: number;
    run_id?: number;
  }

  interface CollectionDetail {
    id: number;
    name: string;
    status: string;
    item_count: number;
    items: CollectionItem[];
  }

  let collections = $state<CollectionInfo[]>([]);
  let selectedId = $state<number | null>(null);
  let detail = $state<CollectionDetail | null>(null);
  let loading = $state(false);
  let detailLoading = $state(false);
  let error = $state<string | null>(null);
  let searchQuery = $state("");

  let showCreateDialog = $state(false);
  let createName = $state("");
  let createItems = $state("");
  let creating = $state(false);

  let processingScope = $state<"pending" | "failed" | null>(null);
  let batchJobId = $state<string | null>(null);
  let batchProgress = $state<string>("");
  let batchOutputDir = $state<string | null>(null);
  let exportPath = $state<string | null>(null);
  let confirmDeleteId = $state<number | null>(null);
  let showAddItems = $state(false);
  let addItemsText = $state("");
  let retryingItemId = $state<number | null>(null);
  let detailRequestVersion = 0;
  let collectionPollInFlight = false;

  let filteredCollections = $derived.by(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return collections;
    return collections.filter((collection) => collection.name.toLowerCase().includes(q));
  });

  let totalItems = $derived(collections.reduce((sum, collection) => sum + (collection.item_count || 0), 0));
  let completedItems = $derived(detail?.items.filter((item) => item.status === "completed").length || 0);
  let failedItems = $derived(detail?.items.filter((item) => ["failed", "cancelled", "interrupted"].includes(item.status)).length || 0);
  let pausedItems = $derived(detail?.items.filter((item) => item.status === "paused").length || 0);
  let pendingItems = $derived(detail?.items.filter((item) => item.status === "pending" && item.run_id == null).length || 0);
  let runningItems = $derived(detail?.items.filter((item) => ["running", "pausing", "cancelling"].includes(item.status) || (item.status === "pending" && item.run_id != null)).length || 0);
  let workProgress = $derived.by(() => {
    const items = detail?.items ?? [];
    if (items.length === 0) return 0;
    const total = items.reduce((sum, item) => sum + itemWorkProgress(item), 0);
    return Math.round(total / items.length);
  });

  async function loadCollections(silent = false) {
    if (!silent) {
      loading = true;
      error = null;
    }
    try {
      collections = await engineCall<CollectionInfo[]>("collection.list");
      if (selectedId && !collections.some((item) => item.id === selectedId)) {
        selectedId = null; detail = null;
      }
    } catch (e) {
      error = `加载合集列表失败：${e}`;
      console.error(e);
    } finally { if (!silent) loading = false; }
  }

  async function selectCollection(id: number, force = false) {
    if (selectedId === id && !force) return;
    const requestVersion = ++detailRequestVersion;
    selectedId = id; detailLoading = !force; detail = force ? detail : null; exportPath = force ? exportPath : null; batchJobId = force ? batchJobId : null; batchProgress = force ? batchProgress : ""; batchOutputDir = force ? batchOutputDir : null;
    try {
      const nextDetail = await engineCall<CollectionDetail>("collection.get", { id });
      if (requestVersion === detailRequestVersion && selectedId === id) detail = nextDetail;
    }
    catch (e) {
      if (requestVersion === detailRequestVersion) error = `加载合集详情失败：${e}`;
      console.error(e);
    }
    finally { if (requestVersion === detailRequestVersion) detailLoading = false; }
  }

  async function createCollection() {
    if (!createName.trim() || !createItems.trim()) return;
    creating = true;
    try {
      const items = createItems.split("\n").map((line) => line.trim()).filter(Boolean);
      const result = await engineCall<{ id?: number }>("collection.create", { name: createName.trim(), items });
      showCreateDialog = false; createName = ""; createItems = ""; await loadCollections();
      if (result?.id) await selectCollection(result.id);
    } catch (e) { error = `创建合集失败：${e}`; }
    finally { creating = false; }
  }

  async function deleteCollection(id: number) {
    try {
      await engineCall("collection.delete", { id });
      if (selectedId === id) { detailRequestVersion += 1; selectedId = null; detail = null; }
      confirmDeleteId = null; await loadCollections();
    } catch (e) { error = `删除合集失败：${e}`; }
  }

  async function addItems() {
    if (!selectedId || !addItemsText.trim()) return;
    try {
      const items = addItemsText.split("\n").map((line) => line.trim()).filter(Boolean);
      await engineCall("collection.add_items", { id: selectedId, items });
      addItemsText = ""; showAddItems = false; await loadCollections(); await selectCollection(selectedId, true);
    } catch (e) { error = `添加条目失败：${e}`; }
  }

  async function removeItem(itemId: number) {
    if (!selectedId) return;
    try {
      await engineCall("collection.remove_items", { id: selectedId, item_ids: [itemId] });
      await loadCollections(); await selectCollection(selectedId, true);
    } catch (e) { error = `移除条目失败：${e}`; }
  }

let hasActionableItems = $derived(detail?.items.some((item) => {
    const s = item.status;
    return item.run_id != null && ["pending", "running", "pausing", "cancelling", "paused"].includes(s);
}) ?? false);

  let hasPausedItems = $derived(detail?.items.some((item) => item.status === "paused") ?? false);
  let hasFailedItems = $derived(detail?.items.some((item) => ["failed", "cancelled", "interrupted"].includes(item.status)) ?? false);

  async function retryItem(itemId: number) {
    if (!selectedId) return;
    const item = detail?.items.find((candidate) => candidate.id === itemId);
    if (!item?.run_id) {
      error = "该条目没有可重试的原任务记录。";
      return;
    }
    retryingItemId = itemId;
    try {
      await engineCall("process.retry", { job_id: item.run_id });
      await loadCollections(); await selectCollection(selectedId, true);
    } catch (e) { error = `重试失败：${e}`; }
    finally { retryingItemId = null; }
  }

  async function batchProcess(scope: "pending" | "failed") {
    if (!selectedId) return;
    const count = scope === "pending" ? pendingItems : failedItems;
    if (count === 0) return;
    const prompt = scope === "pending"
      ? `确定继续处理 ${count} 个未开始条目吗？已完成和失败条目都会跳过。`
      : `确定重试 ${count} 个失败、取消或中断条目吗？已完成条目不会重新处理。`;
    if (!window.confirm(prompt)) return;
    processingScope = scope; batchJobId = null; batchOutputDir = null;
    batchProgress = scope === "pending" ? "正在提交未处理条目…" : "正在提交失败项重试…";
    try {
      const result = await engineCall<{ batch_job_id: string; count?: number; output_dir?: string }>("collection.batch_process", { id: selectedId, scope, opts: { max_concurrency: 1, compile_mode: "precision" } });
      await loadCollections(); await selectCollection(selectedId, true);
      batchJobId = result.batch_job_id; batchOutputDir = result.output_dir || null;
      const submitted = result.count ?? 0;
      if (submitted > 0) {
        batchProgress = scope === "pending"
          ? `已提交 ${submitted} 个未处理条目；已完成和失败条目已跳过。`
          : `已提交 ${submitted} 个失败项重试；已完成条目已跳过。`;
      } else {
        batchProgress = scope === "pending" ? "没有未处理条目。" : "没有可重试的失败项。";
      }
    } catch (e) { batchProgress = `${scope === "pending" ? "继续处理" : "重试失败项"}失败：${e}`; }
    finally { processingScope = null; }
  }

  async function exportCollection() {
    if (!selectedId) return;
    try {
      const result = await engineCall<{ path: string }>("collection.export", { id: selectedId });
      exportPath = result.path;
    } catch (e) { error = `导出失败：${e}`; }
  }

  let stoppingAll = $state(false);

  async function pauseAll() {
    if (!selectedId) return;
    stoppingAll = true;
    try {
      const result = await engineCall<{ paused: number }>("collection.pause_all", { id: selectedId });
      await loadCollections(); await selectCollection(selectedId, true);
      error = `已暂停 ${result.paused} 个任务`;
    } catch (e) { error = `暂停失败：${e}`; }
    finally { stoppingAll = false; }
  }

  async function cancelAll() {
    if (!selectedId) return;
    stoppingAll = true;
    try {
      const result = await engineCall<{ cancelled: number }>("collection.cancel_all", { id: selectedId });
      await loadCollections(); await selectCollection(selectedId, true);
      error = `已取消 ${result.cancelled} 个任务`;
    } catch (e) { error = `取消失败：${e}`; }
    finally { stoppingAll = false; }
  }

  async function resumeAll() {
    if (!selectedId) return;
    stoppingAll = true;
    try {
      const result = await engineCall<{ resumed: number }>("collection.resume_all", { id: selectedId });
      await loadCollections(); await selectCollection(selectedId, true);
      error = `已恢复 ${result.resumed} 个任务`;
    } catch (e) { error = `恢复失败：${e}`; }
    finally { stoppingAll = false; }
  }

  async function importFolder() {
    let path: string | null = null;
    if (runningInTauri()) {
      const selected = await open({ multiple: false, directory: true });
      if (typeof selected === "string") path = selected;
    } else {
      path = prompt("输入文件夹路径：");
    }
    if (!path?.trim()) return;
    try { await engineCall("collection.import_folder", { path: path.trim() }); await loadCollections(); }
    catch (e) { error = `导入文件夹失败：${e}`; }
  }

  function statusLabel(status: string): string {
    const map: Record<string, string> = { active: "可用", completed: "已完成", processing: "处理中", pending: "等待中", running: "运行中", pausing: "等待暂停", cancelling: "正在取消", failed: "失败", paused: "已暂停", cancelled: "已取消", interrupted: "已中断" };
    return map[status] || status;
  }

  function itemWorkProgress(item: CollectionItem): number {
    if (["completed", "failed", "cancelled", "interrupted"].includes(item.status)) return 100;
    const progress = Number(item.progress ?? 0);
    return Math.max(0, Math.min(100, Number.isFinite(progress) ? progress : 0));
  }

  function collectionNeedsRefresh(collection: CollectionDetail | null): boolean {
    return Boolean(collection?.items.some((item) => item.run_id != null && ["pending", "running", "pausing", "cancelling", "paused"].includes(item.status)));
  }

  async function refreshActiveCollection() {
    const id = selectedId;
    if (collectionPollInFlight || id === null || !collectionNeedsRefresh(detail)) return;
    collectionPollInFlight = true;
    try {
      await loadCollections(true);
      if (selectedId === id) await selectCollection(id, true);
    } finally {
      collectionPollInFlight = false;
    }
  }

  function fileName(path: string) { return path.split(/[\\/]/).pop() || path; }
  function sourceIcon(path: string) { return path.startsWith("http") ? "link" : "video"; }

  onMount(() => {
    const timer = window.setInterval(() => {
      void refreshActiveCollection();
    }, 1500);
    return () => window.clearInterval(timer);
  });

  $effect(() => { loadCollections(); });
</script>

<div class="page collections-page">
  <PageHeader
    eyebrow="知识组织空间"
    title="合集"
    description="将同一课程、项目或主题下的视频统一组织，批量处理并导出完整知识库。"
    icon="folder"
  >
    {#snippet actions()}
      <button class="btn btn-secondary" onclick={importFolder}><Icon name="folder-open" size={15} />导入文件夹</button>
      <button class="btn btn-primary" onclick={() => showCreateDialog = true}><Icon name="plus" size={15} />新建合集</button>
    {/snippet}
  </PageHeader>

  <section class="collection-metrics">
    <div class="metric surface"><span class="metric-icon collections"><Icon name="folder" size={19} /></span><div><strong>{collections.length}</strong><small>合集总数</small></div></div>
    <div class="metric surface"><span class="metric-icon items"><Icon name="video" size={19} /></span><div><strong>{totalItems}</strong><small>媒体条目</small></div></div>
    <div class="metric surface"><span class="metric-icon batch"><Icon name="activity" size={19} /></span><div><strong>{collections.filter((item) => item.status === "processing").length}</strong><small>批量处理中</small></div></div>
  </section>

  {#if error}
    <div class="alert alert-error collection-alert"><Icon name="alert" size={17} /><span>{error}</span><button onclick={() => error = null} aria-label="关闭错误"><Icon name="x" size={13} /></button></div>
  {/if}

  <div class="collection-workspace surface">
    <aside class="collection-sidebar">
      <div class="collection-search input-wrap has-icon">
        <span class="input-icon"><Icon name="search" size={15} /></span>
        <input type="search" bind:value={searchQuery} placeholder="搜索合集" aria-label="搜索合集" />
        {#if searchQuery}<button class="search-clear" onclick={() => searchQuery = ""} aria-label="清空搜索"><Icon name="x" size={13} /></button>{/if}
      </div>

      <div class="sidebar-label"><span>我的合集</span><em>{filteredCollections.length}</em></div>
      <div class="collection-list">
        {#if loading && collections.length === 0}
          <div class="side-loading"><span class="loading-ring"></span><p>正在加载合集</p></div>
        {:else if filteredCollections.length === 0}
          <EmptyState icon={searchQuery ? "search" : "folder"} title={searchQuery ? "没有匹配合集" : "暂无合集"} description={searchQuery ? "尝试其他关键词。" : "创建合集或导入文件夹开始组织内容。"} compact />
        {:else}
          {#each filteredCollections as collection (collection.id)}
            <button class="collection-item" class:selected={selectedId === collection.id} onclick={() => selectCollection(collection.id)}>
              <span class="collection-folder"><Icon name="folder" size={17} /></span>
              <span class="collection-copy"><strong>{collection.name}</strong><small>{collection.item_count} 个条目</small></span>
              <span class="collection-status"><StatusPill status={collection.status} label={statusLabel(collection.status)} /></span>
            </button>
          {/each}
        {/if}
      </div>
      <button class="new-collection-inline" onclick={() => showCreateDialog = true}><Icon name="plus" size={14} />创建新合集</button>
    </aside>

    <main class="collection-detail">
      {#if detailLoading}
        <div class="detail-loading"><span class="loading-ring large"></span><h2>正在加载合集</h2><p>读取条目与处理状态…</p></div>
      {:else if detail}
        <header class="detail-header">
          <div class="detail-title-wrap">
            <div class="detail-folder"><Icon name="folder-open" size={22} /></div>
            <div><span>COLLECTION #{detail.id}</span><h2>{detail.name}</h2><p>{detail.item_count} 个媒体条目 · 成功 {completedItems} · 待处理 {pendingItems} · 失败/取消 {failedItems} · 暂停 {pausedItems}</p></div>
          </div>
          <div class="detail-actions">
            <button class="btn btn-secondary btn-sm" onclick={() => showAddItems = true}><Icon name="plus" size={14} />添加条目</button>
            <button class="btn btn-secondary btn-sm" onclick={exportCollection}><Icon name="download" size={14} />导出</button>
            <span class="batch-mode-label"><Icon name="cloud" size={13} />云端精确编译</span>
            <button class="btn btn-primary btn-sm" onclick={() => batchProcess("pending")} disabled={processingScope !== null || stoppingAll || pendingItems === 0} title="只处理从未开始的条目，跳过已完成和失败项">
              <Icon name="play" size={14} />{processingScope === "pending" ? "提交中" : `继续处理 ${pendingItems}`}</button>
            {#if hasFailedItems}
              <button class="btn btn-secondary btn-sm retry-batch" onclick={() => batchProcess("failed")} disabled={processingScope !== null || stoppingAll} title="只重试失败、取消或中断的条目">
                <Icon name="rotate" size={14} />{processingScope === "failed" ? "提交中" : `重试失败项 ${failedItems}`}</button>
            {/if}
            {#if hasPausedItems}
              <button class="btn btn-secondary btn-sm" onclick={resumeAll} disabled={stoppingAll}><Icon name="play" size={14} />恢复全部</button>
            {/if}
            {#if hasActionableItems}
              <button class="btn btn-secondary btn-sm" onclick={pauseAll} disabled={stoppingAll}><Icon name="pause" size={14} />暂停全部</button>
              <button class="btn btn-danger btn-sm" onclick={cancelAll} disabled={stoppingAll}><Icon name="x" size={14} />取消全部</button>
            {/if}
            <button class="icon-btn danger-button" onclick={() => confirmDeleteId = detail?.id ?? null} title="删除合集"><Icon name="trash" size={15} /></button>
          </div>
        </header>

        <div class="detail-progress-strip">
          <div><span>处理进度</span><strong>{workProgress}%</strong></div>
          <div class="progress-track"><div class="progress-bar" style={`width:${workProgress}%`}></div></div>
          <div class="progress-breakdown"><span>成功 {completedItems}/{detail.item_count}</span>{#if pendingItems}<span>待处理 {pendingItems}</span>{/if}{#if runningItems}<span>进行中 {runningItems}</span>{/if}{#if pausedItems}<span>暂停 {pausedItems}</span>{/if}{#if failedItems}<span>失败/取消 {failedItems}</span>{/if}</div>
        </div>

        {#if batchProgress}
          <div class="alert {batchJobId ? 'alert-success' : 'alert-info'} detail-message"><Icon name={batchJobId ? "check" : "info"} size={16} /><span>{batchProgress}</span></div>
        {/if}
        {#if batchOutputDir}
          <div class="alert alert-info detail-message"><Icon name="folder" size={16} /><span>合集输出目录：<code>{batchOutputDir}</code></span></div>
        {/if}
        {#if exportPath}
          <div class="alert alert-success detail-message"><Icon name="download" size={16} /><span>已导出到：<code>{exportPath}</code></span></div>
        {/if}

        <div class="items-toolbar">
          <div><h3>媒体条目</h3><span>每个条目都可独立处理</span></div>
          <span class="item-counter">{detail.items.length} 项</span>
        </div>

        <div class="items-list">
          {#if detail.items.length === 0}
            <EmptyState icon="video" title="合集还是空的" description="添加视频链接或本地媒体路径后，即可统一批量处理。">
              {#snippet action()}<button class="btn btn-primary btn-sm" onclick={() => showAddItems = true}><Icon name="plus" size={14} />添加第一个条目</button>{/snippet}
            </EmptyState>
          {:else}
            <div class="items-head"><span>媒体</span><span>状态与进度</span><span>操作</span></div>
            {#each detail.items as item, index (item.id)}
              <article class="media-row">
                <span class="row-index">{String(index + 1).padStart(2, "0")}</span>
                <span class="row-media-icon"><Icon name={sourceIcon(item.input)} size={17} /></span>
                <span class="row-copy"><strong>{item.title || fileName(item.input)}</strong><small>{item.input}</small></span>
                <span class="row-status">
                  <span><StatusPill status={item.status} label={statusLabel(item.status)} /><em>{Math.round(item.progress || 0)}%</em></span>
                  <span class="progress-track"><span class="progress-bar" style={`width:${item.progress || 0}%`}></span></span>
                </span>
                <span class="row-actions">
                  {#if item.run_id}
                    <button class="link-btn" onclick={() => requestNavigate("tasks")} title="在任务中心查看">
                      <Icon name="external" size={13} />任务
                    </button>
                  {/if}
                  {#if ["failed", "cancelled", "interrupted"].includes(item.status)}
                    <button class="link-btn retry-btn" onclick={() => retryItem(item.id)} disabled={retryingItemId === item.id} title="按原任务参数重试此条目">
                      <Icon name="refresh" size={13} />{retryingItemId === item.id ? "提交中" : "按原参数重试"}
                    </button>
                  {/if}
                  <button class="icon-btn remove-item" onclick={() => removeItem(item.id)} title="从合集中移除"><Icon name="trash" size={14} /></button>
                </span>
              </article>
            {/each}
          {/if}
        </div>
      {:else}
        <div class="collection-welcome">
          <div class="welcome-stack">
            <span class="folder-back"><Icon name="folder" size={42} /></span>
            <span class="folder-front"><Icon name="folder-open" size={50} /></span>
            <span class="welcome-spark"><Icon name="sparkles" size={18} /></span>
          </div>
          <h2>选择一个合集查看详情</h2>
          <p>在左侧选择合集，管理其中的媒体条目、批量启动处理或导出完整笔记集合。</p>
          <div class="welcome-actions"><button class="btn btn-primary" onclick={() => showCreateDialog = true}><Icon name="plus" size={15} />新建合集</button><button class="btn btn-secondary" onclick={importFolder}><Icon name="folder-open" size={15} />导入文件夹</button></div>
        </div>
      {/if}
    </main>
  </div>
</div>

{#if showCreateDialog}
  <div class="modal-overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && (showCreateDialog = false)}>
    <div class="modal-shell create-modal" role="dialog" aria-modal="true" aria-labelledby="create-title">
      <header class="modal-header"><div><span class="modal-kicker">NEW COLLECTION</span><h2 id="create-title">创建内容合集</h2><p>输入合集名称，并逐行添加视频链接或本地文件路径。</p></div><button class="icon-btn" onclick={() => showCreateDialog = false}><Icon name="x" size={15} /></button></header>
      <div class="modal-body modal-form">
        <div class="field"><label class="field-label" for="collection-name">合集名称 <small>必填</small></label><input id="collection-name" type="text" bind:value={createName} placeholder="例如：产品设计课程 · 2026 夏季" /></div>
        <div class="field"><label class="field-label" for="collection-items">媒体条目 <small>每行一个链接或路径</small></label><textarea id="collection-items" bind:value={createItems} rows="8" placeholder="https://example.com/video-01&#10;D:\Courses\lesson-02.mp4"></textarea><span class="field-hint">已识别 {createItems.split("\n").filter((line) => line.trim()).length} 个条目</span></div>
        <div class="modal-tip"><Icon name="info" size={16} /><p>创建合集不会立即开始处理。你可以在检查条目后，从合集详情中统一启动批量任务。</p></div>
      </div>
      <footer class="modal-footer"><button class="btn btn-secondary" onclick={() => showCreateDialog = false}>取消</button><button class="btn btn-primary" onclick={createCollection} disabled={creating || !createName.trim() || !createItems.trim()}><Icon name="plus" size={15} />{creating ? "创建中" : "创建合集"}</button></footer>
    </div>
  </div>
{/if}

{#if showAddItems}
  <div class="modal-overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && (showAddItems = false)}>
    <div class="modal-shell add-modal" role="dialog" aria-modal="true" aria-labelledby="add-title">
      <header class="modal-header"><div><span class="modal-kicker">ADD MEDIA</span><h2 id="add-title">添加媒体条目</h2><p>新条目将添加到「{detail?.name}」，不会影响已完成的任务。</p></div><button class="icon-btn" onclick={() => showAddItems = false}><Icon name="x" size={15} /></button></header>
      <div class="modal-body modal-form"><div class="field"><label class="field-label" for="add-items">链接或本地路径 <small>每行一个</small></label><textarea id="add-items" bind:value={addItemsText} rows="9" placeholder="粘贴视频链接或本地媒体路径…"></textarea><span class="field-hint">将添加 {addItemsText.split("\n").filter((line) => line.trim()).length} 个条目</span></div></div>
      <footer class="modal-footer"><button class="btn btn-secondary" onclick={() => showAddItems = false}>取消</button><button class="btn btn-primary" onclick={addItems} disabled={!addItemsText.trim()}><Icon name="plus" size={15} />添加条目</button></footer>
    </div>
  </div>
{/if}

{#if confirmDeleteId !== null}
  <div class="modal-overlay" role="presentation" onclick={(event) => event.target === event.currentTarget && (confirmDeleteId = null)}>
    <div class="modal-shell delete-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-title">
      <div class="delete-content"><div class="delete-icon"><Icon name="trash" size={23} /></div><h2 id="delete-title">删除这个合集？</h2><p>合集记录将被删除，但已生成的笔记文件不会自动删除。此操作无法撤销。</p></div>
      <footer class="modal-footer"><button class="btn btn-secondary" onclick={() => confirmDeleteId = null}>取消</button><button class="btn btn-danger" onclick={() => confirmDeleteId !== null && deleteCollection(confirmDeleteId)}><Icon name="trash" size={15} />确认删除</button></footer>
    </div>
  </div>
{/if}

<style>
  .collections-page { max-width: 1440px; }
  .collection-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
  .metric { display: flex; align-items: center; gap: 11px; padding: 13px 15px; }
  .metric-icon { display: grid; place-items: center; width: 39px; height: 39px; border-radius: 12px; }
  .metric-icon.collections { color: var(--accent-color); background: var(--accent-soft); }
  .metric-icon.items { color: var(--info-color); background: var(--info-soft); }
  .metric-icon.batch { color: var(--success-color); background: var(--success-soft); }
  .metric div { display: flex; flex-direction: column; }
  .metric strong { font-size: 23px; line-height: 1.05; font-variant-numeric: tabular-nums; }
  .metric small { margin-top: 3px; color: var(--text-secondary); font-size: 13px; }
  .collection-alert { margin-bottom: 14px; }
  .collection-alert span { flex: 1; }
  .collection-alert button { display: grid; place-items: center; flex: 0 0 40px; width: 40px; height: 40px; border: 0; border-radius: 8px; color: inherit; background: transparent; cursor: pointer; }

  .collection-workspace { display: grid; grid-template-columns: minmax(240px, 280px) minmax(0,1fr); min-height: 600px; overflow: hidden; }
  .collection-sidebar { display: flex; min-width: 0; flex-direction: column; border-right: 1px solid var(--border-color); background: var(--bg-subtle); }
  .collection-search { margin: 14px 13px 8px; }
  .collection-search input { min-height: 40px; padding-right: 42px; font-size: 14px; }
  .search-clear { position: absolute; right: 1px; display: grid; place-items: center; width: 40px; height: 40px; border: 0; border-radius: 8px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
  .sidebar-label { display: flex; align-items: center; justify-content: space-between; padding: 8px 16px; color: var(--text-tertiary); font-size: 12px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
  .sidebar-label em { font-style: normal; }
  .collection-list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 7px; }
  .collection-item { display: grid; grid-template-columns: 34px minmax(0,1fr) auto; align-items: center; gap: 9px; width: 100%; min-height: 44px; margin-bottom: 3px; padding: 9px; border: 1px solid transparent; border-radius: 11px; color: var(--text-primary); background: transparent; cursor: pointer; text-align: left; transition: background .14s, border-color .14s; }
  .collection-item:hover { background: var(--bg-hover); }
  .collection-item.selected { border-color: color-mix(in srgb, var(--accent-color) 22%, var(--border-color)); background: var(--accent-faint); box-shadow: inset 3px 0 0 var(--accent-color); }
  .collection-folder { display: grid; place-items: center; width: 33px; height: 33px; border-radius: 10px; color: var(--warning-color); background: var(--warning-soft); }
  .selected .collection-folder { color: var(--accent-color); background: var(--accent-soft); }
  .collection-copy { display: flex; min-width: 0; flex-direction: column; }
  .collection-copy strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .collection-copy small { margin-top: 2px; color: var(--text-tertiary); font-size: 12px; }
  .collection-status { min-width: 0; }
  .new-collection-inline { display: flex; align-items: center; justify-content: center; gap: 6px; margin: 10px 12px 13px; min-height: 40px; border: 1px dashed var(--border-strong); border-radius: 9px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 13px; font-weight: 650; }
  .new-collection-inline:hover { color: var(--accent-color); border-color: var(--accent-color); background: var(--accent-faint); }
  .side-loading { min-height: 210px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-tertiary); font-size: 13px; }
  .loading-ring { width: 27px; height: 27px; margin-bottom: 9px; border: 3px solid var(--bg-progress); border-top-color: var(--accent-color); border-radius: 50%; animation: spin .8s linear infinite; }
  .loading-ring.large { width: 38px; height: 38px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .collection-detail { min-width: 0; background: var(--bg-card); }
  .detail-header { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: 14px; padding: 20px 22px; border-bottom: 1px solid var(--border-color); }
  .detail-title-wrap { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .detail-folder { display: grid; place-items: center; width: 45px; height: 45px; flex: 0 0 auto; border-radius: 14px; color: var(--accent-color); background: var(--accent-soft); }
  .detail-title-wrap > div:last-child { display: flex; min-width: 0; flex-direction: column; }
  .detail-title-wrap span { color: var(--accent-color); font-size: 11px; font-weight: 800; letter-spacing: .11em; }
  .detail-title-wrap h2 { margin-top: 2px; overflow: hidden; font-size: 21px; letter-spacing: -.02em; text-overflow: ellipsis; white-space: nowrap; text-wrap: balance; }
  .detail-title-wrap p { margin-top: 3px; color: var(--text-secondary); font-size: 13px; overflow-wrap: anywhere; text-wrap: pretty; }
  .detail-actions { display: flex; align-items: center; justify-content: flex-start; flex-wrap: wrap; gap: 7px; min-width: 0; }
  .detail-actions .btn { min-height: 40px; white-space: nowrap; }
  .detail-actions .icon-btn { width: 40px; height: 40px; flex: 0 0 40px; }
  .batch-mode-label { display: flex; align-items: center; gap: 5px; color: var(--text-secondary); font-size: 12px; font-weight: 640; }
  .retry-batch { color: var(--warning-color); }
  .retry-btn { color: var(--warning-color) !important; }
  .danger-button { color: var(--danger-color); }
  .detail-progress-strip { display: grid; grid-template-columns: 110px 1fr; align-items: center; gap: 8px 14px; padding: 12px 22px; border-bottom: 1px solid var(--border-color); background: var(--bg-subtle); }
  .detail-progress-strip > div:first-child { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
  .detail-progress-strip span { color: var(--text-secondary); font-size: 12px; }
  .detail-progress-strip strong { font-size: 15px; font-variant-numeric: tabular-nums; }
  .detail-progress-strip .progress-track { height: 6px; }
  .progress-breakdown { grid-column: 2; display: flex; flex-wrap: wrap; gap: 8px; color: var(--text-tertiary); font-size: 12px; }
  .progress-breakdown span { margin: 0; }
  .detail-message { margin: 12px 22px 0; }
  .detail-message code { font-family: var(--font-mono); font-size: 13px; }
  .items-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 18px 22px 10px; }
  .items-toolbar div { display: flex; flex-direction: column; }
  .items-toolbar h3 { font-size: 15px; }
  .items-toolbar span { margin-top: 2px; color: var(--text-tertiary); font-size: 12px; }
  .item-counter { margin: 0 !important; padding: 4px 8px; border-radius: 99px; background: var(--bg-muted); font-size: 12px !important; font-weight: 650; }
  .items-list { padding: 0 22px 22px; }
  .items-head { display: grid; grid-template-columns: minmax(0,1fr) minmax(170px,.55fr); gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--border-color); color: var(--text-tertiary); font-size: 11px; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }
  .items-head > span:last-child { display: none; }
  .media-row { display: grid; grid-template-columns: 28px 34px minmax(0,1fr) minmax(170px,.55fr); align-items: center; gap: 9px; min-height: 58px; padding: 8px 3px 12px; border-bottom: 1px solid var(--border-color); }
  .media-row:last-child { border-bottom: 0; }
  .row-index { color: var(--text-tertiary); font-size: 12px; font-family: var(--font-mono); }
  .row-media-icon { display: grid; place-items: center; width: 33px; height: 33px; border-radius: 10px; color: var(--accent-color); background: var(--accent-soft); }
  .row-copy { display: flex; min-width: 0; flex-direction: column; }
  .row-copy strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .row-copy small { margin-top: 3px; overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .row-status { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
  .row-status > span:first-child { display: flex; align-items: center; justify-content: space-between; gap: 7px; }
  .row-status em { color: var(--text-secondary); font-size: 12px; font-style: normal; font-variant-numeric: tabular-nums; }
  .row-status .progress-track { height: 4px; }
  .row-actions { grid-column: 3 / -1; display: flex; align-items: center; justify-content: flex-start; flex-wrap: wrap; gap: 4px; min-width: 0; }
  .link-btn { display: inline-flex; align-items: center; gap: 4px; min-height: 40px; border: 0; color: var(--accent-color); background: transparent; cursor: pointer; font-size: 12px; font-weight: 650; padding: 4px 8px; border-radius: 6px; }
  .link-btn:hover { background: var(--accent-soft); }
  .remove-item { width: 40px; height: 40px; color: var(--text-tertiary); }
  .remove-item:hover { color: var(--danger-color); background: var(--danger-soft); }
  .detail-loading, .collection-welcome { min-height: 560px; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px; text-align: center; }
  .detail-loading h2, .collection-welcome h2 { font-size: 20px; }
  .detail-loading p, .collection-welcome p { max-width: 420px; margin-top: 6px; color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
  .welcome-stack { position: relative; width: 120px; height: 100px; margin-bottom: 19px; }
  .folder-back, .folder-front { position: absolute; display: grid; place-items: center; color: var(--warning-color); }
  .folder-back { left: 15px; top: 15px; opacity: .35; transform: rotate(-9deg); }
  .folder-front { left: 37px; top: 6px; color: var(--accent-color); }
  .welcome-spark { position: absolute; right: 2px; top: 0; color: var(--accent-color); }
  .welcome-actions { display: flex; gap: 8px; margin-top: 18px; }

  .modal-kicker { color: var(--accent-color); font-size: 12px; font-weight: 800; letter-spacing: .13em; }
  .modal-form { display: flex; flex-direction: column; gap: 18px; }
  .modal-tip { display: flex; align-items: flex-start; gap: 9px; padding: 11px; border: 1px solid color-mix(in srgb, var(--info-color) 18%, var(--border-color)); border-radius: 10px; color: var(--info-color); background: var(--info-soft); }
  .modal-tip p { color: var(--text-secondary); font-size: 13px; line-height: 1.55; }
  .delete-modal { width: min(430px, calc(100vw - 48px)); }
  .delete-content { display: flex; flex-direction: column; align-items: center; padding: 30px 28px 20px; text-align: center; }
  .delete-icon { display: grid; place-items: center; width: 52px; height: 52px; margin-bottom: 15px; border-radius: 16px; color: var(--danger-color); background: var(--danger-soft); }
  .delete-content h2 { font-size: 21px; }
  .delete-content p { margin-top: 7px; color: var(--text-secondary); font-size: 14px; line-height: 1.6; }

  @media (max-width: 1100px) {
    .collection-workspace { grid-template-columns: minmax(220px, 245px) minmax(0,1fr); }
  }

  .collection-sidebar { background: var(--bg-sidebar); }
  .collection-search { margin: 12px 11px 7px; }
  .collection-list { padding: 0 6px; }
  .collection-item.selected { box-shadow: none; }
  .items-toolbar { padding: 16px 19px 9px; }
  .items-list { padding: 0 19px 19px; }


  /* UI v7 — collection management workspace */
  .collections-page { max-width: 1240px; }
  .collection-metrics { gap: 12px; margin-bottom: 16px; }
  .metric { min-height: 76px; padding: 14px 16px; border-radius: 12px; }
  .metric-icon { width: 38px; height: 38px; border-radius: 10px; }
  .metric strong { font-size: 22px; }
  .metric small { font-size: 11px; color: var(--text-tertiary); }
  .collection-workspace { grid-template-columns: 290px minmax(0,1fr); min-height: 560px; border-radius: 14px; box-shadow: var(--shadow-sm); }
  .collection-sidebar { padding: 14px 11px; }
  .collection-search input { min-height: 40px; font-size: 12px; }
  .sidebar-label { padding: 12px 9px 8px; font-size: 10px; }
  .collection-item { grid-template-columns: 38px minmax(0,1fr) auto; gap: 10px; padding: 10px; }
  .collection-folder { width: 36px; height: 36px; }
  .collection-copy strong { font-size: 13px; }
  .collection-copy small { font-size: 10px; }
  .collection-detail { padding: 22px; }
  .detail-header { padding-bottom: 18px; }
  .detail-folder { width: 44px; height: 44px; border-radius: 13px; }
  .detail-title-wrap h2 { font-size: 22px; }
  .detail-title-wrap p, .detail-title-wrap span { font-size: 11px; }
  .detail-progress-strip { padding: 13px; }
  .items-toolbar h3 { font-size: 14px; }
  .items-toolbar span { font-size: 11px; }
  .items-head { font-size: 10px; }
  .media-row { min-height: 58px; padding: 10px 12px; }
  .row-copy strong { font-size: 13px; }
  .row-copy small, .row-status > span:first-child, .row-status em { font-size: 10px; }

  @media (max-width: 1180px) {
    .collection-workspace { grid-template-columns: minmax(240px, 270px) minmax(0,1fr); }
  }

  @media (max-width: 960px) {
    .collection-workspace { grid-template-columns: 1fr; min-height: auto; }
    .collection-sidebar { border-right: 0; border-bottom: 1px solid var(--border-color); max-height: 45vh; overflow: hidden; }
    .collection-list { max-height: 260px; }
    .detail-header { align-items: flex-start; gap: 10px; }
    .detail-actions { width: 100%; flex-wrap: wrap; }
    .items-head { grid-template-columns: minmax(0, 1fr) minmax(140px, .55fr); }
    .media-row { grid-template-columns: 22px 28px minmax(0, 1fr) minmax(140px, .55fr); gap: 7px; padding: 8px 2px 12px; }
    .detail-progress-strip { grid-template-columns: 85px 1fr; }
    .collection-metrics { grid-template-columns: repeat(3, 1fr); }
    .detail-actions .btn { min-height: 40px; padding: 7px 10px; font-size: 11px; }
  }

  @media (max-width: 600px) {
    .collection-metrics { grid-template-columns: 1fr; }
    .metric { min-height: 56px; }
  }

</style>
