export function DocumentTitle({ children }: { children: React.ReactNode }) {
  return (
    <h1
      className="mb-1"
      style={{ fontFamily: "var(--font-document-title)", fontSize: 26, color: "var(--paper-text)" }}
    >
      {children}
    </h1>
  );
}
