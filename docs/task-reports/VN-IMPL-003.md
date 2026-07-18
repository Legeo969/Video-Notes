# VN-IMPL-003 — Task Completion Report

## Summary

Completed the conservative legacy VideoCapsule-to-v0.2 ExchangeBundle conversion and immutable file-backed bundle storage. Conversion is fallible, retains unsupported legacy knowledge as explicit unsupported claims/gaps, never fabricates physical anchors or rights, and commits validated canonical bytes with mandatory digest metadata and concurrent version allocation.

## Files changed

- `desktop/src-tauri/src/compile_v3/{canonical,convert,ir,storage,trust,validate}.rs`
- `desktop/src-tauri/src/lib.rs`, `desktop/src-tauri/src/main.rs`
- `desktop/src-tauri/src/native_engine/mod.rs`
- `desktop/src-tauri/tests/compiler_v3_conformance.rs`, `desktop/src-tauri/tests/conformance_runner.rs`
- `tasks/spec-v0.2/vn-impl-003.json`, `tasks/index.json`

## Specification requirements addressed

- `SPEC-COMPILER-005`: compilation lineage remains source- and evidence-traceable.
- `SPEC-COMPILER-033`: versions are immutable and replayable.
- `SPEC-EVIDENCE-028`: unsupported or unbound legacy knowledge is explicit rather than fabricated.

## Commands executed and results

- `cargo check --locked --features compiler_v3` — passed.
- `cargo test --locked --features compiler_v3` — 110 unit tests passed, 1 environment-gated integration ignored; 9 conformance and 2 runner tests passed.
- `cargo +1.88.0 test --locked --all-targets --all-features` — passed.
- `python scripts/verify_cross_language_interop.py` — canonical/signature/trust comparison passed across 28 fixtures.
- `python scripts/validate_spec_v02.py` — 14 schemas and 25 adversarial regressions passed.

## Security impact

All conversion entry points return errors on invalid ranges or identifiers. Storage validates structure before persistence, verifies mandatory indexed digests during replay, quarantines missing/tampered metadata by failing closed, and serializes concurrent version allocation. Trust Policy authorization remains external to embedded signature keys.

## Compatibility and migration impact

`compiler_v3` remains off by default. Legacy reading and replay remain available. Conversion writes a new immutable v0.2 version and never overwrites the legacy Capsule. Unknown rights remain private/unknown, and unsupported physical evidence bindings are represented as gaps.

## Remaining risks

The feature is still experimental and must not become the default until the separate stabilization/integration task is complete.

## Rollback

Disable the `compiler_v3` feature and revert the listed files. Existing legacy Capsules remain untouched; newly written v0.2 versions can be removed independently after verifying no consumer depends on them.
