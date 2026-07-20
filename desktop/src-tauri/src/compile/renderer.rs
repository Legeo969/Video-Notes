/// Renderer — converts VideoCapsule to Markdown / mindmap format.
///
/// Supports:
/// - `default` / `markdown` : 详细结构化笔记
/// - `lecture`              : 课程讲义风格（时间线 + 讲师重点）
/// - `summary`              : 摘要（全局摘要 + 关键要点）
/// - `mindmap`              : 大纲式思维导图
/// - `concise` / `readable` : 简洁易读（按主题分组、去冗余、紧凑格式）
use crate::compile::{Evidence, EvidenceType, VideoCapsule};

pub fn render(capsule: &VideoCapsule, template: &str) -> Result<String, String> {
    match template {
        "default" | "markdown" => render_markdown(capsule),
        "lecture" => render_lecture(capsule),
        "summary" => render_summary(capsule),
        "mindmap" => render_mindmap(capsule),
        "concise" | "readable" => render_concise(capsule),
        other => Err(format!(
            "unsupported template: {other} (supported: default, lecture, summary, mindmap, concise)"
        )),
    }
}

// ── Shared helpers ─────────────────────────────────────────

fn frontmatter(capsule: &VideoCapsule) -> String {
    let mut s = String::new();
    s.push_str("---\n");
    s.push_str(&format!(
        "video_notes_source_hash: {}\n",
        capsule.source_hash
    ));
    s.push_str(&format!("video_notes_version: {}\n", capsule.version));
    s.push_str(&format!("video_notes_capsule_id: {}\n", capsule.capsule_id));
    s.push_str(&format!(
        "video_notes_ir_schema: {}\n",
        capsule.ir_schema_version
    ));
    if !capsule.source_input.is_empty() {
        s.push_str(&format!(
            "video_notes_source_input: {}\n",
            capsule.source_input
        ));
    }
    s.push_str("---\n\n");
    s
}

fn title_line(capsule: &VideoCapsule) -> String {
    let title = if capsule.source_title.trim().is_empty() {
        "Video Notes"
    } else {
        capsule.source_title.trim()
    };
    format!("# {} — v{}\n\n", title, capsule.version)
}

fn metadata_block(capsule: &VideoCapsule) -> String {
    let mut s = String::new();
    s.push_str("## 元数据\n\n");
    s.push_str(&format!("- **编译模式**: {}\n", MODE_LABEL));
    s.push_str(&format!("- **模型**: {}\n", capsule.model_used));
    s.push_str(&format!("- **时长**: {:.1}s\n", capsule.total_duration));
    s.push_str(&format!("- **处理时间**: {}\n", capsule.processed_at));
    s.push_str(&format!("- **证据数**: {}\n", capsule.evidences.len()));
    s.push_str(&format!(
        "- **IR Schema**: v{}\n",
        capsule.ir_schema_version
    ));
    s.push_str(&format!("- **胶囊 ID**: `{}`\n", capsule.capsule_id));
    s.push('\n');
    s
}

/// Group evidence by visual_context (topic), preserving order
fn group_by_topic(capsule: &VideoCapsule) -> Vec<(&str, Vec<&Evidence>)> {
    let mut topics: Vec<(&str, Vec<&Evidence>)> = Vec::new();
    for ev in &capsule.evidences {
        let ctx = ev.visual_context.as_str();
        if let Some((prev_ctx, list)) = topics.last_mut() {
            if *prev_ctx == ctx {
                list.push(ev);
                continue;
            }
        }
        topics.push((ctx, vec![ev]));
    }
    topics
}

// ── Default / Markdown template ────────────────────────────

