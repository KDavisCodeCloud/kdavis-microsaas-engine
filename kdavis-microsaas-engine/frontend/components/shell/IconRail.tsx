export function IconRail() {
  return (
    <aside
      className="flex flex-col items-center py-4 gap-4 shrink-0"
      style={{ width: "60px", backgroundColor: "#0e1218", borderRight: "1px solid #1c222b", height: "100vh" }}
    >
      <div
        className="w-8 h-8 rounded-[8px] flex items-center justify-center text-[13px] font-bold"
        style={{ backgroundColor: "#6fce8f", color: "#0b0e13" }}
        title="Micro SaaS Engine"
      >
        M
      </div>
      <div className="w-full h-px" style={{ backgroundColor: "#1c222b" }} />
      <div
        className="w-[34px] h-[34px] rounded-[10px] flex items-center justify-center text-[11px] font-bold"
        style={{ backgroundColor: "#5eead41a", color: "#5eead4" }}
        title="Kelvin — Operator"
      >
        K
      </div>
    </aside>
  );
}
