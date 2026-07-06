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
  { id: 2, title: "LLM 推理优化实践", status: "running", progress: 65, stage: "transcribing", input: "https://youtube.com/watch?v=yyy", created_at: "2026-07-05T08:30:00Z" },
  { id: 3, title: "本地视频测试", status: "paused", progress: 30, stage: "extracting_frames", input: "C:\\videos\\test.mp4", created_at: "2026-07-05T09:00:00Z" },
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
  { id: 1, name: "深度学习入门", item_count: 5, status: "completed" },
  { id: 2, name: "LLM 相关", item_count: 3, status: "processing" },
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
  transcription_backend: "whisper_cpp",
  whisper_model: "large-v3",
  whisper_model_dir: "",
  model_dir: "",
  language: "",
  frame_interval: 30,
  frame_mode: "fixed",
  max_frames: 30,
  ocr_enabled: false,
  ocr_backend: "tesseract",
  ocr_http_endpoint: "",
  ocr_http_api_key: "",
  vision_enabled: false,
  template: "default",
  template_id: "default",
  active_provider: "默认",
};

// ── Mock responses ─────────────────────────────────────

const mockResponses: Record<string, (params: any) => any> = {
  "system.info": () => ({
    shell_version: "1.2.0",
    engine_version: "1.2.0",
    protocol_version: 1,
    cuda_available: true,
    ffmpeg_available: true,
  }),
  "system.ping": () => "pong",
  "process.list": () => mockJobs,
  "process.get": (params: { job_id: number }) => mockJobs.find(j => j.id === params.job_id) || null,
  "process.start": (params: any) => {
    const id = Date.now();
    mockJobs.unshift({
      id,
      job_id: `mock-${id}`,
      title: params.title || null,
      status: "running",
      progress: 0,
      stage: "pending",
      input: params.input,
      created_at: new Date().toISOString(),
    } as any);
    return { job_id: id };
  },
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
  "settings.models.scan": () => ["small", "medium", "large-v3"],
  "settings.models.local": () => [
    { id: "small", path: "D:/models/ggml-small.bin", source: "configured" },
    { id: "medium", path: "D:/models/ggml-medium.bin", source: "configured" },
    { id: "large-v3-turbo", path: "D:/models/ggml-large-v3-turbo.gguf", source: "configured" },
  ],
  "doctor.run": () => [
    { name: "FFmpeg", status: "pass", detail: "mock" },
  ],
  "diagnostics.bundle": () => "./output/diagnostics/mock.json",
  "storage.status": () => ({
    export_dir: "D:\\VideoNotes\\exports",
    state_dir: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\state",
    db_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\state\\video_notes.db",
    jobs_root: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\jobs",
    legacy_jobs_root: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\.jobs",
    vault_path: mockSettings.vault_path,
    sizes: {
      export_bytes: 12582912,
      state_bytes: 524288,
      jobs_bytes: 3145728,
      legacy_jobs_bytes: 0,
      db_bytes: 262144,
      vault_bytes: 0,
    },
    counts: {
      jobs: { dirs: 2, files: 6 },
      legacy_jobs: { dirs: 0, files: 0 },
    },
  }),
  "storage.cleanup_orphans": () => ({ removed: 0, current: 0, legacy: 0 }),
  "storage.cleanup_completed": () => ({ removed: 0 }),
  "components.list": () => [
    {
      component: "download-tools",
      version: "1.5.0",
      description: "yt-dlp standalone executable",
      installed: false,
      installed_version: null,
      status: "not_installed",
      size_mb: 20,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\download-tools",
      provides: ["download"],
      missing_files: ["yt-dlp.exe"],
    },
    {
      component: "ffmpeg-tools",
      version: "1.5.0",
      description: "FFmpeg + FFprobe tools",
      installed: false,
      installed_version: null,
      status: "not_installed",
      size_mb: 50,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\ffmpeg-tools",
      provides: ["ffmpeg"],
      missing_files: ["ffmpeg.exe", "ffprobe.exe"],
    },
    {
      component: "whisper-cpp-tools",
      version: "1.5.0",
      description: "whisper.cpp native CLI transcription tools",
      installed: false,
      installed_version: null,
      status: "not_installed",
      size_mb: 50,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\whisper-cpp-tools",
      provides: ["transcription-native"],
      missing_files: ["whisper-cli.exe"],
    },
    {
      component: "tesseract-ocr-tools",
      version: "1.5.0",
      description: "Tesseract native executable OCR tools",
      installed: false,
      installed_version: null,
      status: "not_installed",
      size_mb: 120,
      component_path: "C:\\Users\\mock\\AppData\\Local\\Video Notes AI\\runtime\\components\\tesseract-ocr-tools",
      provides: ["ocr", "ocr-native"],
      missing_files: ["tesseract.exe", "tessdata/"],
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
