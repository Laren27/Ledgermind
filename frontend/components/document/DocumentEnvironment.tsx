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
    // The token named here was never declared anywhere in this repo, so the
    // colour rule was invalid at computed-value time and this element simply
    // inherited from the body. Measured: it rendered #ECEDEF either way, so
    // the class was a no-op that looked deliberate.
    //
    // Repointed at the shell text token rather than the paper one. Both
    // --theme-text-primary and --ink-primary resolve to #2A241E, which is a
    // near-black meant for cream paper; this element is the DESK, sitting on
    // #100D0B. Either of those would have put near-black chrome on a
    // near-black ground. The name it was reaching for was almost certainly
    // the old Tailwind key of the same spelling, which held #ECEDEF.
    <div className="relative min-h-screen w-full overflow-x-hidden text-[color:var(--theme-text-sidebarPrimary)]">
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