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
  note_id?: number | null;
  collection_id?: number | null;
  collection_item_id?: number | null;
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
  completed_at?: string | null;
  error_message?: string | null;
  output_path?: string | null;
  transcript_path?: string | null;
  note_id?: number | null;
  can_resume?: boolean;
  collection_id?: number | null;
  collection_item_id?: number | null;
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

// ── Evidence & VideoCapsule (from compile module) ──────────────

export interface Evidence {
  id: string;
  content: string;
  timestamp_start_sec: number;
  timestamp_end_sec: number;
  evidence_type: string;
  speaker: string | null;
  confidence: number;
}

export interface VideoCapsule {
  capsule_id: string;
  source_hash: string;
  source_title: string;
  version: number;
  total_duration: number;
  processed_at: string;
  model_used: string;
  evidences: Evidence[];
  global_summary: string;
  compilation_mode: string;
  warnings: string[];
}

export interface ProviderProfile {
  name: string;
  provider: string;
  api_key_configured: boolean;
  api_key_preview: string;
  base_url: string;
  model: string;
  vision_model?: string;
  video_input?: boolean;
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

// ── Legacy tree type (for TreeAdapter compatibility) ──────────────
export interface KnowledgeNode {
  id: string;
  label: string;
  kind: "chapter" | "section" | "concept";
  children: KnowledgeNode[];
}

// ── V2 Knowledge Graph types ─────────────────────────────────────

export interface KnowledgeGraph {
  entities: Entity[];
  relations: Relation[];
  chapters: Chapter[];
  source: "ai" | "markdown";
}

export interface Entity {
  id: string;
  name: string;
  entityType: EntityType;
  summary: string;
  importance: number;
  aliases: string[];
  sourceRefs: SourceRef[];
}

export type EntityType =
  | "concept" | "tool" | "technology" | "workflow" | "asset"
  | "library" | "method" | "person" | "organization"
  | "problem" | "solution";

export interface Relation {
  source: string;
  target: string;
  relationType: RelationType;
  confidence: number;
  evidence: string;
}

export type RelationType =
  | "uses" | "depends_on" | "part_of" | "implements"
  | "improves" | "generates" | "imports" | "exports"
  | "related_to" | "similar_to" | "conflicts_with"
  | "requires" | "produces" | "consumes";

export interface Chapter {
  title: string;
  entityIds: string[];
}

export interface SourceRef {
  noteId: string;
  chapter: string;
  timestamp: number | null;
  quote: string;
}

export interface QuizQuestion {
  question: string;
  choices: string[];
  correctIndex: number;
  explanation: string;
}

// ── Evidence-grounded Q&A ────────────────────────────────────

export interface AnswerResponse {
  answer: string;
  citations: number[];
  confidence: "high" | "medium" | "low";
}

// ── Document import ──────────────────────────────────────────

export interface TextDocument {
  title: string | null;
  source_type: string;
  chunks: TextChunk[];
  total_chars: number;
}

export interface TextChunk {
  index: number;
  heading: string | null;
  heading_level: number | null;
  text: string;
  char_offset: number;
  char_length: number;
}

export interface WebPage {
  url: string;
  title: string | null;
  content_type: string;
  text: string;
  raw_length: number;
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
