interface WorkingPaperHeaderProps {
  company: string | null;
  fiscalYear: string | null;
  quarter: string | null;
  financialType: string | null;
  wpRef: string;
  revision: number;
  preparer: string;
  reviewer?: string;
}

export function WorkingPaperHeader({
  company, fiscalYear, quarter, financialType, wpRef, revision, preparer, reviewer,
}: WorkingPaperHeaderProps) {
  const periodLabel = [quarter, fiscalYear].filter(Boolean).join(" ");

  return (
    <div className="mb-6 flex items-start justify-between">
      <div>
        <div style={{ fontFamily: "var(--font-body)", fontSize: 12, fontWeight: 600, letterSpacing: "0.02em", color: "var(--paper-text)" }}>
          {company ?? "UNKNOWN ENTITY"} LIMITED
        </div>
        <div style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "var(--paper-text-muted)", marginTop: 2 }}>
          {financialType ? financialType[0].toUpperCase() + financialType.slice(1) : "Consolidated"} Financial Statements
        </div>
        <div style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "var(--paper-text-muted)" }}>
          {periodLabel || "—"}
        </div>
      </div>

      <div style={{ textAlign: "right", fontFamily: "var(--font-body)", fontSize: 10.5, color: "var(--paper-text-muted)", lineHeight: 1.7 }}>
        <div style={{ fontWeight: 600, color: "var(--paper-text)", marginBottom: 2 }}>WORKING PAPER</div>
        <div>WP REF: {wpRef}</div>
        <div>REVISION: {String(revision).padStart(2, "0")}</div>
        <div>PREPARER: {preparer}</div>
        {reviewer && <div>REVIEWER: {reviewer}</div>}
      </div>
    </div>
  );
}
