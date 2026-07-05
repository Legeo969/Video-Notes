/**
 * Tauri API adapter — 自动选择真实 Tauri invoke 或 mock 模式
 */
import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen } from "@tauri-apps/api/event";
import { invoke as mockInvoke, listen as mockListen, isTauri } from "./mockTauri";

const useMock = !isTauri();

export const invoke = useMock ? mockInvoke : tauriInvoke;
export const listen = useMock ? mockListen : tauriListen;

export async function engineCall<T = unknown>(
  method: string,
  params: Record<string, unknown> = {}
): Promise<T> {
  return invoke<T>("engine_call", { method, params });
}