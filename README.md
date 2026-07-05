# video-notes-ai

将视频（在线链接或本地文件）自动转录为结构化学习笔记，归档到 Obsidian。

**桌面应用**：Tauri 2 + Svelte 5 + Rust | **引擎**：Python 3.10+ | 桌面版本：**V1.5.0** | 引擎包版本：**V1.2.1**

## 产品形态

```
导入视频 / 课程
  → 高质量转录（faster-whisper）
  → 可选：OCR 文字识别 + 视觉理解
  → AI 生成结构化笔记（带截图引用）
  → 归档到 Obsidian vault
  → 在 Obsidian 中完成知识管理
```

## 功能特性

- **桌面应用**：Tauri 原生桌面壳 + Svelte 5 响应式界面
- **CLI 模式**：完整命令行参数，适合批量处理和脚本集成
- **在线视频**：支持 YouTube、Bilibili 等平台（yt-dlp）
- **本地文件**：支持 mp4 / mkv / avi / mov / flv / webm 等格式
- **AI 转录**：faster-whisper，自动 GPU 加速，CUDA 失败降级 CPU
- **模板化笔记**：8 个内置模板（学习 / 会议 / 教程 / 面试等），支持自定义
- **课程合集**：文件夹/playlist 导入，自动组织视频，生成课程总览
- **断点续跑**：任意阶段中断后可恢复，不重复处理
- **任务队列**：enqueue / status / resume / cancel 完整生命周期
- **多供应商配置**：支持多个 LLM profile，独立配置笔记生成和视觉识别
- **视觉识别**：可选关键帧视觉分析 + OCR 文字提取
- **字幕导出**：SRT / ASS / 纯文本格式
- **Obsidian 归档**：自动将笔记复制到 Obsidian vault

## 快速开始

### 环境要求
- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html)（必须，用于音频提取和视频截图）
- Node.js 18+ + Rust（仅开发，运行无需）
- NVIDIA GPU（可选，有 CUDA 时自动启用 GPU 加速）

### 开发运行

```bash
# 1. 安装 Python 依赖
pip install -e ".[vision]"

# 2. 安装前端依赖
cd desktop && npm install

# 3. 启动 Tauri 开发模式
npm run tauri dev
```

### CLI 模式（无需 Tauri）

```bash
# 处理视频
python main.py "https://www.bilibili.com/video/BVxxx"
python main.py "D:\videos\lecture.mp4" --title "课程笔记" --template study

# 启用视觉识别
python main.py <url> --vision --vision-model qwen-vl-plus

# 任务管理
python main.py --job-list
python main.py --job-status 1
python main.py --resume 1
```

## 架构

```
┌─────────────────────────────────────────────────────┐
│  Tauri Desktop Shell (Rust)                         │
│  ├─ Svelte 5 UI (Process / Tasks / Notes / ...)    │
│  └─ Engine Manager — Python 侧车进程管理            │
│       │ Content-Length framed JSON-RPC 2.0 over stdio│
├─────────────────────────────────────────────────────┤
│  Python Engine                                      │
│  ├─ src/api/     — JSON-RPC 2.0 API 层              │
│  ├─ src/application/ — 管线编排 / 服务 / LLM        │
│  ├─ src/domain/  — 领域模型 / 接口                   │
│  └─ src/infrastructure/ — DB / 转录 / 视频 / Provider│
└─────────────────────────────────────────────────────┘
```

## 项目结构

```
video-notes-ai/
├── main.py                     # CLI / Engine 入口
├── src/
│   ├── api/                    # JSON-RPC 2.0 引擎 API
│   │   ├── protocol/           # Content-Length 帧协议 / dispatcher
│   │   ├── dto/                # Pydantic v2 数据传输对象
│   │   ├── handlers/           # RPC 方法处理器
│   │   ├── event_journal.py    # 持久化事件日志
│   │   └── server.py           # 服务器主循环
│   ├── engine.py               # Tauri sidecar 入口
│   ├── application/            # 应用层
│   │   ├── pipeline/           # 视频处理管线 + 8 个阶段
│   │   ├── services/           # PipelineOrchestrator / JobQueue
│   │   ├── llm/                # MAP / REDUCE 笔记生成
│   │   ├── vision/             # 视觉理解
│   │   ├── notes/              # 笔记生成编排
│   │   └── collections/        # 合集管理
│   ├── domain/                 # 领域层
│   │   ├── models/             # 领域实体
│   │   ├── interfaces/         # 纯接口（Ports）
│   │   └── types.py            # PipelineRequest / PipelineResult
│   ├── infrastructure/         # 基础设施
│   │   ├── db/                 # SQLite repositories
│   │   ├── transcription/      # faster-whisper / whisper.cpp
│   │   ├── video/              # yt-dlp / FFmpeg / OCR
│   │   ├── providers/          # OpenAI / DashScope / Mimo
│   │   ├── artifacts/          # Obsidian 归档
│   │   └── system/             # Component Manager
│   ├── app/                    # CLI 入口
│   ├── config/                 # 配置管理
│   └── utils/                  # 工具函数
├── desktop/
│   ├── src/                    # Svelte 5 前端
│   │   ├── pages/              # 5 页面（Process/Tasks/Notes/Settings/Collections）
│   │   └── lib/                # API 适配器 / stores / 组件
│   ├── src-tauri/              # Rust 后端
│   │   ├── src/
│   │   │   ├── engine_manager.rs  # Python 侧车管理
│   │   │   ├── protocol.rs        # Content-Length 帧协议
│   │   │   └── process_tree.rs    # Windows Job Object
│   │   └── tauri.conf.json
│   └── package.json
├── runtime/                    # 组件清单
├── templates/                  # 笔记模板（YAML）
├── tests/                      # ~47 个测试文件
│   └── test_engine_api_smoke.py  # 引擎 API 冒烟测试
├── docs/                       # 设计文档
└── output/                     # 生成的笔记
```

