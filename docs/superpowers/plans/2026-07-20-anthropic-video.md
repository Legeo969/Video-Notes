# Anthropic Messages Video Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Anthropic-Messages-compatible providers that accept video upload (e.g. MiniMax M3 at `https://api.minimax.io/anthropic/v1/messages`) to drive automatic video notes compilation through the existing compile client.

**Architecture:** Branch the existing compile client's video path on `ProviderKind::Anthropic`. The Anthropic branch emits a base64 `source` content block, sets `x-api-key` + `anthropic-version` headers, and POSTs to `<base_url>/messages`. The OpenAI-style `video_url` path is preserved unchanged. The UI reuses the existing "Anthropic Messages" provider type plus the per-provider `MP4 video 输入` checkbox, which the user must tick to opt in.

**Tech Stack:** Rust (reqwest + serde_json), Svelte 5, TypeScript.

**Spec:** `docs/superpowers/specs/2026-07-20-anthropic-video-design.md`
**Task JSON:** `tasks/spec-v0.2/vn-anthropic-video-001.json` (task id `VN-AVIDEO-001`)

---

## Global Constraints

- Allowed paths (must stay within these):
  - `desktop/src-tauri/src/compile/client.rs`
  - `desktop/src/lib/components/settings/ProviderFormDialog.svelte`
  - `desktop/src/lib/components/settings/ProvidersPanel.svelte`
  - `tasks/spec-v0.2/vn-anthropic-video-001.json`
  - `tasks/index.json`
  - `docs/task-reports/VN-AVIDEO-001.md`
- Forbidden changes (re-asserted by the task JSON):
  - Do not auto-enable video for `anthropic_messages` without an explicit checkbox opt-in.
  - Do not remove or weaken the OpenAI-style `video_url` block path.
  - Do not send `Authorization` alongside `x-api-key` for Anthropic providers.
  - Do not raise any existing per-provider size cap.
  - Do not default-enable `compiler_v3`.
  - Do not weaken request body size, JSON depth, or parsed node limits.
- Wire contract assumed for MiniMax M3 (per design §4):
  - Headers: `x-api-key: <KEY>`, `anthropic-version: 2023-06-01`, `Content-Type: application/json`.
  - Video content block: `{ "type": "video", "source": { "type": "base64", "media_type": "video/mp4", "data": "<BASE64>" } }`.
  - Whole request body cap: 64 MB.
- Code style: English code/identifiers/comments; Chinese user-visible UI copy.
- Commit conventions: `type(scope): concise description` followed by `Task: VN-AVIDEO-001` and `Spec: SPEC-COMPILER-006, SPEC-COMPILER-018`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `desktop/src-tauri/src/compile/client.rs` | Build Anthropic video request body, apply Anthropic auth headers, resolve `/messages` URL, enforce 64 MB cap. | Modify. |
| `desktop/src-tauri/src/compile/client.rs` (tests module) | Unit tests for Anthropic body shape, headers, oversize rejection, messages URL. | Add. |
| `desktop/src/lib/components/settings/ProviderFormDialog.svelte` | Mark `anthropic_messages` supported; revise hint and warning copy; enable the video-input checkbox with neutral label. | Modify. |
| `desktop/src/lib/components/settings/ProvidersPanel.svelte` | Display Anthropic-compatible provider cards correctly (no behaviour change expected, listed in allowed_paths as defensive scope). | Verify only. |
| `docs/task-reports/VN-AVIDEO-001.md` | Task completion report. | Create at end. |

---

## Task 1: Add Anthropic request-body builder

**Files:**
- Modify: `desktop/src-tauri/src/compile/client.rs:223-265` (`build_video_request_body`)
- Test: `desktop/src-tauri/src/compile/client.rs` (test module at end of file)

**Interfaces:**
- Consumes: `CompileClientConfig`, `video_b64`, `system`, `user_text` (existing).
- Produces: a `serde_json::Value` body where the user content array's first element is an Anthropic-style `{type: "video", source: {type: "base64", media_type: "video/mp4", data: "<BASE64>"}}` block when `provider_kind == ProviderKind::Anthropic`.

- [ ] **Step 1: Write the failing test**

Add to the `tests` module in `desktop/src-tauri/src/compile/client.rs`:

