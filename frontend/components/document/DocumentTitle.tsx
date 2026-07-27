import React from "react";

interface DocumentTitleProps {
  children: React.ReactNode;
  color?: string;
  dividerColor?: string;
}

export function DocumentTitle({ children, color, dividerColor }: DocumentTitleProps) {
  return (
    <div className="mt-4 mb-10 border-b pb-6" style={{ borderColor: dividerColor ?? "var(--ink-divider, #D8CEC1)" }}>
      <h1
        className="tracking-tight font-semibold"
        style={{
          fontFamily: "var(--font-editorial, 'Fraunces', Georgia, serif)",
          fontSize: "var(--font-size-title, 44px)", /* Scaled down ~15% for elegance */
          color: color ?? "var(--ink-primary, #2A241E)",
          lineHeight: 1.15,
        }}
      >
        {children}
      </h1>
    </div>
  );
}
