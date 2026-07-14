//! Video Notes AI — Tauri Desktop Application
//!
//! Rust/Tauri shell for Video Notes AI.
//! Provides the native engine bridge.

#![deny(unsafe_code)]

pub mod compile;
#[cfg(feature = "compiler_v3")]
pub mod compile_v3;
pub mod native_engine;
pub mod study;

/// Post-compile hook: persists a v0.2 exchange bundle alongside the legacy capsule.
/// Call this after a successful compile to save the v0.2 format.
/// This function is a no-op when `compiler_v3` feature is disabled.
#[cfg(feature = "compiler_v3")]
pub fn persist_v02_after_compile(
    capsule: &compile::VideoCapsule,
    source_hash: &str,
    version: u32,
    storage_dir: &std::path::Path,
) -> Result<(), String> {
    use compile_v3::convert;
    use compile_v3::validate::write_bundle;
    use std::fs;

    let v02_bundle = convert(capsule);
    let bytes = write_bundle(&v02_bundle).map_err(|e| format!("v0.2 write_bundle failed: {e}"))?;

    let capsule_dir = storage_dir.join(source_hash);
    fs::create_dir_all(&capsule_dir).map_err(|e| format!("failed to create v0.2 dir: {e}"))?;
    let v02_path = capsule_dir.join(format!("v{version}.v02.json"));
    fs::write(&v02_path, &bytes).map_err(|e| format!("failed to write v0.2 bundle: {e}"))?;
    Ok(())
}

#[cfg(not(feature = "compiler_v3"))]
pub fn persist_v02_after_compile(
    _capsule: &compile::VideoCapsule,
    _source_hash: &str,
    _version: u32,
    _storage_dir: &std::path::Path,
) -> Result<(), String> {
    Ok(())
}

/// Application state for v0.2 bundle operations.
#[cfg(feature = "compiler_v3")]
pub struct AppState {
    pub storage_dir: std::path::PathBuf,
}
