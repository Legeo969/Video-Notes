use base64::{engine::general_purpose, Engine as _};
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

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const YTDLP_DOWNLOAD_URL: &str =
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe";
const OCR_TEST_IMAGE_BASE64: &str =
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";
const VISION_TEST_IMAGE_BASE64: &str =
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAD6SURBVFhH7ZPrDcMgDIQ9HgMxDruwCpu4dkmrBB8oJRbpj3zS5eFIdwdJiG/mKfCHBUhGZ+SEdUJhSE5YJxSG5IR1QmFITlgnFIbkhHVCYUhOWCcUhuSEdUJhSE5YJxSG5IR1QmFITvg5TXKhQOZIgVPZbt/oLMrxPPMFcuQQI8dDg2UFCqegq9fzPnBVgZJk9TWmpMDhuwuLCuRIvOXXMiHJnii1wC8/zeBRDw0hMT2qFspyjQuoEJ1xn+OWb8gHSdKAaFxA1QJGI3rvuH6UCwr0QWFILWA0Dwps1QJG86DAVi1gdA0U+hGiM77G2XBl8GgNT4GbCzC/AGeNYW4AwZ2AAAAAAElFTkSuQmCC";
const PADDLEOCR_DEFAULT_MODEL: &str = "PaddleOCR-VL-1.6";
const PADDLEOCR_JOBS_PATH: &str = "/api/v2/ocr/jobs";
const VISION_PARALLELISM: usize = 3;
const DEFAULT_COMPONENT_MANIFESTS: &[(&str, &str)] = &[
    (
        "download-tools",
        include_str!("../../../runtime/manifests/download-tools.json"),
    ),
    (
        "ffmpeg-tools",
        include_str!("../../../runtime/manifests/ffmpeg-tools.json"),
    ),
    (
        "whisper-cpp-tools",
        include_str!("../../../runtime/manifests/whisper-cpp-tools.json"),
    ),
    (
        "whisper-cpp-cuda-tools",
        include_str!("../../../runtime/manifests/whisper-cpp-cuda-tools.json"),
    ),
    (
        "tesseract-ocr-tools",
        include_str!("../../../runtime/manifests/tesseract-ocr-tools.json"),
    ),
];

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
}

struct JobControl {
    cancel_requested: AtomicBool,
    pause_requested: AtomicBool,
    current_child: Mutex<Option<u32>>,
    lock: Mutex<()>,
    condvar: Condvar,
}

