"use client";

import { useState } from "react";

interface QueryDockProps {
  onSubmit: (query: string) => void;
  isLoading?: boolean;
  suggestions?: string[];
}

const DEFAULT_SUGGESTIONS = [
  "Compare PAT growth between Eternal and Paytm",
  "Which company expanded EBITDA margin faster in FY26?",
  "Compare consolidated revenue CAGR",
  "Analyze operating cash flow conversion",
];

export function QueryDock({ onSubmit, isLoading, suggestions = DEFAULT_SUGGESTIONS }: QueryDockProps) {
  const [query, setQuery] = useState("");
  const activeSuggestions = suggestions.length > 0 ? suggestions : DEFAULT_SUGGESTIONS;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    onSubmit(query);
  };

  return (
    <div className="my-10">
      <form onSubmit={handleSubmit} className="relative flex flex-col space-y-4">
        <div className="relative flex items-center">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={isLoading}
            placeholder="Query working paper corpus, verify audited financials, or compare entities..."
            className="w-full rounded-sm py-4 pl-6 pr-32 transition-all focus:outline-none"
            style={{
              backgroundColor: "var(--color-cream-inset, #DFD4C4)",
              border: "1px solid var(--ink-divider, #D8CEC1)",
              boxShadow: "inset 0 2px 5px rgba(42, 36, 30, 0.08)",
              fontFamily: "var(--font-editorial, 'Fraunces', Georgia, serif)",
              fontSize: "var(--font-size-input, 19px)",
              color: "var(--ink-primary, #2A241E)",
            }}
          />
          <button
            type="submit"
            disabled={isLoading || !query.trim()}
            className="absolute right-2.5 px-5 py-2.5 rounded-sm text-xs font-semibold tracking-wider uppercase transition-all"
            style={{
              backgroundColor: "var(--ink-primary, #2A241E)",
              color: "var(--theme-surface-paper, #E7DED0)",
              fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)",
              opacity: isLoading || !query.trim() ? 0.5 : 1,
            }}
          >
            {isLoading ? "Auditing..." : "Execute →"}
          </button>
        </div>

        {/* Suggested Investigations — Archival Analyst Pills */}
        <div className="pt-2">
          <div 
            className="uppercase text-[11px] tracking-[0.14em] font-semibold mb-2.5"
            style={{ fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)", color: "var(--ink-metadata, #8B8378)" }}
          >
            Suggested Investigations:
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {activeSuggestions.map((s, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => { setQuery(s); }}
                className="px-3.5 py-1.5 rounded text-[15px] font-medium transition-all text-left border hover:border-[#8B8378] hover:bg-[#DFD4C4]/60"
                style={{
                  fontFamily: "var(--font-ui, 'IBM Plex Sans', sans-serif)",
                  color: "var(--ink-secondary, #5F574D)",
                  backgroundColor: "rgba(223, 212, 196, 0.35)",
                  borderColor: "rgba(216, 206, 193, 0.8)",
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </form>
    </div>
  );
}
