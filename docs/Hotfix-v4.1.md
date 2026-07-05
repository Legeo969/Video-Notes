# Video Notes Product Refactor v4.1 Hotfix

## Scope

This hotfix addresses the Windows-only settings contract failures reported by
`verify_product.ps1` and tightens the Rust/Tauri release gate.

## Root cause

The settings API resolved the per-user file with `expanduser("~")`. On Windows,
Python may prefer `USERPROFILE` over a test's patched `HOME` variable. The tests
therefore read and mutated the developer's real
`%USERPROFILE%\.video-notes-ai\settings.json` instead of the pytest temporary
directory. The three failures were different symptoms of the same isolation
leak:

- expected temporary settings file was not created;
- an existing real API key appeared in the test;
- a real provider named `P` collided with the test provider.

## Changes

- Added `VIDEO_NOTES_SETTINGS_PATH` as the canonical explicit settings-path
  override for tests, portable deployments and managed environments.
- Centralized settings path resolution in `src.config.settings.get_settings_path`.
- Updated settings and diagnostics handlers to use the canonical resolver.
- Made the settings contract fixture set `HOME`, `USERPROFILE` and the explicit
  settings path, so it cannot touch real user secrets on Windows, Linux or macOS.
- Changed `verify_product.ps1` to fail when Cargo is absent unless `-SkipRust`
  is explicitly supplied.
- Added equivalent strict behavior to `verify_product.sh` via `SKIP_RUST=1`.

## Validation

```text
618 passed
52 skipped
4 xfailed
```

The five settings contract tests pass independently. Frontend source files were
not changed by this hotfix; the prior Vite and Svelte validation result remains
applicable, but it should be rerun on the target Windows machine.
