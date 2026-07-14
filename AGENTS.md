# AGENTS.md

## 1. Project identity

This repository implements the **Video Notes Learning Material Compiler**.

It is not merely a video summarization application.

The system compiles multimodal learning materials into evidence-grounded, versioned, reviewable, and reusable knowledge artifacts.

The canonical knowledge lineage is:

```text
LearningMaterial
→ SourceRevision
→ SourceRange / MediaAnchor
→ Evidence
→ Claim / Concept
→ Capsule
→ Artifact
```

Every derived knowledge object must remain traceable to its originating source material.

---

## 2. Authority order

When documents, schemas, tests, and implementation disagree, use the following authority order:

1. Project Charter
2. Accepted RFCs
3. Current versioned Specification
4. JSON Schema and OpenAPI contracts
5. Conformance fixtures and tests
6. Task JSON
7. Reference implementation
8. Examples and explanatory documentation

Existing code does not automatically override the specification.

Examples are non-normative unless explicitly marked otherwise.

---

## 3. Required reading

Before modifying the repository, read:

* `README.md`
* `CONTRIBUTING.md`
* `SECURITY.md`
* `spec/README.md`
* `spec-manifest.yaml`
* `tasks/index.json`
* the current Foundation status document
* the Task JSON assigned to the current change

For security-sensitive work, also read:

* `red-team/v0.2/README.md`
* the current external security review packet
* all findings referenced by the assigned task

---

## 4. Development model

Work is specification-driven.

Every non-trivial change must be associated with a machine-readable Task JSON.

Each task must define:

* task identifier;
* specification references;
* dependencies;
* allowed paths;
* forbidden changes;
* invariants;
* acceptance tests;
* security requirements;
* compatibility requirements.

Do not begin a task whose dependencies are incomplete.

Do not silently expand the task scope.

One branch and one pull request should normally correspond to one task.

---

## 5. Baseline validation

Before making changes, run all repository validation commands available for the current operating system.

The expected baseline includes:

```bash
python scripts/check_repository_hygiene.py
python scripts/verify_source_release.py
python scripts/validate_spec_tasks.py
python scripts/validate_spec_v01.py
python scripts/validate_red_team.py
python scripts/validate_spec_v02.py
python scripts/validate_migration_v01_v02.py
python scripts/validate_quality_benchmark.py
python scripts/media_pipeline_smoke_test.py

npm --prefix desktop ci
npm --prefix desktop run verify

cd desktop/src-tauri
cargo fmt --check
cargo check
cargo test
cargo check --features compiler_v3
cargo test --features compiler_v3
```

If the repository contains renamed or replacement scripts, use the actual repository commands and record the difference.

Do not modify tests, fixtures, schemas, or specifications merely to make a failing baseline pass.

First determine whether the failure is caused by:

* the local environment;
* missing dependencies;
* an implementation defect;
* a test defect;
* a specification contradiction.

---

## 6. Baseline report

Before the first implementation change in a new environment, create or update:

```text
docs/reviews/agent-baseline-report.md
```

Record:

* operating system;
* Rust and Cargo versions;
* Node and npm versions;
* Python version;
* FFmpeg version;
* commands executed;
* command exit codes;
* compiler warnings;
* failed tests;
* missing dependencies;
* canonical JSON interoperability status;
* Ed25519 interoperability status;
* currently blocked tasks.

Do not mix the initial baseline report with unrelated feature changes.

---

## 7. Core invariants

The following invariants must not be weakened without an accepted RFC or specification revision.

### 7.1 Evidence integrity

Evidence represents observable source material.

Evidence must not contain unsupported model conclusions.

A model-generated interpretation belongs in a Claim, Concept, Summary, or Artifact layer.

Evidence marked `verified` must be bound to the reviewed Evidence digest.

Changing Evidence content, anchors, provenance, or review-sensitive fields invalidates the previous review binding.

### 7.2 Source traceability

Claims and Artifacts must retain lineage back to Evidence and source anchors.

Do not create user-visible claims that cannot be traced to source material or an explicit compilation gap.

### 7.3 Physical anchors

Models must not be trusted to generate authoritative physical timestamps.

