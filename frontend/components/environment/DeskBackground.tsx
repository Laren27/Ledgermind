import React from "react";

export function DeskBackground() {
  return (
    <div
      className="pointer-events-none fixed inset-0 z-0"
      style={{
        backgroundImage: `linear-gradient(rgba(18, 15, 12, 0.15), rgba(18, 15, 12, 0.15)), url('/assets/environment/walnut-desk.png')`,
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
      }}
    />
  );
}