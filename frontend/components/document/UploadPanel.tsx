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

  // Sharpened: Translucent parchment fill so inputs blend into paper, crisp archival border (#A89880)
  const inputStyle: React.CSSProperties = {
    fontFamily: "var(--font-ui, sans-serif)",
    fontSize: 14,
    color: "#1A140E",
    background: "rgba(255, 255, 255, 0.5)",
    border: "1px solid #A89880",
    borderRadius: 3,
    padding: "9px 12px",
    width: "100%",
    boxShadow: "inset 0 1px 2px rgba(0,0,0,0.06)",
    colorScheme: "light",
  };

  // Darkened: Deep archival ink (#2A221A) with bold weight to pop legibly against folder shadows
  const labelStyle: React.CSSProperties = {
    fontFamily: "var(--font-archival, monospace)",
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.15em",
    textTransform: "uppercase",
    color: "#2A221A",
    display: "block",
    marginBottom: 6,
  };

  return (
    <div className="relative space-y-8">
      <ArchiveStamp status={lastStatus} />

      {/* Honesty banner — crisp dark text over structured translucent parchment box */}
      <div
        className="px-4 py-3.5 text-[12.5px] leading-relaxed shadow-sm"
        style={{
          fontFamily: "var(--font-body)",
          color: "#2A221A",
          background: "rgba(255, 255, 255, 0.6)",
          border: "1px solid #A89880",
          borderRadius: 4,
        }}
      >
        Uploaded filings are stored immediately but are <strong>not queryable right away</strong>.
        Ingestion runs as a local, developer-triggered step — a filing becomes queryable only
        after that step completes, which may take a few minutes and requires the developer&apos;s
        machine to be on and running the ingestion script.
      </div>

      {/* Upload form */}
      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label style={labelStyle}>PDF Document</label>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            style={inputStyle}
          />
        </div>

        <div className="grid grid-cols-2 gap-5">
          <div>
            <label style={labelStyle}>Company</label>
            <input
              type="text"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. ETERNAL"
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
              style={inputStyle}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-5">
          <div>
            <label style={labelStyle}>Fiscal Year</label>
            <input
              type="text"
              value={fiscalYear}
              onChange={(e) => setFiscalYear(e.target.value)}
              placeholder="e.g. FY27"
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Quarter (optional)</label>
            <input
              type="text"
              value={quarter}
              onChange={(e) => setQuarter(e.target.value)}
              placeholder="e.g. Q1 — leave blank for annual"
              style={inputStyle}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-5">
          <div>
            <label style={labelStyle}>Document Type</label>
            <select
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
              style={inputStyle}
            >
              {DOC_TYPES.map((dt) => (
                <option key={dt.value} value={dt.value}>{dt.label}</option>
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
          className="px-6 py-3 text-xs font-bold tracking-[0.2em] uppercase transition-all shadow-sm"
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
          <div className="text-[13px] font-semibold" style={{ color: "#B0453A", fontFamily: "var(--font-body)" }}>
            {submitError}
          </div>
        )}
        {lastResult && (
          <div className="text-[13px] font-semibold" style={{ color: "#1E5C3A", fontFamily: "var(--font-body)" }}>
            {lastResult}
          </div>
        )}
      </form>

      {/* Registration history: Sharpened 2px top divider (#8C7A64) and rich ink typography */}
      <div className="space-y-4 pt-8 border-t-2" style={{ borderColor: "#8C7A64" }}>
        <div className="flex items-center justify-between">
          <div style={labelStyle}>Registration History</div>
          <button
            type="button"
            onClick={loadPending}
            className="text-[11px] font-bold uppercase tracking-[0.14em] transition-opacity hover:opacity-70"
            style={{ fontFamily: "var(--font-body)", color: "#2A221A", background: "none", border: "none", cursor: "pointer" }}
          >
            {loadingPending ? "Refreshing…" : "↻ Refresh"}
          </button>
        </div>

        {pending.length === 0 ? (
          <div className="text-[13px]" style={{ color: "#5C4D3C", fontFamily: "var(--font-body)" }}>
            No filings registered yet.
          </div>
        ) : (
          <table className="w-full border-collapse" style={{ fontFamily: "var(--font-body)", fontSize: 13 }}>
            <thead>
              <tr style={{ color: "#5C4D3C", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.1em" }}>
                <td style={{ padding: "8px 0", borderBottom: "1px solid #A89880" }}>COMPANY</td>
                <td style={{ padding: "8px 0", borderBottom: "1px solid #A89880" }}>PERIOD</td>
                <td style={{ padding: "8px 0", borderBottom: "1px solid #A89880" }}>STATUS</td>
                <td style={{ padding: "8px 0", textAlign: "right", borderBottom: "1px solid #A89880" }}>REGISTERED</td>
              </tr>
            </thead>
            <tbody>
              {pending.map((row) => (
                <tr key={row.id} style={{ borderBottom: "1px solid #D4C7B5", color: "#1A140E" }}>
                  <td style={{ padding: "10px 0", fontWeight: 700 }}>{row.company}</td>
                  <td style={{ padding: "10px 0" }}>
                    {row.fiscal_year}{row.quarter ? ` ${row.quarter}` : ""}
                  </td>
                  <td style={{ padding: "10px 0", color: STATUS_COLOR[row.status], fontWeight: 700 }}>
                    {STATUS_LABEL[row.status]}
                    {row.status === "failed" && row.error_message && (
                      <div style={{ fontWeight: 400, fontSize: 11.5, opacity: 0.85, color: "#B0453A" }}>
                        {row.error_message.slice(0, 120)}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: "10px 0", textAlign: "right", fontSize: 11.5, color: "#4A3D2C" }}>
                    {new Date(row.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}