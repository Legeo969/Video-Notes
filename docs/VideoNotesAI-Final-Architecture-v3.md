# Video Notes AI — 最终架构与迁移执行设计文档 v3.0

> 文档状态：最终方向 / OpenCode 执行规范  
> 适用平台：Windows 桌面端优先  
> GUI：Tauri 2 + Svelte 5 + TypeScript  
> 业务引擎：Python Sidecar  
> 目标：形成可正式安装、真实处理视频、可恢复任务、可独立更新组件的产品架构

---

## 0. 文档定位

本文件定义 Video Notes AI 的**最终产品架构**，同时规定从当前 PySide6 + Python 单进程桌面应用迁移到最终形态的执行顺序、边界约束和验收门禁。

本方案不是界面 Demo，也不是仅能展示进度的壳。最终产品必须真实运行现有完整处理链：

```text
本地视频 / 音频 / 视频 URL
→ 媒体解析与下载
→ FFmpeg 音频提取
→ faster-whisper CPU / CUDA 转录
→ 智能抽帧
→ OCR
→ 视觉理解
→ 语音与视觉时间线融合
→ MAP / REDUCE 笔记生成
→ Markdown / 字幕 / 图片 / 来源索引输出
→ 笔记编辑、合集管理和任务断点恢复
```

迁移期间允许保留旧 PySide6 GUI 和 `src/core` 作为回滚路径，但最终完成态必须删除二者。

---

# 1. 最终目标

## 1.1 产品目标

最终用户获得一个无需预装 Python、Node.js、Rust、CUDA Toolkit 或开发工具的 Windows 桌面应用，具备：

1. Tauri 原生桌面壳；
2. Svelte 5 正式用户界面；
3. 独立 Python 业务引擎；
4. Whisper CPU / CUDA；
5. OCR CPU / GPU；
6. 视觉模型和 LLM Provider；
7. SQLite 任务、笔记和合集数据库；
8. 阶段级断点续跑；
9. Shell、Engine、组件和模型的独立升级；
10. 安装、更新失败回滚、诊断和卸载能力。

## 1.2 架构目标

最终依赖链固定为：

```text
Svelte Presentation
        ↓ Tauri Commands / Events
Rust Desktop Core
        ↓ Framed JSON-RPC over stdio
Python API
        ↓
Application Use Cases
        ↓
Application Ports / Domain Interfaces
        ↑
Infrastructure Adapters
```

任何 UI、Rust 或外部插件都不得绕过 Engine API 直接访问：

- SQLite Repository；
- PipelineOrchestrator；
- JobQueue；
- StageRunner；
- ProviderFactory；
- `src.core`；
- Python 内部 dataclass。

## 1.3 最终完成定义

只有同时满足以下条件，才算完成最终架构：

- 对外只发布 Tauri GUI；
- `src/gui/` 已删除；
- PySide6 依赖已删除；
- `src/core/` 已删除；
- 正式代码和测试代码无 `src.core` 导入；
- Domain 不依赖 Application、Infrastructure、API 或 GUI；
- Application 通过 Ports 使用 Infrastructure；
- Tauri 只通过版本化 Engine API 操作业务；
- Python Engine 可独立运行完整真实任务；
- 暂停、取消、继续、重试语义保持稳定；
- 旧 SQLite、旧配置、旧 `.jobs` 和旧 stage manifest 可迁移或兼容；
- OCR、CUDA 和模型可独立安装、更新、回滚；
- UI 更新不重打 Python/CUDA；
- Python 业务代码更新不重打 CUDA/OCR；
- 用户机器不依赖系统 Python；
- 安装、升级、回滚、卸载和离线启动通过正式验证。

---

# 2. 当前基线

基于当前源码静态审计：

```text
src/core Python 文件：100
生产代码中引用 src.core 的文件：62
测试代码中引用 src.core 的文件：39
总引用文件：101
测试函数：约 811
pytest 收集结果：799 tests collected，当前环境有 2 个 PySide6 收集错误
```

当前已存在并必须复用的核心能力包括：

- `PipelineOrchestrator`
- `JobQueue`
- `StageRunner`
- `FileManifestStore`
- SQLite repositories
- Whisper Engine
- Frame Extractor
- OCR isolated worker
- Vision Provider
- MAP / REDUCE
- Artifact Writer
- Collection Manager
- Diagnostics
- 当前断点续跑格式

迁移不得重写一套平行业务管线。

---

