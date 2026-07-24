import React from "react";

interface DocumentTitleProps {
  children: React.ReactNode;
}

export function DocumentTitle({ children }: DocumentTitleProps) {
  return (
    <div className="mb-6 border-b pb-4" style={{ borderColor: "var(--theme-border-subtle, rgba(42, 38, 34, 0.14))" }}>
      <h1
        className="tracking-tight font-semibold"
        style={{
          fontFamily: "var(--font-editorial, 'IBM Plex Serif', Georgia, serif)",
          fontSize: "32px",
          color: "var(--theme-text-primary, #2A2622)",
          lineHeight: 1.15,
        }}
      >
        {children}
      </h1>
    </div>
  );
}
