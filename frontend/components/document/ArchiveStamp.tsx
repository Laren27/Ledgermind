"use client";

import { useEffect, useState } from "react";

type RealStatus = "pending" | "processing" | "done" | "failed";

interface ArchiveStampProps {
  status: RealStatus | null;
}

const STAMP_CONFIG: Record<RealStatus, { label: string; color: string }> = {
  pending: { label: "RECEIVED", color: "#8B7355" },
  processing: { label: "INDEXING", color: "#B58A3C" },
  done: { label: "AVAILABLE", color: "#2E6B4A" },
  failed: { label: "FAILED", color: "#B0453A" },
};

// Maps 1:1 to real pending_uploads.status values from the backend.
// No fabricated intermediate stages (OCR/chunking/embedding/etc.) —
// the backend does not emit that granularity today, and inventing a
// progress animation the system can't actually report would violate
// this project's Zero UI-Hallucination Mandate.
export function ArchiveStamp({ status }: ArchiveStampProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(false);
    const t = setTimeout(() => setMounted(true), 30);
    return () => clearTimeout(t);
  }, [status]);

  if (!status) return null;
  const config = STAMP_CONFIG[status];

  return (
    <div
      className="absolute top-6 right-8 transition-all duration-500 ease-out select-none pointer-events-none"
      style={{
        transform: mounted
          ? "rotate(-8deg) scale(1)"
          : "rotate(-8deg) scale(1.6)",
        opacity: mounted ? 0.88 : 0,
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-archival, monospace)",
          fontWeight: 700,
          fontSize: 15,
          letterSpacing: "0.16em",
          color: config.color,
          border: `2.5px solid ${config.color}`,
          borderRadius: 3,
          padding: "6px 16px",
          background: "rgba(255,255,255,0.35)",
        }}
      >
        {config.label}
      </div>
    </div>
  );
}
