use super::*;
use chrono::Utc;
use serde_json::{json, Map, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, Instant},
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
        can_resume: status == "paused",
        settings_snapshot: None,
        workspace_dir: None,
        attempt: 1,
        parent_run_id: None,
        artifact_cleanup_policy: default_artifact_cleanup_policy(),
        note_id: None,
        collection_id: None,
        collection_item_id: None,
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

fn install_test_ffmpeg_component(engine: &NativeEngine) {
    let tools = engine.runtime_dir.join("components").join("ffmpeg-tools");
    fs::create_dir_all(&tools).unwrap();
    fs::write(tools.join(executable_name("ffmpeg")), b"stub").unwrap();
    fs::write(tools.join(executable_name("ffprobe")), b"stub").unwrap();
    write_component_marker(
        &json!({ "component": "ffmpeg-tools", "version": "test" }),
        &tools,
    )
    .unwrap();
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
    assert_eq!(
        config["app"]["security"]["assetProtocol"]["scope"],
        json!([])
    );
    assert_eq!(config["app"]["windows"][0]["devtools"], false);
}

#[test]
fn note_title_search_highlighting_never_uses_raw_html() {
    let source = include_str!("../../../src/pages/Notes.svelte");
    assert!(!source.contains("{@html highlightText"));
    assert!(source.contains("<mark>{segment.text}</mark>"));
}

#[test]
fn component_names_reject_traversal_and_non_identifiers() {
    assert_eq!(
        sanitize_component_name("ffmpeg-tools").unwrap(),
        "ffmpeg-tools"
    );
    for invalid in ["../outside", "..\\outside", ".hidden", "C:", "工具", "a/b"] {
        assert!(
            sanitize_component_name(invalid).is_err(),
            "accepted {invalid}"
        );
    }
}

#[test]
fn component_remove_cannot_escape_runtime_root() {
    let (engine, root) = temp_engine();
    let outside = root.join("outside");
    fs::create_dir_all(&outside).unwrap();
    fs::write(outside.join("keep.txt"), "keep").unwrap();

    let result = engine
        .call("components.remove", json!({ "component": "../../outside" }))
        .expect("method handled");
    assert!(result.is_err());
    assert!(outside.join("keep.txt").is_file());

    let component = engine.runtime_dir.join("components").join("safe-tool");
    fs::create_dir_all(&component).unwrap();
    fs::write(component.join("tool.exe"), "payload").unwrap();
    engine
        .call("components.remove", json!({ "component": "safe-tool" }))
        .expect("method handled")
        .expect("valid component removed");
    assert!(!component.exists());
    let _ = fs::remove_dir_all(root);
}

#[test]
fn provider_secret_helpers_strip_plaintext_and_preview_unicode_safely() {
    let mut settings = json!({
        "providers": [{
            "name": "测试 Provider",
            "api_key": "密钥abcdef1234"
        }]
    })
    .as_object()
    .unwrap()
    .clone();
    strip_plaintext_provider_secrets(&mut settings);
    assert!(settings["providers"][0].get("api_key").is_none());
    assert_eq!(api_key_preview("密钥abcdef1234"), "密钥ab…1234");
}

#[test]
fn mpv_playback_request_uses_original_source_and_timestamp() {
    let (engine, root) = temp_engine();
    fs::create_dir_all(&root).unwrap();
    let source = root.join("source video -- sample.mp4");
    fs::write(&source, b"local-video").unwrap();
    let mut job = test_job(7, "completed");
    job.input = source.to_string_lossy().to_string();
    job.note_id = Some(42);
    insert_job(&engine, job, false);

    let (resolved, start_seconds) = engine
        .resolve_note_playback_request(&json!({
            "note_id": 42,
            "start_seconds": 123.456
        }))
        .unwrap();

    assert_eq!(resolved, source);
    assert_eq!(start_seconds, 123.456);
    assert!(!engine.data_dir.join(".playback_cache").exists());
    let _ = fs::remove_dir_all(root);
}

