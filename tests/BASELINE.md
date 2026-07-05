# 测试基线文档 (V1.0.0-rc.2)

最后更新：2026-06-27

## 测试结果

```text
849 passed / 93 skipped / 9 xfailed / 0 FAILED
Core: 437+ passed
```

> Phase 7 清理了 12 个 strict xfail 测试（旧 `_save_transcript_and_notes` API 的死代码），替换为 ArtifactWriter / CleanupManager 目标测试。
> 新增 10 个 root wrapper 的 deprecation warning。

## 测试分层策略

```
core:     确定性核心测试 — 必须全绿
legacy:   旧架构兼容测试 — xfail 标注，strict=False
external: 外部依赖测试 — 默认 skip（需 RUN_EXTERNAL_TESTS=1）
slow:     长时间运行测试 — 默认 skip
gpu:      需要 CUDA — 默认 skip（需 RUN_GPU_TESTS=1）
ocr:      需要 OCR — 默认 skip（需 RUN_OCR_TESTS=1）
```

## 当前期望失败列表

以下测试因架构演进原因预期失败，已标记 `@pytest.mark.xfail`。

### legacy_arch: 旧 pipeline 函数不存在

| 测试 | 原因 |
|------|------|
| `test_knowledge_model_wiring::test_save_notes_uses_kb_provider_for_blocks_and_embeddings` | `video_pipeline` 已服务化，不再导出 `_save_transcript_and_notes` |

### legacy_arch: 默认行为变化

| 测试 | 原因 |
|------|------|
| `test_frame_extractor::test_extract_frames_no_scene_detected` | 默认 mode 已从 `auto` 改为 `fixed` |
| `test_frame_extractor::test_extract_frames_with_scene_changes` | 同上 |
| `test_frame_extractor::test_fallback_when_too_few_smart_frames` | 默认 `max_frames=30` 覆盖旧 `None` 断言 |
| `test_pipeline_compat::test_field_count_not_exploded` | `PipelineRequest` 新增 `collection_id` 字段（6→7） |
| `test_smart_summary::test_default_behavior_preserved` | 默认进入 V0.5 模板模式，输出不含旧标题 |
| `test_smart_summary::test_global_summary_with_multi_chunk` | 同上 |

### legacy_arch: 旧 pipeline 函数不存在（V0.3.1 已服务化）

以下测试因架构演进原因预期失败。**注意**：部分旧 xpassed 测试已在 V0.8.1 中移入 core（它们测试的是现有模块如 `vault_writer`、`crossref`、`note_generator`、`prompts`，不受 pipeline 服务化影响）。

| 测试 | 原因 |
|------|------|
| `test_knowledge_model_wiring::test_save_notes_uses_kb_provider_for_blocks_and_embeddings` | `video_pipeline` 已服务化，不再导出 `generate_notes` 等旧属性 |

> **V1.0.0-rc.2 Phase 7**：删除了 11 个重复的 strict=True xfail 条目（旧 `_save_transcript_and_notes` 测试的死代码）。这些测试的行为已由 ArtifactWriter / CleanupManager 目标测试覆盖。

### legacy_arch: 接口签名变化

| 测试 | 原因 |
|------|------|
| `test_note_generation_performance::test_generate_notes_passes_timeout_and_retry_limit_to_provider` | 重试逻辑在 `_call_with_retry` 层，不透传到 provider |
| `test_sqlite_index_embedding::test_update_entry_uses_injected_embedding_provider` | `SQLiteNoteIndex` 内部 provider 调用路径变化 |

### env_dep: 环境依赖（Git Bash / CI 沙箱）

| 测试 | 原因 |
|------|------|
| `test_embedding_engine` 全部 6 个 | `Path.home()` 在部分 CI 环境不可用 |
| `test_search_mode::test_hybrid_mode_fuses_results` | 同上 |
| `test_gui_settings_flow` 全部 2 个目标 | PySide6 导入依赖 `PATH` 环境变量 / Qt 运行时 |
| `test_smart_summary` GUI 相关 4 个 | 同上 |
| `test_pipeline_compat::test_runtime_capabilities_detectable` | 同上 |
| `test_architecture_smoke::test_bilibili_cookie_env_path_is_preferred` | `patch.dict` 恢复时环境变量超 32K 字符限制 |

### env_dep: typing_extensions 缺失（CI 沙箱）

