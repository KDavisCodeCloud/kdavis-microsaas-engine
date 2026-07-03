"use client";

import { useState } from "react";
import { UsageTracker } from "@/components/UsageTracker";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

const VERTICALS = [
  "Healthcare / Medical Front Desk",
  "Legal / Professional Services",
  "E-commerce / Retail Ops",
  "Real Estate / Property Management",
  "HR / Ops / People Management",
  "Finance / Accounting / Bookkeeping",
];

interface SessionSummary {
  total_evaluated: number;
  ready_to_build: number;
  validated_pending_review: number;
  watch_list: number;
  rejected: number;
  recommended_first_build: string;
}

export default function ResearchPage() {
  const [selected, setSelected] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggleVertical(v: string) {
    setSelected((prev) =>
      prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]
    );
  }

  async function runResearch() {
    setRunning(true);
    setError(null);
    setSummary(null);
    try {
      const res = await fetch(`${API_BASE}/research/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ verticals: selected.length > 0 ? selected : VERTICALS }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "Research run failed");
      setSummary(data.aggregator_session_summary);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="p-8 max-w-3xl mx-auto space-y-8">
      <UsageTracker eventType="research_page_view" />
      <div>
        <h1 className="text-xl font-semibold">Research Agent</h1>
        <p className="text-sm text-gray-400 mt-1">Select verticals to scan, or run the full swarm.</p>
      </div>

      <div className="space-y-2">
        {VERTICALS.map((v) => (
          <label key={v} className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={selected.includes(v)}
              onChange={() => toggleVertical(v)}
              className="accent-white"
            />
            <span className="text-sm">{v}</span>
          </label>
        ))}
      </div>

      <button
        onClick={runResearch}
        disabled={running}
        className="rounded-lg bg-white text-black px-6 py-2.5 text-sm font-medium disabled:opacity-40"
      >
        {running ? "Running..." : selected.length === 0 ? "Run Full Swarm" : `Run ${selected.length} Vertical${selected.length > 1 ? "s" : ""}`}
      </button>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {summary && (
        <div className="rounded-lg border border-gray-800 p-6 space-y-3">
          <p className="font-medium">Session Complete</p>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <span className="text-gray-400">Evaluated</span><span>{summary.total_evaluated}</span>
            <span className="text-gray-400">Ready to Build</span><span className="text-green-400">{summary.ready_to_build}</span>
            <span className="text-gray-400">Pending Review</span><span>{summary.validated_pending_review}</span>
            <span className="text-gray-400">Watch List</span><span>{summary.watch_list}</span>
            <span className="text-gray-400">Rejected</span><span className="text-red-400">{summary.rejected}</span>
          </div>
          {summary.recommended_first_build && (
            <p className="text-sm pt-2 border-t border-gray-800">
              <span className="text-gray-400">Recommended first build: </span>
              {summary.recommended_first_build}
            </p>
          )}
        </div>
      )}
    </main>
  );
}
