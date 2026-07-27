"use client";

import React, { useMemo, useState } from "react";
import type { PendingUpload } from "@/lib/api";

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

const STATUS_FILTERS: Array<PendingUpload["status"] | "all"> = ["all", "pending", "processing", "done", "failed"];

export function UploadHistoryTable({
  uploads,
  loading,
  onRefresh,
  onBack,
}: {
  uploads: PendingUpload[];
  loading?: boolean;
  onRefresh: () => void;
  onBack: () => void;
}) {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<PendingUpload["status"] | "all">("all");

  const filtered = useMemo(() => {
    return uploads.filter((u) => {
      if (statusFilter !== "all" && u.status !== statusFilter) return false;
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        return u.company.toLowerCase().includes(q) || u.ticker.toLowerCase().includes(q);
      }
      return true;
    });
  }, [uploads, search, statusFilter]);

  return (
    <div className="w-full py-4">
      <button
        type="button"
        onClick={onBack}
        className="mb-4 text-xs font-semibold tracking-wide transition-opacity hover:opacity-70"
        style={{ fontFamily: "var(--font-archival, monospace)", color: "var(--ink-secondary, #5F574D)", background: "none", border: "none", cursor: "pointer" }}
      >
        ← Back to Intake
      </button>

      <div className="mb-4 flex items-baseline justify-between border-b pb-3" style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))" }}>
        <h3 className="font-semibold tracking-tight text-lg" style={{ fontFamily: "var(--font-editorial, Georgia, serif)", color: "var(--ink-primary, #2A241E)" }}>
          Upload History
        </h3>
        <button
          type="button"
          onClick={onRefresh}
          className="font-mono text-xs tracking-widest uppercase transition-opacity hover:opacity-70"
          style={{ color: "var(--ink-metadata, #8B8378)", background: "none", border: "none", cursor: "pointer" }}
        >
          {loading ? "Refreshing…" : "↻ Refresh"}
        </button>
      </div>

      <div className="mb-5 flex items-center gap-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search company or ticker…"
          className="flex-1 px-3 py-2 text-[13px] rounded-sm placeholder:opacity-50"
          style={{
            fontFamily: "var(--font-ui, sans-serif)",
            border: "1px solid var(--ink-divider, rgba(215, 206, 195, 0.55))",
            background: "transparent",
            color: "var(--ink-primary, #2A241E)",
          }}
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as PendingUpload["status"] | "all")}
          className="px-3 py-2 text-[13px] rounded-sm uppercase tracking-wide"
          style={{
            fontFamily: "var(--font-archival, monospace)",
            border: "1px solid var(--ink-divider, rgba(215, 206, 195, 0.55))",
            background: "transparent",
            color: "var(--ink-primary, #2A241E)",
          }}
        >
          {STATUS_FILTERS.map((s) => (
            <option key={s} value={s} style={{ backgroundColor: "#EDE4D3" }}>
              {s === "all" ? "All Statuses" : s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
      </div>

      <table className="w-full border-collapse text-left font-mono" style={{ fontSize: "12px" }}>
        <thead>
          <tr className="border-b text-[10.5px] uppercase tracking-[0.16em]" style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))", color: "var(--ink-metadata, #8B8378)" }}>
            <th className="py-3 pr-4 font-semibold">Company</th>
            <th className="py-3 pr-4 font-semibold">Period</th>
            <th className="py-3 pr-4 font-semibold">Type</th>
            <th className="py-3 pr-6 font-semibold">Status</th>
            <th className="py-3 pr-4 font-semibold text-right">Registered</th>
            <th className="py-3 text-right font-semibold">Last Updated</th>
          </tr>
        </thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr>
              <td colSpan={6} className="py-12 text-center font-mono text-xs italic" style={{ color: "var(--ink-passive, #B7AEA3)" }}>
                {uploads.length === 0 ? "No filings registered yet." : "No uploads match this filter."}
              </td>
            </tr>
          ) : (
            filtered.map((row) => (
              <tr
                key={row.id}
                className="border-b"
                style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))", color: "var(--ink-primary, #2A241E)" }}
              >
                <td className="py-3.5 pr-4 font-sans font-semibold text-[13px]">{row.company}</td>
                <td className="py-3.5 pr-4 font-sans text-[13px]">
                  {row.fiscal_year}{row.quarter ? ` ${row.quarter}` : ""}
                </td>
                <td className="py-3.5 pr-4 uppercase tracking-wider text-[11px]" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                  {row.doc_type.replace(/_/g, " ")}
                </td>
                <td className="py-3.5 pr-6" style={{ color: STATUS_COLOR[row.status], fontWeight: 600 }}>
                  {STATUS_LABEL[row.status]}
                  {row.status === "failed" && row.error_message && (
                    <div
                      className="font-sans normal-case tracking-normal mt-0.5"
                      style={{ fontWeight: 400, fontSize: 11, opacity: 0.85, color: "#B0453A" }}
                    >
                      {row.error_message.slice(0, 160)}
                    </div>
                  )}
                </td>
                <td className="py-3.5 pr-4 text-right tabular-nums" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                  {new Date(row.created_at).toLocaleString()}
                </td>
                <td className="py-3.5 text-right tabular-nums" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                  {new Date(row.updated_at).toLocaleString()}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}