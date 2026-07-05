# Video Notes AI v4 变更清单

- 修改文件：39
- 新增文件：19
- 删除文件：0

## 修改

- `CHANGELOG.md`
- `README.md`
- `desktop/src-tauri/src/engine_manager.rs`
- `desktop/src-tauri/src/main.rs`
- `desktop/src-tauri/src/protocol.rs`
- `desktop/src/App.svelte`
- `desktop/src/lib/api/index.ts`
- `desktop/src/lib/api/mockTauri.ts`
- `desktop/src/lib/components/Sidebar.svelte`
- `desktop/src/lib/types/index.ts`
- `desktop/src/pages/Notes.svelte`
- `desktop/src/pages/Process.svelte`
- `desktop/src/pages/Settings.svelte`
- `desktop/src/pages/Tasks.svelte`
- `desktop/tsconfig.json`
- `main.py`
- `plugins/example/plugin.py`
- `src/api/dto/jobs.py`
- `src/api/event_journal.py`
- `src/api/handlers/diagnostics.py`
- `src/api/handlers/process.py`
- `src/api/handlers/settings.py`
- `src/api/protocol/framing.py`
- `src/api/server.py`
- `src/application/llm/reduce_stage.py`
- `src/application/notes/template_loader.py`
- `src/application/pipeline/context.py`
- `src/application/pipeline/stages/extract_frames_stage.py`
- `src/application/pipeline/stages/map_notes.py`
- `src/application/providers/factory.py`
- `src/application/services/cleanup_manager.py`
- `src/application/services/job_queue.py`
- `src/application/services/media_resolver.py`
- `src/application/services/orchestrator.py`
- `src/db/database.py`
- `src/domain/job_state.py`
- `src/infrastructure/db/processing_metadata.py`
- `src/infrastructure/db/repositories/job_repository.py`
- `src/infrastructure/video/frame_extractor.py`

## 新增

- `desktop/src/lib/stores/jobs.ts`
- `docs/Change-Manifest-v4.md`
- `docs/Product-Architecture-v4.md`
- `docs/Validation-Report-v4.md`
- `scripts/verify_product.ps1`
- `scripts/verify_product.sh`
- `src/application/notes/templates/coding_tutorial.yaml`
- `src/application/notes/templates/default.yaml`
- `src/application/notes/templates/interview.yaml`
- `src/application/notes/templates/lecture.yaml`
- `src/application/notes/templates/meeting.yaml`
- `src/application/notes/templates/product_demo.yaml`
- `src/application/notes/templates/research.yaml`
- `src/application/notes/templates/study.yaml`
- `src/application/services/request_snapshot.py`
- `src/application/services/task_supervisor.py`
- `tests/test_api_settings_contract.py`
- `tests/test_engine_sidecar_boot.py`
- `tests/test_v14_task_runtime.py`

## 删除


> 本清单忽略 `.pytest_cache`、`__pycache__`、`node_modules`、`dist` 和测试生成的 `output`。

## v4.1 Windows verification hotfix

- Centralized settings-path resolution and added `VIDEO_NOTES_SETTINGS_PATH`.
- Prevented Windows pytest runs from reading or mutating real provider profiles.
- Made missing Cargo a hard release-gate failure unless explicitly skipped.
- Added `docs/Hotfix-v4.1.md`.
