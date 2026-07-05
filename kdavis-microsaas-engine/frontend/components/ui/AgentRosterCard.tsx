import { StatusBadge } from "./StatusBadge";

export function AgentRosterCard({ name, status, lastRun, focus, output }: {
  name: string; status: string; lastRun?: string | null; focus?: string; output?: string;
}) {
  return (
    <div className="rounded-[10px] p-3.5" style={{ backgroundColor: "#10151b", border: "1px solid #1c222b" }}>
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <p className="text-[13px] font-bold min-w-0 truncate-text" style={{ color: "#eef2f5" }}>{name}</p>
        <StatusBadge status={status} pill />
      </div>
      {lastRun && <p className="text-[11px] font-mono mb-1" style={{ color: "#5b6673" }}>Last run {lastRun}</p>}
      {focus && <p className="text-[11px] font-mono" style={{ color: "#5b6673" }}>{focus}</p>}
      {output && <p className="text-[11px] font-mono mt-1.5" style={{ color: "#8b96a3" }}>{output}</p>}
    </div>
  );
}
