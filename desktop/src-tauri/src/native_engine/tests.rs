#[cfg(test)]
mod tests {
    use super::super::*;
    use chrono::Utc;
    use serde_json::{json, Map, Value};
    use std::{
        fs,
        path::{Path, PathBuf},
        process::Command,
        sync::Arc,
        time::Duration,
    };
    use uuid::Uuid;

    fn temp_engine() -> (NativeEngine, PathBuf) {
        let root = std::env::temp_dir().join(format!("video-notes-native-{}", Uuid::new_v4()));
        (engine_for_root(&root), root)
    }

    fn engine_for_root(root: &Path) -> NativeEngine {
        let settings_path = root.join("config").join("settings.json");
        let data_dir = root.join("data");
        let runtime_dir = root.join("runtime");
        let manifests_dir = root.join("manifests");
        let export_dir = root.join("exports");
        NativeEngine::for_paths(
            settings_path,
            data_dir,
            runtime_dir,
            manifests_dir,
            export_dir,
        )
    }

    fn test_job(id: u64, status: &str) -> NativeJob {
        NativeJob {
            id,
            job_id: format!("stable-{id}"),
            title: Some(format!("Job {id}")),
            status: status.to_string(),
            progress: 10,
            progress_message: "测试任务".to_string(),
            stage: status.to_string(),
            input: format!("input-{id}.mp4"),
            created_at: Utc::now().to_rfc3339(),
            completed_at: None,
            error_message: None,
            output_path: None,
            transcript_path: None,
            frames_count: 0,
            can_resume: status == "paused",
            settings_snapshot: None,
            workspace_dir: None,
            attempt: 1,
            parent_run_id: None,
artifact_cleanup_policy: default_artifact_cleanup_policy(),
            note_id: None,
        }
    }

    fn insert_job(engine: &NativeEngine, job: NativeJob, active_control: bool) {
        let id = job.id;
        let mut jobs = engine.jobs.lock().unwrap();
        jobs.push(job);
        save_jobs(&engine.jobs_state_path, &jobs).unwrap();
        drop(jobs);
        let mut next = engine.next_job_id.lock().unwrap();
        *next = (*next).max(id + 1);
        drop(next);
        if active_control {
            engine
                .job_controls
                .lock()
                .unwrap()
                .insert(id, Arc::new(JobControl::new()));
        }
    }

    fn shell_command(script: &str) -> Command {
        if cfg!(target_os = "windows") {
            let mut command = hidden_command("cmd");
            command.args(["/C", script]);
            command
        } else {
            let mut command = hidden_command("sh");
            command.args(["-c", script]);
            command
        }
    }

    fn sleep_command() -> Command {
        if cfg!(target_os = "windows") {
            shell_command("ping -n 6 127.0.0.1 > nul")
        } else {
            shell_command("sleep 5")
        }
    }

    #[test]
    fn tauri_csp_allows_windows_asset_protocol_images() {
        let config: Value = serde_json::from_str(include_str!("../../tauri.conf.json")).unwrap();
        let csp = config["app"]["security"]["csp"].as_str().unwrap();
        let img_src = csp
            .split(';')
            .map(str::trim)
            .find(|directive| directive.starts_with("img-src "))
            .unwrap();

        assert!(
            img_src
                .split_whitespace()
                .any(|source| source == "http://asset.localhost"),
            "img-src must allow http://asset.localhost because Tauri convertFileSrc returns it on Windows"
        );
    }

    fn provider_settings() -> Map<String, Value> {
        serde_json::from_value(json!({
            "active_provider": "saved",
            "providers": [{
                "name": "saved",
                "type": "openai_compat",
                "base_url": "https://saved.example/v1",
                "model": "saved-model",
                "vision_model": "saved-vision",
                "api_key": "sk-saved-secret"
            }]
        }))
        .unwrap()
    }

    #[test]
    fn saved_provider_ignores_endpoint_override_but_keeps_secret() {
        let settings = provider_settings();
        let profile = provider_profile_for_request(
            &settings,
            &json!({
                "name": "saved",
                "base_url": "https://attacker.example/v1",
                "api_key": "sk-attacker",
            }),
        )
        .unwrap();
        assert_eq!(profile.base_url, "https://saved.example/v1");
        assert_eq!(profile.api_key, "sk-saved-secret");
        assert_eq!(profile.model, "saved-model");
    }

