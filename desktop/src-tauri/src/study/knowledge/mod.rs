pub mod adapters;
pub mod entity;
pub mod graph;
pub mod parser;
pub mod prompt;
pub mod relation;
pub mod source;
pub mod validator;

use std::time::Duration;

use crate::native_engine::{with_optional_bearer, NativeProviderProfile};

use self::entity::{Entity, EntityType};
use self::graph::{Chapter, GraphSource, KnowledgeGraph};
use self::relation::{Relation, RelationType};

/// Build a knowledge graph from Markdown note content using heading-based parsing.
/// Returns a KnowledgeGraph with chapters derived from headings.
pub fn build_knowledge_graph(content: &str) -> KnowledgeGraph {
    let mut entities: Vec<Entity> = Vec::new();
    let mut relations: Vec<Relation> = Vec::new();
    let mut chapters: Vec<Chapter> = Vec::new();
    let mut current_chapter: Option<(String, Vec<String>)> = None;
    let mut current_section: Option<(String, Vec<String>)> = None;
    let mut in_code_block = false;

    for line in content.lines() {
        let trimmed = line.trim();

        if trimmed.starts_with("```") {
            in_code_block = !in_code_block;
            continue;
        }
        if in_code_block {
            continue;
        }

        if trimmed.starts_with("## ") || trimmed.starts_with("# ") {
            // Flush current section
            if let Some((sec_label, concepts)) = current_section.take() {
                let section_entity_id = slug(&sec_label);
                entities.push(Entity {
                    id: section_entity_id.clone(),
                    name: sec_label.clone(),
                    entity_type: EntityType::Concept,
                    summary: String::new(),
                    importance: 3,
                    aliases: vec![],
                    source_refs: vec![],
                });
                for concept in &concepts {
                    let concept_id = slug(concept);
                    entities.push(Entity {
                        id: concept_id.clone(),
                        name: concept.clone(),
                        entity_type: EntityType::Concept,
                        summary: String::new(),
                        importance: 3,
                        aliases: vec![],
                        source_refs: vec![],
                    });
                    relations.push(Relation {
                        source: section_entity_id.clone(),
                        target: concept_id,
                        relation_type: RelationType::PartOf,
                        confidence: 1.0,
                        evidence: String::new(),
                    });
                }
                if let Some((_, ref mut eids)) = current_chapter {
                    eids.push(section_entity_id);
                }
            }
            // Flush current chapter
            if let Some((label, eids)) = current_chapter.take() {
                chapters.push(Chapter {
                    title: label,
                    entity_ids: eids,
                });
            }
            let prefix_len = if trimmed.starts_with("# ") { 2 } else { 3 };
            current_chapter = Some((trimmed[prefix_len..].trim().to_string(), Vec::new()));
        } else if trimmed.starts_with("### ") {
            if let Some((sec_label, concepts)) = current_section.take() {
                let section_entity_id = slug(&sec_label);
                entities.push(Entity {
                    id: section_entity_id.clone(),
                    name: sec_label.clone(),
                    entity_type: EntityType::Concept,
                    summary: String::new(),
                    importance: 3,
                    aliases: vec![],
                    source_refs: vec![],
                });
                for concept in &concepts {
                    let concept_id = slug(concept);
                    entities.push(Entity {
                        id: concept_id.clone(),
                        name: concept.clone(),
                        entity_type: EntityType::Concept,
                        summary: String::new(),
                        importance: 3,
                        aliases: vec![],
                        source_refs: vec![],
                    });
                    relations.push(Relation {
                        source: section_entity_id.clone(),
                        target: concept_id,
                        relation_type: RelationType::PartOf,
                        confidence: 1.0,
                        evidence: String::new(),
                    });
                }
                if let Some((_, ref mut eids)) = current_chapter {
                    eids.push(section_entity_id);
                }
            }
            let label = trimmed[4..].trim().to_string();
            current_section = Some((label, Vec::new()));
        } else if let Some(stripped) = trimmed
            .strip_prefix("- ")
            .or_else(|| trimmed.strip_prefix("* "))
        {
            let concept = stripped.trim().to_string();
            if !concept.is_empty() {
                if let Some((_, ref mut concepts)) = current_section {
                    concepts.push(concept);
                } else if let Some((_, ref mut eids)) = current_chapter {
                    let concept_id = slug(&concept);
                    entities.push(Entity {
                        id: concept_id.clone(),
                        name: concept.clone(),
                        entity_type: EntityType::Concept,
                        summary: String::new(),
                        importance: 3,
                        aliases: vec![],
                        source_refs: vec![],
                    });
                    eids.push(concept_id);
                }
            }
        }
    }

    // Flush remaining section
    if let Some((sec_label, concepts)) = current_section.take() {
        let section_entity_id = slug(&sec_label);
        entities.push(Entity {
            id: section_entity_id.clone(),
            name: sec_label.clone(),
            entity_type: EntityType::Concept,
            summary: String::new(),
            importance: 3,
            aliases: vec![],
            source_refs: vec![],
        });
        for concept in &concepts {
            let concept_id = slug(concept);
            entities.push(Entity {
                id: concept_id.clone(),
                name: concept.clone(),
                entity_type: EntityType::Concept,
                summary: String::new(),
                importance: 3,
                aliases: vec![],
                source_refs: vec![],
            });
            relations.push(Relation {
                source: section_entity_id.clone(),
                target: concept_id,
                relation_type: RelationType::PartOf,
                confidence: 1.0,
                evidence: String::new(),
            });
        }
        if let Some((_, ref mut eids)) = current_chapter {
            eids.push(section_entity_id);
        }
    }

    // Flush remaining chapter
    if let Some((label, eids)) = current_chapter.take() {
        chapters.push(Chapter {
            title: label,
            entity_ids: eids,
        });
    }

    // If no structure found, create from non-empty lines
    if entities.is_empty() {
        let concepts: Vec<String> = content
            .lines()
            .filter(|l| {
                let t = l.trim();
                !t.is_empty() && !t.starts_with('#') && !t.starts_with("![")
            })
            .take(20)
            .map(|l| l.trim().to_string())
            .collect();
        if !concepts.is_empty() {
            for concept in &concepts {
                entities.push(Entity {
                    id: slug(concept),
                    name: concept.clone(),
                    entity_type: EntityType::Concept,
                    summary: String::new(),
                    importance: 3,
                    aliases: vec![],
                    source_refs: vec![],
                });
            }
            chapters.push(Chapter {
                title: "笔记内容".to_string(),
                entity_ids: concepts.iter().map(|c| slug(c)).collect(),
            });
        }
    }

    KnowledgeGraph {
        entities,
        relations,
        chapters,
        source: GraphSource::Markdown,
    }
}

