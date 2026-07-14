//! End-to-end compile orchestration.

use std::collections::HashMap;
use std::path::Path;

use serde_json::{json, Value};

use crate::compile::bridge::{sha256_hex, ChunkContext};
use crate::compile::calibrate;
use crate::compile::client::{self, CompileClientConfig};
use crate::compile::draft;
use crate::compile::sampler;
use crate::compile::storage::{CapsuleBuilder, CapsuleStore, FileCapsuleStore};
use crate::compile::{CompileMode, Frame, RawCompileOutput, SamplerOptions};

pub type ProgressFn = Box<dyn Fn(&str, u8, &str) + Send + Sync>;
pub type CheckpointFn = Box<dyn Fn() -> Result<(), String> + Send + Sync>;
pub const COMPILE_CANCELLED_ERROR: &str = "compile cancelled by user";

pub struct CompileOptions {
    pub ffmpeg_path: std::path::PathBuf,
    pub ffprobe_path: std::path::PathBuf,
    pub storage_dir: std::path::PathBuf,
    pub sampler: SamplerOptions,
    pub client_config: Option<CompileClientConfig>,
    pub prefer_draft: bool,
    pub on_progress: Option<ProgressFn>,
    pub checkpoint: Option<CheckpointFn>,
    #[allow(dead_code)]
    pub gguf_model_path: Option<std::path::PathBuf>,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct CompileResult {
    pub capsule_id: String,
    pub source_hash: String,
    pub version: u32,
    pub mode: CompileMode,
    pub evidence_count: usize,
    pub total_duration_sec: f32,
    pub sampling_metrics: Value,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone)]
struct CompileChunk {
    sequence: u32,
    frames: Vec<Frame>,
    anchor_indices: Vec<u32>,
    start_sec: f64,
    end_sec: f64,
}

