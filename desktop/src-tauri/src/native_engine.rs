use base64::{engine::general_purpose, Engine as _};
use chrono::{DateTime, Utc};
use serde_json::{json, Map, Value};
use std::collections::{hash_map::DefaultHasher, HashSet};
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

const YTDLP_DOWNLOAD_URL: &str =
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe";
const OCR_TEST_IMAGE_BASE64: &str =
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";
const PADDLEOCR_DEFAULT_MODEL: &str = "PaddleOCR-VL-1.6";
const PADDLEOCR_JOBS_PATH: &str = "/api/v2/ocr/jobs";
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
    jobs: Arc<Mutex<Vec<NativeJob>>>,
    next_job_id: Arc<Mutex<u64>>,
}

#[derive(Clone)]
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
}

impl NativeEngine {
    pub fn new(app_handle: &AppHandle) -> Self {
        let data_dir = local_app_data_dir(app_handle);
        let settings_path = persistent_settings_path(app_handle, &data_dir);
        let runtime_dir = data_dir.join("runtime");
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
            jobs: Arc::new(Mutex::new(Vec::new())),
            next_job_id: Arc::new(Mutex::new(1)),
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
        Self {
            app_handle: None,
            settings_path,
            data_dir,
            runtime_dir,
            manifests_dir,
            default_export_dir,
            jobs: Arc::new(Mutex::new(Vec::new())),
            next_job_id: Arc::new(Mutex::new(1)),
        }
    }

