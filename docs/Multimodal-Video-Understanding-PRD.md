# Video Notes AI 多模态视频理解 PRD / v1.6.1 Hotfix Release

## 1. 背景

当前 Video Notes AI 的核心链路是：

```text
视频/音频
→ Whisper 转录
→ 可选 OCR 抽帧识别画面文字
→ AI 生成结构化 Markdown 笔记
```

这个链路对纯讲解、字幕清晰、文字密集型视频有效，但对软件教程、节点编辑器、PPT 演示、图表推导、代码/参数操作类视频仍不够。原因是很多关键信息存在于画面结构中，而不是语音或 OCR 文本中。

本规格最初把“多模态视频理解”收敛为两个可执行里程碑：

- M1.5：修正并验收当前视觉理解任务链路。
- M2：补齐设置页视觉模型测试能力。

截至 v1.6.0，M1.5/M2 已完成，并继续落地了 timeline context、adaptive frame sampling、segment-level vision、学习型笔记 prompt、清洁 artifact 布局、storage cleanup、任务记录持久化、任务控制、runtime component marker、provider 表单修正、合集批处理资源保护、合集独立导出目录、合集条目进度同步、M7-lite Provider Capability Matrix、settings/task config snapshot retry、safe lineage、workspace tracking、conservative cleanup、Windows process tree kill hardening 和 staged HTTP cancellation checks。

本文是当前 release candidate 的产品/技术验收记录，不表示路线图全部完成。v1.6.0 的发布边界是“核心多模态生成链路、导出产物布局、本机任务中心、合集批处理、合集独立导出目录、合集条目进度同步、M7-lite capability cache、精确重试配置快照、取消/清理安全加固可用”；跨重启 resume、OS thread suspension、mid-command pause 等仍属于后续版本范围。

## 1.1 v1.6.0 发布结论

当前结论：**v1.6.0 当前功能已通过分支验证和对抗审查；installer / release 仍待构建发布。**

发布前必须确认：

- 真实合集批处理默认串行执行，不爆 CPU/RAM/GPU。
- 队列能自动从一个视频推进到下一个视频。
- 成功任务输出 Markdown 和被引用 frames 到 Obsidian/export 或 collection folder。
- 中间文件只留在 AppData job workspace，不污染 Obsidian/export 根目录。
- 失败任务不会阻塞整个 batch queue。
- Tasks 页面状态、进度、实时已用时间、失败信息和历史记录可理解。
- Collections 页面条目状态和合集 `workProgress` 能随子任务推进更新，不再把 `completed/total` 误当作处理进度。

完成本次版本 metadata bump、构建 installer 并通过 release smoke test 后，v1.6.0 可作为 GitHub Release 发布；如果失败，优先修真实运行问题，不继续堆新功能。

## 2. 目标

1. 让视觉理解真实参与 native task pipeline，而不是只保存 UI 开关。
2. 让 OCR 和视觉理解可以独立启用。
3. 让 vision-only 任务也能向笔记生成模型提供可引用图片素材。
4. 让视觉模型失败时任务降级完成，并把失败原因写入 transcript。
5. 让用户能在设置页验证当前活动供应商的 `vision_model` 是否支持图片输入。
6. 让任务中心成为本机任务记录中心：重启后保留历史记录，可执行取消、暂停、继续、重跑等 phase 1 控制。
7. 让本地 runtime component 状态可信：安装/更新后不因工具真实版本格式差异误报更新。
8. 让 AI 供应商配置表单保持空白输入语义，不自动填入与用户意图无关的默认残留值。
9. 让合集批量处理具备资源保护：导入文件夹只创建合集，批量处理进入后台队列，默认串行，最大并发 2。

## 3. 非目标

1. 不做实时视频流理解。
2. 不上传完整视频给模型。
3. v1.6.0 不实现专有 SDK；自动笔记 pipeline 只承诺 OpenAI-compatible `chat/completions` + `image_url`。Gemini / Anthropic / Responses 等非 OpenAI-compatible adapter 不属于 v1.6.0 可用范围，除非后端明确实现。
4. 不做自动模型下载。
5. 不做自动 provider capability 推断；M7-lite 只沉淀 `settings.vision.test` 的 provider/model capability cache，不根据模型名自动分类。
6. 不保证所有 OpenAI-compatible 服务都兼容，只要求失败信息可见、任务可降级。
7. 不保留最终 Markdown 生成缓存；最终笔记每次直接调用当前 provider 生成，避免 OCR/Vision/timeline 非确定性导致缓存污染。
8. Task actions 不做 OS thread suspension、不做 mid-command pause、不做跨重启 resume。
9. Task actions 已做 Windows process tree kill hardening 和 staged HTTP cancellation checks；不承诺强制中断所有不可取消的外部 blocking call。
10. 合集 batch queue 不做持久化与跨重启恢复；已启动的子任务仍按任务中心现有机制持久化。
11. 不保证所有供应商都支持远程模型发现；AI 供应商 `/models` 读取是真实接口探测，失败时必须报错且不得伪造列表或覆盖用户手动输入。PaddleOCR 模型刷新仅重载内置官方静态列表。

## 4. 当前实现状态（v1.6.0）

当前代码已包含以下能力：

