interface CitationMarker { index: number; anchorId: string; }
interface AnalysisSectionProps {
  paragraphs: { text: string; citations: CitationMarker[] }[];
}

export function AnalysisSection({ paragraphs }: AnalysisSectionProps) {
  return (
    <div
      className="mb-5"
      style={{
        fontFamily: "var(--font-body)",
        fontSize: "17px",
        lineHeight: 1.82,
        fontWeight: 400,
        letterSpacing: "-0.01em",
        color: "var(--paper-text)",
        textAlign: "justify",
        textJustify: "inter-word",
        maxWidth: "74ch",
        margin: "0 auto",
      }}
    >
      {paragraphs.map((p, i) => (
        <p
          key={i}
          style={{
            marginBottom: "0.85rem",
            textIndent: "1.2rem",
          }}
        >
          {p.text}
          {p.citations.map((c) => (
            <sup key={c.index} style={{ color: "var(--paper-accent)", marginLeft: 1 }}>
              <a href={`#${c.anchorId}`} style={{ color: "inherit" }}>{c.index}</a>
            </sup>
          ))}
        </p>
      ))}
    </div>
  );
}
