"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type TriggerState = "idle" | "running" | "queued" | "error";

export function ResearchTrigger() {
  const [state, setState] = useState<TriggerState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);

  async function runSwarm() {
    setState("running");
    setError(null);
    setSessionId(null);
    try {
      const res = await fetch(`${API_BASE}/research/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer internal" },
        body: JSON.stringify({ verticals: [] }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
      setSessionId(data.session_id ?? null);
      setState("queued");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
      setState("error");
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-[12px]" style={{ color: "#aab4bd" }}>
        Scan all 6 verticals for validated opportunities. Results appear in Pipeline when complete (~2–3 min).
      </p>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={runSwarm}
          disabled={state === "running" || state === "queued"}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-[8px] text-[12.5px] font-semibold transition-colors"
          style={{
            backgroundColor: state === "running" || state === "queued" ? "#2a3340" : "#6fce8f",
            color: state === "running" || state === "queued" ? "#5b6673" : "#0b0e13",
            cursor: state === "running" || state === "queued" ? "not-allowed" : "pointer",
          }}
        >
          {state === "running" ? "Queuing…" : state === "queued" ? "Swarm Running" : "Run Full Swarm Now"}
        </button>
        <a
          href="/research"
          className="text-[12px]"
          style={{ color: "#5eead4", textDecoration: "none" }}
        >
          Advanced options →
        </a>
      </div>

      {state === "queued" && (
        <p className="text-[11px] font-mono" style={{ color: "#6fce8f" }}>
          Queued{sessionId ? ` · session ${sessionId.slice(0, 8)}` : ""}. Results appear in{" "}
          <a href="/pipeline" style={{ color: "#5eead4" }}>Pipeline</a> when complete.
        </p>
      )}
      {state === "error" && error && (
        <p className="text-[11px] font-mono" style={{ color: "#e05d5d" }}>
          Error: {error}
        </p>
      )}
    </div>
  );
}
