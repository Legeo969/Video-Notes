# File-Manager Style Notes Library — Design

Date: 2026-07-20
Status: Draft (pending user review)
Owner: video-notes-ai
Task ID: `VN-LIBFM-001`
Plan: `C:\Users\lin10\.claude\plans\enumerated-pondering-badger.md`

## 1. Problem

The current Notes page (`desktop/src/pages/Notes.svelte`) renders the on-disk note tree as a flat, search-only list with a 2-button toolbar. Users cannot organize notes into folders, batch-select, or reorder. The disk layout is already recursive (`<export_dir>/**/*.md` up to depth 8 via `collect_markdown_notes`), but the UI hides that structure.

## 2. Goal

Surface the existing on-disk folder structure as a navigable tree, add per-folder CRUD operations (new folder, rename, delete), enable multi-note batch operations (delete, move), and provide drag-and-drop between folders.

## 3. Non-goals

- Modifying the compile output filename scheme (`<export_dir>/<safe>-v<n>.md`).
- Removing any existing notes.* command.
- Adding a notes-* command that operates outside `effective_note_output_dir` or the legacy `output_dir`.
- Persisting expand/collapse state across navigation (per user requirement).
- View-mode toggle (the 4th icon is reserved but unused this round).

## 4. Architecture

**Backend** (`desktop/src-tauri/src/native_engine/mod.rs`): six new commands plug into `impl NativeEngine { pub fn call }` dispatch at line 392-461:

| Command | Role |
|---|---|
| `notes.tree` | Returns `{ folders: [{path, name}], notes: [{id, title, path, folder, created_at, modified_at}] }`. Folders sorted case-insensitive by name; `folder` on each note = parent dir's relative path (`""` = root). |
| `notes.create_folder` | `{ parent, name }` → creates an empty directory under the validated parent. |
| `notes.move` | `{ id, target_folder }` → moves the .md file and any `<old>/assets/<stem>/` directory; re-registers asset scope; updates `job.note_id` for any persisted job pointing at the old path. |
| `notes.rename_folder` | `{ path, new_name }` → renames the folder and walks descendants to update note paths + job ids. |
| `notes.delete_folder` | `{ path, confirm, delete_assets }` → recursive delete; `confirm: true` mandatory; refuses root. |
| `notes.batch_move` / `notes.batch_delete` | Multi-id variants with rollback (move) and `confirm` gate (delete). |

All path-mutating commands funnel through a new private `validate_path_within_roots(path, roots) -> Result<PathBuf, String>` that canonicalizes and rejects `..`, absolute Windows drive prefixes, symlinks pointing outside the export root, and folder-into-self moves.

`NoteEntry` (`mod.rs:258-265`) gains an optional `modified_at: Option<String>` populated from `fs::metadata().modified()`. Backward compatible — `notes.list` is unchanged.

**Frontend** (`desktop/src/pages/Notes.svelte`):

- New state: `tree`, `multiSelect`, `sortField` / `sortDir`, `selectedIds: Set<number>`, `expanded: Set<string>`. No persistence.
- New recursive component `desktop/src/lib/components/notes/TreeRow.svelte` rendering folder + note rows. Per-row: depth, expanded, selected, checked.
- Four toolbar buttons in fixed order: edit (toggles `multiSelect`), new folder, sort, collapse-all. Uses existing icons plus one new `chevrons-inward` arm in `Icon.svelte` for collapse-all.
- Multi-select mirrors `Tasks.svelte:13, 135-147, 332-367` (Set<id> + checkbox + batch-bar).
- Drag-and-drop: HTML5 with `draggable=true` on note rows (when `!multiSelect`), `dragover` highlight on folder rows, `drop` calls `notes.move`.
- Cache invalidation: new `invalidateNoteCaches(oldId)` helper drops both `vn-qa-cache[oldId]` and `vn-graph-cache[oldId]` after a successful move.

## 5. Affected files

| Path | Change |
|---|---|
| `desktop/src-tauri/src/native_engine/mod.rs` | Add 7 new command arms + helpers. |
| `desktop/src-tauri/src/native_engine/tests.rs` | Add ≥1 fixture test per new command. |
| `desktop/src/pages/Notes.svelte` | State, toolbar, list rendering, drag-drop, multi-select, cache invalidation. |
| `desktop/src/lib/components/notes/TreeRow.svelte` | New recursive component. |
| `desktop/src/lib/components/Icon.svelte` | One new `chevrons-inward` arm. |
| `desktop/src/lib/types/index.ts` | Extend `NoteInfo` additively. |
| `desktop/src/lib/api/index.ts` | Gate the dev-only mock transport behind `import.meta.env.DEV` (lazy dynamic import); production bundle excludes the mock entirely. |
| `desktop/src/lib/api/dev/mockTauri.ts` | Dev-only mock Tauri transport — moved out of `lib/api/`. Excluded from production by the `import.meta.env.DEV` gate in `lib/api/index.ts`. |
| `docs/task-reports/VN-LIBFM-001.md` | Task completion report. |

## 6. Invariants preserved

- Existing `notes.list` / `notes.search` / `notes.get` / `notes.update` / `notes.delete` / `notes.open` / `notes.reveal` / `notes.answer` / `notes.video_playback` are byte-identical in behavior.
- Compile output filename scheme unchanged.
- Path-derived `note_id` hash function unchanged (cache invalidation depends on this).
- `allow_note_asset_root` is re-invoked after every move, not weakened.
- `compiler_v3` remains feature-gated.
- New commands refuse to operate outside the configured export roots.

## 7. Acceptance

- All unit tests pass: `cargo test --lib` (existing + ≥7 new).
- `npm --prefix desktop run verify` clean.
- Manual smoke (see plan §Verification) confirms the four-button toolbar, tree rendering, multi-select, drag-drop, and cache invalidation behave as specified.

## 8. Risks

- **Risk**: `note_id` is path-derived, so every move changes the id. Frontend caches (`vn-qa-cache`, `vn-graph-cache`) must be invalidated; backend jobs must be rewritten.
- **Risk**: Asset allowlist refresh could fail silently in headless tests where `app_handle: None`. Tests use `engine_for_root()` (no handle), which short-circuits `allow_note_asset_root`. Production paths verified manually.
- **Rollback**: revert all commits; tree becomes flat list with two-button toolbar; no Capsule data affected.
