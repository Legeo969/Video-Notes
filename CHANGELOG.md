## V2.0.0 (2026-07-05) — 技术栈重构：Tauri + Svelte + 干净分层架构

- **GUI 框架替换**：PySide6 → Tauri 2 + Svelte 5 + TypeScript。桌面壳使用 Rust 原生，前端 20 kB gzipped。
- **架构分层清理**：删除 `src/core/`（100 文件），代码迁移到 `src/application/`（管线/服务/LLM）、`src/infrastructure/`（DB/转录/Provider）、`src/domain/`（模型/接口）。
- **新增 Engine API 层**：`src/api/` — 完整的 JSON-RPC 2.0 over stdin/stdout 协议，18 个文件，9 个冒烟测试。
- **Rust Engine Manager**：Python 侧车进程管理 + Windows Job Object 进程树清理 + Content-Length 帧协议。
- **Python 侧车架构**：Tauri 启动 Python `engine.py --stdio`，通过 framed JSON-RPC 通信，stderr 专用日志通道。
- **桌面应用构建**：`npx tauri build` 生成 MSI + NSIS 安装包。告别 PyInstaller（~1.6 GB 打包）。
- **组件化运行时**：`runtime/manifests/` + `infrastructure/system/component_manager.py` — 支持组件独立安装/更新/回滚。
- **所有导入路径清理**：`src.*` 400+ 处 `src.core.*` 引用全部迁移到正确层，零遗留。
- **测试套件维护**：删除 18 个引用旧架构的测试文件，修复 4 个测试的导入路径。
- **旧 GUI 删除**：`src/gui/`、`src/batch_processor.py`、`src/prompts.py`、`src/transcriber.py`、`src/yt_dlp_compat.py`、`launcher.py`、`video-notes-ai.spec`、`tools/`、旧构建脚本。

## V1.2.0 (2026-07-04) — 稳定运行时与增量代码更新架构

- 将多 GB 的 CUDA、PaddleOCR、Whisper、PySide6 运行时与约 1 MB 的应用代码分离。
- PyInstaller EXE 改为稳定启动壳，实际 GUI/业务代码从安装目录 `app/` 加载。
- 新增 `build_windows.ps1 -CodeOnly`：不运行 pip/PyInstaller，数秒内更新代码。
- 新增代码更新 ZIP、原子替换、运行时 API、依赖指纹和导入模块兼容性校验。
- 依赖或导入集合变化时会拒绝不安全的快速更新，要求完整重新打包。
- 侧边栏、问题报告和会话日志分别记录应用代码版本与重依赖运行时版本。
- 版本号更新为 `1.2.0`。

## V1.1.5 (2026-07-04) — 设置页与合集操作区 UI 修复

- 设置页改为固定标题、独立滚动内容区和固定底部操作栏，保存设置、环境检查和问题报告按钮不再埋在长页面底部。
- 设置内容区禁用横向滚动，窗口缩放时表单保持单列对齐，不再出现横向错位或被误认为页面截断。
- 合集表格操作按钮显式清除全局 `QPushButton` 内边距和最小高度，修复“详情 / 导出 / 删除”只显示一个汉字。
- 合集操作列固定为 224 px，按钮统一为 62×30 px，行高调整为 46 px并居中显示。
- 新增 V19 UI 布局回归测试。
- 版本号更新为 `1.1.5`。

## V1.1.4 (2026-07-04) — 真正按阶段断点续跑

- 修复“继续任务”因当前设置与原任务设置不同而重新提取音频、重新运行 Whisper 的问题。
- 每个内置阶段只计算与自身有关的缓存输入；API Key、LLM 模型或笔记模板变化不再使媒体解析和转录断点失效。
- 显式“继续任务”把状态为 completed 且产物完整的阶段视为权威断点，并兼容 V17 旧版 manifest 哈希。
- 继续时仍严格检查音频、视频、转录、截图及最终文件；文件缺失或为空时只重跑受影响阶段。
- 恢复日志新增“♻️ 断点复用”，可明确看到哪些阶段被跳过。
- 修复视觉分析和融合阶段恢复后字典未还原为领域对象的问题，避免后半段继续时类型错误。
- “从头重跑”仍创建新任务并执行完整管线，用于主动应用已更改的 Whisper、抽帧等前置设置。
- 版本号更新为 `1.1.4`。

## V1.1.3 (2026-07-04) — Windows 子进程控制台彻底隐藏

