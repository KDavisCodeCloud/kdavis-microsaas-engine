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
  rejection_reason: string | null;
  retention_hooks: Record<string, unknown>;
  source_urls: string[];
  verdict_v2_output: Record<string, unknown> | null;
  human_review_status: "pending" | "approved" | "rejected";
  human_review_comment: string | null;
  human_reviewed_by: string | null;
  human_reviewed_at: string | null;
  created_at: string;
}

export interface BuildBrief {
  id: string;
  opportunity_id: string | null;
  product_name: string;
  product_slug: string;
  verdict_score: number | null;
  vertical: string;
  claude_code_brief: { markdown?: string } | null;
  claude_design_brief: { markdown?: string } | null;
  repo_branch: string | null;
  status: string;
  activated_monitoring: boolean;
  mrr_at_activation: number | null;
  mrr_sustained_days: number | null;
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

export interface ApolloLead {
  id: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  company: string | null;
  title: string | null;
  linkedin_url: string | null;
  status: string;
  linkedin_contacted_at: string | null;
  created_at: string;
}

export interface DmSequence {
  id: string;
  lead_id: string;
  product_id: string;
  campaign_build_id: string;
  touch_1: string;
  touch_2: string;
  status: string;
  hitl_approved_by: string | null;
  hitl_approved_at: string | null;
  created_at: string;
  mse_apollo_leads: ApolloLead | null;
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
  { id: "pipeline",  label: "Opportunities",  path: "/pipeline" },
  { id: "outreach",  label: "Outreach",       path: "/outreach" },
  { id: "agents",    label: "Agents",         path: "/agents" },
  { id: "retention", label: "Retention",      path: "/retention" },
] as const;
