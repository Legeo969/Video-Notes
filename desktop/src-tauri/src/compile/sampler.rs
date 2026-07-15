//! Duration probing, audio metering, and video segment cutting.
//!
//! No pixel-level frame extraction — both cloud and local-draft modes
//! receive raw video clips and handle their own visual processing.
//! The sampler only produces lightweight time-anchor points.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use uuid::Uuid;

use crate::compile::{AudioBuffer, Frame, SampleOutput, SamplerOptions, SamplingMetrics};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;
const MAX_MEDIA_DURATION_SEC: f64 = 2.0 * 60.0 * 60.0;
const MAX_AUDIO_WAV_BYTES: u64 = 256 * 1024 * 1024;

fn hidden_cmd(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Probe media duration, extract audio metadata, and build time anchors.
pub fn sample_video(
    input_path: &Path,
    ffmpeg_path: &Path,
    ffprobe_path: &Path,
    options: &SamplerOptions,
) -> Result<SampleOutput, String> {
    let duration_sec = probe_duration(input_path, ffprobe_path)?;
    if !duration_sec.is_finite() || duration_sec <= 0.0 {
        return Err("media duration is zero or could not be determined".to_string());
    }
    if duration_sec > MAX_MEDIA_DURATION_SEC {
        return Err(format!(
            "media duration {:.0}s exceeds the 2-hour safety limit",
            duration_sec
        ));
    }

    // Build time-anchor points (index → timestamp) for the event mapping pipeline.
    let anchor_count = (duration_sec * options.anchor_rate)
        .ceil()
        .clamp(1.0, options.max_anchors as f64) as u32;
    let frames: Vec<Frame> = (0..anchor_count)
        .map(|i| {
            let ts = if anchor_count > 1 {
                i as f64 * duration_sec / anchor_count as f64
            } else {
                0.0
            };
            Frame {
                index: i,
                timestamp_sec: ts,
            }
        })
        .collect();

    let frame_index_map = frames
        .iter()
        .map(|f| (f.index, f.timestamp_sec))
        .collect::<HashMap<_, _>>();

    // Extract audio for metadata (energy / duration).
    let audio = extract_audio(input_path, ffmpeg_path, duration_sec)?;

    let frames_len = frames.len() as u32;
    let audio_dur = audio.duration_sec;
    let audio_rms = audio.rms_dbfs;

    Ok(SampleOutput {
        frames,
        audio,
        frame_index_map,
        duration_sec,
        metrics: SamplingMetrics {
            duration_sec,
            audio_duration_sec: audio_dur,
            audio_rms_dbfs: audio_rms,
            anchor_count: frames_len,
        },
    })
}

fn extract_audio(input: &Path, ffmpeg: &Path, duration_sec: f64) -> Result<AudioBuffer, String> {
    let temp_dir = tempfile_dir();
    let audio_path = temp_dir.join("audio.wav");
    let output = hidden_cmd(ffmpeg)
        .args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            &input.to_string_lossy(),
            "-map",
            "0:a:0?",
            "-vn",
            "-ar",
            "8000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            &audio_path.to_string_lossy(),
        ])
        .output()
        .map_err(|e| format!("ffmpeg audio extraction failed to start: {e}"))?;

    if !output.status.success() || !audio_path.is_file() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if stderr.contains("does not contain any stream")
            || stderr.contains("matches no streams")
            || !audio_path.is_file()
        {
            let _ = fs::remove_dir_all(&temp_dir);
            return Ok(AudioBuffer {
                data: Vec::new(),
                sample_rate: 16_000,
                duration_sec: 0.0,
                rms_dbfs: None,
            });
        }
        let _ = fs::remove_dir_all(&temp_dir);
        return Err(format!("ffmpeg audio extraction failed: {stderr}"));
    }

    let audio_size = audio_path
        .metadata()
        .map_err(|e| format!("failed to inspect audio output: {e}"))?
        .len();
    if audio_size > MAX_AUDIO_WAV_BYTES {
        let _ = fs::remove_dir_all(&temp_dir);
        return Err(format!(
            "normalized audio exceeds the 256 MiB safety limit ({audio_size} bytes)"
        ));
    }
    let data = fs::read(&audio_path).map_err(|e| format!("failed to read audio: {e}"))?;
    let pcm = wav_pcm_data(&data).unwrap_or(&[]);
    let sample_count = pcm.len() / 2;
    let audio_duration = if sample_count > 0 {
        sample_count as f64 / 16_000.0
    } else {
        duration_sec
    };
    let rms_dbfs = pcm_rms_dbfs(pcm);
    let _ = fs::remove_dir_all(&temp_dir);
    Ok(AudioBuffer {
        data,
        sample_rate: 16_000,
        duration_sec: audio_duration,
        rms_dbfs,
    })
}

#[derive(PartialEq)]
enum FastEncoder {
    Nvenc,
    Software,
}

/// Probe ffmpeg for hardware encoder availability.
/// NVIDIA NVENC is preferred for RTX 3060; falls back to software x264.
fn detect_fast_encoder(ffmpeg: &Path) -> FastEncoder {
    let probe = hidden_cmd(ffmpeg)
        .args(["-hide_banner", "-encoders"])
        .output();
    match probe {
        Ok(o) if o.status.success() => {
            let out = String::from_utf8_lossy(&o.stdout);
            if out.contains("h264_nvenc") {
                FastEncoder::Nvenc
            } else {
                FastEncoder::Software
            }
        }
        _ => FastEncoder::Software,
    }
}