- 在应用启动最早阶段安装全局 Windows 子进程隐藏保护，覆盖 yt-dlp、FFmpeg、FFprobe、OCR worker 及第三方库内部启动的命令。
- 自动移除第三方调用中的 `CREATE_NEW_CONSOLE`，统一合并 `CREATE_NO_WINDOW` 与 `STARTF_USESHOWWINDOW/SW_HIDE`。
- 保护同时覆盖 GUI、CLI、失败任务继续/重跑和私有 OCR 子进程，不再在用户操作其他软件时弹出黑色控制台并抢焦点。
- 保留 PyInstaller `console=False` 主程序配置，并继续保留项目自有调用的局部隐藏参数，形成双层防护。
- 版本号更新为 `1.1.3`。

## V1.1.2 (2026-07-04) — 数据库永久清理与空间回收

- 任务页新增“永久清理”，用于删除已经被“清空历史”隐藏的任务记录、Provenance 索引和对应 `.jobs` 工作目录。
- 永久清理不会删除最终导出的 Markdown、转录、截图或 Obsidian 文件。
- 仍属于合集的隐藏任务自动跳过，避免破坏合集总览和来源追踪。
- 清理后执行 WAL checkpoint、`PRAGMA optimize` 和 `VACUUM`，真实缩小 `video_notes.db` 及 WAL 占用。
- 单条“删除任务”改为原子删除任务、关联索引和无引用笔记副本，不再残留完整 Markdown 内容。
- 永久清理会顺带回收旧版本遗留的无引用 `notes` / `note_keywords` 数据，并保护合集引用的笔记路径。
- 数据删除成功但数据库暂时被其他连接占用时，不再误报整个操作失败；界面会提示稍后重新压缩。
- 新增数据库保留策略回归测试，覆盖导出文件保留、合集跳过、共享笔记保护和旧孤儿数据清理。
- 合集批处理改为从 `providers + bindings` 用途绑定解析供应商、模型、Base URL 和 API Key，不再依赖可能为空的旧顶层字段。
- 合集启动请求补齐 `collection_id`，失败任务恢复时也会从 `collection_items` 找回合集归属并完成回写。
- 单任务、普通批处理、合集批处理及任务继续/重跑前统一校验 AI 配置，缺少 Key 时直接提示，不再先下载或转录。
- 任务操作列固定宽度并清除紧凑按钮继承的全局内边距，避免“详情/删除/继续/重跑”等按钮只显示一个汉字。
- LLM 失败提示改为用户可读文本，不再暴露 `request.api_key` 等内部字段。
- 版本号更新为 `1.1.2`。

## V1.1.1 (2026-07-04) — Windows 复用构建离线修复

- 修复 `-ReuseBuildVenv` 仍触发 PEP 517 隔离环境联网下载 `setuptools/wheel` 的问题。
- 复用模式跳过 packaging tools 升级，改为验证现有版本。
- editable install 强制使用 `--no-build-isolation`。
- 复用模式统一使用 `--no-index`，只使用 `.venv-build` 中已安装的依赖；缺失依赖会明确失败，不再被 SSL 重试噪声掩盖。
- Paddle、PaddleOCR/PaddleX、PyInstaller 安装步骤同步支持离线复用。

## V1.1.0 (2026-07-04) — 任务生命周期、原子产物与可验证紧凑 GPU 包

- 同标题任务改为 `标题/run_<job-id>/` 独立产物目录，旧任务不再被覆盖。
- 整个最终产物包先写入 staging 目录，全部成功后再目录级提交；失败重导出会保留上一份完整成品。
- `frames/` 随整个产物包替换，不再残留上一次运行的旧截图。
- 任务操作拆分为“暂停并保留断点”和“取消并清理工作数据”；停止状态在真实 worker 退出后收敛。
- 修复 token 已创建但 worker 尚未启动时任务永久卡在 `cancelling` 的竞态。
- “清空历史”改为隐藏历史行，`.jobs`、Provenance 和最终产物继续拥有稳定数据库父记录；工作区清理由独立操作负责。
- Manifest 写入改为原子替换，并加入请求配置、上游状态和文件指纹哈希；配置或输入变化后不再错误复用旧缓存。
- OCR worker 使用二进制管道解码第三方原生日志，自动去除 ANSI 控制符，并兼容 UTF-8/GB18030。
- 新增 V13 数据库迁移、`is_hidden` 列、迁移前 SQLite Backup API 备份和完整性检查。
- Windows 打包新增 `portable`/`compact` GPU 配置；compact 仍必须通过打包后真实 OCR 推理检查，否则构建失败。
- 版本号更新为 `1.1.0`。

