/**
 * LedgerTable — a three-column accounting table.
 *
 * `rule` is not decoration. In a financial statement a SINGLE rule above a row
 * marks the current period against the prior one, and a DOUBLE rule under a row
 * marks a total. Accountants read the rules before they read the numbers, which
 * is why the border weight is a prop rather than a style choice made here.
 * `double` also bolds the row, for the same reason.
 *
 * The component knows nothing about paths, engines or sql_verified: it receives
 * three column headings and a list of pre-formatted rows. composeDocumentBody()
 * in app/page.tsx is the only function aware of where they came from (ED-024).
 */

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
