"use client";

import React from "react";

interface IndexedFiling {
  company: string;
  period: string;
  active?: boolean;
}

interface SidebarProps {
  userRole: string;
  tenantId?: string;
  activeView: "workbench" | "peer" | "audit";
  onViewChange: (view: "workbench" | "peer" | "audit") => void;
  onSignOut: () => void;
  indexedFilings?: IndexedFiling[];
}

export function Sidebar({
  userRole,
  tenantId,
  activeView,
  onViewChange,
  onSignOut,
  indexedFilings = [],
}: SidebarProps) {
  return (
    <aside
      className="flex w-64 flex-col justify-between p-7 select-none transition-all z-20 shrink-0"
      style={{
        background: "rgba(16, 13, 11, 0.96)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderRight: "1px solid rgba(255, 255, 255, 0.04)",
        boxShadow: "6px 0 30px rgba(0, 0, 0, 0.55)",
      }}
    >
      <div className="space-y-10">
        {/* Brand & Tenant Info */}
        <div>
          <div className="flex items-center space-x-2">
            <span
              className="font-semibold tracking-tight text-xl"
              style={{ fontFamily: "var(--font-editorial, 'Fraunces', serif)", color: "#ECEDEF" }}
            >
              LedgerMind
            </span>
          </div>
          <div className="mt-1.5 flex items-center space-x-2 text-xs opacity-75" style={{ color: "#7B8290", fontFamily: "var(--font-archival, monospace)" }}>
            <span className="uppercase text-[10px] tracking-widest px-1.5 py-0.5 rounded bg-white/[0.04] border border-white/[0.08]">
              {userRole}
            </span>
            {tenantId && <span>• {tenantId}</span>}
          </div>
        </div>

        {/* Workspace Views Navigation — Library Archive Aesthetic */}
        <div className="space-y-1.5">
          <div
            className="px-3 mb-3 text-[10px] font-medium uppercase tracking-[0.22em] opacity-50"
            style={{ color: "#8B8378", fontFamily: "var(--font-archival, monospace)" }}
          >
            Archive Index
          </div>
          {(["workbench", "peer", "audit"] as const).map((view) => {
            const isActive = activeView === view;
            const label = view === "workbench" ? "Query Workbench" : view === "peer" ? "Peer Comparison" : "Audit Trail";
            return (
              <button
                key={view}
                onClick={() => onViewChange(view)}
                className="w-full text-left px-3 py-2.5 rounded text-sm transition-all flex items-center justify-between font-normal"
                style={{
                  background: isActive ? "linear-gradient(90deg, rgba(181, 138, 60, 0.15), rgba(181, 138, 60, 0.02))" : "transparent",
                  color: isActive ? "#ECEDEF" : "#949A6E",
                  borderLeft: isActive ? "3px solid #B58A3C" : "3px solid transparent",
                }}
              >
                <span className={isActive ? "font-medium" : "opacity-80"}>{label}</span>
                {isActive && <span className="h-1.5 w-1.5 rounded-full" style={{ background: "#B58A3C" }} />}
              </button>
            );
          })}
        </div>

        {/* Indexed Filings Registry */}
        {indexedFilings.length > 0 && (
          <div className="space-y-2.5 pt-6 border-t border-white/[0.04]">
            <div
              className="px-3 text-[10px] font-medium uppercase tracking-[0.22em] opacity-50"
              style={{ color: "#8B8378", fontFamily: "var(--font-archival, monospace)" }}
            >
              Active Corpus
            </div>
            <div className="space-y-1">
              {indexedFilings.map((filing, idx) => (
                <div
                  key={idx}
                  className="px-3 py-2 rounded text-xs flex items-center justify-between transition-colors"
                  style={{
                    background: filing.active ? "rgba(62, 217, 192, 0.06)" : "transparent",
                    color: filing.active ? "#3ED9C0" : "#7B8290",
                    fontFamily: "var(--font-archival, monospace)",
                  }}
                >
                  <span className="font-semibold tracking-wider text-[11px]">{filing.company}</span>
                  <span className="text-[10px] opacity-75">{filing.period}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Sign Out Footer */}
      <div className="pt-6 border-t border-white/[0.04]">
        <button
          onClick={onSignOut}
          className="w-full text-left px-3 py-2 rounded text-xs transition-colors hover:bg-white/5 opacity-70 hover:opacity-100"
          style={{ color: "#E2665A", fontFamily: "var(--font-archival, monospace)" }}
        >
          Sign Out →
        </button>
      </div>
    </aside>
  );
}
