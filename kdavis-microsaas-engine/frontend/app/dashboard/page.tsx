import { createClient } from "@/lib/supabase/server";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { MetricCard } from "@/components/ui/MetricCard";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { AgentRosterCard } from "@/components/ui/AgentRosterCard";
import { ResearchTrigger } from "@/components/ui/ResearchTrigger";

const AGENT_CADENCE = [
  { week: 1, name: "Orchestrator + Aggregator", status: "complete", date: "2026-07-04" },
  { week: 2, name: "Market Sizing Agent",        status: "pending",  date: "2026-07-10" },
  { week: 3, name: "Competitor Depth Agent",     status: "pending",  date: "2026-07-17" },
  { week: 4, name: "ICP Validation Agent",       status: "pending",  date: "2026-07-24" },
  { week: 5, name: "Retention Hook Agent",       status: "pending",  date: "2026-07-31" },
  { week: 6, name: "MRR Math Agent",             status: "pending",  date: "2026-08-07" },
  { week: 7, name: "Full Swarm Integration",     status: "pending",  date: "2026-08-14" },
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
                    {new Date(e.created_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
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
