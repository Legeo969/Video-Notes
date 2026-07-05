fn main() {
    // tauri_build handles codegen, manifests, and Windows resources.
    // During development (`cargo check` / `cargo build` in debug mode)
    // we still run the build but the .ico file must be present.
    tauri_build::build()
}
