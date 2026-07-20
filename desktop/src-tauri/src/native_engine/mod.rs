use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{hash_map::DefaultHasher, HashMap, HashSet, VecDeque};
use std::ffi::OsStr;
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime};
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

use crate::compile::storage::CapsuleStore;

mod study;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const YTDLP_DOWNLOAD_URL: &str =
    "https://github.com/yt-dlp/yt-dlp/releases/download/2026.06.09/yt-dlp.exe";
const YTDLP_DOWNLOAD_SHA256: &str =
    "3a48cb955d55c8821b60ccbdbbc6f61bc958f2f3d3b7ad5eaf3d83a543293a27";
const MAX_MEDIA_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_COMPONENT_DOWNLOAD_BYTES: u64 = 512 * 1024 * 1024;
const SMART_COMPILE_CONCURRENCY: usize = 2;
const MAX_COMPILE_CONCURRENCY: usize = 4;
const DEFAULT_COMPONENT_MANIFESTS: &[(&str, &str)] = &[
    (
        "download-tools",
        include_str!("../../../../runtime/manifests/download-tools.json"),
    ),
    (
        "ffmpeg-tools",
        include_str!("../../../../runtime/manifests/ffmpeg-tools.json"),
    ),
    (
        "mpv-tools",
        include_str!("../../../../runtime/manifests/mpv-tools.json"),
    ),
];

/// Lock ordering (must never be acquired in reverse):
///   1. next_job_id
///   2. job_controls
///   3. jobs
///   4. settings_lock
///   5. collection_lock / retry_lock (never held while acquiring jobs)
///   ---
///   6. current_child  (per-job)
///
/// Note: process_resume acquires #3 (jobs) then #2 (job_controls) in
/// sequence, but never holds both simultaneously — the first lock is
/// dropped before the second is acquired, so no deadlock risk exists.
#[derive(Clone)]
pub struct NativeEngine {
    app_handle: Option<AppHandle>,
    settings_path: PathBuf,
    data_dir: PathBuf,
    runtime_dir: PathBuf,
    manifests_dir: PathBuf,
    default_export_dir: PathBuf,
    jobs_state_path: PathBuf,
    jobs: Arc<Mutex<Vec<NativeJob>>>,
    next_job_id: Arc<Mutex<u64>>,
    job_controls: Arc<Mutex<HashMap<u64, Arc<JobControl>>>>,
    settings_lock: Arc<Mutex<()>>,
    collection_lock: Arc<Mutex<()>>,
    retry_lock: Arc<Mutex<()>>,
    compile_scheduler: Arc<CompileScheduler>,
    mpv_session: Arc<Mutex<MpvSession>>,
}

struct MpvSession {
    child: Option<Child>,
    source_path: Option<PathBuf>,
    ipc_path: PathBuf,
}

impl MpvSession {
    fn new() -> Self {
        #[cfg(target_os = "windows")]
        let ipc_path = PathBuf::from(format!(r"\\.\pipe\video-notes-ai-mpv-{}", Uuid::new_v4()));
        #[cfg(not(target_os = "windows"))]
        let ipc_path =
            std::env::temp_dir().join(format!("video-notes-ai-mpv-{}.sock", Uuid::new_v4()));

        Self {
            child: None,
            source_path: None,
            ipc_path,
        }
    }
}

#[derive(Clone, Deserialize, Serialize)]
struct NativeJob {
    id: u64,
    job_id: String,
    title: Option<String>,
    status: String,
    progress: u8,
    progress_message: String,
    stage: String,
    input: String,
    created_at: String,
    completed_at: Option<String>,
    error_message: Option<String>,
    output_path: Option<String>,
    transcript_path: Option<String>,
    can_resume: bool,
    #[serde(default)]
    settings_snapshot: Option<Value>,
    #[serde(default)]
    workspace_dir: Option<String>,
    #[serde(default = "default_job_attempt")]
    attempt: u32,
    #[serde(default)]
    parent_run_id: Option<String>,
    #[serde(default = "default_artifact_cleanup_policy")]
    artifact_cleanup_policy: String,
    #[serde(default)]
    note_id: Option<u32>,
    #[serde(default)]
    collection_id: Option<u64>,
    #[serde(default)]
    collection_item_id: Option<u64>,
}

struct JobControl {
    cancel_requested: AtomicBool,
    pause_requested: AtomicBool,
    current_child: Mutex<Option<u32>>,
    condvar: Condvar,
}

struct CompileSchedulerState {
    limit: usize,
    active: usize,
    queue: VecDeque<u64>,
}

struct CompileScheduler {
    state: Mutex<CompileSchedulerState>,
    condvar: Condvar,
}

struct CompilePermit {
    scheduler: Arc<CompileScheduler>,
}

type RetryContext = (u32, String, Option<(u64, u64)>);

impl JobControl {
    fn new() -> Self {
        Self {
            cancel_requested: AtomicBool::new(false),
            pause_requested: AtomicBool::new(false),
            current_child: Mutex::new(None),
            condvar: Condvar::new(),
        }
    }
}

impl CompileScheduler {
    fn new(limit: usize) -> Self {
        Self {
            state: Mutex::new(CompileSchedulerState {
                limit: limit.clamp(1, MAX_COMPILE_CONCURRENCY),
                active: 0,
                queue: VecDeque::new(),
            }),
            condvar: Condvar::new(),
        }
    }

    fn limit(&self) -> usize {
        self.state
            .lock()
            .map(|state| state.limit)
            .unwrap_or(SMART_COMPILE_CONCURRENCY)
    }

    fn set_limit(&self, limit: usize) {
        if let Ok(mut state) = self.state.lock() {
            state.limit = limit.clamp(1, MAX_COMPILE_CONCURRENCY);
            self.condvar.notify_all();
        }
    }

    fn notify_all(&self) {
        self.condvar.notify_all();
    }

    fn acquire(
        self: &Arc<Self>,
        job_id: u64,
        control: &JobControl,
    ) -> Result<CompilePermit, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "compile scheduler lock poisoned".to_string())?;
        if !state.queue.contains(&job_id) {
            state.queue.push_back(job_id);
        }

        loop {
            if control.cancel_requested.load(Ordering::SeqCst) {
                state.queue.retain(|queued_id| *queued_id != job_id);
                self.condvar.notify_all();
                return Err(crate::compile::engine::COMPILE_CANCELLED_ERROR.to_string());
            }
            if state.active < state.limit && state.queue.front() == Some(&job_id) {
                state.queue.pop_front();
                state.active += 1;
                return Ok(CompilePermit {
                    scheduler: self.clone(),
                });
            }
            state = self
                .condvar
                .wait_timeout(state, Duration::from_millis(250))
                .map_err(|_| "compile scheduler wait poisoned".to_string())?
                .0;
        }
    }
}

impl Drop for CompilePermit {
    fn drop(&mut self) {
        if let Ok(mut state) = self.scheduler.state.lock() {
            state.active = state.active.saturating_sub(1);
            self.scheduler.condvar.notify_all();
        }
    }
}

struct ActiveJobGuard {
    id: u64,
    controls: Arc<Mutex<HashMap<u64, Arc<JobControl>>>>,
}

impl Drop for ActiveJobGuard {
    fn drop(&mut self) {
        if let Ok(mut controls) = self.controls.lock() {
            controls.remove(&self.id);
        }
    }
}

#[derive(Clone)]
struct NoteEntry {
    id: u32,
    title: String,
    path: PathBuf,
    created_at: String,
}

#[derive(Clone, Debug)]
pub(crate) struct NativeProviderProfile {
    pub(crate) provider_type: String,
    pub(crate) base_url: String,
    pub(crate) api_key: String,
    pub(crate) model: String,
    pub(crate) vision_model: String,
    pub(crate) accepts_video: bool,
}

struct CollectionBatchItem {
    id: u64,
    input: String,
    title: String,
    compile_mode: String,
}

#[derive(Clone, Serialize)]
struct ComponentDownloadProgress {
    component: String,
    downloaded_bytes: u64,
    total_bytes: u64,
    stage: String,
}

impl NativeEngine {
    pub fn new(app_handle: &AppHandle) -> Self {
        let data_dir = local_app_data_dir(app_handle);
        let settings_path = persistent_settings_path(app_handle, &data_dir);
        if let Err(error) = migrate_plaintext_provider_secrets(&settings_path) {
            eprintln!("[warn] Could not migrate Provider credentials to the OS vault: {error}");
        }
        let compile_concurrency = compile_concurrency_from_path(&settings_path);
        let runtime_dir = data_dir.join("runtime");
        let jobs_state_path = jobs_state_path(&data_dir);
        let jobs = load_jobs(&jobs_state_path);
        let next_job_id = next_job_id(&jobs);
        let manifests_dir = project_root()
            .map(|root| root.join("runtime").join("manifests"))
            .unwrap_or_else(|| data_dir.join("runtime").join("manifests"));
        let default_export_dir = default_export_dir(app_handle);
        // Clean up stale temp files from previous crashed runs.
        cleanup_stale_temp_files(&runtime_dir);
        let engine = Self {
            app_handle: Some(app_handle.clone()),
            settings_path,
            data_dir,
            runtime_dir,
            manifests_dir,
            default_export_dir,
            jobs_state_path,
            jobs: Arc::new(Mutex::new(jobs)),
            next_job_id: Arc::new(Mutex::new(next_job_id)),
            job_controls: Arc::new(Mutex::new(HashMap::new())),
            settings_lock: Arc::new(Mutex::new(())),
            collection_lock: Arc::new(Mutex::new(())),
            retry_lock: Arc::new(Mutex::new(())),
            compile_scheduler: Arc::new(CompileScheduler::new(compile_concurrency)),
            mpv_session: Arc::new(Mutex::new(MpvSession::new())),
        };
        if let Err(error) = engine.allow_configured_asset_roots() {
            eprintln!("[warn] Could not configure local note asset scope: {error}");
        }
        engine
    }

    /// Cancel all active jobs and kill their child processes.
    /// Called when the application window is closing.
    pub fn cancel_all_jobs(&self) {
        if let Ok(controls) = self.job_controls.lock() {
            for (_, control) in controls.iter() {
                control.cancel_requested.store(true, Ordering::SeqCst);
                control.condvar.notify_all();
                #[cfg(target_os = "windows")]
                if let Ok(mut current_child) = control.current_child.lock() {
                    if let Some(pid) = current_child.take() {
                        kill_process_pid(pid);
                    }
                }
            }
        }
        self.compile_scheduler.notify_all();
        if let Ok(mut session) = self.mpv_session.lock() {
            stop_mpv_session(&mut session);
        }
    }

    /// Returns the directory used for legacy capsule storage.
    /// Shared with the v0.2 FileBundleStore so both formats co-locate.
    #[cfg(feature = "compiler_v3")]
    pub fn capsule_storage_dir(&self) -> PathBuf {
        self.data_dir.join(".capsules")
    }

    #[cfg(test)]
    fn for_paths(
        settings_path: PathBuf,
        data_dir: PathBuf,
        runtime_dir: PathBuf,
        manifests_dir: PathBuf,
        default_export_dir: PathBuf,
    ) -> Self {
        let jobs_state_path = jobs_state_path(&data_dir);
        let jobs = load_jobs(&jobs_state_path);
        let next_job_id = next_job_id(&jobs);
        let compile_concurrency = compile_concurrency_from_path(&settings_path);
        Self {
            app_handle: None,
            settings_path,
            data_dir,
            runtime_dir,
            manifests_dir,
            default_export_dir,
            jobs_state_path,
            jobs: Arc::new(Mutex::new(jobs)),
            next_job_id: Arc::new(Mutex::new(next_job_id)),
            job_controls: Arc::new(Mutex::new(HashMap::new())),
            settings_lock: Arc::new(Mutex::new(())),
            collection_lock: Arc::new(Mutex::new(())),
            retry_lock: Arc::new(Mutex::new(())),
            compile_scheduler: Arc::new(CompileScheduler::new(compile_concurrency)),
            mpv_session: Arc::new(Mutex::new(MpvSession::new())),
        }
    }

    pub fn call(&self, method: &str, params: Value) -> Option<Result<Value, String>> {
        let result = match method {
            "system.ping" => Ok(json!("pong")),
            "system.info" => self.system_info(),
            "system.open_url" => self.system_open_url(params),
            "settings.get" => self.settings_get(),
            "settings.update" => self.settings_update(params),
            "settings.secret.set" => self.settings_secret_set(params),
            "settings.secret.delete" => self.settings_secret_delete(params),
            "settings.providers.list" => self.providers_list(),
            "settings.providers.create" | "settings.providers.add" => self.providers_create(params),
            "settings.providers.update" => self.providers_update(params),
            "settings.providers.delete" | "settings.providers.remove" => {
                self.providers_delete(params)
            }
            "settings.providers.set_active" => self.providers_set_active(params),
            "settings.providers.test" => self.provider_test(params),
            "settings.providers.models" => self.provider_models(params),
            "settings.providers.capabilities.clear" => self.providers_capabilities_clear(params),
            "settings.templates.list" => self.templates_list(),
            "settings.bindings.set" => self.bindings_set(params),
            "doctor.run" => self.doctor_run(),
            "diagnostics.bundle" => self.diagnostics_bundle(),
            "components.list" => self.components_list(),
            "components.check_updates" => self.components_check_updates(),
            "components.verify" => self.components_verify(params),
            "components.install" => self.components_install(params),
            "components.remove" => self.components_remove(params),
            "storage.status" => self.storage_status(),
            "storage.cleanup_orphans" => self.storage_cleanup_orphans(params),
            "storage.cleanup_completed" => self.storage_cleanup_completed(),
            "storage.cleanup_capsules" => self.storage_cleanup_capsules(),
            "storage.cleanup_playback_cache" => self.storage_cleanup_playback_cache(),
            "process.list" => self.process_list(params),
            "process.delete" => self.process_delete(params),
            "process.open_output" => self.process_output_action(params, false),
            "process.reveal_output" => self.process_output_action(params, true),
            "process.pause" => self.process_pause(params),
            "process.cancel" => self.process_cancel(params),
            "process.resume" => self.process_resume(params),
            "process.retry" => self.process_retry(params),
            "notes.list" => self.notes_list(None),
            "notes.search" => self.notes_list(string_param(&params, "query")),
            "notes.get" => self.notes_get(params),
            "notes.update" => self.notes_update(params),
            "notes.delete" => self.notes_delete(params),
            "notes.answer" => self.notes_answer(params),
            "notes.video_playback" => self.notes_video_playback(params),
            "notes.open" => self.notes_open(params),
            "notes.reveal" => self.notes_reveal(params),
            "collection.list" => self.collection_list(),
            "collection.get" => self.collection_get(params),
            "collection.create" => self.collection_create(params),
            "collection.delete" => self.collection_delete(params),
            "collection.add_items" => self.collection_add_items(params),
            "collection.remove_items" => self.collection_remove_items(params),
            "collection.import_folder" => self.collection_import_folder(params),
            "collection.export" => self.collection_export(params),
            "collection.batch_process" => self.collection_batch_process(params),
            "collection.pause_all" => self.collection_pause_all(params),
            "collection.resume_all" => self.collection_resume_all(params),
            "collection.cancel_all" => self.collection_cancel_all(params),
            "study.knowledge" => self.study_knowledge(params),
            "study.quiz" => self.study_quiz(params),
            "compile.video" => self.compile_video(params),
            "compile.list_versions" => self.compile_list_versions(params),
            "compile.replay" => self.compile_replay(params),
            "compile.render" => self.compile_render(params),
            _ => return None,
        };
        // Redact potential API keys/tokens from error messages before
        // returning to the frontend (defense in depth).
        Some(result.map_err(|e| redact_secrets(&e)))
    }

