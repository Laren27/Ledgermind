interface SectionHeadingProps {
  children: React.ReactNode;
  sourceTable?: string;
}

export function SectionHeading({ children, sourceTable }: SectionHeadingProps) {
  return (
    <div
      className="mb-3 flex items-baseline justify-between border-b pb-2"
      style={{ borderColor: "var(--paper-rule-single)" }}
    >
      <span style={{ fontFamily: "var(--font-document-title)", fontSize: 15, color: "var(--paper-text)" }}>
        {children}
      </span>
      {sourceTable && (
        <span style={{ fontFamily: "var(--font-body)", fontSize: 10.5, color: "var(--paper-text-muted)" }}>
          Source Table: {sourceTable}
        </span>
      )}
    </div>
  );
}
