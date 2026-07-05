//! Startup diagnostics for release builds.
//!
//! Tauri release binaries use the Windows GUI subsystem, so panics and early
//! startup errors are otherwise invisible when the user double-clicks the app.
//! This module writes a small persistent log and shows a native error dialog
//! for fatal shell failures.

use chrono::Local;
use std::fs::{create_dir_all, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

pub fn log_path() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("Video Notes AI").join("logs").join("desktop-startup.log")
}

pub fn append(message: impl AsRef<str>) {
    let path = log_path();
    if let Some(parent) = path.parent() {
        let _ = create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(
            file,
            "{}  {}",
            Local::now().format("%Y-%m-%d %H:%M:%S%.3f"),
            message.as_ref()
        );
    }
}

pub fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let message = format!("Unhandled desktop panic: {info}");
        append(&message);
        previous(info);
    }));
}

#[cfg(target_os = "windows")]
pub fn show_fatal_error(title: &str, message: &str) {
    use std::ffi::c_void;
    use std::iter;
    use std::os::windows::ffi::OsStrExt;

    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(
            hwnd: *mut c_void,
            text: *const u16,
            caption: *const u16,
            kind: u32,
        ) -> i32;
    }

    const MB_OK: u32 = 0x0000_0000;
    const MB_ICONERROR: u32 = 0x0000_0010;
    let text: Vec<u16> = std::ffi::OsStr::new(message)
        .encode_wide()
        .chain(iter::once(0))
        .collect();
    let caption: Vec<u16> = std::ffi::OsStr::new(title)
        .encode_wide()
        .chain(iter::once(0))
        .collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            caption.as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}

#[cfg(not(target_os = "windows"))]
pub fn show_fatal_error(title: &str, message: &str) {
    eprintln!("{title}: {message}");
}
