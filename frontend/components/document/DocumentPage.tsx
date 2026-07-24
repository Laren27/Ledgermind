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
  isShifting?: boolean;
}

export function DocumentPage({
  docId, pageNumber, totalPages, confidential, isLoading, children, footerLabelOverride, isShifting = false,
}: DocumentPageProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className="relative mb-16 transition-all duration-500"
      // 💡 WIDER CANVAS: 93% width / 1140px max to optimize line wrapping for financial reports
      style={{ 
        width: "93%", 
        maxWidth: 1140, 
        marginTop: "50px", 
        marginLeft: "4.5%", 
        marginRight: "auto" 
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Layer 4: Pure CSS Organic Imperfect Ream with 240ms Paper Shift */}
      <div className={`transition-all duration-[240ms] ease-out ${isShifting ? "translate-x-[-4px] translate-y-[2px]" : ""}`}>
        <PaperStack />
      </div>

      {/* Layer 5: Active Working Paper Canvas */}
      <div
        key={`${docId}-${pageNumber}`}
        className={`relative flex flex-col justify-between rounded-sm overflow-hidden transition-all duration-[240ms] ease-out ${
          isShifting ? "opacity-40 translate-x-[-8px] scale-[0.997]" : "opacity-100 translate-x-0 scale-100"
        }`}
        style={{
          background: "var(--theme-surface-paper, #E7DED0)",
          border: "1px solid var(--theme-border-subtle)",
          borderRadius: "var(--radius-sm, 3px)",
          boxShadow: isHovered
            ? "var(--shadow-paper-hover)"
            : "var(--shadow-paper-rest)",
          padding: "var(--rhythm-major, 72px) var(--space-12, 48px) var(--space-12, 48px)",
          minHeight: 1080,
          height: "auto",
          transform: isHovered && !isShifting
            ? "perspective(1800px) rotateX(1.8deg) rotateY(-0.8deg) rotateZ(-0.15deg) translateY(-4px)"
            : "perspective(1800px) rotateX(2.5deg) rotateY(-1.2deg) rotateZ(-0.35deg) translateY(0px)",
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

        {/* STRICT VERTICAL RHYTHM SECTION CONTENT */}
        <div className="relative z-10 flex-1 flex flex-col justify-between space-y-[var(--rhythm-major,72px)]">
          <div>{children}</div>

        </div>

        {/* 💡 THE ICONIC ARCHIVAL STAMP WATERMARK (~1.8% Opacity) */}
        <div
          className="pointer-events-none absolute select-none z-0 py-4 px-8 border-y-[1.5px]"
          style={{
            bottom: "26%", right: "7%",
            fontFamily: "var(--font-archival, monospace)",
            color: "var(--ink-primary)",
            borderColor: "var(--ink-primary)",
            opacity: 0.018,
            transform: "rotate(-10deg)",
          }}
        >
          <div className="text-3xl font-extrabold tracking-[0.32em] uppercase text-center">LEDGERMIND</div>
          <div className="text-[11px] tracking-[0.40em] font-semibold text-center mt-1.5 uppercase">Working Paper • Verified</div>
        </div>

        {/* ENGRAVED POWER-USER FOOTER */}
        <div
          className="relative z-10 mt-[var(--rhythm-major,72px)] flex items-center justify-between border-t pt-4 font-normal"
          style={{ 
            borderColor: "var(--ink-divider)", 
            fontFamily: "var(--font-archival, monospace)", 
            fontSize: "var(--font-size-footer, 12px)", 
            color: "var(--ink-footer)",
          }}
        >
          <span>DOC ID: {docId}</span>
          {confidential && <span>CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span className="tabular-metrics">{footerLabelOverride ?? `PAGE ${pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>
    </div>
  );
}
