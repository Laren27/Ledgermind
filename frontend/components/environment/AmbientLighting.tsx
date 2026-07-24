import React from "react";

export function AmbientLighting() {
  return (
    <div
      className="pointer-events-none fixed inset-0 z-0"
      style={{
        background: `
          radial-gradient(circle at 18% 12%, rgba(255, 248, 235, 0.16), transparent 45%),
          radial-gradient(circle at 85% 85%, rgba(0, 0, 0, 0.35), transparent 50%)
        `,
      }}
    />
  );
}