# 3. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                     Video Notes AI                          │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Svelte 5 + TypeScript                                │  │
│  │ Process / Tasks / Notes / Collections / Settings     │  │
│  │ Stores / Components / Generated API Types            │  │
│  └───────────────────┬───────────────────────────────────┘  │
│                      │ Tauri invoke / event                 │
│  ┌───────────────────▼───────────────────────────────────┐  │
│  │ Rust Desktop Core                                    │  │
│  │ Window / Dialog / Notification / Engine Manager      │  │
│  │ Process Tree / Component Manager / Updater / Secrets │  │
│  └───────────────────┬───────────────────────────────────┘  │
│                      │ Content-Length framed JSON-RPC       │
│  ┌───────────────────▼───────────────────────────────────┐  │
│  │ Python Engine                                        │  │
│  │ API / Application / Domain / Infrastructure          │  │
│  │ Pipeline / SQLite / Whisper / OCR / Vision / LLM     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## 3.1 唯一通信链

```text
Svelte → Rust → Python
Python → Rust → Svelte
```

禁止：

- Svelte 直接连接 Python；
- Svelte 打开本地 WebSocket；
- Svelte 直接读写 SQLite；
- Svelte 获得任意 Shell 权限；
- Rust 直接调用 Python 内部模块；
- Python 向 stdout 写普通日志。

## 3.2 技术选型

| 领域 | 最终选择 |
|---|---|
| 桌面壳 | Tauri 2 |
| 前端 | Svelte 5 + TypeScript + Vite SPA |
| 状态管理 | Svelte stores |
| 样式 | CSS variables + scoped CSS |
| Rust/Python IPC | Content-Length framed JSON-RPC over stdin/stdout |
| 前端/Rust IPC | Tauri Commands + Tauri Events |
| Python API 模型 | Pydantic v2 |
| TypeScript 类型 | 从 JSON Schema 自动生成 |
| Python 并发 | 后台任务执行器，首版单活动任务 |
| 持久化 | SQLite + 版本化 migrations |
| 密钥存储 | OS Credential Store，通过 SecretStore Port |
| Shell 更新 | Tauri Updater，签名校验 |
| Engine/组件更新 | 自定义 Component Manager |
| 进程清理 | Windows Job Object |
| 日志 | Python/Rust 分离日志 + 统一问题报告 |

---

# 4. 最终目录结构

```text
video-notes-ai/
├── pyproject.toml
├── README.md
├── src/
│   ├── __init__.py
│   ├── engine.py
│   │
│   ├── api/
│   │   ├── protocol/
│   │   │   ├── framing.py
│   │   │   ├── dispatcher.py
│   │   │   ├── errors.py
│   │   │   └── version.py
│   │   ├── dto/
│   │   │   ├── system.py
│   │   │   ├── jobs.py
│   │   │   ├── notes.py
│   │   │   ├── settings.py
│   │   │   ├── collections.py
│   │   │   └── diagnostics.py
│   │   ├── handlers/
│   │   │   ├── system.py
│   │   │   ├── process.py
│   │   │   ├── notes.py
│   │   │   ├── settings.py
│   │   │   ├── collections.py
│   │   │   └── diagnostics.py
│   │   ├── event_journal.py
│   │   └── server.py
│   │
│   ├── application/
│   │   ├── use_cases/
│   │   ├── ports/
│   │   ├── pipeline/
│   │   ├── services/
│   │   ├── llm/
│   │   ├── vision/
│   │   ├── fusion/
│   │   ├── speech/
│   │   ├── notes/
│   │   ├── collections/
│   │   ├── provenance/
│   │   └── diagnostics/
│   │
│   ├── domain/
│   │   ├── models/
│   │   ├── events/
│   │   ├── value_objects/
│   │   ├── interfaces/
│   │   └── errors.py
│   │
│   ├── infrastructure/
│   │   ├── db/
│   │   ├── artifacts/
│   │   ├── media/
│   │   ├── transcription/
│   │   ├── video/
│   │   ├── ocr/
│   │   ├── providers/
│   │   ├── secrets/
│   │   └── system/
│   │
│   ├── bootstrap/
│   │   ├── container.py
│   │   ├── engine_container.py
│   │   └── cli_container.py
│   │
│   ├── cli/
│   ├── config/
│   ├── plugins/
│   └── utils/
│
├── desktop/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── App.svelte
│   │   ├── lib/
│   │   │   ├── api/
│   │   │   ├── stores/
│   │   │   ├── components/
│   │   │   ├── schemas/
│   │   │   └── types/
│   │   └── pages/
│   │       ├── Process.svelte
│   │       ├── Tasks.svelte
│   │       ├── Notes.svelte
│   │       ├── Collections.svelte
│   │       └── Settings.svelte
│   │
│   └── src-tauri/
│       ├── Cargo.toml
│       ├── build.rs
│       ├── tauri.conf.json
│       ├── capabilities/
│       └── src/
│           ├── main.rs
│           ├── lib.rs
│           ├── engine_manager.rs
│           ├── protocol.rs
│           ├── process_tree.rs
│           ├── component_manager.rs
│           ├── updater.rs
│           ├── secret_store.rs
│           └── commands/
│
├── runtime/
│   ├── manifests/
│   ├── base/
│   ├── transcription-cpu/
│   ├── transcription-cuda/
│   ├── ocr-cpu/
│   ├── ocr-gpu/
│   └── tools/
│
├── migrations/
├── schemas/
├── scripts/
├── tests/
└── dist/
```

