/// MLLM Compile Client — sends raw frames + context to multimodal APIs.
///
/// Supports:
/// - OpenAI Compatible (`/chat/completions`)
/// - Google Gemini (`/models/{model}:generateContent`)
///
/// Key protocol: forces `event_frame_indexes` output, parses structured JSON.

use std::time::Duration;

use base64::engine::general_purpose;
use base64::Engine as _;
use serde_json::{json, Value};

use super::prompt;
use super::repair::{self, RepairResult};
use super::RawCompileOutput;

const COMPILE_TIMEOUT_SEC: u64 = 180;
const MAX_RETRIES: u32 = 3;
const RETRY_BASE_DELAY_MS: u64 = 1000;

/// Provider types supported by the compile client.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ProviderKind {
    OpenAICompat,
    GoogleGemini,
    Anthropic,
}

impl ProviderKind {
    pub fn from_type_str(s: &str) -> Self {
        match s {
            "google_gemini" => Self::GoogleGemini,
            "anthropic_messages" => Self::Anthropic,
            _ => Self::OpenAICompat, // openai_compat, mimo, dashscope, llama_cpp, etc.
        }
    }
}

/// Configuration for the compile client.
#[derive(Debug, Clone)]
pub struct CompileClientConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub provider_kind: ProviderKind,
}

impl CompileClientConfig {
    pub fn new(base_url: String, api_key: String, model: String, provider_kind: ProviderKind) -> Self {
        Self { base_url, api_key, model, provider_kind }
    }
}

/// Compile one video chunk: send frames + transcript to MLLM, parse structured events.
///
/// Returns `RawCompileOutput` with events referencing `event_frame_indexes`.
/// On failure after retries, returns a descriptive error string (never panics).
pub fn compile_chunk(
    config: &CompileClientConfig,
    chunk_index: u32,
    total_chunks: u32,
    frame_indices: &[u32],
    frame_pngs: &[Vec<u8>],
    transcript_text: &str,
    prev_summary: Option<&str>,
) -> Result<RawCompileOutput, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(COMPILE_TIMEOUT_SEC))
        .build()
        .map_err(|e| format!("failed to build HTTP client: {e}"))?;

    let system = prompt::system_prompt(prev_summary);
    let user_text = prompt::user_message(chunk_index, total_chunks, frame_indices, transcript_text);

    let mut last_error = String::new();

    for attempt in 0..MAX_RETRIES {
        if attempt > 0 {
            let delay = RETRY_BASE_DELAY_MS * (1u64 << (attempt - 1)); // 1s, 2s, 4s
            std::thread::sleep(Duration::from_millis(delay));
        }

        match do_compile_request(&client, config, &system, &user_text, frame_pngs) {
            Ok(raw_text) => {
                match repair::repair_mllm_output(&raw_text) {
                    RepairResult::Valid(v) | RepairResult::Repaired(v) => {
                        return parse_compile_response(&v, chunk_index);
                    }
                    RepairResult::Broken { snippet, diagnosis } => {
                        last_error = format!("JSON broken after repair: {diagnosis} (snippet: {snippet})");
                        // Retry
                    }
                }
            }
            Err(e) => {
                last_error = format!("request failed: {e}");
                // Retry
            }
        }
    }

    Err(format!("compile_chunk failed after {MAX_RETRIES} retries: {last_error}"))
}

// ---------------------------------------------------------------------------
// Provider-specific request dispatch
// ---------------------------------------------------------------------------

fn do_compile_request(
    client: &reqwest::blocking::Client,
    config: &CompileClientConfig,
    system: &str,
    user_text: &str,
    frame_pngs: &[Vec<u8>],
) -> Result<String, String> {
    match config.provider_kind {
        ProviderKind::OpenAICompat => openai_chat_completions(client, config, system, user_text, frame_pngs),
        ProviderKind::GoogleGemini => gemini_generate_content(client, config, system, user_text, frame_pngs),
        ProviderKind::Anthropic => anthropic_messages(client, config, system, user_text, frame_pngs),
    }
}

// ---------------------------------------------------------------------------
// OpenAI Compatible: POST {base_url}/chat/completions
// ---------------------------------------------------------------------------