- provider profile 支持 `model` 和 `vision_model`。
- 设置页支持默认 `vision_enabled` 开关。
- 创建任务页会提交 `vision_enabled`。
- native engine 有 `vision_analyzing` 阶段。
- native engine 会抽取关键帧，并调用 OpenAI-compatible vision model。
- 视觉失败时会写入 `## Vision` 降级信息。
- 笔记生成 prompt 已要求只使用提供的 Markdown 图片路径。
- `settings.vision.test` 已支持在设置页验证当前视觉模型。
- Whisper JSON segment 可解析为 timeline，并把 transcript、OCR、Vision、frame path 合并为时间轴上下文。
- 抽帧支持固定/自适应模式：按视频时长、scene change、fps frame number、去重与帧预算生成候选帧。
- Vision 已从单次多图描述升级为 per-segment summary，并以受限并发执行。
- 笔记生成 prompt 已优化为学习型结构，强调步骤、参数、术语、复习问题与可复现性。
- 中间产物已迁移到 `%LOCALAPPDATA%\Video Notes AI\jobs\job-{id}-{timestamp}`。
- Obsidian/export 目录只保留最终 Markdown 和被引用图片：`assets/{note_stem}/frame-xxx.png`。
- Storage Management 已支持清理 AppData job workspace，不删除导出的 Markdown 或 Obsidian assets。
- 删除笔记时会删除对应 `assets/{note_stem}/`。
- Tauri asset protocol 已启用，Notes 页面可显示本地导出图片。
- 任务中心已接入 native task actions phase 1：取消、重跑已实现；暂停/继续为阶段边界协作式控制。
- Task actions phase 2 已完成本地 pipeline 命令 direct child kill：yt-dlp、ffmpeg/ffprobe、whisper-cli、tesseract 在受控 helper 内运行，取消时 kill direct child 并回收进程。
- 任务记录已持久化到 `%LOCALAPPDATA%\Video Notes AI\.jobs\jobs.json`，重启后历史任务仍可见。
- `jobs.json` 写入使用 atomic write，任务控制状态写入避免 stale snapshot 覆盖。
- 重启前仍 non-terminal 的任务会加载为 `interrupted`，不假装继续运行。
- Runtime component 安装成功后写 `.runtime-component.json` marker，更新判断基于 manifest version，而不是直接比较工具真实版本和 GitHub latest。
- 新增 AI 供应商表单不再自动填入 OpenAI 默认 Base URL / model / vision model；切换 API 类型也不覆盖用户输入。
- AI 供应商表单可调用供应商真实 `/models` endpoint 读取模型；读取失败不 fallback 为内置推荐或当前输入值，不覆盖用户手动输入的精确 `model` / `vision_model`。
- PaddleOCR 模型刷新只重载内置官方静态模型列表；官方 hosted API 未提供 model discovery endpoint。
- 合集导入文件夹只创建合集，不自动启动任务；合集批量处理改为后台队列，默认串行处理，最多并发 2。应用重启后不恢复 batch queue，但已启动子任务仍保留在任务中心持久化记录中。
- 合集条目以 `NativeJob` 为运行状态权威来源，`CollectionItem` 只是 durable snapshot；`collection.get/list_items` 返回前按 `run_id` 做 read-through sync。
- 合集 UI 区分 `successProgress` 与 `workProgress`，running/pausing/paused/failed/cancelled 不再导致合集处理进度长期显示 0%。
- M7-lite Provider Capability Matrix 已完成：`settings.vision.test` 结果可沉淀为可刷新/清除的 provider/model capability cache，不保存 API key，不阻止用户继续运行任务。
- Retry 已使用 settings/task config snapshot，并记录 safe lineage、workspace tracking；conservative cleanup 避免误删可继续或可追溯的任务 workspace。
- Windows process tree kill hardening 已完成；取消受控 native 命令时按安全 lineage 回收 process tree。
- HTTP 调用已加入 staged cancellation checks，取消请求可在阶段边界和请求前后尽早停止。

当前仍未完成或需实测的缺口：

- 真实合集 release smoke test 仍需最终确认：资源占用、队列推进、失败隔离、暂停语义、实时进度、collection folder 导出质量。
- Task actions 仍不支持跨重启继续、OS thread suspension 或 mid-command pause；暂停只在当前阶段结束后的安全检查点生效。
- OpenAI-compatible provider 差异仍需通过设置页测试和任务降级路径暴露，不做模型名硬编码判断。
- 合集任务的最终笔记产物已支持写入独立 collection folder，方便整理、移动和导入 Obsidian；仍需真实合集 smoke test 验证路径、图片引用和失败隔离。

v1.6.0 发布范围不包含：

- app 重启后继续 paused/running 任务。
- OS thread suspension 或 mid-command pause。
- 强制中断所有不可取消的外部 blocking HTTP call。

这些项目进入 v1.6.0+ 路线图。v1.5.9 release 可作为历史发布说明保留：其边界不包含 M7-lite、精确配置快照 retry、process tree kill hardening 和 HTTP cancellation checks。

## 5. M1.5：视觉理解任务链路修正

### 5.1 范围

M1.5 只修正当前任务链路，不引入新的时间轴数据结构。

任务流程：

```text
媒体输入
→ Whisper 转录
→ 如果 OCR 或 Vision 任一开启，则抽取关键帧
→ OCR 可选执行
→ Vision 可选执行
→ 写入 transcript
→ 生成 Markdown 笔记
```

### 5.2 抽帧规则（历史 M1.5 baseline）

M1.5 最初 baseline 是固定抽帧：每 60 秒 1 帧、最多 8 帧、OCR 和 Vision 复用同一批帧。该段只保留为历史验收背景。

v1.6.1 当前行为以 M4 Adaptive Frame Sampling 为准：支持 fixed/adaptive frame mode、frame interval、max frames、scene change、fps frame number、去重与失败回退。`ocr_enabled=false && vision_enabled=true` 时仍会抽帧并向 note generation 提供可引用图片素材。

### 5.3 Vision 调用

输入格式：

```json
{
  "model": "<vision_model or model>",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "请分析这些视频关键帧..." },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,..."
          }
        }
      ]
    }
  ],
  "temperature": 0.1
}
```

规则：

- 优先使用当前活动 provider 的 `vision_model`。
- 如果 `vision_model` 为空，回退使用 provider 的 `model`。
- 单次最多发送 4 张图。
- 单次超时 120 秒。
- 使用 `POST <base_url>/chat/completions`。

### 5.4 Transcript 输出

Vision 成功时写入：

