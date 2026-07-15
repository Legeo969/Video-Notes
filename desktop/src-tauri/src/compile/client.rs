//! Capability-aware multimodal compile client.

use std::collections::HashSet;
use std::error::Error;
use std::time::Duration;

use base64::engine::general_purpose;
use base64::Engine as _;
use serde_json::Value;

use super::prompt;
use super::repair;
use super::RawCompileOutput;

const COMPILE_TIMEOUT_SEC: u64 = 420;
const MAX_RETRIES: u32 = 3;
const RETRY_BASE_DELAY_MS: u64 = 1_000;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ProviderKind {
    OpenAICompat,
    OpenAIResponses,
    GoogleGemini,
    Anthropic,
}

impl ProviderKind {
    pub fn from_type_str(value: &str) -> Self {
        match value {
            "google_gemini" => Self::GoogleGemini,
            "anthropic_messages" => Self::Anthropic,
            "openai_responses" => Self::OpenAIResponses,
            _ => Self::OpenAICompat,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CompileClientConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    #[allow(dead_code)]
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
    let user_text = prompt::user_message(
        chunk_index,
        total_chunks,
        anchor_indices,
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
        let body = serde_json::json!({
            "model": config.model,
            "messages": [
                { "role": "system", "content": system },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": format!("data:video/mp4;base64,{}", video_b64)
                            }
                        },
                        { "type": "text", "text": user_text }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4096
        });

        let url = format!("{}/chat/completions", config.base_url.trim_end_matches('/'));
        let response =
            match crate::native_engine::with_optional_bearer(client.post(&url), &config.api_key)
                .json(&body)
                .send()
            {
                Ok(r) => r,
                Err(e) => {
                    // Classify the error type for diagnostics
                    let kind = if e.is_timeout() { "timeout" }
                        else if e.is_connect() { "connect" }
                        else if e.is_builder() { "builder" }
                        else if e.is_redirect() { "redirect" }
                        else if e.is_status() { "status" }
                        else { "unknown" };
                    // Get the full error chain
                    let mut chain = String::new();
                    let mut cause = e.source();
                    while let Some(src) = cause {
                        if !chain.is_empty() { chain.push_str(" → "); }
                        chain.push_str(&format!("{}", src));
                        cause = src.source();
                    }
                    last_error = format!("HTTP request failed (type={kind}): {e}{}",
                        if chain.is_empty() { String::new() } else { format!(" | cause: {chain}") });
                    continue;
                }
            };

        let status = response.status();
        let raw_text = response.text().unwrap_or_default();
        let payload: serde_json::Value = match serde_json::from_str(&raw_text) {
            Ok(v) => v,
            Err(e) => {
                let preview = &raw_text[..raw_text.len().min(200)];
                last_error = format!("Invalid JSON response (status {status}): {e} — preview: {preview:?}");
                continue;
            }
        };

        if !status.is_success() {
            last_error = format!("provider returned {status}: {payload}");
            continue;
        }

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
                return validate_compile_output(output, anchor_indices);
            }
            repair::RepairResult::Broken {
                snippet: _,
                diagnosis,
            } => {
                last_error = format!("Unrecoverable parse error: {diagnosis}");
                continue;
            }
        }
    }

    Err(format!(
        "compile chunk failed after {MAX_RETRIES} attempts: {last_error}"
    ))
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
        events.push(super::RawEvent {
            title,
            event_frame_indexes: indexes,
            description,
            event_type: bounded_string(object.get("event_type"), 40)
                .unwrap_or_else(|| "concept".to_string()),
            speaker: bounded_string(object.get("speaker"), 200),
            confidence: object
                .get("confidence")
                .and_then(Value::as_f64)
                .unwrap_or(0.5)
                .clamp(0.0, 1.0) as f32,
        });
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
        if event.event_frame_indexes.len() != 2 {
            return false;
        }
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
