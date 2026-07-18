# VN-GOV-002 — Task Completion Report

## Summary

Restored truthful, deterministic release gates. Hygiene and source archives now inspect tracked release content rather than local build caches, source contracts match the implemented playback/media/provider behavior, the frontend no longer bundles oversized font families, Rust builds and tests on the declared 1.88 MSRV, all-target/all-feature Clippy is warning-free, and status documents report current authoritative counts.

## Files changed

- `README.md`, `spec/STATUS.yaml`, `docs/FOUNDATION-STATUS-v0.2.0-rc.3.md`
- `docs/reviews/agent-baseline-report.md`
- `scripts/check_repository_hygiene.py`, `scripts/create_source_archive.py`, `scripts/verify_source_release.py`, `scripts/verify_cross_language_interop.py`
- `desktop/package.json`, `desktop/package-lock.json`, `desktop/src/main.ts`, `desktop/src/styles/global.css`
- `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/Cargo.lock`, `desktop/src-tauri/src/main.rs`
- Rust files receiving warning-only, behavior-preserving Clippy/MSRV corrections under the task allowlist
- `tasks/spec-v0.2/vn-gov-002.json`, `tasks/index.json`

## Specification requirements addressed

- `SPEC-COMPILER-033`: reproducible versioned behavior and validation evidence.
- `SPEC-COMPILER-039`: deterministic failure visibility and truthful release checks.

## Commands executed and results

- All 10 Python hygiene/spec/security/migration/quality/media/interoperability commands — passed.
- `npm --prefix desktop ci`, `npm --prefix desktop run verify`, `npm --prefix desktop audit --omit=dev` — passed; 0 vulnerabilities.
- `cargo fmt --check` — passed.
- Default and `compiler_v3` Cargo check/test — passed.
- `cargo clippy --locked --all-targets --all-features -- -D warnings` — passed with 0 warnings.
- Rust 1.88 all-target/all-feature check and test — passed.
- `cargo audit --no-fetch --file Cargo.lock` against the 2026-07-18 RustSec database — 0 vulnerabilities.
- `cargo build --release --locked` — passed.
- `scripts/build_windows_release.ps1` — frontend, Rust application, and NSIS installer build passed.

## Security impact

Tracked generated artifacts, oversized files, unsafe links, and secret patterns remain release failures. Local untracked `node_modules`, `dist`, `target`, and caches no longer create false release failures. Cross-language verification now runs reliably on Windows GBK terminals by explicitly emitting UTF-8.

## Compatibility and migration impact

The lockfile selects dependencies compatible with Rust 1.88. The previous Rust 1.80 resolution was rejected because it forced multiple dependencies onto versions covered by active RustSec denial-of-service advisories. Node 20/22 remains the supported range; Node 24 emits the expected engine warning but passed verification locally. System font stacks replace bundled Noto/LXGW payloads without changing saved data. No persistent-data migration is required.

## Remaining risks

Only 35 files are currently tracked in this dirty local worktree; hygiene intentionally validates the Git release set, while many source files remain pre-existing untracked user content. A release commit must intentionally stage the complete source set before archive generation.

## Rollback

Revert the governance scripts, manifests, lockfiles, font imports, status documents, and warning fixes together. Regenerate dependencies with the declared Node and Rust versions, then rerun the full matrix before publishing.
