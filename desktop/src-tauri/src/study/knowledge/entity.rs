use serde::{Deserialize, Serialize};

use super::source::SourceRef;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entity {
    pub id: String,
    pub name: String,
    #[serde(rename = "entityType")]
    pub entity_type: EntityType,
    #[serde(default)]
    pub summary: String,
    #[serde(default = "default_importance")]
    pub importance: u8,
    #[serde(default)]
    pub aliases: Vec<String>,
    #[serde(default)]
    pub source_refs: Vec<SourceRef>,
}

fn default_importance() -> u8 {
    3
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntityType {
    Concept,
    Tool,
    Technology,
    Workflow,
    Asset,
    Library,
    Method,
    Person,
    Organization,
    Problem,
    Solution,
}

impl EntityType {
    pub fn parse(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "tool" => Self::Tool,
            "technology" => Self::Technology,
            "workflow" => Self::Workflow,
            "asset" => Self::Asset,
            "library" => Self::Library,
            "method" => Self::Method,
            "person" => Self::Person,
            "organization" => Self::Organization,
            "problem" => Self::Problem,
            "solution" => Self::Solution,
            _ => Self::Concept,
        }
    }
}
