import ConfidenceBadge from "./ConfidenceBadge";
import type { QueryResponse } from "@/lib/api";

const FRIENDLY_ERROR_MESSAGES: Record<string, string> = {
  no_data_found:
    "No data was found for this company, metric, or period. It may not yet be indexed.",
  low_confidence_refusal:
    "The available documents don't fully cover this question.",
  dsl_generation_failed:
    "Couldn't interpret this as a structured data request. Try rephrasing with a specific metric name.",
  sql_execution_failed:
    "The database query failed. This data may not be available yet.",
  ambiguous_result:
    "Multiple matching records were found — try specifying 'consolidated' or 'standalone'.",
};

export default function AnswerCard({ data }: { data: QueryResponse }) {
  // Prompt Shield block — distinct from a low-confidence refusal.
  if (data.is_blocked) {
    return (
      <div className="rounded-card border border-coral/25 bg-card-solid p-7 shadow-floating">
        <div className="mb-3 font-mono text-[11px] text-coral">blocked</div>
        <p className="text-[15px] leading-relaxed text-text-primary">
          {data.block_reason ??
            "This query was blocked by the Prompt Shield (trading/investment advice is not supported)."}
        </p>
      </div>
    );
  }

  // Graph-level error or refusal — prioritize Gemini's response_text if present,
  // fall back to friendly message map, and use raw error string as last resort.
  if (data.error) {
    const displayMessage =
      data.response_text ??
      FRIENDLY_ERROR_MESSAGES[data.error] ??
      data.error;

    return (
      <div className="rounded-card border border-amber/25 bg-card-solid p-7 shadow-floating">
        <div className="mb-3 font-mono text-[11px] text-amber">
          error{data.error_node ? ` · ${data.error_node}` : ""}
        </div>
        <p className="text-[15px] leading-relaxed text-text-primary">
          {displayMessage}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-card border border-hairline bg-card-solid p-7 pb-6 shadow-floating">
      <div className="mb-5 flex items-center justify-between">
        <ConfidenceBadge tier={data.confidence_tier} verified={data.sql_verified} />
        <span className="font-mono text-[10.5px] text-text-muted">
          {data.path ?? "unknown"}
        </span>
      </div>

      {(data.company || data.fiscal_year) && (
        <div className="mb-3 text-xs text-text-secondary">
          {[data.company, data.fiscal_year, data.quarter, data.financial_type]
            .filter(Boolean)
            .join(" · ")}
        </div>
      )}

      <p className="mb-5 text-[15px] leading-relaxed text-text-primary">
        {data.response_text ?? "No response text returned."}
      </p>

      {data.crag_triggered && (
        <div className="mb-4 rounded-lg border border-amber/20 bg-amber/10 px-3 py-2 text-[11.5px] text-amber">
          Corrective RAG triggered ({data.crag_count} {data.crag_count === 1 ? "retry" : "retries"})
        </div>
      )}

      {data.citations.length > 0 && (
        <div className="mb-4 border-t border-hairline pt-4">
          <div className="mb-2.5 text-[11px] uppercase tracking-wide text-text-muted font-display">
            Evidence
          </div>
          {data.citations.map((c) => (
            <div key={c.chunk_id} className="flex items-center justify-between py-1.5 text-[12.5px]">
              <span className="text-text-secondary">
                {c.company} · {c.fiscal_year} · p.{c.page_number}
              </span>
              <span className="font-mono text-[11.5px] text-sky">
                relevance {c.reranker_score.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}

      {data.contradictions.length > 0 && (
        <div className="mb-4 border-t border-hairline pt-4">
          <div className="mb-2.5 text-[11px] uppercase tracking-wide text-coral font-display">
            Contradictions flagged
          </div>
          {data.contradictions.map((c, i) => (
            <div key={i} className="mb-2 text-[12px] text-text-secondary">
              <span className="text-coral">{c.severity}</span> — {c.qualitative_claim}{" "}
              <span className="text-text-muted">({c.qualitative_source})</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {data.sql_verified && (
          <div className="flex items-center gap-1.5 rounded-lg border border-hairline bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-text-secondary">
            <span className="text-teal">✓</span>SQL verified
          </div>
        )}
        {data.citations.length > 0 && (
          <div className="flex items-center gap-1.5 rounded-lg border border-hairline bg-white/[0.03] px-2.5 py-1.5 text-[11px] text-text-secondary">
            <span className="text-teal">✓</span>Source linked
          </div>
        )}
      </div>
    </div>
  );
}