最终目录中不再存在：

```text
src/core/
src/gui/
PySide6
旧 GUI workers/controllers
旧 GUI 专用 viewmodels
```

---

# 5. DDD 与依赖规则

## 5.1 Domain

Domain 只能包含：

- 领域实体；
- 值对象；
- 领域事件；
- 纯接口；
- 领域错误；
- 与框架无关的规则。

Domain 禁止导入：

```text
application
infrastructure
api
desktop
PySide6
pydantic
sqlite
requests
aiohttp
```

## 5.2 Application

Application 包含：

- 用例；
- 管线编排；
- 任务生命周期；
- Ports；
- 业务服务；
- DTO 到领域对象的转换边界。

Application 可以依赖 Domain，但不得直接依赖具体数据库、Provider、FFmpeg、Whisper 或 OCR 实现。

示例 Ports：

```text
JobRepository
NoteRepository
CollectionRepository
MediaGateway
TranscriptionGateway
FrameExtractionGateway
OcrGateway
VisionGateway
LlmGateway
ArtifactGateway
SecretStore
Clock
EventPublisher
```

## 5.3 Infrastructure

Infrastructure 实现 Application Ports：

```text
SqliteJobRepository implements JobRepository
FasterWhisperAdapter implements TranscriptionGateway
PaddleOcrAdapter implements OcrGateway
FfmpegMediaAdapter implements MediaGateway
OpenAICompatAdapter implements LlmGateway
```

Infrastructure 不得导入：

```text
api
desktop
gui
application.use_cases
```

允许导入：

```text
domain
application.ports
```

## 5.4 Bootstrap

具体实现的组装只允许发生在组合根：

```text
src/bootstrap/engine_container.py
src/bootstrap/cli_container.py
```

Provider Factory、Repository Factory 和组件探测不放在 Domain 或 Application。

## 5.5 架构自动检查

新增测试：

```text
test_domain_has_no_upward_dependencies
test_application_uses_only_ports
test_infrastructure_has_no_api_or_gui_imports
test_no_src_core_imports
test_no_pyside6_imports_after_final_cutover
```

---

# 6. Python Engine API

## 6.1 API 原则

Engine API 是产品唯一业务接口，具有独立版本：

```text
protocol_version
api_schema_version
engine_version
```

内部数据库字段、dataclass 和 Repository 返回值不得直接暴露给前端。

转换链：

```text
Domain Model
→ API DTO
→ JSON Schema
→ Generated TypeScript Type
```

## 6.2 正式 RPC 方法

### System

```text
system.ping
system.info
system.shutdown
system.snapshot
system.capabilities
```

### Process

```text
process.start
process.pause
process.cancel
process.resume
process.retry
process.list
process.get
process.delete
process.permanent_clean
process.open_output
process.events_since
```

### Notes

```text
notes.list
notes.get
notes.update
notes.delete
notes.search
notes.open
notes.reveal
```

### Settings

```text
settings.get
settings.update
settings.secret.set
settings.secret.delete
settings.providers.list
settings.providers.create
settings.providers.update
settings.providers.delete
settings.providers.set_active
settings.providers.test
settings.models.scan
settings.templates.list
```

### Collections

```text
collection.list
collection.get
collection.create
collection.update
collection.delete
collection.import_folder
collection.add_items
collection.remove_items
collection.batch_process
collection.export
```

### Diagnostics

```text
doctor.run
diagnostics.bundle
logs.tail
components.list
components.install
components.remove
components.verify
```

---

# 7. IPC 协议

## 7.1 传输方式

Rust 与 Python 采用：

