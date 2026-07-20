/**
 * mockTauri.ts — 前端独立开发模式
 *
 * 当在浏览器中开发（非 Tauri 环境）时，使用此 mock 替换 @tauri-apps/api。
 * 让前端可以在没有 Tauri 后端的情况下独立开发和测试 UI。
 *
 * 用法：
 *   import { invoke, listen } from "./mockTauri";
 *
 * 环境检测：
 *   const isTauri = '__TAURI_INTERNALS__' in window;
 */

// ── Mock data ──────────────────────────────────────────

const mockJobs = [
  { id: 1, title: "Transformer 架构详解", status: "completed", progress: 100, stage: "done", input: "https://youtube.com/watch?v=xxx", created_at: "2026-07-04T10:00:00Z" },
  { id: 2, title: "LLM 推理优化实践", status: "running", progress: 65, stage: "compiling", input: "https://youtube.com/watch?v=yyy", created_at: "2026-07-05T08:30:00Z" },
  { id: 3, title: "本地视频测试", status: "paused", progress: 30, stage: "extracting_frames", input: "C:\\videos\\test.mp4", created_at: "2026-07-05T09:00:00Z" },
];

const mockNotes: any[] = [
  {
    id: 1,
    title: "Transformer 架构详解",
    path: "./output/Transformer 架构详解/notes.md",
    created_at: "2026-07-04T10:30:00Z",
    content: `---
video_notes_source_hash: mocksource
video_notes_version: 1
video_notes_capsule_id: mocksource_1
video_notes_ir_schema: 2
---

# Transformer 架构详解

## 核心概念

Transformer 是一种基于自注意力（Self-Attention）机制的神经网络架构。

### 自注意力机制

$$attention(Q,K,V) = softmax\\frac{QK^T}{\\sqrt{d_k}}V$$

## 编码器-解码器结构

### 编码器
- 多头自注意力层
- 前馈神经网络
- 残差连接 + 层归一化

### 解码器
- 掩码多头自注意力
- 编码器-解码器注意力
- 前馈神经网络

## 位置编码

使用正弦和余弦函数：

$$PE(pos, 2i) = sin(pos/10000^{2i/d_{model}})$$

> Transformer 的成功证明了纯注意力机制可以替代 RNN。
`,
  },
  {
    id: 2,
    title: "RLHF 原理与实践",
    path: "./output/RLHF 原理与实践/notes.md",
    created_at: "2026-07-03T15:00:00Z",
    content: `# RLHF 原理与实践

## 三阶段流程

1. **SFT** — 监督微调
2. **RM** — 奖励模型训练
3. **RL** — 强化学习优化

### PPO 算法

PPO 通过剪切概率比来约束策略更新。

\`\`\`text
def ppo_update(states, actions, old_log_probs, rewards):
    # 计算新策略概率
    log_probs = policy(states).log_prob(actions)
    ratio = (log_probs - old_log_probs).exp()

    # 剪切目标
    clipped = ratio.clamp(0.8, 1.2) * rewards
    loss = -min(ratio * rewards, clipped).mean()
    return loss
\`\`\`

## 关键技巧

- **KL 散度惩罚**：防止策略偏离 SFT 模型太远
- **Reward scaling**：对 reward 进行归一化
- **GAE**：广义优势估计

| 方法 | 优点 | 缺点 |
|------|------|------|
| PPO | 稳定 | 超参数敏感 |
| DPO | 简单 | 需成对偏好数据 |

## 实践建议

1. 使用较小的学习率
2. 对 reward model 进行充分训练
3. 在 SFT 阶段收集高质量的 demonstration 数据
`,
  },
];

