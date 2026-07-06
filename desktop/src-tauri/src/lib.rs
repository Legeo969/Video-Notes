//! Video Notes AI — Tauri Desktop Application
//!
//! Rust/Tauri shell for Video Notes AI.
//! Provides the native engine bridge and the transitional Python fallback.

pub mod engine_manager;
pub mod native_engine;
pub mod process_tree;
pub mod protocol;
