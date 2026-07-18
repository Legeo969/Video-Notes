<script module lang="ts">
  const CACHE_KEY = "vn-graph-cache";

  /** Load graph cache from localStorage. */
  function loadGraphCache(): Map<number, KnowledgeGraph> {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return new Map();
      const parsed = JSON.parse(raw);
      const order: number[] = parsed.order ?? [];
      const graphs: Record<string, KnowledgeGraph> = parsed.graphs ?? {};
      const map = new Map<number, KnowledgeGraph>();
      for (const id of order) {
        if (graphs[String(id)]) map.set(id, graphs[String(id)]);
      }
      return map;
    } catch { return new Map(); }
  }

  /** Persist graph cache to localStorage. */
  function saveGraphCache(map: Map<number, KnowledgeGraph>) {
    try {
      const order = Array.from(map.keys());
      const graphs: Record<string, KnowledgeGraph> = {};
      for (const [id, data] of map) graphs[String(id)] = data;
      localStorage.setItem(CACHE_KEY, JSON.stringify({ order, graphs }));
    } catch { /* quota exceeded — silently degrade */ }
  }

  function cacheGraph(id: number, data: KnowledgeGraph) {
    const map = loadGraphCache();
    if (map.has(id)) map.delete(id);
    map.set(id, data);
    saveGraphCache(map);
  }

  /** Exposed for Settings page — returns cached graph count and estimated bytes. */
  (window as any).__graphCacheInfo = () => {
    const map = loadGraphCache();
    let bytes = 0;
    for (const [, data] of map) bytes += JSON.stringify(data).length;
    return { count: map.size, bytes };
  };
  (window as any).__graphCacheClear = () => {
    localStorage.removeItem(CACHE_KEY);
  };

  // Q&A history per note — localStorage, persists across restarts.
  const QA_CACHE_KEY = "vn-qa-cache";
  function loadQACache(): Map<number, Array<{ role: "user" | "assistant"; content: string }>> {
    try {
      const raw = localStorage.getItem(QA_CACHE_KEY);
      if (!raw) return new Map();
      const parsed = JSON.parse(raw);
      return new Map(Object.entries(parsed).map(([k, v]) => [Number(k), v as any]));
    } catch { return new Map(); }
  }
  function saveQACache(map: Map<number, Array<{ role: "user" | "assistant"; content: string }>>) {
    try {
      const obj: Record<string, any> = {};
      for (const [id, history] of map) obj[String(id)] = history;
      localStorage.setItem(QA_CACHE_KEY, JSON.stringify(obj));
    } catch { /* quota exceeded — silently degrade */ }
  }
  function cacheQA(id: number, history: Array<{ role: "user" | "assistant"; content: string }>) {
    const map = loadQACache();
    map.set(id, history);
    saveQACache(map);
  }
  function clearQACache(id: number) {
    const map = loadQACache();
    map.delete(id);
    saveQACache(map);
  }
</script>

