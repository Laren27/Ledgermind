"use client";
import { useState } from "react";

interface PageNavigatorProps {
  current: number;
  total: number;
  onNavigate: (page: number) => void;
}

export function PageNavigator({ current, total, onNavigate }: PageNavigatorProps) {
  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState(String(current));

  function commitJump() {
    const n = parseInt(inputValue, 10);
    if (!isNaN(n) && n >= 1 && n <= total) onNavigate(n);
    else setInputValue(String(current));
    setEditing(false);
  }

  return (
    <div className="mx-auto mt-4 flex items-center justify-center gap-4" style={{ width: "85%", maxWidth: 820 }}>
      <button
        onClick={() => onNavigate(current - 1)}
        disabled={current <= 1}
        style={{ fontFamily: "var(--font-body)", fontSize: 12, color: current <= 1 ? "#3a3530" : "#C9A876" }}
      >
        ← Prior
      </button>

      {editing ? (
        <input
          autoFocus
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onBlur={commitJump}
          onKeyDown={(e) => e.key === "Enter" && commitJump()}
          className="w-10 bg-transparent text-center outline-none"
          style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#EDE5D8", borderBottom: "1px solid #6B6053" }}
        />
      ) : (
        <button
          onClick={() => { setInputValue(String(current)); setEditing(true); }}
          style={{ fontFamily: "var(--font-body)", fontSize: 11, color: "#EDE5D8" }}
        >
          PAGE {current} OF {total}
        </button>
      )}

      <button
        onClick={() => onNavigate(current + 1)}
        disabled={current >= total}
        style={{ fontFamily: "var(--font-body)", fontSize: 12, color: current >= total ? "#3a3530" : "#C9A876" }}
      >
        Next →
      </button>
    </div>
  );
}
