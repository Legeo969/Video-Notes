# Engine working-directory hotfix — v1.2.5

## Symptom

The Tauri shell opened, but the bundled Python engine exited immediately with
`exit code: 0xffffffff`. The same sidecar passed `engine.hello` and
`process.list` when launched manually.

## Root cause

Production launches inherited an unspecified Windows current working directory,
while the Python engine uses relative runtime paths such as `./output`. A
shortcut or shell launch can therefore place runtime files in a non-writable or
unexpected directory.

## Fix

- Launch the bundled engine from `%LOCALAPPDATA%\Video Notes AI\engine-runtime`.
- Create that directory before spawning the process.
- Log the resolved working directory.
- Require a successful `system.info` RPC before reporting the engine as ready.
