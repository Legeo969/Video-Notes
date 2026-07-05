# Model interaction hotfix (version remains 1.5.0)

## Problems fixed

1. The settings page displayed scanned model names as passive text instead of selectable controls.
2. The task creation page used a hard-coded five-model list, independent of the local scan result.
3. Directory names such as `faster-whisper-large-v3-turbo` were passed to the pipeline unchanged, causing the runtime to search for `faster-whisper-faster-whisper-large-v3-turbo`.
4. The scan compatibility endpoint could include the configured default even when it was not installed, making the UI report a false local model.
5. The media source section duplicated file selection and a generic path/link input, making the intended interaction unclear.

## New behavior

- `settings.models.local` returns only models that the runtime can actually resolve, with normalized model IDs, paths, and source directories.
- The runtime now accepts both `faster-whisper-medium/` and direct `medium/` model directories.
- Model directory names are normalized to pipeline IDs, e.g.:
  - `faster-whisper-tiny` -> `tiny`
  - `faster-whisper-large-v3-turbo` -> `large-v3-turbo`
  - direct folder `medium` (with model files) -> `medium`
- Settings automatically reads the local catalog. Scanned models are actual selectable cards with installed/uninstalled states.
- Saving is blocked when the chosen default is not locally available.
- Task creation reads the same catalog and presents an explicit dropdown for the current task.
- The Start button remains disabled until both a media source and a real local model are selected.
- The media source now uses two explicit modes: Local file and Public video link.

## Build note

This hotfix changes Python API handlers. Rebuild the Python sidecar and do not use `-ReuseSidecar` for the first build after applying it.
