/// Intelligent Sampler — Pass 1 of the Multimodal Compile Pipeline.
///
/// Extracts frames and audio from a video with:
/// - 1 fps hard cap (2 fps with high_precision opt-in)
/// - Perceptual hash dedup (dHash, Hamming distance < 10 → discard)
/// - Static scene suppression (audio active + low variance → 0.2 fps)
/// - Minimum 1 frame guarantee
/// - Frame index → timestamp binding protocol

use std::collections::HashMap;
use std::fs;
use std::os::raw::c_ulong;
use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(windows)]
const CREATE_NO_WINDOW: c_ulong = 0x08000000;

/// Create a Command that doesn't flash a console window on Windows.
fn hidden_cmd(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

use image::{GrayImage, ImageEncoder};

#[cfg(test)]
use image::Luma;

use crate::compile::{AudioBuffer, Frame, SamplingMetrics, SampleOutput, SamplerOptions};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Run the intelligent sampler on a video file.
///
/// Returns sampled frames (PNG bytes + metadata), 16 kHz mono audio (WAV),
/// and a frame_index → physical_seconds mapping table.
pub fn sample_video(
    input_path: &Path,
    ffmpeg_path: &Path,
    ffprobe_path: &Path,
    options: &SamplerOptions,
) -> Result<SampleOutput, String> {
    // 1. Probe video duration
    let duration_sec = probe_duration(input_path, ffprobe_path)?;
    if duration_sec <= 0.0 {
        return Err("video duration is zero or could not be determined".to_string());
    }

    // 2. Compute sampling parameters
    let fps = options.effective_fps();
    let interval_sec = 1.0 / fps; // e.g. 1.0 for 1 fps
    let max_candidate_frames = (duration_sec * fps).ceil() as u32;

    // 3. Extract frames to temp directory
    let temp_dir = tempfile_dir();
    let frame_dir = temp_dir.join("frames");
    fs::create_dir_all(&frame_dir).map_err(|e| format!("failed to create frame dir: {e}"))?;

    let frames = extract_frames(
        input_path,
        &frame_dir,
        interval_sec,
        max_candidate_frames,
        ffmpeg_path,
    )?;

    // 4. Compute dHash and variance for each frame, build mapping
    let mut candidate_frames: Vec<Frame> = Vec::new();
    let mut frame_index_map: HashMap<u32, f64> = HashMap::new();
    let mut frame_counter: u32 = 0;

    for (extracted_index, frame_path) in frames.iter().enumerate() {
        let timestamp_sec = extracted_index as f64 * interval_sec;
        let img = image::open(frame_path)
            .map_err(|e| format!("failed to read frame {extracted_index}: {e}"))?;
        let gray = img.to_luma8();
        let (width, height) = (gray.width(), gray.height());
        let phash = compute_dhash(&gray);
        let variance = compute_variance(&gray);

        let png_bytes = encode_png(&img)?;

        let frame = Frame {
            index: frame_counter,
            data: png_bytes,
            phash,
            variance,
            timestamp_sec,
            width,
            height,
        };
        frame_index_map.insert(frame_counter, timestamp_sec);
        candidate_frames.push(frame);
        frame_counter += 1;
    }

    // 5. Apply dedup and static suppression
    let (kept_frames, stats) = filter_frames(&candidate_frames, options, duration_sec);

    // 6. Extract and resample audio
    let audio = extract_audio(input_path, ffmpeg_path, duration_sec)?;

    // 7. Ensure minimum frame guarantee
    let final_frames = if kept_frames.is_empty() && !frames.is_empty() {
        // Fallback: keep the middle frame
        let mid_idx = frames.len() / 2;
        let mid_path = &frames[mid_idx];
        let timestamp_sec = mid_idx as f64 * interval_sec;
        let img = image::open(mid_path)
            .map_err(|e| format!("failed to read fallback frame: {e}"))?;
        let gray = img.to_luma8();
        let (width, height) = (gray.width(), gray.height());
        let png_bytes = encode_png(&img)?;
        vec![Frame {
            index: 0,
            data: png_bytes,
            phash: 0,
            variance: 0.0,
            timestamp_sec,
            width,
            height,
        }]
    } else {
        kept_frames
    };

    // Rebuild frame_index_map from final frames
    let final_map: HashMap<u32, f64> = final_frames
        .iter()
        .map(|f| (f.index, f.timestamp_sec))
        .collect();

    // Cleanup temp dir
    let _ = fs::remove_dir_all(&temp_dir);

    Ok(SampleOutput {
        frames: final_frames,
        audio,
        frame_index_map: final_map,
        duration_sec,
        metrics: stats,
    })
}

// ---------------------------------------------------------------------------
// Frame extraction via ffmpeg
// ---------------------------------------------------------------------------

fn extract_frames(
    input: &Path,
    frame_dir: &Path,
    interval_sec: f64,
    max_frames: u32,
    ffmpeg: &Path,
) -> Result<Vec<PathBuf>, String> {
    let pattern = frame_dir.join("frame-%04d.png");
    let fps_str = format!("fps=1/{interval_sec}");

    let output = hidden_cmd(ffmpeg)
        .args([
            "-y",
            "-i",
            &input.to_string_lossy(),
            "-vf",
            &fps_str,
            "-frames:v",
            &max_frames.to_string(),
            "-qscale:v",
            "2",
            &pattern.to_string_lossy(),
        ])
        .output()
        .map_err(|e| format!("ffmpeg frame extraction failed to start: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("ffmpeg frame extraction failed: {stderr}"));
    }

    let mut frames: Vec<PathBuf> = fs::read_dir(frame_dir)
        .map_err(|e| format!("failed to read frame dir: {e}"))?
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.extension()
                .and_then(|v| v.to_str())
                .map(|ext| matches!(ext.to_ascii_lowercase().as_str(), "png"))
                .unwrap_or(false)
        })
        .collect();
    frames.sort();
    Ok(frames)
}

