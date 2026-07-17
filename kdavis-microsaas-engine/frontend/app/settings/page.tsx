"use client";

import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";

export default function SettingsPage() {
  return (
    <DashboardShell>
      <TopBar title="Settings" />
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="max-w-md space-y-5">
          <SectionCard title="Display">
            <div className="flex items-center justify-between">
              <p className="text-[12px]" style={{ color: "#aab4bd" }}>Timezone</p>
              <p className="text-[11px] font-mono" style={{ color: "#5eead4" }}>America/Phoenix (Arizona)</p>
            </div>
          </SectionCard>
          <SectionCard title="Access">
            <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
              Operator roles (admin / marketing / rnd) are managed in Supabase Auth and control
              which internal factory tables you can read. Contact the account owner to change yours.
            </p>
          </SectionCard>
          <SectionCard title="More settings">
            <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>
              Nothing else configurable here yet.
            </p>
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
