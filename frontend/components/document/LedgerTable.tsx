interface LedgerRow {
  label: string;
  value: string | number;
  delta?: string;
  rule?: "none" | "single" | "double";
}

interface LedgerTableProps {
  columns: [string, string, string];
  rows: LedgerRow[];
}

export function LedgerTable({ columns, rows }: LedgerTableProps) {
  return (
    <table
      className="mb-4 w-full border-collapse"
      style={{ fontFamily: "var(--font-body)", fontSize: 12.5 }}
    >
      <thead>
        <tr style={{ color: "var(--paper-text-muted)", fontSize: 10 }}>
          <td style={{ padding: "4px 0" }}>{columns[0]}</td>
          <td style={{ padding: "4px 0", textAlign: "right" }}>{columns[1]}</td>
          <td style={{ padding: "4px 0", textAlign: "right" }}>{columns[2]}</td>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr
            key={i}
            style={{
              color: "var(--paper-text)",
              borderTop:
                row.rule === "double" ? "var(--table-rule-double)" :
                row.rule === "single" ? "var(--table-rule-single)" : "none",
            }}
          >
            <td style={{ padding: "5px 0", fontWeight: row.rule === "double" ? 600 : 400 }}>{row.label}</td>
            <td style={{ padding: "5px 0", textAlign: "right", fontWeight: row.rule === "double" ? 600 : 400 }}>{row.value}</td>
            <td style={{ padding: "5px 0", textAlign: "right", color: row.delta ? "var(--paper-verified)" : "var(--paper-text-muted)" }}>
              {row.delta ?? "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
