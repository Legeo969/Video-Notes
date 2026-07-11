/// Compile Engine — full pipeline orchestrator.
///
/// Ties together:
///   Sampler (P0) → MLLM Client (P1) → Calibration (P1) → Bridge (P1) → Storage (P2a) → Draft (P2b)
///
/// Exposes a single `compile_video()` entry point with progress reporting.

use std::path::Path;

use serde_json::{json, Value};

use crate::compile::bridge::ChunkContext;
use crate::compile::calibrate;
use crate::compile::client::{self, CompileClientConfig};
use crate::compile::draft;
use crate::compile::sampler;
use crate::compile::storage::{CapsuleBuilder, CapsuleStore, FileCapsuleStore};
use crate::compile::{CompileMode, SamplerOptions};

/// Callback for progress updates during compilation.
pub type ProgressFn = Box<dyn Fn(&str, u8, &str) + Send + Sync>;

/// Full options for a compile video invocation.
pub struct CompileOptions {
    pub ffmpeg_path: std::path::PathBuf,
    pub ffprobe_path: std::path::PathBuf,
    pub storage_dir: std::path::PathBuf,
    pub sampler: SamplerOptions,
    pub client_config: Option<CompileClientConfig>,
    pub prefer_draft: bool,
    pub on_progress: Option<ProgressFn>,
}

impl CompileOptions {
    #[allow(dead_code)]
    pub fn new(
        ffmpeg_path: std::path::PathBuf,
        ffprobe_path: std::path::PathBuf,
        storage_dir: std::path::PathBuf,
    ) -> Self {
        Self {
            ffmpeg_path,
            ffprobe_path,
            storage_dir,
            sampler: SamplerOptions::default(),
            client_config: None,
            prefer_draft: false,
            on_progress: None,
        }
    }
}

/// Result of a compile operation.
#[derive(Debug, Clone, serde::Serialize)]
pub struct CompileResult {
    pub capsule_id: String,
    pub source_hash: String,
    pub version: u32,
    pub mode: CompileMode,
    pub evidence_count: usize,
    pub total_duration_sec: f32,
    pub sampling_metrics: Value,
}