    #[test]
    fn adhoc_provider_uses_explicit_endpoint_and_secret() {
        let settings = provider_settings();
        let profile = provider_profile_for_request(
            &settings,
            &json!({
                "base_url": "https://adhoc.example/v1",
                "api_key": "sk-adhoc",
                "model": "adhoc-model",
            }),
        )
        .unwrap();
        assert_eq!(profile.base_url, "https://adhoc.example/v1");
        assert_eq!(profile.api_key, "sk-adhoc");
        assert_eq!(profile.model, "adhoc-model");
    }

    #[test]
    fn saved_provider_allows_model_override_only() {
        let settings = provider_settings();
        let profile = provider_profile_for_request(
            &settings,
            &json!({
                "name": "saved",
                "base_url": "https://attacker.example/v1",
                "model": "override-model",
                "vision_model": "override-vision",
            }),
        )
        .unwrap();
        assert_eq!(profile.base_url, "https://saved.example/v1");
        assert_eq!(profile.api_key, "sk-saved-secret");
        assert_eq!(profile.model, "override-model");
        assert_eq!(profile.vision_model, "override-vision");
    }

    #[test]
    fn job_saved_provider_ignores_endpoint_override() {
        let settings = provider_settings();
        let profile = provider_profile_for_job(
            &settings,
            &json!({
                "provider_name": "saved",
                "base_url": "https://attacker.example/v1",
                "model": "job-model",
            }),
        )
        .unwrap();
        assert_eq!(profile.base_url, "https://saved.example/v1");
        assert_eq!(profile.api_key, "sk-saved-secret");
        assert_eq!(profile.model, "job-model");
    }

    #[test]
    fn retry_snapshot_attacker_base_url_does_not_override_saved_endpoint() {
        let settings = provider_settings();
        let snapshot = json!({
            "task_params": {
                "input": "old.mp4",
                "provider_name": "saved",
                "base_url": "https://attacker.example/v1",
                "model": "snapshot-model"
            }
        });
        let params = Value::Object(sanitized_retry_task_params(
            Some(&snapshot),
            "fallback.mp4",
            None,
        ));
        let profile = provider_profile_for_job(&settings, &params).unwrap();
        assert_eq!(profile.base_url, "https://saved.example/v1");
        assert_eq!(profile.api_key, "sk-saved-secret");
        assert_eq!(profile.model, "snapshot-model");
    }

    #[test]
    fn saved_provider_ignores_type_override() {
        let settings = provider_settings();
        let profile = provider_profile_for_request(
            &settings,
            &json!({
                "name": "saved",
                "type": "llama_cpp",
                "provider": "llama_cpp",
                "model": "override-model"
            }),
        )
        .unwrap();
        assert_eq!(profile.base_url, "https://saved.example/v1");
        assert_eq!(profile.api_key, "sk-saved-secret");
        assert_eq!(profile.model, "override-model");
    }

