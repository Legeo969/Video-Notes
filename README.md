# Video Notes AI

Video Notes AI 是开放式**学习材料编译器**的第一个参考应用。它把视频、音频及后续可扩展的文档材料编译为可追踪、可回放、不可变版本化的 Evidence、Claim、Concept 与学习 Artifact。

当前版本：[`2.1.0`](VERSION)  
技术栈：Tauri 2、Rust、Svelte 5、FFmpeg/FFprobe。

## 核心链路

```text
本地视频 / 受支持的公开 URL
  → 安全输入校验与隔离下载
  → FFmpeg/FFprobe 媒体规范化
  → 有界帧采样与同步 16 kHz 单声道音频分片
  → 按能力路由的多模态 Provider Adapter
  → 严格 JSON/Anchor 验证与后端物理时间戳
  → 不可变 VideoCapsule 版本
  → Markdown / Mind-map 渲染与历史回放
```

当前编译器不使用 Whisper、Tesseract 或独立 OCR Merge Pass。Cloud Provider 不支持某种输入模态时，系统必须明确降级，不能伪造完整音视频理解结果。

## 支持的 Provider Adapter

- OpenAI-compatible Chat Completions
- OpenAI Responses
- Google Gemini
- Anthropic Messages

Provider 配置必须显式声明音频支持和单次请求帧数上限。

## 仓库结构

```text
desktop/                 Svelte 前端与 Tauri/Rust 应用
runtime/manifests/       FFmpeg、yt-dlp 运行时组件清单
scripts/                 验证、诊断、运行时准备与发布脚本
templates/               内置笔记模板
rfcs/                    设计变更、治理与审计历史
spec/                    当前 Reference Specification v0.2 Foundation Candidate
schemas/                 机器可读 IR JSON Schema
examples/                有效与无效交换样例
conformance/             一致性 fixture 与 manifest
tasks/                   面向人类和 AI 编程工具的 Task JSON
docs/                    当前架构导览、实现状态、发布报告和维护文档
.github/                 CI、Issue 与 Pull Request 模板
```

文档入口：[`docs/README.md`](docs/README.md)。RFC 入口：[`rfcs/README.md`](rfcs/README.md)。

规范入口：[`spec/README.md`](spec/README.md)。当前规范为 **Spec v0.2.0-rc.3 Foundation Complete**，包含 191 条要求、14 个 JSON Schema、25 个 Red Team 处置、确定性迁移、结构质量基线、106 案例语义质量基准；任务入口为 [`tasks/index.json`](tasks/index.json)。

## 开发环境

- Windows 10/11
- Node.js 20 或 22（仓库使用 [`.nvmrc`](.nvmrc) 指定 Node 22）
- Rust 1.80+
- Tauri 2 系统依赖
- FFmpeg/FFprobe（媒体冒烟测试需要）

```powershell
cd desktop
npm ci
npm run tauri dev
```

## 验证

跨平台源码检查：

```bash
python scripts/check_repository_hygiene.py
python scripts/verify_source_release.py
python scripts/validate_spec_tasks.py
python scripts/validate_spec_v01.py
python scripts/validate_red_team.py
python scripts/validate_spec_v02.py
python scripts/validate_migration_v01_v02.py
python scripts/validate_quality_benchmark.py
python scripts/media_pipeline_smoke_test.py
npm --prefix desktop ci
npm --prefix desktop run verify
```

Windows 完整门禁：

```powershell
.\scripts\verify_product.ps1
```

该门禁执行项目卫生、源码与规范契约、迁移/质量/媒体验证、前端构建、Svelte 检查、Rust 格式、`cargo check` 和 Rust 测试。实验编译器使用 `cargo test --features compiler_v3`。

## 构建

```powershell
.\scripts\build_windows_release.ps1
```

安装包输出到：

```text
desktop/src-tauri/target/release/bundle/
```

## 本地数据路径

- 设置：`%APPDATA%\Video Notes AI\settings.json`
- 运行时组件：`%LOCALAPPDATA%\Video Notes AI\runtime\components`
- Job 状态：`%LOCALAPPDATA%\Video Notes AI\native-jobs.json`
- Capsule：`%LOCALAPPDATA%\Video Notes AI\.capsules`
- 默认导出：`Documents\Video Notes AI\exports`

## 安全与贡献

- 安全报告：[`SECURITY.md`](SECURITY.md)
- 贡献流程：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 支持说明：[`SUPPORT.md`](SUPPORT.md)
- 行为准则：[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- License：[`MIT`](LICENSE)

不要提交 API Key、Cookie、私人媒体、用户配置、运行时二进制、`node_modules`、`dist` 或 Rust `target`。
