"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { AgentRosterCard } from "@/components/ui/AgentRosterCard";
import { StatusBadge } from "@/components/ui/StatusBadge";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

type RunStatus = "queued" | "running" | "complete" | "error";
type RunState = { status: RunStatus; sessionId?: string; opportunityCount?: number; error?: string };
type LastRun = { at: string; count: number };

const POLL_INTERVAL_MS = 5000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000; // a full swarm run can genuinely take several minutes

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
  const [runs, setRuns] = useState<Record<string, RunState>>({});
  const [lastRuns, setLastRuns] = useState<Record<string, LastRun>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

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

  // Stop every in-flight poll on unmount -- otherwise a fetch fires
  // against an unmounted component after navigating away mid-run.
  useEffect(() => {
    return () => { Object.values(pollTimers.current).forEach(clearInterval); };
  }, []);

  function startPolling(key: string, sessionId: string) {
    const startedAt = Date.now();
    if (pollTimers.current[key]) clearInterval(pollTimers.current[key]);

    pollTimers.current[key] = setInterval(async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        clearInterval(pollTimers.current[key]);
        setRuns((r) => ({ ...r, [key]: { status: "error", sessionId, error: "Timed out waiting for the run to finish — check the CEO dashboard event feed" } }));
        return;
      }
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) return;
        const res = await fetch(`${API_BASE}/research/session/${sessionId}`, {
          headers: { Authorization: `Bearer ${session.access_token}` },
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail ?? `API error ${res.status}`);

        if (data.session_summary) {
          clearInterval(pollTimers.current[key]);
          setRuns((r) => ({ ...r, [key]: { status: "complete", sessionId, opportunityCount: (data.opportunities ?? []).length } }));
          fetchLastRuns();
        }
      } catch (e) {
        clearInterval(pollTimers.current[key]);
        setRuns((r) => ({ ...r, [key]: { status: "error", sessionId, error: e instanceof Error ? e.message : "Unknown error" } }));
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleRun(agent: AgentDef) {
    const key = agent.name;
    setRuns((r) => ({ ...r, [key]: { status: "queued" } }));

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Not signed in");

      const res = await fetch(`${API_BASE}/research/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({ verticals: agent.vertical ? [agent.vertical] : [] }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail?.message ?? data?.detail ?? `API error ${res.status}`);

      const sessionId = data.session_id as string;
      setRuns((r) => ({ ...r, [key]: { status: "running", sessionId } }));
      startPolling(key, sessionId);
    } catch (e) {
      setRuns((r) => ({ ...r, [key]: { status: "error", error: e instanceof Error ? e.message : "Unknown error" } }));
    }
  }

  function cardProps(agent: AgentDef) {
    const run = runs[agent.name];
    const last = lastRuns[agent.vertical ?? "__all__"];

    const status: string = run?.status ?? (last ? "complete" : "pending");
    const lastRunText = run?.sessionId
      ? undefined // the live run/output line below covers this instead
      : last
        ? `${formatLastRun(last.at)} · ${last.count} opportunit${last.count === 1 ? "y" : "ies"} total`
        : "never run";

    let output: string | undefined;
    if (run?.status === "queued") output = "Queuing session…";
    else if (run?.status === "running") output = "Dispatch running — Verdict evaluates each finding as it lands";
    else if (run?.status === "complete") output = `This run: ${run.opportunityCount ?? 0} opportunit${run.opportunityCount === 1 ? "y" : "ies"} written to the pipeline`;

    const busy = run?.status === "queued" || run?.status === "running";

    return {
      status,
      lastRun: lastRunText,
      output: output ?? undefined,
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
