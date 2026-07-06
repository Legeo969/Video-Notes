# video-notes-ai Project Guidelines

## 0. 沟通要求

- 默认中文解释；代码、技术术语、学术引用使用精准英文
- 直接给出结果和答案，仅在必要处加注释
- 省略客套和填充语
- 精准优于友好

## 1. Current Architecture

Windows desktop app for converting videos into structured Markdown notes.

Current product:

- Tauri 2 + Svelte 5 frontend
- Rust native engine in `desktop/src-tauri/src/native_engine.rs`
- Native tools as runtime components:
  - `yt-dlp.exe`
  - `ffmpeg.exe` / `ffprobe.exe`
  - `whisper-cli.exe`
  - `tesseract.exe`

## 2. Runtime Paths

- Persistent settings: `%APPDATA%\Video Notes AI\settings.json`
- Local runtime: `%LOCALAPPDATA%\Video Notes AI\runtime`
- Jobs/state: `%LOCALAPPDATA%\Video Notes AI\.jobs`
- Default exports: `Documents\Video Notes AI\exports`

## 3. Engineering Rules

- Keep the desktop runtime native-only.
- Prefer standalone executable integrations for download, media, transcription, and OCR.
- Keep changes surgical and tied to the requested behavior.
- Use existing Svelte/Tauri/Rust patterns before adding abstractions.

## 4. Commands

```powershell
cd desktop
npm run tauri dev
```

```powershell
.\scripts\verify_product.ps1
```

```powershell
.\scripts\build_windows_release.ps1
```
