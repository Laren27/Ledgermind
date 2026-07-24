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
      // 💡 REDUCED WIDTH: w-[200px] (~10% narrower than before) to let paper own the screen
      className="flex w-[200px] flex-col justify-between p-5 select-none transition-all z-20 shrink-0"
      style={{
        background: "rgba(16, 13, 11, 0.97)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderRight: "1px solid rgba(255, 255, 255, 0.04)",
        boxShadow: "6px 0 30px rgba(0, 0, 0, 0.55)",
      }}
    >
      <div className="space-y-8">
        {/* Brand & Tenant Info */}
        <div>
          <div className="flex items-center space-x-2">
            <span
              className="font-semibold tracking-tight text-lg"
              style={{ fontFamily: "var(--font-editorial, 'Fraunces', serif)", color: "#ECEDEF" }}
            >
              LedgerMind
            </span>
          </div>
          <div className="mt-1 flex items-center space-x-1.5 text-xs opacity-75" style={{ color: "#7B8290", fontFamily: "var(--font-archival, monospace)" }}>
            <span className="uppercase text-[9.5px] tracking-widest px-1.5 py-0.5 rounded bg-white/[0.04] border border-white/[0.08]">
              {userRole}
            </span>
            {tenantId && <span className="text-[10px]">• {tenantId}</span>}
          </div>
        </div>

        {/* Workspace Views Navigation */}
        <div className="space-y-1">
          <div
            className="px-3 mb-2.5 text-[9.5px] font-medium uppercase tracking-[0.22em] opacity-45"
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
                className="relative w-full text-left px-3.5 py-2 rounded-sm text-xs transition-all flex items-center justify-between font-normal group"
                style={{
                  background: isActive ? "linear-gradient(90deg, rgba(181, 138, 60, 0.12), transparent)" : "transparent",
                  color: isActive ? "#ECEDEF" : "#8B8378",
                }}
              >
                {isActive && (
                  <span 
                    className="absolute left-0 top-1 bottom-1 w-[3px] rounded-r-sm transition-all"
                    style={{
                      background: "linear-gradient(180deg, #C99B4A 0%, #B58A3C 50%, #8A6D3B 100%)",
                      boxShadow: "1px 0 6px rgba(181, 138, 60, 0.4)",
                    }}
                  />
                )}
                <span className={isActive ? "font-medium tracking-wide text-[12.5px]" : "group-hover:text-[#ECEDEF] transition-colors text-[12.5px]"}>
                  {label}
                </span>
              </button>
            );
          })}
        </div>

        {/* Active Archive Registry with Brass Tab Cues */}
        {indexedFilings.length > 0 && (
          <div className="space-y-2 pt-5 border-t border-white/[0.04]">
            <div
              className="px-3 text-[9.5px] font-medium uppercase tracking-[0.22em] opacity-45"
              style={{ color: "#8B8378", fontFamily: "var(--font-archival, monospace)" }}
            >
              Active Corpus
            </div>
            <div className="space-y-1">
              {indexedFilings.map((filing, idx) => (
                <div
                  key={idx}
                  className="relative px-3.5 py-2 rounded-sm text-xs flex items-center justify-between transition-colors"
                  style={{
                    background: filing.active ? "rgba(46, 107, 74, 0.10)" : "transparent",
                    color: filing.active ? "#2E6B4A" : "#7B8290",
                    fontFamily: "var(--font-archival, monospace)",
                  }}
                >
                  {filing.active && (
                    <span 
                      className="absolute left-0 top-1.5 bottom-1.5 w-[2.5px] rounded-r-sm"
                      style={{ background: "#2E6B4A" }}
                    />
                  )}
                  <span className="font-semibold tracking-wider text-[11px] truncate pr-1">{filing.company}</span>
                  <span className="text-[9.5px] opacity-75 shrink-0">{filing.period}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Sign Out Footer */}
      <div className="pt-5 border-t border-white/[0.04]">
        <button
          onClick={onSignOut}
          className="w-full text-left px-3 py-1.5 rounded text-xs transition-colors opacity-65 hover:opacity-100"
          style={{ color: "#E2665A", fontFamily: "var(--font-archival, monospace)" }}
        >
          Sign Out →
        </button>
      </div>
    </aside>
  );
}
