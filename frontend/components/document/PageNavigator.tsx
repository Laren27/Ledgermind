"use client";
import React, { useState } from "react";

interface PageNavigatorProps {
  current: number;
  total: number;
  onNavigate: (page: number) => void;
  onShiftStart?: () => void;
  onShiftEnd?: () => void;
}

export function PageNavigator({ current, total, onNavigate, onShiftStart, onShiftEnd }: PageNavigatorProps) {
  const [disabled, setDisabled] = useState(false);

  if (total <= 1) return null;

  const handlePageChange = (targetPage: number) => {
    if (disabled || targetPage === current) return;
    setDisabled(true);
    if (onShiftStart) onShiftStart();

    // 💡 240ms physical paper slide duration
    setTimeout(() => {
      onNavigate(targetPage);
      if (onShiftEnd) onShiftEnd();
      setDisabled(false);
    }, 240);
  };

  return (
    <div className="flex items-center justify-center pb-12 pt-4 select-none z-20">
      {/* ENGRAVED DESKTOP TRAY CONTROL (Anchored to Walnut Desk) */}
      <div 
        className="flex items-center space-x-8 px-8 py-2.5 rounded-full border transition-all"
        style={{
          background: "rgba(16, 13, 11, 0.96)",
          backdropFilter: "blur(8px)",
          borderColor: "rgba(255, 255, 255, 0.06)",
          boxShadow: "0 8px 24px rgba(0, 0, 0, 0.55), inset 0 1px 1px rgba(255, 255, 255, 0.05)",
          fontFamily: "var(--font-archival, monospace)",
          fontSize: "14.5px",
        }}
      >
        <button
          onClick={() => handlePageChange(Math.max(1, current - 1))}
          disabled={current <= 1 || disabled}
          className="transition-colors text-[#8B8378] hover:text-[#E7DED0] disabled:opacity-25 disabled:hover:text-[#8B8378] font-medium flex items-center space-x-2 tracking-wide"
        >
          <span>←</span>
          <span>Previous Sheet</span>
        </button>

        <span className="text-[#8B8378] font-semibold tracking-widest uppercase text-xs tabular-metrics">
          Sheet <span className="text-[#E7DED0]">{current}</span> / {total}
        </span>

        <button
          onClick={() => handlePageChange(Math.min(total, current + 1))}
          disabled={current >= total || disabled}
          className="transition-colors text-[#8B8378] hover:text-[#E7DED0] disabled:opacity-25 disabled:hover:text-[#8B8378] font-medium flex items-center space-x-2 tracking-wide"
        >
          <span>Next Sheet</span>
          <span>→</span>
        </button>
      </div>
    </div>
  );
}
