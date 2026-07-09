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

/** Normalize any caught exception into a readable string. */
export function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
