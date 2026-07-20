<script lang="ts">
  import Icon from '../Icon.svelte';
  import Self from './TreeRow.svelte';

  type Folder = { path: string; name: string };
  type TreeNote = {
    id: number;
    title: string;
    path: string;
    folder: string;
    created_at: string;
    modified_at?: string;
  };
  type SortField = 'created_at' | 'modified_at' | 'title' | 'path';
  type SortDir = 'asc' | 'desc';

  let {
    folders = [],
    notes = [],
    depth = 0,
    parentPath = '',
    sortField = 'created_at',
    sortDir = 'desc',
    expanded = new Set<string>(),
    selectedId = null,
    selectedIds = new Set<number>(),
    multiSelect = false,
    onSelectNote,
    onToggleFolder,
    onToggleCheck,
    onToggleSelectAll,
  }: {
    folders?: Folder[];
    notes?: TreeNote[];
    depth?: number;
    parentPath?: string;
    sortField?: SortField;
    sortDir?: SortDir;
    expanded?: Set<string>;
    selectedId?: number | null;
    selectedIds?: Set<number>;
    multiSelect?: boolean;
    onSelectNote?: (id: number) => void;
    onToggleFolder?: (path: string) => void;
    onToggleCheck?: (id: number) => void;
    onToggleSelectAll?: (ids: number[]) => void;
  } = $props();

  function compareCi(a: string, b: string): number {
    return (a ?? '').localeCompare(b ?? '', undefined, { sensitivity: 'base' });
  }

  function dateMs(iso: string): number {
    if (!iso) return 0;
    const t = new Date(iso).getTime();
    return Number.isNaN(t) ? 0 : t;
  }

  function directChildFolders(list: Folder[], parent: string): Folder[] {
    const parentParts = parent === '' ? [] : parent.split(/[\\/]/).filter(Boolean);
    const targetDepth = parentParts.length + 1;
    const out: Folder[] = [];
    for (const f of list) {
      const parts = f.path.split(/[\\/]/).filter(Boolean);
      if (parts.length !== targetDepth) continue;
      let match = true;
      for (let i = 0; i < parentParts.length; i++) {
        if (parts[i] !== parentParts[i]) {
          match = false;
          break;
        }
      }
      if (match) out.push(f);
    }
    out.sort((a, b) => compareCi(a.name, b.name));
    return out;
  }

  function notesInFolder(list: TreeNote[], folder: string): TreeNote[] {
    const out = list.filter((n) => (n.folder ?? '') === folder);
    out.sort((a, b) => {
      let primary = 0;
      if (sortField === 'created_at') primary = dateMs(a.created_at) - dateMs(b.created_at);
      else if (sortField === 'modified_at') primary = dateMs(a.modified_at ?? '') - dateMs(b.modified_at ?? '');
      else if (sortField === 'title') primary = compareCi(a.title, b.title);
      else primary = compareCi(a.path, b.path);
      if (primary !== 0) return sortDir === 'asc' ? primary : -primary;
      const tie = compareCi(a.title, b.title);
      if (tie !== 0) return tie;
      return a.id - b.id;
    });
    return out;
  }

  let childFolders = $derived(directChildFolders(folders, parentPath));
  let notesHere = $derived(notesInFolder(notes, parentPath));
  let isOpen = $derived(expanded.has(parentPath));

  // Collect every note id reachable beneath `parentPath`, including descendants.
  // Used by the root-level "select all visible" checkbox in multi-select mode.
  function collectReachableIds(folderPath: string): number[] {
    const direct = notes.filter((n) => (n.folder ?? '') === folderPath).map((n) => n.id);
    const childFolderPaths = directChildFolders(folders, folderPath).map((f) => f.path);
    for (const childPath of childFolderPaths) {
      for (const id of collectReachableIds(childPath)) direct.push(id);
    }
    return direct;
  }
  let reachableIds = $derived(collectReachableIds(parentPath));
  let allReachableSelected = $derived(
    multiSelect && reachableIds.length > 0 && reachableIds.every((id) => selectedIds.has(id))
  );

  function basename(path: string): string {
    return path.split(/[\\/]/).pop() ?? path;
  }
  function shortDate(iso: string): string {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
    } catch {
      return iso;
    }
  }
  function formatDate(iso: string): string {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return iso;
    }
  }

  function handleNoteClick(id: number) {
    if (multiSelect) {
      const next = new Set(selectedIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      selectedIds = next;
      onSelectNote?.(id);
      return;
    }
    onSelectNote?.(id);
  }

  function countInFolder(folderPath: string): number {
    let n = 0;
    // Use backslash to match `noteDirectory` in Notes.svelte (canonical Windows-style separator).
    const prefix = folderPath === '' ? '' : folderPath + '\\';
    for (const note of notes) {
      const f = note.folder ?? '';
      if (f === folderPath) n += 1;
      else if (prefix !== '' && f.startsWith(prefix)) n += 1;
    }
    return n;
  }
</script>

{#if isOpen}
  {#if multiSelect && parentPath === ''}
    <div class="tree-header" style="--depth: 0">
      <label class="tree-check" title="全选可见笔记">
        <input
          type="checkbox"
          checked={allReachableSelected}
          onchange={() => onToggleSelectAll?.(reachableIds)}
          aria-label="全选可见笔记"
        />
      </label>
      <span class="tree-header-label">全选可见笔记（{reachableIds.length}）</span>
    </div>
  {/if}

  {#each childFolders as folder (folder.path)}
    <div class="tree-folder" style="--depth: {depth}">
      <button
        type="button"
        class="folder-row"
        onclick={() => onToggleFolder?.(folder.path)}
        title={folder.path}
        aria-expanded={expanded.has(folder.path)}
      >
        <span class="folder-chevron">
          {#if expanded.has(folder.path)}
            <Icon name="chevron-down" size={12} />
          {:else}
            <Icon name="chevron-right" size={12} />
          {/if}
        </span>
        <span class="folder-icon"><Icon name="folder" size={14} /></span>
        <span class="folder-name">{folder.name}</span>
        <span class="folder-count">{countInFolder(folder.path)}</span>
      </button>
      {#if expanded.has(folder.path)}
        <Self
          {folders}
          {notes}
          depth={depth + 1}
          parentPath={folder.path}
          {sortField}
          {sortDir}
          {expanded}
          {selectedId}
          {selectedIds}
          {multiSelect}
          {onSelectNote}
          {onToggleFolder}
          {onToggleCheck}
          {onToggleSelectAll}
        />
      {/if}
    </div>
  {/each}

  {#each notesHere as note (note.id)}
    <button
      type="button"
      class="tree-note"
      class:with-check={multiSelect}
      class:selected={selectedId === note.id}
      class:checked={multiSelect && selectedIds.has(note.id)}
      style="--depth: {depth + 1}"
      onclick={() => handleNoteClick(note.id)}
    >
      {#if multiSelect}
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <label
          class="tree-check"
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => e.stopPropagation()}
          title="选择"
          aria-label={`选择 ${note.title}`}
        >
          <input
            type="checkbox"
            checked={selectedIds.has(note.id)}
            onchange={() => onToggleCheck?.(note.id)}
          />
        </label>
      {/if}
      <span class="tree-note-icon"><Icon name="file-text" size={14} /></span>
      <span class="tree-note-copy">
        <strong>{note.title}</strong>
        <small>{basename(note.path)}</small>
        <span><Icon name="calendar" size={10} />{formatDate(note.created_at)}</span>
      </span>
      <span class="tree-note-date">{shortDate(note.modified_at ?? note.created_at)}</span>
    </button>
  {/each}
{/if}

<style>
  .tree-folder {
    margin-bottom: 1px;
  }
  .folder-row {
    display: grid;
    grid-template-columns: 22px 22px minmax(0, 1fr) auto;
    align-items: center;
    gap: 6px;
    width: calc(100% - var(--depth, 0) * 14px);
    margin-left: calc(var(--depth, 0) * 14px);
    padding: 7px 9px;
    border: 0;
    border-radius: 7px;
    color: var(--text-secondary);
    background: transparent;
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
    text-align: left;
    transition: background 0.14s, color 0.14s;
  }
  .folder-row:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }
  .folder-chevron {
    display: grid;
    place-items: center;
    width: 16px;
    height: 16px;
    color: var(--text-tertiary);
  }
  .folder-icon {
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border-radius: 5px;
    color: var(--accent-color);
    background: var(--accent-faint);
  }
  .folder-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .folder-count {
    color: var(--text-tertiary);
    font-size: 10px;
    font-weight: 600;
    background: var(--bg-muted);
    padding: 1px 6px;
    border-radius: 99px;
  }

  .tree-note {
    position: relative;
    display: grid;
    grid-template-columns: 22px minmax(0, 1fr) auto;
    align-items: start;
    gap: 8px;
    width: calc(100% - var(--depth, 0) * 14px);
    margin-left: calc(var(--depth, 0) * 14px);
    margin-bottom: 2px;
    padding: 8px 9px;
    border: 1px solid transparent;
    border-radius: 7px;
    color: var(--text-primary);
    background: transparent;
    cursor: pointer;
    text-align: left;
    transition: background 0.14s, border-color 0.14s;
  }
  .tree-note.with-check {
    grid-template-columns: 22px 22px minmax(0, 1fr) auto;
  }
  .tree-note.checked {
    background: color-mix(in srgb, var(--accent-color) 7%, var(--bg-card));
  }
  .tree-note:hover {
    background: var(--bg-hover);
  }
  .tree-note.selected {
    border-color: color-mix(in srgb, var(--accent-color) 22%, var(--border-color));
    background: var(--accent-faint);
    box-shadow: inset 3px 0 0 var(--accent-color);
  }
  .tree-note-icon {
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border-radius: 5px;
    color: var(--text-secondary);
    background: var(--bg-muted);
  }
  .tree-note.selected .tree-note-icon {
    color: var(--accent-color);
    background: var(--accent-soft);
  }
  .tree-note-copy {
    display: flex;
    min-width: 0;
    flex-direction: column;
  }
  .tree-note-copy strong {
    overflow: hidden;
    font-size: 13px;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tree-note-copy small {
    margin-top: 2px;
    overflow: hidden;
    color: var(--text-tertiary);
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tree-note-copy > span {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-top: 4px;
    color: var(--text-tertiary);
    font-size: 10px;
  }
  .tree-note-date {
    color: var(--text-tertiary);
    font-size: 10px;
  }

  .tree-check {
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    cursor: pointer;
  }
  .tree-check input {
    width: 16px;
    height: 16px;
    cursor: pointer;
    accent-color: var(--accent-color);
  }

  .tree-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 4px 6px 8px;
    padding: 5px 8px;
    border-radius: 6px;
    color: var(--text-tertiary);
    background: var(--bg-subtle);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .tree-header-label {
    flex: 1;
    min-width: 0;
  }
</style>
