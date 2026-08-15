"use client";

import Link from "next/link";
import { useRuns, useElapsedSeconds, type ResearchRun } from "@/lib/runs/RunsContext";

function totalOpportunities(run: ResearchRun): number {
  const s = run.summary;
  if (!s) return 0;
  return s.ready_to_build + s.validated_pending_review + s.watch_list + s.rejected;
}

function RunPill({ run }: { run: ResearchRun }) {
  const { dismissRun } = useRuns();
  const active = run.status === "queued" || run.status === "running";
  const elapsed = useElapsedSeconds(run.startedAt, active);

  if (active) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-[8px]" style={{ backgroundColor: "#5a96ff1a", border: "1px solid #5a96ff44" }}>
        <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ backgroundColor: "#7ea6f5" }} />
        <span className="text-[11px] font-mono" style={{ color: "#7ea6f5" }}>
          {run.label} running — {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
        </span>
      </div>
    );
  }

  if (run.status === "complete") {
    const found = totalOpportunities(run);
    const color = found > 0 ? "#6fce8f" : "#8b96a3";
    return (
      <div className="flex items-center gap-3 px-3 py-1.5 rounded-[8px]" style={{ backgroundColor: found > 0 ? "#6fce8f1a" : "#1c222b", border: `1px solid ${found > 0 ? "#6fce8f44" : "#3a4250"}` }}>
        <span className="text-[11px] font-mono" style={{ color }}>
          {run.label} complete — {found > 0 ? `${found} opportunit${found === 1 ? "y" : "ies"} written` : "no opportunities cleared the threshold"}
        </span>
        <Link href="/pipeline" className="text-[11px] font-mono font-semibold underline" style={{ color: "#5eead4" }}>
          Check Opportunities →
        </Link>
        <button onClick={() => dismissRun(run.sessionId)} className="text-[13px] leading-none" style={{ color: "#5b6673" }} aria-label="Dismiss">
          ×
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 px-3 py-1.5 rounded-[8px]" style={{ backgroundColor: "#e05d5d1a", border: "1px solid #e05d5d44" }}>
      <span className="text-[11px] font-mono" style={{ color: "#e05d5d" }}>{run.label} failed — {run.error}</span>
      <button onClick={() => dismissRun(run.sessionId)} className="text-[13px] leading-none" style={{ color: "#5b6673" }} aria-label="Dismiss">
        ×
      </button>
    </div>
  );
}

/**
 * Persistent, dashboard-wide status bar for in-flight and just-finished
 * research runs. Lives inside DashboardShell (rendered on every page), but
 * its actual state comes from RunsContext (mounted once at the root
 * layout) — so a run started on /agents keeps showing here even after
 * navigating to /pipeline, /dashboard, wherever, until it finishes and the
 * user dismisses the completion flag.
 */
export function RunStatusBar() {
  const { runs } = useRuns();
  if (runs.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-6 py-2 shrink-0" style={{ borderBottom: "1px solid #1c222b", backgroundColor: "#0e1218" }}>
      {runs.map((r) => (
        <RunPill key={r.sessionId} run={r} />
      ))}
    </div>
  );
}
