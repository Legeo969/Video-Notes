pub mod knowledge;
pub mod quiz;

use serde::{Deserialize, Serialize};

/// Legacy tree node type, kept for TreeAdapter compatibility.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeNode {
    pub id: String,
    pub label: String,
    #[serde(rename = "kind")]
    pub kind: NodeKind,
    #[serde(default)]
    pub children: Vec<KnowledgeNode>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    Chapter,
    Section,
    Concept,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[allow(dead_code)]
pub struct QuizQuestion {
    pub question: String,
    pub choices: Vec<String>,
    #[serde(rename = "correctIndex")]
    pub correct_index: usize,
    pub explanation: String,
}

// Note: The KnowledgeGraph, Entity, Relation types are now in knowledge/
// module with the V2 schema. Import via:
//   use crate::study::knowledge::graph::{KnowledgeGraph, Chapter, GraphSource};
//   use crate::study::knowledge::entity::{Entity, EntityType};
//   use crate::study::knowledge::relation::{Relation, RelationType};
//   use crate::study::knowledge::source::SourceRef;