| 测试 | 原因 |
|------|------|
| `test_architecture_smoke::test_cli_check_ocr_reports_runtime_versions` | 子进程 main.py 导入链缺 `typing_extensions` |
| `test_architecture_smoke::test_cli_help_still_exposes_existing_options` | 同上 |
| `test_architecture_smoke::test_rebuild_index_cli_handles_gbk_console_encoding` | 同上 |
| `test_smart_summary::test_cli_help_shows_smart_summary` | 同上 |

### external: 需要 ffmpeg

| 测试 | 原因 |
|------|------|
| `test_frame_extractor::test_auto_fallback_respects_max_frames` | `_extract_frame_at_time` 调用 ffmpeg 子进程 |
| `test_frame_extractor::test_extract_frames_creates_output_dir` | 同上 |

## 如何启用外部测试

```bash
# 全量（需 ffmpeg + 网络 + 模型）
RUN_EXTERNAL_TESTS=1 pytest

# 仅 GPU
RUN_GPU_TESTS=1 pytest -m gpu

# 仅 OCR
RUN_OCR_TESTS=1 pytest -m ocr

# Core 快速回归（当前默认）
pytest
```

## 运行策略

| 场景 | 命令 |
|------|------|
| 开发时快速回归 | `pytest` （默认 skip external/slow/gpu/ocr） |
| 提交前检查 | `pytest` + 确认无 FAILED（允许 xfailed） |
| 完整环境验证 | `RUN_EXTERNAL_TESTS=1 pytest` |
| GPU 验证 | `RUN_GPU_TESTS=1 pytest -m gpu` |

## Core Suite（永远全绿）

以下测试文件构成 core 套件，在任何环境都应 100% 通过：

```
test_v031_job_reliability.py    V0.3.1 任务可靠性
test_v04_provenance.py          V0.4   来源追踪
test_v051_template_quality.py   V0.5.1 模板质量
test_v06_collections.py         V0.6   合集
test_v061_import_export.py      V0.6.1 导入导出
test_v07_study.py               V0.7+  复习系统
test_v08_qa.py                  V0.8+  Q&A 检索问答
```

## V0.8.1 Baseline Refresh 变更 (2026-06-24)

| 文件 | 变更 | 说明 |
|------|------|------|
| `test_video_resource_cleanup.py` | 4 tests xfail→core | `archive_to_obsidian` (3) + `crossref` (1) 测试现有模块，不受 pipeline 服务化影响 |
| `test_vision_pipeline.py` | 3 tests xfail→core | `generate_notes/provider_settings` + `note_prompt` (2) 测试现有模块 |
| `test_video_analysis_dependencies.py` | 1 test xfail→core | `pyinstaller_spec` 读取现有 spec 文件 |
| `test_v08_qa.py` | +5 新测试 | Sources 格式 polish + --save-answer / qa_history.jsonl |

| 指标 | V0.7.2 | V0.8 | V0.8.1 | V0.9 | V1.0-beta.1 | V1.0-beta.2 | V1.0-beta.3 | V1.0-beta.4 | V1.0-rc.2 |
|------|--------|------|--------|------|-------------|-------------|-------------|-------------|------------|
| Core passed | 248 | 306 | 319 | **319** | 319 | **338** | **380** | **411** | **437+** |
| Full passed | 572 | 630 | 643 | **643** | 643 | **662** | **704** | **735** | **849** |
| Skipped | 93 | 93 | 93 | 93 | 93 | 93 | **94** | 94 | 93 |
| xfailed | 21 | 21 | 21 | 21 | 21 | 21 | 21 | 21 | **9** |
| XPASS | 8 | 8 | 0 | **0** | 0 | **0** | **0** | **0** | **0** |
| FAILED | 0 | 0 | 0 | **0** | 0 | **0** | **0** | **0** | **0** |

