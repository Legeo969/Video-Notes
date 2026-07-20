# Anthropic Messages Video Upload — Design

Date: 2026-07-20
Status: Draft (pending user review)
Owner: video-notes-ai
Task ID (proposed): `VN-AVIDEO-001`

## 1. Problem

The desktop app currently classifies the `anthropic_messages` provider
type as **unsupported for automatic video notes tasks**. The provider
configuration dialog displays the warning:

> "当前编译器尚未实现此 API 类型的视频请求适配器，因此不能用于自动视频笔记任务。"

The compile client in `desktop/src-tauri/src/compile/client.rs`
recognises `ProviderKind::Anthropic` but the video request body builder
(`build_video_request_body`) only emits the OpenAI-style
`{type: "video_url", video_url: {...}}` content block. This block is
understood by Xiaomi MiMo and other OpenAI-compatible gateways, but **not**
by MiniMax M3 — a vendor whose `/anthropic/v1/messages` endpoint is
wire-compatible with Anthropic's Messages API and additionally accepts a
video content block.

The user wants MiniMax M3 (and any future Anthropic-Messages-compatible
endpoint that accepts video) to work for automatic video notes tasks.

## 2. Goal

Enable automatic video notes compilation against any
`anthropic_messages` provider whose endpoint accepts a base64-encoded MP4
video inside an Anthropic-style `source` content block, as documented for
MiniMax M3.

## 3. Non-goals

This change does NOT:

- Add video input to the upstream Anthropic Messages API (Anthropic
  itself does not currently support video content blocks).
- Modify the OpenAI-compatible, Xiaomi MiMo, Google Gemini, or OpenAI
  Responses adapters.
- Default-enable the `compiler_v3` feature.
- Add a new provider-type entry in the UI (we reuse `anthropic_messages`
  with the existing per-provider video checkbox).

## 4. Wire contract assumed for MiniMax M3

The MiniMax M3 `/anthropic/v1/messages` endpoint exposes the same
request envelope as Anthropic's Messages API:

- Headers:
  - `x-api-key: <KEY>`
  - `anthropic-version: 2023-06-01`
  - `Content-Type: application/json`
- Request body shape (text + video):
  ```json
  {
    "model": "MiniMax-M3",
    "messages": [
      {
        "role": "user",
        "content": [
          { "type": "text", "text": "..." },
          {
            "type": "video",
            "source": {
              "type": "base64",
              "media_type": "video/mp4",
              "data": "<BASE64>"
            }
          }
        ]
      }
    ],
    "max_tokens": 1000
  }
  ```
- Whole-request body must be ≤ 64 MB (per MiniMax M3 documentation).

**Assumption**: MiniMax M3 accepts `source.type: "base64"` for video
content blocks. The vendor's published example only demonstrates
`source.type: "url"`. The base64 shape follows the Anthropic Messages API
convention for base64 sources and is the only viable option for this
project because the desktop compiler generates MP4 chunks locally and
must not upload them to public URLs. The acceptance test
(`m3_smoke_test` below) validates this assumption end-to-end against the
real endpoint; if M3 rejects base64 the test fails loudly instead of
silently falling back to URL upload.

## 5. Design

### 5.1 Provider dispatch

`ProviderKind` already has an `Anthropic` variant mapped from
`"anthropic_messages"`. No new enum variant is added. The
`compile_chunk_video` path gains a branch in two places:

1. `build_video_request_body` selects the content-block shape based on
   `config.provider_kind`:
   - `ProviderKind::Anthropic` → emit Anthropic-style `video` content
     block with base64 `source`.
   - `ProviderKind::OpenAICompat` / `ProviderKind::XiaomiMiMo` →
     unchanged (OpenAI-style `video_url`).
2. `apply_provider_auth` injects Anthropic headers when
   `provider_kind == Anthropic`:
   - `x-api-key: <KEY>` (trimmed, with `Bearer` prefix stripped if the
     user pasted one).
   - `anthropic-version: 2023-06-01`.
   - No `Authorization` header.
