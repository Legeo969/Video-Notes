# VN-BUG-015 — Eliminate runtime component update false positives

## Summary

Runtime component update checks compared the marker's manifest version with the latest GitHub release tag. Those values use unrelated version namespaces for mpv and FFmpeg, and the installer can only install the package pinned by the bundled manifest. The comparison therefore produced false update actions that merely reinstalled the same trusted package.

Update status now compares the installed marker with the digest-pinned manifest bundled in the current application. The response shape is unchanged, but `latest_version` now means the version installable by this application. Runtime version probing also falls back to the verified marker version, so an installed component is no longer rendered as "未安装" when an executable version command cannot be read.

## Files changed

- `desktop/src-tauri/src/native_engine/mod.rs`
- `desktop/src-tauri/src/native_engine/tests.rs`
- `desktop/src/pages/Settings.svelte`
- `tasks/spec-v0.2/vn-bug-015.json`
- `tasks/index.json`
- `docs/task-reports/VN-BUG-015.md`

## Specification requirements addressed

- `SPEC-ARCH-017`: only digest-pinned runtime downloads represented by the trusted bundled manifest are offered for installation.
- `SPEC-ARCH-023`: the UI continues to consume component status through the native engine and cannot supply a URL, version, or digest.

## Reproduction evidence

Two regression assertions failed before the implementation:

- `components_check_updates_uses_bundled_manifest_version`: returned an empty latest version instead of the bundled `2026.07.18` version.
- `components_list_reports_native_manifest_status`: returned `null` instead of the verified marker fallback `1.5.7`.

Both focused tests pass after the implementation.

## Commands executed and results

| Command | Result |
|---|---|
| Ten Python repository validation scripts | Passed; 44 tasks, 14 v0.2 schemas, 25 adversarial regressions, migration, quality, media, and interoperability checks succeeded. |
| `npm run verify` | Passed; `svelte-check` reported 0 errors and 0 warnings, Vite built 149 modules. |
| `cargo fmt --check` | Passed. |
| `cargo check --locked` | Passed. |
| `cargo test --locked` | Passed; 103 tests, 1 environment-gated mpv integration ignored. |
| `cargo check --locked --features compiler_v3` | Passed. |
| `cargo test --locked --features compiler_v3` | Passed; 111 unit tests, 9 conformance tests, and 2 runner tests; 1 environment-gated mpv integration ignored. |
| `node scripts/responsive-audit.mjs --output .build/ui-audit-vn-bug-015-retry --widths 900,1024,1100,1280,1440,1920 --pages settings --settings-tab 插件 --stress --dark` | Passed 6/6 cases with no overlap, clipping, overflow, or undersized controls. |
| `npm run tauri -- build` | Passed; release executable and NSIS installer produced. |
| Installed executable SHA-256 comparison | Passed; installed executable matches the release build byte-for-byte. |

## Security impact

The change removes a live GitHub release-tag lookup from update status. It does not alter download URL resolution during installation, archive/package SHA-256 validation, signature verification, payload digest recording, or component path validation. A dynamic unpinned release is no longer presented as an installable update.

## Compatibility impact

The `components.check_updates` response fields remain unchanged. `latest_version` is now the current application's trusted bundled manifest version instead of an unrelated repository tag. Existing frontends continue to work; labels now describe the action as synchronizing with the current application's bundled version.

## Migration impact

No persisted data migration is required. Existing component markers already contain the manifest version used by the new comparison.

## Remaining risks

If a user deliberately rolls the application back while retaining components installed by a newer application, the versions will be reported as different and the UI will offer synchronization to the rolled-back application's bundled version. The revised wording makes that behavior explicit and avoids claiming it is a globally newer release.

## Rollback instructions

1. Exit Video Notes AI.
2. Restore `video-notes-ai.exe.vn-bug-015.bak-*` under `%LOCALAPPDATA%\Video Notes AI`.
3. No runtime component rollback is required because this task does not modify installed components.
