use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relation {
    pub source: String,
    pub target: String,
    #[serde(rename = "relationType")]
    pub relation_type: RelationType,
    #[serde(default = "default_confidence")]
    pub confidence: f32,
    #[serde(default)]
    pub evidence: String,
}

fn default_confidence() -> f32 {
    0.5
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationType {
    Uses,
    DependsOn,
    PartOf,
    Implements,
    Improves,
    Generates,
    Imports,
    Exports,
    RelatedTo,
    SimilarTo,
    ConflictsWith,
    Requires,
    Produces,
    Consumes,
}

impl RelationType {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "uses" => Self::Uses,
            "depends_on" | "depends on" => Self::DependsOn,
            "part_of" | "part of" => Self::PartOf,
            "implements" => Self::Implements,
            "improves" => Self::Improves,
            "generates" => Self::Generates,
            "imports" => Self::Imports,
            "exports" => Self::Exports,
            "related_to" | "related to" => Self::RelatedTo,
            "similar_to" | "similar to" => Self::SimilarTo,
            "conflicts_with" | "conflicts with" => Self::ConflictsWith,
            "requires" => Self::Requires,
            "produces" => Self::Produces,
            "consumes" => Self::Consumes,
            _ => Self::RelatedTo,
        }
    }
}