interface CitationMarker { index: number; anchorId: string; }
interface AnalysisSectionProps {
  paragraphs: { text: string; citations: CitationMarker[] }[];
}

export function AnalysisSection({ paragraphs }: AnalysisSectionProps) {
  return (
    <div className="mb-5" style={{ fontFamily: "var(--font-body)", fontSize: 13, lineHeight: 1.7, color: "var(--paper-text)" }}>
      {paragraphs.map((p, i) => (
        <p key={i} className="mb-3">
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