## V1.0.9 (2026-07-04) — 数据库生命周期修复与无控制台窗口

- Windows 主程序改为 GUI 子系统启动，双击 EXE 不再常驻 CMD 黑框。
- FFmpeg、FFprobe、诊断工具和合集导入子进程统一使用隐藏窗口参数。
- 打包后的 OCR/模板/doctor 检查改为 `Start-Process -Wait -PassThru`，仍严格校验退出码。
- 首次 V12 迁移前使用 SQLite Backup API 生成 `video_notes.db.pre-v12.bak`。
- 修复缺失或重复 `job_id`，并建立唯一索引。
- 一次性清理无任务、无知识块、无合集归属的旧版孤儿来源/转录记录。
- “清空历史”只清任务记录，不删除 `.jobs`、最终产物或 Obsidian 文件；新增独立“清理工作数据”。
- 修复 GUI 重试误传 UUID 的问题，改用数字 `run_id` 并在恢复前重置错误/完成状态。
- 取消令牌和子进程注册表按数据库共享，任务页取消不再只是修改数据库。
- 统一断点判断到实际 `FileManifestStore`，损坏清单或缺失产物不会被误判为可复用。
- Provenance 现在写入最终帧、OCR、笔记及知识块索引。
- 版本号更新为 `1.0.9`。

---

# Changelog

## V1.0.8 (2026-07-04) — 供应商列表去重与保存语义修复

- 修复点击“保存配置”时隐式追加空白 `新供应商-N` 的问题。
- 供应商只有在用户明确点击“+ 添加”时才会新建。
- 保存配置会更新当前已选供应商，但不会把空编辑表单转成新供应商。
- 启动时自动清理旧版本遗留的未绑定空白供应商，并写回 settings.json。
- 同名供应商自动合并，模型列表去重，失效绑定自动清理。
- 供应商状态改为页面实例私有，避免多个设置页实例共享可变列表。
- 下拉框增加“请选择供应商 / 未绑定”占位提示。
- 版本号更新为 `1.0.8`。

All notable changes to the video-notes-ai project.

---

## V1.0.7 (2026-07-04) — OCR UTF-8 IPC + 日志自动治理

### Fixed
- **OCR 子进程通信**：强制 stdin/stdout/stderr 使用 UTF-8，并使用 ASCII-safe JSON 传输，修复 Windows GBK 环境下 `✓` 等字符导致部分帧 OCR 结果丢失。
- **OCR Worker 日志**：子进程不再单独创建 `session-*.log`；原生崩溃信息通过 stderr 合并到主进程日志。
- **构建检查日志**：`--check-ocr`、`--template-list`、`--doctor` 等只读命令不再生成 session 日志。

### Added
- **日志自动清理**：正常日志保留 7 天，异常日志保留 30 天；最多 50 个文件、总量最多 200 MB；活动进程日志和 `last-stage.json` 永不删除。
- **回归测试**：覆盖 GBK 不可编码字符、UTF-8 Worker 环境、日志保留期、数量/体积上限和只读命令日志抑制。

### Changed
- `pyproject.toml` 版本号 → `1.0.7`

---

## V1.0.3 (2026-06-28) — GUI 修复 + Repository 清理

### Fixed
- **First-run Wizard**：修复"运行环境检查"和"打开完整设置"触发 `setCurrentIndex` 崩溃的问题。
- **Repository hygiene**：清理临时缓存并补充 `.gitignore` 规则，避免本地日志和临时目录进入仓库。

### Changed
- `pyproject.toml` 版本号 → `1.0.3`
- `installer/video-notes-ai.iss` 安装器版本号 → `1.0.3`
- **清理**：移除已废弃的 QA、复习、Embedding 相关模块和测试。

---

## V1.0.2 (2026-06-25) — Vision+MAP/REDUCE 架构 + Provider 独立化

### Added — 新管线架构
- **Speech Layer** (`src/core/speech/`): SpeechTranscriber 使用 faster-whisper 分段并行转录，输出结构化 SpeechSegment
- **Vision Layer** (`src/core/vision/frame_understanding.py`): FrameUnderstandingService 结构化视觉理解 + 重要性阈值 0.65 过滤
- **Fusion Layer** (`src/core/fusion/`): FusionEngine 时间线融合（转录 + 视觉），生成章节和摘要块
- **LLM MAP** (`src/core/llm/map_stage.py`): 并行 LLM 摘要（4 线程），每块生成 JSON 结构化摘要
- **LLM REDUCE** (`src/core/llm/reduce_stage.py`): 最终结构化 Markdown 笔记生成（概要/核心概念/章节笔记/视觉亮点/实践启示）
- `PipelineOrchestrator.run()` 统一入口，新管线为默认路径

