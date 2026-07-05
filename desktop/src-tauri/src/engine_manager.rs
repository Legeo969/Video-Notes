use crate::process_tree::ProcessTree;
use crate::protocol;
use serde_json::Value;
use std::collections::HashMap;
use std::io::BufReader;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use tauri::{AppHandle, Emitter};
use tauri::Manager;
use tokio::sync::{oneshot, Mutex};
use tokio::task;

/// Map of pending JSON-RPC request IDs → response channels.
type PendingMap = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>;

/// Manages the Python engine subprocess lifecycle and JSON-RPC communication.
///
/// Responsibilities:
/// - Spawn / shutdown the `python-engine.exe --stdio` process
/// - Route JSON-RPC responses back to awaiting callers
/// - Forward event notifications (no `id`) to the frontend as Tauri events
/// - Manage a Windows Job Object so engine crashes kill the entire process tree
/// - Timeout handling for requests
pub struct EngineManager {
    child: Option<Child>,
    /// ChildStdin wrapped in a std::sync::Mutex so we can write from shared
    /// references (the `call` method takes `&self`).
    child_stdin: Option<StdMutex<std::process::ChildStdin>>,
    pending: PendingMap,
    next_id: AtomicU64,
    engine_command: String,
    engine_args: Vec<String>,
    app_handle: AppHandle,
    process_tree: Option<ProcessTree>,
}

impl EngineManager {
    pub fn new(app_handle: AppHandle, engine_command: String, engine_args: Vec<String>) -> Self {
        Self {
            child: None,
            child_stdin: None,
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            engine_command,
            engine_args,
            app_handle,
            process_tree: None,
        }
    }

    /// Start the engine subprocess and begin reading its stdout.
    pub async fn start(&mut self) -> Result<(), String> {
        if self.child.is_some() {
            log::info!("Engine is already running");
            return Ok(());
        }

        let mut cmd = Command::new(&self.engine_command);
        cmd.args(&self.engine_args)
            .arg("--stdio")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| {
            let msg = format!(
                "Failed to spawn engine (cmd='{}', args={:?}): {}",
                self.engine_command, self.engine_args, e
            );
            log::error!("{}", msg);
            msg
        })?;

        // --- Process tree cleanup via Windows Job Object --------------------
        #[cfg(target_os = "windows")]
        {
            let mut pt =
                ProcessTree::new().map_err(|e| format!("Failed to create job object: {}", e))?;
            pt.add_process(&child)
                .map_err(|e| format!("Failed to assign process to job: {}", e))?;
            self.process_tree = Some(pt);
        }

        // Take stdin and wrap in a std mutex for thread-safe shared access
        let child_stdin = child.stdin.take().ok_or("No stdin")?;
        self.child_stdin = Some(StdMutex::new(child_stdin));

        // Take stdout for the reader task (child.stdout becomes None)
        let read_stdout = child.stdout.take().ok_or("No stdout")?;

        let pending = self.pending.clone();
        let app_handle = self.app_handle.clone();

        // --- Background reader task: parse Content-Length frames ------------
        task::spawn(async move {
            let mut reader = BufReader::new(read_stdout);

            loop {
                match protocol::read_frame(&mut reader) {
                    Ok(body) => {
                        // Parse as generic JSON value to inspect structure
                        match serde_json::from_slice::<Value>(&body) {
                            Ok(msg) => {
                                // Case 1: Response with an id → route to caller
                                if let Some(id_val) = msg.get("id").and_then(|v| v.as_u64()) {
                                    let mut map = pending.lock().await;
                                    if let Some(tx) = map.remove(&id_val) {
                                        if let Some(error) = msg.get("error") {
                                            let err_msg = error
                                                .get("message")
                                                .and_then(|v| v.as_str())
                                                .unwrap_or("unknown error")
                                                .to_string();
                                            let _ = tx.send(Err(err_msg));
                                        } else {
                                            let result = msg
                                                .get("result")
                                                .cloned()
                                                .unwrap_or(Value::Null);
                                            let _ = tx.send(Ok(result));
                                        }
                                    }
                                // Case 2: Event notification (has method, no id)
                                } else if msg.get("method").is_some() {
                                    let event_name = msg["method"]
                                        .as_str()
                                        .unwrap_or("engine:unknown");
                                    let _ = app_handle.emit(event_name, msg.clone());
                                }
                                // Case 3: Malformed / unknown → ignore
                            }
                            Err(e) => {
                                log::error!("Failed to parse engine response: {}", e);
                            }
                        }
                    }
                    Err(e) => {
                        // Stream closed or error → reader task exits
                        log::error!("Engine stdout read error: {}", e);
                        break;
                    }
                }
            }

            log::info!("Engine reader task exited");
        });

