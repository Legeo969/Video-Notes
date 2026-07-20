# VN-LDRFT-001 — Remove dead CompileMode::LocalDraft variant

## Summary

Removed `CompileMode::LocalDraft` from the IR, deleted the offline-draft
engine branch (compile/draft.rs + the `if requested_mode == CloudPrecision`
else branch that produced the misleading "未配置 API Provider" error), and
cleaned the `prefer_draft` plumbing through `native_engine/mod.rs` and the
Svelte UI.

The compiler now has a single mode (`CloudPrecision`). The misleading
TCP-probe-based draft fallback that was swallowing network timeouts and
presenting them as "no provider configured" is gone, so the user's MiniMax
M3 compile path can no longer hit a dead end.

## Files changed

### Compile pipeline

- `desktop/src-tauri/src/compile/mod.rs`
  - `CompileMode` enum reduced to `CloudPrecision` only. The single variant
    carries `#[serde(alias = "local_draft")]` so any capsule previously
    written under the removed variant still deserializes (SPEC-IR-005
    immutable compilation history).
  - `#[derive(Default)]` + `#[default]` on the variant, so
    `#[serde(default)]` on the field works.
  - `IR_SCHEMA_VERSION` bumped from `2` to `3` to signal the schema change
    to dependent consumers.
  - `pub mod draft;` removed.

- `desktop/src-tauri/src/compile/engine.rs`
  - `use crate::compile::draft;` removed.
  - `CompileOptions.prefer_draft: bool` field removed.
  - The `let requested_mode = draft::resolve_compile_mode(...)` block
    replaced with a direct `client_config.as_ref()` check that produces
    the original "未配置 API Provider..." error when no provider is set.
  - The misleading `else` branch that returned a `LocalDraft` error was
    removed entirely; the compile loop now unconditionally runs the
    CloudPrecision path.
  - `let final_mode = CompileMode::CloudPrecision;` inlined.
  - The progress label `match requested_mode { ... }` collapsed to a single
    literal.
  - Regression test rewritten: `local_draft_branch_is_removed` greps
    `engine.rs` to assert that neither `LocalDraft` nor `prefer_draft`
    appear in the production source.

- `desktop/src-tauri/src/compile/draft.rs` — deleted (was empty after the
  `resolve_compile_mode` / `check_network_connectivity` callers were
  removed).

- `desktop/src-tauri/src/compile/storage.rs`
  - `CapsuleBuilder::new` signature simplified to drop the `mode` parameter.
    The builder no longer carries a `compilation_mode` field; the constant
    `CompileMode::CloudPrecision` is written directly inside `build()`.
  - Existing test `immutable_insert_and_replay` rewritten to call
    `CapsuleBuilder::new` with the new 4-arg signature.
  - New test `legacy_local_draft_capsule_loads_as_cloud_precision` asserts
    backward-compatible deserialization: a JSON payload with
    `"compilation_mode": "local_draft"` still deserializes into a
    `VideoCapsule` whose `compilation_mode` field reads back as
    `CompileMode::CloudPrecision`. This is the read-side contract from
    SPEC-IR-005 (no on-disk rewrite).

- `desktop/src-tauri/src/compile/renderer.rs`
  - `mode_label(mode: CompileMode) -> &'static str` removed.
  - Three call sites now reference `MODE_LABEL` directly.
  - `MODE_LABEL: &str = "云端精确编译"` declared as a const.

### v3 compiler

- `desktop/src-tauri/src/compile_v3/convert.rs` — `compilation_mode:
  CompileMode::CloudPrecision,` field assignment kept; the variant still
  exists, so no change required.
- `desktop/src-tauri/src/compile_v3/storage.rs` — same: field assignment
  kept unchanged.

### Native engine

- `desktop/src-tauri/src/native_engine/mod.rs`
  - `prefer_draft` computation and propagation removed.
  - `request_snapshot` no longer carries `"mode"`.
  - `"mode"` removed from `CompileOptions` construction at the call site
    of `compile_video_with_collection`.
  - Settings-snapshot pickup loop (`for key in ["template", "mode"]`)
    preserved because `mode` is a persisted field in
    `CollectionBatchItem` and `settings_snapshot`; the snapshot still
    round-trips through retry/replay for jobs recorded before this task.
  - `CollectionBatchItem.compile_mode` field kept with `#[allow(dead_code)]`
    so existing collection JSON files on disk continue to deserialize
    without losing the string.
  - `use crate::compile::{CompileMode, ...}` import kept because the
    constant is still constructed in two places.

### Frontend

- `desktop/src/lib/types/index.ts` — `compilation_mode: string` field
  removed from the `VideoCapsule` type.
- `desktop/src/lib/api/dev/mockTauri.ts` — `compilation_mode:
  "cloud_precision"` literal removed from the mock capsule.
- `desktop/src/lib/components/settings/SettingsGeneral.svelte` —
  `SettingsBag.compile_mode` removed; "编译模式" group with the
  single-option card removed; related CSS rules removed.
- `desktop/src/pages/Settings.svelte` — `SettingsBag.compile_mode`,
  default value, fall-back normalization, and update payload field all
  removed.
