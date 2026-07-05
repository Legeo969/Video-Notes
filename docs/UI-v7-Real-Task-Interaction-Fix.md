# UI v7 Real Task Interaction Fix

This hotfix keeps the application version at 1.5.0 and fixes the interaction problems found in the real Windows UI review.

## Fixed

- The Settings → General → Content Enhancement area is now a real two-option control group:
  - OCR text recognition
  - Vision understanding
- Both enhancement switches are clickable cards, not tiny hidden checkbox targets.
- `vision_enabled` is now loaded, saved, and reused by the task creation page.
- The task creation page now shows the actual AI provider used by a real task and disables vision understanding when no active AI provider exists.
- The task creation page now shows a real preflight note for the full runtime chain: media → Whisper → optional OCR/vision → AI note generation.
- Provider configuration now uses API types instead of vendor nicknames:
  - OpenAI Compatible
  - Google Gemini
  - Anthropic Messages
  - OpenAI Responses
  - ChatGPT Codex (Plus/Pro)
- Google Gemini, Anthropic Messages and OpenAI Responses have native text-generation adapters for real note generation.
- ChatGPT Codex (Plus/Pro) is shown as a non-automatic endpoint because it is not a normal API-key service provider.
- Model discovery now handles OpenAI-compatible `/models`, Gemini `/models?key=...`, and Anthropic `/models` style responses.

## Important build note

This patch changes Python backend provider code, so the Windows sidecar must be rebuilt once. Do not use `-ReuseSidecar` for the first build after applying this patch.

```powershell
cd D:\AiWork\Video-Notes-main

Get-Process video-notes-ai, python-engine -ErrorAction SilentlyContinue | Stop-Process -Force

Remove-Item ".\desktop\src-tauri\binaries\python-engine-x86_64-pc-windows-msvc.exe" -Force -ErrorAction SilentlyContinue
Remove-Item ".\desktop\src-tauri\target\release\bundle" -Recurse -Force -ErrorAction SilentlyContinue

.\scripts\build_windows_release.ps1
```

After that first rebuild, later UI-only changes can reuse the sidecar again.

## Verification performed in this environment

- `npm run build`: passed
- `npx svelte-check --tsconfig ./tsconfig.json`: 0 errors, 0 warnings
- Python compile check for modified backend files: passed
- Settings/provider regression subset: 53 passed
