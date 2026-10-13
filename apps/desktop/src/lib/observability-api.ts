import { request } from "./api";

export interface ObservabilityStatus {
  enabled: boolean;
  connected: boolean;
  dashboard_url: string | null;
  collector_endpoint: string;
  project: string;
  project_id: string | null;
  capture_content: false;
}
export interface ObservabilitySummary {
  source: string;
  note: string;
  sample_size: number;
  outcomes: Record<string, number>;
  latency_seconds: { count: number; average: number | null; p50: number | null; p95: number | null; p99: number | null };
  ttft_seconds: { count: number; p95: number | null };
  usage: { input_tokens: number; output_tokens: number; tool_calls: number; tool_errors: number; estimated_cost_usd: null };
  recent: Array<{ trajectory_id: string; run_id: string | null; trace_id: string | null; created_at: string; outcome: string; duration_seconds: number | null; ttft_seconds: number | null }>;
}
export const observabilityApi = {
  summary: (): Promise<ObservabilitySummary> => request("/traces"),
  status: (): Promise<ObservabilityStatus> => request("/traces/status"),
};
