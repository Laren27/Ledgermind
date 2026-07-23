interface AuditLogEntry {
  pageNumber: number;
  query: string;
  path: string | null;
  confidenceTier: string;
  latencyMs: number;
}

export function AuditLogTable({ entries, onJump }: { entries: AuditLogEntry[]; onJump: (page: number) => void }) {
  return (
    <table className="w-full border-collapse" style={{ fontFamily: "var(--font-body)", fontSize: 11.5 }}>
      <thead>
        <tr style={{ color: "var(--paper-text-muted)", fontSize: 10 }}>
          <td style={{ padding: "4px 0" }}>#</td>
          <td style={{ padding: "4px 0" }}>QUERY</td>
          <td style={{ padding: "4px 0" }}>PATH</td>
          <td style={{ padding: "4px 0" }}>CONFIDENCE</td>
          <td style={{ padding: "4px 0", textAlign: "right" }}>LATENCY</td>
        </tr>
      </thead>
      <tbody>
        {entries.map((e) => (
          <tr
            key={e.pageNumber}
            onClick={() => onJump(e.pageNumber)}
            className="cursor-pointer"
            style={{ color: "var(--paper-text)", borderTop: "var(--table-rule-single)" }}
          >
            <td style={{ padding: "6px 4px 6px 0", color: "var(--paper-text-muted)" }}>{e.pageNumber} -</td>
            <td style={{ padding: "6px 16px 6px 0", maxWidth: 320 }}>{e.query}</td>
            <td style={{ padding: "6px 0", color: "var(--paper-text-muted)" }}>{e.path ?? "—"}</td>
            <td style={{ padding: "6px 0", color: e.confidenceTier === "high" ? "var(--paper-verified)" : "var(--paper-text-muted)" }}>
              {e.confidenceTier}
            </td>
            <td style={{ padding: "6px 0", textAlign: "right", color: "var(--paper-text-muted)" }}>{e.latencyMs}ms</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