```text
Content-Length framed JSON-RPC 2.0 over stdin/stdout
```

示例：

```text
Content-Length: 184\r\n
\r\n
{"jsonrpc":"2.0","protocol_version":1,"id":"req-001","method":"system.info","params":{}}
```

禁止使用简单 JSON Lines 作为正式协议，避免第三方库误写、多行异常和编码问题污染边界。

## 7.2 请求信封

```json
{
  "jsonrpc": "2.0",
  "protocol_version": 1,
  "id": "01J...",
  "method": "process.start",
  "params": {},
  "idempotency_key": "01J..."
}
```

## 7.3 响应信封

```json
{
  "jsonrpc": "2.0",
  "protocol_version": 1,
  "id": "01J...",
  "result": {}
}
```

## 7.4 错误信封

```json
{
  "jsonrpc": "2.0",
  "protocol_version": 1,
  "id": "01J...",
  "error": {
    "code": "JOB_ALREADY_RUNNING",
    "message": "已有任务正在运行",
    "details": {},
    "retryable": false
  }
}
```

## 7.5 事件信封

```json
{
  "jsonrpc": "2.0",
  "protocol_version": 1,
  "method": "job.progress",
  "params": {
    "event_id": 108,
    "job_id": 42,
    "run_id": "01J...",
    "attempt": 2,
    "stage": "transcribe",
    "stage_label": "Whisper 转录",
    "stage_progress": 0.63,
    "overall_progress": 0.28,
    "message": "正在使用 CUDA 转录",
    "timestamp": "2026-07-04T20:30:00Z"
  }
}
```

## 7.6 协议约束

- stdout 只允许协议帧；
- stderr 用于普通日志；
- 所有写入通过线程安全 ProtocolWriter；
- 单帧最大 8 MiB；
- 请求必须有超时；
- `process.start` 必须支持幂等键；
- 取消请求必须可重复调用；
- 未识别方法返回标准错误；
- 协议版本不兼容时拒绝启动；
- Engine 启动后首先完成 handshake；
- Rust 维护 pending request map；
- Python 事件包含单调递增 `event_id`。

---

# 8. Rust Desktop Core

Rust 层只负责桌面能力和进程边界，不实现业务规则。

## 8.1 Engine Manager

职责：

- 解析私有 Python runtime；
- 启动 Python Engine；
- 建立 stdin/stdout/stderr；
- 解析协议帧；
- 映射请求和响应；
- 转发事件；
- 请求超时；
- 引擎健康检查；
- 崩溃检测；
- 优雅关闭；
- 强制终止完整进程树；
- 引擎版本兼容检查。

## 8.2 Windows 进程树

Python Engine 及其 FFmpeg、yt-dlp、OCR worker 等后代进程必须加入同一个 Windows Job Object。

应用退出或 Engine 崩溃时：

```text
关闭 Job Object
→ 终止 Python
→ 终止 FFmpeg
→ 终止 OCR worker
→ 终止其他后代进程
```

不得仅结束 Python PID。

## 8.3 Tauri 权限

前端只获得必要权限：

- 窗口；
- 对话框；
- 通知；
- 剪贴板；
- 打开外部链接；
- 最小文件访问。

前端不得获得：

- 任意 Shell；
- 任意进程启动；
- 任意文件系统根目录访问；
- Python runtime 路径；
- API Key 明文；
- updater 私钥。

Capabilities 按窗口拆分并执行最小授权。

---

# 9. 任务生命周期

## 9.1 状态机

```text
QUEUED
  ↓
RUNNING
  ├──→ SUCCEEDED
  ├──→ PAUSE_REQUESTED → PAUSED
  ├──→ CANCEL_REQUESTED → CANCELLED
  ├──→ FAILED
  └──→ INTERRUPTED → RESUMABLE
```

恢复：

```text
PAUSED / RESUMABLE
→ RESUMING
→ RUNNING
```

## 9.2 操作语义

### Pause

```text
停止当前执行
保留数据库记录
保留 .jobs 工作目录
保留 stage manifest
保留已完成阶段产物
状态变为 PAUSED
```

### Cancel

```text
停止当前执行
清理临时工作目录
保留任务历史记录
状态变为 CANCELLED
不删除用户已生成的正式输出，除非明确确认
```

### Resume

```text
验证工作目录
读取 manifest
验证阶段产物
跳过已完成阶段
从首个未完成或无效阶段开始
```

### Retry

