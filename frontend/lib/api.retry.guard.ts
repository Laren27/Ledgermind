/**
 * GUARD — submitQueryStreaming's retry policy.
 *
 * There is no test runner in this project (package.json has no test script and
 * no vitest/jest dependency), and adding one is outside this change. This file
 * is a standalone executable guard instead:
 *
 *   docker compose exec -T -w /app frontend \
 *     node_modules/.bin/sucrase-node lib/api.retry.guard.ts
 *
 * WHAT IT PINS. api/query.py never cancels the graph on client disconnect, on
 * purpose, so every fallback to submitQuery() is a SECOND full pipeline run:
 * a second LLM spend against a 500/day ceiling and a second audit_log row for
 * one user question, with nothing marking either as a retry. Exactly one
 * failure mode may be retried — the socket dropping after the stream opened.
 *
 * NEGATIVE CONTROLS ARE INLINE. Every assertion is immediately followed by the
 * same claim inverted, wrapped so that it MUST throw. An assertion that cannot
 * fail is not evidence, and a control living in a separate block can drift
 * away from the assertion it is supposed to be guarding. The two counts are
 * compared at the end, so a dropped control is itself a failure.
 */

import {
  submitQueryStreaming,
  UnauthorizedError,
  PipelineError,
  RequestFailedError,
} from "./api";

// ── assertion machinery ────────────────────────────────────────────────────
let positives = 0;
let controls = 0;

function eq(actual: unknown, expected: unknown, label: string): void {
  if (actual !== expected) {
    throw new Error(`${label}: expected ${String(expected)}, got ${String(actual)}`);
  }
}
function assertEq(actual: unknown, expected: unknown, label: string): void {
  positives++;
  eq(actual, expected, label);
}
/** The inverted claim. Fails loudly if it does NOT throw. */
function control(fn: () => void, label: string): void {
  controls++;
  let threw = false;
  try {
    fn();
  } catch {
    threw = true;
  }
  if (!threw) throw new Error(`NEGATIVE CONTROL DID NOT FIRE: ${label}`);
}

// ── environment stubs ──────────────────────────────────────────────────────
const SESSION = JSON.stringify({
  accessToken: "t",
  role: "admin",
  tenantId: "a0000000-0000-0000-0000-000000000001",
  expiresAt: Date.now() + 3600000,
});

(globalThis as any).window = globalThis;
(globalThis as any).localStorage = {
  getItem: () => SESSION,
  setItem: () => {},
  removeItem: () => {},
};

const FALLBACK_PAYLOAD = { request_id: "fallback", companies: [] };

const START_FRAME = 'event: start\ndata: {"request_id":"r"}\n\n';
const ERROR_FRAME = 'event: error\ndata: {"message":"Pipeline execution failed"}\n\n';

/** Body that yields the given SSE text once, then ends (`done`). */
function bodyOf(sse: string) {
  let sent = false;
  return {
    getReader: () => ({
      read: async () => {
        if (sent) return { done: true, value: undefined };
        sent = true;
        return { done: false, value: new TextEncoder().encode(sse) };
      },
    }),
  };
}

/** Body whose reader throws mid-stream, after the stream has started. */
function bodyThatDropsMidRead(sse: string) {
  let stage = 0;
  return {
    getReader: () => ({
      read: async () => {
        stage++;
        if (stage === 1) return { done: false, value: new TextEncoder().encode(sse) };
        throw new TypeError("network error: connection reset");
      },
    }),
  };
}

interface Scenario {
  streamResponse?: any;
  streamThrows?: Error;
}

/**
 * Installs fetch, runs the call, and reports how many times the NON-streaming
 * endpoint was hit. That count is the retry count.
 */
