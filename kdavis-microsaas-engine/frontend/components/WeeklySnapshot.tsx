"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface ValueMetrics {
  total_events: number;
  by_type: Record<string, number>;
}

export function WeeklySnapshot({ tenantId }: { tenantId: string }) {
  const [metrics, setMetrics] = useState<ValueMetrics | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API_BASE}/digest/preview/${tenantId}`, {
      method: "POST",
      credentials: "include",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.value_metrics) setMetrics(data.value_metrics);
      })
      .finally(() => setLoading(false));
  }, [tenantId]);

  if (loading) return <p className="text-sm text-gray-400">Loading...</p>;
  if (!metrics) return <p className="text-sm text-gray-400">No activity this week.</p>;

  return (
    <div className="rounded-lg border border-gray-800 p-4 space-y-2">
      <p className="text-sm font-medium">This week</p>
      <p className="text-2xl font-bold">{metrics.total_events} actions</p>
      <ul className="text-sm text-gray-400 space-y-1">
        {Object.entries(metrics.by_type).map(([type, count]) => (
          <li key={type}>
            {type}: <span className="text-white">{count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
