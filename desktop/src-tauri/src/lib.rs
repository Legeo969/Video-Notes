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
    use compile_v3::{BundleStore, FileBundleStore};

    if capsule.source_hash != source_hash || capsule.version != version {
        return Err("v0.2 persistence identity does not match the legacy capsule".to_string());
    }

    let v02_bundle = convert(capsule)?;
    let mut store = FileBundleStore::new(storage_dir.to_path_buf());
    store.insert(&v02_bundle)?;
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
