# VN-AVIDEO-002 — Parse Anthropic-Messages-style response

## Summary

The compile client in `desktop/src-tauri/src/compile/client.rs` was sending
the correct request body to MiniMax M3's Anthropic-Messages-compatible
endpoint (VN-AVIDEO-001) but reading the response as if it were an
OpenAI-style `choices[0].message.content` shape. The real MiniMax M3
response uses the Anthropic Messages shape — top-level `content:
[{type: "text", text: "..."}, ...]` — so the parser always failed with
`API returned no content: payload=...` even though the model had produced
a perfectly valid JSON note payload inside the first text block.

This task introduces `extract_response_text(provider_kind, payload)` which
dispatches on provider kind: Anthropic-style top-level `content[]` for
`ProviderKind::Anthropic`, OpenAI-style `choices[0].message.content` (with
`reasoning_content`/`reasoning` fallbacks) for everything else.

## Files changed

- `desktop/src-tauri/src/compile/client.rs`
  - New helper `extract_response_text(provider_kind, payload) -> Option<String>`.
  - `compile_chunk_video` calls the helper instead of an inline parser.
  - 6 new unit tests in the `tests` module.

## Wire shape (observed)

Real MiniMax M3 response (request_id `06ad772c198f5ff6ea6712146e8a99f7`):

```json
{
  "base_resp": {"status_code": 0, "status_msg": ""},
  "content": [
    {
      "type": "text",
      "text": "{\"events\": [...], \"chunk_summary\": \"...\"}"
    }
  ],
  "id": "06ad772c198f5ff6ea6712146e8a99f7",
  "model": "MiniMax-M3",
  "role": "assistant",
  "stop_reason": "end_turn",
  "type": "message",
  "usage": {...}
}
```

The top-level `content` array is the Anthropic Messages convention. The
compile client now extracts `content[i].text` for each `type == "text"`
block, concatenating them with a single newline between blocks, and hands
the resulting string to the JSON repair / parse / validate pipeline.

Tool-use blocks (`type == "tool_use"`) and any other non-text blocks are
ignored — the parser only contributes text blocks to the extracted string.

## Why a single helper instead of patching the inline parser

The OpenAI-style path covers three vendor quirks (`content`,
`reasoning_content`, `reasoning`) and Anthropic-Messages-compatible
endpoints have a fourth wire shape that cannot be expressed as another
fallback field. Splitting the dispatch by `provider_kind` keeps both
shapes readable and lets future vendor-specific shapes slot in without
growing a chain of fallbacks.

The helper returns `Option<String>` so the existing
`API returned no content: payload={payload}` diagnostic is unchanged —
the raw payload still surfaces to the user / logs when extraction fails.

## Specification requirements addressed

- **SPEC-COMPILER-006**: provider 适配 MUST 包含 negotiation 与资源预检.
  Parsing the response shape the vendor actually emits is part of the
  negotiation contract.

## Commands executed

- `cargo test --lib compile::client::tests::` — 21 passed, 0 failed
  (includes the 6 new `extract_response_text_*` tests).
- `cargo test --lib compile::` — 61 passed, 0 failed.
- `npm --prefix desktop run verify` — `svelte-check` clean, Vite built
  150 modules.
- `python scripts/validate_spec_tasks.py` — 49 tasks pass.
- `python scripts/validate_spec_v01.py` — 10 schemas, 155 requirements
  pass.

## Test results

Six new unit tests:

1. `extract_response_text_handles_anthropic_top_level_content_blocks` —
   parses the real MiniMax M3 shape (single text block with the JSON
   payload).
2. `extract_response_text_concatenates_multiple_anthropic_text_blocks` —
   multi-block responses (e.g. reasoning + final answer) are joined with
   `\n` so the JSON repair step sees a single coherent string.
3. `extract_response_text_ignores_non_text_anthropic_blocks` — `tool_use`
   blocks do not contribute to the extracted text.
4. `extract_response_text_anthropic_returns_none_when_no_text_blocks` —
   empty content array returns `None`, surfacing the existing
   `API returned no content` diagnostic.
5. `extract_response_text_openai_choices_message_still_works` —
   regression: the OpenAI-style `choices[0].message.content` path is
   preserved (with the `reasoning_content`/`reasoning` fallbacks).
6. `extract_response_text_openai_falls_back_to_reasoning_field` —
   vendor quirks (e.g. reasoning-only responses) still parse.

## Security impact

- Non-text blocks are skipped, so untrusted model output containing
  `tool_use` payloads cannot inject executable content into the JSON
  repair / parse pipeline.
- No new outbound requests or auth headers introduced. Existing
  provider auth classification is unchanged.

## Compatibility impact

- Anthropic-Messages-compatible providers (e.g. MiniMax M3) that previously
  produced `API returned no content: payload=...` now extract their JSON
  payloads correctly and proceed through the repair / parse / validate
  chain.
- All OpenAI-style providers (OpenAI, Xiaomi MiMo, Google Gemini, OpenAI
  Responses) keep their existing extraction path. The 21-test client
  suite passes including the regression tests.

## Migration impact

None. No Capsule data or persisted settings changed.

## Remaining risks

- The MiniMax-specific `base_resp.status_code != 0` field is not yet
  inspected — if M3 ever returns `base_resp.status_code != 0` with
  HTTP 200, the parser will see a `content` array containing an error
  string rather than a JSON payload, and the JSON repair step will fail
  with a parse error rather than a vendor message. A future task could
  inspect `base_resp` and surface its `status_msg` to the user.
- Some Anthropic-Messages-compatible vendors may use `input` instead of
  `text` for tool-use blocks (this is the canonical Anthropic SDK
  shape). Tool-use blocks are ignored entirely here, so this is
  irrelevant for the current code path; flagged for future reference.

## Rollback instructions

Revert `desktop/src-tauri/src/compile/client.rs`. The change is
localized to one helper plus a single call-site update; reverting
restores the prior OpenAI-only extraction without affecting any other
provider or any on-disk data.
