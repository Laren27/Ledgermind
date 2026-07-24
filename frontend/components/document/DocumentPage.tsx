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
      // Asymmetric desk placement: Dropped 60px from top, shifted left (4% margin-left)
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
      {/* Layer 4: Pure CSS Stationary Paper Stack */}
      <PaperStack />

      {/* Layer 5: Active Working Paper Canvas */}
      <div
        key={`${docId}-${pageNumber}`}
        className="relative flex flex-col justify-between rounded-sm overflow-hidden transition-all"
        style={{
          background: "var(--paper-background, #E6DFD3)",
          border: "1px solid var(--paper-border, rgba(42, 38, 34, 0.14))",
          borderRadius: "var(--paper-corner-radius, 3px)",
          // Warm ambient occlusion shadows
          boxShadow: isHovered
            ? "0 14px 24px rgba(32, 22, 16, 0.30), 0 40px 90px rgba(20, 14, 10, 0.28)"
            : "0 10px 18px rgba(32, 22, 16, 0.26), 0 28px 70px rgba(20, 14, 10, 0.22)",
          padding: "var(--spacing-page, 48px)",
          minHeight: 1000,
          height: "auto",
          // Permanent 1800px camera perspective — Never flattens to 0deg
          transform: isHovered
            ? "perspective(1800px) rotateX(1.8deg) rotateY(-0.8deg) rotateZ(-0.15deg) translateY(-4px)"
            : "perspective(1800px) rotateX(2.5deg) rotateY(-1.2deg) rotateZ(-0.35deg) translateY(0px)",
          transitionDuration: "350ms",
          transitionTimingFunction: "cubic-bezier(0.2, 0.8, 0.2, 1)",
        }}
      >
        {/* Layer 6: Microscopic Paper Texture Overlay (3.5% Multiply Blend) */}
        <div
          className="pointer-events-none absolute inset-0 z-0"
          style={{
            backgroundImage: `url('/assets/environment/paper-texture.png')`,
            backgroundSize: "cover",
            backgroundPosition: "center",
            opacity: 0.035,
            mixBlendMode: "multiply",
          }}
        />

        {/* Intentional 30px Fold Corner */}
        <div
          className="absolute top-0 right-0 pointer-events-none z-10"
          style={{
            width: "30px",
            height: "30px",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        {/* Layer 7: Semantic HTML / Live React Content */}
        <div className="relative z-10 flex-1">{children}</div>

        {/* Subtle Watermark (~2.2% Opacity) */}
        <div
          className="pointer-events-none absolute select-none z-0"
          style={{
            bottom: "18%", right: "8%",
            fontFamily: "var(--font-editorial, 'IBM Plex Serif', serif)",
            fontSize: 72,
            color: "var(--paper-text, #2A2622)",
            opacity: 0.022,
            transform: "rotate(-8deg)",
          }}
        >
          LedgerMind
        </div>

        {/* Institutional Footer */}
        <div
          className="relative z-10 mt-8 flex items-center justify-between border-t pt-3"
          style={{ 
            borderColor: "var(--paper-border, rgba(42, 38, 34, 0.12))", 
            fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)", 
            fontSize: 10.5, 
            color: "var(--paper-text-muted, #6B6053)" 
          }}
        >
          <span>DOC ID: {docId}</span>
          {confidential && <span>CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span>{footerLabelOverride ?? `PAGE ${pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>
    </div>
  );
}

function DocumentSkeleton() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="h-6 w-1/2 rounded" style={{ background: "var(--paper-border)" }} />
      <div className="h-4 w-full rounded" style={{ background: "var(--paper-border)" }} />
      <div className="h-4 w-5/6 rounded" style={{ background: "var(--paper-border)" }} />
    </div>
  );
}
