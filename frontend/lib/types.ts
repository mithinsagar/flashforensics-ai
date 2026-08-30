export type VerdictStatus = "RECOVERABLE" | "PARTIAL" | "METADATA_ONLY" | "JUNK";

export interface AgentEvent {
  stage: string;
  message: string;
  percent: number;
  agent: string;
  data: Record<string, unknown>;
  timestamp: number;
}

export interface Validation {
  format_detected: string | null;
  header_valid: boolean;
  footer_present: boolean;
  structure_complete: boolean;
  confidence: number;
  evidence: string[];
  problems: string[];
  metadata: Record<string, unknown>;
  true_size: number | null;
}

export interface Classification {
  format: string;
  confidence: number;
  reasoning: string;
  alternatives: string[];
  method: string;
}

export interface Verdict {
  status: VerdictStatus;
  recoverable: boolean;
  confidence: number;
  explanation: string;
  user_priority: number;
  method: string;
}

export interface Fragment {
  fragment_id: string;
  rank?: number;
  offset: number;
  length: number;
  candidates: string[];
  ambiguity_group: string | null;
  entropy: number;
  chi_square: number;
  printable_ratio: number;
  sha256: string;
  header_hex: string;
  sector_start: number;
  sector_end: number;
  cluster_start: number | null;
  cluster_end: number | null;
  in_orphaned_region: boolean;
  format_guess: string;
  mime: string;
  category: string;
  validation: Validation | null;
  classification: Classification;
  verdict: Verdict;
  source: string;
  source_path: string | null;
  declared_size: number | null;
  chain_damage: unknown[];
}

export interface EntropyPoint {
  offset: number;
  length: number;
  mean: number;
  max: number;
  min: number;
  band: string;
}

export interface Anomaly {
  offset: number;
  kind: string;
  detail: string;
  severity: string;
}

export interface DamageReport {
  kind: string;
  detail: string;
  cluster: number | null;
  sector: number | null;
  path: string | null;
}

export interface SessionSummary {
  session_id: string;
  image_name: string;
  image_size: number;
  created_at: number;
  status: string;
  error: string | null;
  filesystem: string | null;
  fragments: number;
  recoverable: number;
  partial: number;
  elapsed_seconds: number | null;
}

export interface SessionDetail extends SessionSummary {
  stage_label: string;
  boot_sector: Record<string, string | number | boolean>;
  filesystem_summary: Record<string, number | string>;
  damage: DamageReport[];
  entropy: {
    points: EntropyPoint[];
    detail: { start: number; end: number; points: EntropyPoint[] };
    stats: Record<string, unknown>;
    anomalies: Anomaly[];
  };
  carve_stats: Record<string, number>;
  classification_stats: Record<string, number>;
  verdict_stats: {
    recoverable: number;
    partial: number;
    metadata_only: number;
    junk: number;
    total: number;
    formats: Record<string, number>;
    bytes_recoverable: number;
  };
  report: string;
  provider: Record<string, unknown>;
}

export interface Citation {
  fragment_id: string;
  format: string;
  offset: number;
  length: number;
  verdict: string;
  similarity: number;
  cited_in_answer: boolean;
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
  retrieved: number;
  filter_applied?: Record<string, unknown> | null;
}

export interface HealthResponse {
  status: string;
  version: string;
  llm: { provider: string; model?: string; available: boolean; note?: string };
  knowledge_base: { formats_indexed: number; embedding_model: string; semantic: boolean; note?: string };
  signatures: number;
  sessions_active: number;
}
