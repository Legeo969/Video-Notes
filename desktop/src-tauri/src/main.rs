// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod engine_manager;
mod process_tree;
mod protocol;

use engine_manager::EngineManager;
use std::sync::Arc;
use tauri::Manager;
use tokio::sync::Mutex;

#[tauri::command]
async fn get_engine_info(engine: tauri::State<'_, Arc<Mutex<EngineManager>>>) -> Result<serde_json::Value, String> {
    let mut mgr = engine.lock().await;
    mgr.call("system.info", serde_json::json!({}))
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn engine_call(
    engine: tauri::State<'_, Arc<Mutex<EngineManager>>>,
    method: String,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let mut mgr = engine.lock().await;
    mgr.call(&method, params).await.map_err(|e| e.to_string())
}

#[tauri::command]
async fn get_engine_status(engine: tauri::State<'_, Arc<Mutex<EngineManager>>>) -> Result<serde_json::Value, String> {
    let mgr = engine.lock().await;
    Ok(serde_json::json!({
        "running": mgr.is_running(),
    }))
}

fn main() {
    env_logger::init();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .setup(|app| {
            // Resolve the engine command and arguments (env var → dev → sidecar)
            let (engine_command, engine_args) =
                engine_manager::resolve_engine_path(app.handle());

            let engine = EngineManager::new(
                app.handle().clone(),
                engine_command,
                engine_args,
            );
            let engine = Arc::new(Mutex::new(engine));

            // Clone before manage so we can spawn the start task
            let engine_clone = engine.clone();
            app.manage(engine);

            // Spawn engine startup in background (setup is synchronous)
            tokio::spawn(async move {
                let mut mgr = engine_clone.lock().await;
                if let Err(e) = mgr.start().await {
                    log::error!("Engine start failed: {}", e);
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_engine_info,
            engine_call,
            get_engine_status,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    // Run with event handler for graceful shutdown
    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            let engine = app_handle.state::<Arc<Mutex<EngineManager>>>();
            let engine_arc = Arc::clone(&*engine);

            // Attempt graceful shutdown using the tokio runtime
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                let _ = handle.block_on(async move {
                    let mut mgr = engine_arc.lock().await;
                    mgr.shutdown().await;
                });
            } else {
                log::warn!("Tokio runtime not available; engine will be killed on Drop");
            }
        }
    });
}