```markdown
## Vision

<vision model returned markdown>
```

Vision 返回空内容时写入：

```markdown
## Vision

No visual details were returned by the vision model.
```

Vision 失败时写入：

```markdown
## Vision

Vision unavailable: <provider error>
```

抽帧失败时写入：

```markdown
## Vision

Frame extraction unavailable: <ffmpeg error>
```

### 5.5 笔记生成图片素材

生成模型输入应包含：

- 音频转写。
- OCR 结果，如果 OCR 开启。
- Vision 结果，如果 Vision 开启。
- 可引用图片素材列表，如果 OCR 或 Vision 任一开启且帧目录存在。

关键规则：

1. `image_context` 不应依赖 `ocr_enabled`。
2. `ocr_enabled=false && vision_enabled=true` 时，笔记生成仍应收到图片素材列表。
3. 图片路径必须是相对 Markdown 路径。
4. 不允许模型编造图片路径。
5. 没有明确相关图片时，不强行插图。
6. 不把图片集中放在文末。

示例：

```markdown
在这一步中，讲师创建了 slope mask，并调整了坡度范围。

![frame-003](lesson-1-frames/frame-003.png)
```

### 5.6 UI 行为

创建任务页：

- 视觉理解开关只在存在活动 provider 时可启用。
- 任务提交时，开启视觉理解必须包含 `vision_enabled: true`。
- 摘要栏必须显示视觉理解开启或关闭。

任务中心：

- Vision 开启时，任务阶段必须出现 `vision_analyzing`。
- 抽帧数量显示真实帧数。
- Vision 失败不应把任务标记为 `failed`。
- 定位输出文件应打开真实 Markdown 产物所在目录。

### 5.7 M1.5 验收标准

基础链路：

- 关闭 OCR、关闭 Vision：任务可完成。
- 开启 OCR、关闭 Vision：任务可完成，transcript 包含 `## OCR`。
- 关闭 OCR、开启 Vision：任务可完成，transcript 包含 `## Vision`，笔记生成输入包含图片素材。
- 同时开启 OCR 和 Vision：复用同一帧目录，任务可完成。

失败降级：

- provider 未配置时，Vision 写入明确失败原因，任务仍可完成。
- 模型不支持图片输入时，Vision 写入 provider error，任务仍可完成。
- 抽帧失败时，OCR 和 Vision 均跳过，任务仍基于转写生成笔记。
- API 超时时，Vision 写入超时错误，任务仍进入 `generating_notes`。

笔记质量：

- 软件教程笔记能在相关段落附近引用关键画面。
- vision-only 任务允许插入截图。
- 没有相关图片时不插图。
- “转写与 OCR 依据”不输出完整 transcript，只列关键依据。

## 6. M2：视觉模型测试

### 6.1 范围

M2 在设置页增加视觉模型测试能力，用于验证当前活动 provider 的 `vision_model` 是否能处理图片输入。

不进入 M2：

- 批量测试多个模型。
- 自动识别模型是否视觉模型。
- provider-specific SDK。
- 上传用户视频帧作为测试图。

### 6.2 RPC

新增 RPC：

```text
settings.vision.test
```

输入：

```json
{
  "provider": "optional provider name",
  "vision_model": "optional model override"
}
```

行为：

1. 读取 provider 参数；如果为空，使用当前活动 provider。
2. 校验 provider 存在。
3. 校验 provider API key 已配置。
4. 校验可用模型：优先使用入参 `vision_model`，否则使用 provider 的 `vision_model`，再否则使用 provider 的 `model`。
5. 使用内置测试图构造 `image_url` data URL。
6. 调用 `<base_url>/chat/completions`。
7. 返回成功状态、模型名、简短结果或错误信息。

输出成功：

```json
{
  "success": true,
  "model": "<model>",
  "message": "Vision model is available",
  "result": "<short model response>"
}
```

输出失败：

```json
{
  "success": false,
  "model": "<model if known>",
  "message": "<human readable failure>",
  "error": "<provider or validation error>"
}
```

### 6.3 设置页 UI

内容增强区域增加：

- `测试 Vision` 按钮。
- 测试中 loading 状态。
- 成功 toast：显示模型名和简短结果。
- 失败 toast：显示可操作错误信息。

按钮状态：

- 没有活动 provider 时禁用，提示先配置 AI 供应商。
- 活动 provider 没有 API key 时禁用或测试时返回明确错误。
- 测试进行中禁用。

### 6.4 错误信息

错误信息必须区分：

- 未配置活动 provider。
- provider 不存在。
- API key 未配置。
- `base_url` 为空。
- 模型 ID 为空。
- HTTP request failed。
- HTTP non-2xx response。
- response JSON 无 `choices[0].message.content`。
- 模型不支持 `image_url` 或 base64 data URL。

### 6.5 M2 验收标准

- 设置页能看到 `测试 Vision` 按钮。
- 无活动 provider 时不能直接测试。
- 无 API key 时给出明确错误。
- 无 `vision_model` 但有 `model` 时，用 `model` 回退测试。
- 兼容模型返回成功 toast。
- 不兼容模型返回失败 toast，并保留 provider error。
- 测试不修改 provider 配置。
- 测试不创建任务、不写入 transcript、不写入 notes。
- v1.6.1 中 `settings.vision.test` 仍是 live probe，并可把测试结果写入 M7-lite capability cache；不自动推断模型能力，也不阻止用户继续运行任务。

## 7. 数据结构

### 7.1 任务参数

```json
{
  "input": "D:\\video.mp4",
  "title": "可选标题",
  "whisper_model": "large-v3",
  "whisper_device": "cuda",
  "ocr_enabled": true,
  "ocr_backend": "paddleocr_http",
  "vision_enabled": true
}
```

### 7.2 任务阶段

```text
resolving
downloading
transcribing
extracting_frames
vision_analyzing
generating_notes
completed
failed
```

