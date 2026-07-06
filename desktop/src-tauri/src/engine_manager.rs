use crate::process_tree::ProcessTree;
use crate::protocol;
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use tauri::{AppHandle, Emitter};
use tauri::Manager;
use tokio::sync::{oneshot, Mutex};
use tokio::task;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Convert protocol event names (``job.progress``) into names accepted by
/// Tauri v2 (``job:progress``).
fn tauri_event_name(name: &str) -> String {
    name.replace('.', ":")
}

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
    child_stdin: Option<Arc<StdMutex<std::process::ChildStdin>>>,
    pending: PendingMap,
    next_id: Arc<AtomicU64>,
    engine_command: String,
    engine_args: Vec<String>,
    engine_working_dir: Option<PathBuf>,
    app_handle: AppHandle,
    process_tree: Option<ProcessTree>,
    last_error: Option<String>,
    connection_lost: Arc<AtomicBool>,
    connection_generation: Arc<AtomicU64>,
}

/// Cheap clone used by Tauri commands after releasing the manager lock.
///
/// Long JSON-RPC waits must not hold ``Mutex<EngineManager>`` because pause,
/// status and shutdown commands need to remain responsive while a request is
/// in flight.
#[derive(Clone)]
pub struct EngineClient {
    child_stdin: Arc<StdMutex<std::process::ChildStdin>>,
    pending: PendingMap,
    next_id: Arc<AtomicU64>,
    connection_lost: Arc<AtomicBool>,
    connection_generation: Arc<AtomicU64>,
    generation: u64,
}

impl EngineManager {
    pub fn new(
        app_handle: AppHandle,
        engine_command: String,
        engine_args: Vec<String>,
        engine_working_dir: Option<PathBuf>,
    ) -> Self {
        Self {
            child: None,
            child_stdin: None,
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: Arc::new(AtomicU64::new(1)),
            engine_command,
            engine_args,
            engine_working_dir,
            app_handle,
            process_tree: None,
            last_error: None,
            connection_lost: Arc::new(AtomicBool::new(false)),
            connection_generation: Arc::new(AtomicU64::new(0)),
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
        #[cfg(target_os = "windows")]
        cmd.creation_flags(CREATE_NO_WINDOW);

        if let Some(working_dir) = self.engine_working_dir.clone() {
            if let Err(error) = std::fs::create_dir_all(&working_dir) {
                let message = format!(
                    "Failed to create engine working directory '{}': {}",
                    working_dir.display(),
                    error
                );
                self.last_error = Some(message.clone());
                return Err(message);
            }
            configure_private_engine_env(&mut cmd, &working_dir, &self.app_handle);
            cmd.current_dir(&working_dir);
        }

        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(error) => {
                let msg = format!(
                    "Failed to spawn engine (cmd='{}', args={:?}): {}",
                    self.engine_command, self.engine_args, error
                );
                self.last_error = Some(msg.clone());
                log::error!("{}", msg);
                return Err(msg);
            }
        };
        self.last_error = None;

        // --- Process tree cleanup via Windows Job Object --------------------
        #[cfg(target_os = "windows")]
        {
            let mut pt = match ProcessTree::new() {
                Ok(value) => value,
                Err(error) => {
                    let message = format!("Failed to create job object: {error}");
                    self.last_error = Some(message.clone());
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(message);
                }
            };
            if let Err(error) = pt.add_process(&child) {
                let message = format!("Failed to assign process to job: {error}");
                self.last_error = Some(message.clone());
                let _ = child.kill();
                let _ = child.wait();
                return Err(message);
            }
            self.process_tree = Some(pt);
        }

        // Take stdio handles. If a handle is unexpectedly missing, terminate
        // the half-started child instead of leaving an orphan background process.
        let child_stdin = match child.stdin.take() {
            Some(value) => value,
            None => {
                let message = "Engine process has no stdin pipe".to_string();
                self.last_error = Some(message.clone());
                let _ = child.kill();
                let _ = child.wait();
                return Err(message);
            }
        };
        let read_stdout = match child.stdout.take() {
            Some(value) => value,
            None => {
                let message = "Engine process has no stdout pipe".to_string();
                self.last_error = Some(message.clone());
                let _ = child.kill();
                let _ = child.wait();
                return Err(message);
            }
        };
        let read_stderr = match child.stderr.take() {
            Some(value) => value,
            None => {
                let message = "Engine process has no stderr pipe".to_string();
                self.last_error = Some(message.clone());
                let _ = child.kill();
                let _ = child.wait();
                return Err(message);
            }
        };
        self.child_stdin = Some(Arc::new(StdMutex::new(child_stdin)));
        let generation = self.connection_generation.fetch_add(1, Ordering::SeqCst) + 1;
        self.connection_lost.store(false, Ordering::SeqCst);

        let pending = self.pending.clone();
        let app_handle = self.app_handle.clone();
        let connection_lost = self.connection_lost.clone();
        let connection_generation = self.connection_generation.clone();

        // --- Background reader: protocol parsing performs blocking pipe I/O.
        // Run it on Tokio's blocking pool so long videos cannot occupy an async
        // scheduler worker while the engine is idle between frames.
        task::spawn_blocking(move || {
            let mut reader = BufReader::new(read_stdout);

            loop {
                match protocol::read_frame(&mut reader) {
                    Ok(body) => match serde_json::from_slice::<Value>(&body) {
                        Ok(msg) => {
                            // Response with an id → route to the waiting caller.
                            if let Some(id_val) = msg.get("id").and_then(|v| v.as_u64()) {
                                let mut map = pending.blocking_lock();
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
                            // Event notification (method without id) → frontend.
                            } else if let Some(event_name) = msg.get("method").and_then(|v| v.as_str()) {
                                let payload = msg.get("params").cloned().unwrap_or(Value::Null);
                                let tauri_event = tauri_event_name(event_name);
                                let _ = app_handle.emit(&tauri_event, payload);
                            }
                        }
                        Err(error) => {
                            log::error!("Failed to parse engine response: {}", error);
                        }
                    },
                    Err(error) => {
                        log::error!("Engine stdout read error: {}", error);
                        break;
                    }
                }
            }

            if connection_generation.load(Ordering::SeqCst) == generation {
                connection_lost.store(true, Ordering::SeqCst);
            }

            // Wake every waiting caller immediately instead of making each one
            // sit through the 300 second timeout after an engine crash.
            let mut map = pending.blocking_lock();
            for (_, tx) in map.drain() {
                let _ = tx.send(Err("Engine connection closed".to_string()));
            }
            drop(map);
            let _ = app_handle.emit("engine:disconnected", serde_json::json!({}));
            log::info!("Engine reader task exited");
        });

        // Always consume stderr. If the pipe fills, Python and any child tools
        // can block even though stdout JSON-RPC remains healthy.
        std::thread::spawn(move || {
            let reader = BufReader::new(read_stderr);
            for line in reader.lines() {
                match line {
                    Ok(text) => log::info!(target: "python_engine", "{}", text),
                    Err(error) => {
                        log::warn!("Engine stderr read error: {}", error);
                        break;
                    }
                }
            }
        });

        self.child = Some(child);
        Ok(())
    }