3. `compile_chunk_video` resolves the request URL as
   `<base_url>/messages` when `provider_kind == Anthropic` (the existing
   `OpenAICompat`/`XiaomiMiMo` path already builds `<base>/chat/completions`).

### 5.2 Payload size validation

`validate_video_payload_size` (in `compile/client.rs`) currently enforces
a per-provider hard limit. Add an `Anthropic` arm with a 64 MB cap on
the **whole** request body (not just the data URL), measured after JSON
serialisation. The cap matches MiniMax M3's documented limit and matches
the `MAX_REQUEST_BYTES` ceiling already implied by the Anthropic Messages
convention.

### 5.3 UI changes

`desktop/src/lib/components/settings/ProviderFormDialog.svelte`:

1. Mark `anthropic_messages` with `supported: true` in
   `providerTypeOptions`.
2. Update the option `hint` text from
   `"Anthropic /v1/messages 接口，使用 claude-* 模型。"` to
   `"Anthropic /v1/messages 接口（兼容 MiniMax M3 等扩展支持视频的端点）。"`.
3. Update the `provider-type-warning` copy to drop the "未实现" framing
   and clarify the opt-in nature: instead of saying the adapter is not
   implemented, say "未勾选 'MP4 video 输入' 时，端点不会接收视频；仅当端点支持 video 块时才勾选".
4. Leave the per-provider "此端点支持 MP4 video_url 输入" checkbox
   enabled (it was previously disabled for `anthropic_messages`); update
   its label to be neutral about the wire shape:
   `"此端点支持视频输入 (Anthropic-style base64 或 OpenAI-style video_url)"`.

`desktop/src-tauri/src/native_engine/mod.rs`
(`provider_supports_video_input`): keep returning `false` for
`anthropic_messages`. The user must explicitly tick the checkbox. The
checkbox state is already persisted in provider config, and the compile
client consults `CompileClientConfig.accepts_video` before sending
video, so the conservative default protects users who only use vanilla
Claude.

### 5.4 Files to change

| Path | Change |
|------|--------|
| `desktop/src-tauri/src/compile/client.rs` | Branch `build_video_request_body`, `apply_provider_auth`, request URL construction, `validate_video_payload_size` Anthropic arm. Add 64 MB cap constant. |
| `desktop/src-tauri/src/compile/client.rs` (test module) | Add Anthropic video request body shape test, auth header test, oversize rejection test. |
| `desktop/src/lib/components/settings/ProviderFormDialog.svelte` | Mark `anthropic_messages` supported; rewrite hint and warning copy; enable and rewrite the video-input checkbox label. |
| `desktop/src-tauri/src/native_engine/mod.rs` | No behavioural change; verify `provider_supports_video_input` still returns `false` for `anthropic_messages` (it already does). |
| `tasks/spec-v0.2/vn-anthropic-video-001.json` | **New** Task JSON. |
| `tasks/index.json` | Register new task. |
| `docs/task-reports/VN-ANTHROPIC-VIDEO-001.md` | **New** task completion report. |

### 5.5 Invariants preserved

- OpenAI-compatible, Xiaomi MiMo, Google Gemini, OpenAI Responses
  request shapes and headers are unchanged.
- `compile_chunk_text_frame` and other non-video code paths are
  unchanged.
- `compiler_v3` remains feature-gated and off by default.
- Resource limits for input bytes, JSON depth, parsed node count, and
  collection lengths are not weakened.
- Per-provider hard caps remain enforceable; the new 64 MB cap on
  Anthropic mirrors vendor docs rather than loosening an existing limit.

### 5.6 Forbidden changes

- Do not delete the legacy OpenAI-style video block path.
- Do not auto-enable video for `anthropic_messages` without an explicit
  checkbox opt-in.
- Do not send `Authorization` alongside `x-api-key` for Anthropic
  providers.
- Do not raise any existing per-provider size cap.
- Do not modify files outside the `allowed_paths` declared in the Task
  JSON.

