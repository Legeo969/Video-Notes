//! Capability-aware multimodal compile client.

use std::collections::HashSet;
use std::error::Error;
use std::time::Duration;

use base64::engine::general_purpose;
use base64::Engine as _;
use reqwest::StatusCode;
use serde_json::Value;

use super::prompt;
use super::repair;
use super::RawCompileOutput;

const COMPILE_TIMEOUT_SEC: u64 = 420;
const MAX_RETRIES: u32 = 3;
const RETRY_BASE_DELAY_MS: u64 = 1_000;
const XIAOMI_MAX_BASE64_BYTES: usize = 50 * 1024 * 1024;
const ANTHROPIC_MAX_REQUEST_BYTES: usize = 64 * 1024 * 1024;

/// Valid event types the schema permits.
const VALID_EVENT_TYPES: &[&str] = &["fact", "procedure", "concept", "failure", "verification"];

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ProviderKind {
    OpenAICompat,
    XiaomiMiMo,
    OpenAIResponses,
    GoogleGemini,
    Anthropic,
}

impl ProviderKind {
    pub fn from_type_str(value: &str) -> Self {
        match value {
            "mimo" | "xiaomi_mimo" => Self::XiaomiMiMo,
            "google_gemini" => Self::GoogleGemini,
            "anthropic_messages" => Self::Anthropic,
            "openai_responses" => Self::OpenAIResponses,
            _ => Self::OpenAICompat,
        }
    }

    pub fn from_profile(provider_type: &str, base_url: &str) -> Self {
        let kind = Self::from_type_str(provider_type);
        if kind == Self::OpenAICompat && base_url.to_ascii_lowercase().contains("xiaomimimo.com") {
            Self::XiaomiMiMo
        } else {
            kind
        }
    }

    fn is_xiaomi_mimo(self) -> bool {
        self == Self::XiaomiMiMo
    }
}

#[derive(Debug, Clone)]
pub struct CompileClientConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub provider_kind: ProviderKind,
    pub accepts_video: bool,
}

impl CompileClientConfig {
    pub fn new(
        base_url: String,
        api_key: String,
        model: String,
        provider_kind: ProviderKind,
    ) -> Self {
        Self {
            base_url,
            api_key,
            model,
            provider_kind,
            accepts_video: false,
        }
    }
}

fn compile_video_request_url(config: &CompileClientConfig) -> String {
    let base = config.base_url.trim_end_matches('/');
    if config.provider_kind == ProviderKind::Anthropic {
        format!("{}/v1/messages", anthropic_api_root(base))
    } else {
        format!("{base}/chat/completions")
    }
}

/// Returns the Anthropic API root, stripping any trailing `/v1` segment so that
/// the caller can append `/v1/...` without producing a doubled `/v1/v1/...`
/// path. Accepts both `https://api.minimaxi.com/anthropic` and
/// `https://api.minimaxi.com/anthropic/v1` as inputs.
fn anthropic_api_root(base: &str) -> &str {
    if base.ends_with("/v1") {
        &base[..base.len() - 3]
    } else {
        base
    }
}

