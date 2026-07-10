use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceRef {
    #[serde(default)]
    pub note_id: String,
    #[serde(default)]
    pub chapter: String,
    #[serde(default)]
    pub timestamp: Option<f32>,
    #[serde(default)]
    pub quote: String,
}