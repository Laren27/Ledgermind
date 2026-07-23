interface SidebarProps {
  userRole: string;
  tenantId: string;
  activeView: "workbench" | "peer" | "audit";
  onViewChange: (view: "workbench" | "peer" | "audit") => void;
  onSignOut: () => void;
  indexedFilings: { company: string; period: string; active?: boolean }[];
}

export function Sidebar({
  userRole, tenantId, activeView, onViewChange, onSignOut, indexedFilings,
}: SidebarProps) {
  const navItem = (
    key: "workbench" | "peer" | "audit",
    label: string,
  ) => (
    <button
      onClick={() => onViewChange(key)}
      className="w-full text-left transition-colors"
      style={{
        fontFamily: "var(--font-body)",
        fontSize: 12,
        padding: "7px 10px",
        borderRadius: 4,
        color: activeView === key ? "#F1E9DC" : "#8A7E6F",
        background: activeView === key ? "rgba(201,168,118,0.09)" : "transparent",
        borderLeft: activeView === key ? "2px solid #C9A876" : "2px solid transparent",
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      className="flex w-[220px] flex-shrink-0 flex-col gap-6 p-4"
      style={{ background: "#1B1611", borderRight: "0.5px solid rgba(210,180,140,0.1)" }}
    >
      <div className="flex items-center gap-2">
        <div
          className="flex h-6 w-6 items-center justify-center rounded"
          style={{ background: "#241C15", border: "0.5px solid rgba(210,180,140,0.25)", fontFamily: "var(--font-document-title)", fontSize: 13, color: "#C9A876" }}
        >
          L
        </div>
        <span style={{ fontFamily: "var(--font-document-title)", fontSize: 14, color: "#F1E9DC" }}>LedgerMind</span>
      </div>

      <div
        className="flex items-center gap-2 rounded-lg p-2"
        style={{ background: "rgba(210,180,140,0.04)" }}
      >
        <div
          className="flex h-6 w-6 items-center justify-center rounded-full"
          style={{ background: "#2A2119", fontSize: 10, color: "#C9A876" }}
        >
          {userRole[0].toUpperCase()}
        </div>
        <div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#EDE5D8" }}>{userRole}</div>
          <div style={{ fontFamily: "var(--font-body)", fontSize: 9.5, color: "#6B6053" }}>{tenantId.slice(0, 8)}</div>
        </div>
      </div>

      <div>
        <div style={{ fontFamily: "var(--font-body)", fontSize: 9.5, textTransform: "uppercase", letterSpacing: "0.08em", color: "#6B6053", marginBottom: 8 }}>
          Workspace
        </div>
        <div className="flex flex-col gap-1">
          {navItem("workbench", "Query workbench")}
          {navItem("peer", "Peer comparison")}
          {navItem("audit", "Audit trail")}
        </div>
      </div>

      <div>
        <div style={{ fontFamily: "var(--font-body)", fontSize: 9.5, textTransform: "uppercase", letterSpacing: "0.08em", color: "#6B6053", marginBottom: 8 }}>
          Indexed filings
        </div>
        <div className="flex flex-col gap-1">
          {indexedFilings.map((f, i) => (
            <div
              key={i}
              className="flex justify-between rounded px-2 py-1.5"
              style={{ background: f.active ? "rgba(255,255,255,0.02)" : "transparent" }}
            >
              <span style={{ fontFamily: "var(--font-body)", fontSize: 12, color: f.active ? "#EDE5D8" : "#8A7E6F" }}>{f.company}</span>
              <span style={{ fontFamily: "var(--font-body)", fontSize: 9.5, color: "#6B6053" }}>{f.period}</span>
            </div>
          ))}
        </div>
      </div>

      <button
        onClick={onSignOut}
        className="mt-auto text-left underline"
        style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#6B6053" }}
      >
        Sign out
      </button>
    </div>
  );
}
