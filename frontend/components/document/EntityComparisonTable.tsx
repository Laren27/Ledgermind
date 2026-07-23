interface EntityComparisonRow {
  label: string;
  valueA: string | number;
  valueB: string | number;
  winner?: "a" | "b" | null;
}

interface EntityComparisonTableProps {
  entityA: string;
  entityB: string;
  rows: EntityComparisonRow[];
}

export function EntityComparisonTable({ entityA, entityB, rows }: EntityComparisonTableProps) {
  return (
    <table
      className="mb-4 w-full border-collapse"
      style={{ fontFamily: "var(--font-body)", fontSize: 12.5 }}
    >
      <thead>
        <tr style={{ color: "var(--paper-text-muted)", fontSize: 10 }}>
          <td style={{ padding: "4px 0" }}>METRIC</td>
          <td style={{ padding: "4px 0", textAlign: "right" }}>{entityA}</td>
          <td style={{ padding: "4px 0", textAlign: "right" }}>{entityB}</td>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} style={{ color: "var(--paper-text)", borderTop: "var(--table-rule-single)" }}>
            <td style={{ padding: "6px 0" }}>{row.label}</td>
            <td
              style={{
                padding: "6px 0", textAlign: "right",
                fontWeight: row.winner === "a" ? 600 : 400,
                color: row.winner === "a" ? "var(--paper-verified)" : "var(--paper-text)",
              }}
            >
              {row.valueA}{row.winner === "a" && " ✓"}
            </td>
            <td
              style={{
                padding: "6px 0", textAlign: "right",
                fontWeight: row.winner === "b" ? 600 : 400,
                color: row.winner === "b" ? "var(--paper-verified)" : "var(--paper-text)",
              }}
            >
              {row.valueB}{row.winner === "b" && " ✓"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
