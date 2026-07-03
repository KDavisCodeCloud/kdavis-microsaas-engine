import { UsageTracker } from "@/components/UsageTracker";
import { WeeklySnapshot } from "@/components/WeeklySnapshot";

export default function DashboardPage() {
  const tenantId = "REPLACE_WITH_AUTH_TENANT_ID";

  return (
    <main className="p-8 max-w-4xl mx-auto space-y-8">
      <UsageTracker eventType="dashboard_view" />
      <div>
        <h1 className="text-xl font-semibold">Dashboard</h1>
      </div>
      <WeeklySnapshot tenantId={tenantId} />
    </main>
  );
}