<script lang="ts">
  import { engineCall } from "../lib/api";
  import { convertFileSrc } from "@tauri-apps/api/core";
  import type { NoteInfo, NoteDetail, KnowledgeGraph, VideoCapsule } from "../lib/types";
  import { marked } from "marked";
  import DOMPurify from "dompurify";
  import Icon from "../lib/components/Icon.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import KnowledgeTree from "../lib/components/study/KnowledgeTree.svelte";
  import QuizPanel from "../lib/components/study/QuizPanel.svelte";
  import EvidenceViewer from "../lib/components/study/EvidenceViewer.svelte";
  import { selectedNoteId as selectedNoteIdStore } from "../lib/stores/jobs";
  import { onMount, onDestroy } from "svelte";
  import { jobs } from "../lib/stores/jobs";

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
  let qaMode = $state(false);
  let evidenceMode = $state(false);
  let graphData = $state<KnowledgeGraph | null>(null);
  let graphLoading = $state(false);
  let capsuleData = $state<VideoCapsule | null>(null);
  let capsuleLoading = $state(false);

  // External mpv playback
  let videoLoading = $state(false);

  let qaQuestion = $state("");
  let qaAnswer = $state<{ answer: string; citations: number[]; confidence: string } | null>(null);
  let qaLoading = $state(false);
  let qaError = $state<string | null>(null);
  let qaHistory = $state<Array<{ role: "user" | "assistant"; content: string }>>([]);
  let compileVersions = $state<Array<{ version: number; created_at: string; model_used: string }>>([]);
  let selectedCompileVersion = $state<number | null>(null);
  let selectedSourceHash = $state<string | null>(null);
  let versionsLoading = $state(false);
  let switchingVersion = $state(false);

  let filteredNotes = $derived.by(() => {
    // Backend search handles filtering, this is a client-side fallback
    if (!searchQuery.trim()) return notes;
    const q = searchQuery.toLowerCase();
    return notes.filter((n) => n.title.toLowerCase().includes(q) || n.path.toLowerCase().includes(q));
  });

  type HighlightSegment = { text: string; matched: boolean };

  function highlightSegments(text: string, query: string): HighlightSegment[] {
    if (!query.trim()) return [{ text, matched: false }];
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try {
      const matcher = new RegExp(`(${escaped})`, 'gi');
      const exactMatch = new RegExp(`^${escaped}$`, 'i');
      return text
        .split(matcher)
        .filter(Boolean)
        .map((part) => ({ text: part, matched: exactMatch.test(part) }));
    } catch {
      return [{ text, matched: false }];
    }
  }

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

  function resolveImageSrc(src: string, notePath: string): string | null {
    if (!src) return null;
    if (/^data:image\//i.test(src) || src.startsWith("#")) return src;
    if (/^[a-z][a-z0-9+.-]*:/i.test(src)) return null;
    const decoded = decodeImagePath(src);
    const cleaned = decoded.replace(/^<|>$/g, '');
    if (WINDOWS_ABSOLUTE_PATH.test(cleaned) || cleaned.startsWith("/") || cleaned.startsWith("\\")) return null;
    const noteDir = noteDirectory(notePath);
    const baseParts = noteDir.split(/[\\/]/).filter(Boolean);
    const parts = [...baseParts];
    for (const segment of cleaned.split(/[\\/]/)) {
      if (!segment || segment === ".") continue;
      if (segment === "..") {
        if (parts.length === baseParts.length) return null;
        parts.pop();
      } else {
        parts.push(segment);
      }
    }
    return convertFileSrc(parts.join("\\"));
  }

  function renderMarkdown(content: string, notePath: string): string {
    try {
      const raw = marked.parse(stripMarkdownMetadata(content), { async: false }) as string;
      const sanitized = DOMPurify.sanitize(raw);
      const doc = new DOMParser().parseFromString(sanitized, "text/html");
      for (const img of Array.from(doc.querySelectorAll("img"))) {
        const resolved = resolveImageSrc(img.getAttribute("src") || "", notePath);
        if (!resolved) {
          img.remove();
          continue;
        }
        img.setAttribute("src", resolved);
        img.setAttribute("loading", "lazy");
      }
      // Clickable timestamps → seek in video player. Build links from text
      // nodes so sanitized content is never reparsed through innerHTML.
      const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
      const textNodes: Text[] = [];
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        textNodes.push(node as Text);
      }
      for (const node of textNodes) {
        if (node.parentElement?.closest("a, code, pre")) continue;
        const matches = Array.from(node.data.matchAll(/\[(\d+):(\d+)–(\d+):(\d+)\]/g));
        if (matches.length === 0) continue;
        const fragment = doc.createDocumentFragment();
        let offset = 0;
        for (const match of matches) {
          const index = match.index ?? 0;
          fragment.append(doc.createTextNode(node.data.slice(offset, index)));
          const link = doc.createElement("a");
          link.className = "citation-link";
          link.href = "#";
          link.dataset.seek = String(Number(match[1]) * 60 + Number(match[2]));
          link.textContent = match[0];
          fragment.append(link);
          offset = index + match[0].length;
        }
        fragment.append(doc.createTextNode(node.data.slice(offset)));
        node.replaceWith(fragment);
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
    selectedNoteId = id; selectedNote = null; sourceView = false; studyMode = false; evidenceMode = false; capsuleData = null; graphData = null; error = null; loadingDetail = true; compileVersions = []; selectedCompileVersion = null; selectedSourceHash = null;
    // Restore cached Q&A history for this note.
    const cached = loadQACache().get(id);
    qaHistory = cached ?? [];
    try {
      selectedNote = await engineCall<NoteDetail>("notes.get", { note_id: id });
      editContent = selectedNote.content;
      // Load compile versions for this note
      loadVersions();
    } catch (e) {
      error = String(e); selectedNoteId = null;
    } finally { loadingDetail = false; }
  }

  function compileMetadata(content: string): { sourceHash: string; version: number | null } | null {
    const normalized = content.replace(/^\uFEFF/, "");
    if (!normalized.startsWith("---")) return null;
    const end = normalized.indexOf("\n---", 3);
    if (end < 0) return null;
    const frontmatter = normalized.slice(3, end);
    const sourceHash = frontmatter.match(/^video_notes_source_hash:\s*([A-Za-z0-9_-]+)\s*$/m)?.[1];
    const versionText = frontmatter.match(/^video_notes_version:\s*(\d+)\s*$/m)?.[1];
    if (!sourceHash) return null;
    return { sourceHash, version: versionText ? Number(versionText) : null };
  }

  async function loadVersions() {
    if (!selectedNote) return;
    const metadata = compileMetadata(selectedNote.content);
    if (!metadata) {
      compileVersions = [];
      selectedCompileVersion = null;
      selectedSourceHash = null;
      return;
    }
    versionsLoading = true;
    selectedSourceHash = metadata.sourceHash;
    try {
      const versions = await engineCall<Array<{ version: number; created_at: string; model_used: string }>>("compile.list_versions", { source_hash: metadata.sourceHash });
      compileVersions = versions || [];
      const knownVersion = compileVersions.some((item) => item.version === metadata.version);
      selectedCompileVersion = knownVersion
        ? metadata.version
        : compileVersions.at(-1)?.version ?? null;
    } catch {
      compileVersions = [];
      selectedCompileVersion = null;
      selectedSourceHash = null;
    } finally {
      versionsLoading = false;
    }
  }

  async function switchCompileVersion() {
    if (!selectedNote || !selectedSourceHash || selectedCompileVersion === null) return;
    switchingVersion = true;
    error = null;
    try {
      const result = await engineCall<{ content: string }>("compile.render", {
        source_hash: selectedSourceHash,
        version: Number(selectedCompileVersion),
        template: "markdown",
      });
      selectedNote = { ...selectedNote, content: result.content };
      editContent = result.content;
      sourceView = false;
      studyMode = false;
      graphData = null;
      qaHistory = [];
      cacheQA(selectedNote.id, []);
    } catch (e) {
      error = `切换编译版本失败：${String(e)}`;
    } finally {
      switchingVersion = false;
    }
  }

  async function openVideoInMpv(startSeconds = 0) {
    if (videoLoading) return;
    const noteId = selectedNoteId;
    if (noteId === null) return;
    videoLoading = true;
    error = null;
    try {
      await engineCall("notes.video_playback", {
        note_id: noteId,
        start_seconds: Math.max(0, startSeconds),
      });
    } catch (e) {
      if (selectedNoteId === noteId) error = `打开视频失败：${String(e)}`;
    } finally {
      videoLoading = false;
    }
  }

  function playVideo() {
    void openVideoInMpv(0);
  }

  function seekTo(seconds: number) {
    void openVideoInMpv(seconds);
  }

  function seekFromNote(startUs: number) {
    const sec = startUs / 1_000_000;
    seekTo(sec);
  }

  // Delegated click handler for timestamp links (avoids inline onclick issues in WebView2)
  function handlePreviewClick(e: Event) {
    const link = (e.target as HTMLElement)?.closest?.('.citation-link');
    if (!link) return;
    e.preventDefault();
    const sec = link.getAttribute('data-seek');
    if (sec) seekTo(Number(sec));
  }

  onMount(() => {
    loadNotes();
    // Check if navigated from another page with a specific note ID
    const pendingNoteId = $selectedNoteIdStore;
    if (pendingNoteId !== null) {
      selectedNoteIdStore.set(null);
      selectNote(pendingNoteId);
    }
  });

  async function loadGraph() {
    if (!selectedNote) return;
    // Use cached data when available.
    const cached = loadGraphCache().get(selectedNote.id);
    if (cached) { graphData = cached; return; }
    graphLoading = true;
    try {
      graphData = await engineCall<KnowledgeGraph>("study.knowledge", { note_id: selectedNote.id });
      cacheGraph(selectedNote.id, graphData);
    } catch (e) {
      error = String(e);
    } finally {
      graphLoading = false;
    }
  }

  async function loadCapsule() {
    if (!selectedSourceHash || selectedCompileVersion === null) return;
    capsuleLoading = true;
    try {
      capsuleData = await engineCall<VideoCapsule>("compile.replay", {
        source_hash: selectedSourceHash,
        version: selectedCompileVersion,
      });
    } catch (e) {
      error = String(e);
    } finally {
      capsuleLoading = false;
    }
  }

  function toggleStudyMode() {
    if (!selectedNote) return;
    studyMode = !studyMode;
    qaMode = false;
    sourceView = false;
    evidenceMode = false;
  }

  function toggleQaMode() {
    if (!selectedNote) return;
    qaMode = !qaMode;
    studyMode = false;
    sourceView = false;
    evidenceMode = false;
  }

  function toggleEvidenceMode() {
    if (!selectedNote) return;
    evidenceMode = !evidenceMode;
    studyMode = false;
    qaMode = false;
    sourceView = false;
  }

  async function askQuestion() {
    if (!selectedNote || !qaQuestion.trim() || !selectedSourceHash || !selectedCompileVersion) return;
    qaLoading = true;
    qaError = null;
    const question = qaQuestion.trim();
    qaHistory = [...qaHistory, { role: "user", content: question }];
    qaQuestion = "";
    try {
      const result = await engineCall<{ answer: string; citations: number[]; confidence: string }>("notes.answer", {
        source_hash: selectedSourceHash,
        version: selectedCompileVersion,
        question,
        history: qaHistory,  // send conversation context for follow-up awareness
      });
      qaAnswer = result;
      qaHistory = [...qaHistory, { role: "assistant", content: result.answer }];
      cacheQA(selectedNote.id, qaHistory);
    } catch (e) {
      qaError = String(e);
      qaHistory = [...qaHistory, { role: "assistant", content: `❌ ${e}` }];
      cacheQA(selectedNote.id, qaHistory);
    } finally {
      qaLoading = false;
    }
  }

  function handleQaKeydown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      askQuestion();
    }
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
    const noteId = selectedNote.id;
    try {
      await engineCall("notes.delete", { id: noteId });
      clearQACache(noteId);
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

  let noteRefreshTimer: ReturnType<typeof setTimeout> | null = null;

  $effect(() => {
    // Auto-refresh notes when any job completes (a new note may be available).
    const _ = $jobs;
    if (noteRefreshTimer) clearTimeout(noteRefreshTimer);
    noteRefreshTimer = setTimeout(() => {
      if (!searchQuery.trim()) loadNotes();
    }, 1000);
  });

  onDestroy(() => {
    if (noteRefreshTimer) clearTimeout(noteRefreshTimer);
  });

  $effect(() => {
    const targetNoteId = $selectedNoteIdStore;
    if (targetNoteId !== null) {
      selectedNoteIdStore.set(null);
      selectNote(targetNoteId);
    }
  });

  $effect(() => {
    if (studyMode && selectedNote && (!graphData || graphData.entities.length === 0)) {
      loadGraph();
    }
  });

  $effect(() => {
    if (evidenceMode && selectedNote && selectedSourceHash && selectedCompileVersion !== null && !capsuleData && !capsuleLoading) {
      loadCapsule();
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
              {#if searchQuery.trim()}
                <strong>
                  {#each highlightSegments(note.title, searchQuery) as segment}
                    {#if segment.matched}<mark>{segment.text}</mark>{:else}{segment.text}{/if}
                  {/each}
                </strong>
              {:else}
                <strong>{note.title}</strong>
              {/if}
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
            <button class:active={!sourceView && !studyMode} onclick={() => { sourceView = false; studyMode = false; evidenceMode = false; }}><Icon name="eye" size={14} />阅读</button>
            <button class:active={sourceView} onclick={() => { sourceView = true; studyMode = false; evidenceMode = false; }}><Icon name="edit" size={14} />源码</button>
            <button class:active={studyMode} onclick={toggleStudyMode} disabled={!selectedNote}><Icon name="brain" size={14} />学习</button>
            <button class:active={qaMode} onclick={toggleQaMode} disabled={!selectedNote || !selectedSourceHash}><Icon name="search" size={14} />问答</button>
            <button class:active={evidenceMode} onclick={toggleEvidenceMode} disabled={!selectedNote || !selectedSourceHash}><Icon name="list" size={14} />证据</button>
            <button onclick={playVideo} disabled={videoLoading} title={videoLoading ? "正在打开 mpv" : "使用 mpv 播放本地视频"}><Icon name="play" size={14} />{videoLoading ? "打开中" : "视频"}</button>
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
        {#if qaMode}
          <div class="qa-panel">
            <div class="qa-head">
              <h3>证据问答</h3>
              {#if qaHistory.length > 0}
                <button class="btn btn-secondary btn-sm" onclick={() => { clearQACache(selectedNote!.id); qaHistory = []; qaAnswer = null; }}>
                  <Icon name="trash" size={13} />清空历史
                </button>
              {/if}
            </div>
            <p class="qa-hint">根据笔记内容提问，AI 将基于证据引用回答。</p>
            <div class="qa-history">
              {#each qaHistory as entry, i (i)}
                <div class="qa-message {entry.role}">
                  <div class="qa-message-role">{entry.role === "user" ? "你" : "AI"}</div>
                  <div class="qa-message-content">{entry.content}</div>
                </div>
              {/each}
              {#if qaLoading}
                <div class="qa-message assistant">
                  <div class="qa-message-role">AI</div>
                  <div class="qa-message-content typing"><span class="loading-ring"></span> 思考中…</div>
                </div>
              {/if}
            </div>
            {#if qaError}
              <div class="qa-error"><Icon name="alert" size={14} /><span>{qaError}</span></div>
            {/if}
            <div class="qa-input-row">
              <input
                type="text"
                bind:value={qaQuestion}
                placeholder="输入关于这个视频的问题…"
                onkeydown={handleQaKeydown}
                disabled={qaLoading}
              />
              <button class="btn btn-primary btn-sm" onclick={askQuestion} disabled={qaLoading || !qaQuestion.trim() || !selectedSourceHash}>
                <Icon name="arrow-right" size={14} />
              </button>
            </div>
          </div>
        {:else if evidenceMode}
          <div class="evidence-panel">
            <div class="evidence-panel-head">
              <h3>证据引用</h3>
              {#if capsuleData?.evidences}
                <span class="evidence-count">{capsuleData.evidences.length} 项</span>
              {/if}
            </div>
            {#if capsuleLoading}
              <div class="evidence-loading"><span class="loading-ring"></span><p>正在加载证据数据…</p></div>
            {:else if !selectedSourceHash || selectedCompileVersion === null}
              <div class="evidence-empty-state">
                <Icon name="info" size={20} />
                <p>需要选择编译版本才能查看证据引用。</p>
              </div>
            {:else}
              <EvidenceViewer bundle={capsuleData} onSeek={seekFromNote} />
            {/if}
          </div>
        {:else if studyMode}
          <div class="study-layout">
            <div class="study-graph">
              <h3>知识图谱</h3>
              {#if graphLoading}
                <div class="study-loading"><span class="loading-ring"></span></div>
              {:else if graphData && graphData.entities.length > 0}
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
                  <select bind:value={selectedCompileVersion} onchange={switchCompileVersion} disabled={versionsLoading || switchingVersion}>
                    {#each compileVersions as v (v.version)}
                      <option value={v.version}>v{v.version} · {v.model_used} · {new Date(v.created_at).toLocaleDateString("zh-CN")}</option>
                    {/each}
                  </select>
                  {#if switchingVersion}<span class="version-loading">切换中…</span>{/if}
                </div>
              {/if}
            </div>

            {#if sourceView}
              <textarea class="source-editor" bind:value={editContent} spellcheck="false" aria-label="Markdown 源码编辑器"></textarea>
            {:else}
              <div class="preview-area" onclick={handlePreviewClick} onkeydown={(e) => e.key === 'Enter' && handlePreviewClick(e)} role="button" tabindex="0">{@html renderMarkdown(selectedNote.content, selectedNote.path)}</div>
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
  .search-box input { min-height: 40px; padding-right: 42px; font-size: 13px; }
  .search-clear { position: absolute; right: 1px; display: grid; place-items: center; width: 40px; height: 40px; border: 0; border-radius: 7px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
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
  .note-item-copy :global(mark) { background: var(--accent-soft); color: var(--accent-color); border-radius: 3px; padding: 0 2px; }
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
  .reader-header { display: grid; grid-template-columns: minmax(0, 1fr); align-items: center; gap: 8px; min-height: 56px; padding: 10px 18px; border-bottom: 1px solid var(--border-color); background: var(--bg-appbar); }
  .reader-breadcrumb { display: flex; align-items: center; gap: 5px; min-width: 0; color: var(--text-tertiary); font-size: 12px; }
  .reader-breadcrumb strong { overflow: hidden; color: var(--text-primary); text-overflow: ellipsis; white-space: nowrap; }
  .reader-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; min-width: 0; }
  .view-toggle { display: flex; flex-wrap: wrap; gap: 2px; max-width: 100%; padding: 3px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-subtle); }
  .view-toggle button { display: flex; align-items: center; justify-content: center; gap: 4px; min-height: 40px; padding: 6px 9px; border: 0; border-radius: 6px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 12px; font-weight: 640; }
  .view-toggle button.active { color: var(--accent-color); background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .danger-action { color: var(--danger-color); }
  .reader-feedback { display: flex; align-items: center; gap: 7px; padding: 7px 18px; border-bottom: 1px solid var(--border-color); font-size: 12px; }
  .reader-feedback.error { color: var(--danger-color); background: var(--danger-soft); }
  .reader-feedback.success { color: var(--success-color); background: var(--success-soft); }
  .citation-link { color: var(--accent-color); cursor: pointer; text-decoration: underline; text-decoration-style: dotted; }
  .citation-link:hover { color: var(--accent-hover); }
  .reader-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 28px 36px 48px; background: var(--bg-app); }
  .document-shell { width: min(820px, 100%); min-height: calc(100vh - 140px); margin: 0 auto; overflow: hidden; border: 1px solid var(--border-color); border-radius: 16px; background: var(--bg-card); box-shadow: var(--shadow-xs); }
  .document-head { padding: 32px 42px 26px; border-bottom: 1px solid var(--border-color); }
  .document-kicker { color: var(--accent-color); font-size: 11px; font-weight: 780; letter-spacing: .14em; text-transform: uppercase; }
  .document-head h1 { max-width: 700px; margin-top: 9px; font-size: 25px; line-height: 1.3; font-weight: 750; letter-spacing: -.035em; }
  .document-meta { display: flex; flex-wrap: wrap; gap: 13px; margin-top: 15px; color: var(--text-secondary); }
  .document-meta span, .document-path { display: flex; align-items: center; gap: 5px; font-size: 12px; }
  .document-path { margin-top: 12px; padding-top: 12px; border-top: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent); color: var(--text-tertiary); }
  .document-path code { overflow: hidden; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .compile-versions { display: flex; align-items: center; gap: 6px; margin-top: 10px; padding-top: 10px; border-top: 1px solid color-mix(in srgb, var(--border-color) 70%, transparent); color: var(--text-tertiary); font-size: 12px; }
  .version-loading { color: var(--accent-color); font-size: 11px; }
  .compile-versions select { min-height: 40px; padding: 0 28px 0 10px; border: 1px solid var(--border-strong); border-radius: 7px; color: var(--text-primary); background: var(--bg-input); font-size: 11px; cursor: pointer; }
  .source-editor { width: 100%; min-height: 600px; padding: 32px 42px; border: 0; border-radius: 0; background: var(--bg-card); font-family: var(--font-mono); font-size: 13px; line-height: 1.75; resize: none; }
  .source-editor:focus { box-shadow: inset 0 0 0 2px var(--accent-glow); }

  .preview-area { padding: 32px 42px 56px; color: var(--text-primary); font-size: 15px; line-height: 1.82; }
  .preview-area :global(h1) { margin: 36px 0 18px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color); font-size: 26px; font-weight: 750; letter-spacing: -.025em; line-height: 1.25; }
  .preview-area :global(h2) { margin: 32px 0 14px; font-size: 22px; font-weight: 720; letter-spacing: -.02em; line-height: 1.3; }
  .preview-area :global(h3) { margin: 26px 0 10px; font-size: 18px; font-weight: 700; line-height: 1.35; }
  .preview-area :global(h1:first-child), .preview-area :global(h2:first-child), .preview-area :global(h3:first-child) { margin-top: 0; }
  .preview-area :global(p) { margin: 12px 0; }
  .preview-area :global(ul), .preview-area :global(ol) { margin: 12px 0; padding-left: 24px; }
  .preview-area :global(li) { margin: 5px 0; }
  .preview-area :global(blockquote) { margin: 18px 0; padding: 14px 18px; border-left: 3px solid var(--accent-color); border-radius: 0 10px 10px 0; color: var(--text-secondary); background: var(--accent-faint); font-size: 14.5px; }
  .preview-area :global(code) { padding: 2px 6px; border-radius: 5px; color: var(--accent-strong); background: var(--accent-soft); font-family: var(--font-mono); font-size: .875em; }
  .preview-area :global(pre) { margin: 18px 0; padding: 16px; overflow-x: auto; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-subtle); }
  .preview-area :global(pre code) { padding: 0; color: var(--text-primary); background: transparent; }
  .preview-area :global(a) { color: var(--accent-color); text-decoration: underline; text-underline-offset: 2px; text-decoration-thickness: 1px; }
  .preview-area :global(img) { display: block; max-width: 100%; height: auto; margin: 18px 0; border-radius: 12px; outline: 1px solid rgba(0, 0, 0, 0.1); }
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
    .reader-header { gap: 8px; padding: 10px 14px; }
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
    grid-template-columns: 1.5fr 1fr;
    gap: 20px;
    width: min(1040px, 100%);
    margin: 0 auto;
    min-height: calc(100vh - 140px);
    padding: 20px 0;
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
    gap: 8px;
    min-height: 420px;
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

  @media (max-width: 1280px) {
    .study-layout { grid-template-columns: 1fr; }
  }

  /* Evidence QA panel */
  .qa-panel {
    max-width: 700px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .qa-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .qa-head h3 { margin: 0; font-size: 18px; }
  .qa-hint { color: var(--text-tertiary); font-size: 13px; margin: -8px 0 0; }
  .qa-history {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-height: 460px;
    overflow-y: auto;
    padding: 4px;
  }
  .qa-message { padding: 10px 13px; border-radius: 10px; font-size: 14px; line-height: 1.6; }
  .qa-message.user { background: var(--accent-soft); align-self: flex-end; max-width: 80%; }
  .qa-message.assistant { background: var(--bg-card); align-self: flex-start; max-width: 90%; }
  .qa-message-role { font-size: 11px; color: var(--text-tertiary); margin-bottom: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .qa-message-content { white-space: pre-wrap; word-break: break-word; }
  .qa-message-content.typing { display: flex; align-items: center; gap: 6px; color: var(--text-tertiary); }
  .qa-error { padding: 8px 12px; border-radius: 8px; background: var(--danger-soft); color: var(--danger); font-size: 12px; display: flex; align-items: flex-start; gap: 6px; }
  .qa-input-row { display: flex; gap: 6px; }
  .qa-input-row input { flex: 1; padding: 9px 13px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text); font-size: 14px; }
  .qa-input-row input:focus { outline: none; border-color: var(--accent); }
  .qa-input-row input:disabled { opacity: 0.6; }

  /* Evidence panel */
  .evidence-panel {
    width: min(760px, 100%);
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 0;
    min-height: calc(100vh - 140px);
    padding: 20px 0;
  }

  .evidence-panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 12px;
    padding: 0 4px;
  }

  .evidence-panel-head h3 {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
  }

  .evidence-count {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 99px;
    background: var(--accent-soft);
    color: var(--accent-color);
    font-size: 11px;
    font-weight: 700;
  }

  .evidence-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    min-height: 160px;
    color: var(--text-tertiary);
    font-size: 13px;
  }

  .evidence-empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    min-height: 160px;
    color: var(--text-tertiary);
    font-size: 13px;
    text-align: center;
  }</style>