Task actions phase 1 额外状态：

```text
pending       新任务已创建，worker 尚未进入处理阶段
running       任务正在执行
pausing       用户已请求暂停，等待当前阶段结束后的安全检查点
paused        任务已在安全检查点暂停，可继续或取消
cancelling    用户已请求取消；本地受控 direct child 会被终止，HTTP/blocking 非受控阶段等待返回或下一检查点
cancelled     任务已取消，属于终态
interrupted   应用重启或进程中断后恢复出的终态记录
```

状态规则：

- `completed` / `failed` / `cancelled` / `interrupted` 是 terminal status。
- `pending` / `running` / `pausing` / `paused` / `cancelling` 是 non-terminal / controllable status，不能直接删除。
- `pending` / `running` / `pausing` / `cancelling` 是 running-like status，用于“正在执行/等待边界/取消中”统计。
- `paused` 是 non-terminal 但不是 running-like；它表示 worker 已在 checkpoint 停住，可继续或取消。
- app 启动加载历史记录时，non-terminal status 一律转为 `interrupted`，避免假装任务仍在运行或可跨重启继续。
- `paused` 不写 `completed_at`；terminal status 写 `completed_at`。
- `can_resume=true` 只允许出现在 `paused`。

### 7.3 任务记录持久化

任务记录文件：

```text
%LOCALAPPDATA%\Video Notes AI\.jobs\jobs.json
```

记录字段：

```json
{
  "id": 1,
  "job_id": "stable uuid",
  "title": "可选标题",
  "status": "running | paused | completed | ...",
  "progress": 70,
  "progress_message": "生成 Markdown 笔记",
  "stage": "generating_notes",
  "input": "D:\\video.mp4",
  "created_at": "RFC3339",
  "completed_at": "RFC3339 or null",
  "error_message": null,
  "output_path": "最终 Markdown 路径 or null",
  "transcript_path": "transcript 路径 or null",
  "frames_count": 8,
  "can_resume": false
}
```

持久化规则：

- `process.start` 创建任务后立即写入记录。
- `process.pause/cancel/resume/retry/delete` 的状态变更必须持久化成功后才返回成功。
- background progress 更新可 best-effort 写盘，但不得用旧 snapshot 覆盖更新的 action 状态。
- `jobs.json` 使用 atomic write，避免半写文件。
- `next_job_id` 由历史最大 `id + 1` 初始化，避免重启后 id 冲突。
- 任务记录只保存运行元数据，不保存 API key、cookie 或 provider secret。

### 7.4 Task Actions Phase 1

RPC：

```text
process.pause
process.cancel
process.resume
process.retry
```

`process.pause`：

- 允许状态：`pending` / `running`。
- 立即转为 `pausing`。
- `pause_requested=true` 必须先于 `status=pausing` 写入，避免 worker 在状态变化和 flag 变化之间越过 checkpoint。
- worker 到达安全检查点后转为 `paused`，并设置 `can_resume=true`。
- 不暂停当前正在执行的 `ffmpeg` / `whisper-cli` / `yt-dlp` / `tesseract` / HTTP request。

`process.resume`：

- 允许状态：`pausing` / `paused`。
- `pausing + resume` 表示撤销尚未生效的暂停请求；任务可能从未停住。
- `paused + resume` 表示唤醒 checkpoint wait 并继续 pipeline。
- 任务继续后 `can_resume=false`。

`process.cancel`：

- 允许状态：`pending` / `running` / `pausing` / `paused` / `cancelling`。
- 立即转为 `cancelling`。
- 如果 worker 正在 `paused` 等待，取消会唤醒 worker 并在 checkpoint 终止为 `cancelled`。
- 如果当前阶段正在受控本地 direct child process 中执行，会尝试终止并回收该 child；如果处于非受控 blocking 阶段或 HTTP call，可能需要等该阶段返回后才进入 `cancelled`。
- cancel 意图优先于当前 stage error；用户取消后不应被后续错误覆盖为 `failed`。

`process.retry`：

- 允许状态：`completed` / `failed` / `cancelled` / `interrupted`。
- 创建一个新任务，旧任务保持原 terminal 记录。
- 新任务继承旧任务 `input` / `title`。
- 新任务使用当前 settings，不保证复现旧任务的 provider/model/OCR/frame 配置。

`process.delete` 防护：

- backend 拒绝删除 non-terminal status 任务。
- 用户需要先取消或等待任务进入 terminal status 后再删除记录。

### 7.5 Runtime Component 状态

组件安装位置：

```text
%LOCALAPPDATA%\Video Notes AI\runtime\components\{component}
```

安装成功后写入 marker：

```text
.runtime-component.json
```

marker 内容：

```json
{
  "component": "ffmpeg-tools",
  "manifest_version": "1.6.1",
  "installed_at": "RFC3339"
}
```

规则：

- UI 显示的 `installed_version` 仍来自真实工具命令，例如 `ffmpeg -version`。
- 是否显示“更新”不再直接比较真实工具版本和 GitHub latest tag。
- `update_available` 优先比较 marker 的 `manifest_version` 与当前 manifest `version`。
- 无 marker 的已安装组件不因版本文本格式差异误报更新。
- 缺文件仍显示 `missing_files` / 需修复状态。

### 7.6 Provider 表单行为

新增供应商：

- 表单初始为空。
- 不自动填入 `https://api.openai.com/v1`。
- 不自动填入 `gpt-4o-mini` 或其他默认模型。
- 切换 API 类型不覆盖用户已输入的 Base URL / model / vision model。

编辑供应商：

- 加载已有 provider 的 name、type、base_url、model、vision_model。
- API key 不明文回填；用户可输入新 key 覆盖。
- “读取模型”只调用供应商真实 `{base_url}/models` endpoint。
- `/models` 成功时，返回的模型 ID 可用于下拉选择文本模型和视觉模型。
- `/models` 失败时必须显示错误，不 fallback 为当前 `model` / `vision_model`，不使用内置推荐列表伪造结果。
- 手动输入的精确 `model` / `vision_model` 是 source of truth；读取失败不得清空或覆盖它们。
- 非 OpenAI-compatible provider adapter 如果后端未实现，不应在 v1.6.0 中标为可用于自动任务。

