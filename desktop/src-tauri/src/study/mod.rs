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

// ── New graph types ─────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeGraph {
    pub nodes: Vec<GraphNode>,
    #[serde(default)]
    pub relations: Vec<KnowledgeRelation>,
    pub source: GraphSource,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphNode {
    pub id: String,
    pub name: String,
    #[serde(rename = "nodeType")]
    pub node_type: GraphNodeType,
    #[serde(default)]
    pub importance: u8,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphNodeType {
    Concept,
    Tool,
    Method,
    Technology,
    Person,
    Formula,
    Problem,
    Solution,
    Chapter,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KnowledgeRelation {
    #[serde(rename = "sourceId")]
    pub source_id: String,
    #[serde(rename = "targetId")]
    pub target_id: String,
    #[serde(rename = "relationType")]
    pub relation_type: RelationType,
    #[serde(default = "default_confidence")]
    pub confidence: u8,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationType {
    DependsOn,
    UsedFor,
    PartOf,
    Improves,
    Replaces,
    ConflictsWith,
    SimilarTo,
}

fn default_confidence() -> u8 {
    3
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphSource {
    Ai,
    Markdown,
}

/// Convert a heading-based tree (old KnowledgeNode) into a flat graph.
impl From<Vec<KnowledgeNode>> for KnowledgeGraph {
    fn from(nodes: Vec<KnowledgeNode>) -> Self {
        fn flatten(
            tree: Vec<KnowledgeNode>,
            parent_kind: &NodeKind,
        ) -> (Vec<GraphNode>, Vec<KnowledgeRelation>) {
            let mut g_nodes = Vec::new();
            let mut g_relations = Vec::new();
            for n in tree {
                let node_type = match (&n.kind, parent_kind) {
                    (NodeKind::Chapter, _) => GraphNodeType::Chapter,
                    _ => GraphNodeType::Concept,
                };
                let child_id = n.id.clone();
                g_nodes.push(GraphNode {
                    id: child_id.clone(),
                    name: n.label.clone(),
                    node_type,
                    importance: 3,
                    summary: String::new(),
                    source: String::new(),
                });
                if !n.children.is_empty() {
                    let (sub_nodes, sub_rels) = flatten(n.children, &n.kind);
                    for sub in &sub_nodes {
                        g_relations.push(KnowledgeRelation {
                            source_id: child_id.clone(),
                            target_id: sub.id.clone(),
                            relation_type: RelationType::PartOf,
                            confidence: 3,
                        });
                    }
                    g_nodes.extend(sub_nodes);
                    g_relations.extend(sub_rels);
                }
            }
            (g_nodes, g_relations)
        }
        let (nodes, relations) = flatten(nodes, &NodeKind::Chapter);
        Self {
            nodes,
            relations,
            source: GraphSource::Markdown,
        }
    }
}
