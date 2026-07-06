# Video Notes AI Final Architecture v3

Status: updated for v1.5 native desktop runtime.

## 1. Product Boundary

The Windows desktop product is a Tauri 2 application with a Rust native engine. It does not bundle Python, start a Python sidecar, or communicate with an engine over stdin/stdout JSON-RPC.

Historical Python engine and CLI code may remain in the repository for reference and tests, but it is outside the desktop installer runtime path.

## 2. Runtime Flow

```text
Svelte UI
  -> Tauri invoke
  -> Rust NativeEngine
  -> local settings / runtime component manager / job state
  -> native executables:
       yt-dlp.exe
       ffmpeg.exe / ffprobe.exe
       whisper-cli.exe
       tesseract.exe
  -> Markdown notes, transcript, frames, and artifacts
```

## 3. Required Native Components

Runtime manifests live in `runtime/manifests`.

Required components:

- `download-tools`
- `ffmpeg-tools`
- `whisper-cpp-tools`
- `tesseract-ocr-tools`

These components are native executable payloads. They must not require `base-engine`, Python DLLs, `site-packages`, faster-whisper, PaddleOCR, or PaddlePaddle.

`scripts/prepare_runtime_payload_sources.ps1` prepares native payload source directories and writes `payload-source-map.json`. `scripts/stage_runtime_payloads.py` can then copy those sources into `runtime/packages` for release packaging.

## 4. Storage Layout

Settings are persistent across reinstall:

```text
%APPDATA%\Video Notes AI\settings.json
```

Runtime state is user-local and removable:

```text
%LOCALAPPDATA%\Video Notes AI\.jobs
%LOCALAPPDATA%\Video Notes AI\runtime
```

Default exports:

```text
Documents\Video Notes AI\exports
```

When an Obsidian vault is configured, generated Markdown notes are written to the configured vault path. Export/cache directories are not automatically deleted unless a cleanup action explicitly targets them.

## 5. Release Gate

The release gate proves:

- Tauri bundling is active and targets NSIS/MSI.
- `bundle.externalBin` does not include `python-engine`.
- Rust `NativeEngine` provides system, settings, task, notes, collections, components, and doctor APIs.
- Windows release build runs `npm ci`, frontend build, and `npm run tauri build`.
- Required native component manifests exist and match the product version.
- Runtime installation does not execute package-manager installs.

## 6. Removed Release Requirements

The following are no longer release requirements:

- `desktop/src-tauri/src/engine_manager.rs`
- `desktop/src-tauri/src/process_tree.rs`
- `desktop/src-tauri/src/protocol.rs`
- `scripts/prepare_tauri_sidecar.ps1`
- `scripts/compute_sidecar_fingerprint.py`
- bundled `python-engine`
- Windows Job Object cleanup for a Python process tree
- Content-Length framed JSON-RPC sidecar smoke tests

## 7. OCR Strategy

OCR in the desktop product is native Tesseract. PaddleOCR/PaddlePaddle is intentionally not part of the current desktop runtime because it requires a Python stack.

## 8. Transcription Strategy

Transcription in the desktop product is whisper.cpp native CLI. faster-whisper/CTranslate2 Python components are intentionally not part of the current desktop runtime.