    /// Create a lightweight RPC client that can outlive the lifecycle lock.
    ///
    /// The subprocess lifecycle remains owned by `EngineManager`; only the
    /// synchronized stdin handle and pending-response registry are shared.
    pub fn client(&self) -> Result<EngineClient, String> {
        let child_stdin = self
            .child_stdin
            .as_ref()
            .cloned()
            .ok_or_else(|| "Engine not started".to_string())?;
        Ok(EngineClient {
            child_stdin,
            pending: self.pending.clone(),
            next_id: self.next_id.clone(),
            connection_lost: self.connection_lost.clone(),
            connection_generation: self.connection_generation.clone(),
            generation: self.connection_generation.load(Ordering::SeqCst),
        })
    }

    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        self.client()?.call(method, params).await
    }

    /// Gracefully shut down the engine.
    pub fn shutdown(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child_stdin.take();
        self.connection_lost.store(true, Ordering::SeqCst);
        self.process_tree.take(); // drops ProcessTree → TerminateJobObject
    }

    /// Record a lifecycle/readiness failure for the status API and UI banner.
    pub fn mark_error(&mut self, message: impl Into<String>) {
        self.last_error = Some(message.into());
    }

    /// Most recent engine startup/runtime failure, safe to expose to the UI.
    pub fn last_error(&self) -> Option<String> {
        self.last_error.clone()
    }

    /// Returns `true` if the engine subprocess is still alive.
    pub fn is_running(&mut self) -> bool {
        if self.connection_lost.load(Ordering::SeqCst) {
            if self.child.is_some() {
                let message = "Engine connection closed".to_string();
                log::warn!("{}", message);
                self.last_error = Some(message);
                self.shutdown();
            }
            return false;
        }

        let Some(child) = self.child.as_mut() else {
            return false;
        };
        match child.try_wait() {
            Ok(None) => true,
            Ok(Some(status)) => {
                let message = format!("Engine exited with status: {}", status);
                log::warn!("{}", message);
                self.last_error = Some(message);
                self.child = None;
                self.child_stdin = None;
                false
            }
            Err(error) => {
                let message = format!("Could not query engine status: {}", error);
                log::warn!("{}", message);
                self.last_error = Some(message);
                false
            }
        }
    }
}

