import * as XLSX from "xlsx";

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

function buildFilename(entityA: string, entityB: string, ext: string) {
  return `${entityA}_vs_${entityB}_comparison.${ext}`.replace(/\s+/g, "_");
}

function downloadCsv(entityA: string, entityB: string, rows: EntityComparisonRow[]) {
  const escapeCell = (v: string | number) => {
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };

  const header = ["Metric", entityA, entityB].map(escapeCell).join(",");
  const body = rows
    .map((r) => [r.label, r.valueA, r.valueB].map(escapeCell).join(","))
    .join("\n");
  const csv = `${header}\n${body}`;

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = buildFilename(entityA, entityB, "csv");
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function downloadXlsx(entityA: string, entityB: string, rows: EntityComparisonRow[]) {
  const sheetData = [
    ["Metric", entityA, entityB],
    ...rows.map((r) => [r.label, r.valueA, r.valueB]),
  ];

  const worksheet = XLSX.utils.aoa_to_sheet(sheetData);
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, worksheet, "Comparison");
  XLSX.writeFile(workbook, buildFilename(entityA, entityB, "xlsx"));
}

export function EntityComparisonTable({ entityA, entityB, rows }: EntityComparisonTableProps) {
  return (
    <div className="mb-4">
      <table
        className="w-full border-collapse"
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
      <div className="flex items-center gap-4" style={{ paddingTop: 6 }}>
        <button
          type="button"
          onClick={() => downloadCsv(entityA, entityB, rows)}
          className="text-[11px] uppercase tracking-[0.14em] font-medium transition-opacity hover:opacity-70"
          style={{
            fontFamily: "var(--font-body)",
            color: "var(--paper-text-muted)",
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
          }}
        >
          ↓ Export CSV
        </button>
        <button
          type="button"
          onClick={() => downloadXlsx(entityA, entityB, rows)}
          className="text-[11px] uppercase tracking-[0.14em] font-medium transition-opacity hover:opacity-70"
          style={{
            fontFamily: "var(--font-body)",
            color: "var(--paper-text-muted)",
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
          }}
        >
          ↓ Export XLSX
        </button>
      </div>
    </div>
  );
}
