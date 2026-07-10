use super::entity::{Entity, EntityType};
use super::graph::{Chapter, KnowledgeGraph};
use super::relation::{Relation, RelationType};

/// Validate a KnowledgeGraph produced by AI.
/// Returns a list of warning messages; the graph is still usable.
pub(crate) fn validate_graph(kg: &KnowledgeGraph) -> Vec<String> {
    let mut warnings: Vec<String> = Vec::new();

    let entity_ids: std::collections::HashSet<&str> =
        kg.entities.iter().map(|e| e.id.as_str()).collect();

    // Validate each entity
    for entity in &kg.entities {
        validate_entity(entity, &mut warnings);
    }

    // Validate each relation
    for relation in &kg.relations {
        validate_relation(relation, &entity_ids, &mut warnings);
    }

    // Validate each chapter
    for chapter in &kg.chapters {
        validate_chapter(chapter, &entity_ids, &mut warnings);
    }

    warnings
}

fn validate_entity(entity: &Entity, warnings: &mut Vec<String>) {
    if entity.id.is_empty() {
        warnings.push("Entity with empty id found".to_string());
    }
    if entity.name.is_empty() {
        warnings.push(format!("Entity '{}' has empty name", entity.id));
    }
    if entity.importance < 1 || entity.importance > 5 {
        warnings.push(format!(
            "Entity '{}' has importance {} (must be 1-5)",
            entity.id, entity.importance
        ));
    }
    // Validate entity type is a known variant
    match entity.entity_type {
        EntityType::Concept
        | EntityType::Tool
        | EntityType::Technology
        | EntityType::Workflow
        | EntityType::Asset
        | EntityType::Library
        | EntityType::Method
        | EntityType::Person
        | EntityType::Organization
        | EntityType::Problem
        | EntityType::Solution => {}
    }
}

fn validate_relation(
    relation: &Relation,
    entity_ids: &std::collections::HashSet<&str>,
    warnings: &mut Vec<String>,
) {
    if !entity_ids.contains(relation.source.as_str()) {
        warnings.push(format!(
            "Relation references unknown source entity '{}'",
            relation.source
        ));
    }
    if !entity_ids.contains(relation.target.as_str()) {
        warnings.push(format!(
            "Relation references unknown target entity '{}'",
            relation.target
        ));
    }
    if relation.confidence < 0.0 || relation.confidence > 1.0 {
        warnings.push(format!(
            "Relation '{}' -> '{}' has confidence {} (must be 0.0-1.0)",
            relation.source, relation.target, relation.confidence
        ));
    }
    // Validate relation type is a known variant
    match relation.relation_type {
        RelationType::Uses
        | RelationType::DependsOn
        | RelationType::PartOf
        | RelationType::Implements
        | RelationType::Improves
        | RelationType::Generates
        | RelationType::Imports
        | RelationType::Exports
        | RelationType::RelatedTo
        | RelationType::SimilarTo
        | RelationType::ConflictsWith
        | RelationType::Requires
        | RelationType::Produces
        | RelationType::Consumes => {}
    }
}

fn validate_chapter(
    chapter: &Chapter,
    entity_ids: &std::collections::HashSet<&str>,
    warnings: &mut Vec<String>,
) {
    if chapter.title.is_empty() {
        warnings.push("Chapter with empty title found".to_string());
    }
    for eid in &chapter.entity_ids {
        if !entity_ids.contains(eid.as_str()) {
            warnings.push(format!(
                "Chapter '{}' references unknown entity '{}'",
                chapter.title, eid
            ));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::study::knowledge::entity::Entity;
    use crate::study::knowledge::graph::{Chapter, GraphSource, KnowledgeGraph};
    use crate::study::knowledge::relation::Relation;

    fn make_valid_entity(id: &str) -> Entity {
        Entity {
            id: id.to_string(),
            name: id.to_string(),
            entity_type: EntityType::Concept,
            summary: "A test entity".to_string(),
            importance: 3,
            aliases: vec![],
            source_refs: vec![],
        }
    }

    #[test]
    fn test_valid_graph_no_warnings() {
        let kg = KnowledgeGraph {
            entities: vec![
                make_valid_entity("a"),
                make_valid_entity("b"),
            ],
            relations: vec![Relation {
                source: "a".to_string(),
                target: "b".to_string(),
                relation_type: RelationType::Uses,
                confidence: 0.9,
                evidence: "A uses B".to_string(),
            }],
            chapters: vec![Chapter {
                title: "Intro".to_string(),
                entity_ids: vec!["a".to_string(), "b".to_string()],
            }],
            source: GraphSource::Ai,
        };
        let warnings = validate_graph(&kg);
        assert!(warnings.is_empty(), "Expected no warnings: {:?}", warnings);
    }

    #[test]
    fn test_missing_entity_id_warning() {
        let kg = KnowledgeGraph {
            entities: vec![Entity {
                id: "".to_string(),
                name: "empty".to_string(),
                entity_type: EntityType::Concept,
                summary: "".to_string(),
                importance: 3,
                aliases: vec![],
                source_refs: vec![],
            }],
            relations: vec![],
            chapters: vec![],
            source: GraphSource::Ai,
        };
        let warnings = validate_graph(&kg);
        assert!(warnings.iter().any(|w| w.contains("empty id")));
    }

    #[test]
    fn test_relation_to_unknown_entity() {
        let kg = KnowledgeGraph {
            entities: vec![make_valid_entity("a")],
            relations: vec![Relation {
                source: "a".to_string(),
                target: "ghost".to_string(),
                relation_type: RelationType::Uses,
                confidence: 0.5,
                evidence: "".to_string(),
            }],
            chapters: vec![],
            source: GraphSource::Ai,
        };
        let warnings = validate_graph(&kg);
        assert!(warnings.iter().any(|w| w.contains("unknown target")));
    }

    #[test]
    fn test_out_of_range_importance() {
        let kg = KnowledgeGraph {
            entities: vec![Entity {
                id: "x".to_string(),
                name: "X".to_string(),
                entity_type: EntityType::Concept,
                summary: "".to_string(),
                importance: 99,
                aliases: vec![],
                source_refs: vec![],
            }],
            relations: vec![],
            chapters: vec![],
            source: GraphSource::Ai,
        };
        let warnings = validate_graph(&kg);
        assert!(warnings.iter().any(|w| w.contains("importance 99")));
    }
}