async function run(sc: Scenario): Promise<{ retries: number; error: unknown; value: unknown }> {
  let retries = 0;
  (globalThis as any).fetch = async (url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (path === "/api/query") {
      retries++;
      return { ok: true, status: 200, json: async () => FALLBACK_PAYLOAD };
    }
    if (path === "/api/query/stream") {
      if (sc.streamThrows) throw sc.streamThrows;
      return sc.streamResponse;
    }
    throw new Error(`unexpected fetch: ${path}`);
  };

  let error: unknown = null;
  let value: unknown = null;
  try {
    value = await submitQueryStreaming("q", () => {});
  } catch (e) {
    error = e;
  }
  return { retries, error, value };
}

async function main() {
  // CASE 1 — server-emitted `error` event. The pipeline RAN and failed.
  {
    const r = await run({
      streamResponse: { ok: true, status: 200, body: bodyOf(START_FRAME + ERROR_FRAME) },
    });

    assertEq(r.retries, 0, "error event MUST NOT retry");
    control(() => eq(r.retries, 1, "inverted"), "error event MUST NOT retry [inverted]");

    assertEq(r.error instanceof PipelineError, true, "error event surfaces as PipelineError");
    control(
      () => eq(r.error instanceof PipelineError, false, "inverted"),
      "error event surfaces as PipelineError [inverted]"
    );
  }

  // CASE 2 — !res.ok. Rejected outright, before any stream existed.
  {
    const r = await run({ streamResponse: { ok: false, status: 500, body: null } });

    assertEq(r.retries, 0, "!res.ok MUST NOT retry");
    control(() => eq(r.retries, 1, "inverted"), "!res.ok MUST NOT retry [inverted]");

    const classified =
      r.error instanceof RequestFailedError && (r.error as RequestFailedError).status === 500;
    assertEq(classified, true, "!res.ok surfaces as RequestFailedError carrying its status");
    control(
      () => eq(classified, false, "inverted"),
      "!res.ok surfaces as RequestFailedError carrying its status [inverted]"
    );
  }

  // CASE 3 — socket dropped after `start`, no `complete`. THE retryable case.
  // Both shapes it takes: the reader draining cleanly, and the reader throwing.
  {
    const drained = await run({
      streamResponse: { ok: true, status: 200, body: bodyOf(START_FRAME) },
    });

    assertEq(drained.retries, 1, "dropped socket (drained) MUST retry exactly once");
    control(
      () => eq(drained.retries, 0, "inverted"),
      "dropped socket (drained) MUST retry exactly once [inverted]"
    );

    assertEq(drained.value, FALLBACK_PAYLOAD, "dropped socket resolves with the fallback payload");
    control(
      () => eq(drained.value, null, "inverted"),
      "dropped socket resolves with the fallback payload [inverted]"
    );

    const threwMidRead = await run({
      streamResponse: { ok: true, status: 200, body: bodyThatDropsMidRead(START_FRAME) },
    });

    assertEq(threwMidRead.retries, 1, "dropped socket (mid-read throw) MUST retry exactly once");
    control(
      () => eq(threwMidRead.retries, 0, "inverted"),
      "dropped socket (mid-read throw) MUST retry exactly once [inverted]"
    );
  }

  // CASE 4 — 401. Exempt exactly as before: a dead session is not transport.
  {
    const r = await run({ streamResponse: { ok: false, status: 401, body: null } });

    assertEq(r.retries, 0, "401 MUST NOT retry");
    control(() => eq(r.retries, 1, "inverted"), "401 MUST NOT retry [inverted]");

    assertEq(r.error instanceof UnauthorizedError, true, "401 still surfaces as UnauthorizedError");
    control(
      () => eq(r.error instanceof UnauthorizedError, false, "inverted"),
      "401 still surfaces as UnauthorizedError [inverted]"
    );
  }

  // Every assertion must have kept its control. A control that was deleted or
  // never reached is a hole in the guard, so the counts are themselves pinned.
  eq(positives, controls, "assertion count vs negative-control count");
  if (positives === 0) throw new Error("no assertions ran");

  console.log(
    `GUARD PASS — ${positives} assertions, ${controls} inline negative controls, counts equal`
  );
}

main().catch((e) => {
  console.error("GUARD FAIL —", e instanceof Error ? e.message : e);
  process.exit(1);
});