fn openai_chat_completions(
    client: &reqwest::blocking::Client,
    config: &CompileClientConfig,
    system: &str,
    user_text: &str,
    frame_pngs: &[Vec<u8>],
) -> Result<String, String> {
    let url = format!(
        "{}/chat/completions",
        config.base_url.trim_end_matches('/')
    );

    let mut content: Vec<Value> = Vec::new();
    content.push(json!({"type": "text", "text": user_text}));

    // Add up to 4 frames (token budget cap)
    for png in frame_pngs.iter().take(4) {
        let b64 = general_purpose::STANDARD.encode(png);
        content.push(json!({
            "type": "image_url",
            "image_url": {
                "url": format!("data:image/png;base64,{b64}")
            }
        }));
    }

    let body = json!({
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content}
        ],
        "temperature": 0.1,
        "max_tokens": 4096
    });

    let resp = crate::native_engine::with_optional_bearer(
        client.post(&url),
        &config.api_key,
    )
    .json(&body)
    .send()
    .map_err(|e| format!("OpenAI request failed: {e}"))?;

    let status = resp.status();
    let payload: Value = resp.json().map_err(|e| format!("OpenAI response parse failed: {e}"))?;

    if !status.is_success() {
        return Err(format!("OpenAI returned {status}: {payload}"));
    }

    extract_text_content(&payload)
}

// ---------------------------------------------------------------------------
// Google Gemini: POST {base_url}/models/{model}:generateContent
// ---------------------------------------------------------------------------

fn gemini_generate_content(
    client: &reqwest::blocking::Client,
    config: &CompileClientConfig,
    system: &str,
    user_text: &str,
    frame_pngs: &[Vec<u8>],
) -> Result<String, String> {
    let url = format!(
        "{}/models/{}:generateContent",
        config.base_url.trim_end_matches('/'),
        config.model
    );

    let mut parts: Vec<Value> = Vec::new();
    parts.push(json!({"text": user_text}));

    for png in frame_pngs.iter().take(4) {
        let b64 = general_purpose::STANDARD.encode(png);
        parts.push(json!({
            "inline_data": {
                "mime_type": "image/png",
                "data": b64
            }
        }));
    }

    let body = json!({
        "system_instruction": {
            "parts": [{"text": system}]
        },
        "contents": [
            {
                "role": "user",
                "parts": parts
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096
        }
    });

    let resp = client
        .post(&url)
        .header("x-goog-api-key", &config.api_key)
        .json(&body)
        .send()
        .map_err(|e| format!("Gemini request failed: {e}"))?;

    let status = resp.status();
    let payload: Value = resp.json().map_err(|e| format!("Gemini response parse failed: {e}"))?;

    if !status.is_success() {
        return Err(format!("Gemini returned {status}: {payload}"));
    }

    // Extract text from candidates[0].content.parts[0].text
    payload
        .pointer("/candidates/0/content/parts/0/text")
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or_else(|| {
            let err = payload
                .pointer("/promptFeedback/blockReason")
                .and_then(Value::as_str)
                .unwrap_or("no text in Gemini response");
            format!("Gemini: {err}")
        })
}

// ---------------------------------------------------------------------------
// Anthropic: POST {base_url}/messages
// ---------------------------------------------------------------------------

fn anthropic_messages(
    client: &reqwest::blocking::Client,
    config: &CompileClientConfig,
    system: &str,
    user_text: &str,
    frame_pngs: &[Vec<u8>],
) -> Result<String, String> {
    let url = format!(
        "{}/messages",
        config.base_url.trim_end_matches('/')
    );

    let mut content: Vec<Value> = Vec::new();
    content.push(json!({"type": "text", "text": user_text}));

    for png in frame_pngs.iter().take(4) {
        let b64 = general_purpose::STANDARD.encode(png);
        content.push(json!({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64
            }
        }));
    }

    let body = json!({
        "model": config.model,
        "system": system,
        "messages": [
            {"role": "user", "content": content}
        ],
        "temperature": 0.1,
        "max_tokens": 4096
    });

    let resp = client
        .post(&url)
        .header("x-api-key", &config.api_key)
        .header("anthropic-version", "2023-06-01")
        .json(&body)
        .send()
        .map_err(|e| format!("Anthropic request failed: {e}"))?;

    let status = resp.status();
    let payload: Value = resp.json().map_err(|e| format!("Anthropic response parse failed: {e}"))?;

    if !status.is_success() {
        return Err(format!("Anthropic returned {status}: {payload}"));
    }

    // Extract text from content[0].text
    payload
        .pointer("/content/0/text")
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .ok_or_else(|| {
            let stop = payload
                .get("stop_reason")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            format!("Anthropic stopped: {stop}")
        })
}

