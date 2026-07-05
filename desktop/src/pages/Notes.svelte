<script lang="ts">
  import { invoke } from "../lib/api";
  import type { NoteInfo, NoteDetail } from "../lib/types";
  import { marked } from "marked";
  import DOMPurify from "dompurify";

  // ── State ──────────────────────────────────────────────
  let notes = $state<NoteInfo[]>([]);
  let searchQuery = $state("");
  let selectedNoteId = $state<number | null>(null);
  let selectedNote = $state<NoteDetail | null>(null);
  let sourceView = $state(false);
  let editContent = $state("");
  let loading = $state(false);
  let loadingDetail = $state(false);
  let saving = $state(false);
  let deleting = $state(false);
  let error = $state<string | null>(null);
  let successMsg = $state<string | null>(null);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  // ── Derived ────────────────────────────────────────────
  let filteredNotes = $derived.by(() => {
    if (!searchQuery.trim()) return notes;
    const q = searchQuery.toLowerCase();
    return notes.filter(
      (n) =>
        n.title.toLowerCase().includes(q) || n.path.toLowerCase().includes(q)
    );
  });

  let isEditing = $derived(sourceView && selectedNote !== null);

  // ── Markdown rendering ─────────────────────────────────
  function renderMarkdown(content: string): string {
    try {
      const raw = marked.parse(content, { async: false }) as string;
      return DOMPurify.sanitize(raw);
    } catch {
      return `<p>渲染错误</p>`;
    }
  }

  // ── API calls ──────────────────────────────────────────
  async function loadNotes() {
    loading = true;
    error = null;
    try {
      notes = await invoke<NoteInfo[]>("notes.list");
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  function onSearchInput() {
    if (searchTimer) clearTimeout(searchTimer);
    if (!searchQuery.trim()) {
      loadNotes();
      return;
    }
    searchTimer = setTimeout(async () => {
      loading = true;
      error = null;
      try {
        notes = await invoke<NoteInfo[]>("notes.search", {
          query: searchQuery,
        });
      } catch (e) {
        error = String(e);
      } finally {
        loading = false;
      }
    }, 300);
  }

  async function selectNote(id: number) {
    if (selectedNoteId === id) return;
    selectedNoteId = id;
    selectedNote = null;
    sourceView = false;
    error = null;
    loadingDetail = true;
    try {
      selectedNote = await invoke<NoteDetail>("notes.get", { id });
      editContent = selectedNote.content;
    } catch (e) {
      error = String(e);
      selectedNoteId = null;
    } finally {
      loadingDetail = false;
    }
  }

  function toggleSourceView() {
    if (!selectedNote) return;
    sourceView = !sourceView;
  }

  async function saveNote() {
    if (!selectedNote) return;
    saving = true;
    error = null;
    try {
      await invoke("notes.update", { id: selectedNote.id, content: editContent });
      selectedNote = { ...selectedNote, content: editContent };
      sourceView = false;
      showSuccess("笔记已保存");
    } catch (e) {
      error = String(e);
    } finally {
      saving = false;
    }
  }

  function copyContent() {
    if (!selectedNote) return;
    const text = sourceView ? editContent : selectedNote.content;
    navigator.clipboard.writeText(text).then(
      () => showSuccess("已复制到剪贴板"),
      () => { error = "复制失败"; }
    );
  }

  async function deleteNote() {
    if (!selectedNote) return;
    if (!confirm(`确定删除「${selectedNote.title}」？此操作不可恢复。`)) return;
    deleting = true;
    error = null;
    try {
      await invoke("notes.delete", { id: selectedNote.id });
      showSuccess("笔记已删除");
      selectedNoteId = null;
      selectedNote = null;
      sourceView = false;
      await loadNotes();
    } catch (e) {
      error = String(e);
    } finally {
      deleting = false;
    }
  }

  async function openInEditor() {
    if (!selectedNote) return;
    try {
      await invoke("notes.open", { id: selectedNote.id });
    } catch (e) {
      error = String(e);
    }
  }

  async function openContainingDir() {
    if (!selectedNote) return;
    try {
      await invoke("notes.reveal", { id: selectedNote.id });
    } catch (e) {
      error = String(e);
    }
  }

  // ── Helpers ────────────────────────────────────────────
  let successTimer: ReturnType<typeof setTimeout> | null = null;
  function showSuccess(msg: string) {
    successMsg = msg;
    if (successTimer) clearTimeout(successTimer);
    successTimer = setTimeout(() => {
      successMsg = null;
    }, 3000);
  }

  function formatDate(iso: string): string {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  function truncate(text: string, max = 80): string {
    const clean = text.replace(/#{1,6}\s/g, "").replace(/\n+/g, " ").trim();
    return clean.length > max ? clean.slice(0, max) + "…" : clean;
  }

  // ── Effects ────────────────────────────────────────────
  $effect(() => {
    loadNotes();
  });
</script>

<div class="page notes-page">
  <!-- ── Left Panel: Search + List ──────────────────────── -->
  <aside class="notes-list-panel">
    <div class="search-bar">
      <svg class="search-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M7.333 12.667A5.333 5.333 0 107.333 2a5.333 5.333 0 000 10.667zM14 14l-2.9-2.9" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <input
        type="text"
        bind:value={searchQuery}
        placeholder="搜索笔记..."
        oninput={onSearchInput}
      />
      {#if searchQuery}
        <button class="clear-search" onclick={() => { searchQuery = ""; loadNotes(); }} title="清除搜索">
          &times;
        </button>
      {/if}
    </div>

    <div class="notes-list">
      {#if loading && notes.length === 0}
        <div class="loading-hint">加载中...</div>
      {:else if filteredNotes.length === 0}
        <div class="empty-hint">
          {searchQuery ? "未找到匹配的笔记" : "暂无笔记"}
        </div>
      {:else}
        {#each filteredNotes as note (note.id)}
          <button
            class="note-item"
            class:selected={selectedNoteId === note.id}
            onclick={() => selectNote(note.id)}
          >
            <div class="note-item-title">{note.title}</div>
            <div class="note-item-meta">{formatDate(note.created_at)}</div>
          </button>
        {/each}
      {/if}
    </div>
  </aside>

  <!-- ── Right Panel: Detail ────────────────────────────── -->
  <main class="note-detail-panel">
    {#if loadingDetail}
      <div class="detail-loading">加载笔记内容...</div>
    {:else if selectedNote}
      <!-- Top bar -->
      <div class="detail-topbar">
        <h3 class="detail-title">{selectedNote.title}</h3>
        <div class="detail-actions">
          <button
            class="btn-sm"
            onclick={toggleSourceView}
            title={sourceView ? "切换到预览" : "编辑源码"}
          >
            {sourceView ? "预览" : "编辑"}
          </button>

          {#if sourceView}
            <button
              class="btn-sm btn-primary"
              onclick={saveNote}
              disabled={saving}
            >
              {saving ? "保存中..." : "保存"}
            </button>
          {/if}

          <button class="btn-sm" onclick={copyContent} title="复制内容">
            复制
          </button>

          <button class="btn-sm" onclick={openInEditor} title="在编辑器中打开">
            打开
          </button>

          <button class="btn-sm" onclick={openContainingDir} title="打开所在文件夹">
            目录
          </button>

          <button
            class="btn-sm btn-danger"
            onclick={deleteNote}
            disabled={deleting}
          >
            {deleting ? "删除中..." : "删除"}
          </button>
        </div>
      </div>

      <!-- Feedback messages -->
      {#if error}
        <div class="feedback error-msg">{error}</div>
      {/if}
      {#if successMsg}
        <div class="feedback success-msg">{successMsg}</div>
      {/if}

      <!-- Content area -->
      <div class="detail-content">
        {#if sourceView}
          <textarea
            class="source-editor"
            bind:value={editContent}
            spellcheck="false"
          ></textarea>
        {:else}
          <div class="preview-area">
            {@html renderMarkdown(selectedNote.content)}
          </div>
        {/if}
      </div>
    {:else}
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none" class="empty-icon">
          <rect x="6" y="8" width="36" height="32" rx="3" stroke="currentColor" stroke-width="2"/>
          <path d="M16 18h16M16 24h12M16 30h8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <p>选择一个笔记</p>
        <span class="empty-sub">从左侧列表中选择一个笔记查看详情</span>
      </div>
    {/if}
  </main>
</div>

<style>
  /* ── Page layout ───────────────────────────────────── */
  .notes-page {
    display: flex;
    gap: 0;
    height: 100%;
    max-width: none;
    margin: -24px; /* offset .page padding from App.svelte */
    overflow: hidden;
  }

  /* ── Left panel ────────────────────────────────────── */
  .notes-list-panel {
    width: 320px;
    min-width: 260px;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border-color);
    background: var(--bg-sidebar);
  }

  .search-bar {
    position: relative;
    padding: 16px;
    border-bottom: 1px solid var(--border-color);
  }

  .search-bar input {
    width: 100%;
    padding: 8px 12px 8px 36px;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background: var(--bg-input);
    color: var(--text-primary);
    font-size: 14px;
    outline: none;
    transition: border-color 0.15s;
  }

  .search-bar input:focus {
    border-color: var(--accent-color);
  }

  .search-icon {
    position: absolute;
    left: 28px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--text-secondary);
    pointer-events: none;
  }

  .clear-search {
    position: absolute;
    right: 24px;
    top: 50%;
    transform: translateY(-50%);
    background: none;
    border: none;
    color: var(--text-secondary);
    font-size: 18px;
    cursor: pointer;
    padding: 2px 6px;
    line-height: 1;
  }

  .clear-search:hover {
    color: var(--text-primary);
  }

  .notes-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }

  .note-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 12px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-primary);
    cursor: pointer;
    transition: background 0.1s;
    margin-bottom: 2px;
  }

  .note-item:hover {
    background: var(--bg-hover);
  }

  .note-item.selected {
    background: var(--accent-bg);
  }

  .note-item-title {
    font-size: 14px;
    font-weight: 500;
    margin-bottom: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .note-item-meta {
    font-size: 12px;
    color: var(--text-secondary);
  }

  .loading-hint,
  .empty-hint {
    padding: 32px 16px;
    text-align: center;
    color: var(--text-secondary);
    font-size: 14px;
  }

  /* ── Right panel ───────────────────────────────────── */
  .note-detail-panel {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--bg-primary);
  }

  .detail-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-card);
    flex-shrink: 0;
  }

  .detail-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .detail-actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }

  /* ── Feedback ───────────────────────────────────────── */
  .feedback {
    padding: 8px 24px;
    font-size: 13px;
    flex-shrink: 0;
  }

  .error-msg {
    background: #fde8e8;
    color: #c53030;
    border-bottom: 1px solid #f8d7da;
  }

  :global(.dark) .error-msg {
    background: #3a1a1a;
    color: #fc8181;
  }

  .success-msg {
    background: #e6ffed;
    color: #22543d;
    border-bottom: 1px solid #c6f6d5;
  }

  :global(.dark) .success-msg {
    background: #1a2e1a;
    color: #68d391;
  }

  /* ── Content ────────────────────────────────────────── */
  .detail-content {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
  }

  .source-editor {
    width: 100%;
    height: 100%;
    min-height: 300px;
    padding: 16px;
    border: 1px solid var(--border-color);
    border-radius: 6px;
    background: var(--bg-input);
    color: var(--text-primary);
    font-family: "SF Mono", "Fira Code", "Consolas", monospace;
    font-size: 14px;
    line-height: 1.6;
    resize: none;
    outline: none;
    transition: border-color 0.15s;
  }

  .source-editor:focus {
    border-color: var(--accent-color);
  }

  /* ── Markdown preview ───────────────────────────────── */
  .preview-area {
    max-width: 800px;
  }

  .preview-area :global(h1),
  .preview-area :global(h2),
  .preview-area :global(h3),
  .preview-area :global(h4) {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    font-weight: 600;
    color: var(--text-primary);
  }

  .preview-area :global(h1) { font-size: 1.6em; }
  .preview-area :global(h2) { font-size: 1.35em; }
  .preview-area :global(h3) { font-size: 1.15em; }

  .preview-area :global(p) {
    margin-bottom: 1em;
    line-height: 1.7;
    color: var(--text-primary);
  }

  .preview-area :global(ul),
  .preview-area :global(ol) {
    margin-bottom: 1em;
    padding-left: 1.5em;
  }

  .preview-area :global(li) {
    margin-bottom: 0.3em;
    line-height: 1.6;
  }

  .preview-area :global(blockquote) {
    margin: 1em 0;
    padding: 8px 16px;
    border-left: 3px solid var(--accent-color);
    background: var(--accent-bg);
    border-radius: 0 4px 4px 0;
    color: var(--text-primary);
  }

  .preview-area :global(blockquote p) {
    margin-bottom: 0;
  }

  .preview-area :global(code) {
    padding: 2px 6px;
    border-radius: 3px;
    background: var(--bg-hover);
    font-family: "SF Mono", "Fira Code", "Consolas", monospace;
    font-size: 0.9em;
  }

  .preview-area :global(pre) {
    margin: 1em 0;
    padding: 16px;
    border-radius: 6px;
    background: #1e1e2e;
    overflow-x: auto;
  }

  .preview-area :global(pre code) {
    padding: 0;
    background: transparent;
    color: #cdd6f4;
    font-size: 13px;
    line-height: 1.5;
  }

  .preview-area :global(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
  }

  .preview-area :global(th),
  .preview-area :global(td) {
    padding: 8px 12px;
    text-align: left;
    border: 1px solid var(--border-color);
    font-size: 14px;
  }

  .preview-area :global(th) {
    background: var(--bg-hover);
    font-weight: 600;
  }

  .preview-area :global(hr) {
    margin: 2em 0;
    border: none;
    border-top: 1px solid var(--border-color);
  }

  .preview-area :global(a) {
    color: var(--accent-color);
    text-decoration: none;
  }

  .preview-area :global(a:hover) {
    text-decoration: underline;
  }

  .preview-area :global(img) {
    max-width: 100%;
    border-radius: 6px;
    margin: 1em 0;
  }

  .preview-area :global(strong) {
    font-weight: 600;
  }

  .preview-area :global(em) {
    font-style: italic;
  }

  /* ── Empty state ────────────────────────────────────── */
  .detail-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-secondary);
    font-size: 14px;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-secondary);
    gap: 12px;
  }

  .empty-icon {
    opacity: 0.4;
    margin-bottom: 4px;
  }

  .empty-state p {
    font-size: 16px;
    font-weight: 500;
    color: var(--text-primary);
  }

  .empty-sub {
    font-size: 13px;
    color: var(--text-secondary);
  }
</style>
