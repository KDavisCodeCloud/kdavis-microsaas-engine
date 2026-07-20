"use client";

import { useEffect, useState, useCallback } from "react";
import { createClient } from "@/lib/supabase/client";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ProgressBar } from "@/components/ui/ProgressBar";
import type { Opportunity, BuildBrief } from "@/lib/types";

const STATUS_FILTER_OPTIONS = ["all", "READY_TO_BUILD", "validated", "needs_correction", "watch", "rejected"];
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function filterLabel(f: string): string {
  if (f === "all") return "All";
  if (f === "READY_TO_BUILD") return "Build Queue";
  return f.replace(/_/g, " ");
}

const DENSITY_COLOR: Record<string, string> = {
  green: "#6fce8f",
  yellow: "#e8963f",
  red: "#e05d5d",
};

// Verdict v5.0 confidence score (2026-07-19) — lives inside verdict_v2_output,
// same storage pattern as every other v3-v5 field (no dedicated column).
// Distinct from the older top-level build_confidence_score, which is the
// upstream Dispatch submission's own self-reported score, not Verdict's
// independently-verified one.
interface ConfidenceBreakdown {
  pain_evidence?: number;
  gap_verified?: number;
  math_reliability?: number;
  gtm_realism?: number;
}

const CONFIDENCE_BANDS = [
  { min: 90, color: "#3fd17a", label: "STRONG BUILD" },
  { min: 75, color: "#5a96ff", label: "BUILD" },
  { min: 60, color: "#f5a623", label: "CONDITIONAL" },
  { min: 45, color: "#ff8c00", label: "WEAK" },
  { min: 0, color: "#ff4444", label: "DO NOT BUILD" },
];

function confidenceBand(score: number) {
  return CONFIDENCE_BANDS.find((b) => score >= b.min) ?? CONFIDENCE_BANDS[CONFIDENCE_BANDS.length - 1];
}

const BREAKDOWN_LABELS: Record<string, string> = {
  pain_evidence: "Pain Evidence",
  gap_verified: "Gap Verified",
  math_reliability: "Math Reliability",
  gtm_realism: "GTM Realism",
};

