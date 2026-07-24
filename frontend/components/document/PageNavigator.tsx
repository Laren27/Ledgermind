"use client";
import React from "react";

interface PageNavigatorProps {
  current: number;
  total: number;
  onNavigate: (page: number) => void;
}

export function PageNavigator({ current, total, onNavigate }: PageNavigatorProps) {
  if (total <= 1) return null;

  return (
    <div className="flex items-center justify-center pb-12 pt-2 select-none z-20">
      {/* 💡 ENGRAVED DESKTOP TRAY CONTROL (Anchored to Walnut Environment) */}
      <div 
        className="flex items-center space-x-8 px-8 py-3 rounded-full border transition-all"
        style={{
          background: "rgba(18, 15, 13, 0.92)",
          backdropFilter: "blur(8px)",
          borderColor: "rgba(255, 255, 255, 0.06)",
          boxShadow: "0 8px 24px rgba(0, 0, 0, 0.50), inset 0 1px 1px rgba(255, 255, 255, 0.05)",
          fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)",
          fontSize: "15px",
        }}
      >
        <button
          onClick={() => onNavigate(Math.max(1, current - 1))}
          disabled={current <= 1}
          className="transition-colors text-[#6E6458] hover:text-[#E7DED0] disabled:opacity-25 disabled:hover:text-[#6E6458] font-medium flex items-center space-x-2 tracking-wide"
        >
          <span>←</span>
          <span>Previous</span>
        </button>

        <span className="text-[#8B8378] font-semibold tracking-widest uppercase text-xs">
          Page <span className="text-[#E7DED0]">{current}</span> / {total}
        </span>

        <button
          onClick={() => onNavigate(Math.min(total, current + 1))}
          disabled={current >= total}
          className="transition-colors text-[#6E6458] hover:text-[#E7DED0] disabled:opacity-25 disabled:hover:text-[#6E6458] font-medium flex items-center space-x-2 tracking-wide"
        >
          <span>Next</span>
          <span>→</span>
        </button>
      </div>
    </div>
  );
}