const mockCollections = [
  {
    id: 1,
    name: "深度学习入门",
    item_count: 5,
    status: "completed",
    items: [
      { id: 1, input: "D:/courses/deep-learning/01-transformer.mp4", title: "Transformer 架构详解", status: "completed", progress: 100, run_id: 1 },
      { id: 2, input: "D:/courses/deep-learning/02-attention.mp4", title: "注意力机制", status: "completed", progress: 100, run_id: 2 },
      { id: 3, input: "D:/courses/deep-learning/03-training.mp4", title: "模型训练流程", status: "completed", progress: 100, run_id: 3 },
      { id: 4, input: "D:/courses/deep-learning/04-evaluation.mp4", title: "模型评估", status: "completed", progress: 100, run_id: 4 },
      { id: 5, input: "D:/courses/deep-learning/05-deployment.mp4", title: "部署实践", status: "completed", progress: 100, run_id: 5 },
    ],
  },
  {
    id: 2,
    name: "LLM 相关",
    item_count: 3,
    status: "processing",
    items: [
      { id: 6, input: "https://example.com/llm-01", title: "LLM 推理优化", status: "running", progress: 65, run_id: 6 },
      { id: 7, input: "https://example.com/llm-02", title: "提示工程", status: "pending", progress: 0 },
      { id: 8, input: "https://example.com/llm-03", title: "模型对齐", status: "failed", progress: 25, run_id: 8 },
    ],
  },
];

const mockProviders: any[] = [
  {
    name: "默认",
    provider: "mimo",
    api_key_configured: true,
    api_key_preview: "sk-****8fa2",
    base_url: "",
    model: "mimo-v2.5",
    vision_model: "mimo-v2.5",
    models: ["mimo-v2.5"],
    active: true,
  },
];

const mockTemplates = [
  { id: "default", name: "通用总结", description: "适合一般视频内容", path: "builtin://default" },
  { id: "study", name: "学习笔记", description: "适合课程与知识讲解", path: "builtin://study" },
  { id: "meeting", name: "会议纪要", description: "适合会议和复盘", path: "builtin://meeting" },
];

let mockSettings: any = {
  output_dir: "D:\\VideoNotes\\exports",
  vault_path: "",
  template: "default",
  template_id: "default",
  active_provider: "默认",
};

// ── Mock responses ─────────────────────────────────────

