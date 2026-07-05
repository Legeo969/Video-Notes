# UI v7 Online Video Hotfix

This patch keeps app version 1.5.0 and fixes the online-video task path.

## Fixed

- Validates public video URLs before creating a task.
- Adds Bilibili-oriented guidance for complete `/video/BV...` links.
- Adds an optional cookie file / raw cookie field in Settings → General & Transcription.
- Passes the cookie setting into the Python pipeline without storing it in task snapshots.
- Passes cookies to yt-dlp for both audio-only and video download paths.
- Replaces opaque yt-dlp exceptions with actionable errors for login/cookie/403/412/unsupported URL failures.

## Build note

Python code changed. Rebuild without `-ReuseSidecar` once.
