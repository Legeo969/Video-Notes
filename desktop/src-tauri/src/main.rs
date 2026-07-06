// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod native_engine;
mod startup_diagnostics;

use native_engine::NativeEngine;
use tauri::{Emitter, Manager};

#[tauri::command]
async fn get_engine_info(
    native_engine: tauri::State<'_, NativeEngine>,
) -> Result<serde_json::Value, String> {
    if let Some(result) = native_engine.call("system.info", serde_json::json!({})) {
        return result;
    }
    Err("system.info is not available".to_string())
}

#[tauri::command]
async fn engine_call(
    native_engine: tauri::State<'_, NativeEngine>,
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    if let Some(result) = native_engine.call(&method, params.clone()) {
        return result;
    }
    Err(format!(
        "{method} is not available in the Rust native engine yet."
    ))
}

#[tauri::command]
async fn get_engine_status() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({
        "running": true,
        "native_running": true,
        "error": null,
        "startup_log": startup_diagnostics::log_path().to_string_lossy(),
    }))
}

fn build_app() -> Result<tauri::App<tauri::Wry>, tauri::Error> {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .setup(|app| {
            startup_diagnostics::append("Tauri setup started");

            let native_engine = NativeEngine::new(app.handle());

            let app_handle = app.handle().clone();
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

    app.run(|_app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            startup_diagnostics::append("Desktop exit requested");
            startup_diagnostics::append("Desktop shutdown completed");
        }
    });
}