## 6. Acceptance tests

### 6.1 Unit tests (`cargo test -p desktop`)

1. `anthropic_video_request_body_uses_video_source_base64`
   - Construct `CompileClientConfig` with `provider_kind = Anthropic`,
     `accepts_video = true`, a fixed base64 string.
   - Call `build_video_request_body` (it must succeed and assert no
     `Authorization` header shape).
   - Assert `body.messages[0].content[1]` equals
     `{"type":"video","source":{"type":"base64","media_type":"video/mp4","data":"<BASE64>"}}`.
   - Assert `body.model == "MiniMax-M3"` (or whatever fixture model).
2. `anthropic_video_request_sets_x_api_key_header`
   - Build a request with `apply_provider_auth` and assert headers:
     - `x-api-key == "test-key"` (after trim and Bearer-strip).
     - `anthropic-version == "2023-06-01"`.
     - `Authorization` absent.
3. `anthropic_video_request_rejects_oversized_payload`
   - Build a `CompileClientConfig` and call `validate_video_payload_size`
     with a synthetic body length > 64 MB; assert it returns an error.
4. `anthropic_video_request_resolves_messages_url`
   - Verify `compile_chunk_video` posts to `<base_url>/messages` for
     `Anthropic`, not `<base_url>/chat/completions`.

### 6.2 UI smoke (manual, recorded in task report)

- Open provider configuration dialog.
- Select "Anthropic Messages"; assert the warning copy now describes
  opt-in, not "未实现".
- Tick "此端点支持视频输入"; assert the Base URL field, model fields, and
  API Key field remain enabled.
- Save a provider with `https://api.minimax.io/anthropic/v1` and a
  dummy model; assert it persists and the provider card shows it as
  enabled.

### 6.3 M3 end-to-end smoke (manual, recorded in task report)

- Generate a 1-3 MB local MP4 chunk.
- Invoke the compile client with `provider_kind = Anthropic`,
  `accepts_video = true`, the real M3 base URL and a real API key.
- Observe the response:
  - **Success (200 + JSON content)**: record the exact JSON shape
    observed; update spec/Task if any field name diverges.
  - **4xx with a structured error indicating unsupported source.type**:
    record the error; the task is then **failed** and the design must be
    revised (likely to pre-upload to a presigned URL, which is out of
    scope and would require a new task).
  - **Network error / 5xx**: record and retry once; if persistent, mark
    the task blocked on environment.

## 7. Spec and task registration

- Affected specification sections (cited in the Task JSON):
  - `SPEC-COMPILER-006` — provider 适配 MUST 包含 negotiation 与资源预检。
  - `SPEC-COMPILER-018` — Planner MUST 使用 Manifest 中的预算与能力清单，不
    通过 Provider 接口猜测。
- New Task JSON: `tasks/spec-v0.2/vn-anthropic-video-001.json`
  - Status: `pending` initially; `done` after acceptance tests pass and
    the manual smoke test records success.
  - Dependencies: `VN-IMPL-001` (so the Anthropic `ProviderKind`
    already exists) and `VN-IMPL-003` is **not** required.
  - `allowed_paths` lists exactly the files in §5.4 (excluding the Task
    JSON itself, the index update, and the task report, which are
    standard task-tracking files and always allowed).
  - `forbidden_changes` lists exactly the items in §5.6.

## 8. Risks and rollback

- **Risk**: MiniMax M3 does not actually accept base64 source for video.
  The smoke test in §6.3 catches this and the task fails fast.
- **Risk**: Sending `x-api-key` to a non-Anthropic endpoint that was
  misclassified as Anthropic could leak credentials to error logs.
  Mitigation: `apply_provider_auth` is gated on
  `provider_kind == Anthropic` only; the user must explicitly select
  `anthropic_messages` type to reach this branch.
- **Rollback**: revert the diff. `anthropic_messages` reverts to
  `supported: false` and the video compile branch disappears. No
  Capsule data is affected because no persistence layer changes.
