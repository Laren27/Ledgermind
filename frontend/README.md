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
- **AnswerCard**: renders `response_text`, `confidence_tier`,
  `sql_verified`, `citations[]` (using the real `reranker_score` field —
  there's no separate similarity score in your schema, so that's gone),
  `contradictions[]`, and handles `is_blocked` (Prompt Shield) and
  `error` (graph failure) as distinct states, not folded into a generic
  error.
- **401 handling**: an expired/invalid token clears the session and
  bounces back to the login screen, rather than silently failing.

## What's still illustrative, not live

- **PipelineTrack**: your `/api/query` endpoint is one synchronous call
  that returns a final result — there's no stage-by-stage event stream
  from the backend, so the pipeline can't authentically animate node-by-
  node per query. It's shown as a static illustration of the process, not
  wired to per-request state. Making it real would mean the backend
  streaming progress events (SSE/WebSocket) — a real feature to design
  later, not a frontend bug.
- **CorpusPanel**: no `/api/corpus-status` endpoint exists yet — numbers
  are hardcoded, matching the earlier decision to keep this static with
  a disclaimer rather than build a live endpoint for it right now.

## File map

```
app/
  page.tsx         — auth gate, search, answer card, pipeline
  layout.tsx       — fonts (Instrument Sans, Manrope, IBM Plex Mono)
components/
  LoginForm.tsx
  SearchBar.tsx
  AnswerCard.tsx        — handles normal / blocked / error states
  ConfidenceBadge.tsx
  PipelineTrack.tsx
  CorpusPanel.tsx        — still static, see above
lib/
  auth.ts          — login, token storage, session check
  api.ts           — submitQuery(), typed to the real QueryResponse
```

## Next steps

1. Seed a test user in Postgres if you don't have one, log in for real.
2. Point `NEXT_PUBLIC_API_URL` at wherever the backend actually runs.
3. Decide whether `role_filtered_response()` trims fields the UI expects
   for `viewer` role — if a viewer never gets `sql_query`/`dsl_object`,
   confirm the UI doesn't break on their absence (it's typed optional,
   but worth a real test with a viewer-role login).
