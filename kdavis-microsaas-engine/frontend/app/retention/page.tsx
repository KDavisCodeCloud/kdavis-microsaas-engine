import { createClient } from "@/lib/supabase/server";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { AgentRosterCard } from "@/components/ui/AgentRosterCard";

const SEQUENCE_TYPES = [
  {
    trigger: "Day 1 post-signup",
    name: "Onboarding Welcome",
    description: "Welcome email + quick-win checklist",
    status: "active",
  },
  {
    trigger: "Day 3 — no feature use",
    name: "Activation Nudge",
    description: "Feature highlight + tutorial link",
    status: "active",
  },
  {
    trigger: "Day 7 — no value event",
    name: "Value Reframe",
    description: "Social proof + persona case study",
    status: "active",
  },
  {
    trigger: "Day 14 — churn risk",
    name: "Rescue Sequence",
    description: "Direct outreach + offer downgrade path",
    status: "active",
  },
  {
    trigger: "invoice.payment_failed",
    name: "Pre-Billing Recovery",
    description: "Payment failure → 3-email recovery sequence",
    status: "active",
  },
  {
    trigger: "subscription.deleted",
    name: "Win-Back",
    description: "30/60/90 day re-engagement after churn",
    status: "active",
  },
];

const N8N_WORKFLOWS = [
  { name: "Weekly Digest",     status: "active",  trigger: "CRON — every Monday 9am" },
  { name: "Retention Trigger", status: "active",  trigger: "Webhook — payment events from Stripe" },
];

const RETENTION_AGENTS = [
  { name: "Retention Monitor",  status: "pending", lastRun: null, output: "Not yet built — Week 5+" },
  { name: "Churn Predictor",    status: "pending", lastRun: null, output: "Not yet built" },
  { name: "Win-Back Composer",  status: "pending", lastRun: null, output: "Not yet built" },
];

export default async function RetentionPage() {
  const supabase = await createClient();
  const { data: sequences } = await supabase
    .from("retention_sequences")
    .select("id, trigger, sequence_type, status, started_at, metadata")
    .order("started_at", { ascending: false })
    .limit(20);

  const liveSequences = sequences ?? [];

  return (
    <DashboardShell>
      <TopBar title="Retention" />
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="space-y-5">
          {/* 6 Retention Loops */}
          <SectionCard title="6 Retention Loops (ships before any feature work)">
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
              {SEQUENCE_TYPES.map((seq) => (
                <div
                  key={seq.name}
                  className="rounded-[10px] p-3.5"
                  style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}
                >
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <p className="text-[12.5px] font-semibold min-w-0 truncate-text" style={{ color: "#eef2f5" }}>{seq.name}</p>
                    <StatusBadge status={seq.status} pill />
                  </div>
                  <p className="text-[11px] font-mono mb-1" style={{ color: "#5b6673" }}>{seq.trigger}</p>
                  <p className="text-[12px]" style={{ color: "#aab4bd" }}>{seq.description}</p>
                </div>
              ))}
            </div>
          </SectionCard>

          {/* n8n Workflows */}
          <SectionCard title="n8n Automation Workflows">
            {N8N_WORKFLOWS.map((wf, i) => (
              <div
                key={wf.name}
                className="flex items-center justify-between py-2.5 min-w-0"
                style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
              >
                <div className="min-w-0">
                  <p className="text-[12.5px] font-semibold" style={{ color: "#eef2f5" }}>{wf.name}</p>
                  <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{wf.trigger}</p>
                </div>
                <StatusBadge status={wf.status} />
              </div>
            ))}
            <p className="text-[11px] font-mono mt-3" style={{ color: "#5b6673" }}>
              n8n runs on Node 22. Start: <span className="font-mono" style={{ color: "#8b96a3" }}>cd ~/projects/n8n && ./start-n8n.sh</span>
            </p>
          </SectionCard>

          {/* Live Sequence Log */}
          <SectionCard title="Active Sequences (Live)">
            {liveSequences.length === 0 ? (
              <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
                No active retention sequences. Sequences activate when tenants hit trigger conditions.
              </p>
            ) : (
              liveSequences.map((s, i) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between py-2.5 min-w-0"
                  style={{ borderTop: i > 0 ? "1px solid #1c222b" : "none" }}
                >
                  <div className="min-w-0">
                    <p className="text-[12px] font-semibold truncate-text" style={{ color: "#eef2f5" }}>{s.sequence_type}</p>
                    <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{s.trigger}</p>
                  </div>
                  <StatusBadge status={s.status} />
                </div>
              ))
            )}
          </SectionCard>

          {/* Retention Agents */}
          <SectionCard title="Retention Agents">
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
              {RETENTION_AGENTS.map((a) => <AgentRosterCard key={a.name} {...a} />)}
            </div>
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