const mockResponses: Record<string, (params: any) => any> = {
  "system.info": () => ({
    shell_version: "2.1.0",
    engine_version: "2.1.0",
    protocol_version: 1,
    cuda_available: true,
    ffmpeg_available: true,
  }),
  "system.ping": () => "pong",
  "process.list": () => mockJobs,
  "process.pause": (params: { job_id: number }) => {
    const job = mockJobs.find(j => j.id === params.job_id);
    if (job) job.status = "paused";
    return true;
  },
  "process.cancel": (params: { job_id: number }) => {
    const job = mockJobs.find(j => j.id === params.job_id);
    if (job) job.status = "cancelled";
    return true;
  },
  "process.resume": (params: { job_id: number }) => {
    const job = mockJobs.find(j => j.id === params.job_id);
    if (job) job.status = "running";
    return { job_id: params.job_id };
  },
  "process.retry": (params: { job_id: number }) => ({ job_id: params.job_id + 1000 }),
  "compile.video": (params: any) => {
    const id = Math.max(0, ...mockJobs.map(job => job.id)) + 1;
    mockJobs.unshift({
      id,
      title: params.title || "Mock compile",
      status: "running",
      progress: 5,
      stage: "sampling",
      input: params.input || "",
      created_at: new Date().toISOString(),
    });
    return { job_id: id };
  },
  "compile.list_versions": () => [
    { version: 1, created_at: "2026-07-11T12:00:00Z", model_used: "mimo-v2.5" },
    { version: 2, created_at: "2026-07-12T12:00:00Z", model_used: "mimo-v2.5" },
  ],
  "compile.render": (params: { source_hash: string; version: number }) => ({
    content: `---
video_notes_source_hash: ${params.source_hash}
video_notes_version: ${params.version}
video_notes_capsule_id: ${params.source_hash}_${params.version}
video_notes_ir_schema: 2
---

# Transformer 架构详解 — v${params.version}

## 全局摘要

这是浏览器开发模式下的版本 ${params.version} 示例。`,
    capsule_id: `${params.source_hash}_${params.version}`,
    template: "markdown",
  }),
  "compile.replay": (params: { source_hash: string; version: number }) => ({
    ir_schema_version: 2,
    capsule_id: `${params.source_hash}_${params.version}`,
    source_hash: params.source_hash,
    source_title: "Mock Video Title",
    version: params.version,
    total_duration: 3600.0,
    processed_at: "2026-07-11T12:00:00Z",
    model_used: "mimo-v2.5",
    evidences: [
      {
        id: "ev_mock_001",
        content: "Transformer uses self-attention mechanism to process sequential data without recurrence, enabling parallel computation across all positions.",
        timestamp_start_sec: 120.5,
        timestamp_end_sec: 145.3,
        evidence_type: "concept",
        speaker: "Lecturer",
        confidence: 0.95,
      },
      {
        id: "ev_mock_002",
        content: "The attention score is computed as softmax(QK^T / sqrt(d_k)), where the scaling factor prevents vanishing gradients in the softmax.",
        timestamp_start_sec: 320.0,
        timestamp_end_sec: 350.8,
        evidence_type: "fact",
        speaker: "Lecturer",
        confidence: 0.92,
      },
      {
        id: "ev_mock_003",
        content: "To train a Transformer model, you need to set up the learning rate scheduler with warmup steps, typically 4000 steps for the base model.",
        timestamp_start_sec: 890.2,
        timestamp_end_sec: 920.0,
        evidence_type: "procedure",
        speaker: "TA",
        confidence: 0.78,
      },
      {
        id: "ev_mock_004",
        content: "One common failure mode is when the model diverges due to learning rate being too high during the initial warmup phase.",
        timestamp_start_sec: 1500.0,
        timestamp_end_sec: 1525.3,
        evidence_type: "failure",
        speaker: null,
        confidence: 0.65,
      },
      {
        id: "ev_mock_005",
        content: "We verified that the attention patterns align with the expected syntactic structure in 92% of test cases.",
        timestamp_start_sec: 2100.0,
        timestamp_end_sec: 2120.0,
        evidence_type: "verification",
        speaker: "Reviewer",
        confidence: 0.88,
      },
    ],
    global_summary: "This video covers the Transformer architecture, including self-attention, training procedures, common failure modes, and verification results.",
    warnings: [],
  }),
  "notes.list": () => mockNotes,
  "notes.tree": () => {
    const folderSet = new Set<string>();
    const out: any[] = [];
    for (const note of mockNotes) {
      const segments = String(note.path ?? '').split(/[\\/]/);
      segments.pop();
      const folder = segments.join('\\');
      if (folder) folderSet.add(folder);
      out.push({
        id: note.id,
        title: note.title,
        path: note.path,
        folder,
        created_at: note.created_at,
        modified_at: null,
      });
    }
    const folders = Array.from(folderSet)
      .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))
      .map((path) => ({ path, name: path.split(/[\\/]/).pop() ?? path }));
    return { folders, notes: out };
  },
  "notes.get": (params: { id?: number; note_id?: number }) => {
    const note = mockNotes.find(n => n.id === (params.note_id ?? params.id));
    if (!note) return null;
    return { id: note.id, title: note.title, content: note.content, path: note.path };
  },
  "notes.search": (params: { query: string }) => {
    if (!params.query) return mockNotes;
    const q = params.query.toLowerCase();
    return mockNotes.filter(n => n.title.toLowerCase().includes(q));
  },
  "notes.update": (params: { id: number, content: string }) => {
    const note = mockNotes.find(n => n.id === params.id);
    if (note) note.content = params.content;
    return { success: true };
  },
  "notes.delete": (params: { id: number }) => {
    const idx = mockNotes.findIndex(n => n.id === params.id);
    if (idx !== -1) mockNotes.splice(idx, 1);
    return { success: true };
  },
  "notes.open": (params: { id: number }) => {
    console.log(`[mock] open note ${params.id} in system editor`);
    return { success: true };
  },
  "notes.reveal": (params: { id: number }) => {
    console.log(`[mock] reveal note ${params.id} in file explorer`);
    return { success: true };
  },
  "system.open_url": (params: any) => {
    console.log(`[mock] open url ${params.url}`);
    return true;
  },
  "collection.list": () => mockCollections,
  "collection.get": (params: { id: number }) => mockCollections.find(c => c.id === params.id) || null,
  "settings.get": () => ({ ...mockSettings, providers: mockProviders }),
  "settings.update": (params: any) => {
    mockSettings = { ...mockSettings, ...(params?.patches ?? params ?? {}) };
    if (mockSettings.template) mockSettings.template_id = mockSettings.template;
    return true;
  },
  "settings.providers.list": () => mockProviders,
  "settings.providers.create": (params: any) => {
    const profile = {
      name: params.name,
      provider: params.provider ?? "openai_compat",
      api_key_configured: Boolean(params.api_key),
      api_key_preview: params.api_key ? "sk-****mock" : "",
      base_url: params.base_url ?? "",
      model: params.model ?? "",
      vision_model: params.vision_model ?? params.model ?? "",
      models: [params.model, params.vision_model].filter(Boolean),
      active: false,
    };
    mockProviders.push(profile);
    return true;
  },
  "settings.providers.update": (params: any) => {
    const profile = mockProviders.find(p => p.name === params.name);
    if (profile) Object.assign(profile, params);
    return true;
  },
  "settings.providers.delete": (params: any) => {
    const index = mockProviders.findIndex(p => p.name === params.name);
    if (index >= 0) mockProviders.splice(index, 1);
    return true;
  },
  "settings.providers.set_active": (params: any) => {
    mockSettings.active_provider = params.name;
    mockProviders.forEach(p => p.active = p.name === params.name);
    return true;
  },
  "settings.providers.test": () => ({ success: true, message: "Mock 连接成功" }),
  "settings.providers.models": () => ["qwen-plus", "qwen-max", "qwen-vl-max", "gpt-4o"],
  "settings.secret.set": (params: any) => {
    const profile = mockProviders.find(p => p.name === params.provider);
    if (profile) {
      profile.api_key_configured = true;
      profile.api_key_preview = "sk-****mock";
    }
    return true;
  },
  "settings.secret.delete": (params: any) => {
    const profile = mockProviders.find(p => p.name === params.provider);
    if (profile) {
      profile.api_key_configured = false;
      profile.api_key_preview = "";
    }
    return true;
  },
  "settings.templates.list": () => mockTemplates,
  "doctor.run": () => [
    { name: "FFmpeg", status: "pass", detail: "mock" },
  ],
  "diagnostics.bundle": () => "./output/diagnostics/mock.json",
  "storage.status": () => ({
    export_dir: "D:\\VideoNotes\\exports",
    jobs_root: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\jobs",
    legacy_jobs_root: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\.jobs",
    vault_path: mockSettings.vault_path,
    sizes: {
      exports: 12582912,
      jobs: 3145728,
      legacy_jobs: 0,
      runtime: 734003200,
    },
    counts: {
      exports: { dirs: 1, files: 8 },
      jobs: { dirs: 2, files: 6 },
      legacy_jobs: { dirs: 0, files: 0 },
      runtime: { dirs: 5, files: 12 },
    },
    tasks: {
      total: 3,
      running: 1,
      completed: 1,
      failed: 1,
    },
  }),
  "storage.cleanup_orphans": () => ({ removed: 0 }),
  "storage.cleanup_completed": () => ({ removed: 0 }),
  "storage.cleanup_capsules": () => ({ removed: 0 }),
  "study.knowledge": (params: { note_id: number }) => ({
    entities: [
      { id: "transformer-architecture", name: "Transformer Architecture", entityType: "concept", importance: 5, summary: "Core architecture of modern LLMs", aliases: ["Transformer"], sourceRefs: [{ noteId: "", chapter: "Section 1", timestamp: null, quote: "" }] },
      { id: "self-attention", name: "Self-Attention", entityType: "concept", importance: 5, summary: "Mechanism that weighs input tokens against each other", aliases: [], sourceRefs: [{ noteId: "", chapter: "1.1", timestamp: null, quote: "" }] },
      { id: "multi-head-attention", name: "Multi-Head Attention", entityType: "concept", importance: 4, summary: "Parallel attention heads capture different relationships", aliases: ["MHA"], sourceRefs: [{ noteId: "", chapter: "1.2", timestamp: null, quote: "" }] },
      { id: "positional-encoding", name: "Positional Encoding", entityType: "method", importance: 4, summary: "Adds position information to token embeddings", aliases: ["PE"], sourceRefs: [] },
      { id: "ffn", name: "Feed-Forward Network", entityType: "method", importance: 3, summary: "Per-token MLP transformation", aliases: ["FFN"], sourceRefs: [] },
      { id: "training-optimization", name: "Training & Optimization", entityType: "concept", importance: 4, summary: "How transformers are trained", aliases: [], sourceRefs: [] },
      { id: "cross-entropy-loss", name: "Cross-Entropy Loss", entityType: "method", importance: 3, summary: "Loss function for language modeling", aliases: [], sourceRefs: [] },
      { id: "adam-optimizer", name: "Adam Optimizer", entityType: "tool", importance: 3, summary: "Adaptive learning rate optimizer", aliases: ["Adam"], sourceRefs: [] },
    ],
    relations: [
      { source: "self-attention", target: "transformer-architecture", relationType: "part_of", confidence: 1.0, evidence: "Self-attention is the core component of Transformer" },
      { source: "multi-head-attention", target: "transformer-architecture", relationType: "part_of", confidence: 1.0, evidence: "" },
      { source: "positional-encoding", target: "transformer-architecture", relationType: "part_of", confidence: 1.0, evidence: "" },
      { source: "ffn", target: "transformer-architecture", relationType: "part_of", confidence: 1.0, evidence: "" },
      { source: "cross-entropy-loss", target: "training-optimization", relationType: "part_of", confidence: 1.0, evidence: "" },
      { source: "adam-optimizer", target: "training-optimization", relationType: "part_of", confidence: 1.0, evidence: "" },
      { source: "self-attention", target: "multi-head-attention", relationType: "improves", confidence: 0.8, evidence: "Multi-head attention extends self-attention" },
      { source: "positional-encoding", target: "self-attention", relationType: "depends_on", confidence: 0.7, evidence: "Positional encoding is needed for attention to distinguish token positions" },
    ],
    chapters: [
      { title: "Transformer Architecture", entityIds: ["self-attention", "multi-head-attention", "positional-encoding", "ffn"] },
      { title: "Training & Optimization", entityIds: ["cross-entropy-loss", "adam-optimizer"] },
    ],
    source: "ai",
  }),
  "study.quiz": (params: { note_id: number }) => {
    return [
      { question: "Transformer 的核心机制是什么？", choices: ["循环神经网络", "自注意力机制", "卷积神经网络", "生成对抗网络"], correctIndex: 1, explanation: "Transformer 的核心是自注意力（Self-Attention）机制，它允许模型在处理序列时直接关注所有位置。" },
      { question: "Transformer 编码器中的残差连接有什么作用？", choices: ["加速训练收敛", "防止梯度消失", "两者都是", "两者都不是"], correctIndex: 2, explanation: "残差连接既能帮助梯度在深层网络中传播（防止梯度消失），也能加速训练收敛。" },
      { question: "注意力公式中为什么要除以 √d_k？", choices: ["增加数值稳定性", "使概率分布更尖锐", "减少计算量", "标准化输入"], correctIndex: 0, explanation: "除以 √d_k 是缩放点积注意力的关键步骤，防止点积值过大导致 softmax 输出过于尖锐。" },
      { question: "Transformer 位置编码的作用是？", choices: ["提供词嵌入", "注入位置信息", "加速计算", "减少参数量"], correctIndex: 1, explanation: "由于 Transformer 没有循环结构，需要额外注入位置信息来区分不同位置的 token。" },
      { question: "Transformer 为什么比 RNN 更适合并行计算？", choices: ["参数更少", "使用注意力机制", "不需要梯度", "使用卷积操作"], correctIndex: 1, explanation: "注意力机制允许同时计算所有位置的关联，而 RNN 必须按顺序逐步计算。" },
    ];
  },
  "components.list": () => [
    {
      component: "download-tools",
      version: "1.5.7",
      description: "yt-dlp standalone executable",
      installed: false,
      installed_version: null,
      latest_version: null,
      update_available: false,
      status: "not_installed",
      size_mb: 20,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\download-tools",
      provides: ["download"],
      missing_files: ["yt-dlp.exe"],
      downloadable: true,
    },
    {
      component: "ffmpeg-tools",
      version: "1.5.7",
      description: "FFmpeg + FFprobe tools",
      installed: false,
      installed_version: null,
      latest_version: null,
      update_available: false,
      status: "not_installed",
      size_mb: 50,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\ffmpeg-tools",
      provides: ["ffmpeg"],
      missing_files: ["ffmpeg.exe", "ffprobe.exe"],
      downloadable: true,
    },
    {
      component: "mpv-tools",
      version: "0.41.0-dev-g94335ab87",
      description: "mpv first-party Windows MSVC build for direct local playback",
      installed: true,
      installed_version: null,
      latest_version: null,
      update_available: false,
      status: "integrity_failed",
      size_mb: 27,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\mpv-tools",
      provides: ["video-playback", "timestamp-seek"],
      missing_files: [],
      downloadable: true,
    },
  ],
  "components.install": (params: any) => ({ ok: true, component: params.component, status: "installed" }),
  "components.verify": (params: any) => ({ ok: false, components: [{ component: params.component, ok: false, status: "not_installed" }] }),
  "components.remove": (params: any) => ({ ok: true, component: params.component, status: "removed" }),
};

