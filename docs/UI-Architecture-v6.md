# Video Notes AI UI v6 Architecture

## Design goal

UI v6 replaces the previous dashboard-heavy visual language with a calmer desktop productivity workspace. The interface is optimized for long-running local media jobs, dense task inspection, note reading, and configuration management rather than marketing-style cards.

## Shell

- **216 px navigation rail** with a single prominent “Create note” action.
- **62 px application bar** with page context, engine health, active task count, and direct note search entry.
- **Scrollable content canvas** with consistent 24–26 px page gutters.
- Notes retain a full-height split workspace below the application bar.

## Design system

- Neutral app canvas with white/elevated content surfaces.
- Violet primary accent used only for selection, primary actions, and progress.
- Compact 8–11 px supporting text and 19–25 px page/document headings.
- Three surface levels: app background, sidebar/subtle surface, card/elevated surface.
- Shared button, field, switch, status, progress, modal, toast, and empty-state primitives.
- Complete persisted light/dark appearance.

## Panel treatment

### Create notes
- Focused two-column creation canvas.
- Segmented Whisper model selector.
- Clear media drop target, enhancement controls, fixed submission summary, and sticky task overview.

### Task center
- Compact operational metrics.
- Dense task table with progress, status, timing, recovery controls, and side inspector.

### Notes library
- 304 px library column and distraction-reduced reader.
- Markdown reading/editing, local path metadata, and document actions remain intact.

### Collections
- Searchable collection navigator and structured detail workspace.
- Batch progress, media rows, dialogs, and empty states use the same system primitives.

### Settings
- Narrow category navigation with calm content panes.
- Provider editor retains secure credential handling while using a clearer modal hierarchy.

## Compatibility

UI v6 changes only the desktop presentation layer and Windows build preflight. Python RPC names, database schema, task snapshots, provider profiles, notes, and collections are unchanged.
