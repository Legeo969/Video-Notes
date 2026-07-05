# Video Notes AI UI v5 — Product Interface Architecture

## Goal

UI v5 replaces the page-by-page prototype styling with a single product-grade desktop design system. The refactor keeps the existing Tauri/Python RPC contracts intact and focuses on clarity, consistency, density, accessibility, and long-running task workflows.

## Design system

- Unified semantic color tokens for application backgrounds, surfaces, borders, text, accent, success, warning, danger, and information states.
- Shared spacing, radius, typography, shadow, focus, form, modal, toast, progress, empty-state, and status-pill primitives.
- Light and dark appearances with persisted local preference.
- Dense desktop layouts optimized for 1200×800 and larger while retaining a 960 px minimum safety width.
- SVG icon system with no external icon runtime dependency.

## Application shell

- 236 px dark workspace sidebar with clear navigation hierarchy.
- Live processing-engine status and active-task count.
- Persistent light/dark appearance toggle.
- Sticky, compact engine error banner that preserves access to the rest of the app.

## Page architecture

### Create Notes

- Three-step workflow header.
- Dedicated local-media picker and URL/path field.
- Visual Whisper model selector with quality indicators.
- OCR and vision enhancement cards.
- Persistent-snapshot and checkpoint visibility.
- Live current-task panel and workspace overview.

### Task Center

- Summary metrics for all, running, paused, attention-needed, and completed tasks.
- Searchable, filterable task table.
- Inline progress, stage, status, and task actions.
- Detail panel for checkpoints, attempts, timestamps, artifacts, and errors.

### Notes Library

- Full-height two-pane reading workspace.
- Searchable note list with dates and local paths.
- Reading/source modes, Markdown editing, save, copy, reveal, open, and delete actions.
- Rich Markdown document styling and metadata summary.

### Collections

- Searchable collection navigation.
- Collection metrics and completion progress.
- Media item list with per-item status and progress.
- Productized create, add-items, and delete dialogs.
- Native folder-picker integration in Tauri mode.

### Settings

- Category navigation for general/transcription, AI providers, templates, and diagnostics.
- Productized provider cards and secure provider editor.
- Whisper model cards and local model discovery.
- Template selection cards.
- Structured environment-health results and diagnostic bundle export.
- Floating unsaved-changes action bar.

## Shared components

- `Icon.svelte`
- `PageHeader.svelte`
- `EmptyState.svelte`
- `StatusPill.svelte`

## Behavioral compatibility

No engine RPC methods were renamed or removed. Existing job, note, collection, provider, template, and diagnostics contracts remain compatible with the v4.2.6 backend baseline.