### Added — 三套独立 Provider 体系
- **主 LLM provider** (`api_key`/`provider`/`base_url`) → MAP/REDUCE 文本摘要
- **Vision provider** (`vision_api_key`/`vision_provider`/`vision_base_url`) → 帧视觉理解，缺失时明确报错
- **KB/Ask provider** (`kb_api_key`/`kb_provider`/`kb_base_url`/`kb_model`) → QA 问答，缺失时走 fallback 摘要
- `llm/__init__.py`: `get_provider()` 增加 GUI 显示名归一化（`bailian`→`dashscope`, `自定义`→`openai_compat`）

### Fixed — 运行时问题
- **笔记语言**: MAP/REDUCE system prompt 改为中文，强制笔记使用简体中文撰写（英文视频也生成中文笔记）
- **语言字段**: 修复 `transcribe_with_segments` 未将 `info.language` 传递给 `SpeechResult.language` 的问题
- **知识块提取**: `KnowledgeIndexer` 改用 `request.provider`/`request.api_key` 构造 provider（不再环境变量推断），失败时打印完整 traceback
- **Obsidian 图片路径**: 修复 `![[frame.jpg]]` 格式在 Obsidian vault 中找不到图片的问题（自动补 `frames/` 前缀）
- **CleanupManager**: 修复误拒删除 `.jobs/{uuid}` 子目录的问题（检查路径而非 basename）
- **GUI .env 加载**: `main.py` 增加 `load_dotenv()` 调用，GUI 模式也能读取 `.env` 中的 API Key
- **LLM 401 快速失败**: 所有 MAP 块失败时立即抛出 RuntimeError，避免空笔记
- **10 项代码审查修复**: 详见 commit `ef2c482`

### Changed
- `SpeechTranscriber` 构造函数从 `(model, model_size)` 改为 `(model_size, model_dir)`
- `PipelineRequest` 新增 `beam_size`/`vad_filter` properties
- `pyproject.toml` 版本号 → `1.0.2`

---

## V1.0.1 (2026-06-25) — Hotfix Release

### Fixed
- **Docs**: Fixed path spacing in bash code blocks (GitHub rendering of `\v`/`\w` in paths)
- **Docs**: Synced Release Notes test baseline to current (793 passed / 469 core)
- **Docs**: Fixed Release Notes GUI sidebar icon mismatch (⚙ 处理 → 🎬 处理)
- **Docs**: Clarified Lite=auto CI, Full/GPU=manual build in Release Notes
- **Repo**: Fixed installer `MyAppURL` to the correct GitHub repo URL
- **Repo**: Added missing `.gitignore` entries (`.note_index/`, `*.db`, `issue_bundle_*.zip`, `logs/`)
- **Repo**: Removed tracked AI tool metadata directories from git (`.agent`, `.agents`, `.codegraph`, `.codex`, `.comet`, `.opencode`)
- **Repo**: Removed root `requirements.txt` (use `requirements/base.txt` instead)

---

## V1.0.0 (2026-06-25) — First Stable Release

### Overview
Video Notes AI v1.0 is a complete desktop application that converts video lectures
into structured, searchable Markdown notes. This is the first stable release after
a 4-version RC cycle.

See `docs/RELEASE_NOTES_v1.0.md` for the full user-facing feature list.

---

## V1.0.0-rc.2 (2026-06-25) — Final Polish / Release Notes

### Added
- **Release Notes**: `docs/RELEASE_NOTES_v1.0.md` — user-facing feature summary for GitHub Release
- **Version consistency check**: `scripts/release_check.py` now verifies pyproject.toml / CHANGELOG / README / installer versions match
- **Tests**: 21 new tests for release notes structure, version consistency, GUI polish validation

### Changed
- **GUI**: Sidebar icon deduplication (处理: ⚙ → 🎬, 设置: ⚙)
- **GUI**: Page title "问答 (Q&A)" → "问答"
- **GUI**: "重新处理" button → "重试" (consistent with job list)
- **GUI**: "Sources：" → "来源引用："
- **GUI**: Error messages no longer expose Python tracebacks to users
- **GUI**: Unified half-width `:` → full-width `：` in status messages
- **README**: Updated test counts (772+), core counts (448), sidebar layout description (8 pages)
- **CHANGELOG**: Normalized version header to full SEMVER format (V1.0.0-rc.1)