function ConfidenceMeter({ score, breakdown }: { score: number; breakdown: ConfidenceBreakdown }) {
  const band = confidenceBand(score);
  return (
    <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] font-mono uppercase" style={{ color: "#5b6673" }}>Verdict Confidence</p>
        <span className="text-[13px] font-bold font-mono" style={{ color: band.color }}>{score}%</span>
      </div>

      {/* Main bar with 75% build-threshold marker */}
      <div className="relative w-full rounded-full mb-1.5" style={{ height: "6px", backgroundColor: "#1c222b" }}>
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${score}%`, backgroundColor: band.color }}
        />
        <div
          className="absolute top-0 bottom-0 w-px"
          style={{ left: "75%", backgroundColor: "#3a4250" }}
          title="75% build threshold"
        />
      </div>
      <p className="text-[11px] font-mono font-semibold mb-3" style={{ color: band.color }}>{band.label}</p>

      {/* Component breakdown */}
      <div className="space-y-1.5">
        {Object.entries(breakdown).map(([key, value]) => (
          <div key={key} className="flex items-center gap-2">
            <span className="text-[10px] font-mono w-28 shrink-0" style={{ color: "#5b6673" }}>
              {BREAKDOWN_LABELS[key] ?? key}
            </span>
            <div className="flex-1 rounded-full" style={{ height: "4px", backgroundColor: "#1c222b" }}>
              <div
                className="h-full rounded-full"
                style={{ width: `${((value ?? 0) / 25) * 100}%`, backgroundColor: band.color }}
              />
            </div>
            <span className="text-[10px] font-mono w-10 text-right shrink-0" style={{ color: "#5b6673" }}>{value ?? 0}/25</span>
          </div>
        ))}
      </div>

      {score < 75 && (
        <p className="text-[11px] mt-3" style={{ color: "#ff8c00" }}>
          ⚠ Score below the 75% build threshold. Review the reasoning above before approving.
        </p>
      )}
    </div>
  );
}

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

  const [reviewComment, setReviewComment] = useState<Record<string, string>>({});
  const [reviewState, setReviewState] = useState<Record<string, "idle" | "submitting" | "error">>({});
  const [reviewError, setReviewError] = useState<Record<string, string>>({});

  const [briefs, setBriefs] = useState<BuildBrief[]>([]);
  const [briefsLoading, setBriefsLoading] = useState(true);
  const [expandedBrief, setExpandedBrief] = useState<string | null>(null);
  const [briefDoc, setBriefDoc] = useState<"code" | "design">("code");

  const fetchData = useCallback(async () => {
    const { data } = await supabase
      .from("opportunity_pipeline")
      .select("id, vertical, pain_point, solution_concept, mrr_calculation, conservative_mrr_potential, build_confidence_score, competition_density, status, rejection_reason, retention_hooks, source_urls, verdict_v2_output, human_review_status, human_review_comment, human_reviewed_by, human_reviewed_at, created_at")
      .order("build_confidence_score", { ascending: false });
    setOpportunities((data ?? []) as Opportunity[]);
    setLoading(false);
  }, [supabase]);

  async function submitReview(opportunityId: string, decision: "approved" | "rejected") {
    if (decision === "rejected" && !window.confirm(
      "Reject this opportunity? It will be permanently removed from the Opportunities list " +
      "(the agent's research and your comment are kept in an archive for tuning, but it will " +
      "no longer show up here)."
    )) {
      return;
    }

    setReviewState((s) => ({ ...s, [opportunityId]: "submitting" }));
    setReviewError((e) => ({ ...e, [opportunityId]: "" }));
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Not signed in");

      const res = await fetch(`${API_BASE}/pipeline/${opportunityId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
        body: JSON.stringify({ decision, comment: reviewComment[opportunityId] || null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
      setReviewState((s) => ({ ...s, [opportunityId]: "idle" }));
      if (decision === "rejected") {
        setExpanded((cur) => (cur === opportunityId ? null : cur));
      }
      await fetchData();
    } catch (e: unknown) {
      setReviewError((err) => ({ ...err, [opportunityId]: e instanceof Error ? e.message : "Unknown error" }));
      setReviewState((s) => ({ ...s, [opportunityId]: "error" }));
    }
  }

  const fetchBriefs = useCallback(async () => {
    const { data } = await supabase
      .from("mse_build_briefs")
      // opportunity_pipeline(...) is a PostgREST embedded select via the
      // real FK (mse_build_briefs.opportunity_id -> opportunity_pipeline.id,
      // migration 20260717000011) -- pulls what a brief actually IS
      // directly, instead of forcing a click into a 10k+ char raw markdown
      // document just to answer "what is this" (real gap Kelvin hit
      // 2026-07-21 on Ninety Nine Comply).
      .select("id, opportunity_id, product_name, product_slug, verdict_score, vertical, claude_code_brief, claude_design_brief, repo_branch, status, activated_monitoring, mrr_at_activation, mrr_sustained_days, created_at, opportunity_pipeline(pain_point, solution_concept, mrr_calculation, conservative_mrr_potential)")
      .order("created_at", { ascending: false });
    setBriefs((data ?? []) as unknown as BuildBrief[]);
    setBriefsLoading(false);
  }, [supabase]);

  // opportunity_id's FK is to-one, but Supabase's JS client types (and
  // sometimes the raw response) represent an embedded relationship as an
  // array regardless of cardinality unless the relationship name is
  // explicitly disambiguated -- normalize both shapes here once rather
  // than at every render site.
  function briefOpportunity(brief: BuildBrief) {
    const opp = brief.opportunity_pipeline;
    if (!opp) return null;
    return Array.isArray(opp) ? opp[0] ?? null : opp;
  }

  useEffect(() => { fetchData(); fetchBriefs(); }, [fetchData, fetchBriefs]);

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
      <TopBar title="Opportunities">
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
                {filterLabel(f)}
                {f !== "all" && (
                  <span className="ml-1.5" style={{ color: "#5b6673" }}>
                    ({opportunities.filter((o) => o.status === f).length})
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Opportunity list */}
          <SectionCard title={`${filterLabel(filter)} Opportunities`}>
            {loading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading opportunities…</p>
            ) : visible.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                {filter === "all"
                  ? "No opportunities yet. Run the research swarm to populate this list."
                  : `No ${filter} opportunities.`}
              </p>
            ) : (
              visible.map((opp, i) => {
                const confidenceScore = typeof opp.verdict_v2_output?.confidence_score === "number"
                  ? (opp.verdict_v2_output.confidence_score as number)
                  : null;
                const belowConfidenceThreshold = confidenceScore !== null && confidenceScore < 75;
                return (
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
                      {opp.rejection_reason && (
                        <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#e05d5d11", border: "1px solid #e05d5d44" }}>
                          <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#e05d5d" }}>Verdict Agent Reasoning</p>
                          <p className="text-[12px]" style={{ color: "#aab4bd" }}>{opp.rejection_reason}</p>
                        </div>
                      )}

                      {typeof opp.verdict_v2_output?.confidence_score === "number" && (
                        <ConfidenceMeter
                          score={opp.verdict_v2_output.confidence_score as number}
                          breakdown={(opp.verdict_v2_output.confidence_breakdown as ConfidenceBreakdown) ?? {}}
                        />
                      )}

                      <p className="text-[10px] font-mono" style={{ color: "#3a4250" }}>Added {new Date(opp.created_at).toLocaleDateString("en-US", { timeZone: "America/Phoenix" })}</p>

                      <div className="rounded-[8px] p-3.5 space-y-2.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                        <div className="flex items-center justify-between">
                          <p className="text-[10px] font-mono uppercase" style={{ color: "#5b6673" }}>Your Review</p>
                          {opp.human_review_status !== "pending" && (
                            <StatusBadge status={opp.human_review_status} />
                          )}
                        </div>
                        {opp.human_reviewed_by && (
                          <p className="text-[10px] font-mono" style={{ color: "#3a4250" }}>
                            {opp.human_review_status} by {opp.human_reviewed_by}
                            {opp.human_reviewed_at ? ` on ${new Date(opp.human_reviewed_at).toLocaleDateString("en-US", { timeZone: "America/Phoenix" })}` : ""}
                          </p>
                        )}
                        {opp.human_review_comment && (
                          <p className="text-[12px]" style={{ color: "#aab4bd" }}>&ldquo;{opp.human_review_comment}&rdquo;</p>
                        )}
                        <textarea
                          value={reviewComment[opp.id] ?? ""}
                          onChange={(e) => setReviewComment((c) => ({ ...c, [opp.id]: e.target.value }))}
                          placeholder="Comment for the agent — why approved/rejected, what to look for next time"
                          rows={2}
                          className="w-full px-3 py-2 rounded-[6px] text-[12px] outline-none resize-none"
                          style={{ backgroundColor: "#0b0e13", border: "1px solid #1c222b", color: "#eef2f5" }}
                        />
                        {reviewError[opp.id] && (
                          <p className="text-[11px] font-mono" style={{ color: "#e05d5d" }}>{reviewError[opp.id]}</p>
                        )}
                        <div className="flex gap-2">
                          <button
                            onClick={() => submitReview(opp.id, "approved")}
                            disabled={reviewState[opp.id] === "submitting" || belowConfidenceThreshold}
                            title={belowConfidenceThreshold ? "Confidence score below 75% — review the reasoning above before approving" : undefined}
                            className="px-4 py-2 rounded-[8px] text-[12px] font-bold"
                            style={{
                              backgroundColor: "#6fce8f",
                              color: "#0b0e13",
                              opacity: reviewState[opp.id] === "submitting" || belowConfidenceThreshold ? 0.5 : 1,
                              cursor: belowConfidenceThreshold ? "not-allowed" : "pointer",
                            }}
                          >
                            Approve → Build Queue
                          </button>
                          <button
                            onClick={() => submitReview(opp.id, "rejected")}
                            disabled={reviewState[opp.id] === "submitting"}
                            className="px-4 py-2 rounded-[8px] text-[12px] font-bold"
                            style={{ backgroundColor: "#e05d5d", color: "#0b0e13", opacity: reviewState[opp.id] === "submitting" ? 0.5 : 1 }}
                          >
                            Reject &amp; Delete
                          </button>
                        </div>
                      </div>

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
              );})
            )}
          </SectionCard>

          {/* Build briefs — generated by brief_generator on every Verdict PASS */}
          <SectionCard title="Build Briefs">
            {briefsLoading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading briefs…</p>
            ) : briefs.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                No build briefs yet. Briefs are generated automatically when Verdict passes an opportunity.
              </p>
            ) : (
              briefs.map((brief, i) => {
                const opp = briefOpportunity(brief);
                return (
                <div key={brief.id}>
                  <button
                    onClick={() => { setExpandedBrief(expandedBrief === brief.id ? null : brief.id); setBriefDoc("code"); }}
                    className="w-full text-left py-3 min-w-0"
                    style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="flex-1 min-w-0">
                        <p className="text-[12.5px] font-semibold truncate-text min-w-0 mb-0.5" style={{ color: "#eef2f5" }}>{brief.product_name}</p>
                        <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>{brief.vertical}</p>
                        {opp?.pain_point && (
                          <p className="text-[11px] truncate-text mt-0.5" style={{ color: "#8b96a3" }}>{opp.pain_point}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {brief.verdict_score !== null && (
                          <p className="text-[13px] font-bold font-mono" style={{ color: "#5eead4" }}>{brief.verdict_score}/100</p>
                        )}
                        <StatusBadge status={brief.status} />
                        <span className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{expandedBrief === brief.id ? "▲" : "▼"}</span>
                      </div>
                    </div>
                  </button>

                  {expandedBrief === brief.id && (
                    <div className="ml-2 mb-3 space-y-2.5">
                      {opp && (
                        <div className="rounded-[8px] p-3.5 space-y-2" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                          <p className="text-[10px] font-mono uppercase" style={{ color: "#5b6673" }}>What This Is</p>
                          <p className="text-[13px]" style={{ color: "#eef2f5" }}>{opp.solution_concept}</p>
                          {opp.pain_point && (
                            <>
                              <p className="text-[10px] font-mono uppercase mt-2" style={{ color: "#5b6673" }}>Pain Point</p>
                              <p className="text-[12px]" style={{ color: "#aab4bd" }}>{opp.pain_point}</p>
                            </>
                          )}
                          <div className="flex items-center gap-4 mt-2">
                            <p className="text-[13px] font-bold font-mono" style={{ color: "#5eead4" }}>
                              ${Number(opp.conservative_mrr_potential).toLocaleString()}/mo
                            </p>
                            {opp.mrr_calculation && (
                              <p className="text-[10px] font-mono flex-1" style={{ color: "#5b6673" }}>{opp.mrr_calculation}</p>
                            )}
                          </div>
                        </div>
                      )}
                      <div className="flex gap-2">
                        <button
                          onClick={() => setBriefDoc("code")}
                          className="px-3 py-1.5 rounded-[8px] text-[11px] font-mono"
                          style={{
                            backgroundColor: briefDoc === "code" ? "#5eead41a" : "#10151b",
                            border: `1px solid ${briefDoc === "code" ? "#5eead4" : "#1c222b"}`,
                            color: briefDoc === "code" ? "#5eead4" : "#8b96a3",
                          }}
                        >
                          Claude Code Brief
                        </button>
                        <button
                          onClick={() => setBriefDoc("design")}
                          className="px-3 py-1.5 rounded-[8px] text-[11px] font-mono"
                          style={{
                            backgroundColor: briefDoc === "design" ? "#5eead41a" : "#10151b",
                            border: `1px solid ${briefDoc === "design" ? "#5eead4" : "#1c222b"}`,
                            color: briefDoc === "design" ? "#5eead4" : "#8b96a3",
                          }}
                        >
                          Claude Design Brief
                        </button>
                      </div>
                      <div
                        className="rounded-[8px] p-3.5 overflow-y-auto"
                        style={{ backgroundColor: "#10151b", border: "1px solid #1c222b", maxHeight: "420px" }}
                      >
                        <pre className="text-[11.5px] whitespace-pre-wrap font-mono" style={{ color: "#aab4bd" }}>
                          {(briefDoc === "code" ? brief.claude_code_brief?.markdown : brief.claude_design_brief?.markdown)
                            ?? "No content generated for this brief yet."}
                        </pre>
                      </div>
                      {brief.repo_branch && (
                        <p className="text-[10px] font-mono" style={{ color: "#3a4250" }}>Branch: {brief.repo_branch}</p>
                      )}
                    </div>
                  )}
                </div>
              );})
            )}
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
