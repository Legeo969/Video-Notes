<script lang="ts">
  import { engineCall } from "../lib/api";
  import { convertFileSrc } from "@tauri-apps/api/core";
  import type { NoteInfo, NoteDetail, KnowledgeGraph } from "../lib/types";
  import { marked } from "marked";
  import DOMPurify from "dompurify";
  import Icon from "../lib/components/Icon.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import KnowledgeTree from "../lib/components/study/KnowledgeTree.svelte";
  import QuizPanel from "../lib/components/study/QuizPanel.svelte";
  import { selectedNoteId as selectedNoteIdStore } from "../lib/stores/jobs";
  import { onMount, onDestroy } from "svelte";

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
  let studyMode = $state(false);
  let graphData = $state<KnowledgeGraph | null>(null);
  let graphLoading = $state(false);
  let compileVersions = $state<Array<{ version: number; created_at: string; model_used: string }>>([]);
  let selectedCompileVersion = $state<number | null>(null);
  let versionsLoading = $state(false);

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
    // Strip angle brackets added by decode_markdown_image_paths for Obsidian compat
    const cleaned = decoded.replace(/^<|>$/g, '');
    const noteDir = noteDirectory(notePath);
    const absolute = WINDOWS_ABSOLUTE_PATH.test(cleaned) || cleaned.startsWith("/")
      ? cleaned
      : `${noteDir}\\${cleaned}`;
    // Path traversal guard
    if (absolute.includes("..") && !absolute.startsWith(noteDir.replace(/[\\/]$/, ''))) {
      return src;
    }
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
    selectedNoteId = id; selectedNote = null; sourceView = false; studyMode = false; graphData = null; error = null; loadingDetail = true; compileVersions = []; selectedCompileVersion = null;
    try {
      selectedNote = await engineCall<NoteDetail>("notes.get", { note_id: id });
      editContent = selectedNote.content;
      // Load compile versions for this note
      loadVersions();
    } catch (e) {
      error = String(e); selectedNoteId = null;
    } finally { loadingDetail = false; }
  }

  async function loadVersions() {
    if (!selectedNote) return;
    versionsLoading = true;
    try {
      const path = selectedNote.path;
      // Derive source_hash from note path (SHA-256 of the path)
      const encoder = new TextEncoder();
      const data = encoder.encode(path);
      const hashBuffer = await crypto.subtle.digest("SHA-256", data);
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      const sourceHash = hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
      const versions = await engineCall<Array<{ version: number; created_at: string; model_used: string }>>("compile.list_versions", { source_hash: sourceHash });
      compileVersions = versions || [];
      if (compileVersions.length > 0) {
        selectedCompileVersion = compileVersions[compileVersions.length - 1].version;
      }
    } catch {
      // No compiled versions — ignore silently
      compileVersions = [];
    } finally {
      versionsLoading = false;
    }
  }

  async function loadGraph() {
    if (!selectedNote) return;
    graphLoading = true;
    try {
      graphData = await engineCall<KnowledgeGraph>("study.knowledge", { note_id: selectedNote.id });
    } catch (e) {
      error = String(e);
    } finally {
      graphLoading = false;
    }
  }

  function toggleStudyMode() {
    if (!selectedNote) return;
    studyMode = !studyMode;
    sourceView = false;
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
    if (!selectedNote || !confirm(`确定删除「${selectedNote.title}」？对应 assets 图片目录也会一起删除，此操作不可恢复。`)) return;
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

  onMount(() => {
    loadNotes();
  });

  $effect(() => {
    const targetNoteId = $selectedNoteIdStore;
    if (targetNoteId !== null) {
      selectedNoteId = targetNoteId;
      selectedNoteIdStore.set(null);
    }
  });

  $effect(() => {
    if (studyMode && selectedNote && (!graphData || graphData.nodes.length === 0)) {
      loadGraph();
    }
  });

  onDestroy(() => {
    if (searchTimer) clearTimeout(searchTimer);
    if (successTimer) clearTimeout(successTimer);
  });
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
            <button class:active={!sourceView && !studyMode} onclick={() => { sourceView = false; studyMode = false; }}><Icon name="eye" size={14} />阅读</button>
            <button class:active={sourceView} onclick={() => { sourceView = true; studyMode = false; }}><Icon name="edit" size={14} />源码</button>
            <button class:active={studyMode} onclick={toggleStudyMode} disabled={!selectedNote}><Icon name="brain" size={14} />学习</button>
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
        {#if studyMode}
          <div class="study-layout">
            <div class="study-graph">
              <h3>知识图谱</h3>
              {#if graphLoading}
                <div class="study-loading"><span class="loading-ring"></span></div>
              {:else if graphData && graphData.nodes.length > 0}
                <KnowledgeTree graph={graphData} />
              {:else}
                <p class="study-empty">点击下方按钮生成知识图谱</p>
              {/if}
              <button class="btn btn-secondary btn-sm" onclick={loadGraph} disabled={graphLoading}>
                <Icon name="refresh" size={13} />生成知识图谱
              </button>
            </div>
            <div class="study-quiz">
              <h3>测验</h3>
              <QuizPanel noteId={selectedNote.id} onError={(msg) => error = msg} />
            </div>
          </div>
        {:else}
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
              {#if compileVersions.length > 0}
                <div class="compile-versions">
                  <Icon name="layers" size={13} />
                  <span>编译版本</span>
                  <select bind:value={selectedCompileVersion} onchange={() => {}}>
                    {#each compileVersions as v (v.version)}
                      <option value={v.version}>v{v.version} · {v.model_used} · {new Date(v.created_at).toLocaleDateString("zh-CN")}</option>
                    {/each}
                  </select>
                </div>
              {/if}
            </div>

            {#if sourceView}
              <textarea class="source-editor" bind:value={editContent} spellcheck="false" aria-label="Markdown 源码编辑器"></textarea>
            {:else}
              <div class="preview-area">{@html renderMarkdown(selectedNote.content, selectedNote.path)}</div>
            {/if}
          </article>
        {/if}
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
  .notes-workspace { display: grid; grid-template-columns: minmax(280px, 330px) minmax(0,1fr); width: 100%; height: 100%; min-height: 0; background: var(--bg-app); }
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

  .library-tools { display: flex; gap: 7px; padding: 0 14px 12px; border-bottom: 1px solid var(--border-color); }
  .search-box { flex: 1; }
  .search-box input { min-height: 34px; font-size: 13px; }
  .search-clear { position: absolute; right: 6px; display: grid; place-items: center; width: 22px; height: 22px; border: 0; border-radius: 5px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
  .library-error { display: flex; gap: 7px; margin: 10px 14px 0; padding: 8px; border-radius: 8px; color: var(--danger-color); background: var(--danger-soft); font-size: 12px; }
  .list-label { display: flex; align-items: center; justify-content: space-between; padding: 11px 17px 6px; color: var(--text-tertiary); font-size: 11px; font-weight: 740; letter-spacing: .08em; text-transform: uppercase; }
  .list-label em { font-style: normal; }
  .notes-list { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 0 8px 10px; }
  .note-item { position: relative; display: grid; grid-template-columns: 32px minmax(0,1fr) auto; align-items: start; gap: 8px; width: 100%; margin-bottom: 2px; padding: 9px; border: 1px solid transparent; border-radius: 10px; color: var(--text-primary); background: transparent; cursor: pointer; text-align: left; transition: background .14s, border-color .14s, transform .14s; }
  .note-item:hover { background: var(--bg-hover); transform: translateX(1px); }
  .note-item.selected { border-color: color-mix(in srgb, var(--accent-color) 22%, var(--border-color)); background: var(--accent-faint); box-shadow: inset 3px 0 0 var(--accent-color); }
  .note-file-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 8px; color: var(--text-secondary); background: var(--bg-muted); }
  .selected .note-file-icon { color: var(--accent-color); background: var(--accent-soft); }
  .note-item-copy { display: flex; min-width: 0; flex-direction: column; }
  .note-item-copy strong { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .note-item-copy small { margin-top: 2px; overflow: hidden; color: var(--text-tertiary); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .note-item-copy > span { display: flex; align-items: center; gap: 4px; margin-top: 5px; color: var(--text-tertiary); font-size: 10px; }
  .note-date { color: var(--text-tertiary); font-size: 10px; }
  .list-loading { min-height: 200px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-tertiary); font-size: 12px; }
  .loading-ring { width: 26px; height: 26px; margin-bottom: 9px; border: 3px solid var(--bg-progress); border-top-color: var(--accent-color); border-radius: 50%; animation: spin .8s linear infinite; }
  .loading-ring.large { width: 36px; height: 36px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .library-footer { display: flex; align-items: flex-start; gap: 6px; padding: 10px 14px; border-top: 1px solid var(--border-color); color: var(--text-tertiary); font-size: 10px; line-height: 1.45; }

  .reader-panel { display: flex; min-width: 0; height: 100%; min-height: 0; flex-direction: column; overflow: hidden; }
  .reader-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 58px; padding: 11px 18px; border-bottom: 1px solid var(--border-color); background: color-mix(in srgb, var(--bg-card) 94%, transparent); backdrop-filter: blur(10px); }
  .reader-breadcrumb { display: flex; align-items: center; gap: 5px; min-width: 0; color: var(--text-tertiary); font-size: 12px; }
  .reader-breadcrumb strong { overflow: hidden; color: var(--text-primary); text-overflow: ellipsis; white-space: nowrap; }
  .reader-actions { display: flex; align-items: center; gap: 5px; flex: 0 0 auto; }
  .view-toggle { display: flex; gap: 2px; padding: 3px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-subtle); }
  .view-toggle button { display: flex; align-items: center; gap: 4px; min-height: 26px; padding: 4px 7px; border: 0; border-radius: 6px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 12px; font-weight: 640; }
  .view-toggle button.active { color: var(--accent-color); background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .danger-action { color: var(--danger-color); }
  .reader-feedback { display: flex; align-items: center; gap: 7px; padding: 7px 18px; border-bottom: 1px solid var(--border-color); font-size: 12px; }
  .reader-feedback.error { color: var(--danger-color); background: var(--danger-soft); }
  .reader-feedback.success { color: var(--success-color); background: var(--success-soft); }
  .reader-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 28px 36px 48px; }
  .document-shell { width: min(840px, 100%); min-height: calc(100vh - 140px); margin: 0 auto; overflow: hidden; border: 1px solid var(--border-color); border-radius: 16px; background: var(--bg-card); box-shadow: var(--shadow-sm); }
  .document-head { padding: 30px 40px 24px; border-bottom: 1px solid var(--border-color); background: linear-gradient(145deg, var(--accent-faint), var(--bg-card) 72%); }
  .document-kicker { color: var(--accent-color); font-size: 11px; font-weight: 780; letter-spacing: .14em; }
  .document-head h1 { max-width: 700px; margin-top: 9px; font-size: 25px; line-height: 1.3; font-weight: 750; letter-spacing: -.035em; }
  .document-meta { display: flex; flex-wrap: wrap; gap: 13px; margin-top: 15px; color: var(--text-secondary); }
  .document-meta span, .document-path { display: flex; align-items: center; gap: 5px; font-size: 12px; }
  .document-path { margin-top: 12px; padding-top: 12px; border-top: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent); color: var(--text-tertiary); }
  .document-path code { overflow: hidden; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .compile-versions { display: flex; align-items: center; gap: 6px; margin-top: 10px; padding-top: 10px; border-top: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent); color: var(--text-tertiary); font-size: 12px; }
  .compile-versions select { min-height: 28px; padding: 0 24px 0 8px; border: 1px solid var(--border-strong); border-radius: 6px; color: var(--text-primary); background: var(--bg-input); font-size: 11px; cursor: pointer; }
  .source-editor { width: 100%; min-height: 600px; padding: 30px 40px; border: 0; border-radius: 0; background: var(--bg-card); font-family: var(--font-mono); font-size: 13px; line-height: 1.75; resize: none; }
  .source-editor:focus { box-shadow: inset 0 0 0 2px var(--accent-glow); }

  .preview-area { padding: 30px 40px 56px; color: var(--text-primary); font-size: 15px; line-height: 1.85; }
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

  .notes-workspace { grid-template-columns: minmax(260px, 304px) minmax(0, 1fr); background: var(--bg-app); }
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
  .notes-workspace { grid-template-columns: 310px minmax(0, 1fr); }
  .library-header { padding: 20px 18px 16px; }
  .library-title h1 { font-size: 20px; }
  .library-title span, .note-count span { font-size: 10px; }
  .library-tools { padding: 0 14px 14px; }
  .search-box input { min-height: 38px; font-size: 13px; }
  .list-label { padding: 12px 17px 8px; font-size: 10px; }
  .notes-list { padding: 4px 9px 12px; }
  .note-item { grid-template-columns: 36px minmax(0,1fr) auto; gap: 10px; padding: 11px; }
  .note-file-icon { width: 34px; height: 34px; }
  .note-item-copy strong { font-size: 13px; }
  .note-item-copy small, .note-item-copy > span, .note-date { font-size: 10px; }
  .reader-header { min-height: 60px; padding: 11px 20px; }
  .reader-breadcrumb { font-size: 12px; }
  .reader-scroll { padding: 26px 34px 52px; }
  .document-shell { width: min(860px, 100%); }
  .document-head { padding: 34px 44px 26px; }
  .document-head h1 { font-size: 28px; }
  .document-meta span, .document-path { font-size: 11px; }
  .preview-area { padding: 34px 44px 58px; font-size: 15px; line-height: 1.82; }
  .source-editor { padding: 34px 44px; font-size: 13px; }
  .welcome-reader h2 { font-size: 21px; }
  .welcome-reader > p { font-size: 13px; }

  @media (max-width: 1180px) {
    .notes-workspace { grid-template-columns: minmax(260px, 300px) minmax(0,1fr); }
    .reader-scroll { padding: 24px; }
  }

  @media (max-width: 1100px) {
    .reader-scroll { padding: 22px 20px; }
    .document-head { padding: 26px 24px 20px; }
    .preview-area { padding: 26px 24px 44px; }
    .source-editor { padding: 26px 24px; }
  }

  @media (max-width: 960px) {
    .notes-workspace { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
    .library-panel { height: auto; max-height: 45vh; min-height: 180px; border-right: 0; border-bottom: 1px solid var(--border-color); }
    .notes-list { max-height: 250px; }
    .reader-header { flex-wrap: wrap; gap: 8px; padding: 10px 14px; }
    .reader-actions { flex-wrap: wrap; gap: 4px; }
    .reader-scroll { padding: 18px 14px 36px; }
    .document-head { padding: 20px 18px 16px; }
    .document-head h1 { font-size: 22px; }
    .preview-area { padding: 20px 18px 36px; }
    .source-editor { padding: 20px 18px; }
    .library-header { padding: 16px 14px 12px; }
  }

  @media (max-width: 900px) {
    .library-panel { min-height: 160px; }
    .note-item { grid-template-columns: 32px minmax(0,1fr) auto; gap: 8px; padding: 8px; }
    .note-file-icon { width: 30px; height: 30px; }
    .reader-scroll { padding: 14px 12px 32px; }
    .document-head { padding: 16px 14px 14px; }
    .document-head h1 { font-size: 20px; }
    .preview-area { padding: 16px 14px 32px; }
    .source-editor { padding: 16px 14px; }
  }

  /* Study mode layout */
  .study-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    width: min(920px, 100%);
    margin: 0 auto;
    min-height: calc(100vh - 140px);
  }
  .study-graph, .study-quiz {
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    padding: 20px;
    box-shadow: var(--shadow-xs);
  }
  .study-graph {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-height: calc(100vh - 180px);
    overflow-y: auto;
  }
  .study-graph h3, .study-quiz h3 {
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
    margin-bottom: 4px;
  }
  .study-quiz {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .study-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 120px;
  }
  .study-loading .loading-ring {
    width: 28px; height: 28px;
    border: 3px solid var(--bg-progress);
    border-top-color: var(--accent-color);
    border-radius: 50%;
    animation: spin .8s linear infinite;
  }
  .study-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 120px;
    color: var(--text-tertiary);
    font-size: 13px;
  }

  @media (max-width: 768px) {
    .study-layout { grid-template-columns: 1fr; }
  }

</style>
