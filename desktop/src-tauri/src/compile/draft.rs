/// Local Draft Mode — graceful degradation when offline.
///
/// When the MLLM API is unreachable, the system falls back to a local draft engine
/// that uses a GGUF vision model via llama.cpp subprocess.
///
/// Requires: a GGUF model file (e.g. moondream-2b-int4.gguf) configured in settings.
/// Without it, Local Draft mode returns an error directing the user to configure one.

use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
use std::process::Command;
use std::time::Duration;

use crate::compile::{CompileMode, RawCompileOutput, RawEvent};

#[cfg(windows)]
const CREATE_NO_WINDOW: std::os::raw::c_ulong = 0x08000000;

fn hidden_cmd(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Timeout for network connectivity check.
const NETWORK_CHECK_TIMEOUT_SEC: u64 = 3;

/// Check if the MLLM API is reachable.
pub fn check_network_connectivity(api_base_url: &str) -> bool {
    let host = api_base_url
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");

    if host.is_empty() {
        return false;
    }

    let port = if api_base_url.starts_with("https://") {
        443
    } else {
        80
    };

    let Ok(addr) = (host, port).to_socket_addrs() else {
        return false;
    };
    let Some(addr) = addr.into_iter().next() else {
        return false;
    };

    TcpStream::connect_timeout(&addr, Duration::from_secs(NETWORK_CHECK_TIMEOUT_SEC)).is_ok()
}

/// Determine the compilation mode based on network availability.
pub fn resolve_compile_mode(api_base_url: &str, prefer_draft: bool) -> CompileMode {
    if prefer_draft {
        return CompileMode::LocalDraft;
    }
    if check_network_connectivity(api_base_url) {
        CompileMode::CloudPrecision
    } else {
        CompileMode::LocalDraft
    }
}

/// Generate a local draft using a GGUF vision model via llama.cpp.
///
/// Returns an error if no model path is configured or the model file doesn't exist.
pub fn generate_local_draft(
    video_title: &str,
    duration_sec: f64,
    frame_pngs: &[Vec<u8>],
    gguf_model_path: Option<&str>,
) -> Result<RawCompileOutput, String> {
    let model_path = gguf_model_path
        .filter(|p| !p.trim().is_empty())
        .ok_or_else(|| {
            "Local draft mode requires a GGUF model path. Set 'draft_model_path' in settings.".to_string()
        })?;

    if !Path::new(model_path).exists() {
        return Err(format!(
            "GGUF model file not found: {model_path}. Check 'draft_model_path' in settings."
        ));
    }

    let title = if video_title.is_empty() { "Untitled" } else { video_title };

    // Use the first frame for analysis (most representative)
    if let Some(frame) = frame_pngs.first() {
        let temp_dir = std::env::temp_dir().join("video-notes-draft");
        let _ = std::fs::create_dir_all(&temp_dir);
        let frame_path = temp_dir.join("draft-frame.png");
        std::fs::write(&frame_path, frame).map_err(|e| format!("failed to write temp frame: {e}"))?;

        let cli = find_llama_cli().ok_or_else(|| {
            "llama-cli not found on PATH or E:\\llama.cpp. Install llama.cpp or add it to your PATH.".to_string()
        })?;

        let cli_name = cli.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("llama-cli")
            .to_lowercase();

        let prompt = "Describe what you see in this image in one short sentence.";

        let model_path_obj = std::path::Path::new(model_path);
        let model_dir = model_path_obj.parent().unwrap_or(std::path::Path::new("."));

        // Build CLI args based on model type
        let is_qwen = cli_name.contains("qwen2vl") || model_path.to_lowercase().contains("qwen2.5-vl");
        let mut args: Vec<String> = Vec::new();

        if is_qwen || cli_name.contains("qwen2vl") {
            // Qwen2.5-VL needs --mmproj
            args.push("-m".to_string());
            args.push(model_path.to_string());
            // Find mmproj file next to model
            if let Ok(entries) = std::fs::read_dir(model_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_string();
                    if name.contains("mmproj") && name.ends_with(".gguf") {
                        args.push("--mmproj".to_string());
                        args.push(entry.path().to_string_lossy().to_string());
                        break;
                    }
                }
            }
            args.push("--image".to_string());
            args.push(frame_path.to_string_lossy().to_string());
            args.push("-p".to_string());
            args.push(prompt.to_string());
            args.push("-n".to_string());
            args.push("100".to_string());
            args.push("--temp".to_string());
            args.push("0.1".to_string());
        } else {
            // Generic llama-cli format
            args.push("-m".to_string());
            args.push(model_path.to_string());
            args.push("--image".to_string());
            args.push(frame_path.to_string_lossy().to_string());
            args.push("-p".to_string());
            args.push(prompt.to_string());
            args.push("-n".to_string());
            args.push("100".to_string());
            args.push("--temp".to_string());
            args.push("0.1".to_string());
        }

        let output = hidden_cmd(&cli)
            .args(&args)
            .output()
            .map_err(|e| format!("llama-cli execution failed: {e}"))?;

        let _ = std::fs::remove_file(&frame_path);

        let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if text.is_empty() || text.contains("usage:") {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!(
                "llama-cli produced no output. Check model file compatibility.\nstdout: {}\nstderr: {}",
                text.chars().take(300).collect::<String>(),
                stderr.chars().take(300).collect::<String>(),
            ));
        }

        let desc = text.clone();
        return Ok(RawCompileOutput {
            events: vec![RawEvent {
                title: format!("Local Draft: {title}"),
                event_frame_indexes: vec![0, (frame_pngs.len().max(1) - 1) as u32],
                description: desc,
                event_type: "concept".to_string(),
                speaker: None,
                confidence: 0.35,
            }],
            chunk_summary: format!(
                "[DRAFT] {title} — {:.0}s video, {} frames analyzed by local model. {}",
                duration_sec, frame_pngs.len(), text,
            ),
        });
    }

    // No frames available
    Ok(RawCompileOutput {
        events: vec![RawEvent {
            title: format!("Local Draft: {title}"),
            event_frame_indexes: vec![0, 0],
            description: format!("Local draft mode. Video: {:.0}s. No frames available for analysis.", duration_sec),
            event_type: "concept".to_string(),
            speaker: None,
            confidence: 0.3,
        }],
        chunk_summary: format!("[DRAFT] {title} — {:.0}s video, no frames analyzed.", duration_sec),
    })
}