        self.child = Some(child);
        Ok(())
    }

    /// Send a JSON-RPC request and await the response (with 300 s timeout).
    ///
    /// Takes `&mut self` because writing to the engine's stdin requires
    /// mutable access to the `ChildStdin` handle (protected by an internal
    /// `std::sync::Mutex`).
    pub async fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();

        // Register pending response channel
        {
            let mut map = self.pending.lock().await;
            map.insert(id, tx);
        }

        // Build JSON-RPC request
        let request = serde_json::json!({
            "jsonrpc": "2.0",
            "protocol_version": 1,
            "id": id,
            "method": method,
            "params": params,
        });

        // Write frame to engine's stdin
        if let Some(ref stdin_mutex) = self.child_stdin {
            let body = serde_json::to_string(&request).map_err(|e| e.to_string())?;
            let mut locked = stdin_mutex.lock().unwrap();
            protocol::write_frame(&mut *locked, body.as_bytes())
                .map_err(|e| format!("Write error: {}", e))?;
        } else {
            return Err("Engine not started".to_string());
        }

        // Await response with 5-minute timeout
        let result = tokio::time::timeout(std::time::Duration::from_secs(300), rx).await;

        // Clean up the pending entry regardless of timeout/success
        {
            let mut map = self.pending.lock().await;
            map.remove(&id);
        }

        result
            .map_err(|_| "Request timeout (300 s)".to_string())?
            .map_err(|_| "Response channel closed".to_string())?
    }

    /// Gracefully shut down the engine.
    pub async fn shutdown(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child_stdin.take();
        self.process_tree.take(); // drops ProcessTree → TerminateJobObject
    }

    /// Returns `true` if the engine subprocess has been started.
    pub fn is_running(&self) -> bool {
        self.child.is_some()
    }
}

impl Drop for EngineManager {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
        }
        // ProcessTree drops here, closing the Job Object handle.
        // On Windows this terminates the entire process tree (engine + children).
    }
}

/// Resolve the Python engine command and arguments.
///
/// Resolution order:
/// 1. `VIDEO_NOTES_ENGINE` environment variable (full path to executable)
/// 2. Development mode (debug build): `python engine.py`
/// 3. Bundled sidecar binary (production / release build)
pub fn resolve_engine_path(app_handle: &AppHandle) -> (String, Vec<String>) {
    // 1. Environment variable override
    if let Ok(path) = std::env::var("VIDEO_NOTES_ENGINE") {
        let path = path.trim().to_string();
        if !path.is_empty() {
            log::info!("Using engine from VIDEO_NOTES_ENGINE: {}", path);
            return (path, vec![]);
        }
    }

    // 2. Development mode
    if cfg!(debug_assertions) {
        log::info!("Dev mode: using 'python engine.py' as engine");
        return ("python".to_string(), vec!["engine.py".to_string()]);
    }

    // 3. Production: bundled sidecar
    log::info!("Production mode: resolving bundled sidecar");
    resolve_bundled_sidecar(app_handle).unwrap_or_else(|| {
        log::warn!("Sidecar not found, falling back to 'python-engine'");
        ("python-engine".to_string(), vec![])
    })
}

/// Try to locate the bundled sidecar binary in production builds.
///
/// Checks locations relative to the current executable and the
/// resource directory — the two places Tauri places sidecar binaries.
fn resolve_bundled_sidecar(app_handle: &AppHandle) -> Option<(String, Vec<String>)> {
    // Short-circuit in debug mode so unused-path warnings are avoided;
    // `resolve_engine_path` already handles the debug case above.
    if cfg!(debug_assertions) {
        return None;
    }

    let bin_name = if cfg!(target_os = "windows") {
        "python-engine.exe"
    } else {
        "python-engine"
    };

    // Check next to the current executable (common in portable builds)
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(exe_dir) = exe_path.parent() {
            // Direct sibling
            let direct = exe_dir.join(bin_name);
            if direct.exists() {
                log::info!("Found sidecar at: {}", direct.display());
                return Some((direct.to_string_lossy().to_string(), vec![]));
            }
            // Inside binaries/ subdirectory (Tauri default layout)
            let in_binaries = exe_dir.join("binaries").join(bin_name);
            if in_binaries.exists() {
                log::info!("Found sidecar at: {}", in_binaries.display());
                return Some((in_binaries.to_string_lossy().to_string(), vec![]));
            }
        }
    }

    // Check the resource / data directory
    if let Ok(res_dir) = app_handle.path().resource_dir() {
        let in_res = res_dir.join("binaries").join(bin_name);
        if in_res.exists() {
            log::info!("Found sidecar in resources at: {}", in_res.display());
            return Some((in_res.to_string_lossy().to_string(), vec![]));
        }
    }

    None
}
