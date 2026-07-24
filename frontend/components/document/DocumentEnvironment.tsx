import React from "react";

interface DocumentEnvironmentProps {
  surface?: "desk" | "flat";
  children: React.ReactNode;
}

export function DocumentEnvironment({ surface = "desk", children }: DocumentEnvironmentProps) {
  return (
    <div
      className="relative min-h-screen w-full overflow-x-hidden"
      style={{
        background: surface === "desk" ? "var(--desk-background)" : "var(--paper-background)",
      }}
    >
      {/* Ambient Top-Down Spotlight Vignette */}
      {surface === "desk" && (
        <div 
          className="pointer-events-none absolute inset-0 z-0"
          style={{
            background: "radial-gradient(circle at 50% 40%, transparent 40%, rgba(8, 7, 6, 0.6) 100%)",
          }}
        />
      )}
      
      <div className="relative z-10">
        {children}
      </div>
    </div>
  );
}