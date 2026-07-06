# video-notes-ai Project Guidelines

## 0. 沟通要求

- 默认中文解释；代码、技术术语、学术引用使用精准英文
- 直接给出结果和答案，仅在必要处加注释
- 省略客套和填充语
- 精准优于友好

## 1. Current Architecture

Windows desktop app for converting videos into structured Markdown notes.

Current desktop product:

- Tauri 2 + Svelte 5 frontend
- Rust native engine in `desktop/src-tauri/src/native_engine.rs`
- No Python sidecar in the installer
- No bundled Python runtime component
- Native tools as runtime components:
  - `yt-dlp.exe`
  - `ffmpeg.exe` / `ffprobe.exe`
  - `whisper-cli.exe`
  - `tesseract.exe`

Historical Python engine/CLI code remains in `src/` and `main.py`, but it is not the desktop runtime boundary.

## 2. Runtime Paths

- Persistent settings: `%APPDATA%\Video Notes AI\settings.json`
- Local runtime: `%LOCALAPPDATA%\Video Notes AI\runtime`
- Jobs/state: `%LOCALAPPDATA%\Video Notes AI\.jobs`
- Default exports: `Documents\Video Notes AI\exports`

## 3. Engineering Rules

- Do not reintroduce `python-engine`, `engine_manager.rs`, `process_tree.rs`, or sidecar JSON-RPC into the desktop release path.
- Do not add Python runtime components to required desktop manifests.
- Prefer native executable integrations for download, media, transcription, and OCR.
- Keep changes surgical and tied to the requested behavior.
- Use existing Svelte/Tauri/Rust patterns before adding abstractions.

## 4. Commands

```powershell
cd desktop
npm run tauri dev
```

```powershell
cd desktop
npm run build
npm run tauri build
```

```powershell
python scripts/verify_release_gate.py
python -m pytest tests/test_release_gate.py tests/test_installed_runtime_verifier.py -q
```