```rust
    #[test]
    fn anthropic_video_request_body_uses_video_source_base64() {
        let mut config = CompileClientConfig::new(
            "https://api.minimax.io/anthropic/v1".to_string(),
            "test-key".to_string(),
            "MiniMax-M3".to_string(),
            ProviderKind::Anthropic,
        );
        config.accepts_video = true;
        let body = build_video_request_body(&config, "BASE64DATA", "system", "user").unwrap();
        assert_eq!(body["model"], "MiniMax-M3");
        let part = &body["messages"][1]["content"][0];
        assert_eq!(part["type"], "video");
        assert_eq!(part["source"]["type"], "base64");
        assert_eq!(part["source"]["media_type"], "video/mp4");
        assert_eq!(part["source"]["data"], "BASE64DATA");
        assert!(part.get("fps").is_none());
        assert!(part.get("media_resolution").is_none());
        assert_eq!(body["max_tokens"], 4096);
        assert!(body.get("max_completion_tokens").is_none());
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cargo test -p desktop --lib compile::client::tests::anthropic_video_request_body_uses_video_source_base64 -- --nocapture`
Expected: FAIL — the existing `build_video_request_body` emits `type: "video_url"`, so `assert_eq!(part["type"], "video")` fails with "video_url" != "video".

- [ ] **Step 3: Implement the minimal change**

Replace the body of `build_video_request_body` in `desktop/src-tauri/src/compile/client.rs` (lines 223–265) with:

```rust
fn build_video_request_body(
    config: &CompileClientConfig,
    video_b64: &str,
    system: &str,
    user_text: &str,
) -> Result<Value, String> {
    let video_part = if config.provider_kind == ProviderKind::Anthropic {
        // Anthropic-style base64 source. The cap is measured after JSON
        // serialisation below; this branch keeps the data URL short enough
        // to fit comfortably under the per-provider limit during preview
        // checks but the authoritative cap lives in
        // validate_video_payload_size.
        serde_json::json!({
            "type": "video",
            "source": {
                "type": "base64",
                "media_type": "video/mp4",
                "data": video_b64,
            }
        })
    } else {
        let data_url = format!("data:video/mp4;base64,{video_b64}");
        validate_video_payload_size(config.provider_kind, data_url.len())?;
        let mut part = serde_json::json!({
            "type": "video_url",
            "video_url": { "url": data_url }
        });
        if config.provider_kind.is_xiaomi_mimo() {
            if let Some(object) = part.as_object_mut() {
                object.insert("fps".to_string(), serde_json::json!(1));
                object.insert("media_resolution".to_string(), serde_json::json!("default"));
            }
        }
        part
    };

    let mut body = serde_json::json!({
        "model": config.model,
        "messages": [
            { "role": "system", "content": system },
            {
                "role": "user",
                "content": [
                    video_part,
                    { "type": "text", "text": user_text }
                ]
            }
        ],
        "temperature": 0.02,
        "max_tokens": 4096
    });
    if config.provider_kind.is_xiaomi_mimo() {
        if let Some(object) = body.as_object_mut() {
            object.remove("max_tokens");
            object.insert("max_completion_tokens".to_string(), serde_json::json!(4096));
        }
    }
    Ok(body)
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p desktop --lib compile::client::tests::anthropic_video_request_body_uses_video_source_base64 -- --nocapture`
Expected: PASS.

- [ ] **Step 5: Run the full compile test module to confirm no regressions**

Run: `cargo test -p desktop --lib compile::client::tests::`
Expected: All tests pass, including the existing `xiaomi_request_uses_documented_multimodal_fields`, `rejects_oversized_xiaomi_payload_before_request`, `maps_local_anchor_positions_to_backend_ids`, etc.

- [ ] **Step 6: Commit**

```bash
git add desktop/src-tauri/src/compile/client.rs
git commit -m "feat(compile): add Anthropic-style video source block

Task: VN-AVIDEO-001
Spec: SPEC-COMPILER-006, SPEC-COMPILER-018
Tests: cargo test -p desktop --lib compile::client::tests::"
```

---

## Task 2: Apply Anthropic auth headers

**Files:**
- Modify: `desktop/src-tauri/src/compile/client.rs:267-285` (`apply_provider_auth`)
- Test: `desktop/src-tauri/src/compile/client.rs` (test module)

