/**
 * mockTauri.ts — 前端独立开发模式
 *
 * 当在浏览器中开发（非 Tauri 环境）时，使用此 mock 替换 @tauri-apps/api。
 * 让前端可以在没有 Rust/Python 后端的情况下独立开发和测试 UI。
 *
 * 用法：
 *   import { invoke, listen } from "./mockTauri";
 *
 * 环境检测：
 *   const isTauri = '__TAURI_INTERNALS__' in window;
 */

// ── Mock data ──────────────────────────────────────────

const mockJobs = [
  { id: 1, title: "Transformer 架构详解", status: "completed", progress: 1.0, stage: "done", input: "https://youtube.com/watch?v=xxx", created_at: "2026-07-04T10:00:00Z" },
  { id: 2, title: "LLM 推理优化实践", status: "running", progress: 0.65, stage: "transcribing", input: "https://youtube.com/watch?v=yyy", created_at: "2026-07-05T08:30:00Z" },
  { id: 3, title: "本地视频测试", status: "paused", progress: 0.3, stage: "extracting_frames", input: "C:\\videos\\test.mp4", created_at: "2026-07-05T09:00:00Z" },
];

const mockNotes: any[] = [
  {
    id: 1,
    title: "Transformer 架构详解",
    path: "./output/Transformer 架构详解/notes.md",
    created_at: "2026-07-04T10:30:00Z",
    content: `# Transformer 架构详解

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

\`\`\`python
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
  { id: 1, name: "深度学习入门", item_count: 5, status: "completed" },
  { id: 2, name: "LLM 相关", item_count: 3, status: "processing" },
];

// ── Mock responses ─────────────────────────────────────

const mockResponses: Record<string, (params: any) => any> = {
  "system.info": () => ({
    shell_version: "1.2.0",
    engine_version: "1.2.0",
    protocol_version: 1,
    python_version: "3.10",
    cuda_available: true,
    ffmpeg_available: true,
  }),
  "system.ping": () => "pong",
  "process.list": () => mockJobs,
  "process.get": (params: { id: number }) => mockJobs.find(j => j.id === params.id) || null,
  "process.start": (params: any) => ({ job_id: Date.now(), ...params }),
  "notes.list": () => mockNotes,
  "notes.get": (params: { id: number }) => {
    const note = mockNotes.find(n => n.id === params.id);
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
  "collection.list": () => mockCollections,
  "collection.get": (params: { id: number }) => mockCollections.find(c => c.id === params.id) || null,
  "settings.get": () => ({
    output_dir: "./output",
    model_dir: "",
    whisper_model: "large-v3",
    language: "",
    frame_interval: 30,
    frame_mode: "fixed",
    max_frames: 30,
    ocr_enabled: false,
    vision_enabled: false,
    providers: [
      { name: "默认", provider: "mimo", api_key_configured: true, api_key_preview: "sk-****8fa2", base_url: "", model: "mimo-v2.5" },
    ],
  }),
};

// ── Mock implementation ─────────────────────────────────

let eventListeners: Record<string, Array<(data: any) => void>> = {};
let eventIdCounter = 0;

// Simulate progress events for running jobs
setInterval(() => {
  const running = mockJobs.find(j => j.status === "running");
  if (running) {
    running.progress = Math.min(1, running.progress + 0.05);
    const listeners = eventListeners["job.progress"] || [];
    listeners.forEach(fn => fn({
      event_id: ++eventIdCounter,
      job_id: running.id,
      stage: running.stage,
      stage_progress: running.progress,
      overall_progress: running.progress * 0.7,
      message: `处理中... ${Math.round(running.progress * 100)}%`,
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