# Changelog

## Unreleased
- **Foundation Complete** — all 8 gates verified 2026-07-15:
  - Independent security review (Tencent HanaAgent, non-author, 25 findings closed)
  - Rust conformance (`cargo check --features compiler_v3` 0 errors, 11/11 tests pass)
  - Python/Rust cross-language interop (28 fixtures byte-identical)
  - Semantic quality baseline (106 cases, precision 0.991 / recall 0.991)
- Added v0.2 ExchangeBundle storage layer (`BundleStore` trait + `FileBundleStore`)
- Wired `compiler_v3` persistence into compile pipeline (post-compile v0.2 bundle save)
- Added `list_v02_versions` Tauri command and TypeScript bindings
- Built EvidenceViewer Svelte component for evidence citation navigation
- Fixed 7 Svelte a11y warnings across KnowledgeTree, Notes, and Process pages
- Expanded semantic corpus from 2 to 106 cases via batch subtitle processing
- Created subtitle-to-annotation conversion tool for streamlined Ground Truth creation
- Updated Foundation status documents to reflect gate closure

- Hardened the Foundation Candidate to `0.2.0-rc.2`: verified ExecutionPlan and Evidence review digests, introduced VN-C14N-1 golden vectors and signature domain separation, and added strict JSON parser resource limits.
- Expanded Red Team coverage to 23 findings and 20 exchange-bundle adversarial fixtures.

### Specification v0.2 Foundation Candidate

- Merged all 19 Red Team findings into 28 new normative requirements; the active specification now contains 183 requirements.
- Promoted the machine contract to `0.2.0-rc.1` with 13 JSON Schemas, signed fixtures and 17 adversarial regressions.
- Added conservative two-stage v0.1-to-v0.2 migration analysis and materialization, including immutable identities, rights downgrade, content binding, re-signing and migration reports.
- Added three valid migration fixtures and one expected blocked migration.
- Added a structural quality benchmark; all three foundation bundles score 1.0 for Anchor resolution, supported-Claim evidence, Artifact locators and SourceRange identity.
- Added experimental Rust `compiler_v3` IR, validation, signature verification and conformance tests behind an off-by-default Cargo feature.
- Added the independent security review packet and explicit external-review gate.

### Specification v0.1 alpha

- Repositioned the platform as an open learning-material compiler with Video Notes as the reference application.
- Added four normative volumes covering Architecture, Knowledge IR, Compiler and Evidence.
- Added a normative glossary, invariants, error model, compatibility and security model.
- Added 155 traceable `SPEC-*` requirements.
- Added ten Draft 2020-12 JSON Schemas and seven valid/invalid exchange fixtures.
- Added cross-object conformance validation for identity, Anchor, Claim, Gap, Capsule and Artifact lineage.
- Added Mermaid architecture, IR, compiler, job and lineage diagrams.
- Added machine-readable task and requirement traceability indexes.
- Started the Spec v0.2 adversarial review with 18 findings (1 Critical, 11 High, 6 Medium).
- Added a v0.2 draft Schema bundle and 13 adversarial regression fixtures; 15 findings are regression-ready.

### Repository hygiene

- Removed committed frontend build output and obsolete local-tool files.
- Added repository policy, security, support, license, editor, CI, and contribution metadata.
- Separated current documentation, implementation status, release reports, and generated RFC bundles.
- Removed obsolete Whisper/Tesseract-era architecture and PRD documents; retained only normative RFC history.
- Added an automated repository hygiene gate and linked it into release verification.
- Replaced hard-coded release verification with the canonical `VERSION` file.
- Added deterministic source archive generation with normalized metadata and SHA-256 output.

## 2.1.0 — 2026-07-12

### Product completion

- Connected normalized 16 kHz mono WAV audio to capability-aware cloud compilation.
- Replaced calculated frame times with FFmpeg presentation timestamps (`showinfo`).
- Added OpenAI-compatible, OpenAI Responses, Gemini, and Anthropic provider adapters.
- Added explicit provider audio capability and per-request frame limits.
- Added bounded multimodal request construction; selected frames are no longer silently dropped.
- Added strict model-output repair, schema validation, anchor validation, and output-size limits.
- Added marked local visual drafts and per-chunk cloud failure fallback.
- Added `HybridFallback` mode and user-visible compilation warnings.
- Fixed `Evidence.version` propagation and added IR schema version 2.
- Added immutable, atomic file storage with version reservations and rebuildable indexes.
- Added SHA-256 source identity and version replay metadata in rendered Markdown.
- Fixed the Notes version selector to replay the real Capsule rather than hashing the note path.
- Added task pause/cancel checkpoints and cancellable `yt-dlp` downloads.
- Added supported public URL validation, per-job download isolation, cookie-file support, and cleanup.
- Added media safety limits: 8 GiB source, 2-hour duration, bounded frame payload, and bounded PCM WAV.
- Removed Whisper/Tesseract runtime expectations from the current compiler and packaging scripts.
- Updated the browser development mock for compile, replay, audio capability, and current runtime components.

### User interface

- Added provider audio-input and maximum-frame capability controls.
- Added native-engine status and active-task count in the sidebar.
- Corrected local-draft, diagnostics, and runtime wording.
- Fixed Svelte typing issues in the knowledge tree and settings state.

### Verification

- Added `scripts/verify_source_release.py`.
- Added `scripts/media_pipeline_smoke_test.py`.
- Frontend production build passes.
- `svelte-check` reports 0 errors and 0 warnings.
- Offline production dependency audit reports 0 known vulnerabilities.
- FFmpeg smoke media produces six monotonically increasing physical PTS anchors and 16 kHz mono WAV.

### RFC repository

- Added the complete long-term RFC suite under `rfcs/`.
- Added governance, project charter, architecture, media, model runtime, IR, storage, local-first runtime, job lifecycle, security, observability, release, and conformance RFCs.
- Preserved RFC-0001 v2.1 as a historical input under `rfcs/legacy/`.
- Added a machine-readable RFC index and reusable proposal template.

### Compatibility

- Existing Capsule files remain readable through `serde(default)` fields.
- Existing note Markdown remains readable; only newly rendered notes contain replay frontmatter.
- Runtime settings remain file-compatible. Old provider type aliases are normalized at use time.
### Spec v0.2 Red Team dev.2

- Completed a 19-finding adversarial catalog covering lineage, identity, rights, provider drift, external dependencies and exchange authenticity.
- Added 12 draft Schemas, 3 signed valid bundles and 17 adversarial fixtures.
- Added Ed25519 exchange verification and source-aware canonical range validation.

### v0.1.0-alpha.1 metadata erratum

- Corrected the working status count from four to five invalid v0.1 fixtures.
- Preserved the original Alpha archive and documented the correction in `spec/ERRATA-v0.1.0-alpha.1.md`.

