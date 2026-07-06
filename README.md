# Video Notes AI

Windows desktop app for turning local videos or online videos into structured Markdown notes.

Current desktop architecture is **Tauri 2 + Svelte 5 + Rust native engine**. The installer does **not** bundle or start a Python sidecar.

## Current Product Shape

```text
Video / URL
  -> yt-dlp.exe for online media
  -> FFmpeg / FFprobe native executables
  -> whisper.cpp native CLI transcription
  -> optional Tesseract native OCR
  -> optional OpenAI-compatible vision / note generation
  -> Markdown notes and artifacts in the configured vault/export directory
```

## Runtime Components

Required native component manifests:

- `download-tools`: `yt-dlp.exe`
- `ffmpeg-tools`: `ffmpeg.exe`, `ffprobe.exe`
- `whisper-cpp-tools`: `whisper-cli.exe` and whisper.cpp DLLs
- `tesseract-ocr-tools`: `tesseract.exe`, `tessdata/`

Removed from the desktop release path:

- Python sidecar binary
- bundled Python runtime component
- faster-whisper Python component
- PaddleOCR / PaddlePaddle Python component

The repository still contains historical Python engine/CLI code, but the desktop app and installer use the Rust native engine.

## Important Paths

- Settings: `%APPDATA%\Video Notes AI\settings.json`
- Runtime components: `%LOCALAPPDATA%\Video Notes AI\runtime\components`
- Runtime packages/cache: `%LOCALAPPDATA%\Video Notes AI\runtime\packages`
- Jobs/state: `%LOCALAPPDATA%\Video Notes AI\.jobs`
- Default exports: `Documents\Video Notes AI\exports`

## Development

```powershell
cd desktop
npm install
npm run tauri dev
```

## Build

```powershell
cd desktop
npm run build
npm run tauri build
```

Installer output:

```text
desktop/src-tauri/target/release/bundle/nsis/
```

## Release Checks

```powershell
python scripts/verify_release_gate.py
python -m pytest tests/test_release_gate.py tests/test_installed_runtime_verifier.py -q
```

`scripts/build_windows_release.ps1` runs the native desktop build. It does not prepare or bundle `python-engine`.
