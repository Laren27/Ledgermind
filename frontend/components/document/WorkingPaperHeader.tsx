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
  const entityName = company ? `${company.toUpperCase()} LIMITED` : "UNKNOWN ENTITY LIMITED";
  const statementType = financialType
    ? `${financialType.charAt(0).toUpperCase() + financialType.slice(1)} Financial Statements`
    : "Consolidated Financial Statements";
  const periodString = [quarter, fiscalYear].filter(Boolean).join(" ");

  return (
    <div className="flex items-start justify-between pb-6 mb-8 border-b" style={{ borderColor: "var(--ink-divider, #D8CEC1)" }}>
      {/* Left Column: Entity & Statement Metadata */}
      <div className="space-y-1">
        <div
          className="font-semibold uppercase tracking-[0.12em]"
          style={{
            fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)",
            fontSize: "var(--font-size-entity, 14px)",
            color: "var(--ink-metadata, #8B8378)",
          }}
        >
          {entityName}
        </div>
        <div
          className="font-normal"
          style={{
            fontFamily: "var(--font-ui, 'IBM Plex Sans', sans-serif)",
            fontSize: "var(--font-size-metadata, 13px)",
            color: "var(--ink-secondary, #5F574D)",
          }}
        >
          {statementType} {periodString && `— ${periodString}`}
        </div>
      </div>

      {/* Right Column: Archival Working Paper Reference */}
      <div
        className="text-right space-y-0.5 uppercase tracking-wider"
        style={{
          fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)",
          fontSize: "11px",
          color: "var(--ink-metadata, #8B8378)",
        }}
      >
        <div className="font-semibold text-xs tracking-widest" style={{ color: "var(--ink-primary, #2A241E)" }}>Working Paper</div>
        <div>REF: {wpRef}</div>
        <div>REV: {String(revision).padStart(2, "0")}</div>
        <div className="lowercase tracking-normal opacity-85">preparer: {preparer}</div>
      </div>
    </div>
  );
}
