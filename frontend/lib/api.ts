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

export interface QueryResponse {
  request_id: string;
  query: string;
  path: string | null;
  is_blocked: boolean;
  block_reason: string | null;

  // F14 (2026-08-22): the backend emits a LIST and OMITS the scalar `company`
  // entirely -- api/response_shaping.py says "OMITTED, not substituted", because
  // a multi-issuer answer has no single correct value for a scalar field.
  //
  // `[]` IS A LEGAL STATE, not an error: it means no issuer resolved, and
  // retriever._build_filter then drops the company condition and logs a WARNING,
  // so the search ran UNFILTERED across the whole tenant. Rendering a corpus
  // name for that case would assert something the system did not resolve.
  //
  // The old scalar declaration described a field that has not been on the wire
  // since F14 shipped. Confirmed live against Render 2026-08-22: top-level keys
  // include `companies` and do not include `company`. That is why every working
  // paper header read "GENERAL CORPUS ARCHIVE" and every quantitative section
  // heading rendered empty.
  companies: string[];
  fiscal_year: string | null;
  quarter: string | null;
  financial_type: string;

  response_text: string | null;
  confidence_score?: number;
  // OPTIONAL because the backend now OMITS it on a Prompt Shield block.
  // graph.py routes a block straight to audit_writer, so confidence_node never
  // runs and no tier is ever computed; the key is absent rather than carrying
  // the default "low", which was indistinguishable from a measured low.
  // Absent means NOT SCORED. It does not mean low.
  confidence_tier?: "high" | "medium" | "low";
  crag_triggered?: boolean;
  crag_count?: number;

  citations: CitationResponse[];
  contradictions: ContradictionResponse[];

  dsl_object?: Record<string, unknown> | null;
  sql_query?: string | null;
  sql_result?: Record<string, unknown>[] | null;
  sql_verified?: boolean;

  error: string | null;
  error_node?: string | null;

  latency_ms?: number;
  tokens_used?: number;
  // Always false — the semantic cache (blueprint §15) was never built and
  // nothing writes this. Kept in the contract for when it is. Do not render.
  cache_hit?: boolean;
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

/**
 * The pipeline RAN and reported its own failure: the server emitted an SSE
 * `error` event. Re-running reproduces it at double cost, so this never
 * retries. api/query.py sends this once headers are gone and a 500 is no
 * longer possible, so it is the only way a mid-flight pipeline failure can
 * reach us.
 */
export class PipelineError extends Error {}

/** HTTP-level failure BEFORE the stream began (non-2xx, or a 2xx with no body). */
export class RequestFailedError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** The connection never established, or failed before a single byte arrived. */
export class TransportError extends Error {}

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

export interface TraceEvent {
  node: string;
  label: string;
  status: string;
  detail?: string;
  duration_ms?: number;
}

/**
 * Streams the query pipeline over SSE, reporting each LangGraph node as it
 * completes, and resolves with the same payload POST /api/query returns.
 *
 * Uses fetch + ReadableStream rather than EventSource: EventSource is GET-only
 * and cannot set an Authorization header, and moving the JWT into a query
 * string would put it in server access logs and browser history.
 *
 * RETRIES EXACTLY ONE CASE: the socket dropped after the stream started and
 * before `complete` arrived. Nothing else falls back.
 *
 * The previous version retried every failure, and the cost was not
 * theoretical. api/query.py deliberately never cancels the graph task on
 * client disconnect ("the pipeline must still finish so audit_writer_node
 * writes its row"), so one user question became two full pipeline runs, two
 * LLM spends against a 500/day ceiling, and two audit_log rows with nothing
 * marking either as a retry. A server-emitted `error` event and a non-2xx are
 * both pipeline failures: re-running reproduces them and doubles the bill.
 *
 * Non-retried failures surface as distinct classes so a caller can tell a
 * refusal from an outage from a dead connection.
 */
export async function submitQueryStreaming(
  question: string,
  onNode: (event: TraceEvent) => void,
  executionContext?: Record<string, any>
): Promise<QueryResponse> {
  const session = getSession();
  if (!session) {
    throw new UnauthorizedError("Not logged in");
  }

  // Set the moment a readable body is in hand. Distinguishes "the stream
  // started and then died" (retryable) from "we never connected" (not).
  let streamStarted = false;
  let outcome: QueryResponse | "retry";

  try {
    const res = await fetch(`${API_URL}/api/query/stream`, {
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
    if (!res.ok || !res.body) {
      throw new RequestFailedError(`Stream failed (${res.status})`, res.status);
    }

    streamStarted = true;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: QueryResponse | null = null;
    let streamError: string | null = null;

    // SSE frames are separated by a blank line. Partial frames stay in the
    // buffer until their terminator arrives -- a chunk boundary can land
    // anywhere, including mid-JSON.
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        let eventName = "message";
        const dataLines: string[] = [];

        for (const line of frame.split("\n")) {
          if (line.startsWith(":")) continue; // heartbeat comment
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length === 0) continue;

        let payload: any;
        try {
          payload = JSON.parse(dataLines.join("\n"));
        } catch {
          continue; // malformed frame: skip it rather than kill the stream
        }

        if (eventName === "node") onNode(payload as TraceEvent);
        else if (eventName === "complete") result = payload as QueryResponse;
        else if (eventName === "error") streamError = payload?.message ?? "Pipeline error";
      }
    }

    if (streamError) throw new PipelineError(streamError);

    // Reader drained with no `complete`: the connection ended mid-pipeline.
    // THE ONE RETRYABLE CASE -- nothing reported a failure, the bytes just
    // stopped arriving.
    outcome = result ?? "retry";
  } catch (err) {
    // A dead session is a real failure, not a transport problem -- retrying
    // over the non-streaming endpoint would just 401 again.
    if (err instanceof UnauthorizedError) throw err;
    // The pipeline ran and failed. Re-running reproduces the failure and
    // spends a second LLM budget and a second audit row to do it.
    if (err instanceof PipelineError) throw err;
    // The server rejected the request outright. Same reasoning.
    if (err instanceof RequestFailedError) throw err;
    // Threw before a single byte: DNS, refused connection, TLS. There is no
    // stream to have dropped, and submitQuery would hit the same wall.
    if (!streamStarted) {
      throw new TransportError(
        err instanceof Error ? err.message : "Could not reach the query endpoint"
      );
    }
    // reader.read() threw part-way through -- a dropped socket by another name.
    outcome = "retry";
  }

  // Outside the try on purpose: a throw from this call must reach the caller,
  // not be caught by the block above and retried a second time.
  if (outcome === "retry") return submitQuery(question, executionContext);
  return outcome;
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