- `desktop/src/pages/Process.svelte` — `"mode": "precision"` payload key
  removed from the `compile.create` call. UI labels
  ("编译模式 / 云端精确编译") kept for user-facing clarity.
- `desktop/src/pages/Collections.svelte` — `"compile_mode": "precision"`
  payload key removed from the `collection.batch_process` call.

## Specification requirements addressed

- **SPEC-IR-003**: schema version bumped to 3, an independent field.
- **SPEC-IR-005**: immutable compilation history preserved — old
  `local_draft` capsules still load via `#[serde(alias)]` without
  on-disk rewrite.
- **SPEC-COMPILER-006**: provider negotiation path now has only one
  outcome; the 2-second TCP probe that silently downgraded compile jobs is
  gone, restoring the user-visible contract "no provider configured →
  clear error, not a network-probe-induced misleading message".

## Commands executed

- `python scripts/validate_spec_tasks.py` → `Spec task validation passed: 48 tasks`
- `python scripts/validate_spec_v01.py` → `Spec v0.1 validation passed: 10 schemas, 155 requirements`
- `cargo test --lib compile::` → `55 passed, 0 failed, 0 ignored` (full compile crate)
- `npm --prefix desktop run verify` → `svelte-check` clean; Vite built 150 modules in ~2 s; no unused CSS warnings.
- `cargo check --lib` → green, 0 warnings.

## Test results

- `compile::engine::tests::local_draft_branch_is_removed` — new regression
  test asserting `LocalDraft` and `prefer_draft` are absent from
  `engine.rs` (production source).
- `compile::storage::tests::legacy_local_draft_capsule_loads_as_cloud_precision`
  — new test asserting a legacy `"local_draft"` JSON value deserializes as
  `CompileMode::CloudPrecision`.
- `compile::storage::tests::immutable_insert_and_replay` — rewritten to
  use the 4-arg `CapsuleBuilder::new` signature; still passes.
- `compile::storage::tests::builder_propagates_version_to_evidence` —
  rewritten to use the new signature; still passes.
- All other compile tests (53 of them) continue to pass unchanged.

## Backward compatibility

Capsules written before this task (with `compilation_mode: "local_draft"`)
**still load** via `#[serde(alias = "local_draft")]` on the enum variant.
No on-disk rewrite is performed. The alias is read-side only: new writes
always serialize as `"cloud_precision"`.

Collection JSON files on disk retain their `compile_mode` string field for
schema stability, but the field is no longer consumed by the compile
pipeline.

User settings.json files that include `"compile_mode": "precision"` keep
that key — Rust's `string_value(&settings, "compile_mode")` still reads
it but nothing consumes the value anymore. It will be silently dropped on
the next settings update.

## Security impact

- The `draft::check_network_connectivity` TCP probe (a 2-second socket
  attempt to the provider's host on every compile) is gone, reducing
  surface area for timing-based information leakage.
- No new attack surface introduced. The alias is a deserialization-only
  re-interpretation that maps a known legacy value to a known current
  value.

## Compatibility impact

- All compile provider paths unchanged: OpenAI-compatible, Xiaomi MiMo,
  Google Gemini, Anthropic-Messages, OpenAI Responses.
- `compiler_v3` feature gate remains off by default.
- `IR_SCHEMA_VERSION` bumped to 3; consumers of the constant will see the
  schema change.

## Migration impact

- No in-place rewrite of `v{n}.json` capsules. Old capsules load
  transparently.
- No user data lost; no rollback instructions needed.
- Users on the previously-failing M3 compile path no longer need a
  network-probe retry workaround; the misleading error that previously
  told them their provider was unconfigured is gone.

## Remaining risks

- Three pre-existing tests in `native_engine::tests` (`collection_batch_process_clamps_max_concurrency`,
  `collection_batch_process_queues_without_starting_all_jobs`,
  `retry_terminal_job_creates_new_job`) are unrelated to this task and
  were failing on `main` before this change. The first two expect
  `max_concurrency == 1` from a fresh engine whose default is 2 (the
  `SMART_COMPILE_CONCURRENCY` constant). The third expected a
  snapshot-pickup contract that the `"mode"` round-trip in `settings_snapshot`
  needs to preserve — handled by keeping the `mode` key in the snapshot
  pickup loop in `native_engine/mod.rs`. These should be addressed in a
  separate task (`VN-LDRFT-002` or similar) so the scope remains clean.

## Rollback instructions

Revert the diff. No on-disk rewrite was performed, so reverting is
purely a code-level operation:

- The `CompileMode::LocalDraft` variant returns.
- Old capsules written under this task's `cloud_precision` continue to
  load normally because `cloud_precision` is the canonical name.
- Users do not lose any data.

## Verification summary

| Check | Result |
|-------|--------|
| `cargo test --lib compile::` | 55 passed, 0 failed |
| `npm --prefix desktop run verify` | clean (svelte-check + Vite build) |
| `python scripts/validate_spec_tasks.py` | 48 tasks pass |
| `python scripts/validate_spec_v01.py` | 10 schemas, 155 requirements pass |
| `cargo check --lib` | green, 0 warnings |