impl EngineClient {
    /// Send a JSON-RPC request without locking the process lifecycle manager.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        if self.connection_lost.load(Ordering::SeqCst) {
            return Err("Engine connection closed".to_string());
        }

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
        let body = serde_json::to_string(&request).map_err(|e| e.to_string())?;
        let write_result = {
            let mut locked = self
                .child_stdin
                .lock()
                .map_err(|_| "Engine stdin lock poisoned".to_string())?;
            protocol::write_frame(&mut *locked, body.as_bytes())
        };
        if let Err(error) = write_result {
            if self.connection_generation.load(Ordering::SeqCst) == self.generation {
                self.connection_lost.store(true, Ordering::SeqCst);
            }
            let mut map = self.pending.lock().await;
            map.remove(&id);
            return Err(format!("Write error: {}", error));
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
/// 2. Development mode (debug build): `python -m src.engine` from project root
/// 3. Bundled sidecar binary (production / release build)
pub fn resolve_engine_path(app_handle: &AppHandle) -> (String, Vec<String>, Option<PathBuf>) {
    // 1. Environment variable override
    if let Ok(path) = std::env::var("VIDEO_NOTES_ENGINE") {
        let path = path.trim().to_string();
        if !path.is_empty() {
            let working_dir = std::env::var("VIDEO_NOTES_ENGINE_CWD")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .map(PathBuf::from);
            log::info!("Using engine from VIDEO_NOTES_ENGINE: {}", path);
            return (path, vec![], working_dir);
        }
    }

    // 2. Development mode. Use `python -m src.engine` from the project
    // root instead of relying on Tauri's current working directory.
    if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let explicit_root = std::env::var("VIDEO_NOTES_PROJECT_ROOT")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(PathBuf::from);
        let current_dir = std::env::current_dir().ok();
        let candidates = [
            explicit_root,
            current_dir.clone(),
            current_dir
                .as_ref()
                .and_then(|cwd| cwd.parent().map(PathBuf::from)),
            Some(manifest_dir.join("..").join("..")),
        ];

        if let Some(project_root) = candidates
            .into_iter()
            .flatten()
            .find(|root| root.join("src").join("engine.py").is_file())
        {
            let python = std::env::var("VIDEO_NOTES_PYTHON")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .unwrap_or_else(|| "python".to_string());
            log::info!(
                "Dev mode: using python module src.engine from {}",
                project_root.display()
            );
            return (
                python,
                vec!["-m".to_string(), "src.engine".to_string()],
                Some(project_root),
            );
        }

        log::warn!("Project root containing src/engine.py was not found");
        return (
            "python".to_string(),
            vec!["-m".to_string(), "src.engine".to_string()],
            None,
        );
    }

    // 3. Production: bundled sidecar.
    log::info!("Production mode: resolving bundled sidecar");
    if let Some((command, args)) = resolve_bundled_sidecar(app_handle) {
        // The Python engine intentionally uses relative paths such as
        // `./output`. A Windows shortcut does not guarantee a writable current
        // directory, so inheriting the desktop process CWD can make the
        // sidecar exit before its first RPC request. Always launch production
        // engines from a stable per-user data directory.
        let working_dir = production_engine_working_dir(app_handle);
        return (command, args, Some(working_dir));
    }

    // Developer-friendly release fallback: when a release executable is run
    // directly from target/release inside the source checkout, use the local
    // Python module rather than silently exiting because the sidecar was not
    // prepared. Installed builds still require the bundled sidecar.
    if let Some(project_root) = find_project_root_near_executable() {
        let python = std::env::var("VIDEO_NOTES_PYTHON")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| "python".to_string());
        log::warn!(
            "Bundled sidecar not found; using source checkout at {}",
            project_root.display()
        );
        return (
            python,
            vec!["-m".to_string(), "src.engine".to_string()],
            Some(project_root),
        );
    }

    log::warn!("Bundled sidecar not found; engine startup will report a visible error");
    ("python-engine".to_string(), vec![], None)
}




/// Stable writable working directory for the bundled production engine.
///
/// Keep runtime output outside the installation directory so both per-user and
/// system-wide installations behave the same way.
fn production_engine_working_dir(app_handle: &AppHandle) -> PathBuf {
    if let Some(base) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(base).join("Video Notes AI");
    }

    app_handle
        .path()
        .app_local_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("Video Notes AI"))
}

fn configure_private_engine_env(
    cmd: &mut Command,
    working_dir: &PathBuf,
    app_handle: &AppHandle,
) {
    let data_dir = working_dir;
    let state_dir = data_dir.join("state");
    cmd.env("VIDEO_NOTES_DATA_DIR", data_dir)
        .env("VIDEO_NOTES_STATE_DIR", &state_dir)
        .env("VIDEO_NOTES_JOBS_DIR", data_dir.join("jobs"))
        .env("VIDEO_NOTES_SETTINGS_PATH", state_dir.join("settings.json"))
        .env("VIDEO_NOTES_DEFAULT_OUTPUT_DIR", default_export_dir(app_handle));
}

fn default_export_dir(app_handle: &AppHandle) -> PathBuf {
    if let Ok(user_profile) = std::env::var("USERPROFILE") {
        let trimmed = user_profile.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed)
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

/// Locate a source checkout when a release executable is launched directly
/// from `desktop/src-tauri/target/release` during development.
fn find_project_root_near_executable() -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(root) = std::env::var("VIDEO_NOTES_PROJECT_ROOT") {
        if !root.trim().is_empty() {
            candidates.push(PathBuf::from(root));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut current = exe.parent().map(PathBuf::from);
        for _ in 0..8 {
            let Some(path) = current else { break };
            candidates.push(path.clone());
            current = path.parent().map(PathBuf::from);
        }
    }
    candidates
        .into_iter()
        .find(|root| root.join("src").join("engine.py").is_file())
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
