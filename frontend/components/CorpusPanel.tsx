import type { CorpusStatus } from "@/lib/api";

const FALLBACK: CorpusStatus = {
  companies: 4,
  filings: 5,
  chunksIndexed: 1021,
  lastIngestedLabel: "2h ago",
};

export default function CorpusPanel({ status }: { status?: CorpusStatus }) {
  const s = status ?? FALLBACK;
  const stats: [string, string][] = [
    [String(s.companies), "Companies"],
    [String(s.filings), "Filings"],
    [s.chunksIndexed.toLocaleString(), "Chunks indexed"],
    [s.lastIngestedLabel, "Last ingested"],
  ];

  return (
    <div className="mt-5 flex gap-5 rounded-xl border border-hairline bg-white/[0.025] px-4.5 py-3.5">
      {stats.map(([num, label]) => (
        <div key={label} className="flex flex-col gap-0.5">
          <span className="font-mono text-[15px] font-medium text-text-primary">
            {num}
          </span>
          <span className="text-[10.5px] uppercase tracking-wide text-text-muted">
            {label}
          </span>
        </div>
      ))}
    </div>
  );
}
