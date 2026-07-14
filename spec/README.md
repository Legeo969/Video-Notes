# Video Notes Learning Material Compiler Specification

**Specification line:** v0.2  
**Status:** Foundation Complete (`0.2.0-rc.3`)  
**Reference implementation:** Video Notes AI 2.1.x  
**Normative language:** `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, `MAY`

本目录定义一个开放的**学习材料编译器（Learning Material Compiler）**。Video Notes 是第一个参考应用，不是规范的全部边界。

学习材料编译器把视频、音频、幻灯片、文档等原始材料转换为可验证、可版本化、可复用的知识中间表示，再从同一份中间表示生成笔记、问答索引、学习指南、测验、图谱和其他 Artifact。

```text
Learning Material
    → Source Revision
    → Normalized Media / Document Units
    → Backend-owned Anchors
    → Evidence
    → Claims and Concepts
    → Immutable Capsule
    → Derived Artifacts
```

## 规范权威层级

发生冲突时，按以下顺序解释：

1. Project Charter；
2. Accepted RFC；
3. Versioned Specification；
4. JSON Schema / OpenAPI；
5. Conformance Tests；
6. Reference Implementation；
7. Examples and tutorials。

当前 v0.2 仍未提供公开 API 兼容承诺。任何冲突都必须被记录为规范缺陷，不能通过“实现已经如此”静默解决。

## 四个规范卷册

| 卷册 | 目录 | 主要问题 |
|---|---|---|
| Architecture | `architecture/` | 系统是什么、边界在哪里、谁信任谁 |
| Knowledge IR | `ir/` | 如何表示 Source、Anchor、Evidence、Claim、Capsule 与 Artifact |
| Compiler | `compiler/` | 如何规划、执行、重试、恢复、诊断和提交编译 |
| Evidence | `evidence/` | 什么可以称为证据、如何引用、如何处理冲突和不确定性 |

公共规范位于本目录根部：

- `glossary.md`：唯一术语定义；
- `normative-language.md`：规范语言；
- `invariants.md`：跨卷册不变量；
- `error-model.md`：诊断和错误分类；
- `compatibility.md`：版本和迁移；
- `security-model.md`：威胁模型与安全边界。

## v0.2 范围

v0.2 聚焦“长视频课程、讲座和操作演示”的可靠编译，同时保证 IR 不被视频格式锁死。PDF、PPT、网页和代码仓库可在后续 Source Adapter 中接入。

v0.2 不承诺：

- 通用网页研究代理；
- 实时多人协作；
- 移动客户端；
- 无网络情况下的完整语音理解；
- 未经标注语料验证的概率校准；
- 所有 Provider 具有等价模态能力。

## 规范性要求标识

每项可测试要求使用稳定标识：

```text
SPEC-ARCH-001
SPEC-IR-001
SPEC-COMPILER-001
SPEC-EVIDENCE-001
```

要求标识一经发布不得复用。删除要求时保留 tombstone，并在变更日志中记录替代项。

## 实现一致性

实现可以：

- 提供规范未定义的实验扩展；
- 使用不同数据库、模型或 UI；
- 优化内部算法。

实现不可以：

- 伪造 Source Anchor；
- 把模型输出的秒数当作权威时间；
- 静默丢弃失败区间；
- 覆盖已提交 Capsule；
- 把推断渲染成直接引用；
- 在没有 capability negotiation 时调用不支持的 Provider。

## Foundation Complete

所有 Foundation 门禁已于 2026-07-15 关闭：

1. ✅ **独立安全审查** — Tencent HanaAgent（非作者评审人）全部 25 个发现关闭。
2. ✅ **Rust 一致性** — `cargo check --features compiler_v3` 0 errors，11/11 测试通过。
3. ✅ **跨语言互操作** — Python/Rust 规范化字节、签名载荷、信任决策在 28 个 fixture 上完全匹配。
4. ✅ **语义质量基线** — 106 案例标注语料库（97 个真实 Unreal 教程视频 + 9 个补充案例），evidence precision 0.991 / recall 0.991。

详见 [`ROADMAP.md`](ROADMAP.md) 和 [`docs/FOUNDATION-STATUS-v0.2.0-rc.3.md`](../docs/FOUNDATION-STATUS-v0.2.0-rc.3.md)。
