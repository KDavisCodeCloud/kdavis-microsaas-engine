import { StatusBadge } from "./StatusBadge";

export function AgentRosterCard({ name, status, lastRun, focus, output, action, error }: {
  name: string; status: string; lastRun?: string | null; focus?: string; output?: string;
  action?: { label: string; onClick: () => void; disabled?: boolean };
  error?: string | null;
}) {
  return (
    <div className="rounded-[10px] p-3.5 flex flex-col" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <p className="text-[13px] font-bold min-w-0 truncate-text" style={{ color: "#eef2f5" }}>{name}</p>
        <StatusBadge status={status} pill />
      </div>
      {lastRun && <p className="text-[11px] font-mono mb-1" style={{ color: "#5b6673" }}>Last run {lastRun}</p>}
      {focus && <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{focus}</p>}
      {output && <p className="text-[11px] font-mono mt-1.5" style={{ color: "#8b96a3" }}>{output}</p>}
      {error && <p className="text-[11px] font-mono mt-1.5" style={{ color: "#e05d5d" }}>{error}</p>}
      {action && (
        <button
          onClick={action.onClick}
          disabled={action.disabled}
          className="mt-3 w-full rounded-[8px] py-1.5 text-[11px] font-mono font-semibold transition-colors"
          style={{
            backgroundColor: action.disabled ? "#1c222b" : "#5a96ff22",
            color: action.disabled ? "#5b6673" : "#7ea6f5",
            border: `1px solid ${action.disabled ? "#1c222b" : "#5a96ff55"}`,
            cursor: action.disabled ? "not-allowed" : "pointer",
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
