# VN-AVIDEO-001 — Anthropic Messages video upload

## Summary

Enables automatic video-notes compilation against Anthropic-Messages-compatible
endpoints that accept base64 video (e.g. MiniMax M3).

Implementation unit tests and frontend verification passed. The external MiniMax
M3 smoke remains blocked because `MINIMAX_API_KEY` was not set in this environment.

## Wire contract (post-fix)

The MiniMax Token Plan exposes its Anthropic-compatible Messages API at:

- Base URL: `https://api.minimaxi.com/anthropic` (no trailing `/v1`)
- Headers: `x-api-key: <KEY>`, `anthropic-version: 2023-06-01`, `Content-Type: application/json`
- Video content block: `{ "type": "video", "source": { "type": "base64", "media_type": "video/mp4", "data": "<BASE64>" } }`
- Whole request body cap: 64 MB

The compile client constructs request URLs by stripping a trailing `/v1` (if any)
and appending `/v1/messages` (compile path) or `/v1/models` (model discovery).
This matches the Anthropic SDK convention and accepts all three forms:

- `https://api.minimaxi.com/anthropic` (Token Plan form)
- `https://api.minimaxi.com/anthropic/v1` (already-versioned form)
- `https://api.anthropic.com/v1` (vanilla Anthropic form)

## Files changed

- `desktop/src-tauri/src/compile/client.rs`
- `desktop/src-tauri/src/native_engine/mod.rs`
- `desktop/src/lib/components/settings/ProviderFormDialog.svelte`
- `docs/task-reports/VN-AVIDEO-001.md`

## Specification requirements addressed

- SPEC-COMPILER-006 (provider 适配 MUST 包含 negotiation 与资源预检)
- SPEC-COMPILER-018 (Planner MUST 使用 Manifest 中的预算与能力清单)

## Commands executed

- `ffmpeg -version`
- MiniMax network probe: `curl -sS --max-time 10 -o /dev/null -w "%{http_code}" https://api.minimax.io/anthropic/v1/messages -H "x-api-key: test" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" -d '{}'`
- `python scripts/validate_spec_tasks.py`
- `ffmpeg -y -f lavfi -i "testsrc=size=320x240:rate=5:duration=2" -f lavfi -i "sine=frequency=440:duration=2" -c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 32 -c:a aac -b:a 64k /tmp/m3-smoke.mp4`
- `ls -l /tmp/m3-smoke.mp4`
- `cargo test --lib compile::client::tests::`
- `npm --prefix desktop run verify`
- `python scripts/validate_spec_v01.py`

The ignored `m3_smoke_compile_video` command was not executed because
`MINIMAX_API_KEY` was unset.

## Test results

- Prerequisite probe: FFmpeg `8.1.1-essentials_build-www.gyan.dev` was available
  and exited with status 0.
- Prerequisite probe: `MINIMAX_API_KEY` status was `UNSET`.
- Network probe: `api.minimax.io` returned HTTP `401` with curl exit status 0,
  confirming DNS/TLS/network reachability with the intentionally invalid test key.
- MP4 generation: passed. `/tmp/m3-smoke.mp4` was created at 24,391 bytes
  (well below 5 MB), with 320x240 H.264 video and AAC audio.
- `python scripts/validate_spec_tasks.py`: passed (`Spec task validation passed: 46 tasks`).
- `cargo test --lib compile::client::tests::`: passed, 12 passed, 0 failed,
  0 ignored, 99 filtered out.
- `npm --prefix desktop run verify`: passed. `svelte-check` reported 0 errors
  and 0 warnings; Vite transformed 149 modules and completed the production build.
- `python scripts/validate_spec_v01.py`: passed (`Spec v0.1 validation passed: 10 schemas, 155 requirements`).
- End-to-end MiniMax M3 request: `BLOCKED: MINIMAX_API_KEY env var not set`.

## UI verification

UI verification was performed by diff inspection rather than an interactive run,
as required for the unattended subagent environment. Inspection of
`.superpowers/sdd/review-52f2e13..837b22c.diff` confirmed:

- `anthropic_messages.supported` changed from `false` to `true`, so the option no
  longer receives the “（暂不可用于自动任务）” suffix.
- The new `{:else if providerForm.provider === "anthropic_messages"}` branch is
  present and explains MiniMax M3 compatibility, explicit opt-in behavior, and
  that native Anthropic Claude does not accept video.
- The checkbox label is updated to “此端点支持视频输入 (Anthropic-style base64 或
  OpenAI-style video_url)”; it remains disabled only for unsupported provider
  types, so it is enabled for `anthropic_messages`.

Provider configuration/save verification was not performed interactively because
Step 1 was explicitly replaced with diff inspection and no real MiniMax API key
was available for Step 2.

## M3 end-to-end smoke

`BLOCKED: MINIMAX_API_KEY env var not set`.

No real M3 request was sent, so the outcome cannot honestly be classified as
SUCCESS, VENDOR-REJECTS-BASE64, or NETWORK-FAILED. The network-only prerequisite
probe reached the MiniMax endpoint and returned HTTP 401 for the test key. The
temporary `m3_smoke_compile_video` test was not added because the required key
was absent, and no such test remains in the source tree.

## Security impact

- Anthropic auth headers are gated on `provider_kind == Anthropic`.
- 64 MB cap enforces the vendor-documented limit.
- No credentials are written to logs or task snapshots.

## Compatibility impact

- OpenAI-compatible, Xiaomi MiMo, Google Gemini, OpenAI Responses paths
  are unchanged.
- Vanilla Anthropic Claude endpoints keep working as text-only providers.

## Migration impact

None. No Capsule or persisted data is affected.

## Remaining risks

- `BLOCKED: MINIMAX_API_KEY env var not set`; MiniMax M3 acceptance of the
  Anthropic-style base64 video block is not verified end to end.
- The provider dialog was verified by diff inspection, not by launching and
  interacting with the desktop application.
- Saving a real “MiniMax M3 Smoke” provider and exercising “测试连接” were skipped
  because interactive UI execution and a real key were unavailable.
- A future run with a real key must execute the ignored smoke test temporarily,
  classify the actual vendor response, and remove the test before committing.

## Rollback instructions

Revert the six implementation commits added by this task (`10c815a`, `5ef5828`,
`8d7b122`, `c145547`, `52f2e13`, and `837b22c`) plus the task-report commit. The
provider form dialog returns to "Anthropic Messages = unsupported for automatic
tasks".
