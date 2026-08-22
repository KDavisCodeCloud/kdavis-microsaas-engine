"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { AgentRosterCard } from "@/components/ui/AgentRosterCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useRuns } from "@/lib/runs/RunsContext";

// Must match api/routers/research.py's VALID_VERTICALS exactly -- these
// strings are what actually gets POSTed to /research/run.
type AgentDef = {
  name: string;
  vertical: string | null; // null = Dispatch (full swarm) / Verdict (no button, runs inline)
  focus: string;
  runnable: boolean;
};

const AGENT_DEFS: AgentDef[] = [
  { name: "Dispatch (Orchestrator)", vertical: null, focus: "Fans out to all 6 verticals via asyncio.gather", runnable: true },
  { name: "Verdict (Aggregator)", vertical: null, focus: "7-gate quality filter, READY_TO_BUILD stamp — runs automatically inside Dispatch, not independently triggerable", runnable: false },
  { name: "Ledger", vertical: "Finance / Accounting / Bookkeeping", focus: "Finance / Accounting vertical intel", runnable: true },
  { name: "Anchor", vertical: "Real Estate / Property Management", focus: "Real Estate / Property Mgmt vertical intel", runnable: true },
  { name: "Comply", vertical: "Legal / Professional Services", focus: "Legal / Professional Services vertical intel", runnable: true },
  { name: "Runway", vertical: "HR / Ops / People Management", focus: "HR / Ops / People Mgmt vertical intel", runnable: true },
  { name: "Pulse", vertical: "Healthcare / Medical Front Desk", focus: "Healthcare / Medical Front Desk vertical intel", runnable: true },
  { name: "Scout", vertical: "E-commerce / Retail Ops", focus: "E-commerce / Retail Ops vertical intel", runnable: true },
  // Industry vertical agents (2026-08-22) -- pointed at specific business
  // segments rather than problem categories, per agents/{trades,care,
  // service,field}_intel/agent.py. Named plainly (not codenamed like the
  // six above) since their own system prompts already self-identify this
  // way ("You are Trades...", "You are Care...", etc.) -- these are the
  // first vertical modules with a real agent.py at all, unlike the six
  // above which still fall through to Dispatch's generic fallback prompt.
  { name: "Trades", vertical: "Residential Trades / Service Contractors", focus: "HVAC/plumbing/electrical/roofing/landscaping/pool/pest control vertical intel", runnable: true },
  { name: "Care", vertical: "Care Services (Childcare/Elder/Pet)", focus: "Childcare/elder care/pet care vertical intel", runnable: true },
  { name: "Service", vertical: "Personal Services (Salon/Spa/Fitness)", focus: "Salon/spa/fitness/barbershop vertical intel", runnable: true },
  { name: "Field", vertical: "Field/Repair Services (Auto/Equipment)", focus: "Auto/equipment/appliance repair vertical intel", runnable: true },
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

type LastRun = { at: string; count: number };

function formatLastRun(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

export default function AgentsPage() {
  const supabase = createClient();
  const { runs, startRun, getRunByLabel } = useRuns();
  const [lastRuns, setLastRuns] = useState<Record<string, LastRun>>({});

  const fetchLastRuns = useCallback(async () => {
    const { data } = await supabase
      .from("opportunity_pipeline")
      .select("vertical, created_at")
      .order("created_at", { ascending: false })
      .limit(500);
    if (!data) return;

    const byVertical: Record<string, LastRun> = {};
    let globalLatest: string | null = null;
    let globalCount = 0;
    for (const row of data as { vertical: string; created_at: string }[]) {
      globalCount += 1;
      if (!globalLatest) globalLatest = row.created_at;
      if (!byVertical[row.vertical]) {
        byVertical[row.vertical] = { at: row.created_at, count: 1 };
      } else {
        byVertical[row.vertical].count += 1;
      }
    }
    if (globalLatest) byVertical.__all__ = { at: globalLatest, count: globalCount };
    setLastRuns(byVertical);
  }, [supabase]);

  useEffect(() => { fetchLastRuns(); }, [fetchLastRuns]);

  // Whenever any tracked run flips to complete, refresh "last run" data --
  // covers a run that finished while the user was on a different tab.
  useEffect(() => {
    if (runs.some((r) => r.status === "complete")) fetchLastRuns();
  }, [runs, fetchLastRuns]);

  async function handleRun(agent: AgentDef) {
    try {
      await startRun(agent.name, agent.vertical ? [agent.vertical] : []);
    } catch {
      // startRun's failure is surfaced via the run's own "error" status,
      // read back through getRunByLabel below -- no local error state needed here.
    }
  }

  function cardProps(agent: AgentDef) {
    const run = getRunByLabel(agent.name);
    const last = lastRuns[agent.vertical ?? "__all__"];

    const status: string = run?.status ?? (last ? "complete" : "pending");
    const lastRunText = run
      ? undefined // the live output line below covers this instead
      : last
        ? `${formatLastRun(last.at)} · ${last.count} opportunit${last.count === 1 ? "y" : "ies"} total`
        : "never run";

    let output: string | undefined;
    if (run?.status === "queued") output = "Queuing session…";
    else if (run?.status === "running") output = "Dispatch running — Verdict evaluates each finding as it lands. Safe to leave this tab.";
    else if (run?.status === "complete" && run.summary) {
      const found = run.summary.ready_to_build + run.summary.validated_pending_review + run.summary.watch_list + run.summary.rejected;
      output = found > 0 ? `This run: ${found} opportunit${found === 1 ? "y" : "ies"} written — check Opportunities` : "This run: no opportunities cleared the threshold";
    }

    const busy = run?.status === "queued" || run?.status === "running";

    return {
      status,
      lastRun: lastRunText,
      output,
      error: run?.status === "error" ? run.error : null,
      action: agent.runnable
        ? {
            label: busy ? "Running…" : agent.vertical ? "Run Now" : "Run Full Swarm",
            onClick: () => handleRun(agent),
            disabled: busy,
          }
        : undefined,
    };
  }

  return (
    <DashboardShell>
      <TopBar title="Agents" />
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {/* Agent Roster */}
          <SectionCard title="Agent Roster">
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
              {AGENT_DEFS.map((a) => (
                <AgentRosterCard key={a.name} name={a.name} focus={a.focus} {...cardProps(a)} />
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
