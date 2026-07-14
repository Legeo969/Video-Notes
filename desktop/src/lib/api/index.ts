/**
 * Typed Tauri adapter.
 *
 * Browser/Vite development uses the mock transport. Tauri mode always routes
 * business calls through the single `engine_call` command.
 */
import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen, type UnlistenFn } from "@tauri-apps/api/event";
import { invoke as mockInvoke, listen as mockListen, isTauri } from "./mockTauri";

const useMock = !isTauri();

export async function engineCall<T = unknown>(
  method: string,
  params: Record<string, unknown> = {}
): Promise<T> {
  if (useMock) {
    return mockInvoke<T>(method, params);
  }
  return tauriInvoke<T>("engine_call", { method, params });
}

/**
 * Convert the engine's logical dotted event names to Tauri-safe names.
 *
 * Tauri v2 rejects dots in event names; colons are allowed. Engine event
 * names stay logical (for example ``job.progress``), while the desktop
 * transport uses ``job:progress``.
 */
export function toTauriEventName(eventName: string): string {
  return eventName.replaceAll(".", ":");
}

/** Normalize Tauri's Event<T> wrapper and the browser mock into one payload API. */
export async function onEngineEvent<T>(
  eventName: string,
  handler: (payload: T) => void
): Promise<UnlistenFn> {
  if (useMock) {
    return mockListen<T>(eventName, handler);
  }
  return tauriListen<T>(toTauriEventName(eventName), (event) => handler(event.payload));
}

export interface EngineStatus {
  running: boolean;
  error?: string | null;
  startup_log?: string;
}

export async function getEngineStatus(): Promise<EngineStatus> {
  if (useMock) {
    return { running: true, error: null, startup_log: "" };
  }
  return tauriInvoke<EngineStatus>("get_engine_status");
}

export function runningInTauri(): boolean {
  return !useMock;
}

// ── v0.2 Bundle store ──────────────────────────────────────────

export interface StoredBundle {
  version: number;
  bundle_id: string;
  source_title: string;
  content_digest: string;
  created_at: string;
  status: string;
}

export async function listV02Versions(sourceHash: string): Promise<StoredBundle[]> {
  if (!runningInTauri()) return [];
  return tauriInvoke<StoredBundle[]>("list_v02_versions", { sourceHash });
}

/** Normalize any caught exception into a readable string. */
export function toErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return translateError(message);
}

/** Map common API / provider errors to human-readable Chinese messages. */
function translateError(msg: string): string {
  // Preserve already-friendly messages
  if (msg.startsWith("⚠️") || msg.includes("请")) return msg;

  const patterns: [RegExp, string][] = [
    [/413\b.*(?:Payload Too Large|请求体过大|太大)/i, "⚠️ 请求体过大：视频片段或音频数据超出供应商限制。可尝试缩短视频或在设置中更换支持大文件的供应商。"],
    [/403\b.*(?:Forbidden|insufficient_quota|quota)/i, "⚠️ API 配额不足或权限被拒：请检查供应商账户余额或 API Key 权限。"],
    [/429\b.*(?:Too Many Requests|rate limit|频率)/i, "⚠️ 请求过于频繁：供应商限流中，请等待几分钟后重试。"],
    [/401\b.*(?:Unauthorized|invalid.*api|api.*key|认证)/i, "⚠️ API Key 无效：请在设置中检查并更新供应商 API Key。"],
    [/502\b.*(?:Bad Gateway)/i, "⚠️ 供应商服务暂时不可用（502 Bad Gateway），请稍后重试。"],
    [/503\b.*(?:Service Unavailable)/i, "⚠️ 供应商服务暂时不可用（503），请稍后重试。"],
    [/504\b.*(?:Gateway Timeout)/i, "⚠️ 供应商请求超时（504），请稍后重试或更换其他模型。"],
    [/timeout/i, "⚠️ 请求超时：视频可能过长或供应商响应较慢。可尝试缩短视频或检查网络连接。"],
    [/cancelled/i, "⏹️ 任务已被取消。"],
    [/(?:ffmpeg|ffprobe).*(?:not found|不可用)/i, "⚠️ FFmpeg 未安装：请在「设置 → 插件管理」中安装 ffmpeg-tools。"],
    [/(?:yt-dlp).*(?:not found|不可用)/i, "⚠️ yt-dlp 未安装：请在「设置 → 插件管理」中安装 download-tools。"],
    [/file not found|no such file/i, "⚠️ 文件不存在：请检查输入路径是否正确，文件未被移动或删除。"],
    [/can't find|no such/i, "⚠️ 找不到文件或资源，请检查路径或链接是否正确。"],
    [/network|ECONNREFUSED|ECONNRESET|ENOTFOUND/i, "⚠️ 网络连接失败：请检查网络和代理设置。"],
    [/empty.*(?:response|content|result)/i, "⚠️ 供应商返回了空结果：请稍后重试或检查 API 状态。"],
    [/model.*(?:not found|unavailable|not supported)/i, "⚠️ 模型不可用：请在设置中检查当前选中的模型是否被供应商支持。"],
  ];

  for (const [regex, replacement] of patterns) {
    if (regex.test(msg)) return replacement;
  }

  // No translation matched: prefix with generic warning
  if (/error|fail|err|失败|错误/i.test(msg) && !msg.startsWith("⚠️")) {
    return `⚠️ ${msg}`;
  }

  return msg;
}