### Fixed
- release_check.py version regex now captures pre-release patch numbers (e.g. `rc.1`, not just `rc`)
- test_version_is_beta4 → test_version_is_valid (version-agnostic)

---

## V1.0.0-rc.1 (2026-06-24) — Installer / Crash Report / GUI Polish

### Added
- **Windows Installer**: `installer/video-notes-ai.iss` — Inno Setup script for lite edition (`.exe` setup with Start Menu shortcuts, Chinese/English bilingual wizard)
- **Issue Bundle**: `src/core/diagnostics/issue_bundle.py` — collect version info, diagnostics report, sanitized settings, recent logs, job metadata into timestamped zip
- **CLI `--issue-bundle`**: generates issue bundle from command line for bug reporting
- **GUI Issue Report**: "生成问题报告包" button in Settings panel (emits `issue_bundle_requested` signal)
- **QA Copy**: "复制回答" button in QA widget copies full markdown answer to clipboard
- **QA Sources Interaction**: double-click on sources table row shows full quote popup
- **Empty State**: job list and collection list show friendly placeholder messages when empty

### Changed
- **Version**: `pyproject.toml` updated to 1.0.0-rc.1
- **Refresh Buttons**: job list and collection list refresh buttons disabled during reload to prevent duplicate clicks
- **File Open UX**: job detail widget shows user-friendly error dialogs when file/directory not found
- **Study Output Path**: study export uses absolute path for output directory (fixed relative `.jobs/` path bug)
- **Release Checklist**: added RC专区 (installer smoke, first-run wizard, --doctor in exe, clean VM, issue bundle)

### Fixed
- `sanitize_value()` now handles non-string settings values (int, bool, etc.)
- Collection list refresh button styling now consistent with other widgets

---

## V1.0-beta.4 (2026-06-24) — Installer / Release

### Added
- **Release script**: `scripts/release_check.py` — pre-release verification (compileall + CLI smoke + core tests)
- **Smoke test script**: `scripts/smoke_exe.py` — post-build exe validation (--help / --template-list / --doctor)
- **Release workflow**: `.github/workflows/release.yml` — CI pipeline: check → build → smoke → zip → GitHub Release
- **Release checklist**: `docs/RELEASE_CHECKLIST.md` — step-by-step release guide
- **README**: GUI Beta Quickstart section with first-run workflow and feature overview

### Changed
- **Version**: `pyproject.toml` updated to 1.0.0-beta.4
- **GPU spec**: Fixed runtime hook path (`tools/` added to sys.path in `gpu.spec`)

---

## V1.0-beta.3 (2026-06-24) — First-run Wizard

### Added
- **Diagnostics core**: `src/core/diagnostics/` — `EnvironmentChecker` + `DiagnosticReport` (12 checks)
- **First-run Wizard**: `src/gui/dialogs/first_run_wizard.py` — QWizard (welcome / env check / settings / finish)
- **CLI**: `--doctor` flag for environment diagnostics
- **Settings**: `first_run_completed` flag + "运行环境检查" button in settings panel
- **Tests**: 42 new tests (models / checker / CLI / wizard / settings)

### Changed
- **Version**: `pyproject.toml` updated to 1.0.0-beta.3

---

## V1.0-beta.2 (2026-06-24) — GUI Study / QA

### Added
- **QAExporter**: `src/core/qa/exporter.py` — shared save logic (CLI + GUI)
- **Study widget**: `src/gui/widgets/study_export_widget.py` — flashcard / quiz / Anki export
- **QA widget**: `src/gui/widgets/qa_widget.py` — question input + Markdown preview + sources table
- **Sidebar**: + 复习 / 问答 (8 items total)
- **Tests**: 19 new tests (QAExporter / GUI widgets)

### Changed
- **CLI**: `_cmd_ask` refactored to use `QAExporter`
- **Version**: `pyproject.toml` updated to 1.0.0-beta.2

---

## V1.0-beta.1 (2026-06-24) — GUI Job / Collection

### Added
- **Job widgets**: `JobListWidget` (table + refresh/retry/cancel) + `JobDetailWidget` (status / artifacts / resume)
- **Collection widgets**: `CollectionListWidget` (create/import/export) + `CollectionDetailWidget` (items / overview / export)
- **Sidebar**: + 任务 / 合集 (6 items, tab mapping updated)
- **Auto-refresh**: tab switch triggers data refresh

