interface DocumentEnvironmentProps {
  surface?: "desk" | "flat";
  children: React.ReactNode;
}

export function DocumentEnvironment({ surface = "desk", children }: DocumentEnvironmentProps) {
  return (
    <div
      className="min-h-screen w-full"
      style={{
        background: surface === "desk" ? "var(--desk-background)" : "var(--paper-background)",
      }}
    >
      {children}
    </div>
  );
}
