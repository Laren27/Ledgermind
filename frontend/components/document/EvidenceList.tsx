interface EvidenceItem {
  index: number;
  label: string;
  page: number;
  relevance: number;
  id: string;
}

export function EvidenceList({ items }: { items: EvidenceItem[] }) {
  if (!items.length) return null;
  return (
    <div className="mt-4 border-t pt-3" style={{ borderColor: "var(--paper-border)", fontFamily: "var(--font-body)", fontSize: 10.5, color: "var(--paper-text-muted)", lineHeight: 1.9 }}>
      {items.map((item) => (
        <div key={item.id} id={item.id}>
          <sup style={{ color: "var(--paper-accent)" }}>{item.index}</sup>{" "}
          {item.label} — p.{item.page}, relevance {item.relevance.toFixed(2)}
        </div>
      ))}
    </div>
  );
}