impl JobControl {
    fn new() -> Self {
        Self {
            cancel_requested: AtomicBool::new(false),
            pause_requested: AtomicBool::new(false),
            current_child: Mutex::new(None),
            lock: Mutex::new(()),
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
struct NativeProviderProfile {
    base_url: String,
    api_key: String,
    model: String,
    vision_model: String,
}

#[derive(Clone, Debug)]
struct OcrRuntimeConfig {
    enabled: bool,
    backend: String,
    endpoint: String,
    api_key: String,
    model: String,
}

struct CollectionBatchItem {
    id: u64,
    input: String,
    title: String,
}

#[derive(Clone, Debug)]
struct TimelineSegment {
    start_sec: f64,
    end_sec: f64,
    text: String,
    ocr_text: Option<String>,
    vision_summary: Option<String>,
    frame_paths: Vec<PathBuf>,
}

#[derive(Clone, Debug)]
struct FrameSampleResult {
    frames: Vec<PathBuf>,
    timestamps_sec: Vec<f64>,
    duration_sec: f64,
    interval_sec: f64,
    kept_count: u32,
    candidate_count: u32,
}

#[derive(Clone, Debug)]
struct FrameSamplingMetrics {
    duration_sec: f64,
    interval_sec: f64,
    kept_count: u32,
    candidate_count: u32,
}

struct OcrExtraction {
    text: String,
    frame_sampling: Option<FrameSamplingMetrics>,
}

impl From<&FrameSampleResult> for FrameSamplingMetrics {
    fn from(result: &FrameSampleResult) -> Self {
        Self {
            duration_sec: result.duration_sec,
            interval_sec: result.interval_sec,
            kept_count: result.kept_count,
            candidate_count: result.candidate_count,
        }
    }
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
            "settings.ocr.test" => self.ocr_test(params),
            "settings.vision.test" => self.vision_test(params),
            "settings.templates.list" => self.templates_list(),
            "settings.models.scan" | "settings.models.local" => self.local_models(),
            "settings.bindings.set" => self.bindings_set(params),
            "doctor.run" => self.doctor_run(),
            "diagnostics.bundle" => self.diagnostics_bundle(),
            "components.list" => self.components_list(),
            "components.verify" => self.components_verify(params),
            "components.install" => self.components_install(params),
            "components.remove" => self.components_remove(params),
            "storage.status" => self.storage_status(),
            "storage.cleanup_orphans" => self.storage_cleanup_orphans(params),
            "storage.cleanup_completed" => self.storage_cleanup_completed(),
            "process.list" => self.process_list(params),
            "process.start" => self.process_start(params),
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
            _ => return None,
        };
        Some(result)
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
        let settings = self.read_settings();
        Ok(json!({
            "has_ffmpeg": tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir),
            "has_ytdlp": tool_exists("yt-dlp", &["download-tools"], &self.runtime_dir),
            "has_whisper": false,
            "has_whisper_cpp": tool_exists("whisper-cli", &["whisper-cpp-cuda-tools", "whisper-cpp-tools"], &self.runtime_dir)
                || tool_exists("main", &["whisper-cpp-cuda-tools", "whisper-cpp-tools"], &self.runtime_dir),
            "has_ocr": tool_exists("tesseract", &["tesseract-ocr-tools"], &self.runtime_dir)
                || !string_value(&settings, "ocr_http_endpoint")
                    .or_else(|| string_value(&settings, "ocr_api_url"))
                    .unwrap_or_default()
                    .trim()
                    .is_empty(),
            "has_cuda": tool_exists("whisper-cli", &["whisper-cpp-cuda-tools"], &self.runtime_dir),
            "has_vision": tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir),
            "has_gui": true,
        }))
    }

    fn settings_get(&self) -> Result<Value, String> {
        let raw = self.read_settings();
        let template = string_value(&raw, "template")
            .or_else(|| string_value(&raw, "template_id"))
            .unwrap_or_else(|| "default".to_string());
        let model_dir = string_value(&raw, "whisper_model_dir")
            .or_else(|| string_value(&raw, "model_dir"))
            .unwrap_or_default();
        let active_provider = string_value(&raw, "active_provider").unwrap_or_default();
        Ok(json!({
            "output_dir": string_value(&raw, "output_dir").unwrap_or_else(|| self.default_export_dir.to_string_lossy().to_string()),
            "transcription_backend": "whisper_cpp",
            "whisper_model": string_value(&raw, "whisper_model").unwrap_or_else(|| "large-v3".to_string()),
            "whisper_model_dir": model_dir,
            "model_dir": model_dir,
            "whisper_device": string_value(&raw, "whisper_device").unwrap_or_else(|| "auto".to_string()),
            "language": string_value(&raw, "language").unwrap_or_default(),
            "frame_interval": raw.get("frame_interval").and_then(Value::as_i64).unwrap_or(30),
            "frame_mode": string_value(&raw, "frame_mode").unwrap_or_else(|| "fixed".to_string()),
            "max_frames": raw.get("max_frames").and_then(Value::as_i64).unwrap_or(30),
            "ocr_enabled": raw.get("ocr_enabled").and_then(Value::as_bool).unwrap_or(false),
            "ocr_backend": string_value(&raw, "ocr_backend").unwrap_or_else(|| "tesseract".to_string()),
            "ocr_http_endpoint": string_value(&raw, "ocr_http_endpoint")
                .or_else(|| string_value(&raw, "ocr_api_url"))
                .unwrap_or_default(),
            "ocr_http_api_key": string_value(&raw, "ocr_http_api_key")
                .or_else(|| string_value(&raw, "ocr_api_key"))
                .unwrap_or_default(),
            "ocr_model": string_value(&raw, "ocr_model").unwrap_or_else(|| PADDLEOCR_DEFAULT_MODEL.to_string()),
            "vision_enabled": raw.get("vision_enabled").and_then(Value::as_bool).unwrap_or(false),
            "template": template,
            "template_id": template,
            "detail_level": string_value(&raw, "detail_level").unwrap_or_else(|| "standard".to_string()),
            "vault_path": string_value(&raw, "vault_path").unwrap_or_default(),
            "export_mode": string_value(&raw, "export_mode").unwrap_or_else(|| "markdown".to_string()),
            "active_provider": active_provider,
            "providers": provider_profiles(&raw, &active_provider),
            "bindings": raw.get("bindings").cloned().unwrap_or_else(|| json!({})),
            "provider": raw.get("provider").cloned().unwrap_or(Value::Null),
            "ai_model": raw.get("ai_model").cloned().unwrap_or(Value::Null),
            "base_url": raw.get("base_url").cloned().unwrap_or(Value::Null),
            "vision_provider": raw.get("vision_provider").cloned().unwrap_or(Value::Null),
            "vision_model": raw.get("vision_model").cloned().unwrap_or(Value::Null),
            "vision_base_url": raw.get("vision_base_url").cloned().unwrap_or(Value::Null),
            "subtitle_format": string_value(&raw, "subtitle_format").unwrap_or_else(|| "none".to_string()),
            "bilibili_cookie_file": string_value(&raw, "bilibili_cookie_file")
                .or_else(|| string_value(&raw, "bilibili_cookies"))
                .unwrap_or_default(),
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
            "transcription_backend",
            "whisper_model",
            "whisper_model_dir",
            "model_dir",
            "whisper_device",
            "language",
            "frame_interval",
            "frame_mode",
            "max_frames",
            "ocr_enabled",
            "ocr_backend",
            "ocr_http_endpoint",
            "ocr_http_api_key",
            "ocr_model",
            "ocr_api_url",
            "ocr_api_key",
            "vision_enabled",
            "template",
            "template_id",
            "detail_level",
            "vault_path",
            "export_mode",
            "provider",
            "ai_model",
            "base_url",
            "vision_provider",
            "vision_model",
            "vision_base_url",
            "subtitle_format",
            "bilibili_cookie_file",
            "bilibili_cookies",
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
            if let Some(value) = raw.get("whisper_model_dir").cloned() {
                raw.entry("model_dir".to_string()).or_insert(value);
            }
            raw.insert("transcription_backend".to_string(), json!("whisper_cpp"));
            let backend = string_value(raw, "ocr_backend")
                .filter(|value| {
                    matches!(
                        value.as_str(),
                        "tesseract" | "paddleocr_http" | "custom_http"
                    )
                })
                .unwrap_or_else(|| "tesseract".to_string());
            raw.insert("ocr_backend".to_string(), json!(backend));
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

    fn ocr_test(&self, params: Value) -> Result<Value, String> {
        let settings = self.read_settings();
        let backend = string_param(&params, "ocr_backend")
            .or_else(|| string_value(&settings, "ocr_backend"))
            .filter(|value| {
                matches!(
                    value.as_str(),
                    "tesseract" | "paddleocr_http" | "custom_http"
                )
            })
            .unwrap_or_else(|| "tesseract".to_string());

        if backend == "tesseract" {
            let success = tool_exists("tesseract", &["tesseract-ocr-tools"], &self.runtime_dir);
            return Ok(json!({
                "success": success,
                "message": if success {
                    "Tesseract 可用"
                } else {
                    "未找到 tesseract.exe，请先安装 OCR 插件或把 Tesseract 加入 PATH"
                },
            }));
        }

        let endpoint = string_param(&params, "ocr_http_endpoint")
            .or_else(|| string_value(&settings, "ocr_http_endpoint"))
            .or_else(|| string_value(&settings, "ocr_api_url"))
            .unwrap_or_default();
        if endpoint.trim().is_empty() {
            return Ok(json!({
                "success": false,
                "message": "OCR HTTP Endpoint 不能为空",
            }));
        }

        let api_key = string_param(&params, "ocr_http_api_key")
            .or_else(|| string_value(&settings, "ocr_http_api_key"))
            .or_else(|| string_value(&settings, "ocr_api_key"))
            .unwrap_or_default();
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(20))
            .build()
            .map_err(|error| error.to_string())?;

        if backend == "paddleocr_http" {
            if bearer_token(&api_key).is_empty() {
                return Ok(json!({
                    "success": false,
                    "message": "PaddleOCR API Key 不能为空",
                }));
            }
            let endpoint = normalise_paddleocr_jobs_endpoint(endpoint.trim());
            let model = string_param(&params, "ocr_model")
                .or_else(|| string_value(&settings, "ocr_model"))
                .unwrap_or_else(|| PADDLEOCR_DEFAULT_MODEL.to_string());
            let test_pdf = simple_pdf_bytes("OCR TEST");
            return match submit_paddleocr_job(
                &client,
                &endpoint,
                &api_key,
                &model,
                test_pdf,
                "ocr-test.pdf",
            ) {
                Ok(job_id) => Ok(json!({
                    "success": true,
                    "message": format!("PaddleOCR 服务可用，jobId: {job_id}"),
                    "job_id": job_id,
                })),
                Err(error) => Ok(json!({
                    "success": false,
                    "message": error,
                })),
            };
        }

        match ocr_http_json_with_image(
            &client,
            endpoint.trim(),
            &api_key,
            OCR_TEST_IMAGE_BASE64,
            "ocr-test.png",
        ) {
            Ok(value) => {
                let text_count = extract_text_from_ocr_json(&value).len();
                Ok(json!({
                    "success": true,
                    "message": if text_count > 0 {
                        format!("OCR 服务可用，返回 JSON，并解析到 {} 段文本", text_count)
                    } else {
                        "OCR 服务可用，返回 JSON；测试图未识别到文字属于正常情况".to_string()
                    },
                    "text_count": text_count,
                }))
            }
            Err(error) => Ok(json!({
                "success": false,
                "message": error,
            })),
        }
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

        let client = match reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
        {
            Ok(client) => client,
            Err(error) => {
                let message = "HTTP client init failed";
                let error = error.to_string();
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
                    "message": message,
                    "error": error,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }));
            }
        };
        let url = format!(
            "{}/chat/completions",
            profile.base_url.trim_end_matches('/')
        );
        let response = match with_optional_bearer(client.post(url), &profile.api_key)
            .json(&json!({
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            { "type": "text", "text": "Describe what you see in this image in one short sentence." },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": format!("data:image/png;base64,{}", VISION_TEST_IMAGE_BASE64)
                                }
                            }
                        ]
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 100
            }))
            .send()
        {
            Ok(response) => response,
            Err(error) => {
                let message = "HTTP request failed";
                let error = error.to_string();
                let (capability_cache_saved, capability_cache_error) = self.maybe_update_provider_capability(
                    cache_provider.as_deref(),
                    &model,
                    "vision",
                    "fail",
                    message,
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
        };

        let status = response.status();
        if !status.is_success() {
            let body = response.text().unwrap_or_default();
            let message = format!("Vision model returned HTTP {status}");
            let error = body.chars().take(500).collect::<String>();
            let cache_message = format!("HTTP {}", status.as_u16());
            let (capability_cache_saved, capability_cache_error) = self
                .maybe_update_provider_capability(
                    cache_provider.as_deref(),
                    &model,
                    "vision",
                    "fail",
                    &cache_message,
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
                let message = "Response is not valid JSON";
                let error = format!(
                    "{e}: {}",
                    payload_text.chars().take(300).collect::<String>()
                );
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
                    "message": message,
                    "error": error,
                    "capability_cache_saved": capability_cache_saved,
                    "capability_cache_error": capability_cache_error,
                }));
            }
        };

        let result = payload
            .get("choices")
            .and_then(Value::as_array)
            .and_then(|choices| choices.first())
            .and_then(|choice| choice.get("message"))
            .and_then(|message| message.get("content"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|content| !content.is_empty())
            .map(ToOwned::to_owned);

        match result {
            Some(text) => {
                let result = text.chars().take(200).collect::<String>();
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

    fn local_models(&self) -> Result<Value, String> {
        let raw = self.read_settings();
        let mut dirs = Vec::new();
        if let Some(model_dir) =
            string_value(&raw, "whisper_model_dir").or_else(|| string_value(&raw, "model_dir"))
        {
            if !model_dir.trim().is_empty() {
                dirs.push(PathBuf::from(model_dir));
            }
        }
        dirs.push(self.data_dir.join("models"));

        let mut models = Vec::new();
        for dir in dirs {
            collect_whisper_models(&dir, &mut models);
        }
        Ok(json!(models))
    }

    fn doctor_run(&self) -> Result<Value, String> {
        let settings = self.read_settings();
        let ffmpeg = tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir);
        let ytdlp = tool_exists("yt-dlp", &["download-tools"], &self.runtime_dir);
        let whisper_cpp = tool_exists(
            "whisper-cli",
            &["whisper-cpp-cuda-tools", "whisper-cpp-tools"],
            &self.runtime_dir,
        ) || tool_exists(
            "main",
            &["whisper-cpp-cuda-tools", "whisper-cpp-tools"],
            &self.runtime_dir,
        );
        let tesseract = tool_exists("tesseract", &["tesseract-ocr-tools"], &self.runtime_dir);
        let http_ocr = !string_value(&settings, "ocr_http_endpoint")
            .or_else(|| string_value(&settings, "ocr_api_url"))
            .unwrap_or_default()
            .trim()
            .is_empty();
        Ok(json!([
            check_item("Rust native engine", true, "in-process"),
            check_item("FFmpeg", ffmpeg, "system PATH or ffmpeg-tools"),
            check_item("yt-dlp", ytdlp, "download-tools"),
            check_item("whisper.cpp", whisper_cpp, "whisper-cpp-tools"),
            check_item(
                "OCR provider",
                tesseract || http_ocr,
                "tesseract-ocr-tools or OCR HTTP endpoint"
            )
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
            let latest_version =
                if installed && manifest_string(&manifest, "download_url").is_some() {
                    component_latest_version(&manifest)
                        .map(Value::String)
                        .unwrap_or(Value::Null)
                } else {
                    Value::Null
                };
            let manifest_version = manifest_string(&manifest, "version").unwrap_or_default();
            let marker_version = read_component_marker_version(&component_path);
            let update_available = installed
                && missing.is_empty()
                && manifest_string(&manifest, "download_url").is_some()
                && marker_version
                    .as_deref()
                    .map(|version| version != manifest_version)
                    .unwrap_or(false);
            result.push(json!({
                "component": component,
                "version": manifest.get("version").and_then(Value::as_str).unwrap_or(""),
                "description": manifest.get("description").and_then(Value::as_str).unwrap_or(""),
                "installed": installed,
                "installed_version": installed_version,
                "latest_version": latest_version,
                "update_available": update_available,
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

    fn components_verify(&self, params: Value) -> Result<Value, String> {
        let component = required_string(&params, "component")?;
        let manifest = self.read_manifest(&component)?;
        let component_path = self.runtime_dir.join("components").join(&component);
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
        let manifest = self.read_manifest(&component)?;
        let source = self.runtime_dir.join("packages").join(&component);
        let target = self.runtime_dir.join("components").join(&component);
        if !source.is_dir() {
            let download_error = if manifest_string(&manifest, "download_url").is_some() {
                match install_component_from_download(&manifest, &target) {
                    Ok(()) => {
                        write_component_marker(&manifest, &target)?;
                        return Ok(
                            json!({ "ok": true, "component": component, "status": "installed" }),
                        );
                    }
                    Err(error) => Some(error),
                }
            } else {
                None
            };
            let path_result = match component.as_str() {
                "download-tools" => install_download_tools(&target),
                "ffmpeg-tools" => install_ffmpeg_tools_from_path(&target),
                "whisper-cpp-tools" => install_whisper_cpp_tools_from_path(&target),
                "whisper-cpp-cuda-tools" => install_whisper_cpp_tools_from_path(&target),
                "tesseract-ocr-tools" => install_tesseract_tools_from_path(&target),
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

    fn process_start(&self, params: Value) -> Result<Value, String> {
        self.process_start_internal(params, None)
    }

    fn process_start_internal(
        &self,
        params: Value,
        lineage: Option<(u32, String)>,
    ) -> Result<Value, String> {
        let input = required_string(&params, "input")?;
        let title = string_param(&params, "title").or_else(|| {
            Path::new(&input)
                .file_stem()
                .and_then(|value| value.to_str())
                .map(ToOwned::to_owned)
        });
        let settings = self.read_settings();
        let output_dir = string_param(&params, "output_dir")
            .map(PathBuf::from)
            .unwrap_or_else(|| effective_note_output_dir(&settings, &self.default_export_dir));
        let model_dirs = whisper_model_dirs(&settings, &self.data_dir);
        let whisper_model = string_param(&params, "whisper_model")
            .or_else(|| string_value(&settings, "whisper_model"))
            .unwrap_or_else(|| "large-v3".to_string());
        let whisper_device = string_param(&params, "whisper_device")
            .or_else(|| string_value(&settings, "whisper_device"))
            .unwrap_or_else(|| "auto".to_string());
        let provider = provider_profile_for_job(&settings, &params).ok();
        let ocr_enabled = params
            .get("ocr_enabled")
            .and_then(Value::as_bool)
            .unwrap_or_else(|| {
                settings
                    .get("ocr_enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
            });
        let ocr_backend = string_param(&params, "ocr_backend")
            .or_else(|| string_value(&settings, "ocr_backend"))
            .filter(|value| {
                matches!(
                    value.as_str(),
                    "tesseract" | "paddleocr_http" | "custom_http"
                )
            })
            .unwrap_or_else(|| "tesseract".to_string());
        let ocr_config = OcrRuntimeConfig {
            enabled: ocr_enabled,
            backend: ocr_backend.clone(),
            endpoint: string_param(&params, "ocr_http_endpoint")
                .or_else(|| string_value(&settings, "ocr_http_endpoint"))
                .or_else(|| string_value(&settings, "ocr_api_url"))
                .unwrap_or_default(),
            api_key: string_param(&params, "ocr_http_api_key")
                .or_else(|| string_value(&settings, "ocr_http_api_key"))
                .or_else(|| string_value(&settings, "ocr_api_key"))
                .unwrap_or_default(),
            model: string_param(&params, "ocr_model")
                .or_else(|| string_value(&settings, "ocr_model"))
                .unwrap_or_else(|| PADDLEOCR_DEFAULT_MODEL.to_string()),
        };
        let vision_enabled = params
            .get("vision_enabled")
            .and_then(Value::as_bool)
            .unwrap_or_else(|| {
                settings
                    .get("vision_enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
            });
        let frame_interval = params
            .get("frame_interval")
            .and_then(Value::as_f64)
            .or_else(|| settings.get("frame_interval").and_then(Value::as_f64))
            .unwrap_or(60.0);
        let max_frames = params
            .get("max_frames")
            .and_then(Value::as_u64)
            .map(|v| v as u32)
            .or_else(|| {
                settings
                    .get("max_frames")
                    .and_then(Value::as_u64)
                    .map(|v| v as u32)
            })
            .unwrap_or(8);
        let frame_mode = string_param(&params, "frame_mode")
            .or_else(|| string_value(&settings, "frame_mode"))
            .unwrap_or_else(|| "fixed".to_string());
        let provider_name = string_param(&params, "provider_name")
            .or_else(|| string_param(&params, "active_provider"))
            .or_else(|| string_value(&settings, "active_provider"))
            .unwrap_or_default();
        let settings_snapshot = Some(build_job_settings_snapshot(
            &input,
            title.as_deref(),
            &output_dir,
            &whisper_model,
            &whisper_device,
            &provider_name,
            provider.as_ref(),
            &ocr_config,
            vision_enabled,
            frame_interval,
            max_frames,
            &frame_mode,
            &params,
        ));
        let (attempt, parent_run_id) = lineage
            .map(|(attempt, parent)| (attempt.max(1), Some(parent)))
            .unwrap_or((1, None));
        let artifact_cleanup_policy = string_param(&params, "artifact_cleanup_policy")
            .map(|value| normalize_artifact_cleanup_policy(&value))
            .unwrap_or_else(default_artifact_cleanup_policy);
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
            job_id: Uuid::new_v4().to_string(),
            title: title.clone(),
            status: "pending".to_string(),
            progress: 0,
            progress_message: "任务已创建".to_string(),
            stage: "pending".to_string(),
            input: input.clone(),
            created_at: Utc::now().to_rfc3339(),
            completed_at: None,
            error_message: None,
            output_path: None,
            transcript_path: None,
            frames_count: 0,
            can_resume: false,
            settings_snapshot,
            workspace_dir: None,
            attempt,
            parent_run_id,
            artifact_cleanup_policy,
        };
        let control = Arc::new(JobControl::new());
        {
            let mut controls = self
                .job_controls
                .lock()
                .map_err(|_| "job controls lock poisoned".to_string())?;
            controls.insert(id, control.clone());
        }
        {
            let mut jobs = self
                .jobs
                .lock()
                .map_err(|_| "jobs lock poisoned".to_string())?;
            jobs.push(job);
            if let Err(error) = save_jobs(&self.jobs_state_path, &jobs) {
                jobs.retain(|job| job.id != id);
                if let Ok(mut controls) = self.job_controls.lock() {
                    controls.remove(&id);
                }
                return Err(error);
            }
        }

        let jobs = self.jobs.clone();
        let jobs_state_path = self.jobs_state_path.clone();
        let job_controls = self.job_controls.clone();
        let app_handle = self.app_handle.clone();
        let runtime_dir = self.runtime_dir.clone();
        std::thread::spawn(move || {
            run_native_job(
                jobs,
                jobs_state_path,
                app_handle,
                id,
                input,
                title,
                output_dir,
                runtime_dir,
                model_dirs,
                whisper_model,
                whisper_device,
                provider,
                ocr_config,
                vision_enabled,
                frame_interval,
                max_frames,
                frame_mode,
                control,
                job_controls,
            );
        });

        Ok(json!({ "job_id": id }))
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
        let control = self.job_control(id)?;
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
        Ok(json!(true))
    }

    fn process_retry(&self, params: Value) -> Result<Value, String> {
        let id = job_id_param(&params)?;
        let (input, title, snapshot, attempt, parent_run_id, cleanup_policy) = {
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
            (
                job.input.clone(),
                job.title.clone(),
                job.settings_snapshot.clone(),
                job.attempt.saturating_add(1),
                job.parent_run_id
                    .clone()
                    .unwrap_or_else(|| job.job_id.clone()),
                job.artifact_cleanup_policy.clone(),
            )
        };
        let mut retry_params =
            sanitized_retry_task_params(snapshot.as_ref(), &input, title.as_ref());
        retry_params.insert(
            "artifact_cleanup_policy".to_string(),
            json!(normalize_artifact_cleanup_policy(&cleanup_policy)),
        );
        self.process_start_internal(Value::Object(retry_params), Some((attempt, parent_run_id)))
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
            runner.run_collection_batch(id, items, max_concurrency, batch_output_dir);
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

    fn run_collection_batch(
        &self,
        id: u64,
        items: Vec<CollectionBatchItem>,
        max_concurrency: usize,
        output_dir: PathBuf,
    ) {
        let mut pending = items.into_iter();
        let mut active: Vec<(u64, u64)> = Vec::new();
        let mut pending_done = false;
        loop {
            while active.len() < max_concurrency && !pending_done {
                match pending.next() {
                    Some(item) => {
                        let item_id = item.id;
                        match self.process_start(json!({
                            "input": item.input,
                            "title": item.title,
                            "output_dir": output_dir.to_string_lossy(),
                        })) {
                            Ok(result) => {
                                if let Some(run_id) = result.get("job_id").and_then(Value::as_u64) {
                                    let _ = self.update_collection_item_start(id, item_id, run_id);
                                    active.push((item_id, run_id));
                                } else {
                                    let _ = self.update_collection_item_failed(
                                        id,
                                        item_id,
                                        "process.start did not return job_id",
                                    );
                                }
                            }
                            Err(error) => {
                                let _ = self.update_collection_item_failed(id, item_id, &error);
                                continue;
                            }
                        }
                    }
                    None => pending_done = true,
                }
            }

            if active.is_empty() && pending_done {
                break;
            }

            if !active.is_empty() {
                let snapshots = if let Ok(jobs) = self.jobs.lock() {
                    active
                        .iter()
                        .filter_map(|(item_id, run_id)| {
                            jobs.iter()
                                .find(|job| job.id == *run_id)
                                .cloned()
                                .map(|job| (*item_id, job))
                        })
                        .collect::<Vec<_>>()
                } else {
                    Vec::new()
                };
                for (item_id, job) in &snapshots {
                    let _ = self.update_collection_item_from_job(id, *item_id, job);
                }
                active.retain(|(_, run_id)| {
                    snapshots
                        .iter()
                        .find(|(_, job)| job.id == *run_id)
                        .map(|(_, job)| !is_terminal_status(&job.status))
                        .unwrap_or(false)
                });
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

    fn update_collection_item_from_job(
        &self,
        collection_id: u64,
        item_id: u64,
        job: &NativeJob,
    ) -> Result<(), String> {
        self.update_collection_item(collection_id, item_id, |item| {
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
        read_json_file(&self.manifests_dir.join(format!("{component}.json")))
            .or_else(|_| {
                default_component_manifest(component)
                    .ok_or_else(|| format!("manifest '{component}' not found in bundled defaults"))
            })
            .map_err(|error| format!("manifest '{component}' not found or invalid: {error}"))
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
            "note_id": null,
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
    let mut jobs = fs::read_to_string(jobs_state_path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Vec<NativeJob>>(&raw).ok())
        .unwrap_or_default();
    let now = Utc::now().to_rfc3339();
    let mut changed = false;
    for job in &mut jobs {
        if matches!(
            job.status.as_str(),
            "pending" | "running" | "pausing" | "cancelling" | "paused"
        ) {
            job.status = "interrupted".to_string();
            job.stage = "interrupted".to_string();
            job.progress_message = "应用已重启，任务已中断".to_string();
            job.completed_at = Some(now.clone());
            job.can_resume = false;
            changed = true;
        }
    }
    if changed {
        let _ = save_jobs(jobs_state_path, &jobs);
    }
    jobs
}

fn save_jobs(jobs_state_path: &Path, jobs: &[NativeJob]) -> Result<(), String> {
    write_json_atomic(jobs_state_path, &json!(jobs))
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

fn sanitized_retry_task_params(
    snapshot: Option<&Value>,
    fallback_input: &str,
    fallback_title: Option<&String>,
) -> Map<String, Value> {
    const ALLOWED: &[&str] = &[
        "input",
        "title",
        "output_dir",
        "whisper_model",
        "whisper_device",
        "provider_name",
        "active_provider",
        "base_url",
        "model",
        "vision_model",
        "ocr_enabled",
        "ocr_backend",
        "ocr_http_endpoint",
        "ocr_model",
        "vision_enabled",
        "frame_interval",
        "max_frames",
        "frame_mode",
        "template",
        "artifact_cleanup_policy",
    ];
    let mut params = Map::new();
    if let Some(task_params) = snapshot
        .and_then(|value| value.get("task_params"))
        .and_then(Value::as_object)
    {
        for key in ALLOWED {
            if let Some(value) = task_params.get(*key) {
                params.insert((*key).to_string(), value.clone());
            }
        }
    }
    if !params.contains_key("input") {
        params.insert("input".to_string(), json!(fallback_input));
    }
    if !params.contains_key("title") {
        params.insert("title".to_string(), json!(fallback_title));
    }
    if let Some(policy) = params
        .get("artifact_cleanup_policy")
        .and_then(Value::as_str)
        .map(normalize_artifact_cleanup_policy)
    {
        params.insert("artifact_cleanup_policy".to_string(), json!(policy));
    }
    params
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

fn sanitize_snapshot_endpoint(endpoint: &str) -> String {
    let trimmed = endpoint.trim();
    if trimmed.is_empty() {
        return String::new();
    }
    if let Ok(mut url) = reqwest::Url::parse(trimmed) {
        url.set_query(None);
        url.set_fragment(None);
        return url.to_string().trim_end_matches('/').to_string();
    }
    let without_query = trimmed.split('?').next().unwrap_or(trimmed);
    without_query
        .split('#')
        .next()
        .unwrap_or(without_query)
        .trim_end_matches('/')
        .to_string()
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

struct StageRecord {
    name: String,
    duration_ms: u64,
}

struct JobProfileMetrics {
    job_id: u64,
    start_time: String,
    job_start: std::time::Instant,
    output_dir: PathBuf,
    file_stem: String,
    stages: Vec<StageRecord>,
    frame_sampling: Option<FrameSamplingMetrics>,
}

impl Drop for JobProfileMetrics {
    fn drop(&mut self) {
        if self.file_stem.is_empty() {
            return;
        }
        let total_ms = self.job_start.elapsed().as_millis() as u64;
        let metrics_path = self
            .output_dir
            .join(format!("{}-{}-metrics.json", self.file_stem, self.job_id));
        if let Ok(json) = serde_json::to_string_pretty(&serde_json::json!({
            "job_id": self.job_id,
            "start_time": self.start_time,
            "total_ms": total_ms,
            "frame_sampling": self.frame_sampling.as_ref().map(|sampling| serde_json::json!({
                "duration_sec": sampling.duration_sec,
                "interval_sec": sampling.interval_sec,
                "candidate_count": sampling.candidate_count,
                "kept_count": sampling.kept_count,
            })),
            "stages": self.stages.iter().map(|s| serde_json::json!({
                "name": s.name,
                "duration_ms": s.duration_ms,
            })).collect::<Vec<_>>(),
        })) {
            let _ = fs::write(&metrics_path, json);
        }
    }
}

struct StageTimer {
    name: String,
    start: std::time::Instant,
}

struct JobControlCleanup {
    id: u64,
    controls: Arc<Mutex<HashMap<u64, Arc<JobControl>>>>,
}

impl Drop for JobControlCleanup {
    fn drop(&mut self) {
        if let Ok(mut controls) = self.controls.lock() {
            controls.remove(&self.id);
        }
    }
}

impl StageTimer {
    fn new(name: &str) -> Self {
        Self {
            name: name.to_string(),
            start: std::time::Instant::now(),
        }
    }
    fn finish(self) -> StageRecord {
        StageRecord {
            name: self.name,
            duration_ms: self.start.elapsed().as_millis() as u64,
        }
    }
}

fn run_native_job(
    jobs: Arc<Mutex<Vec<NativeJob>>>,
    jobs_state_path: PathBuf,
    app_handle: Option<AppHandle>,
    id: u64,
    input: String,
    title: Option<String>,
    output_dir: PathBuf,
    runtime_dir: PathBuf,
    model_dirs: Vec<PathBuf>,
    whisper_model: String,
    whisper_device: String,
    provider: Option<NativeProviderProfile>,
    ocr_config: OcrRuntimeConfig,
    vision_enabled: bool,
    frame_interval: f64,
    max_frames: u32,
    frame_mode: String,
    control: Arc<JobControl>,
    job_controls: Arc<Mutex<HashMap<u64, Arc<JobControl>>>>,
) {
    let _control_cleanup = JobControlCleanup {
        id,
        controls: job_controls,
    };
    let run_stamp = Utc::now().format("%Y%m%d-%H%M%S").to_string();
    let workspace_dir = runtime_dir
        .parent()
        .unwrap_or(&runtime_dir)
        .join("jobs")
        .join(format!("job-{id}-{run_stamp}"));
    let mut profile = JobProfileMetrics {
        job_id: id,
        start_time: chrono::Utc::now().to_rfc3339(),
        job_start: std::time::Instant::now(),
        output_dir: workspace_dir.clone(),
        file_stem: String::new(),
        stages: Vec::new(),
        frame_sampling: None,
    };
    macro_rules! checkpoint {
        ($stage:expr, $progress:expr, $message:expr) => {
            if !checkpoint_job_control(
                &jobs,
                &jobs_state_path,
                &app_handle,
                id,
                &control,
                $stage,
                $progress,
                $message,
            ) {
                return;
            }
        };
    }

    checkpoint!("resolving", 0, "检查输入文件");

    update_job(
        &jobs,
        &jobs_state_path,
        &app_handle,
        id,
        "running",
        "resolving",
        8,
        "检查输入文件",
        None,
        None,
        None,
    );
    checkpoint!("resolving", 8, "检查输入文件");

    if let Err(error) =
        fs::create_dir_all(&output_dir).and_then(|_| fs::create_dir_all(&workspace_dir))
    {
        update_job(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            "failed",
            "failed",
            100,
            "创建输出目录失败",
            Some(error.to_string()),
            None,
            None,
        );
        return;
    }
    update_job_workspace(
        &jobs,
        &jobs_state_path,
        id,
        workspace_dir.to_string_lossy().to_string(),
    );

    let t_dl = StageTimer::new("downloading");
    checkpoint!("downloading", 12, "准备媒体输入");
    let input_path = if input.starts_with("http://") || input.starts_with("https://") {
        update_job(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            "running",
            "downloading",
            18,
            "使用 native yt-dlp 下载",
            None,
            None,
            None,
        );
        match download_with_ytdlp(&input, &workspace_dir, id, &runtime_dir, &control) {
            Ok(path) => path,
            Err(error) => {
                if is_cancellation_error(&error) {
                    checkpoint!("cancelled", 18, "任务已取消");
                    return;
                }
                update_job(
                    &jobs,
                    &jobs_state_path,
                    &app_handle,
                    id,
                    "failed",
                    "failed",
                    100,
                    "native 下载失败",
                    Some(error),
                    None,
                    None,
                );
                return;
            }
        }
    } else {
        let path = PathBuf::from(&input);
        if !path.is_file() {
            update_job(
                &jobs,
                &jobs_state_path,
                &app_handle,
                id,
                "failed",
                "failed",
                100,
                "输入文件不存在",
                Some(format!("Input file not found: {}", path.display())),
                None,
                None,
            );
            return;
        }
        path
    };
    checkpoint!("downloading", 25, "媒体输入已准备");
    profile.stages.push(t_dl.finish());

    let base_title = title
        .clone()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            input_path
                .file_stem()
                .and_then(|value| value.to_str())
                .map(ToOwned::to_owned)
        })
        .unwrap_or_else(|| format!("native-job-{id}"));
    let base_file_stem = sanitize_filename(&base_title);
    let file_stem = format!("{base_file_stem}-{run_stamp}");
    profile.file_stem = file_stem.clone();
    let transcript_path = workspace_dir.join(format!("{file_stem}-{id}-transcript.txt"));
    let note_path = output_dir.join(format!("{file_stem}-{id}.md"));

    let t_whisper = StageTimer::new("transcribing");
    checkpoint!("transcribing", 30, "准备语音转录");

    update_job(
        &jobs,
        &jobs_state_path,
        &app_handle,
        id,
        "running",
        "transcribing",
        35,
        "使用 native whisper.cpp 转录",
        None,
        None,
        None,
    );

    let (mut transcript, whisper_json) = match transcribe_with_whisper_cpp(
        &input_path,
        &workspace_dir,
        &file_stem,
        id,
        &runtime_dir,
        &model_dirs,
        &whisper_model,
        &whisper_device,
        &control,
    ) {
        Ok((text, json)) => (text, json),
        Err(error) if is_cancellation_error(&error) => {
            checkpoint!("cancelled", 35, "任务已取消");
            return;
        }
        Err(error) => (
            format!(
                "Native transcript unavailable\n\nSource: {}\n\nReason: {}",
                input_path.display(),
                error
            ),
            None,
        ),
    };
    checkpoint!("transcribing", 45, "语音转录阶段完成");
    profile.stages.push(t_whisper.finish());
    if ocr_config.enabled {
        let t_ocr = StageTimer::new("extracting_frames");
        checkpoint!("extracting_frames", 50, "准备抽帧和 OCR");
        update_job(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            "running",
            "extracting_frames",
            55,
            &format!("使用 {} OCR", ocr_config.backend),
            None,
            None,
            None,
        );
        let ocr_result = match ocr_config.backend.as_str() {
            "paddleocr_http" | "custom_http" => extract_ocr_with_http(
                &input_path,
                &workspace_dir,
                &file_stem,
                id,
                &runtime_dir,
                &ocr_config,
                frame_interval,
                max_frames,
                &frame_mode,
                &control,
            ),
            _ => extract_ocr_with_tesseract(
                &input_path,
                &workspace_dir,
                &file_stem,
                id,
                &runtime_dir,
                frame_interval,
                max_frames,
                &frame_mode,
                &control,
            ),
        };
        let frame_dir = workspace_dir.join(format!("{file_stem}-{id}-frames"));
        update_job_frames(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            count_frame_files(&frame_dir),
        );
        match ocr_result {
            Ok(ocr) if !ocr.text.trim().is_empty() => {
                if profile.frame_sampling.is_none() {
                    profile.frame_sampling = ocr.frame_sampling;
                }
                transcript.push_str("\n\n## OCR\n\n");
                transcript.push_str(&ocr.text);
            }
            Ok(ocr) => {
                if profile.frame_sampling.is_none() {
                    profile.frame_sampling = ocr.frame_sampling;
                }
                transcript.push_str("\n\n## OCR\n\nNo readable text detected in sampled frames.");
            }
            Err(error) => {
                if is_cancellation_error(&error) {
                    checkpoint!("cancelled", 55, "任务已取消");
                    return;
                }
                transcript.push_str("\n\n## OCR\n\nOCR unavailable: ");
                transcript.push_str(&error);
            }
        }
        checkpoint!("extracting_frames", 60, "抽帧和 OCR 阶段完成");
        profile.stages.push(t_ocr.finish());
    }
    // Build timeline segments early (before vision, reused later for context)
    let mut segments: Vec<TimelineSegment> = (|| {
        let json_str = whisper_json.as_ref()?;
        let mut segs = parse_whisper_segments(json_str);
        if segs.is_empty() {
            return None;
        }
        let frame_dir = workspace_dir.join(format!("{file_stem}-{id}-frames"));
        if frame_dir.exists() {
            let frame_paths = collect_frame_files(&frame_dir).ok().unwrap_or_default();
            let timestamps_sec: Vec<f64> = frame_paths
                .iter()
                .filter_map(|fp| frame_index_from_path(fp).map(|idx| idx as f64 * frame_interval))
                .collect();
            let frame_ocrs = ocr_text_by_frame(&transcript);
            merge_frames_into_timeline(&mut segs, &frame_ocrs, &frame_paths, &timestamps_sec);
        }
        Some(segs)
    })()
    .unwrap_or_default();
    let t_vision = StageTimer::new("vision_analyzing");
    if vision_enabled {
        checkpoint!("vision_analyzing", 60, "准备视觉理解");
        update_job(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            "running",
            "vision_analyzing",
            62,
            "调用视觉模型分析各片段关键帧",
            None,
            None,
            None,
        );
        let frame_dir = workspace_dir.join(format!("{file_stem}-{id}-frames"));
        let (fetch_frames, timestamps_sec) = if count_frame_files(&frame_dir) > 0 {
            let paths = collect_frame_files(&frame_dir).unwrap_or_default();
            let timestamps: Vec<f64> = paths
                .iter()
                .filter_map(|fp| frame_index_from_path(fp).map(|idx| idx as f64 * frame_interval))
                .collect();
            (paths, timestamps)
        } else {
            match extract_sample_frames(
                &input_path,
                &workspace_dir,
                &file_stem,
                id,
                &runtime_dir,
                frame_interval,
                max_frames,
                &frame_mode,
                &control,
            ) {
                Ok(result) => {
                    if profile.frame_sampling.is_none() {
                        profile.frame_sampling = Some(FrameSamplingMetrics::from(&result));
                    }
                    (result.frames, result.timestamps_sec)
                }
                Err(error) => {
                    if is_cancellation_error(&error) {
                        checkpoint!("cancelled", 62, "任务已取消");
                        return;
                    }
                    transcript.push_str("\n\n## Vision\n\nFrame extraction unavailable: ");
                    transcript.push_str(&error);
                    (Vec::new(), Vec::new())
                }
            }
        };
        if !fetch_frames.is_empty() {
            update_job_frames(
                &jobs,
                &jobs_state_path,
                &app_handle,
                id,
                fetch_frames.len().try_into().unwrap_or(u32::MAX),
            );
            // Rebuild segments now that we have frames
            segments = (|| {
                let json_str = whisper_json.as_ref()?;
                let mut segs = parse_whisper_segments(json_str);
                if segs.is_empty() {
                    return None;
                }
                let frame_ocrs = ocr_text_by_frame(&transcript);
                merge_frames_into_timeline(&mut segs, &frame_ocrs, &fetch_frames, &timestamps_sec);
                Some(segs)
            })()
            .unwrap_or_default();
            match provider.as_ref() {
                Some(profile) => {
                    let vision_jobs: Vec<(usize, f64, f64, String, Vec<PathBuf>)> = segments
                        .iter()
                        .enumerate()
                        .filter(|(_, segment)| !segment.frame_paths.is_empty())
                        .map(|(i, segment)| {
                            (
                                i,
                                segment.start_sec,
                                segment.end_sec,
                                segment.text.clone(),
                                segment.frame_paths.clone(),
                            )
                        })
                        .collect();
                    let mut vision_results: Vec<(usize, f64, f64, Result<String, String>)> =
                        Vec::new();
                    for batch in vision_jobs.chunks(VISION_PARALLELISM) {
                        checkpoint!("vision_analyzing", 64, "视觉理解阶段执行中");
                        let mut handles = Vec::new();
                        for (segment_index, start_sec, end_sec, text, frame_paths) in
                            batch.iter().cloned()
                        {
                            if control.cancel_requested.load(Ordering::SeqCst) {
                                checkpoint!("cancelled", 64, "任务已取消");
                                return;
                            }
                            let profile = profile.clone();
                            handles.push(std::thread::spawn(move || {
                                let paths: Vec<&PathBuf> = frame_paths.iter().collect();
                                let result = analyze_segment_vision(
                                    &profile, start_sec, end_sec, &text, &paths,
                                );
                                (segment_index, start_sec, end_sec, result)
                            }));
                        }
                        for handle in handles {
                            if control.cancel_requested.load(Ordering::SeqCst) {
                                checkpoint!("cancelled", 64, "任务已取消");
                                return;
                            }
                            match handle.join() {
                                Ok(result) => vision_results.push(result),
                                Err(_) => vision_results.push((
                                    usize::MAX,
                                    0.0,
                                    0.0,
                                    Err("vision worker panicked".to_string()),
                                )),
                            }
                        }
                    }
                    vision_results.sort_by_key(|(segment_index, _, _, _)| *segment_index);
                    let mut vision_texts: Vec<String> = Vec::new();
                    for (i, start_sec, end_sec, result) in vision_results {
                        match result {
                            Ok(text) => {
                                let trimmed = text.trim().to_string();
                                if !trimmed.is_empty() && i < segments.len() {
                                    segments[i].vision_summary = Some(trimmed.clone());
                                    vision_texts.push(format!(
                                        "### [{:.0}s–{:.0}s]\n{}",
                                        start_sec, end_sec, trimmed
                                    ));
                                }
                            }
                            Err(error) => {
                                vision_texts.push(format!(
                                    "### [{:.0}s–{:.0}s]\nVision unavailable: {}",
                                    start_sec, end_sec, error
                                ));
                            }
                        }
                    }
                    if !vision_texts.is_empty() {
                        transcript.push_str("\n\n## Vision\n\n");
                        transcript.push_str(&vision_texts.join("\n\n"));
                    } else {
                        transcript.push_str(
                            "\n\n## Vision\n\nNo visual details were returned by the vision model.",
                        );
                    }
                }
                None => {
                    transcript.push_str(
                        "\n\n## Vision\n\nVision unavailable: configure an active AI provider.",
                    );
                }
            }
        }
        checkpoint!("vision_analyzing", 68, "视觉理解阶段完成");
    }
    profile.stages.push(t_vision.finish());
    checkpoint!("indexing", 69, "准备写入 transcript");
    if let Err(error) = fs::write(&transcript_path, transcript) {
        update_job(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            "failed",
            "failed",
            100,
            "写入 transcript 失败",
            Some(error.to_string()),
            None,
            None,
        );
        return;
    }

    let t_gen = StageTimer::new("generating_notes");
    checkpoint!("generating_notes", 70, "准备生成 Markdown 笔记");
    update_job(
        &jobs,
        &jobs_state_path,
        &app_handle,
        id,
        "running",
        "generating_notes",
        70,
        "生成 Markdown 笔记",
        None,
        None,
        Some(transcript_path.to_string_lossy().to_string()),
    );

    let transcript_text = fs::read_to_string(&transcript_path).unwrap_or_default();
    let transcript_preview = transcript_text.chars().take(6000).collect::<String>();
    let image_context = if ocr_config.enabled || vision_enabled {
        let frame_dir = workspace_dir.join(format!("{file_stem}-{id}-frames"));
        if let Some(asset_dir) =
            copy_frame_assets(&frame_dir, &output_dir, &format!("{file_stem}-{id}"))
        {
            markdown_image_context(&note_path, &asset_dir, &transcript_text, 8)
        } else {
            String::new()
        }
    } else {
        String::new()
    };
    checkpoint!("generating_notes", 72, "准备调用文本模型");
    let timeline_context = if segments.is_empty() {
        None
    } else {
        let mut lines = Vec::new();
        for seg in &segments {
            lines.push(format!(
                "- [{:.0}s–{:.0}s] {}",
                seg.start_sec, seg.end_sec, seg.text
            ));
            if let Some(ocr) = &seg.ocr_text {
                if !ocr.trim().is_empty() {
                    lines.push(format!(
                        "  OCR: {}",
                        ocr.trim().chars().take(200).collect::<String>()
                    ));
                }
            }
            if let Some(vision) = &seg.vision_summary {
                if !vision.trim().is_empty() {
                    lines.push(format!(
                        "  Vision: {}",
                        vision.trim().chars().take(200).collect::<String>()
                    ));
                }
            }
            for fp in &seg.frame_paths {
                if let Some(name) = fp.file_name().and_then(|v| v.to_str()) {
                    lines.push(format!("  Frame: {}", name));
                }
            }
        }
        Some(lines.join("\n"))
    };
    let generated_note = provider
        .as_ref()
        .and_then(|profile| {
            synthesize_note_with_provider(
                profile,
                &base_title,
                &input_path,
                &transcript_text,
                &image_context,
                timeline_context.as_deref().unwrap_or(""),
            )
            .ok()
        })
        .unwrap_or_else(|| {
            format!(
                "# {base_title}\n\n- Source: `{}`\n- Engine: Rust native\n- Created: {}\n\n## Summary\n\nNative note generation is handled by the Rust engine. Configure an active OpenAI-compatible provider to synthesize structured notes; this fallback note includes the native transcript output.\n\n## Transcript\n\n{}\n\nFull transcript: `{}`.\n",
                input_path.display(),
                Utc::now().to_rfc3339(),
                transcript_preview,
                transcript_path.display()
            )
        });
    checkpoint!("generating_notes", 88, "文本模型调用完成");
    checkpoint!("generating_notes", 90, "Markdown 笔记生成完成");
    let note = if generated_note.trim_start().starts_with('#') {
        generated_note
    } else {
        format!(
            "# {base_title}\n\n- Source: `{}`\n- Engine: Rust native\n- Created: {}\n\n{}",
            input_path.display(),
            Utc::now().to_rfc3339(),
            generated_note
        )
    };
    profile.stages.push(t_gen.finish());
    checkpoint!("indexing", 95, "准备写入笔记");
    if let Err(error) = fs::write(&note_path, note) {
        update_job(
            &jobs,
            &jobs_state_path,
            &app_handle,
            id,
            "failed",
            "failed",
            100,
            "写入笔记失败",
            Some(error.to_string()),
            None,
            Some(transcript_path.to_string_lossy().to_string()),
        );
        return;
    }
    checkpoint!("completed", 99, "准备完成任务");

    update_job(
        &jobs,
        &jobs_state_path,
        &app_handle,
        id,
        "completed",
        "completed",
        100,
        "native markdown 产物已生成",
        None,
        Some(note_path.to_string_lossy().to_string()),
        Some(transcript_path.to_string_lossy().to_string()),
    );
}

fn update_job(
    jobs: &Arc<Mutex<Vec<NativeJob>>>,
    jobs_state_path: &Path,
    app_handle: &Option<AppHandle>,
    id: u64,
    status: &str,
    stage: &str,
    progress: u8,
    message: &str,
    error_message: Option<String>,
    output_path: Option<String>,
    transcript_path: Option<String>,
) {
    let mut event = None;
    if let Ok(mut locked) = jobs.lock() {
        if let Some(job) = locked.iter_mut().find(|job| job.id == id) {
            let mut next_status = status.to_string();
            let mut next_stage = stage.to_string();
            let mut next_message = message.to_string();
            let mut next_error = error_message;
            if status == "failed" && matches!(job.status.as_str(), "cancelling" | "cancelled") {
                next_status = "cancelled".to_string();
                next_stage = "cancelled".to_string();
                next_message = "任务已取消".to_string();
                next_error = None;
            }
            if status == "running"
                && matches!(
                    job.status.as_str(),
                    "pausing" | "paused" | "cancelling" | "cancelled"
                )
            {
                return;
            }
            job.status = next_status.clone();
            job.stage = next_stage.clone();
            job.progress = progress;
            job.progress_message = next_message.clone();
            job.can_resume = next_status == "paused";
            if is_terminal_status(&next_status) {
                job.completed_at = Some(Utc::now().to_rfc3339());
            }
            if let Some(error) = next_error {
                job.error_message = Some(error);
            }
            if let Some(path) = output_path {
                job.output_path = Some(path);
            }
            if let Some(path) = transcript_path {
                job.transcript_path = Some(path);
            }
            event = Some(job_progress_event(
                job,
                id,
                &next_status,
                &next_stage,
                progress,
                &next_message,
            ));
            let _ = save_jobs(jobs_state_path, &locked);
        }
    }
    if let (Some(handle), Some(payload)) = (app_handle, event) {
        let _ = handle.emit("job:progress", payload);
    }
}

fn update_job_workspace(
    jobs: &Arc<Mutex<Vec<NativeJob>>>,
    jobs_state_path: &Path,
    id: u64,
    workspace_dir: String,
) {
    if let Ok(mut locked) = jobs.lock() {
        if let Some(job) = locked.iter_mut().find(|job| job.id == id) {
            job.workspace_dir = Some(workspace_dir);
            let _ = save_jobs(jobs_state_path, &locked);
        }
    }
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

fn checkpoint_job_control(
    jobs: &Arc<Mutex<Vec<NativeJob>>>,
    jobs_state_path: &Path,
    app_handle: &Option<AppHandle>,
    id: u64,
    control: &Arc<JobControl>,
    stage: &str,
    progress: u8,
    message: &str,
) -> bool {
    if control.cancel_requested.load(Ordering::SeqCst) {
        update_job(
            jobs,
            jobs_state_path,
            app_handle,
            id,
            "cancelled",
            "cancelled",
            progress,
            "任务已取消",
            None,
            None,
            None,
        );
        return false;
    }

    if control.pause_requested.load(Ordering::SeqCst) {
        update_job(
            jobs,
            jobs_state_path,
            app_handle,
            id,
            "paused",
            stage,
            progress,
            "已在阶段边界暂停",
            None,
            None,
            None,
        );
        let mut guard = match control.lock.lock() {
            Ok(guard) => guard,
            Err(_) => return false,
        };
        while control.pause_requested.load(Ordering::SeqCst)
            && !control.cancel_requested.load(Ordering::SeqCst)
        {
            guard = match control.condvar.wait(guard) {
                Ok(guard) => guard,
                Err(_) => return false,
            };
        }
        drop(guard);
        if control.cancel_requested.load(Ordering::SeqCst) {
            update_job(
                jobs,
                jobs_state_path,
                app_handle,
                id,
                "cancelled",
                "cancelled",
                progress,
                "任务已取消",
                None,
                None,
                None,
            );
            return false;
        }
        update_job(
            jobs,
            jobs_state_path,
            app_handle,
            id,
            "running",
            stage,
            progress,
            message,
            None,
            None,
            None,
        );
    }
    true
}

fn update_job_frames(
    jobs: &Arc<Mutex<Vec<NativeJob>>>,
    jobs_state_path: &Path,
    app_handle: &Option<AppHandle>,
    id: u64,
    frames_count: u32,
) {
    let mut event = None;
    if let Ok(mut locked) = jobs.lock() {
        if let Some(job) = locked.iter_mut().find(|job| job.id == id) {
            if matches!(job.status.as_str(), "cancelling" | "cancelled") {
                return;
            }
            job.frames_count = frames_count;
            event = Some(job.to_value());
            let _ = save_jobs(jobs_state_path, &locked);
        }
    }
    if let (Some(handle), Some(payload)) = (app_handle, event) {
        let _ = handle.emit("job:progress", payload);
    }
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

fn with_optional_bearer(
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

fn provider_profile_for_job(
    settings: &Map<String, Value>,
    params: &Value,
) -> Result<NativeProviderProfile, String> {
    let provider_name = string_param(params, "provider_name")
        .or_else(|| string_param(params, "active_provider"))
        .or_else(|| string_value(settings, "active_provider"));
    if let Some(name) = provider_name {
        if let Some(profile) = find_provider(settings, &name) {
            return provider_from_saved_value(profile, params);
        }
    }
    provider_profile_for_request(settings, params)
}

fn build_job_settings_snapshot(
    input: &str,
    title: Option<&str>,
    output_dir: &Path,
    whisper_model: &str,
    whisper_device: &str,
    provider_name: &str,
    provider: Option<&NativeProviderProfile>,
    ocr_config: &OcrRuntimeConfig,
    vision_enabled: bool,
    frame_interval: f64,
    max_frames: u32,
    frame_mode: &str,
    raw_params: &Value,
) -> Value {
    let mut task_params = Map::new();
    task_params.insert("input".to_string(), json!(input));
    task_params.insert("title".to_string(), json!(title));
    task_params.insert(
        "output_dir".to_string(),
        json!(output_dir.to_string_lossy().to_string()),
    );
    task_params.insert("whisper_model".to_string(), json!(whisper_model));
    task_params.insert("whisper_device".to_string(), json!(whisper_device));
    task_params.insert("provider_name".to_string(), json!(provider_name));
    if let Some(provider) = provider {
        if provider_name.trim().is_empty() {
            task_params.insert(
                "base_url".to_string(),
                json!(sanitize_snapshot_endpoint(&provider.base_url)),
            );
        }
        task_params.insert("model".to_string(), json!(provider.model));
        task_params.insert("vision_model".to_string(), json!(provider.vision_model));
    }
    task_params.insert("ocr_enabled".to_string(), json!(ocr_config.enabled));
    task_params.insert("ocr_backend".to_string(), json!(ocr_config.backend));
    task_params.insert(
        "ocr_http_endpoint".to_string(),
        json!(sanitize_snapshot_endpoint(&ocr_config.endpoint)),
    );
    task_params.insert("ocr_model".to_string(), json!(ocr_config.model));
    task_params.insert("vision_enabled".to_string(), json!(vision_enabled));
    task_params.insert("frame_interval".to_string(), json!(frame_interval));
    task_params.insert("max_frames".to_string(), json!(max_frames));
    task_params.insert("frame_mode".to_string(), json!(frame_mode));
    if let Some(value) = raw_params.get("template") {
        task_params.insert("template".to_string(), value.clone());
    }
    json!({
        "version": 1,
        "created_at": Utc::now().to_rfc3339(),
        "task_params": task_params,
        "settings": {
            "whisper_model": whisper_model,
            "whisper_device": whisper_device,
            "ocr_enabled": ocr_config.enabled,
            "ocr_backend": ocr_config.backend,
            "ocr_http_endpoint": sanitize_snapshot_endpoint(&ocr_config.endpoint),
            "ocr_model": ocr_config.model,
            "vision_enabled": vision_enabled,
            "frame_interval": frame_interval,
            "max_frames": max_frames,
            "frame_mode": frame_mode,
            "active_provider": provider_name,
            "provider": provider.map(|profile| {
                let mut value = Map::new();
                if provider_name.trim().is_empty() {
                    value.insert("base_url".to_string(), json!(sanitize_snapshot_endpoint(&profile.base_url)));
                }
                value.insert("model".to_string(), json!(profile.model));
                value.insert("vision_model".to_string(), json!(profile.vision_model));
                Value::Object(value)
            }),
        }
    })
}

fn active_provider_profile(settings: &Map<String, Value>) -> Result<NativeProviderProfile, String> {
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

fn analyze_segment_vision(
    profile: &NativeProviderProfile,
    start_sec: f64,
    end_sec: f64,
    transcript_snippet: &str,
    frames: &[&PathBuf],
) -> Result<String, String> {
    let model = if profile.vision_model.trim().is_empty() {
        &profile.model
    } else {
        &profile.vision_model
    };
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|error| error.to_string())?;
    let url = format!(
        "{}/chat/completions",
        profile.base_url.trim_end_matches('/')
    );
    let mut content = vec![json!({
        "type": "text",
        "text": format!(
            "请分析这段视频 [{:.0}s–{:.0}s] 中的关键帧，提取对学习笔记有帮助的视觉信息。\n该片段转写：{}\n要求：用中文输出，重点说明画面中的 UI、图表、步骤、参数、节点、对象关系；不要泛泛描述。",
            start_sec, end_sec,
            transcript_snippet.chars().take(500).collect::<String>()
        )
    })];
    for frame in frames.iter().take(2) {
        let bytes = fs::read(frame).map_err(|error| error.to_string())?;
        let image = general_purpose::STANDARD.encode(bytes);
        content.push(json!({
            "type": "image_url",
            "image_url": {
                "url": format!("data:image/png;base64,{image}")
            }
        }));
    }
    if content.len() == 1 {
        return Err("no frames available for segment vision".to_string());
    }
    let response = with_optional_bearer(client.post(url), &profile.api_key)
        .json(&json!({
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": format!("You are a visual analysis assistant for video learning notes. Focus on what happens in the time range [{:.0}s–{:.0}s]. Return concise Chinese Markdown only.", start_sec, end_sec)
                },
                {
                    "role": "user",
                    "content": content
                }
            ],
            "temperature": 0.1
        }))
        .send()
        .map_err(|error| error.to_string())?;
    let status = response.status();
    let payload: Value = response.json().map_err(|error| error.to_string())?;
    if !status.is_success() {
        return Err(format!(
            "vision segment chat completion returned {status}: {payload}"
        ));
    }
    payload
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|content| !content.is_empty())
        .map(ToOwned::to_owned)
        .ok_or_else(|| "vision segment returned no content".to_string())
}

fn synthesize_note_with_provider(
    profile: &NativeProviderProfile,
    title: &str,
    source: &Path,
    transcript: &str,
    image_context: &str,
    timeline_context: &str,
) -> Result<String, String> {
    let clipped = transcript.chars().take(24_000).collect::<String>();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|error| error.to_string())?;
    let url = format!(
        "{}/chat/completions",
        profile.base_url.trim_end_matches('/')
    );
    let timeline_section = if timeline_context.trim().is_empty() {
        String::new()
    } else {
        format!(
            "\n\n时间线分段（含时间戳、转写片段、OCR、视觉信息）：\n{}\n",
            timeline_context
        )
    };
    let response = with_optional_bearer(client.post(url), &profile.api_key)
        .json(&json!({
            "model": profile.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You generate precise Chinese Markdown study notes for learning from video transcripts, OCR, and vision context. Return Markdown only. Preserve concrete teaching value: operations, parameters, node names, file paths, visual evidence, warnings, assignments, and practice tasks. Avoid filler, generic summaries, and full transcript repetition. If image assets are provided, insert only clearly relevant Markdown image links next to the related concepts, steps, or examples. If no image is clearly relevant, do not insert an image. Do not place images in a final gallery. Use only the provided relative image paths. When timeline context is provided, organize notes chronologically by time segments."
                },
                {
                    "role": "user",
                    "content": format!(
                        "标题：{}\n来源：{}\n\n请生成结构化学习笔记，优先服务复习和实操。\n\n必须保留：\n- 教学目标/作业要求\n- 关键概念、节点/工具/文件路径/参数\n- 按时间顺序的操作步骤和视觉变化\n- 易错点、注意事项、实践建议\n- 支撑结论的少量关键转写/OCR/Vision 依据\n\n避免：\n- 泛泛总结、空洞评价和套话\n- 完整复述 transcript\n- 重复 OCR 或 Vision 描述\n- 没有学习价值的段落\n\n建议结构：\n# 标题\n## 本节目标\n## 操作步骤\n## 关键概念与参数\n## 视觉/截图依据\n## 易错点\n## 作业/练习\n## 关键依据\n\n图片素材：\n{}\n{}要求：\n- 只有图片与当前段落内容明确相关时，才在对应段落附近插入图片；没有相关图片就不插。\n- 图片语法必须使用素材中给出的 Markdown，例如 ![frame-001](xxx/frame-001.png)。\n- 不要把图片集中放在文末，也不要编造图片路径。\n- “关键依据”只列出支撑笔记结论的关键转写/OCR/Vision 片段，不要把它写成完整原文转录。\n\n转写：\n{}",
                        title,
                        source.display(),
                        if image_context.trim().is_empty() { "无" } else { image_context },
                        timeline_section,
                        clipped
                    )
                }
            ],
            "temperature": 0.2
        }))
        .send()
        .map_err(|error| error.to_string())?;
    if !response.status().is_success() {
        return Err(format!("chat completion returned {}", response.status()));
    }
    let payload: Value = response.json().map_err(|error| error.to_string())?;
    payload
        .get("choices")
        .and_then(Value::as_array)
        .and_then(|choices| choices.first())
        .and_then(|choice| choice.get("message"))
        .and_then(|message| message.get("content"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|content| !content.is_empty())
        .map(ToOwned::to_owned)
        .ok_or_else(|| "chat completion returned no content".to_string())
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
        return value
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

#[derive(Clone, Copy)]
enum ControlledOutputMode {
    Piped,
    Null,
}

const CANCELLATION_ERROR_PREFIX: &str = "__VIDEO_NOTES_CANCELLED__";

fn cancellation_error(label: &str) -> String {
    format!("{CANCELLATION_ERROR_PREFIX}: {label}")
}

fn is_cancellation_error(error: &str) -> bool {
    error.starts_with(CANCELLATION_ERROR_PREFIX)
}

fn read_pipe_thread<R: Read + Send + 'static>(mut reader: R) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = reader.read_to_end(&mut bytes);
        bytes
    })
}

fn run_controlled_command(
    mut command: Command,
    control: &Arc<JobControl>,
    label: &str,
    stdout_mode: ControlledOutputMode,
    stderr_mode: ControlledOutputMode,
) -> Result<Output, String> {
    if control.cancel_requested.load(Ordering::SeqCst) {
        return Err(cancellation_error(label));
    }
    match stdout_mode {
        ControlledOutputMode::Piped => {
            command.stdout(Stdio::piped());
        }
        ControlledOutputMode::Null => {
            command.stdout(Stdio::null());
        }
    }
    match stderr_mode {
        ControlledOutputMode::Piped => {
            command.stderr(Stdio::piped());
        }
        ControlledOutputMode::Null => {
            command.stderr(Stdio::null());
        }
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("failed to run {label}: {error}"))?;
    if let Ok(mut current_child) = control.current_child.lock() {
        *current_child = Some(child.id());
    }
    let stdout_reader = child.stdout.take().map(read_pipe_thread);
    let stderr_reader = child.stderr.take().map(read_pipe_thread);

    let status = loop {
        if control.cancel_requested.load(Ordering::SeqCst) {
            kill_process_tree_or_child(&mut child);
            let _ = child.wait();
            let stdout = stdout_reader
                .map(|reader| reader.join().unwrap_or_default())
                .unwrap_or_default();
            let stderr = stderr_reader
                .map(|reader| reader.join().unwrap_or_default())
                .unwrap_or_default();
            let _ = (stdout, stderr);
            if let Ok(mut current_child) = control.current_child.lock() {
                *current_child = None;
            }
            return Err(cancellation_error(label));
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => std::thread::sleep(Duration::from_millis(75)),
            Err(error) => {
                kill_process_tree_or_child(&mut child);
                let _ = child.wait();
                if let Ok(mut current_child) = control.current_child.lock() {
                    *current_child = None;
                }
                return Err(format!("failed to wait for {label}: {error}"));
            }
        }
    };

    let stdout = stdout_reader
        .map(|reader| reader.join().unwrap_or_default())
        .unwrap_or_default();
    let stderr = stderr_reader
        .map(|reader| reader.join().unwrap_or_default())
        .unwrap_or_default();
    if let Ok(mut current_child) = control.current_child.lock() {
        *current_child = None;
    }
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

fn kill_process_tree_or_child(child: &mut std::process::Child) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    #[cfg(target_os = "windows")]
    {
        let pid = child.id().to_string();
        let system_taskkill = std::env::var_os("SystemRoot")
            .map(PathBuf::from)
            .map(|root| root.join("System32").join("taskkill.exe"));
        let candidates = system_taskkill
            .into_iter()
            .chain(std::iter::once(PathBuf::from("taskkill")));
        for candidate in candidates {
            let status = hidden_command(candidate)
                .args(["/PID", &pid, "/T", "/F"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            if status.map(|status| status.success()).unwrap_or(false) {
                return;
            }
        }
    }
    let _ = child.kill();
}

fn run_controlled_command_piped(
    command: Command,
    control: &Arc<JobControl>,
    label: &str,
) -> Result<Output, String> {
    run_controlled_command(
        command,
        control,
        label,
        ControlledOutputMode::Piped,
        ControlledOutputMode::Piped,
    )
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
        "whisper-cpp-tools" | "whisper-cpp-cuda-tools" => ("whisper-cli", &["--version"]),
        "tesseract-ocr-tools" => ("tesseract", &["--version"]),
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

fn component_latest_version(manifest: &Value) -> Option<String> {
    let url = manifest_string(manifest, "download_url")?;
    let (owner, repo) = github_repo_from_url(&url)?;
    let api_url = format!("https://api.github.com/repos/{owner}/{repo}/releases/latest");
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(5))
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
    let mut parts = rest.split('/');
    let owner = parts.next()?.trim();
    let repo = parts.next()?.trim();
    if owner.is_empty() || repo.is_empty() {
        None
    } else {
        Some((owner.to_string(), repo.to_string()))
    }
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

fn whisper_model_dirs(settings: &Map<String, Value>, data_dir: &Path) -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Some(model_dir) =
        string_value(settings, "whisper_model_dir").or_else(|| string_value(settings, "model_dir"))
    {
        if !model_dir.trim().is_empty() {
            dirs.push(PathBuf::from(model_dir));
        }
    }
    dirs.push(data_dir.join("models"));
    dirs
}

fn resolve_whisper_model(model_dirs: &[PathBuf], model_id: &str) -> Option<PathBuf> {
    let candidates = [
        model_id.to_string(),
        format!("{model_id}.bin"),
        format!("{model_id}.gguf"),
        format!("ggml-{model_id}.bin"),
        format!("ggml-{model_id}.gguf"),
    ];
    for dir in model_dirs {
        for candidate in candidates.iter() {
            let path = dir.join(candidate);
            if path.is_file() {
                return Some(path);
            }
        }
    }
    None
}

fn whisper_components_for_device(device: &str) -> Vec<&'static str> {
    match device {
        "cuda" => vec!["whisper-cpp-cuda-tools"],
        "cpu" => vec!["whisper-cpp-tools"],
        _ => vec!["whisper-cpp-cuda-tools", "whisper-cpp-tools"],
    }
}

fn transcribe_with_whisper_cpp(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
    model_dirs: &[PathBuf],
    whisper_model: &str,
    whisper_device: &str,
    control: &Arc<JobControl>,
) -> Result<(String, Option<String>), String> {
    let ffmpeg = resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir).ok_or_else(|| {
        "ffmpeg not found; install ffmpeg-tools or add FFmpeg to PATH".to_string()
    })?;
    let whisper_components = whisper_components_for_device(whisper_device);
    let whisper = resolve_tool_path("whisper-cli", &whisper_components, runtime_dir)
        .or_else(|| resolve_tool_path("main", &whisper_components, runtime_dir))
        .ok_or_else(|| {
            if whisper_device == "cuda" {
                "whisper.cpp CUDA executable not found; install whisper-cpp-cuda-tools".to_string()
            } else {
                "whisper.cpp executable not found; install whisper-cpp-tools".to_string()
            }
        })?;
    let model = resolve_whisper_model(model_dirs, whisper_model).ok_or_else(|| {
        format!("Whisper model '{whisper_model}' not found; configure whisper_model_dir")
    })?;

    let audio_path = output_dir.join(format!("{file_stem}-{id}.wav"));
    let mut ffmpeg_command = hidden_command(&ffmpeg);
    ffmpeg_command.args([
        "-y",
        "-i",
        &input_path.to_string_lossy(),
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        &audio_path.to_string_lossy(),
    ]);
    let ffmpeg_output = run_controlled_command_piped(ffmpeg_command, control, "ffmpeg")?;
    if !ffmpeg_output.status.success() {
        return Err(format!(
            "ffmpeg failed: {}",
            String::from_utf8_lossy(&ffmpeg_output.stderr).trim()
        ));
    }

    let out_prefix = output_dir.join(format!("{file_stem}-{id}-whisper"));
    let mut whisper_command = hidden_command(&whisper);
    whisper_command.args([
        "-m",
        &model.to_string_lossy(),
        "-f",
        &audio_path.to_string_lossy(),
        "-otxt",
        "-oj",
        "-of",
        &out_prefix.to_string_lossy(),
    ]);
    let whisper_output = run_controlled_command_piped(whisper_command, control, "whisper.cpp")?;
    let _ = fs::remove_file(&audio_path);
    if !whisper_output.status.success() {
        return Err(format!(
            "whisper.cpp failed: {}",
            String::from_utf8_lossy(&whisper_output.stderr).trim()
        ));
    }

    let txt_path = out_prefix.with_extension("txt");
    let text = fs::read_to_string(&txt_path)
        .map(|text| text.trim().to_string())
        .map_err(|error| format!("whisper.cpp did not produce transcript: {error}"))?;
    let json_str = fs::read_to_string(out_prefix.with_extension("json")).ok();
    Ok((text, json_str))
}

fn download_with_ytdlp(
    url: &str,
    output_dir: &Path,
    id: u64,
    runtime_dir: &Path,
    control: &Arc<JobControl>,
) -> Result<PathBuf, String> {
    let ytdlp = resolve_tool_path("yt-dlp", &["download-tools"], runtime_dir).ok_or_else(|| {
        "yt-dlp not found; install download-tools or add yt-dlp to PATH".to_string()
    })?;
    let download_dir = output_dir.join(format!("download-{id}"));
    fs::create_dir_all(&download_dir).map_err(|error| error.to_string())?;
    let template = download_dir.join("%(title).180s.%(ext)s");
    let mut command = hidden_command(&ytdlp);
    command.args(["--no-playlist", "-o", &template.to_string_lossy(), url]);
    let output = run_controlled_command_piped(command, control, "yt-dlp")?;
    if !output.status.success() {
        return Err(format!(
            "yt-dlp failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    newest_file(&download_dir).ok_or_else(|| {
        format!(
            "yt-dlp completed but no media file was found in {}",
            download_dir.display()
        )
    })
}

fn extract_ocr_with_tesseract(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
    frame_interval: f64,
    max_frames: u32,
    frame_mode: &str,
    control: &Arc<JobControl>,
) -> Result<OcrExtraction, String> {
    let tesseract = resolve_tool_path("tesseract", &["tesseract-ocr-tools"], runtime_dir)
        .ok_or_else(|| "tesseract not found; install tesseract-ocr-tools".to_string())?;
    let mut output = String::new();
    let frame_result = extract_sample_frames(
        input_path,
        output_dir,
        file_stem,
        id,
        runtime_dir,
        frame_interval,
        max_frames,
        frame_mode,
        control,
    )?;
    for frame in &frame_result.frames {
        let mut command = hidden_command(&tesseract);
        command.arg(&frame).arg("stdout");
        let result = run_controlled_command_piped(command, control, "tesseract")?;
        if result.status.success() {
            let text = String::from_utf8_lossy(&result.stdout).trim().to_string();
            if !text.is_empty() {
                output.push_str(&format!(
                    "### {}\n\n{}\n\n",
                    frame
                        .file_name()
                        .and_then(|value| value.to_str())
                        .unwrap_or("frame"),
                    text
                ));
            }
        }
    }
    Ok(OcrExtraction {
        text: output,
        frame_sampling: Some(FrameSamplingMetrics::from(&frame_result)),
    })
}

fn extract_ocr_with_http(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
    config: &OcrRuntimeConfig,
    frame_interval: f64,
    max_frames: u32,
    frame_mode: &str,
    control: &Arc<JobControl>,
) -> Result<OcrExtraction, String> {
    let endpoint = config.endpoint.trim();
    if endpoint.is_empty() {
        return Err(
            "OCR HTTP endpoint is empty. Set PaddleOCR / Custom OCR endpoint in Settings."
                .to_string(),
        );
    }
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|error| error.to_string())?;
    let mut output = String::new();
    let endpoint = if config.backend == "paddleocr_http" {
        normalise_paddleocr_jobs_endpoint(endpoint)
    } else {
        endpoint.to_string()
    };
    let frame_result = extract_sample_frames(
        input_path,
        output_dir,
        file_stem,
        id,
        runtime_dir,
        frame_interval,
        max_frames,
        frame_mode,
        control,
    )?;
    for frame in &frame_result.frames {
        if control.cancel_requested.load(Ordering::SeqCst) {
            return Err(cancellation_error("OCR HTTP"));
        }
        let text = if config.backend == "paddleocr_http" {
            ocr_frame_with_paddleocr(
                &client,
                &frame,
                &endpoint,
                &config.api_key,
                &config.model,
                control,
            )?
        } else {
            ocr_frame_with_http(&client, &frame, &endpoint, &config.api_key)?
        };
        if control.cancel_requested.load(Ordering::SeqCst) {
            return Err(cancellation_error("OCR HTTP"));
        }
        if !text.trim().is_empty() {
            output.push_str(&format!(
                "### {}\n\n{}\n\n",
                frame
                    .file_name()
                    .and_then(|value| value.to_str())
                    .unwrap_or("frame"),
                text.trim()
            ));
        }
    }
    Ok(OcrExtraction {
        text: output,
        frame_sampling: Some(FrameSamplingMetrics::from(&frame_result)),
    })
}

fn get_video_duration(
    input_path: &Path,
    runtime_dir: &Path,
    control: &Arc<JobControl>,
) -> Result<Option<f64>, String> {
    let ffprobe = resolve_tool_path("ffprobe", &["ffmpeg-tools"], runtime_dir)
        .or_else(|| resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir))
        .ok_or_else(|| "ffprobe not found for duration probe".to_string())?;
    let mut command = hidden_command(&ffprobe);
    command.args([
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        &input_path.to_string_lossy(),
    ]);
    let output = run_controlled_command_piped(command, control, "ffprobe duration")?;
    if !output.status.success() {
        return Ok(None);
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(stdout.trim().parse::<f64>().ok())
}

fn get_video_fps(
    input_path: &Path,
    runtime_dir: &Path,
    control: &Arc<JobControl>,
) -> Result<Option<f64>, String> {
    let ffprobe = resolve_tool_path("ffprobe", &["ffmpeg-tools"], runtime_dir)
        .or_else(|| resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir))
        .ok_or_else(|| "ffprobe not found for fps probe".to_string())?;
    let mut command = hidden_command(&ffprobe);
    command.args([
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate",
        "-of",
        "csv=p=0",
        &input_path.to_string_lossy(),
    ]);
    let output = run_controlled_command_piped(command, control, "ffprobe fps")?;
    if !output.status.success() {
        return Ok(None);
    }
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if stdout.is_empty() {
        return Ok(None);
    }
    // ffprobe returns rational like "30000/1001" or "25/1"
    if let Some(slash) = stdout.find('/') {
        let num: f64 = match stdout[..slash].parse() {
            Ok(value) => value,
            Err(_) => return Ok(None),
        };
        let den: f64 = match stdout[slash + 1..].parse() {
            Ok(value) => value,
            Err(_) => return Ok(None),
        };
        if den > 0.0 {
            Ok(Some(num / den))
        } else {
            Ok(None)
        }
    } else {
        Ok(stdout.parse::<f64>().ok())
    }
}

fn detect_scene_timestamps(
    input_path: &Path,
    runtime_dir: &Path,
    threshold: f64,
    control: &Arc<JobControl>,
) -> Result<Vec<f64>, String> {
    let ffmpeg = resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir)
        .ok_or_else(|| "ffmpeg not found for scene detection".to_string())?;
    let threshold_str = format!("select='gt(scene,{threshold})',showinfo");
    let mut command = hidden_command(&ffmpeg);
    command.args([
        "-i",
        &input_path.to_string_lossy(),
        "-vf",
        &threshold_str,
        "-vsync",
        "vfr",
        "-f",
        "null",
        "-",
    ]);
    let output = run_controlled_command(
        command,
        control,
        "ffmpeg scene detection",
        ControlledOutputMode::Null,
        ControlledOutputMode::Piped,
    )?;
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        // Check if it failed because there's no video stream (audio-only)
        if stderr.contains("Stream map") && stderr.contains("No matching streams") {
            return Ok(Vec::new());
        }
        return Err(format!("ffmpeg scene detection failed: {}", stderr.trim()));
    }
    let mut timestamps = Vec::new();
    for line in stderr.lines() {
        if let Some(pos) = line.find("pts_time:") {
            let rest = &line[pos + 9..];
            let end = rest
                .find(|c: char| !c.is_ascii_digit() && c != '.')
                .unwrap_or(rest.len());
            if end > 0 {
                if let Ok(ts) = rest[..end].parse::<f64>() {
                    timestamps.push(ts);
                }
            }
        }
    }
    timestamps.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    timestamps.dedup();
    Ok(timestamps)
}

fn dedup_frames(
    frames: Vec<PathBuf>,
    timestamps: Vec<f64>,
    similarity_threshold: f64,
) -> (Vec<PathBuf>, Vec<f64>, u32, Vec<String>) {
    if frames.is_empty() || frames.len() < 2 {
        let kept = frames.len() as u32;
        return (frames, timestamps, kept, Vec::new());
    }
    let mut keep_indices: Vec<usize> = Vec::new();
    let mut discard_reasons: Vec<String> = Vec::new();

    // Always keep first frame
    keep_indices.push(0);
    let mut prev_img = match image::open(&frames[0]) {
        Ok(img) => img
            .resize_exact(64, 64, image::imageops::FilterType::Lanczos3)
            .to_rgba8(),
        Err(_) => {
            // If we can't read a frame, keep it
            let kept = frames.len() as u32;
            return (
                frames,
                timestamps,
                kept,
                vec!["dedup skipped: could not read frames".to_string()],
            );
        }
    };

    for i in 1..frames.len() {
        let curr_img = match image::open(&frames[i]) {
            Ok(img) => img
                .resize_exact(64, 64, image::imageops::FilterType::Lanczos3)
                .to_rgba8(),
            Err(_) => {
                keep_indices.push(i);
                continue;
            }
        };

        // Compute pixel difference ratio (simple MSE approach)
        let total_pixels = (64 * 64 * 4) as f64;
        let mut diff_sum = 0.0f64;
        for y in 0..64 {
            for x in 0..64 {
                let p1 = prev_img.get_pixel(x, y);
                let p2 = curr_img.get_pixel(x, y);
                let dr = (p1[0] as f64 - p2[0] as f64).abs();
                let dg = (p1[1] as f64 - p2[1] as f64).abs();
                let db = (p1[2] as f64 - p2[2] as f64).abs();
                let da = (p1[3] as f64 - p2[3] as f64).abs();
                diff_sum += (dr + dg + db + da) / 4.0;
            }
        }
        let diff_ratio = diff_sum / (255.0 * total_pixels);

        // similarity_threshold: e.g. 0.95 means 95% similar → discard
        let similarity = 1.0 - diff_ratio;
        if similarity < similarity_threshold {
            // Different enough, keep it
            keep_indices.push(i);
            prev_img = curr_img;
        } else {
            discard_reasons.push(format!(
                "frame {} (t={:.1}s): {:.1}% similar to previous",
                frames[i]
                    .file_name()
                    .and_then(|v| v.to_str())
                    .unwrap_or("?"),
                timestamps[i],
                similarity * 100.0
            ));
        }
    }

    // Ensure last frame is kept (if not already)
    let last = frames.len() - 1;
    if keep_indices.last() != Some(&last) {
        keep_indices.push(last);
        // Remove the discard reason if we had previously discarded it
        if keep_indices.len() > 1 {
            // Keep last frame regardless
        }
    }

    let kept_count = keep_indices.len() as u32;
    let filtered_frames: Vec<PathBuf> = keep_indices.iter().map(|&i| frames[i].clone()).collect();
    let filtered_timestamps: Vec<f64> = keep_indices.iter().map(|&i| timestamps[i]).collect();

    (
        filtered_frames,
        filtered_timestamps,
        kept_count,
        discard_reasons,
    )
}

fn extract_sample_frames(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
    frame_interval: f64,
    max_frames: u32,
    frame_mode: &str,
    control: &Arc<JobControl>,
) -> Result<FrameSampleResult, String> {
    let ffmpeg = resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir).ok_or_else(|| {
        "ffmpeg not found; install ffmpeg-tools or add FFmpeg to PATH".to_string()
    })?;
    let frame_dir = output_dir.join(format!("{file_stem}-{id}-frames"));
    fs::create_dir_all(&frame_dir).map_err(|error| error.to_string())?;
    let pattern = frame_dir.join("frame-%03d.png");

    let (interval, duration) = if frame_mode == "adaptive" {
        let dur = get_video_duration(input_path, runtime_dir, control)?.unwrap_or(0.0);
        if dur <= 0.0 {
            (frame_interval, 0.0)
        } else {
            let computed = (dur / max_frames as f64).max(10.0);
            (computed, dur)
        }
    } else {
        (frame_interval, 0.0)
    };

    if frame_mode == "adaptive" && duration > 0.0 {
        // Adaptive mode: scene detection + dedup pipeline
        // Step 1: Generate evenly-spaced timestamps (ms)
        let max_frames = max_frames.max(2);
        let mut timestamps_ms: Vec<u64> = (0..max_frames)
            .map(|i| ((i as f64 * duration) / max_frames as f64).round() as u64)
            .collect();

        // Step 2: Add scene change timestamps from source video (threshold 0.4)
        match detect_scene_timestamps(input_path, runtime_dir, 0.4, control) {
            Ok(scene_ts) => {
                for ts in &scene_ts {
                    timestamps_ms.push((ts * 1000.0).round() as u64);
                }
            }
            Err(error) => {
                if is_cancellation_error(&error) {
                    return Err(error);
                }
            }
        }

        // Step 3: Sort, deduplicate, cap at max_frames
        timestamps_ms.sort();
        timestamps_ms.dedup();
        let _candidate_count = timestamps_ms.len() as u32;
        let final_ts_ms: Vec<u64> = if timestamps_ms.len() > max_frames as usize {
            let n = max_frames as usize;
            let step = (timestamps_ms.len() - 1) as f64 / (n - 1) as f64;
            (0..n)
                .map(|i| {
                    let idx = (i as f64 * step).round() as usize;
                    timestamps_ms[idx.min(timestamps_ms.len() - 1)]
                })
                .collect()
        } else {
            timestamps_ms
        };

        // Step 4: Compute frame numbers for ffmpeg select expression
        let fps = get_video_fps(input_path, runtime_dir, control)?.unwrap_or(30.0);
        let select_terms: Vec<String> = final_ts_ms
            .iter()
            .map(|ts_ms| format!("eq(n,{})", ((*ts_ms as f64 / 1000.0) * fps).round()))
            .collect();
        let select_expr = select_terms.join("+");

        // Step 5: Single ffmpeg call with select filter
        let mut command = hidden_command(&ffmpeg);
        command.args([
            "-y",
            "-i",
            &input_path.to_string_lossy(),
            "-vf",
            &format!("select='{}'", select_expr),
            "-vsync",
            "vfr",
            &pattern.to_string_lossy(),
        ]);
        let ffmpeg_output =
            run_controlled_command_piped(command, control, "ffmpeg adaptive frames")?;
        if !ffmpeg_output.status.success() {
            if control.cancel_requested.load(Ordering::SeqCst) {
                return Err(cancellation_error("ffmpeg adaptive frames"));
            }
            // Fallback: try simple rate-limited extraction
            let fps_str = format!("fps=1/{}", interval);
            let frames_str = max_frames.to_string();
            let mut fallback_command = hidden_command(&ffmpeg);
            fallback_command.args([
                "-y",
                "-i",
                &input_path.to_string_lossy(),
                "-vf",
                &fps_str,
                "-frames:v",
                &frames_str,
                &pattern.to_string_lossy(),
            ]);
            let fallback =
                run_controlled_command_piped(fallback_command, control, "ffmpeg frames")?;
            if !fallback.status.success() {
                return Err(format!(
                    "ffmpeg frame extraction failed: {}",
                    String::from_utf8_lossy(&fallback.stderr).trim()
                ));
            }
        }

        // Step 6: Collect extracted frames
        let mut frames: Vec<PathBuf> = fs::read_dir(&frame_dir)
            .map_err(|error| error.to_string())?
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("png"))
            .collect();
        frames.sort();
        let extracted_count = frames.len();

        // Map extracted frames to their timestamps (in output order)
        let extracted_ts: Vec<f64> = (0..extracted_count)
            .map(|i| {
                if i < final_ts_ms.len() {
                    final_ts_ms[i] as f64 / 1000.0
                } else {
                    interval * i as f64
                }
            })
            .collect();
        let candidate_count_final = extracted_count as u32;

        // Step 7: Dedup near-duplicate frames (95% similarity threshold)
        if frames.len() >= 2 {
            let (deduped_frames, deduped_ts, kept_count, _discard) =
                dedup_frames(frames, extracted_ts, 0.95);
            return Ok(FrameSampleResult {
                frames: deduped_frames,
                timestamps_sec: deduped_ts,
                duration_sec: duration,
                interval_sec: interval,
                kept_count,
                candidate_count: candidate_count_final,
            });
        }

        return Ok(FrameSampleResult {
            frames,
            timestamps_sec: extracted_ts,
            duration_sec: duration,
            interval_sec: interval,
            kept_count: candidate_count_final,
            candidate_count: candidate_count_final,
        });
    }

    // Fixed mode: rate-limited frame extraction
    let fps_str = format!("fps=1/{}", interval);
    let frames_str = max_frames.to_string();
    let mut command = hidden_command(&ffmpeg);
    command.args([
        "-y",
        "-i",
        &input_path.to_string_lossy(),
        "-vf",
        &fps_str,
        "-frames:v",
        &frames_str,
        &pattern.to_string_lossy(),
    ]);
    let ffmpeg_output = run_controlled_command_piped(command, control, "ffmpeg frames")?;
    if !ffmpeg_output.status.success() {
        return Err(format!(
            "ffmpeg frame extraction failed: {}",
            String::from_utf8_lossy(&ffmpeg_output.stderr).trim()
        ));
    }
    let mut frames: Vec<PathBuf> = fs::read_dir(&frame_dir)
        .map_err(|error| error.to_string())?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("png"))
        .collect();
    frames.sort();
    let kept = frames.len() as u32;
    let timestamps_sec: Vec<f64> = frames
        .iter()
        .filter_map(|fp| {
            let idx = frame_index_from_path(fp)?;
            Some(idx as f64 * interval)
        })
        .collect();
    Ok(FrameSampleResult {
        frames,
        timestamps_sec,
        duration_sec: duration,
        interval_sec: interval,
        kept_count: kept,
        candidate_count: kept,
    })
}

fn collect_frame_files(frame_dir: &Path) -> Result<Vec<PathBuf>, String> {
    let mut frames = fs::read_dir(frame_dir)
        .map_err(|error| error.to_string())?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .and_then(|value| value.to_str())
                .map(|ext| {
                    matches!(
                        ext.to_ascii_lowercase().as_str(),
                        "png" | "jpg" | "jpeg" | "webp"
                    )
                })
                .unwrap_or(false)
        })
        .collect::<Vec<_>>();
    frames.sort();
    Ok(frames)
}

fn copy_frame_assets(frame_dir: &Path, output_dir: &Path, asset_stem: &str) -> Option<PathBuf> {
    let frames = collect_frame_files(frame_dir).ok()?;
    if frames.is_empty() {
        return None;
    }
    let asset_dir = output_dir.join("assets").join(asset_stem);
    fs::create_dir_all(&asset_dir).ok()?;
    for frame in frames {
        let Some(file_name) = frame.file_name() else {
            continue;
        };
        let _ = fs::copy(&frame, asset_dir.join(file_name));
    }
    Some(asset_dir)
}

fn parse_whisper_segments(json_str: &str) -> Vec<TimelineSegment> {
    let parsed: serde_json::Value = match serde_json::from_str(json_str) {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    // Support two whisper.cpp JSON output formats:
    // 1. "segments": [{"start": 0.0, "end": 5.2, "text": "..."}]
    // 2. "transcription": [{"offsets": {"from": 260, "to": 4060}, "text": "..."}]
    let raw_segments = parsed["segments"]
        .as_array()
        .or_else(|| parsed["transcription"].as_array())
        .map(|segments| segments.clone())
        .unwrap_or_default();
    raw_segments
        .iter()
        .filter_map(|seg| {
            let (start_ms, end_ms) =
                if let (Some(s), Some(e)) = (seg["start"].as_f64(), seg["end"].as_f64()) {
                    // Format 1: start/end in seconds
                    (s * 1000.0, e * 1000.0)
                } else if let (Some(from), Some(to)) = (
                    seg["offsets"]["from"].as_f64(),
                    seg["offsets"]["to"].as_f64(),
                ) {
                    // Format 2: offsets.from/offsets.to in milliseconds
                    (from, to)
                } else {
                    return None;
                };
            let text = seg["text"].as_str()?.trim().to_string();
            if text.is_empty() {
                return None;
            }
            Some(TimelineSegment {
                start_sec: start_ms / 1000.0,
                end_sec: end_ms / 1000.0,
                text,
                ocr_text: None,
                vision_summary: None,
                frame_paths: Vec::new(),
            })
        })
        .collect()
}

fn frame_index_from_path(path: &Path) -> Option<usize> {
    let stem = path.file_stem()?.to_str()?;
    stem.strip_prefix("frame-")?.parse::<usize>().ok()
}

fn merge_frames_into_timeline(
    segments: &mut [TimelineSegment],
    frame_ocrs: &HashMap<String, String>,
    frame_paths: &[PathBuf],
    timestamps_sec: &[f64],
) {
    for (frame_path, ts) in frame_paths.iter().zip(timestamps_sec.iter()) {
        let file_name = frame_path
            .file_name()
            .and_then(|v| v.to_str())
            .unwrap_or("")
            .to_string();
        let ocr_text = frame_ocrs.get(&file_name).cloned();

        // Find nearest segment by timestamp
        let mut best: Option<usize> = None;
        for (i, seg) in segments.iter().enumerate() {
            if *ts >= seg.start_sec && *ts < seg.end_sec {
                best = Some(i);
                break;
            }
        }
        // Fallback: find segment with closest start
        let best = best.unwrap_or_else(|| {
            let mut closest = 0usize;
            let mut min_dist = f64::MAX;
            for (i, seg) in segments.iter().enumerate() {
                let dist = (*ts - seg.start_sec).abs();
                if dist < min_dist {
                    min_dist = dist;
                    closest = i;
                }
            }
            closest
        });

        let seg = &mut segments[best];
        seg.frame_paths.push(frame_path.clone());
        if let Some(text) = ocr_text {
            seg.ocr_text = Some(text);
        }
    }
}

fn markdown_image_context(
    note_path: &Path,
    frame_dir: &Path,
    transcript: &str,
    limit: usize,
) -> String {
    let mut frames = fs::read_dir(frame_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .and_then(|value| value.to_str())
                .map(|ext| {
                    matches!(
                        ext.to_ascii_lowercase().as_str(),
                        "png" | "jpg" | "jpeg" | "webp"
                    )
                })
                .unwrap_or(false)
        })
        .collect::<Vec<_>>();
    frames.sort();
    if frames.is_empty() {
        return String::new();
    }

    let note_dir = note_path.parent().unwrap_or_else(|| Path::new("."));
    let ocr_by_frame = ocr_text_by_frame(transcript);
    let mut context = String::new();
    for frame in frames.into_iter().take(limit) {
        let rel = frame.strip_prefix(note_dir).unwrap_or(&frame);
        let rel = rel.to_string_lossy().replace('\\', "/");
        let label = frame
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("frame");
        let file_name = frame
            .file_name()
            .and_then(|value| value.to_str())
            .unwrap_or(label);
        let ocr = ocr_by_frame
            .get(file_name)
            .map(|value| value.chars().take(260).collect::<String>())
            .unwrap_or_default();
        context.push_str(&format!(
            "- {}: ![{}]({})\n  OCR: {}\n",
            file_name,
            label,
            rel,
            if ocr.trim().is_empty() {
                "无可用文字"
            } else {
                ocr.trim()
            }
        ));
    }
    context
}

fn ocr_text_by_frame(transcript: &str) -> HashMap<String, String> {
    let mut by_frame = HashMap::new();
    let mut current: Option<String> = None;
    let mut buffer = String::new();
    for line in transcript.lines() {
        if let Some(name) = line.trim().strip_prefix("### ") {
            if let Some(frame) = current.take() {
                by_frame.insert(frame, buffer.trim().to_string());
                buffer.clear();
            }
            current = Some(name.trim().to_string());
        } else if current.is_some() {
            buffer.push_str(line);
            buffer.push('\n');
        }
    }
    if let Some(frame) = current {
        by_frame.insert(frame, buffer.trim().to_string());
    }
    by_frame
}

fn ocr_frame_with_paddleocr(
    client: &reqwest::blocking::Client,
    frame: &Path,
    endpoint: &str,
    api_key: &str,
    model: &str,
    control: &Arc<JobControl>,
) -> Result<String, String> {
    if control.cancel_requested.load(Ordering::SeqCst) {
        return Err(cancellation_error("PaddleOCR"));
    }
    if api_key.trim().is_empty() {
        return Err("PaddleOCR API Key is empty".to_string());
    }
    let bytes = fs::read(frame).map_err(|error| error.to_string())?;
    let filename = frame
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("frame.png");
    let job_id = submit_paddleocr_job(client, endpoint, api_key, model, bytes, filename)?;
    if control.cancel_requested.load(Ordering::SeqCst) {
        return Err(cancellation_error("PaddleOCR"));
    }
    let json_url = poll_paddleocr_job(client, endpoint, api_key, &job_id, control)?;
    if control.cancel_requested.load(Ordering::SeqCst) {
        return Err(cancellation_error("PaddleOCR"));
    }
    fetch_paddleocr_jsonl_text(client, &json_url)
}

fn normalise_paddleocr_jobs_endpoint(endpoint: &str) -> String {
    let trimmed = endpoint.trim().trim_end_matches('/');
    if let Some(index) = trimmed.find(PADDLEOCR_JOBS_PATH) {
        return trimmed[..index + PADDLEOCR_JOBS_PATH.len()].to_string();
    }
    if trimmed.contains("paddleocr.aistudio-app.com") {
        return "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs".to_string();
    }
    format!("{trimmed}{PADDLEOCR_JOBS_PATH}")
}

fn submit_paddleocr_job(
    client: &reqwest::blocking::Client,
    endpoint: &str,
    api_key: &str,
    model: &str,
    bytes: Vec<u8>,
    filename: &str,
) -> Result<String, String> {
    let model = if model.trim().is_empty() {
        PADDLEOCR_DEFAULT_MODEL
    } else {
        model.trim()
    };
    let optional_payload = json!({
        "useDocOrientationClassify": false,
        "useDocUnwarping": false,
        "useChartRecognition": false,
    });
    let file_part =
        reqwest::blocking::multipart::Part::bytes(bytes).file_name(filename.to_string());
    let form = reqwest::blocking::multipart::Form::new()
        .text("model", model.to_string())
        .text("optionalPayload", optional_payload.to_string())
        .part("file", file_part);
    let response = client
        .post(endpoint)
        .bearer_auth(bearer_token(api_key))
        .multipart(form)
        .send()
        .map_err(|error| format!("PaddleOCR job submit failed: {error}"))?;
    let status = response.status();
    let text = response
        .text()
        .map_err(|error| format!("PaddleOCR job response read failed: {error}"))?;
    if !status.is_success() {
        return Err(format!(
            "PaddleOCR job submit failed: HTTP {status}: {text}"
        ));
    }
    let value = serde_json::from_str::<Value>(&text)
        .map_err(|error| format!("PaddleOCR job response is not JSON: {error}; body: {text}"))?;
    value
        .get("data")
        .and_then(|data| data.get("jobId"))
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
        .ok_or_else(|| format!("PaddleOCR job response missing data.jobId: {value}"))
}

fn poll_paddleocr_job(
    client: &reqwest::blocking::Client,
    endpoint: &str,
    api_key: &str,
    job_id: &str,
    control: &Arc<JobControl>,
) -> Result<String, String> {
    let job_url = format!("{}/{}", endpoint.trim_end_matches('/'), job_id);
    for _ in 0..60 {
        if control.cancel_requested.load(Ordering::SeqCst) {
            return Err(cancellation_error("PaddleOCR poll"));
        }
        let response = client
            .get(&job_url)
            .bearer_auth(bearer_token(api_key))
            .send()
            .map_err(|error| format!("PaddleOCR job poll failed: {error}"))?;
        let status = response.status();
        let text = response
            .text()
            .map_err(|error| format!("PaddleOCR job poll response read failed: {error}"))?;
        if !status.is_success() {
            return Err(format!("PaddleOCR job poll failed: HTTP {status}: {text}"));
        }
        let value = serde_json::from_str::<Value>(&text).map_err(|error| {
            format!("PaddleOCR job poll response is not JSON: {error}; body: {text}")
        })?;
        let data = value
            .get("data")
            .ok_or_else(|| format!("PaddleOCR job poll response missing data: {value}"))?;
        let state = data.get("state").and_then(Value::as_str).unwrap_or("");
        match state {
            "done" => {
                return data
                    .get("resultUrl")
                    .and_then(|result_url| result_url.get("jsonUrl"))
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned)
                    .ok_or_else(|| format!("PaddleOCR job done but jsonUrl is missing: {value}"));
            }
            "failed" => {
                let error = data
                    .get("errorMsg")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown error");
                return Err(format!("PaddleOCR job failed: {error}"));
            }
            "pending" | "running" | "" => {
                if control.cancel_requested.load(Ordering::SeqCst) {
                    return Err(cancellation_error("PaddleOCR poll"));
                }
                std::thread::sleep(Duration::from_secs(5));
                if control.cancel_requested.load(Ordering::SeqCst) {
                    return Err(cancellation_error("PaddleOCR poll"));
                }
            }
            other => return Err(format!("PaddleOCR job returned unknown state: {other}")),
        }
    }
    Err("PaddleOCR job timed out after 300 seconds".to_string())
}

fn fetch_paddleocr_jsonl_text(
    client: &reqwest::blocking::Client,
    json_url: &str,
) -> Result<String, String> {
    let response = client
        .get(json_url)
        .send()
        .map_err(|error| format!("PaddleOCR result download failed: {error}"))?;
    let status = response.status();
    let text = response
        .text()
        .map_err(|error| format!("PaddleOCR result read failed: {error}"))?;
    if !status.is_success() {
        return Err(format!(
            "PaddleOCR result download failed: HTTP {status}: {text}"
        ));
    }
    let mut output = Vec::new();
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        let value = serde_json::from_str::<Value>(line)
            .map_err(|error| format!("PaddleOCR jsonl line is invalid JSON: {error}"))?;
        output.extend(extract_text_from_ocr_json(&value));
    }
    output.dedup();
    Ok(output.join("\n"))
}

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

fn escape_pdf_text(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('(', "\\(")
        .replace(')', "\\)")
}

fn ocr_frame_with_http(
    client: &reqwest::blocking::Client,
    frame: &Path,
    endpoint: &str,
    api_key: &str,
) -> Result<String, String> {
    let bytes = fs::read(frame).map_err(|error| error.to_string())?;
    let image = general_purpose::STANDARD.encode(bytes);
    let filename = frame
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("frame.png");
    let value = ocr_http_json_with_image(client, endpoint, api_key, &image, filename)?;
    let text = extract_text_from_ocr_json(&value).join("\n");
    Ok(text)
}

fn ocr_http_json_with_image(
    client: &reqwest::blocking::Client,
    endpoint: &str,
    api_key: &str,
    image_base64: &str,
    filename: &str,
) -> Result<Value, String> {
    let mut request = client.post(endpoint).json(&json!({
        "image": image_base64,
        "filename": filename,
    }));
    if !api_key.trim().is_empty() {
        request = request.bearer_auth(bearer_token(api_key));
    }
    let response = request
        .send()
        .map_err(|error| format!("OCR HTTP request failed: {error}"))?;
    if !response.status().is_success() {
        return Err(format!(
            "OCR HTTP request failed: HTTP {}",
            response.status()
        ));
    }
    response
        .json::<Value>()
        .map_err(|error| format!("OCR HTTP response is not JSON: {error}"))
}

fn extract_text_from_ocr_json(value: &Value) -> Vec<String> {
    let mut result = Vec::new();
    collect_ocr_text(value, None, &mut result);
    result.dedup();
    result
}

fn collect_ocr_text(value: &Value, key: Option<&str>, result: &mut Vec<String>) {
    match value {
        Value::String(text) => {
            let key = key.unwrap_or("");
            if matches!(
                key,
                "text"
                    | "ocr_text"
                    | "rec_text"
                    | "rec_texts"
                    | "recognized_text"
                    | "transcription"
                    | "label"
                    | "word"
                    | "words"
                    | "data"
                    | "result"
                    | "results"
            ) && !text.trim().is_empty()
            {
                result.push(text.trim().to_string());
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_ocr_text(item, key, result);
            }
        }
        Value::Object(map) => {
            for (child_key, child) in map {
                collect_ocr_text(child, Some(child_key.as_str()), result);
            }
        }
        _ => {}
    }
}

fn newest_file(dir: &Path) -> Option<PathBuf> {
    fs::read_dir(dir)
        .ok()?
        .flatten()
        .filter_map(|entry| {
            let path = entry.path();
            if !path.is_file() {
                return None;
            }
            let modified = entry.metadata().ok()?.modified().ok()?;
            Some((path, modified))
        })
        .max_by_key(|(_, modified)| *modified)
        .map(|(path, _)| path)
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

fn note_id(path: &Path) -> u32 {
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

fn sanitize_filename(value: &str) -> String {
    let cleaned: String = value
        .chars()
        .map(|ch| match ch {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            ch if ch.is_control() => '_',
            ch => ch,
        })
        .collect();
    let trimmed = cleaned.trim().trim_matches('.').to_string();
    if trimmed.is_empty() {
        "video-note".to_string()
    } else {
        trimmed
    }
}

fn collect_whisper_models(dir: &Path, result: &mut Vec<Value>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_whisper_models(&path, result);
            continue;
        }
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !name.ends_with(".bin") && !name.ends_with(".gguf") {
            continue;
        }
        let id = name
            .trim_start_matches("ggml-")
            .trim_end_matches(".bin")
            .trim_end_matches(".gguf")
            .to_string();
        if result
            .iter()
            .any(|item| item.get("id").and_then(Value::as_str) == Some(id.as_str()))
        {
            continue;
        }
        result.push(json!({
            "id": id,
            "label": id,
            "path": path.to_string_lossy(),
            "backend": "whisper_cpp",
        }));
    }
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

fn install_component_from_download(manifest: &Value, target: &Path) -> Result<(), String> {
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
        download_file_with_fallback(&url, &package_path)?;
        ensure_non_empty_file(&package_path, "component package")?;
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
        fs::rename(&stage, target).map_err(|error| error.to_string())?;
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
        download_file_with_fallback(YTDLP_DOWNLOAD_URL, &exe)?;
        ensure_non_empty_file(&exe, "yt-dlp.exe")?;
        if target.exists() {
            fs::remove_dir_all(target).map_err(|error| error.to_string())?;
        }
        fs::rename(&temp, target).map_err(|error| error.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temp);
    }
    result
}

fn download_file_with_fallback(url: &str, target: &Path) -> Result<(), String> {
    match download_file_with_reqwest(url, target) {
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

fn download_file_with_reqwest(url: &str, target: &Path) -> Result<(), String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|error| error.to_string())?;
    let response = client
        .get(url)
        .header("User-Agent", "Video Notes AI")
        .send()
        .map_err(|error| format!("failed to download: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("HTTP {}", response.status()));
    }
    let bytes = response
        .bytes()
        .map_err(|error| format!("failed to read response body: {error}"))?;
    fs::write(target, bytes).map_err(|error| error.to_string())
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

fn read_component_marker_version(target: &Path) -> Option<String> {
    let text = fs::read_to_string(component_marker_path(target)).ok()?;
    let marker: Value = serde_json::from_str(&text).ok()?;
    marker
        .get("manifest_version")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
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
        fs::rename(&temp, target).map_err(|error| error.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temp);
    }
    result
}

fn install_whisper_cpp_tools_from_path(target: &Path) -> Result<(), String> {
    let whisper = system_executable_path("whisper-cli")
        .or_else(|| system_executable_path("main"))
        .ok_or_else(|| {
            "Missing local package and whisper-cli was not found on PATH. Install whisper.cpp CLI or put a component package in runtime\\packages\\whisper-cpp-tools.".to_string()
        })?;
    let source_dir = whisper
        .parent()
        .ok_or_else(|| format!("Invalid whisper executable path: {}", whisper.display()))?;
    let parent = target
        .parent()
        .ok_or_else(|| format!("Invalid component target: {}", target.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temp = parent.join(format!("{}.install", Uuid::new_v4()));
    fs::create_dir_all(&temp).map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        fs::copy(&whisper, temp.join(executable_name("whisper-cli")))
            .map_err(|error| error.to_string())?;
        for entry in fs::read_dir(source_dir)
            .map_err(|error| error.to_string())?
            .flatten()
        {
            let path = entry.path();
            if path.extension().and_then(|value| value.to_str()) == Some("dll") {
                fs::copy(&path, temp.join(entry.file_name())).map_err(|error| error.to_string())?;
            }
        }
        if target.exists() {
            fs::remove_dir_all(target).map_err(|error| error.to_string())?;
        }
        fs::rename(&temp, target).map_err(|error| error.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temp);
    }
    result
}

fn install_tesseract_tools_from_path(target: &Path) -> Result<(), String> {
    let tesseract = system_executable_path("tesseract").ok_or_else(|| {
        "Missing local package and tesseract was not found on PATH. Install Tesseract OCR or put a component package in runtime\\packages\\tesseract-ocr-tools.".to_string()
    })?;
    let source_dir = tesseract
        .parent()
        .ok_or_else(|| format!("Invalid tesseract executable path: {}", tesseract.display()))?;
    let tessdata = find_tessdata_dir(source_dir).ok_or_else(|| {
        "Tesseract was found, but tessdata was not found. Set TESSDATA_PREFIX or put tessdata next to tesseract.exe.".to_string()
    })?;
    let parent = target
        .parent()
        .ok_or_else(|| format!("Invalid component target: {}", target.display()))?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temp = parent.join(format!("{}.install", Uuid::new_v4()));
    fs::create_dir_all(&temp).map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        fs::copy(&tesseract, temp.join(executable_name("tesseract")))
            .map_err(|error| error.to_string())?;
        copy_dir_recursive(&tessdata, &temp.join("tessdata"))?;
        if target.exists() {
            fs::remove_dir_all(target).map_err(|error| error.to_string())?;
        }
        fs::rename(&temp, target).map_err(|error| error.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(&temp);
    }
    result
}

fn find_tessdata_dir(tesseract_dir: &Path) -> Option<PathBuf> {
    let sibling = tesseract_dir.join("tessdata");
    if sibling.is_dir() {
        return Some(sibling);
    }
    if let Ok(prefix) = std::env::var("TESSDATA_PREFIX") {
        let candidate = PathBuf::from(prefix);
        if candidate.is_dir()
            && candidate.file_name().and_then(|value| value.to_str()) == Some("tessdata")
        {
            return Some(candidate);
        }
        let nested = candidate.join("tessdata");
        if nested.is_dir() {
            return Some(nested);
        }
    }
    None
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

fn count_frame_files(path: &Path) -> u32 {
    let Ok(entries) = fs::read_dir(path) else {
        return 0;
    };
    entries
        .flatten()
        .filter(|entry| {
            entry
                .path()
                .extension()
                .and_then(|value| value.to_str())
                .map(|ext| {
                    matches!(
                        ext.to_ascii_lowercase().as_str(),
                        "png" | "jpg" | "jpeg" | "webp"
                    )
                })
                .unwrap_or(false)
        })
        .count()
        .try_into()
        .unwrap_or(u32::MAX)
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
mod tests {
    use super::*;
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
        let jobs = reloaded
            .call("process.list", json!({ "limit": 10 }))
            .expect("method handled")
            .expect("list succeeds");
        for job in jobs.as_array().unwrap() {
            assert_eq!(job["status"], "interrupted");
            assert_eq!(job["can_resume"], false);
            assert!(job["completed_at"].as_str().unwrap_or_default().len() > 0);
        }

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
