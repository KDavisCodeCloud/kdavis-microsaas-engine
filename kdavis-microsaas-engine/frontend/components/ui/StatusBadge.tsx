const STATUS_MAP: Record<string, { text: string; bg: string; label?: string }> = {
  active:         { text: "#6fce8f", bg: "#6fce8f22" },
  pass:           { text: "#6fce8f", bg: "#6fce8f22" },
  healthy:        { text: "#6fce8f", bg: "#6fce8f22" },
  complete:       { text: "#6fce8f", bg: "#6fce8f22" },
  closed:         { text: "#6fce8f", bg: "#6fce8f22" },
  READY_TO_BUILD: { text: "#6fce8f", bg: "#6fce8f22", label: "READY" },
  building:       { text: "#7ea6f5", bg: "#5b8def22" },
  pending:        { text: "#7ea6f5", bg: "#5b8def22" },
  running:        { text: "#7ea6f5", bg: "#5b8def22" },
  validated:      { text: "#7ea6f5", bg: "#5b8def22" },
  planning:       { text: "#e8963f", bg: "#e8963f22" },
  flagged:        { text: "#e8963f", bg: "#e8963f22" },
  watch:          { text: "#e8963f", bg: "#e8963f22" },
  yellow:         { text: "#e8963f", bg: "#e8963f22" },
  error:          { text: "#e05d5d", bg: "#e05d5d22" },
  rejected:       { text: "#e05d5d", bg: "#e05d5d22" },
  red:            { text: "#e05d5d", bg: "#e05d5d22" },
  paused:         { text: "#9aa2ab", bg: "#2a2a2a" },
  discovered:     { text: "#9aa2ab", bg: "#2a2a2a" },
  idle:           { text: "#9aa2ab", bg: "#2a2a2a" },
  green:          { text: "#6fce8f", bg: "#6fce8f22" },
};

export function StatusBadge({ status, pill = false }: { status: string; pill?: boolean }) {
  const s = STATUS_MAP[status] ?? { text: "#9aa2ab", bg: "#2a2a2a" };
  return (
    <span
      className="inline-flex items-center font-mono font-semibold"
      style={{ fontSize: "10px", padding: "2px 7px", borderRadius: pill ? "20px" : "5px", backgroundColor: s.bg, color: s.text, whiteSpace: "nowrap" }}
    >
      {s.label ?? status.replace(/_/g, " ").toUpperCase()}
    </span>
  );
}
