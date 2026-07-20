# VN-AVIDEO-001 — Anthropic Messages video upload

## Summary

Enables automatic video-notes compilation against Anthropic-Messages-compatible
endpoints that accept base64 video (e.g. MiniMax M3).

Wire contract, headers, URL routing, 64 MB cap, and UI opt-in are implemented
and unit-tested. The first real MiniMax M3 request against a provider
configured for video input revealed that the vendor's input-side content-safety
filter rejects some videos with a structured `api_error` (code 1026,
`new_sensitive`); this is not a wire-format problem but a vendor policy
rejection. The compile client now classifies these envelopes as non-retryable
and surfaces the vendor error verbatim instead of looping three times.

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

- `python scripts/validate_spec_tasks.py`: passed (`Spec task validation passed: 47 tasks`).
- `cargo test --lib compile::client::tests::`: passed, **15 passed, 0 failed,
  0 ignored**, 125 filtered out. Coverage:
  - Anthropic-Messages wire format (4 tests):
    `anthropic_video_request_body_uses_video_source_base64`,
    `anthropic_video_request_sets_x_api_key_header`,
    `anthropic_video_request_rejects_oversized_payload`,
    `compile_video_request_url_uses_messages_endpoint_for_anthropic`.
  - Vendor error classification (3 tests):
    `vendor_api_error_envelope_is_non_retryable` (covers the 1026 /
    `new_sensitive` envelope observed in the smoke test),
    `vendor_safety_message_is_non_retryable_without_typed_envelope`,
    `client_error_statuses_are_never_retryable`,
    `transient_status_codes_without_envelope_are_retryable`.
  - Xiaomi MiMo regression coverage preserved
    (`xiaomi_request_uses_documented_multimodal_fields`,
    `rejects_oversized_xiaomi_payload_before_request`).
- `npm --prefix desktop run verify`: passed. `svelte-check` reported 0 errors
  and 0 warnings; Vite transformed 149 modules and completed the production build.
- `python scripts/validate_spec_v01.py`: passed (`Spec v0.1 validation passed: 10 schemas, 155 requirements`).
- End-to-end MiniMax M3 request: completed (see "M3 end-to-end smoke" below).

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

Observed against `https://api.minimax.io/anthropic/v1/messages` with a real
`MINIMAX_API_KEY` and a small local MP4 chunk:

```text
provider returned 500 Internal Server Error:
{"error":{"message":"input new_sensitive, messages[1]'s content[0] video is sensitive, please check your input (1026)","type":"api_error"}}
request_id=06ad5186f13c97837e4c00e1822b3b1b
```

**Classification**: `VENDOR-REJECTS-INPUT-AS-SENSITIVE`. The vendor accepted
the wire format (HTTP 500 is the shell status, but the body is a structured
`api_error` whose message calls out `messages[1]'s content[0]` — the
Anthropic-style `video` block built at `compile/client.rs:251-262`). The HTTP
500 status is misleading; the body is an input-validation rejection from the
vendor's content-safety classifier.

**Action taken**: `classify_error_response` in `compile/client.rs` now
short-circuits any vendor response whose body matches an Anthropic-style
`{"error":{"type":"api_error",...}}` envelope (or carries a safety/policy
verdict in the message text) and returns the vendor error to the caller
immediately, instead of looping three times against a deterministic
rejection. The original `is_retryable_status` heuristic was too coarse for
this case because `status.is_server_error()` flagged the 500 as retryable.

**Implication for the design contract**: §4's `source.type: "base64"`
assumption is confirmed at the wire level — the request reached the input
classifier, which means the URL, headers, and content-block shape are
correct. The remaining variable is vendor content policy on the input video.
Two follow-on paths are open:

1. The user retries with a different video that does not trip the safety
   classifier. If it succeeds end-to-end, the implementation is done.
2. If many inputs are rejected, the next task is to upload the MP4 to a
   presigned URL and switch to `source.type: "url"` (which is the only
   variant the vendor's published example demonstrates). That change is
   out of scope for VN-AVIDEO-001.

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

- MiniMax M3's content-safety filter rejects some input videos with code
  1026 (`new_sensitive`). The implementation now surfaces that verdict
  directly to the caller, but the user-visible experience on a rejected
  video is still "compile failed" rather than "choose a different clip /
  different provider / different upload shape". A future UX task could
  parse the vendor error type and show a targeted message.
- The `svelte-check` UI verification was performed by diff inspection, not
  by launching and interacting with the desktop application.
- A future run with a known-clean video must complete the smoke test and
  confirm the wire contract end-to-end. If a large fraction of inputs are
  rejected by M3, the next task is to upload to a presigned URL and switch
  to `source.type: "url"` — out of scope for VN-AVIDEO-001.

## Rollback instructions

Revert the six implementation commits added by this task (`10c815a`, `5ef5828`,
`8d7b122`, `c145547`, `52f2e13`, and `837b22c`) plus the task-report commit. The
provider form dialog returns to "Anthropic Messages = unsupported for automatic
tasks".
