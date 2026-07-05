# UI v6 Change Manifest

Application version: **1.4.0**

## Added

- `desktop/src/lib/components/Topbar.svelte`
- Context-aware application bar with engine and active-task status.
- Direct navigation from the top search action to the Notes library.
- New neutral/violet light and dark design tokens.
- Cargo mirror compatibility preflight in the Windows release script.

## Reworked

- `desktop/src/App.svelte`
- `desktop/src/lib/components/Sidebar.svelte`
- `desktop/src/lib/components/PageHeader.svelte`
- `desktop/src/styles/global.css`
- All five Svelte page styles: Process, Tasks, Notes, Collections, Settings.

## Version files

- `desktop/package.json`
- `desktop/package-lock.json`
- `desktop/src-tauri/Cargo.toml`
- `desktop/src-tauri/Cargo.lock` application package entry
- `desktop/src-tauri/tauri.conf.json`

## Preserved

- Tauri command names and event translation.
- Python sidecar protocol.
- SQLite migrations and stored user data.
- Task resume/retry behavior.
- Provider API key storage and profile contracts.
