use serde_json::{json, Value};

/// Build a knowledge graph from Markdown note content.
/// Returns a serde_json::Value matching the KnowledgeNode structure.
pub fn build_knowledge_graph(content: &str) -> Value {
    let mut chapters: Vec<super::KnowledgeNode> = Vec::new();
    let mut current_chapter: Option<(String, Vec<super::KnowledgeNode>)> = None;
    let mut current_section: Option<(String, Vec<String>)> = None;
    let mut in_code_block = false;

    for line in content.lines() {
        let trimmed = line.trim();

        // Track code fences to avoid parsing code content as headings
        if trimmed.starts_with("```") {
            in_code_block = !in_code_block;
            continue;
        }
        if in_code_block {
            continue;
        }

        if trimmed.starts_with("## ") {
            // Flush current section
            let chapter_slug = current_chapter.as_ref()
                .map(|(l, _)| slug(l))
                .unwrap_or_default();
            if let Some((sec_label, concepts)) = current_section.take() {
                let children = concepts.into_iter().map(|c| super::KnowledgeNode {
                    id: format!("{}-{}", slug(&sec_label), slug(&c)),
                    label: c,
                    kind: super::NodeKind::Concept,
                    children: Vec::new(),
                }).collect();
                if let Some((_, ref mut sections)) = current_chapter {
                    sections.push(super::KnowledgeNode {
                        id: format!("{}-{}", chapter_slug, slug(&sec_label)),
                        label: sec_label,
                        kind: super::NodeKind::Section,
                        children,
                    });
                }
            }
            // Flush current chapter
            if let Some((label, sections)) = current_chapter.take() {
                chapters.push(super::KnowledgeNode {
                    id: slug(&label),
                    label,
                    kind: super::NodeKind::Chapter,
                    children: sections,
                });
            }
            current_chapter = Some((trimmed[3..].trim().to_string(), Vec::new()));
        } else if trimmed.starts_with("### ") {
            // Flush current section
            let chapter_slug = current_chapter.as_ref()
                .map(|(l, _)| slug(l))
                .unwrap_or_default();
            if let Some((sec_label, concepts)) = current_section.take() {
                let children = concepts.into_iter().map(|c| super::KnowledgeNode {
                    id: format!("{}-{}", slug(&sec_label), slug(&c)),
                    label: c,
                    kind: super::NodeKind::Concept,
                    children: Vec::new(),
                }).collect();
                if let Some((_, ref mut sections)) = current_chapter {
                    sections.push(super::KnowledgeNode {
                        id: format!("{}-{}", chapter_slug, slug(&sec_label)),
                        label: sec_label,
                        kind: super::NodeKind::Section,
                        children,
                    });
                }
            }
            let label = trimmed[4..].trim().to_string();
            current_section = Some((label, Vec::new()));
        } else if trimmed.starts_with("# ") {
            // # heading — same as ## (chapter level)
            let chapter_slug = current_chapter.as_ref()
                .map(|(l, _)| slug(l))
                .unwrap_or_default();
            if let Some((sec_label, concepts)) = current_section.take() {
                let children = concepts.into_iter().map(|c| super::KnowledgeNode {
                    id: format!("{}-{}", slug(&sec_label), slug(&c)),
                    label: c,
                    kind: super::NodeKind::Concept,
                    children: Vec::new(),
                }).collect();
                if let Some((_, ref mut sections)) = current_chapter {
                    sections.push(super::KnowledgeNode {
                        id: format!("{}-{}", chapter_slug, slug(&sec_label)),
                        label: sec_label,
                        kind: super::NodeKind::Section,
                        children,
                    });
                }
            }
            if let Some((label, sections)) = current_chapter.take() {
                chapters.push(super::KnowledgeNode {
                    id: slug(&label),
                    label,
                    kind: super::NodeKind::Chapter,
                    children: sections,
                });
            }
            current_chapter = Some((trimmed[2..].trim().to_string(), Vec::new()));
        } else if let Some(stripped) = trimmed.strip_prefix("- ").or_else(|| trimmed.strip_prefix("* ")) {
            let concept = stripped.trim().to_string();
            if !concept.is_empty() {
                if let Some((_, ref mut concepts)) = current_section {
                    concepts.push(concept);
                } else if let Some((_, ref mut sections)) = current_chapter {
                    // List item without a ### heading — treat as direct chapter concept
                    sections.push(super::KnowledgeNode {
                        id: slug(&concept),
                        label: concept.clone(),
                        kind: super::NodeKind::Concept,
                        children: Vec::new(),
                    });
                }
            }
        }
    }

    // Flush remaining section
    let chapter_slug = current_chapter.as_ref()
        .map(|(l, _)| slug(l))
        .unwrap_or_default();
    if let Some((sec_label, concepts)) = current_section.take() {
        let children = concepts.into_iter().map(|c| super::KnowledgeNode {
            id: format!("{}-{}", slug(&sec_label), slug(&c)),
            label: c,
            kind: super::NodeKind::Concept,
            children: Vec::new(),
        }).collect();
        if let Some((_, ref mut sections)) = current_chapter {
            sections.push(super::KnowledgeNode {
                id: format!("{}-{}", chapter_slug, slug(&sec_label)),
                label: sec_label,
                kind: super::NodeKind::Section,
                children,
            });
        }
    }

    // Flush remaining chapter
    if let Some((label, sections)) = current_chapter.take() {
        chapters.push(super::KnowledgeNode {
            id: slug(&label),
            label,
            kind: super::NodeKind::Chapter,
            children: sections,
        });
    }

    // If no structure found, create a flat list from non-empty lines
    if chapters.is_empty() {
        let concepts: Vec<super::KnowledgeNode> = content.lines()
            .filter(|l| {
                let t = l.trim();
                !t.is_empty() && !t.starts_with('#') && !t.starts_with("![")

            })
            .take(20)
            .map(|l| super::KnowledgeNode {
                id: slug(l.trim()),
                label: l.trim().to_string(),
                kind: super::NodeKind::Concept,
                children: Vec::new(),
            })
            .collect();
        if !concepts.is_empty() {
            chapters.push(super::KnowledgeNode {
                id: "content".to_string(),
                label: "笔记内容".to_string(),
                kind: super::NodeKind::Chapter,
                children: concepts,
            });
        }
    }

    json!(chapters)
}

fn slug(text: &str) -> String {
    text.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == ' ' || *c == '-' || *c == '_')
        .collect::<String>()
        .trim()
        .replace(' ', "-")
}
