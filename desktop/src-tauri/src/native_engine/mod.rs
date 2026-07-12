use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{hash_map::DefaultHasher, HashMap, HashSet};
use std::ffi::OsStr;
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime};
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

use crate::compile::storage::CapsuleStore;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const YTDLP_DOWNLOAD_URL: &str =
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe";
const VISION_TEST_IMAGE_BASE64: &str =
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAD6SURBVFhH7ZPrDcMgDIQ9HgMxDruwCpu4dkmrBB8oJRbpj3zS5eFIdwdJiG/mKfCHBUhGZ+SEdUJhSE5YJxSG5IR1QmFITlgnFIbkhHVCYUhOWCcUhuSEdUJhSE5YJxSG5IR1QmFITvg5TXKhQOZIgVPZbt/oLMrxPPMFcuQQI8dDg2UFCqegq9fzPnBVgZJk9TWmpMDhuwuLCuRIvOXXMiHJnii1wC8/zeBRDw0hMT2qFspyjQuoEJ1xn+OWb8gHSdKAaFxA1QJGI3rvuH6UCwr0QWFILWA0Dwps1QJG86DAVi1gdA0U+hGiM77G2XBl8GgNT4GbCzC/AGeNYW4AwZ2AAAAAAElFTkSuQmCC";
const DEFAULT_COMPONENT_MANIFESTS: &[(&str, &str)] = &[
    (
        "download-tools",
        include_str!("../../../../runtime/manifests/download-tools.json"),
    ),
    (
        "ffmpeg-tools",
        include_str!("../../../../runtime/manifests/ffmpeg-tools.json"),
    ),
];

/// Lock ordering (must never be acquired in reverse):
///   1. next_job_id
///   2. job_controls
///   3. jobs
///   4. settings_lock
///   ---
///   5. current_child  (per-job)
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
    #[allow(dead_code)]
    next_job_id: Arc<Mutex<u64>>,
    job_controls: Arc<Mutex<HashMap<u64, Arc<JobControl>>>>,
    settings_lock: Arc<Mutex<()>>,
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
    frames_count: u32,
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
}

struct JobControl {
    cancel_requested: AtomicBool,
    pause_requested: AtomicBool,
    current_child: Mutex<Option<u32>>,
    condvar: Condvar,
}

impl JobControl {
    #[allow(dead_code)]
    fn new() -> Self {
        Self {
            cancel_requested: AtomicBool::new(false),
            pause_requested: AtomicBool::new(false),
            current_child: Mutex::new(None),
            condvar: Condvar::new(),
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
    pub(crate) base_url: String,
    pub(crate) api_key: String,
    pub(crate) model: String,
    pub(crate) vision_model: String,
}

struct CollectionBatchItem {
    id: u64,
    input: String,
    title: String,
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
        Self {
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
        }
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
        }
    }