/// Find the llama.cpp CLI executable by scanning PATH and common install dirs.
fn find_llama_cli() -> Option<std::path::PathBuf> {
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    let mut dirs: Vec<std::path::PathBuf> = std::env::split_paths(&path_var).collect();
    // Common install locations
    for candidate in ["E:\\llama.cpp", "C:\\llama.cpp", "D:\\llama.cpp"] {
        let p = std::path::PathBuf::from(candidate);
        if p.is_dir() {
            dirs.push(p);
        }
    }
    let candidates = [
        "llama-qwen2vl-cli.exe", "llama-llava-cli.exe", "llama-minicpmv-cli.exe",
        "llama-cli.exe", "llama-cli", "llama.cpp.exe", "main.exe", "main",
    ];
    for dir in &dirs {
        for name in &candidates {
            let full = dir.join(name);
            if full.is_file() {
                return Some(full);
            }
        }
    }
    None
}

// ─── Tests ─────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_compile_mode_prefer_draft() {
        let mode = resolve_compile_mode("https://api.openai.com/v1", true);
        assert_eq!(mode, CompileMode::LocalDraft);
    }

    #[test]
    fn test_generate_local_draft_errors_without_model() {
        let result = generate_local_draft("Test", 60.0, &[], None);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("draft_model_path"));
    }

    #[test]
    fn test_generate_local_draft_errors_model_not_found() {
        let result = generate_local_draft("Test", 60.0, &[], Some("/nonexistent/model.gguf"));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("not found"));
    }

    #[test]
    fn test_check_network_connectivity_invalid_url() {
        let result = check_network_connectivity("not-a-url");
        assert!(!result);
    }
}