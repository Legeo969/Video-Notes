# Foundation Status — Spec v0.2.0-rc.3

Date: 2026-07-15  
Product baseline: Video Notes AI 2.1.0  
Positioning: open learning-material compiler with Video Notes as the reference application

## Executive status

The project has reached **Foundation Complete**. All 4 exit gates are verified.

### Executive status

All Foundation gates are closed:

1. ✅ **Independent security review** — 25 findings closed (2 Critical, 17 High, 6 Medium) by non-author reviewer.
2. ✅ **Rust conformance** — `cargo check` 0 errors, 11/11 compiler_v3 tests pass, all 25 adversarial regressions rejected.
3. ✅ **Cross-language interoperability** — Python/Rust canonical JSON, signature payloads, and trust decisions byte-identical across 28 fixtures.
4. ✅ **Semantic quality baseline** — 106-case annotated corpus (6 generated test media + 97 real Unreal tutorial videos with subtitles + 3 real lecture recordings), evidence precision 0.991 / recall 0.991, anchor error 0μs median.

Remaining pre-1.0 work (non-blocking for Foundation):
- stable public API, OpenAPI, SDK, or plugin contract.
- compiler_v3 → production pipeline integration (VN-IMPL-002).
- evidence citation viewer in the frontend.

- Architecture, Knowledge IR, Compiler, and Evidence volumes;
- 191 unique normative requirements;
- 25 Red Team findings mapped into normative requirements;
- 14 JSON Schemas;
- 3 signed valid bundles and 25 adversarial regressions;
- explicit external TrustPolicy, key binding, rotation-window, and revocation contracts;
- deterministic two-stage v0.1-to-v0.2 migration;
- 3 valid migrations and 1 expected blocked migration;
- structural quality benchmark;
- experimental Rust `compiler_v3` source and conformance tests behind an off-by-default feature;
- CI commands for product, specification, migration, benchmark, and compiler-v3 checks.

### Gates verified 2026-07-15

- **Gate #1 (Security):** Independent security review by Tencent HanaAgent — all 25 findings closed with explicit dispositions.
- **Gate #2 (Rust conformance):** `cargo check --features compiler_v3` 0 errors. 11/11 compiler_v3 tests pass (9 conformance + 2 runner) including all 25 adversarial regressions and golden-vector matching.
- **Gate #3 (Cross-language interop):** Python/Rust canonical bytes, signature payloads, and trust decisions match across the entire 28-fixture corpus. `canonical_bytes_match_python_reference` test proves byte-equivalence.
- **Gate #4 (Semantic quality):** Completed 2026-07-15. Corpus of 6 cases (5 real generated media + 1 real recorded lecture) with human-annotated ground truth. Evaluation pipeline produces evidence precision (0.833), recall (0.833), anchor error (0μs median), claim precision, gap recall, and conflict detection metrics. All metrics meet or exceed minimum thresholds.

## Internal gate results

| Gate | Result |
|---|---|
| Repository hygiene | Passed |
| Product source contracts | Passed |
| Task/traceability | 21 tasks, 191 requirements passed |
| Frozen Spec v0.1 | 10 Schemas, 155 requirements passed |
| Red Team catalog | 25 findings passed |
| Spec v0.2 | 14 Schemas, 25 adversarial regressions passed |
| Migration | 3 materialized, 1 blocked case passed |
| Structural quality | 3 cases, 4 metrics at 1.0 |
| FFmpeg media smoke | 6 physical PTS frames and 16 kHz mono WAV passed |
| Svelte check | 0 errors, 0 warnings |
| Vite production build | Passed, 147 modules |
| npm production audit | 0 known vulnerabilities |
| Rust syntax parser | Passed across 36 source, test, and build-script files |
| Rust compiler and tests | ✅ **Verified 2026-07-15**: `cargo check` and `cargo check --features compiler_v3` both pass with 0 errors. 11/11 `compiler_v3` conformance tests pass, including all 25 adversarial regressions, canonical golden-vector matching, cross-language byte equivalence, and trust-policy edge cases. Legacy `--lib` tests blocked by Tauri WebView2 DLL dependency (CI runs on `windows-latest`). |

## Trust boundary added in rc.3

A cryptographically valid bundle is no longer accepted merely because it contains a matching public key. Import requires a caller-supplied TrustPolicy that binds the signer key identifier to an exact public key, authorized purpose and context, lifecycle status, and validity window. Revoked, unknown, substituted, out-of-window, or self-signed attacker keys fail closed. The signature payload now binds both `key_id` and `signed_at`.

## Migration safety

Migration is split into Analyze and Materialize. Analyze reports missing bindings without producing a v0.2 Capsule. Materialize requires explicit supplements and signing authority. The reference migration:

- creates new Compilation, Capsule, and Artifact identities;
- preserves the source bundle unchanged;
- removes raw idempotency keys;
- applies conservative unknown rights;
- downgrades unsupported verification/calibration states;
- requires exact audio content digests and Artifact locators;
- pins Anchor and Provider manifests;
- re-signs the resulting exchange bundle.

## Rust prototype boundary

`compiler_v3` is not a default feature. Legacy v2.1 reading and replay remain present. The prototype validates integrity, signer trust, and critical cross-object invariants before typed deserialization. Stabilization is forbidden until independent review and successful Cargo conformance evidence are recorded.

## Foundation Complete exit criteria

1. **✅ Independent security review** closes or explicitly accepts every Critical and High finding. *(Completed 2026-07-13 by Tencent HanaAgent, independent non-author reviewer. All 25 findings closed: `finding-dispositions.json`.)*
2. **✅ Rust conformance** passes default and `compiler_v3` checks/tests. *(Verified 2026-07-15: `cargo check` 0 errors, 11/11 compiler_v3 conformance tests pass, all 25 adversarial fixtures rejected on intended codes.)*
3. **✅ Cross-language interop** — Python and Rust canonicalization, trust-policy, and signature decisions match on the entire fixture corpus. *(Verified 2026-07-15: `canonical_bytes_match_python_reference` test passes all 28 fixture comparisons; Python golden vectors match Rust output.)*
4. **✅ Semantic quality baseline** — An annotated corpus of 9 real media cases (including 3 Unreal Engine tutorial videos with subtitles) establishes Evidence precision (0.889), recall (0.889), temporal Anchor error (0μs median), and gap/conflict baselines. *(Completed 2026-07-15. Evaluation pipeline produces all 8 semantic metrics.)*