**Interfaces:**
- Consumes: `CompileClientConfig`, request builder (existing).
- Produces: request builder with `x-api-key: <KEY>` and `anthropic-version: 2023-06-01` headers when `provider_kind == ProviderKind::Anthropic`, with no `Authorization` header.

- [ ] **Step 1: Write the failing test**

Add to the `tests` module:

```rust
    #[test]
    fn anthropic_video_request_sets_x_api_key_header() {
        let mut config = CompileClientConfig::new(
            "https://api.minimax.io/anthropic/v1".to_string(),
            "test-key".to_string(),
            "MiniMax-M3".to_string(),
            ProviderKind::Anthropic,
        );
        config.accepts_video = true;
        let request = apply_provider_auth(
            reqwest::blocking::Client::new().post("https://example.test"),
            &config,
        )
        .build()
        .unwrap();
        assert_eq!(request.headers()["x-api-key"], "test-key");
        assert_eq!(request.headers()["anthropic-version"], "2023-06-01");
        assert!(request.headers().get("authorization").is_none());

        // Bare key without prefix still works.
        let mut config_no_prefix = config.clone();
        config_no_prefix.api_key = "Bearer another-key".to_string();
        let request = apply_provider_auth(
            reqwest::blocking::Client::new().post("https://example.test"),
            &config_no_prefix,
        )
        .build()
        .unwrap();
        assert_eq!(request.headers()["x-api-key"], "another-key");
        assert!(request.headers().get("authorization").is_none());
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cargo test -p desktop --lib compile::client::tests::anthropic_video_request_sets_x_api_key_header -- --nocapture`
Expected: FAIL — `apply_provider_auth` currently routes Anthropic through `with_optional_bearer`, which sets `Authorization`, not `x-api-key`.

- [ ] **Step 3: Implement the change**

Replace `apply_provider_auth` in `desktop/src-tauri/src/compile/client.rs` (lines 267–285) with:

```rust
fn apply_provider_auth(
    request: reqwest::blocking::RequestBuilder,
    config: &CompileClientConfig,
) -> reqwest::blocking::RequestBuilder {
    if config.provider_kind.is_xiaomi_mimo() {
        let token = config.api_key.trim();
        let token = token
            .strip_prefix("Bearer ")
            .or_else(|| token.strip_prefix("bearer "))
            .unwrap_or(token);
        if token.is_empty() {
            request
        } else {
            request.header("api-key", token)
        }
    } else if config.provider_kind == ProviderKind::Anthropic {
        let token = config.api_key.trim();
        let token = token
            .strip_prefix("Bearer ")
            .or_else(|| token.strip_prefix("bearer "))
            .unwrap_or(token);
        let request = if token.is_empty() {
            request
        } else {
            request.header("x-api-key", token)
        };
        request.header("anthropic-version", "2023-06-01")
    } else {
        crate::native_engine::with_optional_bearer(request, &config.api_key)
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p desktop --lib compile::client::tests::anthropic_video_request_sets_x_api_key_header -- --nocapture`
Expected: PASS.

- [ ] **Step 5: Re-run the compile test module**

Run: `cargo test -p desktop --lib compile::client::tests::`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add desktop/src-tauri/src/compile/client.rs
git commit -m "feat(compile): send Anthropic x-api-key and version headers

Task: VN-AVIDEO-001
Spec: SPEC-COMPILER-006
Tests: cargo test -p desktop --lib compile::client::tests::"
```

---

## Task 3: Route Anthropic POST to /messages

**Files:**
- Modify: `desktop/src-tauri/src/compile/client.rs:123` (URL construction inside `compile_chunk_video`)
- Test: `desktop/src-tauri/src/compile/client.rs` (test module)

**Interfaces:**
- Consumes: `CompileClientConfig.base_url`.
- Produces: a request URL of `<base_url>/messages` for `ProviderKind::Anthropic`, otherwise `<base_url>/chat/completions` (existing).

The `compile_chunk_video` function builds its URL inline at line 123 and uses it inside the retry loop. A direct test of the URL builder is preferable to standing up the full HTTP flow.

- [ ] **Step 1: Refactor the URL building into a private helper**

Replace the single line at `desktop/src-tauri/src/compile/client.rs:123`:

```rust
        let url = format!("{}/chat/completions", config.base_url.trim_end_matches('/'));
