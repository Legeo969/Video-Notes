use chrono::Utc;
use serde_json::{json, Map, Value};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter, Manager};
use uuid::Uuid;

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
            "settings.providers.delete" | "settings.providers.remove" => self.providers_delete(params),
            "settings.providers.set_active" => self.providers_set_active(params),
            "settings.providers.test" => Ok(json!({
                "success": false,
                "message": "Native provider connectivity test is not migrated yet"
            })),
            "settings.providers.models" => Ok(json!([])),
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
            "notes.list" | "notes.search" => Ok(json!([])),
            "collection.list" => Ok(json!([])),
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
            "python_version": null,
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
            "python_version": null,
            "timestamp": Utc::now().to_rfc3339(),
        }))
    }

    fn system_capabilities(&self) -> Result<Value, String> {
        Ok(json!({
            "has_ffmpeg": tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir),
            "has_ytdlp": tool_exists("yt-dlp", &["download-tools"], &self.runtime_dir),
            "has_whisper": false,
            "has_whisper_cpp": tool_exists("whisper-cli", &["whisper-cpp-tools"], &self.runtime_dir)
                || tool_exists("main", &["whisper-cpp-tools"], &self.runtime_dir),
            "has_ocr": tool_exists("tesseract", &["tesseract-ocr-tools"], &self.runtime_dir),
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
            "transcription_backend": string_value(&raw, "transcription_backend").unwrap_or_else(|| "whisper_cpp".to_string()),
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
        if string_value(&raw, "active_provider").unwrap_or_default().is_empty() {
            raw.insert("active_provider".to_string(), json!(name));
            raw.insert("bindings".to_string(), json!({ "llm": { "provider": name, "model": model } }));
        }
        self.write_settings(raw)?;
        Ok(json!(true))
    }

    fn providers_update(&self, params: Value) -> Result<Value, String> {
        let name = required_string(&params, "name")?;
        let mut raw = self.read_settings();
        let profile = find_provider_mut(&mut raw, &name)?;
        if let Some(provider_type) = string_param(&params, "provider").or_else(|| string_param(&params, "type")) {
            profile.insert("type".to_string(), json!(normalise_provider_type(Some(&provider_type))));
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
        let model = profile.get("model").and_then(Value::as_str).unwrap_or("").to_string();
        let vision_model = profile
            .get("vision_model")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        profile.insert("models".to_string(), json!(clean_models(vec![model, vision_model])));
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
        if let Some(model_dir) = string_value(&raw, "whisper_model_dir")
            .or_else(|| string_value(&raw, "model_dir"))
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
        let ffmpeg = tool_exists("ffmpeg", &["ffmpeg-tools"], &self.runtime_dir);
        let ytdlp = tool_exists("yt-dlp", &["download-tools"], &self.runtime_dir);
        let whisper_cpp = tool_exists("whisper-cli", &["whisper-cpp-tools"], &self.runtime_dir)
            || tool_exists("main", &["whisper-cpp-tools"], &self.runtime_dir);
        let tesseract = tool_exists("tesseract", &["tesseract-ocr-tools"], &self.runtime_dir);
        Ok(json!([
            check_item("Rust native engine", true, "in-process"),
            check_item("Python", false, "not used by native engine"),
            check_item("FFmpeg", ffmpeg, "system PATH or ffmpeg-tools"),
            check_item("yt-dlp", ytdlp, "download-tools"),
            check_item("whisper.cpp", whisper_cpp, "whisper-cpp-tools"),
            check_item("Tesseract OCR", tesseract, "tesseract-ocr-tools")
        ]))
    }

    fn diagnostics_bundle(&self) -> Result<Value, String> {
        let dir = self.data_dir.join("diagnostics");
        fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
        let path = dir.join(format!("diagnostics-{}.json", Utc::now().format("%Y%m%d-%H%M%S")));
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
        if !self.manifests_dir.is_dir() {
            return Ok(json!(result));
        }
        for entry in fs::read_dir(&self.manifests_dir).map_err(|error| error.to_string())? {
            let path = entry.map_err(|error| error.to_string())?.path();
            if path.extension().and_then(|value| value.to_str()) != Some("json") {
                continue;
            }
            let Ok(manifest) = read_json_file(&path) else {
                continue;
            };
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
        let source = self.runtime_dir.join("packages").join(&component);
        if !source.is_dir() {
            return Err(format!(
                "Native installer expects an existing package at {}",
                source.display()
            ));
        }
        let target = self.runtime_dir.join("components").join(&component);
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
        let jobs = self.jobs.lock().map_err(|_| "jobs lock poisoned".to_string())?;
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
            let mut jobs = self.jobs.lock().map_err(|_| "jobs lock poisoned".to_string())?;
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
        let whisper_model = string_value(&settings, "whisper_model").unwrap_or_else(|| "large-v3".to_string());
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
            );
        });

        Ok(json!({ "job_id": id }))
    }

    fn process_delete(&self, params: Value) -> Result<Value, String> {
        let id = params
            .get("job_id")
            .and_then(Value::as_u64)
            .ok_or_else(|| "job_id is required".to_string())?;
        let mut jobs = self.jobs.lock().map_err(|_| "jobs lock poisoned".to_string())?;
        let old_len = jobs.len();
        jobs.retain(|job| job.id != id);
        Ok(json!(jobs.len() != old_len))
    }

    fn read_manifest(&self, component: &str) -> Result<Value, String> {
        read_json_file(&self.manifests_dir.join(format!("{component}.json")))
            .map_err(|error| format!("manifest '{component}' not found or invalid: {error}"))
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

    let transcript = match transcribe_with_whisper_cpp(
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
            "Native transcript unavailable\n\nSource: {}\n\nReason: {}\n\nThis task ran without Python.",
            input_path.display(),
            error
        ),
    };
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

    let transcript_preview = fs::read_to_string(&transcript_path)
        .unwrap_or_default()
        .chars()
        .take(6000)
        .collect::<String>();
    let note = format!(
        "# {base_title}\n\n- Source: `{}`\n- Engine: Rust native\n- Created: {}\n\n## Summary\n\nNative note generation is handled by the Rust engine. AI synthesis is the next migration stage; this note includes the native transcript output for now.\n\n## Transcript\n\n{}\n\nFull transcript: `{}`.\n",
        input_path.display(),
        Utc::now().to_rfc3339(),
        transcript_preview,
        transcript_path.display()
    );
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
        return PathBuf::from(base).join("Video Notes AI").join("settings.json");
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
    if let Some(model_dir) = string_value(settings, "whisper_model_dir")
        .or_else(|| string_value(settings, "model_dir"))
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
    let ffmpeg = resolve_tool_path("ffmpeg", &["ffmpeg-tools"], runtime_dir)
        .ok_or_else(|| "ffmpeg not found; install ffmpeg-tools or add FFmpeg to PATH".to_string())?;
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
    let ytdlp = resolve_tool_path("yt-dlp", &["download-tools"], runtime_dir)
        .ok_or_else(|| "yt-dlp not found; install download-tools or add yt-dlp to PATH".to_string())?;
    let download_dir = output_dir.join(format!("download-{id}"));
    fs::create_dir_all(&download_dir).map_err(|error| error.to_string())?;
    let template = download_dir.join("%(title).180s.%(ext)s");
    let output = Command::new(&ytdlp)
        .args([
            "--no-playlist",
            "-o",
            &template.to_string_lossy(),
            url,
        ])
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
                        "ocr_backend": "tesseract",
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
        assert_eq!(settings["ocr_backend"], "tesseract");
        assert_eq!(settings["template"], "summary");

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn components_list_reports_native_manifest_status() {
        let (engine, root) = temp_engine();
        fs::create_dir_all(&engine.manifests_dir).unwrap();
        fs::create_dir_all(
            engine
                .runtime_dir
                .join("components")
                .join("download-tools"),
        )
        .unwrap();
        fs::write(
            engine
                .runtime_dir
                .join("components")
                .join("download-tools")
                .join(if cfg!(target_os = "windows") { "yt-dlp.exe" } else { "yt-dlp" }),
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
}
