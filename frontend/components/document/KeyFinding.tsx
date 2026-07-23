interface KeyFindingProps {
  label?: string;
  children: React.ReactNode;
}

export function KeyFinding({ label = "Conclusion", children }: KeyFindingProps) {
  return (
    <div className="mb-4" style={{ borderLeft: "2px solid var(--paper-accent)", paddingLeft: 12 }}>
      <div style={{ fontFamily: "var(--font-body)", fontSize: 10, textTransform: "uppercase", color: "var(--paper-accent)", marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontFamily: "var(--font-document-title)", fontStyle: "italic", fontSize: 15, color: "var(--paper-text)" }}>
        {children}
      </div>
    </div>
  );
}
