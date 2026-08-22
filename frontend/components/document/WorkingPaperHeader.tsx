import React from "react";

interface WorkingPaperHeaderProps {
  // F14: a LIST, and the three states are genuinely different.
  //   undefined/null -> no query has run yet (pending sheet)
  //   []             -> a query ran and resolved NO issuer, so retrieval was
  //                     UNFILTERED across the tenant
  //   [a, b, ...]    -> the issuers the router actually resolved
  companies?: string[] | null;
  fiscalYear?: string | null;
  quarter?: string | null;
  financialType?: string | null;
  wpRef?: string;
  revision?: number;
  preparer?: string;
}

export function WorkingPaperHeader({
  companies,
  fiscalYear,
  quarter,
  financialType,
  wpRef = "WP-PENDING",
  revision = 1,
  preparer = "analyst",
}: WorkingPaperHeaderProps) {
  // The previous fallback stood in for a falsy scalar company. After F14 that
  // field stopped existing, so the fallback became the label on EVERY working
  // paper -- a corpus name asserted even for answers that named one issuer.
  // It is not a safe default: an empty issuer list means the search ran
  // unfiltered, which is a fact about SCOPE and must read as one, never as the
  // name of a thing in the archive.
  const entityName =
    companies == null
      ? "NO QUERY EXECUTED"
      : companies.length === 0
      ? "NO ISSUER RESOLVED — SEARCH UNFILTERED"
      : companies.length === 1
      ? `${companies[0].toUpperCase()} LIMITED`
      // Plural: no "LIMITED" suffix. It is one legal entity's suffix and
      // appending it to a set of issuers would be a claim about none of them.
      : companies.map((c) => c.toUpperCase()).join(" · ");
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
