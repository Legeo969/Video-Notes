# Video Notes AI v4 验证报告

> 验证日期：2026-07-05
> 验证环境：Linux 容器，Python 3.13.5，Node/Vite 环境可用；未安装 Rust/Cargo 工具链

## 1. 验证范围

本报告验证本轮产品级重构的主要行为：

- Python 任务生命周期、请求快照和凭据引用。
- SQLite v14 迁移、事件日志、断点恢复、暂停、取消和重试。
- 处理管线阶段行为、设置 API、模板加载、推荐、预览和校验。
- Svelte 构建与静态检查。
- Rust 源码静态审查。

## 2. Python 活跃后端套件

执行命令：

```bash
python -m pytest -q \
  --ignore=tests/test_collection_delete.py \
  --ignore=tests/test_smart_summary.py \
  --ignore=tests/test_provider_profile_settings.py
```

结果：

```text
618 passed, 52 skipped, 4 xfailed
```

说明：

- `skipped` 主要为需要可选外部依赖、模型或特定运行条件的测试。
- `xfailed` 为仓库已有的显式预期失败。
- 本轮新增的任务运行时与设置契约测试均通过。

### 2.1 为什么排除 3 个旧测试文件

| 文件 | 原因 |
|---|---|
| `tests/test_collection_delete.py` | 导入已退出正式产品链的 `PySide6.QtWidgets` / Qt GUI |
| `tests/test_smart_summary.py` | 导入已退出正式产品链的 Qt GUI |
| `tests/test_provider_profile_settings.py` | 包含针对旧源码文本/旧 Qt 设置实现的断言，与当前 Engine API 设置契约不一致 |

这些文件没有被删除，也没有被伪装成通过。它们应在后续迁移中标记为 `legacy`，或移入历史测试目录；正式发布测试应以 Tauri/Svelte + Python Engine 为产品边界。

## 3. 聚焦回归

已覆盖：

- 请求快照不保存密钥。
- 恢复时保留原模型、模板、抽帧和视觉参数。
- 当前激活供应商变化后，仍按原任务的命名配置获取凭据。
- 旧快照兼容回退。
- 启动时将遗留活动任务标为 `interrupted`。
- 同一任务不可重复启动。
- 同时只允许一个重任务。
- `resume` 沿用 `run_id`。
- `retry` 生成新 `run_id`，并记录 `parent_run_id` 与 `attempt`。
- 进度写入 SQLite。
- 事件写入 `job_events`。
- 设置供应商增删改、激活、模型扫描和连接测试契约。
- 8 套内置模板的加载、推荐、预览和校验。
- 自动抽帧不突破 `max_frames`。
- OCR 入口收敛。
- Vision Provider 工厂调用修复。
- Sidecar stdout 不被普通进度输出污染。

## 4. Python Sidecar 协议握手

新增子进程级测试会显式阻止 `yt_dlp`、`faster_whisper` 和 `ctranslate2` 导入，再启动：

```bash
python -m src.engine --stdio
```

验证结果：

- 收到 `engine.hello`。
- `system.info` 正常响应。
- `system.shutdown` 正常响应。
- 进程退出码为 0。
- stderr 无 Traceback。

这证明设置页、诊断页和基础引擎握手不再被可选下载/模型依赖阻断。

## 5. Svelte 前端

执行命令：

```bash
cd desktop
npm run build
npx svelte-check --tsconfig ./tsconfig.json
```

结果：

```text
Vite production build: success
svelte-check: 0 errors, 0 warnings
```

验证点：

- 业务调用统一经过 `engine_call`。
- Tauri 事件 payload 自动解包。
- Process 与 Tasks 页面使用共享 jobs store。
- 开始、暂停、取消、继续和重试使用统一 job id。
- 设置页与 Python API 字段一致。
- Mock API 与正式契约同步。

## 6. Rust/Tauri 状态

完成的源码级修复：

- 长 RPC 不再占用 `EngineManager` 生命周期锁。
- stdin 写入串行化。
- stderr 持续消费。
- Sidecar 断开后立即唤醒 pending RPC。
- 阻塞 stdout 读取放入 blocking pool。
- 协议帧最大 8 MiB。
- 事件只转发 Python notification 的 `params`。
- 开发模式定位项目根并运行 `python -m src.engine --stdio`。
- Windows Job Object 继续管理 Sidecar 子进程树。

限制：当前验证容器没有 `cargo` 和 `rustfmt`，因此没有声明 Rust 已编译通过。正式发布前必须在 Windows 开发机执行：

```powershell
cd desktop\src-tauri
cargo fmt --check
cargo check
cargo test
cd ..
npm run tauri build
```

## 7. 尚未执行的真实环境测试

本轮没有在该容器中完成：

- Windows 安装包构建。
- 打包后的 `python-engine.exe` 启动。
- CUDA Whisper。
- PaddleOCR GPU。
- 真实 LLM/Vision API 调用。
- 大型本地视频完整处理。
- yt-dlp 在线 URL 下载。
- 应用在转录/抽帧/LLM 阶段被强杀后的真实恢复。
- 自动更新、签名和回滚。

这些项目属于发布门禁，不应以单元测试替代。

## 8. 结论

本轮已完成“产品内核重构基线”：任务执行入口、任务真相、参数恢复、事件链和前端状态均已收敛，可以继续进入 Windows Sidecar 打包与真实媒体端到端验证。

当前准确状态：

- Python 产品内核：通过活跃测试套件。
- Svelte 前端：生产构建与静态检查通过。
- Rust/Tauri：完成源码修复，待 Windows 工具链编译验证。
- 安装发布：尚未完成，不应称为最终安装包。

## v4.1 Windows settings-isolation regression

The three Windows failures in `test_api_settings_contract.py` shared one root cause: platform-specific home-directory expansion bypassed pytest's temporary `HOME`. The settings path now supports an explicit cross-platform override and the fixture uses it.

Post-fix active backend result:

```text
618 passed, 52 skipped, 4 xfailed
```
