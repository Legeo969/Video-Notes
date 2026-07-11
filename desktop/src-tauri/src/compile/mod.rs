pub mod sampler;
pub mod repair;

use std::collections::HashMap;

/// A single video frame with perceptual hash and variance metadata.
#[derive(Debug, Clone)]
pub struct Frame {
    /// Sequential frame index (0, 1, 2, ...) used for timestamp binding.
    pub index: u32,
    /// Raw PNG bytes of the frame (for MLLM input).
    pub data: Vec<u8>,
    /// Perceptual hash (dHash) for dedup comparison.
    pub phash: u64,
    /// Pixel variance for entropy budget / static scene detection.
    pub variance: f32,
    /// Physical timestamp in seconds, computed from frame_index × interval.
    pub timestamp_sec: f64,
    /// Frame width in pixels.
    pub width: u32,
    /// Frame height in pixels.
    pub height: u32,
}

/// 16 kHz mono PCM audio buffer (WAV format for MLLM input).
#[derive(Debug, Clone)]
pub struct AudioBuffer {
    /// Raw WAV bytes.
    pub data: Vec<u8>,
    /// Sample rate (always 16000 after resampling).
    pub sample_rate: u32,
    /// Duration in seconds.
    pub duration_sec: f64,
}

/// Output of the Intelligent Sampler (Pass 1).
#[derive(Debug, Clone)]
pub struct SampleOutput {
    /// Kept frames after dedup and static suppression.
    pub frames: Vec<Frame>,
    /// Resampled audio (16 kHz mono PCM WAV).
    pub audio: AudioBuffer,
    /// frame_index → physical_seconds mapping table.
    pub frame_index_map: HashMap<u32, f64>,
    /// Total video duration.
    pub duration_sec: f64,
    /// Sampling statistics.
    pub metrics: SamplingMetrics,
}

/// Configuration for the Intelligent Sampler.
#[derive(Debug, Clone)]
pub struct SamplerOptions {
    /// Global frame rate cap in fps (default: 1.0, max: 2.0 with high_precision).
    pub fps_limit: f64,
    /// Allow 2 fps if user confirmed high-precision mode.
    pub high_precision: bool,
    /// Minimum number of frames to keep even if all are static (default: 1).
    pub min_frames: u32,
    /// pHash Hamming distance threshold for dedup (default: 10).
    pub phash_threshold: u32,
    /// Variance threshold for static scene detection (default: 5.0).
    pub static_variance_threshold: f32,
    /// Audio energy threshold in dB for voice activity detection (default: -30.0).
    pub audio_energy_threshold_db: f32,
}

impl Default for SamplerOptions {
    fn default() -> Self {
        Self {
            fps_limit: 1.0,
            high_precision: false,
            min_frames: 1,
            phash_threshold: 10,
            static_variance_threshold: 5.0,
            audio_energy_threshold_db: -30.0,
        }
    }
}

impl SamplerOptions {
    /// Returns the effective fps limit after applying high_precision cap.
    pub fn effective_fps(&self) -> f64 {
        if self.high_precision {
            self.fps_limit.min(2.0)
        } else {
            self.fps_limit.min(1.0)
        }
    }
}

/// Sampling statistics for the profile log.
#[derive(Debug, Clone, Copy)]
pub struct SamplingMetrics {
    /// Total candidate frames before dedup/suppression.
    pub total_candidates: u32,
    /// Frames kept after all filtering.
    pub frames_kept: u32,
    /// Frames discarded by pHash dedup.
    pub frames_deduped: u32,
    /// Frames discarded by static scene suppression.
    pub frames_static_suppressed: u32,
    /// Audio duration in seconds.
    pub audio_duration_sec: f64,
}

/// Raw compile output from MLLM (Pass 2), before normalization.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RawCompileOutput {
    /// Events identified by the MLLM, each with frame index bounds.
    #[serde(default)]
    pub events: Vec<RawEvent>,
    /// Summary of this chunk.
    #[serde(default)]
    pub chunk_summary: String,
}

/// A single event from MLLM raw output.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RawEvent {
    /// Event title / label.
    pub title: String,
    /// Frame index range [start, end] — NOT physical seconds.
    #[serde(default)]
    pub event_frame_indexes: Vec<u32>,
    /// Detailed description.
    #[serde(default)]
    pub description: String,
    /// Event type.
    #[serde(default)]
    pub event_type: String,
    /// Speaker if identifiable.
    #[serde(default)]
    pub speaker: Option<String>,
    /// Raw model confidence (0.0–1.0).
    #[serde(default)]
    pub confidence: f32,
}