"use client";

/**
 * Sidebar — navigation chrome, role-gated.
 *
 * THE ROLE CHECK BELOW IS NOT A SECURITY BOUNDARY, and reading it as one is the
 * classic frontend mistake. `userRole` comes from session.role, which comes from
 * a JSON blob in localStorage that the user can edit in DevTools. Change it to
 * "admin", reload, and the Upload Filing entry appears.
 *
 * What actually refuses is require_role("admin") on the backend route, which
 * reads the role from the SIGNATURE-VERIFIED JWT claim -- the edited localStorage
 * copy never reaches it. The forged role buys a button and a 403.
 *
 * So this is a USABILITY control: showing an entry that always 403s is worse
 * than not showing it. Keep it, and do not add anything here that a 403 does not
 * already back.
 *
 * `activeView` is narrowed by the caller. page.tsx has FIVE views and this type
 * has four -- "upload-history" is a continuation of Intake reached only from a
 * link inside UploadPanel, so page.tsx passes "upload" while it is open, keeping
 * the Intake entry highlighted. One narrowing at the boundary, rather than a
 * fifth entry nobody should click.
 *
 * `indexedFilings` HAS NO PRODUCER. It was three literals passed in by
 * page.tsx and rendered as the live corpus index, with one entry flagged
 * `active` and coloured green against no backend state at all -- it read
 * identically after a successful ingest, after a failed one, and against a
 * nine-document database or an eleven-document one.
 *
 * No endpoint returns `documents.ingestion_state`. `GET /api/documents/pending`
 * is NOT a substitute: different table, different value domain
 * (pending|processing|done|failed, the pre-ingestion queue), and admin-only.
 *
 * So the panel says what it does not know. The prop stays in the signature,
 * unchanged, so wiring it up later is one call site and no refactor.
 */

import React from "react";

interface IndexedFiling {
  company: string;
  period: string;
  active?: boolean;
}

type SidebarView = "workbench" | "peer" | "audit" | "upload";

interface SidebarProps {
  userRole: string;
  tenantId?: string;
  activeView: SidebarView;
  onViewChange: (view: SidebarView) => void;
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
  const views: SidebarView[] =
    userRole === "admin"
      ? ["workbench", "peer", "audit", "upload"]
      : ["workbench", "peer", "audit"];

  const viewLabels: Record<SidebarView, string> = {
    workbench: "Query Workbench",
    peer: "Peer Comparison",
    audit: "Audit Trail",
    upload: "Upload Filing",
  };

  return (
    <aside
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
          {/* IDENTITY, ON TWO LINES INSTEAD OF ONE WRAPPED ONE.
              The tenant id was rendered whole: 36 characters of UUID beside
              the role badge in a 200px rail, so it wrapped across three lines
              and broke mid-string. It was also the first thing in the sidebar.

              A SHORT PREFIX, NOT A NAME. The tenants table does carry a `name`
              column, but no endpoint returns it, so this component cannot know
              it and a friendly label here would be invented. The prefix is
              honest about being an identifier; `title` carries the whole value
              for anyone who needs to read or copy it.

              No user identity line: the JWT carries `sub`, `tenant_id`, `role`,
              `iat` and `exp` and no email or display name, so there is nothing
              to show that would not be fabricated. */}
          <div
            className="mt-1.5 flex flex-col gap-1"
            style={{ color: "#7B8290", fontFamily: "var(--font-archival, monospace)" }}
          >
            <span className="uppercase text-[9.5px] tracking-widest px-1.5 py-0.5 rounded bg-white/[0.04] border border-white/[0.08] self-start">
              {userRole}
            </span>
            {tenantId && (
              <span
                className="text-[9.5px] tracking-wider whitespace-nowrap overflow-hidden text-ellipsis opacity-70"
                title={`Tenant ${tenantId}`}
              >
                TENANT {tenantId.slice(0, 8).toUpperCase()}
              </span>
            )}
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
          {views.map((view) => {
            const isActive = activeView === view;
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
                  {viewLabels[view]}
                </span>
              </button>
            );
          })}
        </div>

        {/* Active Archive Registry with Brass Tab Cues */}
        <div className="space-y-2 pt-5 border-t border-white/[0.04]">
          <div
            className="px-3 text-[9.5px] font-medium uppercase tracking-[0.22em] opacity-45"
            style={{ color: "#8B8378", fontFamily: "var(--font-archival, monospace)" }}
          >
            Active Corpus
          </div>
          {indexedFilings.length === 0 ? (
            /* NAMES THE REASON. "No filings indexed" would be a claim about
               the corpus; the corpus is fine and this component simply has no
               way to see it. The distinction is the whole point of the
               zero-UI-hallucination mandate -- omit rather than substitute,
               and say which one you are doing. */
            <div
              className="px-3.5 py-2 text-[10px] leading-relaxed"
              style={{ color: "#7B8290", fontFamily: "var(--font-archival, monospace)" }}
            >
              Not available &mdash; no endpoint reports filing ingestion state.
            </div>
          ) : (
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
          )}
        </div>
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
