use super::super::graph::{Chapter, KnowledgeGraph};
use crate::study::KnowledgeNode;
use crate::study::NodeKind;

/// Convert a KnowledgeGraph to the legacy tree format (Vec<KnowledgeNode>)
/// for the existing KnowledgeTree UI.
#[allow(dead_code)]
pub(crate) fn graph_to_tree(kg: &KnowledgeGraph) -> Vec<KnowledgeNode> {
    if kg.chapters.is_empty() {
        return flatten_entities_as_flat_tree(kg);
    }

    let mut tree: Vec<KnowledgeNode> = Vec::new();

    for chapter in &kg.chapters {
        let children = chapter_entity_nodes(chapter, kg);
        tree.push(KnowledgeNode {
            id: slug(&chapter.title),
            label: chapter.title.clone(),
            kind: NodeKind::Chapter,
            children,
        });
    }

    tree
}

/// Build tree nodes for entities referenced in a chapter.
fn chapter_entity_nodes(chapter: &Chapter, kg: &KnowledgeGraph) -> Vec<KnowledgeNode> {
    let mut children: Vec<KnowledgeNode> = Vec::new();

    for eid in &chapter.entity_ids {
        if let Some(entity) = kg.entity_by_id(eid) {
            let mut node = KnowledgeNode {
                id: entity.id.clone(),
                label: entity.name.clone(),
                kind: NodeKind::Concept,
                children: Vec::new(),
            };

            // Add relations as child concept hints
            let related: Vec<String> = kg
                .relations
                .iter()
                .filter(|r| r.source == entity.id || r.target == entity.id)
                .map(|r| {
                    let other_id = if r.source == entity.id {
                        &r.target
                    } else {
                        &r.source
                    };
                    if let Some(other) = kg.entity_by_id(other_id) {
                        other.name.clone()
                    } else {
                        other_id.clone()
                    }
                })
                .collect();

            if !related.is_empty() {
                let summary = if entity.summary.is_empty() {
                    format!("关联: {}", related.join(" · "))
                } else {
                    format!("{} — 关联: {}", entity.summary, related.join(" · "))
                };
                node.children.push(KnowledgeNode {
                    id: format!("{}-relations", entity.id),
                    label: summary,
                    kind: NodeKind::Section,
                    children: related
                        .into_iter()
                        .map(|name| KnowledgeNode {
                            id: slug(&name),
                            label: name,
                            kind: NodeKind::Concept,
                            children: Vec::new(),
                        })
                        .collect(),
                });
            }

            children.push(node);
        }
    }

    children
}

/// Fallback: if no chapters, create a flat tree listing all entities.
fn flatten_entities_as_flat_tree(kg: &KnowledgeGraph) -> Vec<KnowledgeNode> {
    if kg.entities.is_empty() {
        return vec![];
    }

    vec![KnowledgeNode {
        id: "entities".to_string(),
        label: "知识实体".to_string(),
        kind: NodeKind::Chapter,
        children: kg
            .entities
            .iter()
            .map(|e| KnowledgeNode {
                id: e.id.clone(),
                label: e.name.clone(),
                kind: NodeKind::Concept,
                children: Vec::new(),
            })
            .collect(),
    }]
}

fn slug(text: &str) -> String {
    text.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == ' ' || *c == '-' || *c == '_')
        .collect::<String>()
        .trim()
        .replace(' ', "-")
}