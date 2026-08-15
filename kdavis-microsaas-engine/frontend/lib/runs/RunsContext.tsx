"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import type { SessionSummary } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const POLL_INTERVAL_MS = 5000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000; // a full swarm run can genuinely take several minutes
const STORAGE_KEY = "mse_active_research_runs";

export type RunStatus = "queued" | "running" | "complete" | "error";

export interface ResearchRun {
  sessionId: string;
  label: string; // e.g. "Ledger", "Dispatch (Orchestrator)", "Research Swarm" -- whatever fired it
  verticals: string[];
  status: RunStatus;
  startedAt: number;
  summary?: SessionSummary; // populated once status === "complete"
  error?: string;
}

interface StoredRun {
  sessionId: string;
  label: string;
  verticals: string[];
  startedAt: number;
}

interface RunsContextValue {
  runs: ResearchRun[];
  startRun: (label: string, verticals: string[]) => Promise<ResearchRun>;
  dismissRun: (sessionId: string) => void;
  getRunByLabel: (label: string) => ResearchRun | undefined;
}

const RunsContext = createContext<RunsContextValue | null>(null);

export function useRuns(): RunsContextValue {
  const ctx = useContext(RunsContext);
  if (!ctx) throw new Error("useRuns must be used within RunsProvider");
  return ctx;
}

// Ticks a re-render every second while `active` -- Date.now() - startedAt
// alone doesn't trigger React to re-render, this is what makes an
// "Xm elapsed" label actually count up live.
export function useElapsedSeconds(startedAt: number, active: boolean): number {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  return Math.floor((Date.now() - startedAt) / 1000);
}

function loadStoredRuns(): StoredRun[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredRun[]) : [];
  } catch {
    return [];
  }
}

function saveStoredRuns(runs: StoredRun[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(runs));
}

/**
 * Tracks in-flight and just-completed research runs (Dispatch/Verdict via
 * POST /research/run) across client-side navigation between dashboard
 * pages. Mounted once in app/layout.tsx, above every page — unlike a
 * page's own local useState/useEffect (the previous approach on both
 * /research and /agents), this survives navigating away and back, since
 * Next.js App Router unmounts individual page components on route change
 * but never unmounts the root layout. In-flight runs are also mirrored
 * to localStorage so a hard refresh resumes polling instead of losing
 * track of a run that's still going for real on the backend.
 */
export function RunsProvider({ children }: { children: React.ReactNode }) {
  const supabase = createClient();
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const hydrated = useRef(false);

  const persistInFlight = useCallback((current: ResearchRun[]) => {
    const inFlight: StoredRun[] = current
      .filter((r) => r.status === "queued" || r.status === "running")
      .map((r) => ({ sessionId: r.sessionId, label: r.label, verticals: r.verticals, startedAt: r.startedAt }));
    saveStoredRuns(inFlight);
  }, []);

  const pollSession = useCallback((sessionId: string, startedAt: number) => {
    if (pollTimers.current[sessionId]) return; // already polling this one

    const finish = (patch: Partial<ResearchRun>) => {
      clearInterval(pollTimers.current[sessionId]);
      delete pollTimers.current[sessionId];
      setRuns((prev) => {
        const next = prev.map((r) => (r.sessionId === sessionId ? { ...r, ...patch } : r));
        persistInFlight(next);
        return next;
      });
    };

    const tick = async () => {
      // startedAt is captured directly (not read back from `runs` state)
      // so this never depends on a possibly-stale closure over state --
      // pollSession itself has no `runs` dependency at all.
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        finish({ status: "error", error: "Timed out waiting for the run to finish — check the CEO dashboard event feed" });
        return;
      }
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) return; // not signed in yet (e.g. still on /login) -- try again next tick
        const res = await fetch(`${API_BASE}/research/session/${sessionId}`, {
          headers: { Authorization: `Bearer ${session.access_token}` },
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.detail ?? `API error ${res.status}`);
        if (data.session_summary) {
          finish({ status: "complete", summary: data.session_summary as SessionSummary });
        }
      } catch (e) {
        finish({ status: "error", error: e instanceof Error ? e.message : "Unknown error" });
      }
    };

    pollTimers.current[sessionId] = setInterval(tick, POLL_INTERVAL_MS);
    tick(); // check immediately -- important for a run resumed from localStorage that may have already finished while unwatched
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supabase, persistInFlight]);

  // Resume any runs that were still in flight the last time this app was
  // loaded (survives a hard refresh, not just SPA navigation). Runs once.
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const stored = loadStoredRuns();
    if (stored.length === 0) return;
    setRuns(stored.map((s) => ({ ...s, status: "running" as RunStatus })));
    stored.forEach((s) => pollSession(s.sessionId, s.startedAt));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    return () => { Object.values(pollTimers.current).forEach(clearInterval); };
  }, []);

  const startRun = useCallback(async (label: string, verticals: string[]): Promise<ResearchRun> => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) throw new Error("Not signed in");

    const res = await fetch(`${API_BASE}/research/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
      body: JSON.stringify({ verticals }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data?.detail?.message ?? data?.detail ?? `API error ${res.status}`);

    const run: ResearchRun = { sessionId: data.session_id, label, verticals, status: "running", startedAt: Date.now() };
    setRuns((prev) => {
      const next = [...prev.filter((r) => r.sessionId !== run.sessionId), run];
      persistInFlight(next);
      return next;
    });
    pollSession(run.sessionId, run.startedAt);
    return run;
  }, [supabase, pollSession, persistInFlight]);

  const dismissRun = useCallback((sessionId: string) => {
    setRuns((prev) => prev.filter((r) => r.sessionId !== sessionId));
  }, []);

  const getRunByLabel = useCallback(
    (label: string) => [...runs].reverse().find((r) => r.label === label),
    [runs],
  );

  return (
    <RunsContext.Provider value={{ runs, startRun, dismissRun, getRunByLabel }}>
      {children}
    </RunsContext.Provider>
  );
}
