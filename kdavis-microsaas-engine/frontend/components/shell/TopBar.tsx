"use client";

import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export function TopBar({ title, children }: { title: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const supabase = createClient();

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  }, [supabase]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div
      className="flex items-center justify-between px-6 shrink-0"
      style={{ height: "52px", borderBottom: "1px solid #1c222b", backgroundColor: "#0b0e13" }}
    >
      <h1 className="text-[19px] font-bold" style={{ color: "#eef2f5" }}>{title}</h1>
      <div className="flex items-center gap-3">
        {children}
        <span className="text-[11px] font-mono" style={{ color: "#5b6673" }} suppressHydrationWarning>
          {new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/Phoenix" })}
        </span>
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setOpen((v) => !v)}
            className="w-[30px] h-[30px] rounded-full flex items-center justify-center text-[11px] font-bold"
            style={{ backgroundColor: "#6fce8f", color: "#0b0e13" }}
            title={email ?? "Operator"}
          >
            K
          </button>
          {open && (
            <div
              className="absolute right-0 rounded-[10px] overflow-hidden z-50"
              style={{ width: "190px", top: "38px", backgroundColor: "#141a22", border: "1px solid #1c222b", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}
            >
              {email && (
                <div className="px-3 py-2.5" style={{ borderBottom: "1px solid #1c222b" }}>
                  <p className="text-[11px] font-mono truncate-text" style={{ color: "#8b96a3" }}>{email}</p>
                </div>
              )}
              <Link
                href="/profile"
                onClick={() => setOpen(false)}
                className="block px-3 py-2.5 text-[12.5px] transition-colors hover:bg-[#1c222b]"
                style={{ color: "#aab4bd", textDecoration: "none" }}
              >
                Profile
              </Link>
              <Link
                href="/settings"
                onClick={() => setOpen(false)}
                className="block px-3 py-2.5 text-[12.5px] transition-colors hover:bg-[#1c222b]"
                style={{ color: "#aab4bd", textDecoration: "none" }}
              >
                Settings
              </Link>
              <button
                onClick={handleSignOut}
                className="w-full text-left px-3 py-2.5 text-[12.5px] transition-colors hover:bg-[#1c222b]"
                style={{ color: "#e05d5d", borderTop: "1px solid #1c222b" }}
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
