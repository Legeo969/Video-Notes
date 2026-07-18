# VN-SEC-002 — Task Completion Report

## Summary

Hardened native execution, local resource access, runtime component integrity, rendered note text, and Provider credential storage. Untrusted paths and component names are containment-checked, shell interpretation is removed, executable payloads are hash-verified both at installation and before use, and Provider secrets are stored in Windows Credential Manager.

## Files changed

- `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/Cargo.lock`
- `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/capabilities/default.json`
- `desktop/src-tauri/src/main.rs`
- `desktop/src-tauri/src/native_engine/mod.rs`, `desktop/src-tauri/src/native_engine/tests.rs`
- `desktop/src/pages/Notes.svelte`
- `desktop/src/lib/components/study/KnowledgeTree.svelte`
- `runtime/manifests/*.json`
- `tasks/spec-v0.2/vn-sec-002.json`, `tasks/index.json`

## Specification requirements addressed

- `SPEC-ARCH-013`: native and WebView trust boundaries remain explicit.
- `SPEC-COMPILER-010`: untrusted local/remote inputs are validated and resource-bounded.
- `SPEC-COMPILER-039`: runtime behavior is deterministic and fails closed on integrity errors.

## Commands executed and results

- `cargo test --locked` — 102 passed, 0 failed, 1 environment-gated integration ignored.
- `cargo test --locked --features compiler_v3` — 110 unit tests plus 11 conformance/runner tests passed; 1 environment-gated integration ignored.
- `cargo clippy --locked --all-targets --all-features -- -D warnings` — passed.
- `npm --prefix desktop run verify` — Svelte 0 errors/0 warnings; production build passed.
- Full Python specification, security, migration, quality, media, and interoperability matrix — passed.

## Security impact

- Prevents directory traversal during component removal and local-image resolution.
- Prevents Markdown title markup from becoming executable WebView HTML.
- Removes `cmd.exe`/shell interpretation from path and URL opening.
- Requires pinned SHA-256 values and verifies installed executable payloads before launch.
- Restricts asset protocol scope and disables production devtools.
- Removes persisted plaintext Provider keys and migrates them into the OS credential vault.
- Removes the unused native clipboard plugin and its capability; note copying continues through the WebView Clipboard API.
- Retains SSRF protections for local/private IPv4 and IPv6 ranges using portable prefix checks.

## Compatibility and migration impact

Existing Provider profiles remain readable. A legacy plaintext `api_key` is migrated to the Windows credential vault and removed from settings on the next settings update/read path. No Capsule, Evidence, or public IR migration is required.

## Remaining risks

The real mpv launch integration remains environment-gated because it requires an installed player and user-provided media path. Command construction, source-path preservation, named-pipe seek, and route behavior are covered by default tests.

## Rollback

Revert the files listed above as one task. If rolling back credential migration, export required Provider keys from the OS vault first; the application intentionally does not reconstruct plaintext settings.