    pub fn call(&self, method: &str, params: Value) -> Option<Result<Value, String>> {
        let result = match method {
            "system.ping" => Ok(json!("pong")),
            "system.info" => self.system_info(),
            "system.snapshot" => self.system_snapshot(),
            "system.capabilities" => self.system_capabilities(),
            "system.open_url" => self.system_open_url(params),
            "system.shutdown" => Ok(json!(true)),
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
            "settings.vision.test" => self.vision_test(params),
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
            "notes.get_by_path" => self.notes_get_by_path(params),
            "notes.update" => self.notes_update(params),
            "notes.delete" => self.notes_delete(params),
            "notes.open" => self.notes_open(params),
            "notes.reveal" => self.notes_reveal(params),
            "collection.list" => self.collection_list(),
            "collection.get" => self.collection_get(params),
            "collection.create" => self.collection_create(params),
            "collection.update" => self.collection_update(params),
            "collection.delete" => self.collection_delete(params),
            "collection.list_items" => self.collection_list_items(params),
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

    fn system_snapshot(&self) -> Result<Value, String> {
        Ok(json!({
            "engine_version": env!("CARGO_PKG_VERSION"),
            "protocol_version": 1,
            "engine_kind": "rust-native",
            "timestamp": Utc::now().to_rfc3339(),
        }))
    }

    fn system_capabilities(&self) -> Result<Value, String> {
        Ok(json!({
            "has_ffmpeg": tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir),
            "has_ytdlp": tool_exists("yt-dlp", &["download-tools"], &self.runtime_dir),
            "has_gui": true,
        }))
    }

    fn settings_get(&self) -> Result<Value, String> {
        let raw = self.read_settings();
        let template = string_value(&raw, "template")
            .or_else(|| string_value(&raw, "template_id"))
            .unwrap_or_else(|| "default".to_string());
        let active_provider = string_value(&raw, "active_provider").unwrap_or_default();
        Ok(json!({
            "output_dir": string_value(&raw, "output_dir").unwrap_or_else(|| self.default_export_dir.to_string_lossy().to_string()),
            "compile_mode": string_value(&raw, "compile_mode").unwrap_or_else(|| "precision".to_string()),
            "template": template,
            "template_id": template,
            "vault_path": string_value(&raw, "vault_path").unwrap_or_default(),
            "active_provider": active_provider,
            "providers": provider_profiles(&raw, &active_provider),
            "bindings": raw.get("bindings").cloned().unwrap_or_else(|| json!({})),
            "provider": raw.get("provider").cloned().unwrap_or(Value::Null),
            "ai_model": raw.get("ai_model").cloned().unwrap_or(Value::Null),
            "base_url": raw.get("base_url").cloned().unwrap_or(Value::Null),
            "bilibili_cookie_file": string_value(&raw, "bilibili_cookie_file")
                .or_else(|| string_value(&raw, "bilibili_cookies"))
                .unwrap_or_default(),
            "draft_model_path": string_value(&raw, "draft_model_path").unwrap_or_default(),
        }))
    }

    fn settings_update(&self, params: Value) -> Result<Value, String> {
        let patches = params
            .get("patches")
            .and_then(Value::as_object)
            .or_else(|| params.as_object())
            .ok_or_else(|| "patches must be an object".to_string())?;
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
            "draft_model_path",
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
        Ok(json!(true))
    }

    fn settings_secret_set(&self, params: Value) -> Result<Value, String> {
        let provider = required_string(&params, "provider")?;
        let key = string_param(&params, "api_key")
            .or_else(|| string_param(&params, "key"))
            .ok_or_else(|| "api_key is required".to_string())?;
        self.update_settings(|raw| {
            let profile = find_provider_mut(raw, &provider)?;
            profile.insert("api_key".to_string(), json!(key));
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn settings_secret_delete(&self, params: Value) -> Result<Value, String> {
        let provider = required_string(&params, "provider")?;
        self.update_settings(|raw| {
            let profile = find_provider_mut(raw, &provider)?;
            profile.remove("api_key");
            Ok(())
        })?;
        Ok(json!(true))
    }

    fn providers_list(&self) -> Result<Value, String> {
        let raw = self.read_settings();
        let active = string_value(&raw, "active_provider").unwrap_or_default();
        Ok(json!(provider_profiles(&raw, &active)))
    }

    fn providers_create(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
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
        if let Some(api_key) = string_param(&params, "api_key") {
            if !api_key.is_empty() {
                entry.insert("api_key".to_string(), json!(api_key));
            }
        }
        self.update_settings(|raw| {
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
        })?;
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
        let raw = self.read_settings();
        let cache_provider = capability_cache_provider_name(&raw, &params);
        let profile = provider_profile_for_request(&raw, &params)?;
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
        let raw = self.read_settings();
        let profile = provider_profile_for_request(&raw, &params)?;
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

    fn vision_test(&self, params: Value) -> Result<Value, String> {
        let raw = self.read_settings();
        let cache_provider = capability_cache_provider_name(&raw, &params);
        let profile = provider_profile_for_request(&raw, &params)?;
        let model = string_param(&params, "vision_model")
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| {
                if profile.vision_model.trim().is_empty() {
                    &profile.model
                } else {
                    &profile.vision_model
                }
                .to_string()
            });

        // Determine provider kind to dispatch to the correct API format
        let active_name = string_value(&raw, "active_provider").unwrap_or_default();
        let provider_type = raw
            .get("providers")
            .and_then(Value::as_array)
            .and_then(|providers| {
                providers.iter().find(|p| {
                    p.get("name").and_then(Value::as_str) == Some(&active_name)
                })
            })
            .and_then(|p| p.get("type").and_then(Value::as_str))
            .unwrap_or("openai_compat");
        let provider_kind = crate::compile::client::ProviderKind::from_type_str(provider_type);

        let client = match reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
        {
            Ok(client) => client,
            Err(error) => {
                let (capability_cache_saved, capability_cache_error) = self
                    .maybe_update_provider_capability(
                        cache_provider.as_deref(),
                        &model,
                        "vision",
                        "fail",
                        "HTTP client init failed",
                        None,
                    );
                return Ok(json!({
                    "success": false,
                    "model": model,
                    "message": "HTTP client init failed",
                    "error": error.to_string(),
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }));
            }
        };

        let b64 = VISION_TEST_IMAGE_BASE64;

        // Dispatch to the correct provider format
        let (request_url, request_body): (String, Value) = match provider_kind {
            crate::compile::client::ProviderKind::GoogleGemini => {
                let url = format!(
                    "{}/models/{}:generateContent",
                    profile.base_url.trim_end_matches('/'),
                    model
                );
                let body = json!({
                    "system_instruction": {
                        "parts": [{"text": "You are a vision assistant. Describe what you see."}]
                    },
                    "contents": [{
                        "role": "user",
                        "parts": [
                            {"text": "Describe what you see in this image in one short sentence."},
                            {"inline_data": {"mime_type": "image/png", "data": b64}}
                        ]
                    }],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 100}
                });
                (url, body)
            }
            crate::compile::client::ProviderKind::Anthropic => {
                let url = format!(
                    "{}/messages",
                    profile.base_url.trim_end_matches('/')
                );
                let body = json!({
                    "model": model,
                    "system": "You are a vision assistant. Describe what you see.",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe what you see in this image in one short sentence."},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
                        ]
                    }],
                    "temperature": 0.1,
                    "max_tokens": 100
                });
                (url, body)
            }
            _ => {
                // OpenAI Compatible (default)
                let url = format!(
                    "{}/chat/completions",
                    profile.base_url.trim_end_matches('/')
                );
                let body = json!({
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe what you see in this image in one short sentence."},
                            {"type": "image_url", "image_url": {"url": format!("data:image/png;base64,{b64}")}}
                        ]
                    }],
                    "temperature": 0.1,
                    "max_tokens": 100
                });
                (url, body)
            }
        };

        // Send the request
        let response = match Self::send_provider_request(&client, &request_url, &request_body, &profile.api_key, provider_kind) {
            Ok(resp) => resp,
            Err(error) => {
                let (capability_cache_saved, capability_cache_error) = self
                    .maybe_update_provider_capability(
                        cache_provider.as_deref(),
                        &model,
                        "vision",
                        "fail",
                        "HTTP request failed",
                        None,
                    );
                return Ok(json!({
                    "success": false,
                    "model": model,
                    "message": "HTTP request failed",
                    "error": error,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }));
            }
        };

        let status = response.status();
        if !status.is_success() {
            let body = response.text().unwrap_or_default();
            let message = format!("Vision model returned HTTP {status}");
            let error = body.chars().take(500).collect::<String>();
            let (capability_cache_saved, capability_cache_error) = self
                .maybe_update_provider_capability(
                    cache_provider.as_deref(),
                    &model,
                    "vision",
                    "fail",
                    &format!("HTTP {}", status.as_u16()),
                    None,
                );
            return Ok(json!({
                "success": false,
                "model": model,
                "message": message,
                "error": error,
                "capability_cache_saved": capability_cache_saved,
                "capability_cache_error": capability_cache_error,
            }));
        }

        let payload_text = response.text().unwrap_or_default();
        let payload: Value = match serde_json::from_str(&payload_text) {
            Ok(v) => v,
            Err(e) => {
                let (capability_cache_saved, capability_cache_error) = self
                    .maybe_update_provider_capability(
                        cache_provider.as_deref(),
                        &model,
                        "vision",
                        "fail",
                        "Invalid JSON response",
                        None,
                    );
                return Ok(json!({
                    "success": false,
                    "model": model,
                    "message": "Response is not valid JSON",
                    "error": format!("{e}: {}", payload_text.chars().take(300).collect::<String>()),
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }));
            }
        };

        // Extract text based on provider kind
        let text = Self::extract_vision_text(&payload, provider_kind);

        match text {
            Some(content) => {
                let result = content.chars().take(200).collect::<String>();
                let (capability_cache_saved, capability_cache_error) = self
                    .maybe_update_provider_capability(
                        cache_provider.as_deref(),
                        &model,
                        "vision",
                        "pass",
                        "Vision model is available",
                        None,
                    );
                Ok(json!({
                    "success": true,
                    "model": model,
                    "message": "Vision model is available",
                    "result": result,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }))
            }
            None => {
                let message = "Vision model returned no content";
                let error = format!(
                    "response payload: {}",
                    payload_text.chars().take(500).collect::<String>()
                );
                let (capability_cache_saved, capability_cache_error) = self
                    .maybe_update_provider_capability(
                        cache_provider.as_deref(),
                        &model,
                        "vision",
                        "fail",
                        "No content returned",
                        None,
                    );
                Ok(json!({
                    "success": false,
                    "model": model,
                    "message": message,
                    "error": error,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }))
            }
        }
    }

    /// Send a provider request with the appropriate auth header.
    fn send_provider_request(
        client: &reqwest::blocking::Client,
        url: &str,
        body: &Value,
        api_key: &str,
        kind: crate::compile::client::ProviderKind,
    ) -> Result<reqwest::blocking::Response, String> {
        use crate::compile::client::ProviderKind;
        let req = client.post(url);
        let req = match kind {
            ProviderKind::GoogleGemini => req.header("x-goog-api-key", api_key),
            ProviderKind::Anthropic => req
                .header("x-api-key", api_key)
                .header("anthropic-version", "2023-06-01"),
            _ => crate::native_engine::with_optional_bearer(req, api_key),
        };
        req.json(body).send().map_err(|e| e.to_string())
    }

    /// Extract response text from a provider response based on provider kind.
    fn extract_vision_text(
        payload: &Value,
        kind: crate::compile::client::ProviderKind,
    ) -> Option<String> {
        use crate::compile::client::ProviderKind;
        match kind {
            ProviderKind::GoogleGemini => payload
                .pointer("/candidates/0/content/parts/0/text")
                .and_then(Value::as_str),
            ProviderKind::Anthropic => payload
                .pointer("/content/0/text")
                .and_then(Value::as_str),
            _ => {
                // OpenAI Compatible: choices[0].message.content or .reasoning
                payload
                    .get("choices")
                    .and_then(Value::as_array)
                    .and_then(|c| c.first())
                    .and_then(|c| c.get("message"))
                    .and_then(|m| {
                        m.get("content").or_else(|| m.get("reasoning"))
                    })
                    .and_then(Value::as_str)
            }
        }
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
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

    fn maybe_update_provider_capability(
        &self,
        provider: Option<&str>,
        model: &str,
        capability: &str,
        status: &str,
        message: &str,
        error: Option<&str>,
    ) -> (bool, Option<String>) {
        match provider {
            Some(provider) => {
                self.update_provider_capability(provider, model, capability, status, message, error)
            }
            None => (
                false,
                Some("Ad-hoc provider test is not cached".to_string()),
            ),
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
            let installed_version = if installed && missing.is_empty() {
                component_runtime_version(component, &component_path)
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
                "status": if installed && missing.is_empty() { "ok" } else if installed { "missing_files" } else { "not_installed" },
                "size_mb": manifest.get("size_mb").cloned().unwrap_or(Value::Null),
                "component_path": component_path.to_string_lossy(),
                "provides": manifest.get("provides").cloned().unwrap_or_else(|| json!([])),
                "missing_files": missing,
                "downloadable": manifest_string(&manifest, "download_url").is_some(),
            }));
        }
        Ok(json!(result))
    }

    /// Check all installed downloadable components for updates via GitHub API.
    /// Called manually by the frontend "检查更新" button — never auto-triggered.
    /// Fails gracefully: if the GitHub API is unreachable, no update is shown.
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
            // Compare the actual binary version against the latest upstream
            // GitHub release tag. If either is unavailable (binary not found,
            // network down, timeout), skip silently.
            let installed_version = if installed {
                component_runtime_version(component, &component_path)
                    .unwrap_or_default()
            } else {
                String::new()
            };
            let latest_version = component_latest_version(&manifest).unwrap_or_default();
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
        let ok = component_path.is_dir() && missing.is_empty();
        Ok(json!({
            "ok": ok,
            "components": [{
                "component": component,
                "ok": ok,
                "status": if ok { "ok" } else { "missing_files" },
                "missing_files": missing,
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
        let target = self.runtime_dir.join("components").join(&component);
        if target.exists() {
            fs::remove_dir_all(&target).map_err(|error| error.to_string())?;
        }
        Ok(json!({ "ok": true, "component": component, "status": "removed" }))
    }

    fn storage_status(&self) -> Result<Value, String> {
        let raw = self.read_settings();
        let export_dir = effective_note_output_dir(&raw, &self.default_export_dir);
        let jobs_root = self.data_dir.join("jobs");
        let legacy_jobs_root = self.data_dir.join(".jobs");
        let vault_path = string_value(&raw, "vault_path").unwrap_or_default();
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
            "vault_path": vault_path,
            "sizes": {
                "exports": dir_size(&export_dir),
                "jobs": dir_size(&jobs_root),
                "legacy_jobs": dir_size(&legacy_jobs_root),
                "runtime": dir_size(&self.runtime_dir),
            },
            "counts": {
                "exports": dir_counts(&export_dir),
                "jobs": dir_counts(&jobs_root),
                "legacy_jobs": dir_counts(&legacy_jobs_root),
                "runtime": dir_counts(&self.runtime_dir),
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
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let known_ids = jobs.iter().map(|job| job.id).collect::<HashSet<_>>();
        let running_ids = jobs
            .iter()
            .filter(|job| {
                matches!(
                    job.status.as_str(),
                    "pending" | "running" | "pausing" | "cancelling" | "paused"
                )
            })
            .map(|job| job.id)
            .collect::<HashSet<_>>();
        drop(jobs);

        let mut removed = 0;
        for root in [self.data_dir.join("jobs"), self.data_dir.join(".jobs")] {
            removed += cleanup_workspace_dirs(&root, |dir, job_id| {
                if running_ids.contains(&job_id) || known_ids.contains(&job_id) {
                    return false;
                }
                workspace_is_older_than(dir, min_age)
            })?;
        }
        Ok(json!({ "removed": removed }))
    }

    fn storage_cleanup_completed(&self) -> Result<Value, String> {
        let jobs = self
            .jobs
            .lock()
            .map_err(|_| "jobs lock poisoned".to_string())?;
        let completed_ids = jobs
            .iter()
            .filter(|job| job.status == "completed")
            .map(|job| job.id)
            .collect::<HashSet<_>>();
        drop(jobs);

        let mut removed = 0;
        for root in [self.data_dir.join("jobs"), self.data_dir.join(".jobs")] {
            removed += cleanup_workspace_dirs(&root, |_, job_id| completed_ids.contains(&job_id))?;
        }
        Ok(json!({ "removed": removed }))
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
        let input = {
            let jobs = self
                .jobs
                .lock()
                .map_err(|_| "jobs lock poisoned".to_string())?;
            let job = jobs
                .iter()
                .find(|job| job.id == id)
                .ok_or_else(|| format!("Job {id} not found"))?;
            if !is_terminal_status(&job.status) {
                return Err(format!("Job {id} cannot be retried from {}", job.status));
            }
            job.input.clone()
        };
        // Route to compile pipeline
        self.compile_video(json!({ "input": input }))
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
        let notes = self
            .note_entries()?
            .into_iter()
            .filter(|note| {
                query.is_empty()
                    || note.title.to_lowercase().contains(&query)
                    || note.path.to_string_lossy().to_lowercase().contains(&query)
            })
            .map(|note| {
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
        note_detail(note)
    }

    fn notes_get_by_path(&self, params: Value) -> Result<Value, String> {
        let path = required_string(&params, "path")?;
        let path = PathBuf::from(path);
        if !path.is_file() {
            return Err(format!("Note not found: {}", path.display()));
        }
        // Constrain reads to known note output directories to prevent
        // arbitrary file access via crafted paths.
        let canonical = path.canonicalize().map_err(|e| e.to_string())?;
        let allowed_roots = [
            self.default_export_dir.canonicalize().unwrap_or_else(|_| self.default_export_dir.to_path_buf()),
            effective_note_output_dir(&self.read_settings(), &self.default_export_dir)
                .canonicalize()
                .unwrap_or_else(|_| self.default_export_dir.to_path_buf()),
        ];
        let is_allowed = allowed_roots.iter().any(|root| canonical.starts_with(root));
        if !is_allowed {
            return Err(format!("Access denied: path is outside note output directories"));
        }
        note_detail(note_entry_from_path(path)?)
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
        let mut store = self.read_collection_store();
        let jobs = self
            .jobs
            .lock()
            .map(|jobs| jobs.clone())
            .unwrap_or_default();
        if let Some(collections) = store.get_mut("collections").and_then(Value::as_array_mut) {
            for collection in collections {
                let id = collection.get("id").and_then(Value::as_u64).unwrap_or(0);
                if collection_id.is_some() && collection_id != Some(id) {
                    continue;
                }
                sync_collection_value_from_jobs(collection, &jobs);
            }
        }
        self.write_collection_store(store.clone())?;
        Ok(store)
    }

    fn collection_create(&self, params: Value) -> Result<Value, String> {
        let name = string_param(&params, "name")
            .or_else(|| string_param(&params, "title"))
            .ok_or_else(|| "name is required".to_string())?;
        let mut store = self.read_collection_store();
        let id = next_store_id(&mut store, "next_collection_id");
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
        self.write_collection_store(store)?;
        Ok(json!({ "id": id, "name": name }))
    }

    fn collection_update(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let mut store = self.read_collection_store();
        let collection = find_collection_mut(&mut store, id)
            .ok_or_else(|| format!("Collection {id} not found"))?;
        if let Some(name) = string_param(&params, "name").or_else(|| string_param(&params, "title"))
        {
            collection["name"] = json!(name);
        }
        self.write_collection_store(store)?;
        Ok(json!(true))
    }

    fn collection_delete(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let mut store = self.read_collection_store();
        let collections = store
            .entry("collections".to_string())
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or_else(|| "collections must be an array".to_string())?;
        let old_len = collections.len();
        collections.retain(|collection| collection.get("id").and_then(Value::as_u64) != Some(id));
        let removed = collections.len() != old_len;
        self.write_collection_store(store)?;
        Ok(json!(removed))
    }

    fn collection_list_items(&self, params: Value) -> Result<Value, String> {
        let detail = self.collection_get(params)?;
        Ok(detail.get("items").cloned().unwrap_or_else(|| json!([])))
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
        let mut store = self.read_collection_store();
        let collection = find_collection_mut(&mut store, id)
            .ok_or_else(|| format!("Collection {id} not found"))?;
        let collection = collection
            .as_object_mut()
            .ok_or_else(|| "collection must be an object".to_string())?;
        let items = collection
            .entry("items".to_string())
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or_else(|| "items must be an array".to_string())?;
        let mut next_id = items
            .iter()
            .filter_map(|item| item.get("id").and_then(Value::as_u64))
            .max()
            .unwrap_or(0)
            + 1;
        for input in new_inputs {
            items.push(collection_item(next_id, &input));
            next_id += 1;
        }
        let result = json!(items.clone());
        self.write_collection_store(store)?;
        Ok(result)
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
        let mut store = self.read_collection_store();
        let collection = find_collection_mut(&mut store, id)
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
        self.write_collection_store(store)?;
        Ok(json!(true))
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
        let output_dir = string_value(&settings, "output_dir")
            .map(PathBuf::from)
            .unwrap_or_else(|| self.default_export_dir.clone());
        fs::create_dir_all(&output_dir).map_err(|error| error.to_string())?;
        let path = output_dir.join(format!("collection-{}-{}.md", id, sanitize_filename(name)));
        let mut body = format!("# {name}\n\n");
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
            let input = item.get("input").and_then(Value::as_str).unwrap_or("");
            let status = item
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("pending");
            body.push_str(&format!("- [{status}] {title}: `{input}`\n"));
        }
        fs::write(&path, body).map_err(|error| error.to_string())?;
        Ok(json!({ "path": path.to_string_lossy() }))
    }

    fn collection_batch_process(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let items = detail
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let items = items
            .into_iter()
            .filter_map(|item| {
                let item_id = item.get("id")?.as_u64()?;
                let input = item.get("input")?.as_str()?.trim().to_string();
                if input.is_empty() {
                    return None;
                }
                // Skip items that already have a non-terminal running job
                let run_id = item.get("run_id").and_then(Value::as_u64);
                let status = item.get("status").and_then(Value::as_str).unwrap_or("");
                if run_id.is_some() && !is_terminal_status(status) {
                    return None;
                }
                let title = item
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                Some(CollectionBatchItem {
                    id: item_id,
                    input,
                    title,
                })
            })
            .collect::<Vec<_>>();
        if items.is_empty() {
            return Err("collection has no processable items".to_string());
        }
        let max_concurrency = params
            .get("opts")
            .and_then(|opts| opts.get("max_concurrency"))
            .or_else(|| params.get("max_concurrency"))
            .and_then(Value::as_u64)
            .unwrap_or(1)
            .clamp(1, 2) as usize;
        let collection_name = detail
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("collection");
        let output_dir = collection_output_dir(
            &self.read_settings(),
            &self.default_export_dir,
            collection_name,
            id,
        );
        let count = items.len();
        let batch_job_id = format!("batch-{id}-{}", Utc::now().timestamp());
        let runner = self.clone();
        let _ = self.set_collection_status(id, "processing");
        let batch_output_dir = output_dir.clone();
        std::thread::spawn(move || {
            if let Err(panic) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                runner.run_collection_batch(id, items, max_concurrency, batch_output_dir);
            })) {
                let _ = panic;
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
        let items = detail.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut paused = 0u32;
        for item in &items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let status = item.get("status").and_then(Value::as_str).unwrap_or("");
            if let Some(run_id) = run_id {
                if matches!(status, "pending" | "running") {
                    if self.process_pause(json!({ "job_id": run_id })).is_ok() {
                        paused += 1;
                    }
                }
            }
        }
        Ok(json!({ "paused": paused }))
    }

    fn collection_resume_all(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let items = detail.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut resumed = 0u32;
        for item in &items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let status = item.get("status").and_then(Value::as_str).unwrap_or("");
            if let Some(run_id) = run_id {
                if matches!(status, "pausing" | "paused") {
                    if self.process_resume(json!({ "job_id": run_id })).is_ok() {
                        resumed += 1;
                    }
                }
            }
        }
        Ok(json!({ "resumed": resumed }))
    }

    fn collection_cancel_all(&self, params: Value) -> Result<Value, String> {
        let id = required_u64(&params, "id")?;
        let detail = self.collection_get(json!({ "id": id }))?;
        let items = detail.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut cancelled = 0u32;
        for item in &items {
            let run_id = item.get("run_id").and_then(Value::as_u64);
            let status = item.get("status").and_then(Value::as_str).unwrap_or("");
            if let Some(run_id) = run_id {
                if matches!(status, "pending" | "running" | "pausing" | "paused" | "cancelling") {
                    if self.process_cancel(json!({ "job_id": run_id })).is_ok() {
                        cancelled += 1;
                    }
                }
            }
        }
        Ok(json!({ "cancelled": cancelled }))
    }

    fn run_collection_batch(
        &self,
        id: u64,
        items: Vec<CollectionBatchItem>,
        max_concurrency: usize,
        _output_dir: PathBuf,
    ) {
        // Note: output_dir is unused — compile pipeline stores capsules in .capsules/
        // and renders notes on demand via compile.render.
        let mut pending = items.into_iter();
        struct ActiveItem {
            handle: Option<std::thread::JoinHandle<()>>,
        }
        let mut active: Vec<ActiveItem> = Vec::new();
        let mut pending_done = false;

        loop {
            // Check if collection was deleted mid-batch; stop processing if so
            {
                let store = self.read_collection_store();
                if find_collection(&store, id).is_none() {
                    break;
                }
            }

            // Spawn new items up to max_concurrency
            while active.len() < max_concurrency && !pending_done {
                match pending.next() {
                    Some(item) => {
                        let item_id = item.id;
                        let _ = self.update_collection_item_start(id, item_id, 0);
                        let runner = self.clone();
                        let input = item.input;
                        let title = item.title;
                        let handle = std::thread::spawn(move || {
                            let result = runner.compile_video(json!({
                                "input": input,
                                "title": title,
                            }));
                            match result {
                                Ok(value) => {
                                    let capsule_id = value
                                        .get("capsule_id")
                                        .and_then(Value::as_str)
                                        .unwrap_or("");
                                    let _ = runner.update_collection_item(id, item_id, |item| {
                                        item["status"] = json!("completed");
                                        item["progress"] = json!(100);
                                        item["capsule_id"] = json!(capsule_id);
                                        if let Some(obj) = item.as_object_mut() {
                                            obj.remove("error_message");
                                        }
                                    });
                                }
                                Err(error) => {
                                    let _ = runner.update_collection_item_failed(id, item_id, &error);
                                }
                            }
});
                        active.push(ActiveItem {
                            handle: Some(handle),
                        });
                    }
                    None => pending_done = true,
                }
            }

            // Clean up finished threads
            active.retain(|item| {
                if let Some(ref handle) = item.handle {
                    if handle.is_finished() {
                        return false; // remove from active
                    }
                }
                true
            });

            if active.is_empty() && pending_done {
                break;
            }

            std::thread::sleep(Duration::from_millis(500));
        }
        let _ = self.refresh_collection_status(id);
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
                object.remove("error_message");
                object.remove("output_path");
            }
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
        let mut store = self.read_collection_store();
        let collection = find_collection_mut(&mut store, collection_id)
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
        self.write_collection_store(store)
    }

    fn set_collection_status(&self, id: u64, status: &str) -> Result<(), String> {
        let mut store = self.read_collection_store();
        let collection = find_collection_mut(&mut store, id)
            .ok_or_else(|| format!("Collection {id} not found"))?;
        collection["status"] = json!(status);
        self.write_collection_store(store)
    }

    fn refresh_collection_status(&self, id: u64) -> Result<(), String> {
        let mut store = self.read_collection_store();
        let collection = find_collection_mut(&mut store, id)
            .ok_or_else(|| format!("Collection {id} not found"))?;
        collection["status"] = json!(aggregate_collection_status(collection));
        self.write_collection_store(store)
    }

    fn read_collection_store(&self) -> Map<String, Value> {
        read_json_file(&self.collection_store_path())
            .ok()
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_else(|| {
                let mut store = Map::new();
                store.insert("next_collection_id".to_string(), json!(1));
                store.insert("collections".to_string(), json!([]));
                store
            })
    }

    fn write_collection_store(&self, store: Map<String, Value>) -> Result<(), String> {
        write_json_atomic(&self.collection_store_path(), &Value::Object(store))
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

    fn read_settings(&self) -> Map<String, Value> {
        read_json_file(&self.settings_path)
            .ok()
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_default()
    }

    fn write_settings(&self, raw: Map<String, Value>) -> Result<(), String> {
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
        let mut raw = self.read_settings();
        let result = update(&mut raw)?;
        self.write_settings(raw)?;
        Ok(result)
    }

    fn study_knowledge(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("note_id")
            .and_then(Value::as_u64)
            .ok_or_else(|| "note_id is required".to_string())? as u32;
        let note = self.note_entries()?
            .into_iter()
            .find(|n| n.id == id)
            .ok_or_else(|| format!("Note {id} not found"))?;
        let content = std::fs::read_to_string(&note.path)
            .map_err(|e| format!("Failed to read note: {e}"))?;

        // Try AI-powered knowledge graph first
        let settings = self.read_settings();
        if let Ok(profile) = active_provider_profile(&settings) {
            match crate::study::knowledge::build_knowledge_graph_ai(&profile, &content) {
                Ok(kg) if kg.is_populated() => {
                    return Ok(serde_json::to_value(kg).unwrap_or_default());
                }
                _ => {}
            }
        }

        // Fallback: heading-based parsing → KnowledgeGraph
        let kg = crate::study::knowledge::build_knowledge_graph(&content);
        Ok(serde_json::to_value(kg).unwrap_or_default())
    }

    fn study_quiz(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("note_id")
            .and_then(Value::as_u64)
            .ok_or_else(|| "note_id is required".to_string())? as u32;
        let note = self.note_entries()?
            .into_iter()
            .find(|n| n.id == id)
            .ok_or_else(|| format!("Note {id} not found"))?;
        let content = std::fs::read_to_string(&note.path)
            .map_err(|e| format!("Failed to read note: {e}"))?;
        let settings = self.read_settings();
        let profile = active_provider_profile(&settings)
            .map_err(|e| format!("No active provider: {e}"))?;
        crate::study::quiz::generate_quiz(&profile, &content)
    }

    /// Start a multimodal compile pipeline on a video file.
    fn compile_video(&self, params: Value) -> Result<Value, String> {
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
        let runtime_dir = self.runtime_dir.clone();

        // Resolve ffmpeg/ffprobe paths
        let ffmpeg = crate::native_engine::resolve_tool_path(
            "ffmpeg", &["ffmpeg-tools"], &runtime_dir,
        ).ok_or_else(|| "ffmpeg not found; install ffmpeg-tools".to_string())?;
        let ffprobe = crate::native_engine::resolve_tool_path(
            "ffprobe", &["ffmpeg-tools"], &runtime_dir,
        ).ok_or_else(|| "ffprobe not found".to_string())?;

        // Provider config
        let provider = crate::native_engine::active_provider_profile(&settings).ok();
        let client_config = provider.as_ref().map(|p| {
            let active_name = crate::native_engine::string_value(&settings, "active_provider")
                .unwrap_or_default();
            let provider_type = settings
                .get("providers")
                .and_then(Value::as_array)
                .and_then(|providers| {
                    providers.iter().find(|p| {
                        p.get("name").and_then(Value::as_str) == Some(&active_name)
                    })
                })
                .and_then(|p| p.get("type").and_then(Value::as_str))
                .unwrap_or("openai_compat");

            crate::compile::client::CompileClientConfig::new(
                p.base_url.clone(),
                p.api_key.clone(),
                p.vision_model.clone(),
                crate::compile::client::ProviderKind::from_type_str(provider_type),
            )
        });

        let storage_dir = self.data_dir.join(".capsules");

        // Compute source hash
        let input_path = std::path::Path::new(&input);
        let source_hash = match std::fs::read(input_path) {
            Ok(bytes) => {
                use sha2::{Digest, Sha256};
                let mut hasher = Sha256::new();
                hasher.update(&bytes);
                format!("{:x}", hasher.finalize())
            }
            Err(_) => {
                use sha2::{Digest, Sha256};
                let mut hasher = Sha256::new();
                hasher.update(input.as_bytes());
                format!("{:x}", hasher.finalize())
            }
        };

        let prefer_draft = crate::native_engine::string_param(&params, "mode")
            .map(|m| m == "draft")
            .unwrap_or(false);
        let gguf_model_path = crate::native_engine::string_value(&settings, "draft_model_path")
            .map(std::path::PathBuf::from);

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
            progress_message: "准备编译".to_string(),
            stage: "pending".to_string(),
            input: input.clone(),
            created_at: chrono::Utc::now().to_rfc3339(),
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
            artifact_cleanup_policy: "keep_all".to_string(),
            note_id: None,
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

        // Spawn background compile thread
        let jobs = self.jobs.clone();
        let jobs_state_path = self.jobs_state_path.clone();
        let thread_input = input.clone();
        let thread_title = title.clone();
        let thread_source_hash = source_hash.clone();
        let app_handle = self.app_handle.clone();
        let export_dir = self.default_export_dir.clone();

        std::thread::spawn(move || {
            let set_job = |status: &str, stage: &str, progress: u8, message: &str| {
                if let Ok(mut guard) = jobs.lock() {
                    if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                        job.status = status.to_string();
                        job.stage = stage.to_string();
                        job.progress = progress;
                        job.progress_message = message.to_string();
                        if status == "completed" || status == "failed"
                            || status == "cancelled" || status == "interrupted"
                        {
                            job.completed_at = Some(chrono::Utc::now().to_rfc3339());
                        }
                    }
                    let _ = save_jobs(&jobs_state_path, &guard);
                }
                if let Some(ref handle) = app_handle {
                    let _ = handle.emit("job:progress", json!({
                        "event_id": chrono::Utc::now().timestamp_millis(),
                        "job_id": id,
                        "stable_job_id": null,
                        "status": status,
                        "stage": stage,
                        "progress": progress,
                        "message": message,
                        "timestamp": chrono::Utc::now().to_rfc3339(),
                    }));
                }
            };

            set_job("running", "resolving", 5, "检查输入文件");

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
                        let _ = handle.emit("job:progress", json!({
                            "event_id": chrono::Utc::now().timestamp_millis(),
                            "job_id": id,
                            "stable_job_id": null,
                            "status": "running",
                            "stage": stage,
                            "progress": pct,
                            "message": msg,
                            "timestamp": chrono::Utc::now().to_rfc3339(),
                        }));
                    }
                }
            };

            let storage_dir_for_render = storage_dir.clone();
            let opts = crate::compile::engine::CompileOptions {
                ffmpeg_path: ffmpeg,
                ffprobe_path: ffprobe,
                storage_dir,
                sampler: crate::compile::SamplerOptions::default(),
                client_config,
                prefer_draft,
                on_progress: Some(Box::new(progress_cb)),
                gguf_model_path,
            };

            let result = crate::compile::engine::compile_video(
                std::path::Path::new(&thread_input),
                &thread_source_hash,
                &thread_title,
                &opts,
            );

            match result {
                Ok(compile_result) => {
                    // Render capsule to markdown and write to export directory
                    let store = crate::compile::storage::FileCapsuleStore::new(storage_dir_for_render);
                    if let Ok(capsule) = store.get(&compile_result.source_hash, compile_result.version) {
                        match crate::compile::renderer::render(&capsule, "markdown") {
                            Ok(markdown) => {
                                let _ = std::fs::create_dir_all(&export_dir);
                                let safe_name: String = thread_title.chars()
                                    .map(|c| if c.is_alphanumeric() || c == ' ' || c == '-' || c == '_' { c } else { '_' })
                                    .collect();
                                let file_name = format!("{}-v{}.md", safe_name.trim(), compile_result.version);
                                let output_path = export_dir.join(&file_name);
                                if std::fs::write(&output_path, &markdown).is_ok() {
                                    if let Ok(mut guard) = jobs.lock() {
                                        if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                                            job.output_path = Some(output_path.to_string_lossy().to_string());
                                            job.note_id = Some(crate::native_engine::note_id(&output_path));
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
                    set_job("failed", "failed", 100, &error);
                    if let Ok(mut guard) = jobs.lock() {
                        if let Some(job) = guard.iter_mut().find(|j| j.id == id) {
                            job.error_message = Some(error);
                        }
                        let _ = save_jobs(&jobs_state_path, &guard);
                    }
                }
            }
        });

        Ok(json!({ "job_id": id }))
    }

    /// List all compiled versions for a source hash.
    fn compile_list_versions(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let versions = store.list_versions(&source_hash)
            .map_err(|e| format!("list versions failed: {e}"))?;
        Ok(serde_json::to_value(&versions).unwrap_or_default())
    }

    /// Replay a specific compiled version.
    fn compile_replay(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let version = params.get("version")
            .and_then(Value::as_u64)
            .ok_or_else(|| "version is required".to_string())? as u32;
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let capsule = store.get(&source_hash, version)
            .map_err(|e| format!("replay failed: {e}"))?;
        Ok(serde_json::to_value(&capsule).unwrap_or_default())
    }

    /// Render a compiled capsule to Markdown / mindmap.
    fn compile_render(&self, params: Value) -> Result<Value, String> {
        let source_hash = crate::native_engine::required_string(&params, "source_hash")?;
        let version = params.get("version")
            .and_then(Value::as_u64)
            .ok_or_else(|| "version is required".to_string())? as u32;
        let template = crate::native_engine::string_param(&params, "template")
            .unwrap_or_else(|| "markdown".to_string());
        let storage_dir = self.data_dir.join(".capsules");
        let store = crate::compile::storage::FileCapsuleStore::new(storage_dir);
        let capsule = store.get(&source_hash, version)
            .map_err(|e| format!("render: capsule not found: {e}"))?;
        let output = crate::compile::renderer::render(&capsule, &template)
            .map_err(|e| format!("render failed: {e}"))?;
        Ok(serde_json::json!({ "content": output, "capsule_id": capsule.capsule_id, "template": template }))
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
            "frames_count": self.frames_count,
            "note_id": self.note_id,
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

fn jobs_state_path(data_dir: &Path) -> PathBuf {
    data_dir.join(".jobs").join("jobs.json")
}

fn load_jobs(jobs_state_path: &Path) -> Vec<NativeJob> {
    // Guard against unbounded memory: if jobs.json exceeds 16 MB, something
    // is wrong (likely thousands of stale jobs). Read anyway but log a warning.
    let mut jobs = fs::metadata(jobs_state_path)
        .ok()
        .filter(|m| m.len() <= 16 * 1024 * 1024)
        .and_then(|_| fs::read_to_string(jobs_state_path).ok())
        .or_else(|| {
            // Large file fallback: try reading anyway but warn
            match fs::read_to_string(jobs_state_path) {
                Ok(raw) => {
                    eprintln!("[warn] jobs.json is large (>16MB), consider cleaning old jobs");
                    Some(raw)
                }
                Err(_) => None,
            }
        })
        .and_then(|raw| serde_json::from_str::<Vec<NativeJob>>(&raw).ok())
        .unwrap_or_default();
    let now = Utc::now().to_rfc3339();
    let mut changed = false;
    for job in &mut jobs {
        if matches!(
            job.status.as_str(),
            "pending" | "running" | "pausing" | "cancelling"
        ) {
            job.status = "interrupted".to_string();
            job.stage = "interrupted".to_string();
            job.progress_message = format!(
                "应用重启时任务中断（进度 {}%）",
                job.progress
            );
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
                job.progress_message = format!(
                    "{}（应用重启后保持暂停）",
                    job.progress_message
                );
                changed = true;
            }
        }
    }
    if changed {
        let _ = save_jobs(jobs_state_path, &jobs);
    }
    jobs
}

static JOBS_SAVE_TIMES: std::sync::LazyLock<std::sync::Mutex<std::collections::HashMap<PathBuf, std::time::Instant>>> =
    std::sync::LazyLock::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

fn save_jobs(jobs_state_path: &Path, jobs: &[NativeJob]) -> Result<(), String> {
    // Rate-limit non-terminal saves to at most once per 2 seconds
    let has_terminal = jobs.iter().any(|j| is_terminal_status(&j.status));
    if !has_terminal {
        if let Ok(guard) = JOBS_SAVE_TIMES.lock() {
            if let Some(last) = guard.get(jobs_state_path) {
                if last.elapsed() < Duration::from_secs(2) {
                    return Ok(());
                }
            }
        }
    }
    let result = write_json_atomic(jobs_state_path, &json!(jobs));
    if result.is_ok() {
        if let Ok(mut guard) = JOBS_SAVE_TIMES.lock() {
            guard.insert(jobs_state_path.to_path_buf(), std::time::Instant::now());
        }
    }
    result
}

fn default_job_attempt() -> u32 {
    1
}

fn default_artifact_cleanup_policy() -> String {
    "keep_all".to_string()
}

fn normalize_artifact_cleanup_policy(policy: &str) -> String {
    match policy {
        "delete_workspace" => "delete_workspace".to_string(),
        _ => default_artifact_cleanup_policy(),
    }
}

fn cleanup_deleted_job_workspace(job: &NativeJob, data_dir: &Path) {
    if normalize_artifact_cleanup_policy(&job.artifact_cleanup_policy) != "delete_workspace" {
        return;
    }
    let Some(workspace_dir) = &job.workspace_dir else {
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
                    Some(json!({
                        "name": name,
                        "provider": object.get("type").and_then(Value::as_str).unwrap_or("openai_compat"),
                        "api_key_configured": !api_key.trim().is_empty(),
                        "api_key_preview": if api_key.len() > 8 { format!("{}…{}", &api_key[..4], &api_key[api_key.len() - 4..]) } else { "".to_string() },
                        "base_url": object.get("base_url").and_then(Value::as_str).unwrap_or(""),
                        "model": model,
                        "vision_model": object.get("vision_model").and_then(Value::as_str).unwrap_or(model),
                        "models": models,
                        "active": name.eq_ignore_ascii_case(active),
                        "capabilities": object.get("capabilities").cloned().unwrap_or_else(|| json!({})),
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

pub(crate) fn active_provider_profile(settings: &Map<String, Value>) -> Result<NativeProviderProfile, String> {
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
    let base_url = profile
        .get("base_url")
        .and_then(Value::as_str)
        .unwrap_or("https://api.openai.com/v1")
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
    if provider_type != "openai_compat"
        && provider_type != "openai"
        && provider_type != "dashscope"
        && provider_type != "mimo"
        && provider_type != "llama_cpp"
    {
        return Err(format!(
            "Native provider '{}' is not migrated yet; use OpenAI Compatible.",
            provider_type
        ));
    }
    Ok(NativeProviderProfile {
        base_url: if base_url.is_empty() {
            "https://api.openai.com/v1".to_string()
        } else {
            normalise_provider_base_url(&base_url)
        },
        api_key,
        model,
        vision_model,
    })
}

fn fetch_provider_models(profile: &NativeProviderProfile) -> Result<Vec<String>, String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|error| error.to_string())?;
    let url = format!("{}/models", profile.base_url.trim_end_matches('/'));
    let response = with_optional_bearer(client.get(url), &profile.api_key)
        .send()
        .map_err(|error| error.to_string())?;
    if !response.status().is_success() {
        return Err(format!("models endpoint returned {}", response.status()));
    }
    let payload: Value = response.json().map_err(|error| error.to_string())?;
    let models = payload
        .get("data")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.get("id").and_then(Value::as_str))
                .map(ToOwned::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
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

fn find_collection<'a>(store: &'a Map<String, Value>, id: u64) -> Option<&'a Value> {
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
    if let Some(items) = collection.get_mut("items").and_then(Value::as_array_mut) {
        for item in items {
            let Some(run_id) = item.get("run_id").and_then(Value::as_u64) else {
                continue;
            };
            let Some(job) = jobs.iter().find(|job| job.id == run_id) else {
                // Job was deleted (e.g. via process.delete); clear stale reference
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
        }
    }
    collection["status"] = json!(aggregate_collection_status(collection));
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
    if statuses.iter().any(|status| *status == "cancelling") {
        "cancelling"
    } else if statuses.iter().any(|status| *status == "pausing") {
        "pausing"
    } else if statuses
        .iter()
        .any(|status| matches!(*status, "pending" | "running"))
    {
        "processing"
    } else if statuses.iter().any(|status| *status == "paused") {
        "paused"
    } else if statuses.iter().all(|status| *status == "completed") {
        "completed"
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
    let exe = executable_name(name);
    for component in components {
        if runtime_dir
            .join("components")
            .join(component)
            .join(&exe)
            .is_file()
        {
            return true;
        }
    }
    hidden_command(&exe)
        .arg("--version")
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn hidden_command(program: impl AsRef<OsStr>) -> Command {
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

fn component_latest_version(manifest: &Value) -> Option<String> {
    let url = manifest_string(manifest, "download_url")?;
    let (owner, repo) = github_repo_from_url(&url)?;
    let api_url = format!("https://api.github.com/repos/{owner}/{repo}/releases/latest");
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .ok()?;
    let response = client
        .get(api_url)
        .header("User-Agent", "Video-Notes-AI")
        .send()
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    let payload: Value = response.json().ok()?;
    payload
        .get("tag_name")
        .or_else(|| payload.get("name"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
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

fn resolve_tool_path(name: &str, components: &[&str], runtime_dir: &Path) -> Option<PathBuf> {
    let exe = executable_name(name);
    for component in components {
        let path = runtime_dir.join("components").join(component).join(&exe);
        if path.is_file() {
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


#[allow(dead_code)]
fn simple_pdf_bytes(text: &str) -> Vec<u8> {
    let content = format!("BT /F1 24 Tf 20 40 Td ({}) Tj ET", escape_pdf_text(text));
    let objects = [
        "<< /Type /Catalog /Pages 2 0 R >>".to_string(),
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_string(),
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 90] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_string(),
        format!("<< /Length {} >>\nstream\n{}\nendstream", content.len(), content),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_string(),
    ];
    let mut bytes = b"%PDF-1.4\n".to_vec();
    let mut offsets = vec![0usize];
    for (index, object) in objects.iter().enumerate() {
        offsets.push(bytes.len());
        bytes.extend_from_slice(format!("{} 0 obj\n{}\nendobj\n", index + 1, object).as_bytes());
    }
    let xref_offset = bytes.len();
    bytes.extend_from_slice(format!("xref\n0 {}\n", offsets.len()).as_bytes());
    bytes.extend_from_slice(b"0000000000 65535 f \n");
    for offset in offsets.iter().skip(1) {
        bytes.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    bytes.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n",
            offsets.len(),
            xref_offset
        )
        .as_bytes(),
    );
    bytes
}

#[allow(dead_code)]
fn escape_pdf_text(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('(', "\\(")
        .replace(')', "\\)")
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
    let result = hidden_command("cmd")
        .args(["/C", "start", "", &path.to_string_lossy()])
        .spawn();

    #[cfg(target_os = "macos")]
    let result = hidden_command("open").arg(path).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = hidden_command("xdg-open").arg(path).spawn();

    result.map_err(|error| error.to_string())?;
    Ok(json!(true))
}

fn open_url(url: &str) -> Result<Value, String> {
    let trimmed = url.trim();
    if !(trimmed.starts_with("https://") || trimmed.starts_with("http://")) {
        return Err("url must start with http:// or https://".to_string());
    }

    #[cfg(target_os = "windows")]
    let result = hidden_command("cmd")
        .args(["/C", "start", "", trimmed])
        .spawn();

    #[cfg(target_os = "macos")]
    let result = hidden_command("open").arg(trimmed).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = hidden_command("xdg-open").arg(trimmed).spawn();

    result.map_err(|error| error.to_string())?;
    Ok(json!(true))
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
    use sha2::{Sha256, Digest};
    let bytes = fs::read(path).map_err(|e| format!("failed to read file for hash check: {e}"))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let actual = hasher.finalize();
    let actual_hex = actual.iter().map(|b| format!("{b:02x}")).collect::<String>();
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
    let patterns = ["bearer ", "token=", "api_key=", "api-key=", "sessdata=", "authorization:"];
    let mut result = text.to_string();
    for pat in &patterns {
        if let Some(pos) = lower.find(pat) {
            let value_start = pos + pat.len();
            let value_end = result[value_start..]
                .find(|c: char| c.is_whitespace() || c == '&' || c == '"' || c == '\'')
                .map(|e| value_start + e)
                .unwrap_or(result.len());
            if value_end > value_start + 4 {
                result = format!("{}{}[REDACTED]{}", &result[..value_start], pat, &result[value_end..]);
            }
        }
    }
    result
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
    let reserved = ["CON", "PRN", "AUX", "NUL",
        "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
        "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9"];
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
    if component.contains('/')
        || component.contains('\\')
        || component.contains("..")
        || component.starts_with('.')
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
        let package_path = temp.join(download_filename(&url));
        download_file_with_fallback(&url, &package_path, component, handle)?;
        ensure_non_empty_file(&package_path, "component package")?;
        // Verify SHA256 hash if the manifest specifies one.
        if let Some(expected_hash) = manifest_string(manifest, "sha256") {
            if !expected_hash.is_empty() {
                verify_sha256(&package_path, &expected_hash)?;
            } else {
                eprintln!("[warn] component package has empty sha256 in manifest, skipping integrity check");
            }
        }
        let archive_type =
            manifest_string(manifest, "archive_type").unwrap_or_else(|| infer_archive_type(&url));
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
    let mut file = fs::File::create(target)
        .map_err(|error| format!("failed to create file: {error}"))?;
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
        file.write_all(&buffer[..n])
            .map_err(|error| format!("failed to write download: {error}"))?;
        downloaded += n as u64;
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
    let mut file = fs::File::create(target)
        .map_err(|error| format!("failed to create file: {error}"))?;
    response
        .copy_to(&mut file)
        .map_err(|error| format!("failed to write download: {error}"))?;
    Ok(())
}

fn download_file_with_curl(url: &str, target: &Path) -> Result<(), String> {
    let args = vec![
        "-L".to_string(),
        "--fail".to_string(),
        "--retry".to_string(),
        "2".to_string(),
        "--output".to_string(),
        target.to_string_lossy().to_string(),
        url.to_string(),
    ];
    let output = command_output("curl.exe", &args)?;
    if output.status.success() {
        Ok(())
    } else {
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
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
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

fn write_component_marker(manifest: &Value, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target).map_err(|error| error.to_string())?;
    let marker = json!({
        "component": manifest_string(manifest, "component").unwrap_or_default(),
        "manifest_version": manifest_string(manifest, "version").unwrap_or_default(),
        "installed_at": Utc::now().to_rfc3339(),
    });
    write_json_atomic(&component_marker_path(target), &marker)
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
