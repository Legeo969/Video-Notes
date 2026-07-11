/// Renderer — converts VideoCapsule to Markdown / mindmap format.
///
/// Supports:
/// - `markdown`: 详细结构化 Markdown 笔记
/// - `mindmap`: 大纲式思维导图格式 (缩进列表)

use crate::compile::{CompileMode, Evidence, EvidenceType, VideoCapsule};

/// Render a VideoCapsule to a string using the specified template.
pub fn render(capsule: &VideoCapsule, template: &str) -> Result<String, String> {
    match template {
        "markdown" => render_markdown(capsule),
        "mindmap" => render_mindmap(capsule),
        other => Err(format!("unsupported template: {other} (supported: markdown, mindmap)")),
    }
}

// ---------------------------------------------------------------------------
// Markdown template
// ---------------------------------------------------------------------------

fn render_markdown(capsule: &VideoCapsule) -> Result<String, String> {
    let mut md = String::new();

    // Title
    md.push_str(&format!(
        "# Video Notes — v{}\n\n",
        capsule.version
    ));

    // Metadata block
    md.push_str("## 元数据\n\n");
    md.push_str(&format!("- **编译模式**: {}\n", mode_label(capsule.compilation_mode)));
    md.push_str(&format!("- **模型**: {}\n", capsule.model_used));
    md.push_str(&format!("- **时长**: {:.1}s\n", capsule.total_duration));
    md.push_str(&format!("- **处理时间**: {}\n", capsule.processed_at));
    md.push_str(&format!("- **证据数**: {}\n", capsule.evidences.len()));
    md.push_str(&format!("- **胶囊 ID**: `{}`\n", capsule.capsule_id));
    md.push('\n');

    // Global summary
    md.push_str("## 全局摘要\n\n");
    md.push_str(&capsule.global_summary);
    md.push_str("\n\n");

    // Evidences grouped by chunk
    if capsule.evidences.is_empty() {
        md.push_str("_本编译未生成任何证据。_\n\n");
        return Ok(md);
    }

    md.push_str("## 详细记录\n\n");

    // Group by chunk_sequence
    let mut chunks: Vec<(u32, Vec<&Evidence>)> = Vec::new();
    for ev in &capsule.evidences {
        let idx = chunks.iter().position(|(seq, _)| *seq == ev.chunk_sequence);
        if let Some(i) = idx {
            chunks[i].1.push(ev);
        } else {
            chunks.push((ev.chunk_sequence, vec![ev]));
        }
    }
    chunks.sort_by_key(|(seq, _)| *seq);

    for (chunk_seq, evidences) in &chunks {
        md.push_str(&format!("### 切片 {}\n\n", chunk_seq));

        for ev in evidences {
            md.push_str(&evidence_markdown(ev));
            md.push('\n');
        }
    }

    Ok(md)
}

fn evidence_markdown(ev: &Evidence) -> String {
    let mut s = String::new();

    // Timestamp and type
    let ts = format_timestamp(ev.timestamp_start_sec, ev.timestamp_end_sec);
    let etype = evidence_type_label(ev.evidence_type);
    s.push_str(&format!("**{}** `{}` `{}`\n\n", ev.visual_context, ts, etype));

    // Speaker
    if let Some(ref speaker) = ev.speaker {
        s.push_str(&format!("*讲者: {}*\n\n", speaker));
    }

    // Content
    s.push_str(&format!("{}\n\n", ev.content));

    // Confidence and review flag
    if ev.confidence < 0.4 {
        s.push_str(&format!(
            "> ⚠️ 置信度偏低 ({:.0}%) — 建议人工复核\n\n",
            ev.confidence * 100.0
        ));
    } else {
        s.push_str(&format!("> 置信度: {:.0}%\n\n", ev.confidence * 100.0));
    }

    // Redundancy flag
    if ev.is_redundant {
        s.push_str("> 🔄 此条可能与前后内容重复\n\n");
    }

    s
}

// ---------------------------------------------------------------------------
// Mindmap template
// ---------------------------------------------------------------------------

fn render_mindmap(capsule: &VideoCapsule) -> Result<String, String> {
    let mut mm = String::new();

    mm.push_str(&format!(
        "# Video Notes v{}\n",
        capsule.version
    ));

    // Metadata as first-level siblings
    mm.push_str(&format!(
        "- 编译模式: {} | 模型: {} | 时长: {:.0}s | 证据: {}\n",
        mode_label(capsule.compilation_mode),
        capsule.model_used,
        capsule.total_duration,
        capsule.evidences.len()
    ));

    // Global summary
    mm.push_str("- 全局摘要\n");
    for line in capsule.global_summary.lines() {
        let trimmed = line.trim();
        if !trimmed.is_empty() {
            mm.push_str(&format!("  - {trimmed}\n"));
        }
    }

    // Evidences by chunk
    let mut chunks: Vec<(u32, Vec<&Evidence>)> = Vec::new();
    for ev in &capsule.evidences {
        let idx = chunks.iter().position(|(seq, _)| *seq == ev.chunk_sequence);
        if let Some(i) = idx {
            chunks[i].1.push(ev);
        } else {
            chunks.push((ev.chunk_sequence, vec![ev]));
        }
    }
    chunks.sort_by_key(|(seq, _)| *seq);

    for (chunk_seq, evidences) in &chunks {
        mm.push_str(&format!("- 切片 {chunk_seq}\n"));
        for ev in evidences {
            let ts = format_timestamp(ev.timestamp_start_sec, ev.timestamp_end_sec);
            let etype = evidence_type_label(ev.evidence_type);
            mm.push_str(&format!(
                "  - {} | {} | {} | conf: {:.0}%\n",
                ev.visual_context, ts, etype, ev.confidence * 100.0
            ));
            mm.push_str(&format!("    - {}\n", ev.content.replace('\n', " ")));
        }
    }

    Ok(mm)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn mode_label(mode: CompileMode) -> &'static str {
    match mode {
        CompileMode::CloudPrecision => "云端精确编译",
        CompileMode::LocalDraft => "本地草稿模式",
    }
}

