"use client";

import { useState, useEffect, useRef } from "react";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { MSE_VERTICALS, type SessionSummary } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type RunState = "idle" | "running" | "polling" | "complete" | "error";

export default function ResearchPage() {
  const [selected, setSelected] = useState<string[]>([]);
  const [state, setState] = useState<RunState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function toggleVertical(v: string) {
    setSelected((prev) => prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]);
  }

  function selectAll() { setSelected([...MSE_VERTICALS]); }
  function selectNone() { setSelected([]); }

  // Poll session when we have an ID
  useEffect(() => {
    if (!sessionId || state !== "polling") return;
    const poll = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/research/session/${sessionId}`, {
          headers: { Authorization: "Bearer internal" },
        });
        if (res.ok) {
          const data = await res.json();
          if (data.session_summary && Object.keys(data.session_summary).length > 0) {
            setSummary(data.session_summary as SessionSummary);
            setState("complete");
            clearInterval(poll);
            if (timerRef.current) clearInterval(timerRef.current);
          }
        }
      } catch { /* keep polling */ }
    }, 5000);
    return () => clearInterval(poll);
  }, [sessionId, state]);

  // Elapsed timer
  useEffect(() => {
    if (state === "running" || state === "polling") {
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
      if (state === "idle") setElapsed(0);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [state]);

  async function runSwarm() {
    setState("running");
    setError(null);
    setSummary(null);
    setElapsed(0);
    const verticals = selected.length > 0 ? selected : [...MSE_VERTICALS];

    try {
      const res = await fetch(`${API_BASE}/research/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer internal" },
        body: JSON.stringify({ verticals }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "Run failed");

      // /research/run returns immediately with session_id; poll for results
      if (data.session_id) {
        setSessionId(data.session_id);
        setState("polling");
      } else if (data.session_summary) {
        // If the endpoint returned a summary directly (synchronous mode)
        setSummary(data.session_summary as SessionSummary);
        setState("complete");
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
      setState("error");
    }
  }

  const isRunning = state === "running" || state === "polling";
  const activeVerticals = selected.length > 0 ? selected : [...MSE_VERTICALS];

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
                : `Run ${selected.length === 0 ? "Full Swarm" : `${activeVerticals.length} Vertical${activeVerticals.length > 1 ? "s" : ""}`}`}
            </button>
            {state === "complete" && <StatusBadge status="complete" />}
            {state === "error" && <StatusBadge status="error" />}
          </div>

          {/* Live status while running */}
          {isRunning && (
            <SectionCard title="Swarm in Progress">
              <div className="space-y-3">
                <p className="text-[12px]" style={{ color: "#aab4bd" }}>
                  Running {activeVerticals.length} verticals in parallel via Sonnet. Pipeline results appear when complete.
                </p>
                {activeVerticals.map((v) => (
                  <div key={v} className="flex items-center gap-3 min-w-0">
                    <span className="text-[12px] truncate-text flex-1 min-w-0" style={{ color: "#8b96a3" }}>{v}</span>
                    <StatusBadge status="running" />
                  </div>
                ))}
                <div className="pt-2">
                  <ProgressBar value={(elapsed / 180) * 100} accent="#6fce8f" height={4} />
                  <p className="text-[10px] font-mono mt-1" style={{ color: "#5b6673" }}>
                    Est. ~3 min · {elapsed}s elapsed
                  </p>
                </div>
              </div>
            </SectionCard>
          )}

          {/* Error state */}
          {state === "error" && error && (
            <SectionCard title="Error">
              <p className="text-[12px] font-mono" style={{ color: "#e05d5d" }}>{error}</p>
              <p className="text-[11px] font-mono mt-2" style={{ color: "#5b6673" }}>
                Make sure the MSE API is running on {API_BASE} and you&apos;re authenticated.
              </p>
            </SectionCard>
          )}

          {/* Results */}
          {state === "complete" && summary && (
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
