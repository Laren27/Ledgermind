"use client";

import React from "react";

export interface AuditLogEntry {
  pageNumber: number;
  query: string;
  path: string | null;
  // OPTIONAL: the backend OMITS confidence_tier on a Prompt Shield block,
  // because graph.py sends a block straight to audit_writer and
  // confidence_node never runs. Absent means NOT SCORED -- it does not mean
  // low, and it must not render as a tier.
  confidenceTier?: string;
  // Admin-only field. Absent for viewer/analyst per role_filtered_response,
  // so this must render as "not available", never as a bare unit.
  latencyMs?: number | null;
  isSuccess: boolean;
}

export function AuditLogTable({ entries, onJump }: { entries: AuditLogEntry[]; onJump: (page: number) => void }) {
  return (
    <div className="w-full py-4">
      <div className="mb-4 flex items-baseline justify-between border-b pb-3" style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))" }}>
        <h3 className="font-semibold tracking-tight text-lg" style={{ fontFamily: "var(--font-editorial, Georgia, serif)", color: "var(--ink-primary, #2A241E)" }}>
          Execution & Lineage Registry
        </h3>
        {/* The strapline used to claim this was an immutable system log. It is
            not a log and it is not immutable: `entries` is derived from the
            `pages` array in app/page.tsx -- React state, built from answers
            received in THIS browser tab and cleared on sign-out and on a 401.

            The real audit_log is not reachable from here at all. No endpoint
            returns its rows: api/metrics.py issues five aggregate queries over
            it and projects no row identity, and nothing else in the API touches
            the table. As of 2026-08-22 that is 834 rows on Supabase and 4,301
            locally that this view cannot see.

            So the strapline now says what the table below actually contains,
            matching the empty state's own wording ("current workspace
            session"), which was already accurate. */}
        <span className="font-mono text-xs tracking-widest uppercase" style={{ color: "var(--ink-metadata, #8B8378)" }}>
          Current Session Only &middot; Not Persisted
        </span>
      </div>

      <table className="w-full border-collapse text-left font-mono" style={{ fontSize: "12px" }}>
        <thead>
          <tr className="border-b text-[10.5px] uppercase tracking-[0.16em]" style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))", color: "var(--ink-metadata, #8B8378)" }}>
            <th className="py-3 pr-3 font-semibold w-12">#</th>
            <th className="py-3 pr-4 font-semibold text-center w-16">Status</th>
            <th className="py-3 pr-6 font-semibold">Query / Action Logged</th>
            <th className="py-3 pr-4 font-semibold w-28">Path</th>
            <th className="py-3 pr-4 font-semibold w-28">Confidence</th>
            <th className="py-3 text-right font-semibold w-24">Latency</th>
          </tr>
        </thead>
        <tbody>
          {entries.length === 0 ? (
            <tr>
              <td colSpan={6} className="py-12 text-center font-mono text-xs italic" style={{ color: "var(--ink-passive, #B7AEA3)" }}>
                No audit entries logged in the current workspace session.
              </td>
            </tr>
          ) : (
            entries.map((e) => {
              const numStr = String(e.pageNumber).padStart(2, "0");
              return (
                <tr
                  key={e.pageNumber}
                  onClick={() => onJump(e.pageNumber)}
                  className="cursor-pointer transition-colors hover:bg-[#DFD4C4]/30 border-b group"
                  style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))", color: "var(--ink-primary, #2A241E)" }}
                >
                  <td className="py-3.5 pr-3 tabular-nums font-semibold" style={{ color: "var(--ink-metadata, #8B8378)" }}>
                    {numStr}
                  </td>
                  
                  {/* Stamped Ledger Tick: Green (✓) for Success, Red (✗) for Failure */}
                  <td className="py-3.5 pr-4 text-center text-sm font-bold select-none">
                    {e.isSuccess ? (
                      <span title="Execution Verified" style={{ color: "var(--color-teal-500, #2E6B4A)" }}>✓</span>
                    ) : (
                      <span title="Execution Failed / Blocked" style={{ color: "var(--color-coral-500, #E2665A)" }}>✗</span>
                    )}
                  </td>

                  <td className="py-3.5 pr-6 font-sans text-[13.5px] font-normal truncate max-w-[340px] group-hover:underline" style={{ color: "var(--ink-primary, #2A241E)" }}>
                    {e.query}
                  </td>
                  <td className="py-3.5 pr-4 uppercase tracking-wider text-[11px]" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                    {e.path ?? "—"}
                  </td>
                  <td className="py-3.5 pr-4 uppercase tracking-wider text-[11px]" style={{ color: e.confidenceTier === "high" ? "var(--color-teal-500, #2E6B4A)" : "var(--ink-metadata, #8B8378)" }}>
                    {/* Same em-dash idiom as the admin-only Latency column
                        below: a field that is not available reads as absent,
                        never as a value. A bare {undefined} renders an empty
                        cell, which looks like a rendering fault. */}
                    {e.confidenceTier ?? "—"}
                  </td>
                  <td className="py-3.5 text-right tabular-nums font-medium" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                    {e.latencyMs != null ? `${e.latencyMs}ms` : "—"}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
