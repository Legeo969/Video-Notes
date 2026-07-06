# Video Notes AI 多模态视频理解 PRD

## 1. 背景

当前 Video Notes AI 的核心链路是：

```text
视频/音频
→ Whisper 转录
→ 可选 OCR 抽帧识别画面文字
→ AI 生成结构化 Markdown 笔记
```

这个链路对纯讲解、字幕清晰、文字密集型视频有效，但对软件教程、节点编辑器、PPT 演示、图表推导、代码/参数操作类视频仍不够。原因是很多关键信息存在于画面结构中，而不是语音或 OCR 文本中。

目标是升级为：

```text
视频
→ 音频转写
→ 关键帧抽取
→ OCR 画面文字
→ 多模态视觉模型分析关键帧
→ 按时间轴融合 transcript + OCR + vision
→ 生成带图片引用的学习笔记
```

## 2. 产品目标

1. 让应用能接入 OpenAI-compatible 多模态视觉模型，例如 Qwen-VL/Qwen-Omni、GPT 视觉模型、Gemini、Claude 视觉模型或本地兼容服务。
2. 让视觉模型真实参与任务管线，而不是只在 UI 中显示开关。
3. 让生成的笔记能引用相关画面，而不是只在文末堆截图。
4. 保持主安装包轻量，不内置大模型，不强制用户安装 Python。
5. 对不支持视觉输入的供应商给出明确错误或降级说明。

## 3. 非目标

1. 第一阶段不做实时视频流理解。
2. 第一阶段不上传完整视频给模型。
3. 第一阶段不实现专有 SDK，例如只支持某一家服务商的非兼容 API。
4. 第一阶段不做自动模型下载。
5. 第一阶段不保证每个多模态模型都兼容，只保证 OpenAI-compatible `chat/completions` + `image_url` 格式。

## 4. 目标用户

### 4.1 学习型用户

用户观看教程视频，希望自动得到：

- 章节化学习笔记
- 操作步骤
- 关键参数
- 画面截图引用
- 后续行动项

### 4.2 技术教程用户

用户处理 Houdini、Unreal Engine、Gaea、Blender、代码 IDE、产品后台等视频，希望模型理解：

- 软件界面
- 节点关系
- 参数面板
- 图表内容
- 操作顺序

## 5. 核心用户故事

1. 作为用户，我希望在设置里配置一个视觉模型，这样处理视频时可以自动理解画面内容。
2. 作为用户，我希望创建任务时可以打开或关闭视觉理解，避免每个任务都产生额外成本。
3. 作为用户，我希望任务详情里能看到视觉理解是否真实运行、抽取了多少帧、是否失败。
4. 作为用户，我希望生成的笔记在相关段落附近插入截图，而不是统一放在文末。
5. 作为用户，我希望如果视觉模型不支持图片输入，应用能告诉我失败原因。
6. 作为用户，我希望 OCR 和视觉理解可以独立启用：OCR 负责文字，视觉模型负责画面语义。

## 6. 功能范围

### 6.1 设置页

新增或调整能力：

- AI 供应商配置中保留：
  - 文本模型
  - 视觉模型
- 内容增强区域提供：
  - OCR 文字识别开关
  - 视觉理解开关
  - 视觉模型测试按钮

视觉模型测试逻辑：

```text
使用内置测试图
→ 调用当前活动供应商的 vision_model
→ 返回画面理解结果
→ 显示成功/失败/错误详情
```

验收：

- 没有活动供应商时，视觉测试按钮禁用或提示配置供应商。
- 没有视觉模型时，提示填写 vision_model。
- 模型不支持图片输入时，显示接口返回错误。

### 6.2 创建任务页

用户可在单次任务中覆盖默认增强能力：

- OCR 开关
- OCR 后端选择
- 视觉理解开关

展示真实链路：

```text
媒体 → Whisper 转录 → 可选 OCR → 可选视觉理解 → AI 生成笔记
```

验收：

- 视觉理解开启后，提交参数必须包含 `vision_enabled: true`。
- 没有活动供应商时不能启用视觉理解。
- 摘要栏准确显示视觉理解开启/关闭。

### 6.3 任务管线

#### 6.3.1 抽帧

初版策略：

- 默认每 60 秒抽 1 帧
- 最多 8 帧
- OCR 和视觉理解复用同一批帧

后续可配置：

- 抽帧间隔
- 最大帧数
- 是否按场景变化抽帧
- 是否抽取开头/结尾/章节边界帧

#### 6.3.2 OCR

OCR 输出结构：

```markdown
## OCR

### frame-001.png

识别文字...
```

#### 6.3.3 Vision

视觉模型输入：

```json
{
  "model": "<vision_model>",
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
  ]
}
```

