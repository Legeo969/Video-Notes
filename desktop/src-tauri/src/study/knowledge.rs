use crate::native_engine::{NativeProviderProfile, with_optional_bearer};
use serde_json::{json, Value};
use std::time::Duration;

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

const KG_SYSTEM_PROMPT: &str = r#"You are a knowledge graph extraction engine.

Extract entities and their relationships from the provided technical notes.

Output format (strict JSON, no extra text):

{
  "nodes": [
    {
      "id": "unique-id",
      "name": "Entity Name",
      "type": "concept",
      "importance": 3,
      "summary": "One-sentence explanation",
      "source": "section reference"
    }
  ],
  "relations": [
    {
      "sourceId": "id-of-source-node",
      "targetId": "id-of-target-node",
      "relationType": "depends_on",
      "confidence": 4
    }
  ]
}

Rules:
- node.type must be one of: concept, tool, method, technology, person, formula, problem, solution, chapter
- relationType must be one of: depends_on, used_for, part_of, improves, replaces, conflicts_with, similar_to
- importance: 1-5 (5 = most important)
- confidence: 1-5 for relations
- Only extract entities explicitly mentioned in the text
- Do NOT fabricate relationships
- Chinese notes → Chinese output"#;

/// Build a knowledge graph from note content using the AI provider.
pub(crate) fn build_knowledge_graph_ai(
    profile: &NativeProviderProfile,
    content: &str,
) -> Result<super::KnowledgeGraph, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| format!("HTTP client init failed: {e}"))?;

    let url = format!(
        "{}/chat/completions",
        profile.base_url.trim_end_matches('/')
    );

    let response = with_optional_bearer(client.post(&url), &profile.api_key)
        .json(&serde_json::json!({
            "model": profile.model,
            "messages": [
                { "role": "system", "content": KG_SYSTEM_PROMPT },
                { "role": "user", "content": format!("笔记内容：\n\n{}", content) }
            ],
            "temperature": 0.3,
            "max_tokens": 4096
        }))
        .send()
        .map_err(|e| format!("HTTP request failed: {e}"))?;

    let status = response.status();
    let payload: serde_json::Value =
        response.json().map_err(|e| format!("Invalid JSON response: {e}"))?;

    if !status.is_success() {
        return Err(format!("knowledge graph API returned {status}: {payload}"));
    }

    let text = payload
        .get("choices")
        .and_then(|c| c.as_array())
        .and_then(|c| c.first())
        .and_then(|c| c.get("message"))
        .and_then(|m| m.get("content").or_else(|| m.get("reasoning")))
        .and_then(|m| m.as_str())
        .ok_or_else(|| "API returned no content".to_string())?;

    // Parse the AI response as JSON
    let parsed: serde_json::Value = serde_json::from_str(text)
        .map_err(|e| format!("Failed to parse AI response as JSON: {e}"))?;

    parse_kg_response(&parsed)
}

fn parse_kg_response(value: &serde_json::Value) -> Result<super::KnowledgeGraph, String> {
    // Try strict schema first: { "nodes": [...], "relations": [...] }
    if let Some(nodes) = value.get("nodes").and_then(|v| v.as_array()) {
        if let Some(relations) = value.get("relations").and_then(|v| v.as_array()) {
            let parsed_nodes: Vec<super::GraphNode> = nodes
                .iter()
                .filter_map(|n| {
                    let id = n.get("id")?.as_str()?;
                    let name = n.get("name")?.as_str()?;
                    if name.is_empty() {
                        return None;
                    }
                    let node_type = parse_node_type(
                        n.get("type").and_then(|v| v.as_str()).unwrap_or("concept"),
                    );
                    let importance = n
                        .get("importance")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(3)
                        .min(5)
                        .max(1) as u8;
                    let summary = n
                        .get("summary")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let source = n
                        .get("source")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    Some(super::GraphNode {
                        id: id.to_string(),
                        name: name.to_string(),
                        node_type,
                        importance,
                        summary,
                        source,
                    })
                })
                .collect();

            let parsed_relations: Vec<super::KnowledgeRelation> = relations
                .iter()
                .filter_map(|r| {
                    let source_id = r
                        .get("sourceId")
                        .or_else(|| r.get("source_id"))?
                        .as_str()?;
                    let target_id = r
                        .get("targetId")
                        .or_else(|| r.get("target_id"))?
                        .as_str()?;
                    let relation_type = parse_relation_type(
                        r.get("relationType")
                            .or_else(|| r.get("relation_type"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("depends_on"),
                    );
                    let confidence = r
                        .get("confidence")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(3)
                        .min(5)
                        .max(1) as u8;
                    Some(super::KnowledgeRelation {
                        source_id: source_id.to_string(),
                        target_id: target_id.to_string(),
                        relation_type,
                        confidence,
                    })
                })
                .collect();

            if parsed_nodes.is_empty() {
                return Err("AI returned empty node list".to_string());
            }

            return Ok(super::KnowledgeGraph {
                nodes: parsed_nodes,
                relations: parsed_relations,
                source: super::GraphSource::Ai,
            });
        }
    }

    Err("AI response does not match expected schema".to_string())
}

fn parse_node_type(s: &str) -> super::GraphNodeType {
    match s.to_lowercase().as_str() {
        "tool" => super::GraphNodeType::Tool,
        "method" => super::GraphNodeType::Method,
        "technology" => super::GraphNodeType::Technology,
        "person" => super::GraphNodeType::Person,
        "formula" => super::GraphNodeType::Formula,
        "problem" => super::GraphNodeType::Problem,
        "solution" => super::GraphNodeType::Solution,
        "chapter" => super::GraphNodeType::Chapter,
        _ => super::GraphNodeType::Concept,
    }
}

fn parse_relation_type(s: &str) -> super::RelationType {
    match s.to_lowercase().as_str() {
        "used_for" => super::RelationType::UsedFor,
        "part_of" => super::RelationType::PartOf,
        "improves" => super::RelationType::Improves,
        "replaces" => super::RelationType::Replaces,
        "conflicts_with" => super::RelationType::ConflictsWith,
        "similar_to" => super::RelationType::SimilarTo,
        _ => super::RelationType::DependsOn,
    }
}

pub(crate) fn slug(text: &str) -> String {
    text.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == ' ' || *c == '-' || *c == '_')
        .collect::<String>()
        .trim()
        .replace(' ', "-")
}