### 7.7 产物目录

v1.6.1 当前规则：中间产物写入 AppData job workspace，导出目录只保留最终学习产物。

AppData job workspace：

```text
%LOCALAPPDATA%\Video Notes AI\jobs\job-{id}-{timestamp}\
  downloads\
  frames\
    frame-001.png
    frame-002.png
  transcript.txt
  whisper.json
  {note-stem}-{id}-metrics.json
```

配置 Obsidian Vault 时：

```text
<vault>\video-notes\
  lesson-1-20260707-220831-1.md
  assets\lesson-1-20260707-220831-1\
    frame-001.png
    frame-002.png
```

规则：

- Markdown 文件名包含时间戳和 job id，避免覆盖历史导出。
- 只复制最终 Markdown 实际引用的 frame 到 `assets/{note_stem}/`。
- transcript、Whisper JSON、metrics、下载源文件、未引用 frame 不写入 Obsidian/export 根目录。
- Storage cleanup 只清理 AppData job workspace；不删除已导出的 Markdown 和 Obsidian assets。
- 删除笔记时同步删除对应 `assets/{note_stem}/`。

未配置 Vault 时：

```text
Documents\Video Notes AI\exports\
```

### 7.8 合集导出目录（已在当前工作区实现，待真实合集验收）

原 v1.5.8 行为：合集批处理中的每个视频仍按单视频导出规则写入 export 根目录，图片写入 `assets/{note_stem}/`。这保证单视频路径稳定，但合集完成后笔记会分散在 export 根目录，不利于整理一个课程、系列教程或项目资料。

当前工作区实现：合集批处理启动时为该合集创建一个稳定的 collection export folder，合集内所有最终笔记和被引用图片集中写入该文件夹。`collection.batch_process` 返回 `output_dir`，合集详情页会显示该输出目录。

建议目录结构：

```text
<export-root>\collections\{collection_slug}-{collection_id}\
  0001-lesson-title.md
  0002-lesson-title.md
  0003-lesson-title.md
  assets\
    0001-lesson-title\
      frame-001.png
      frame-002.png
    0002-lesson-title\
      frame-001.png
```

规则：

- collection export folder 只保存最终学习产物，不保存 transcript、Whisper JSON、metrics、下载源文件或未引用 frame。
- 单视频任务保持现有 export 根目录规则，避免改变既有用户习惯。
- 合集任务默认集中到 collection folder；后续可在 UI 暴露“按合集导出 / 平铺导出”的选项。
- 当前实现暂时沿用原有 note 文件名：`{note_stem}-{job_id}.md`；后续可增加合集内顺序号前缀，例如 `0001-`，保证 Obsidian 和文件管理器中的课程顺序稳定。
- note 内图片路径使用相对路径，例如 `assets/{note_stem}/frame-001.png`。
- 删除合集或清理合集导出时，应有明确确认，不和 Storage cleanup 的 AppData workspace cleanup 混在一起。
- 如果单个视频 retry 属于某个 collection，应优先写回同一个 collection export folder，并避免覆盖已有 note。
- 如果合集名称变化，不自动移动旧导出目录；只影响后续新批处理。

验收：

- 跑一个 3 个视频的合集后，export 根目录只新增一个 collection folder。
- 3 个 Markdown 文件都在该 collection folder 下。
- 每个 Markdown 引用的图片都在该 collection folder 的 `assets/` 下，且相对路径可在 Notes 页面和 Obsidian 中打开。
- AppData 中间产物仍可被 Storage cleanup 清理，不影响 collection folder 中的最终产物。
- 某个视频失败不应删除已成功生成的 collection notes。

边界：

- `collection.batch_process` 创建/使用 collection output folder，并让子任务把最终 Markdown 和被引用 assets 写入该目录。
- `collection.export` 只导出 collection metadata/index summary；它不移动、不打包、不重排已生成的 child notes/assets。
- 普通 `process.start` 仍写入常规 export/vault 目标；只有 collection batch 内部启动的子任务使用 collection output folder override。
- v1.6.1 已记录 task config snapshot 和 lineage；collection child retry 应优先沿用原 collection association 和 output folder，仍需通过真实合集 smoke test 验证。

### 7.9 Collection batch state model

第一性原理：`NativeJob` 是运行状态 authoritative source；`CollectionItem` 是可持久化的 durable snapshot。snapshot 可用于重启后展示历史，但当 `run_id` 对应的 `NativeJob` 仍存在时，API 返回值必须以 `NativeJob` 为准。

规则：

- `collection.batch_process` 启动子任务后，`CollectionItem.run_id` 记录对应的 `NativeJob.id`。
- batch runner 可轮询写入 `CollectionItem.status/progress/output_path/error_message`，但这只是优化，不是唯一同步路径。
- `collection.get` / `collection.list_items` / `collection.list` 返回前应按 `run_id -> NativeJob` 做 read-through sync。
- 如果匹配到 `NativeJob`，返回的 item 必须同步：`status`、`progress`、`job_id`、`output_path`、`error_message`。
- app restart 后 non-terminal `NativeJob` 被恢复为 `interrupted`，collection detail 再次读取时也应反映到对应 item。
- 如果 `run_id` 找不到对应 `NativeJob`，保留 snapshot，不猜测状态。

进度分两类：

```text
successProgress = completed_count / total_count

workProgress = average(itemWork):
  completed                 -> 100
  failed/cancelled/interrupted -> 100  // 本次尝试已结束，但不是成功
  running/pausing/paused/cancelling -> latest NativeJob.progress
  pending without run_id     -> 0
```

