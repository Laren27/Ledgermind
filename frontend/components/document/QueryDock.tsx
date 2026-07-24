"use client";

import { useState } from "react";

interface QueryDockProps {
  onSubmit: (query: string) => void;
  isLoading?: boolean;
  suggestions?: string[];
}

export function QueryDock({ onSubmit, isLoading, suggestions = [] }: QueryDockProps) {
  const [query, setQuery] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    onSubmit(query);
  };

  return (
    <div className="my-8">
      <form onSubmit={handleSubmit} className="relative flex flex-col space-y-3">
        <div className="relative flex items-center">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={isLoading}
            placeholder="Query working paper corpus, verify audited financials, or compare entities..."
            className="w-full rounded-sm py-3.5 pl-5 pr-28 text-sm transition-all focus:outline-none"
            style={{
              backgroundColor: "var(--color-cream-inset, #DFD4C4)",
              border: "1px solid rgba(42, 38, 34, 0.14)",
              boxShadow: "inset 0 1px 3px rgba(32, 22, 16, 0.08)",
              fontFamily: "var(--font-editorial, 'IBM Plex Serif', serif)",
              color: "var(--theme-text-primary, #2A2622)",
            }}
          />
          <button
            type="submit"
            disabled={isLoading || !query.trim()}
            className="absolute right-2 px-4 py-1.5 rounded-sm text-xs font-semibold tracking-wider uppercase transition-all"
            style={{
              backgroundColor: "var(--theme-text-primary, #2A2622)",
              color: "var(--theme-surface-paper, #E7DED0)",
              fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)",
              opacity: isLoading || !query.trim() ? 0.6 : 1,
            }}
          >
            {isLoading ? "Executing..." : "Execute →"}
          </button>
        </div>

        {suggestions.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 pt-1 text-xs" style={{ fontFamily: "var(--font-archival, 'IBM Plex Mono', monospace)", color: "var(--theme-text-muted, #6B6053)" }}>
            <span className="uppercase text-[10px] tracking-wider font-semibold opacity-70">Suggested Queries:</span>
            {suggestions.map((s, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => { setQuery(s); }}
                className="hover:underline text-left text-xs opacity-85 transition-opacity"
              >
                "{s}"
              </button>
            ))}
          </div>
        )}
      </form>
    </div>
  );
}