    #[test]
    fn settings_update_round_trips_defaults() {
        let (engine, root) = temp_engine();
        let updated = engine
            .call(
                "settings.update",
                json!({
                    "patches": {
                        "whisper_model": "base",
                        "transcription_backend": "whisper_cpp",
                        "ocr_backend": "paddleocr_http",
                        "ocr_http_endpoint": "http://127.0.0.1:8868/ocr",
                        "ocr_http_api_key": "local-token",
                        "ocr_model": "PP-OCRv6",
                        "template": "summary"
                    }
                }),
            )
            .expect("method handled")
            .expect("update succeeds");
        assert_eq!(updated, json!(true));

        let settings = engine
            .call("settings.get", json!({}))
            .expect("method handled")
            .expect("get succeeds");
        assert_eq!(settings["whisper_model"], "base");
        assert_eq!(settings["transcription_backend"], "whisper_cpp");
        assert_eq!(settings["ocr_backend"], "paddleocr_http");
        assert_eq!(settings["ocr_http_endpoint"], "http://127.0.0.1:8868/ocr");
        assert_eq!(settings["ocr_http_api_key"], "local-token");
        assert_eq!(settings["ocr_model"], "PP-OCRv6");
        assert_eq!(settings["template"], "summary");

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn process_jobs_persist_and_reload() {
        let (engine, root) = temp_engine();
        let input = root.join("missing.mp4");

        let started = engine
            .call(
                "process.start",
                json!({ "input": input.to_string_lossy(), "title": "Persisted Job" }),
            )
            .expect("method handled")
            .expect("start succeeds");
        assert_eq!(started["job_id"], 1);
        assert!(engine.jobs_state_path.is_file());

        let reloaded = engine_for_root(&root);
        let jobs = reloaded
            .call("process.list", json!({ "limit": 10 }))
            .expect("method handled")
            .expect("list succeeds");
        let first = jobs.as_array().unwrap().first().unwrap();
        assert_eq!(first["id"], 1);
        assert_eq!(first["title"], "Persisted Job");

        let second = reloaded
            .call(
                "process.start",
                json!({ "input": input.to_string_lossy(), "title": "Second Job" }),
            )
            .expect("method handled")
            .expect("start succeeds");
        assert_eq!(second["job_id"], 2);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn loading_running_job_marks_interrupted() {
        let (engine, root) = temp_engine();
        let job = NativeJob {
            id: 7,
            job_id: "stable-running".to_string(),
            title: Some("Running Job".to_string()),
            status: "running".to_string(),
            progress: 35,
            progress_message: "处理中".to_string(),
            stage: "transcribing".to_string(),
            input: "input.mp4".to_string(),
            created_at: Utc::now().to_rfc3339(),
            completed_at: None,
            error_message: None,
            output_path: None,
            transcript_path: None,
            frames_count: 0,
            can_resume: false,
            settings_snapshot: None,
            workspace_dir: None,
            attempt: 1,
            parent_run_id: None,
            artifact_cleanup_policy: default_artifact_cleanup_policy(),
            note_id: None,
        };
        save_jobs(&engine.jobs_state_path, &[job]).unwrap();

        let reloaded = engine_for_root(&root);
        let jobs = reloaded
            .call("process.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        let first = jobs.as_array().unwrap().first().unwrap();
        assert_eq!(first["id"], 7);
        assert_eq!(first["status"], "interrupted");
        assert_eq!(first["stage"], "interrupted");
        assert_eq!(first["progress_message"], "应用已重启，任务已中断");
        assert!(first["completed_at"].as_str().unwrap_or_default().len() > 0);
        assert_eq!(*reloaded.next_job_id.lock().unwrap(), 8);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn loading_active_jobs_marks_interrupted() {
        let (engine, root) = temp_engine();
        let jobs = ["pending", "running", "pausing", "cancelling", "paused"]
            .iter()
            .enumerate()
            .map(|(index, status)| test_job(index as u64 + 1, status))
            .collect::<Vec<_>>();
        save_jobs(&engine.jobs_state_path, &jobs).unwrap();

        let reloaded = engine_for_root(&root);
        let list = reloaded
            .call("process.list", json!({ "limit": 10 }))
            .expect("method handled")
            .expect("list succeeds");
        let jobs = list.as_array().unwrap();

        // pending, running, pausing, cancelling -> interrupted
        for job in jobs.iter().take(4) {
            assert_eq!(job["status"], "interrupted", "job {} should be interrupted", job["id"]);
            assert_eq!(job["can_resume"], false);
            assert!(job["completed_at"].as_str().unwrap_or_default().len() > 0);
        }
        // paused -> stays paused
        let paused = &jobs[4];
        assert_eq!(paused["status"], "paused", "paused job should stay paused after restart");
        assert_eq!(paused["can_resume"], true);
        assert!(paused["completed_at"].as_str().unwrap_or_default().is_empty(), "paused job should not have completed_at");

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn task_action_invalid_transitions_are_rejected() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "completed"), false);
        let pause = engine
            .call("process.pause", json!({ "job_id": 1 }))
            .expect("method handled");
        assert!(pause.is_err());

        insert_job(&engine, test_job(2, "running"), true);
        let retry = engine
            .call("process.retry", json!({ "job_id": 2 }))
            .expect("method handled");
        assert!(retry.is_err());

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn cancel_active_job_enters_cancelling() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "running"), true);

        let result = engine
            .call("process.cancel", json!({ "job_id": 1 }))
            .expect("method handled")
            .expect("cancel succeeds");
        assert_eq!(result, json!(true));
        let job = engine.jobs.lock().unwrap().first().unwrap().clone();
        assert_eq!(job.status, "cancelling");
        assert_eq!(job.can_resume, false);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn pause_active_job_enters_pausing() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "pending"), true);

        let result = engine
            .call("process.pause", json!({ "job_id": 1 }))
            .expect("method handled")
            .expect("pause succeeds");
        assert_eq!(result, json!(true));
        let job = engine.jobs.lock().unwrap().first().unwrap().clone();
        assert_eq!(job.status, "pausing");
        assert_eq!(job.can_resume, false);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn retry_terminal_job_creates_new_job() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "failed"), false);

        let result = engine
            .call("process.retry", json!({ "job_id": 1 }))
            .expect("method handled")
            .expect("retry succeeds");
        assert_eq!(result["job_id"], json!(2));
        let jobs = engine.jobs.lock().unwrap();
        assert_eq!(jobs.len(), 2);
        assert_eq!(jobs[0].status, "failed");
        assert_eq!(jobs[1].input, "input-1.mp4");

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn delete_active_job_is_rejected() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "paused"), true);

        let result = engine
            .call("process.delete", json!({ "job_id": 1 }))
            .expect("method handled");
        assert!(result.is_err());
        assert_eq!(engine.jobs.lock().unwrap().len(), 1);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn controlled_command_cancel_before_spawn() {
        let control = Arc::new(JobControl::new());
        control.cancel_requested.store(true, Ordering::SeqCst);

        let result =
            run_controlled_command_piped(shell_command("echo should-not-run"), &control, "test");

        assert!(result.is_err());
        assert!(is_cancellation_error(&result.unwrap_err()));
        assert!(control.current_child.lock().unwrap().is_none());
    }

    #[test]
    fn controlled_command_captures_stdout_and_stderr() {
        let control = Arc::new(JobControl::new());

        let stdout =
            run_controlled_command_piped(shell_command("echo stdout-ok"), &control, "stdout")
                .expect("stdout command succeeds");
        assert!(stdout.status.success());
        assert!(String::from_utf8_lossy(&stdout.stdout).contains("stdout-ok"));

        let stderr =
            run_controlled_command_piped(shell_command("echo stderr-ok 1>&2"), &control, "stderr")
                .expect("stderr command succeeds");
        assert!(stderr.status.success());
        assert!(String::from_utf8_lossy(&stderr.stderr).contains("stderr-ok"));
    }

    #[test]
    fn controlled_command_supports_stdout_null_stderr_piped() {
        let control = Arc::new(JobControl::new());

        let output = run_controlled_command(
            shell_command("echo hidden-stdout && echo visible-stderr 1>&2"),
            &control,
            "mixed",
            ControlledOutputMode::Null,
            ControlledOutputMode::Piped,
        )
        .expect("mixed command succeeds");

        assert!(output.status.success());
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8_lossy(&output.stderr).contains("visible-stderr"));
    }

    #[test]
    fn controlled_command_cancelled_child_clears_current_child() {
        let control = Arc::new(JobControl::new());
        let run_control = control.clone();
        let handle = std::thread::spawn(move || {
            run_controlled_command_piped(sleep_command(), &run_control, "sleep")
        });

        for _ in 0..50 {
            if control.current_child.lock().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(control.current_child.lock().unwrap().is_some());
        control.cancel_requested.store(true, Ordering::SeqCst);

        let result = handle.join().expect("controlled command thread joins");
        assert!(result.is_err());
        assert!(is_cancellation_error(&result.unwrap_err()));
        assert!(control.current_child.lock().unwrap().is_none());
    }

    #[test]
    fn failed_update_after_cancel_becomes_cancelled() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "cancelling"), true);

        update_job(
            &engine.jobs,
            &engine.jobs_state_path,
            &engine.app_handle,
            &engine.data_dir,
            1,
            "failed",
            "failed",
            100,
            "long stage failed",
            Some("tool error".to_string()),
            None,
            None,
        );

        let job = engine.jobs.lock().unwrap().first().unwrap().clone();
        assert_eq!(job.status, "cancelled");
        assert_eq!(job.stage, "cancelled");
        assert!(job.error_message.is_none());
        assert!(job.completed_at.is_some());

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn invalid_actions_do_not_mutate_control_flags() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "completed"), true);
        let control = engine.job_control(1).unwrap();

        let pause = engine
            .call("process.pause", json!({ "job_id": 1 }))
            .expect("method handled");
        assert!(pause.is_err());
        assert!(!control.pause_requested.load(Ordering::SeqCst));
        assert!(!control.cancel_requested.load(Ordering::SeqCst));

        control.pause_requested.store(true, Ordering::SeqCst);
        let resume = engine
            .call("process.resume", json!({ "job_id": 1 }))
            .expect("method handled");
        assert!(resume.is_err());
        assert!(control.pause_requested.load(Ordering::SeqCst));

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn frame_updates_ignore_cancelled_jobs() {
        let (engine, root) = temp_engine();
        insert_job(&engine, test_job(1, "cancelled"), false);

        update_job_frames(
            &engine.jobs,
            &engine.jobs_state_path,
            &engine.app_handle,
            1,
            9,
        );

        let job = engine.jobs.lock().unwrap().first().unwrap().clone();
        assert_eq!(job.frames_count, 0);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn components_list_reports_native_manifest_status() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(&engine.manifests_dir).unwrap();
        fs::create_dir_all(engine.runtime_dir.join("components").join("download-tools")).unwrap();
        fs::write(
            engine
                .runtime_dir
                .join("components")
                .join("download-tools")
                .join(if cfg!(target_os = "windows") {
                    "yt-dlp.exe"
                } else {
                    "yt-dlp"
                }),
            "",
        )
        .unwrap();
        write_json_atomic(
            &engine.manifests_dir.join("download-tools.json"),
            &json!({
                "component": "download-tools",
                "version": "1.5.7",
                "description": "yt-dlp standalone executable",
                "size_mb": 20,
                "provides": ["download"],
                "files": [if cfg!(target_os = "windows") { "yt-dlp.exe" } else { "yt-dlp" }]
            }),
        )
        .unwrap();

        let components = engine
            .call("components.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        let first = components.as_array().unwrap().first().unwrap();
        assert_eq!(first["component"], "download-tools");
        assert_eq!(first["installed"], true);
        assert_eq!(first["status"], "ok");
        assert_eq!(first["missing_files"].as_array().unwrap().len(), 0);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn components_list_uses_marker_for_update_status() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(&engine.manifests_dir).unwrap();
        let component_dir = engine.runtime_dir.join("components").join("ffmpeg-tools");
        fs::create_dir_all(&component_dir).unwrap();
        fs::write(
            component_dir.join(if cfg!(target_os = "windows") {
                "ffmpeg.exe"
            } else {
                "ffmpeg"
            }),
            "",
        )
        .unwrap();
        fs::write(
            component_dir.join(if cfg!(target_os = "windows") {
                "ffprobe.exe"
            } else {
                "ffprobe"
            }),
            "",
        )
        .unwrap();
        write_json_atomic(
            &engine.manifests_dir.join("ffmpeg-tools.json"),
            &json!({
                "component": "ffmpeg-tools",
                "version": "2026.07.08",
                "description": "FFmpeg tools",
                "download_url": "https://example.invalid/ffmpeg.zip",
                "files": [
                    if cfg!(target_os = "windows") { "ffmpeg.exe" } else { "ffmpeg" },
                    if cfg!(target_os = "windows") { "ffprobe.exe" } else { "ffprobe" }
                ]
            }),
        )
        .unwrap();

        let components = engine
            .call("components.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        let ffmpeg = components
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["component"] == "ffmpeg-tools")
            .unwrap();
        assert_eq!(ffmpeg["update_available"], false);

        write_json_atomic(
            &component_marker_path(&component_dir),
            &json!({
                "component": "ffmpeg-tools",
                "manifest_version": "2026.07.01",
                "installed_at": "2026-07-01T00:00:00Z"
            }),
        )
        .unwrap();

        let components = engine
            .call("components.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        let ffmpeg = components
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["component"] == "ffmpeg-tools")
            .unwrap();
        assert_eq!(ffmpeg["update_available"], true);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn components_list_uses_bundled_manifests_without_runtime_manifest_dir() {
        let (engine, root) = temp_engine();

        let components = engine
            .call("components.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        let names = components
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|item| item.get("component").and_then(Value::as_str))
            .collect::<Vec<_>>();

        assert!(names.contains(&"download-tools"));
        assert!(names.contains(&"ffmpeg-tools"));
        assert!(names.contains(&"whisper-cpp-tools"));
        assert!(names.contains(&"whisper-cpp-cuda-tools"));
        assert!(names.contains(&"tesseract-ocr-tools"));

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn note_title_prefers_metadata_over_generic_heading() {
        let root = std::env::temp_dir().join(format!("video-notes-note-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let note = root.join("summary.md");
        fs::write(
            &note,
            "---\ntitle: Real Lesson Title\ndate: 2026-07-06\n---\n\n# 概要\n\nBody",
        )
        .unwrap();

        assert_eq!(note_title(&note), "Real Lesson Title");

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn note_title_falls_back_to_filename_for_generic_heading() {
        let root = std::env::temp_dir().join(format!("video-notes-note-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let note = root.join("lesson-name.md");
        fs::write(&note, "# 概要\n\nBody").unwrap();

        assert_eq!(note_title(&note), "lesson-name");

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn ocr_http_json_parser_collects_common_text_fields() {
        let value = json!({
            "data": {
                "rec_texts": ["第一行", "第二行"],
                "items": [{ "text": "第三行" }]
            }
        });

        let text = extract_text_from_ocr_json(&value);
        assert_eq!(text.len(), 3);
        assert!(text.iter().any(|item| item == "第一行"));
        assert!(text.iter().any(|item| item == "第二行"));
        assert!(text.iter().any(|item| item == "第三行"));
    }

    #[test]
    fn ocr_test_reports_missing_http_endpoint() {
        let (engine, root) = temp_engine();

        let result = engine
            .call(
                "settings.ocr.test",
                json!({ "ocr_backend": "paddleocr_http" }),
            )
            .expect("method handled")
            .expect("test succeeds");

        assert_eq!(result["success"], false);
        assert!(result["message"]
            .as_str()
            .unwrap_or_default()
            .contains("Endpoint"));

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn paddleocr_endpoint_is_normalised_to_jobs_url() {
        assert_eq!(
            normalise_paddleocr_jobs_endpoint("https://paddleocr.aistudio-app.com"),
            "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
        );
        assert_eq!(
            normalise_paddleocr_jobs_endpoint(
                "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/abc"
            ),
            "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
        );
    }

    #[test]
    fn bearer_token_strips_existing_scheme() {
        assert_eq!(bearer_token("bearer abc123"), "abc123");
        assert_eq!(bearer_token("Bearer abc123"), "abc123");
        assert_eq!(bearer_token("abc123"), "abc123");
    }

    #[test]
    fn process_start_creates_native_markdown_artifact() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(root.join("input")).unwrap();
        let input = root.join("input").join("lesson.mp4");
        fs::write(&input, "fake video bytes").unwrap();

        let started = engine
            .call(
                "process.start",
                json!({ "input": input.to_string_lossy(), "title": "Lesson One" }),
            )
            .expect("method handled")
            .expect("start succeeds");
        assert_eq!(started["job_id"], 1);

        let mut completed = None;
        for _ in 0..50 {
            let jobs = engine
                .call("process.list", json!({ "limit": 10 }))
                .expect("method handled")
                .expect("list succeeds");
            let first = jobs.as_array().unwrap().first().cloned();
            if first
                .as_ref()
                .and_then(|job| job.get("status"))
                .and_then(Value::as_str)
                == Some("completed")
            {
                completed = first;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }

        let job = completed.expect("job completed");
        let output_path = PathBuf::from(job["output_path"].as_str().unwrap());
        let transcript_path = PathBuf::from(job["transcript_path"].as_str().unwrap());
        assert!(output_path.is_file());
        assert_eq!(output_path.parent(), Some(root.join("exports").as_path()));
        assert!(transcript_path.is_file());
        assert_eq!(job["progress"], 100);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn process_start_accepts_output_dir_override() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(root.join("input")).unwrap();
        let input = root.join("input").join("lesson.mp4");
        fs::write(&input, "fake video bytes").unwrap();
        let output_dir = root.join("exports").join("collections").join("Course-1");

        engine
            .call(
                "process.start",
                json!({
                    "input": input.to_string_lossy(),
                    "title": "Lesson One",
                    "output_dir": output_dir.to_string_lossy(),
                }),
            )
            .expect("method handled")
            .expect("start succeeds");

        let mut completed = None;
        for _ in 0..50 {
            let jobs = engine
                .call("process.list", json!({ "limit": 10 }))
                .expect("method handled")
                .expect("list succeeds");
            let first = jobs.as_array().unwrap().first().cloned();
            if first
                .as_ref()
                .and_then(|job| job.get("status"))
                .and_then(Value::as_str)
                == Some("completed")
            {
                completed = first;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }

        let job = completed.expect("job completed");
        let output_path = PathBuf::from(job["output_path"].as_str().unwrap());
        assert!(output_path.is_file());
        assert_eq!(output_path.parent(), Some(output_dir.as_path()));

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn notes_rpc_scans_updates_and_deletes_markdown() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(root.join("exports")).unwrap();
        let note_path = root.join("exports").join("lesson.md");
        let asset_dir = root.join("exports").join("assets").join("lesson");
        fs::create_dir_all(&asset_dir).unwrap();
        fs::write(&note_path, "# Lesson Title\n\nOriginal content").unwrap();
        fs::write(asset_dir.join("frame-001.png"), "mock image").unwrap();

        let notes = engine
            .call("notes.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        let note = notes
            .as_array()
            .unwrap()
            .first()
            .cloned()
            .expect("note exists");
        assert_eq!(note["title"], "Lesson Title");

        let id = note["id"].as_u64().unwrap();
        let detail = engine
            .call("notes.get", json!({ "note_id": id }))
            .expect("method handled")
            .expect("get succeeds");
        assert!(detail["content"]
            .as_str()
            .unwrap()
            .contains("Original content"));

        let searched = engine
            .call("notes.search", json!({ "query": "lesson" }))
            .expect("method handled")
            .expect("search succeeds");
        assert_eq!(searched.as_array().unwrap().len(), 1);

        engine
            .call("notes.update", json!({ "id": id, "content": "# Updated" }))
            .expect("method handled")
            .expect("update succeeds");
        assert_eq!(fs::read_to_string(&note_path).unwrap(), "# Updated");

        engine
            .call("notes.delete", json!({ "id": id }))
            .expect("method handled")
            .expect("delete succeeds");
        assert!(!note_path.exists());
        assert!(!asset_dir.exists());

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn collection_rpc_persists_items_and_exports() {
        let (engine, root) = temp_engine();
        let created = engine
            .call(
                "collection.create",
                json!({ "name": "Course", "items": ["a.mp4", "b.mp4"] }),
            )
            .expect("method handled")
            .expect("create succeeds");
        let id = created["id"].as_u64().unwrap();

        let list = engine
            .call("collection.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        assert_eq!(list.as_array().unwrap().len(), 1);
        assert_eq!(list[0]["item_count"], 2);

        engine
            .call(
                "collection.add_items",
                json!({ "id": id, "items": ["c.mp4"] }),
            )
            .expect("method handled")
            .expect("add succeeds");
        let detail = engine
            .call("collection.get", json!({ "id": id }))
            .expect("method handled")
            .expect("get succeeds");
        assert_eq!(detail["item_count"], 3);

        engine
            .call(
                "collection.remove_items",
                json!({ "id": id, "item_ids": [2] }),
            )
            .expect("method handled")
            .expect("remove succeeds");
        let detail = engine
            .call("collection.get", json!({ "id": id }))
            .expect("method handled")
            .expect("get succeeds");
        assert_eq!(detail["item_count"], 2);

        let exported = engine
            .call("collection.export", json!({ "id": id }))
            .expect("method handled")
            .expect("export succeeds");
        assert!(PathBuf::from(exported["path"].as_str().unwrap()).is_file());

        engine
            .call("collection.delete", json!({ "id": id }))
            .expect("method handled")
            .expect("delete succeeds");
        let list = engine
            .call("collection.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        assert_eq!(list.as_array().unwrap().len(), 0);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn collection_batch_process_queues_without_starting_all_jobs() {
        let (engine, root) = temp_engine();
        let created = engine
            .call(
                "collection.create",
                json!({ "name": "Course", "items": ["a.mp4", "b.mp4", "c.mp4"] }),
            )
            .expect("method handled")
            .expect("create succeeds");
        let id = created["id"].as_u64().unwrap();

        let queued = engine
            .call("collection.batch_process", json!({ "id": id }))
            .expect("method handled")
            .expect("batch succeeds");
        assert_eq!(queued["count"], 3);
        assert_eq!(queued["queued_count"], 3);
        assert_eq!(queued["max_concurrency"], 1);
        assert_eq!(queued["run_ids"].as_array().unwrap().len(), 0);
        assert_eq!(
            PathBuf::from(queued["output_dir"].as_str().unwrap()),
            root.join("exports")
                .join("collections")
                .join(format!("Course-{id}"))
        );

        std::thread::sleep(Duration::from_millis(100));
        let jobs = engine.jobs.lock().unwrap();
        assert!(
            jobs.len() < 3,
            "batch runner should not synchronously start all jobs"
        );
        drop(jobs);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn collection_item_progress_updates_from_native_job() {
        let (engine, root) = temp_engine();
        let created = engine
            .call(
                "collection.create",
                json!({ "name": "Course", "items": ["a.mp4"] }),
            )
            .expect("method handled")
            .expect("create succeeds");
        let collection_id = created["id"].as_u64().unwrap();
        let mut job = test_job(9, "completed");
        job.progress = 100;
        job.output_path = Some(
            root.join("exports")
                .join("a.md")
                .to_string_lossy()
                .to_string(),
        );

        engine
            .update_collection_item_from_job(collection_id, 1, &job)
            .expect("item update succeeds");

        let detail = engine
            .call("collection.get", json!({ "id": collection_id }))
            .expect("method handled")
            .expect("get succeeds");
        let item = &detail["items"][0];
        assert_eq!(item["run_id"], 9);
        assert_eq!(item["job_id"], job.job_id);
        assert_eq!(item["status"], "completed");
        assert_eq!(item["progress"], 100);
        assert_eq!(item["output_path"], job.output_path.unwrap());

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn collection_batch_process_clamps_max_concurrency() {
        let (engine, root) = temp_engine();
        let created = engine
            .call(
                "collection.create",
                json!({ "name": "Course", "items": ["a.mp4"] }),
            )
            .expect("method handled")
            .expect("create succeeds");
        let id = created["id"].as_u64().unwrap();

        let queued = engine
            .call(
                "collection.batch_process",
                json!({ "id": id, "opts": { "max_concurrency": 99 } }),
            )
            .expect("method handled")
            .expect("batch succeeds");
        assert_eq!(queued["max_concurrency"], 2);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn parse_whisper_segments_from_valid_json() {
        let json = r#"{
            "segments": [
                {"start": 0.0, "end": 5.2, "text": "  hello world"},
                {"start": 5.2, "end": 12.0, "text": "  this is a test"}
            ]
        }"#;
        let segments = parse_whisper_segments(json);
        assert_eq!(segments.len(), 2);
        assert!((segments[0].start_sec - 0.0).abs() < 0.01);
        assert!((segments[0].end_sec - 5.2).abs() < 0.01);
        assert_eq!(segments[0].text, "hello world");
        assert_eq!(segments[1].text, "this is a test");
        assert!(segments[0].ocr_text.is_none());
        assert!(segments[0].vision_summary.is_none());
        assert!(segments[0].frame_paths.is_empty());
    }

    #[test]
    fn parse_whisper_segments_from_transcription_format() {
        let json = r#"{
            "transcription": [
                {"offsets": {"from": 260, "to": 4060}, "text": "  first segment"},
                {"offsets": {"from": 4860, "to": 11080}, "text": "  second segment here"}
            ]
        }"#;
        let segments = parse_whisper_segments(json);
        assert_eq!(segments.len(), 2);
        assert!((segments[0].start_sec - 0.26).abs() < 0.01);
        assert!((segments[0].end_sec - 4.06).abs() < 0.01);
        assert_eq!(segments[0].text, "first segment");
        assert_eq!(segments[1].text, "second segment here");
        assert!((segments[1].start_sec - 4.86).abs() < 0.01);
    }

    #[test]
    fn parse_whisper_segments_handles_empty_and_missing() {
        assert!(parse_whisper_segments("").is_empty());
        assert!(parse_whisper_segments("{}").is_empty());
        assert!(parse_whisper_segments(r#"{"segments":[]}"#).is_empty());
        assert!(parse_whisper_segments(r#"{"transcription":[]}"#).is_empty());
        assert!(parse_whisper_segments(r#"{"not_segments":[]}"#).is_empty());
    }

    #[test]
    fn parse_whisper_segments_filters_empty_text() {
        let json = r#"{
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "  "},
                {"start": 1.0, "end": 2.0, "text": "valid"}
            ]
        }"#;
        let segments = parse_whisper_segments(json);
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].text, "valid");
    }

    #[test]
    fn frame_index_from_path_parses_correctly() {
        let cases = [
            ("frame-001.png", Some(1)),
            ("frame-999.png", Some(999)),
            ("frame-0.png", Some(0)),
            ("not-a-frame.png", None),
            ("frame-abc.png", None),
        ];
        for (name, expected) in &cases {
            let path = std::path::Path::new(name);
            assert_eq!(frame_index_from_path(path), *expected, "failed for {name}");
        }
    }

    #[test]
    fn merge_frames_into_timeline_assigns_to_segments() {
        let mut segments = vec![
            TimelineSegment {
                start_sec: 0.0,
                end_sec: 60.0,
                text: "intro".to_string(),
                ocr_text: None,
                vision_summary: None,
                frame_paths: Vec::new(),
            },
            TimelineSegment {
                start_sec: 60.0,
                end_sec: 120.0,
                text: "main content".to_string(),
                ocr_text: None,
                vision_summary: None,
                frame_paths: Vec::new(),
            },
        ];
        let frame_dir = std::env::temp_dir().join("timeline-test-frames");
        let _ = fs::create_dir_all(&frame_dir);
        let frame1 = frame_dir.join("frame-001.png");
        let frame2 = frame_dir.join("frame-002.png");
        fs::write(&frame1, b"dummy").ok();
        fs::write(&frame2, b"dummy").ok();

        let mut frame_ocrs = std::collections::HashMap::new();
        frame_ocrs.insert("frame-001.png".to_string(), "slide 1".to_string());
        frame_ocrs.insert("frame-002.png".to_string(), "slide 2".to_string());

        let frame_paths = vec![frame1, frame2];
        merge_frames_into_timeline(&mut segments, &frame_ocrs, &frame_paths, &[30.0, 90.0]);

        assert_eq!(segments[0].frame_paths.len(), 1);
        assert_eq!(segments[0].ocr_text.as_deref(), Some("slide 1"));
        assert_eq!(segments[1].frame_paths.len(), 1);
        assert_eq!(segments[1].ocr_text.as_deref(), Some("slide 2"));
        let _ = fs::remove_dir_all(&frame_dir);
    }
}
