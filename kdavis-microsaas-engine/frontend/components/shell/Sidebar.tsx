"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "@/lib/types";

export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav
      className="flex flex-col py-4 shrink-0"
      style={{ width: "196px", backgroundColor: "#0e1218", borderRight: "1px solid #1c222b", height: "100vh" }}
    >
      <div className="px-3 mb-5">
        <p className="text-[14px] font-bold tracking-wide" style={{ color: "#eef2f5" }}>
          MICRO SAAS ENGINE
        </p>
        <p className="text-[10px] font-mono" style={{ color: "#5b6673" }}>
          Research Factory v1
        </p>
      </div>

      <ul className="flex flex-col gap-0.5 px-2">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.path || pathname.startsWith(item.path + "/");
          return (
            <li key={item.id}>
              <Link
                href={item.path}
                className="flex items-center gap-2 rounded-[8px] transition-colors"
                style={{
                  padding: "9px 10px",
                  backgroundColor: active ? "#5eead41a" : "transparent",
                  color: active ? "#5eead4" : "#8b96a3",
                  fontWeight: active ? 600 : 400,
                  fontSize: "12.5px",
                  textDecoration: "none",
                }}
              >
                <span
                  className="shrink-0"
                  style={{
                    width: 12, height: 12,
                    border: `1.5px solid ${active ? "#5eead4" : "#5b6673"}`,
                    borderRadius: "2px",
                    display: "inline-block",
                  }}
                />
                <span className="truncate-text">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