Physical media locations must be derived from backend-controlled media anchors, such as:

* video presentation timestamps;
* audio window boundaries;
* document page regions;
* text spans;
* structural nodes.

### 7.4 Immutable compilation history

Existing Capsules and source revisions must not be overwritten in place.

A recompilation creates a new version.

Migration must preserve the original data and provide a rollback path.

### 7.5 Partial failure visibility

Do not silently discard failed chunks, missing source ranges, unsupported media, unresolved references, or validation failures.

Represent them using structured diagnostics or compilation gaps.

### 7.6 Security boundary

Input media, imported documents, model output, exchange bundles, remote URLs, and rendered content are untrusted.

Apply resource limits, schema validation, reference validation, trust policy validation, and output sanitization before use.

---

## 8. Trust and signatures

A cryptographically valid signature is not sufficient to establish trust.

Exchange bundle verification must distinguish:

```text
signature_valid
```

from:

```text
signer_authorized
```

Signer authorization must come from an external Trust Policy.

Do not authorize a signer merely because its public key is embedded in the signed package.

Verification must account for:

* `key_id`;
* exact Ed25519 public key;
* signature purpose;
* signature context;
* signing time;
* validity period;
* revocation status;
* canonicalization profile;
* protocol domain separation.

Test private keys must only be used in fixtures and tests.

Never commit production credentials, private signing keys, API keys, access tokens, or user secrets.

---

## 9. Canonical JSON

Security-sensitive digests and signatures must use the project-defined canonical JSON profile.

Do not rely on ordinary serializer output as a canonical representation.

Cross-language implementations must produce identical bytes for the published golden vectors.

When changing canonicalization behavior:

1. create a specification task;
2. update the canonicalization profile;
3. update golden vectors;
4. update all language implementations;
5. add migration and compatibility analysis.

Do not change canonicalization as an incidental refactor.

---

## 10. Resource limits

Untrusted inputs must be subject to explicit limits.

The current security profile includes limits for:

* input byte size;
* maximum JSON depth;
* maximum parsed node count;
* cumulative string size;
* collection lengths;
* media duration;
* media dimensions;
* frame payload;
* audio payload;
* redirects;
* decompressed content.

Do not remove or raise these limits without a task that includes security review and denial-of-service analysis.

---

## 11. Compiler v3

`compiler_v3` is an experimental reference implementation behind a feature gate.

Do not enable it by default until all Foundation completion gates are satisfied.

Changes to `compiler_v3` must preserve:

* current production behavior when the feature is disabled;
* v2.1 legacy read and replay compatibility;
* v0.2 schema conformance;
* signature and Trust Policy validation;
* migration rollback;
* valid fixture acceptance;
* attack fixture rejection.

Do not delete the legacy compatibility path merely because the v3 implementation compiles.

---

## 12. Specification changes

A specification change is required before implementation when a change affects:

* public IR fields;
* object identity;
* hashing or canonicalization;
* storage format;
* Capsule compatibility;
* migration behavior;
* public API;
* signature behavior;
* Trust Policy;
* permission inheritance;
* validation semantics;
* stable error behavior;
* feature stabilization.

Small implementation choices that do not alter observable contracts may use an ADR or code review instead of a new RFC.

Do not modify normative specification text and production implementation in the same task unless the assigned task explicitly allows both.

---

## 13. Forbidden behavior

Agents must not:

* perform an unscoped architecture rewrite;
* introduce a new framework without a task;
* weaken validation to accept invalid fixtures;
* delete or rewrite attack fixtures to make tests pass;
* mark model-generated content as verified;
* fabricate missing Evidence, provenance, rights, or review data;
* silently upgrade permissions during migration;
* overwrite existing Capsules;
* enable `compiler_v3` by default;
* use `panic!`, unchecked `unwrap()`, or equivalent behavior on untrusted input paths;
* invoke another language model to repair security-sensitive JSON;
* expose user media or credentials in logs;
* treat structural conformance scores as semantic accuracy;
* claim tests passed without executing them;
* modify files outside the task’s `allowed_paths`;
* combine unrelated refactoring with a scoped task.

---

