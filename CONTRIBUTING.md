# 贡献指南

感谢参与 Video Notes AI。项目采用规范、Task 与测试驱动的维护方式；代码实现、公开契约和迁移策略必须保持可追踪。

## 开始之前

1. 阅读 [`README.md`](README.md)、[`AGENTS.md`](AGENTS.md) 和 [`spec/README.md`](spec/README.md)。
2. 选择或新增一个 [`tasks/`](tasks/index.json) 中的 Task JSON，明确允许路径、禁止变更和验收测试。
3. 涉及公开数据格式、跨模块协议、安全边界、持久化兼容或发布策略的改动，必须先更新版本化规范，并在 Task 中记录影响和验收。
4. 仅修复局部实现且不改变稳定契约的改动，可以直接提交 Pull Request，但应附测试和变更说明。

## 本地验证

```powershell
python scripts/check_repository_hygiene.py
python scripts/verify_source_release.py
python scripts/validate_spec_tasks.py
python scripts/validate_spec_v01.py
python scripts/validate_red_team.py
python scripts/validate_spec_v02.py
python scripts/media_pipeline_smoke_test.py
.\scripts\verify_product.ps1
```

前端开发：

```powershell
cd desktop
npm ci
npm run dev
```

## 变更要求

- 不得提交 `node_modules`、`dist`、`target`、运行时下载包、用户设置、API Key、Cookie 或媒体样本。
- Rust 持久化字段必须提供兼容默认值，并附旧数据读取测试。
- Provider 改动必须声明输入模态、输出格式和请求预算。
- 媒体时间戳必须来自后端 PTS 或后端音频窗口，禁止接受模型生成的物理秒数。
- 云端失败必须明确降级，不得把视觉草稿伪装成完整音视频理解结果。
- 新功能应更新 `CHANGELOG.md` 的 `Unreleased` 区域。

## Pull Request

Pull Request 应说明：Task ID、受影响的 `SPEC-*` 要求、问题、方案、兼容性影响、安全影响、测试证据和关联 Issue。一个 PR 尽量只完成一个可独立审查的目标。

## Specification validation environment

Install the pinned validation dependencies before running Schema or Red Team gates:

```bash
python -m pip install --requirement requirements-dev.txt
python scripts/validate_spec_tasks.py
python scripts/validate_spec_v01.py
python scripts/validate_red_team.py
python scripts/validate_spec_v02.py
```