// ---------------------------------------------------------------------------
// Audio extraction via ffmpeg
// ---------------------------------------------------------------------------

fn extract_audio(input: &Path, ffmpeg: &Path, duration_sec: f64) -> Result<AudioBuffer, String> {
    let temp_dir = tempfile_dir();
    let audio_path = temp_dir.join("audio.wav");

    let output = hidden_cmd(ffmpeg)
        .args([
            "-y",
            "-i",
            &input.to_string_lossy(),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            &audio_path.to_string_lossy(),
        ])
        .output()
        .map_err(|e| format!("ffmpeg audio extraction failed to start: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        // Audio-only files may have no audio stream — return empty buffer
        if stderr.contains("No such filter") || stderr.contains("Invalid data found") {
            return Ok(AudioBuffer {
                data: Vec::new(),
                sample_rate: 16000,
                duration_sec: 0.0,
            });
        }
        return Err(format!("ffmpeg audio extraction failed: {stderr}"));
    }

    let data = fs::read(&audio_path).map_err(|e| format!("failed to read audio: {e}"))?;

    // Hack: compute audio duration from WAV header if available
    let audio_duration = if data.len() > 44 {
        // WAV: data sub-chunk size / (sample_rate * bytes_per_sample * channels)
        let data_size = u32::from_le_bytes([data[40], data[41], data[42], data[43]]);
        if data_size > 0 {
            data_size as f64 / (16000.0 * 2.0) // 16-bit mono = 2 bytes/sample
        } else {
            duration_sec
        }
    } else {
        duration_sec
    };

    let _ = fs::remove_dir_all(&temp_dir);

    Ok(AudioBuffer {
        data,
        sample_rate: 16000,
        duration_sec: audio_duration,
    })
}

// ---------------------------------------------------------------------------
// Perceptual hash (dHash — Difference Hash)
// ---------------------------------------------------------------------------

/// Compute a 64-bit difference hash for a grayscale image.
///
/// Algorithm: resize to 9×8, then for each row compare adjacent pixels
/// (left > right → set bit). This yields a 64-bit hash.
pub fn compute_dhash(img: &GrayImage) -> u64 {
    let small = image::imageops::resize(
        img,
        9,
        8,
        image::imageops::FilterType::Lanczos3,
    );
    let mut hash: u64 = 0;
    for y in 0..8 {
        for x in 0..8 {
            let left = small.get_pixel(x, y).0[0];
            let right = small.get_pixel(x + 1, y).0[0];
            if left > right {
                let bit = (y * 8 + x) as u64;
                hash |= 1 << bit;
            }
        }
    }
    hash
}

/// Compute Hamming distance between two 64-bit hashes.
pub fn hamming_distance(a: u64, b: u64) -> u32 {
    (a ^ b).count_ones()
}

// ---------------------------------------------------------------------------
// Frame variance
// ---------------------------------------------------------------------------

/// Compute pixel intensity variance (mean of squared differences from mean).
/// Low variance (< ~5.0) indicates a near-static or blank frame.
pub fn compute_variance(img: &GrayImage) -> f32 {
    let pixels: Vec<f32> = img.pixels().map(|p| p.0[0] as f32).collect();
    let len = pixels.len() as f32;
    if len == 0.0 {
        return 0.0;
    }
    let mean = pixels.iter().sum::<f32>() / len;
    let variance = pixels.iter().map(|v| (v - mean).powi(2)).sum::<f32>() / len;
    variance
}

// ---------------------------------------------------------------------------
// Frame filtering: dedup + static suppression
// ---------------------------------------------------------------------------

fn filter_frames(
    candidates: &[Frame],
    options: &SamplerOptions,
    _duration_sec: f64,
) -> (Vec<Frame>, SamplingMetrics) {
    let total = candidates.len() as u32;
    if candidates.is_empty() {
        return (
            Vec::new(),
            SamplingMetrics {
                total_candidates: 0,
                frames_kept: 0,
                frames_deduped: 0,
                frames_static_suppressed: 0,
                audio_duration_sec: 0.0,
            },
        );
    }

    let mut kept: Vec<Frame> = Vec::new();
    let mut deduped_count = 0u32;
    let mut suppressed_count = 0u32;

    let mut consecutive_static = 0u32;
    // Static scene downsampling: keep at most 1 frame per 5 seconds (0.2 fps)
    let static_keep_interval = (5.0 * options.effective_fps()).ceil() as u32;

    for frame in candidates.iter() {
        // Check for static scene suppression
        let is_static = frame.variance < options.static_variance_threshold;
        if is_static {
            consecutive_static += 1;
            if consecutive_static > 1
                && (consecutive_static % static_keep_interval) != 1
            {
                suppressed_count += 1;
                continue;
            }
        } else {
            consecutive_static = 0;
        }

        // Check pHash dedup against last kept frame
        if let Some(last) = kept.last() {
            if hamming_distance(last.phash, frame.phash) < options.phash_threshold {
                deduped_count += 1;
                continue;
            }
        }

        kept.push(frame.clone());
    }

    // Minimum frame guarantee
    if kept.is_empty() {
        kept.push(candidates.first().unwrap().clone());
        deduped_count = deduped_count.saturating_sub(1);
    }

    let kept_count = kept.len() as u32;

    (
        kept,
        SamplingMetrics {
            total_candidates: total,
            frames_kept: kept_count,
            frames_deduped: deduped_count,
            frames_static_suppressed: suppressed_count,
            audio_duration_sec: 0.0, // filled by caller
        },
    )
}

// ---------------------------------------------------------------------------
// Video duration probe via ffprobe
// ---------------------------------------------------------------------------

fn probe_duration(input: &Path, ffprobe: &Path) -> Result<f64, String> {
    let output = hidden_cmd(ffprobe)
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            &input.to_string_lossy(),
        ])
        .output()
        .map_err(|e| format!("ffprobe failed to start: {e}"))?;

    if !output.status.success() {
        return Err("ffprobe could not probe video duration".to_string());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .trim()
        .parse::<f64>()
        .map_err(|_| "failed to parse duration from ffprobe output".to_string())
}

