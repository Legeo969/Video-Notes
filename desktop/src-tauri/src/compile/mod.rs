pub mod bridge;
pub mod calibrate;
pub mod client;
pub mod draft;
pub mod engine;
pub mod prompt;
pub mod renderer;
pub mod repair;
pub mod sampler;
pub mod storage;

use std::collections::HashMap;

pub const IR_SCHEMA_VERSION: u32 = 2;

/// A time-anchor point mapping a stable index to a media timestamp.
#[derive(Debug, Clone, Copy)]
pub struct Frame {
    /// Stable anchor identifier exposed to the model.
    pub index: u32,
    /// Media timestamp in seconds.
    pub timestamp_sec: f64,
}

/// Audio-stream metadata discovered without transcoding the source media.
#[derive(Debug, Clone)]
pub struct AudioBuffer {
    pub has_audio: bool,
    pub duration_sec: f64,
    /// Reserved for bounded metering. `None` means audio was not decoded.
    pub rms_dbfs: Option<f32>,
}

/// Output of the media sampler.
#[derive(Debug, Clone)]
pub struct SampleOutput {
    pub frames: Vec<Frame>,
    pub audio: AudioBuffer,
    pub frame_index_map: HashMap<u32, f64>,
    pub duration_sec: f64,
    pub metrics: SamplingMetrics,
}

/// Budget for time-anchor points.
#[derive(Debug, Clone)]
pub struct SamplerOptions {
    /// Desired anchor points per second of media.
    pub anchor_rate: f64,
    /// Absolute cap on anchor points regardless of duration.
    pub max_anchors: u32,
}

impl Default for SamplerOptions {
    fn default() -> Self {
        Self {
            anchor_rate: 1.0,
            max_anchors: 600,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct SamplingMetrics {
    pub duration_sec: f64,
    pub audio_duration_sec: f64,
    pub audio_rms_dbfs: Option<f32>,
    pub anchor_count: u32,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RawCompileOutput {
    #[serde(default)]
    pub events: Vec<RawEvent>,
    #[serde(default)]
    pub chunk_summary: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RawEvent {
    pub title: String,
    #[serde(default)]
    pub event_frame_indexes: Vec<u32>,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub event_type: String,
    #[serde(default)]
    pub speaker: Option<String>,
    #[serde(default)]
    pub confidence: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceType {
    Fact,
    Procedure,
    Concept,
    Failure,
    Verification,
    Draft,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Evidence {
    pub id: String,
    pub source_hash: String,
    pub version: u32,
    pub chunk_sequence: u32,
    pub content: String,
    pub timestamp_start_sec: f32,
    pub timestamp_end_sec: f32,
    pub evidence_type: EvidenceType,
    pub speaker: Option<String>,
    pub confidence: f32,
    pub visual_context: String,
    pub prev_chunk_summary_hash: Option<String>,
    pub is_redundant: bool,
    #[serde(default)]
    pub needs_review: bool,
    #[serde(default)]
    pub review_reasons: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CompileMode {
    CloudPrecision,
    LocalDraft,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct VideoCapsule {
    #[serde(default = "default_ir_schema_version")]
    pub ir_schema_version: u32,
    pub capsule_id: String,
    pub source_hash: String,
    #[serde(default)]
    pub source_title: String,
    pub version: u32,
    pub total_duration: f32,
    pub processed_at: String,
    pub model_used: String,
    pub evidences: Vec<Evidence>,
    pub global_summary: String,
    pub compilation_mode: CompileMode,
    #[serde(default)]
    pub warnings: Vec<String>,
    #[serde(default)]
    pub source_input: String,
}

fn default_ir_schema_version() -> u32 {
    1
}
