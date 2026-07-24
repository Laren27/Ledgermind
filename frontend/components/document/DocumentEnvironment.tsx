import React from "react";
import { DeskBackground } from "@/components/environment/DeskBackground";
import { AmbientLighting } from "@/components/environment/AmbientLighting";
import { ExecutiveObjects } from "@/components/environment/ExecutiveObjects";

interface DocumentEnvironmentProps {
  surface?: "desk" | "flat";
  children: React.ReactNode;
}

export function DocumentEnvironment({ surface = "desk", children }: DocumentEnvironmentProps) {
  if (surface !== "desk") {
    return (
      <div className="min-h-screen w-full" style={{ background: "var(--paper-background)" }}>
        {children}
      </div>
    );
  }

  return (
    <div className="relative min-h-screen w-full overflow-x-hidden text-[var(--text-primary)]">
      {/* Environmental Framing Layers (Fixed behind application UI) */}
      <DeskBackground />
      <AmbientLighting />
      <ExecutiveObjects />

      {/* Live React Content Layer (Preserves all existing flex and sidebar layouts without shrink-wrapping) */}
      <div className="relative z-10 min-h-screen w-full">
        {children}
      </div>
    </div>
  );
}