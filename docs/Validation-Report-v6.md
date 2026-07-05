# UI v6 Validation Report

## Frontend

```text
npm run build
136 modules transformed
production build successful
```

```text
svelte-check
0 errors
0 warnings
```

## Python regression

The non-Qt suite was executed in the Linux artifact environment:

```text
626 passed
61 skipped
6 xfailed
2 legacy GUI tests failed
```

The two failures reference the removed pre-Tauri `src.gui` package and are unrelated to UI v6. `tests/test_collection_delete.py` could not be collected because this environment does not include the full PySide6 Qt package. Windows release verification remains the authoritative desktop build gate.

## Windows build compatibility

`build_windows_release.ps1` now detects a lockfile referencing `cc 1.2.75` and normalizes it to `1.2.65`, matching the registry mirror available on the user’s Windows environment before invoking Tauri.

## Required final gate

On Windows:

```powershell
.\scripts\build_windows_release.ps1 -ReuseSidecar
```

Expected installer:

```text
desktop\src-tauri\target\release\bundle\nsis\Video Notes AI_1.4.0_x64-setup.exe
```