视觉输出写入 transcript：

```markdown
## Vision

- frame-001.png: 画面展示了...
- frame-002.png: 这里切换到...
```

验收：

- 视觉开启时任务阶段出现 `视觉理解`。
- 视觉失败时不终止整个任务，但要把失败原因写入 transcript。
- 视觉成功时，笔记生成上下文包含 Vision 内容。

### 6.4 笔记生成

生成模型输入应包含：

- 音频转写
- OCR 结果
- Vision 结果
- 可引用图片素材列表

图片引用规则：

1. 只有与段落明确相关时才插图。
2. 图片必须使用本地相对路径。
3. 不要把图片集中放到文末。
4. 不要编造图片路径。

示例：

```markdown
在这一步中，讲师创建了 slope mask，并调整了坡度范围。

![frame-003](2-generating-slope-masks-1-frames/frame-003.png)
```

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

### 7.2 任务状态

新增或使用阶段：

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

### 7.3 产物目录

当配置 Obsidian Vault：

```text
<vault>\video-notes\
  lesson-1.md
  lesson-1-transcript.txt
  lesson-1-frames\
    frame-001.png
    frame-002.png
```

未配置 Vault：

```text
Documents\Video Notes AI\exports\
```

## 8. 错误处理

### 8.1 视觉模型不支持图片

行为：

- 任务不中断。
- transcript 写入：

```markdown
## Vision

Vision unavailable: <provider error>
```

- 任务中心显示任务仍可完成。

### 8.2 抽帧失败

行为：

- OCR 和 Vision 均跳过。
- transcript 写入失败原因。
- 笔记仍基于转写生成。

### 8.3 API 超时

行为：

- 单次视觉调用超时时间默认 120 秒。
- 失败后降级，不阻塞主链路。

## 9. 性能与成本控制

默认限制：

- 最多 8 张视觉帧
- 每次视觉调用最多发送 4 张图
- 图片使用 PNG 抽帧，后续可压缩为 JPEG
- 视觉模型只在用户显式开启时运行

后续优化：

- 自动缩放图片
- JPEG 压缩质量设置
- 按视频长度动态抽帧
- 按场景变化抽帧
- 多段视觉摘要再 reduce

## 10. 隐私与本地存储

1. 视频文件不上传，除非用户启用远程 OCR 或远程视觉模型。
2. 发送给视觉模型的是抽取帧图，不是完整视频。
3. API Key 保存在本机设置中，不写入任务快照。
4. 诊断日志不应包含 API Key。

## 11. 验收标准

### 11.1 基础链路

- 关闭 OCR、关闭视觉：任务可完成。
- 开启 OCR、关闭视觉：任务可完成，transcript 包含 OCR。
- 关闭 OCR、开启视觉：任务可完成，transcript 包含 Vision。
- 同时开启 OCR 和视觉：复用抽帧目录，任务可完成。

### 11.2 UI

- 设置页视觉开关与创建任务页视觉开关状态一致。
- 创建任务页无活动供应商时不能启用视觉理解。
- 任务中心显示 `视觉理解` 阶段。
- 抽帧数量显示真实帧数。
- 完成任务显示真实运行时长。
- 定位文件能打开真实 Markdown 产物所在目录。

### 11.3 笔记质量

- 软件教程笔记中能引用关键画面。
- 图片插入位置与段落语义相关。
- 没有相关图片时不强行插图。
- “转写与 OCR 依据”不输出完整转录，只输出关键依据。

## 12. 里程碑

### M1：OpenAI-compatible 视觉模型接入

- 抽帧复用
- 视觉模型调用
- Vision 写入 transcript
- UI 恢复视觉开关
- 任务中心显示视觉阶段

### M2：视觉模型测试

- 设置页增加测试按钮
- 内置测试图
- 显示测试结果和错误详情

### M3：时间轴融合

- transcript segment 时间戳
- OCR frame 时间戳
- Vision frame 时间戳
- 生成 timeline multimodal context

### M4：逐帧/分段学习模式

- 按时间段聚合画面
- 每段生成视觉摘要
- 汇总成章节式学习笔记

## 13. 风险

1. 不同供应商的 OpenAI-compatible 兼容程度不同。
2. 部分模型不支持 base64 data URL，只支持公网图片 URL。
3. 多图输入成本较高。
4. 视频教程画面变化快时，固定间隔抽帧可能漏掉关键步骤。
5. 视觉模型可能泛泛描述画面，需要更强 prompt 和时间轴上下文。

## 14. 后续设计方向

长期目标不是简单“加一个视觉开关”，而是形成统一的多模态时间轴：

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

最终笔记生成应基于 timeline，而不是把 transcript、OCR、Vision 简单拼接。

