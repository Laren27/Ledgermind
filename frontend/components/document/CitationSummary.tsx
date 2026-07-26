interface CitationSummaryProps {
  count: number;
}

export function CitationSummary({ count }: CitationSummaryProps) {
  if (count === 0) return null;

  return (
    <div
      className="py-2 px-3 text-xs font-medium"
      style={{ color: "var(--ink-metadata)", fontFamily: "var(--font-archival)" }}
    >
      {count} Supporting {count === 1 ? "Citation" : "Citations"}
    </div>
  );
}
