#[cfg(test)]
mod tests {
    use super::super::*;
    use chrono::Utc;
    use serde_json::{json, Map, Value};
    use std::{
        fs,
        path::{Path, PathBuf},
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

}
