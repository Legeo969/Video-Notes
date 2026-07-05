# UI v7 CUDA、存储目录、笔记库闭环集中修复

版本号保持 `1.5.0`，不升级。

## 修复范围

### 1. Whisper CUDA 闭环

- 设置页新增 `Whisper 运行设备`：`auto` / `cuda` / `cpu`。
- 设置页新增 `计算精度`：`auto` / `float16` / `int8_float16` / `int8` / `float32`。
- 创建任务页同步显示并允许本次任务覆盖设备与精度。
- 任务快照保存 `whisper_device` 与 `whisper_compute_type`。
- 后端转录阶段将设备与精度传入 `faster-whisper` / `CTranslate2`。
- 显式选择 `cuda` 时，如果 CUDA 不可用或 compute type 不支持，直接报错，不再静默降级 CPU。
- `auto` 模式仍允许 CUDA 失败时自动降级到 CPU/int8。
- `system.info` 返回 `cuda_device_count` 与 `cuda_compute_types`，供诊断展示。

### 2. 输出目录与运行目录分离

- `process.*`、`notes.*`、`collections.*` 默认读取用户设置中的 `output_dir`。
- 不再因为 Tauri Sidecar 工作目录在 AppData 而生成第二套 `engine-runtime/output/.note_index`。
- 任务断点工作区改为私有目录：`%LOCALAPPDATA%\\Video Notes AI\\.jobs`。
- 用户输出目录只负责最终产物与笔记索引：Markdown、transcript、frames、`.note_index`。
- 新任务不会再在用户输出目录创建 `.jobs`。

### 3. 笔记库闭环

- 笔记库使用与任务产物相同的用户输出目录：`.note_index/video_notes.db`。
- 任务完成后，`IndexProvenanceStage` 写入的 note 记录可以被 `notes.list` 读取。
- 已经生成在用户输出目录中的 `.note_index` 会重新变为可见笔记库来源。

### 4. 创建任务输入框可用性

- 全局表单样式加入 `input[type="url"]`，公开视频链接输入框恢复全宽。
- 创建任务页链接输入框增加专用 `url-input` 样式，避免只显示短输入区域。

## 验证

```text
python -m compileall src：通过
pytest tests/test_api_settings_contract.py tests/test_engine_api_smoke.py tests/test_pipeline_stages.py tests/test_v18_resume_checkpoint.py：通过
pytest tests/test_job_queue.py tests/test_v14_task_runtime.py tests/test_db_job_repo.py：通过
Vite production build：通过
svelte-check：0 errors, 0 warnings
```

## 更新注意

本次修改影响 Python 后端转录、任务目录、服务启动目录解析和请求快照字段，必须重新构建 Python Sidecar。首次构建不要使用 `-ReuseSidecar`。
