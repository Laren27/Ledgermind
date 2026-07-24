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
  const [flattened, setFlattened] = useState(false);

  return (
    <div
      className="relative mx-auto my-6"
      style={{ width: "85%", maxWidth: 820 }}
      onMouseEnter={() => setFlattened(true)}
      onMouseLeave={() => setFlattened(false)}
    >
      {/* Layer 4: Pure CSS Stationary Paper Stack */}
      <PaperStack />

      {/* Layer 5: Active Working Paper Canvas */}
      <div
        key={`${docId}-${pageNumber}`}
        className="relative flex flex-col justify-between transition-transform rounded-sm overflow-hidden"
        style={{
          background: "var(--paper-background, #E6DFD3)",
          border: "1px solid var(--paper-border, rgba(42, 38, 34, 0.12))",
          borderRadius: "3px",
          // 💡 WARM BROWN DOUBLE-SHADOWS: Contact shadow + Lift shadow
          boxShadow: "0 8px 18px rgba(40, 30, 20, 0.16), 0 40px 80px rgba(40, 30, 20, 0.20)",
          padding: "var(--spacing-page, 48px)",
          minHeight: 1000,
          height: "auto",
          transform: flattened
            ? "perspective(1400px) rotateX(0deg) rotateY(0deg) rotateZ(0deg)"
            : "perspective(1400px) rotateX(3deg) rotateY(-1.5deg) rotateZ(-0.5deg)",
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

        {/* Top-Right Fold Corner */}
        <div
          className="absolute top-0 right-0 pointer-events-none z-10"
          style={{
            width: "40px",
            height: "40px",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        {/* Layer 7: Semantic HTML / Live React Content */}
        <div className="relative z-10 flex-1">{children}</div>

        {/* Diagonal Watermark */}
        <div
          className="pointer-events-none absolute select-none z-0"
          style={{
            bottom: "18%", right: "8%",
            fontFamily: "var(--font-document-title, serif)",
            fontSize: 72,
            color: "var(--paper-text, #2A2622)",
            opacity: 0.05,
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
            fontFamily: "var(--font-body, monospace)", 
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