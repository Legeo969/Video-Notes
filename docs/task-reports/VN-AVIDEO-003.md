# VN-AVIDEO-003 — Friendly hint for vendor policy/safety rejections

## Summary

After VN-AVIDEO-001 + VN-AVIDEO-002 the compile client surfaces vendor
errors verbatim and fails fast, but the user-visible message was still
machine-shaped:

```
compile chunk rejected by provider: provider returned 500 Internal Server
Error: {"error":{"message":"input new_sensitive, ... (1026)","type":"api_error"}}
request_id=06ad7e5302a7e31cebc8d1f79b63b5e1 (vendor returned api_error envelope)
```

VN-AVIDEO-003 reshapes that into three pieces:

1. the **vendor's own message** (`input new_sensitive, ...`),
2. a **short technical disposition** (`500 safety/policy verdict, request_id=...`),
3. a **localized hint** explaining what the user can do next (try a
   different clip, switch provider, or check configuration).

## Files changed

- `desktop/src-tauri/src/compile/client.rs`
  - New `VendorErrorKind` enum (`SafetyPolicy` / `ApiError` / `Other`) with
    `user_hint()` returning a Chinese hint per kind.
  - `ErrorDisposition::NonRetryable` carries both `reason` and `kind`.
  - `classify_error_response` now produces `kind`. The message-sniffing
    safety heuristic is run **before** the typed-envelope heuristic so a
    safety verdict inside an `api_error` envelope (MiniMax M3 case)
    classifies as `SafetyPolicy`, not `ApiError`.
  - `compile_chunk_video` builds the user-visible string from
    `extract_vendor_message(payload)` + `describe_disposition(...)` +
    `kind.user_hint()`. The raw payload (including `request_id`) is still
    inside `vendor_message`, so support escalations have everything they
    need.
  - New helpers: `extract_vendor_message`, `extract_request_id`,
    `describe_disposition`.
  - 8 new unit tests; updated existing `vendor_api_error_envelope_*` and
    `vendor_safety_*` tests to assert the new `kind` field.

## Resulting user-visible strings

Before:

```
compile chunk rejected by provider: provider returned 500 Internal Server
Error: {...} (vendor returned api_error envelope)
```

After (MiniMax M3 safety rejection):

```
input new_sensitive, messages[1]'s content[0] video is sensitive, please
check your input (1026) (500 safety/policy verdict, request_id=06ad7e...).
提示：供应商内容安全策略拒绝该视频。可尝试更换视频，或在设置中切换到其他多模态 Provider。
```

For non-safety `api_error` (e.g. "model not found"):

```
model 'unknown-model' is not supported on this endpoint
(500 vendor api_error envelope, request_id=...).
提示：供应商 API 返回错误，请检查 Provider 配置或稍后重试。
```

For generic 4xx without envelope:

```
provider returned 400 Bad Request: {...}
(400 vendor error). 提示：供应商拒绝了该请求，请稍后重试或更换 Provider。
```

## Why the sniff-before-envelope ordering

MiniMax M3 wraps its safety verdict inside an `api_error` envelope:

```json
{"error": {"type": "api_error", "message": "input new_sensitive, ...", "code": 1026}}
```

If we classified strictly by `error.type`, every safety verdict would land
in the `ApiError` bucket and the user would see "检查 Provider 配置" — the
wrong advice, since their provider config is fine and the problem is the
clip's content. The message-text sniff runs first so `SafetyPolicy`
takes priority when both signals are present.

## Specification requirements addressed

- **SPEC-COMPILER-006**: provider 适配 MUST 包含 negotiation 与资源预检.
  Error reporting is part of the negotiation contract; users deserve an
  actionable hint rather than a raw payload dump.

## Commands executed

- `cargo test --lib compile::client::tests::` — 29 passed, 0 failed.
- `cargo test --lib compile::` — 69 passed, 0 failed.
- `npm --prefix desktop run verify` — svelte-check clean; Vite built
  150 modules.
- `python scripts/validate_spec_tasks.py` — 49 tasks pass.

## Test results

8 new unit tests:

- `extract_vendor_message_prefers_error_message_field` — Anthropic/OpenAI
  `error.message` is the primary source.
- `extract_vendor_message_falls_back_to_code` — numeric-only errors
  surface as `error code 1026`.
- `extract_vendor_message_falls_back_to_top_level_message` — legacy
  top-level `message` field is still recognized.
- `extract_request_id_finds_top_level_request_id` — vendor request_id at
  the root is captured.
- `extract_request_id_finds_nested_error_request_id` — vendor request_id
  inside `error.request_id` is captured too.
- `vendor_error_kind_user_hint_mentions_alternative_paths_for_safety` —
  the safety hint must suggest at least one actionable next step
  (changing the clip or switching provider).
- `describe_disposition_includes_request_id_when_available` — the
  disposition string includes the status, the kind label, and the
  vendor's request_id.

The existing `vendor_safety_envelope_with_typed_api_error_is_safety_policy`
and `vendor_api_error_envelope_without_safety_message_is_api_error` tests
cover the priority ordering of the two classifiers.

## Compatibility impact

- All non-retryable error paths now carry an extra `kind` field; the
  wire contract (HTTP status, JSON body) is unchanged.
- Error strings are longer than before because they include the hint.
  Existing log scrapers that grep for `vendor returned api_error envelope`
  or `safety` will still match because the disposition tag is appended
  inside the parentheses.

## Migration impact

None. No Capsule data or persisted settings changed.

## Remaining risks

- The safety hint is generic ("更换视频 / 切换 Provider"). If users
  frequently hit this with the same provider + clip combination, a
  follow-up task could surface vendor-specific remediation (e.g. an
  M3-specific link to the safety policy or a way to mark a clip as
  "skip on safety rejection").
- The `extract_request_id` helper looks at three key variants
  (`request_id`, `requestId`, `trace_id`) at two locations. Some
  vendors may use a different key entirely; future failures would
  surface as "500 safety/policy verdict" without the request_id
  suffix. Acceptable degradation.

## Rollback instructions

Revert `desktop/src-tauri/src/compile/client.rs`. The pre-task error
string format is restored automatically. No on-disk data is affected.