// ---------------------------------------------------------------------------
// Response parsing
// ---------------------------------------------------------------------------

/// Extract the response text from an OpenAI-compatible response payload.
fn extract_text_content(payload: &Value) -> Result<String, String> {
    payload
        .pointer("/choices/0/message/content")
        .and_then(|c| c.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            let finish = payload
                .pointer("/choices/0/finish_reason")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            format!("OpenAI response empty (finish_reason: {finish})")
        })
}

/// Parse the repaired JSON into a `RawCompileOutput`.
fn parse_compile_response(value: &Value, _chunk_index: u32) -> Result<RawCompileOutput, String> {
    let events = value
        .get("events")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|e| {
                    let title = e.get("title")?.as_str()?.to_string();
                    let event_frame_indexes: Vec<u32> = e
                        .get("event_frame_indexes")
                        .and_then(Value::as_array)
                        .map(|arr| arr.iter().filter_map(|v| v.as_u64().map(|u| u as u32)).collect())
                        .unwrap_or_default();
                    let description = e.get("description").and_then(Value::as_str).unwrap_or("").to_string();
                    let event_type = e.get("event_type").and_then(Value::as_str).unwrap_or("concept").to_string();
                    let speaker = e.get("speaker").and_then(Value::as_str).map(|s| s.to_string());
                    let confidence = e.get("confidence").and_then(Value::as_f64).map(|f| f as f32).unwrap_or(0.5);
                    Some(super::RawEvent {
                        title,
                        event_frame_indexes,
                        description,
                        event_type,
                        speaker,
                        confidence,
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let chunk_summary = value
        .get("chunk_summary")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    Ok(RawCompileOutput {
        events,
        chunk_summary,
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_provider_kind_from_type() {
        assert_eq!(ProviderKind::from_type_str("openai_compat"), ProviderKind::OpenAICompat);
        assert_eq!(ProviderKind::from_type_str("google_gemini"), ProviderKind::GoogleGemini);
        assert_eq!(ProviderKind::from_type_str("anthropic_messages"), ProviderKind::Anthropic);
        assert_eq!(ProviderKind::from_type_str("mimo"), ProviderKind::OpenAICompat);
        assert_eq!(ProviderKind::from_type_str("dashscope"), ProviderKind::OpenAICompat);
        assert_eq!(ProviderKind::from_type_str("llama_cpp"), ProviderKind::OpenAICompat);
    }

    #[test]
    fn test_parse_compile_response_full() {
        let json = json!({
            "events": [
                {
                    "title": "Introduction to Rust",
                    "event_frame_indexes": [0, 3],
                    "description": "The speaker introduces Rust as a systems programming language.",
                    "event_type": "concept",
                    "speaker": "Alice",
                    "confidence": 0.92
                },
                {
                    "title": "Ownership Demo",
                    "event_frame_indexes": [4, 8],
                    "description": "Live coding demonstration of ownership rules.",
                    "event_type": "demonstration",
                    "speaker": null,
                    "confidence": 0.85
                }
            ],
            "chunk_summary": "Introduction to Rust ownership system."
        });

        let result = parse_compile_response(&json, 1).unwrap();
        assert_eq!(result.events.len(), 2);
        assert_eq!(result.events[0].title, "Introduction to Rust");
        assert_eq!(result.events[0].event_frame_indexes, vec![0, 3]);
        assert_eq!(result.events[0].speaker, Some("Alice".to_string()));
        assert!((result.events[0].confidence - 0.92).abs() < 0.01);
        assert_eq!(result.events[1].title, "Ownership Demo");
        assert!(result.events[1].speaker.is_none());
        assert_eq!(result.chunk_summary, "Introduction to Rust ownership system.");
    }

    #[test]
    fn test_parse_compile_response_empty() {
        let json = json!({
            "events": [],
            "chunk_summary": ""
        });
        let result = parse_compile_response(&json, 0).unwrap();
        assert!(result.events.is_empty());
        assert!(result.chunk_summary.is_empty());
    }

    #[test]
    fn test_extract_text_content_missing() {
        let payload = json!({"choices": [{"message": {"content": ""}}]});
        assert!(extract_text_content(&payload).is_err());
    }

    #[test]
    fn test_extract_text_content_present() {
        let payload = json!({"choices": [{"message": {"content": "hello"}}]});
        assert_eq!(extract_text_content(&payload).unwrap(), "hello");
    }
}