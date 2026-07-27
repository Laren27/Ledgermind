"use client";

import { useState, useEffect, useCallback } from "react";
import {
  uploadDocument,
  fetchPendingUploads,
  type PendingUpload,
} from "@/lib/api";

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
  pending: "var(--ink-metadata, #8B8378)",
  processing: "#B58A3C",
  done: "var(--paper-verified, #2E6B4A)",
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
        `Stored — "${file.name}" is pending ingestion (id: ${result.pending_id.slice(0, 8)}).`
      );
      resetForm();
      loadPending();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  };

  const inputStyle: React.CSSProperties = {
    fontFamily: "var(--font-ui, sans-serif)",
    fontSize: 14,
    color: "var(--ink-primary, #2A241E)",
    background: "rgba(223, 212, 196, 0.20)",
    border: "1px solid var(--ink-divider, #D8CEC1)",
    borderRadius: 4,
    padding: "8px 10px",
    width: "100%",
  };

  const labelStyle: React.CSSProperties = {
    fontFamily: "var(--font-archival, monospace)",
    fontSize: 10.5,
    letterSpacing: "0.14em",
    textTransform: "uppercase",
    color: "var(--ink-metadata, #8B8378)",
    display: "block",
    marginBottom: 4,
  };

  return (
    <div className="space-y-10">
      {/* Honesty banner — always visible, not just after upload */}
      <div
        className="px-4 py-3 text-[12.5px] leading-relaxed"
        style={{
          fontFamily: "var(--font-body)",
          color: "var(--ink-secondary, #5F574D)",
          background: "rgba(181, 138, 60, 0.08)",
          border: "1px solid rgba(181, 138, 60, 0.25)",
          borderRadius: 4,
        }}
      >
        Uploaded filings are stored immediately but are <strong>not queryable right away</strong>.
        Ingestion runs as a local, developer-triggered step — a filing becomes queryable only
        after that step completes, which may take a few minutes and requires the developer's
        machine to be on and running the ingestion script.
      </div>

      {/* Upload form */}
      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label style={labelStyle}>PDF File</label>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
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

        <div className="grid grid-cols-2 gap-4">
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

        <div className="grid grid-cols-2 gap-4">
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
          className="px-4 py-2 text-xs font-semibold tracking-[0.16em] uppercase transition-all"
          style={{
            fontFamily: "var(--font-archival, monospace)",
            color: submitting ? "var(--ink-passive, #B7AEA3)" : "var(--ink-primary, #2A241E)",
            background: "rgba(223, 212, 196, 0.35)",
            border: "1px solid rgba(216, 206, 193, 0.8)",
            borderRadius: 4,
            cursor: submitting ? "default" : "pointer",
          }}
        >
          {submitting ? "Uploading…" : "Execute Upload →"}
        </button>

        {submitError && (
          <div className="text-[12.5px]" style={{ color: "#B0453A", fontFamily: "var(--font-body)" }}>
            {submitError}
          </div>
        )}
        {lastResult && (
          <div className="text-[12.5px]" style={{ color: "var(--paper-verified, #2E6B4A)", fontFamily: "var(--font-body)" }}>
            {lastResult}
          </div>
        )}
      </form>

      {/* Pending uploads status list */}
      <div className="space-y-3 pt-6 border-t" style={{ borderColor: "var(--ink-divider, #D8CEC1)" }}>
        <div className="flex items-center justify-between">
          <div style={labelStyle}>Upload Status</div>
          <button
            type="button"
            onClick={loadPending}
            className="text-[11px] uppercase tracking-[0.12em] transition-opacity hover:opacity-70"
            style={{ fontFamily: "var(--font-body)", color: "var(--ink-metadata, #8B8378)", background: "none", border: "none", cursor: "pointer" }}
          >
            {loadingPending ? "Refreshing…" : "↻ Refresh"}
          </button>
        </div>

        {pending.length === 0 ? (
          <div className="text-[12.5px]" style={{ color: "var(--ink-metadata, #8B8378)", fontFamily: "var(--font-body)" }}>
            No uploads yet.
          </div>
        ) : (
          <table className="w-full border-collapse" style={{ fontFamily: "var(--font-body)", fontSize: 12.5 }}>
            <thead>
              <tr style={{ color: "var(--paper-text-muted, #8B8378)", fontSize: 10 }}>
                <td style={{ padding: "4px 0" }}>COMPANY</td>
                <td style={{ padding: "4px 0" }}>PERIOD</td>
                <td style={{ padding: "4px 0" }}>STATUS</td>
                <td style={{ padding: "4px 0", textAlign: "right" }}>UPLOADED</td>
              </tr>
            </thead>
            <tbody>
              {pending.map((row) => (
                <tr key={row.id} style={{ borderTop: "var(--table-rule-single, 1px solid #D8CEC1)" }}>
                  <td style={{ padding: "6px 0" }}>{row.company}</td>
                  <td style={{ padding: "6px 0" }}>
                    {row.fiscal_year}{row.quarter ? ` ${row.quarter}` : ""}
                  </td>
                  <td style={{ padding: "6px 0", color: STATUS_COLOR[row.status], fontWeight: 600 }}>
                    {STATUS_LABEL[row.status]}
                    {row.status === "failed" && row.error_message && (
                      <div style={{ fontWeight: 400, fontSize: 11, opacity: 0.8 }}>
                        {row.error_message.slice(0, 120)}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: "6px 0", textAlign: "right", fontSize: 11, opacity: 0.75 }}>
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