fn render_markdown(capsule: &VideoCapsule) -> Result<String, String> {
    let mut md = frontmatter(capsule);
    md.push_str(&title_line(capsule));
    md.push_str(&metadata_block(capsule));

    if !capsule.warnings.is_empty() {
        md.push_str("## 编译警告\n\n");
        for w in &capsule.warnings {
            md.push_str(&format!("> ⚠️ {}\n\n", w));
        }
    }

    if !capsule.global_summary.trim().is_empty() {
        let summary: Vec<&str> = capsule
            .global_summary
            .lines()
            .filter(|l| !l.trim().starts_with("[Chunk"))
            .collect();
        if !summary.is_empty() {
            md.push_str("## 全局摘要\n\n");
            md.push_str(&format!("{}\n\n", summary.join("\n")));
        }
    }

    if capsule.evidences.is_empty() {
        md.push_str("_本编译未生成任何证据。_\n\n");
        return Ok(md);
    }

    md.push_str("## 详细记录\n\n");
    for (topic, evidences) in &group_by_topic(capsule) {
        if !topic.is_empty() {
            md.push_str(&format!("### {}\n\n", topic));
        }
        for ev in evidences {
            let s = ev.timestamp_start_sec as u64;
            let e = ev.timestamp_end_sec as u64;
            let ts = if (s as f32 - ev.timestamp_end_sec).abs() < 0.5 {
                format!("[{:02}:{:02}]", s / 60, s % 60)
            } else {
                format!("[{:02}:{:02}–{:02}:{:02}]", s / 60, s % 60, e / 60, e % 60)
            };
            let icon = evidence_icon(ev.evidence_type);
            md.push_str(&format!("- {} {} {}\n", icon, ts, ev.content));
            if ev.confidence < 0.4 {
                md.push_str(&format!("  > 置信度 {:.0}%\n", ev.confidence * 100.0));
            }
        }
        md.push('\n');
    }

    let low_count = capsule
        .evidences
        .iter()
        .filter(|e| e.confidence < 0.4)
        .count();
    if low_count > 0 {
        md.push_str(&format!("> ⚠️ 共 {} 条低置信度笔记\n\n", low_count));
    }
    Ok(md)
}

// ── Lecture template ────────────────────────────────────────

fn render_lecture(capsule: &VideoCapsule) -> Result<String, String> {
    let mut md = frontmatter(capsule);
    let title = if capsule.source_title.trim().is_empty() {
        "Video Notes"
    } else {
        capsule.source_title.trim()
    };
    md.push_str(&format!("# {} — 课程笔记\n\n", title));

    if !capsule.global_summary.trim().is_empty() {
        // Filter out [Chunk N] summary lines
        let summary: Vec<&str> = capsule
            .global_summary
            .lines()
            .filter(|l| !l.trim().starts_with("[Chunk"))
            .collect();
        if !summary.is_empty() {
            md.push_str(&format!("{}\n\n", summary.join("\n")));
        }
    }

    if capsule.evidences.is_empty() {
        return Ok(md);
    }

    md.push_str("## 课堂时间线\n\n");
    for (_topic, evidences) in &group_by_topic(capsule) {
        for ev in evidences {
            let ts = ts_full(ev.timestamp_start_sec, ev.timestamp_end_sec);
            let icon = evidence_icon(ev.evidence_type);
            md.push_str(&format!("- {} {} {}\n", icon, ts, ev.content));
            if ev.confidence < 0.4 {
                md.push_str(&format!("  > 置信度 {:.0}%\n", ev.confidence * 100.0));
            }
        }
    }

    md.push_str("---\n\n");
    md.push_str(&format!(
        "_编译于 {} | 模型: {} | 时长: {:.1}s_\n",
        capsule.processed_at, capsule.model_used, capsule.total_duration
    ));
    Ok(md)
}

// ── Summary template ────────────────────────────────────────

fn render_summary(capsule: &VideoCapsule) -> Result<String, String> {
    let mut md = frontmatter(capsule);
    let title = if capsule.source_title.trim().is_empty() {
        "Video Notes"
    } else {
        capsule.source_title.trim()
    };
    md.push_str(&format!("# {} — 摘要\n\n", title));
    md.push_str(&format!(
        "> ⏱ {:.0}s · 模型: {} · {} 条笔记\n\n",
        capsule.total_duration,
        capsule.model_used,
        capsule.evidences.len()
    ));

    if !capsule.global_summary.trim().is_empty() {
        let summary: Vec<&str> = capsule
            .global_summary
            .lines()
            .filter(|l| !l.trim().starts_with("[Chunk"))
            .collect();
        if !summary.is_empty() {
            md.push_str(&format!("{}\n\n", summary.join("\n")));
        }
    }

    if capsule.evidences.is_empty() {
        return Ok(md);
    }

    md.push_str("## 关键要点\n\n");
    for (_topic, evidences) in &group_by_topic(capsule) {
        for ev in evidences {
            if ev.confidence < 0.3 {
                continue;
            }
            let ts = ts_full(ev.timestamp_start_sec, ev.timestamp_end_sec);
            let icon = evidence_icon(ev.evidence_type);
            md.push_str(&format!("- {} **{}** {}\n", icon, ts, ev.content));
        }
    }
    md.push('\n');

    let low_conf = capsule
        .evidences
        .iter()
        .filter(|e| e.confidence < 0.4)
        .count();
    if low_conf > 0 {
        md.push_str(&format!("> ⚠️ {} 条笔记置信度偏低\n\n", low_conf));
    }
    Ok(md)
}

