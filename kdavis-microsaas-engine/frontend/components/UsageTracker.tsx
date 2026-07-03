"use client";

import { useEffect, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export function useTrackEvent() {
  return useCallback(async (eventType: string, metadata: Record<string, unknown> = {}) => {
    try {
      await fetch(`${API_BASE}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ event_type: eventType, metadata }),
      });
    } catch {
      // Silent — tracking must never break the product
    }
  }, []);
}

interface UsageTrackerProps {
  eventType: string;
  metadata?: Record<string, unknown>;
}

export function UsageTracker({ eventType, metadata = {} }: UsageTrackerProps) {
  const track = useTrackEvent();

  useEffect(() => {
    track(eventType, metadata);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return null;
}
