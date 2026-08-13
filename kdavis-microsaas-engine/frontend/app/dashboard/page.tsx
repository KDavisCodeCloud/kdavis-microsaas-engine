import { createClient } from "@/lib/supabase/server";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { MetricCard } from "@/components/ui/MetricCard";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { AgentRosterCard } from "@/components/ui/AgentRosterCard";
import { ResearchTrigger } from "@/components/ui/ResearchTrigger";

// Real marketing factory agent build status — replaces a stale, never-built
// "Week 1-7 research swarm" placeholder (Market Sizing/Competitor Depth/ICP
// Validation/Retention Hook/MRR Math agents never existed as real files;
// this reflects what's actually in agents/marketing/ and agents/orchestrator/
// as of 2026-08-12, verified against the live codebase, not a roadmap guess.
const AGENT_CADENCE = [
  { week: 1, name: "Dispatch + Verdict v5.0 (orchestrator/aggregator)", status: "complete", date: "2026-07-19" },
  { week: 2, name: "MKT-ORCH Campaign Orchestrator",       status: "complete", date: "2026-07-23" },
  { week: 3, name: "MKT-R1 Research Core",                 status: "complete", date: "2026-07-23" },
  { week: 4, name: "MKT-O1 Apollo List Builder",           status: "flagged",  date: "blocked: Apollo key on Free plan, no API access" },
  { week: 5, name: "MKT-O2 Cold DM Sequence Writer",       status: "complete", date: "2026-07-23" },
  { week: 6, name: "MKT-O3 Email Sequence Loader",         status: "flagged",  date: "blocked: Systeme.io has no campaigns API yet" },
  { week: 7, name: "MKT-O4 Outreach Monitor",              status: "complete", date: "2026-07-23" },
  { week: 8, name: "MKT-O5 Sequence Sender + CAN-SPAM guard", status: "complete", date: "2026-08-12" },
  { week: 9, name: "MKT-S1 SEO Content Factory",           status: "complete", date: "2026-07-23" },
  { week: 10, name: "MKT-V1 Content Multiplier",           status: "complete", date: "2026-08-12" },
];

export default async function DashboardPage() {
  const supabase = await createClient();

  const [{ data: pipeline }, { data: events }] = await Promise.all([
    supabase.from("opportunity_pipeline").select("id, status").neq("status", "rejected"),
    supabase.from("agent_events").select("*").order("created_at", { ascending: false }).limit(8),
  ]);

  const opportunities = pipeline ?? [];
  const ready = opportunities.filter((o) => o.status === "READY_TO_BUILD").length;
  const validated = opportunities.filter((o) => o.status === "validated").length;
  const agentEvents = (events ?? []) as { id: string; agent_name: string; department: string; action: string; verdict: string; created_at: string }[];

  return (
    <DashboardShell>
      <TopBar title="Overview" />
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {/* Metric cards */}
          <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
            <MetricCard label="Pipeline Opportunities" value={String(opportunities.length)} subtext="non-rejected" accent="#5eead4" />
            <MetricCard label="Ready to Build"         value={String(ready)}                subtext="READY_TO_BUILD status" accent="#6fce8f" />
            <MetricCard label="Validated"              value={String(validated)}            subtext="pending operator review" accent="#7ea6f5" />
            <MetricCard label="MRR Floor"              value="$4,000"                       subtext="DB-enforced minimum" accent="#e8963f" />
          </div>

          {/* Quick actions */}
          <SectionCard title="Research Swarm">
            <ResearchTrigger />
          </SectionCard>

          {/* Agent cadence */}
          <SectionCard title="Agent Build Cadence">
            <div className="space-y-0">
              {AGENT_CADENCE.map((w, i) => (
                <div
                  key={w.week}
                  className="flex items-center gap-3 py-2.5 min-w-0"
                  style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                >
                  <span
                    className="shrink-0 w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold"
                    style={{
                      border: `1.5px solid ${w.status === "complete" ? "#6fce8f" : "#3a4250"}`,
                      color: w.status === "complete" ? "#6fce8f" : "#5b6673",
                    }}
                  >
                    {w.week}
                  </span>
                  <span
                    className="flex-1 text-[12.5px]"
                    style={{ color: w.status === "complete" ? "#5b6673" : "#eef2f5", textDecoration: w.status === "complete" ? "line-through" : "none" }}
                  >
                    {w.name}
                  </span>
                  <span className="text-[11px] font-mono shrink-0" style={{ color: "#5b6673" }}>{w.date}</span>
                  <StatusBadge status={w.status} />
                </div>
              ))}
            </div>
          </SectionCard>

          {/* Recent agent events */}
          <SectionCard title="Recent Agent Activity">
            {agentEvents.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                No events yet. Run the research swarm to populate this feed.
              </p>
            ) : (
              agentEvents.map((e, i) => (
                <div
                  key={e.id}
                  className="flex items-center gap-2 py-2 min-w-0"
                  style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                >
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: e.verdict === "pass" ? "#6fce8f" : e.verdict === "flagged" ? "#e05d5d" : "#e8963f" }} />
                  <span className="text-[12px] font-semibold shrink-0 truncate-text" style={{ maxWidth: "120px", color: "#eef2f5" }}>{e.agent_name}</span>
                  <span className="text-[12px] flex-1 truncate-text min-w-0" style={{ color: "#aab4bd" }}>{e.action}</span>
                  <span className="text-[11px] font-mono shrink-0" style={{ color: "#5b6673" }}>
                    {new Date(e.created_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/Phoenix" })}
                  </span>
                </div>
              ))
            )}
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
