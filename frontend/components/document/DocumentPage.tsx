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
        marginTop: "60px", 
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
        className="relative flex flex-col justify-between rounded-sm overflow-hidden transition-all"
        style={{
          background: "var(--theme-surface-paper, #E7DED0)",
          border: "1px solid var(--theme-border-subtle, rgba(42, 38, 34, 0.16))",
          borderRadius: "var(--paper-radius, 3px)",
          boxShadow: isHovered
            ? "var(--shadow-paper-hover)"
            : "var(--shadow-paper-rest)",
          padding: "var(--spacing-page, 48px)",
          minHeight: 1000,
          height: "auto",
          transform: isHovered
            ? "perspective(1800px) rotateX(1.8deg) rotateY(-0.8deg) rotateZ(-0.15deg) translateY(-4px)"
            : "perspective(1800px) rotateX(2.5deg) rotateY(-1.2deg) rotateZ(-0.35deg) translateY(0px)",
          transitionDuration: "350ms",
          transitionTimingFunction: "cubic-bezier(0.2, 0.8, 0.2, 1)",
          fontFamily: "var(--font-ui, 'IBM Plex Sans', sans-serif)",
          fontSize: "var(--font-size-body, 18px)",
          color: "var(--ink-primary, #2A241E)",
          lineHeight: 1.6,
        }}
      >
        {/* Layer 6: Subconscious Microscopic Texture Overlay (Opacity 0.018) */}
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

        {/* Intentional 30px Fold Corner */}
        <div
          className="absolute top-0 right-0 pointer-events-none z-10"
          style={{
            width: "var(--paper-foldSize, 30px)",
            height: "var(--paper-foldSize, 30px)",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        {/* Layer 7: Semantic HTML / Live React Content */}
        <div className="relative z-10 flex-1">{children}</div>

        {/* Subtle Institutional Watermark (~2% Opacity) */}
        <div
          className="pointer-events-none absolute select-none z-0"
          style={{
            bottom: "18%", right: "8%",
            fontFamily: "var(--font-editorial, 'Fraunces', serif)",
            fontSize: "72px",
            color: "var(--ink-primary, #2A241E)",
            opacity: 0.02,
            transform: "rotate(-8deg)",
          }}
        >
          LedgerMind
        </div>

        {/* 💡 ENGRAVED ARCHIVAL FOOTER (12px Stamped Ink) */}
        <div
          className="relative z-10 mt-12 flex items-center justify-between border-t pt-4 font-normal"
          style={{ 
            borderColor: "var(--ink-divider, #D8CEC1)", 
            fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)", 
            fontSize: "var(--font-size-footer, 12px)", 
            color: "var(--ink-footer, #7D7468)",
            letterSpacing: "0.04em"
          }}
        >
          <span>DOC ID: {docId}</span>
          {confidential && <span className="tracking-widest uppercase font-medium">CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span>{footerLabelOverride ?? `PAGE ${pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>
    </div>
  );
}
