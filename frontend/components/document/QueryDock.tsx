"use client";

import { useState } from "react";

interface QueryDockProps {
  onSubmit: (query: string) => void;
  isLoading?: boolean;
  suggestions?: string[];
}

const INDEX_TABS = [
  "What was Eternal's revenue growth in FY26?",
  "What is Paytm's PAT for FY26?",
  "What were Titan's key risk factors in Q1FY26?",
  "Summarize Eternal's management commentary on profitability",
];

export function QueryDock({ onSubmit, isLoading, suggestions = INDEX_TABS }: QueryDockProps) {
  const [query, setQuery] = useState("");
  const activeTabs = suggestions.length > 0 ? suggestions : INDEX_TABS;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    onSubmit(query);
  };

  return (
    <div className="my-8 space-y-8">
      {/* 💡 NOTEBOOK BASELINE INPUT (No Box Border, Apple Notes / Notion Aesthetic) */}
      <form onSubmit={handleSubmit} className="relative">
        <div 
          className="relative flex items-baseline justify-between border-b pb-3 transition-colors focus-within:border-[#8B8378]"
          style={{ borderColor: "var(--ink-divider, #D8CEC1)" }}
        >
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={isLoading}
            placeholder="Ask the filing..."
            className="w-full bg-transparent border-none p-0 focus:outline-none transition-all"
            style={{
              fontFamily: "var(--font-editorial, 'Fraunces', Georgia, serif)",
              fontSize: "var(--font-size-input, 20px)",
              color: "var(--ink-primary, #2A241E)",
            }}
          />
          <button
            type="submit"
            disabled={isLoading || !query.trim()}
            className="ml-4 shrink-0 px-3 py-1 text-xs font-semibold tracking-[0.16em] uppercase transition-all duration-200"
            style={{
              color: isLoading || !query.trim() ? "var(--ink-passive, #B7AEA3)" : "var(--ink-primary, #2A241E)",
              fontFamily: "var(--font-archival, monospace)",
            }}
          >
            {isLoading ? "Executing..." : "Execute →"}
          </button>
        </div>
      </form>

      {/* 💡 ARCHIVAL INDEX TABS (Clipped Paper Labels, No Button Styling) */}
      <div className="space-y-2.5">
        <div 
          className="uppercase text-[11px] tracking-[0.16em] font-medium"
          style={{ fontFamily: "var(--font-archival, monospace)", color: "var(--ink-metadata, #8B8378)" }}
        >
          Suggested Investigations
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          {activeTabs.map((tab, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => { setQuery(tab); }}
              className="px-3.5 py-1.5 rounded-sm text-[14.5px] font-normal transition-all duration-150 text-left border select-none hover:-translate-y-[1px]"
              style={{
                fontFamily: "var(--font-ui, sans-serif)",
                color: "var(--ink-secondary, #5F574D)",
                backgroundColor: "rgba(223, 212, 196, 0.35)",
                borderColor: "rgba(216, 206, 193, 0.8)",
                boxShadow: "0 1px 2px rgba(42, 36, 30, 0.04)",
              }}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
