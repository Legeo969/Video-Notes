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
            last_error = format!("provider returned {status}: {payload}");
            match classify_error_response(status, &payload) {
                ErrorDisposition::NonRetryable { reason } => {
                    return Err(format!("compile chunk rejected by provider: {last_error} ({reason})"));
                }
                ErrorDisposition::Retryable => continue,
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

        let raw_text = payload
            .get("choices")
            .and_then(|c| c.as_array())
            .and_then(|c| c.first())
            .and_then(|c| c.get("message"))
            .and_then(|m| {
                // content → reasoning_content (some providers use this as output field
                // instead of chain-of-thought) → reasoning (Anthropic-style)
                ["content", "reasoning_content", "reasoning"]
                    .iter()
                    .find_map(|field| m.get(*field).and_then(|v| v.as_str()))
            })
            .ok_or_else(|| format!("API returned no content: payload={}", payload))?;

        // Use same repair/parse/validate chain as frame-based path
        match repair::repair_mllm_output(raw_text) {
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
    NonRetryable { reason: &'static str },
    /// Vendor may have hit a transient condition (network blip, rate limit,
    /// generic 5xx without a structured error envelope). Worth retrying.
    Retryable,
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
        };
    }

    // Anthropic-style error envelope: {"type":"error","error":{"type":"api_error",...}}
    // or OpenAI-compatible: {"error":{"type":"...","code":..., "message":...}}
    let error_obj = payload.get("error");
    if let Some(error_obj) = error_obj {
        // `error.type == "api_error"` is the Anthropic-Messages shape used by
        // MiniMax M3 and friends. Any vendor that returns this envelope for an
        // input-side failure should be treated as deterministic.
        if let Some(kind) = error_obj.get("type").and_then(Value::as_str) {
            if kind == "api_error" {
                return ErrorDisposition::NonRetryable {
                    reason: "vendor returned api_error envelope",
                };
            }
        }

        // Belt-and-braces: catch input-safety rejections even when vendors
        // embed the verdict in the message string instead of a typed code.
        // Examples observed: "input new_sensitive, ...", "input blocked",
        // "content policy violation".
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
        }
    }
}

fn preview_text(text: &str) -> String {
    text.chars().take(200).collect()
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
                ErrorDisposition::NonRetryable { reason: "client error status" },
                "status {status} should be non-retryable"
            );
        }
    }

    #[test]
    fn vendor_api_error_envelope_is_non_retryable() {
        // MiniMax M3 / Anthropic-Messages style envelope for input-side
        // rejection: a 500 status carrying a structured `api_error`.
        let payload = serde_json::json!({
            "type": "error",
            "error": {
                "type": "api_error",
                "message": "input new_sensitive, messages[1]'s content[0] video is sensitive, please check your input (1026)",
                "code": 1026
            }
        });
        match classify_error_response(StatusCode::INTERNAL_SERVER_ERROR, &payload) {
            ErrorDisposition::NonRetryable { reason } => {
                assert_eq!(reason, "vendor returned api_error envelope");
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
            ErrorDisposition::NonRetryable { reason } => {
                assert!(reason.contains("policy") || reason.contains("safety"));
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
