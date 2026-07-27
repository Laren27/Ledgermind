import { getSession, logout } from "./auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

export interface CorpusStatus {
  companies: number;
  filings?: number;
  documents?: number;
  total_chunks?: number;
  chunks?: number;
  last_updated?: string;
  status?: string;
  [key: string]: any;
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

export interface UploadDocumentParams {
  file: File;
  company: string;
  ticker: string;
  fiscalYear: string;
  docType: string;
  filingDate: string;
  quarter?: string;
  version?: string;
}

export interface UploadDocumentResponse {
  doc_id: string;
  pending_id: string;
  status: string;
  gate_score: number;
  message: string;
}

export interface PendingUpload {
  id: string;
  storage_key: string;
  company: string;
  ticker: string;
  fiscal_year: string;
  quarter: string | null;
  doc_type: string;
  filing_date: string;
  version: string;
  status: "pending" | "processing" | "done" | "failed";
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export class UnauthorizedError extends Error {}

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

export async function uploadDocument(
  params: UploadDocumentParams
): Promise<UploadDocumentResponse> {
  const session = getSession();
  if (!session) {
    throw new UnauthorizedError("Not logged in");
  }

  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("company", params.company);
  formData.append("ticker", params.ticker);
  formData.append("fiscal_year", params.fiscalYear);
  formData.append("doc_type", params.docType);
  formData.append("filing_date", params.filingDate);
  if (params.quarter) formData.append("quarter", params.quarter);
  formData.append("version", params.version ?? "v1");

  const res = await fetch(`${API_URL}/api/documents/upload`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
    },
    body: formData,
  });

  if (res.status === 401) {
    logout();
    throw new UnauthorizedError("Session expired");
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Upload failed (${res.status}): ${detail}`);
  }

  return res.json();
}

export async function fetchPendingUploads(): Promise<PendingUpload[]> {
  const session = getSession();
  if (!session) {
    throw new UnauthorizedError("Not logged in");
  }

  const res = await fetch(`${API_URL}/api/documents/pending`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
    },
  });

  if (res.status === 401) {
    logout();
    throw new UnauthorizedError("Session expired");
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Fetching pending uploads failed (${res.status}): ${detail}`);
  }

  const data = await res.json();
  return data.pending_uploads;
}