pub fn compile_video(
    input_path: &Path,
    source_hash: &str,
    title: &str,
    opts: &CompileOptions,
) -> Result<CompileResult, String> {
    let callback = opts.on_progress.as_ref();
    let progress = |stage: &str, percent: u8, message: &str| {
        if let Some(callback) = callback {
            callback(stage, percent, message);
        }
    };
    let checkpoint = || -> Result<(), String> {
        if let Some(callback) = opts.checkpoint.as_ref() {
            callback()?;
        }
        Ok(())
    };

    checkpoint()?;
    progress("resolving", 2, "检查输入文件");
    if !input_path.is_file() {
        return Err(format!("input file not found: {}", input_path.display()));
    }

    let requested_mode = match &opts.client_config {
        Some(config) => draft::resolve_compile_mode(&config.base_url, opts.prefer_draft),
        None => CompileMode::LocalDraft,
    };
    progress(
        "sampling",
        5,
        match requested_mode {
            CompileMode::CloudPrecision => "模式: Cloud Precision",
            _ => "模式: Local Draft",
        },
    );

    checkpoint()?;

    progress("sampling", 8, "提取受预算约束的帧与音频");
    let sample = sampler::sample_video(
        input_path,
        &opts.ffmpeg_path,
        &opts.ffprobe_path,
        &opts.sampler,
    )?;

    checkpoint()?;

    // Video-capable providers use fixed-duration segments (1 minute max
    // to stay under typical request-body limits).
    let use_video = opts
        .client_config
        .as_ref()
        .map(|config| config.accepts_video)
        .unwrap_or(false);
    let max_segment = if use_video { Some(120.0) } else { None };
    let (chunks, anchor_map) = build_chunks(
        &sample.frames,
        &sample.frame_index_map,
        sample.duration_sec,
        max_segment,
    );
    let total_chunks = chunks.len() as u32;
    progress(
        "sampling",
        15,
        &format!(
            "{} 个锚点，{} 个编译切片，音频 {:.1}s",
            sample.frames.len(),
            total_chunks,
            sample.audio.duration_sec
        ),
    );

    let mut context = ChunkContext::first(total_chunks);
    let mut outputs: Vec<RawCompileOutput> = Vec::with_capacity(chunks.len());

    for (index, chunk) in chunks.iter().enumerate() {
        checkpoint()?;
        let percent = 18 + ((index as f32 / chunks.len() as f32) * 55.0) as u8;
        progress(
            "compiling",
            percent,
            &format!("编译切片 {}/{}", index + 1, chunks.len()),
        );

        let has_audio = !sample.audio.data.is_empty() && chunk.end_sec > chunk.start_sec;

        let output = if requested_mode == CompileMode::CloudPrecision {
            if let Some(config) = &opts.client_config {
                if config.accepts_video {
                    let video_data = sampler::cut_video_segment(
                        input_path,
                        &opts.ffmpeg_path,
                        chunk.start_sec,
                        chunk.end_sec,
                    )
                    .map_err(|e| format!("chunk {} video cut failed: {}", chunk.sequence, e))?;
                    if video_data.is_empty() {
                        return Err(format!(
                            "chunk {} video cut produced empty output",
                            chunk.sequence
                        ));
                    }
                    let output = client::compile_chunk_video(
                        config,
                        chunk.sequence,
                        total_chunks,
                        &chunk.anchor_indices,
                        &video_data,
                        Some(&context.prev_chunk_summary),
                    )?;
                    output
                } else {
                    // Provider does not support video; use local draft
                    draft::generate_local_draft(
                        title,
                        chunk.end_sec - chunk.start_sec,
                        &chunk.frames,
                        &chunk.anchor_indices,
                        has_audio,
                    )
                }
            } else {
                draft::generate_local_draft(
                    title,
                    chunk.end_sec - chunk.start_sec,
                    &chunk.frames,
                    &chunk.anchor_indices,
                    has_audio,
                )
            }
        } else {
            draft::generate_local_draft(
                title,
                chunk.end_sec - chunk.start_sec,
                &chunk.frames,
                &chunk.anchor_indices,
                has_audio,
            )
        };
        context = context.advance(&output.chunk_summary);
        outputs.push(output);
        checkpoint()?;
    }

    checkpoint()?;
    progress("normalizing", 76, "双向上下文校准与证据归一化");
    for index in 0..outputs.len() {
        let summary = outputs[index].chunk_summary.clone();
        let previous = if index > 0 {
            outputs[index - 1].chunk_summary.clone()
        } else {
            String::new()
        };
        let next = if index + 1 < outputs.len() {
            outputs[index + 1].chunk_summary.clone()
        } else {
            String::new()
        };
        for event in &mut outputs[index].events {
            if event.event_type.eq_ignore_ascii_case("draft") {
                event.confidence = event.confidence.min(0.3);
            } else {
                event.confidence =
                    calibrate::calibrate_confidence(event.confidence, &summary, &previous, &next);
            }
        }
    }

    let final_mode = if requested_mode == CompileMode::CloudPrecision
        && opts
            .client_config
            .as_ref()
            .map(|c| c.accepts_video)
            .unwrap_or(false)
    {
        CompileMode::CloudPrecision
    } else {
        CompileMode::LocalDraft
    };
    let model_used = match (&opts.client_config, final_mode) {
        (Some(config), CompileMode::CloudPrecision) => config.model.clone(),
        _ => "local-draft".to_string(),
    };
    let mut builder = CapsuleBuilder::new(
        source_hash.to_string(),
        title.to_string(),
        model_used,
        sample.duration_sec as f32,
        final_mode,
    );

    let mut previous_summary_hash: Option<String> = None;
    for (index, output) in outputs.into_iter().enumerate() {
        let current_summary_hash = sha256_hex(&output.chunk_summary);
        builder.add_chunk(
            index as u32,
            output.events,
            &output.chunk_summary,
            &anchor_map,
            previous_summary_hash.clone(),
        );
        previous_summary_hash = Some(current_summary_hash);
    }

    checkpoint()?;
    progress("storing", 88, "分配不可变版本并原子提交");
    let mut store = FileCapsuleStore::new(opts.storage_dir.clone());
    let version = store.reserve_next_version(source_hash)?;
    let capsule = builder.build(version);
    let evidence_count = capsule.evidences.len();
    #[allow(unused_mut)]
    let mut warnings = capsule.warnings.clone();
    let mode = capsule.compilation_mode;

    // Persist v0.2 bundle alongside the legacy capsule (behind feature gate)
    #[cfg(feature = "compiler_v3")]
    {
        use crate::compile_v3::{BundleStore, FileBundleStore as V3FileStore};
        let mut v3_store = V3FileStore::new(opts.storage_dir.clone());
        if let Err(e) = v3_store.insert(&crate::compile_v3::convert(&capsule)) {
            warnings.push(format!("v0.2 persistence warning: {e}"));
        }
    }

    let capsule_id = match store.insert(capsule) {
        Ok(id) => id,
        Err(error) => {
            store.cancel_reservation(source_hash, version);
            return Err(format!("storage error: {error}"));
        }
    };

    progress("complete", 100, "编译完成");
    Ok(CompileResult {
        capsule_id,
        source_hash: source_hash.to_string(),
        version,
        mode,
        evidence_count,
        total_duration_sec: sample.duration_sec as f32,
        sampling_metrics: json!({
            "anchor_count": sample.metrics.anchor_count,
            "audio_duration_sec": sample.metrics.audio_duration_sec,
            "audio_rms_dbfs": sample.metrics.audio_rms_dbfs,
            "duration_sec": sample.metrics.duration_sec,
        }),
        warnings,
    })
}