// ── Concise / Readable template ────────────────────────────

fn render_concise(capsule: &VideoCapsule) -> Result<String, String> {
    let mut md = String::new();
    let title = if capsule.source_title.trim().is_empty() {
        "Video Notes"
    } else {
        capsule.source_title.trim()
    };
    md.push_str(&format!("# {} — 简明笔记\n\n", title));

    // Quick stats
    md.push_str(&format!(
        "> ⏱ {:?} · {} 条笔记 · 模式: {}\n\n",
        core::time::Duration::from_secs_f32(capsule.total_duration),
        capsule.evidences.len(),
        MODE_LABEL
    ));

    // Global summary (filtered)
    let summary_lines: Vec<&str> = capsule
        .global_summary
        .lines()
        .filter(|l| !l.trim().starts_with("[Chunk"))
        .collect();
    if !summary_lines.is_empty() {
        md.push_str(&format!("{}\n\n", summary_lines.join("\n")));
    }

    if capsule.evidences.is_empty() {
        md.push_str("_本编译未生成任何证据。_\n\n");
        return Ok(md);
    }

    // Group by visual_context (topic), preserving order
    let mut topics: Vec<(&str, Vec<&Evidence>)> = Vec::new();
    for ev in &capsule.evidences {
        let ctx = ev.visual_context.as_str();
        let last = topics.last_mut();
        if let Some((prev_ctx, list)) = last {
            if *prev_ctx == ctx {
                list.push(ev);
                continue;
            }
        }
        topics.push((ctx, vec![ev]));
    }

    for (topic, evidences) in &topics {
        if !topic.is_empty() {
            md.push_str(&format!("### {}\n\n", topic));
        }

        for ev in evidences {
            let ts = ts_full(ev.timestamp_start_sec, ev.timestamp_end_sec);
            let icon = evidence_icon(ev.evidence_type);
            let speaker = ev.speaker.as_deref().unwrap_or("");

            // Compact line: icon timestamp content
            md.push_str(&format!("- {} **{}** {}", icon, ts, ev.content));

            if !speaker.is_empty() {
                md.push_str(&format!(" _— {}_", speaker));
            }
            md.push('\n');

            // Only show confidence/review when notable
            if ev.needs_review || ev.confidence < 0.4 {
                md.push_str(&format!(
                    "  > ⚠️ 置信度 {:.0}% — 建议复核\n",
                    ev.confidence * 100.0
                ));
                if !ev.review_reasons.is_empty() {
                    md.push_str(&format!("  > 原因: {}\n", ev.review_reasons.join(", ")));
                }
            }
        }
        md.push('\n');
    }

    // Warning count at bottom
    let low_conf_count = capsule
        .evidences
        .iter()
        .filter(|e| e.confidence < 0.4)
        .count();
    if low_conf_count > 0 {
        md.push_str(&format!(
            "> ⚠️ 共 {} 条低置信度笔记，建议查看完整版\n\n",
            low_conf_count
        ));
    }

    Ok(md)
}

// ── Mindmap template ────────────────────────────────────────

fn render_mindmap(capsule: &VideoCapsule) -> Result<String, String> {
    let mut mm = String::new();
    mm.push_str(&format!("# Video Notes v{}\n", capsule.version));
    mm.push_str(&format!(
        "- 编译模式: {} | 模型: {} | 时长: {:.0}s | 笔记: {}\n",
        MODE_LABEL,
        capsule.model_used,
        capsule.total_duration,
        capsule.evidences.len()
    ));

    if !capsule.global_summary.trim().is_empty() {
        mm.push_str("- 全局摘要\n");
        for line in capsule.global_summary.lines() {
            let trimmed = line.trim();
            if !trimmed.is_empty() && !trimmed.starts_with("[Chunk") {
                mm.push_str(&format!("  - {trimmed}\n"));
            }
        }
    }

    for (topic, evidences) in &group_by_topic(capsule) {
        let label = if topic.is_empty() { "其他" } else { topic };
        mm.push_str(&format!("- {label}\n"));
        for ev in evidences {
            let ts = ts_full(ev.timestamp_start_sec, ev.timestamp_end_sec);
            let icon = evidence_icon(ev.evidence_type);
            mm.push_str(&format!(
                "  - {} {} | {} | conf: {:.0}%\n",
                icon,
                ts,
                ev.content,
                ev.confidence * 100.0
            ));
        }
    }
    Ok(mm)
}

