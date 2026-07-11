/// Compile prompts — system instructions that enforce the RFC timestamp binding
/// protocol: MLLM must output `event_frame_indexes` (not absolute seconds).

/// System instruction: injected at the start of each compile chunk.
pub fn system_prompt(prev_summary: Option<&str>) -> String {
    let context_bridge = match prev_summary {
        Some(summary) if !summary.is_empty() => {
            format!(
                "\n\n## Previous chunk context (for cross-chunk reference resolution)\n\
                The following is a summary of the immediately preceding video segment. \
                If you see references to concepts/events introduced earlier, connect them.\n\n\
                PREVIOUS_CHUNK_SUMMARY:\n{summary}\n\n\
                END_PREVIOUS_CHUNK_SUMMARY\n"
            )
        }
        _ => String::new(),
    };

    format!(
        "You are a precise video analysis engine. Your task is to analyze the provided \
        video frames and audio transcript, then output a structured JSON list of events \
        that occurred in this segment.\n\
        \n\
        ## CRITICAL RULES\n\
        \n\
        1. TIMESTAMP PROTOCOL: You are given a set of frames, each with an index. \
        You MUST output `event_frame_indexes` as an array of exactly two integers \
        `[start, end]` representing the frame indices you saw. \
        Example: {{\"event_frame_indexes\": [3, 7]}}\n\
        DO NOT output absolute timestamps in seconds. DO NOT output timestamps as floats.\n\
        \n\
        2. Each event should have:\n\
           - \"title\": short descriptive title in Chinese\n\
           - \"event_frame_indexes\": [start_frame_index, end_frame_index]\n\
           - \"description\": detailed description in Chinese (2-4 sentences)\n\
           - \"event_type\": one of \"concept\", \"procedure\", \"fact\", \"demonstration\", \"discussion\", \"summary\"\n\
           - \"speaker\": speaker name if identifiable from context, otherwise null\n\
           - \"confidence\": float between 0.0 and 1.0\n\
        \n\
        3. Be conservative with confidence. Only mark high confidence (0.9+) for \
        clearly visible/audible events.\n\
        \n\
        4. Output valid JSON only. No markdown code fences. No commentary outside the JSON.\n\
        \n\
        5. Format:\n\
        {{\n\
          \"events\": [\n\
            {{\"title\": \"...\", \"event_frame_indexes\": [0, 2], \"description\": \"...\", \"event_type\": \"concept\", \"speaker\": null, \"confidence\": 0.85}},\n\
            ...\n\
          ],\n\
          \"chunk_summary\": \"2-3 sentence summary of this video segment in Chinese\"\n\
        }}{context_bridge}"
    )
}

/// User message template: provides frame context and transcript text.
pub fn user_message(
    chunk_index: u32,
    total_chunks: u32,
    frame_indices: &[u32],
    transcript_text: &str,
) -> String {
    let frame_list: String = frame_indices
        .iter()
        .map(|i| format!("  frame_{i}: index={i}"))
        .collect::<Vec<_>>()
        .join("\n");
    let count = frame_indices.len();

    format!(
        "## Video segment {chunk_index}/{total_chunks}\n\n\
        I am providing you with {count} frames from this segment. Their indices are:\n\
        {frame_list}\n\n\
        Audio transcript for this segment:\n\
        {transcript_text}\n\n\
        Analyze the content and output events with frame index references."
    )
}

/// System prompt for the final merge that concatenates all chunk summaries.
#[allow(dead_code)]
pub fn merge_summaries_prompt(chunk_summaries: &[(u32, String)]) -> String {
    let chunks: String = chunk_summaries
        .iter()
        .map(|(idx, summary)| format!("--- Chunk {idx} ---\n{summary}\n"))
        .collect::<Vec<_>>()
        .join("\n");

    format!(
        "You are a video note compiler. Below are summaries of consecutive video segments.\n\
        Merge them into a coherent, well-structured full-video summary in Chinese.\n\
        Deduplicate repeated concepts. Preserve the chronological order.\n\n\
        {chunks}\n\n\
        Output a single merged summary in Chinese, 3-5 paragraphs."
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_system_prompt_contains_frame_indexes_constraint() {
        let prompt = system_prompt(None);
        assert!(
            prompt.contains("event_frame_indexes"),
            "prompt must require frame index output"
        );
        assert!(
            prompt.contains("DO NOT output absolute timestamps"),
            "prompt must forbid absolute timestamps"
        );
    }

    #[test]
    fn test_system_prompt_with_prev_summary_includes_context() {
        let prompt = system_prompt(Some("Previous segment discussed Rust ownership."));
        assert!(prompt.contains("PREVIOUS_CHUNK_SUMMARY"));
        assert!(prompt.contains("Previous segment discussed Rust ownership."));
    }

    #[test]
    fn test_system_prompt_without_prev_summary_omits_context() {
        let prompt = system_prompt(Some(""));
        assert!(!prompt.contains("PREVIOUS_CHUNK_SUMMARY"));
    }

    #[test]
    fn test_user_message_includes_frame_list() {
        let msg = user_message(1, 3, &[0, 1, 2, 3], "Hello world");
        assert!(msg.contains("1/3"));
        assert!(msg.contains("frame_0"));
        assert!(msg.contains("frame_3"));
        assert!(msg.contains("Hello world"));
    }

    #[test]
    fn test_merge_summaries_prompt() {
        let chunks = vec![(1, "intro".into()), (2, "main content".into())];
        let prompt = merge_summaries_prompt(&chunks);
        assert!(prompt.contains("Chunk 1"));
        assert!(prompt.contains("Chunk 2"));
        assert!(prompt.contains("intro"));
        assert!(prompt.contains("main content"));
    }
}