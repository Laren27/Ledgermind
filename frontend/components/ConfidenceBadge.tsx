interface ConfidenceBadgeProps {
  tier: "high" | "medium" | "low";
  verified?: boolean;
}

const TIER_STYLES = {
  high: { bg: "bg-teal-dim", text: "text-teal", border: "border-teal/25", dot: "bg-teal shadow-[0_0_6px_#3ED9C0]" },
  medium: { bg: "bg-amber/10", text: "text-amber", border: "border-amber/25", dot: "bg-amber" },
  low: { bg: "bg-coral/10", text: "text-coral", border: "border-coral/25", dot: "bg-coral" },
};

const TIER_LABEL = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export default function ConfidenceBadge({ tier, verified }: ConfidenceBadgeProps) {
  const s = TIER_STYLES[tier];
  const label = verified ? "SQL verified" : TIER_LABEL[tier];

  return (
    <div
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11.5px] font-medium font-display ${s.bg} ${s.text} ${s.border}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full pulse ${s.dot}`} />
      {label}
    </div>
  );
}