### Changed
- **Version**: `pyproject.toml` updated to 1.0.0-beta.1

---

## V0.9 (2026-06-24) — Release Engineering

### Added
- **Dependency layering**: `requirements/` directory with 7 layered files (base / gui / vision / ocr / cuda / build / dev)
- **CI**: `.github/workflows/test.yml` — core tests across Python 3.10–3.13
- **CI**: `.github/workflows/build-smoke.yml` — PyInstaller lite build on tag push

### Changed
- **Version**: `pyproject.toml` updated from 0.1.0 to 0.9.0
- **Build**: Formalized `build/build.py` interactive build script for lite / full / gpu variants
- **Build specs**: `build/lite.spec` (~440MB), `build/full.spec` (~520MB), `build/gpu.spec` (~580MB+)
- **Docs**: README reorganized around V0.3–V0.8 user workflows
- **Docs**: CHANGELOG updated with complete version history V0.1–V0.9

---

## V0.8.1 (2026-06-24) — QA Polish + Baseline Refresh

### Added
- `--save-answer` flag: saves QA results to `artifacts/qa/` or `collections/{id}/qa/`
- `qa_history.jsonl`: append-only manifest for QA sessions
- Backtick source_ref format (`\`job@HH:MM:SS\``) in Sources output

### Changed
- Sources output: empty/whitespace quotes no longer render blank blockquote
- 8 xpassed tests resolved: precise per-test xfail marking instead of module-level

### Fixed
- XPASS noise eliminated: 0 unexpected xpassed in full suite

---

## V0.8 (2026-06-24) — Collection Q&A with Citations

### Added
- **QA module** (`src/core/qa/`): models / formatter / retriever / engine
- **Retriever** with 3-layer fallback: vector search → keyword overlap → top-N blocks
- **QAEngine**: LLM-powered or fallback summary, citations attached by system (not LLM)
- **Citation formatter**: unified `source_ref` format (job_id@HH:MM:SS)
- CLI: `--ask-job <id>`, `--ask-collection <id>`, `--max-blocks`, `--save-answer`
- 58 new core tests (319 core total)

### Design
- No LLM → auto fallback to summary mode (never crashes)
- Citations assembled from `block_sources`, not generated by LLM
- CJK + ASCII mixed tokenization for keyword overlap

---

## V0.7.2 (2026-06-24) — Regression Baseline Cleanup

### Added
- `tests/BASELINE.md`: full failure classification documentation
- `tests/conftest.py`: `--run-external/--run-slow/--run-gpu/--run-ocr` CLI switches
- `tests/helpers/markers.py`: `requires_ffmpeg/cuda/ocr/gui` skip factories
- 6 pytest markers in `pyproject.toml`

### Changed
- Core suite (306 tests) achieves 100% pass rate
- 22 legacy tests → `xfail(strict=False)`, 71 external → `skip`

### Fixed
- Settings atomic write (tmpfile + flush + fsync + os.replace)
- Note generator retries parameter passthrough
- Processing metadata dict compatibility in `get_recent_runs()`
- `SQLiteIndex` injectable embedding provider
- `VectorIndex` fallback when `knowledge_blocks` table missing

---

## V0.7.1 (2026-06-24) — Anki / Study Export Polish

### Added
- **Anki TSV export**: Front/Back/Tags format with HTML entity escaping
- **`source_ref` field**: `job_id@HH:MM:SS` appended to CSV and Anki Back
- **Difficulty heuristics**: sources≥3→hard, chars≤100→easy, chars≥500→hard
- **Tags**: `video-notes-ai scope:{id} job:{id} type:{block_type} template:{template_id}`
- **`study_export.json` manifest**: metadata for all exported study materials
- CLI: `--anki-export`, `--anki-export-job`

### Changed
- CSV export expanded from 5 to 6 columns (added source_ref)
- Flashcards and quiz commands auto-generate TSV + manifest
- 96 tests (31 new for V0.7.1)

---

## V0.7 (2026-06-24) — Study System / 复习系统

### Added
- **Study module** (`src/core/study/`): models / generator / exporter
- **deterministic flashcards**: block_type → question template, 5 quiz templates by index
- **StudyExport**: flashcards.md / flashcards.csv / quiz.md
- **Job and collection scope** for study generation
- CLI: `--flashcards-job`, `--quiz-job`, `--flashcards-collection`, `--quiz-collection`, `--study-export`, `--max-items`
- 65 new tests

