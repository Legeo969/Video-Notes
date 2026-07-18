//! Media probing and bounded video segment cutting.
//!
//! The sampler never decodes an entire source merely to discover metadata.
//! Cloud compilation receives short video clips with their embedded audio.

use std::collections::HashMap;
use std::fs;
use std::io::{self, Read};
use std::path::Path;
use std::process::{Command, Output, Stdio};
use std::thread;
use std::time::Duration;

use crate::compile::{AudioBuffer, Frame, SampleOutput, SamplerOptions, SamplingMetrics};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;
const MAX_MEDIA_DURATION_SEC: f64 = 2.0 * 60.0 * 60.0;
const MAX_PROCESS_OUTPUT_BYTES: usize = 64 * 1024;
const MAX_VIDEO_SEGMENT_BYTES: u64 = 96 * 1024 * 1024;

#[derive(Clone, Copy, Default)]
pub struct ProcessControl<'a> {
    pub checkpoint: Option<&'a (dyn Fn() -> Result<(), String> + Send + Sync)>,
    pub on_started: Option<&'a (dyn Fn(u32) + Send + Sync)>,
    pub on_finished: Option<&'a (dyn Fn(u32) + Send + Sync)>,
}

impl ProcessControl<'_> {
    fn checkpoint(self) -> Result<(), String> {
        if let Some(checkpoint) = self.checkpoint {
            checkpoint()?;
        }
        Ok(())
    }
}

struct ProcessGuard<'a> {
    pid: u32,
    on_finished: Option<&'a (dyn Fn(u32) + Send + Sync)>,
}

impl Drop for ProcessGuard<'_> {
    fn drop(&mut self) {
        if let Some(on_finished) = self.on_finished {
            on_finished(self.pid);
        }
    }
}

