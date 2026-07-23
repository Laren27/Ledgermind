"use client";

import { useState } from "react";

interface SearchBarProps {
  onSubmit: (query: string) => void;
  loading?: boolean;
}

export default function SearchBar({ onSubmit, loading }: SearchBarProps) {
  const [value, setValue] = useState(
    "What was Eternal's revenue growth from FY25 to FY26?"
  );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
      className="flex items-center gap-2.5 max-w-[460px] rounded-2xl border border-[var(--glass-border)] bg-[var(--glass-fill)] py-1.5 pl-5 pr-1.5 backdrop-blur-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] transition-shadow focus-within:border-teal/40 focus-within:shadow-[inset_0_1px_0_rgba(255,255,255,0.05),0_0_0_3px_rgba(62,217,192,0.08)]"
    >
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Ask about FY26 revenue, margins, or filings…"
        className="flex-1 bg-transparent py-3 text-sm text-text-primary placeholder:text-text-muted outline-none font-body"
      />
      <button
        type="submit"
        disabled={loading}
        className="whitespace-nowrap rounded-xl bg-teal px-4.5 py-2.5 text-[13px] font-semibold text-[#06110E] font-display disabled:opacity-60"
      >
        {loading ? "Asking…" : "Ask →"}
      </button>
    </form>
  );
}
