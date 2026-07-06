<script lang="ts">
  import { engineCall } from "../lib/api";
  import { convertFileSrc } from "@tauri-apps/api/core";
  import type { NoteInfo, NoteDetail } from "../lib/types";
  import { marked } from "marked";
  import DOMPurify from "dompurify";
  import Icon from "../lib/components/Icon.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";

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

  let filteredNotes = $derived.by(() => {
    if (!searchQuery.trim()) return notes;
    const q = searchQuery.toLowerCase();
    return notes.filter((n) => n.title.toLowerCase().includes(q) || n.path.toLowerCase().includes(q));
  });

  let wordCount = $derived(selectedNote ? selectedNote.content.replace(/\s/g, "").length : 0);
  let readingMinutes = $derived(Math.max(1, Math.ceil(wordCount / 500)));

  const WINDOWS_ABSOLUTE_PATH = /^[a-zA-Z]:[\\/]/;

  function stripMarkdownMetadata(content: string): string {
    const normalized = content.replace(/^\uFEFF/, "");
    const lines = normalized.split(/\r?\n/);
    if (lines[0]?.trim() !== "---") return normalized;
    const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
    return end > 0 ? lines.slice(end + 1).join("\n").trimStart() : normalized;
  }

  function noteDirectory(path: string): string {
    const parts = path.split(/[\\/]/);
    parts.pop();
    return parts.join("\\");
  }

  function decodeImagePath(path: string): string {
    try { return decodeURI(path); }
    catch { return path; }
  }

  function resolveImageSrc(src: string, notePath: string): string {
    if (!src || /^(https?:|data:|blob:|asset:|file:)/i.test(src) || src.startsWith("#")) return src;
    const decoded = decodeImagePath(src);
    const absolute = WINDOWS_ABSOLUTE_PATH.test(decoded) || decoded.startsWith("/")
      ? decoded
      : `${noteDirectory(notePath)}\\${decoded}`;
    return convertFileSrc(absolute);
  }

  function renderMarkdown(content: string, notePath: string): string {
    try {
      const raw = marked.parse(stripMarkdownMetadata(content), { async: false }) as string;
      const sanitized = DOMPurify.sanitize(raw);
      const doc = new DOMParser().parseFromString(sanitized, "text/html");
      for (const img of Array.from(doc.querySelectorAll("img"))) {
        img.setAttribute("src", resolveImageSrc(img.getAttribute("src") || "", notePath));
        img.setAttribute("loading", "lazy");
      }
      return doc.body.innerHTML;
    } catch {
      return `<p>渲染错误</p>`;
    }
  }

  async function loadNotes() {
    loading = true;
    error = null;
    try { notes = await engineCall<NoteInfo[]>("notes.list"); }
    catch (e) { error = String(e); }
    finally { loading = false; }
  }

  function onSearchInput() {
    if (searchTimer) clearTimeout(searchTimer);
    if (!searchQuery.trim()) { loadNotes(); return; }
    searchTimer = setTimeout(async () => {
      loading = true; error = null;
      try { notes = await engineCall<NoteInfo[]>("notes.search", { query: searchQuery }); }
      catch (e) { error = String(e); }
      finally { loading = false; }
    }, 300);
  }

  async function selectNote(id: number) {
    if (selectedNoteId === id) return;
    selectedNoteId = id; selectedNote = null; sourceView = false; error = null; loadingDetail = true;
    try {
      selectedNote = await engineCall<NoteDetail>("notes.get", { note_id: id });
      editContent = selectedNote.content;
    } catch (e) {
      error = String(e); selectedNoteId = null;
    } finally { loadingDetail = false; }
  }

  function toggleSourceView() { if (selectedNote) sourceView = !sourceView; }

  async function saveNote() {
    if (!selectedNote) return;
    saving = true; error = null;
    try {
      await engineCall("notes.update", { id: selectedNote.id, content: editContent });
      selectedNote = { ...selectedNote, content: editContent };
      sourceView = false; showSuccess("笔记已保存");
    } catch (e) { error = String(e); }
    finally { saving = false; }
  }

  function copyContent() {
    if (!selectedNote) return;
    const text = sourceView ? editContent : selectedNote.content;
    navigator.clipboard.writeText(text).then(() => showSuccess("已复制到剪贴板"), () => { error = "复制失败"; });
  }

  async function deleteNote() {
    if (!selectedNote || !confirm(`确定删除「${selectedNote.title}」？此操作不可恢复。`)) return;
    deleting = true; error = null;
    try {
      await engineCall("notes.delete", { id: selectedNote.id });
      showSuccess("笔记已删除"); selectedNoteId = null; selectedNote = null; sourceView = false; await loadNotes();
    } catch (e) { error = String(e); }
    finally { deleting = false; }
  }

  async function openInEditor() {
    if (!selectedNote) return;
    try { await engineCall("notes.open", { id: selectedNote.id }); }
    catch (e) { error = String(e); }
  }

  async function openContainingDir() {
    if (!selectedNote) return;
    try { await engineCall("notes.reveal", { id: selectedNote.id }); }
    catch (e) { error = String(e); }
  }

  let successTimer: ReturnType<typeof setTimeout> | null = null;
  function showSuccess(msg: string) {
    successMsg = msg;
    if (successTimer) clearTimeout(successTimer);
    successTimer = setTimeout(() => { successMsg = null; }, 3000);
  }

  function formatDate(iso: string): string {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  }

  function shortDate(iso: string): string {
    try {
      const d = new Date(iso);
      return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
    } catch { return iso; }
  }

  function basename(path: string) { return path.split(/[\\/]/).pop() || path; }

  $effect(() => { loadNotes(); });