// ---------------------------------------------------------------------------
// PNG encoding
// ---------------------------------------------------------------------------

fn encode_png(img: &image::DynamicImage) -> Result<Vec<u8>, String> {
    let mut buf = Vec::new();
    let encoder = image::codecs::png::PngEncoder::new(&mut buf);
    let rgb = img.to_rgb8();
    let (width, height) = (rgb.width(), rgb.height());
    encoder
        .write_image(rgb.as_raw(), width, height, image::ExtendedColorType::Rgb8)
        .map_err(|e| format!("PNG encode failed: {e}"))?;
    Ok(buf)
}

// ---------------------------------------------------------------------------
// Temp directory helper
// ---------------------------------------------------------------------------

fn tempfile_dir() -> PathBuf {
    let temp = resolve_temp_dir();
    let dir = temp.join(format!("vn-compile-{}", std::process::id()));
    let _ = fs::create_dir_all(&dir);
    dir
}

/// Resolve the system temp directory, handling unexpanded environment variables on Windows.
fn resolve_temp_dir() -> PathBuf {
    let raw = std::env::temp_dir();

    // Case 1: Path is absolute and contains no unexpanded vars — use it directly
    let path_str = raw.to_string_lossy();
    if raw.is_absolute() && !path_str.contains('%') {
        return raw;
    }

    // Case 2: Path contains unexpanded vars (e.g. `%USERPROFILE%\AppData\Local\Temp`)
    // or is relative. Reconstruct from USERPROFILE.
    if let Ok(profile) = std::env::var("USERPROFILE") {
        if !profile.contains('%') {
            let fallback = PathBuf::from(profile).join("AppData").join("Local").join("Temp");
            let _ = std::fs::create_dir_all(&fallback);
            return fallback;
        }
    }

    // Case 3: Last resort — try to expand env vars in the raw path
    let expanded = expand_windows_env_vars(&path_str);
    let fallback = PathBuf::from(&expanded);
    if fallback.is_absolute() {
        let _ = std::fs::create_dir_all(&fallback);
        return fallback;
    }

    // Case 4: Ultimate fallback — use current dir temp
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")).join("temp")
}

