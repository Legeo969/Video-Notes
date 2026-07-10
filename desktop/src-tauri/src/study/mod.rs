pub mod knowledge;
pub mod quiz;

use serde::{Deserialize, Serialize};

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
