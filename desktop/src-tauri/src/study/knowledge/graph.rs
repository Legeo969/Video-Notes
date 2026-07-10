use serde::{Deserialize, Serialize};

use super::entity::Entity;
use super::relation::Relation;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Chapter {
    pub title: String,
    #[serde(rename = "entityIds")]
    #[serde(default)]
    pub entity_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphSource {
    Ai,
    Markdown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeGraph {
    pub entities: Vec<Entity>,
    #[serde(default)]
    pub relations: Vec<Relation>,
    #[serde(default)]
    pub chapters: Vec<Chapter>,
    pub source: GraphSource,
}

impl KnowledgeGraph {
    /// Returns true if the graph has at least one entity.
    pub fn is_populated(&self) -> bool {
        !self.entities.is_empty()
    }

    /// Look up an entity by ID.
    #[allow(dead_code)]
    pub fn entity_by_id(&self, id: &str) -> Option<&Entity> {
        self.entities.iter().find(|e| e.id == id)
    }
}