```text
保留原任务历史
创建新的 run_id / attempt
默认复用可验证的已有阶段
允许用户选择“从头重跑”
```

### Delete

```text
删除任务历史记录
默认不删除用户输出
仅允许终态任务执行
```

### Permanent Clean

```text
删除任务历史
删除 .jobs 工作目录
删除本任务生成的输出
必须二次确认并展示待删除路径
```

---

# 10. 断点续跑契约

## 10.1 Stage Manifest

每个阶段保存：

```json
{
  "schema": 2,
  "stage": "transcribe",
  "status": "completed",
  "input_hash": "...",
  "outputs": {},
  "artifact_files": [],
  "completed_at": "...",
  "engine_version": "...",
  "stage_version": 2
}
```

## 10.2 恢复规则

显式点击“继续任务”时：

1. `status == completed`；
2. 产物存在；
3. 产物非空；
4. 可反序列化；
5. 阶段版本兼容；

满足后，完成阶段视为权威断点。

不得因为以下变化重跑媒体解析或 Whisper：

- API Key 改动；
- LLM 模型改动；
- 笔记模板改动；
- Provider 配置改动；
- UI 设置改动。

只有与阶段真实输入相关的变化才允许使该阶段失效。

## 10.3 兼容旧断点

新 Engine 必须识别旧 manifest：

```text
读取旧 schema
→ 迁移为内存中的新表示
→ 验证产物
→ 继续任务
```

不得仅因 schema 升级而重新提取音频和重新运行 Whisper。

---

# 11. 崩溃恢复

SQLite 不能恢复 Python 线程、CUDA 上下文或 FFmpeg 句柄，因此“自动重启”定义为持久断点恢复，不是内存恢复。

Engine 异常退出：

```text
Rust 检测退出
→ 终止 Job Object
→ 重启 Engine
→ 扫描 RUNNING / PAUSE_REQUESTED 任务
→ 检查 heartbeat 和 engine_pid
→ 标记 INTERRUPTED
→ 验证 manifests
→ 标记 RESUMABLE
→ UI 提示用户继续
```

任务表最终应包含：

```text
run_id
attempt
engine_pid
heartbeat_at
lease_expires_at
last_event_id
interrupted_reason
resume_from_stage
```

---

# 12. Event Journal

为解决 UI 重载、Rust 重连和 Engine 重启后的事件丢失，增加事件日志。

事件分类：

```text
job.created
job.started
job.stage_started
job.progress
job.stage_completed
job.paused
job.cancelled
job.failed
job.interrupted
job.resumable
job.completed
artifact.created
component.changed
```

规则：

- 状态变化事件持久化；
- 高频 progress 事件节流；
- 每个 job 维护单调 `event_id`；
- UI 启动后先调用 `system.snapshot`；
- 再调用 `process.events_since(last_event_id)`；
- 前端 store 只把后端状态视为权威状态。

---

# 13. 数据与迁移

## 13.1 用户数据目录

统一通过 App Data 路径管理：

```text
data/
├── app.db
├── jobs/
├── config/
├── templates/
├── logs/
├── cache/
├── models/
├── components/
└── backups/
```

用户输出目录继续由用户配置，不强制迁入 App Data。

## 13.2 必须兼容的数据

- SQLite 数据库；
- `.jobs` 工作目录；
- stage manifests；
- Provider 配置；
- 模型目录；
- 笔记和字幕输出；
- 合集记录；
- 自定义模板；
- 日志；
- 插件配置。

## 13.3 数据库迁移

所有 schema 变化必须：

```text
检测当前 schema
→ 创建备份
→ 在事务中迁移
→ 执行完整性检查
→ 提交
```

失败时：

```text
回滚事务
→ 恢复备份
→ 保留迁移日志
→ 禁止继续启动写操作
```

不得在没有 migration 和 rollback 测试时直接修改生产表。

---

# 14. 设置和密钥安全

## 14.1 普通设置

可返回给前端：

- Provider 名称；
- Base URL；
- 模型；
- 输出目录；
- 模型目录；
- OCR/视觉开关；
- 并发数；
- 模板；
- UI 偏好。

## 14.2 Secret

API Key 不得通过 `settings.get` 返回明文。

返回：

```json
{
  "api_key_configured": true,
  "api_key_preview": "sk-****8fa2"
}
```

操作：

```text
settings.secret.set
settings.secret.delete
```

SecretStore 使用 OS Credential Store。CLI 可使用同一 SecretStore，或通过环境变量覆盖。

## 14.3 脱敏

以下位置必须统一脱敏：

