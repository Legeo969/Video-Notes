# VN-BUG-014 — Expose the direct playback component in Settings

## Summary

The Settings plugin page previously filtered the backend component list down to download and FFmpeg capabilities, so the bundled `mpv-tools` direct-playback component was invisible. The page now renders every component returned by `components.list`, presents `mpv-tools` first with local-playback and timestamp-seek labels, and offers an explicit reinstall/repair action when an installed component fails integrity validation.

The plugin layout was also verified at narrow and wide desktop widths. Component actions wrap without overlap, controls retain a minimum 40 px height, and the navigation collapses according to the content width remaining after the application sidebar.

## Files changed

- `desktop/src/pages/Settings.svelte`
- `desktop/src/lib/api/mockTauri.ts`
- `desktop/scripts/responsive-audit.mjs`
- `tasks/spec-v0.2/vn-bug-014.json`
- `tasks/index.json`
- `docs/task-reports/VN-BUG-014.md`

## Specification requirements addressed

- `SPEC-ARCH-023`: runtime capabilities exposed by the native component registry remain visible and installable through Settings.
- Component installation and repair still use the existing native `components.install` route.
- No frontend-controlled URL, package digest, or integrity rule was introduced.

## Commands executed and results

| Command | Result |
|---|---|
| `node scripts/responsive-audit.mjs --output .build/ui-audit-plugins --widths 900,1024,1100,1280,1440,1920 --pages settings --settings-tab 插件 --stress` | Passed 6/6 light-theme stress cases with no overflow, overlap, clipping, or undersized interactive controls. |
| `node scripts/responsive-audit.mjs --output .build/ui-audit-plugins-repair --widths 900,1100,1440,1920 --pages settings --settings-tab 插件 --dark` | Passed 4/4 dark-theme repair-state cases; the `mpv-tools` card and repair action were present. |
| `npm run verify` | Passed; `svelte-check` reported 0 errors and 0 warnings, and Vite built 149 modules. |
| `python scripts/check_repository_hygiene.py` | Passed. |
| `python scripts/verify_source_release.py` | Passed; 3 runtime manifests and 11 contracts verified. |
| `python scripts/validate_spec_tasks.py` | Passed with 43 tasks before final status update. |
| `python scripts/validate_spec_v01.py` | Passed; 10 schemas and 155 requirements. |
| `python scripts/validate_red_team.py` | Passed; 25 findings. |
| `python scripts/validate_spec_v02.py` | Passed; 14 schemas and 25 adversarial regressions. |
| `python scripts/validate_migration_v01_v02.py` | Passed; 3 materialized and 1 expected blocked case. |
| `python scripts/validate_quality_benchmark.py` | Passed; 3 structural cases and 4 metrics at 1.0. |
| `python scripts/media_pipeline_smoke_test.py` | Passed; 6-second media, 6 physical PTS frames, and 16 kHz mono audio. |
| `python scripts/verify_cross_language_interop.py` | Passed; 28 fixtures and trust decisions. |
| `cargo fmt --check` | Passed. |
| `cargo check --locked` | Passed. |
| `cargo test --locked` | Passed; 102 tests, 1 environment-gated mpv integration ignored. |
| `cargo check --locked --features compiler_v3` | Passed. |
| `cargo test --locked --features compiler_v3` | Passed; 110 unit tests, 9 conformance tests, and 2 runner tests; 1 environment-gated mpv integration ignored. |
| `npm run tauri -- build` | Passed; release executable and NSIS installer produced. |
| Installed `mpv.exe --version` and marker digest verification | Passed; mpv `v0.41.0-dev-g94335ab87` launched and both payload digests matched the marker. |

## Security impact

No trust or integrity behavior changed. The UI repair action delegates to the native installer, which continues to enforce the bundled manifest URL, archive SHA-256, package structure, and installed-file digests. The local legacy component was repaired only after the pinned package length and SHA-256 were verified and its extracted payload matched the existing files byte-for-byte.

## Compatibility impact

No protocol, storage, manifest, or public API changed. Existing download and FFmpeg cards remain available. Unknown future components returned by the backend are now shown instead of silently hidden.

## Migration impact

No persisted application data migration is required. A legacy local mpv marker without `file_sha256` was replaced by a verified current marker; the prior component directory was retained as a rollback backup.

## Remaining risks

The environment-gated Rust test that launches mpv against a caller-supplied media file remains ignored in the general suite because it requires `VN_TEST_MPV_PATH` and `VN_TEST_MEDIA_PATH`. Command construction, exact timestamp seeking, component discovery, real mpv startup, and local payload integrity were all verified independently.

## Rollback instructions

1. Exit Video Notes AI.
2. Restore the executable backup named `video-notes-ai.exe.vn-bug-014.bak-*` under `%LOCALAPPDATA%\Video Notes AI`.
3. If component rollback is also required, replace `runtime\components\mpv-tools` with `runtime\components\mpv-tools.vn-bug-014.bak-*` from the same application data directory.