UI 规则：

- 不允许把 `completed_count / total_count` 标为 batch 处理进度；它只能表示成功完成率。
- 主进度条使用 `workProgress`。
- UI 同时展示成功、失败/取消、暂停、进行中数量。
- 一个 item `running 50%` 且 `completed_count=0` 时，collection `workProgress` 不能显示 0%。
- `paused` item 的 `workProgress` 保持最后 progress，不归零。

Collection status 聚合优先级：

```text
if any cancelling -> cancelling
else if any pausing -> pausing
else if any pending/running with run_id -> processing
else if any paused -> paused
else if total > 0 and all completed -> completed
else if any failed/cancelled/interrupted -> failed  // UI 可显示“部分失败/需处理”
else active
```

验收：

- batch 完成后不得无条件把 collection status 写回 `active`。
- 2 个 completed + 1 个 failed 的 collection 必须显示失败/需处理状态或等价提示。
- 1 个 paused 且无 running/pausing item 的 collection 必须显示 paused。
- 新建但未启动 batch 的 collection item 是 `pending` 且无 `run_id`，不应触发前端自动轮询刷新。
- 已启动 batch 的 running-like/paused item 有 `run_id`，前端可以定时刷新 detail 以展示实时进度。

## 8. 测试计划 / Release Smoke Test

v1.6.1 当前 release gate 证据：

| Gate | Required result | Current status | Evidence |
|---|---|---|---|
| Version metadata | package / Tauri / Cargo 均为 1.6.1 | pending | 本次 bump：`desktop/package.json`, `desktop/package-lock.json`, `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/Cargo.lock`, `desktop/src-tauri/tauri.conf.json` |
| bundle identifier | 不以 `.app` 结尾 | pass | `com.videonotesai.desktop` |
| product verification | 自动验证 / 对抗审查通过 | pass | 分支验证与对抗审查已通过；本次版本 bump 后未运行 build |
| release build | NSIS installer generated | pending | 待运行 `build_windows_release.ps1` |
| installer artifact | 1.6.1 installer exists | pending | 待生成 `Video Notes AI_1.6.1_x64-setup.exe` |
| real collection smoke test | install 后真实合集通过 | pending | 需用户手工确认最终包 |

预期安装包路径：

```text
desktop\src-tauri\target\release\bundle\nsis\Video Notes AI_1.6.1_x64-setup.exe
```

仍需完成的 release smoke test：

- 使用已打包 installer 安装/启动应用。
- 跑真实合集任务，确认 batch queue 默认串行、自动推进、失败隔离。
- 检查 Obsidian/export 目录只包含最终 Markdown 和被引用 assets。
- 检查 AppData job workspace 保存中间文件、metrics 和 transcript。
- 检查 Tasks 页面历史记录、未结束任务/暂停任务统计、失败信息和 terminal status。
- 需要时抽测 cancel：本地受控 direct child 应被终止，任务最终进入 `cancelled`，不被 stage error 覆盖成 `failed`。
- 抽测 pause/resume：`pausing` 是等待 checkpoint，`paused` 才是真正停住；`pausing` 上的 resume 是撤销暂停请求。
- 检查合集 `workProgress`、成功数、失败/取消数、暂停数、进行中数展示正确。
- 新建未启动 batch 的合集不应因为 `pending` item 无限自动刷新。
- AI 供应商 `/models` 成功时展示真实列表；失败时显示错误，不伪造列表、不覆盖手动输入。
- OCR “刷新模型”只刷新内置 PaddleOCR 官方静态列表，并提示官方无 model discovery endpoint。

后续不打包验证优先使用 `npm run build` 和 targeted manual test；只有发布前再运行完整 packaging。installer 构建和真实合集 smoke test 通过后，v1.6.1 作为 hotfix 可发布 GitHub Release。

### 8.1 Rust native engine

建议覆盖：

- `settings.vision.test` 无活动 provider。
- `settings.vision.test` 无 API key。
- `settings.vision.test` 使用 `vision_model`。
- `settings.vision.test` 无 `vision_model` 时回退 `model`。
- vision-only 任务生成 `image_context`。
- Vision 失败后任务仍完成。
- non-terminal job 重启加载后变 `interrupted`。
- `process.pause` 从 `pending/running` 进入 `pausing`，checkpoint 后进入 `paused`。
- `process.resume` 从 `paused/pausing` 恢复 running，并清除 `can_resume`。
- `process.cancel` 从 non-terminal status 进入 `cancelling`，checkpoint 后进入 `cancelled`。
- cancel 后 stage error 不覆盖为 `failed`。
- `process.retry` 对 terminal job 创建新 job，旧 job 保持 terminal。
- `process.delete` 拒绝 non-terminal job。
- `jobs.json` atomic write 不产生半写文件；action 状态不会被旧 progress snapshot 覆盖。
- runtime component 无 marker 时不因工具版本文本差异误报更新；旧 marker manifest version 显示 update。
- `collection.get/list_items/list` 按 `run_id` 从 `NativeJob` read-through sync item 状态和进度。
- collection status 按 item 状态聚合，不在 batch 结束后无条件设为 `active`。

### 8.2 Svelte UI

建议覆盖：

- 创建任务时 `vision_enabled` 正确提交。
- 无活动 provider 时 Vision 开关不可启用。
- 设置页 Vision 测试按钮 disabled/loading/success/failure 状态。
- Tasks “运行中/未结束/已暂停”口径不混淆；`paused` 不应被解释为正在执行。
- `pausing` 显示为等待暂停/暂停请求中，不显示为已暂停。
- `pausing` 的 resume 文案是撤销暂停请求；`paused` 的 resume 文案是继续执行。
- `paused` 显示继续/取消，不允许删除 non-terminal 记录。
- 任务中心已用时间应按前端时间每秒更新，non-terminal 使用 `now - created_at`，terminal 使用 `completed_at - created_at`。
- 新增 provider 表单为空，不自动填 OpenAI Base URL 或默认模型。
- 切换 provider type 不覆盖用户输入。
- AI 供应商读取模型只展示真实 `/models` 返回；失败时显示错误，不伪造模型列表。
- `ffmpeg-tools` 更新后不继续显示“更新”，除非 manifest version 变化。

