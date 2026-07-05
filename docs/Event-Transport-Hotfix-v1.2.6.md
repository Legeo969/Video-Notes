# Event transport hotfix — v1.2.6

Tauri v2 event names may contain alphanumeric characters and `-`, `/`, `:`, `_`, but not `.`.
The Python engine intentionally keeps logical JSON-RPC notification names such as `job.progress`.
At the Tauri boundary these are translated to `job:progress`; the frontend adapter performs the same translation before subscribing.

The Windows launcher also applies `CREATE_NO_WINDOW` to the Python sidecar process so release builds do not show an extra console while preserving stdin/stdout/stderr pipes.
