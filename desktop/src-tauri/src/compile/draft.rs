/// Local Draft Mode — graceful degradation when offline.
///
/// When the MLLM API is unreachable, the system falls back to a local draft engine
/// that produces low-confidence keywords + rough summary instead of failing.
///
/// Current implementation: stub that returns a minimal draft.
/// Future: WASM-based Moondream/TinyLlama via wasmtime or rusty_v8.

use std::time::Duration;

use crate::compile::{CompileMode, RawCompileOutput};

/// Timeout for network connectivity check.
const NETWORK_CHECK_TIMEOUT_SEC: u64 = 3;

/// Check if the MLLM API is reachable.
///
/// Returns `true` if the API endpoint responds within the timeout.
/// Uses a minimal TCP connection check (not a full HTTP request).
pub fn check_network_connectivity(api_base_url: &str) -> bool {
    // Parse the host from the URL
    let host = api_base_url
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");

    if host.is_empty() {
        return false;
    }

    let port = if api_base_url.starts_with("https://") {
        443
    } else {
        80
    };

    std::net::TcpStream::connect_timeout(
        &format!("{host}:{port}")
            .parse()
            .unwrap_or_else(|_| panic!("invalid address: {host}:{port}")),
        Duration::from_secs(NETWORK_CHECK_TIMEOUT_SEC),
    )
    .is_ok()
}

/// Determine the compilation mode based on network availability.
pub fn resolve_compile_mode(api_base_url: &str, prefer_draft: bool) -> CompileMode {
    if prefer_draft {
        return CompileMode::LocalDraft;
    }
    if check_network_connectivity(api_base_url) {
        CompileMode::CloudPrecision
    } else {
        CompileMode::LocalDraft
    }
}

/// Generate a local draft when the cloud MLLM is unavailable.
///
/// The draft contains:
/// - Minimal event list with low confidence (0.3)
/// - All evidence_type set to `Draft`
/// - Rough summary based on available metadata
///
/// Current implementation: heuristic-based stub.
/// Future: WASM Moondream/TinyLlama inference.
pub fn generate_local_draft(
    video_title: &str,
    duration_sec: f64,
    frame_count: usize,
    _frame_pngs: &[Vec<u8>],
    _transcript_text: &str,
) -> RawCompileOutput {
    let title = if video_title.is_empty() {
        "Untitled Video"
    } else {
        video_title
    };

    let event = crate::compile::RawEvent {
        title: format!("Draft: {title}"),
        event_frame_indexes: vec![0, (frame_count.max(1) - 1) as u32],
        description: format!(
            "Local draft mode. Video duration: {:.0}s, frames sampled: {frame_count}. \
            Full analysis unavailable offline.",
            duration_sec
        ),
        event_type: "concept".to_string(),
        speaker: None,
        confidence: 0.3,
    };

    RawCompileOutput {
        events: vec![event],
        chunk_summary: format!(
            "[DRAFT] {title} — {:.0}s video with {frame_count} frames. \
            Offline mode — run again when online for full analysis.",
            duration_sec
        ),
    }
}

/// Estimated cost display for a cloud compile (informational).
#[allow(dead_code)]
pub fn estimated_cost(frame_count: usize, duration_sec: f64) -> String {
    // Rough estimate: ~$0.01 per 1000 input tokens for GPT-4o
    // Each frame ~200 tokens, audio ~1 token per 0.1s
    let frame_tokens = frame_count * 200;
    let audio_tokens = (duration_sec * 10.0) as usize;
    let total_tokens = frame_tokens + audio_tokens + 500; // overhead
    let cost_usd = total_tokens as f64 * 0.00001; // $0.01/1K tokens = $0.00001 per token

    format!(
        "~{total_tokens} tokens (~{:.4} USD, {frame_count} frames × {:.0}s audio)",
        cost_usd, duration_sec
    )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_compile_mode_prefer_draft() {
        let mode = resolve_compile_mode("https://api.openai.com/v1", true);
        assert_eq!(mode, CompileMode::LocalDraft);
    }

    #[test]
    fn test_generate_local_draft_has_low_confidence() {
        let draft = generate_local_draft("Test Video", 120.0, 10, &[], "");
        assert_eq!(draft.events.len(), 1);
        assert!((draft.events[0].confidence - 0.3).abs() < 0.01);
        assert!(draft.chunk_summary.contains("[DRAFT]"));
    }

    #[test]
    fn test_generate_local_draft_empty_title() {
        let draft = generate_local_draft("", 60.0, 5, &[], "");
        assert!(draft.events[0].title.contains("Untitled"));
    }

    #[test]
    fn test_estimated_cost_non_negative() {
        let cost = estimated_cost(50, 600.0);
        assert!(!cost.is_empty());
        assert!(cost.contains("tokens"));
    }

    #[test]
    fn test_check_network_connectivity_invalid_url() {
        // Should not panic on garbage input
        let result = check_network_connectivity("not-a-url");
        assert!(!result);
    }
}