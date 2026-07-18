# 安全政策

## 支持范围

当前维护分支为 `2.1.x`。旧版本仅在迁移风险较低时接受安全回补。

## 报告漏洞

请不要在公开 Issue 中披露可利用细节。通过仓库的私密安全报告渠道提交以下信息：

- 受影响版本与平台；
- 最小复现步骤；
- 预期影响与攻击前提；
- 可行的缓解建议；
- 是否包含用户媒体、API Key、Cookie 或其他敏感数据。

维护者确认后会进行分级、修复、回归验证和发布说明。未建立私密报告渠道前，请联系仓库维护者并仅发送最小必要信息。

## 安全边界

视频、URL、模型输出、Markdown、运行时组件和诊断包均视为不可信输入。安全相关变更必须符合 [`spec/security-model.md`](spec/security-model.md)。

## Exchange bundle trust

A valid Ed25519 signature is not sufficient for import. Applications must provide an external TrustPolicy. Unknown, revoked, key-substituted, out-of-scope, or out-of-window signers are rejected. Test trust keys under `conformance/` are fixtures only and must never be reused in production.