/// Build a knowledge graph from note content using the AI provider.
pub(crate) fn build_knowledge_graph_ai(
    profile: &NativeProviderProfile,
    content: &str,
) -> Result<KnowledgeGraph, String> {
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
                { "role": "system", "content": prompt::KG_SYSTEM_PROMPT_V2 },
                { "role": "user", "content": format!("笔记内容：\n\n{}", content) }
            ],
            "temperature": 0.3,
            "max_tokens": 4096
        }))
        .send()
        .map_err(|e| format!("HTTP request failed: {e}"))?;

    let status = response.status();
    let payload: serde_json::Value = response
        .json()
        .map_err(|e| format!("Invalid JSON response: {e}"))?;

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

    // Parse the AI response as JSON (V2 schema)
    let parsed: serde_json::Value = serde_json::from_str(text)
        .map_err(|e| format!("Failed to parse AI response as JSON: {e}"))?;

    parser::parse_ai_response(&parsed)
}

pub(crate) fn slug(text: &str) -> String {
    text.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == ' ' || *c == '-' || *c == '_')
        .collect::<String>()
        .trim()
        .replace(' ', "-")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_knowledge_graph_basic() {
        let content = "# Chapter 1\n- concept A\n- concept B\n## Section 1\n- item 1\n- item 2\n";
        let kg = build_knowledge_graph(content);
        assert!(kg.is_populated(), "Should have entities");
        assert!(!kg.chapters.is_empty(), "Should have chapters");
        assert_eq!(kg.chapters[0].title, "Chapter 1");
        // Should have entities: section-1, item-1, item-2, concept-a, concept-b
        assert!(kg.entities.len() >= 4, "Should have at least 4 entities");
    }

    #[test]
    fn test_build_knowledge_graph_empty() {
        let kg = build_knowledge_graph("");
        assert!(!kg.is_populated());
    }

    #[test]
    fn test_entity_by_id() {
        let content = "# Test\n- foo\n- bar\n";
        let kg = build_knowledge_graph(content);
        let foo = kg.entity_by_id("foo");
        assert!(foo.is_some(), "Entity 'foo' should exist");
        assert_eq!(foo.unwrap().name, "foo");
    }

    #[test]
    fn test_slug() {
        assert_eq!(slug("Hello World"), "hello-world");
        assert_eq!(slug("Test-123"), "test-123");
        assert_eq!(slug("Special!@#Chars"), "specialchars");
    }
}