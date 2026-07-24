import { getSession, logout } from "./auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// --- Types mirrored exactly from app/api/query.py's Pydantic models ---
// Note: role_filtered_response() trims this per-role before it reaches the
// client, so viewers may receive fewer fields than analysts/admins (e.g.
// dsl_object/sql_query are likely admin/analyst-only). Every field below
// is typed optional/nullable to reflect that — don't assume they're always
// present.

export interface CitationResponse {
  chunk_id: string;
  doc_id: string;
  page_number: number;
  company: string;
  fiscal_year: string;
  financial_type: string;
  filing_date: string;
  reranker_score: number;
  text_preview: string;
}

export interface ContradictionResponse {
  type: string;
  qualitative_claim: string;
  qualitative_source: string;
  quantitative_value: number;
  quantitative_metric: string;
  delta_pct: number | null;
  severity: string;
}

// Added to resolve Vercel build error in CorpusPanel.tsx
export interface CorpusStatus {
  companies: number;
  filings?: number;
  documents?: number;
  total_chunks?: number;
  chunks?: number;
  last_updated?: string;
  status?: string;
  [key: string]: any; // Allows custom backend metric fields without TypeScript compilation errors
}

export interface QueryResponse {
  request_id: string;
  query: string;
  path: string | null;
  is_blocked: boolean;
  block_reason: string | null;

  company: string | null;
  fiscal_year: string | null;
  quarter: string | null;
  financial_type: string;

  response_text: string | null;
  confidence_score: number;
  confidence_tier: "high" | "medium" | "low";
  crag_triggered: boolean;
  crag_count: number;

  citations: CitationResponse[];
  contradictions: ContradictionResponse[];

  dsl_object?: Record<string, unknown> | null;
  sql_query?: string | null;
  sql_result?: Record<string, unknown>[] | null;
  sql_verified: boolean;

  error: string | null;
  error_node: string | null;

  latency_ms: number;
  tokens_used: number;
  cache_hit: boolean;
}

export class UnauthorizedError extends Error {}

/**
 * Sends a natural language query to POST /api/query.
 * Throws UnauthorizedError on 401 (expired/invalid token) — callers should
 * catch this and redirect to login, not treat it like a generic failure.
 */
export async function submitQuery(
  question: string,
  executionContext?: Record<string, any>
): Promise<QueryResponse> {
  const session = getSession();
  if (!session) {
    throw new UnauthorizedError("Not logged in");
  }

  const res = await fetch(`${API_URL}/api/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.accessToken}`,
    },
    body: JSON.stringify({
      query: question,
      execution_context: executionContext ?? null,
    }),
  });

  if (res.status === 401) {
    logout();
    throw new UnauthorizedError("Session expired");
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Query failed (${res.status}): ${detail}`);
  }

  return res.json();
}