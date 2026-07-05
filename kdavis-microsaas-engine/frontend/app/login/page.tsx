"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const [email, setEmail] = useState("kdav2k5@gmail.com");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const supabase = createClient();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) setError(error.message);
    else setSent(true);
    setLoading(false);
  }

  return (
    <div className="min-h-screen bg-base flex items-center justify-center">
      <div className="w-full max-w-sm p-8 rounded-[14px] border border-border" style={{ backgroundColor: "#141a22" }}>
        <div className="flex items-center gap-3 mb-8">
          <div className="w-9 h-9 rounded-[10px] flex items-center justify-center text-base font-bold" style={{ backgroundColor: "#6fce8f", color: "#0b0e13" }}>M</div>
          <div>
            <p className="text-[14px] font-bold tracking-wide" style={{ color: "#eef2f5" }}>MICRO SAAS ENGINE</p>
            <p className="text-[10px] font-mono" style={{ color: "#5b6673" }}>Operator Access</p>
          </div>
        </div>
        {sent ? (
          <p className="text-[12.5px]" style={{ color: "#aab4bd" }}>
            Magic link sent to <span className="text-mint font-mono">{email}</span>. Check your inbox.
          </p>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-[11px] font-mono text-text-label mb-2 uppercase tracking-wider">Email</label>
              <input
                type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                className="w-full px-3 py-2.5 rounded-[8px] border border-border bg-tile text-[12.5px] text-text-primary font-mono outline-none focus:border-mint transition-colors"
              />
            </div>
            {error && <p className="text-[11px] font-mono text-red">{error}</p>}
            <button
              type="submit" disabled={loading}
              className="w-full py-2.5 rounded-[8px] text-[12.5px] font-semibold transition-colors"
              style={{ backgroundColor: loading ? "#2a3340" : "#6fce8f", color: loading ? "#5b6673" : "#0b0e13" }}
            >
              {loading ? "Sending..." : "Send Magic Link"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
