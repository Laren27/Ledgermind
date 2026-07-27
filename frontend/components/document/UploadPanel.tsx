"use client";

import { useState } from "react";
import { uploadDocument, type PendingUpload } from "@/lib/api";
import { ArchiveStamp } from "./ArchiveStamp";

const DOC_TYPES = [
  { value: "annual_report", label: "Annual Report" },
  { value: "quarterly_result", label: "Quarterly Result" },
  { value: "drhp", label: "DRHP" },
  { value: "transcript", label: "Earnings Transcript" },
];

const STATUS_LABEL: Record<PendingUpload["status"], string> = {
  pending: "Pending — Awaiting Local Ingestion",
  processing: "Processing…",
  done: "Indexed — Queryable",
  failed: "Failed",
};

const STATUS_COLOR: Record<PendingUpload["status"], string> = {
  pending: "#8B7355",
  processing: "#B58A3C",
  done: "#1E5C3A",
  failed: "#B0453A",
};

// Permanent design decision, not a temporary cap: Archive Intake is a
// registration desk, not an archive browser. It always shows exactly the
// latest 3 registrations — see Upload History for the full record.
const INTAKE_PREVIEW_COUNT = 3;

interface UploadPanelProps {
  pending: PendingUpload[];
  loadingPending: boolean;
  onRefresh: () => void;
  onViewHistory: () => void;
}