fn build_chunks(
    frames: &[Frame],
    frame_map: &HashMap<u32, f64>,
    total_duration: f64,
    segment_duration_sec: Option<f64>,
) -> (Vec<CompileChunk>, HashMap<u32, f64>) {
    let mut anchors = frame_map.clone();
    if !frames.is_empty() {
        // Video segment mode: split by fixed duration
        if let Some(max_dur) = segment_duration_sec.filter(|d| *d > 0.0) {
            let count = (total_duration / max_dur).ceil().max(1.0) as usize;
            let mut chunks = Vec::with_capacity(count);
            let mut anchor_offset: u32 = 0;
            for index in 0..count {
                let start_sec = index as f64 * max_dur;
                let end_sec = ((index + 1) as f64 * max_dur)
                    .min(total_duration)
                    .max(start_sec + 0.1);
                let chunk_dur = end_sec - start_sec;
                // Generate 1-per-second anchors so the model can express
                // sub-segment time ranges (e.g. [3, 7] for seconds 3→7).
                let num_anchors = chunk_dur.ceil() as u32 + 1;
                let chunk_anchors: Vec<u32> = (0..num_anchors).map(|i| anchor_offset + i).collect();

                // Collect frames that fall within this segment (for draft fallback)
                let seg_frames: Vec<Frame> = frames
                    .iter()
                    .filter(|f| f.timestamp_sec >= start_sec && f.timestamp_sec < end_sec)
                    .copied()
                    .collect();
                for &a in &chunk_anchors {
                    let ts = start_sec + (a - anchor_offset) as f64;
                    anchors.insert(a, ts.min(total_duration));
                }
                chunks.push(CompileChunk {
                    sequence: index as u32,
                    frames: seg_frames,
                    anchor_indices: chunk_anchors,
                    start_sec,
                    end_sec,
                });
                anchor_offset += num_anchors;
            }
            return (chunks, anchors);
        }

        // No segment_duration: use the full duration as one chunk (non-video path with frames)
        let anchor_indices: Vec<u32> = frames.iter().map(|f| f.index).collect();
        let chunks = vec![CompileChunk {
            sequence: 0,
            frames: frames.to_vec(),
            anchor_indices,
            start_sec: frames.first().map(|f| f.timestamp_sec).unwrap_or(0.0),
            end_sec: total_duration,
        }];
        return (chunks, anchors);
    }

    // No frames available: single chunk covering the full duration
    let duration = total_duration.max(0.1);
    let start_anchor = 0u32;
    let end_anchor = 1u32;
    anchors.insert(start_anchor, 0.0);
    anchors.insert(end_anchor, duration);
    let chunks = vec![CompileChunk {
        sequence: 0,
        frames: Vec::new(),
        anchor_indices: vec![start_anchor, end_anchor],
        start_sec: 0.0,
        end_sec: duration,
    }];
    (chunks, anchors)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn audio_only_media_gets_single_chunk() {
        let (chunks, anchors) = build_chunks(&[], &HashMap::new(), 125.0, None);
        assert_eq!(chunks.len(), 1);
        assert_eq!(anchors.get(&0), Some(&0.0));
        assert_eq!(anchors.get(&1), Some(&125.0));
    }

    #[test]
    fn visual_chunks_use_duration_segments() {
        let frames = (0..17)
            .map(|index| Frame {
                index,
                timestamp_sec: index as f64,
            })
            .collect::<Vec<_>>();
        let map = frames
            .iter()
            .map(|frame| (frame.index, frame.timestamp_sec))
            .collect();
        // 60s segments for a 17s video → only 1 chunk
        let (chunks, anchors) = build_chunks(&frames, &map, 17.0, Some(60.0));
        assert_eq!(chunks.len(), 1);
        // 1-per-second anchors: 0..=17 (18 anchors total)
        assert_eq!(chunks[0].anchor_indices.len(), 18);
        assert_eq!(anchors.get(&0), Some(&0.0));
        assert_eq!(anchors.get(&17), Some(&17.0));
    }
}