fn hidden_cmd(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Probe media metadata once and build backend-controlled time anchors.
pub fn sample_video(
    input_path: &Path,
    ffprobe_path: &Path,
    options: &SamplerOptions,
    process_control: ProcessControl<'_>,
) -> Result<SampleOutput, String> {
    let (duration_sec, has_audio) = probe_media(input_path, ffprobe_path, process_control)?;
    if !duration_sec.is_finite() || duration_sec <= 0.0 {
        return Err("media duration is zero or could not be determined".to_string());
    }
    if duration_sec > MAX_MEDIA_DURATION_SEC {
        return Err(format!(
            "media duration {:.0}s exceeds the 2-hour safety limit",
            duration_sec
        ));
    }

    let anchor_count = (duration_sec * options.anchor_rate)
        .ceil()
        .clamp(1.0, options.max_anchors as f64) as u32;
    let frames: Vec<Frame> = (0..anchor_count)
        .map(|index| {
            let timestamp_sec = if anchor_count > 1 {
                index as f64 * duration_sec / anchor_count as f64
            } else {
                0.0
            };
            Frame {
                index,
                timestamp_sec,
            }
        })
        .collect();
    let frame_index_map = frames
        .iter()
        .map(|frame| (frame.index, frame.timestamp_sec))
        .collect::<HashMap<_, _>>();
    let audio = AudioBuffer {
        has_audio,
        duration_sec: if has_audio { duration_sec } else { 0.0 },
        rms_dbfs: None,
    };

    Ok(SampleOutput {
        metrics: SamplingMetrics {
            duration_sec,
            audio_duration_sec: audio.duration_sec,
            audio_rms_dbfs: audio.rms_dbfs,
            anchor_count: frames.len() as u32,
        },
        frames,
        audio,
        frame_index_map,
        duration_sec,
    })
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum FastEncoder {
    Nvenc,
    Software,
}

/// Probe the encoder once per compile. An NVENC failure later permanently
/// switches that compile to software instead of retrying NVENC for every clip.
pub(crate) fn detect_fast_encoder(
    ffmpeg: &Path,
    process_control: ProcessControl<'_>,
) -> Result<FastEncoder, String> {
    let mut command = hidden_cmd(ffmpeg);
    command.args(["-hide_banner", "-encoders"]);
    let output = run_output(&mut command, "ffmpeg encoder probe", process_control)?;
    if output.status.success() && String::from_utf8_lossy(&output.stdout).contains("h264_nvenc") {
        Ok(FastEncoder::Nvenc)
    } else {
        Ok(FastEncoder::Software)
    }
}

/// Cut one bounded MP4 segment. Every child process is registered with the
/// owning job and polled through its cancellation checkpoint.
pub(crate) fn cut_video_segment(
    input: &Path,
    ffmpeg: &Path,
    start_sec: f64,
    end_sec: f64,
    encoder: &mut FastEncoder,
    process_control: ProcessControl<'_>,
) -> Result<Vec<u8>, String> {
    let temp_dir = tempfile::Builder::new()
        .prefix("vn-compile-")
        .tempdir_in(resolve_temp_dir())
        .map_err(|error| format!("failed to create video segment workspace: {error}"))?;
    let output_path = temp_dir.path().join("segment.mp4");
    let duration_sec = (end_sec - start_sec).clamp(0.1, 120.0);

    let mut command = segment_command(
        ffmpeg,
        input,
        &output_path,
        start_sec,
        duration_sec,
        *encoder,
    );
    let output = run_output(&mut command, "ffmpeg video segment", process_control)?;

    if !output.status.success() && *encoder == FastEncoder::Nvenc {
        *encoder = FastEncoder::Software;
        let mut fallback = segment_command(
            ffmpeg,
            input,
            &output_path,
            start_sec,
            duration_sec,
            *encoder,
        );
        let fallback_output =
            run_output(&mut fallback, "ffmpeg software fallback", process_control)?;
        if !fallback_output.status.success() {
            return Err(format!(
                "ffmpeg video segment failed (NVENC: {}; software: {})",
                process_error(&output),
                process_error(&fallback_output)
            ));
        }
    } else if !output.status.success() {
        return Err(format!(
            "ffmpeg video segment failed: {}",
            process_error(&output)
        ));
    }

    let output_size = output_path
        .metadata()
        .map_err(|error| format!("failed to inspect video segment: {error}"))?
        .len();
    if output_size == 0 {
        return Err("ffmpeg video segment produced empty output".to_string());
    }
    if output_size > MAX_VIDEO_SEGMENT_BYTES {
        return Err(format!(
            "video segment exceeds the 96 MiB safety limit ({output_size} bytes)"
        ));
    }
    fs::read(&output_path).map_err(|error| format!("failed to read video segment: {error}"))
}

fn segment_command(
    ffmpeg: &Path,
    input: &Path,
    output: &Path,
    start_sec: f64,
    duration_sec: f64,
    encoder: FastEncoder,
) -> Command {
    let mut command = hidden_cmd(ffmpeg);
    command
        .args(["-hide_banner", "-loglevel", "error", "-y", "-ss"])
        .arg(format!("{start_sec:.3}"))
        .arg("-i")
        .arg(input)
        .arg("-t")
        .arg(format!("{duration_sec:.3}"))
        .args(["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]);
    match encoder {
        FastEncoder::Nvenc => {
            command.args([
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p3",
                "-cq",
                "30",
                "-vf",
                "scale=640:-2",
            ]);
        }
        FastEncoder::Software => {
            command.args([
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "32",
                "-vf",
                "scale=640:-2",
            ]);
        }
    }
    command
        .args([
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-movflags",
            "+faststart",
        ])
        .arg(output);
    command
}

fn probe_media(
    input: &Path,
    ffprobe: &Path,
    process_control: ProcessControl<'_>,
) -> Result<(f64, bool), String> {
    let mut command = hidden_cmd(ffprobe);
    command
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,duration",
            "-of",
            "json",
            "-i",
        ])
        .arg(input);
    let output = run_output(&mut command, "ffprobe media probe", process_control)?;
    if !output.status.success() {
        return Err(format!(
            "ffprobe could not inspect media: {}",
            process_error(&output)
        ));
    }
    parse_media_probe(&output.stdout)
}

fn parse_media_probe(data: &[u8]) -> Result<(f64, bool), String> {
    let value: serde_json::Value = serde_json::from_slice(data)
        .map_err(|error| format!("failed to parse ffprobe JSON: {error}"))?;
    let streams = value
        .get("streams")
        .and_then(serde_json::Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    let has_audio = streams.iter().any(|stream| {
        stream.get("codec_type").and_then(serde_json::Value::as_str) == Some("audio")
    });
    let format_duration = value.pointer("/format/duration").and_then(json_number);
    let stream_duration = streams
        .iter()
        .filter_map(|stream| stream.get("duration").and_then(json_number))
        .filter(|duration| duration.is_finite() && *duration > 0.0)
        .fold(0.0_f64, f64::max);
    let duration_sec = format_duration.unwrap_or(stream_duration);
    if !duration_sec.is_finite() || duration_sec <= 0.0 {
        return Err("failed to parse a positive duration from ffprobe output".to_string());
    }
    Ok((duration_sec, has_audio))
}

fn json_number(value: &serde_json::Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|text| text.parse::<f64>().ok()))
}

fn run_output(
    command: &mut Command,
    label: &str,
    process_control: ProcessControl<'_>,
) -> Result<Output, String> {
    process_control.checkpoint()?;
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("{label} failed to start: {error}"))?;
    let pid = child.id();
    if let Some(on_started) = process_control.on_started {
        on_started(pid);
    }
    let _guard = ProcessGuard {
        pid,
        on_finished: process_control.on_finished,
    };
    let stdout = child.stdout.take().ok_or_else(|| {
        let _ = child.kill();
        let _ = child.wait();
        format!("{label} stdout was not captured")
    })?;
    let stderr = child.stderr.take().ok_or_else(|| {
        let _ = child.kill();
        let _ = child.wait();
        format!("{label} stderr was not captured")
    })?;
    let stdout_reader = thread::spawn(move || read_capped(stdout));
    let stderr_reader = thread::spawn(move || read_capped(stderr));

    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if let Err(error) = process_control.checkpoint() {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = join_reader(stdout_reader, label, "stdout");
                    let _ = join_reader(stderr_reader, label, "stderr");
                    return Err(error);
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                let _ = join_reader(stdout_reader, label, "stdout");
                let _ = join_reader(stderr_reader, label, "stderr");
                return Err(format!("failed while waiting for {label}: {error}"));
            }
        }
    };
    let stdout = join_reader(stdout_reader, label, "stdout")?;
    let stderr = join_reader(stderr_reader, label, "stderr")?;
    process_control.checkpoint()?;
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

