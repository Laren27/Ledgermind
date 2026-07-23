"use client";
import { useState } from "react";

interface QueryDockProps {
  onSubmit: (query: string) => void;
  isLoading: boolean;
  suggestions?: string[];
}

export function QueryDock({ onSubmit, isLoading, suggestions }: QueryDockProps) {
  const [value, setValue] = useState("");

  function handleSubmit() {
    if (!value.trim() || isLoading) return;
    onSubmit(value.trim());
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="mb-8">
      <div
        className="flex items-center gap-3 rounded-md px-4 py-3"
        style={{
          background: "rgba(255,255,255,0.35)",
          backdropFilter: "blur(14px)",
          border: "1px solid rgba(42,38,34,0.12)",
          boxShadow: "0 12px 30px rgba(0,0,0,0.12)",
        }}
      >
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="What was Eternal's YoY consolidated revenue growth in Q4FY26?"
          disabled={isLoading}
          className="flex-1 bg-transparent outline-none"
          style={{
            fontFamily: "var(--font-document-title)",
            fontStyle: "italic",
            fontSize: 14,
            color: "var(--paper-text)",
          }}
        />
        <button
          type="button"
          onClick={handleSubmit}
          disabled={isLoading}
          style={{
            fontFamily: "var(--font-body)",
            fontSize: 12,
            color: "var(--paper-accent)",
            whiteSpace: "nowrap",
            opacity: isLoading ? 0.5 : 1,
          }}
        >
          {isLoading ? "Executing…" : "Execute →"}
        </button>
      </div>

      {suggestions && suggestions.length > 0 && (
        <div className="mt-2 flex gap-4" style={{ fontFamily: "var(--font-body)", fontSize: 10.5, color: "var(--paper-text-muted)" }}>
          <span>SUGGESTED:</span>
          {suggestions.map((s, i) => (
            <button key={i} type="button" onClick={() => setValue(s)} style={{ textDecoration: "underline", textUnderlineOffset: 2 }}>
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
