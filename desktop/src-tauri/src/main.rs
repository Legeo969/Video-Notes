// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod engine_manager;
mod process_tree;
mod protocol;
mod startup_diagnostics;

use engine_manager::EngineManager;
use std::sync::Arc;
use tauri::{Emitter, Manager};
use tokio::sync::Mutex;

#[tauri::command]
async fn get_engine_info(
    engine: tauri::State<'_, Arc<Mutex<EngineManager>>>,
) -> Result<serde_json::Value, String> {
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
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
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
        "running": running,
        "error": mgr.last_error(),
        "startup_log": startup_diagnostics::log_path().to_string_lossy(),
    }))
}

fn build_app() -> Result<tauri::App<tauri::Wry>, tauri::Error> {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .setup(|app| {
            startup_diagnostics::append("Tauri setup started");

            // Resolve the engine command and arguments (env var → dev → sidecar)
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

            let engine_clone = engine.clone();
            let app_handle = app.handle().clone();
            app.manage(engine);

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

            // Use Tauri's runtime instead of tokio::spawn directly. Calling
            // tokio::spawn from synchronous setup can panic in release builds
            // when no Tokio context is entered, which previously looked like
            // "double-click does nothing" because the console is hidden.
            tauri::async_runtime::spawn(async move {
                let result = async {
                    let client = {
                        let mut mgr = engine_clone.lock().await;
                        mgr.start().await?;
                        mgr.client()?
                    };

                    // Spawning a process is not the same as a ready engine.
                    // Verify one real RPC before clearing the UI error banner.
                    tokio::time::timeout(
                        std::time::Duration::from_secs(20),
                        client.call("system.info", serde_json::json!({})),
                    )
                    .await
                    .map_err(|_| "Engine readiness check timed out after 20 seconds".to_string())??;
                    Ok::<(), String>(())
                }
                .await;

                match result {
                    Ok(()) => {
                        startup_diagnostics::append("Python engine started and passed readiness check");
                        let _ = app_handle.emit(
                            "engine:started",
                            serde_json::json!({ "running": true }),
                        );
                    }
                    Err(error) => {
                        {
                            let mut mgr = engine_clone.lock().await;
                            mgr.mark_error(error.clone());
                            mgr.shutdown();
                        }
                        startup_diagnostics::append(format!(
                            "Python engine failed readiness check: {error}"
                        ));
                        let _ = app_handle.emit(
                            "engine:start_failed",
                            serde_json::json!({
                                "error": error,
                                "startup_log": startup_diagnostics::log_path().to_string_lossy(),
                            }),
                        );
                    }
                }
            });

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
