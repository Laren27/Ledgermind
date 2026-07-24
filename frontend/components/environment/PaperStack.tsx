import React from "react";

/**
 * LedgerMind Paper Stack
 *
 * Philosophy:
 * - Imperfect but intentional
 * - Top sheet nearly perfect
 * - Lower sheets progressively more offset
 * - Bottom sheet grounds the stack
 * - Warm shadows (not black)
 */

const STACK_SHEETS = [
  {
    x: "0px",
    y: "3px",
    rotate: "-0.08deg",
    width: "100.2%",
    height: "100.2%",
    shadow: "0 3px 8px rgba(58,42,28,.12)",
    opacity: 0.98,
  },
  {
    x: "-1px",
    y: "6px",
    rotate: "0.12deg",
    width: "100.5%",
    height: "100.4%",
    shadow: "0 5px 12px rgba(58,42,28,.14)",
    opacity: 0.94,
  },
  {
    x: "2px",
    y: "10px",
    rotate: "-0.22deg",
    width: "100.8%",
    height: "100.7%",
    shadow: "0 7px 16px rgba(58,42,28,.16)",
    opacity: 0.90,
  },
  {
    x: "-2px",
    y: "15px",
    rotate: "0.18deg",
    width: "101.1%",
    height: "101.0%",
    shadow: "0 10px 22px rgba(58,42,28,.18)",
    opacity: 0.87,
  },
  {
    x: "3px",
    y: "20px",
    rotate: "-0.28deg",
    width: "101.4%",
    height: "101.3%",
    shadow: "0 14px 30px rgba(58,42,28,.20)",
    opacity: 0.84,
  },
  {
    x: "-2px",
    y: "26px",
    rotate: "0.32deg",
    width: "101.8%",
    height: "101.7%",
    shadow: "0 22px 48px rgba(52,36,22,.26)",
    opacity: 0.82,
  },
];

export function PaperStack() {
  return (
    <>
      {STACK_SHEETS.map((sheet, index) => (
        <div
          key={index}
          aria-hidden
          className="absolute inset-0 pointer-events-none rounded-[4px] transition-all duration-700 ease-out"
          style={{
            background:
              index % 2 === 0
                ? "var(--paper-background-shadowed, #E8E1D6)"
                : "var(--paper-background, #F3EEE6)",

            border: "1px solid rgba(55,44,33,.10)",

            width: sheet.width,
            height: sheet.height,

            transform: `
              translate(${sheet.x}, ${sheet.y})
              rotate(${sheet.rotate})
            `,

            boxShadow: sheet.shadow,

            opacity: sheet.opacity,

            zIndex: -(index + 1),

            transformOrigin: "center center",

            willChange: "transform",
          }}
        />
      ))}
    </>
  );
}
