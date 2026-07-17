"use client";

import { useEffect, useState, useCallback } from "react";
import { createClient } from "@/lib/supabase/client";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ProgressBar } from "@/components/ui/ProgressBar";
import type { Opportunity } from "@/lib/types";

const STATUS_FILTER_OPTIONS = ["all", "READY_TO_BUILD", "validated", "watch", "rejected"];
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const DENSITY_COLOR: Record<string, string> = {
  green: "#6fce8f",
  yellow: "#e8963f",
  red: "#e05d5d",
};

export default function PipelinePage() {
  const supabase = createClient();
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [buildFormFor, setBuildFormFor] = useState<string | null>(null);
  const [stripeKey, setStripeKey] = useState("");
  const [buildState, setBuildState] = useState<"idle" | "queuing" | "queued" | "error">("idle");
  const [buildError, setBuildError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const { data } = await supabase
      .from("opportunity_pipeline")
      .select("id, vertical, pain_point, solution_concept, mrr_calculation, conservative_mrr_potential, build_confidence_score, competition_density, status, retention_hooks, source_urls, created_at")
      .order("build_confidence_score", { ascending: false });
    setOpportunities((data ?? []) as Opportunity[]);
    setLoading(false);
  }, [supabase]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const visible = opportunities.filter((o) => filter === "all" || o.status === filter);
  const ready = opportunities.filter((o) => o.status === "READY_TO_BUILD").length;
  const validated = opportunities.filter((o) => o.status === "validated").length;

  async function submitBuild(opportunityId: string) {
    setBuildState("queuing");
    setBuildError(null);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Not signed in");

      const res = await fetch(`${API_BASE}/factory/build/${opportunityId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({ stripe_api_key: stripeKey }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
      setBuildState("queued");
      setStripeKey("");
    } catch (e: unknown) {
      setBuildError(e instanceof Error ? e.message : "Unknown error");
      setBuildState("error");
    }
  }

  return (
    <DashboardShell>
      <TopBar title="Opportunity Pipeline">
        <span className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
          {opportunities.length} total · {ready} ready · {validated} validated
        </span>
      </TopBar>

      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {/* Filter tabs */}
          <div className="flex gap-2 flex-wrap">
            {STATUS_FILTER_OPTIONS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className="px-3 py-1.5 rounded-[8px] text-[11px] font-mono transition-colors"
                style={{
                  backgroundColor: filter === f ? "#5eead41a" : "#10151b",
                  border: `1px solid ${filter === f ? "#5eead4" : "#1c222b"}`,
                  color: filter === f ? "#5eead4" : "#8b96a3",
                }}
              >
                {f === "all" ? "All" : f.replace(/_/g, " ")}
                {f !== "all" && (
                  <span className="ml-1.5" style={{ color: "#5b6673" }}>
                    ({opportunities.filter((o) => o.status === f).length})
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Opportunity list */}
          <SectionCard title={`${filter === "all" ? "All" : filter.replace(/_/g, " ")} Opportunities`}>
            {loading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading pipeline…</p>
            ) : visible.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                {filter === "all"
                  ? "Pipeline is empty. Run the research swarm to populate it."
                  : `No ${filter} opportunities.`}
              </p>
            ) : (
              visible.map((opp, i) => (
                <div key={opp.id}>
                  <button
                    onClick={() => setExpanded(expanded === opp.id ? null : opp.id)}
                    className="w-full text-left py-3 min-w-0"
                    style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="shrink-0 w-1 rounded-full" style={{ height: "40px", backgroundColor: DENSITY_COLOR[opp.competition_density ?? ""] ?? "#3a4250" }} />
                      <div className="flex-1 min-w-0">
                        <p className="text-[12.5px] font-semibold truncate-text min-w-0 mb-0.5" style={{ color: "#eef2f5" }}>{opp.solution_concept}</p>
                        <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>{opp.vertical}</p>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <div className="text-right">
                          <p className="text-[13px] font-bold font-mono" style={{ color: "#5eead4" }}>${Number(opp.conservative_mrr_potential).toLocaleString()}/mo</p>
                          <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{opp.build_confidence_score ?? 0}% conf.</p>
                        </div>
                        <StatusBadge status={opp.status} />
                        <span className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{expanded === opp.id ? "▲" : "▼"}</span>
                      </div>
                    </div>
                    <div className="ml-5 mt-2">
                      <ProgressBar value={opp.build_confidence_score ?? 0} accent={DENSITY_COLOR[opp.competition_density ?? ""] ?? "#5eead4"} height={3} />
                    </div>
                  </button>

                  {expanded === opp.id && (
                    <div className="ml-5 mb-3 space-y-3">
                      <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                        <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Pain Point</p>
                        <p className="text-[12px]" style={{ color: "#aab4bd" }}>{opp.pain_point}</p>
                      </div>
                      {opp.mrr_calculation && (
                        <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                          <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>MRR Math</p>
                          <p className="text-[12px] font-mono" style={{ color: "#aab4bd" }}>{opp.mrr_calculation}</p>
                        </div>
                      )}
                      {opp.source_urls && opp.source_urls.length > 0 && (
                        <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                          <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Sources</p>
                          {opp.source_urls.map((url, j) => (
                            <p key={j} className="text-[11px] font-mono truncate-text" style={{ color: "#5eead4" }}>{url}</p>
                          ))}
                        </div>
                      )}
                      <p className="text-[10px] font-mono" style={{ color: "#3a4250" }}>Added {new Date(opp.created_at).toLocaleDateString("en-US", { timeZone: "America/Phoenix" })}</p>

                      {opp.status === "READY_TO_BUILD" && (
                        <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#6fce8f11", border: "1px solid #6fce8f44" }}>
                          {buildFormFor !== opp.id ? (
                            <button
                              onClick={() => { setBuildFormFor(opp.id); setBuildState("idle"); setBuildError(null); }}
                              className="px-4 py-2 rounded-[8px] text-[12px] font-bold"
                              style={{ backgroundColor: "#6fce8f", color: "#0b0e13" }}
                            >
                              Build This Product →
                            </button>
                          ) : buildState === "queued" ? (
                            <p className="text-[12px] font-semibold" style={{ color: "#6fce8f" }}>
                              Build queued. Status will move to &ldquo;building&rdquo; then &ldquo;launched&rdquo; — this takes several minutes (Supabase provisioning alone can take up to 5). Refresh to check.
                            </p>
                          ) : (
                            <div className="space-y-2.5">
                              <p className="text-[11px] font-mono uppercase" style={{ color: "#5b6673" }}>
                                Stripe secret key for this product&apos;s dedicated account
                              </p>
                              <input
                                type="password"
                                value={stripeKey}
                                onChange={(e) => setStripeKey(e.target.value)}
                                placeholder="sk_live_..."
                                className="w-full px-3 py-2 rounded-[6px] text-[12px] font-mono outline-none"
                                style={{ backgroundColor: "#10151b", border: "1px solid #1c222b", color: "#eef2f5" }}
                              />
                              {buildError && (
                                <p className="text-[11px] font-mono" style={{ color: "#e05d5d" }}>{buildError}</p>
                              )}
                              <div className="flex gap-2">
                                <button
                                  onClick={() => submitBuild(opp.id)}
                                  disabled={!stripeKey || buildState === "queuing"}
                                  className="px-4 py-2 rounded-[8px] text-[12px] font-bold"
                                  style={{
                                    backgroundColor: !stripeKey || buildState === "queuing" ? "#2a3340" : "#6fce8f",
                                    color: !stripeKey || buildState === "queuing" ? "#5b6673" : "#0b0e13",
                                  }}
                                >
                                  {buildState === "queuing" ? "Queuing…" : "Confirm & Build"}
                                </button>
                                <button
                                  onClick={() => { setBuildFormFor(null); setStripeKey(""); }}
                                  className="px-4 py-2 rounded-[8px] text-[12px] font-semibold"
                                  style={{ backgroundColor: "transparent", border: "1px solid #1c222b", color: "#8b96a3" }}
                                >
                                  Cancel
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
