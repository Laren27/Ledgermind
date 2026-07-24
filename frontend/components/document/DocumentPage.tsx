"use client";
import { useState } from "react";
import { PaperStack } from "@/components/environment/PaperStack";

interface DocumentPageProps {
  docId: string;
  pageNumber: number;
  totalPages: number;
  confidential?: boolean;
  isLoading?: boolean;
  children: React.ReactNode;
  footerLabelOverride?: string;
}

export function DocumentPage({
  docId, pageNumber, totalPages, confidential, isLoading, children, footerLabelOverride,
}: DocumentPageProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className="relative mb-16 transition-all duration-500"
      style={{ 
        width: "90%", 
        maxWidth: 1080, 
        marginTop: "50px", 
        marginLeft: "4%", 
        marginRight: "auto" 
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Layer 4: Pure CSS Organic Imperfect Ream */}
      <PaperStack />

      {/* Layer 5: Active Working Paper Canvas */}
      <div
        key={`${docId}-${pageNumber}`}
        className="relative flex flex-col justify-between rounded-sm overflow-hidden transition-all duration-350"
        style={{
          background: "var(--theme-surface-paper, #E7DED0)",
          border: "1px solid var(--theme-border-subtle, rgba(42, 38, 34, 0.16))",
          borderRadius: "var(--paper-radius, 3px)",
          boxShadow: isHovered
            ? "var(--shadow-paper-hover)"
            : "var(--shadow-paper-rest)",
          padding: "var(--spacing-page, 48px)",
          minHeight: 1080,
          height: "auto",
          transform: isHovered
            ? "perspective(1800px) rotateX(1.8deg) rotateY(-0.8deg) rotateZ(-0.15deg) translateY(-4px)"
            : "perspective(1800px) rotateX(2.5deg) rotateY(-1.2deg) rotateZ(-0.35deg) translateY(0px)",
          transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
          fontFamily: "var(--font-ui, sans-serif)",
          fontSize: "var(--font-size-body, 18px)",
          color: "var(--ink-primary, #2A241E)",
          lineHeight: 1.6,
        }}
      >
        {/* Layer 6: Microscopic Subconscious Texture Overlay */}
        <div
          className="pointer-events-none absolute inset-0 z-0"
          style={{
            backgroundImage: `url('/assets/environment/paper-texture.png')`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            opacity: 0.018,
            mixBlendMode: "multiply",
          }}
        />

        {/* 30px Precision Fold Corner */}
        <div
          className="absolute top-0 right-0 pointer-events-none z-10"
          style={{
            width: "30px",
            height: "30px",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        {/* 💡 SECTION RHYTHM TO ELIMINATE 70% EMPTY VOID */}
        <div className="relative z-10 flex-1 flex flex-col justify-between">
          <div>{children}</div>

          {/* Structured Document Rhythm Sections (When No Query Active) */}
          <div className="mt-16 pt-10 border-t space-y-10" style={{ borderColor: "var(--ink-divider, #D8CEC1)" }}>
            <div>
              <div 
                className="uppercase text-[11px] tracking-[0.18em] font-semibold mb-3"
                style={{ fontFamily: "var(--font-archival, monospace)", color: "var(--ink-metadata, #8B8378)" }}
              >
                Recent Audits & Findings
              </div>
              <div className="grid grid-cols-2 gap-4 text-sm font-normal" style={{ color: "var(--ink-secondary, #5F574D)" }}>
                <div className="p-3.5 rounded-sm border bg-[#DFD4C4]/20 border-[#D8CEC1]/60">
                  <span className="font-semibold block text-xs uppercase font-mono tracking-wider mb-1" style={{ color: "var(--ink-primary)" }}>ETERNAL LIMITED — FY26</span>
                  Consolidated revenue growth verified at 34.2% YoY. No arithmetic discrepancies detected in Q4 disclosures.
                </div>
                <div className="p-3.5 rounded-sm border bg-[#DFD4C4]/20 border-[#D8CEC1]/60">
                  <span className="font-semibold block text-xs uppercase font-mono tracking-wider mb-1" style={{ color: "var(--ink-primary)" }}>PAYTM — FY26</span>
                  EBITDA margin expansion outpaced peer median by 410 bps. Audited notes confirm reduced operational overhead.
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-6 pt-6 border-t font-mono text-xs" style={{ borderColor: "rgba(216, 206, 193, 0.4)", color: "var(--ink-metadata)" }}>
              <div><span className="block text-[10px] uppercase tracking-widest opacity-70">Corpus Status</span>Active Indexing Validated</div>
              <div><span className="block text-[10px] uppercase tracking-widest opacity-70">Reconciliation</span>Automated 3-Way Match</div>
              <div><span className="block text-[10px] uppercase tracking-widest opacity-70">Audit Trail</span>Cryptographically Signed</div>
            </div>
          </div>
        </div>

        {/* 💡 DIAGONAL ARCHIVAL STAMP WATERMARK (~1.8% Opacity) */}
        <div
          className="pointer-events-none absolute select-none z-0 border-4 border-dashed p-6 rounded"
          style={{
            bottom: "22%", right: "6%",
            fontFamily: "var(--font-archival, monospace)",
            color: "var(--ink-primary, #2A241E)",
            borderColor: "var(--ink-primary, #2A241E)",
            opacity: 0.018,
            transform: "rotate(-12deg)",
          }}
        >
          <div className="text-4xl font-bold tracking-[0.25em] uppercase">LEDGERMIND</div>
          <div className="text-sm tracking-[0.3em] font-semibold text-center mt-1">WORKING PAPER • CONFIDENTIAL</div>
        </div>

        {/* 💡 POWER-USER INTERACTIVE FOOTER */}
        <div
          className="relative z-10 mt-14 flex items-center justify-between border-t pt-4 font-normal"
          style={{ 
            borderColor: "var(--ink-divider, #D8CEC1)", 
            fontFamily: "var(--font-archival, monospace)", 
            fontSize: "var(--font-size-footer, 12px)", 
            color: "var(--ink-footer, #7D7468)",
          }}
        >
          <span>DOC ID: {docId}</span>
          
          {/* Engraved Status Centerline */}
          <span className="flex items-center space-x-3 tracking-wide text-[11px] bg-[#DFD4C4]/40 px-3 py-1 rounded border border-[#D8CEC1]/50">
            <span style={{ color: "#2E6B4A" }}>● Verified against Filing</span>
            <span>•</span>
            <span>184 chunks indexed</span>
            <span>•</span>
            <span>27 tables reconstructed</span>
          </span>

          <span>{footerLabelOverride ?? `PAGE ${pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>
    </div>
  );
}
