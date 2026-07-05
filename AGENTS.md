# video-notes-ai Project Guidelines

## Overview

Windows 桌面工具：将视频（在线链接或本地文件）自动转录为结构化学习笔记。
**Tauri 2 + Svelte 5** 桌面壳 + **Python Engine** 侧车进程。
Python 3.10+ 业务引擎：faster-whisper + AI 笔记生成 + 视觉识别。
笔记直接归档到 Obsidian，知识管理在 Obsidian 中完成。

## Architecture

```
┌──────────────────────────────────────────────┐
│  Tauri Shell (Rust)                           │
│  ├─ Svelte 5 + TypeScript                     │
│  ├─ Engine Manager (spawn/manage sidecar)     │
│  ├─ Windows Job Object (process tree cleanup) │
│  └─ JSON-RPC 2.0 over stdin/stdout             │
│       (Content-Length framed)                 │
├──────────────────────────────────────────────┤
│  Python Engine (sidecar process)              │
│  ├─ api/          — JSON-RPC endpoints         │
│  ├─ application/  — Pipeline / Services / LLM │
│  ├─ domain/       — Domain models / Ports     │
│  └─ infrastructure/ — DB / Transcription / ...│
└──────────────────────────────────────────────┘
```

## Directory Structure

```
video-notes-ai/
├── main.py                     # CLI / Engine 入口
├── src/
│   ├── api/                    # JSON-RPC 2.0 引擎 API
│   │   ├── protocol/           # Content-Length 帧协议 / dispatcher / errors
│   │   ├── dto/                # Pydantic v2 数据传输对象
│   │   ├── handlers/           # RPC 方法处理器（system/process/notes/settings/collections/diagnostics）
│   │   ├── event_journal.py    # 持久化事件日志（SQLite）
│   │   └── server.py           # stdin/stdout 服务器主循环
│   ├── engine.py               # Tauri sidecar 入口
│   ├── application/            # 应用层
│   │   ├── pipeline/           # 视频处理管线 + stages（8 个阶段）
│   │   ├── services/           # PipelineOrchestrator / JobQueue / ArtifactWriter
│   │   ├── llm/                # MAP / REDUCE 笔记生成 + prompts
│   │   ├── vision/             # 视觉理解
│   │   ├── notes/              # 笔记生成编排 + 模板管理
│   │   ├── collections/        # 合集管理
│   │   ├── fusion/             # 转录 + 视觉融合
│   │   ├── speech/             # 语音识别编排
│   │   ├── provenance/         # 来源追踪
│   │   ├── providers/          # Provider 工厂 + 配置
│   │   └── diagnostics/        # 环境诊断
│   ├── domain/                 # 领域层（无外部依赖）
│   │   ├── models/             # 领域实体
│   │   ├── interfaces/         # 纯接口（Ports）
│   │   ├── types.py            # PipelineRequest / PipelineResult
│   │   └── job_state.py        # 任务状态机
│   ├── infrastructure/         # 基础设施
│   │   ├── db/                 # SQLite + repositories
│   │   ├── transcription/      # faster-whisper / whisper.cpp / subtitle_writer
│   │   ├── video/              # yt-dlp / FFmpeg / OCR worker
│   │   ├── providers/          # OpenAI / DashScope / Mimo / OCR / Vision
│   │   ├── artifacts/          # Obsidian 归档策略
│   │   └── system/             # Component Manager
│   ├── app/                    # 应用启动 + CLI
│   │   ├── bootstrap.py        # 启动引导（检测 Tauri / CLI 模式）
│   │   └── cli/                # argparse + 命令注册
│   ├── config/                 # 配置管理（settings 读写）
│   └── utils/                  # 工具函数（logging, system, runtime）
├── desktop/                    # Tauri 桌面应用
│   ├── src/                    # Svelte 5 前端
│   │   ├── pages/              # 5 页面（Process/Tasks/Notes/Settings/Collections）
│   │   └── lib/                # API 适配器 / stores / 组件
│   └── src-tauri/              # Rust 后端
│       └── src/
│           ├── engine_manager.rs  # Python 侧车进程管理
│           ├── protocol.rs        # Content-Length 帧协议
│           └── process_tree.rs    # Windows Job Object
├── runtime/                    # 组件清单（component manifests）
├── templates/                  # 笔记模板（YAML）
├── tests/                      # ~47 个测试文件（584+ 测试）
├── docs/                       # 设计文档
└── output/                     # 生成的笔记输出
```

## Tech Stack

- **Desktop Shell**: Tauri 2 + Rust
- **Frontend**: Svelte 5 + TypeScript + Vite
- **Engine**: Python 3.10+
- **Transcription**: faster-whisper (CTranslate2)
- **Download**: yt-dlp
- **Note Generation**: OpenAI-compatible API (mimo / 阿里云百炼 / custom)
- **Visual Recognition**: Multi-modal LLM + PaddleOCR
- **Collection Management**: SQLite-backed video collections
- **Obsidian Sync**: Markdown notes auto-archived to vault
- **Packaging**: Tauri bundler (MSI / NSIS)
- **IPC**: Content-Length framed JSON-RPC 2.0 over stdin/stdout
- **Process Management**: Windows Job Object
- **Testing**: pytest

## Key Architecture

- **No more PySide6 or src/core/**: Both removed in v1.2.0 refactor
- **Tauri as primary desktop entry**: `VideoNotesAI.exe` or `npm run tauri dev`
- **CLI fallback**: `python main.py <url>` for headless/server use
- **Dev mode**: `cd desktop && npm run tauri dev` — auto-starts Python sidecar
- **API-first design**: All business logic exposed via `src/api/` JSON-RPC protocol
- **Component-based distribution**: CUDA/OCR/Whisper installed as independent components
- **Sidecar protocol**: Python engine runs as child process, communicates via Content-Length framed JSON-RPC 2.0 over stdin/stdout
- **Process tree safety**: Windows Job Object ensures no orphan processes on crash
- **DDD layering**: Domain → Application (via Ports) → Infrastructure (implements Ports)
- **Strict API boundary**: Frontend never accesses DB, filesystem, or Python internals directly

## Key Commands

### Run
```bash
# Tauri dev (auto-starts Python engine)
cd desktop && npm run tauri dev

# CLI single
python main.py <url|file> --model large-v3 --output ./output

# CLI with vision
python main.py <url> --vision --vision-model qwen-vl-plus
```

### Test
```bash
# Full suite (~584 passing)
python -m pytest tests/ -v --tb=short

# Engine API smoke tests (9 tests, no external deps)
python -m pytest tests/test_engine_api_smoke.py -v

# Compile check
python -m compileall src tests
```

### Build
```bash
cd desktop && npx tauri build
# → dist/Video Notes AI_1.2.0_x64_en-US.msi
# → dist/Video Notes AI_1.2.0_x64-setup.exe
```

## Coding Style

- 4-space indentation
- Type hints on public functions and parameters
- `snake_case` for functions/variables, `PascalCase` for classes
- Follow existing patterns in src/ modules
- No linter/formatter configured — keep PEP 8

## Testing Philosophy

- All tests target module-level behaviour
- New features should include test coverage for main success path + key edge cases
- Tests must not depend on network access or real API keys
- Vision tests use mocked providers
- Engine API tests use monkeypatched dispatch/response
