import React from "react";

export function ExecutiveObjects() {
  return (
    <div
      className="pointer-events-none fixed inset-0 z-0 hidden opacity-85 transition-opacity duration-700 2xl:block"
      style={{
        backgroundImage: `url('/assets/environment/executive-objects.png')`,
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundRepeat: "no-repeat",
      }}
    />
  );
}