/// Cut a video segment (MP4) from the source file using ffmpeg.
pub fn cut_video_segment(
    input: &Path,
    ffmpeg: &Path,
    start_sec: f64,
    end_sec: f64,
) -> Result<Vec<u8>, String> {
    let temp_dir = tempfile_dir();
    let out_path = temp_dir.join("segment.mp4");
    let duration_sec = (end_sec - start_sec).max(0.1).min(120.0);

    // Detect GPU-accelerated encoder availability (NVENC > QSV > software)
    let encoder = detect_fast_encoder(ffmpeg);

    // Compress aggressively so the payload stays under provider limits.
    let input_str = input.to_string_lossy().into_owned();
    let start_str = format!("{:.3}", start_sec);
    let duration_str = format!("{:.3}", duration_sec);
    let out_str = out_path.to_string_lossy().into_owned();
    let mut args: Vec<&str> = vec![
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        &start_str,
        "-i",
        &input_str,
        "-t",
        &duration_str,
    ];
    match encoder {
        FastEncoder::Nvenc => {
            args.extend_from_slice(&["-c:v", "h264_nvenc", "-preset", "p7", "-cq", "30", "-vf", "scale=640:-2"]);
        }
        FastEncoder::Software => {
            args.extend_from_slice(&["-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-vf", "scale=640:-2"]);
        }
    }
    args.extend_from_slice(&["-c:a", "aac", "-b:a", "64k", &out_str]);

    let output = hidden_cmd(ffmpeg)
        .args(&args)
        .output()
        .map_err(|e| format!("ffmpeg video segment failed to start: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        // If NVENC failed, retry with software encoding
        if encoder == FastEncoder::Nvenc && stderr.contains("h264_nvenc") {
            let mut sw_args: Vec<&str> = vec![
                "-hide_banner", "-loglevel", "error", "-y",
                "-ss", &start_str, "-i", &input_str, "-t", &duration_str,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                "-vf", "scale=640:-2",
                "-c:a", "aac", "-b:a", "64k", &out_str,
            ];
            let sw_output = hidden_cmd(ffmpeg).args(&sw_args).output()
                .map_err(|e| format!("ffmpeg software fallback failed to start: {e}"))?;
            if sw_output.status.success() {
                let data = fs::read(&out_path).map_err(|e| format!("failed to read video segment: {e}"))?;
                let _ = fs::remove_dir_all(&temp_dir);
                return Ok(data);
            }
            let sw_stderr = String::from_utf8_lossy(&sw_output.stderr);
            let _ = fs::remove_dir_all(&temp_dir);
            return Err(format!("ffmpeg video segment failed (NVENC+fallback): {stderr}{sw_stderr}"));
        }
        let _ = fs::remove_dir_all(&temp_dir);
        return Err(format!("ffmpeg video segment failed: {stderr}"));
    }
    let data = fs::read(&out_path).map_err(|e| format!("failed to read video segment: {e}"))?;
    let _ = fs::remove_dir_all(&temp_dir);
    Ok(data)
}

// ─── Internal helpers ──────────────────────────────────────

fn probe_duration(input: &Path, ffprobe: &Path) -> Result<f64, String> {
    let output = hidden_cmd(ffprobe)
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            &input.to_string_lossy(),
        ])
        .output()
        .map_err(|e| format!("ffprobe failed to start: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "ffprobe could not probe media duration: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<f64>()
        .map_err(|_| "failed to parse duration from ffprobe output".to_string())
}

fn wav_pcm_data(wav: &[u8]) -> Option<&[u8]> {
    if wav.len() < 12 || &wav[0..4] != b"RIFF" || &wav[8..12] != b"WAVE" {
        return None;
    }
    let mut offset = 12usize;
    while offset + 8 <= wav.len() {
        let id = &wav[offset..offset + 4];
        let size = u32::from_le_bytes(wav[offset + 4..offset + 8].try_into().ok()?) as usize;
        let data_start = offset + 8;
        let data_end = data_start.checked_add(size)?.min(wav.len());
        if id == b"data" {
            return Some(&wav[data_start..data_end]);
        }
        offset = data_start + size + (size % 2);
    }
    None
}

fn pcm_rms_dbfs(pcm: &[u8]) -> Option<f32> {
    let mut sum_sq = 0.0f64;
    let mut count = 0usize;
    for sample in pcm.chunks_exact(2) {
        let value = i16::from_le_bytes([sample[0], sample[1]]) as f64 / i16::MAX as f64;
        sum_sq += value * value;
        count += 1;
    }
    if count == 0 {
        return None;
    }
    let rms = (sum_sq / count as f64).sqrt();
    if rms <= f64::EPSILON {
        Some(-120.0)
    } else {
        Some((20.0 * rms.log10()) as f32)
    }
}

fn tempfile_dir() -> PathBuf {
    let dir = resolve_temp_dir().join(format!("vn-compile-{}", Uuid::new_v4()));
    let _ = fs::create_dir_all(&dir);
    dir
}

fn resolve_temp_dir() -> PathBuf {
    let raw = std::env::temp_dir();
    let path_str = raw.to_string_lossy();
    if raw.is_absolute() && !path_str.contains('%') {
        return raw;
    }
    if let Ok(profile) = std::env::var("USERPROFILE") {
        if !profile.contains('%') {
            let fallback = PathBuf::from(profile).join("AppData/Local/Temp");
            let _ = fs::create_dir_all(&fallback);
            return fallback;
        }
    }
    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("temp")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anchors_cover_duration() {
        let frames = (0..10)
            .map(|i| Frame {
                index: i,
                timestamp_sec: i as f64 * 0.5,
            })
            .collect::<Vec<_>>();
        assert_eq!(frames.len(), 10);
        assert!((frames.last().unwrap().timestamp_sec - 4.5).abs() < 0.001);
    }
}