## 技术栈

| 层 | 技术 |
|------|--------|
| 桌面壳 | Tauri 2 + Rust |
| 前端 | Svelte 5 + TypeScript + Vite |
| 业务引擎 | Python 3.10+ |
| API 协议 | JSON-RPC 2.0 over stdin/stdout (Content-Length framed) |
| 转录 | faster-whisper (CTranslate2) |
| LLM | OpenAI 兼容 API（mimo / 阿里云百炼 / 自定义） |
| 视觉 | 多模态 LLM + PaddleOCR |
| 下载 | yt-dlp |
| 视频处理 | FFmpeg |
| 持久化 | SQLite + 版本化迁移 |
| 进程管理 | Windows Job Object |
| 测试 | pytest (584+ 测试) |

## CLI 参数速查

### 核心处理
| 参数 | 说明 |
|------|------|
| `input` | 视频 URL 或本地文件路径 |
| `--output` | 输出目录，默认 `./output` |
| `--title` | 视频标题（留空自动检测） |
| `--model` | Whisper 模型，默认 `large-v3` |
| `--model-dir` | 本地模型目录 |
| `--gpt-model` | AI 模型名称 |
| `--api-key` | API Key |
| `--base-url` | 自定义 API 端点 |
| `--temperature` | AI 温度 0.0–2.0，默认 0.3 |
| `--frame-interval` | 截图间隔（秒），0 禁用 |
| `--frame-mode` | 截图模式：auto / fixed / disabled |
| `--max-frames` | 自动截图最多保留数 |

### 视觉识别
| 参数 | 说明 |
|------|------|
| `--vision` | 启用视觉识别 |
| `--vision-model` | 视觉模型名称 |
| `--ocr` | 启用 OCR 文字识别 |

### 任务管理
| 参数 | 说明 |
|------|------|
| `--resume <id>` | 断点续跑 |
| `--job-list` | 查看所有任务 |
| `--job-status <id>` | 查看任务详情 |

### 模板
| 参数 | 说明 |
|------|------|
| `--template <id/path>` | 指定笔记模板 |
| `--template-list` | 列出所有可用模板 |
| `--template-preview <id>` | 预览模板详情 |
| `--template-recommend <q>` | 智能推荐模板 |

### 合集
| 参数 | 说明 |
|------|------|
| `--collection <id>` | 关联到指定合集 |
| `--collection-create <name>` | 创建合集 |
| `--collection-list` | 列出所有合集 |
| `--collection-status <id>` | 合集状态 |
| `--collection-export <id>` | 导出合集 |
| `--folder <path>` | 从文件夹导入 |
| `--playlist <url>` | 从 playlist 导入 |

### 其他
| 参数 | 说明 |
|------|------|
| `--doctor` | 运行环境诊断 |
| `--issue-bundle` | 生成问题报告包 |
| `--with-citations` | 笔记中附带来源引用 |
| `--smart-summary` | 长文智能总结 |

## 环境变量

在 `.env` 文件中配置：

```bash
MIMO_API_KEY=your-mimo-key
DASHSCOPE_API_KEY=your-dashscope-key
```

## FFmpeg

FFmpeg 是必须的系统依赖。自动扫描 PATH 及常见安装路径。如未安装：

```bash
winget install Gyan.FFmpeg
```

## 依赖拆分

```bash
pip install -e .               # 核心（CLI + API）
pip install -e ".[vision]"     # + 视觉增强
pip install -e ".[ocr]"        # + OCR（PaddleOCR CPU）
pip install -e ".[ocr-gpu]"    # + OCR（PaddleOCR GPU）
pip install -e ".[cuda]"       # + CUDA 加速
pip install -e ".[dev]"        # + 开发工具
```
