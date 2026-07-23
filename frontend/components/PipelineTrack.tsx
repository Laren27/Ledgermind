interface Stage {
  label: string;
  state: "retrieval" | "verified" | "done";
}

const STAGES: Stage[] = [
  { label: "PDF source", state: "retrieval" },
  { label: "Chunk retrieval", state: "retrieval" },
  { label: "Reranking", state: "retrieval" },
  { label: "DSL compile", state: "verified" },
  { label: "SQL verified", state: "verified" },
  { label: "Response", state: "done" },
];

const NODE_STYLE: Record<Stage["state"], string> = {
  retrieval: "bg-sky shadow-[0_0_10px_rgba(79,184,232,0.45)]",
  verified: "bg-teal shadow-[0_0_10px_rgba(62,217,192,0.5)]",
  done: "bg-text-primary shadow-[0_0_8px_rgba(236,237,239,0.4)] pulse",
};

const CONNECTOR_STYLE: Record<Stage["state"], string> = {
  retrieval: "bg-gradient-to-r from-sky to-sky/10",
  verified: "bg-gradient-to-r from-teal to-teal/10",
  done: "",
};

export default function PipelineTrack({ activeIndex }: { activeIndex?: number }) {
  return (
    <div>
      <div className="mb-5 font-mono text-[11px] uppercase tracking-wide text-text-muted">
        Verification pipeline
      </div>
      <div className="flex items-center rounded-2xl border border-hairline bg-bg-elevated px-7 py-5">
        {STAGES.map((stage, i) => (
          <div key={stage.label} className="flex flex-1 items-center">
            <div className="flex flex-1 flex-col items-center gap-2.5">
              <div
                className={`h-2.5 w-2.5 rounded-full ${NODE_STYLE[stage.state]} ${
                  activeIndex !== undefined && i > activeIndex ? "opacity-30" : ""
                }`}
              />
              <div
                className={`text-xs font-medium font-display ${
                  stage.state === "done" ? "text-text-primary" : "text-text-secondary"
                }`}
              >
                {stage.label}
              </div>
            </div>
            {i < STAGES.length - 1 && (
              <div className={`-mt-5 h-px flex-[0.5] ${CONNECTOR_STYLE[stage.state]}`} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