fn read_capped(mut reader: impl Read) -> io::Result<Vec<u8>> {
    let mut kept = Vec::new();
    let mut buffer = [0_u8; 8 * 1024];
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        let remaining = MAX_PROCESS_OUTPUT_BYTES.saturating_sub(kept.len());
        kept.extend_from_slice(&buffer[..count.min(remaining)]);
    }
    Ok(kept)
}

fn join_reader(
    handle: thread::JoinHandle<io::Result<Vec<u8>>>,
    label: &str,
    stream: &str,
) -> Result<Vec<u8>, String> {
    handle
        .join()
        .map_err(|_| format!("{label} {stream} reader panicked"))?
        .map_err(|error| format!("failed to read {label} {stream}: {error}"))
}

fn process_error(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).trim().to_string()
}

fn resolve_temp_dir() -> std::path::PathBuf {
    let raw = std::env::temp_dir();
    let path_str = raw.to_string_lossy();
    if raw.is_absolute() && !path_str.contains('%') {
        return raw;
    }
    if let Ok(profile) = std::env::var("USERPROFILE") {
        if !profile.contains('%') {
            let fallback = std::path::PathBuf::from(profile).join("AppData/Local/Temp");
            let _ = fs::create_dir_all(&fallback);
            return fallback;
        }
    }
    std::env::current_dir()
        .unwrap_or_else(|_| std::path::PathBuf::from("."))
        .join("temp")
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::time::Instant;

    use super::*;

    #[test]
    fn anchors_cover_duration() {
        let frames = (0..10)
            .map(|index| Frame {
                index,
                timestamp_sec: index as f64 * 0.5,
            })
            .collect::<Vec<_>>();
        assert_eq!(frames.len(), 10);
        assert!((frames.last().unwrap().timestamp_sec - 4.5).abs() < 0.001);
    }

    #[test]
    fn media_probe_detects_duration_and_audio_without_decoding() {
        let (duration, has_audio) = parse_media_probe(
            br#"{"streams":[{"codec_type":"video"},{"codec_type":"audio"}],"format":{"duration":"123.45"}}"#,
        )
        .unwrap();
        assert!((duration - 123.45).abs() < 0.001);
        assert!(has_audio);
    }

    #[test]
    fn media_probe_falls_back_to_stream_duration() {
        let (duration, has_audio) = parse_media_probe(
            br#"{"streams":[{"codec_type":"video","duration":"9.5"}],"format":{"duration":"N/A"}}"#,
        )
        .unwrap();
        assert!((duration - 9.5).abs() < 0.001);
        assert!(!has_audio);
    }

    #[test]
    fn cancellable_process_is_reaped_and_unregistered() {
        let checkpoints = AtomicU32::new(0);
        let started_pid = AtomicU32::new(0);
        let finished_pid = AtomicU32::new(0);
        let checkpoint = || {
            if checkpoints.fetch_add(1, Ordering::SeqCst) >= 2 {
                Err("cancelled for test".to_string())
            } else {
                Ok(())
            }
        };
        let started = |pid| started_pid.store(pid, Ordering::SeqCst);
        let finished = |pid| finished_pid.store(pid, Ordering::SeqCst);
        let control = ProcessControl {
            checkpoint: Some(&checkpoint),
            on_started: Some(&started),
            on_finished: Some(&finished),
        };
        #[cfg(windows)]
        let mut command = {
            let mut command = Command::new("ping");
            command.args(["-n", "30", "127.0.0.1"]);
            command
        };
        #[cfg(not(windows))]
        let mut command = Command::new("sleep");
        #[cfg(not(windows))]
        command.arg("30");

        let started_at = Instant::now();
        let error = run_output(&mut command, "test child", control).unwrap_err();
        assert_eq!(error, "cancelled for test");
        assert!(started_at.elapsed() < Duration::from_secs(5));
        assert_ne!(started_pid.load(Ordering::SeqCst), 0);
        assert_eq!(
            started_pid.load(Ordering::SeqCst),
            finished_pid.load(Ordering::SeqCst)
        );
    }
}