</script>

<div class="notes-workspace">
  <aside class="library-panel">
    <div class="library-header">
      <div class="library-title"><div class="library-icon"><Icon name="note" size={18} /></div><div><span>KNOWLEDGE LIBRARY</span><h1>笔记库</h1></div></div>
      <div class="note-count"><strong>{notes.length}</strong><span>篇笔记</span></div>
    </div>

    <div class="library-tools">
      <div class="search-box input-wrap has-icon">
        <span class="input-icon"><Icon name="search" size={15} /></span>
        <input type="search" bind:value={searchQuery} placeholder="搜索标题、路径或关键词" oninput={onSearchInput} />
        {#if searchQuery}<button class="search-clear" onclick={() => { searchQuery = ""; loadNotes(); }} aria-label="清除搜索"><Icon name="x" size={13} /></button>{/if}
      </div>
      <button class="icon-btn" title="刷新笔记" onclick={loadNotes} disabled={loading}><Icon name="refresh" size={15} /></button>
    </div>

    {#if error && !selectedNote}
      <div class="library-error"><Icon name="alert" size={15} /><span>{error}</span></div>
    {/if}

    <div class="list-label"><span>全部笔记</span><em>{filteredNotes.length}</em></div>

    <div class="notes-list">
      {#if loading && notes.length === 0}
        <div class="list-loading"><span class="loading-ring"></span><p>正在读取笔记库</p></div>
      {:else if filteredNotes.length === 0}
        <EmptyState icon={searchQuery ? "search" : "note"} title={searchQuery ? "未找到匹配笔记" : "暂无笔记"} description={searchQuery ? "尝试使用其他关键词。" : "完成一个视频处理任务后，笔记会自动出现在这里。"} compact />
      {:else}
        {#each filteredNotes as note (note.id)}
          <button class="note-item" class:selected={selectedNoteId === note.id} onclick={() => selectNote(note.id)}>
            <span class="note-file-icon"><Icon name="file-text" size={16} /></span>
            <span class="note-item-copy">
              <strong>{note.title}</strong>
              <small>{basename(note.path)}</small>
              <span><Icon name="calendar" size={11} />{formatDate(note.created_at)}</span>
            </span>
            <span class="note-date">{shortDate(note.created_at)}</span>
          </button>
        {/each}
      {/if}
    </div>

    <div class="library-footer"><Icon name="shield" size={13} /><span>笔记文件保存在本地，内容不会上传到第三方存储。</span></div>
  </aside>

  <main class="reader-panel">
    {#if loadingDetail}
      <div class="reader-loading"><span class="loading-ring large"></span><h2>正在加载笔记</h2><p>正在读取 Markdown 内容与元数据…</p></div>
    {:else if selectedNote}
      <header class="reader-header">
        <div class="reader-breadcrumb"><span>笔记库</span><Icon name="chevron-right" size={12} /><strong>{selectedNote.title}</strong></div>
        <div class="reader-actions">
          <div class="view-toggle">
            <button class:active={!sourceView} onclick={() => sourceView = false}><Icon name="eye" size={14} />阅读</button>
            <button class:active={sourceView} onclick={() => sourceView = true}><Icon name="edit" size={14} />源码</button>
          </div>
          {#if sourceView}<button class="btn btn-primary btn-sm" onclick={saveNote} disabled={saving}><Icon name="save" size={14} />{saving ? "保存中" : "保存"}</button>{/if}
          <button class="icon-btn" onclick={copyContent} title="复制内容"><Icon name="copy" size={15} /></button>
          <button class="icon-btn" onclick={openInEditor} title="外部编辑器打开"><Icon name="external" size={15} /></button>
          <button class="icon-btn" onclick={openContainingDir} title="打开所在目录"><Icon name="folder-open" size={15} /></button>
          <button class="icon-btn danger-action" onclick={deleteNote} disabled={deleting} title="删除笔记"><Icon name="trash" size={15} /></button>
        </div>
      </header>

      {#if error}<div class="reader-feedback error"><Icon name="alert" size={15} /><span>{error}</span></div>{/if}
      {#if successMsg}<div class="reader-feedback success"><Icon name="check" size={15} /><span>{successMsg}</span></div>{/if}

      <div class="reader-scroll">
        <article class="document-shell">
          <div class="document-head">
            <span class="document-kicker">AI VIDEO NOTE</span>
            <h1>{selectedNote.title}</h1>
            <div class="document-meta">
              <span><Icon name="file-text" size={13} />Markdown 笔记</span>
              <span><Icon name="activity" size={13} />约 {wordCount.toLocaleString()} 字</span>
              <span><Icon name="clock" size={13} />约 {readingMinutes} 分钟阅读</span>
            </div>
            <div class="document-path"><Icon name="folder" size={13} /><code>{selectedNote.path}</code></div>
          </div>

          {#if sourceView}
            <textarea class="source-editor" bind:value={editContent} spellcheck="false" aria-label="Markdown 源码编辑器"></textarea>
          {:else}
            <div class="preview-area">{@html renderMarkdown(selectedNote.content, selectedNote.path)}</div>
          {/if}
        </article>
      </div>
    {:else}
      <div class="welcome-reader">
        <div class="welcome-visual">
          <div class="sheet back-sheet"></div>
          <div class="sheet front-sheet"><Icon name="file-text" size={34} /><span></span><span></span><span class="short"></span></div>
          <div class="spark spark-one"><Icon name="sparkles" size={18} /></div>
          <div class="spark spark-two"><Icon name="sparkles" size={13} /></div>
        </div>
        <h2>打开一篇笔记开始阅读</h2>
        <p>从左侧选择笔记，查看 AI 生成的结构化内容、编辑 Markdown 源码或导出到外部工具。</p>
        <div class="welcome-features">
          <span><Icon name="eye" size={14} />沉浸式阅读</span>
          <span><Icon name="edit" size={14} />Markdown 编辑</span>
          <span><Icon name="folder-open" size={14} />本地文件管理</span>
        </div>
      </div>
    {/if}
  </main>
</div>

<style>
  .notes-workspace { display: grid; grid-template-columns: 330px minmax(0,1fr); width: 100%; height: 100%; min-height: 0; background: var(--bg-app); }
  .library-panel { display: flex; min-width: 0; height: 100%; min-height: 0; overflow: hidden; flex-direction: column; border-right: 1px solid var(--border-color); background: var(--bg-card); }
  .library-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 20px 18px 16px; }
  .library-title { display: flex; align-items: center; gap: 10px; }
  .library-icon { display: grid; place-items: center; width: 37px; height: 37px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .library-title > div:last-child { display: flex; flex-direction: column; }
  .library-title span { color: var(--accent-color); font-size: 11px; font-weight: 800; letter-spacing: .12em; }
  .library-title h1 { margin-top: 2px; font-size: 20px; letter-spacing: -.02em; }
  .note-count { display: flex; flex-direction: column; align-items: flex-end; }
  .note-count strong { font-size: 18px; line-height: 1; }
  .note-count span { margin-top: 3px; color: var(--text-tertiary); font-size: 12px; }

  .library-tools { display: flex; gap: 7px; padding: 0 14px 14px; border-bottom: 1px solid var(--border-color); }
  .search-box { flex: 1; }
  .search-box input { min-height: 36px; font-size: 14px; }
  .search-clear { position: absolute; right: 6px; display: grid; place-items: center; width: 24px; height: 24px; border: 0; border-radius: 6px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
  .library-error { display: flex; gap: 7px; margin: 10px 14px 0; padding: 8px; border-radius: 8px; color: var(--danger-color); background: var(--danger-soft); font-size: 13px; }
  .list-label { display: flex; align-items: center; justify-content: space-between; padding: 12px 17px 7px; color: var(--text-tertiary); font-size: 12px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
  .list-label em { font-style: normal; }
  .notes-list { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 0 8px 10px; }
  .note-item { position: relative; display: grid; grid-template-columns: 34px minmax(0,1fr) auto; align-items: start; gap: 9px; width: 100%; margin-bottom: 3px; padding: 10px; border: 1px solid transparent; border-radius: 11px; color: var(--text-primary); background: transparent; cursor: pointer; text-align: left; transition: background .14s, border-color .14s, transform .14s; }
  .note-item:hover { background: var(--bg-hover); transform: translateX(1px); }
  .note-item.selected { border-color: color-mix(in srgb, var(--accent-color) 22%, var(--border-color)); background: var(--accent-faint); box-shadow: inset 3px 0 0 var(--accent-color); }
  .note-file-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; color: var(--text-secondary); background: var(--bg-muted); }
  .selected .note-file-icon { color: var(--accent-color); background: var(--accent-soft); }
  .note-item-copy { display: flex; min-width: 0; flex-direction: column; }
  .note-item-copy strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .note-item-copy small { margin-top: 2px; overflow: hidden; color: var(--text-tertiary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .note-item-copy > span { display: flex; align-items: center; gap: 4px; margin-top: 6px; color: var(--text-tertiary); font-size: 11px; }
  .note-date { color: var(--text-tertiary); font-size: 11px; }
  .list-loading { min-height: 220px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-tertiary); font-size: 13px; }
  .loading-ring { width: 27px; height: 27px; margin-bottom: 10px; border: 3px solid var(--bg-progress); border-top-color: var(--accent-color); border-radius: 50%; animation: spin .8s linear infinite; }
  .loading-ring.large { width: 38px; height: 38px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .library-footer { display: flex; align-items: flex-start; gap: 6px; padding: 10px 14px; border-top: 1px solid var(--border-color); color: var(--text-tertiary); font-size: 11px; line-height: 1.45; }

  .reader-panel { display: flex; min-width: 0; height: 100%; min-height: 0; flex-direction: column; overflow: hidden; }
  .reader-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 65px; padding: 13px 20px; border-bottom: 1px solid var(--border-color); background: color-mix(in srgb, var(--bg-card) 94%, transparent); backdrop-filter: blur(10px); }
  .reader-breadcrumb { display: flex; align-items: center; gap: 6px; min-width: 0; color: var(--text-tertiary); font-size: 13px; }
  .reader-breadcrumb strong { overflow: hidden; color: var(--text-primary); text-overflow: ellipsis; white-space: nowrap; }
  .reader-actions { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
  .view-toggle { display: flex; gap: 2px; padding: 3px; border: 1px solid var(--border-color); border-radius: 9px; background: var(--bg-subtle); }
  .view-toggle button { display: flex; align-items: center; gap: 5px; min-height: 28px; padding: 5px 8px; border: 0; border-radius: 6px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 13px; font-weight: 650; }
  .view-toggle button.active { color: var(--accent-color); background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .danger-action { color: var(--danger-color); }
  .reader-feedback { display: flex; align-items: center; gap: 7px; padding: 8px 20px; border-bottom: 1px solid var(--border-color); font-size: 13px; }
  .reader-feedback.error { color: var(--danger-color); background: var(--danger-soft); }
  .reader-feedback.success { color: var(--success-color); background: var(--success-soft); }
  .reader-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 30px 38px 54px; }
  .document-shell { width: min(860px, 100%); min-height: calc(100vh - 150px); margin: 0 auto; overflow: hidden; border: 1px solid var(--border-color); border-radius: 18px; background: var(--bg-card); box-shadow: var(--shadow-sm); }
  .document-head { padding: 34px 44px 26px; border-bottom: 1px solid var(--border-color); background: linear-gradient(145deg, var(--accent-faint), var(--bg-card) 72%); }
  .document-kicker { color: var(--accent-color); font-size: 12px; font-weight: 800; letter-spacing: .15em; }
  .document-head h1 { max-width: 720px; margin-top: 10px; font-size: 27px; line-height: 1.3; font-weight: 760; letter-spacing: -.035em; }
  .document-meta { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 17px; color: var(--text-secondary); }
  .document-meta span, .document-path { display: flex; align-items: center; gap: 5px; font-size: 13px; }
  .document-path { margin-top: 13px; padding-top: 13px; border-top: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent); color: var(--text-tertiary); }
  .document-path code { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .source-editor { width: 100%; min-height: 650px; padding: 34px 44px; border: 0; border-radius: 0; background: var(--bg-card); font-family: var(--font-mono); font-size: 14px; line-height: 1.75; resize: none; }
  .source-editor:focus { box-shadow: inset 0 0 0 2px var(--accent-glow); }

  .preview-area { padding: 34px 44px 60px; color: var(--text-primary); font-size: 16px; line-height: 1.85; }
  .preview-area :global(h1) { margin: 34px 0 16px; padding-bottom: 10px; border-bottom: 1px solid var(--border-color); font-size: 24px; }
  .preview-area :global(h2) { margin: 30px 0 13px; font-size: 23px; }
  .preview-area :global(h3) { margin: 24px 0 10px; font-size: 20px; }
  .preview-area :global(h1:first-child), .preview-area :global(h2:first-child), .preview-area :global(h3:first-child) { margin-top: 0; }
  .preview-area :global(p) { margin: 10px 0; }
  .preview-area :global(ul), .preview-area :global(ol) { margin: 11px 0; padding-left: 23px; }
  .preview-area :global(li) { margin: 5px 0; }
  .preview-area :global(blockquote) { margin: 16px 0; padding: 12px 16px; border-left: 3px solid var(--accent-color); border-radius: 0 9px 9px 0; color: var(--text-secondary); background: var(--accent-faint); }
  .preview-area :global(code) { padding: 2px 5px; border-radius: 5px; color: var(--accent-strong); background: var(--accent-soft); font-family: var(--font-mono); font-size: .9em; }
  .preview-area :global(pre) { margin: 16px 0; padding: 15px; overflow-x: auto; border: 1px solid var(--border-color); border-radius: 11px; background: var(--bg-subtle); }
  .preview-area :global(pre code) { padding: 0; color: var(--text-primary); background: transparent; }
  .preview-area :global(table) { width: 100%; margin: 17px 0; border-collapse: collapse; }
  .preview-area :global(th), .preview-area :global(td) { padding: 9px 11px; border: 1px solid var(--border-color); text-align: left; }
  .preview-area :global(th) { background: var(--bg-subtle); }
  .preview-area :global(a) { color: var(--accent-color); }
  .preview-area :global(img) { display: block; max-width: 100%; height: auto; margin: 14px 0; border-radius: 11px; outline: 1px solid rgba(0, 0, 0, 0.1); }
  :global(.dark) .preview-area :global(img) { outline-color: rgba(255, 255, 255, 0.1); }

  .reader-loading, .welcome-reader { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px; text-align: center; }
  .reader-loading h2, .welcome-reader h2 { font-size: 21px; }
  .reader-loading p, .welcome-reader > p { max-width: 430px; margin-top: 7px; color: var(--text-secondary); font-size: 14px; line-height: 1.65; }
  .welcome-visual { position: relative; width: 150px; height: 130px; margin-bottom: 24px; }
  .sheet { position: absolute; width: 82px; height: 105px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card); box-shadow: var(--shadow-sm); }
  .back-sheet { left: 28px; top: 10px; transform: rotate(-8deg); background: var(--accent-faint); }
  .front-sheet { left: 42px; top: 3px; display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 18px; color: var(--accent-color); transform: rotate(5deg); }
  .front-sheet > span { width: 45px; height: 4px; border-radius: 99px; background: var(--bg-progress); }
  .front-sheet > span.short { width: 29px; }
  .spark { position: absolute; display: grid; place-items: center; color: var(--accent-color); }
  .spark-one { right: 5px; top: 10px; }
  .spark-two { left: 3px; bottom: 15px; }
  .welcome-features { display: flex; gap: 9px; margin-top: 18px; }
  .welcome-features span { display: flex; align-items: center; gap: 5px; padding: 7px 9px; border: 1px solid var(--border-color); border-radius: 8px; color: var(--text-secondary); background: var(--bg-card); font-size: 12px; }

  @media (max-width: 1100px) {
    .notes-workspace { grid-template-columns: 285px minmax(0,1fr); }
    .reader-scroll { padding: 24px; }
    .document-head, .preview-area, .source-editor { padding-left: 30px; padding-right: 30px; }
  }

  .notes-workspace { grid-template-columns: 304px minmax(0, 1fr); background: var(--bg-app); }
  .library-panel { background: var(--bg-sidebar); }
  .library-header { padding: 17px 15px 13px; }
  .library-title span { color: var(--text-tertiary); }
  .library-tools { padding: 0 12px 12px; }
  .notes-list { padding: 4px 7px 10px; }
  .note-item { margin-bottom: 2px; padding: 9px; border-radius: 10px; }
  .note-item.selected { box-shadow: none; }
  .reader-header { min-height: 58px; padding: 10px 17px; background: var(--bg-appbar); }
  .reader-scroll { padding: 24px 30px 48px; background: var(--bg-app); }
  .document-shell { width: min(820px, 100%); border-radius: 16px; box-shadow: var(--shadow-xs); }
  .document-head { padding: 29px 38px 23px; background: var(--bg-card); }
  .document-head h1 { font-size: 30px; }
  .preview-area { padding: 30px 38px 54px; }
  .source-editor { padding: 30px 38px; }
  @media (max-width: 1100px) { .notes-workspace { grid-template-columns: 270px minmax(0,1fr); } }


  /* UI v7 — readable library and document workspace */
  .notes-workspace { grid-template-columns: 340px minmax(0, 1fr); }
  .library-header { padding: 22px 20px 18px; }
  .library-title h1 { font-size: 22px; }
  .library-title span, .note-count span { font-size: 11px; }
  .library-tools { padding: 0 16px 16px; }
  .search-box input { min-height: 42px; font-size: 14px; }
  .list-label { padding: 14px 18px 9px; font-size: 11px; }
  .notes-list { padding: 4px 10px 14px; }
  .note-item { grid-template-columns: 40px minmax(0,1fr) auto; gap: 11px; padding: 12px; }
  .note-file-icon { width: 38px; height: 38px; }
  .note-item-copy strong { font-size: 14px; }
  .note-item-copy small, .note-item-copy > span, .note-date { font-size: 11px; }
  .reader-header { min-height: 66px; padding: 12px 22px; }
  .reader-breadcrumb { font-size: 13px; }
  .reader-scroll { padding: 28px 36px 56px; }
  .document-shell { width: min(900px, 100%); }
  .document-head { padding: 38px 48px 30px; }
  .document-head h1 { font-size: 32px; }
  .document-meta span, .document-path { font-size: 12px; }
  .preview-area { padding: 38px 48px 64px; font-size: 16px; line-height: 1.82; }
  .source-editor { padding: 38px 48px; font-size: 14px; }
  .welcome-reader h2 { font-size: 23px; }
  .welcome-reader > p { font-size: 14px; }

  @media (max-width: 1180px) {
    .notes-workspace { grid-template-columns: 300px minmax(0,1fr); }
    .reader-scroll { padding: 24px; }
  }

</style>
