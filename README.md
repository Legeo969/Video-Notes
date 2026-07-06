# Video Notes AI

Windows desktop app for turning local videos or online videos into structured Markdown notes.

Current architecture: **Tauri 2 + Svelte 5 + Rust native engine**.

## Product Flow

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

## Verification

```powershell
.\scripts\verify_product.ps1
```

## Build

```powershell
.\scripts\build_windows_release.ps1
```

Installer output:

```text
desktop/src-tauri/target/release/bundle/nsis/
```