/// Expand common Windows environment variables in a path string.
fn expand_windows_env_vars(path: &str) -> String {
    let mut result = path.to_string();
    // %USERPROFILE%
    if let Ok(val) = std::env::var("USERPROFILE") {
        if !val.contains('%') {
            result = result.replace("%USERPROFILE%", &val);
        }
    }
    // %LOCALAPPDATA%
    if let Ok(val) = std::env::var("LOCALAPPDATA") {
        if !val.contains('%') {
            result = result.replace("%LOCALAPPDATA%", &val);
        }
    }
    // %APPDATA%
    if let Ok(val) = std::env::var("APPDATA") {
        if !val.contains('%') {
            result = result.replace("%APPDATA%", &val);
        }
    }
    result
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dhash_identical_images_have_zero_distance() {
        let img1 = GrayImage::from_pixel(16, 16, Luma([128u8]));
        let img2 = GrayImage::from_pixel(16, 16, Luma([128u8]));
        let h1 = compute_dhash(&img1);
        let h2 = compute_dhash(&img2);
        assert_eq!(hamming_distance(h1, h2), 0);
    }

    #[test]
    fn test_dhash_different_images_have_nonzero_distance() {
        let img1 = GrayImage::from_pixel(16, 16, Luma([0u8]));
        let img2 = GrayImage::from_pixel(16, 16, Luma([255u8]));
        let h1 = compute_dhash(&img1);
        let h2 = compute_dhash(&img2);
        let dist = hamming_distance(h1, h2);
        assert!(dist > 0, "all-black vs all-white should differ");
    }

    #[test]
    fn test_variance_uniform_is_zero() {
        let img = GrayImage::from_pixel(4, 4, Luma([100u8]));
        let v = compute_variance(&img);
        assert!((v - 0.0).abs() < 0.01, "uniform image should have ~0 variance");
    }

    #[test]
    fn test_variance_nonuniform_is_positive() {
        let mut img = GrayImage::new(4, 4);
        img.put_pixel(0, 0, Luma([0u8]));
        img.put_pixel(0, 1, Luma([255u8]));
        let v = compute_variance(&img);
        assert!(v > 0.0, "non-uniform image should have positive variance");
    }

    #[test]
    fn test_filter_frames_empty_input() {
        let opts = SamplerOptions::default();
        let (kept, metrics) = filter_frames(&[], &opts, 0.0);
        assert!(kept.is_empty());
        assert_eq!(metrics.total_candidates, 0);
    }

    #[test]
    fn test_filter_frames_dedup_similar_frames() {
        let opts = SamplerOptions::default();
        let f1 = Frame {
            index: 0, data: vec![], phash: 0xFFFF_FFFF_FFFF_FFFF,
            variance: 100.0, timestamp_sec: 0.0, width: 100, height: 100,
        };
        let f2 = Frame {
            index: 1, data: vec![], phash: 0xFFFF_FFFF_FFFF_FFF0, // Hamming dist = 4
            variance: 100.0, timestamp_sec: 1.0, width: 100, height: 100,
        };
        let f3 = Frame {
            index: 2, data: vec![], phash: 0x0000_0000_0000_0000, // very different
            variance: 100.0, timestamp_sec: 2.0, width: 100, height: 100,
        };
        let (kept, metrics) = filter_frames(&vec![f1, f2, f3], &opts, 10.0);
        // f1 kept, f2 deduped (distance 4 < 10), f3 kept (different)
        assert_eq!(kept.len(), 2);
        assert_eq!(metrics.frames_deduped, 1);
        assert_eq!(metrics.frames_kept, 2);
    }

    #[test]
    fn test_min_frame_guarantee() {
        let opts = SamplerOptions::default();
        // Single black frame followed by similar black frame
        let f1 = Frame {
            index: 0, data: vec![], phash: 0,
            variance: 0.1, timestamp_sec: 0.0, width: 100, height: 100,
        };
        let f2 = Frame {
            index: 1, data: vec![], phash: 0,
            variance: 0.1, timestamp_sec: 1.0, width: 100, height: 100,
        };
        let (kept, metrics) = filter_frames(&vec![f1, f2], &opts, 10.0);
        // f1 kept (first), f2 deduped → fallback keeps at least 1
        assert!(!kept.is_empty(), "minimum 1 frame guarantee");
        assert_eq!(metrics.frames_kept, 1);
    }
}