import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { AgentRosterCard } from "@/components/ui/AgentRosterCard";
import { StatusBadge } from "@/components/ui/StatusBadge";

const AGENT_ROSTER = [
  { name: "Dispatch (Orchestrator)", status: "active",  focus: "Fans out to all 6 verticals via asyncio.gather",  lastRun: "last session",  output: "LangGraph StateGraph, 5 nodes" },
  { name: "Verdict (Aggregator)",    status: "active",  focus: "7-gate quality filter, READY_TO_BUILD stamp",     lastRun: "last session",  output: "Gate 1: MRR ≥$4K · Gate 7: score threshold" },
  { name: "Ledger",                  status: "pending", focus: "Finance / Accounting vertical intel",              lastRun: null,            output: "Week 6 — builds 2026-08-07" },
  { name: "Anchor",                  status: "pending", focus: "Real Estate / Property Mgmt vertical intel",       lastRun: null,            output: "Week 4 — builds 2026-07-24" },
  { name: "Comply",                  status: "pending", focus: "Legal / Professional Services vertical intel",     lastRun: null,            output: "Week 3 — builds 2026-07-17" },
  { name: "Runway",                  status: "pending", focus: "HR / Ops / People Mgmt vertical intel",           lastRun: null,            output: "Week 5 — builds 2026-07-31" },
  { name: "Pulse",                   status: "pending", focus: "Healthcare / Medical Front Desk vertical intel",   lastRun: null,            output: "Week 2 — builds 2026-07-10" },
  { name: "Scout",                   status: "pending", focus: "E-commerce / Retail Ops vertical intel",          lastRun: null,            output: "Week 7 — builds 2026-08-14" },
];

const CADENCE = [
  { week: 1,  agent: "Dispatch + Verdict",  date: "2026-07-04", status: "complete", notes: "Orchestrator + aggregator wired to /research/run" },
  { week: 2,  agent: "Pulse",               date: "2026-07-10", status: "pending",  notes: "Healthcare vertical market sizing" },
  { week: 3,  agent: "Comply",              date: "2026-07-17", status: "pending",  notes: "Legal vertical competitor depth" },
  { week: 4,  agent: "Anchor",              date: "2026-07-24", status: "pending",  notes: "Real estate ICP validation" },
  { week: 5,  agent: "Runway",              date: "2026-07-31", status: "pending",  notes: "HR/ops retention hook analysis" },
  { week: 6,  agent: "Ledger",              date: "2026-08-07", status: "pending",  notes: "Finance vertical MRR math" },
  { week: 7,  agent: "Scout + Integration", date: "2026-08-14", status: "pending",  notes: "E-commerce + full swarm integration test" },
];

export default function AgentsPage() {
  return (
    <DashboardShell>
      <TopBar title="Agents" />
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {/* Agent Roster */}
          <SectionCard title="Agent Roster">
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
              {AGENT_ROSTER.map((a) => (
                <AgentRosterCard key={a.name} {...a} />
              ))}
            </div>
          </SectionCard>

          {/* Build Cadence */}
          <SectionCard title="Build Cadence — Thursdays">
            {CADENCE.map((w, i) => (
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
                <div className="flex-1 min-w-0">
                  <p className="text-[12.5px] font-semibold" style={{ color: w.status === "complete" ? "#5b6673" : "#eef2f5" }}>
                    {w.agent}
                  </p>
                  <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>{w.notes}</p>
                </div>
                <span className="text-[11px] font-mono shrink-0" style={{ color: "#5b6673" }}>{w.date}</span>
                <StatusBadge status={w.status} />
              </div>
            ))}
          </SectionCard>

          {/* Architecture notes */}
          <SectionCard title="Architecture">
            {[
              ["Framework",       "LangGraph StateGraph (5 nodes: initialize → dispatch → aggregate → write → summarize)"],
              ["Fallback",        "Sonnet stub runs when vertical agent module not yet built — plug in via VERTICAL_MODULE_MAP"],
              ["Parallelism",     "asyncio.gather() fans out all 6 verticals simultaneously"],
              ["Event emission",  "Each node calls POST /events on state change (CEO dashboard feed)"],
              ["DB write",        "node_write_pipeline maps all NOT NULL columns — $4K MRR floor enforced"],
              ["Sanitization",    "DataSanitizationShield runs before every LLM call"],
              ["Model routing",   "Haiku for high-volume scraping · Sonnet for analysis"],
            ].map(([label, value], i) => (
              <div
                key={label}
                className="flex gap-3 py-2.5 min-w-0"
                style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
              >
                <span className="shrink-0 text-[11px] font-mono" style={{ color: "#5b6673", width: "110px" }}>{label}</span>
                <span className="text-[12px] flex-1 min-w-0" style={{ color: "#aab4bd" }}>{value}</span>
              </div>
            ))}
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
