"use client";
import { useState } from "react";

interface DocumentPageProps {
  docId: string;
  pageNumber: number;
  totalPages: number;
  confidential?: boolean;
  isLoading?: boolean;
  children: React.ReactNode;
  footerLabelOverride?: string;
}

// 💡 ORGANIC PAPER STACK: Realistic physical offsets and micro-rotations
const ORGANIC_STACK_LAYERS = [
  { rotate: "-1.2deg", translateX: "3px",  translateY: "4px",  scale: 0.998, opacity: 0.95 },
  { rotate: "0.8deg",  translateX: "-2px", translateY: "8px",  scale: 0.995, opacity: 0.85 },
  { rotate: "-0.5deg", translateX: "5px",  translateY: "13px", scale: 0.990, opacity: 0.70 },
  { rotate: "1.4deg",  translateX: "-4px", translateY: "18px", scale: 0.985, opacity: 0.50 },
  { rotate: "-0.8deg", translateX: "2px",  translateY: "24px", scale: 0.980, opacity: 0.30 },
];

export function DocumentPage({
  docId, pageNumber, totalPages, confidential, isLoading, children, footerLabelOverride,
}: DocumentPageProps) {
  const [flattened, setFlattened] = useState(false);

  return (
    <div
      className="relative mx-auto my-6"
      style={{ width: "88%", maxWidth: 840 }}
      onMouseEnter={() => setFlattened(true)}
      onMouseLeave={() => setFlattened(false)}
    >
      {/* 1. THE THICK LEDGER STACK (Underlying physical sheets) */}
      {ORGANIC_STACK_LAYERS.map((layer, i) => (
        <div
          key={i}
          className="absolute inset-0 transition-transform duration-500 ease-out"
          style={{
            background: i % 2 === 0 ? "var(--paper-background-shadowed)" : "var(--paper-background)",
            border: "1px solid var(--paper-border)",
            borderRadius: "var(--paper-corner-radius)",
            boxShadow: "var(--shadow-page-stack)",
            transform: flattened
              ? `translate(0px, ${(i + 1) * 3}px) rotate(0deg) scale(1)`
              : `translate(${layer.translateX}, ${layer.translateY}) rotate(${layer.rotate}) scale(${layer.scale})`,
            zIndex: -1 - i,
            opacity: layer.opacity,
          }}
        />
      ))}

      {/* 2. THE ACTIVE WORKING PAPER (With automatic page-turn physics) */}
      <div
        key={`${docId}-${pageNumber}`} // ⚡ Changing key triggers the CSS pageTurn animation!
        className="animate-page-turn relative flex flex-col justify-between transition-transform"
        style={{
          background: "var(--paper-background)",
          border: "1px solid var(--paper-border)",
          borderRadius: "var(--paper-corner-radius)",
          padding: "var(--spacing-page)",
          minHeight: 1040,
          transform: flattened
            ? "perspective(1400px) rotateX(0deg) rotateY(0deg) rotateZ(0deg)"
            : "perspective(1400px) rotateX(3deg) rotateY(-1.5deg) rotateZ(-0.5deg)",
          transitionDuration: "var(--animation-duration-normal)",
          transitionTimingFunction: "var(--animation-easing)",
        }}
      >
        {/* Realistic 3D Dog-Ear Corner Curl */}
        <div
          className="absolute top-0 right-0 pointer-events-none overflow-hidden"
          style={{
            width: "var(--paper-fold-size)",
            height: "var(--paper-fold-size)",
            borderRadius: "0 var(--paper-corner-radius) 0 0",
          }}
        >
          <div
            className="absolute top-0 right-0 w-full h-full"
            style={{
              background: "linear-gradient(225deg, #FFF8ED 0%, #C8BEAC 45%, rgba(0,0,0,0.35) 50%, transparent 50%)",
              boxShadow: "-3px 3px 6px rgba(0,0,0,0.2)",
            }}
          />
        </div>

        {/* Paper Content */}
        <div className="flex-1 z-10">{children}</div>

        {/* Subtle Ledger Watermark */}
        <div
          className="pointer-events-none absolute select-none z-0"
          style={{
            bottom: "15%", right: "6%",
            fontFamily: "var(--font-document-title)",
            fontSize: 84,
            fontWeight: 700,
            color: "var(--paper-text)",
            opacity: "var(--watermark-opacity)",
            transform: "rotate(var(--watermark-rotation))",
          }}
        >
          LedgerMind
        </div>

        {/* Working Paper Footer */}
        <div
          className="mt-12 flex items-center justify-between border-t pt-4 z-10"
          style={{ 
            borderColor: "var(--paper-border)", 
            fontFamily: "var(--font-body)", 
            fontSize: 11, 
            color: "var(--paper-text-muted)" 
          }}
        >
          <span>DOC ID: {docId}</span>
          {confidential && <span className="tracking-widest font-semibold">CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span className="font-semibold">{footerLabelOverride ?? `PAGE ${pageNumber} OF ${totalPages}`}</span>
        </div>
      </div>
    </div>
  );
}