- Python 日志；
- Rust 日志；
- RPC trace；
- 问题报告；
- 错误堆栈；
- Provider 测试结果；
- UI 通知。

---

# 15. 正式 UI 功能

## 15.1 Process

- 本地视频/音频；
- 视频 URL；
- Cookie；
- 标题；
- 输出目录；
- Whisper 模型、目录、语言、beam、VAD；
- 抽帧模式、间隔、最大帧数；
- OCR；
- 视觉理解；
- Provider、模型和模板；
- 开始、暂停、取消；
- 阶段进度、总体进度；
- 日志摘要；
- 完成后打开笔记。

## 15.2 Tasks

- 列表和筛选；
- 详情；
- 状态、阶段、进度；
- 开始/结束时间；
- 错误详情；
- 暂停、取消、继续、重试；
- 删除、永久清理；
- 打开输出；
- 打开笔记；
- 显示复用断点阶段。

## 15.3 Notes

- 笔记列表；
- Markdown 渲染；
- 源码编辑；
- 保存；
- 搜索；
- 复制；
- 删除；
- 打开文件；
- 打开目录；
- 任务和合集关联；
- 图片和字幕关联。

Markdown 渲染必须执行 HTML 清理并禁止任意脚本。

## 15.4 Settings

- Provider CRUD；
- 激活 Provider；
- Secret 更新；
- Base URL；
- 模型和视觉模型；
- Whisper 模型目录；
- OCR；
- 输出目录；
- 模板；
- 并发数；
- CUDA、FFmpeg、OCR 检查；
- Provider 测试；
- 问题报告；
- 组件管理。

## 15.5 Collections

- 列表和详情；
- 创建、编辑、删除；
- 导入文件夹；
- 添加/移除条目；
- 批量处理；
- 批量进度；
- Markdown 导出；
- 打开输出。

---

# 16. Python Runtime 与组件化

## 16.1 最终分发原则

用户机器不得依赖系统 Python，也不得在首次启动时执行不受控在线 `pip install`。

正式分发采用：

```text
Tauri Shell
+
Private Python Runtime
+
Engine App
+
Versioned Components
```

## 16.2 发布目录

```text
VideoNotesAI/
├── VideoNotesAI.exe
├── engine/
│   ├── runtime/
│   ├── app/
│   ├── manifests/
│   └── engine.json
├── components/
│   ├── transcription-cpu/
│   ├── transcription-cuda/
│   ├── ocr-cpu/
│   ├── ocr-gpu/
│   └── tools/
├── resources/
└── updater/
```

## 16.3 组件划分

```text
base-engine
transcription-cpu
transcription-cuda
ocr-cpu
ocr-gpu
ffmpeg-tools
whisper-models
ocr-models
```

组件清单：

```json
{
  "component": "transcription-cuda",
  "version": "1.2.0",
  "platform": "windows-x86_64",
  "engine_api": 1,
  "sha256": "...",
  "signature": "...",
  "requires": {
    "base-engine": ">=2.0.0 <3.0.0"
  }
}
```

## 16.4 安装策略

提供两种发行版：

```text
Standard
Shell + Base Engine + CPU Transcription + FFmpeg

Portable GPU
Shell + Base Engine + CUDA Transcription + OCR GPU + FFmpeg
```

其他组件可由 Component Manager 安装。

## 16.5 PyInstaller 退出条件

迁移初期允许继续复用现有稳定 Engine Runtime。

只有新的 Private Python Runtime 在干净 Windows 环境通过以下验证后，才能删除 PyInstaller：

- Engine 启动；
- Whisper CPU；
- Whisper CUDA；
- OCR CPU；
- OCR GPU；
- FFmpeg；
- yt-dlp；
- Provider；
- 断点恢复；
- 安装；
- 更新；
- 回滚；
- 卸载。

---

# 17. 更新和回滚

## 17.1 版本维度

独立维护：

```text
shell_version
engine_version
protocol_version
api_schema_version
db_schema_version
component_versions
model_versions
```

## 17.2 Shell 更新

Tauri Updater 负责 Shell 安装包更新，要求：

- SemVer；
- 签名；
- HTTPS；
- 更新说明；
- 失败提示；
- 安装前关闭 Engine；
- 安装后兼容检查。

## 17.3 Engine 和组件更新

Component Manager 流程：

```text
下载到临时目录
→ SHA-256 校验
→ 签名校验
→ 兼容性校验
→ 停止 Engine
→ 原子切换 current 指针
→ 启动自检
→ 成功后清理旧版本
```