## 14. Test discipline

For implementation tasks:

1. identify the relevant specification requirements;
2. reproduce the missing or incorrect behavior;
3. add or confirm a failing test;
4. implement the smallest conforming change;
5. run focused tests;
6. run the full required validation suite;
7. document remaining uncertainty.

For security findings, every fixed Critical or High issue must include a regression test or attack fixture.

A valid exchange bundle must be accepted.

An attack fixture must be rejected for the intended reason, not because of an unrelated parse failure.

---

## 15. Task completion report

Every completed task must create:

```text
docs/task-reports/<TASK-ID>.md
```

The report must contain:

* task identifier;
* summary;
* files changed;
* specification requirements addressed;
* commands executed;
* test results;
* security impact;
* compatibility impact;
* migration impact;
* remaining risks;
* rollback instructions.

Do not report only “completed” or “tests passed.”

Include enough evidence for a reviewer to reproduce the result.

---

## 16. Commit conventions

Use descriptive commits associated with a Task ID.

Recommended format:

```text
type(scope): concise description

Task: VN-XXXX-000
Spec: SPEC-XXX-000, SPEC-YYY-000
Tests: <commands executed>
```

Examples:

```text
test(compiler-v3): establish Rust conformance baseline

Task: VN-IMPL-002
Spec: SPEC-COMPILER-081, SPEC-SECURITY-044
Tests: cargo test --features compiler_v3
```

```text
fix(signature): enforce external signer authorization

Task: VN-SEC-004
Spec: SPEC-SECURITY-071
Tests: python scripts/validate_spec_v02.py
```

---

## 17. Pull request rules

A pull request must:

* correspond to a Task JSON;
* remain within the allowed path scope;
* cite affected specification requirements;
* include tests;
* include a task report;
* describe security and compatibility impact;
* avoid unrelated formatting;
* pass repository validation;
* include migration notes when persistent data is affected.

Do not merge a change merely because generated code appears plausible.

---

## 18. Foundation status

**Foundation Complete** — all 8 gates verified 2026-07-15.

Completed gates:

1. ✅ real Rust `cargo check` and `cargo test` — 0 errors, 11/11 compiler_v3 tests pass.
2. ✅ `compiler_v3` conformance execution — all 25 adversarial fixtures rejected on expected codes.
3. ✅ Python/Rust canonical JSON byte equivalence — 28 fixtures match byte-for-byte.
4. ✅ Python/Rust Ed25519 payload and verification equivalence — golden vectors match.
5. ✅ non-author independent security review — Tencent HanaAgent, all 25 findings closed.
6. ✅ closure of all Critical and High findings — 2 Critical, 17 High explicitly dispositioned.
7. ✅ v0.1 to v0.2 migration verification — 3 materialized, 1 blocked case pass.
8. ✅ initial human-annotated semantic quality benchmark — 106 cases, precision 0.991 / recall 0.991.

Agents must not change the project status backwards or re-open closed gates without maintainer approval.

---

## 19. Recommended implementation order

Unless a Task JSON specifies otherwise, prioritize:

```text
1. Rust build and test baseline
2. compiler_v3 conformance
3. canonical JSON interoperability
4. signature and Trust Policy interoperability
5. v0.1 to v0.2 compatibility and migration
6. compiler_v3 production pipeline integration
7. v0.2 Capsule storage and replay
8. Evidence citation viewer
9. Evidence-grounded question answering
10. additional learning-material source adapters
11. OpenAPI and SDK stabilization
12. human-annotated semantic benchmark
```

Do not prioritize broad UI work, plugin ecosystems, or additional source formats before the Foundation gates are closed.

---

## 20. Stop conditions

Stop the current task and report the issue when:

* the specification contradicts itself;
* the required dependency is incomplete;
* the requested change exceeds allowed paths;
* a stable schema must change;
* migration would lose information;
* a security invariant would be weakened;
* the implementation requires fabricated provenance or rights;
* the test can only pass by deleting or weakening a fixture;
* the local environment cannot execute a required validation step.

Do not conceal or bypass a stop condition.

Create a focused finding, ADR, RFC proposal, or blocking report instead.
