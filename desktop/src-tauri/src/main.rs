// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod engine_manager;
mod native_engine;
mod process_tree;
mod protocol;
mod startup_diagnostics;

use engine_manager::EngineManager;
use native_engine::NativeEngine;
use std::sync::Arc;
use tauri::{Emitter, Manager};
use tokio::sync::Mutex;

#[tauri::command]
async fn get_engine_info(
    engine: tauri::State<'_, Arc<Mutex<EngineManager>>>,
    native_engine: tauri::State<'_, NativeEngine>,
) -> Result<serde_json::Value, String> {
    if let Some(result) = native_engine.call("system.info", serde_json::json!({})) {
        return result;
    }
    let client = {
        let mut mgr = engine.lock().await;
        if !mgr.is_running() {
            mgr.start().await?;
        }
        mgr.client()?
    };
    client.call("system.info", serde_json::json!({})).await
}

#[tauri::command]
async fn engine_call(
    engine: tauri::State<'_, Arc<Mutex<EngineManager>>>,
    native_engine: tauri::State<'_, NativeEngine>,
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    if let Some(result) = native_engine.call(&method, params.clone()) {
        return result;
    }
    if !python_fallback_enabled() {
        return Err(format!(
            "{method} is not available in the Rust native engine yet. Python fallback is disabled."
        ));
    }
    let client = {
        let mut mgr = engine.lock().await;
        if !mgr.is_running() {
            mgr.start().await?;
        }
        mgr.client()?
    };
    client.call(&method, params).await
}

#[tauri::command]
async fn get_engine_status(
    engine: tauri::State<'_, Arc<Mutex<EngineManager>>>,
) -> Result<serde_json::Value, String> {
    let mut mgr = engine.lock().await;
    let running = mgr.is_running();
    Ok(serde_json::json!({
        "running": true,
        "native_running": true,
        "python_running": running,
        "error": mgr.last_error(),
        "startup_log": startup_diagnostics::log_path().to_string_lossy(),
    }))
}

fn python_fallback_enabled() -> bool {
    std::env::var("VIDEO_NOTES_ENABLE_PYTHON_FALLBACK")
        .map(|value| matches!(value.trim(), "1" | "true" | "TRUE" | "yes" | "YES"))
        .unwrap_or(false)
}

fn build_app() -> Result<tauri::App<tauri::Wry>, tauri::Error> {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .setup(|app| {
            startup_diagnostics::append("Tauri setup started");

            // Resolve the optional legacy engine command for explicit fallback.
            let (engine_command, engine_args, engine_working_dir) =
                engine_manager::resolve_engine_path(app.handle());

            startup_diagnostics::append(format!(
                "Resolved engine command: {} {:?}; cwd={}",
                engine_command,
                engine_args,
                engine_working_dir
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "<inherited>".to_string())
            ));

            let engine = EngineManager::new(
                app.handle().clone(),
                engine_command,
                engine_args,
                engine_working_dir,
            );
            let engine = Arc::new(Mutex::new(engine));
            let native_engine = NativeEngine::new(app.handle());

            let app_handle = app.handle().clone();
            app.manage(engine);
            app.manage(native_engine);

            // Be explicit about the main window on release startup. This also
            // recovers from stale minimized/hidden state in some Windows shells.
            if let Some(window) = app.get_webview_window("main") {
                if let Err(error) = window.show() {
                    startup_diagnostics::append(format!("Could not show main window: {error}"));
                }
                if let Err(error) = window.set_focus() {
                    startup_diagnostics::append(format!("Could not focus main window: {error}"));
                }
            } else {
                startup_diagnostics::append("Main webview window was not created");
            }

            startup_diagnostics::append("Rust native engine is ready");
            let _ = app_handle.emit(
                "engine:started",
                serde_json::json!({ "running": true, "engine_kind": "rust-native" }),
            );

            startup_diagnostics::append("Tauri setup completed");
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_engine_info,
            engine_call,
            get_engine_status,
        ])
        .build(tauri::generate_context!())
}

fn main() {
    startup_diagnostics::install_panic_hook();
    startup_diagnostics::append(format!(
        "Desktop process starting; version {}",
        env!("CARGO_PKG_VERSION")
    ));
    env_logger::init();

    let app = match build_app() {
        Ok(app) => app,
        Err(error) => {
            let log_path = startup_diagnostics::log_path();
            let message = format!(
                "Video Notes AI 启动失败。\n\n{error}\n\n诊断日志：{}",
                log_path.display()
            );
            startup_diagnostics::append(format!("Tauri build failed: {error}"));
            startup_diagnostics::show_fatal_error("Video Notes AI 启动失败", &message);
            return;
        }
    };

    startup_diagnostics::append("Desktop event loop starting");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            startup_diagnostics::append("Desktop exit requested");
            let engine = app_handle.state::<Arc<Mutex<EngineManager>>>();
            if let Ok(mut mgr) = engine.try_lock() {
                mgr.shutdown();
                startup_diagnostics::append("Desktop shutdown completed");
            } else {
                // A task may still hold the lifecycle lock. Dropping the app
                // state will invoke EngineManager::drop and terminate the child.
                startup_diagnostics::append(
                    "Engine lifecycle lock was busy during exit; relying on Drop cleanup",
                );
            };
        }
    });
}