失败：

```text
停止新版本
→ 恢复旧 current
→ 启动旧 Engine
→ 写入回滚报告
```

## 17.4 保留策略

至少保留：

- 当前版本；
- 上一稳定版本；
- 最近一次成功配置；
- 更新前数据库备份。

---

# 18. 可观测性与诊断

## 18.1 日志

```text
shell.log
engine.log
pipeline.log
component-manager.log
update.log
session-*.log
```

所有日志：

- 轮转；
- 限制大小；
- UTF-8；
- 包含版本；
- 包含 session_id；
- 包含 job_id/run_id；
- 脱敏。

## 18.2 问题报告

问题报告包包含：

- Shell/Engine/Protocol/DB 版本；
- OS、CPU、GPU；
- CUDA、FFmpeg、OCR 检测；
- 组件清单；
- 最近日志；
- 任务状态；
- manifest 摘要；
- 配置摘要；
- 崩溃报告。

不得包含：

- API Key；
- Cookie；
- 完整用户视频；
- 完整转录和笔记，除非用户明确勾选。

---

# 19. 迁移执行顺序

## Gate 0：真实基线

必须生成：

```text
module_inventory.json
import_graph.json
test_baseline.json
ui_feature_matrix.md
rpc_contract.md
sample_data_manifest.json
```

记录：

- 真实测试收集数；
- 当前失败和跳过；
- `src.core` 全部引用；
- 旧数据库样例；
- 可继续的断点任务样例；
- 当前 GUI 功能矩阵。

## Gate 1：Headless Python Engine

新增正式 API 和协议客户端，不修改现有管线。

必须使用真实 MP4 验证：

```text
完整处理
暂停
继续
取消
笔记输出
```

关键验收：

```text
Whisper 完成后暂停
→ 再继续
→ 不重新提取音频
→ 不重新运行 Whisper
```

Gate 1 未通过不得开始正式 Tauri 页面。

## Gate 2：Rust Engine Manager

完成：

- 进程启动；
- framed JSON-RPC；
- request map；
- 事件转发；
- 超时；
- Job Object；
- 崩溃检测；
- 重启；
- 优雅退出。

必须验证：

```text
强制结束 Python
→ 后代进程全部结束
→ Engine 重启
→ 任务变为 RESUMABLE
```

## Gate 3：Process + Tasks

通过 Tauri UI 真实完成：

- 本地视频；
- URL；
- GPU Whisper；
- 暂停；
- 继续；
- 取消；
- 任务详情。

此阶段通过后，新桌面端已经具备正式核心生产能力。

## Gate 4：Notes + Settings + Collections

所有页面接入真实 Engine API，不允许假数据。

## Gate 5：默认 GUI 切换

- Tauri 成为默认入口；
- PySide6 进入只读维护；
- 新功能只开发在 Tauri；
- 保留旧 GUI 一个发布周期用于回滚。

## Gate 6：Python 分层清理

执行：

```text
迁移 src.core 真实实现
→ 修改正式导入
→ 修改测试 patch 路径
→ 修改插件 API
→ 架构测试清零
→ 删除 src/core
```

不得一次性文本替换后直接删除。

## Gate 7：Runtime 组件化

构建 Private Python Runtime 和组件管理器，替换旧 Engine Runtime。

## Gate 8：最终删除

只有全部门禁通过后删除：

```text
src/gui/
PySide6
src/core/
旧 GUI 打包配置
旧兼容代理
```

---

# 20. 测试体系

## 20.1 Python

- Domain 单元测试；
- Application use case 测试；
- Adapter contract 测试；
- API protocol 测试；
- Stage resume 测试；
- 数据库 migration 测试；
- 真实管线集成测试；
- GPU/OCR 环境测试。

## 20.2 Rust

- frame parser；
- pending request；
- timeout；
- Engine crash；
- process tree cleanup；
- component manifest；
- atomic rollback；
- updater compatibility。

## 20.3 Svelte

- store；
- 表单验证；
- 任务动作；
- API error mapping；
- 页面组件测试；
- Markdown sanitization。

## 20.4 端到端

必须覆盖：

```text
安装
首次启动
本地视频
URL
Whisper CPU
Whisper CUDA
OCR
视觉分析
Provider
暂停
继续
取消
崩溃恢复
笔记编辑
合集批处理
更新
回滚
卸载
离线启动
旧数据迁移
```

---

# 21. 发布验收标准

正式版本必须同时满足：

