// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod compile;
mod native_engine;
mod startup_diagnostics;
mod study;

#[cfg(feature = "compiler_v3")]
mod compile_v3;

use native_engine::NativeEngine;
use tauri::{Emitter, Manager};

#[cfg(feature = "compiler_v3")]
use video_notes_ai::AppState;

#[tauri::command]
async fn get_engine_info(
    native_engine: tauri::State<'_, NativeEngine>,
) -> Result<serde_json::Value, String> {
    let engine = native_engine.inner().clone();
    let result =
        tokio::task::spawn_blocking(move || engine.call("system.info", serde_json::json!({})))
            .await
            .map_err(|error| error.to_string())?;
    if let Some(result) = result {
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
    let engine = native_engine.inner().clone();
    let method_for_call = method.clone();
    let result = tokio::task::spawn_blocking(move || engine.call(&method_for_call, params))
        .await
        .map_err(|error| error.to_string())?;
    if let Some(result) = result {
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

/// List all stored v0.2 bundle versions for a source hash.
#[cfg(feature = "compiler_v3")]
#[tauri::command]
fn list_v02_versions(
    source_hash: String,
    state: tauri::State<'_, AppState>,
) -> Result<Vec<crate::compile_v3::StoredBundle>, String> {
    use crate::compile_v3::BundleStore;
    let store = crate::compile_v3::FileBundleStore::new(state.storage_dir.clone());
    store.list_versions(&source_hash)
}

fn build_app() -> Result<tauri::App<tauri::Wry>, tauri::Error> {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            let _ = app.get_webview_window("main").map(|window| {
                let _ = window.show();
                let _ = window.set_focus();
            });
        }))
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                startup_diagnostics::append("Window close requested; cancelling jobs");
                if let Some(engine) = window.try_state::<NativeEngine>() {
                    engine.cancel_all_jobs();
                }
            }
        })
        .setup(|app| {
            startup_diagnostics::append("Tauri setup started");

            let native_engine = NativeEngine::new(app.handle());

            #[cfg(feature = "compiler_v3")]
            {
                let storage_dir = native_engine.capsule_storage_dir();
                app.manage(AppState { storage_dir });
            }

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
            #[cfg(feature = "compiler_v3")]
            list_v02_versions,
        ])
        .build(tauri::generate_context!())
}

fn main() {
    startup_diagnostics::install_panic_hook();
    startup_diagnostics::append(format!(
        "Desktop process starting; version {}",
        env!("CARGO_PKG_VERSION")
    ));

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
