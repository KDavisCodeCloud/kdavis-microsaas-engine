export function ProgressBar({ value, accent = "#5eead4", height = 5 }: { value: number; accent?: string; height?: number }) {
  return (
    <div className="w-full rounded-full overflow-hidden" style={{ height, backgroundColor: "#1c222b" }}>
      <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(100, Math.max(0, value))}%`, backgroundColor: accent }} />
    </div>
  );
}