export function UploadPanel({ pending, loadingPending, onRefresh, onViewHistory }: UploadPanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [company, setCompany] = useState("");
  const [ticker, setTicker] = useState("");
  const [fiscalYear, setFiscalYear] = useState("");
  const [docType, setDocType] = useState(DOC_TYPES[1].value);
  const [filingDate, setFilingDate] = useState("");
  const [quarter, setQuarter] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);
  const [lastPendingId, setLastPendingId] = useState<string | null>(null);

  const lastStatus = lastPendingId
    ? pending.find((p) => p.id === lastPendingId)?.status ?? "pending"
    : null;

  const resetForm = () => {
    setFile(null);
    setCompany("");
    setTicker("");
    setFiscalYear("");
    setFilingDate("");
    setQuarter("");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !company || !ticker || !fiscalYear || !filingDate) return;

    setSubmitting(true);
    setSubmitError(null);
    setLastResult(null);

    try {
      const result = await uploadDocument({
        file,
        company,
        ticker,
        fiscalYear,
        docType,
        filingDate,
        quarter: quarter || undefined,
      });
      setLastResult(
        `Registered — "${file.name}" is pending ingestion (id: ${result.pending_id.slice(0, 8)}).`
      );
      setLastPendingId(result.pending_id);
      resetForm();
      onRefresh();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  };

  // Reverted to the sizes that actually fit the photographed paper. The
  // previous "~10% scale up" pushed content past the photo's bottom edge —
  // border softened one more step per review, at no height cost.
  const inputStyle: React.CSSProperties = {
    fontFamily: "var(--font-ui, sans-serif)",
    fontSize: 13.5,
    color: "#1A140E",
    background: "rgba(255, 255, 255, 0.4)",
    border: "1px solid rgba(184, 170, 145, 0.55)",
    borderRadius: 3,
    padding: "7px 10px",
    width: "100%",
    boxShadow: "none",
    colorScheme: "light",
  };

  const labelStyle: React.CSSProperties = {
    fontFamily: "var(--font-archival, monospace)",
    fontSize: 10.5,
    fontWeight: 700,
    letterSpacing: "0.13em",
    textTransform: "uppercase",
    color: "#2A221A",
    display: "block",
    marginBottom: 3,
  };

  const historyPreview = pending.slice(0, INTAKE_PREVIEW_COUNT);
  const hasMore = pending.length > INTAKE_PREVIEW_COUNT;

  return (
    // Deliberately no background color here — an opaque fallback previously
    // flattened the photographed paper's lighting. Registration History is
    // permanently capped at 3 rows, so real overflow risk stays low.
    <div className="relative space-y-5 px-[78px] pt-11 pb-9">
      <ArchiveStamp status={lastStatus} />

      <div className="mb-1">
        <h2
          style={{
            fontFamily: "var(--font-editorial, 'Fraunces', Georgia, serif)",
            fontSize: 25,
            color: "#1A140E",
            marginBottom: 4,
          }}
        >
          Archive Intake
        </h2>
        <p
          style={{
            fontFamily: "var(--font-archival, monospace)",
            fontSize: 11,
            color: "#5C4D3C",
            letterSpacing: "0.03em",
          }}
        >
          Register a new corporate filing into the LedgerMind archive.
        </p>
        <div className="mt-3 border-b" style={{ borderColor: "rgba(184, 170, 145, 0.55)" }} />
      </div>

      {/* Archival annotation: darker-than-paper tone, thin low-contrast border,
          no shadow — reads as a conservation note pasted on, not a web card. */}
      <div
        className="px-4 py-2.5 text-[12.5px] leading-snug"
        style={{
          fontFamily: "var(--font-body)",
          color: "#2A221A",
          backgroundColor: "#E6DABE",
          border: "1px solid rgba(140, 122, 100, 0.45)",
          borderRadius: 3,
        }}
      >
        Filings are stored immediately but require a local ingestion step, run by the developer, before becoming queryable.
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label style={labelStyle}>PDF Document</label>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="file:mr-3 file:px-3 file:py-1.5 file:rounded-[3px] file:border file:border-[#9C8C72] file:bg-[#D9CDB5] file:text-[#1A140E] file:text-xs file:font-bold file:uppercase file:tracking-wide file:cursor-pointer file:font-mono hover:file:bg-[#CFC0A2]"
            style={inputStyle}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label style={labelStyle}>Company</label>
            <input
              type="text"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. ETERNAL"
              className="placeholder:opacity-50"
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Ticker</label>
            <input
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="e.g. ETERNAL"
              className="placeholder:opacity-50"
              style={inputStyle}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label style={labelStyle}>Fiscal Year</label>
            <input
              type="text"
              value={fiscalYear}
              onChange={(e) => setFiscalYear(e.target.value)}
              placeholder="e.g. FY27"
              className="placeholder:opacity-50"
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Quarter (optional)</label>
            <input
              type="text"
              value={quarter}
              onChange={(e) => setQuarter(e.target.value)}
              placeholder="e.g. Q1 — blank for annual"
              className="placeholder:opacity-50"
              style={inputStyle}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label style={labelStyle}>Document Type</label>
            <select
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
              style={inputStyle}
            >
              {DOC_TYPES.map((dt) => (
                <option
                  key={dt.value}
                  value={dt.value}
                  style={{ backgroundColor: "#EDE4D3", color: "#1A140E" }}
                >
                  {dt.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Filing Date</label>
            <input
              type="date"
              value={filingDate}
              onChange={(e) => setFilingDate(e.target.value)}
              style={inputStyle}
            />
          </div>
        </div>

        {/* Signature button: emboss shadow kept (near-zero height cost) but
            size reverted — the bigger version was part of what overflowed. */}
        <button
          type="submit"
          disabled={submitting || !file || !company || !ticker || !fiscalYear || !filingDate}
          className="px-6 py-2.5 text-xs font-bold tracking-[0.22em] uppercase transition-all"
          style={{
            fontFamily: "var(--font-archival, monospace)",
            color: submitting ? "#8C8273" : "#1A140E",
            background: "#E3D5B8",
            border: "1px solid #9C8C72",
            borderRadius: 3,
            cursor: submitting ? "default" : "pointer",
            boxShadow:
              "inset 0 1px 0 rgba(255,255,255,0.5), inset 0 -1px 2px rgba(0,0,0,0.12), 0 1px 3px rgba(0,0,0,0.15)",
          }}
          onMouseEnter={(e) => { if (!submitting) e.currentTarget.style.transform = "translateY(-1px)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.transform = "translateY(0)"; }}
        >
          {submitting ? "Registering…" : "Register Filing →"}
        </button>

        {submitError && (
          <div className="text-[12.5px] font-semibold" style={{ color: "#B0453A", fontFamily: "var(--font-body)" }}>
            {submitError}
          </div>
        )}
        {lastResult && (
          <div className="text-[12.5px] font-semibold" style={{ color: "#1E5C3A", fontFamily: "var(--font-body)" }}>
            {lastResult}
          </div>
        )}
      </form>

      {/* Permanent fixed-size preview — always exactly the latest 3, never grows.
          REAL FIX this round: table-layout fixed + explicit column widths via
          colgroup. Previously columns had zero horizontal gutter between them
          and only avoided colliding by accident of font size — any future
          font bump would break it again. This makes the layout robust. */}
      <div className="space-y-3 pt-6 border-t-2" style={{ borderColor: "#8C7A64" }}>
        <div className="flex items-center justify-between">
          <div style={labelStyle}>Registration History</div>
          <button
            type="button"
            onClick={onRefresh}
            className="text-[10.5px] font-bold uppercase tracking-[0.14em] transition-opacity hover:opacity-70"
            style={{ fontFamily: "var(--font-body)", color: "#2A221A", background: "none", border: "none", cursor: "pointer" }}
          >
            {loadingPending ? "Refreshing…" : "↻ Refresh"}
          </button>
        </div>

        {pending.length === 0 ? (
          <div className="text-[12.5px]" style={{ color: "#5C4D3C", fontFamily: "var(--font-body)" }}>
            No filings registered yet.
          </div>
        ) : (
          <>
            <table
              className="w-full border-collapse"
              style={{ fontFamily: "var(--font-body)", fontSize: 12.5, tableLayout: "fixed" }}
            >
              <colgroup>
                <col style={{ width: "26%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "34%" }} />
                <col style={{ width: "22%" }} />
              </colgroup>
              <thead>
                <tr style={{ color: "#5C4D3C", fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>
                  <td style={{ padding: "6px 8px 6px 0", borderBottom: "1px solid rgba(184, 170, 145, 0.55)" }}>COMPANY</td>
                  <td style={{ padding: "6px 8px 6px 0", borderBottom: "1px solid rgba(184, 170, 145, 0.55)" }}>PERIOD</td>
                  <td style={{ padding: "6px 8px 6px 0", borderBottom: "1px solid rgba(184, 170, 145, 0.55)" }}>STATUS</td>
                  <td style={{ padding: "6px 0", textAlign: "right", borderBottom: "1px solid rgba(184, 170, 145, 0.55)" }}>REGISTERED</td>
                </tr>
              </thead>
              <tbody>
                {historyPreview.map((row) => (
                  <tr key={row.id} style={{ borderBottom: "1px solid #E2DACB", color: "#1A140E" }}>
                    <td style={{ padding: "8px 8px 8px 0", fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis" }}>{row.company}</td>
                    <td style={{ padding: "8px 8px 8px 0" }}>
                      {row.fiscal_year}{row.quarter ? ` ${row.quarter}` : ""}
                    </td>
                    <td style={{ padding: "8px 8px 8px 0", color: STATUS_COLOR[row.status], fontWeight: 700 }}>
                      {STATUS_LABEL[row.status]}
                    </td>
                    <td style={{ padding: "8px 0", textAlign: "right", fontSize: 11, color: "#4A3D2C" }}>
                      {new Date(row.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[11.5px]" style={{ color: "#5C4D3C", fontFamily: "var(--font-body)" }}>
              {hasMore ? "Showing latest 3 registrations. " : ""}
              <button
                type="button"
                onClick={onViewHistory}
                className="font-bold underline transition-opacity hover:opacity-70"
                style={{ color: "#2A221A", background: "none", border: "none", cursor: "pointer", padding: 0 }}
              >
                View Full Upload History →
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