1. 用户机器无需预装 Python、Node 或 Rust；
2. 安装后直接启动；
3. 能处理真实本地视频和 URL；
4. CPU 和 CUDA Whisper 均可运行；
5. OCR 和视觉分析可运行；
6. Provider 能生成真实笔记；
7. 暂停后可继续；
8. 继续时不重复已完成的音频提取和 Whisper；
9. 取消能终止完整进程树；
10. Engine 崩溃后任务变为可继续；
11. Settings、Secret 和模型目录重启后仍存在；
12. Notes 可查看、编辑、保存；
13. Collections 可创建、批处理和导出；
14. 旧数据库和旧工作目录兼容；
15. 关闭应用后无孤儿进程；
16. UI 更新不重打 CUDA；
17. Python 业务更新不重打 CUDA/OCR；
18. 组件更新具备签名、原子替换和回滚；
19. 问题报告完整且不泄漏密钥；
20. 干净 Windows 虚拟机验收通过。

---

# 22. OpenCode 硬性约束

OpenCode 必须遵守：

```text
1. 不创建第二套 Pipeline。
2. 不使用 Mock 作为完成标准。
3. 不让 Svelte 直接连接 Python。
4. 不让前端直接访问数据库。
5. 不把内部 dataclass 直接暴露为 API。
6. 不把普通日志写入协议 stdout。
7. 不合并 Pause 和 Cancel。
8. 不破坏旧 stage manifest。
9. 不在 Gate 1 前删除旧 GUI。
10. 不在功能等价前删除 PySide6。
11. 不在 import graph 清零前删除 src/core。
12. 不依赖用户系统 Python。
13. 不在正式启动时在线 pip install。
14. 不把 API Key 明文返回前端。
15. 不绕过 migration 修改生产数据库。
16. 不在兼容性检查失败时强行应用快速更新。
17. 每个 Gate 必须提交测试结果和产物清单。
18. 门禁失败必须停止，不得继续后续阶段。
```

---

# 23. 架构决策记录

## ADR-001：Tauri 作为唯一正式 GUI

选择 Tauri 2 + Svelte 5 + Vite SPA。Tauri 负责桌面能力，Svelte 负责展示，不承载业务实现。

## ADR-002：Rust 与 Python 使用 stdio framed JSON-RPC

不使用前端直连 WebSocket，不开放本地端口。协议采用 Content-Length framing。

## ADR-003：Python Engine 是唯一业务边界

GUI、Rust 和插件只能通过版本化 API 使用业务。

## ADR-004：断点恢复以持久产物为准

恢复的是 SQLite、manifest 和阶段产物，不声称恢复内存中的推理上下文。

## ADR-005：Private Python Runtime

用户不依赖系统 Python；正式安装包携带受控、固定版本的运行时。

## ADR-006：组件独立更新

Shell、Engine、CUDA/OCR Runtime 和模型独立版本化。

## ADR-007：旧架构延后删除

先建立真实可用的新主干，再删除 PySide6 和 `src/core`。

---

# 24. 最终产品形态

最终产品不是“Tauri 包一层 Python”，而是一个清晰分层的桌面系统：

```text
Tauri/Svelte
负责用户体验

Rust Desktop Core
负责操作系统能力、进程、安装和更新

Python Engine API
负责稳定的产品业务接口

Application + Domain
负责业务规则和任务编排

Infrastructure
负责 Whisper、OCR、FFmpeg、Provider 和数据库

Component Manager
负责大型运行时和模型的安装、升级与回滚
```

该形态允许：

```text
修改 UI
→ 只发布 Shell

修改 Python 业务
→ 只发布 Engine App

升级 Provider 或普通依赖
→ 发布 Base Engine

升级 CUDA/OCR
→ 发布对应组件

升级模型
→ 发布模型清单
```

不再因为修改按钮、页面或业务代码而重新打包全部 CUDA、PaddleOCR 和 Whisper 运行时。

## 22. Runtime Payload 构建脚本

组件发布前先运行 `prepare_runtime_payload_sources.ps1` 准备隔离来源，并写出
`payload-source-map.json`。

随后由 `stage_runtime_payloads.py` 将来源复制到 `runtime/packages/<component>`，
再由 `verify_runtime_payloads.py` 校验 manifest files、缺失文件和非法路径。

`download-tools` 使用官方 `yt-dlp.exe` standalone executable；`ffmpeg-tools`
使用 `ffmpeg.exe` / `ffprobe.exe`。两者都按 native tool component 管理，
不作为 Python package 进入 base runtime。
