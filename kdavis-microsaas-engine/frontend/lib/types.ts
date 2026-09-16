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
  // Joined via opportunity_id's FK to opportunity_pipeline (migration
  // 20260717000011) -- what a brief actually IS, shown directly in the
  // dashboard instead of forcing a click into a 10k+ char raw markdown
  // document just to answer "what is this". Supabase returns this as an
  // array even for a to-one FK relationship unless the relationship is
  // explicitly disambiguated; both shapes are handled where this is used.
  opportunity_pipeline: BuildBriefOpportunity | BuildBriefOpportunity[] | null;
}

type BuildBriefOpportunity = {
  pain_point: string | null;
  solution_concept: string;
  mrr_calculation: string | null;
  conservative_mrr_potential: number;
  human_review_status: string;
};

// build_tasks (migration 20260806000019) -- the per-product build
// checklist team.thdstack.com writes to (mark complete + notes). This
// dashboard reads it read-only: the actual doing/checking-off happens on
// the team dashboard, this just reflects live progress.
export interface BuildTask {
  id: string;
  opportunity_id: string;
  task_type: "standard" | "custom";
  title: string;
  status: "pending" | "in_progress" | "completed";
  notes: string | null;
  completed_by: string | null;
  completed_at: string | null;
  sort_order: number;
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

// mse_linkedin_leads (2026-08-14, supabase/migrations/20260814000023_linkedin_leads.sql)
// -- Apollo.io is suspended, LinkedIn manual outreach is the active
// first-customer channel. Separate from ApolloLead: no email at all,
// carries source (linkedin_manual | linkedin_engager) and, for engager
// leads, which post they interacted with.
export interface LinkedInLead {
  id: string;
  product_id: string | null;
  first_name: string | null;
  last_name: string | null;
  title: string | null;
  company: string | null;
  linkedin_url: string;
  location: string | null;
  source: "linkedin_manual" | "linkedin_engager";
  source_post_url: string | null;
  interaction_type: string | null;
  interaction_note: string | null;
  status: "pending_dm" | "contacted";
  contacted_at: string | null;
  created_at: string;
}

export interface DmSequence {
  id: string;
  lead_id: string | null;
  linkedin_lead_id: string | null;
  // "job_posting_signal" added 2026-09-16 for the infra-consulting ICP
  // (mse_products.slug='thdagentic-consulting') -- see
  // agents/marketing/mkt_o2_cold_dm_writer.py's _INFRA_CONSULTING_SYSTEM_PROMPT.
  lead_source: "apollo" | "linkedin_manual" | "linkedin_engager" | "job_posting_signal";
  product_id: string;
  campaign_build_id: string | null;
  touch_1: string;
  touch_2: string;
  // Only ever populated for lead_source="job_posting_signal" (3-touch
  // sequence) -- null/undefined for every other source's 2-touch rows.
  touch_3?: string | null;
  status: string;
  hitl_approved_by: string | null;
  hitl_approved_at: string | null;
  created_at: string;
  mse_apollo_leads: ApolloLead | null;
  mse_linkedin_leads: LinkedInLead | null;
}

// mse_research_reports.report_json shape — see
// agents/marketing/mkt_r1_research_core.py's module docstring for the
// authoritative schema. Only the fields the Marketing panel displays are
// typed here; the row itself may carry more.
export interface ResearchReport {
  id: string;
  product_id: string;
  cycle_date: string;
  report_json: {
    pain_language?: { phrase: string; context: string; source: string; frequency: number }[];
    content_angles?: { angle: string; supporting_data: string }[];
    icp_channels?: string[];
    willingness_to_pay_band?: string;
    suggested_price?: number;
  };
  created_at: string;
}

// campaign_builds — MKT-ORCH's fan-out record (agents/marketing/mkt_orch_campaign_orchestrator.py).
export interface CampaignBuild {
  id: string;
  product_id: string;
  research_opp_id: string;
  triggered_at: string;
  apollo_status: string;
  dm_sequence_status: string;
  email_sequence_status: string;
  seo_factory_status: string;
  social_status: string;
  lead_finder_status?: string;
}

export const MSE_VERTICALS = [
  "Healthcare / Medical Front Desk",
  "Legal / Professional Services",
  "E-commerce / Retail Ops",
  "Real Estate / Property Management",
  "HR / Ops / People Management",
  "Finance / Accounting / Bookkeeping",
  // Industry vertical agents (2026-08-22) -- must stay in sync with
  // agents/orchestrator/agent.py's VERTICAL_MODULE_MAP and
  // api/routers/research.py's VALID_VERTICALS.
  "Residential Trades / Service Contractors",
  "Care Services (Childcare/Elder/Pet)",
  "Personal Services (Salon/Spa/Fitness)",
  "Field/Repair Services (Auto/Equipment)",
] as const;

export const NAV_ITEMS = [
  { id: "overview",  label: "Overview",       path: "/dashboard" },
  { id: "swarm",     label: "Research Swarm", path: "/research" },
  { id: "pipeline",  label: "Opportunities",  path: "/pipeline" },
  { id: "outreach",  label: "Outreach",       path: "/outreach" },
  { id: "agents",    label: "Agents",         path: "/agents" },
  { id: "retention", label: "Retention",      path: "/retention" },
] as const;
