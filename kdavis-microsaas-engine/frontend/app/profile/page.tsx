"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { DashboardShell } from "@/components/shell/DashboardShell";
import { TopBar } from "@/components/shell/TopBar";
import { SectionCard } from "@/components/ui/SectionCard";

type ProfileInfo = {
  email: string;
  role: string;
  lastSignInAt: string | null;
  createdAt: string | null;
};

export default function ProfilePage() {
  const supabase = createClient();
  const [profile, setProfile] = useState<ProfileInfo | null>(null);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      const user = data.user;
      if (!user) return;
      setProfile({
        email: user.email ?? "",
        role: (user.app_metadata?.role as string) ?? "authenticated",
        lastSignInAt: user.last_sign_in_at ?? null,
        createdAt: user.created_at ?? null,
      });
    });
  }, [supabase]);

  const fmt = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/Phoenix", dateStyle: "medium", timeStyle: "short" }) : "—";

  return (
    <DashboardShell>
      <TopBar title="Profile" />
      <div className="flex-1 overflow-y-auto p-6 min-w-0">
        <div className="max-w-md space-y-5">
          <SectionCard title="Operator">
            <div className="flex items-center gap-3 mb-4">
              <div
                className="w-11 h-11 rounded-full flex items-center justify-center text-[15px] font-bold shrink-0"
                style={{ backgroundColor: "#6fce8f", color: "#0b0e13" }}
              >
                {profile?.email?.[0]?.toUpperCase() ?? "K"}
              </div>
              <div className="min-w-0">
                <p className="text-[13px] font-semibold truncate-text" style={{ color: "#eef2f5" }}>{profile?.email ?? "Loading…"}</p>
                <p className="text-[11px] font-mono uppercase" style={{ color: "#5eead4" }}>{profile?.role ?? ""}</p>
              </div>
            </div>
            <div className="space-y-2.5">
              <div className="flex items-center justify-between">
                <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Account created</p>
                <p className="text-[11px] font-mono" style={{ color: "#aab4bd" }}>{fmt(profile?.createdAt ?? null)}</p>
              </div>
              <div className="flex items-center justify-between">
                <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>Last sign-in</p>
                <p className="text-[11px] font-mono" style={{ color: "#aab4bd" }}>{fmt(profile?.lastSignInAt ?? null)}</p>
              </div>
            </div>
          </SectionCard>
        </div>
      </div>
    </DashboardShell>
  );
}