#[test]
fn mpv_playback_command_preserves_path_and_precise_start() {
    let mpv = Path::new(r"C:\runtime\mpv.exe");
    let source = Path::new(r"D:\课程 视频\--lesson.mp4");
    let ipc = Path::new(r"\\.\pipe\video-notes-ai-mpv-test");
    let command = mpv_playback_command(mpv, source, 123.4567, ipc);
    let args = command
        .get_args()
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();

    assert_eq!(command.get_program(), mpv.as_os_str());
    assert!(args.iter().any(|arg| arg == "--start=123.457"));
    assert!(args
        .iter()
        .any(|arg| arg == "--input-ipc-server=\\\\.\\pipe\\video-notes-ai-mpv-test"));
    assert_eq!(args[args.len() - 2], "--");
    assert_eq!(args.last().unwrap(), &source.to_string_lossy());
    assert!(args.iter().all(|arg| !arg.contains("ffmpeg")));
}

#[test]
fn mpv_seek_commands_are_absolute_exact_and_resume_playback() {
    let commands = mpv_seek_commands(123.4567);

    assert_eq!(
        commands[0],
        json!({ "command": ["seek", 123.4567, "absolute+exact"] })
    );
    assert_eq!(commands[1], json!({ "command": ["set", "pause", "no"] }));
}

#[test]
#[ignore = "requires VN_TEST_MPV_PATH and VN_TEST_MEDIA_PATH"]
fn mpv_playback_route_launches_real_player() {
    let installed_mpv =
        PathBuf::from(std::env::var("VN_TEST_MPV_PATH").expect("VN_TEST_MPV_PATH is required"));
    let media =
        PathBuf::from(std::env::var("VN_TEST_MEDIA_PATH").expect("VN_TEST_MEDIA_PATH is required"));
    assert!(installed_mpv.is_file());
    assert!(media.is_file());

    let (engine, root) = temp_engine();
    let component = engine.runtime_dir.join("components").join("mpv-tools");
    fs::create_dir_all(&component).unwrap();
    fs::copy(&installed_mpv, component.join(executable_name("mpv"))).unwrap();
    if let Some(parent) = installed_mpv.parent() {
        let vulkan = parent.join("vulkan-1.dll");
        if vulkan.is_file() {
            fs::copy(vulkan, component.join("vulkan-1.dll")).unwrap();
        }
    }

    let mut job = test_job(8, "completed");
    job.input = media.to_string_lossy().to_string();
    job.note_id = Some(84);
    insert_job(&engine, job, false);

    let result = engine
        .call(
            "notes.video_playback",
            json!({ "note_id": 84, "start_seconds": 19.0 }),
        )
        .expect("method handled")
        .expect("mpv launch succeeds");
    assert_eq!(result["player"], "mpv");
    assert_eq!(result["start_seconds"], 19.0);
    assert_eq!(result["path"], media.to_string_lossy().as_ref());
    assert_eq!(result["launched"], true);
    assert_eq!(result["reused"], false);
    assert!(!engine.data_dir.join(".playback_cache").exists());

    std::thread::sleep(Duration::from_millis(500));
    let first_pid = engine
        .mpv_session
        .lock()
        .unwrap()
        .child
        .as_ref()
        .expect("mpv child exists")
        .id();

    let seek_result = engine
        .call(
            "notes.video_playback",
            json!({ "note_id": 84, "start_seconds": 42.0 }),
        )
        .expect("method handled")
        .expect("mpv seek succeeds");
    assert_eq!(seek_result["start_seconds"], 42.0);
    assert_eq!(seek_result["launched"], false);
    assert_eq!(seek_result["reused"], true);

    let mut session = engine.mpv_session.lock().unwrap();
    let child = session.child.as_mut().expect("mpv child remains available");
    assert_eq!(child.id(), first_pid, "timestamp seek must reuse mpv PID");
    assert!(
        child.try_wait().unwrap().is_none(),
        "mpv should remain running after in-place seek"
    );
    drop(session);
    engine.cancel_all_jobs();
    let _ = fs::remove_dir_all(root);
}

