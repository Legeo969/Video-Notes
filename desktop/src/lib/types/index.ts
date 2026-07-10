export type PageName = "process" | "tasks" | "notes" | "collections" | "settings";

export type JobStatus =
  | "pending"
  | "running"
  | "pausing"
  | "cancelling"
  | "paused"
  | "interrupted"
  | "failed"
  | "cancelled"
  | "completed";

export interface JobInfo {
  id: number;
  job_id: string;
  title: string | null;
  status: JobStatus | string;
  progress: number; // 0..100
  progress_message?: string | null;
  stage: string;
  last_active_stage?: string | null;
  input: string;
  created_at?: string | null;
  completed_at?: string | null;
  elapsed_sec?: number;
  error_message?: string | null;
  output_path?: string | null;
  transcript_path?: string | null;
  frames_count?: number;
  note_id?: number | null;
  attempt?: number;
  parent_run_id?: string | null;
  settings_snapshot?: Record<string, unknown> | null;
  workspace_dir?: string | null;
  artifact_cleanup_policy?: string;
  can_resume?: boolean;
  heartbeat_at?: string | null;
}

export interface JobProgressEvent {
  event_id: number;
  job_id: number;
  stable_job_id?: string | null;
  status: JobStatus | string;
  stage: string;
  progress: number;
  message: string;
  timestamp?: string | null;
}

export interface NoteInfo {
  id: number;
  title: string;
  path: string;
  created_at: string;
}

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
  vision_model?: string;
  models?: string[];
  active?: boolean;
  capabilities?: Record<
    string,
    {
      text?: "pass" | "fail" | "unknown";
      vision?: "pass" | "fail" | "unknown";
      last_tested_at?: string | null;
      message?: string | null;
      error?: string | null;
    }
  >;
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

// Study Assistant types
export interface KnowledgeNode {
  id: string;
  label: string;
  kind: "chapter" | "section" | "concept";
  children: KnowledgeNode[];
}

export interface KnowledgeGraph {
  nodes: GraphNode[];
  relations: KnowledgeRelation[];
  source: "ai" | "markdown";
}

export interface GraphNode {
  id: string;
  name: string;
  nodeType: GraphNodeType;
  importance: number;
  summary: string;
  source: string;
}

export type GraphNodeType =
  | "concept" | "tool" | "method" | "technology"
  | "person" | "formula" | "problem" | "solution" | "chapter";

export interface KnowledgeRelation {
  sourceId: string;
  targetId: string;
  relationType: RelationType;
  confidence: number;
}

export type RelationType =
  | "depends_on" | "used_for" | "part_of" | "improves"
  | "replaces" | "conflicts_with" | "similar_to";

export interface QuizQuestion {
  question: string;
  choices: string[];
  correctIndex: number;
  explanation: string;
}