### 8.3 手工验收

使用 30-60 秒本地视频：

1. 关闭 OCR 和 Vision，确认主链路完成。
2. 开启 OCR，确认 transcript 包含 `## OCR`。
3. 关闭 OCR、开启 Vision，确认 transcript 包含 `## Vision`，note 可引用帧图。
4. 使用不支持图片输入的模型，确认任务不失败且 transcript 记录错误。
5. 在设置页测试兼容和不兼容模型，确认 toast 信息可理解。

长任务控制验收：

1. 本地长视频转写中点击取消：状态先 `cancelling`，当前受控 ffmpeg/whisper direct child 应被终止并最终 `cancelled`，不能变 `failed`。
2. URL 下载中点击取消：受控 yt-dlp direct child 应被终止并最终 `cancelled`。
3. 转写中点击暂停：状态先 `pausing`，当前阶段结束后进入 `paused`。
4. `paused` 后点击继续：状态恢复并继续后续阶段。
5. `paused` 后点击取消：worker 被唤醒并最终 `cancelled`。
6. app 重启：`pending/running/pausing/cancelling/paused` 记录统一变 `interrupted`。
7. 对 `failed/cancelled/interrupted/completed` 执行 retry：创建新任务，旧任务保持 terminal。
8. 对 non-terminal job 执行 delete：backend 拒绝，UI 显示错误。
9. Storage orphan cleanup 不删除 paused/non-terminal job workspace。
10. Tasks / Process / Settings 的未结束任务统计口径一致；running-like 与 paused 应分开展示或明确标注。

## 9. 风险

1. 不同供应商的 OpenAI-compatible 兼容程度不同。
2. 部分模型不支持 base64 data URL，只支持公网图片 URL。
3. 多图输入成本较高。
4. 固定 60 秒抽帧可能漏掉快速操作步骤。
5. 视觉模型可能泛泛描述画面，需要后续优化 prompt 和时间轴上下文。
6. Cancel 已加固 Windows process tree kill 和 staged HTTP cancellation checks；不可取消的外部 blocking call 仍可能延迟进入 terminal status。
7. Retry 使用 settings/task config snapshot，避免无意漂移到当前配置。
8. `paused` 不能跨重启继续；重启后会变 `interrupted`。

## 10. 后续方向

后续方向按“先建立结构，再提升质量，再沉淀能力”的顺序推进。以下状态以 v1.6.1 为准。

### M3：Timeline Context（已完成）

目标：把 transcript、OCR、Vision 从“拼接文本”升级为统一的多模态时间轴。

核心结构：

```text
TimelineSegment {
  start_sec
  end_sec
  transcript
  ocr_text
  vision_summary
  frame_paths
}
```

范围：

- transcript segment 带 `start_sec` / `end_sec`。
- frame 文件带采样时间点。
- OCR 结果归入最近的 timeline segment。
- Vision summary 归入对应 frame 所在 segment。
- note generation 使用 timeline context，而不是整段 transcript 拼接。

验收：

- 生成笔记按视频时间顺序组织步骤。
- 每个插图都能追溯到 timeline segment。
- 没有 OCR 或 Vision 时，timeline 仍能基于 transcript 工作。
- timeline context 由 native engine 内部构建，并通过 metrics / transcript / final note 间接调试；未单独保留长期缓存文件。

### M4：Adaptive Frame Sampling（已完成）

目标：在成本可控的前提下减少漏掉关键画面的概率。

范围：

- 按视频长度动态调整 frame interval。
- 支持 scene change detection。
- 保留开头、结尾、章节切换附近帧。
- 对连续近似画面去重。
- 暴露 frame sampling 摘要：候选帧数、保留帧数、丢弃原因。

验收：

- 同样帧数预算下，比固定 60 秒抽帧覆盖更多关键步骤。
- 静态画面视频不会产生大量重复帧。
- 快速操作视频不会只留下无意义中间帧。
- 抽帧策略失败时可回退到 M1.5 固定抽帧。

### M4.5：Pipeline Metrics（已完成）

目标：让多模态 pipeline 的阶段耗时和抽帧决策可追踪。

当前输出：

- `StageRecord` / `StageTimer` 记录主要阶段耗时。
- metrics 文件写入 AppData job workspace。
- 覆盖阶段：`downloading`、`transcribing`、`extracting_frames`、`vision_analyzing`、`generating_notes`。
- frame sampling metrics：`duration_sec`、`interval_sec`、`candidate_count`、`kept_count`。

### M4.6：Study-oriented Prompt Optimization（已完成）

目标：减少泛泛总结，把输出约束为可复习、可复现的教程笔记。

当前规则：

- prompt 强调学习目标、步骤、参数、术语、复习问题和 action checklist。
- 不输出完整 transcript，只保留关键证据。
- 图片引用必须服务于步骤理解，不作为装饰图。
- 不设置固定 `max_tokens` 上限，避免长教程被硬截断。
- 最终 Markdown 生成缓存已移除：不再维护 `generation-cache`，每次用当前 timeline/OCR/Vision 输入直接生成。

### M4.7：Artifact Layout / Storage Cleanup / Task Records / Task Actions（v1.6.1 已完成发布范围）

已完成：