#[test]
fn unreferenced_routes_are_not_exposed() {
    let (engine, root) = temp_engine();
    for method in [
        "system.snapshot",
        "system.capabilities",
        "system.shutdown",
        "notes.get_by_path",
        "notes.video_source",
        "collection.update",
        "collection.list_items",
        "study.import",
        "study.import_web",
    ] {
        assert!(
            engine.call(method, json!({})).is_none(),
            "{method} remains exposed"
        );
    }
    assert!(engine
        .call("study.knowledge", json!({ "note_id": 1 }))
        .is_some());
    let _ = fs::remove_dir_all(root);
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
fn generic_provider_requires_explicit_video_capability() {
    let settings = provider_settings();
    let saved = provider_profile_for_request(&settings, &json!({ "name": "saved" })).unwrap();
    assert!(!saved.accepts_video);

    let explicit = provider_profile_for_request(
        &settings,
        &json!({
            "base_url": "https://compatible.example/v1",
            "model": "video-model",
            "video_input": true
        }),
    )
    .unwrap();
    assert!(explicit.accepts_video);
}

#[test]
fn known_video_endpoint_is_detected_but_explicit_false_wins() {
    let settings = provider_settings();
    let detected = provider_profile_for_request(
        &settings,
        &json!({
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5"
        }),
    )
    .unwrap();
    assert!(detected.accepts_video);

    let disabled = provider_profile_for_request(
        &settings,
        &json!({
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5",
            "video_input": false
        }),
    )
    .unwrap();
    assert!(!disabled.accepts_video);
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
fn settings_update_round_trips_defaults() {
    let (engine, root) = temp_engine();
    let updated = engine
        .call(
            "settings.update",
            json!({
                "patches": {
                    "compile_mode": "draft",
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
    assert_eq!(settings["compile_mode"], "draft");
    assert_eq!(settings["template"], "summary");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn process_jobs_persist_and_reload() {
    let (engine, root) = temp_engine();
    install_test_ffmpeg_component(&engine);
    let input = root.join("input.mp4");
    fs::write(&input, b"test media").unwrap();

    let started = engine
        .call(
            "compile.video",
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
            "compile.video",
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
        can_resume: false,
        settings_snapshot: None,
        workspace_dir: None,
        attempt: 1,
        parent_run_id: None,
        artifact_cleanup_policy: default_artifact_cleanup_policy(),
        note_id: None,
        collection_id: None,
        collection_item_id: None,
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
    assert_eq!(first["progress_message"], "应用重启时任务中断（进度 35%）");
    assert!(!first["completed_at"]
        .as_str()
        .unwrap_or_default()
        .is_empty());
    assert_eq!(*reloaded.next_job_id.lock().unwrap(), 8);

    let _ = fs::remove_dir_all(root);
}

#[test]
fn corrupt_jobs_state_is_quarantined_instead_of_silently_overwritten() {
    let root = std::env::temp_dir().join(format!("video-notes-jobs-{}", Uuid::new_v4()));
    let state_path = root.join(".jobs").join("jobs.json");
    fs::create_dir_all(state_path.parent().unwrap()).unwrap();
    fs::write(&state_path, b"{not valid json").unwrap();

    assert!(load_jobs(&state_path).is_empty());
    assert!(!state_path.exists());
    let backups = fs::read_dir(state_path.parent().unwrap())
        .unwrap()
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_name()
                .to_string_lossy()
                .starts_with("jobs.corrupt-")
        })
        .count();
    assert_eq!(backups, 1);
    let _ = fs::remove_dir_all(root);
}

#[test]
fn jobs_save_throttle_never_drops_structural_or_terminal_changes() {
    let root = std::env::temp_dir().join(format!("video-notes-jobs-{}", Uuid::new_v4()));
    let state_path = root.join("jobs.json");
    let first = test_job(1, "running");
    save_jobs(&state_path, std::slice::from_ref(&first)).unwrap();

    let mut second = test_job(2, "pending");
    save_jobs(&state_path, &[first.clone(), second.clone()]).unwrap();
    assert_eq!(load_jobs(&state_path).len(), 2);

    second.status = "failed".to_string();
    second.stage = "failed".to_string();
    second.error_message = Some("provider error".to_string());
    second.completed_at = Some(Utc::now().to_rfc3339());
    save_jobs(&state_path, &[first, second]).unwrap();
    let persisted = load_jobs(&state_path);
    assert_eq!(persisted[1].status, "failed");
    assert_eq!(
        persisted[1].error_message.as_deref(),
        Some("provider error")
    );
    let _ = fs::remove_dir_all(root);
}

#[test]
fn terminal_job_event_contains_output_and_error_state() {
    let mut job = test_job(7, "completed");
    job.progress = 100;
    job.completed_at = Some(Utc::now().to_rfc3339());
    job.output_path = Some("notes/result.md".to_string());
    job.note_id = Some(19);
    job.collection_id = Some(3);
    job.collection_item_id = Some(4);

    let event = job_progress_event(&job, job.id, &job.status, &job.stage, 100, "完成");

    assert_eq!(event["completed_at"], json!(job.completed_at));
    assert_eq!(event["output_path"], "notes/result.md");
    assert_eq!(event["note_id"], 19);
    assert_eq!(event["collection_id"], 3);
    assert_eq!(event["collection_item_id"], 4);
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
    for id in 1..=4 {
        let job = jobs
            .iter()
            .find(|job| job["id"].as_u64() == Some(id))
            .expect("reloaded job exists");
        assert_eq!(
            job["status"], "interrupted",
            "job {} should be interrupted",
            job["id"]
        );
        assert_eq!(job["can_resume"], false);
        assert!(!job["completed_at"].as_str().unwrap_or_default().is_empty());
    }
    // paused -> stays paused
    let paused = jobs
        .iter()
        .find(|job| job["id"].as_u64() == Some(5))
        .expect("paused job exists");
    assert_eq!(
        paused["status"], "paused",
        "paused job should stay paused after restart"
    );
    assert_eq!(paused["can_resume"], true);
    assert!(
        paused["completed_at"]
            .as_str()
            .unwrap_or_default()
            .is_empty(),
        "paused job should not have completed_at"
    );

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
    assert!(!job.can_resume);

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
    assert!(!job.can_resume);

    let _ = fs::remove_dir_all(root);
}

#[test]
fn retry_terminal_job_creates_new_job() {
    let (engine, root) = temp_engine();
    install_test_ffmpeg_component(&engine);
    let input = root.join("original.mp4");
    fs::write(&input, b"test media").unwrap();

    let mut original = test_job(1, "failed");
    original.input = input.to_string_lossy().to_string();
    original.title = Some("Original title".to_string());
    original.settings_snapshot = Some(json!({
        "input": original.input,
        "title": "Original title",
        "template": "lecture",
        "mode": "draft",
    }));
    insert_job(&engine, original, false);

    let result = engine
        .call("process.retry", json!({ "job_id": 1 }))
        .expect("method handled")
        .expect("retry succeeds");
    assert_eq!(result["job_id"], json!(2));
    let jobs = engine.jobs.lock().unwrap();
    assert_eq!(jobs.len(), 2);
    assert_eq!(jobs[0].status, "failed");
    assert_eq!(jobs[1].input, input.to_string_lossy());
    assert_eq!(jobs[1].title.as_deref(), Some("Original title"));
    assert_eq!(jobs[1].attempt, 2);
    assert_eq!(jobs[1].parent_run_id.as_deref(), Some("stable-1"));
    assert_eq!(
        jobs[1].settings_snapshot.as_ref().unwrap()["template"],
        "lecture"
    );
    assert_eq!(jobs[1].settings_snapshot.as_ref().unwrap()["mode"], "draft");
    drop(jobs);
    engine.cancel_all_jobs();

    let _ = fs::remove_dir_all(root);
}

#[test]
fn retry_params_for_legacy_job_fall_back_to_recorded_input_and_title() {
    let mut job = test_job(9, "interrupted");
    job.input = "legacy/lesson.mp4".to_string();
    job.title = Some("Legacy lesson".to_string());
    job.settings_snapshot = None;

    let params = retry_params_for_job(&job);

    assert_eq!(params["input"], "legacy/lesson.mp4");
    assert_eq!(params["title"], "Legacy lesson");
    assert!(params.get("template").is_none());
    assert!(params.get("mode").is_none());
}

#[test]
fn retry_returns_existing_active_attempt_instead_of_duplicating_it() {
    let (engine, root) = temp_engine();
    let source = test_job(1, "failed");
    let mut active_retry = test_job(2, "running");
    active_retry.parent_run_id = Some(source.job_id.clone());
    insert_job(&engine, source, false);
    insert_job(&engine, active_retry, false);

    let result = engine
        .call("process.retry", json!({ "job_id": 1 }))
        .expect("method handled")
        .expect("active retry is reused");

    assert_eq!(result["job_id"], 2);
    assert_eq!(result["deduplicated"], true);
    assert_eq!(engine.jobs.lock().unwrap().len(), 2);
    let _ = fs::remove_dir_all(root);
}

#[test]
fn completed_job_cannot_be_retried() {
    let (engine, root) = temp_engine();
    insert_job(&engine, test_job(1, "completed"), false);

    let result = engine
        .call("process.retry", json!({ "job_id": 1 }))
        .expect("method handled");

    assert!(result.is_err());
    assert_eq!(engine.jobs.lock().unwrap().len(), 1);
    let _ = fs::remove_dir_all(root);
}

#[test]
fn collection_batch_scopes_skip_completed_and_active_items() {
    assert!(collection_item_matches_batch_scope(
        "pending", None, "pending"
    ));
    assert!(!collection_item_matches_batch_scope(
        "pending",
        Some(10),
        "pending"
    ));
    assert!(!collection_item_matches_batch_scope(
        "completed",
        Some(9),
        "pending"
    ));
    assert!(!collection_item_matches_batch_scope(
        "failed",
        Some(8),
        "pending"
    ));

    for status in ["failed", "cancelled", "interrupted"] {
        assert!(collection_item_matches_batch_scope(
            status,
            Some(7),
            "failed"
        ));
    }
    assert!(!collection_item_matches_batch_scope(
        "completed",
        Some(6),
        "failed"
    ));
    assert!(!collection_item_matches_batch_scope(
        "pending", None, "failed"
    ));
    assert!(!collection_item_matches_batch_scope(
        "running",
        Some(5),
        "failed"
    ));
}

#[test]
fn collection_batch_process_rejects_unsafe_scope() {
    let (engine, root) = temp_engine();
    let created = engine
        .call(
            "collection.create",
            json!({ "name": "Course", "items": ["lesson.mp4"] }),
        )
        .expect("method handled")
        .expect("create succeeds");

    let result = engine
        .call(
            "collection.batch_process",
            json!({ "id": created["id"], "scope": "all" }),
        )
        .expect("method handled");

    assert!(result.is_err());
    assert!(engine.jobs.lock().unwrap().is_empty());
    let _ = fs::remove_dir_all(root);
}

#[test]
fn collection_batch_claim_prevents_duplicate_pending_runs() {
    let (engine, root) = temp_engine();
    let created = engine
        .call(
            "collection.create",
            json!({ "name": "Course", "items": ["a.mp4", "b.mp4"] }),
        )
        .expect("method handled")
        .expect("create succeeds");
    let id = created["id"].as_u64().unwrap();

    engine
        .call(
            "collection.batch_process",
            json!({ "id": id, "scope": "pending" }),
        )
        .expect("method handled")
        .expect("first batch claims pending items");
    let duplicate = engine
        .call(
            "collection.batch_process",
            json!({ "id": id, "scope": "pending" }),
        )
        .expect("method handled");

    assert!(duplicate.is_err());
    let _ = fs::remove_dir_all(root);
}

#[test]
fn concurrent_collection_creates_do_not_lose_updates_or_duplicate_ids() {
    let (engine, root) = temp_engine();
    let handles = (0..12)
        .map(|index| {
            let engine = engine.clone();
            std::thread::spawn(move || {
                engine
                    .call(
                        "collection.create",
                        json!({ "name": format!("Course {index}"), "items": [] }),
                    )
                    .expect("method handled")
                    .expect("create succeeds")["id"]
                    .as_u64()
                    .unwrap()
            })
        })
        .collect::<Vec<_>>();
    let ids = handles
        .into_iter()
        .map(|handle| handle.join().expect("create thread succeeds"))
        .collect::<HashSet<_>>();

    assert_eq!(ids.len(), 12);
    let list = engine
        .call("collection.list", json!({}))
        .expect("method handled")
        .expect("list succeeds");
    assert_eq!(list.as_array().unwrap().len(), 12);
    let _ = fs::remove_dir_all(root);
}

#[test]
fn collection_with_unstarted_pending_item_is_active_not_processing() {
    let unstarted = json!({
        "items": [{ "id": 1, "input": "lesson.mp4", "status": "pending" }]
    });
    let running = json!({
        "items": [{ "id": 1, "input": "lesson.mp4", "status": "pending", "run_id": 7 }]
    });

    assert_eq!(aggregate_collection_status(&unstarted), "active");
    assert_eq!(aggregate_collection_status(&running), "processing");
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
    let manifest = read_json_file(&engine.manifests_dir.join("download-tools.json")).unwrap();
    write_component_marker(
        &manifest,
        &engine.runtime_dir.join("components").join("download-tools"),
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
    assert_eq!(first["installed_version"], "1.5.7");

    let _ = fs::remove_dir_all(root);
}

#[test]
fn components_check_updates_uses_bundled_manifest_version() {
    let (engine, root) = temp_engine();
    fs::create_dir_all(&engine.manifests_dir).unwrap();
    let component_dir = engine.runtime_dir.join("components").join("download-tools");
    fs::create_dir_all(&component_dir).unwrap();
    let marker_path = component_marker_path(&component_dir);
    write_json_atomic(
        &engine.manifests_dir.join("download-tools.json"),
        &json!({
            "component": "download-tools",
            "version": "2026.07.18",
            "description": "yt-dlp standalone executable",
            "download_url": "https://example.invalid/yt-dlp.exe",
            "files": [if cfg!(target_os = "windows") { "yt-dlp.exe" } else { "yt-dlp" }]
        }),
    )
    .unwrap();
    write_json_atomic(
        &marker_path,
        &json!({
            "component": "download-tools",
            "manifest_version": "2026.06.09",
            "installed_at": "2026-06-09T00:00:00Z"
        }),
    )
    .unwrap();

    let stale = engine
        .call("components.check_updates", json!({}))
        .expect("method handled")
        .expect("check succeeds");
    let stale = stale.as_array().unwrap().first().unwrap();
    assert_eq!(stale["installed_version"], "2026.06.09");
    assert_eq!(stale["latest_version"], "2026.07.18");
    assert_eq!(stale["update_available"], true);

    write_json_atomic(
        &marker_path,
        &json!({
            "component": "download-tools",
            "manifest_version": "2026.07.18",
            "installed_at": "2026-07-18T00:00:00Z"
        }),
    )
    .unwrap();
    let current = engine
        .call("components.check_updates", json!({}))
        .expect("method handled")
        .expect("check succeeds");
    let current = current.as_array().unwrap().first().unwrap();
    assert_eq!(current["installed_version"], "2026.07.18");
    assert_eq!(current["latest_version"], "2026.07.18");
    assert_eq!(current["update_available"], false);

    let _ = fs::remove_dir_all(root);
}

#[test]
fn component_payload_tampering_is_detected_before_execution() {
    let (_engine, root) = temp_engine();
    let target = root.join("runtime").join("components").join("safe-tool");
    fs::create_dir_all(&target).unwrap();
    let executable = target.join(if cfg!(target_os = "windows") {
        "tool.exe"
    } else {
        "tool"
    });
    fs::write(&executable, "original").unwrap();
    write_component_marker(
        &json!({ "component": "safe-tool", "version": "1.0.0" }),
        &target,
    )
    .unwrap();
    verify_component_payload(&target).unwrap();

    fs::write(&executable, "tampered").unwrap();
    assert!(verify_component_payload(&target).is_err());
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

    assert_eq!(
        read_marker_version(&component_dir).as_deref(),
        Some("2026.07.01")
    );

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
    assert!(names.contains(&"mpv-tools"));

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
fn bearer_token_strips_existing_scheme() {
    assert_eq!(bearer_token("bearer abc123"), "abc123");
    assert_eq!(bearer_token("Bearer abc123"), "abc123");
    assert_eq!(bearer_token("abc123"), "abc123");
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
    let vault = root.join("vault");
    engine
        .call(
            "settings.update",
            json!({ "patches": { "vault_path": vault.to_string_lossy() } }),
        )
        .expect("method handled")
        .expect("settings update succeeds");
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
    let export_path = PathBuf::from(exported["path"].as_str().unwrap());
    assert!(export_path.is_dir());
    assert!(export_path.join("README.md").is_file());

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
    assert_eq!(queued["max_concurrency"], 1);

    let _ = fs::remove_dir_all(root);
}

#[test]
fn collection_batch_item_matches_task_center_job_state() {
    let (engine, root) = temp_engine();
    install_test_ffmpeg_component(&engine);

    let input = root.join("lesson.mp4");
    fs::write(&input, b"test media").unwrap();
    let created = engine
        .call(
            "collection.create",
            json!({ "name": "Course", "items": [input.to_string_lossy()] }),
        )
        .expect("method handled")
        .expect("create succeeds");
    let collection_id = created["id"].as_u64().unwrap();

    engine
        .call("collection.batch_process", json!({ "id": collection_id }))
        .expect("method handled")
        .expect("batch succeeds");

    let deadline = Instant::now() + Duration::from_secs(2);
    let job = loop {
        let jobs = engine
            .call("process.list", json!({}))
            .expect("method handled")
            .expect("list succeeds");
        if let Some(job) = jobs.as_array().and_then(|jobs| jobs.first()) {
            if is_terminal_status(job["status"].as_str().unwrap_or_default()) {
                break job.clone();
            }
        }
        assert!(
            Instant::now() < deadline,
            "batch job did not reach a terminal state"
        );
        std::thread::sleep(Duration::from_millis(10));
    };

    let detail = engine
        .call("collection.get", json!({ "id": collection_id }))
        .expect("method handled")
        .expect("collection get succeeds");
    let item = detail["items"].as_array().unwrap().first().unwrap();

    assert_ne!(job["id"], 0);
    assert_eq!(item["run_id"], job["id"]);
    assert_eq!(item["status"], job["status"]);
    assert_eq!(item["progress"], job["progress"]);
    assert_eq!(item["status"], "failed");

    let collection_store_path = engine.collection_store_path();
    let persisted: Value = serde_json::from_str(
        &fs::read_to_string(&collection_store_path).expect("collection state exists"),
    )
    .expect("collection state is valid JSON");
    assert_eq!(persisted["collections"][0]["items"][0]["status"], "failed");

    let mut stale = persisted;
    stale["collections"][0]["items"][0]["status"] = json!("completed");
    stale["collections"][0]["items"][0]["progress"] = json!(100);
    fs::write(
        &collection_store_path,
        serde_json::to_vec(&stale).expect("serialize stale collection state"),
    )
    .expect("write stale collection state");

    let detail = engine
        .call("collection.get", json!({ "id": collection_id }))
        .expect("method handled")
        .expect("collection get succeeds");
    let item = detail["items"].as_array().unwrap().first().unwrap();
    assert_eq!(item["status"], "failed");

    let persisted_after_read: Value = serde_json::from_str(
        &fs::read_to_string(&collection_store_path).expect("collection state exists"),
    )
    .expect("collection state is valid JSON");
    assert_eq!(
        persisted_after_read["collections"][0]["items"][0]["status"],
        "failed"
    );

    let _ = fs::remove_dir_all(root);
}

#[test]
fn collection_sync_uses_explicit_binding_and_retry_lineage() {
    let mut old_job = test_job(7, "interrupted");
    old_job.input = "course/lesson.mp4".to_string();
    old_job.progress = 0;

    let mut retried_job = test_job(12, "completed");
    retried_job.input = old_job.input.clone();
    retried_job.parent_run_id = Some(old_job.job_id.clone());
    retried_job.progress = 100;
    retried_job.output_path = Some("notes/lesson.md".to_string());

    let mut recovered_job = test_job(15, "failed");
    recovered_job.input = "course/recovered.mp4".to_string();
    recovered_job.collection_id = Some(42);
    recovered_job.collection_item_id = Some(2);
    recovered_job.progress = 100;
    recovered_job.error_message = Some("provider error".to_string());

    let mut collection = json!({
        "id": 42,
        "status": "failed",
        "items": [
            {
                "id": 1,
                "input": "course/lesson.mp4",
                "run_id": 7,
                "status": "interrupted",
                "progress": 0
            },
            {
                "id": 2,
                "input": "course/recovered.mp4",
                "status": "interrupted",
                "progress": 0
            },
            {
                "id": 3,
                "input": "course/not-started.mp4",
                "status": "pending",
                "progress": 0
            }
        ]
    });

    sync_collection_value_from_jobs(&mut collection, &[old_job, retried_job, recovered_job]);

    assert_eq!(collection["items"][0]["run_id"], 12);
    assert_eq!(collection["items"][0]["status"], "completed");
    assert_eq!(collection["items"][0]["progress"], 100);
    assert_eq!(collection["items"][0]["output_path"], "notes/lesson.md");

    assert_eq!(collection["items"][1]["run_id"], 15);
    assert_eq!(collection["items"][1]["status"], "failed");
    assert_eq!(collection["items"][1]["progress"], 100);
    assert_eq!(collection["items"][1]["error_message"], "provider error");

    assert!(collection["items"][2].get("run_id").is_none());
    assert_eq!(collection["items"][2]["status"], "pending");
    assert_eq!(collection["items"][2]["progress"], 0);
    assert_eq!(collection["status"], "active");
}