/// Run the complete compile pipeline on a video file.
///
/// Stages:
///   1. Determine mode (cloud vs draft)
///   2. Sample video → frames + audio + frame_index_map
///   3. Split into chunks → compile each via MLLM or draft
///   4. Calibrate confidence for each chunk
///   5. Build VideoCapsule
///   6. Store capsule
///   7. Return capsule_id
pub fn compile_video(
    input_path: &Path,
    source_hash: &str,
    title: &str,
    opts: &CompileOptions,
) -> Result<CompileResult, String> {
    let progress = opts.on_progress.as_ref();
    let progress = |stage: &str, pct: u8, msg: &str| {
        if let Some(f) = progress {
            f(stage, pct, msg);
        }
    };

    progress("resolving", 2, "检查输入文件");
    if !input_path.is_file() {
        return Err(format!("input file not found: {}", input_path.display()));
    }

    // --- Determine mode ---
    let mode = if let Some(config) = &opts.client_config {
        draft::resolve_compile_mode(&config.base_url, opts.prefer_draft)
    } else {
        CompileMode::LocalDraft
    };
    let mode_label = match mode {
        CompileMode::CloudPrecision => "Cloud Precision",
        CompileMode::LocalDraft => "Local Draft",
    };
    progress("sampling", 5, &format!("模式: {mode_label}"));

    // --- Stage 1: Sample ---
    progress("sampling", 10, "智能采样: 提取帧和音频");
    let sample = sampler::sample_video(
        input_path,
        &opts.ffmpeg_path,
        &opts.ffprobe_path,
        &opts.sampler,
    )?;

    let total_chunks = if sample.frames.len() <= 10 {
        1
    } else {
        ((sample.frames.len() + 9) / 10) as u32
    };

    progress(
        "sampling",
        15,
        &format!(
            "帧: {} (去重后), 切片: {total_chunks}, 音频: {:.0}s",
            sample.metrics.frames_kept,
            sample.audio.duration_sec
        ),
    );

    // --- Stage 2: Compile ---
    let mut builder = CapsuleBuilder::new(
        source_hash.to_string(),
        opts.client_config
            .as_ref()
            .map(|c| c.model.clone())
            .unwrap_or_else(|| "draft".to_string()),
        sample.duration_sec as f32,
        mode,
    );

    let mut ctx = ChunkContext::first(total_chunks);

    // Split frames into chunks of 10
    let chunk_size = 10;
    let frame_chunks: Vec<&[crate::compile::Frame]> =
        sample.frames.chunks(chunk_size).collect();

    for (chunk_idx, chunk_frames) in frame_chunks.iter().enumerate() {
        let seq = chunk_idx as u32;
        let base_pct = 20 + ((chunk_idx as f32 / frame_chunks.len() as f32) * 60.0) as u8;

        progress(
            "compiling",
            base_pct,
            &format!("编译切片 {}/{}", seq + 1, total_chunks),
        );

        let frame_indices: Vec<u32> = chunk_frames.iter().map(|f| f.index).collect();
        let frame_pngs: Vec<Vec<u8>> = chunk_frames.iter().map(|f| f.data.clone()).collect();
        let transcript_text = ""; // Audio-to-text handled by MLLM directly

        let raw_output = match mode {
            CompileMode::CloudPrecision => {
                if let Some(config) = &opts.client_config {
                    client::compile_chunk(
                        config,
                        seq,
                        total_chunks,
                        &frame_indices,
                        &frame_pngs,
                        transcript_text,
                        Some(&ctx.prev_chunk_summary),
                    )?
                } else {
                    draft::generate_local_draft(title, sample.duration_sec, chunk_frames.len(), &frame_pngs, transcript_text)
                }
            }
            CompileMode::LocalDraft => {
                draft::generate_local_draft(title, sample.duration_sec, chunk_frames.len(), &frame_pngs, transcript_text)
            }
        };

        // --- Stage 3: Normalize ---
        progress("normalizing", base_pct + 5, "归一化: 置信度校准");

        // Compute calibrated confidence for each event
        for event in &raw_output.events {
            let cal_conf = calibrate::calibrate_confidence(
                event.confidence,
                &raw_output.chunk_summary,
                &ctx.prev_chunk_summary,
                "", // next summary not available yet
            );
            builder.add_chunk(
                seq,
                vec![event.clone()],
                &raw_output.chunk_summary,
                &sample.frame_index_map,
                cal_conf,
            );
        }

        // Advance context bridge
        ctx = ctx.advance(&raw_output.chunk_summary);
    }

    // --- Stage 4: Store ---
    progress("storing", 85, "存储不可变胶囊");

    let mut store = FileCapsuleStore::new(opts.storage_dir.clone());

    // Determine next version number
    let version = store
        .list_versions(source_hash)
        .map(|versions| versions.iter().map(|v| v.version).max().unwrap_or(0) + 1)
        .unwrap_or(1);

    let capsule = builder.build(version);

    let _model_name = opts
        .client_config
        .as_ref()
        .map(|c| c.model.clone())
        .unwrap_or_else(|| "draft".to_string());

    let capsule_id = store.insert(capsule).map_err(|e| format!("storage error: {e}"))?;

    progress("complete", 100, "编译完成");

    Ok(CompileResult {
        capsule_id,
        source_hash: source_hash.to_string(),
        version,
        mode,
        evidence_count: frame_chunks.len(),
        total_duration_sec: sample.duration_sec as f32,
        sampling_metrics: json!({
            "total_candidates": sample.metrics.total_candidates,
            "frames_kept": sample.metrics.frames_kept,
            "frames_deduped": sample.metrics.frames_deduped,
            "frames_static_suppressed": sample.metrics.frames_static_suppressed,
        }),
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compile_options_default() {
        let opts = CompileOptions::new(
            Path::new("ffmpeg").to_path_buf(),
            Path::new("ffprobe").to_path_buf(),
            Path::new("/tmp/store").to_path_buf(),
        );
        assert!(!opts.prefer_draft);
        assert!(opts.client_config.is_none());
    }

    #[test]
    fn test_compile_result_serialization() {
        let result = CompileResult {
            capsule_id: "abc_1".to_string(),
            source_hash: "abc".to_string(),
            version: 1,
            mode: CompileMode::CloudPrecision,
            evidence_count: 5,
            total_duration_sec: 120.0,
            sampling_metrics: json!({"frames_kept": 12}),
        };
        let json = serde_json::to_value(&result).unwrap();
        assert_eq!(json["capsule_id"], "abc_1");
        assert_eq!(json["version"], 1);
    }
}