    pub fn call(&self, method: &str, params: Value) -> Option<Result<Value, String>> {
        let result = match method {
            "system.ping" => Ok(json!("pong")),
            "system.info" => self.system_info(),
            "system.snapshot" => self.system_snapshot(),
            "system.capabilities" => self.system_capabilities(),
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
            "settings.ocr.test" => self.ocr_test(params),
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
            "storage.cleanup_orphans" | "storage.cleanup_completed" => Ok(json!({ "removed": 0 })),
            "process.list" => self.process_list(params),
            "process.start" => self.process_start(params),
            "process.delete" => self.process_delete(params),
            "process.pause" | "process.cancel" | "process.resume" | "process.retry" => {
                Err("Native task actions are not migrated yet".to_string())
            }
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
            "has_whisper_cpp": tool_exists("whisper-cli", &["whisper-cpp-tools"], &self.runtime_dir)
                || tool_exists("main", &["whisper-cpp-tools"], &self.runtime_dir),
            "has_ocr": tool_exists("tesseract", &["tesseract-ocr-tools"], &self.runtime_dir)
                || !string_value(&settings, "ocr_http_endpoint")
                    .or_else(|| string_value(&settings, "ocr_api_url"))
                    .unwrap_or_default()
                    .trim()
                    .is_empty(),
            "has_cuda": false,
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
            "whisper_compute_type": string_value(&raw, "whisper_compute_type").unwrap_or_else(|| "auto".to_string()),
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
            "whisper_compute_type",
            "language",
            "frame_interval",
            "frame_mode",
            "max_frames",
            "ocr_enabled",
            "ocr_backend",
            "ocr_http_endpoint",
            "ocr_http_api_key",
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
        let mut raw = self.read_settings();
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
        let backend = string_value(&raw, "ocr_backend")
            .filter(|value| {
                matches!(
                    value.as_str(),
                    "tesseract" | "paddleocr_http" | "custom_http"
                )
            })
            .unwrap_or_else(|| "tesseract".to_string());
        raw.insert("ocr_backend".to_string(), json!(backend));
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn settings_secret_set(&self, params: Value) -> Result<Value, String> {
        let provider = required_string(&params, "provider")?;
        let key = string_param(&params, "api_key")
            .or_else(|| string_param(&params, "key"))
            .ok_or_else(|| "api_key is required".to_string())?;
        let mut raw = self.read_settings();
        let profile = find_provider_mut(&mut raw, &provider)?;
        profile.insert("api_key".to_string(), json!(key));
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn settings_secret_delete(&self, params: Value) -> Result<Value, String> {
        let provider = required_string(&params, "provider")?;
        let mut raw = self.read_settings();
        let profile = find_provider_mut(&mut raw, &provider)?;
        profile.remove("api_key");
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn providers_list(&self) -> Result<Value, String> {
        let raw = self.read_settings();
        let active = string_value(&raw, "active_provider").unwrap_or_default();
        Ok(json!(provider_profiles(&raw, &active)))
    }

    fn providers_create(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        let mut raw = self.read_settings();
        if find_provider(&raw, &name).is_some() {
            return Err(format!("Provider '{name}' already exists"));
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
            json!(string_param(&params, "base_url").unwrap_or_default()),
        );
        entry.insert("models".to_string(), json!(models));
        entry.insert("model".to_string(), json!(model));
        entry.insert("vision_model".to_string(), json!(vision_model));
        if let Some(api_key) = string_param(&params, "api_key") {
            if !api_key.is_empty() {
                entry.insert("api_key".to_string(), json!(api_key));
            }
        }

        let providers = raw
            .entry("providers".to_string())
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or_else(|| "providers must be an array".to_string())?;
        providers.push(Value::Object(entry));
        if string_value(&raw, "active_provider")
            .unwrap_or_default()
            .is_empty()
        {
            raw.insert("active_provider".to_string(), json!(name));
            raw.insert(
                "bindings".to_string(),
                json!({ "llm": { "provider": name, "model": model } }),
            );
        }
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn providers_update(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        let mut raw = self.read_settings();
        let profile = find_provider_mut(&mut raw, &name)?;
        if let Some(provider_type) =
            string_param(&params, "provider").or_else(|| string_param(&params, "type"))
        {
            profile.insert(
                "type".to_string(),
                json!(normalise_provider_type(Some(&provider_type))),
            );
        }
        if let Some(base_url) = string_param(&params, "base_url") {
            profile.insert("base_url".to_string(), json!(base_url));
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
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn providers_delete(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        let mut raw = self.read_settings();
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
        if string_value(&raw, "active_provider")
            .map(|value| value.eq_ignore_ascii_case(&name))
            .unwrap_or(false)
        {
            raw.insert("active_provider".to_string(), json!(""));
        }
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn providers_set_active(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        let mut raw = self.read_settings();
        let profile = find_provider(&raw, &name)
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
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn provider_test(&self, params: Value) -> Result<Value, String> {
        let raw = self.read_settings();
        let profile = provider_profile_for_request(&raw, &params)?;
        match fetch_provider_models(&profile) {
            Ok(models) => Ok(json!({
                "success": true,
                "message": format!("服务可用，读取到 {} 个模型", models.len()),
                "models": models,
            })),
            Err(error) => Ok(json!({
                "success": false,
                "message": error,
            })),
        }
    }

    fn provider_models(&self, params: Value) -> Result<Value, String> {
        let raw = self.read_settings();
        let profile = provider_profile_for_request(&raw, &params)?;
        let models = match fetch_provider_models(&profile) {
            Ok(models) => models,
            Err(_) => clean_models(vec![profile.model.clone(), profile.vision_model.clone()]),
        };
        Ok(json!(models))
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
            let test_pdf = simple_pdf_bytes("OCR TEST");
            return match submit_paddleocr_job(
                &client,
                &endpoint,
                &api_key,
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

    fn bindings_set(&self, params: Value) -> Result<Value, String> {
        let purpose = required_string(&params, "purpose")?;
        if purpose != "llm" && purpose != "vision" {
            return Err("purpose must be 'llm' or 'vision'".to_string());
        }
        let provider = required_string(&params, "provider")?;
        let model = string_param(&params, "model").unwrap_or_default();
        let mut raw = self.read_settings();
        if find_provider(&raw, &provider).is_none() {
            return Err(format!("Provider '{provider}' not found"));
        }
        let bindings = raw
            .entry("bindings".to_string())
            .or_insert_with(|| json!({}))
            .as_object_mut()
            .ok_or_else(|| "bindings must be an object".to_string())?;
        bindings.insert(purpose, json!({ "provider": provider, "model": model }));
        self.write_settings(raw)?;
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
        let whisper_cpp = tool_exists("whisper-cli", &["whisper-cpp-tools"], &self.runtime_dir)
            || tool_exists("main", &["whisper-cpp-tools"], &self.runtime_dir);
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
            result.push(json!({
                "component": component,
                "version": manifest.get("version").and_then(Value::as_str).unwrap_or(""),
                "description": manifest.get("description").and_then(Value::as_str).unwrap_or(""),
                "installed": installed,
                "installed_version": if installed { manifest.get("version").cloned().unwrap_or(Value::Null) } else { Value::Null },
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
                "tesseract-ocr-tools" => install_tesseract_tools_from_path(&target),
                _ => Err(format!(
                    "Missing local package: {}. Put the component package there, then install again.",
                    source.display()
                )),
            };
            match path_result {
                Ok(()) => {
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
        let export_dir = string_value(&raw, "output_dir")
            .map(PathBuf::from)
            .unwrap_or_else(|| self.default_export_dir.clone());
        let state_dir = self.data_dir.join("state");
        let jobs_root = self.data_dir.join("jobs");
        let legacy_jobs_root = self.data_dir.join(".jobs");
        let db_path = state_dir.join("video_notes.db");
        let vault_path = string_value(&raw, "vault_path").unwrap_or_default();
        Ok(json!({
            "export_dir": export_dir.to_string_lossy(),
            "state_dir": state_dir.to_string_lossy(),
            "db_path": db_path.to_string_lossy(),
            "jobs_root": jobs_root.to_string_lossy(),
            "legacy_jobs_root": legacy_jobs_root.to_string_lossy(),
            "vault_path": vault_path,
            "sizes": {
                "exports": dir_size(&export_dir),
                "state": dir_size(&state_dir),
                "jobs": dir_size(&jobs_root),
                "legacy_jobs": dir_size(&legacy_jobs_root),
                "runtime": dir_size(&self.runtime_dir),
            },
            "counts": {
                "exports": dir_counts(&export_dir),
                "jobs": dir_counts(&jobs_root),
                "legacy_jobs": dir_counts(&legacy_jobs_root),
                "runtime": dir_counts(&self.runtime_dir),
            }
        }))
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
        let input = required_string(&params, "input")?;
        let title = string_param(&params, "title").or_else(|| {
            Path::new(&input)
                .file_stem()
                .and_then(|value| value.to_str())
                .map(ToOwned::to_owned)
        });
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
        };
        {
            let mut jobs = self
                .jobs
                .lock()
                .map_err(|_| "jobs lock poisoned".to_string())?;
            jobs.push(job);
        }

        let jobs = self.jobs.clone();
        let app_handle = self.app_handle.clone();
        let settings = self.read_settings();
        let output_dir = string_value(&settings, "output_dir")
            .map(PathBuf::from)
            .unwrap_or_else(|| self.default_export_dir.clone());
        let runtime_dir = self.runtime_dir.clone();
        let model_dirs = whisper_model_dirs(&settings, &self.data_dir);
        let whisper_model =
            string_value(&settings, "whisper_model").unwrap_or_else(|| "large-v3".to_string());
        let provider = active_provider_profile(&settings).ok();
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
            backend: ocr_backend,
            endpoint: string_param(&params, "ocr_http_endpoint")
                .or_else(|| string_value(&settings, "ocr_http_endpoint"))
                .or_else(|| string_value(&settings, "ocr_api_url"))
                .unwrap_or_default(),
            api_key: string_param(&params, "ocr_http_api_key")
                .or_else(|| string_value(&settings, "ocr_http_api_key"))
                .or_else(|| string_value(&settings, "ocr_api_key"))
                .unwrap_or_default(),
        };
        std::thread::spawn(move || {
            run_native_job(
                jobs,
                app_handle,
                id,
                input,
                title,
                output_dir,
                runtime_dir,
                model_dirs,
                whisper_model,
                provider,
                ocr_config,
            );
        });

        Ok(json!({ "job_id": id }))
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
        let old_len = jobs.len();
        jobs.retain(|job| job.id != id);
        Ok(json!(jobs.len() != old_len))
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
        roots.push(
            string_value(&settings, "output_dir")
                .map(PathBuf::from)
                .unwrap_or_else(|| self.default_export_dir.clone()),
        );
        if let Some(vault_path) = string_value(&settings, "vault_path") {
            roots.push(PathBuf::from(vault_path).join("video-notes"));
        }

        let mut notes = Vec::new();
        for root in roots {
            collect_markdown_notes(&root, &mut notes, 0)?;
        }
        notes.sort_by(|left, right| right.created_at.cmp(&left.created_at));
        Ok(notes)
    }

    fn collection_list(&self) -> Result<Value, String> {
        let store = self.read_collection_store();
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
        let store = self.read_collection_store();
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
        if items.is_empty() {
            return Err("collection has no processable items".to_string());
        }
        let mut run_ids = Vec::new();
        for item in items {
            let input = item.get("input").and_then(Value::as_str).unwrap_or("");
            if input.trim().is_empty() {
                continue;
            }
            let title = item.get("title").and_then(Value::as_str).unwrap_or("");
            let result = self.process_start(json!({ "input": input, "title": title }))?;
            if let Some(run_id) = result.get("job_id").and_then(Value::as_u64) {
                run_ids.push(run_id);
            }
        }
        Ok(json!({
            "batch_job_id": format!("batch-{id}-{}", Utc::now().timestamp()),
            "run_ids": run_ids,
            "count": run_ids.len(),
        }))
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
}

impl NativeJob {
    fn to_value(&self) -> Value {
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
            "elapsed_sec": null,
            "error_message": self.error_message,
            "output_path": self.output_path,
            "transcript_path": self.transcript_path,
            "frames_count": self.frames_count,
            "note_id": null,
            "attempt": 1,
            "parent_run_id": null,
            "can_resume": self.can_resume,
            "heartbeat_at": Utc::now().to_rfc3339(),
        })
    }
}

fn run_native_job(
    jobs: Arc<Mutex<Vec<NativeJob>>>,
    app_handle: Option<AppHandle>,
    id: u64,
    input: String,
    title: Option<String>,
    output_dir: PathBuf,
    runtime_dir: PathBuf,
    model_dirs: Vec<PathBuf>,
    whisper_model: String,
    provider: Option<NativeProviderProfile>,
    ocr_config: OcrRuntimeConfig,
) {
    update_job(
        &jobs,
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

    if let Err(error) = fs::create_dir_all(&output_dir) {
        update_job(
            &jobs,
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

    let input_path = if input.starts_with("http://") || input.starts_with("https://") {
        update_job(
            &jobs,
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
        match download_with_ytdlp(&input, &output_dir, id, &runtime_dir) {
            Ok(path) => path,
            Err(error) => {
                update_job(
                    &jobs,
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
    let file_stem = sanitize_filename(&base_title);
    let transcript_path = output_dir.join(format!("{file_stem}-{id}-transcript.txt"));
    let note_path = output_dir.join(format!("{file_stem}-{id}.md"));

    update_job(
        &jobs,
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

    let mut transcript = match transcribe_with_whisper_cpp(
        &input_path,
        &output_dir,
        &file_stem,
        id,
        &runtime_dir,
        &model_dirs,
        &whisper_model,
    ) {
        Ok(text) => text,
        Err(error) => format!(
            "Native transcript unavailable\n\nSource: {}\n\nReason: {}",
            input_path.display(),
            error
        ),
    };
    if ocr_config.enabled {
        update_job(
            &jobs,
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
                &output_dir,
                &file_stem,
                id,
                &runtime_dir,
                &ocr_config,
            ),
            _ => extract_ocr_with_tesseract(&input_path, &output_dir, &file_stem, id, &runtime_dir),
        };
        match ocr_result {
            Ok(ocr_text) if !ocr_text.trim().is_empty() => {
                transcript.push_str("\n\n## OCR\n\n");
                transcript.push_str(&ocr_text);
            }
            Ok(_) => {
                transcript.push_str("\n\n## OCR\n\nNo readable text detected in sampled frames.");
            }
            Err(error) => {
                transcript.push_str("\n\n## OCR\n\nOCR unavailable: ");
                transcript.push_str(&error);
            }
        }
    }
    if let Err(error) = fs::write(&transcript_path, transcript) {
        update_job(
            &jobs,
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

    update_job(
        &jobs,
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
    let generated_note = provider
        .as_ref()
        .and_then(|profile| {
            synthesize_note_with_provider(profile, &base_title, &input_path, &transcript_text).ok()
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
    if let Err(error) = fs::write(&note_path, note) {
        update_job(
            &jobs,
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

    update_job(
        &jobs,
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
            job.status = status.to_string();
            job.stage = stage.to_string();
            job.progress = progress;
            job.progress_message = message.to_string();
            if status == "completed" || status == "failed" {
                job.completed_at = Some(Utc::now().to_rfc3339());
            }
            if let Some(error) = error_message {
                job.error_message = Some(error);
            }
            if let Some(path) = output_path {
                job.output_path = Some(path);
            }
            if let Some(path) = transcript_path {
                job.transcript_path = Some(path);
            }
            event = Some(json!({
                "event_id": Utc::now().timestamp_millis(),
                "job_id": id,
                "stable_job_id": job.job_id,
                "status": status,
                "stage": stage,
                "progress": progress,
                "message": message,
                "timestamp": Utc::now().to_rfc3339(),
            }));
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
    let temp = path.with_extension("tmp");
    let mut file = fs::File::create(&temp).map_err(|error| error.to_string())?;
    let body = serde_json::to_vec_pretty(value).map_err(|error| error.to_string())?;
    file.write_all(&body).map_err(|error| error.to_string())?;
    file.write_all(b"\n").map_err(|error| error.to_string())?;
    file.sync_all().map_err(|error| error.to_string())?;
    drop(file);
    fs::rename(&temp, path).map_err(|error| error.to_string())
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
        other if !other.is_empty() => other.to_string(),
        _ => "openai_compat".to_string(),
    }
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
            return provider_from_value(profile, params);
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
    if provider_type != "openai_compat" && provider_type != "openai" {
        return Err(format!(
            "Native provider '{}' is not migrated yet; use OpenAI Compatible.",
            provider_type
        ));
    }
    if model.is_empty() {
        return Err("model is required".to_string());
    }
    if api_key.is_empty() {
        return Err("api_key is required".to_string());
    }
    Ok(NativeProviderProfile {
        base_url: if base_url.is_empty() {
            "https://api.openai.com/v1".to_string()
        } else {
            base_url
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
    let response = client
        .get(url)
        .bearer_auth(&profile.api_key)
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

fn synthesize_note_with_provider(
    profile: &NativeProviderProfile,
    title: &str,
    source: &Path,
    transcript: &str,
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
    let response = client
        .post(url)
        .bearer_auth(&profile.api_key)
        .json(&json!({
            "model": profile.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You generate concise, structured Chinese Markdown study notes from video transcripts. Return Markdown only."
                },
                {
                    "role": "user",
                    "content": format!(
                        "标题：{}\n来源：{}\n\n请生成结构化学习笔记，包含摘要、关键概念、步骤/论证、行动项和原始转写要点。\n\n转写：\n{}",
                        title,
                        source.display(),
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
    Command::new(&exe)
        .arg("--version")
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn executable_name(name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{name}.exe")
    } else {
        name.to_string()
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
        Command::new(candidate)
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

fn transcribe_with_whisper_cpp(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
    model_dirs: &[PathBuf],
    whisper_model: &str,
) -> Result<String, String> {
    let ffmpeg = resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir).ok_or_else(|| {
        "ffmpeg not found; install ffmpeg-tools or add FFmpeg to PATH".to_string()
    })?;
    let whisper = resolve_tool_path("whisper-cli", &["whisper-cpp-tools"], runtime_dir)
        .or_else(|| resolve_tool_path("main", &["whisper-cpp-tools"], runtime_dir))
        .ok_or_else(|| "whisper.cpp executable not found; install whisper-cpp-tools".to_string())?;
    let model = resolve_whisper_model(model_dirs, whisper_model).ok_or_else(|| {
        format!("Whisper model '{whisper_model}' not found; configure whisper_model_dir")
    })?;

    let audio_path = output_dir.join(format!("{file_stem}-{id}.wav"));
    let ffmpeg_output = Command::new(&ffmpeg)
        .args([
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
        ])
        .output()
        .map_err(|error| format!("failed to run ffmpeg: {error}"))?;
    if !ffmpeg_output.status.success() {
        return Err(format!(
            "ffmpeg failed: {}",
            String::from_utf8_lossy(&ffmpeg_output.stderr).trim()
        ));
    }

    let out_prefix = output_dir.join(format!("{file_stem}-{id}-whisper"));
    let whisper_output = Command::new(&whisper)
        .args([
            "-m",
            &model.to_string_lossy(),
            "-f",
            &audio_path.to_string_lossy(),
            "-otxt",
            "-of",
            &out_prefix.to_string_lossy(),
        ])
        .output()
        .map_err(|error| format!("failed to run whisper.cpp: {error}"))?;
    let _ = fs::remove_file(&audio_path);
    if !whisper_output.status.success() {
        return Err(format!(
            "whisper.cpp failed: {}",
            String::from_utf8_lossy(&whisper_output.stderr).trim()
        ));
    }

    let txt_path = out_prefix.with_extension("txt");
    fs::read_to_string(&txt_path)
        .map(|text| text.trim().to_string())
        .map_err(|error| format!("whisper.cpp did not produce transcript: {error}"))
}

fn download_with_ytdlp(
    url: &str,
    output_dir: &Path,
    id: u64,
    runtime_dir: &Path,
) -> Result<PathBuf, String> {
    let ytdlp = resolve_tool_path("yt-dlp", &["download-tools"], runtime_dir).ok_or_else(|| {
        "yt-dlp not found; install download-tools or add yt-dlp to PATH".to_string()
    })?;
    let download_dir = output_dir.join(format!("download-{id}"));
    fs::create_dir_all(&download_dir).map_err(|error| error.to_string())?;
    let template = download_dir.join("%(title).180s.%(ext)s");
    let output = Command::new(&ytdlp)
        .args(["--no-playlist", "-o", &template.to_string_lossy(), url])
        .output()
        .map_err(|error| format!("failed to run yt-dlp: {error}"))?;
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
) -> Result<String, String> {
    let tesseract = resolve_tool_path("tesseract", &["tesseract-ocr-tools"], runtime_dir)
        .ok_or_else(|| "tesseract not found; install tesseract-ocr-tools".to_string())?;
    let mut output = String::new();
    for frame in extract_sample_frames(input_path, output_dir, file_stem, id, runtime_dir)? {
        let result = Command::new(&tesseract)
            .arg(&frame)
            .arg("stdout")
            .output()
            .map_err(|error| format!("failed to run tesseract: {error}"))?;
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
    Ok(output)
}

fn extract_ocr_with_http(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
    config: &OcrRuntimeConfig,
) -> Result<String, String> {
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
    for frame in extract_sample_frames(input_path, output_dir, file_stem, id, runtime_dir)? {
        let text = if config.backend == "paddleocr_http" {
            ocr_frame_with_paddleocr(&client, &frame, &endpoint, &config.api_key)?
        } else {
            ocr_frame_with_http(&client, &frame, &endpoint, &config.api_key)?
        };
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
    Ok(output)
}

fn extract_sample_frames(
    input_path: &Path,
    output_dir: &Path,
    file_stem: &str,
    id: u64,
    runtime_dir: &Path,
) -> Result<Vec<PathBuf>, String> {
    let ffmpeg = resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir).ok_or_else(|| {
        "ffmpeg not found; install ffmpeg-tools or add FFmpeg to PATH".to_string()
    })?;
    let frame_dir = output_dir.join(format!("{file_stem}-{id}-frames"));
    fs::create_dir_all(&frame_dir).map_err(|error| error.to_string())?;
    let pattern = frame_dir.join("frame-%03d.png");
    let ffmpeg_output = Command::new(&ffmpeg)
        .args([
            "-y",
            "-i",
            &input_path.to_string_lossy(),
            "-vf",
            "fps=1/60",
            "-frames:v",
            "8",
            &pattern.to_string_lossy(),
        ])
        .output()
        .map_err(|error| format!("failed to run ffmpeg for frames: {error}"))?;
    if !ffmpeg_output.status.success() {
        return Err(format!(
            "ffmpeg frame extraction failed: {}",
            String::from_utf8_lossy(&ffmpeg_output.stderr).trim()
        ));
    }
    let mut frames = fs::read_dir(&frame_dir)
        .map_err(|error| error.to_string())?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("png"))
        .collect::<Vec<_>>();
    frames.sort();
    Ok(frames)
}

fn ocr_frame_with_paddleocr(
    client: &reqwest::blocking::Client,
    frame: &Path,
    endpoint: &str,
    api_key: &str,
) -> Result<String, String> {
    if api_key.trim().is_empty() {
        return Err("PaddleOCR API Key is empty".to_string());
    }
    let bytes = fs::read(frame).map_err(|error| error.to_string())?;
    let filename = frame
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("frame.png");
    let job_id = submit_paddleocr_job(client, endpoint, api_key, bytes, filename)?;
    let json_url = poll_paddleocr_job(client, endpoint, api_key, &job_id)?;
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
    bytes: Vec<u8>,
    filename: &str,
) -> Result<String, String> {
    let optional_payload = json!({
        "useDocOrientationClassify": false,
        "useDocUnwarping": false,
        "useChartRecognition": false,
    });
    let file_part =
        reqwest::blocking::multipart::Part::bytes(bytes).file_name(filename.to_string());
    let form = reqwest::blocking::multipart::Form::new()
        .text("model", PADDLEOCR_DEFAULT_MODEL.to_string())
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
) -> Result<String, String> {
    let job_url = format!("{}/{}", endpoint.trim_end_matches('/'), job_id);
    for _ in 0..60 {
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
            "pending" | "running" | "" => std::thread::sleep(Duration::from_secs(5)),
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
    let result = Command::new("cmd")
        .args(["/C", "start", "", &path.to_string_lossy()])
        .spawn();

    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg(path).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = Command::new("xdg-open").arg(path).spawn();

    result.map_err(|error| error.to_string())?;
    Ok(json!(true))
}

fn reveal_path(path: &Path) -> Result<Value, String> {
    #[cfg(target_os = "windows")]
    let result = Command::new("explorer")
        .arg(format!("/select,{}", path.to_string_lossy()))
        .spawn();

    #[cfg(target_os = "macos")]
    let result = Command::new("open")
        .args(["-R", &path.to_string_lossy()])
        .spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let result = Command::new("xdg-open")
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
                stage_component_files(&extracted, &stage, &files)?;
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
        match Command::new(&candidate).args(args).output() {
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
        Command::new("where").arg(&exe).output().ok()?
    } else {
        Command::new("which").arg(&exe).output().ok()?
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
        let settings_path = root.join("config").join("settings.json");
        let data_dir = root.join("data");
        let runtime_dir = root.join("runtime");
        let manifests_dir = root.join("manifests");
        let export_dir = root.join("exports");
        let engine = NativeEngine::for_paths(
            settings_path,
            data_dir,
            runtime_dir,
            manifests_dir,
            export_dir,
        );
        (engine, root)
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
        assert_eq!(settings["template"], "summary");

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
                "version": "1.5.0",
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
        assert!(transcript_path.is_file());
        assert_eq!(job["progress"], 100);

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn notes_rpc_scans_updates_and_deletes_markdown() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(root.join("exports")).unwrap();
        let note_path = root.join("exports").join("lesson.md");
        fs::write(&note_path, "# Lesson Title\n\nOriginal content").unwrap();

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
}
