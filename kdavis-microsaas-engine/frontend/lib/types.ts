export type SwarmStatus = "idle" | "running" | "complete" | "error";

export interface VerticalResult {
  vertical: string;
  status: SwarmStatus;
}

export interface SessionSummary {
  session_id: string;
  verticals_scanned: number;
  ready_to_build: number;
  validated_pending_review: number;
  watch_list: number;
  rejected: number;
  top_opportunity: string | null;
  recommended_first_build: string | null;
}

export interface Opportunity {
  id: string;
  vertical: string;
  pain_point: string;
  solution_concept: string;
  mrr_calculation: string | null;
  conservative_mrr_potential: number;
  build_confidence_score: number | null;
  competition_density: "red" | "yellow" | "green" | null;
  status: string;
  retention_hooks: Record<string, unknown>;
  source_urls: string[];
  created_at: string;
}

export interface AgentEvent {
  id: string;
  agent_name: string;
  department: string;
  action: string;
  verdict: "pass" | "flagged" | "pending";
  product: string | null;
  created_at: string;
}

export interface RetentionSequence {
  id: string;
  tenant_id: string;
  trigger: string;
  sequence_type: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  metadata: Record<string, unknown>;
}

export const MSE_VERTICALS = [
  "Healthcare / Medical Front Desk",
  "Legal / Professional Services",
  "E-commerce / Retail Ops",
  "Real Estate / Property Management",
  "HR / Ops / People Management",
  "Finance / Accounting / Bookkeeping",
] as const;

export const NAV_ITEMS = [
  { id: "overview",  label: "Overview",       path: "/dashboard" },
  { id: "swarm",     label: "Research Swarm", path: "/research" },
  { id: "pipeline",  label: "Pipeline",       path: "/pipeline" },
  { id: "agents",    label: "Agents",         path: "/agents" },
  { id: "retention", label: "Retention",      path: "/retention" },
] as const;
