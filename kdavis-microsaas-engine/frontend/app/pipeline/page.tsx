"use client";

import { useEffect, useState } from "react";
import { UsageTracker } from "@/components/UsageTracker";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Opportunity {
  id: string;
  vertical: string;
  solution_concept: string;
  conservative_mrr_potential: number;
  build_confidence_score: number;
  competition_density: "red" | "yellow" | "green";
  status: string;
  owner: string | null;
}

const DENSITY_COLOR = {
  green: "text-green-400",
  yellow: "text-yellow-400",
  red: "text-red-400",
};

export default function PipelinePage() {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/pipeline`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => setOpportunities(d.opportunities ?? []))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="p-8 max-w-5xl mx-auto space-y-6">
      <UsageTracker eventType="pipeline_view" />
      <h1 className="text-xl font-semibold">Opportunity Pipeline</h1>
      {loading && <p className="text-gray-400 text-sm">Loading...</p>}
      <div className="space-y-3">
        {opportunities.map((opp) => (
          <div key={opp.id} className="rounded-lg border border-gray-800 p-4 flex items-start justify-between gap-4">
            <div className="space-y-1">
              <p className="font-medium">{opp.solution_concept}</p>
              <p className="text-sm text-gray-400">{opp.vertical}</p>
            </div>
            <div className="text-right space-y-1 shrink-0">
              <p className="text-sm font-mono">${opp.conservative_mrr_potential.toLocaleString()}/mo</p>
              <p className="text-sm">Confidence: {opp.build_confidence_score}/100</p>
              <p className={`text-xs font-medium ${DENSITY_COLOR[opp.competition_density]}`}>
                {opp.competition_density.toUpperCase()}
              </p>
              <span className="text-xs rounded px-1.5 py-0.5 bg-gray-800">{opp.status}</span>
            </div>
          </div>
        ))}
        {!loading && opportunities.length === 0 && (
          <p className="text-gray-400 text-sm">No opportunities in pipeline yet. Run the research agent.</p>
        )}
      </div>
    </main>
  );
}
