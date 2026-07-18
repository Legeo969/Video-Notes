# VN-BUG-012 — Task Completion Report

## Summary

Made Task Center, Collections, Provider capability routing, media process ownership, and direct local playback deterministic. Completed work is skipped during continuation, retries preserve the original task snapshot and lineage, collection/task state converges on one persisted run, FFmpeg children are bounded and reaped, and evidence timestamps seek one reusable mpv window without transcoding the source for playback.

## Files changed

- `desktop/src-tauri/src/compile/{mod,engine,sampler,client,calibrate,prompt,renderer,storage}.rs`
- `desktop/src-tauri/src/native_engine/mod.rs`, `desktop/src-tauri/src/native_engine/tests.rs`
- `desktop/src/lib/components/settings/ProviderFormDialog.svelte`
- `desktop/src/lib/stores/jobs.ts`, `desktop/src/lib/types/index.ts`
- `desktop/src/pages/Collections.svelte`, `desktop/src/pages/Tasks.svelte`, `desktop/src/pages/Settings.svelte`
- `tasks/spec-v0.2/vn-bug-012.json`, `tasks/index.json`

## Specification requirements addressed

- `SPEC-ARCH-004`, `SPEC-ARCH-006`: state ownership and event propagation are explicit.
- `SPEC-COMPILER-008`, `SPEC-COMPILER-028`: media execution and Provider failures remain visible and bounded.
- `SPEC-COMPILER-033`: retries and persisted outputs are deterministic and immutable.

## Commands executed and results

- Focused collection, retry, terminal-event, Provider capability, mpv IPC, and cancellable-process regressions — passed in the Rust suite.
- `cargo test --locked` — 102 passed, 0 failed, 1 ignored.
- `cargo test --locked --features compiler_v3` — 110 unit tests and 11 conformance/runner tests passed; 1 ignored.
- `npm --prefix desktop run verify` — 0 Svelte errors/warnings; Vite build passed.
- `python scripts/media_pipeline_smoke_test.py` — 6 PTS frames and 16 kHz mono audio passed.

## Security impact

FFmpeg/FFprobe process IDs are registered to the owning task, cancellation reaps the active child, stdout/stderr and segment output are capped, temporary segments are automatically removed, and corrupt persisted job state is quarantined instead of silently overwritten. Provider errors cannot be converted into successful jobs.

## Compatibility and migration impact

Existing jobs without snapshots fall back to recorded input/title and current defaults. New jobs persist non-secret retry snapshots and lineage. Existing collection items reconcile by explicit binding and compatible exact-input fallback; completed items are never reprocessed by continuation. No storage schema migration is required.

## Remaining risks

Network Provider calls can only observe cancellation at request completion/timeout because the current blocking HTTP adapter is not streaming-cancellable. FFmpeg cancellation, which caused the observed process buildup, is immediate and covered by a child-reaping regression.

## Rollback

Revert the listed compiler, native engine, store/type, and UI files together. Preserve existing `native-jobs.json` and collection data; both remain backward-readable.
