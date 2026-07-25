"use client";

import React from "react";

export interface AuditLogEntry {
  pageNumber: number;
  query: string;
  path: string | null;
  confidenceTier: string;
  latencyMs: number;
  isSuccess: boolean;
}

export function AuditLogTable({ entries, onJump }: { entries: AuditLogEntry[]; onJump: (page: number) => void }) {
  return (
    <div className="w-full py-4">
      <div className="mb-4 flex items-baseline justify-between border-b pb-3" style={{ borderColor: "var(--ink-divider, rgba(215, 206, 195, 0.55))" }}>
        <h3 className="font-semibold tracking-tight text-lg" style={{ fontFamily: "var(--font-editorial, Georgia, serif)", color: "var(--ink-primary, #2A241E)" }}>
          Execution & Lineage Registry
        </h3>
        <span className="font-mono text-xs tracking-widest uppercase" style={{ color: "var(--ink-metadata, #8B8378)" }}>
          Immutable System Log
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
                    {e.confidenceTier}
                  </td>
                  <td className="py-3.5 text-right tabular-nums font-medium" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                    {e.latencyMs}ms
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