/// Compile a chunk by sending a video clip directly to an Omni-capable model.
/// Uses `video_url` content type with Base64-encoded MP4 data.
pub fn compile_chunk_video(
    config: &CompileClientConfig,
    chunk_index: u32,
    total_chunks: u32,
    anchor_indices: &[u32],
    video_mp4: &[u8],
    prev_summary: Option<&str>,
) -> Result<RawCompileOutput, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(COMPILE_TIMEOUT_SEC))
        .build()
        .map_err(|e| format!("failed to build HTTP client: {e}"))?;
    let system = prompt::system_prompt(prev_summary);
    // Keep model-facing anchors local to this clip, then map them back to the
    // immutable backend anchor IDs after validation.
    let local_anchor_indices = (0..anchor_indices.len())
        .map(|index| index as u32)
        .collect::<Vec<_>>();
    let user_text = prompt::user_message(
        chunk_index,
        total_chunks,
        &local_anchor_indices,
        true,
        Some("video clip with embedded audio"),
    );

    let mut last_error = String::new();
    for attempt in 0..MAX_RETRIES {
        if attempt > 0 {
            std::thread::sleep(Duration::from_millis(
                RETRY_BASE_DELAY_MS * (1u64 << (attempt - 1)),
            ));
        }

        let video_b64 = general_purpose::STANDARD.encode(video_mp4);
        let body = build_video_request_body(config, &video_b64, &system, &user_text)?;

        let url = compile_video_request_url(config);
        let response = match apply_provider_auth(client.post(&url), config)
            .json(&body)
            .send()
        {
            Ok(r) => r,
            Err(e) => {
                // Classify the error type for diagnostics
                let kind = if e.is_timeout() {
                    "timeout"
                } else if e.is_connect() {
                    "connect"
                } else if e.is_builder() {
                    "builder"
                } else if e.is_redirect() {
                    "redirect"
                } else if e.is_status() {
                    "status"
                } else {
                    "unknown"
                };
                // Get the full error chain
                let mut chain = String::new();
                let mut cause = e.source();
                while let Some(src) = cause {
                    if !chain.is_empty() {
                        chain.push_str(" → ");
                    }
                    chain.push_str(&format!("{}", src));
                    cause = src.source();
                }
                last_error = format!(
                    "HTTP request failed (type={kind}): {e}{}",
                    if chain.is_empty() {
                        String::new()
                    } else {
                        format!(" | cause: {chain}")
                    }
                );
                continue;
            }
        };

        let status = response.status();
        let raw_text = response.text().unwrap_or_default();

        if !status.is_success() {
            let payload = serde_json::from_str::<serde_json::Value>(&raw_text)
                .unwrap_or_else(|_| serde_json::json!({ "raw": preview_text(&raw_text) }));
            // Keep the raw vendor payload (including request_id) in the
            // error so users and logs can quote it back to the vendor
            // support team. The user-visible hint is appended after.
            let vendor_message = extract_vendor_message(&payload)
                .unwrap_or_else(|| format!("provider returned {status}: {payload}"));
            match classify_error_response(status, &payload) {
                ErrorDisposition::NonRetryable { kind, .. } => {
                    return Err(format!(
                        "{} ({}). {}",
                        vendor_message,
                        describe_disposition(status, &payload, kind),
                        kind.user_hint()
                    ));
                }
                ErrorDisposition::Retryable => {
                    last_error = format!("provider returned {status}: {payload}");
                    continue;
                }
            }
        }

        let payload: serde_json::Value = match serde_json::from_str(&raw_text) {
            Ok(v) => v,
            Err(e) => {
                last_error = format!(
                    "Invalid JSON response (status {status}): {e} — preview: {:?}",
                    preview_text(&raw_text)
                );
                continue;
            }
        };

        let raw_text = extract_response_text(config.provider_kind, &payload).ok_or_else(|| {
            format!("API returned no content: payload={}", payload)
        })?;

        // Use same repair/parse/validate chain as frame-based path
        match repair::repair_mllm_output(&raw_text) {
            repair::RepairResult::Valid(value) | repair::RepairResult::Repaired(value) => {
                let output = parse_compile_response(&value)?;
                let output = validate_compile_output(output, &local_anchor_indices)?;
                return map_local_anchors(output, anchor_indices);
            }
            repair::RepairResult::Broken { diagnosis } => {
                last_error = format!("Unrecoverable parse error: {diagnosis}");
                continue;
            }
        }
    }

    Err(format!(
        "compile chunk failed after {MAX_RETRIES} attempts: {last_error}"
    ))
}

