"use client";

import { useState, useEffect, useCallback } from "react";
import {
  uploadDocument,
  fetchPendingUploads,
  type PendingUpload,
} from "@/lib/api";
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

const HISTORY_PREVIEW_COUNT = 4;

export function UploadPanel() {
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

  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [loadingPending, setLoadingPending] = useState(false);

  const loadPending = useCallback(async () => {
    setLoadingPending(true);
    try {
      const rows = await fetchPendingUploads();
      setPending(rows);
    } catch {
      // silent — this list is a convenience view, not critical path
    } finally {
      setLoadingPending(false);
    }
  }, []);

  useEffect(() => {
    loadPending();
  }, [loadPending]);

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
      loadPending();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  };

  // Softened further: lighter border, no inset shadow, lower-contrast so the
  // paper (not the inputs) stays visually dominant per design review notes.
  const inputStyle: React.CSSProperties = {
    fontFamily: "var(--font-ui, sans-serif)",
    fontSize: 13.5,
    color: "#1A140E",
    background: "rgba(255, 255, 255, 0.4)",
    border: "1px solid #C9BFA9",
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

  const historyPreview = pending.slice(0, HISTORY_PREVIEW_COUNT);
  const hiddenCount = pending.length - historyPreview.length;

  return (
    <div className="relative space-y-5 px-10 pt-8 pb-8">
      <ArchiveStamp status={lastStatus} />

      <div className="mb-1">
        <h2
          style={{
            fontFamily: "var(--font-editorial, 'Fraunces', Georgia, serif)",
            fontSize: 24,
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
        <div className="mt-3 border-b" style={{ borderColor: "#C9BFA9" }} />
      </div>

      {/* Notice — trimmed to one line; full explanation still lives in the
          status labels below (Pending/Processing/Indexed), so nothing is lost. */}
      <div
        className="px-4 py-2.5 text-[12px] leading-snug"
        style={{
          fontFamily: "var(--font-body)",
          color: "#2A221A",
          background: "rgba(255, 255, 255, 0.45)",
          border: "1px solid #C9BFA9",
          borderRadius: 4,
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

        <button
          type="submit"
          disabled={submitting || !file || !company || !ticker || !fiscalYear || !filingDate}
          className="px-6 py-2.5 text-xs font-bold tracking-[0.2em] uppercase transition-all shadow-sm"
          style={{
            fontFamily: "var(--font-archival, monospace)",
            color: submitting ? "#8C8273" : "#1A140E",
            background: "#D4C5A9",
            border: "1px solid #9C8C72",
            borderRadius: 3,
            cursor: submitting ? "default" : "pointer",
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

      {/* Registration history: capped preview, not an unbounded growing table —
          Audit Trail is the honest full record; this is just a quick glance. */}
      <div className="space-y-3 pt-5 border-t-2" style={{ borderColor: "#8C7A64" }}>
        <div className="flex items-center justify-between">
          <div style={labelStyle}>Registration History</div>
          <button
            type="button"
            onClick={loadPending}
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
            <table className="w-full border-collapse" style={{ fontFamily: "var(--font-body)", fontSize: 12.5 }}>
              <thead>
                <tr style={{ color: "#5C4D3C", fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>
                  <td style={{ padding: "6px 0", borderBottom: "1px solid #C9BFA9" }}>COMPANY</td>
                  <td style={{ padding: "6px 0", borderBottom: "1px solid #C9BFA9" }}>PERIOD</td>
                  <td style={{ padding: "6px 0", borderBottom: "1px solid #C9BFA9" }}>STATUS</td>
                  <td style={{ padding: "6px 0", textAlign: "right", borderBottom: "1px solid #C9BFA9" }}>REGISTERED</td>
                </tr>
              </thead>
              <tbody>
                {historyPreview.map((row) => (
                  <tr key={row.id} style={{ borderBottom: "1px solid #D4C7B5", color: "#1A140E" }}>
                    <td style={{ padding: "8px 0", fontWeight: 700 }}>{row.company}</td>
                    <td style={{ padding: "8px 0" }}>
                      {row.fiscal_year}{row.quarter ? ` ${row.quarter}` : ""}
                    </td>
                    <td style={{ padding: "8px 0", color: STATUS_COLOR[row.status], fontWeight: 700 }}>
                      {STATUS_LABEL[row.status]}
                      {row.status === "failed" && row.error_message && (
                        <div style={{ fontWeight: 400, fontSize: 11, opacity: 0.85, color: "#B0453A" }}>
                          {row.error_message.slice(0, 120)}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "8px 0", textAlign: "right", fontSize: 11, color: "#4A3D2C" }}>
                      {new Date(row.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {hiddenCount > 0 && (
              <div className="text-[11.5px]" style={{ color: "#5C4D3C", fontFamily: "var(--font-body)" }}>
                +{hiddenCount} earlier {hiddenCount === 1 ? "filing" : "filings"} — see Audit Trail for the full record.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
