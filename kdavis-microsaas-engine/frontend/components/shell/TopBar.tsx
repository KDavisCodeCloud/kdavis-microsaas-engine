import type { ReactNode } from "react";

export function TopBar({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div
      className="flex items-center justify-between px-6 shrink-0"
      style={{ height: "52px", borderBottom: "1px solid #1c222b", backgroundColor: "#0b0e13" }}
    >
      <h1 className="text-[19px] font-bold" style={{ color: "#eef2f5" }}>{title}</h1>
      <div className="flex items-center gap-3">
        {children}
        <span className="text-[11px] font-mono" style={{ color: "#5b6673" }} suppressHydrationWarning>
          {new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
        </span>
        <div
          className="w-[30px] h-[30px] rounded-full flex items-center justify-center text-[11px] font-bold"
          style={{ backgroundColor: "#6fce8f", color: "#0b0e13" }}
          title="Kelvin — Operator"
        >
          K
        </div>
      </div>
    </div>
  );
}
