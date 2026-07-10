use super::entity::{Entity, EntityType};
use super::graph::{Chapter, GraphSource, KnowledgeGraph};
use super::relation::{Relation, RelationType};
use super::source::SourceRef;
use super::validator;

/// Parse the AI response JSON and return a validated KnowledgeGraph.
pub(crate) fn parse_ai_response(value: &serde_json::Value) -> Result<KnowledgeGraph, String> {
    let entities = parse_entities(value)?;
    let relations = parse_relations(value, &entities)?;
    let chapters = parse_chapters(value, &entities)?;

    let kg = KnowledgeGraph {
        entities,
        relations,
        chapters,
        source: GraphSource::Ai,
    };

    // Run validation, log warnings but don't fail on non-critical issues
    let warnings = validator::validate_graph(&kg);
    if kg.entities.is_empty() {
        return Err("AI returned empty entity list".to_string());
    }
    // Emit warnings via log (they'll show in Tauri console)
    for w in &warnings {
        eprintln!("[knowledge-graph] validation warning: {w}");
    }

    Ok(kg)
}

fn parse_entities(value: &serde_json::Value) -> Result<Vec<Entity>, String> {
    let arr = value
        .get("entities")
        .and_then(|v| v.as_array())
        .ok_or_else(|| "AI response missing 'entities' array".to_string())?;

    let entities: Vec<Entity> = arr
        .iter()
        .filter_map(|item| {
            let id = item.get("id")?.as_str()?;
            let name = item.get("name")?.as_str()?;
            if name.is_empty() {
                return None;
            }
            let entity_type = EntityType::from_str(
                item.get("entityType")
                    .or_else(|| item.get("type"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("concept"),
            );
            let importance = item
                .get("importance")
                .and_then(|v| v.as_u64())
                .unwrap_or(3)
                .min(5)
                .max(1) as u8;
            let summary = item
                .get("summary")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let aliases: Vec<String> = item
                .get("aliases")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|a| a.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let source_refs: Vec<SourceRef> = item
                .get("sourceRefs")
                .or_else(|| item.get("source_refs"))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|s| {
                            Some(SourceRef {
                                note_id: s
                                    .get("noteId")
                                    .or_else(|| s.get("note_id"))
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                chapter: s
                                    .get("chapter")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                                timestamp: s
                                    .get("timestamp")
                                    .and_then(|v| v.as_f64().map(|f| f as f32)),
                                quote: s
                                    .get("quote")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string(),
                            })
                        })
                        .collect()
                })
                .unwrap_or_default();

            Some(Entity {
                id: id.to_string(),
                name: name.to_string(),
                entity_type,
                importance,
                summary,
                aliases,
                source_refs,
            })
        })
        .collect();

    Ok(entities)
}

fn parse_relations(
    value: &serde_json::Value,
    entities: &[Entity],
) -> Result<Vec<Relation>, String> {
    let empty = vec![];
    let arr = value
        .get("relations")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty);

    let entity_ids: std::collections::HashSet<&str> =
        entities.iter().map(|e| e.id.as_str()).collect();

    let relations: Vec<Relation> = arr
        .iter()
        .filter_map(|item| {
            let source = item
                .get("source")
                .or_else(|| item.get("sourceId").or_else(|| item.get("source_id")))?
                .as_str()?;
            let target = item
                .get("target")
                .or_else(|| item.get("targetId").or_else(|| item.get("target_id")))?
                .as_str()?;

            // Skip relations referencing unknown entities
            if !entity_ids.contains(source) || !entity_ids.contains(target) {
                return None;
            }

            let relation_type = RelationType::from_str(
                item.get("relationType")
                    .or_else(|| item.get("relation_type"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("related_to"),
            );
            let confidence = item
                .get("confidence")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.5)
                .clamp(0.0, 1.0) as f32;
            let evidence = item
                .get("evidence")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            Some(Relation {
                source: source.to_string(),
                target: target.to_string(),
                relation_type,
                confidence,
                evidence,
            })
        })
        .collect();

    Ok(relations)
}

fn parse_chapters(
    value: &serde_json::Value,
    entities: &[Entity],
) -> Result<Vec<Chapter>, String> {
    let empty = vec![];
    let arr = value
        .get("chapters")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty);

    let entity_ids_set: std::collections::HashSet<&str> =
        entities.iter().map(|e| e.id.as_str()).collect();

    let chapters: Vec<Chapter> = arr
        .iter()
        .filter_map(|item| {
            let title = item.get("title")?.as_str()?;
            let raw_ids: Vec<String> = item
                .get("entityIds")
                .or_else(|| item.get("entity_ids").or_else(|| item.get("entities")))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| {
                            let id = v.as_str()?;
                            // Only include entities that exist
                            if entity_ids_set.contains(id) {
                                Some(id.to_string())
                            } else {
                                None
                            }
                        })
                        .collect()
                })
                .unwrap_or_default();

            Some(Chapter {
                title: title.to_string(),
                entity_ids: raw_ids,
            })
        })
        .collect();

    Ok(chapters)
}