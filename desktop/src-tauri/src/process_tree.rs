//! Windows Job Object management for process tree cleanup.
//!
//! On Windows, creates a Job Object and assigns child processes to it.
//! When the job handle is closed (on drop), all associated processes are
//! terminated via `TerminateJobObject`. On non-Windows platforms this is a no-op.

use std::io;
use std::process::Child;

// ---------------------------------------------------------------------------
// Windows implementation: raw Win32 FFI for Job Objects
// ---------------------------------------------------------------------------
#[cfg(target_os = "windows")]
mod ffi {
    use std::ffi::c_void;
    use std::io;
    use std::ptr;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(
            lpJobAttributes: *mut c_void,
            lpName: *const u16,
        ) -> *mut c_void;

        fn AssignProcessToJobObject(
            hJob: *mut c_void,
            hProcess: *mut c_void,
        ) -> i32;

        fn TerminateJobObject(
            hJob: *mut c_void,
            uExitCode: u32,
        ) -> i32;

        fn CloseHandle(
            hObject: *mut c_void,
        ) -> i32;
    }

    pub fn create_job_object() -> io::Result<*mut c_void> {
        let handle = unsafe { CreateJobObjectW(ptr::null_mut(), ptr::null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error());
        }
        Ok(handle)
    }

    pub fn assign_process(job: *mut c_void, process: *mut c_void) -> io::Result<()> {
        let ret = unsafe { AssignProcessToJobObject(job, process) };
        if ret == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn terminate_job(job: *mut c_void) {
        if !job.is_null() {
            unsafe { TerminateJobObject(job, 1); }
        }
    }

    pub fn close_handle(handle: *mut c_void) {
        if !handle.is_null() {
            unsafe { CloseHandle(handle); }
        }
    }
}

#[cfg(target_os = "windows")]
pub struct ProcessTree {
    handle: *mut std::ffi::c_void,
}

// Raw pointer is safe to Send/Sync — it is an owned Windows HANDLE
// that can be transferred between threads and does not alias.
#[cfg(target_os = "windows")]
unsafe impl Send for ProcessTree {}
#[cfg(target_os = "windows")]
unsafe impl Sync for ProcessTree {}

#[cfg(target_os = "windows")]
impl ProcessTree {
    /// Create a new unnamed Job Object.
    pub fn new() -> io::Result<Self> {
        let handle = ffi::create_job_object()?;
        Ok(Self { handle })
    }

    /// Assign a child process (and all its future children) to this job.
    ///
    /// On Windows, child processes created by `child` are automatically
    /// added to the same job object, ensuring the entire process tree is
    /// managed together.
    pub fn add_process(&mut self, child: &Child) -> io::Result<()> {
        use std::os::windows::io::AsRawHandle;
        let process_handle = child.as_raw_handle() as *mut std::ffi::c_void;
        ffi::assign_process(self.handle, process_handle)
    }
}

#[cfg(target_os = "windows")]
impl Drop for ProcessTree {
    fn drop(&mut self) {
        // Terminate all processes in the job, then close the handle.
        // This kills the engine + all its children (FFmpeg, OCR, etc.)
        ffi::terminate_job(self.handle);
        ffi::close_handle(self.handle);
    }
}

// ---------------------------------------------------------------------------
// Non-Windows stub (no-op)
// ---------------------------------------------------------------------------
#[cfg(not(target_os = "windows"))]
pub struct ProcessTree;

#[cfg(not(target_os = "windows"))]
impl ProcessTree {
    pub fn new() -> io::Result<Self> {
        Ok(Self)
    }

    pub fn add_process(&mut self, _child: &Child) -> io::Result<()> {
        Ok(())
    }
}
