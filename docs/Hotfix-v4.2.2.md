# Video Notes AI v4.2.2 hotfix

## Fixed

- Removed the invalid Tauri v2 shell plugin configuration `plugins.shell.sidecar`.
- Kept the Python sidecar under `bundle.externalBin`, which is the supported Tauri v2 packaging configuration.
- Switched the default Windows bundle target to NSIS to avoid the unrelated WiX `light.exe` failure on this machine.
- Bumped the desktop application version to 1.2.3.

## Rebuild

```powershell
cd D:\AiWork\Video-Notes-main
.\scripts\build_windows_release.ps1 -ReuseSidecar
```
