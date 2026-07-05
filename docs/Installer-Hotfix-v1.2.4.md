# Installer hotfix v1.2.4

## Symptom

NSIS/makensis fails with:

```text
File: failed creating mmap of ...python-engine-x86_64-pc-windows-msvc.exe
```

## Root cause

The sidecar was built from the machine-wide Python environment. PyInstaller
therefore discovered unrelated developer packages such as Torch, Paddle,
ModelScope, datasets and their native libraries. The resulting one-file
sidecar grew into the multi-gigabyte range and exceeded the practical mmap /
2 GiB limits of NSIS.

## Fix

- Build the sidecar in `.build/sidecar/venv` instead of global Python.
- Install only `requirements/sidecar.txt` plus PyInstaller.
- Keep PaddleOCR/Torch/ModelScope as optional runtimes rather than embedding
  them in the core desktop installer.
- Add a 1700 MiB preflight limit before Tauri invokes NSIS.
- When `-ReuseSidecar` points to an oversized legacy binary, rebuild it
  automatically instead of failing at the end of the installer process.

## Required command after applying the hotfix

Run once without reusing the old sidecar:

```powershell
cd D:\AiWork\Video-Notes-main
.\scripts\build_windows_release.ps1
```

Later code-only rebuilds may use:

```powershell
.\scripts\build_windows_release.ps1 -ReuseSidecar
```
