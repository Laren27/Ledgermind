"use client";
import { useState } from "react";

interface DocumentPageProps {
  docId: string;
  pageNumber: number;
  totalPages: number;
  confidential?: boolean;
  isLoading?: boolean;
  children: React.ReactNode;
}

const STACK_DEPTH = 4;

export function DocumentPage({
  docId, pageNumber, totalPages, confidential, isLoading, children,
}: DocumentPageProps) {
  const [flattened, setFlattened] = useState(false);

  return (
    <div
      className="relative mx-auto"
      style={{ width: "85%", maxWidth: 820 }}
      onMouseEnter={() => setFlattened(true)}
      onMouseLeave={() => setFlattened(false)}
    >
      {Array.from({ length: STACK_DEPTH }).map((_, i) => (
        <div
          key={i}
          className="absolute inset-0 rounded-sm"
          style={{
            background: "var(--paper-background-shadowed)",
            border: "1px solid var(--paper-border)",
            boxShadow: "0 2px 6px rgba(0,0,0,0.25)",
            transform: `translate(${(i + 1) * 5}px, ${(i + 1) * 5}px)`,
            zIndex: -1 - i,
            opacity: 1 - i * 0.14,
          }}
        />
      ))}

      <div
        className="relative rounded-sm transition-transform flex flex-col"
        style={{
          background: "var(--paper-background)",
          border: "1px solid var(--paper-border)",
          boxShadow: `var(--shadow-contact), var(--shadow-lift)`,
          padding: "var(--spacing-page)",
          minHeight: 1000,
          height: "auto",
          transform: flattened
            ? "perspective(1400px) rotateX(0deg) rotateY(0deg)"
            : "perspective(1400px) rotateX(4deg) rotateY(-2deg) rotateZ(-1deg)",
          transitionDuration: "var(--animation-duration-normal)",
          transitionTimingFunction: "var(--animation-easing)",
        }}
      >
        <div
          className="absolute top-0 right-0"
          style={{
            width: "var(--paper-fold-size)",
            height: "var(--paper-fold-size)",
            background: "linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.08) 50%)",
            clipPath: "polygon(100% 0, 0 0, 100% 100%)",
          }}
        />

        <div className="flex-1">{isLoading ? <DocumentSkeleton /> : children}</div>

        <div
          className="pointer-events-none absolute select-none"
          style={{
            bottom: "18%", right: "8%",
            fontFamily: "var(--font-document-title)",
            fontSize: 72,
            color: "var(--paper-text)",
            opacity: "var(--watermark-opacity)",
            transform: "rotate(var(--watermark-rotation))",
          }}
        >
          LedgerMind
        </div>

        <div
          className="mt-8 flex items-center justify-between border-t pt-3"
          style={{ borderColor: "var(--paper-border)", fontFamily: "var(--font-body)", fontSize: 10.5, color: "var(--paper-text-muted)" }}
        >
          <span>DOC ID: {docId}</span>
          {confidential && <span>CONFIDENTIAL — INTERNAL USE ONLY</span>}
          <span>PAGE {pageNumber} OF {totalPages}</span>
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