### Design
- No LLM, no new DB schema — purely deterministic from `knowledge_blocks` + `block_sources`
- CSV: `lineterminator='\n'` for Windows CRLF fix

---

## V0.6.1 (2026-06-24) — Collection Import & Export

### Added
- **`CollectionFolderImporter`**: recursive folder import (10+ media extensions, hidden dir filter, natural sort)
- **`CollectionPlaylistImporter`**: yt-dlp flat playlist import with skip+warning on missing URLs
- **`CollectionExporter`**: structured export to `output/collections/{id}/` with overview, concept index, items, notes, assets, `.meta.json`
- CLI: `--folder`, `--playlist`, `--recursive`, `--sort`, `--collection-export`
- 39 new tests (198 total)

---

## V0.6 (2026-06-24) — Collections / 课程与合集

### Added
- **Collections module** (`src/core/collections/`): models / schema / service / renderer
- 3 new DB tables: `collections`, `collection_items`, `collection_summaries`
- **`CollectionService`**: create / list / get / add_job / get_items / get_status / generate_overview
- **`CollectionOverviewRenderer`**: deterministic aggregation (info, videos, summaries, concept index, quality warnings) — no LLM
- `PipelineRequest.collection_id`: auto-assign jobs to collections on success
- CLI: `--collection-create`, `--collection-list`, `--collection-status`, `--collection-add-job`, `--collection-overview`
- `--batch`/single mode `--collection` auto-assignment
- Slug generation: ASCII→lowercase-hyphen, CJK→`col-{md5[:8]}`
- 43 new tests (V0.4+ V0.5+ V0.6 = 159 total)

---

## V0.5.1 (2026-06-24) — Template Quality & Recommendations

### Added
- **`TemplateRecommender`**: heuristic recommendation (12 keyword rules CN/EN), `recommend_templates()` / `best_template()`
- **Template file structure validation**: `validate_template_file(path)` with 10 checks
- **Template preview**: `TemplateRegistry.preview_template(id)` → human-readable summary
- CLI: `--template-preview <id|path>`, `--template-validate <path>`, `--template-recommend <query>`
- Auto-save `template_validation.json` to artifacts after note generation
- 41 new tests (84 total)

### Changed
- CLI `--job-status` shows template validation status (✅/⚠️/❌ with warning count)
- Template validation is silent on failure (non-blocking)

---

## V0.5 (2026-06-24) — Template-Based Note Generation

- **模板化笔记生成**：根据视频类型生成不同结构的笔记
- **8 个内置 YAML 模板**：default / study / meeting / coding_tutorial / lecture / interview / product_demo / research
- **TemplateRegistry**：加载、查询、用户自定义模板
- **PromptBuilder**：模板 → system prompt + user prompt（全量 / chunk / merge 三种模式）
- **TemplateValidator**：Markdown heading 校验（必需章节 / 空章节 / 重复标题 / 输出过短）
- **CLI**：`--template-list` / `--template study`（ID 或文件路径，智能识别）
- **PipelineRequest** 新增 `template_id` 字段
- **NoteGenerator** 接入模板系统：分段 chunk 模板注入 → LLM 模板合并（去重+补齐）+ 模板校验
- **向后兼容**：旧 `--template /path/to/file.md` 仍可用；不传 template 默认使用 default 模板
- 43 项新测试（140 total）

---

## V0.4.1 (2026-06-24)

## V0.4.1 (2026-06-24)

### Provenance Polish

- **CLI**: `--reindex-job <id>` — 从 artifacts 重建 provenance 索引
- **CLI**: `--reindex-all` — 批量重建所有已完成任务的 provenance 索引
- **CLI**: `--citation-preview <id>` — 预览来源引用（Markdown 格式，不写文件）
- **CLI**: `--job-status` 显示 provenance 状态（已索引/引用就绪）
- **CLI**: `--job-list` 标记 📎=引用就绪 / 📄=已索引
- **ProvenanceIndexer**: `index_job(dry_run=True)` — 仅计算不写入
- **ProvenanceIndexer**: `check_provenance_status(job_id)` — 查询各表行数 + citation_ready 状态
- **ProvenanceIndexer**: `check_all_jobs_provenance()` — 批量状态查询
- **block_sources**: UNIQUE(block_id, source_kind, source_id) 去重约束
- **block_sources**: INSERT OR IGNORE + UPDATE 替代 DELETE+INSERT（避免丢失手动绑定）
- **V0.4.1 迁移**: `_migrate_block_sources_dedup()` — 清理已有重复行
- **vector_index**: `search_unified()` — 合并去重搜索结果 + sources 列表格式
- **测试**: 15 新测试（dry_run / 状态检查 / 去重 / 幂等 / unified search），97 total