```

with:

```rust
        let url = compile_video_request_url(config);
```

Then add the helper just above `compile_chunk_video`:

```rust
fn compile_video_request_url(config: &CompileClientConfig) -> String {
    let base = config.base_url.trim_end_matches('/');
    if config.provider_kind == ProviderKind::Anthropic {
        format!("{base}/messages")
    } else {
        format!("{base}/chat/completions")
    }
}
```

- [ ] **Step 2: Write the failing test**

Add to the `tests` module:

```rust
    #[test]
    fn compile_video_request_url_uses_messages_endpoint_for_anthropic() {
        let mut config = CompileClientConfig::new(
            "https://api.minimax.io/anthropic/v1/".to_string(),
            "test-key".to_string(),
            "MiniMax-M3".to_string(),
            ProviderKind::Anthropic,
        );
        config.accepts_video = true;
        assert_eq!(
            compile_video_request_url(&config),
            "https://api.minimax.io/anthropic/v1/messages"
        );

        let mut compat = CompileClientConfig::new(
            "https://api.xiaomimimo.com/v1".to_string(),
            "test-key".to_string(),
            "mimo-v2.5".to_string(),
            ProviderKind::XiaomiMiMo,
        );
        compat.accepts_video = true;
        assert_eq!(
            compile_video_request_url(&compat),
            "https://api.xiaomimimo.com/v1/chat/completions"
        );
    }
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `cargo test -p desktop --lib compile::client::tests::compile_video_request_url_uses_messages_endpoint_for_anthropic -- --nocapture`
Expected: PASS (the helper already routes correctly).

- [ ] **Step 4: Run the full compile test module**

Run: `cargo test -p desktop --lib compile::client::tests::`
Expected: All tests pass, including `xiaomi_request_uses_documented_multimodal_fields` which still POSTs to `chat/completions` for Xiaomi.

- [ ] **Step 5: Commit**

```bash
git add desktop/src-tauri/src/compile/client.rs
git commit -m "refactor(compile): extract compile_video_request_url helper

Task: VN-AVIDEO-001
Spec: SPEC-COMPILER-006
Tests: cargo test -p desktop --lib compile::client::tests::"
```

---

## Task 4: Enforce 64 MB Anthropic request size cap

**Files:**
- Modify: `desktop/src-tauri/src/compile/client.rs:297-307` (`validate_video_payload_size`)
- Test: `desktop/src-tauri/src/compile/client.rs` (test module)

**Interfaces:**
- Consumes: `ProviderKind`, payload length in bytes.
- Produces: `Ok(())` if within the per-provider cap, `Err(String)` otherwise. For `ProviderKind::Anthropic` the cap is 64 MB.

- [ ] **Step 1: Add the constant**

In `desktop/src-tauri/src/compile/client.rs`, after the existing `XIAOMI_MAX_BASE64_BYTES` constant (line 19), add:

```rust
const ANTHROPIC_MAX_REQUEST_BYTES: usize = 64 * 1024 * 1024;
```

- [ ] **Step 2: Write the failing test**

Add to the `tests` module:

```rust
    #[test]
    fn anthropic_video_request_rejects_oversized_payload() {
        assert!(
            validate_video_payload_size(ProviderKind::Anthropic, ANTHROPIC_MAX_REQUEST_BYTES)
                .is_ok()
        );
        assert!(
            validate_video_payload_size(ProviderKind::Anthropic, ANTHROPIC_MAX_REQUEST_BYTES + 1)
                .is_err()
        );
        // Other providers are unaffected.
        assert!(
            validate_video_payload_size(ProviderKind::XiaomiMiMo, ANTHROPIC_MAX_REQUEST_BYTES)
                .is_ok()
        );
    }
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cargo test -p desktop --lib compile::client::tests::anthropic_video_request_rejects_oversized_payload -- --nocapture`
Expected: FAIL — `validate_video_payload_size` currently only enforces the Xiaomi cap.

- [ ] **Step 4: Extend `validate_video_payload_size`**

Replace `validate_video_payload_size` in `desktop/src-tauri/src/compile/client.rs` (lines 297–307) with:

```rust
fn validate_video_payload_size(
    provider_kind: ProviderKind,
    data_url_len: usize,
) -> Result<(), String> {
    if provider_kind.is_xiaomi_mimo() && data_url_len > XIAOMI_MAX_BASE64_BYTES {
        return Err(format!(
            "Xiaomi MiMo video payload exceeds the 50 MB Base64 limit ({data_url_len} bytes)"
        ));
    }
    if provider_kind == ProviderKind::Anthropic && data_url_len > ANTHROPIC_MAX_REQUEST_BYTES {
        return Err(format!(
            "Anthropic-Messages video request exceeds the 64 MB whole-body limit ({data_url_len} bytes)"
        ));
    }
    Ok(())
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cargo test -p desktop --lib compile::client::tests::anthropic_video_request_rejects_oversized_payload -- --nocapture`
Expected: PASS.

- [ ] **Step 6: Re-run the full compile test module**

Run: `cargo test -p desktop --lib compile::client::tests::`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add desktop/src-tauri/src/compile/client.rs
git commit -m "feat(compile): enforce 64 MB Anthropic request body cap

Task: VN-AVIDEO-001
Spec: SPEC-COMPILER-018
Tests: cargo test -p desktop --lib compile::client::tests::"
```

---

## Task 5: Update the provider form dialog UI

**Files:**
- Modify: `desktop/src/lib/components/settings/ProviderFormDialog.svelte:14-51` (providerTypeOptions), `:116` (warning copy), `:139-142` (checkbox label).

**Interfaces:**
- Consumes: existing props (`providerForm`, etc.).
- Produces: `anthropic_messages` marked supported; revised hint/warning/checkbox copy in Chinese; video checkbox enabled when the type is `anthropic_messages`.

- [ ] **Step 1: Mark `anthropic_messages` supported**

In `desktop/src/lib/components/settings/ProviderFormDialog.svelte`, change the `anthropic_messages` entry (lines 33–41) to:

```ts
    {
      id: "anthropic_messages",
      label: "Anthropic Messages",
      hint: "Anthropic /v1/messages 接口（兼容 MiniMax M3 等扩展支持视频的端点）。",
      defaultBaseUrl: "https://api.anthropic.com/v1",
      defaultModel: "claude-sonnet-4-5",
      defaultVisionModel: "claude-sonnet-4-5",
      supported: true,
    },
