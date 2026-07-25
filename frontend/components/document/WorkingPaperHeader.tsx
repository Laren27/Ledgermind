import React from "react";

interface WorkingPaperHeaderProps {
  company?: string | null;
  fiscalYear?: string | null;
  quarter?: string | null;
  financialType?: string | null;
  wpRef?: string;
  revision?: number;
  preparer?: string;
}

export function WorkingPaperHeader({
  company,
  fiscalYear,
  quarter,
  financialType,
  wpRef = "WP-PENDING",
  revision = 1,
  preparer = "analyst",
}: WorkingPaperHeaderProps) {
  // 💡 Replaced "UNKNOWN ENTITY LIMITED" with "GENERAL CORPUS ARCHIVE" for clean draft state
  const entityName = company ? `${company.toUpperCase()} LIMITED` : "GENERAL CORPUS ARCHIVE";
  const statementType = financialType
    ? `${financialType.charAt(0).toUpperCase() + financialType.slice(1)} Financial Statements`
    : "Consolidated Financial Statements";
  const periodString = [quarter, fiscalYear].filter(Boolean).join(" ");

  return (
    <div className="flex items-start justify-between pb-5 mb-8 border-b" style={{ borderColor: "var(--ink-divider, #D8CEC1)" }}>
      {/* Tight Left Baseline Grid */}
      <div className="space-y-0.5">
        <div
          className="font-semibold uppercase tracking-[0.12em]"
          style={{
            fontFamily: "var(--font-archival, monospace)",
            fontSize: "var(--font-size-entity, 14px)",
            color: "var(--ink-metadata, #8B8378)",
          }}
        >
          {entityName}
        </div>
        <div
          className="font-normal tracking-wide"
          style={{
            fontFamily: "var(--font-ui, sans-serif)",
            fontSize: "var(--font-size-metadata, 13px)",
            color: "var(--ink-secondary, #5F574D)",
          }}
        >
          {statementType} {periodString && `— ${periodString}`}
        </div>
      </div>

      {/* Ultra-Small Engineering Drawing Specification */}
      <div
        className="text-right space-y-0.5 uppercase tracking-[0.14em] select-none"
        style={{
          fontFamily: "var(--font-archival, monospace)",
          fontSize: "10.5px",
          color: "var(--ink-passive, #B7AEA3)",
        }}
      >
        <div className="font-semibold text-[11px] tracking-[0.18em]" style={{ color: "var(--ink-primary, #2A241E)" }}>Working Paper</div>
        <div>REF: <span style={{ color: "var(--ink-metadata)" }}>{wpRef}</span></div>
        <div>REV: <span style={{ color: "var(--ink-metadata)" }}>{String(revision).padStart(2, "0")}</span></div>
        <div>Prepared By: <span className="lowercase font-medium" style={{ color: "var(--ink-secondary)" }}>{preparer}</span></div>
        <div>Generated: <span style={{ color: "var(--ink-metadata)" }}>2026-07-25</span></div>
      </div>
    </div>
  );
}
