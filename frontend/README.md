# LedgerMind Frontend

Wired against the real Phase 5 backend contract (`app/api/query.py`,
`app/auth/*`) — not mocked data anymore.

## Setup

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

You'll need a seeded user (email/password) in your `users` table to log in
— the login screen hits `POST /auth/login` for real.

## What's real now

- **Login**: `lib/auth.ts` calls `POST /auth/login`, stores the JWT +
  role + tenant_id in localStorage (see the security note in that file —
  fine for solo/local use, would need an httpOnly cookie for anything
  with real users).
- **Query**: `lib/api.ts`'s `submitQuery()` calls `POST /api/query` with
  `Authorization: Bearer <token>`, typed against the exact `QueryResponse`
  Pydantic model from your backend — no invented fields.
- **Answer rendering**: `composeDocumentBody()` in `app/page.tsx` is the
  single place aware of path/engine internals. It renders `response_text`,
  `citations[]` (on the real `reranker_score` — there is no separate
  similarity score in the schema), `contradictions[]`, and handles
  `is_blocked` (Prompt Shield) and `error` (graph failure) as distinct
  states rather than one generic error. `confidence_tier` is optional on
  the wire: the backend omits it on a Prompt Shield block, because the
  confidence node never runs there, and absent means not scored — it does
  not mean low.
- **Streaming**: `submitQueryStreaming()` reads `POST /api/query/stream`
  as SSE and reports each LangGraph node as it completes; `ExecutionTrace`
  renders those events. Only a dropped socket falls back to the
  non-streaming endpoint — a server-side pipeline error and a non-2xx are
  surfaced as distinct error classes rather than re-run.
- **401 handling**: an expired/invalid token clears the session and
  bounces back to the login screen, rather than silently failing.

## What is not wired, and says so on screen

Both of the components this section used to describe have been deleted.
`PipelineTrack` was superseded by `ExecutionTrace`, which is driven by
real SSE node events. `CorpusPanel` was removed rather than kept as a
static placeholder: every value it displayed was a literal, and its
real-data path could not have worked — its fallback object used two
fields its own type never declared, and typechecked only because that
type carried an `any` index signature.

What remains unwired is now stated in the UI instead of in this file:

- **Sidebar → Active Corpus**: renders "not available — no endpoint
  reports filing ingestion state". No endpoint returns
  `documents.ingestion_state`. `GET /api/documents/pending` is a different
  table with a different value domain and is admin-only, so it is not a
  substitute.
- **Audit Trail**: labelled "Current Session Only · Not Persisted". It is
  built from React state for this browser tab. The real `audit_log` is
  unreachable — no endpoint returns its rows.

## File map

```
app/
  page.tsx         — auth gate, view routing, composeDocumentBody()
  layout.tsx       — Fraunces, IBM Plex Sans, IBM Plex Mono via next/font
  globals.css      — THE palette: primitives + --theme-* aliases
components/
  LoginForm.tsx
  document/        — the working-paper UI (Sidebar, QueryDock,
                     DocumentPage, ExecutionTrace, AuditLogTable,
                     UploadPanel, WorkingPaperHeader, tables, …)
  document/globals.css — paper-surface tokens, imported by layout.tsx
  environment/     — desk background, lighting, paper stack
lib/
  auth.ts          — login, token storage, session check
  api.ts           — submitQuery(), submitQueryStreaming(), typed to the
                     real response shape
  api.retry.guard.ts — executable guard for the retry policy; run with
                     sucrase-node (there is no test runner here)
```

## Next steps

1. Seed a test user in Postgres if you don't have one, log in for real.
2. Point `NEXT_PUBLIC_API_URL` at wherever the backend actually runs.
3. Decide whether `role_filtered_response()` trims fields the UI expects
   for `viewer` role — if a viewer never gets `sql_query`/`dsl_object`,
   confirm the UI doesn't break on their absence (it's typed optional,
   but worth a real test with a viewer-role login).