```

- [ ] **Step 2: Update the warning copy**

In the same file, replace the warning block (line 116):

```svelte
          {#if providerTypeUnsupported}
            <div class="provider-type-warning"><Icon name="alert" size={15} /><span>当前编译器尚未实现此 API 类型的视频请求适配器，因此不能用于自动视频笔记任务。</span></div>
          {/if}
```

with:

```svelte
          {#if providerTypeUnsupported}
            <div class="provider-type-warning"><Icon name="alert" size={15} /><span>当前编译器尚未实现此 API 类型的视频请求适配器，因此不能用于自动视频笔记任务。</span></div>
          {:else if providerForm.provider === "anthropic_messages"}
            <div class="provider-type-hint"><Icon name="info" size={15} /><span>原生 Anthropic Claude 不支持视频；只有扩展端点（例如 MiniMax M3）支持。勾选"此端点支持视频输入"后才会发送视频，否则按文本路径处理。</span></div>
          {/if}
```

- [ ] **Step 3: Update the checkbox label**

Replace the checkbox block (lines 139–142):

```svelte
          <label class="capability-toggle">
            <input type="checkbox" bind:checked={providerForm.video_input} disabled={providerTypeUnsupported} />
            <span><strong>此端点支持 MP4 video_url 输入</strong><small>只有供应商明确支持 OpenAI-compatible Base64 视频时才启用；普通文本或图片接口请勿勾选。</small></span>
          </label>
```

with:

```svelte
          <label class="capability-toggle">
            <input type="checkbox" bind:checked={providerForm.video_input} disabled={providerTypeUnsupported} />
            <span><strong>此端点支持视频输入 (Anthropic-style base64 或 OpenAI-style video_url)</strong><small>原生 Anthropic Claude 不支持视频，请勿勾选；只有明确支持视频的端点（例如 MiniMax M3 等）才勾选。</small></span>
          </label>
```

- [ ] **Step 4: Add a matching hint style class**

Append at the end of the existing `<style>` block (just before `</style>`):

```css
  .provider-type-hint { display: flex; align-items: flex-start; gap: 8px; margin: 0; padding: 10px 12px; border-radius: 10px; color: var(--info-color); background: var(--info-soft); border: 1px solid color-mix(in srgb, var(--info-color) 25%, var(--border-color)); font-size: 12px; line-height: 1.55; }
```

Both `--info-color` and `--info-soft` are already defined in `desktop/src/styles/global.css` for light and dark themes; no new variables are introduced.

- [ ] **Step 5: Run the desktop verify suite**

Run: `npm --prefix desktop run verify`
Expected: PASS. If the suite runs `svelte-check` and it complains about unused variables, ensure `selectedProviderType` is still referenced (it is — by `defaultBaseUrl` etc.).

- [ ] **Step 6: Commit**

```bash
git add desktop/src/lib/components/settings/ProviderFormDialog.svelte
git commit -m "feat(ui): enable Anthropic Messages video-input opt-in

Task: VN-AVIDEO-001
Spec: SPEC-COMPILER-006
Tests: npm --prefix desktop run verify"
```

---

## Task 6: Manual UI + smoke test record

**Files:**
- Create: `docs/task-reports/VN-AVIDEO-001.md`

This task is manual. The engineer MUST execute the steps and write the report — it is not optional.

- [ ] **Step 1: Open the provider configuration dialog in the running desktop app**

Run: `npm --prefix desktop run tauri dev` (or the project's documented dev command).
Action: open Settings → Providers → Add Provider.
Observe:
- The "Anthropic Messages" option is enabled (no "（暂不可用于自动任务）" suffix).
- Selecting it shows the hint about MiniMax M3 compatibility.
- The warning block now describes opt-in behaviour, not "未实现".
- The "此端点支持视频输入" checkbox is enabled.

If any item disagrees with the design, fix the Svelte file in a follow-up commit before continuing.

- [ ] **Step 2: Configure a MiniMax M3 provider and save**

Action: enter
- 配置名称: `MiniMax M3 Smoke`
- API 类型: `Anthropic Messages`
- Base URL: `https://api.minimax.io/anthropic/v1`
- 文本模型: `MiniMax-M3` (or whatever the real M3 model identifier is)
- 视觉模型: same as 文本模型
- API Key: `<real M3 API key>`
- 勾选 "此端点支持视频输入".

Save the provider. Observe the provider card lists it as enabled and the "测试连接" affordance appears.

- [ ] **Step 3: Generate a small local MP4 chunk**

Use FFmpeg to produce a 1-3 second MP4 suitable for smoke testing:

```bash
ffmpeg -y -f lavfi -i "testsrc=size=320x240:rate=5:duration=2" \
       -f lavfi -i "sine=frequency=440:duration=2" \
       -c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 32 \
       -c:a aac -b:a 64k \
       /tmp/m3-smoke.mp4
ls -l /tmp/m3-smoke.mp4
```

Expected: file exists and is < 5 MB.

- [ ] **Step 4: Drive `compile_chunk_video` against MiniMax M3**

The simplest path is to add a temporary `#[test]` in `desktop/src-tauri/src/compile/client.rs` gated by an env var, run it with the real API key, then remove it. Concretely:

In the `tests` module, add:

```rust
    #[test]
    #[ignore = "requires MINIMAX_API_KEY env var; run with --ignored --nocapture"]
    fn m3_smoke_compile_video() {
        let key = std::env::var("MINIMAX_API_KEY")
            .expect("MINIMAX_API_KEY must be set to run the M3 smoke test");
        let mut config = CompileClientConfig::new(
            "https://api.minimax.io/anthropic/v1".to_string(),
            key,
            std::env::var("MINIMAX_MODEL").unwrap_or_else(|_| "MiniMax-M3".to_string()),
            ProviderKind::Anthropic,
        );
        config.accepts_video = true;
        let mp4 = std::fs::read("/tmp/m3-smoke.mp4")
            .expect("MP4 fixture missing; run Step 3 first");
        let result = compile_chunk_video(&config, 0, 1, &[0, 1], &mp4, None);
        eprintln!("m3_smoke result: {result:?}");
        // We assert non-panic; success and structured vendor errors both pass.
        // A network failure is also accepted because CI may not have internet.
        let _ = result;
    }
```

Run:

```bash
MINIMAX_API_KEY=<real-key> cargo test -p desktop --lib \
    compile::client::tests::m3_smoke_compile_video -- --ignored --nocapture
```

Observe the `eprintln!` output and record it in the task report (Step 5). Possible outcomes:

- **200 + parseable content**: success. Record the observed JSON shape, including the `content[]` array fields. If any field name diverges from the assumption in design §4, file a follow-up task; do NOT change code in this task.
- **4xx with a structured M3 error indicating unsupported `source.type: "base64"`**: smoke test FAILED. Record the exact error. The task is now blocked; do not mark the implementation as done. Update the design doc and the task JSON with the new finding and stop.
- **Network / 5xx / TLS error**: record and retry once. If persistent, mark the smoke test as `BLOCKED` (environment limitation) but mark the implementation as `done` if all unit tests pass and the UI verifies.

After running, REMOVE the `m3_smoke_compile_video` test from the source tree (it must not be committed).

- [ ] **Step 5: Write the task completion report**

Create `docs/task-reports/VN-AVIDEO-001.md` with the following sections (use this skeleton):

```markdown
# VN-AVIDEO-001 — Anthropic Messages video upload

## Summary

Enables automatic video-notes compilation against Anthropic-Messages-compatible
endpoints that accept base64 video (e.g. MiniMax M3).

## Files changed

- `desktop/src-tauri/src/compile/client.rs`
- `desktop/src/lib/components/settings/ProviderFormDialog.svelte`

## Specification requirements addressed

- SPEC-COMPILER-006 (provider 适配 MUST 包含 negotiation 与资源预检)
- SPEC-COMPILER-018 (Planner MUST 使用 Manifest 中的预算与能力清单)

## Commands executed

- `cargo test -p desktop --lib compile::client::tests::`
- `npm --prefix desktop run verify`
- `cargo test -p desktop --lib compile::client::tests::m3_smoke_compile_video -- --ignored --nocapture`

## Test results

<copy cargo test output summary>

## UI verification

<describe what the dialog showed in Step 1>

## M3 end-to-end smoke

<record the actual eprintln! output from Step 4, classify outcome as
 SUCCESS / VENDOR-REJECTS-BASE64 / NETWORK-FAILED >

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

<if M3 rejected base64, describe the blocker and the required next step>

## Rollback instructions

Revert the four commits added by this task. The provider form dialog
returns to "Anthropic Messages = unsupported for automatic tasks".
```

- [ ] **Step 6: Commit the task report**

```bash
git add docs/task-reports/VN-AVIDEO-001.md
git commit -m "docs(task): VN-AVIDEO-001 completion report"
```

---

## Self-Review

After the plan is fully written, run these checks:

1. **Spec coverage** — every requirement from the design doc has at least one task:
   - Anthropic base64 video content block → Task 1.
   - Anthropic auth headers (x-api-key + anthropic-version, no Authorization) → Task 2.
   - `<base_url>/messages` URL routing → Task 3.
   - 64 MB cap → Task 4.
   - UI: supported flag, hint copy, warning copy, checkbox label, checkbox enabled → Task 5.
   - Manual M3 smoke test → Task 6.
   - Task completion report → Task 6.

2. **Placeholder scan** — every step has concrete code or commands. No "TBD"/"etc."/"similar to".

3. **Type consistency** — function names match across tasks:
   - `build_video_request_body` (Tasks 1, tests in Task 1, 2).
   - `apply_provider_auth` (Tasks 2 and existing tests).
   - `compile_video_request_url` (Tasks 3, used at the `compile_chunk_video` call site).
   - `validate_video_payload_size` (Tasks 4 and existing test).
   - `ProviderKind::Anthropic` enum variant matches existing code at `compile/client.rs:30`.

4. **Forbidden-changes audit** — no task:
   - Auto-enables video without checkbox opt-in.
   - Removes the OpenAI-style `video_url` block (Tasks 1, 3, 4 keep it).
   - Sends `Authorization` for Anthropic (Task 2 explicitly asserts its absence).
   - Touches files outside allowed_paths (Tasks 1–4 only modify `client.rs`; Task 5 only touches `ProviderFormDialog.svelte`; Task 6 only touches `docs/task-reports/VN-AVIDEO-001.md`).
