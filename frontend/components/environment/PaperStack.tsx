import React from "react";

// 💡 BOUND NOTEBOOK STACK: 5 sheets with tight vertical stepping, micro-rotations, and warm brown shadows
const STACK_SHEETS = [
  { translateY: "2px",  translateX: "1px",  rotate: "-0.3deg", scaleX: 0.998, opacity: 0.95 },
  { translateY: "5px",  translateX: "-1px", rotate: "0.4deg",  scaleX: 0.995, opacity: 0.85 },
  { translateY: "8px",  translateX: "2px",  rotate: "-0.5deg", scaleX: 0.990, opacity: 0.70 },
  { translateY: "11px", translateX: "-2px", rotate: "0.2deg",  scaleX: 0.985, opacity: 0.50 },
  { translateY: "14px", translateX: "1px",  rotate: "-0.2deg", scaleX: 0.980, opacity: 0.30 },
];

export function PaperStack() {
  return (
    <>
      {STACK_SHEETS.map((sheet, i) => (
        <div
          key={i}
          className="absolute inset-0 pointer-events-none transition-transform duration-500 rounded-sm"
          style={{
            background: i % 2 === 0 ? "var(--paper-background-shadowed, #DCD4C6)" : "var(--paper-background, #E6DFD3)",
            border: "1px solid rgba(42, 38, 34, 0.12)",
            borderRadius: "3px",
            // Warm brown shadow (rgba(40, 30, 20)) instead of pure black
            boxShadow: "0 4px 12px rgba(40, 30, 20, 0.18)",
            transform: `translate(${sheet.translateX}, ${sheet.translateY}) rotate(${sheet.rotate}) scaleX(${sheet.scaleX})`,
            zIndex: -1 - i,
            opacity: sheet.opacity,
          }}
        />
      ))}
    </>
  );
}