// ── Mock implementation ─────────────────────────────────

let eventListeners: Record<string, Array<(data: any) => void>> = {};
let eventIdCounter = 0;

// Simulate progress events for running jobs
setInterval(() => {
  const running = mockJobs.find(j => j.status === "running");
  if (running) {
    running.progress = Math.min(100, running.progress + 5);
    const listeners = eventListeners["job.progress"] || [];
    listeners.forEach(fn => fn({
      event_id: ++eventIdCounter,
      job_id: running.id,
      stage: running.stage,
      status: running.status,
      progress: running.progress,
      message: `处理中... ${Math.round(running.progress)}%`,
      timestamp: new Date().toISOString(),
    }));
  }
}, 2000);

export async function invoke<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T> {
  // Simulate network delay
  await new Promise(r => setTimeout(r, 100 + Math.random() * 200));

  const handler = mockResponses[method];
  if (handler) {
    return handler(params || {}) as T;
  }

  console.warn(`[mockTauri] Unhandled method: ${method}`, params);
  return null as T;
}

export async function listen<T = unknown>(event: string, handler: (data: T) => void): Promise<() => void> {
  if (!eventListeners[event]) {
    eventListeners[event] = [];
  }
  eventListeners[event].push(handler as (data: any) => void);

  return () => {
    eventListeners[event] = eventListeners[event].filter(h => h !== handler);
  };
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