fn evidence_type_label(et: EvidenceType) -> &'static str {
    match et {
        EvidenceType::Fact => "事实",
        EvidenceType::Procedure => "步骤",
        EvidenceType::Concept => "概念",
        EvidenceType::Failure => "错误",
        EvidenceType::Verification => "验证",
        EvidenceType::Draft => "草稿",
    }
}

fn format_timestamp(start: f32, end: f32) -> String {
    if (start - end).abs() < 0.5 {
        format!("[{:.0}s]", start)
    } else {
        format!("[{:.0}s–{:.0}s]", start, end)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile::{CompileMode, Evidence, EvidenceType, VideoCapsule};

    fn sample_capsule() -> VideoCapsule {
        VideoCapsule {
            capsule_id: "abc_1".to_string(),
            source_hash: "abc".to_string(),
            version: 1,
            total_duration: 120.0,
            processed_at: "2026-07-11T12:00:00Z".to_string(),
            model_used: "gpt-4o".to_string(),
            evidences: vec![
                Evidence {
                    id: "e1".to_string(),
                    source_hash: "abc".to_string(),
                    version: 1,
                    chunk_sequence: 0,
                    content: "The speaker introduces Rust as a systems programming language with a focus on memory safety.".to_string(),
                    timestamp_start_sec: 0.0,
                    timestamp_end_sec: 5.0,
                    evidence_type: EvidenceType::Concept,
                    speaker: Some("Alice".to_string()),
                    confidence: 0.92,
                    visual_context: "Introduction to Rust".to_string(),
                    prev_chunk_summary_hash: None,
                    is_redundant: false,
                },
                Evidence {
                    id: "e2".to_string(),
                    source_hash: "abc".to_string(),
                    version: 1,
                    chunk_sequence: 1,
                    content: "Live demonstration of the borrow checker rejecting an invalid reference.".to_string(),
                    timestamp_start_sec: 10.0,
                    timestamp_end_sec: 25.0,
                    evidence_type: EvidenceType::Procedure,
                    speaker: None,
                    confidence: 0.35,
                    visual_context: "Borrow Checker Demo".to_string(),
                    prev_chunk_summary_hash: None,
                    is_redundant: false,
                },
            ],
            global_summary: "A thorough introduction to Rust's ownership system, covering borrowing, references, and the borrow checker.".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
        }
    }

    #[test]
    fn test_render_markdown_includes_title() {
        let md = render_markdown(&sample_capsule()).unwrap();
        assert!(md.contains("# Video Notes — v1"));
        assert!(md.contains("全局摘要"));
        assert!(md.contains("Introduction to Rust"));
        assert!(md.contains("Borrow Checker Demo"));
    }

    #[test]
    fn test_render_markdown_low_confidence_flag() {
        let md = render_markdown(&sample_capsule()).unwrap();
        assert!(md.contains("置信度偏低"));
        assert!(md.contains("人工复核"));
    }

    #[test]
    fn test_render_mindmap_includes_hierarchy() {
        let mm = render_mindmap(&sample_capsule()).unwrap();
        assert!(mm.contains("切片 0"));
        assert!(mm.contains("切片 1"));
        assert!(mm.contains("全局摘要"));
    }

    #[test]
    fn test_render_unknown_template() {
        let cap = sample_capsule();
        let result = render(&cap, "html");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("unsupported template"));
    }

    #[test]
    fn test_render_empty_evidences() {
        let cap = VideoCapsule {
            capsule_id: "empty_1".to_string(),
            source_hash: "empty".to_string(),
            version: 1,
            total_duration: 0.0,
            processed_at: "".to_string(),
            model_used: "test".to_string(),
            evidences: vec![],
            global_summary: "Nothing to see.".to_string(),
            compilation_mode: CompileMode::LocalDraft,
        };
        let md = render(&cap, "markdown").unwrap();
        assert!(md.contains("未生成任何证据"));
    }

    #[test]
    fn test_format_timestamp() {
        assert_eq!(format_timestamp(0.0, 0.0), "[0s]");
        assert_eq!(format_timestamp(5.0, 10.0), "[5s–10s]");
        assert_eq!(format_timestamp(42.5, 43.0), "[43s–43s]"); // < 0.5 apart, shows start
    }

    #[test]
    fn test_evidence_type_labels() {
        assert_eq!(evidence_type_label(EvidenceType::Fact), "事实");
        assert_eq!(evidence_type_label(EvidenceType::Procedure), "步骤");
        assert_eq!(evidence_type_label(EvidenceType::Draft), "草稿");
    }
}