### CLI 快速参考

```bash
python main.py --reindex-job 1           # 为任务 #1 重建索引
python main.py --reindex-all              # 批量重建
python main.py --citation-preview 1       # 预览引用
python main.py --job-list                 # 查看所有任务（含 provenance 状态）
python main.py --job-status 1             # 查看任务详情（含 provenance）
```

---

## V0.4 (2026-06-24)

## V0.4 (2026-06-24) — 可信知识库与来源追踪

### Added
- Provenance data model: `SourceRef` + `ProvenanceBlock` dataclasses
- New DB tables: `video_sources`, `transcript_segments`, `frame_assets`, `ocr_results`, `block_sources`
- `ProvenanceIndexer` service: reads `artifacts/` → populates provenance tables
- `SourceLinker`: time-window binding (blocks with timestamps) + text-overlap fallback
- `CitationRenderer`: Markdown citation output with timestamps, quotes, and screenshot refs
- CLI flag `--with-citations` for auto-generating source references in notes
- `PipelineResult.job_id` field for provenance tracking
- Search results now carry `block_id`, `job_id`, `start_time`, `source_kind`, `source_quote`

### Changed
- `knowledge_blocks` table extended: `note_id_int` (INTEGER FK), `job_id`, `block_index`, `summary`, `start_time`, `end_time`, `confidence`, `content_hash`
- `save_blocks()` accepts `job_id` parameter
- `KnowledgeIndexer.index()` accepts `job_id` parameter
- `PipelineOrchestrator` calls `ProvenanceIndexer` after knowledge indexing stage
- `VectorIndex.search()` now LEFT JOINs `knowledge_blocks` + `block_sources` for rich results

### Design Principles
- Provenance indexing failure does NOT block main pipeline (try/except + warning)
- All index operations are idempotent (re-running does not duplicate records)
- DB-first provenance with optional `provenance.json` export (portable copy)
- First version uses time-window + text overlap (no complex embedding alignment yet)

---

## V0.3.1 (2026-06-24)

### Added
- Resumable job artifacts: stage outputs persisted to `.jobs/{job_id}/artifacts/`
- Per-stage manifests (`transcript.manifest.json`, `frames.manifest.json`, etc.) for crash recovery
- Atomic artifact writes: `.tmp` → `fsync` → `validate` → `os.replace` → manifest → DB
- Cancellation token and process registry for graceful job termination
- SQLite WAL mode and `busy_timeout=5000` for concurrent access

### Changed
- Artifact/temp directory separation: `artifacts/` preserved on success, `temp/` cleaned up
- Pipeline orchestrator: stage-level checkpoint support, skip completed stages via manifest
- Resume logic: traverses `STAGE_ORDER` checking manifests to determine starting point
- `processing_runs` table extended with 8 new columns (`stage`, `job_dir`, `elapsed_sec`, etc.)

### Fixed
- `max_retries` passed to OpenAI API (invalid param) — removed from invoker kwargs
- `retries` NameError in `note_generator.py` — changed to `_RETRY_MAX` constant

---

## V0.3 (2026-06-24)

### Added
- Task queue system with `JobState` enum (10 states: PENDING → COMPLETED/FAILED/CANCELLED)
- `JobQueue` manager: enqueue, update stage, complete, fail, cancel, list, count
- `ProcessingMetadata` rewrite: full job lifecycle tracking
- Pipeline orchestrator with unified `PipelineRequest`/`PipelineResult`
- CLI flags: `--job-list`, `--job-status <id>`, `--resume <id>`
- Stage checkpoint files for incremental processing

### Changed
- Core pipeline split into 8 independent services under `src/core/services/`
- `video_pipeline.py` → backward-compatible layer delegating to orchestrator
- `note_embeddings` v2 schema: model, provider, content_hash, dimensions columns

---

## V0.2 (2026-06)

### Added
- Embedding engine with model/provider tracking and SHA-256 caching
- Vector search via SQLite + Python cosine scan
- `TranscriptionBackend` protocol with `@register_backend` decorator
- faster-whisper (default) and whisper.cpp backends
- PyInstaller packaging: lite/full/gpu three tiers

---

## V0.1 (2026-05)

### Added
- Initial project: video download, audio extraction, transcription
- LLM-based note generation
- Basic SQLite database for notes and embeddings
- PySide6 GUI (MainWindow, Settings, Search)