## V1.0-beta.2 GUI Study/QA 变更 (2026-06-24)

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/core/qa/exporter.py` | +新建 | QAExporter 类 — save_answer / append_history / get_output_dir |
| `src/core/qa/__init__.py` | +1 导出 | 新增 QAExporter |
| `src/app/cli.py` | 重构 | `_cmd_ask` 保存逻辑改用 QAExporter |
| `src/gui/widgets/study_export_widget.py` | +新建 | StudyExportWidget + StudyWorker (QThread) |
| `src/gui/widgets/qa_widget.py` | +新建 | QAWidget + QAWorker (QThread) |
| `src/gui/widgets/__init__.py` | +2 导出 | StudyExportWidget, QAWidget |
| `src/gui/windows/sidebar_nav.py` | +2 导航项 | 复习、问答 |
| `src/gui/windows/main_window.py` | +2 tabs | Tab 8(复习) + Tab 9(问答)，_SIDEBAR_TO_TAB 更新 |
| `tests/test_v10_beta2.py` | +19 新测试 | QAExporter (8) + GUI widgets (11) |

## V1.0-beta.3 First-run Wizard & Diagnostics 变更 (2026-06-24)

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/core/diagnostics/__init__.py` | +新建 | 诊断模块入口 |
| `src/core/diagnostics/models.py` | +新建 | DiagnosticCheck + DiagnosticReport 数据模型 |
| `src/core/diagnostics/checker.py` | +新建 | EnvironmentChecker — run_all() + 12 项检查 |
| `src/gui/dialogs/__init__.py` | +新建 | 对话框模块入口 |
| `src/gui/dialogs/first_run_wizard.py` | +新建 | FirstRunWizard (QWizard) 4 页：欢迎/环境/设置/完成 |
| `src/app/cli.py` | +1 命令 | `--doctor` 环境诊断 CLI + `_cmd_doctor()` |
| `src/gui/windows/main_window.py` | +2 方法 | `_maybe_show_wizard()` + `open_diagnostics_wizard()` 启动时自动检查 |
| `src/gui/windows/settings_panel.py` | +1 信号+按钮 | `diagnostics_requested` 信号 + "运行环境检查"按钮 |
| `pyproject.toml` | 版本 | 1.0.0-beta.2 → 1.0.0-beta.3 |
| `tests/test_v10_beta3.py` | +42 新测试 | 诊断模型(16) + 检查器(13) + CLI(4) + GUI向导(7) + 设置集成(2) |

## V1.0-beta.4 Installer / Release 变更 (2026-06-24)

| 文件 | 变更 | 说明 |
|------|------|------|
| `scripts/release_check.py` | +新建 | 发布前验证脚本 (compileall + CLI smoke + core tests) |
| `scripts/smoke_exe.py` | +新建 | 打包后冒烟测试 (--help / --template-list / --doctor) |
| `docs/RELEASE_CHECKLIST.md` | +新建 | 发布检查清单（发布前/测试/构建/冒烟/打包/标签） |
| `.github/workflows/release.yml` | +新建 | CI 发布流程 (check → build → smoke → zip → GitHub Release) |
| `build/gpu.spec` | 修复 | tools/ 路径加入 sys.path，解决 cuda_runtime_hook 加载问题 |
| `CHANGELOG.md` | +4 条目 | V1.0-beta.1 ~ V1.0-beta.4 完整记录 |
| `README.md` | +1 节 | GUI Beta Quickstart（首次启动流程 + 侧边栏功能表 + CLI 命令） |
| `pyproject.toml` | 版本 | 1.0.0-beta.3 → 1.0.0-beta.4 |
| `tests/test_v10_beta4.py` | +31 新测试 | 发布脚本(13) + 冒烟测试(6) + 命名(3) + checklist(3) + README(3) + CI(3) |

## V1.0.0-rc.2 Phase 7 Legacy 清理 (2026-06-27)

| 文件 | 变更 | 说明 |
|------|------|------|
| `tests/test_vision_pipeline.py` | 删除 6 strict xfail, 新增 2 测试 | 替换旧 `_save_transcript_and_notes` 死代码为 ArtifactWriter 目标测试 |
| `tests/test_video_resource_cleanup.py` | 删除 5 strict xfail, 新增 3 测试 | 替换为 CleanupManager (cleanup_temp/cleanup_job/safe_remove) 目标测试 |
| `tests/test_video_analysis_dependencies.py` | 删除 strict xfail, 新增 1 测试 | 替换 requirements.txt 检查为 pyproject.toml extras 检查 |
| `src/audio_extractor.py` | +deprecation warning | 10 个 root wrapper 标注 FutureWarning |
| `src/batch_processor.py` | +deprecation warning | 同上 |
| `src/downloader.py` | +deprecation warning | 同上 |
| `src/frame_extractor.py` | +deprecation warning | 同上 |
| `src/gui.py` | +deprecation warning | 同上 |
| `src/note_search.py` | +deprecation warning | 同上 |
| `src/prompts.py` | +deprecation warning | 同上 |
| `src/subtitle_writer.py` | +deprecation warning | 同上 |
| `src/transcriber.py` | +deprecation warning | 同上 |
| `src/yt_dlp_compat.py` | +deprecation warning | 同上 |
| `src/core/video/downloader.py` | 修复 import | 改用 `src.core.video.yt_dlp_compat` 避免内部触发 deprecation warning |
| `src/core/pipeline/video_pipeline.py` | 增强 docstring | 添加 Compatibility Contract 文档 |
