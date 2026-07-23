interface MetricCalloutProps {
  label: string;
  value: string;
  status?: "verified" | "estimated" | "refused";
}

const STATUS_COLOR: Record<string, string> = {
  verified: "var(--paper-verified)",
  estimated: "#B08A3E",
  refused: "#A0453F",
};

export function MetricCallout({ label, value, status }: MetricCalloutProps) {
  return (
    <div className="mb-4">
      <div style={{ fontFamily: "var(--font-body)", fontSize: 10, textTransform: "uppercase", color: "var(--paper-text-muted)" }}>
        {label}
      </div>
      <div style={{ fontFamily: "var(--font-body)", fontSize: 20, color: "var(--paper-text)", marginTop: 2 }}>
        {value}
        {status && (
          <span style={{ marginLeft: 8, fontSize: 13, color: STATUS_COLOR[status] }}>
            {status === "verified" ? "✓" : status === "refused" ? "!" : "~"}
          </span>
        )}
      </div>
    </div>
  );
}
