"use client";

import { useState } from "react";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { MSE_VERTICALS } from "@/lib/types";
import { useRuns, useElapsedSeconds } from "@/lib/runs/RunsContext";

const RUN_LABEL = "Research Swarm";
const EST_SECONDS = 180;

export default function ResearchPage() {
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { getRunByLabel, startRun } = useRuns();

  // Sourced from RunsContext (mounted once at the root layout), not local
  // state -- this is what makes the run survive navigating to another tab
  // and back. See frontend/lib/runs/RunsContext.tsx's module docstring.
  const run = getRunByLabel(RUN_LABEL);
  const isRunning = run?.status === "queued" || run?.status === "running";
  const elapsed = useElapsedSeconds(run?.startedAt ?? Date.now(), isRunning);

  function toggleVertical(v: string) {
    setSelected((prev) => (prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]));
  }
  function selectAll() { setSelected([...MSE_VERTICALS]); }
  function selectNone() { setSelected([]); }

  async function runSwarm() {
    setError(null);
    const verticals = selected.length > 0 ? selected : [...MSE_VERTICALS];
    try {
      await startRun(RUN_LABEL, verticals);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
  }

  const activeVerticals = run?.verticals ?? (selected.length > 0 ? selected : [...MSE_VERTICALS]);
  const summary = run?.summary;

  return (
    <DashboardShell>
      <TopBar title="Research Swarm">
        {isRunning && (
          <span className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
            {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} elapsed
          </span>
        )}
      </TopBar>

      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {/* Vertical selector */}
          <SectionCard title="Select Verticals">
            <div className="flex gap-2 mb-4">
              <button
                onClick={selectAll}
                className="text-[11px] font-mono px-3 py-1 rounded-[6px]"
                style={{ border: "1px solid #1c222b", color: "#8b96a3", backgroundColor: "transparent" }}
              >
                All
              </button>
              <button
                onClick={selectNone}
                className="text-[11px] font-mono px-3 py-1 rounded-[6px]"
                style={{ border: "1px solid #1c222b", color: "#8b96a3", backgroundColor: "transparent" }}
              >
                None
              </button>
            </div>
            <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
              {MSE_VERTICALS.map((v) => {
                const active = selected.includes(v) || selected.length === 0;
                return (
                  <label
                    key={v}
                    className="flex items-center gap-3 cursor-pointer rounded-[8px] px-3 py-2.5 transition-colors"
                    style={{
                      backgroundColor: selected.includes(v) ? "#5eead41a" : "#10151b",
                      border: `1px solid ${selected.includes(v) ? "#5eead4" : "#1c222b"}`,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(v)}
                      onChange={() => toggleVertical(v)}
                      className="shrink-0"
                      style={{ accentColor: "#5eead4" }}
                    />
                    <span className="text-[12.5px]" style={{ color: active ? "#eef2f5" : "#5b6673" }}>{v}</span>
                  </label>
                );
              })}
            </div>
          </SectionCard>

          {/* Fire button */}
          <div className="flex items-center gap-4">
            <button
              onClick={runSwarm}
              disabled={isRunning}
              className="px-6 py-3 rounded-[10px] text-[13px] font-bold transition-colors"
              style={{
                backgroundColor: isRunning ? "#2a3340" : "#6fce8f",
                color: isRunning ? "#5b6673" : "#0b0e13",
                cursor: isRunning ? "not-allowed" : "pointer",
              }}
            >
              {isRunning
                ? "Swarm Running…"
                : `Run ${selected.length === 0 ? "Full Swarm" : `${selected.length} Vertical${selected.length > 1 ? "s" : ""}`}`}
            </button>
            {run?.status === "complete" && <StatusBadge status="complete" />}
            {run?.status === "error" && <StatusBadge status="error" />}
          </div>

          {/* Live status while running */}
          {isRunning && (
            <SectionCard title="Swarm in Progress">
              <div className="space-y-3">
                <p className="text-[12px]" style={{ color: "#aab4bd" }}>
                  Running {activeVerticals.length} vertical{activeVerticals.length > 1 ? "s" : ""} in parallel via Sonnet.
                  Navigate away if you like — this keeps running and the status bar stays visible until it&apos;s done.
                </p>
                {activeVerticals.map((v) => (
                  <div key={v} className="flex items-center gap-3 min-w-0">
                    <span className="text-[12px] truncate-text flex-1 min-w-0" style={{ color: "#8b96a3" }}>{v}</span>
                    <StatusBadge status="running" />
                  </div>
                ))}
                <div className="pt-2">
                  <ProgressBar value={(elapsed / EST_SECONDS) * 100} accent="#6fce8f" height={4} />
                  <p className="text-[10px] font-mono mt-1" style={{ color: "#5b6673" }}>
                    Est. ~3 min · {elapsed}s elapsed
                  </p>
                </div>
              </div>
            </SectionCard>
          )}

          {/* Error state */}
          {run?.status === "error" && (
            <SectionCard title="Error">
              <p className="text-[12px] font-mono" style={{ color: "#e05d5d" }}>{run.error}</p>
              <p className="text-[11px] font-mono mt-2" style={{ color: "#5b6673" }}>
                Make sure the MSE API is running and you&apos;re authenticated.
              </p>
            </SectionCard>
          )}
          {error && (
            <SectionCard title="Error">
              <p className="text-[12px] font-mono" style={{ color: "#e05d5d" }}>{error}</p>
            </SectionCard>
          )}

          {/* Results */}
          {run?.status === "complete" && summary && (
            <SectionCard title="Session Results">
              <div className="grid gap-4 mb-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))" }}>
                {[
                  { label: "Verticals Scanned", value: summary.verticals_scanned, accent: "#5eead4" },
                  { label: "Ready to Build",    value: summary.ready_to_build,    accent: "#6fce8f" },
                  { label: "Validated",         value: summary.validated_pending_review, accent: "#7ea6f5" },
                  { label: "Watch List",        value: summary.watch_list,        accent: "#e8963f" },
                  { label: "Rejected",          value: summary.rejected,          accent: "#e05d5d" },
                ].map((m) => (
                  <div key={m.label} className="rounded-[10px] p-3" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                    <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>{m.label}</p>
                    <p className="text-[22px] font-extrabold" style={{ color: m.accent }}>{m.value}</p>
                  </div>
                ))}
              </div>
              {summary.ready_to_build + summary.validated_pending_review + summary.watch_list + summary.rejected === 0 && (
                <p className="text-[12px] font-mono mb-3" style={{ color: "#8b96a3" }}>
                  No opportunities cleared the $3,500/mo MRR floor this run — nothing worth reviewing was found, which is normal (historical hit rate is roughly 1 in 15).
                </p>
              )}
              {summary.recommended_first_build && (
                <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #6fce8f44" }}>
                  <p className="text-[11px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Recommended First Build</p>
                  <p className="text-[13px] font-bold" style={{ color: "#6fce8f" }}>{summary.recommended_first_build}</p>
                </div>
              )}
              <p className="text-[11px] font-mono mt-4" style={{ color: "#5b6673" }}>
                Results saved to pipeline. View in{" "}
                <a href="/pipeline" style={{ color: "#5eead4" }}>Pipeline →</a>
              </p>
            </SectionCard>
          )}
        </div>
      </div>
    </DashboardShell>
  );
}