- 中间产物写入 AppData job workspace。
- Obsidian/export 目录只保留最终 Markdown 和被引用 frame assets。
- Storage Management 可清理 orphan/completed job workspace。
- Note deletion 会删除对应 `assets/{note_stem}/`。
- 本机任务记录写入 `%LOCALAPPDATA%\Video Notes AI\.jobs\jobs.json`。
- 重启后任务中心可读取历史任务；non-terminal 任务恢复为 `interrupted`。
- `process.pause/cancel/resume/retry` phase 1 已接入 backend。
- Task actions phase 2 已覆盖本地受控 direct child kill：`yt-dlp`、`ffmpeg/ffprobe`、`whisper-cli`、`tesseract` 在受控 helper 内运行，取消时 kill direct child 并回收进程。
- Storage cleanup 把 `paused` 视为 non-terminal，避免误删可继续任务的 workspace。
- Tasks / Process / Settings 采用“本机任务记录 / 未结束任务”口径，running-like 与 paused 不混用。
- Retry 使用 settings/task config snapshot，并记录 safe lineage 与 workspace tracking。
- Conservative cleanup 避免删除 non-terminal、paused、lineage 仍需引用的 workspace。
- Windows process tree kill hardening 已覆盖受控 native command cancellation。
- HTTP 调用已加入 staged cancellation checks。

未完成：

- task action 后续增强：跨重启 resume 与更完整 partial artifact cleanup policy。
- full provider capability cache。

v1.6.1 明确不承诺：OS thread suspension、mid-command pause、跨重启继续、强制中断所有不可取消的 blocking HTTP request。

### M4.8：Runtime Component / Provider UX Fixes（已完成）

Runtime component：

- `download-tools` / `ffmpeg-tools` / `whisper-cpp-tools` / `tesseract-ocr-tools` 的状态来自 manifest + installed files + marker。
- `.runtime-component.json` 记录安装时 manifest version。
- `ffmpeg-tools` 不再因 nightly version 字符串和 GitHub latest tag 不匹配而持续显示“更新”。
- 没有 `download_url` 的组件不显示误导性安装按钮。

Provider UX：

- 新增 provider 表单保持空白。
- 切换 API 类型不覆盖用户输入。
- OpenAI-compatible 允许空 API key；空 key 不发送 `Authorization: Bearer`。
- localhost base URL 可规范化为 `/v1`。
- 移除不可用/误导的 provider 类型入口。

### M5：Segment-level Vision Summary（已完成）

目标：让 Vision 输出从“泛泛描述几张图”变成可用于学习笔记的分段语义。

流程：

```text
frames
→ group by timeline segment
→ per-segment vision summary
→ reduce into timeline context
→ generate notes
```

每段 Vision 输出应包含：

```text
time: 120-180s
frames: frame-003.png, frame-004.png
summary: 讲师在节点编辑器中连接 slope mask 到 erosion input，并调整 strength 参数。
evidence: UI node labels, parameter panel
```

验收：

- Vision summary 明确描述 UI、参数、节点、图表或对象关系。
- 每段 summary 可追溯到 frame path。
- note generation 能使用 segment summary 插入更稳定的图片引用。
- Vision reduce 失败时，可回退使用原始 per-frame summary。

### M6：Study-oriented Note Generation（已完成）

目标：从“生成一篇 Markdown”升级为“生成可复习、可复现教程步骤的学习产物”。

范围：

- 章节式学习笔记。
- 操作步骤清单。
- 关键参数表。
- 术语表。
- 复习问题。
- action checklist。
- Obsidian-friendly tags / links。

验收：

- 用户只看笔记即可复现主要教程步骤。
- 参数、节点名、菜单路径不被埋在长段落里。
- 图片引用服务于步骤理解，不作为装饰图。
- 不输出完整 transcript，只保留关键证据。

### M7：Provider Capability Matrix（M7-lite 已完成）

目标：沉淀不同 OpenAI-compatible provider/model 的视觉能力，减少用户试错。v1.6.1 已完成 M7-lite：保存 `settings.vision.test` 结果，支持刷新/清除，不保存 API key，不按模型名硬编码判断。

能力字段：

```text
supports_image_url
supports_base64_data_url
max_images_per_request
recommended_image_format
timeout_sec
last_tested_at
last_error
```

范围：

- `settings.vision.test` 的结果可写入 capability cache。（v1.6.1 已完成）
- 创建任务前根据 capability 给出提示。（M7-lite 已完成基础提示，完整矩阵仍可继续增强）
- 不根据模型名硬编码能力，只使用测试结果和用户配置。
- capability cache 可被用户刷新或清除。（v1.6.1 已完成）

验收：

- 测试成功后，provider/model 显示最近一次视觉测试结果。
- 测试失败后，创建任务页能提示潜在不兼容原因。
- capability cache 不保存 API key。
- capability 信息过期时不会阻止用户继续运行任务。

### 推荐顺序

已完成顺序：

1. M3 Timeline Context。
2. M4 Adaptive Frame Sampling。
3. M4.5 Pipeline Metrics。
4. M5 Segment-level Vision Summary。
5. M4.6 / M6 Study-oriented Note Generation。
6. M4.7B Artifact Layout / Storage Cleanup。
7. M4.7A Persistent Task Records。
8. Task Actions Phase 1。
9. Task Actions Phase 2 direct child kill。
10. M4.8 Runtime Component / Provider UX Fixes。
11. Collection batch queue resource protection。
12. Collection export folder。

剩余优先级：

1. 完成当前真实合集 release smoke test：资源占用、自动串行推进、失败隔离、导出质量。
2. Task Actions 后续增强：跨重启 resume、partial artifact cleanup policy。
3. M7 Provider Capability Matrix 增强：在 M7-lite cache 基础上继续完善 provider 兼容性提示。
4. 更多真实长视频质量验收：CG / Houdini / Blender / Unreal / software tutorial。

理由：核心多模态质量链路、导出产物布局、本机任务记录、任务控制发布范围、合集资源保护、合集独立导出目录、精确重试配置快照、取消边界加固和 M7-lite capability cache 已落地；下一步先构建 installer 并用真实合集验证 release candidate，再继续提升跨重启恢复和 provider 兼容性可见性。
