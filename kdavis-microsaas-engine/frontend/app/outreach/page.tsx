"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import type { DmSequence, ApolloLead, LinkedInLead } from "@/lib/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function leadDisplayName(seq: DmSequence): string {
  const lead = seq.mse_apollo_leads ?? seq.mse_linkedin_leads;
  if (!lead) return "Unknown";
  return `${lead.first_name ?? "Unknown"} ${lead.last_name ?? ""}`.trim();
}

function leadCompanyLine(seq: DmSequence): string {
  const lead = seq.mse_apollo_leads ?? seq.mse_linkedin_leads;
  return lead?.company ? ` · ${lead.company}` : "";
}

const SOURCE_LABEL: Record<DmSequence["lead_source"], string> = {
  apollo: "Email",
  linkedin_manual: "LinkedIn",
  linkedin_engager: "LinkedIn · Engager",
};

const SOURCE_COLOR: Record<DmSequence["lead_source"], string> = {
  apollo: "#5eead4",
  linkedin_manual: "#e8963f",
  linkedin_engager: "#6fce8f",
};

export default function OutreachPage() {
  const supabase = createClient();
  const [sequences, setSequences] = useState<DmSequence[]>([]);
  const [readyToPaste, setReadyToPaste] = useState<DmSequence[]>([]);
  const [leads, setLeads] = useState<ApolloLead[]>([]);
  const [linkedinLeads, setLinkedinLeads] = useState<LinkedInLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const leadEmbed =
      "*, mse_apollo_leads(id, first_name, last_name, company, title, email, linkedin_url), " +
      "mse_linkedin_leads(id, first_name, last_name, company, title, linkedin_url, source, interaction_type, interaction_note)";

    const [{ data: seqData }, { data: readyData }, { data: leadData }, { data: liLeadData }] = await Promise.all([
      supabase.from("mse_dm_sequences").select(leadEmbed).eq("status", "pending_hitl").order("created_at", { ascending: true }),
      // 'approved_manual' -- LinkedIn-sourced sequences a human already
      // approved but that MKT-O5 will never touch (it only polls
      // 'approved_hitl'); this is Kelvin's copy-and-paste queue.
      supabase.from("mse_dm_sequences").select(leadEmbed).eq("status", "approved_manual").order("hitl_approved_at", { ascending: true }),
      supabase
        .from("mse_apollo_leads")
        .select("*")
        .not("linkedin_url", "is", null)
        .is("linkedin_contacted_at", null)
        .order("created_at", { ascending: true }),
      supabase
        .from("mse_linkedin_leads")
        .select("*")
        .eq("status", "pending_dm")
        .order("created_at", { ascending: true }),
    ]);
    setSequences((seqData ?? []) as unknown as DmSequence[]);
    setReadyToPaste((readyData ?? []) as unknown as DmSequence[]);
    setLeads((leadData ?? []) as ApolloLead[]);
    setLinkedinLeads((liLeadData ?? []) as LinkedInLead[]);
    setLoading(false);
  }, [supabase]);

  useEffect(() => { fetchData(); }, [fetchData]);

  async function callBackend(path: string) {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) throw new Error("Not signed in");
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` },
      body: JSON.stringify({ resolved_by: session.user.email }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? `API error ${res.status}`);
    return data;
  }

  async function handleApprove(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await callBackend(`/outreach/dm-sequences/${id}/approve`);
      await fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
    setBusyId(null);
  }

  async function handleReject(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await callBackend(`/outreach/dm-sequences/${id}/reject`);
      await fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
    setBusyId(null);
  }

  async function handleMarkContacted(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await callBackend(`/outreach/leads/${id}/mark-contacted`);
      await fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
    setBusyId(null);
  }

  async function handleMarkSent(linkedinLeadId: string) {
    setBusyId(linkedinLeadId);
    setError(null);
    try {
      await callBackend(`/outreach/linkedin-leads/${linkedinLeadId}/mark-sent`);
      await fetchData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    }
    setBusyId(null);
  }

  async function handleCopy(text: string) {
    await navigator.clipboard.writeText(text);
  }

  return (
    <DashboardShell>
      <TopBar title="Outreach">
        <span className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
          {sequences.length} pending approval · {readyToPaste.length} ready to paste · {linkedinLeads.length} LinkedIn queued
        </span>
      </TopBar>

      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {error && (
            <div className="rounded-[8px] p-3" style={{ backgroundColor: "#e05d5d1a", border: "1px solid #e05d5d" }}>
              <p className="text-[11px] font-mono" style={{ color: "#e05d5d" }}>{error}</p>
            </div>
          )}

          <SectionCard title="Pending DM Approval — email sends via Resend, LinkedIn is pasted manually">
            {loading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading…</p>
            ) : sequences.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                Nothing pending. New sequences appear here once MKT-O2 writes them.
              </p>
            ) : (
              sequences.map((seq, i) => (
                <div key={seq.id} className="py-3" style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}>
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span
                          className="px-1.5 py-0.5 rounded-[4px] text-[9.5px] font-bold uppercase tracking-wide shrink-0"
                          style={{ backgroundColor: SOURCE_COLOR[seq.lead_source], color: "#0b0e13" }}
                        >
                          {SOURCE_LABEL[seq.lead_source]}
                        </span>
                        <p className="text-[12.5px] font-semibold truncate-text" style={{ color: "#eef2f5" }}>
                          {leadDisplayName(seq)}{leadCompanyLine(seq)}
                        </p>
                      </div>
                      <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>
                        {seq.mse_apollo_leads?.email ?? seq.mse_linkedin_leads?.linkedin_url ?? "no contact on file"}
                      </p>
                      {seq.mse_linkedin_leads?.source === "linkedin_engager" && seq.mse_linkedin_leads.interaction_note && (
                        <p className="text-[11px] font-mono truncate-text" style={{ color: "#6fce8f" }}>
                          {seq.mse_linkedin_leads.interaction_type ?? "engaged"} on: {seq.mse_linkedin_leads.interaction_note}
                        </p>
                      )}
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <button
                        onClick={() => handleApprove(seq.id)}
                        disabled={busyId === seq.id}
                        className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                        style={{ backgroundColor: "#6fce8f", color: "#0b0e13", opacity: busyId === seq.id ? 0.5 : 1 }}
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleReject(seq.id)}
                        disabled={busyId === seq.id}
                        className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                        style={{ backgroundColor: "#e05d5d1a", border: "1px solid #e05d5d", color: "#e05d5d", opacity: busyId === seq.id ? 0.5 : 1 }}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                  <div className="rounded-[8px] p-3 mb-1.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                    <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Touch 1</p>
                    <p className="text-[12px]" style={{ color: "#aab4bd" }}>{seq.touch_1}</p>
                  </div>
                  <div className="rounded-[8px] p-3" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                    <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Touch 2 (+3 days)</p>
                    <p className="text-[12px]" style={{ color: "#aab4bd" }}>{seq.touch_2}</p>
                  </div>
                </div>
              ))
            )}
          </SectionCard>

          <SectionCard title="Ready to Paste — LinkedIn (approved, not auto-sent)">
            <div
              className="rounded-[8px] p-3.5 mb-4 flex items-start gap-2.5"
              style={{ backgroundColor: "#e8963f22", border: "2px solid #e8963f" }}
            >
              <span style={{ fontSize: "18px", lineHeight: 1 }}>⚠️</span>
              <p className="text-[13px] font-bold uppercase tracking-wide" style={{ color: "#e8963f" }}>
                Manual send only. Copy the text below, open the profile, paste, send yourself,
                then Mark Sent. Nothing here is ever posted to LinkedIn automatically.
              </p>
            </div>
            {loading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading…</p>
            ) : readyToPaste.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                Nothing approved and waiting yet.
              </p>
            ) : (
              readyToPaste.map((seq, i) => {
                const lead = seq.mse_linkedin_leads;
                return (
                  <div key={seq.id} className="py-3" style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}>
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 mb-0.5">
                          <span
                            className="px-1.5 py-0.5 rounded-[4px] text-[9.5px] font-bold uppercase tracking-wide shrink-0"
                            style={{ backgroundColor: SOURCE_COLOR[seq.lead_source], color: "#0b0e13" }}
                          >
                            {SOURCE_LABEL[seq.lead_source]}
                          </span>
                          <p className="text-[12.5px] font-semibold truncate-text" style={{ color: "#eef2f5" }}>
                            {leadDisplayName(seq)}{leadCompanyLine(seq)}
                          </p>
                        </div>
                        <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>
                          {lead?.title ? `${lead.title} · ` : ""}{lead?.linkedin_url ?? "no profile on file"}
                        </p>
                      </div>
                      <div className="flex gap-2 shrink-0">
                        {lead?.linkedin_url && (
                          <a
                            href={lead.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                            style={{ backgroundColor: "#5eead41a", border: "1px solid #5eead4", color: "#5eead4", textDecoration: "none" }}
                          >
                            Open LinkedIn →
                          </a>
                        )}
                        <button
                          onClick={() => handleCopy(seq.touch_1)}
                          className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                          style={{ backgroundColor: "#1c222b", color: "#eef2f5" }}
                        >
                          Copy Touch 1
                        </button>
                        {lead && (
                          <button
                            onClick={() => handleMarkSent(lead.id)}
                            disabled={busyId === lead.id}
                            className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                            style={{ backgroundColor: "#6fce8f", color: "#0b0e13", opacity: busyId === lead.id ? 0.5 : 1 }}
                          >
                            Mark Sent
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="rounded-[8px] p-3 mb-1.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                      <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Touch 1</p>
                      <p className="text-[12px]" style={{ color: "#aab4bd" }}>{seq.touch_1}</p>
                    </div>
                    <div className="rounded-[8px] p-3" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
                      <p className="text-[10px] font-mono uppercase mb-1" style={{ color: "#5b6673" }}>Touch 2 (+3 days)</p>
                      <p className="text-[12px]" style={{ color: "#aab4bd" }}>{seq.touch_2}</p>
                    </div>
                  </div>
                );
              })
            )}
          </SectionCard>

          <SectionCard title="LinkedIn Manual Outreach — Apollo leads with a LinkedIn profile on file">
            <div
              className="rounded-[8px] p-3.5 mb-4 flex items-start gap-2.5"
              style={{ backgroundColor: "#e8963f22", border: "2px solid #e8963f" }}
            >
              <span style={{ fontSize: "18px", lineHeight: 1 }}>⚠️</span>
              <p className="text-[13px] font-bold uppercase tracking-wide" style={{ color: "#e8963f" }}>
                Manual outreach only — never automated. Open each profile below and send
                the cold DM yourself. Do not build or run any tool that sends these automatically.
              </p>
            </div>
            {loading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading…</p>
            ) : leads.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                No LinkedIn leads waiting on manual outreach.
              </p>
            ) : (
              leads.map((lead, i) => (
                <div
                  key={lead.id}
                  className="flex items-center justify-between gap-3 py-2.5 min-w-0"
                  style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span
                        className="px-1.5 py-0.5 rounded-[4px] text-[9.5px] font-bold uppercase tracking-wide shrink-0"
                        style={{ backgroundColor: "#e8963f", color: "#0b0e13" }}
                      >
                        Manual send
                      </span>
                      <p className="text-[12.5px] font-semibold truncate-text" style={{ color: "#eef2f5" }}>
                        {lead.first_name ?? "Unknown"} {lead.last_name ?? ""}
                      </p>
                    </div>
                    <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>
                      {[lead.title, lead.company].filter(Boolean).join(" · ") || "—"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <a
                      href={lead.linkedin_url ?? "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                      style={{ backgroundColor: "#5eead41a", border: "1px solid #5eead4", color: "#5eead4", textDecoration: "none" }}
                    >
                      Open LinkedIn →
                    </a>
                    <button
                      onClick={() => handleMarkContacted(lead.id)}
                      disabled={busyId === lead.id}
                      className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold transition-colors"
                      style={{ backgroundColor: "#6fce8f", color: "#0b0e13", opacity: busyId === lead.id ? 0.5 : 1 }}
                    >
                      Mark Contacted
                    </button>
                  </div>
                </div>
              ))
            )}
          </SectionCard>

          <SectionCard title="LinkedIn Leads Queued — waiting on MKT-O2 to write DM copy">
            {loading ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Loading…</p>
            ) : linkedinLeads.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                Nothing queued. Leads appear here right after a CSV upload or engager paste to
                POST /marketing/linkedin/intake, until n8n's daily job runs MKT-O2 on them.
              </p>
            ) : (
              linkedinLeads.map((lead, i) => (
                <div
                  key={lead.id}
                  className="flex items-center justify-between gap-3 py-2.5 min-w-0"
                  style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span
                        className="px-1.5 py-0.5 rounded-[4px] text-[9.5px] font-bold uppercase tracking-wide shrink-0"
                        style={{ backgroundColor: lead.source === "linkedin_engager" ? "#6fce8f" : "#e8963f", color: "#0b0e13" }}
                      >
                        {lead.source === "linkedin_engager" ? "Engager" : "Manual"}
                      </span>
                      <p className="text-[12.5px] font-semibold truncate-text" style={{ color: "#eef2f5" }}>
                        {lead.first_name ?? "Unknown"} {lead.last_name ?? ""}
                      </p>
                    </div>
                    <p className="text-[11px] font-mono truncate-text" style={{ color: "#5b6673" }}>
                      {[lead.title, lead.company].filter(Boolean).join(" · ") || "—"}
                      {lead.interaction_note ? ` · ${lead.interaction_note}` : ""}
                    </p>
                  </div>
                  <a
                    href={lead.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-3 py-1.5 rounded-[8px] text-[11px] font-semibold shrink-0 transition-colors"
                    style={{ backgroundColor: "#5eead41a", border: "1px solid #5eead4", color: "#5eead4", textDecoration: "none" }}
                  >
                    Open LinkedIn →
                  </a>
                </div>
              ))
            )}
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