fn build_video_request_body(
    config: &CompileClientConfig,
    video_b64: &str,
    system: &str,
    user_text: &str,
) -> Result<Value, String> {
    let video_part = if config.provider_kind == ProviderKind::Anthropic {
        // Anthropic-style base64 source. The 64 MB cap is enforced below via
        // validate_video_payload_size.
        validate_video_payload_size(config.provider_kind, video_b64.len())?;
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

/// Outcome of inspecting a non-2xx response from the compile provider.
#[derive(Debug, Clone, PartialEq)]
enum ErrorDisposition {
    /// Vendor rejected the request deterministically; retrying would burn
    /// attempts on the same input. Surface the error to the caller instead.
    NonRetryable { reason: &'static str, kind: VendorErrorKind },
    /// Vendor may have hit a transient condition (network blip, rate limit,
    /// generic 5xx without a structured error envelope). Worth retrying.
    Retryable,
}

/// Coarse category of a non-retryable vendor rejection.
///
/// The category drives the user-facing message emitted by `compile_chunk_video`:
/// safety/policy verdicts get a localized hint suggesting alternative
/// providers or different clips; structural API errors get a generic
/// "provider rejected the request" framing; anything else falls through.
#[derive(Debug, Clone, Copy, PartialEq)]
enum VendorErrorKind {
    /// Vendor's content-safety classifier rejected the input. Observed on
    /// MiniMax M3 with code 1026 ("input new_sensitive") and similar.
    SafetyPolicy,
    /// Vendor returned an Anthropic-Messages `api_error` envelope (or the
    /// OpenAI-compatible equivalent) for a non-safety reason (e.g. invalid
    /// parameter, model not found).
    ApiError,
    /// 4xx with no structured envelope, or anything else deterministic.
    Other,
}

impl VendorErrorKind {
    /// Short, user-visible hint appended after the vendor's own message.
    fn user_hint(self) -> &'static str {
        match self {
            VendorErrorKind::SafetyPolicy => {
                "提示：供应商内容安全策略拒绝该视频。可尝试更换视频，或在设置中切换到其他多模态 Provider。"
            }
            VendorErrorKind::ApiError => {
                "提示：供应商 API 返回错误，请检查 Provider 配置或稍后重试。"
            }
            VendorErrorKind::Other => {
                "提示：供应商拒绝了该请求，请稍后重试或更换 Provider。"
            }
        }
    }
}

/// Classify a non-2xx response so we can fail-fast on deterministic input
/// rejections and only retry on genuinely transient conditions.
///
/// Anthropic-Messages-compatible vendors (e.g. MiniMax M3) return HTTP 500 with
/// a structured `{"error": {"type": "api_error", "code": "1026", ...}}` body
/// when the input video triggers content-safety filters. That envelope is not
/// transient — the next attempt with the same video hits the same rejection.
/// `is_server_error()` alone is too coarse: it would loop three times on a
/// rejection that can only be resolved by the user (different clip, different
/// provider, or a different upload shape like a presigned URL).
fn classify_error_response(status: StatusCode, payload: &Value) -> ErrorDisposition {
    // Well-known deterministic status codes are never worth retrying.
    if matches!(
        status,
        StatusCode::BAD_REQUEST
            | StatusCode::UNAUTHORIZED
            | StatusCode::FORBIDDEN
            | StatusCode::NOT_FOUND
            | StatusCode::CONFLICT
            | StatusCode::UNPROCESSABLE_ENTITY
    ) {
        return ErrorDisposition::NonRetryable {
            reason: "client error status",
            kind: VendorErrorKind::Other,
        };
    }

    // Anthropic-style error envelope: {"type":"error","error":{"type":"api_error",...}}
    // or OpenAI-compatible: {"error":{"type":"...","code":..., "message":...}}
    let error_obj = payload.get("error");
    if let Some(error_obj) = error_obj {
        // Inspect the message text first: safety verdicts can be folded
        // into the `api_error` envelope (e.g. MiniMax M3 with 1026) so
        // they would otherwise be misclassified as plain ApiError.
        if let Some(message) = error_obj.get("message").and_then(Value::as_str) {
            let lower = message.to_ascii_lowercase();
            if lower.contains("input new_sensitive")
                || lower.contains("input is sensitive")
                || lower.contains("input blocked")
                || lower.contains("content policy")
                || lower.contains("safety")
            {
                return ErrorDisposition::NonRetryable {
                    reason: "vendor rejected input as policy/safety violation",
                    kind: VendorErrorKind::SafetyPolicy,
                };
            }
        }

        // `error.type == "api_error"` is the Anthropic-Messages shape used by
        // MiniMax M3 and friends. Any vendor that returns this envelope for
        // an input-side failure should be treated as deterministic; if the
        // message sniffing above did not classify it as a safety verdict
        // it falls into the generic ApiError bucket.
        if let Some(kind) = error_obj.get("type").and_then(Value::as_str) {
            if kind == "api_error" {
                return ErrorDisposition::NonRetryable {
                    reason: "vendor returned api_error envelope",
                    kind: VendorErrorKind::ApiError,
                };
            }
        }
    }

    // Status-based fallback: 408 / 429 / 5xx without a structured envelope
    // are treated as transient. Same behaviour the old `is_retryable_status`
    // produced for vendors that don't speak Anthropic-Messages envelopes.
    if status == StatusCode::REQUEST_TIMEOUT
        || status == StatusCode::TOO_MANY_REQUESTS
        || status.is_server_error()
    {
        ErrorDisposition::Retryable
    } else {
        ErrorDisposition::NonRetryable {
            reason: "non-retryable status",
            kind: VendorErrorKind::Other,
        }
    }
}

fn preview_text(text: &str) -> String {
    text.chars().take(200).collect()
}

/// Pull the human-readable vendor message out of a structured error envelope.
///
/// Tries, in order:
///   - `error.message` (Anthropic-Messages and OpenAI-compatible shape)
///   - `error.code` as a fallback (some vendors only include a numeric code)
///   - `message` at the top level (some legacy shapes)
///
/// Returns `None` if none of those fields are present so the caller can fall
/// back to the full payload.
fn extract_vendor_message(payload: &Value) -> Option<String> {
    let error_obj = payload.get("error");
    if let Some(message) = error_obj
        .and_then(|obj| obj.get("message"))
        .and_then(Value::as_str)
    {
        let trimmed = message.trim();
        if !trimmed.is_empty() {
            return Some(trimmed.to_string());
        }
    }
    if let Some(code) = error_obj
        .and_then(|obj| obj.get("code"))
        .and_then(Value::as_i64)
    {
        return Some(format!("error code {code}"));
    }
    payload
        .get("message")
        .and_then(Value::as_str)
        .map(|m| m.trim().to_string())
        .filter(|m| !m.is_empty())
}

/// Short technical classification string used in the error envelope so the
/// caller can still see WHY the request failed (status + disposition kind).
/// Kept deliberately short — the longer vendor hint lives in
/// `VendorErrorKind::user_hint`.
fn describe_disposition(status: StatusCode, payload: &Value, kind: VendorErrorKind) -> String {
    let kind_label = match kind {
        VendorErrorKind::SafetyPolicy => "safety/policy verdict",
        VendorErrorKind::ApiError => "vendor api_error envelope",
        VendorErrorKind::Other => "vendor error",
    };
    if let Some(req_id) = extract_request_id(payload) {
        format!("{status} {kind_label}, request_id={req_id}")
    } else {
        format!("{status} {kind_label}")
    }
}

/// Try to extract a vendor `request_id` from the error body. Several
/// Anthropic-Messages-compatible vendors use `request_id`, some use
/// `id` (collision with the message id), some nest it under `error`.
/// We try the most common positions in order.
fn extract_request_id(payload: &Value) -> Option<String> {
    const KEYS: &[&str] = &["request_id", "requestId", "trace_id"];
    for key in KEYS {
        if let Some(value) = payload.get(*key).and_then(Value::as_str) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    if let Some(error_obj) = payload.get("error") {
        for key in KEYS {
            if let Some(value) = error_obj.get(*key).and_then(Value::as_str) {
                let trimmed = value.trim();
                if !trimmed.is_empty() {
                    return Some(trimmed.to_string());
                }
            }
        }
    }
    None
}

/// Extract the model's text output from a provider response.
///
/// Two wire shapes are in play, picked by `provider_kind`:
///
/// - **Anthropic-Messages-compatible** (`ProviderKind::Anthropic`):
///   top-level `content: [{type: "text", text: "..."}, ...]`. The text blocks
///   are concatenated with a single newline between them so multi-block
///   responses (e.g. reasoning + final) round-trip into the repair/parse
///   pipeline as one string.
///
/// - **OpenAI-style** (everything else): `choices[0].message.content` —
///   with fallbacks for vendors that use `reasoning_content` or
///   `reasoning` instead.
///
/// Returning `None` signals the caller to surface the raw payload in the
/// "API returned no content" error so the user can debug.
fn extract_response_text(provider_kind: ProviderKind, payload: &Value) -> Option<String> {
    if provider_kind == ProviderKind::Anthropic {
        // Anthropic Messages shape: top-level `content` array of blocks.
        // Each block has a `type` and either a `text` field (text blocks)
        // or an `input` field (tool-use blocks, ignored here). Multiple
        // text blocks are concatenated so downstream JSON repair sees a
        // single coherent string.
        let blocks = payload.get("content").and_then(Value::as_array)?;
        let mut parts: Vec<&str> = Vec::with_capacity(blocks.len());
        for block in blocks {
            let block_obj = block.as_object()?;
            if !matches!(
                block_obj.get("type").and_then(Value::as_str),
                Some("text") | None
            ) {
                continue;
            }
            if let Some(text) = block_obj.get("text").and_then(Value::as_str) {
                if !text.is_empty() {
                    parts.push(text);
                }
            }
        }
        if parts.is_empty() {
            None
        } else {
            Some(parts.join("\n"))
        }
    } else {
        // OpenAI-style: choices[0].message.content (with reasoning fallbacks).
        payload
            .get("choices")
            .and_then(|c| c.as_array())
            .and_then(|c| c.first())
            .and_then(|c| c.get("message"))
            .and_then(|m| {
                ["content", "reasoning_content", "reasoning"]
                    .iter()
                    .find_map(|field| m.get(*field).and_then(|v| v.as_str()))
            })
            .map(|s| s.to_string())
    }
}

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

fn map_local_anchors(
    mut output: RawCompileOutput,
    backend_anchors: &[u32],
) -> Result<RawCompileOutput, String> {
    for event in &mut output.events {
        for anchor in &mut event.event_frame_indexes {
            let local_index = usize::try_from(*anchor)
                .map_err(|_| format!("local anchor index is not representable: {anchor}"))?;
            *anchor = backend_anchors.get(local_index).copied().ok_or_else(|| {
                format!(
                    "local anchor index {anchor} is outside backend anchor map of {} entries",
                    backend_anchors.len()
                )
            })?;
        }
    }
    Ok(output)
}

fn parse_compile_response(value: &Value) -> Result<RawCompileOutput, String> {
    let root = value
        .as_object()
        .ok_or_else(|| "compile response root must be an object".to_string())?;
    let events_value = root
        .get("events")
        .and_then(Value::as_array)
        .ok_or_else(|| "compile response must contain an events array".to_string())?;
    if events_value.len() > 100 {
        return Err("compile response contains too many events".to_string());
    }

    let mut events = Vec::with_capacity(events_value.len());
    for event in events_value {
        let Some(object) = event.as_object() else {
            continue;
        };
        let title = bounded_string(object.get("title"), 200).unwrap_or_default();
        let description = bounded_string(object.get("description"), 8_000).unwrap_or_default();
        if title.is_empty() && description.is_empty() {
            continue;
        }
        // title is required as it becomes the visual_context/section header
        if title.is_empty() {
            continue;
        }

        // Validate event_type against the strictly allowed set
        let raw_type = bounded_string(object.get("event_type"), 40).unwrap_or_default();
        let event_type = if VALID_EVENT_TYPES.contains(&raw_type.as_str()) {
            raw_type
        } else {
            "concept".to_string()
        };

        let indexes = object
            .get("event_frame_indexes")
            .and_then(Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(|value| value.as_u64())
                    .filter(|value| *value <= u32::MAX as u64)
                    .map(|value| value as u32)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        // Validate anchor count — must be exactly 2
        if indexes.len() != 2 {
            continue;
        }

        // Validate anchor order — first must not be after second
        if indexes[0] > indexes[1] {
            continue;
        }

        let confidence = object
            .get("confidence")
            .and_then(Value::as_f64)
            .map(|c| c.clamp(0.0, 1.0) as f32)
            .unwrap_or(0.5);

        events.push(super::RawEvent {
            title,
            event_frame_indexes: indexes,
            description,
            event_type,
            speaker: bounded_string(object.get("speaker"), 200),
            confidence,
        });
    }
    if events.is_empty() {
        return Err("model returned zero valid events after schema validation".to_string());
    }
    let chunk_summary = bounded_string(root.get("chunk_summary"), 12_000).unwrap_or_default();
    Ok(RawCompileOutput {
        events,
        chunk_summary,
    })
}

fn bounded_string(value: Option<&Value>, max_chars: usize) -> Option<String> {
    value.and_then(Value::as_str).map(|text| {
        let trimmed = text.trim();
        if trimmed.chars().count() <= max_chars {
            trimmed.to_string()
        } else {
            trimmed.chars().take(max_chars).collect()
        }
    })
}

fn validate_compile_output(
    mut output: RawCompileOutput,
    frame_indices: &[u32],
) -> Result<RawCompileOutput, String> {
    let allowed = frame_indices.iter().copied().collect::<HashSet<_>>();
    let before = output.events.len();
    let raw_anchors: Vec<String> = output
        .events
        .iter()
        .map(|e| format!("{:?}", e.event_frame_indexes))
        .collect();
    output.events.retain_mut(|event| {
        // Anchor count and order already validated in parse_compile_response;
        // here we only verify they reference known backbone anchors.
        event
            .event_frame_indexes
            .iter()
            .all(|index| allowed.contains(index))
    });
    if output.events.is_empty() {
        return Err(format!(
            "model returned no events with valid anchors (allowed={:?}, raw={}, total={})",
            allowed,
            raw_anchors.join(";"),
            before,
        ));
    }
    if output.chunk_summary.trim().is_empty() {
        output.chunk_summary = output
            .events
            .iter()
            .map(|event| event.title.as_str())
            .collect::<Vec<_>>()
            .join("; ");
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_kind_mapping_is_complete() {
        assert_eq!(
            ProviderKind::from_type_str("google_gemini"),
            ProviderKind::GoogleGemini
        );
        assert_eq!(
            ProviderKind::from_type_str("anthropic_messages"),
            ProviderKind::Anthropic
        );
        assert_eq!(
            ProviderKind::from_type_str("openai_responses"),
            ProviderKind::OpenAIResponses
        );
        assert_eq!(
            ProviderKind::from_profile("openai_compat", "https://api.xiaomimimo.com/v1"),
            ProviderKind::XiaomiMiMo
        );
        assert_eq!(
            ProviderKind::from_type_str("mimo"),
            ProviderKind::XiaomiMiMo
        );
    }

    #[test]
    fn xiaomi_request_uses_documented_multimodal_fields() {
        let mut config = CompileClientConfig::new(
            "https://api.xiaomimimo.com/v1".to_string(),
            "test-key".to_string(),
            "mimo-v2.5".to_string(),
            ProviderKind::XiaomiMiMo,
        );
        config.accepts_video = true;
        let body = build_video_request_body(&config, "abc", "system", "user").unwrap();
        let part = &body["messages"][1]["content"][0];
        assert_eq!(part["type"], "video_url");
        assert_eq!(part["fps"], 1);
        assert_eq!(part["media_resolution"], "default");
        assert!(body.get("max_tokens").is_none());
        assert_eq!(body["max_completion_tokens"], 4096);

        let request = apply_provider_auth(
            reqwest::blocking::Client::new().post("https://example.test"),
            &config,
        )
        .build()
        .unwrap();
        assert_eq!(request.headers()["api-key"], "test-key");
        assert!(request.headers().get("authorization").is_none());
    }

    #[test]
    fn compile_video_request_url_uses_messages_endpoint_for_anthropic() {
        // Token Plan form: base_url without /v1 → code appends /v1/messages.
        let config = CompileClientConfig::new(
            "https://api.minimaxi.com/anthropic".to_string(),
            "test-key".to_string(),
            "MiniMax-M3".to_string(),
            ProviderKind::Anthropic,
        );
        assert_eq!(
            compile_video_request_url(&config),
            "https://api.minimaxi.com/anthropic/v1/messages"
        );

        // Already-versioned form: code must not double /v1.
        let config = CompileClientConfig::new(
            "https://api.minimaxi.com/anthropic/v1/".to_string(),
            "test-key".to_string(),
            "MiniMax-M3".to_string(),
            ProviderKind::Anthropic,
        );
        assert_eq!(
            compile_video_request_url(&config),
            "https://api.minimaxi.com/anthropic/v1/messages"
        );

        // Anthropic-vanilla form: also accepted.
        let config = CompileClientConfig::new(
            "https://api.anthropic.com/v1/".to_string(),
            "test-key".to_string(),
            "claude-sonnet-4-5".to_string(),
            ProviderKind::Anthropic,
        );
        assert_eq!(
            compile_video_request_url(&config),
            "https://api.anthropic.com/v1/messages"
        );

        // Non-Anthropic providers are unaffected.
        let compat = CompileClientConfig::new(
            "https://api.xiaomimimo.com/v1".to_string(),
            "test-key".to_string(),
            "mimo-v2.5".to_string(),
            ProviderKind::XiaomiMiMo,
        );
        assert_eq!(
            compile_video_request_url(&compat),
            "https://api.xiaomimimo.com/v1/chat/completions"
        );
    }

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

    #[test]
    fn transient_status_codes_without_envelope_are_retryable() {
        assert_eq!(
            classify_error_response(StatusCode::REQUEST_TIMEOUT, &serde_json::json!({})),
            ErrorDisposition::Retryable
        );
        assert_eq!(
            classify_error_response(StatusCode::TOO_MANY_REQUESTS, &serde_json::json!({})),
            ErrorDisposition::Retryable
        );
        assert_eq!(
            classify_error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &serde_json::json!({ "raw": "upstream timeout" })
            ),
            ErrorDisposition::Retryable
        );
    }

    #[test]
    fn client_error_statuses_are_never_retryable() {
        for status in [
            StatusCode::BAD_REQUEST,
            StatusCode::UNAUTHORIZED,
            StatusCode::FORBIDDEN,
            StatusCode::NOT_FOUND,
            StatusCode::UNPROCESSABLE_ENTITY,
        ] {
            assert_eq!(
                classify_error_response(status, &serde_json::json!({})),
                ErrorDisposition::NonRetryable {
                    reason: "client error status",
                    kind: VendorErrorKind::Other,
                },
                "status {status} should be non-retryable"
            );
        }
    }

    #[test]
    fn vendor_safety_envelope_with_typed_api_error_is_safety_policy() {
        // MiniMax M3 / Anthropic-Messages envelope for input-side
        // rejection: a 500 status carrying a structured `api_error` whose
        // message advertises a content-safety verdict. The message sniff
        // must beat the typed-envelope classifier so the user sees the
        // "safety policy" hint rather than the generic api_error hint.
        let payload = serde_json::json!({
            "type": "error",
            "error": {
                "type": "api_error",
                "message": "input new_sensitive, messages[1]'s content[0] video is sensitive, please check your input (1026)",
                "code": 1026
            }
        });
        match classify_error_response(StatusCode::INTERNAL_SERVER_ERROR, &payload) {
            ErrorDisposition::NonRetryable { reason, kind } => {
                assert_eq!(reason, "vendor rejected input as policy/safety violation");
                assert_eq!(kind, VendorErrorKind::SafetyPolicy);
            }
            other => panic!("expected NonRetryable, got {other:?}"),
        }
    }

    #[test]
    fn vendor_api_error_envelope_without_safety_message_is_api_error() {
        // A typed `api_error` envelope whose message is non-safety text
        // (e.g. "model not found", "invalid parameter") must fall into
        // the generic ApiError bucket so the user gets the right hint.
        let payload = serde_json::json!({
            "type": "error",
            "error": {
                "type": "api_error",
                "message": "model 'unknown-model' is not supported on this endpoint",
                "code": 404
            }
        });
        match classify_error_response(StatusCode::INTERNAL_SERVER_ERROR, &payload) {
            ErrorDisposition::NonRetryable { reason, kind } => {
                assert_eq!(reason, "vendor returned api_error envelope");
                assert_eq!(kind, VendorErrorKind::ApiError);
            }
            other => panic!("expected NonRetryable, got {other:?}"),
        }
    }

    #[test]
    fn vendor_safety_message_is_non_retryable_without_typed_envelope() {
        // Vendor returns 500 (so it would otherwise look transient) but the
        // message text describes an input-side safety decision. Fail fast.
        let payload = serde_json::json!({
            "error": {
                "message": "input blocked by safety classifier"
            }
        });
        match classify_error_response(StatusCode::INTERNAL_SERVER_ERROR, &payload) {
            ErrorDisposition::NonRetryable { reason, kind } => {
                assert!(reason.contains("policy") || reason.contains("safety"));
                assert_eq!(kind, VendorErrorKind::SafetyPolicy);
            }
            other => panic!("expected NonRetryable, got {other:?}"),
        }
    }

    #[test]
    fn rejects_oversized_xiaomi_payload_before_request() {
        assert!(
            validate_video_payload_size(ProviderKind::XiaomiMiMo, XIAOMI_MAX_BASE64_BYTES + 1)
                .is_err()
        );
        assert!(validate_video_payload_size(
            ProviderKind::OpenAICompat,
            XIAOMI_MAX_BASE64_BYTES + 1
        )
        .is_ok());
    }

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
        // Non-Anthropic, non-Xiaomi providers are unaffected.
        assert!(
            validate_video_payload_size(ProviderKind::OpenAICompat, ANTHROPIC_MAX_REQUEST_BYTES)
                .is_ok()
        );
    }

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

    #[test]
    fn extract_response_text_handles_anthropic_top_level_content_blocks() {
        // VN-AVIDEO-002: the MiniMax M3 (Anthropic-Messages-compatible)
        // response uses top-level `content: [{type:"text", text:"..."}]`,
        // not the OpenAI-style `choices[0].message.content`. The shape
        // observed in production:
        let payload = serde_json::json!({
            "id": "06ad772c198f5ff6ea6712146e8a99f7",
            "model": "MiniMax-M3",
            "role": "assistant",
            "stop_reason": "end_turn",
            "type": "message",
            "base_resp": {"status_code": 0, "status_msg": ""},
            "content": [
                {"type": "text", "text": "{\"events\": [], \"chunk_summary\": \"ok\"}"}
            ],
            "usage": {"input_tokens": 100, "output_tokens": 10}
        });
        let text = extract_response_text(ProviderKind::Anthropic, &payload)
            .expect("Anthropic-style content block must be extracted");
        assert_eq!(text, "{\"events\": [], \"chunk_summary\": \"ok\"}");
    }

    #[test]
    fn extract_response_text_concatenates_multiple_anthropic_text_blocks() {
        // Anthropic-Messages responses may contain several text blocks
        // (e.g. reasoning + final answer). They must be joined so the JSON
        // repair step sees a single coherent string.
        let payload = serde_json::json!({
            "type": "message",
            "content": [
                {"type": "text", "text": "reasoning step"},
                {"type": "text", "text": "{\"events\": [], \"chunk_summary\": \"x\"}"}
            ]
        });
        let text = extract_response_text(ProviderKind::Anthropic, &payload).unwrap();
        assert_eq!(text, "reasoning step\n{\"events\": [], \"chunk_summary\": \"x\"}");
    }

    #[test]
    fn extract_response_text_ignores_non_text_anthropic_blocks() {
        // Tool-use blocks (and other types we don't consume) must be
        // skipped; only `type:"text"` (or untagged) blocks contribute to
        // the extracted text.
        let payload = serde_json::json!({
            "content": [
                {"type": "tool_use", "id": "x", "name": "x", "input": {}},
                {"type": "text", "text": "{\"events\": [], \"chunk_summary\": \"\"}"}
            ]
        });
        let text = extract_response_text(ProviderKind::Anthropic, &payload).unwrap();
        assert_eq!(text, "{\"events\": [], \"chunk_summary\": \"\"}");
    }

    #[test]
    fn extract_response_text_anthropic_returns_none_when_no_text_blocks() {
        let payload = serde_json::json!({
            "type": "message",
            "content": [
                {"type": "tool_use", "id": "x", "name": "x", "input": {}}
            ]
        });
        assert!(extract_response_text(ProviderKind::Anthropic, &payload).is_none());
    }

    #[test]
    fn extract_response_text_openai_choices_message_still_works() {
        // Regression: OpenAI-style responses must continue to extract from
        // choices[0].message.content. Also covers the reasoning_content
        // and reasoning fallbacks some vendors use.
        let payload = serde_json::json!({
            "choices": [{
                "message": {
                    "content": "{\"events\": [], \"chunk_summary\": \"a\"}",
                    "reasoning_content": null
                }
            }]
        });
        let text = extract_response_text(ProviderKind::OpenAICompat, &payload).unwrap();
        assert_eq!(text, "{\"events\": [], \"chunk_summary\": \"a\"}");
    }

    #[test]
    fn extract_response_text_openai_falls_back_to_reasoning_field() {
        let payload = serde_json::json!({
            "choices": [{
                "message": {"reasoning": "{\"events\": [], \"chunk_summary\": \"r\"}"}
            }]
        });
        let text = extract_response_text(ProviderKind::XiaomiMiMo, &payload).unwrap();
        assert_eq!(text, "{\"events\": [], \"chunk_summary\": \"r\"}");
    }

    #[test]
    fn extract_vendor_message_prefers_error_message_field() {
        let payload = serde_json::json!({
            "error": {
                "type": "api_error",
                "message": "input new_sensitive, please check your input",
                "code": 1026
            }
        });
        assert_eq!(
            extract_vendor_message(&payload).as_deref(),
            Some("input new_sensitive, please check your input")
        );
    }

    #[test]
    fn extract_vendor_message_falls_back_to_code() {
        let payload = serde_json::json!({"error": {"code": 1026}});
        assert_eq!(
            extract_vendor_message(&payload).as_deref(),
            Some("error code 1026")
        );
    }

    #[test]
    fn extract_vendor_message_falls_back_to_top_level_message() {
        let payload = serde_json::json!({"message": "service unavailable"});
        assert_eq!(
            extract_vendor_message(&payload).as_deref(),
            Some("service unavailable")
        );
    }

    #[test]
    fn extract_request_id_finds_top_level_request_id() {
        let payload = serde_json::json!({
            "error": {"type": "api_error", "message": "x"},
            "request_id": "06ad7e5302a7e31cebc8d1f79b63b5e1"
        });
        assert_eq!(
            extract_request_id(&payload).as_deref(),
            Some("06ad7e5302a7e31cebc8d1f79b63b5e1")
        );
    }

    #[test]
    fn extract_request_id_finds_nested_error_request_id() {
        let payload = serde_json::json!({
            "error": {"type": "api_error", "message": "x", "request_id": "abc"}
        });
        assert_eq!(extract_request_id(&payload).as_deref(), Some("abc"));
    }

    #[test]
    fn vendor_error_kind_user_hint_mentions_alternative_paths_for_safety() {
        let hint = VendorErrorKind::SafetyPolicy.user_hint();
        assert!(hint.contains("视频") || hint.contains("Provider"));
        // The hint must suggest at least one actionable next step.
        assert!(
            hint.contains("更换") || hint.contains("切换"),
            "safety hint should suggest changing clip or provider: {hint}"
        );
    }

    #[test]
    fn describe_disposition_includes_request_id_when_available() {
        let payload = serde_json::json!({
            "error": {"type": "api_error", "message": "x"},
            "request_id": "abc-123"
        });
        let text = describe_disposition(
            StatusCode::INTERNAL_SERVER_ERROR,
            &payload,
            VendorErrorKind::SafetyPolicy,
        );
        assert!(text.contains("500"));
        assert!(text.contains("safety/policy verdict"));
        assert!(text.contains("abc-123"));
    }

    #[test]
    fn maps_local_anchor_positions_to_backend_ids() {
        let output = RawCompileOutput {
            events: vec![super::super::RawEvent {
                title: "valid".to_string(),
                event_frame_indexes: vec![0, 2],
                description: "mapped".to_string(),
                event_type: "concept".to_string(),
                speaker: None,
                confidence: 0.9,
            }],
            chunk_summary: "summary".to_string(),
        };
        let mapped = map_local_anchors(output, &[94, 99, 105]).unwrap();
        assert_eq!(mapped.events[0].event_frame_indexes, vec![94, 105]);
    }

    #[test]
    fn rejects_local_anchor_positions_outside_backend_map() {
        let output = RawCompileOutput {
            events: vec![super::super::RawEvent {
                title: "invalid".to_string(),
                event_frame_indexes: vec![0, 3],
                description: "not mapped".to_string(),
                event_type: "concept".to_string(),
                speaker: None,
                confidence: 0.9,
            }],
            chunk_summary: "summary".to_string(),
        };
        assert!(map_local_anchors(output, &[94, 99, 105]).is_err());
    }

    #[test]
    fn rejects_untrusted_anchor_indexes() {
        let output = RawCompileOutput {
            events: vec![super::super::RawEvent {
                title: "bad".to_string(),
                event_frame_indexes: vec![1, 999],
                description: "bad anchor".to_string(),
                event_type: "concept".to_string(),
                speaker: None,
                confidence: 0.9,
            }],
            chunk_summary: "summary".to_string(),
        };
        assert!(validate_compile_output(output, &[1, 2]).is_err());
    }

    #[test]
    fn parses_and_bounds_confidence() {
        let value = serde_json::json!({
            "events": [{
                "title": "Intro",
                "event_frame_indexes": [1, 2],
                "description": "Description",
                "event_type": "concept",
                "confidence": 9.0
            }],
            "chunk_summary": "Summary"
        });
        let output = parse_compile_response(&value).unwrap();
        assert_eq!(output.events[0].confidence, 1.0);
    }
}
