# Video Notes AI Final Architecture v3

Status: native-only desktop runtime.

## 1. Product Boundary

The Windows desktop product is a Tauri 2 application with a Rust native engine.

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

These components are standalone executable payloads.

`scripts/prepare_runtime_payload_sources.ps1` prepares native payload source directories and can stage them into `runtime/packages`.

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

## 5. Verification

Product verification is:

```powershell
.\scripts\verify_product.ps1
```

Release build is:

```powershell
.\scripts\build_windows_release.ps1
```

## 6. OCR Strategy

OCR uses Tesseract native executable tooling.

## 7. Transcription Strategy

Transcription uses whisper.cpp native CLI tooling.
