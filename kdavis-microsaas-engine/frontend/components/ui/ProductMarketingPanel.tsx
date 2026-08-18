"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import type { ResearchReport, CampaignBuild } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type FireState = "idle" | "queuing" | "queued" | "error";

const STATUS_COLOR: Record<string, string> = {
  fired: "#6fce8f",
  pending: "#5b6673",
};

function AgentStatusRow({ label, status }: { label: string; status: string | undefined }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] font-mono" style={{ color: "#8b96a3" }}>{label}</span>
      <span className="text-[11px] font-mono" style={{ color: STATUS_COLOR[status ?? "pending"] ?? "#5b6673" }}>
        {status ?? "pending"}
      </span>
    </div>
  );
}

// Shown in a launched opportunity's expand section (frontend/app/pipeline/page.tsx)
// in place of the pre-build Verdict/MRR/Build-CTA content, which no longer
// applies once a product is live. Reads mse_research_reports/campaign_builds/
// mse_leads/mse_dm_sequences/mse_email_sequences/mse_icp_configs directly via
// Supabase (matches this page's and outreach/page.tsx's existing convention
// of reading display data straight from Supabase, not through FastAPI) —
// only the two fire actions go through the backend
// (api/routers/product_marketing.py).
export function ProductMarketingPanel({ productId, vertical }: { productId: string; vertical: string }) {
  const supabase = createClient();

  const [report, setReport] = useState<ResearchReport | null>(null);
  const [campaignBuild, setCampaignBuild] = useState<CampaignBuild | null>(null);
  const [counts, setCounts] = useState<{ leads: number; dmSequences: number; emailSequences: number } | null>(null);
  const [icpConfigured, setIcpConfigured] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  const [researchState, setResearchState] = useState<FireState>("idle");
  const [researchError, setResearchError] = useState<string | null>(null);
  const [campaignState, setCampaignState] = useState<FireState>("idle");
  const [campaignError, setCampaignError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [reportRes, buildRes, leadsRes, dmRes, emailRes, icpRes] = await Promise.all([
      supabase.from("mse_research_reports").select("*").eq("product_id", productId).order("cycle_date", { ascending: false }).limit(1).maybeSingle(),
      supabase.from("campaign_builds").select("*").eq("product_id", productId).order("triggered_at", { ascending: false }).limit(1).maybeSingle(),
      supabase.from("mse_leads").select("*", { count: "exact", head: true }).eq("product_id", productId),
      supabase.from("mse_dm_sequences").select("*", { count: "exact", head: true }).eq("product_id", productId),
      supabase.from("mse_email_sequences").select("*", { count: "exact", head: true }).eq("product_id", productId),
      supabase.from("mse_icp_configs").select("product_id").eq("product_id", productId).maybeSingle(),
    ]);

    setReport((reportRes.data as ResearchReport | null) ?? null);
    setCampaignBuild((buildRes.data as CampaignBuild | null) ?? null);
    setCounts({ leads: leadsRes.count ?? 0, dmSequences: dmRes.count ?? 0, emailSequences: emailRes.count ?? 0 });
    setIcpConfigured(Boolean(icpRes.data));
    setLoading(false);
  }, [supabase, productId]);

  useEffect(() => {
    load();
  }, [load]);

  async function runResearch() {
    setResearchState("queuing");
    setResearchError(null);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Not signed in");

      const res = await fetch(`${API_BASE}/products/${productId}/run-research`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
      setResearchState("queued");
      setTimeout(load, 8000);
    } catch (e: unknown) {
      setResearchError(e instanceof Error ? e.message : "Unknown error");
      setResearchState("error");
    }
  }

  async function runCampaign() {
    setCampaignState("queuing");
    setCampaignError(null);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Not signed in");

      const res = await fetch(`${API_BASE}/products/${productId}/run-campaign`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
      setCampaignState("queued");
      setTimeout(load, 8000);
    } catch (e: unknown) {
      setCampaignError(e instanceof Error ? e.message : "Unknown error");
      setCampaignState("error");
    }
  }

  if (loading) {
    return <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading marketing status…</p>;
  }

  const painLanguage = report?.report_json?.pain_language ?? [];
  const contentAngles = report?.report_json?.content_angles ?? [];
  const icpChannels = report?.report_json?.icp_channels ?? [];

  return (
    <div className="space-y-3">
      {/* Fire buttons */}
      <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
        <p className="text-[10px] font-mono uppercase mb-2.5" style={{ color: "#5b6673" }}>Fire Marketing</p>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={runResearch}
            disabled={researchState === "queuing"}
            className="px-3.5 py-2 rounded-[8px] text-[12px] font-semibold"
            style={{
              backgroundColor: researchState === "queuing" ? "#2a3340" : "#5a96ff1a",
              border: "1px solid #5a96ff",
              color: researchState === "queuing" ? "#5b6673" : "#5a96ff",
              cursor: researchState === "queuing" ? "not-allowed" : "pointer",
            }}
          >
            {researchState === "queuing" ? "Queuing…" : "🔬 Run Research"}
          </button>
          <button
            onClick={runCampaign}
            disabled={campaignState === "queuing" || !report}
            title={!report ? "Run research for this product first" : undefined}
            className="px-3.5 py-2 rounded-[8px] text-[12px] font-semibold"
            style={{
              backgroundColor: !report || campaignState === "queuing" ? "#2a3340" : "#6fce8f",
              color: !report || campaignState === "queuing" ? "#5b6673" : "#0b0e13",
              cursor: !report || campaignState === "queuing" ? "not-allowed" : "pointer",
            }}
          >
            {campaignState === "queuing" ? "Queuing…" : "📣 Fire Campaign"}
          </button>
        </div>
        {researchState === "queued" && (
          <p className="text-[11px] font-mono mt-2" style={{ color: "#5a96ff" }}>
            MKT-R1 queued for {vertical} — refreshes automatically in a few seconds.
          </p>
        )}
        {researchError && <p className="text-[11px] font-mono mt-2" style={{ color: "#e05d5d" }}>{researchError}</p>}
        {campaignState === "queued" && (
          <p className="text-[11px] font-mono mt-2" style={{ color: "#6fce8f" }}>
            Campaign queued — lead sourcing, DM/email drafts, SEO, and community content firing per channel.
          </p>
        )}
        {campaignError && <p className="text-[11px] font-mono mt-2" style={{ color: "#e05d5d" }}>{campaignError}</p>}
      </div>

      {/* Latest research report */}
      <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
        <p className="text-[10px] font-mono uppercase mb-2" style={{ color: "#5b6673" }}>Latest Research</p>
        {!report ? (
          <p className="text-[12px]" style={{ color: "#5b6673" }}>No research run yet for this product — click Run Research.</p>
        ) : (
          <div className="space-y-2">
            <p className="text-[10.5px] font-mono" style={{ color: "#3a4250" }}>Cycle {report.cycle_date}</p>
            {painLanguage.length > 0 && (
              <div>
                <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Pain Language</p>
                {painLanguage.slice(0, 3).map((p, i) => (
                  <p key={i} className="text-[12px]" style={{ color: "#aab4bd" }}>&ldquo;{p.phrase}&rdquo;</p>
                ))}
              </div>
            )}
            {contentAngles.length > 0 && (
              <div>
                <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Content Angles</p>
                {contentAngles.slice(0, 3).map((a, i) => (
                  <p key={i} className="text-[12px]" style={{ color: "#aab4bd" }}>{a.angle}</p>
                ))}
              </div>
            )}
            {icpChannels.length > 0 && (
              <p className="text-[11px] font-mono" style={{ color: "#5eead4" }}>Channels: {icpChannels.join(", ")}</p>
            )}
            {report.report_json.willingness_to_pay_band && (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                Pricing signal: {report.report_json.willingness_to_pay_band}
                {report.report_json.suggested_price ? ` · suggested $${report.report_json.suggested_price}` : ""}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Campaign + outreach status */}
      <div className="rounded-[8px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
        <p className="text-[10px] font-mono uppercase mb-2" style={{ color: "#5b6673" }}>Campaign Status</p>
        {!campaignBuild ? (
          <p className="text-[12px]" style={{ color: "#5b6673" }}>No campaign fired yet.</p>
        ) : (
          <div className="space-y-1.5">
            <AgentStatusRow label="Lead sourcing" status={campaignBuild.lead_finder_status ?? campaignBuild.apollo_status} />
            <AgentStatusRow label="DM sequences (MKT-O2)" status={campaignBuild.dm_sequence_status} />
            <AgentStatusRow label="Email sequences (MKT-O3)" status={campaignBuild.email_sequence_status} />
            <AgentStatusRow label="SEO content (MKT-S1)" status={campaignBuild.seo_factory_status} />
            <AgentStatusRow label="Community content (MKT-V1)" status={campaignBuild.social_status} />
          </div>
        )}
        {counts && (
          <div className="flex gap-4 mt-3 pt-3" style={{ borderTop: "1px solid #1c222b" }}>
            <div>
              <p className="text-[15px] font-bold font-mono" style={{ color: "#eef2f5" }}>{counts.leads}</p>
              <p className="text-[10px] font-mono" style={{ color: "#5b6673" }}>leads</p>
            </div>
            <div>
              <p className="text-[15px] font-bold font-mono" style={{ color: "#eef2f5" }}>{counts.dmSequences}</p>
              <p className="text-[10px] font-mono" style={{ color: "#5b6673" }}>DM sequences</p>
            </div>
            <div>
              <p className="text-[15px] font-bold font-mono" style={{ color: "#eef2f5" }}>{counts.emailSequences}</p>
              <p className="text-[10px] font-mono" style={{ color: "#5b6673" }}>email sequences</p>
            </div>
          </div>
        )}
        {icpConfigured === false && (
          <p className="text-[10.5px] font-mono mt-2.5" style={{ color: "#e8963f" }}>
            No lead-finder ICP config set for this product — lead sourcing will skip Google/registry search until one is added via POST /marketing/icp.
          </p>
        )}
      </div>
    </div>
  );
}
