import type { ReactNode, CSSProperties } from "react";

export function SectionCard({ title, children, style }: { title: string; children: ReactNode; style?: CSSProperties }) {
  return (
    <div className="rounded-[14px]" style={{ backgroundColor: "#141a22", border: "1px solid #1c222b", padding: "20px", ...style }}>
      <p className="text-[13px] font-bold mb-3.5" style={{ color: "#c7cfd6" }}>{title}</p>
      {children}
    </div>
  );
}
