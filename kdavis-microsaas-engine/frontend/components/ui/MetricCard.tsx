interface MetricCardProps {
  label: string;
  value: string;
  subtext?: string;
  accent?: string;
}

export function MetricCard({ label, value, subtext, accent = "#5eead4" }: MetricCardProps) {
  return (
    <div
      className="rounded-[14px] p-4"
      style={{ background: `linear-gradient(150deg, ${accent}24 0%, #141a22 75%)`, border: "1px solid #1c222b", padding: "16px 18px" }}
    >
      <p className="text-[11px] font-mono uppercase tracking-wider mb-2" style={{ color: "#5b6673" }}>{label}</p>
      <p className="text-[24px] font-extrabold leading-none mb-1.5" style={{ color: accent }}>{value}</p>
      {subtext && <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{subtext}</p>}
    </div>
  );
}
