"use client";

import { useEffect, useState } from "react";

interface MilestoneToastProps {
  milestones: string[];
}

const MILESTONE_LABELS: Record<string, string> = {
  first_event: "First action logged",
  ten_events: "10 actions — you're using it",
  fifty_events: "50 actions — real traction",
  hundred_events: "100 actions milestone",
  five_hundred_events: "500 actions — power user",
};

export function MilestoneToast({ milestones }: MilestoneToastProps) {
  const [visible, setVisible] = useState(milestones.length > 0);

  useEffect(() => {
    if (milestones.length > 0) {
      setVisible(true);
      const t = setTimeout(() => setVisible(false), 5000);
      return () => clearTimeout(t);
    }
  }, [milestones]);

  if (!visible || milestones.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {milestones.map((key) => (
        <div
          key={key}
          className="rounded-lg bg-gray-900 text-white px-4 py-3 text-sm shadow-lg"
        >
          {MILESTONE_LABELS[key] ?? key}
        </div>
      ))}
    </div>
  );
}
