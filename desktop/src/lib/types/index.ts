export type PageName = "process" | "tasks" | "notes" | "collections" | "settings";

export interface JobInfo {
  id: number;
  title: string;
  status: string;
  progress: number;
  stage: string;
  input: string;
}

export interface NoteInfo {
  id: number;
  title: string;
  path: string;
  created_at: string;
}

/** Full note detail returned by notes.get */
export interface NoteDetail {
  id: number;
  title: string;
  content: string;
  path: string;
}

export interface CollectionInfo {
  id: number;
  name: string;
  item_count: number;
  status: string;
}

export interface ProviderProfile {
  name: string;
  provider: string;
  api_key_configured: boolean;
  api_key_preview: string;
  base_url: string;
  model: string;
}

export interface RpcRequest {
  jsonrpc: "2.0";
  protocol_version: number;
  id: number;
  method: string;
  params: Record<string, unknown>;
}

export interface RpcResponse<T = unknown> {
  jsonrpc: "2.0";
  id: number;
  result?: T;
  error?: {
    code: string;
    message: string;
    retryable: boolean;
  };
}

export interface RpcEvent {
  jsonrpc: "2.0";
  method: string;
  params: Record<string, unknown>;
}