    fn system_info(&self) -> Result<Value, String> {
        Ok(json!({
            "shell_version": env!("CARGO_PKG_VERSION"),
            "engine_version": env!("CARGO_PKG_VERSION"),
            "protocol_version": 1,
            "engine_kind": "rust-native",
            "cuda_available": false,
            "cuda_device_count": 0,
            "cuda_compute_types": [],
            "ffmpeg_available": tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir),
        }))
    }

    fn system_open_url(&self, params: Value) -> Result<Value, String> {
        let url = required_string(&params, "url")?;
        open_url(&url)
    }

    fn settings_get(&self) -> Result<Value, String> {
        let settings = self.read_settings();
        let template = string_value(&settings, "template")
            .or_else(|| string_value(&settings, "template_id"))
            .unwrap_or_else(|| "default".to_string());
        let active_provider = string_value(&settings, "active_provider").unwrap_or_default();
        Ok(json!({
            "output_dir": string_value(&settings, "output_dir").unwrap_or_else(|| self.default_export_dir.to_string_lossy().to_string()),
            "compile_mode": string_value(&settings, "compile_mode").unwrap_or_else(|| "precision".to_string()),
            "template": template,
            "template_id": template,
            "vault_path": string_value(&settings, "vault_path").unwrap_or_default(),
            "active_provider": active_provider,
            "providers": provider_profiles(&settings, &active_provider),
            "bindings": settings.get("bindings").cloned().unwrap_or_else(|| json!({})),
            "provider": settings.get("provider").cloned().unwrap_or(Value::Null),
            "ai_model": settings.get("ai_model").cloned().unwrap_or(Value::Null),
            "base_url": settings.get("base_url").cloned().unwrap_or(Value::Null),
            "bilibili_cookie_file": string_value(&settings, "bilibili_cookie_file")
                .or_else(|| string_value(&settings, "bilibili_cookies"))
                .unwrap_or_default(),
            "compile_concurrency": configured_compile_concurrency(&settings),
            "effective_compile_concurrency": self.compile_scheduler.limit(),
        }))
    }

    fn settings_update(&self, params: Value) -> Result<Value, String> {
        let patches = params
            .get("patches")
            .and_then(Value::as_object)
            .or_else(|| params.as_object())
            .ok_or_else(|| "patches must be an object".to_string())?;
        let compile_concurrency = patches
            .get("compile_concurrency")
            .map(validate_compile_concurrency)
            .transpose()?;
        let allowed = [
            "output_dir",
            "compile_mode",
            "template",
            "template_id",
            "vault_path",
            "provider",
            "ai_model",
            "base_url",
            "bilibili_cookie_file",
            "bilibili_cookies",
            "compile_concurrency",
        ];
        self.update_settings(|raw| {
            for key in allowed {
                if let Some(value) = patches.get(key) {
                    raw.insert(key.to_string(), value.clone());
                }
            }
            if let Some(value) = raw.get("template").cloned() {
                raw.entry("template_id".to_string()).or_insert(value);
            }
            Ok(())
        })?;
        if let Some(configured) = compile_concurrency {
            self.compile_scheduler
                .set_limit(effective_compile_concurrency(configured));
        }
        self.allow_configured_asset_roots()?;
        Ok(json!(true))
    }

    fn settings_secret_set(&self, params: Value) -> Result<Value, String> {
        let provider = required_string(&params, "provider")?;
        let key = string_param(&params, "api_key")
            .or_else(|| string_param(&params, "key"))
            .ok_or_else(|| "api_key is required".to_string())?;
        credential_store(&provider, &key)?;
        self.update_settings(|raw| {
            let profile = find_provider_mut(raw, &provider)?;
            profile.remove("api_key");
            profile.insert("credential_vault".to_string(), json!(true));
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn settings_secret_delete(&self, params: Value) -> Result<Value, String> {
        let provider = required_string(&params, "provider")?;
        credential_delete(&provider)?;
        self.update_settings(|raw| {
            let profile = find_provider_mut(raw, &provider)?;
            profile.remove("api_key");
            profile.remove("credential_vault");
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn providers_list(&self) -> Result<Value, String> {
        let settings = self.read_settings();
        let active = string_value(&settings, "active_provider").unwrap_or_default();
        Ok(json!(provider_profiles(&settings, &active)))
    }

    fn providers_create(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        let api_key = string_param(&params, "api_key").filter(|value| !value.is_empty());
        if let Some(key) = api_key.as_deref() {
            credential_store(&name, key)?;
        }
        let model = string_param(&params, "model").unwrap_or_default();
        let vision_model = string_param(&params, "vision_model").unwrap_or_default();
        let mut models = clean_models(vec![model.clone(), vision_model.clone()]);
        if models.is_empty() && !model.is_empty() {
            models.push(model.clone());
        }
        let mut entry = Map::new();
        entry.insert("name".to_string(), json!(name));
        entry.insert(
            "type".to_string(),
            json!(normalise_provider_type(
                string_param(&params, "provider")
                    .or_else(|| string_param(&params, "type"))
                    .as_deref(),
            )),
        );
        entry.insert(
            "base_url".to_string(),
            json!(normalise_provider_base_url(
                &string_param(&params, "base_url").unwrap_or_default()
            )),
        );
        entry.insert("models".to_string(), json!(models));
        entry.insert("model".to_string(), json!(model));
        entry.insert("vision_model".to_string(), json!(vision_model));
        let provider_type = entry
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("openai_compat")
            .to_string();
        let base_url = entry.get("base_url").and_then(Value::as_str).unwrap_or("");
        entry.insert(
            "video_input".to_string(),
            json!(params
                .get("video_input")
                .and_then(Value::as_bool)
                .unwrap_or(provider_supports_video_input(&provider_type, base_url))),
        );
        entry.insert(
            "audio_input".to_string(),
            json!(params
                .get("audio_input")
                .and_then(Value::as_bool)
                .unwrap_or(matches!(provider_type.as_str(), "google_gemini"))),
        );
        if api_key.is_some() {
            entry.insert("credential_vault".to_string(), json!(true));
        }
        let result = self.update_settings(|raw| {
            if find_provider(raw, &name).is_some() {
                return Err(format!("Provider '{name}' already exists"));
            }
            let providers = raw
                .entry("providers".to_string())
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or_else(|| "providers must be an array".to_string())?;
            providers.push(Value::Object(entry));
            if string_value(raw, "active_provider")
                .unwrap_or_default()
                .is_empty()
            {
                raw.insert("active_provider".to_string(), json!(name));
                raw.insert(
                    "bindings".to_string(),
                    json!({ "llm": { "provider": name, "model": model } }),
                );
            }
            Ok(())
        });
        if result.is_err() && api_key.is_some() {
            let _ = credential_delete(&name);
        }
        result?;
        Ok(json!(true))
    }

    fn providers_update(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        self.update_settings(|raw| {
            let profile = find_provider_mut(raw, &name)?;
            let old_model = profile
                .get("model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let old_vision_model = profile
                .get("vision_model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if let Some(provider_type) =
                string_param(&params, "provider").or_else(|| string_param(&params, "type"))
            {
                profile.insert(
                    "type".to_string(),
                    json!(normalise_provider_type(Some(&provider_type))),
                );
            }
            if let Some(base_url) = string_param(&params, "base_url") {
                profile.insert(
                    "base_url".to_string(),
                    json!(normalise_provider_base_url(&base_url)),
                );
            }
            if let Some(model) = string_param(&params, "model") {
                profile.insert("model".to_string(), json!(model));
            }
            if let Some(vision_model) = string_param(&params, "vision_model") {
                profile.insert("vision_model".to_string(), json!(vision_model));
            }
            if let Some(audio_input) = params.get("audio_input").and_then(Value::as_bool) {
                profile.insert("audio_input".to_string(), json!(audio_input));
            }
            if let Some(video_input) = params.get("video_input").and_then(Value::as_bool) {
                profile.insert("video_input".to_string(), json!(video_input));
            }
            let model = profile
                .get("model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let vision_model = profile
                .get("vision_model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            profile.insert(
                "models".to_string(),
                json!(clean_models(vec![model, vision_model])),
            );
            let new_model = profile
                .get("model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let new_vision_model = profile
                .get("vision_model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if old_model != new_model || old_vision_model != new_vision_model {
                profile.remove("capabilities");
            }
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn providers_delete(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        self.update_settings(|raw| {
            let providers = raw
                .entry("providers".to_string())
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or_else(|| "providers must be an array".to_string())?;
            let old_len = providers.len();
            providers.retain(|profile| {
                profile
                    .get("name")
                    .and_then(Value::as_str)
                    .map(|value| !value.eq_ignore_ascii_case(&name))
                    .unwrap_or(true)
            });
            if providers.len() == old_len {
                return Err(format!("Provider '{name}' not found"));
            }
            if string_value(raw, "active_provider")
                .map(|value| value.eq_ignore_ascii_case(&name))
                .unwrap_or(false)
            {
                raw.insert("active_provider".to_string(), json!(""));
            }
            Ok(())
        })?;
        credential_delete(&name)?;
        Ok(json!(true))
    }

    fn providers_set_active(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        self.update_settings(|raw| {
            let profile = find_provider(raw, &name)
                .ok_or_else(|| format!("Provider '{name}' not found"))?
                .clone();
            let model = profile
                .get("model")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let vision_model = profile
                .get("vision_model")
                .and_then(Value::as_str)
                .unwrap_or(&model)
                .to_string();
            raw.insert("active_provider".to_string(), json!(name));
            raw.insert(
                "bindings".to_string(),
                json!({
                    "llm": { "provider": name, "model": model },
                    "vision": { "provider": name, "model": vision_model },
                }),
            );
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn provider_test(&self, params: Value) -> Result<Value, String> {
        let settings = self.read_settings();
        let cache_provider = capability_cache_provider_name(&settings, &params);
        let profile = provider_profile_for_request(&settings, &params)?;
        match fetch_provider_models(&profile) {
            Ok(models) => {
                let (capability_cache_saved, capability_cache_error) =
                    match cache_provider.as_deref() {
                        Some(provider_name) => self.update_provider_capability(
                            provider_name,
                            &profile.model,
                            "text",
                            "pass",
                            "Text model is available",
                            None,
                        ),
                        None => (
                            false,
                            Some("Ad-hoc provider test is not cached".to_string()),
                        ),
                    };
                Ok(json!({
                    "success": true,
                    "message": format!("服务可用，读取到 {} 个模型", models.len()),
                    "models": models,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }))
            }
            Err(error) => {
                let (capability_cache_saved, capability_cache_error) =
                    match cache_provider.as_deref() {
                        Some(provider_name) => self.update_provider_capability(
                            provider_name,
                            &profile.model,
                            "text",
                            "fail",
                            "Text model test failed",
                            None,
                        ),
                        None => (
                            false,
                            Some("Ad-hoc provider test is not cached".to_string()),
                        ),
                    };
                Ok(json!({
                    "success": false,
                    "message": error,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }))
            }
        }
    }

    fn provider_models(&self, params: Value) -> Result<Value, String> {
        let settings = self.read_settings();
        let profile = provider_profile_for_request(&settings, &params)?;
        let models = fetch_provider_models(&profile)?;
        Ok(json!(models))
    }

    fn providers_capabilities_clear(&self, params: Value) -> Result<Value, String> {
        let provider = string_param(&params, "provider").unwrap_or_default();
        self.update_settings(|raw| {
            if provider.trim().is_empty() {
                if let Some(providers) = raw.get_mut("providers").and_then(Value::as_array_mut) {
                    for profile in providers {
                        if let Some(object) = profile.as_object_mut() {
                            object.remove("capabilities");
                        }
                    }
                }
            } else {
                let profile = find_provider_mut(raw, &provider)?;
                profile.remove("capabilities");
            }
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn update_provider_capability(
        &self,
        provider: &str,
        model: &str,
        capability: &str,
        status: &str,
        message: &str,
        error: Option<&str>,
    ) -> (bool, Option<String>) {
        if provider.trim().is_empty() || model.trim().is_empty() {
            return (false, Some("provider/model is empty".to_string()));
        }
        let safe_message = sanitize_capability_cache_text(message);
        let safe_error = error.map(sanitize_capability_cache_text);
        match self.update_settings(|raw| {
            let profile = find_provider_mut(raw, provider)?;
            let current_model = if capability == "vision" {
                profile
                    .get("vision_model")
                    .and_then(Value::as_str)
                    .filter(|value| !value.trim().is_empty())
                    .or_else(|| profile.get("model").and_then(Value::as_str))
                    .unwrap_or("")
            } else {
                profile.get("model").and_then(Value::as_str).unwrap_or("")
            };
            if current_model.trim() != model.trim() {
                return Err("capability target model changed".to_string());
            }
            let capabilities = profile
                .entry("capabilities".to_string())
                .or_insert_with(|| json!({}));
            if !capabilities.is_object() {
                *capabilities = json!({});
            }
            let capabilities = capabilities
                .as_object_mut()
                .ok_or_else(|| "capabilities must be an object".to_string())?;
            let model_entry = capabilities
                .entry(model.to_string())
                .or_insert_with(|| json!({}));
            if !model_entry.is_object() {
                *model_entry = json!({});
            }
            let model_entry = model_entry
                .as_object_mut()
                .ok_or_else(|| "capability entry must be an object".to_string())?;
            model_entry.insert(capability.to_string(), json!(status));
            model_entry.insert("last_tested_at".to_string(), json!(Utc::now().to_rfc3339()));
            model_entry.insert("message".to_string(), json!(safe_message));
            model_entry.insert(
                "error".to_string(),
                safe_error.clone().map(Value::from).unwrap_or(Value::Null),
            );
            Ok(())
        }) {
            Ok(()) => (true, None),
            Err(error) => (false, Some(sanitize_capability_cache_text(&error))),
        }
    }

    fn bindings_set(&self, params: Value) -> Result<Value, String> {
        let purpose = required_string(&params, "purpose")?;
        if purpose != "llm" && purpose != "vision" {
            return Err("purpose must be 'llm' or 'vision'".to_string());
        }
        let provider = required_string(&params, "provider")?;
        let model = string_param(&params, "model").unwrap_or_default();
        self.update_settings(|raw| {
            if find_provider(raw, &provider).is_none() {
                return Err(format!("Provider '{provider}' not found"));
            }
            let bindings = raw
                .entry("bindings".to_string())
                .or_insert_with(|| json!({}))
                .as_object_mut()
                .ok_or_else(|| "bindings must be an object".to_string())?;
            bindings.insert(purpose, json!({ "provider": provider, "model": model }));
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn templates_list(&self) -> Result<Value, String> {
        Ok(json!([
            {
                "id": "default",
                "name": "默认学习笔记",
                "description": "通用结构化笔记模板",
                "path": "builtin://default"
            },
            {
                "id": "lecture",
                "name": "课程讲义",
                "description": "适合课程、讲座和教程",
                "path": "builtin://lecture"
            },
            {
                "id": "summary",
                "name": "摘要",
                "description": "短摘要与关键要点",
                "path": "builtin://summary"
            },
            {
                "id": "mindmap",
                "name": "思维导图",
                "description": "大纲式思维导图",
                "path": "builtin://mindmap"
            }
        ]))
    }

    fn doctor_run(&self) -> Result<Value, String> {
        let ffmpeg = tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir);
        let ytdlp = tool_exists("yt-dlp", &["download-tools"], &self.runtime_dir);
        Ok(json!([
            check_item("Rust native engine", true, "in-process"),
            check_item("FFmpeg", ffmpeg, "system PATH or ffmpeg-tools"),
            check_item("yt-dlp", ytdlp, "download-tools"),
        ]))
    }

    fn diagnostics_bundle(&self) -> Result<Value, String> {
        let dir = self.data_dir.join("diagnostics");
        fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
        let path = dir.join(format!(
            "diagnostics-{}.json",
            Utc::now().format("%Y%m%d-%H%M%S")
        ));
        let payload = json!({
            "engine_kind": "rust-native",
            "version": env!("CARGO_PKG_VERSION"),
            "settings_path": self.settings_path,
            "data_dir": self.data_dir,
            "runtime_dir": self.runtime_dir,
            "created_at": Utc::now().to_rfc3339(),
        });
        write_json_atomic(&path, &payload)?;
        Ok(json!(path.to_string_lossy().to_string()))
    }

    fn components_list(&self) -> Result<Value, String> {
        let mut result = Vec::new();
        for manifest in self.component_manifests()? {
            let Some(component) = manifest.get("component").and_then(Value::as_str) else {
                continue;
            };
            let component_path = self.runtime_dir.join("components").join(component);
            let files = manifest
                .get("files")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let missing = missing_files(&component_path, &files);
            let installed = component_path.is_dir();
            let integrity = if installed && missing.is_empty() {
                verify_component_payload(&component_path)
            } else {
                Err("component files are missing".to_string())
            };
            let verified = integrity.is_ok();
            let installed_version = if verified {
                component_runtime_version(component, &component_path)
                    .or_else(|| read_marker_version(&component_path))
                    .map(Value::String)
                    .unwrap_or(Value::Null)
            } else {
                Value::Null
            };
            // Do NOT call GitHub API here — that was blocking and rate-limited.
            // latest_version and update_available are populated on-demand via
            // components_check_updates (triggered by the "检查更新" button).
            result.push(json!({
                "component": component,
                "version": manifest.get("version").and_then(Value::as_str).unwrap_or(""),
                "description": manifest.get("description").and_then(Value::as_str).unwrap_or(""),
                "installed": installed,
                "installed_version": installed_version,
                "latest_version": Value::Null,
                "update_available": false,
                "status": if verified { "ok" } else if installed && missing.is_empty() { "integrity_failed" } else if installed { "missing_files" } else { "not_installed" },
                "integrity_error": integrity.err(),
                "size_mb": manifest.get("size_mb").cloned().unwrap_or(Value::Null),
                "component_path": component_path.to_string_lossy(),
                "provides": manifest.get("provides").cloned().unwrap_or_else(|| json!([])),
                "missing_files": missing,
                "downloadable": manifest_string(&manifest, "download_url").is_some(),
            }));
        }
        Ok(json!(result))
    }

    /// Compare installed components with the digest-pinned manifests bundled with this app.
    /// The installer can only install these trusted manifests, so a live repository tag is
    /// neither an installable nor a meaningful update candidate.
    fn components_check_updates(&self) -> Result<Value, String> {
        let mut results = Vec::new();
        for manifest in self.component_manifests()? {
            let Some(component) = manifest.get("component").and_then(Value::as_str) else {
                continue;
            };
            let download_url = match manifest_string(&manifest, "download_url") {
                Some(url) if !url.is_empty() => url,
                _ => continue,
            };
            let component_path = self.runtime_dir.join("components").join(component);
            let installed = component_path.is_dir();
            let installed_version = if installed {
                read_marker_version(&component_path).unwrap_or_default()
            } else {
                String::new()
            };
            let latest_version = manifest_string(&manifest, "version").unwrap_or_default();
            let update_available = installed
                && !installed_version.is_empty()
                && !latest_version.is_empty()
                && installed_version != latest_version;
            results.push(json!({
                "component": component,
                "installed_version": installed_version,
                "latest_version": latest_version,
                "update_available": update_available,
                "download_url": download_url,
            }));
        }
        Ok(json!(results))
    }

    fn components_verify(&self, params: Value) -> Result<Value, String> {
        let component = required_string(&params, "component")?;
        let safe = sanitize_component_name(&component)?;
        let manifest = self.read_manifest(&safe)?;
        let component_path = self.runtime_dir.join("components").join(&safe);
        let files = manifest
            .get("files")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let missing = missing_files(&component_path, &files);
        let integrity = if component_path.is_dir() && missing.is_empty() {
            verify_component_payload(&component_path)
        } else {
            Err("component files are missing".to_string())
        };
        let ok = integrity.is_ok();
        Ok(json!({
            "ok": ok,
            "components": [{
                "component": component,
                "ok": ok,
                "status": if ok { "ok" } else if missing.is_empty() { "integrity_failed" } else { "missing_files" },
                "missing_files": missing,
                "integrity_error": integrity.err(),
            }]
        }))
    }

    fn components_install(&self, params: Value) -> Result<Value, String> {
        let component = required_string(&params, "component")?;
        let safe = sanitize_component_name(&component)?;
        let manifest = self.read_manifest(&safe)?;
        let source = self.runtime_dir.join("packages").join(&safe);
        let target = self.runtime_dir.join("components").join(&safe);
        if !source.is_dir() {
            // If the manifest has a download_url, try downloading it.
            let download_error = if manifest_string(&manifest, "download_url").is_some() {
                match self.app_handle.as_ref() {
                    Some(handle) => {
                        match install_component_from_download(
                            &manifest, &target, &component, handle,
                        ) {
                            Ok(()) => {
                                write_component_marker(&manifest, &target)?;
                                return Ok(json!({
                                    "ok": true,
                                    "component": component,
                                    "status": "installed"
                                }));
                            }
                            Err(error) => Some(error),
                        }
                    }
                    None => Some("App handle not available for download".to_string()),
                }
            } else {
                None
            };
            let path_result = match component.as_str() {
                "download-tools" => install_download_tools(&target),
                "ffmpeg-tools" => install_ffmpeg_tools_from_path(&target),
                _ => Err(format!(
                    "Missing local package: {}. Put the component package there, then install again.",
                    source.display()
                )),
            };
            match path_result {
                Ok(()) => {
                    write_component_marker(&manifest, &target)?;
                    return Ok(json!({
                        "ok": true,
                        "component": component,
                        "status": "installed"
                    }));
                }
                Err(path_error) => {
                    return if let Some(download_error) = download_error {
                        Err(format!(
                            "Download install failed: {download_error}; PATH/local import failed: {path_error}"
                        ))
                    } else {
                        Err(path_error)
                    };
                }
            }
        }
        if target.exists() {
            fs::remove_dir_all(&target).map_err(|error| error.to_string())?;
        }
        copy_dir_recursive(&source, &target)?;
        write_component_marker(&manifest, &target)?;
        Ok(json!({ "ok": true, "component": component, "status": "installed" }))
    }

    fn components_remove(&self, params: Value) -> Result<Value, String> {
        let component = required_string(&params, "component")?;
        let safe = sanitize_component_name(&component)?;
        let components_root = self.runtime_dir.join("components");
        fs::create_dir_all(&components_root).map_err(|error| error.to_string())?;
        let root = components_root
            .canonicalize()
            .map_err(|error| format!("failed to resolve components root: {error}"))?;
        let target = components_root.join(&safe);
        if target.exists() {
            let resolved = target
                .canonicalize()
                .map_err(|error| format!("failed to resolve component target: {error}"))?;
            if !resolved.starts_with(&root) || resolved.parent() != Some(root.as_path()) {
                return Err("component target escapes the components directory".to_string());
            }
            fs::remove_dir_all(&target).map_err(|error| error.to_string())?;
        }
        Ok(json!({ "ok": true, "component": safe, "status": "removed" }))
    }

    fn storage_status(&self) -> Result<Value, String> {
        let settings = self.read_settings();
        let export_dir = effective_note_output_dir(&settings, &self.default_export_dir);
        let jobs_root = self.data_dir.join("jobs");
        let legacy_jobs_root = self.data_dir.join(".jobs");
        let capsule_root = self.data_dir.join(".capsules");
        let vault_path = string_value(&settings, "vault_path").unwrap_or_default();
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let total_tasks = jobs.len();
        let running_tasks = jobs
            .iter()
            .filter(|job| {
                matches!(
                    job.status.as_str(),
                    "pending" | "running" | "pausing" | "cancelling" | "paused"
                )
            })
            .count();
        let completed_tasks = jobs.iter().filter(|job| job.status == "completed").count();
        let failed_tasks = jobs
            .iter()
            .filter(|job| matches!(job.status.as_str(), "failed" | "interrupted" | "cancelled"))
            .count();
        Ok(json!({
            "export_dir": export_dir.to_string_lossy(),
            "jobs_root": jobs_root.to_string_lossy(),
            "legacy_jobs_root": legacy_jobs_root.to_string_lossy(),
            "downloads_root": self.data_dir.join(".downloads").to_string_lossy(),
            "capsule_root": capsule_root.to_string_lossy(),
            "vault_path": vault_path,
            "playback_cache": self.data_dir.join(".playback_cache").to_string_lossy(),
            "sizes": {
                "exports": dir_size(&export_dir),
                "jobs": dir_size(&jobs_root),
                "legacy_jobs": dir_size(&legacy_jobs_root),
                "downloads": dir_size(&self.data_dir.join(".downloads")),
                "capsules": dir_size(&capsule_root),
                "runtime": dir_size(&self.runtime_dir),
                "playback_cache": dir_size(&self.data_dir.join(".playback_cache")),
            },
            "counts": {
                "exports": dir_counts(&export_dir),
                "jobs": dir_counts(&jobs_root),
                "legacy_jobs": dir_counts(&legacy_jobs_root),
                "downloads": dir_counts(&self.data_dir.join(".downloads")),
                "capsules": dir_counts(&capsule_root),
                "runtime": dir_counts(&self.runtime_dir),
                "playback_cache": dir_counts(&self.data_dir.join(".playback_cache")),
            },
            "tasks": {
                "total": total_tasks,
                "running": running_tasks,
                "completed": completed_tasks,
                "failed": failed_tasks,
            }
        }))
    }

    fn storage_cleanup_orphans(&self, params: Value) -> Result<Value, String> {
        let min_age_hours = params
            .get("min_age_hours")
            .and_then(Value::as_u64)
            .unwrap_or(24);
        let min_age = Duration::from_secs(min_age_hours.saturating_mul(60 * 60));

        // Remove orphan-status jobs (failed / cancelled / interrupted) from the list
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let (keep, dead): (Vec<_>, Vec<_>) =
            std::mem::take(&mut *jobs).into_iter().partition(|job| {
                // Keep active, pending, paused, and completed jobs
                !matches!(job.status.as_str(), "failed" | "cancelled" | "interrupted")
            });
        *jobs = keep;
        save_jobs(&self.jobs_state_path, &jobs)?;
        drop(jobs);

        let mut removed_workspaces = 0;
        for dead_job in &dead {
            cleanup_deleted_job_workspace(dead_job, &self.data_dir);
            removed_workspaces += 1;
        }

        // Also clean up truly orphaned workspace dirs (no job record at all)
        let known_ids: HashSet<_> = {
            let jobs = self.jobs.lock().map_err(|_| "jobs lock poisoned")?;
            jobs.iter().map(|j| j.id).collect()
        };
        let running_ids = {
            let jobs = self.jobs.lock().map_err(|_| "jobs lock poisoned")?;
            jobs.iter()
                .filter(|job| {
                    matches!(
                        job.status.as_str(),
                        "pending" | "running" | "pausing" | "cancelling" | "paused"
                    )
                })
                .map(|job| job.id)
                .collect::<HashSet<_>>()
        };
        for root in [
            self.data_dir.join("jobs"),
            self.data_dir.join(".jobs"),
            self.data_dir.join(".downloads"),
        ] {
            removed_workspaces += cleanup_workspace_dirs(&root, |dir, job_id| {
                if running_ids.contains(&job_id) || known_ids.contains(&job_id) {
                    return false;
                }
                workspace_is_older_than(dir, min_age)
            })?;
        }

        Ok(json!({ "removed": removed_workspaces }))
    }

    fn storage_cleanup_completed(&self) -> Result<Value, String> {
        // Remove all completed jobs from the list and clean up their workspaces
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let (keep, completed): (Vec<_>, Vec<_>) = std::mem::take(&mut *jobs)
            .into_iter()
            .partition(|job| job.status != "completed");
        *jobs = keep;
        save_jobs(&self.jobs_state_path, &jobs)?;
        drop(jobs);

        let mut removed = completed.len() as u64;
        for dead_job in &completed {
            cleanup_deleted_job_workspace(dead_job, &self.data_dir);
        }

        // Also clean up any leftover workspace dirs for completed jobs
        {
            let jobs = self.jobs.lock().map_err(|_| "jobs lock poisoned")?;
            let completed_ids: HashSet<_> = jobs
                .iter()
                .filter(|job| job.status == "completed")
                .map(|job| job.id)
                .collect();
            drop(jobs);
            for root in [
                self.data_dir.join("jobs"),
                self.data_dir.join(".jobs"),
                self.data_dir.join(".downloads"),
            ] {
                removed +=
                    cleanup_workspace_dirs(&root, |_, job_id| completed_ids.contains(&job_id))?;
            }
        }

        Ok(json!({ "removed": removed }))
    }

    fn storage_cleanup_capsules(&self) -> Result<Value, String> {
        let capsule_root = self.data_dir.join(".capsules");
        if !capsule_root.is_dir() {
            return Ok(json!({ "removed": false }));
        }
        let count = dir_counts(&capsule_root);
        fs::remove_dir_all(&capsule_root).map_err(|e| format!("failed to remove capsules: {e}"))?;
        Ok(json!({ "removed": count }))
    }

    /// Delete the playback cache directory.
    fn storage_cleanup_playback_cache(&self) -> Result<Value, String> {
        let cache_root = self.data_dir.join(".playback_cache");
        if !cache_root.is_dir() {
            return Ok(json!({ "removed": false, "size": 0 }));
        }
        let size = dir_size(&cache_root);
        let count = dir_counts(&cache_root);
        fs::remove_dir_all(&cache_root)
            .map_err(|e| format!("failed to remove playback cache: {e}"))?;
        Ok(json!({ "removed": count, "size": size }))
    }

    fn process_list(&self, params: Value) -> Result<Value, String> {
        let limit = params.get("limit").and_then(Value::as_u64).unwrap_or(200) as usize;
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let mut values: Vec<Value> = jobs.iter().map(NativeJob::to_value).collect();
        values.sort_by(|left, right| {
            right["id"]
                .as_u64()
                .unwrap_or(0)
                .cmp(&left["id"].as_u64().unwrap_or(0))
        });
        values.truncate(limit);
        Ok(json!(values))
    }

    fn process_pause(&self, params: Value) -> Result<Value, String> {
        let id = job_id_param(&params)?;
        let control = self.job_control(id)?;
        let previous_pause = control.pause_requested.swap(true, Ordering::SeqCst);
        if let Err(error) = self.transition_job_action(
            id,
            &["pending", "running"],
            "pausing",
            None,
            "已请求暂停；将在当前阶段结束后暂停",
            false,
        ) {
            control
                .pause_requested
                .store(previous_pause, Ordering::SeqCst);
            return Err(error);
        }
        Ok(json!(true))
    }

    fn process_cancel(&self, params: Value) -> Result<Value, String> {
        let id = job_id_param(&params)?;
        let control = self.job_control(id)?;
        let previous_cancel = control.cancel_requested.swap(true, Ordering::SeqCst);
        if let Err(error) = self.transition_job_action(
            id,
            &["pending", "running", "pausing", "paused", "cancelling"],
            "cancelling",
            None,
            "已请求取消；将在安全检查点停止",
            false,
        ) {
            control
                .cancel_requested
                .store(previous_cancel, Ordering::SeqCst);
            return Err(error);
        }
        control.condvar.notify_all();
        self.compile_scheduler.notify_all();
        Ok(json!(true))
    }

    fn process_resume(&self, params: Value) -> Result<Value, String> {
        let id = job_id_param(&params)?;

        // In-session resume: job has an active JobControl
        if let Ok(control) = self.job_control(id) {
            let previous_pause = control.pause_requested.swap(false, Ordering::SeqCst);
            if let Err(error) = self.transition_job_action(
                id,
                &["pausing", "paused"],
                "running",
                None,
                "继续执行任务",
                false,
            ) {
                control
                    .pause_requested
                    .store(previous_pause, Ordering::SeqCst);
                return Err(error);
            }
            control.condvar.notify_all();
            return Ok(json!(true));
        }

        // After restart: job is paused but no JobControl exists
        // Compile pipeline tasks run synchronously — cannot resume after restart.
        Err("Job is not active; compile pipeline tasks run synchronously and cannot be resumed after restart".to_string())
    }

    fn process_retry(&self, params: Value) -> Result<Value, String> {
        let id = job_id_param(&params)?;
        let _retry_guard = self
            .retry_lock
            .lock()
            .map_err(|_| "retry lock poisoned".to_string())?;
        let (retry_params, retry_context) = {
            let jobs = self
                .jobs
                .lock()
                .map_err(|_| "jobs lock poisoned".to_string())?;
            let job = jobs
                .iter()
                .find(|job| job.id == id)
                .ok_or_else(|| format!("Job {id} not found"))?;
            if !is_retryable_status(&job.status) {
                return Err(format!("Job {id} cannot be retried from {}", job.status));
            }
            if let Some(existing) = jobs.iter().rev().find(|candidate| {
                candidate.parent_run_id.as_deref() == Some(job.job_id.as_str())
                    && is_active_status(&candidate.status)
            }) {
                return Ok(json!({
                    "job_id": existing.id,
                    "stable_job_id": existing.job_id,
                    "deduplicated": true,
                }));
            }
            (
                retry_params_for_job(job),
                (
                    job.attempt.saturating_add(1),
                    job.job_id.clone(),
                    job.collection_id.zip(job.collection_item_id),
                ),
            )
        };
        self.compile_video_with_collection(retry_params, None, Some(retry_context))
    }

    fn job_control(&self, id: u64) -> Result<Arc<JobControl>, String> {
        self.job_controls
            .lock()
            .map_err(|_| "job controls lock poisoned".to_string())?
            .get(&id)
            .cloned()
            .ok_or_else(|| format!("Job {id} is not active"))
    }

    fn transition_job_action(
        &self,
        id: u64,
        allowed: &[&str],
        status: &str,
        stage: Option<&str>,
        message: &str,
        can_resume: bool,
    ) -> Result<(), String> {
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let event = {
            let job = jobs
                .iter_mut()
                .find(|job| job.id == id)
                .ok_or_else(|| format!("Job {id} not found"))?;
            if !allowed.contains(&job.status.as_str()) {
                return Err(format!("Job {id} cannot transition from {}", job.status));
            }
            job.status = status.to_string();
            if let Some(stage) = stage {
                job.stage = stage.to_string();
            }
            job.progress_message = message.to_string();
            job.can_resume = can_resume;
            if is_terminal_status(status) {
                job.completed_at = Some(Utc::now().to_rfc3339());
            }
            job_progress_event(job, id, status, &job.stage, job.progress, message)
        };
        save_jobs(&self.jobs_state_path, &jobs)?;
        if let Some(handle) = &self.app_handle {
            let _ = handle.emit("job:progress", event);
        }
        Ok(())
    }

    fn process_delete(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("job_id")
            .and_then(Value::as_u64)
            .ok_or_else(|| "job_id is required".to_string())?;
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        if jobs
            .iter()
            .any(|job| job.id == id && is_active_status(&job.status))
        {
            return Err(format!("Job {id} is active and cannot be deleted"));
        }
        let removed_job = jobs.iter().find(|job| job.id == id).cloned();
        let old_len = jobs.len();
        jobs.retain(|job| job.id != id);
        save_jobs(&self.jobs_state_path, &jobs)?;
        if let Some(job) = removed_job {
            cleanup_deleted_job_workspace(&job, &self.data_dir);
        }
        if jobs.len() != old_len {
            if let Ok(mut controls) = self.job_controls.lock() {
                controls.remove(&id);
            }
        }
        Ok(json!(jobs.len() != old_len))
    }

    fn process_output_action(&self, params: Value, reveal: bool) -> Result<Value, String> {
        let id = params
            .get("job_id")
            .or_else(|| params.get("id"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "job_id is required".to_string())?;
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let path = jobs
            .iter()
            .find(|job| job.id == id)
            .and_then(|job| job.output_path.as_ref())
            .map(PathBuf::from)
            .ok_or_else(|| format!("Job {id} has no note output yet"))?;
        if !path.is_file() {
            return Err(format!("Note output not found: {}", path.display()));
        }
        if reveal {
            reveal_path(&path)
        } else {
            open_path(&path)
        }
    }

    fn notes_list(&self, query: Option<String>) -> Result<Value, String> {
        let query = query.unwrap_or_default().to_lowercase();
        let entries = self.note_entries()?;
        for note in &entries {
            self.allow_note_asset_root(&note.path)?;
        }
        let notes = entries
            .into_iter()
            .map(|note| {
                if query.is_empty() {
                    // No search: return all notes with neutral score
                    return (note, 0i64);
                }
                // Score: content match = 3, title match = 2, path match = 1
                let mut score = 0i64;
                if note.title.to_lowercase().contains(&query) {
                    score += 2;
                }
                if note.path.to_string_lossy().to_lowercase().contains(&query) {
                    score += 1;
                }
                if score < 3 {
                    // Try full-text content search for better results
                    if let Ok(content) = std::fs::read_to_string(&note.path) {
                        let body = content
                            .split("---")
                            .nth(2) // skip frontmatter
                            .unwrap_or(&content)
                            .to_lowercase();
                        if body.contains(&query) {
                            // Count occurrences for ranking
                            let count = body.matches(&query).count() as i64;
                            score = score.max(3 + count.min(10));
                        }
                    }
                }
                (note, score)
            })
            .filter(|(_, score)| query.is_empty() || *score > 0)
            .map(|(note, _)| {
                json!({
                    "id": note.id,
                    "title": note.title,
                    "path": note.path.to_string_lossy(),
                    "created_at": note.created_at,
                })
            })
            .collect::<Vec<_>>();
        Ok(json!(notes))
    }

    fn notes_get(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("note_id")
            .or_else(|| params.get("id"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "note_id is required".to_string())? as u32;
        let note = self
            .note_entries()?
            .into_iter()
            .find(|note| note.id == id)
            .ok_or_else(|| format!("Note {id} not found"))?;
        self.allow_note_asset_root(&note.path)?;
        note_detail(note)
    }

    fn notes_update(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("id")
            .or_else(|| params.get("note_id"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "id is required".to_string())? as u32;
        let content = params
            .get("content")
            .and_then(Value::as_str)
            .ok_or_else(|| "content is required".to_string())?;
        let note = self
            .note_entries()?
            .into_iter()
            .find(|note| note.id == id)
            .ok_or_else(|| format!("Note {id} not found"))?;
        fs::write(&note.path, content).map_err(|error| error.to_string())?;
        Ok(json!(true))
    }

    fn notes_delete(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("id")
            .or_else(|| params.get("note_id"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "id is required".to_string())? as u32;
        let note = self
            .note_entries()?
            .into_iter()
            .find(|note| note.id == id)
            .ok_or_else(|| format!("Note {id} not found"))?;
        remove_note_assets(&note.path)?;
        fs::remove_file(&note.path).map_err(|error| error.to_string())?;
        Ok(json!(true))
    }

    fn notes_open(&self, params: Value) -> Result<Value, String> {
        let note = self.note_from_id_params(params)?;
        open_path(&note.path)
    }

    fn notes_reveal(&self, params: Value) -> Result<Value, String> {
        let note = self.note_from_id_params(params)?;
        reveal_path(&note.path)
    }

    fn resolve_note_video_source(&self, note_id: u32) -> Result<Value, String> {
        // First try: look up the job that produced this note
        {
            if let Ok(jobs) = self.jobs.lock() {
                if let Some(job) = jobs.iter().find(|j| j.note_id == Some(note_id)) {
                    if let Ok(path) = self.resolve_video_path(job) {
                        return Ok(json!({ "path": path, "is_local": true }));
                    }
                }
            }
        }

        // Fallback: read video_notes_source_input from note file frontmatter
        if let Ok(notes) = self.note_entries() {
            if let Some(note) = notes.iter().find(|n| n.id == note_id) {
                if let Ok(content) = std::fs::read_to_string(&note.path) {
                    if let Some(input) =
                        parse_frontmatter_value(&content, "video_notes_source_input")
                    {
                        let is_local =
                            !input.starts_with("http://") && !input.starts_with("https://");
                        if is_local && std::path::Path::new(&input).is_file() {
                            return Ok(json!({ "path": input, "is_local": true }));
                        }
                        // Return the input even if file is gone — frontend will show error
                        return Ok(json!({ "path": input, "is_local": is_local }));
                    }
                }
            }
        }

        Err(format!("no video source found for note {note_id}"))
    }

    /// Open a note's original local video in mpv without playback transcoding.
    fn notes_video_playback(&self, params: Value) -> Result<Value, String> {
        let (source_path, start_seconds) = self.resolve_note_playback_request(&params)?;
        let mpv = resolve_tool_path("mpv", &["mpv-tools"], &self.runtime_dir)
            .ok_or_else(|| "缺少 mpv 播放组件，请在设置的运行组件中安装 mpv-tools".to_string())?;

        let mut session = self
            .mpv_session
            .lock()
            .map_err(|_| "mpv 播放器状态锁定失败".to_string())?;

        let player_running = match session.child.as_mut() {
            Some(child) => child
                .try_wait()
                .map(|status| status.is_none())
                .unwrap_or(false),
            None => false,
        };
        if player_running && session.source_path.as_deref() == Some(source_path.as_path()) {
            let commands = mpv_seek_commands(start_seconds);
            if send_mpv_ipc(&session.ipc_path, &commands).is_ok() {
                return Ok(json!({
                    "path": source_path.to_string_lossy(),
                    "is_local": true,
                    "playable": true,
                    "player": "mpv",
                    "start_seconds": start_seconds,
                    "launched": false,
                    "reused": true
                }));
            }
        }

        stop_mpv_session(&mut session);
        let child = mpv_playback_command(&mpv, &source_path, start_seconds, &session.ipc_path)
            .spawn()
            .map_err(|error| format!("mpv 启动失败: {error}"))?;
        session.child = Some(child);
        session.source_path = Some(source_path.clone());

        Ok(json!({
            "path": source_path.to_string_lossy(),
            "is_local": true,
            "playable": true,
            "player": "mpv",
            "start_seconds": start_seconds,
            "launched": true,
            "reused": false
        }))
    }

    fn resolve_note_playback_request(&self, params: &Value) -> Result<(PathBuf, f64), String> {
        let note_id = params
            .get("note_id")
            .and_then(Value::as_u64)
            .ok_or_else(|| "note_id is required".to_string())?;
        let start_seconds = params
            .get("start_seconds")
            .and_then(Value::as_f64)
            .unwrap_or(0.0);
        if !start_seconds.is_finite() || start_seconds < 0.0 {
            return Err("start_seconds must be a non-negative finite number".to_string());
        }

        let source = self.resolve_note_video_source(note_id as u32)?;
        let source_path = source
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| "no path in source".to_string())?
            .to_string();
        let is_local = source
            .get("is_local")
            .and_then(Value::as_bool)
            .unwrap_or(false);

        if !is_local {
            return Err("mpv 笔记播放仅支持已下载的本地视频".to_string());
        }

        let src = PathBuf::from(&source_path);
        if !src.is_file() {
            return Err(format!("source file not found: {}", src.display()));
        }
        Ok((src, start_seconds))
    }

    /// Resolve the actual video file path from a job record.
    fn resolve_video_path(&self, job: &NativeJob) -> Result<String, String> {
        let input = job.input.clone();
        let is_local = !input.starts_with("http://") && !input.starts_with("https://");
        if is_local {
            if std::path::Path::new(&input).is_file() {
                return Ok(input);
            }
            return Err(format!("source file not found: {input}"));
        }
        let download_dir = self
            .data_dir
            .join(".downloads")
            .join(format!("job-{}", job.id));
        if download_dir.is_dir() {
            let video_extensions = ["mp4", "webm", "mkv", "avi", "mov"];
            let found = std::fs::read_dir(&download_dir)
                .ok()
                .and_then(|entries| {
                    entries.flatten().find(|entry| {
                        let name = entry.file_name().to_string_lossy().to_lowercase();
                        video_extensions.iter().any(|ext| name.ends_with(ext))
                    })
                })
                .map(|entry| entry.path().to_string_lossy().to_string());
            match found {
                Some(path) => Ok(path),
                None => Err(format!(
                    "no video file found in download directory: {}",
                    download_dir.display()
                )),
            }
        } else {
            Err(format!(
                "download directory not found: {}",
                download_dir.display()
            ))
        }
    }

    /// Answer a question about a compiled video, grounded in its evidence.
    /// Params: source_hash, version, question
    fn notes_answer(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let version = params
            .get("version")
            .and_then(Value::as_u64)
            .ok_or_else(|| "version is required".to_string())? as u32;
        let question = crate::native_engine::required_string(&params, "question")?;
        let history: Vec<serde_json::Value> = params
            .get("history")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|item| {
                        let role = item.get("role")?.as_str()?;
                        let content = item.get("content")?.as_str()?;
                        Some(serde_json::json!({ "role": role, "content": content }))
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        // Load capsule from storage
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let capsule = store
            .get(&source_hash, version)
            .map_err(|e| format!("capsule not found: {e}"))?;

        // Build evidence context from the capsule
        let mut evidence_text = String::new();
        for (i, ev) in capsule.evidences.iter().enumerate() {
            let start_s = ev.timestamp_start_sec;
            let end_s = ev.timestamp_end_sec;
            evidence_text.push_str(&format!(
                "Evidence {}: [{:.0}s–{:.0}s] {}\n",
                i + 1,
                start_s,
                end_s,
                ev.content
            ));
        }

        let source_title = &capsule.source_title;

        // Get active provider for the AI call
        let settings = self.read_settings();
        let profile = crate::native_engine::active_provider_profile(&settings)
            .map_err(|e| format!("No active provider: {e}"))?;

        let client = reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()
            .map_err(|e| format!("HTTP client init failed: {e}"))?;

        let url = format!(
            "{}/chat/completions",
            profile.base_url.trim_end_matches('/')
        );

        let system_prompt = r#"你是一个基于视频学习材料的问答助手。

你将收到一段学习笔记（包含带时间戳的逐条证据列表）和一个用户问题。

你的任务是：
1. 仔细阅读证据列表中的每一条
2. 结合视频笔记的主题，用中文回答用户问题
3. 在回答中引用相关的证据编号，格式为 [证据 N]
4. 如果问题涉及的知识不在提供的证据范围内，诚实地说明
5. 回答要简洁、准确，基于证据本身

输出格式（严格 JSON，无额外文字）：
{
  "answer": "你的中文回答",
  "citations": [1, 3, 5],
  "confidence": "high|medium|low"
}

如果问题与笔记内容完全无关，将 answer 设为空字符串，citations 留空数组。"#;

        let user_message = format!(
            "## 笔记主题\n{}\n\n## 证据列表\n{}\n\n## 用户问题\n{}",
            source_title, evidence_text, question
        );

        let mut messages = vec![serde_json::json!({"role": "system", "content": system_prompt})];
        messages.extend(history);
        messages.push(serde_json::json!({"role": "user", "content": user_message}));

        let response =
            crate::native_engine::with_optional_bearer(client.post(&url), &profile.api_key)
                .json(&serde_json::json!({
                    "model": profile.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 2048
                }))
                .send()
                .map_err(|e| format!("HTTP request failed: {e}"))?;

        let status = response.status();
        let payload: serde_json::Value = response
            .json()
            .map_err(|e| format!("Invalid JSON response: {e}"))?;

        if !status.is_success() {
            return Err(format!("notes.answer API returned {status}: {payload}"));
        }

        let text = payload
            .get("choices")
            .and_then(|c| c.as_array())
            .and_then(|c| c.first())
            .and_then(|c| c.get("message"))
            .and_then(|m| m.get("content").or_else(|| m.get("reasoning")))
            .and_then(|m| m.as_str())
            .ok_or_else(|| "API returned no content".to_string())?;

        // Try to parse as JSON, fall back to plain text
        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(text) {
            return Ok(parsed);
        }

        // Return as plain text answer
        Ok(serde_json::json!({
            "answer": text,
            "citations": [],
            "confidence": "medium"
        }))
    }

    fn note_from_id_params(&self, params: Value) -> Result<NoteEntry, String> {
        let id = params
            .get("id")
            .or_else(|| params.get("note_id"))
            .and_then(Value::as_u64)
            .ok_or_else(|| "id is required".to_string())? as u32;
        self.note_entries()?
            .into_iter()
            .find(|note| note.id == id)
            .ok_or_else(|| format!("Note {id} not found"))
    }

    fn note_entries(&self) -> Result<Vec<NoteEntry>, String> {
        let settings = self.read_settings();
        let mut roots = Vec::new();
        roots.push(effective_note_output_dir(
            &settings,
            &self.default_export_dir,
        ));
        let legacy_export_dir = string_value(&settings, "output_dir")
            .map(PathBuf::from)
            .unwrap_or_else(|| self.default_export_dir.clone());
        if !roots.iter().any(|root| root == &legacy_export_dir) {
            roots.push(legacy_export_dir);
        }

        let mut notes = Vec::new();
        for root in roots {
            collect_markdown_notes(&root, &mut notes, 0)?;
        }
        notes.sort_by(|left, right| right.created_at.cmp(&left.created_at));
        Ok(notes)
    }

    fn collection_list(&self) -> Result<Value, String> {
        let store = self.synced_collection_store(None)?;
        let collections = store
            .get("collections")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|collection| {
                let items = collection
                    .get("items")
                    .and_then(Value::as_array)
                    .map(Vec::len)
                    .unwrap_or(0);
                json!({
                    "id": collection.get("id").and_then(Value::as_u64).unwrap_or(0),
                    "name": collection.get("name").and_then(Value::as_str).unwrap_or("Untitled"),
                    "item_count": items,
                    "status": collection.get("status").and_then(Value::as_str).unwrap_or("active"),
                })
            })
            .collect::<Vec<_>>();
        Ok(json!(collections))
    }

    fn collection_get(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let store = self.synced_collection_store(Some(id))?;
        let collection = find_collection(&store, id)
            .cloned()
            .ok_or_else(|| format!("Collection {id} not found"))?;
        let items = collection
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        Ok(json!({
            "id": id,
            "name": collection.get("name").and_then(Value::as_str).unwrap_or("Untitled"),
            "status": collection.get("status").and_then(Value::as_str).unwrap_or("active"),
            "item_count": items.len(),
            "items": items,
        }))
    }

    fn synced_collection_store(
        &self,
        collection_id: Option<u64>,
    ) -> Result<Map<String, Value>, String> {
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?
            .clone();
        let _guard = self
            .collection_lock
            .lock()
            .map_err(|_| "collection lock poisoned".to_string())?;
        let mut store = self.read_collection_store_unlocked()?;
        let original = store.clone();
        if let Some(collections) = store.get_mut("collections").and_then(Value::as_array_mut) {
            for collection in collections {
                let id = collection.get("id").and_then(Value::as_u64).unwrap_or(0);
                if collection_id.is_some() && collection_id != Some(id) {
                    continue;
                }
                sync_collection_value_from_jobs(collection, &jobs);
            }
        }
        if store != original {
            write_json_atomic(&self.collection_store_path(), &Value::Object(store.clone()))?;
        }
        Ok(store)
    }

    fn collection_create(&self, params: Value) -> Result<Value, String> {
        let name = string_param(&params, "name")
            .or_else(|| string_param(&params, "title"))
            .ok_or_else(|| "name is required".to_string())?;
        let items = params
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter_map(|item| item.as_str().map(ToOwned::to_owned))
            .filter(|item| !item.trim().is_empty())
            .enumerate()
            .map(|(index, input)| collection_item((index + 1) as u64, &input))
            .collect::<Vec<_>>();
        self.update_collection_store(|store| {
            let id = next_store_id(store, "next_collection_id");
            store
                .entry("collections".to_string())
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or_else(|| "collections must be an array".to_string())?
                .push(json!({
                    "id": id,
                    "name": name,
                    "status": "active",
                    "created_at": Utc::now().to_rfc3339(),
                    "items": items,
                }));
            Ok(json!({ "id": id, "name": name }))
        })
    }

    fn collection_delete(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        self.update_collection_store(|store| {
            let collections = store
                .entry("collections".to_string())
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or_else(|| "collections must be an array".to_string())?;
            let old_len = collections.len();
            collections
                .retain(|collection| collection.get("id").and_then(Value::as_u64) != Some(id));
            Ok(json!(collections.len() != old_len))
        })
    }

    fn collection_add_items(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let new_inputs = params
            .get("items")
            .and_then(Value::as_array)
            .ok_or_else(|| "items must be a list".to_string())?
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(ToOwned::to_owned)
            .collect::<Vec<_>>();
        self.update_collection_store(|store| {
            let collection = find_collection_mut(store, id)
                .ok_or_else(|| format!("Collection {id} not found"))?;
            let collection = collection
                .as_object_mut()
                .ok_or_else(|| "collection must be an object".to_string())?;
            let items = collection
                .entry("items".to_string())
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or_else(|| "items must be an array".to_string())?;
            let next_id = items
                .iter()
                .filter_map(|item| item.get("id").and_then(Value::as_u64))
                .max()
                .unwrap_or(0)
                + 1;
            for (offset, input) in new_inputs.into_iter().enumerate() {
                items.push(collection_item(next_id + offset as u64, &input));
            }
            Ok(json!(items.clone()))
        })
    }

    fn collection_remove_items(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let item_ids = params
            .get("item_ids")
            .and_then(Value::as_array)
            .ok_or_else(|| "item_ids must be a list".to_string())?
            .iter()
            .filter_map(Value::as_u64)
            .collect::<Vec<_>>();
        self.update_collection_store(|store| {
            let collection = find_collection_mut(store, id)
                .ok_or_else(|| format!("Collection {id} not found"))?;
            let collection = collection
                .as_object_mut()
                .ok_or_else(|| "collection must be an object".to_string())?;
            let items = collection
                .entry("items".to_string())
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or_else(|| "items must be an array".to_string())?;
            items.retain(|item| {
                !item_ids.contains(&item.get("id").and_then(Value::as_u64).unwrap_or(0))
            });
            Ok(json!(true))
        })
    }

    fn collection_import_folder(&self, params: Value) -> Result<Value, String> {
        let path = PathBuf::from(required_string(&params, "path")?);
        if !path.is_dir() {
            return Err(format!("Folder not found: {}", path.display()));
        }
        let mut inputs = Vec::new();
        collect_media_files(&path, &mut inputs, 0)?;
        let name = path
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or("Imported Folder")
            .to_string();
        self.collection_create(json!({ "name": name, "items": inputs }))
    }

    fn collection_export(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let name = detail
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("collection");
        let settings = self.read_settings();

        // Export to Obsidian vault: {vault_path}/video-notes/collections/{name}/
        let vault = string_value(&settings, "vault_path")
            .filter(|v| !v.trim().is_empty())
            .map(PathBuf::from);
        let export_root = match &vault {
            Some(vault_path) => vault_path
                .join("video-notes")
                .join("collections")
                .join(format!("{}-{}", sanitize_filename(name), id)),
            None => return Err("请先在设置中配置 Obsidian 笔记库路径（vault_path）".to_string()),
        };
        fs::create_dir_all(&export_root).map_err(|e| format!("创建导出目录失败：{e}"))?;

        let mut exported = 0u32;
        let mut index_lines = Vec::new();

        for item in detail
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            let title = item
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("Untitled");
            let status = item
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("pending");

            if status != "completed" {
                index_lines.push(format!("- [ ] {title} — 状态：{status}"));
                continue;
            }

            // Read the note content from the compiled output file
            let note_path = item
                .get("output_path")
                .and_then(Value::as_str)
                .map(PathBuf::from);
            let content = note_path
                .filter(|p| p.is_file())
                .and_then(|p| std::fs::read_to_string(p).ok());

            match content {
                Some(text) => {
                    // Keep Obsidian-compatible frontmatter, just ensure it's valid
                    let filename = sanitize_filename(title);
                    let note_file = export_root.join(format!("{filename}.md"));
                    fs::write(&note_file, &text).map_err(|e| format!("写入笔记文件失败：{e}"))?;
                    exported += 1;
                    index_lines.push(format!("- [x] [{title}]({filename}.md)"));
                }
                None => {
                    index_lines.push(format!("- [ ] {title} — 笔记文件未找到"));
                }
            }
        }

        // Write index file
        let index_path = export_root.join("README.md");
        let mut index_body =
            format!("# {name}\n\n> 由 Video Notes AI 导出至 Obsidian\n\n共 {exported} 篇笔记\n\n");
        index_body.push_str(&index_lines.join("\n"));
        index_body.push('\n');
        fs::write(&index_path, &index_body).map_err(|e| format!("写入索引文件失败：{e}"))?;

        Ok(json!({ "path": export_root.to_string_lossy() }))
    }

    fn collection_batch_process(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let scope = string_param(&params, "scope").unwrap_or_else(|| "pending".to_string());
        if !matches!(scope.as_str(), "pending" | "failed") {
            return Err("scope must be 'pending' or 'failed'".to_string());
        }
        let mode = params
            .get("opts")
            .and_then(|opts| opts.get("compile_mode"))
            .and_then(Value::as_str)
            .unwrap_or("precision")
            .to_string();
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?
            .clone();
        let batch_job_id = format!("batch-{id}-{}", Uuid::new_v4());
        let (collection_name, items) = self.update_collection_store(|store| {
            let collection = find_collection_mut(store, id)
                .ok_or_else(|| format!("Collection {id} not found"))?;
            sync_collection_value_from_jobs(collection, &jobs);
            let collection_name = collection
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or("collection")
                .to_string();
            let collection_items = collection
                .get_mut("items")
                .and_then(Value::as_array_mut)
                .ok_or_else(|| "items must be an array".to_string())?;
            let mut claimed = Vec::new();
            for item in collection_items {
                let Some(item_id) = item.get("id").and_then(Value::as_u64) else {
                    continue;
                };
                let input = item
                    .get("input")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if input.is_empty() {
                    continue;
                }
                let run_id = item.get("run_id").and_then(Value::as_u64);
                let status = item.get("status").and_then(Value::as_str).unwrap_or("");
                if !collection_item_matches_batch_scope(status, run_id, &scope) {
                    continue;
                }
                claimed.push(CollectionBatchItem {
                    id: item_id,
                    input,
                    title: item
                        .get("title")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    compile_mode: mode.clone(),
                });
                item["status"] = json!("queued");
                item["progress"] = json!(0);
                item["batch_id"] = json!(batch_job_id.clone());
                if let Some(object) = item.as_object_mut() {
                    object.remove("run_id");
                    object.remove("job_id");
                    object.remove("error_message");
                    object.remove("output_path");
                }
            }
            if claimed.is_empty() {
                return Err(format!(
                    "collection has no processable items for scope '{scope}'"
                ));
            }
            collection["status"] = json!("processing");
            Ok((collection_name, claimed))
        })?;
        let max_concurrency = self.compile_scheduler.limit();
        let output_dir = collection_output_dir(
            &self.read_settings(),
            &self.default_export_dir,
            &collection_name,
            id,
        );
        let count = items.len();
        let runner = self.clone();
        let thread_batch_id = batch_job_id.clone();
        std::thread::spawn(move || {
            if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                runner.run_collection_batch(id, &thread_batch_id, items);
            }))
            .is_err()
            {
                let _ = runner.fail_collection_batch(id, &thread_batch_id, "批量处理线程异常退出");
            }
        });
        Ok(json!({
            "batch_job_id": batch_job_id,
            "run_ids": [],
            "count": count,
            "queued_count": count,
            "max_concurrency": max_concurrency,
            "output_dir": output_dir.to_string_lossy(),
        }))
    }

    fn collection_pause_all(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let items = detail
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut paused = 0u32;
        for item in &items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let status = item.get("status").and_then(Value::as_str).unwrap_or("");
            if let Some(run_id) = run_id {
                if matches!(status, "pending" | "running")
                    && self.process_pause(json!({ "job_id": run_id })).is_ok()
                {
                    paused += 1;
                }
            }
        }
        Ok(json!({ "paused": paused }))
    }

    fn collection_resume_all(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let items = detail
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut resumed = 0u32;
        for item in &items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let status = item.get("status").and_then(Value::as_str).unwrap_or("");
            if let Some(run_id) = run_id {
                if matches!(status, "pausing" | "paused")
                    && self.process_resume(json!({ "job_id": run_id })).is_ok()
                {
                    resumed += 1;
                }
            }
        }
        Ok(json!({ "resumed": resumed }))
    }

    fn collection_cancel_all(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let items = detail
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut cancelled = 0u32;
        for item in &items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let status = item.get("status").and_then(Value::as_str).unwrap_or("");
            if let Some(run_id) = run_id {
                if matches!(
                    status,
                    "pending" | "running" | "pausing" | "paused" | "cancelling"
                ) && self.process_cancel(json!({ "job_id": run_id })).is_ok()
                {
                    cancelled += 1;
                }
            }
        }
        let queued_cancelled = self.update_collection_store(|store| {
            let collection = find_collection_mut(store, id)
                .ok_or_else(|| format!("Collection {id} not found"))?;
            let mut count = 0u32;
            if let Some(items) = collection.get_mut("items").and_then(Value::as_array_mut) {
                for item in items {
                    if item.get("status").and_then(Value::as_str) == Some("queued") {
                        item["status"] = json!("cancelled");
                        item["progress"] = json!(100);
                        item["error_message"] = json!("用户取消了批量处理");
                        if let Some(object) = item.as_object_mut() {
                            object.remove("batch_id");
                        }
                        count += 1;
                    }
                }
            }
            collection["status"] = json!(aggregate_collection_status(collection));
            Ok(count)
        })?;
        Ok(json!({ "cancelled": cancelled + queued_cancelled }))
    }

    fn run_collection_batch(&self, id: u64, batch_id: &str, items: Vec<CollectionBatchItem>) {
        for item in &items {
            // Check if collection was deleted mid-batch
            {
                let Ok(store) = self.read_collection_store() else {
                    break;
                };
                let Some(collection) = find_collection(&store, id) else {
                    break;
                };
                let is_claimed = collection
                    .get("items")
                    .and_then(Value::as_array)
                    .and_then(|collection_items| {
                        collection_items
                            .iter()
                            .find(|value| value.get("id").and_then(Value::as_u64) == Some(item.id))
                    })
                    .and_then(|value| value.get("batch_id"))
                    .and_then(Value::as_str)
                    == Some(batch_id);
                if !is_claimed {
                    continue;
                }
            }
            let result = self.compile_video_with_collection(
                json!({
                    "input": item.input.clone(),
                    "title": item.title.clone(),
                    "mode": item.compile_mode.clone(),
                }),
                Some((id, item.id)),
                None,
            );
            match result {
                Ok(value) => {
                    let outcome = value
                        .get("job_id")
                        .and_then(Value::as_u64)
                        .ok_or_else(|| "batch compile did not return a job_id".to_string())
                        .and_then(|job_id| {
                            self.update_collection_item_from_job(id, item.id, job_id)
                        });
                    if let Err(error) = outcome {
                        let _ = self.update_collection_item_failed(id, item.id, &error);
                    }
                }
                Err(error) => {
                    let _ = self.update_collection_item_failed(id, item.id, &error);
                }
            }
        }
        let _ = self.refresh_collection_status(id);
    }

    fn fail_collection_batch(&self, id: u64, batch_id: &str, error: &str) -> Result<(), String> {
        self.update_collection_store(|store| {
            let collection = find_collection_mut(store, id)
                .ok_or_else(|| format!("Collection {id} not found"))?;
            if let Some(items) = collection.get_mut("items").and_then(Value::as_array_mut) {
                for item in items {
                    let matches_batch =
                        item.get("batch_id").and_then(Value::as_str) == Some(batch_id);
                    let status = item.get("status").and_then(Value::as_str).unwrap_or("");
                    if matches_batch && !is_terminal_status(status) {
                        item["status"] = json!("failed");
                        item["progress"] = json!(100);
                        item["error_message"] = json!(error);
                    }
                }
            }
            collection["status"] = json!(aggregate_collection_status(collection));
            Ok(())
        })
    }

    fn update_collection_item_start(
        &self,
        collection_id: u64,
        item_id: u64,
        run_id: u64,
    ) -> Result<(), String> {
        self.update_collection_item(collection_id, item_id, |item| {
            item["run_id"] = json!(run_id);
            item["status"] = json!("pending");
            item["progress"] = json!(0);
            if let Some(object) = item.as_object_mut() {
                object.remove("batch_id");
                object.remove("error_message");
                object.remove("output_path");
            }
        })
    }

    fn update_collection_item_from_job(
        &self,
        collection_id: u64,
        item_id: u64,
        job_id: u64,
    ) -> Result<(), String> {
        let job = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?
            .iter()
            .find(|job| job.id == job_id)
            .cloned()
            .ok_or_else(|| format!("Job {job_id} not found"))?;
        self.update_collection_item(collection_id, item_id, |item| {
            sync_collection_item_from_job(item, &job);
        })
    }

    fn update_collection_item_failed(
        &self,
        collection_id: u64,
        item_id: u64,
        error: &str,
    ) -> Result<(), String> {
        self.update_collection_item(collection_id, item_id, |item| {
            item["status"] = json!("failed");
            item["progress"] = json!(100);
            item["error_message"] = json!(error);
        })
    }

    fn update_collection_item<F>(
        &self,
        collection_id: u64,
        item_id: u64,
        update: F,
    ) -> Result<(), String>
    where
        F: FnOnce(&mut Value),
    {
        self.update_collection_store(|store| {
            let collection = find_collection_mut(store, collection_id)
                .ok_or_else(|| format!("Collection {collection_id} not found"))?;
            let items = collection
                .get_mut("items")
                .and_then(Value::as_array_mut)
                .ok_or_else(|| "items must be an array".to_string())?;
            let item = items
                .iter_mut()
                .find(|item| item.get("id").and_then(Value::as_u64) == Some(item_id))
                .ok_or_else(|| format!("Collection item {item_id} not found"))?;
            update(item);
            collection["status"] = json!(aggregate_collection_status(collection));
            Ok(())
        })
    }

    fn refresh_collection_status(&self, id: u64) -> Result<(), String> {
        self.update_collection_store(|store| {
            let collection = find_collection_mut(store, id)
                .ok_or_else(|| format!("Collection {id} not found"))?;
            collection["status"] = json!(aggregate_collection_status(collection));
            Ok(())
        })
    }

    fn read_collection_store(&self) -> Result<Map<String, Value>, String> {
        let _guard = self
            .collection_lock
            .lock()
            .map_err(|_| "collection lock poisoned".to_string())?;
        self.read_collection_store_unlocked()
    }

    fn read_collection_store_unlocked(&self) -> Result<Map<String, Value>, String> {
        let path = self.collection_store_path();
        if !path.exists() {
            let mut store = Map::new();
            store.insert("next_collection_id".to_string(), json!(1));
            store.insert("collections".to_string(), json!([]));
            return Ok(store);
        }
        let store = read_json_file(&path)?
            .as_object()
            .cloned()
            .ok_or_else(|| "collection store root must be an object".to_string())?;
        if !store.get("collections").is_some_and(Value::is_array) {
            return Err("collection store collections must be an array".to_string());
        }
        Ok(store)
    }

    fn update_collection_store<T, F>(&self, update: F) -> Result<T, String>
    where
        F: FnOnce(&mut Map<String, Value>) -> Result<T, String>,
    {
        let _guard = self
            .collection_lock
            .lock()
            .map_err(|_| "collection lock poisoned".to_string())?;
        let mut store = self.read_collection_store_unlocked()?;
        let result = update(&mut store)?;
        write_json_atomic(&self.collection_store_path(), &Value::Object(store))?;
        Ok(result)
    }

    fn collection_store_path(&self) -> PathBuf {
        self.data_dir.join("state").join("collections.json")
    }

    fn read_manifest(&self, component: &str) -> Result<Value, String> {
        // Reject path traversal attempts in component name.
        let safe = sanitize_component_name(component)?;
        read_json_file(&self.manifests_dir.join(format!("{safe}.json")))
            .or_else(|_| {
                default_component_manifest(&safe)
                    .ok_or_else(|| format!("manifest '{safe}' not found in bundled defaults"))
            })
            .map_err(|error| format!("manifest '{safe}' not found or invalid: {error}"))
    }

    fn component_manifests(&self) -> Result<Vec<Value>, String> {
        let mut manifests = Vec::new();
        let mut seen = HashSet::new();
        if self.manifests_dir.is_dir() {
            for entry in fs::read_dir(&self.manifests_dir).map_err(|error| error.to_string())? {
                let path = entry.map_err(|error| error.to_string())?.path();
                if path.extension().and_then(|value| value.to_str()) != Some("json") {
                    continue;
                }
                let Ok(manifest) = read_json_file(&path) else {
                    continue;
                };
                if let Some(component) = manifest.get("component").and_then(Value::as_str) {
                    seen.insert(component.to_string());
                    manifests.push(manifest);
                }
            }
        }
        for (component, manifest) in DEFAULT_COMPONENT_MANIFESTS {
            if seen.contains(*component) {
                continue;
            }
            manifests.push(
                serde_json::from_str::<Value>(manifest).map_err(|error| {
                    format!("bundled manifest '{component}' is invalid: {error}")
                })?,
            );
        }
        manifests.sort_by(|left, right| {
            left.get("component")
                .and_then(Value::as_str)
                .unwrap_or("")
                .cmp(right.get("component").and_then(Value::as_str).unwrap_or(""))
        });
        Ok(manifests)
    }

    fn read_settings_raw(&self) -> Map<String, Value> {
        read_json_file(&self.settings_path)
            .ok()
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_default()
    }

    fn read_settings(&self) -> Map<String, Value> {
        let mut raw = self.read_settings_raw();
        hydrate_provider_secrets(&mut raw);
        raw
    }

    fn write_settings(&self, mut raw: Map<String, Value>) -> Result<(), String> {
        strip_plaintext_provider_secrets(&mut raw);
        write_json_atomic(&self.settings_path, &Value::Object(raw))
    }

    fn update_settings<F, T>(&self, update: F) -> Result<T, String>
    where
        F: FnOnce(&mut Map<String, Value>) -> Result<T, String>,
    {
        let _guard = self
            .settings_lock
            .lock()
            .map_err(|_| "settings lock poisoned".to_string())?;
        let mut raw = self.read_settings_raw();
        let result = update(&mut raw)?;
        self.write_settings(raw)?;
        Ok(result)
    }

    fn allow_note_asset_root(&self, note_path: &Path) -> Result<(), String> {
        let Some(handle) = self.app_handle.as_ref() else {
            return Ok(());
        };
        let directory = note_path
            .parent()
            .ok_or_else(|| "note path has no parent directory".to_string())?;
        handle
            .asset_protocol_scope()
            .allow_directory(directory, true)
            .map_err(|error| error.to_string())
    }

    fn allow_configured_asset_roots(&self) -> Result<(), String> {
        let Some(handle) = self.app_handle.as_ref() else {
            return Ok(());
        };
        let settings = self.read_settings_raw();
        let mut roots = vec![self.default_export_dir.clone()];
        if let Some(path) = string_value(&settings, "output_dir") {
            roots.push(PathBuf::from(path));
        }
        if let Some(path) = string_value(&settings, "vault_path") {
            roots.push(PathBuf::from(path).join("video-notes"));
        }
        for root in roots {
            handle
                .asset_protocol_scope()
                .allow_directory(root, true)
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }

    /// Start a multimodal compile pipeline on a video file.
    fn compile_video(&self, params: Value) -> Result<Value, String> {
        self.compile_video_with_collection(params, None, None)
    }

    fn compile_video_with_collection(
        &self,
        params: Value,
        collection_binding: Option<(u64, u64)>,
        retry_context: Option<RetryContext>,
    ) -> Result<Value, String> {
        let input = crate::native_engine::required_string(&params, "input")?;
        let title = crate::native_engine::string_param(&params, "title")
            .or_else(|| {
                std::path::Path::new(&input)
                    .file_stem()
                    .and_then(|v| v.to_str())
                    .map(ToOwned::to_owned)
            })
            .unwrap_or_else(|| "Untitled".to_string());

        let settings = self.read_settings();
        let template = crate::native_engine::string_param(&params, "template")
            .or_else(|| crate::native_engine::string_value(&settings, "template"))
            .unwrap_or_else(|| "default".to_string());

        let settings = self.read_settings();
        let runtime_dir = self.runtime_dir.clone();

        // Resolve ffmpeg/ffprobe paths
        let ffmpeg =
            crate::native_engine::resolve_tool_path("ffmpeg", &["ffmpeg-tools"], &runtime_dir)
                .ok_or_else(|| "ffmpeg not found; install ffmpeg-tools".to_string())?;
        let ffprobe =
            crate::native_engine::resolve_tool_path("ffprobe", &["ffmpeg-tools"], &runtime_dir)
                .ok_or_else(|| "ffprobe not found".to_string())?;

        // Provider config
        let provider = crate::native_engine::active_provider_profile(&settings).ok();
        let client_config = provider.as_ref().map(|profile| {
            let mut config = crate::compile::client::CompileClientConfig::new(
                profile.base_url.clone(),
                profile.api_key.clone(),
                profile.vision_model.clone(),
                crate::compile::client::ProviderKind::from_profile(
                    &profile.provider_type,
                    &profile.base_url,
                ),
            );
            config.accepts_video = profile.accepts_video;
            config
        });

        let storage_dir = self.data_dir.join(".capsules");

        let input_is_url = is_http_url(&input);
        let ytdlp = if input_is_url {
            validate_public_media_url(&input)?;
            Some(
                crate::native_engine::resolve_tool_path(
                    "yt-dlp",
                    &["download-tools"],
                    &runtime_dir,
                )
                .ok_or_else(|| "yt-dlp not found; install download-tools".to_string())?,
            )
        } else {
            None
        };
        if !input_is_url {
            let local_path = std::path::Path::new(&input);
            if !local_path.is_file() {
                return Err(format!("input file not found: {input}"));
            }
            let size = local_path
                .metadata()
                .map_err(|error| format!("failed to inspect input file: {error}"))?
                .len();
            if size > MAX_MEDIA_BYTES {
                return Err(format!(
                    "input media exceeds the 8 GiB safety limit ({size} bytes)"
                ));
            }
        }
        let downloads_root = self.data_dir.join(".downloads");

        let prefer_draft = crate::native_engine::string_param(&params, "mode")
            .map(|m| m == "draft")
            .unwrap_or(false);
        let request_snapshot = json!({
            "input": input.clone(),
            "title": title.clone(),
            "template": template.clone(),
            "mode": if prefer_draft { "draft" } else { "precision" },
        });
        let (attempt, parent_run_id, inherited_collection_binding) = retry_context
            .map(|(attempt, parent_run_id, binding)| (attempt, Some(parent_run_id), binding))
            .unwrap_or((1, None, None));
        let collection_binding = collection_binding.or(inherited_collection_binding);
        let bilibili_cookie_file =
            if input_is_url && input.to_ascii_lowercase().contains("bilibili.com") {
                crate::native_engine::string_value(&settings, "bilibili_cookie_file")
                    .or_else(|| crate::native_engine::string_value(&settings, "bilibili_cookies"))
                    .map(std::path::PathBuf::from)
                    .filter(|path| !path.as_os_str().is_empty())
            } else {
                None
            };
        if let Some(path) = bilibili_cookie_file.as_ref() {
            if !path.is_file() {
                return Err(format!(
                    "Bilibili cookie file not found: {}",
                    path.display()
                ));
            }
        }

        // Create a NativeJob so the task appears in the task center
        let id = {
            let mut next = self
                .next_job_id
                .lock()
                .map_err(|_| "job id lock poisoned".to_string())?;
            let id = *next;
            *next += 1;
            id
        };
        let job = NativeJob {
            id,
            job_id: uuid::Uuid::new_v4().to_string(),
            title: Some(title.clone()),
            status: "pending".to_string(),
            progress: 0,
            progress_message: "等待运行名额".to_string(),
            stage: "pending".to_string(),
            input: input.clone(),
            created_at: chrono::Utc::now().to_rfc3339(),
            completed_at: None,
            error_message: None,
            output_path: None,
            transcript_path: None,
            can_resume: false,
            settings_snapshot: Some(request_snapshot),
            workspace_dir: None,
            attempt,
            parent_run_id,
            artifact_cleanup_policy: "keep_all".to_string(),
            note_id: None,
            collection_id: collection_binding.map(|(collection_id, _)| collection_id),
            collection_item_id: collection_binding.map(|(_, item_id)| item_id),
        };
        {
            let mut jobs = self
                .jobs
                .lock()
                .map_err(|_| "jobs lock poisoned".to_string())?;
            jobs.push(job);
            if let Err(error) = save_jobs(&self.jobs_state_path, &jobs) {
                jobs.retain(|j| j.id != id);
                return Err(error);
            }
        }
        if let Some((collection_id, collection_item_id)) = collection_binding {
            if let Err(error) =
                self.update_collection_item_start(collection_id, collection_item_id, id)
            {
                if let Ok(mut jobs) = self.jobs.lock() {
                    jobs.retain(|job| job.id != id);
                    let _ = save_jobs(&self.jobs_state_path, &jobs);
                }
                return Err(error);
            }
        }
        let job_control = Arc::new(JobControl::new());
        self.job_controls
            .lock()
            .map_err(|_| "job controls lock poisoned".to_string())?
            .insert(id, job_control.clone());

        // Spawn background compile thread
        let jobs = self.jobs.clone();
        let jobs_state_path = self.jobs_state_path.clone();
        let thread_input = input.clone();
        let thread_title = title.clone();
        let thread_ytdlp = ytdlp.clone();
        let thread_downloads_root = downloads_root.clone();
        let app_handle = self.app_handle.clone();
        // Resolve the effective export directory (vault path or default)
        let export_dir = effective_note_output_dir(&settings, &self.default_export_dir);
        let job_controls = self.job_controls.clone();
        let thread_control = job_control.clone();
        let thread_cookie_file = bilibili_cookie_file.clone();
        let compile_scheduler = self.compile_scheduler.clone();

        let thread_handle = std::thread::spawn(move || {
            let _active_job_guard = ActiveJobGuard {
                id,
                controls: job_controls,
            };
            let set_job = |status: &str, stage: &str, progress: u8, message: &str| {
                if let Ok(mut guard) = jobs.lock() {
                    if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                        job.status = status.to_string();
                        job.stage = stage.to_string();
                        job.progress = progress;
                        job.progress_message = message.to_string();
                        if status != "paused" && status != "pausing" {
                            job.can_resume = false;
                        }
                        if status == "completed"
                            || status == "failed"
                            || status == "cancelled"
                            || status == "interrupted"
                        {
                            job.completed_at = Some(chrono::Utc::now().to_rfc3339());
                        }
                    }
                    let _ = save_jobs(&jobs_state_path, &guard);
                }
                if let Some(ref handle) = app_handle {
                    let _ = handle.emit(
                        "job:progress",
                        json!({
                            "event_id": chrono::Utc::now().timestamp_millis(),
                            "job_id": id,
                            "stable_job_id": null,
                            "status": status,
                            "stage": stage,
                            "progress": progress,
                            "message": message,
                            "timestamp": chrono::Utc::now().to_rfc3339(),
                        }),
                    );
                }
            };

            let _compile_permit = match compile_scheduler.acquire(id, thread_control.as_ref()) {
                Ok(permit) => permit,
                Err(error) => {
                    if error == crate::compile::engine::COMPILE_CANCELLED_ERROR {
                        set_job("cancelled", "cancelled", 100, "任务已取消");
                    } else {
                        set_job("failed", "failed", 100, &error);
                        if let Ok(mut guard) = jobs.lock() {
                            if let Some(job) = guard.iter_mut().find(|job| job.id == id) {
                                job.error_message = Some(error);
                            }
                            let _ = save_jobs(&jobs_state_path, &guard);
                        }
                    }
                    return;
                }
            };

            if thread_control.cancel_requested.load(Ordering::SeqCst) {
                set_job("cancelled", "cancelled", 100, "任务已取消");
                return;
            }
            set_job("running", "resolving", 5, "检查输入文件");

            let mut downloaded_workspace: Option<std::path::PathBuf> = None;
            let resolved_input = if is_http_url(&thread_input) {
                let workspace = thread_downloads_root.join(format!("job-{id}"));
                set_job("running", "downloading", 8, "下载在线媒体");
                let Some(ytdlp) = thread_ytdlp.as_ref() else {
                    set_job("failed", "failed", 100, "yt-dlp 不可用");
                    return;
                };
                match download_public_media(
                    ytdlp,
                    &thread_input,
                    &workspace,
                    thread_cookie_file.as_deref(),
                    Some(thread_control.as_ref()),
                ) {
                    Ok(path) => {
                        downloaded_workspace = Some(workspace);
                        path
                    }
                    Err(error) => {
                        if error == crate::compile::engine::COMPILE_CANCELLED_ERROR {
                            set_job("cancelled", "cancelled", 100, "任务已取消");
                        } else {
                            set_job("failed", "failed", 100, &error);
                            if let Ok(mut guard) = jobs.lock() {
                                if let Some(job) = guard.iter_mut().find(|job| job.id == id) {
                                    job.error_message = Some(error);
                                }
                                let _ = save_jobs(&jobs_state_path, &guard);
                            }
                        }
                        return;
                    }
                }
            } else {
                std::path::PathBuf::from(&thread_input)
            };

            let resolved_source_hash = match crate::compile::storage::file_hash(&resolved_input) {
                Ok(hash) => hash,
                Err(error) => {
                    set_job("failed", "failed", 100, &error);
                    if let Some(workspace) = downloaded_workspace.take() {
                        let _ = std::fs::remove_dir_all(workspace);
                    }
                    return;
                }
            };

            let checkpoint_cb = {
                let control = thread_control.clone();
                let jobs = jobs.clone();
                let jobs_state_path = jobs_state_path.clone();
                let app_handle = app_handle.clone();
                move || -> Result<(), String> {
                    if control.cancel_requested.load(Ordering::SeqCst) {
                        return Err(crate::compile::engine::COMPILE_CANCELLED_ERROR.to_string());
                    }
                    if !control.pause_requested.load(Ordering::SeqCst) {
                        return Ok(());
                    }

                    let event = if let Ok(mut guard) = jobs.lock() {
                        let event = guard.iter_mut().find(|job| job.id == id).map(|job| {
                            job.status = "paused".to_string();
                            job.progress_message = "任务已暂停".to_string();
                            job.can_resume = true;
                            let stage = job.stage.clone();
                            let progress = job.progress;
                            job_progress_event(job, id, "paused", &stage, progress, "任务已暂停")
                        });
                        let _ = save_jobs(&jobs_state_path, &guard);
                        event
                    } else {
                        None
                    };
                    if let (Some(handle), Some(event)) = (app_handle.as_ref(), event) {
                        let _ = handle.emit("job:progress", event);
                    }

                    let mut wait_guard = control
                        .current_child
                        .lock()
                        .map_err(|_| "job control lock poisoned".to_string())?;
                    while control.pause_requested.load(Ordering::SeqCst)
                        && !control.cancel_requested.load(Ordering::SeqCst)
                    {
                        let result = control
                            .condvar
                            .wait_timeout(wait_guard, Duration::from_millis(250))
                            .map_err(|_| "job control wait poisoned".to_string())?;
                        wait_guard = result.0;
                    }
                    drop(wait_guard);

                    if control.cancel_requested.load(Ordering::SeqCst) {
                        return Err(crate::compile::engine::COMPILE_CANCELLED_ERROR.to_string());
                    }
                    Ok(())
                }
            };

            let progress_cb = {
                let jobs = jobs.clone();
                let jobs_state_path = jobs_state_path.clone();
                let app_handle = app_handle.clone();
                move |stage: &str, pct: u8, msg: &str| {
                    if let Ok(mut guard) = jobs.lock() {
                        if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                            job.stage = stage.to_string();
                            job.progress = pct;
                            job.progress_message = msg.to_string();
                        }
                        let _ = save_jobs(&jobs_state_path, &guard);
                    }
                    if let Some(ref handle) = app_handle {
                        let _ = handle.emit(
                            "job:progress",
                            json!({
                                "event_id": chrono::Utc::now().timestamp_millis(),
                                "job_id": id,
                                "stable_job_id": null,
                                "status": "running",
                                "stage": stage,
                                "progress": pct,
                                "message": msg,
                                "timestamp": chrono::Utc::now().to_rfc3339(),
                            }),
                        );
                    }
                }
            };

            let storage_dir_for_render = storage_dir.clone();
            let process_started_cb = {
                let control = thread_control.clone();
                move |pid: u32| {
                    if let Ok(mut current_child) = control.current_child.lock() {
                        *current_child = Some(pid);
                    }
                }
            };
            let process_finished_cb = {
                let control = thread_control.clone();
                move |pid: u32| {
                    if let Ok(mut current_child) = control.current_child.lock() {
                        if *current_child == Some(pid) {
                            *current_child = None;
                        }
                    }
                }
            };
            let opts = crate::compile::engine::CompileOptions {
                ffmpeg_path: ffmpeg,
                ffprobe_path: ffprobe,
                storage_dir,
                sampler: crate::compile::SamplerOptions::default(),
                client_config,
                prefer_draft,
                on_progress: Some(Box::new(progress_cb)),
                checkpoint: Some(Box::new(checkpoint_cb)),
                on_process_started: Some(Box::new(process_started_cb)),
                on_process_finished: Some(Box::new(process_finished_cb)),
            };

            let result = crate::compile::engine::compile_video(
                &resolved_input,
                &resolved_source_hash,
                &thread_title,
                &opts,
            );

            match result {
                Ok(compile_result) => {
                    if let Ok(mut guard) = jobs.lock() {
                        if let Some(_job) = guard.iter_mut().find(|job| job.id == id) {}
                        let _ = save_jobs(&jobs_state_path, &guard);
                    }
                    // Render capsule to markdown and write to export directory
                    let store = crate::compile::storage::FileCapsuleStore::new(
                        storage_dir_for_render.clone(),
                    );
                    if let Ok(mut capsule) =
                        store.get(&compile_result.source_hash, compile_result.version)
                    {
                        // Record the original source path in capsule for frontmatter
                        capsule.source_input = thread_input.clone();
                        match crate::compile::renderer::render(&capsule, &template) {
                            Ok(markdown) => {
                                let _ = std::fs::create_dir_all(&export_dir);
                                let safe_name: String = thread_title
                                    .chars()
                                    .map(|c| {
                                        if c.is_alphanumeric() || c == ' ' || c == '-' || c == '_' {
                                            c
                                        } else {
                                            '_'
                                        }
                                    })
                                    .collect();
                                let file_name =
                                    format!("{}-v{}.md", safe_name.trim(), compile_result.version);
                                let output_path = export_dir.join(&file_name);
                                if std::fs::write(&output_path, &markdown).is_ok() {
                                    if let Ok(mut guard) = jobs.lock() {
                                        if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                                            job.output_path =
                                                Some(output_path.to_string_lossy().to_string());
                                            job.note_id =
                                                Some(crate::native_engine::note_id(&output_path));
                                        }
                                        let _ = save_jobs(&jobs_state_path, &guard);
                                    }
                                }
                            }
                            Err(e) => {
                                eprintln!("render markdown failed: {e}");
                            }
                        }
                    }
                    set_job("completed", "complete", 100, "编译完成");
                }
                Err(error) => {
                    if error == crate::compile::engine::COMPILE_CANCELLED_ERROR {
                        set_job("cancelled", "cancelled", 100, "任务已取消");
                    } else {
                        set_job("failed", "failed", 100, &error);
                        if let Ok(mut guard) = jobs.lock() {
                            if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                                job.error_message = Some(error);
                            }
                            let _ = save_jobs(&jobs_state_path, &guard);
                        }
                    }
                }
            }

            if let Some(workspace) = downloaded_workspace {
                let _ = std::fs::remove_dir_all(workspace);
            }
        });

        // If sync mode, wait for the compile thread to complete
        if params.get("sync").and_then(Value::as_bool).unwrap_or(false) {
            let _ = thread_handle.join();
        }

        Ok(json!({ "job_id": id }))
    }

    /// List all compiled versions for a source hash.
    fn compile_list_versions(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let versions = store
            .list_versions(&source_hash)
            .map_err(|e| format!("list versions failed: {e}"))?;
        Ok(serde_json::to_value(&versions).unwrap_or_default())
    }

    /// Replay a specific compiled version.
    fn compile_replay(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let version = params
            .get("version")
            .and_then(Value::as_u64)
            .ok_or_else(|| "version is required".to_string())? as u32;
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let capsule = store
            .get(&source_hash, version)
            .map_err(|e| format!("replay failed: {e}"))?;
        Ok(serde_json::to_value(&capsule).unwrap_or_default())
    }

    /// Render a compiled capsule to Markdown / mindmap.
    fn compile_render(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let version = params
            .get("version")
            .and_then(Value::as_u64)
            .ok_or_else(|| "version is required".to_string())? as u32;
        let template = crate::native_engine::string_param(&params, "template")
            .unwrap_or_else(|| "markdown".to_string());
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let capsule = store
            .get(&source_hash, version)
            .map_err(|e| format!("render: capsule not found: {e}"))?;
        let output = crate::compile::renderer::render(&capsule, &template)
            .map_err(|e| format!("render failed: {e}"))?;
        Ok(
            serde_json::json!({ "content": output, "capsule_id": capsule.capsule_id, "template": template }),
        )
    }
}

impl NativeJob {
    fn to_value(&self) -> Value {
        let elapsed_sec = job_elapsed_sec(&self.created_at, self.completed_at.as_deref());
        json!({
            "id": self.id,
            "job_id": self.job_id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "stage": self.stage,
            "last_active_stage": self.stage,
            "input": self.input,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "elapsed_sec": elapsed_sec,
            "error_message": self.error_message,
            "output_path": self.output_path,
            "transcript_path": self.transcript_path,
            "note_id": self.note_id,
            "collection_id": self.collection_id,
            "collection_item_id": self.collection_item_id,
            "settings_snapshot": self.settings_snapshot,
            "workspace_dir": self.workspace_dir,
            "attempt": self.attempt,
            "parent_run_id": self.parent_run_id,
            "artifact_cleanup_policy": self.artifact_cleanup_policy,
            "can_resume": self.can_resume,
            "heartbeat_at": Utc::now().to_rfc3339(),
        })
    }
}

fn configured_compile_concurrency(settings: &Map<String, Value>) -> u64 {
    settings
        .get("compile_concurrency")
        .and_then(Value::as_u64)
        .filter(|value| *value <= MAX_COMPILE_CONCURRENCY as u64)
        .unwrap_or(0)
}

fn effective_compile_concurrency(configured: u64) -> usize {
    match configured {
        1..=4 => configured as usize,
        _ => SMART_COMPILE_CONCURRENCY,
    }
}

fn validate_compile_concurrency(value: &Value) -> Result<u64, String> {
    value
        .as_u64()
        .filter(|configured| *configured <= MAX_COMPILE_CONCURRENCY as u64)
        .ok_or_else(|| "compile_concurrency must be an integer from 0 (smart) to 4".to_string())
}

fn compile_concurrency_from_path(settings_path: &Path) -> usize {
    let configured = read_json_file(settings_path)
        .ok()
        .and_then(|value| value.as_object().cloned())
        .map(|settings| configured_compile_concurrency(&settings))
        .unwrap_or(0);
    effective_compile_concurrency(configured)
}

fn jobs_state_path(data_dir: &Path) -> PathBuf {
    data_dir.join(".jobs").join("jobs.json")
}

fn load_jobs(jobs_state_path: &Path) -> Vec<NativeJob> {
    if !jobs_state_path.exists() {
        return Vec::new();
    }
    const MAX_JOBS_STATE_BYTES: u64 = 16 * 1024 * 1024;
    let metadata = match fs::metadata(jobs_state_path) {
        Ok(metadata) => metadata,
        Err(error) => {
            eprintln!("[error] Could not inspect jobs state: {error}");
            return Vec::new();
        }
    };
    if metadata.len() > MAX_JOBS_STATE_BYTES {
        quarantine_corrupt_jobs_state(
            jobs_state_path,
            &format!("state exceeds {MAX_JOBS_STATE_BYTES} bytes"),
        );
        return Vec::new();
    }
    let contents = match fs::read_to_string(jobs_state_path) {
        Ok(contents) => contents,
        Err(error) => {
            eprintln!("[error] Could not read jobs state: {error}");
            return Vec::new();
        }
    };
    let mut jobs = match serde_json::from_str::<Vec<NativeJob>>(&contents) {
        Ok(jobs) => jobs,
        Err(error) => {
            quarantine_corrupt_jobs_state(jobs_state_path, &error.to_string());
            return Vec::new();
        }
    };
    let now = Utc::now().to_rfc3339();
    let mut changed = false;
    for job in &mut jobs {
        if matches!(
            job.status.as_str(),
            "pending" | "running" | "pausing" | "cancelling"
        ) {
            job.status = "interrupted".to_string();
            job.stage = "interrupted".to_string();
            job.progress_message = format!("应用重启时任务中断（进度 {}%）", job.progress);
            job.completed_at = Some(now.clone());
            job.can_resume = false;
            changed = true;
        } else if job.status == "paused" {
            // Keep paused jobs as paused after restart — user can resume
            if !job.can_resume {
                job.can_resume = true;
                changed = true;
            }
            if !job.progress_message.contains("重启") {
                job.progress_message = format!("{}（应用重启后保持暂停）", job.progress_message);
                changed = true;
            }
        }
    }
    if changed {
        let _ = save_jobs(jobs_state_path, &jobs);
    }
    jobs
}

fn quarantine_corrupt_jobs_state(path: &Path, reason: &str) {
    let backup = path.with_file_name(format!(
        "jobs.corrupt-{}-{}.json",
        Utc::now().timestamp_millis(),
        Uuid::new_v4()
    ));
    match fs::rename(path, &backup) {
        Ok(()) => eprintln!(
            "[error] Quarantined invalid jobs state to {}: {reason}",
            backup.display()
        ),
        Err(error) => eprintln!(
            "[error] Jobs state is invalid ({reason}) and could not be quarantined: {error}"
        ),
    }
}

static JOBS_SAVE_STATE: std::sync::LazyLock<
    std::sync::Mutex<std::collections::HashMap<PathBuf, (std::time::Instant, u64)>>,
> = std::sync::LazyLock::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

fn save_jobs(jobs_state_path: &Path, jobs: &[NativeJob]) -> Result<(), String> {
    let signature = jobs_persistence_signature(jobs);
    if let Ok(guard) = JOBS_SAVE_STATE.lock() {
        if let Some((last, previous_signature)) = guard.get(jobs_state_path) {
            if *previous_signature == signature && last.elapsed() < Duration::from_secs(2) {
                return Ok(());
            }
        }
    }
    let result = write_json_atomic(jobs_state_path, &json!(jobs));
    if result.is_ok() {
        if let Ok(mut guard) = JOBS_SAVE_STATE.lock() {
            guard.insert(
                jobs_state_path.to_path_buf(),
                (std::time::Instant::now(), signature),
            );
        }
    }
    result
}

fn jobs_persistence_signature(jobs: &[NativeJob]) -> u64 {
    let mut hasher = DefaultHasher::new();
    jobs.len().hash(&mut hasher);
    for job in jobs {
        job.id.hash(&mut hasher);
        job.job_id.hash(&mut hasher);
        job.status.hash(&mut hasher);
        job.stage.hash(&mut hasher);
        job.completed_at.hash(&mut hasher);
        job.error_message.hash(&mut hasher);
        job.output_path.hash(&mut hasher);
        job.transcript_path.hash(&mut hasher);
        job.note_id.hash(&mut hasher);
        job.collection_id.hash(&mut hasher);
        job.collection_item_id.hash(&mut hasher);
    }
    hasher.finish()
}

fn default_job_attempt() -> u32 {
    1
}

fn default_artifact_cleanup_policy() -> String {
    "keep_all".to_string()
}

fn cleanup_deleted_job_workspace(job: &NativeJob, data_dir: &Path) {
    // Skip if workspace_dir is not set (e.g. local file compilation)
    let Some(workspace_dir) = &job.workspace_dir else {
        // Fallback: try the downloads directory by job ID
        let downloads_dir = data_dir.join(".downloads").join(format!("job-{}", job.id));
        if downloads_dir.is_dir() {
            let _ = std::fs::remove_dir_all(&downloads_dir);
        }
        return;
    };
    let workspace_path = PathBuf::from(workspace_dir);
    if workspace_path.as_os_str().is_empty() || workspace_path.parent().is_none() {
        return;
    }
    let Some(name) = workspace_path.file_name().and_then(|value| value.to_str()) else {
        return;
    };
    if !name.starts_with("job-") {
        return;
    }
    let Ok(workspace_path) = workspace_path.canonicalize() else {
        return;
    };
    let allowed_roots = [data_dir.join("jobs"), data_dir.join(".jobs")];
    for root in allowed_roots {
        let Ok(root) = root.canonicalize() else {
            continue;
        };
        if workspace_path.parent() == Some(root.as_path()) {
            let _ = fs::remove_dir_all(&workspace_path);
            return;
        }
    }
}

fn sanitize_capability_cache_text(value: &str) -> String {
    let mut text = value.chars().take(300).collect::<String>();
    for marker in [
        "Bearer ",
        "sk-",
        "api_key",
        "Authorization",
        "token",
        "secret",
    ] {
        while let Some(index) = text.to_ascii_lowercase().find(&marker.to_ascii_lowercase()) {
            let start = (index + marker.len()).min(text.len());
            let end = text[start..]
                .find(|ch: char| ch.is_whitespace() || ch == ',' || ch == '}' || ch == '"')
                .map(|offset| start + offset)
                .unwrap_or_else(|| text.len());
            text.replace_range(index..end, "[redacted]");
        }
    }
    text
}

fn job_id_param(params: &Value) -> Result<u64, String> {
    params
        .get("job_id")
        .or_else(|| params.get("id"))
        .and_then(Value::as_u64)
        .ok_or_else(|| "job_id is required".to_string())
}

fn is_active_status(status: &str) -> bool {
    matches!(
        status,
        "pending" | "running" | "pausing" | "cancelling" | "paused"
    )
}

fn is_terminal_status(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "cancelled" | "interrupted")
}

fn is_retryable_status(status: &str) -> bool {
    matches!(status, "failed" | "cancelled" | "interrupted")
}

fn collection_item_matches_batch_scope(status: &str, run_id: Option<u64>, scope: &str) -> bool {
    match scope {
        "pending" => status == "pending" && run_id.is_none(),
        "failed" => is_retryable_status(status),
        _ => false,
    }
}

fn next_job_id(jobs: &[NativeJob]) -> u64 {
    jobs.iter().map(|job| job.id).max().unwrap_or(0) + 1
}

fn job_elapsed_sec(created_at: &str, completed_at: Option<&str>) -> Option<u64> {
    let start = DateTime::parse_from_rfc3339(created_at).ok()?;
    let end = completed_at
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .unwrap_or_else(|| Utc::now().with_timezone(start.offset()));
    Some((end - start).num_seconds().max(0) as u64)
}

fn job_progress_event(
    job: &NativeJob,
    id: u64,
    status: &str,
    stage: &str,
    progress: u8,
    message: &str,
) -> Value {
    json!({
        "event_id": Utc::now().timestamp_millis(),
        "job_id": id,
        "stable_job_id": job.job_id,
        "status": status,
        "stage": stage,
        "progress": progress,
        "message": message,
        "completed_at": job.completed_at,
        "error_message": job.error_message,
        "output_path": job.output_path,
        "transcript_path": job.transcript_path,
        "note_id": job.note_id,
        "can_resume": job.can_resume,
        "collection_id": job.collection_id,
        "collection_item_id": job.collection_item_id,
        "timestamp": Utc::now().to_rfc3339(),
    })
}

fn local_app_data_dir(app_handle: &AppHandle) -> PathBuf {
    if let Some(base) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(base).join("Video Notes AI");
    }
    app_handle
        .path()
        .app_local_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("Video Notes AI"))
}

fn persistent_settings_path(app_handle: &AppHandle, data_dir: &Path) -> PathBuf {
    if let Some(base) = std::env::var_os("APPDATA") {
        return PathBuf::from(base)
            .join("Video Notes AI")
            .join("settings.json");
    }
    app_handle
        .path()
        .app_config_dir()
        .unwrap_or_else(|_| data_dir.join("config"))
        .join("settings.json")
}

fn default_export_dir(app_handle: &AppHandle) -> PathBuf {
    if let Ok(user_profile) = std::env::var("USERPROFILE") {
        if !user_profile.trim().is_empty() {
            return PathBuf::from(user_profile)
                .join("Documents")
                .join("Video Notes AI")
                .join("exports");
        }
    }
    app_handle
        .path()
        .document_dir()
        .unwrap_or_else(|_| std::env::temp_dir())
        .join("Video Notes AI")
        .join("exports")
}

fn effective_note_output_dir(settings: &Map<String, Value>, default_export_dir: &Path) -> PathBuf {
    if let Some(vault_path) = string_value(settings, "vault_path") {
        if !vault_path.trim().is_empty() {
            return PathBuf::from(vault_path).join("video-notes");
        }
    }
    string_value(settings, "output_dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| default_export_dir.to_path_buf())
}

fn collection_output_dir(
    settings: &Map<String, Value>,
    default_export_dir: &Path,
    collection_name: &str,
    collection_id: u64,
) -> PathBuf {
    let name = if collection_name.trim().is_empty() {
        "collection"
    } else {
        collection_name
    };
    effective_note_output_dir(settings, default_export_dir)
        .join("collections")
        .join(format!("{}-{collection_id}", sanitize_filename(name)))
}

fn project_root() -> Option<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let candidate = manifest_dir.join("..").join("..");
    if candidate.join("runtime").join("manifests").is_dir() {
        return Some(candidate);
    }
    None
}

fn read_json_file(path: &Path) -> Result<Value, String> {
    let text = fs::read_to_string(path).map_err(|error| error.to_string())?;
    serde_json::from_str(&text).map_err(|error| error.to_string())
}

fn write_json_atomic(path: &Path, value: &Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("state.json");
    let temp = path.with_file_name(format!(".{file_name}.{}.tmp", Uuid::new_v4()));
    let mut file = fs::File::create(&temp).map_err(|error| error.to_string())?;
    let body = serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?;
    file.write_all(&body).map_err(|error| error.to_string())?;
    file.write_all(b"\n").map_err(|error| error.to_string())?;
    file.sync_all().map_err(|error| error.to_string())?;
    drop(file);
    fs::rename(&temp, path).map_err(|error| {
        let _ = fs::remove_file(&temp);
        error.to_string()
    })
}

fn string_value(raw: &Map<String, Value>, key: &str) -> Option<String> {
    raw.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn string_param(params: &Value, key: &str) -> Option<String> {
    params
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn required_string(params: &Value, key: &str) -> Result<String, String> {
    string_param(params, key).ok_or_else(|| format!("{key} is required"))
}

fn bearer_token(value: &str) -> String {
    let trimmed = value.trim();
    if trimmed
        .get(..7)
        .map(|prefix| prefix.eq_ignore_ascii_case("bearer "))
        .unwrap_or(false)
    {
        trimmed[7..].trim().to_string()
    } else {
        trimmed.to_string()
    }
}

pub(crate) fn with_optional_bearer(
    request: reqwest::blocking::RequestBuilder,
    api_key: &str,
) -> reqwest::blocking::RequestBuilder {
    let token = bearer_token(api_key);
    if token.is_empty() {
        request
    } else {
        request.bearer_auth(token)
    }
}

fn required_u64(params: &Value, key: &str) -> Result<u64, String> {
    params
        .get(key)
        .or_else(|| params.get("collection_id"))
        .and_then(|value| {
            value
                .as_u64()
                .or_else(|| value.as_str().and_then(|text| text.parse::<u64>().ok()))
        })
        .ok_or_else(|| format!("{key} is required"))
}

fn provider_type_supports_audio(value: &str) -> bool {
    matches!(
        value,
        "openai_compat" | "openai" | "mimo" | "xiaomi_mimo" | "dashscope" | "google_gemini"
    )
}

fn normalise_provider_type(value: Option<&str>) -> String {
    match value.unwrap_or("openai_compat").trim() {
        "mimo" | "dashscope" | "openai" | "自定义" | "custom" => "openai_compat".to_string(),
        "llama" | "llama.cpp" | "llama_cpp" => "llama_cpp".to_string(),
        other if !other.is_empty() => other.to_string(),
        _ => "openai_compat".to_string(),
    }
}

fn normalise_provider_base_url(base_url: &str) -> String {
    let mut url = base_url.trim().trim_end_matches('/').to_string();
    for suffix in ["/chat/completions", "/responses", "/models"] {
        if url.ends_with(suffix) {
            let len = url.len() - suffix.len();
            url.truncate(len);
            break;
        }
    }
    if url == "http://127.0.0.1:8080" || url == "http://localhost:8080" {
        url.push_str("/v1");
    }
    url
}

fn clean_models(values: Vec<String>) -> Vec<String> {
    let mut result = Vec::new();
    for value in values {
        let value = value.trim();
        if !value.is_empty() && !result.iter().any(|item: &String| item == value) {
            result.push(value.to_string());
        }
    }
    result
}

fn provider_supports_video_input(provider_type: &str, base_url: &str) -> bool {
    let endpoint = base_url.to_ascii_lowercase();
    matches!(provider_type, "mimo" | "xiaomi_mimo" | "dashscope")
        || endpoint.contains("xiaomimimo.com")
        || endpoint.contains("dashscope.aliyuncs.com")
}

const PROVIDER_CREDENTIAL_SERVICE: &str = "com.videonotesai.desktop.provider";

fn credential_account(provider: &str) -> String {
    use sha2::{Digest, Sha256};
    format!(
        "provider-{:x}",
        Sha256::digest(provider.trim().to_lowercase().as_bytes())
    )
}

#[cfg(target_os = "windows")]
fn credential_entry(provider: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(PROVIDER_CREDENTIAL_SERVICE, &credential_account(provider))
        .map_err(|error| format!("credential vault unavailable: {error}"))
}

fn credential_store(provider: &str, secret: &str) -> Result<(), String> {
    if secret.trim().is_empty() {
        return Err("credential must not be empty".to_string());
    }
    #[cfg(target_os = "windows")]
    {
        credential_entry(provider)?
            .set_password(secret)
            .map_err(|error| format!("failed to store credential in OS vault: {error}"))
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = provider;
        Err("OS credential vault is not available on this platform build".to_string())
    }
}

fn credential_load(provider: &str) -> Result<Option<String>, String> {
    #[cfg(target_os = "windows")]
    {
        match credential_entry(provider)?.get_password() {
            Ok(secret) => Ok(Some(secret)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(format!("failed to read credential from OS vault: {error}")),
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = provider;
        Ok(None)
    }
}

fn credential_delete(provider: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        match credential_entry(provider)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(format!(
                "failed to delete credential from OS vault: {error}"
            )),
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = provider;
        Ok(())
    }
}

fn strip_plaintext_provider_secrets(raw: &mut Map<String, Value>) {
    let Some(providers) = raw.get_mut("providers").and_then(Value::as_array_mut) else {
        return;
    };
    for provider in providers {
        if let Some(object) = provider.as_object_mut() {
            object.remove("api_key");
        }
    }
}

fn hydrate_provider_secrets(raw: &mut Map<String, Value>) {
    let Some(providers) = raw.get_mut("providers").and_then(Value::as_array_mut) else {
        return;
    };
    for provider in providers {
        let Some(object) = provider.as_object_mut() else {
            continue;
        };
        if object.get("api_key").and_then(Value::as_str).is_some() {
            continue;
        }
        let Some(name) = object.get("name").and_then(Value::as_str) else {
            continue;
        };
        match credential_load(name) {
            Ok(Some(secret)) => {
                object.insert("api_key".to_string(), json!(secret));
                object.insert("credential_vault".to_string(), json!(true));
            }
            Ok(None) => {}
            Err(error) => eprintln!("[warn] {error}"),
        }
    }
}

fn migrate_plaintext_provider_secrets(settings_path: &Path) -> Result<(), String> {
    if !settings_path.is_file() {
        return Ok(());
    }
    let mut raw = read_json_file(settings_path)?
        .as_object()
        .cloned()
        .ok_or_else(|| "settings root must be an object".to_string())?;
    let Some(providers) = raw.get_mut("providers").and_then(Value::as_array_mut) else {
        return Ok(());
    };
    let mut changed = false;
    for provider in providers {
        let Some(object) = provider.as_object_mut() else {
            continue;
        };
        let Some(name) = object
            .get("name")
            .and_then(Value::as_str)
            .map(ToOwned::to_owned)
        else {
            continue;
        };
        let Some(secret) = object
            .get("api_key")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .map(ToOwned::to_owned)
        else {
            object.remove("api_key");
            continue;
        };
        credential_store(&name, &secret)?;
        object.remove("api_key");
        object.insert("credential_vault".to_string(), json!(true));
        changed = true;
    }
    if changed {
        write_json_atomic(settings_path, &Value::Object(raw))?;
    }
    Ok(())
}

fn api_key_preview(api_key: &str) -> String {
    let chars = api_key.chars().collect::<Vec<_>>();
    if chars.len() <= 8 {
        return String::new();
    }
    let prefix = chars.iter().take(4).collect::<String>();
    let suffix = chars.iter().rev().take(4).rev().collect::<String>();
    format!("{prefix}…{suffix}")
}

fn provider_profiles(raw: &Map<String, Value>, active: &str) -> Vec<Value> {
    raw.get("providers")
        .and_then(Value::as_array)
        .map(|providers| {
            providers
                .iter()
                .filter_map(|profile| {
                    let object = profile.as_object()?;
                    let name = object.get("name").and_then(Value::as_str).unwrap_or("");
                    if name.trim().is_empty() {
                        return None;
                    }
                    let api_key = object.get("api_key").and_then(Value::as_str).unwrap_or("");
                    let models = object.get("models").cloned().unwrap_or_else(|| json!([]));
                    let model = object
                        .get("model")
                        .and_then(Value::as_str)
                        .or_else(|| models.as_array()?.first()?.as_str())
                        .unwrap_or("");
                    let audio_input_value = {
                        let provider_type = object.get("type").and_then(Value::as_str).unwrap_or("openai_compat");
                        let requested = object.get("audio_input").and_then(Value::as_bool)
                            .unwrap_or(matches!(provider_type, "google_gemini" | "mimo"));
                        requested && provider_type_supports_audio(provider_type)
                    };
                    let provider_type = object
                        .get("type")
                        .and_then(Value::as_str)
                        .unwrap_or("openai_compat");
                    let base_url = object
                        .get("base_url")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    let video_input_value = object
                        .get("video_input")
                        .and_then(Value::as_bool)
                        .unwrap_or_else(|| provider_supports_video_input(provider_type, base_url));
                    Some(json!({
                        "name": name,
                        "provider": object.get("type").and_then(Value::as_str).unwrap_or("openai_compat"),
                        "api_key_configured": !api_key.trim().is_empty(),
                        "api_key_preview": api_key_preview(api_key),
                        "base_url": object.get("base_url").and_then(Value::as_str).unwrap_or(""),
                        "model": model,
                        "vision_model": object.get("vision_model").and_then(Value::as_str).unwrap_or(model),
                        "models": models,
                        "active": name.eq_ignore_ascii_case(active),
                        "capabilities": object.get("capabilities").cloned().unwrap_or_else(|| json!({})),
                        "audio_input": audio_input_value,
                        "video_input": video_input_value,
                        "max_frames_per_request": object.get("max_frames_per_request").and_then(Value::as_u64).unwrap_or(8),
                    }))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn provider_profile_for_request(
    settings: &Map<String, Value>,
    params: &Value,
) -> Result<NativeProviderProfile, String> {
    if let Some(name) = string_param(params, "name") {
        if let Some(profile) = find_provider(settings, &name) {
            return provider_from_saved_value(profile, params);
        }
    }
    let mut merged = Map::new();
    for key in [
        "provider",
        "type",
        "base_url",
        "model",
        "vision_model",
        "api_key",
        "audio_input",
        "video_input",
        "max_frames_per_request",
    ] {
        if let Some(value) = params.get(key) {
            merged.insert(key.to_string(), value.clone());
        }
    }
    if !merged.is_empty() {
        return provider_from_map(&merged);
    }
    active_provider_profile(settings)
}

fn capability_cache_provider_name(settings: &Map<String, Value>, params: &Value) -> Option<String> {
    if let Some(name) = string_param(params, "name") {
        if find_provider(settings, &name).is_some() {
            return Some(name);
        }
    }
    let has_ad_hoc_fields = ["base_url", "api_key", "type", "provider"]
        .iter()
        .any(|key| string_param(params, key).is_some());
    if has_ad_hoc_fields {
        None
    } else {
        string_value(settings, "active_provider")
            .filter(|name| find_provider(settings, name).is_some())
    }
}

pub(crate) fn active_provider_profile(
    settings: &Map<String, Value>,
) -> Result<NativeProviderProfile, String> {
    let active = string_value(settings, "active_provider").ok_or_else(|| {
        "No active provider configured. Set an AI provider in Settings.".to_string()
    })?;
    let profile = find_provider(settings, &active)
        .ok_or_else(|| format!("Active provider '{active}' not found"))?;
    provider_from_value(profile, &Value::Null)
}

fn provider_from_value(
    profile: &Map<String, Value>,
    override_params: &Value,
) -> Result<NativeProviderProfile, String> {
    let mut merged = profile.clone();
    for key in [
        "provider",
        "type",
        "base_url",
        "model",
        "vision_model",
        "api_key",
        "audio_input",
        "video_input",
        "max_frames_per_request",
    ] {
        if let Some(value) = override_params.get(key) {
            if !value.is_null() {
                merged.insert(key.to_string(), value.clone());
            }
        }
    }
    provider_from_map(&merged)
}

fn provider_from_saved_value(
    profile: &Map<String, Value>,
    override_params: &Value,
) -> Result<NativeProviderProfile, String> {
    let mut merged = profile.clone();
    for key in ["model", "vision_model"] {
        if let Some(value) = override_params.get(key) {
            if !value.is_null() {
                merged.insert(key.to_string(), value.clone());
            }
        }
    }
    provider_from_map(&merged)
}

fn provider_from_map(profile: &Map<String, Value>) -> Result<NativeProviderProfile, String> {
    let provider_type = profile
        .get("provider")
        .or_else(|| profile.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("openai_compat")
        .to_string();
    let default_base_url = match provider_type.as_str() {
        "google_gemini" => "https://generativelanguage.googleapis.com/v1beta",
        "anthropic_messages" => "https://api.anthropic.com/v1",
        _ => "https://api.openai.com/v1",
    };
    let base_url = profile
        .get("base_url")
        .and_then(Value::as_str)
        .unwrap_or(default_base_url)
        .trim()
        .trim_end_matches('/')
        .to_string();
    let model = profile
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let vision_model = profile
        .get("vision_model")
        .and_then(Value::as_str)
        .unwrap_or(&model)
        .trim()
        .to_string();
    let api_key = profile
        .get("api_key")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if !matches!(
        provider_type.as_str(),
        "openai_compat"
            | "openai"
            | "openai_responses"
            | "google_gemini"
            | "anthropic_messages"
            | "dashscope"
            | "mimo"
            | "xiaomi_mimo"
            | "llama_cpp"
    ) {
        return Err(format!("Unsupported provider type '{provider_type}'"));
    }
    // Generic OpenAI-compatible endpoints do not guarantee `video_url`.
    // Require an explicit capability flag, with a conservative default only
    // for endpoints whose video contract is known.
    let accepts_video = profile
        .get("video_input")
        .and_then(Value::as_bool)
        .unwrap_or_else(|| provider_supports_video_input(&provider_type, &base_url));
    Ok(NativeProviderProfile {
        provider_type,
        base_url: if base_url.is_empty() {
            default_base_url.to_string()
        } else {
            normalise_provider_base_url(&base_url)
        },
        api_key,
        model,
        vision_model,
        accepts_video,
    })
}

fn fetch_provider_models(profile: &NativeProviderProfile) -> Result<Vec<String>, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|error| error.to_string())?;
    let url = if profile.provider_type == "anthropic_messages" {
        let base = profile.base_url.trim_end_matches('/');
        let root = if base.ends_with("/v1") { &base[..base.len() - 3] } else { base };
        format!("{root}/v1/models")
    } else {
        format!("{}/models", profile.base_url.trim_end_matches('/'))
    };
    let request = match profile.provider_type.as_str() {
        "google_gemini" => client
            .get(url)
            .header("x-goog-api-key", profile.api_key.as_str()),
        "anthropic_messages" => client
            .get(url)
            .header("x-api-key", profile.api_key.as_str())
            .header("anthropic-version", "2023-06-01"),
        _ => with_optional_bearer(client.get(url), &profile.api_key),
    };
    let response = request.send().map_err(|error| error.to_string())?;
    if !response.status().is_success() {
        return Err(format!("models endpoint returned {}", response.status()));
    }
    let payload: Value = response.json().map_err(|error| error.to_string())?;
    let models = if profile.provider_type == "google_gemini" {
        payload
            .get("models")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("name").and_then(Value::as_str))
                    .map(|name| name.strip_prefix("models/").unwrap_or(name).to_string())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default()
    } else {
        payload
            .get("data")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("id").and_then(Value::as_str))
                    .map(ToOwned::to_owned)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default()
    };
    if models.is_empty() {
        Err("models endpoint returned no model ids".to_string())
    } else {
        Ok(models)
    }
}

fn find_provider<'a>(raw: &'a Map<String, Value>, name: &str) -> Option<&'a Map<String, Value>> {
    raw.get("providers")?
        .as_array()?
        .iter()
        .filter_map(Value::as_object)
        .find(|profile| {
            profile
                .get("name")
                .and_then(Value::as_str)
                .map(|value| value.eq_ignore_ascii_case(name))
                .unwrap_or(false)
        })
}

fn find_provider_mut<'a>(
    raw: &'a mut Map<String, Value>,
    name: &str,
) -> Result<&'a mut Map<String, Value>, String> {
    let providers = raw
        .entry("providers".to_string())
        .or_insert_with(|| json!([]))
        .as_array_mut()
        .ok_or_else(|| "providers must be an array".to_string())?;
    for profile in providers {
        let Some(object) = profile.as_object_mut() else {
            continue;
        };
        if object
            .get("name")
            .and_then(Value::as_str)
            .map(|value| value.eq_ignore_ascii_case(name))
            .unwrap_or(false)
        {
            return Ok(object);
        }
    }
    Err(format!("Provider '{name}' not found"))
}

fn next_store_id(store: &mut Map<String, Value>, key: &str) -> u64 {
    let next = store.get(key).and_then(Value::as_u64).unwrap_or(1);
    store.insert(key.to_string(), json!(next + 1));
    next
}

fn find_collection(store: &Map<String, Value>, id: u64) -> Option<&Value> {
    store
        .get("collections")?
        .as_array()?
        .iter()
        .find(|collection| collection.get("id").and_then(Value::as_u64) == Some(id))
}

fn find_collection_mut(store: &mut Map<String, Value>, id: u64) -> Option<&mut Value> {
    store
        .get_mut("collections")?
        .as_array_mut()?
        .iter_mut()
        .find(|collection| collection.get("id").and_then(Value::as_u64) == Some(id))
}

fn sync_collection_value_from_jobs(collection: &mut Value, jobs: &[NativeJob]) {
    let collection_id = collection.get("id").and_then(Value::as_u64);
    if let Some(items) = collection.get_mut("items").and_then(Value::as_array_mut) {
        for item in items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let Some(job) = latest_collection_item_job(collection_id, item, jobs) else {
                if run_id.is_none() {
                    continue;
                }
                // The bound job was deleted and no exact-input replacement exists.
                if let Some(obj) = item.as_object_mut() {
                    obj.remove("run_id");
                }
                item["status"] = json!("interrupted");
                item["progress"] = json!(0);
                if let Some(obj) = item.as_object_mut() {
                    obj.remove("output_path");
                    obj.remove("error_message");
                }
                continue;
            };
            sync_collection_item_from_job(item, job);
        }
    }
    collection["status"] = json!(aggregate_collection_status(collection));
}

fn latest_collection_item_job<'a>(
    collection_id: Option<u64>,
    item: &Value,
    jobs: &'a [NativeJob],
) -> Option<&'a NativeJob> {
    let item_id = item.get("id").and_then(Value::as_u64);
    let bound = item
        .get("run_id")
        .and_then(Value::as_u64)
        .and_then(|run_id| jobs.iter().find(|job| job.id == run_id));
    let latest_explicit = collection_id
        .zip(item_id)
        .and_then(|(collection_id, item_id)| {
            jobs.iter()
                .filter(|job| {
                    job.collection_id == Some(collection_id)
                        && job.collection_item_id == Some(item_id)
                })
                .max_by_key(|job| job.id)
        });
    let latest_descendant = bound.and_then(|ancestor| {
        jobs.iter()
            .filter(|candidate| job_descends_from(candidate, ancestor, jobs))
            .max_by_key(|job| job.id)
    });

    [bound, latest_explicit, latest_descendant]
        .into_iter()
        .flatten()
        .max_by_key(|job| job.id)
}

fn job_descends_from(candidate: &NativeJob, ancestor: &NativeJob, jobs: &[NativeJob]) -> bool {
    let mut parent_id = candidate.parent_run_id.as_deref();
    for _ in 0..jobs.len().min(64) {
        let Some(parent) = parent_id else {
            return false;
        };
        if parent == ancestor.job_id {
            return true;
        }
        parent_id = jobs
            .iter()
            .find(|job| job.job_id == parent)
            .and_then(|job| job.parent_run_id.as_deref());
    }
    false
}

fn sync_collection_item_from_job(item: &mut Value, job: &NativeJob) {
    item["run_id"] = json!(job.id);
    item["job_id"] = json!(job.job_id.clone());
    item["status"] = json!(job.status.clone());
    item["progress"] = json!(job.progress);
    if let Some(output_path) = &job.output_path {
        item["output_path"] = json!(output_path);
    }
    if let Some(error_message) = &job.error_message {
        item["error_message"] = json!(error_message);
    } else if let Some(object) = item.as_object_mut() {
        object.remove("error_message");
    }
    if let Some(object) = item.as_object_mut() {
        object.remove("batch_id");
    }
}

fn retry_params_for_job(job: &NativeJob) -> Value {
    let mut params = Map::new();
    params.insert("input".to_string(), json!(job.input.clone()));
    if let Some(title) = job
        .title
        .as_deref()
        .filter(|title| !title.trim().is_empty())
    {
        params.insert("title".to_string(), json!(title));
    }
    if let Some(snapshot) = job.settings_snapshot.as_ref().and_then(Value::as_object) {
        for key in ["template", "mode"] {
            if let Some(value) = snapshot.get(key).and_then(Value::as_str) {
                params.insert(key.to_string(), json!(value));
            }
        }
    }
    Value::Object(params)
}

fn aggregate_collection_status(collection: &Value) -> &'static str {
    let items = collection
        .get("items")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[]);
    if items.is_empty() {
        return "active";
    }
    let statuses = items
        .iter()
        .map(|item| {
            item.get("status")
                .and_then(Value::as_str)
                .unwrap_or("pending")
        })
        .collect::<Vec<_>>();
    if statuses.contains(&"cancelling") {
        "cancelling"
    } else if statuses.contains(&"pausing") {
        "pausing"
    } else if items.iter().any(|item| {
        let status = item
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("pending");
        status == "queued"
            || status == "running"
            || (status == "pending" && item.get("run_id").and_then(Value::as_u64).is_some())
    }) {
        "processing"
    } else if statuses.contains(&"paused") {
        "paused"
    } else if statuses.iter().all(|status| *status == "completed") {
        "completed"
    } else if statuses.contains(&"pending") {
        "active"
    } else if statuses
        .iter()
        .any(|status| matches!(*status, "failed" | "cancelled" | "interrupted"))
    {
        "failed"
    } else {
        "active"
    }
}

fn collection_item(id: u64, input: &str) -> Value {
    json!({
        "id": id,
        "input": input,
        "status": "pending",
        "title": source_title(input),
        "progress": 0,
    })
}

fn source_title(input: &str) -> String {
    let value = input.trim();
    if value.starts_with("http://") || value.starts_with("https://") {
        // Strip query string and fragment before extracting the last path
        // segment. Without this, a URL like
        //   https://www.bilibili.com/video/BV1BoM76iEih/?vd_source=abc123
        // would produce the title "?vd_source=abc123" instead of "BV1BoM76iEih".
        let path_part = value.split(['?', '#']).next().unwrap_or(value);
        return path_part
            .trim_end_matches('/')
            .rsplit('/')
            .next()
            .filter(|part| !part.is_empty())
            .unwrap_or(value)
            .to_string();
    }
    Path::new(value)
        .file_stem()
        .and_then(|part| part.to_str())
        .filter(|part| !part.is_empty())
        .unwrap_or(value)
        .to_string()
}

fn tool_exists(name: &str, components: &[&str], runtime_dir: &Path) -> bool {
    resolve_tool_path(name, components, runtime_dir).is_some()
}

pub(crate) fn hidden_command(program: impl AsRef<OsStr>) -> Command {
    let mut command = Command::new(program);
    #[cfg(target_os = "windows")]
    {
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

#[cfg(target_os = "windows")]
fn kill_process_pid(pid: u32) -> bool {
    let pid_str = pid.to_string();
    let system_taskkill = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .map(|root| root.join("System32").join("taskkill.exe"));
    let candidates = system_taskkill
        .into_iter()
        .chain(std::iter::once(PathBuf::from("taskkill")));
    for candidate in candidates {
        let status = hidden_command(candidate)
            .args(["/PID", &pid_str, "/T", "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        if status.map(|status| status.success()).unwrap_or(false) {
            return true;
        }
    }
    false
}

fn executable_name(name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn component_runtime_version(component: &str, component_path: &Path) -> Option<String> {
    let (exe, args): (&str, &[&str]) = match component {
        "download-tools" => ("yt-dlp", &["--version"]),
        "ffmpeg-tools" => ("ffmpeg", &["-version"]),
        "mpv-tools" => ("mpv", &["--version"]),
        _ => return None,
    };
    let path = component_path.join(executable_name(exe));
    if !path.is_file() {
        return None;
    }
    let output = hidden_command(path).args(args).output().ok()?;
    if !output.status.success() {
        return None;
    }
    first_non_empty_line(&output).map(|line| line.chars().take(120).collect())
}

fn first_non_empty_line(output: &Output) -> Option<String> {
    let text = if output.stdout.is_empty() {
        String::from_utf8_lossy(&output.stderr)
    } else {
        String::from_utf8_lossy(&output.stdout)
    };
    text.lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(ToOwned::to_owned)
}

fn stop_mpv_session(session: &mut MpvSession) {
    if let Some(mut child) = session.child.take() {
        if child.try_wait().ok().flatten().is_none() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    session.source_path = None;
}

fn mpv_seek_commands(start_seconds: f64) -> [Value; 2] {
    [
        json!({ "command": ["seek", start_seconds, "absolute+exact"] }),
        json!({ "command": ["set", "pause", "no"] }),
    ]
}

fn send_mpv_ipc(ipc_path: &Path, commands: &[Value]) -> Result<(), String> {
    let mut last_error = None;
    for _ in 0..50 {
        match fs::OpenOptions::new().write(true).open(ipc_path) {
            Ok(mut pipe) => {
                for command in commands {
                    serde_json::to_writer(&mut pipe, command)
                        .map_err(|error| format!("mpv IPC 命令编码失败: {error}"))?;
                    pipe.write_all(b"\n")
                        .map_err(|error| format!("mpv IPC 写入失败: {error}"))?;
                }
                return pipe
                    .flush()
                    .map_err(|error| format!("mpv IPC 刷新失败: {error}"));
            }
            Err(error) => {
                last_error = Some(error);
                std::thread::sleep(Duration::from_millis(20));
            }
        }
    }

    Err(format!(
        "mpv IPC 连接失败: {}",
        last_error
            .map(|error| error.to_string())
            .unwrap_or_else(|| "unknown error".to_string())
    ))
}

fn mpv_playback_command(
    mpv_path: &Path,
    source_path: &Path,
    start_seconds: f64,
    ipc_path: &Path,
) -> Command {
    let mut command = Command::new(mpv_path);
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);
    command
        .args([
            "--no-config",
            "--no-terminal",
            "--force-window=yes",
            "--hwdec=auto-safe",
        ])
        .arg(format!("--start={start_seconds:.3}"))
        .arg(format!("--input-ipc-server={}", ipc_path.to_string_lossy()))
        .arg("--")
        .arg(source_path);
    command
}

/// Resolve a GitHub release download URL dynamically.
///
/// If the URL still works (200), returns `None` (no resolution needed).
/// If the URL returns a client error (404/410), fetches the latest release
/// from the GitHub API and finds a matching asset by extension and prefix.
fn resolve_github_release_url(url: &str) -> Option<String> {
    let (owner, repo) = github_repo_from_url(url)?;

    // Fast path: try the hardcoded URL first
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .ok()?;
    if let Ok(response) = client.head(url).send() {
        if response.status().is_success() {
            return None; // URL still works, no resolution needed
        }
    }

    // Only resolve /releases/download/ URLs
    if !url.contains("/releases/download/") {
        return None;
    }

    // Extract filename pattern from the original URL
    let original_filename = url.split('/').next_back()?;
    let ext = original_filename.rsplit('.').next()?;

    // Fetch latest release assets from GitHub API
    let api_url = format!("https://api.github.com/repos/{owner}/{repo}/releases/latest");
    let response = client
        .get(&api_url)
        .header("User-Agent", "Video-Notes-AI")
        .send()
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    let payload: Value = response.json().ok()?;
    let assets = payload.get("assets")?.as_array()?;

    // Find best-matching asset: same extension, closest prefix match
    // Strategy: match by extension, then pick the simplest name
    // (exclude "dev", "debug", "lgpl" suffixes)
    let exclude_keywords = ["dev", "debug", "lgpl", "v3"];
    let mut best: Option<String> = None;
    for asset in assets {
        let name = asset.get("name")?.as_str()?;
        if !name.ends_with(&format!(".{}", ext)) {
            continue;
        }
        if exclude_keywords.iter().any(|k| name.contains(k)) {
            continue;
        }
        // Prefer the asset name that starts with the same prefix as the original
        let original_prefix = original_filename.split('-').next()?;
        if name.starts_with(original_prefix) {
            return asset
                .get("browser_download_url")?
                .as_str()
                .map(|s| s.to_string());
        }
        best = best.or_else(|| {
            asset
                .get("browser_download_url")?
                .as_str()
                .map(|s| s.to_string())
        });
    }
    best
}

fn github_repo_from_url(url: &str) -> Option<(String, String)> {
    let marker = "github.com/";
    let rest = url.split(marker).nth(1)?;
    let path = rest.split(['?', '#']).next()?;
    let mut parts = path.split('/');
    let owner = parts.next()?.trim();
    let repo = parts.next()?.trim();
    if owner.is_empty() || repo.is_empty() {
        None
    } else {
        Some((owner.to_string(), repo.to_string()))
    }
}

pub(crate) fn resolve_tool_path(
    name: &str,
    components: &[&str],
    runtime_dir: &Path,
) -> Option<PathBuf> {
    let exe = executable_name(name);
    for component in components {
        let component_path = runtime_dir.join("components").join(component);
        let path = component_path.join(&exe);
        if path.is_file() && verify_component_payload(&component_path).is_ok() {
            return Some(path);
        }
    }
    Some(PathBuf::from(exe)).filter(|candidate| {
        hidden_command(candidate)
            .arg("--version")
            .output()
            .map(|output| output.status.success())
            .unwrap_or(false)
    })
}

fn collect_markdown_notes(
    root: &Path,
    notes: &mut Vec<NoteEntry>,
    depth: usize,
) -> Result<(), String> {
    if depth > 8 || !root.is_dir() {
        return Ok(());
    }
    let entries = fs::read_dir(root).map_err(|error| error.to_string())?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_markdown_notes(&path, notes, depth + 1)?;
            continue;
        }
        if path.extension().and_then(|value| value.to_str()) != Some("md") {
            continue;
        }
        if let Ok(note) = note_entry_from_path(path) {
            notes.push(note);
        }
    }
    Ok(())
}

fn collect_media_files(root: &Path, result: &mut Vec<String>, depth: usize) -> Result<(), String> {
    if depth > 3 || !root.is_dir() {
        return Ok(());
    }
    for entry in fs::read_dir(root)
        .map_err(|error| error.to_string())?
        .flatten()
    {
        let path = entry.path();
        if path.is_dir() {
            collect_media_files(&path, result, depth + 1)?;
            continue;
        }
        let ext = path
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("")
            .to_lowercase();
        if matches!(
            ext.as_str(),
            "mp4" | "mkv" | "mov" | "avi" | "webm" | "mp3" | "wav" | "m4a" | "flac"
        ) {
            result.push(path.to_string_lossy().to_string());
        }
    }
    result.sort();
    Ok(())
}

fn note_entry_from_path(path: PathBuf) -> Result<NoteEntry, String> {
    let metadata = fs::metadata(&path).map_err(|error| error.to_string())?;
    let modified = metadata
        .modified()
        .ok()
        .map(DateTime::<Utc>::from)
        .unwrap_or_else(Utc::now)
        .to_rfc3339();
    let title = note_title(&path);
    Ok(NoteEntry {
        id: note_id(&path),
        title,
        path,
        created_at: modified,
    })
}

fn note_detail(note: NoteEntry) -> Result<Value, String> {
    let content = fs::read_to_string(&note.path).map_err(|error| error.to_string())?;
    Ok(json!({
        "id": note.id,
        "title": note.title,
        "content": content,
        "path": note.path.to_string_lossy(),
    }))
}

fn note_title(path: &Path) -> String {
    let fallback = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("Untitled")
        .to_string();
    if let Ok(content) = fs::read_to_string(path) {
        if let Some(title) = metadata_title(&content) {
            return title;
        }
        for line in content.lines().take(80) {
            let trimmed = line.trim();
            if let Some(title) = trimmed.strip_prefix("# ") {
                let title = title.trim();
                if !title.is_empty() && !is_generic_note_heading(title) {
                    return title.to_string();
                }
            }
        }
    }
    fallback
}

fn metadata_title(content: &str) -> Option<String> {
    let mut lines = content.lines();
    let first = lines.next()?.trim_start_matches('\u{feff}').trim();
    if first == "---" {
        for line in lines {
            let trimmed = line.trim();
            if trimmed == "---" {
                break;
            }
            if let Some(title) = parse_title_line(trimmed) {
                return Some(title);
            }
        }
    }
    for line in content.lines().take(20) {
        let trimmed = line.trim();
        if trimmed.starts_with("# ") {
            break;
        }
        if let Some(title) = parse_title_line(trimmed) {
            return Some(title);
        }
    }
    None
}

fn parse_title_line(line: &str) -> Option<String> {
    let value = line.strip_prefix("title:")?.trim();
    let value = value
        .split(" date:")
        .next()
        .unwrap_or(value)
        .split(" tags:")
        .next()
        .unwrap_or(value)
        .trim()
        .trim_matches('"')
        .trim_matches('\'')
        .trim();
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

fn is_generic_note_heading(title: &str) -> bool {
    matches!(
        title.trim().to_lowercase().as_str(),
        "概要" | "摘要" | "总结" | "summary" | "overview"
    )
}

pub(crate) fn note_id(path: &Path) -> u32 {
    let mut hasher = DefaultHasher::new();
    path.to_string_lossy().to_lowercase().hash(&mut hasher);
    let id = (hasher.finish() & 0x7fff_ffff) as u32;
    if id == 0 {
        1
    } else {
        id
    }
}

fn open_path(path: &Path) -> Result<Value, String> {
    #[cfg(target_os = "windows")]
    open_with_shell_execute(path.as_os_str())?;

    #[cfg(target_os = "macos")]
    let result = hidden_command("open").arg(path).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = hidden_command("xdg-open").arg(path).spawn();

    #[cfg(not(target_os = "windows"))]
    result.map_err(|error| error.to_string())?;
    Ok(json!(true))
}

fn open_url(url: &str) -> Result<Value, String> {
    let trimmed = url.trim();
    if !(trimmed.starts_with("https://") || trimmed.starts_with("http://")) {
        return Err("url must start with http:// or https://".to_string());
    }

    #[cfg(target_os = "windows")]
    open_with_shell_execute(OsStr::new(trimmed))?;

    #[cfg(target_os = "macos")]
    let result = hidden_command("open").arg(trimmed).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = hidden_command("xdg-open").arg(trimmed).spawn();

    #[cfg(not(target_os = "windows"))]
    result.map_err(|error| error.to_string())?;
    Ok(json!(true))
}

#[cfg(target_os = "windows")]
fn open_with_shell_execute(target: &OsStr) -> Result<(), String> {
    hidden_command("rundll32.exe")
        .arg("url.dll,FileProtocolHandler")
        .arg(target)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("failed to open target: {error}"))
}

fn reveal_path(path: &Path) -> Result<Value, String> {
    #[cfg(target_os = "windows")]
    let result = {
        let parent = path.parent().unwrap_or(path);
        hidden_command("explorer")
            .arg(parent.to_string_lossy().as_ref())
            .spawn()
    };

    #[cfg(target_os = "macos")]
    let result = hidden_command("open")
        .args(["-R", &path.to_string_lossy()])
        .spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = hidden_command("xdg-open")
        .arg(path.parent().unwrap_or_else(|| Path::new(".")))
        .spawn();

    result.map_err(|error| error.to_string())?;
    Ok(json!(true))
}

fn check_item(name: &str, ok: bool, detail: &str) -> Value {
    json!({
        "name": name,
        "status": if ok { "pass" } else { "warn" },
        "detail": detail,
    })
}

/// Rename a directory, falling back to copy+remove for cross-volume moves.
/// `fs::rename` fails with "Invalid cross-device link" on Windows when
/// source and target are on different volumes/drives.
fn rename_dir_cross_volume(source: &Path, target: &Path) -> Result<(), String> {
    match fs::rename(source, target) {
        Ok(()) => Ok(()),
        Err(e) if e.raw_os_error() == Some(17) || e.to_string().contains("cross-device") => {
            // Cross-volume: copy recursively then remove source.
            copy_dir_recursive(source, target)?;
            let _ = fs::remove_dir_all(source);
            Ok(())
        }
        Err(e) => Err(e.to_string()),
    }
}

/// Verify the SHA256 hash of a downloaded file against the expected value.
fn verify_sha256(path: &Path, expected: &str) -> Result<(), String> {
    use sha2::{Digest, Sha256};
    let bytes = fs::read(path).map_err(|e| format!("failed to read file for hash check: {e}"))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let actual = hasher.finalize();
    let actual_hex = actual
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    if actual_hex.to_lowercase() != expected.trim().to_lowercase() {
        return Err(format!(
            "SHA256 mismatch: expected {expected}, got {actual_hex}"
        ));
    }
    Ok(())
}

/// Redact potential API keys and bearer tokens from error messages
/// before returning to the frontend. Looks for common patterns:
/// `Bearer xxxx`, `token=xxxx`, `api_key=xxxx`, `SESSDATA=xxxx`.
fn redact_secrets(text: &str) -> String {
    let lower = text.to_lowercase();
    let patterns = [
        "bearer ",
        "token=",
        "api_key=",
        "api-key=",
        "sessdata=",
        "authorization:",
    ];
    let mut result = text.to_string();
    for pat in &patterns {
        if let Some(pos) = lower.find(pat) {
            let value_start = pos + pat.len();
            let value_end = result[value_start..]
                .find(|c: char| c.is_whitespace() || c == '&' || c == '"' || c == '\'')
                .map(|e| value_start + e)
                .unwrap_or(result.len());
            if value_end > value_start + 4 {
                result = format!(
                    "{}{}[REDACTED]{}",
                    &result[..value_start],
                    pat,
                    &result[value_end..]
                );
            }
        }
    }
    result
}

/// Extract a value from YAML-like frontmatter (between --- markers).
fn parse_frontmatter_value(content: &str, key: &str) -> Option<String> {
    let normalized = content.trim_start_matches('\u{feff}');
    let lines = normalized.lines().collect::<Vec<_>>();
    if lines.first()?.trim() != "---" {
        return None;
    }
    let end = lines[1..].iter().position(|l| l.trim() == "---")?;
    for line in &lines[1..=end] {
        if let Some(value) = line
            .strip_prefix(&format!("{key}: "))
            .or_else(|| line.strip_prefix(&format!("{key}:")))
        {
            return Some(value.trim().to_string());
        }
    }
    None
}

/// Remove stale temporary files left behind by previous crashed runs.
/// Cleans up `.{name}.{uuid}.tmp` files and `{uuid}.download` directories
/// in the runtime directory and its packages/components subdirectories.
fn cleanup_stale_temp_files(runtime_dir: &Path) {
    let scan_dirs = [
        runtime_dir.to_path_buf(),
        runtime_dir.join("packages"),
        runtime_dir.join("components"),
    ];
    for dir in &scan_dirs {
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            // Atomic write temp files: ".{name}.{uuid}.tmp"
            // Download temp dirs: "{uuid}.download" or "{uuid}.install"
            if (name.starts_with('.') && name.ends_with(".tmp"))
                || name.ends_with(".download")
                || name.ends_with(".install")
            {
                if path.is_dir() {
                    let _ = fs::remove_dir_all(&path);
                } else {
                    let _ = fs::remove_file(&path);
                }
            }
        }
    }
}

fn sanitize_filename(value: &str) -> String {
    let cleaned: String = value
        .chars()
        .map(|ch| match ch {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            // Filter Cc (control) and Cf (format) characters — includes
            // null bytes, BOM (U+FEFF), zero-width spaces (U+200B-200D),
            // LTR/RTL marks (U+200E-200F), bidi controls (U+202A-202E).
            ch if ch.is_control() || is_format_char(ch) => '_',
            ch => ch,
        })
        .collect();
    let trimmed = cleaned.trim().trim_matches('.').to_string();
    // Truncate to 180 chars (matching yt-dlp's %(title).180s template)
    // to avoid Windows 255-char path limit when suffixed with timestamp + id.
    let truncated = if trimmed.chars().count() > 180 {
        trimmed.chars().take(180).collect::<String>()
    } else {
        trimmed
    };
    if truncated.is_empty() {
        return "video-note".to_string();
    }
    // Block Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
    // Even with suffixes, a leading "CON." can cause issues on some Windows versions.
    let upper = truncated.to_uppercase();
    let reserved = [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ];
    if reserved.contains(&upper.as_str()) {
        return format!("video-note-{truncated}");
    }
    truncated
}

/// Returns true for Unicode Cf (format) characters that are invisible
/// but not caught by `char::is_control()` (which only covers Cc).
fn is_format_char(ch: char) -> bool {
    matches!(ch,
        '\u{00AD}' | // Soft hyphen
        '\u{0600}'..='\u{0605}' | // Arabic number signs
        '\u{061C}' | // Arabic letter mark
        '\u{06DD}' | // Arabic end of ayah
        '\u{070F}' | // Syriac abbreviation mark
        '\u{180E}' | // Mongolian vowel separator
        '\u{200B}'..='\u{200F}' | // Zero-width space, ZWJ, ZWNJ, LTR/RTL marks
        '\u{202A}'..='\u{202E}' | // Bidi embedding controls
        '\u{2060}'..='\u{2064}' | // Word joiner, invisible operators
        '\u{2066}'..='\u{2069}' | // Isolate controls
        '\u{FEFF}' | // BOM / ZWNBSP
        '\u{FFF9}'..='\u{FFFB}' // Interlinear annotation
    )
}

/// Validate a component name to prevent path traversal.
/// Component names must be simple identifiers (alphanumeric, hyphens,
/// underscores) — no path separators, no `..`, no leading dots.
fn sanitize_component_name(component: &str) -> Result<String, String> {
    if component.is_empty() {
        return Err("component name cannot be empty".to_string());
    }
    if component.len() > 64
        || !component
            .chars()
            .all(|value| value.is_ascii_alphanumeric() || matches!(value, '-' | '_'))
    {
        return Err(format!("invalid component name: '{component}'"));
    }
    Ok(component.to_string())
}

fn missing_files(component_path: &Path, files: &[Value]) -> Vec<String> {
    files
        .iter()
        .filter_map(Value::as_str)
        .filter(|relative| {
            let relative_path = relative.trim_end_matches('/');
            if relative.ends_with('/') {
                !component_path.join(relative_path).is_dir()
            } else {
                !component_path.join(relative_path).is_file()
            }
        })
        .map(ToOwned::to_owned)
        .collect()
}

fn default_component_manifest(component: &str) -> Option<Value> {
    DEFAULT_COMPONENT_MANIFESTS
        .iter()
        .find(|(name, _)| *name == component)
        .and_then(|(_, manifest)| serde_json::from_str::<Value>(manifest).ok())
}

fn install_component_from_download(
    manifest: &Value,
    target: &Path,
    component: &str,
    handle: &AppHandle,
) -> Result<(), String> {
    let url = manifest_string(manifest, "download_url")
        .ok_or_else(|| "manifest has no download_url".to_string())?;
    let files = manifest
        .get("files")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if files.is_empty() {
        return Err("manifest has no files".to_string());
    }
    let parent = target
        .parent()
        .ok_or_else(|| format!("Invalid component target: {}", target.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temp = parent.join(format!("{}.download", Uuid::new_v4()));
    let stage = temp.join("stage");
    fs::create_dir_all(&stage).map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        // Resolve GitHub release URLs dynamically: if the hardcoded URL 404s,
        // fetch the latest release from GitHub API
        let resolved_url = resolve_github_release_url(&url);
        let final_url = resolved_url.as_deref().unwrap_or(&url);
        let package_path = temp.join(download_filename(final_url));
        download_file_with_fallback(final_url, &package_path, component, handle)?;
        ensure_non_empty_file(&package_path, "component package")?;
        let expected_hash = manifest_string(manifest, "sha256")
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| "component manifest must pin a non-empty sha256".to_string())?;
        verify_sha256(&package_path, &expected_hash)?;
        let archive_type = manifest_string(manifest, "archive_type")
            .unwrap_or_else(|| infer_archive_type(final_url));
        match archive_type.as_str() {
            "exe" => {
                let first = files
                    .first()
                    .and_then(Value::as_str)
                    .ok_or_else(|| "manifest has no executable target".to_string())?;
                fs::copy(&package_path, stage.join(first.trim_end_matches('/')))
                    .map_err(|error| error.to_string())?;
            }
            "zip" => {
                let extracted = temp.join("extracted");
                fs::create_dir_all(&extracted).map_err(|error| error.to_string())?;
                extract_zip_archive(&package_path, &extracted)?;
                if manifest
                    .get("copy_payload_dir")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    stage_component_payload_dir(&extracted, &stage, &files)?;
                } else {
                    stage_component_files(&extracted, &stage, &files)?;
                }
            }
            "7z" => {
                let extracted = temp.join("extracted");
                fs::create_dir_all(&extracted).map_err(|error| error.to_string())?;
                extract_7z_archive(&package_path, &extracted)?;
                if manifest
                    .get("copy_payload_dir")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    stage_component_payload_dir(&extracted, &stage, &files)?;
                } else {
                    stage_component_files(&extracted, &stage, &files)?;
                }
            }
            other => return Err(format!("unsupported archive_type '{other}'")),
        }
        let missing = missing_files(&stage, &files);
        if !missing.is_empty() {
            return Err(format!(
                "downloaded package is missing required files: {}",
                missing.join(", ")
            ));
        }
        if target.exists() {
            fs::remove_dir_all(target).map_err(|error| error.to_string())?;
        }
        rename_dir_cross_volume(&stage, target)?;
        Ok(())
    })();
    let _ = fs::remove_dir_all(&temp);
    result
}

fn install_download_tools(target: &Path) -> Result<(), String> {
    let parent = target
        .parent()
        .ok_or_else(|| format!("Invalid component target: {}", target.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temp = parent.join(format!("{}.download", Uuid::new_v4()));
    fs::create_dir_all(&temp).map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        let exe = temp.join("yt-dlp.exe");
        download_file(YTDLP_DOWNLOAD_URL, &exe)?;
        ensure_non_empty_file(&exe, "yt-dlp.exe")?;
        verify_sha256(&exe, YTDLP_DOWNLOAD_SHA256)?;
        if target.exists() {
            fs::remove_dir_all(target).map_err(|error| error.to_string())?;
        }
        rename_dir_cross_volume(&temp, target)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temp);
    }
    result
}

fn download_file(url: &str, target: &Path) -> Result<(), String> {
    match download_file_with_reqwest_no_progress(url, target) {
        Ok(()) => Ok(()),
        Err(primary_error) => match download_file_with_curl(url, target) {
            Ok(()) => Ok(()),
            Err(curl_error) => match download_file_with_powershell(url, target) {
                Ok(()) => Ok(()),
                Err(powershell_error) => Err(format!(
                    "reqwest download failed: {primary_error}; curl fallback failed: {curl_error}; PowerShell fallback failed: {powershell_error}"
                )),
            },
        },
    }
}

fn download_file_with_fallback(
    url: &str,
    target: &Path,
    component: &str,
    handle: &AppHandle,
) -> Result<(), String> {
    match download_file_with_reqwest(url, target, component, handle) {
        Ok(()) => Ok(()),
        Err(primary_error) => match download_file_with_curl(url, target) {
            Ok(()) => Ok(()),
            Err(curl_error) => match download_file_with_powershell(url, target) {
                Ok(()) => Ok(()),
                Err(powershell_error) => Err(format!(
                    "reqwest download failed: {primary_error}; curl fallback failed: {curl_error}; PowerShell fallback failed: {powershell_error}"
                )),
            },
        },
    }
}

fn download_file_with_reqwest(
    url: &str,
    target: &Path,
    component: &str,
    handle: &AppHandle,
) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|error| error.to_string())?;
    let mut response = client
        .get(url)
        .header("User-Agent", "Video Notes AI")
        .send()
        .map_err(|error| format!("failed to download: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()));
    }
    let total = response.content_length().unwrap_or(0);
    if total > MAX_COMPONENT_DOWNLOAD_BYTES {
        return Err(format!(
            "download is too large: {total} bytes exceeds the {} byte limit",
            MAX_COMPONENT_DOWNLOAD_BYTES
        ));
    }
    let mut file =
        fs::File::create(target).map_err(|error| format!("failed to create file: {error}"))?;
    let mut downloaded = 0u64;
    let mut buffer = [0u8; 65536];
    let mut reader = std::io::BufReader::new(&mut response);
    loop {
        let n = reader
            .read(&mut buffer)
            .map_err(|error| format!("failed to read download stream: {error}"))?;
        if n == 0 {
            break;
        }
        downloaded = downloaded
            .checked_add(n as u64)
            .ok_or_else(|| "download size overflow".to_string())?;
        if downloaded > MAX_COMPONENT_DOWNLOAD_BYTES {
            drop(file);
            let _ = fs::remove_file(target);
            return Err(format!(
                "download exceeded the {} byte limit",
                MAX_COMPONENT_DOWNLOAD_BYTES
            ));
        }
        file.write_all(&buffer[..n])
            .map_err(|error| format!("failed to write download: {error}"))?;
        if total > 0 {
            let _ = handle.emit(
                "component:download-progress",
                ComponentDownloadProgress {
                    component: component.to_string(),
                    downloaded_bytes: downloaded,
                    total_bytes: total,
                    stage: "downloading".to_string(),
                },
            );
        }
    }
    Ok(())
}

fn download_file_with_reqwest_no_progress(url: &str, target: &Path) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|error| error.to_string())?;
    let mut response = client
        .get(url)
        .header("User-Agent", "Video Notes AI")
        .send()
        .map_err(|error| format!("failed to download: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()));
    }
    if response.content_length().unwrap_or(0) > MAX_COMPONENT_DOWNLOAD_BYTES {
        return Err(format!(
            "download exceeds the {} byte limit",
            MAX_COMPONENT_DOWNLOAD_BYTES
        ));
    }
    let mut file =
        fs::File::create(target).map_err(|error| format!("failed to create file: {error}"))?;
    let mut downloaded = 0u64;
    let mut buffer = [0u8; 65536];
    loop {
        let n = response
            .read(&mut buffer)
            .map_err(|error| format!("failed to read download stream: {error}"))?;
        if n == 0 {
            break;
        }
        downloaded = downloaded
            .checked_add(n as u64)
            .ok_or_else(|| "download size overflow".to_string())?;
        if downloaded > MAX_COMPONENT_DOWNLOAD_BYTES {
            drop(file);
            let _ = fs::remove_file(target);
            return Err(format!(
                "download exceeded the {} byte limit",
                MAX_COMPONENT_DOWNLOAD_BYTES
            ));
        }
        file.write_all(&buffer[..n])
            .map_err(|error| format!("failed to write download: {error}"))?;
    }
    Ok(())
}

fn download_file_with_curl(url: &str, target: &Path) -> Result<(), String> {
    let args = vec![
        "-L".to_string(),
        "--fail".to_string(),
        "--retry".to_string(),
        "2".to_string(),
        "--max-filesize".to_string(),
        MAX_COMPONENT_DOWNLOAD_BYTES.to_string(),
        "--output".to_string(),
        target.to_string_lossy().to_string(),
        url.to_string(),
    ];
    let output = command_output("curl.exe", &args)?;
    if output.status.success() {
        validate_download_size(target)
    } else {
        let _ = fs::remove_file(target);
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

fn download_file_with_powershell(url: &str, target: &Path) -> Result<(), String> {
    let script = format!(
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '{}' -OutFile '{}'",
        powershell_quote(url),
        powershell_quote(&target.to_string_lossy())
    );
    let args = vec![
        "-NoProfile".to_string(),
        "-ExecutionPolicy".to_string(),
        "Bypass".to_string(),
        "-Command".to_string(),
        script,
    ];
    let output = command_output("powershell.exe", &args)?;
    if output.status.success() {
        validate_download_size(target)
    } else {
        let _ = fs::remove_file(target);
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

fn validate_download_size(path: &Path) -> Result<(), String> {
    let size = fs::metadata(path)
        .map_err(|error| format!("downloaded file metadata is unavailable: {error}"))?
        .len();
    if size <= MAX_COMPONENT_DOWNLOAD_BYTES {
        return Ok(());
    }
    let _ = fs::remove_file(path);
    Err(format!(
        "download is too large: {size} bytes exceeds the {} byte limit",
        MAX_COMPONENT_DOWNLOAD_BYTES
    ))
}

fn ensure_non_empty_file(path: &Path, label: &str) -> Result<(), String> {
    let len = fs::metadata(path)
        .map_err(|error| format!("{label} was not created: {error}"))?
        .len();
    if len == 0 {
        Err(format!("{label} download produced an empty file"))
    } else {
        Ok(())
    }
}

fn manifest_string(manifest: &Value, key: &str) -> Option<String> {
    manifest
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn component_marker_path(target: &Path) -> PathBuf {
    target.join(".runtime-component.json")
}

/// Read the manifest version from a component's marker file.
/// Returns `None` if the component is not installed or the marker is missing.
fn read_marker_version(component_path: &Path) -> Option<String> {
    let marker_path = component_marker_path(component_path);
    let content = fs::read_to_string(marker_path).ok()?;
    let marker: Value = serde_json::from_str(&content).ok()?;
    marker
        .get("manifest_version")
        .and_then(Value::as_str)
        .map(|s| s.to_string())
        .filter(|s| !s.is_empty())
}

fn write_component_marker(manifest: &Value, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target).map_err(|error| error.to_string())?;
    let file_sha256 = component_payload_hashes(target)?;
    if file_sha256.is_empty() {
        return Err("component payload contains no files".to_string());
    }
    let marker = json!({
        "component": manifest_string(manifest, "component").unwrap_or_default(),
        "manifest_version": manifest_string(manifest, "version").unwrap_or_default(),
        "installed_at": Utc::now().to_rfc3339(),
        "file_sha256": file_sha256,
    });
    write_json_atomic(&component_marker_path(target), &marker)
}

fn component_payload_hashes(target: &Path) -> Result<Map<String, Value>, String> {
    if !target.is_dir() {
        return Err("component directory does not exist".to_string());
    }
    let mut stack = vec![target.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = stack.pop() {
        for entry in fs::read_dir(&directory).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path).map_err(|error| error.to_string())?;
            if metadata.file_type().is_symlink() {
                return Err(format!(
                    "component payload contains a symbolic link: {}",
                    path.display()
                ));
            }
            if metadata.is_dir() {
                stack.push(path);
            } else if metadata.is_file() && path != component_marker_path(target) {
                files.push(path);
            }
        }
    }
    files.sort();
    let mut hashes = Map::new();
    for path in files {
        let relative = path
            .strip_prefix(target)
            .map_err(|_| "component file escapes payload root".to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        hashes.insert(relative, json!(sha256_file(&path)?));
    }
    Ok(hashes)
}

fn sha256_file(path: &Path) -> Result<String, String> {
    use sha2::{Digest, Sha256};
    let mut file = fs::File::open(path).map_err(|error| error.to_string())?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn verify_component_payload(target: &Path) -> Result<(), String> {
    let marker = read_json_file(&component_marker_path(target))?;
    let expected = marker
        .get("file_sha256")
        .and_then(Value::as_object)
        .ok_or_else(|| "component marker has no file digests; reinstall required".to_string())?;
    let actual = component_payload_hashes(target)?;
    if expected != &actual {
        return Err("component payload digest mismatch; reinstall required".to_string());
    }
    Ok(())
}

fn download_filename(url: &str) -> String {
    url.split('/')
        .next_back()
        .and_then(|part| part.split('?').next())
        .filter(|part| !part.is_empty())
        // Reject path traversal segments that could escape the temp directory.
        .filter(|part| *part != ".." && *part != "." && !part.contains('\\'))
        .unwrap_or("component-package")
        .to_string()
}

fn infer_archive_type(url: &str) -> String {
    let path = url.split('?').next().unwrap_or(url).to_lowercase();
    if path.ends_with(".zip") {
        "zip".to_string()
    } else if path.ends_with(".exe") {
        "exe".to_string()
    } else {
        "zip".to_string()
    }
}

fn extract_zip_archive(archive: &Path, target: &Path) -> Result<(), String> {
    let script = format!(
        "Expand-Archive -LiteralPath '{}' -DestinationPath '{}' -Force",
        powershell_quote(&archive.to_string_lossy()),
        powershell_quote(&target.to_string_lossy())
    );
    let powershell_args = vec![
        "-NoProfile".to_string(),
        "-ExecutionPolicy".to_string(),
        "Bypass".to_string(),
        "-Command".to_string(),
        script,
    ];
    let powershell_result = command_output("powershell.exe", &powershell_args);
    if let Ok(output) = &powershell_result {
        if output.status.success() {
            return Ok(());
        }
    }
    let tar_args = vec![
        "-xf".to_string(),
        archive.to_string_lossy().to_string(),
        "-C".to_string(),
        target.to_string_lossy().to_string(),
    ];
    let tar_result = command_output("tar.exe", &tar_args);
    if let Ok(output) = &tar_result {
        if output.status.success() {
            return Ok(());
        }
    }
    let powershell_error = match powershell_result {
        Ok(output) => String::from_utf8_lossy(&output.stderr).trim().to_string(),
        Err(error) => error,
    };
    let tar_error = match tar_result {
        Ok(output) => String::from_utf8_lossy(&output.stderr).trim().to_string(),
        Err(error) => error,
    };
    Err(format!(
        "PowerShell unzip failed: {powershell_error}; tar fallback failed: {tar_error}"
    ))
}

/// Extract a `.7z` archive. Tries `7z`, then `7zr`, then auto-downloads portable 7zr.exe.
fn extract_7z_archive(archive: &Path, target: &Path) -> Result<(), String> {
    let args = vec![
        "x".to_string(),
        archive.to_string_lossy().to_string(),
        format!("-o{}", target.to_string_lossy()),
        "-y".to_string(),
    ];

    // Try locally-installed 7z first (full 7-Zip or portable 7za/7zr)
    let seven_z = command_output("7z", &args)
        .or_else(|_| command_output("7zr", &args))
        .or_else(|_| command_output("7za", &args));
    if let Ok(output) = &seven_z {
        if output.status.success() {
            return Ok(());
        }
    }

    let error_msg = match seven_z {
        Ok(output) => String::from_utf8_lossy(&output.stderr).trim().to_string(),
        Err(error) => error,
    };
    Err(format!(
        "解压 7z 文件失败。请从 https://7-zip.org 安装 7-Zip 后重试：{error_msg}"
    ))
}

fn command_output(name: &str, args: &[String]) -> Result<Output, String> {
    let mut errors = Vec::new();
    for candidate in executable_candidates(name) {
        match hidden_command(&candidate).args(args).output() {
            Ok(output) => return Ok(output),
            Err(error) => errors.push(format!("{}: {error}", candidate.display())),
        }
    }
    Err(format!("failed to run {name}; tried {}", errors.join("; ")))
}

fn executable_candidates(name: &str) -> Vec<PathBuf> {
    let mut candidates = vec![PathBuf::from(name)];
    if cfg!(target_os = "windows") {
        if let Ok(system_root) = std::env::var("SystemRoot").or_else(|_| std::env::var("WINDIR")) {
            let system32 = PathBuf::from(&system_root).join("System32");
            candidates.push(system32.join(name));
            if name.eq_ignore_ascii_case("powershell.exe") {
                candidates.push(
                    system32
                        .join("WindowsPowerShell")
                        .join("v1.0")
                        .join("powershell.exe"),
                );
            }
        }
    }
    candidates
}

fn powershell_quote(value: &str) -> String {
    value.replace('\'', "''")
}

fn stage_component_files(source_root: &Path, stage: &Path, files: &[Value]) -> Result<(), String> {
    fs::create_dir_all(stage).map_err(|error| error.to_string())?;
    for file in files.iter().filter_map(Value::as_str) {
        let trimmed = file.trim_end_matches('/');
        if file.ends_with('/') {
            let source = source_root
                .join(trimmed)
                .is_dir()
                .then(|| source_root.join(trimmed))
                .or_else(|| find_dir_recursive(source_root, path_leaf(trimmed)))
                .ok_or_else(|| format!("required directory '{file}' not found in package"))?;
            copy_dir_recursive(&source, &stage.join(trimmed))?;
        } else {
            let source = source_root
                .join(trimmed)
                .is_file()
                .then(|| source_root.join(trimmed))
                .or_else(|| find_file_recursive(source_root, path_leaf(trimmed)))
                .ok_or_else(|| format!("required file '{file}' not found in package"))?;
            if let Some(parent) = stage.join(trimmed).parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            fs::copy(&source, stage.join(trimmed)).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn stage_component_payload_dir(
    source_root: &Path,
    stage: &Path,
    files: &[Value],
) -> Result<(), String> {
    let first_file = files
        .iter()
        .filter_map(Value::as_str)
        .find(|file| !file.ends_with('/'))
        .ok_or_else(|| "manifest has no payload file".to_string())?;
    let payload_file = find_file_recursive(source_root, path_leaf(first_file))
        .ok_or_else(|| format!("required file '{first_file}' not found in package"))?;
    let payload_dir = payload_file
        .parent()
        .ok_or_else(|| format!("invalid payload file path: {}", payload_file.display()))?;
    copy_dir_recursive(payload_dir, stage)
}

fn is_http_url(value: &str) -> bool {
    let lower = value.trim().to_ascii_lowercase();
    lower.starts_with("https://") || lower.starts_with("http://")
}

fn validate_public_media_url(value: &str) -> Result<(), String> {
    let url = reqwest::Url::parse(value).map_err(|error| format!("invalid media URL: {error}"))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("only HTTP(S) media URLs are supported".to_string());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("media URLs with embedded credentials are not allowed".to_string());
    }
    let host = url
        .host_str()
        .ok_or_else(|| "media URL must include a host".to_string())?
        .trim_end_matches('.')
        .to_ascii_lowercase();
    if host == "localhost" || host.ends_with(".localhost") || host.ends_with(".local") {
        return Err("local and private media hosts are not allowed".to_string());
    }
    if let Ok(ip) = host.parse::<std::net::IpAddr>() {
        let forbidden = match ip {
            std::net::IpAddr::V4(ip) => {
                ip.is_private()
                    || ip.is_loopback()
                    || ip.is_link_local()
                    || ip.is_unspecified()
                    || ip.is_broadcast()
                    || ip.is_documentation()
            }
            std::net::IpAddr::V6(ip) => {
                let first_segment = ip.segments()[0];
                ip.is_loopback()
                    || ip.is_unspecified()
                    || first_segment & 0xfe00 == 0xfc00
                    || first_segment & 0xffc0 == 0xfe80
            }
        };
        if forbidden {
            return Err("local and private media IP addresses are not allowed".to_string());
        }
    }
    const ALLOWED: &[&str] = &[
        "youtube.com",
        "youtu.be",
        "bilibili.com",
        "vimeo.com",
        "x.com",
        "twitter.com",
    ];
    if !ALLOWED
        .iter()
        .any(|domain| host == *domain || host.ends_with(&format!(".{domain}")))
    {
        return Err(format!("unsupported media host: {host}"));
    }
    Ok(())
}

fn download_public_media(
    ytdlp: &Path,
    url: &str,
    workspace: &Path,
    cookie_file: Option<&Path>,
    control: Option<&JobControl>,
) -> Result<PathBuf, String> {
    validate_public_media_url(url)?;
    if workspace.exists() {
        fs::remove_dir_all(workspace)
            .map_err(|error| format!("failed to reset download workspace: {error}"))?;
    }
    fs::create_dir_all(workspace)
        .map_err(|error| format!("failed to create download workspace: {error}"))?;
    let output_template = workspace.join("source.%(ext)s");
    let mut command = hidden_command(ytdlp);
    command
        .arg("--no-playlist")
        .arg("--no-part")
        .arg("--restrict-filenames")
        .arg("--max-filesize")
        .arg("8G")
        .arg("--merge-output-format")
        .arg("mp4")
        .arg("--output")
        .arg(&output_template);
    if let Some(cookie_file) = cookie_file {
        command.arg("--cookies").arg(cookie_file);
    }
    let log_path = workspace.join("yt-dlp.log");
    let stdout_log = fs::File::create(&log_path)
        .map_err(|error| format!("failed to create yt-dlp log: {error}"))?;
    let stderr_log = stdout_log
        .try_clone()
        .map_err(|error| format!("failed to clone yt-dlp log handle: {error}"))?;
    let mut child = command
        .arg("--")
        .arg(url)
        .stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log))
        .spawn()
        .map_err(|error| format!("failed to launch yt-dlp: {error}"))?;
    if let Some(control) = control {
        if let Ok(mut current) = control.current_child.lock() {
            *current = Some(child.id());
        }
    }
    let status = loop {
        if control
            .map(|control| control.cancel_requested.load(Ordering::SeqCst))
            .unwrap_or(false)
        {
            let _ = child.kill();
            let _ = child.wait();
            if let Some(control) = control {
                if let Ok(mut current) = control.current_child.lock() {
                    *current = None;
                }
            }
            let _ = fs::remove_dir_all(workspace);
            return Err(crate::compile::engine::COMPILE_CANCELLED_ERROR.to_string());
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => std::thread::sleep(Duration::from_millis(200)),
            Err(error) => {
                let _ = child.kill();
                if let Some(control) = control {
                    if let Ok(mut current) = control.current_child.lock() {
                        *current = None;
                    }
                }
                let _ = fs::remove_dir_all(workspace);
                return Err(format!("failed while waiting for yt-dlp: {error}"));
            }
        }
    };
    if let Some(control) = control {
        if let Ok(mut current) = control.current_child.lock() {
            *current = None;
        }
    }
    if !status.success() {
        let detail = fs::read_to_string(&log_path)
            .unwrap_or_default()
            .chars()
            .take(800)
            .collect::<String>();
        let _ = fs::remove_dir_all(workspace);
        return Err(format!("online media download failed: {}", detail.trim()));
    }
    let mut candidates = fs::read_dir(workspace)
        .map_err(|error| format!("failed to inspect downloaded media: {error}"))?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .filter(|path| {
            !matches!(
                path.extension()
                    .and_then(|ext| ext.to_str())
                    .unwrap_or("")
                    .to_ascii_lowercase()
                    .as_str(),
                "part" | "ytdl" | "json" | "description" | "log"
            )
        })
        .collect::<Vec<_>>();
    candidates
        .sort_by_key(|path| std::cmp::Reverse(path.metadata().map(|meta| meta.len()).unwrap_or(0)));
    let candidate = candidates
        .into_iter()
        .next()
        .ok_or_else(|| "yt-dlp completed without producing a media file".to_string())?;
    let size = candidate
        .metadata()
        .map_err(|error| format!("failed to inspect downloaded media size: {error}"))?
        .len();
    if size > MAX_MEDIA_BYTES {
        let _ = fs::remove_dir_all(workspace);
        return Err(format!(
            "downloaded media exceeds the 8 GiB limit ({size} bytes)"
        ));
    }
    Ok(candidate)
}

fn path_leaf(path: &str) -> &str {
    path.rsplit(['/', '\\'])
        .next()
        .filter(|part| !part.is_empty())
        .unwrap_or(path)
}

fn find_file_recursive(root: &Path, file_name: &str) -> Option<PathBuf> {
    let entries = fs::read_dir(root).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file()
            && path
                .file_name()
                .and_then(|value| value.to_str())
                .map(|value| value.eq_ignore_ascii_case(file_name))
                .unwrap_or(false)
        {
            return Some(path);
        }
        if path.is_dir() {
            if let Some(found) = find_file_recursive(&path, file_name) {
                return Some(found);
            }
        }
    }
    None
}

fn find_dir_recursive(root: &Path, dir_name: &str) -> Option<PathBuf> {
    let entries = fs::read_dir(root).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            if path
                .file_name()
                .and_then(|value| value.to_str())
                .map(|value| value.eq_ignore_ascii_case(dir_name))
                .unwrap_or(false)
            {
                return Some(path);
            }
            if let Some(found) = find_dir_recursive(&path, dir_name) {
                return Some(found);
            }
        }
    }
    None
}

fn install_ffmpeg_tools_from_path(target: &Path) -> Result<(), String> {
    let ffmpeg = system_executable_path("ffmpeg").ok_or_else(|| {
        "Missing local package and ffmpeg was not found on PATH. Install system FFmpeg or put a component package in runtime\\packages\\ffmpeg-tools.".to_string()
    })?;
    let ffprobe = system_executable_path("ffprobe").ok_or_else(|| {
        "Missing local package and ffprobe was not found on PATH. Install system FFmpeg or put a component package in runtime\\packages\\ffmpeg-tools.".to_string()
    })?;
    let parent = target
        .parent()
        .ok_or_else(|| format!("Invalid component target: {}", target.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temp = parent.join(format!("{}.install", Uuid::new_v4()));
    fs::create_dir_all(&temp).map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        fs::copy(&ffmpeg, temp.join(executable_name("ffmpeg")))
            .map_err(|error| error.to_string())?;
        fs::copy(&ffprobe, temp.join(executable_name("ffprobe")))
            .map_err(|error| error.to_string())?;
        if target.exists() {
            fs::remove_dir_all(target).map_err(|error| error.to_string())?;
        }
        rename_dir_cross_volume(&temp, target)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temp);
    }
    result
}

fn system_executable_path(name: &str) -> Option<PathBuf> {
    let exe = executable_name(name);
    let output = if cfg!(target_os = "windows") {
        hidden_command("where").arg(&exe).output().ok()?
    } else {
        hidden_command("which").arg(&exe).output().ok()?
    };
    if !output.status.success() {
        return None;
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(PathBuf::from)
        .find(|path| path.is_file())
}

fn copy_dir_recursive(source: &Path, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target).map_err(|error| error.to_string())?;
    for entry in fs::read_dir(source).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let source_path = entry.path();
        let target_path = target.join(entry.file_name());
        if source_path.is_dir() {
            copy_dir_recursive(&source_path, &target_path)?;
        } else {
            fs::copy(&source_path, &target_path).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn dir_size(path: &Path) -> u64 {
    let Ok(entries) = fs::read_dir(path) else {
        return 0;
    };
    entries
        .flatten()
        .map(|entry| {
            let path = entry.path();
            if path.is_dir() {
                dir_size(&path)
            } else {
                entry.metadata().map(|metadata| metadata.len()).unwrap_or(0)
            }
        })
        .sum()
}

fn remove_note_assets(note_path: &Path) -> Result<(), String> {
    let Some(note_dir) = note_path.parent() else {
        return Ok(());
    };
    let Some(note_stem) = note_path.file_stem().and_then(|value| value.to_str()) else {
        return Ok(());
    };
    let asset_dir = note_dir.join("assets").join(note_stem);
    if asset_dir.is_dir() {
        fs::remove_dir_all(&asset_dir).map_err(|error| {
            format!(
                "Failed to remove note assets {}: {error}",
                asset_dir.display()
            )
        })?;
    }
    Ok(())
}

fn cleanup_workspace_dirs<F>(root: &Path, should_remove: F) -> Result<u64, String>
where
    F: Fn(&Path, u64) -> bool,
{
    let Ok(entries) = fs::read_dir(root) else {
        return Ok(0);
    };
    let mut removed = 0;
    for entry in entries {
        let entry = entry.map_err(|error| error.to_string())?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let Some(job_id) = workspace_job_id(&path) else {
            continue;
        };
        if should_remove(&path, job_id) {
            fs::remove_dir_all(&path)
                .map_err(|error| format!("Failed to remove {}: {error}", path.display()))?;
            removed += 1;
        }
    }
    Ok(removed)
}

fn workspace_job_id(path: &Path) -> Option<u64> {
    let name = path.file_name()?.to_str()?;
    let rest = name.strip_prefix("job-")?;
    rest.split('-').next()?.parse().ok()
}

fn workspace_is_older_than(path: &Path, min_age: Duration) -> bool {
    if min_age.is_zero() {
        return true;
    }
    let Ok(metadata) = path.metadata() else {
        return false;
    };
    let modified = metadata.modified().unwrap_or(SystemTime::now());
    modified
        .elapsed()
        .map(|age| age >= min_age)
        .unwrap_or(false)
}

fn dir_counts(path: &Path) -> Value {
    let mut dirs = 0u64;
    let mut files = 0u64;
    count_dir(path, &mut dirs, &mut files);
    json!({ "dirs": dirs, "files": files })
}

fn count_dir(path: &Path, dirs: &mut u64, files: &mut u64) {
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            *dirs += 1;
            count_dir(&path, dirs, files);
        } else {
            *files += 1;
        }
    }
}

#[cfg(test)]
mod tests;