// ── Helpers ─────────────────────────────────────────────────

// VN-LDRFT-001 removed CompileMode::LocalDraft; CloudPrecision is the only
// compilation mode now, so the human-readable label is a constant.
const MODE_LABEL: &str = "云端精确编译";

/// Formatted timestamp with brackets: [MM:SS] or [MM:SS–MM:SS]
fn ts_full(start: f32, end: f32) -> String {
    let s = start as u64;
    let e = end as u64;
    if s == e || (e - s) <= 1 {
        format!("[{:02}:{:02}]", s / 60, s % 60)
    } else {
        format!("[{:02}:{:02}–{:02}:{:02}]", s / 60, s % 60, e / 60, e % 60)
    }
}

/// Emoji icon for evidence type (concise template)
fn evidence_icon(et: EvidenceType) -> &'static str {
    match et {
        EvidenceType::Fact => "\u{1f4a1}",        // 💡
        EvidenceType::Procedure => "\u{1f3af}",   // 🎯
        EvidenceType::Concept => "\u{1f4d6}",     // 📖
        EvidenceType::Failure => "\u{26a0}️",      // ⚠️
        EvidenceType::Verification => "\u{2705}", // ✅
        EvidenceType::Draft => "\u{1f58a}️",       // 🖊️
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile::{CompileMode, Evidence, EvidenceType, VideoCapsule};

    fn sample_capsule() -> VideoCapsule {
        VideoCapsule {
            ir_schema_version: 2,
            capsule_id: "abc_1".to_string(),
            source_hash: "abc".to_string(),
            source_title: "Rust course".to_string(),
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
                    content: "The speaker introduces Rust as a systems programming language."
                        .to_string(),
                    timestamp_start_sec: 0.0,
                    timestamp_end_sec: 5.0,
                    evidence_type: EvidenceType::Concept,
                    speaker: Some("Alice".to_string()),
                    confidence: 0.92,
                    visual_context: "Introduction".to_string(),
                    prev_chunk_summary_hash: None,
                    is_redundant: false,
                    needs_review: false,
                    review_reasons: vec![],
                },
                Evidence {
                    id: "e2".to_string(),
                    source_hash: "abc".to_string(),
                    version: 1,
                    chunk_sequence: 1,
                    content: "Live demonstration of borrow checker.".to_string(),
                    timestamp_start_sec: 10.0,
                    timestamp_end_sec: 25.0,
                    evidence_type: EvidenceType::Procedure,
                    speaker: None,
                    confidence: 0.35,
                    visual_context: "Borrow Checker".to_string(),
                    prev_chunk_summary_hash: None,
                    is_redundant: false,
                    needs_review: true,
                    review_reasons: vec!["low_confidence".to_string()],
                },
            ],
            global_summary: "Introduction to Rust's ownership system.".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
            warnings: vec![],
            source_input: String::new(),
        }
    }

    #[test]
    fn test_render_default() {
        let md = render(&sample_capsule(), "default").unwrap();
        assert!(md.contains("详细记录"));
    }

    #[test]
    fn test_render_lecture() {
        let md = render(&sample_capsule(), "lecture").unwrap();
        assert!(md.contains("课堂时间线"));
    }

    #[test]
    fn test_render_summary() {
        let md = render(&sample_capsule(), "summary").unwrap();
        assert!(md.contains("关键要点"));
    }

    #[test]
    fn test_render_mindmap() {
        let mm = render(&sample_capsule(), "mindmap").unwrap();
        assert!(mm.contains("Introduction"));
        assert!(mm.contains("Rust as a systems programming language"));
    }

    #[test]
    fn test_render_concise() {
        let md = render(&sample_capsule(), "concise").unwrap();
        assert!(md.contains("简明笔记"));
        assert!(md.contains("💡") || md.contains("📖")); // has type icons
        assert!(md.contains("置信度")); // low conf note flagged
    }

    #[test]
    fn test_render_unknown_template() {
        assert!(render(&sample_capsule(), "html").is_err());
    }

    #[test]
    fn test_ts_full() {
        assert_eq!(ts_full(0.0, 0.0), "[00:00]");
        assert_eq!(ts_full(5.0, 10.0), "[00:05–00:10]");
        assert_eq!(ts_full(65.0, 130.0), "[01:05–02:10]");
    }
}
