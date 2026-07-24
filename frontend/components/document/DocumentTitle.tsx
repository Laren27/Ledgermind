import React from "react";

interface DocumentTitleProps {
  children: React.ReactNode;
}

export function DocumentTitle({ children }: DocumentTitleProps) {
  return (
    <div className="mb-8 border-b pb-5" style={{ borderColor: "var(--ink-divider, #D8CEC1)" }}>
      <h1
        className="tracking-tight font-semibold"
        style={{
          fontFamily: "var(--font-editorial, 'Fraunces', Georgia, serif)",
          fontSize: "var(--font-size-title, 52px)",
          color: "var(--ink-primary, #2A241E)",
          lineHeight: 1.1,
          fontWeight: 600,
        }}
      >
        {children}
      </h1>
    </div>
  );
}
