# LedgerMind — Operational Runbook

Environment and invocation notes. Not architecture (see ARCHITECTURE.md) and not
spec divergence (see IMPLEMENTATION_DELTAS.md) — this file exists because the
local stack's CONFIGURATION has cost more diagnosis time than the application
code has. Every entry below is a real incident, not a precaution.

---

## Running the stack

`docker compose up -d --build` is the ONE correct way. A backgrounded local
`uvicorn` has caused multi-hour false-regression chases; if something looks
wrong, `lsof -i :8000` before forming any theory.

**Wait for readiness before probing.** `up -d` returns when the container
STARTS, not when uvicorn binds the port. On 2026-08-02 a probe run immediately
after a rebuild got `ConnectionRefused` and looked exactly like an import error
killing the app — the logs showed a clean startup and the container was 28
seconds old.

    docker compose up -d --build
    until docker compose exec -T backend python -c \
      "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=2)" \
      2>/dev/null; do sleep 2; done; echo READY

Changed `env_file` values need `--force-recreate`, or verify directly:
`docker compose exec -T backend printenv GEMINI_MODEL QDRANT_URL`.

**Before any measurement or sweep, check QDRANT_URL.** It must be the https
Cloud URL. If it reads `http://qdrant:6333` you are on a small local collection
and nothing measured against it counts — this invalidated every local
measurement taken before 2026-08-01. The tell is a qdrant_client warning about
an api-key on an insecure connection: Cloud is HTTPS-only, so that warning means
local Docker.

---

## Script invocation

Scripts import `app.*`, so they must run as MODULES, not paths. `python
scripts/X.py` puts `/app/scripts` on `sys.path` instead of `/app` and fails with
`ModuleNotFoundError: No module named 'app'`.

    docker compose exec -T backend python -m scripts.regression_check
    docker compose exec -T backend python -m scripts.process_pending_uploads --once

**eval_runner is the exception — run it from the HOST**, in `backend/`, with
`../golden_dataset/` paths. It is an HTTP client and does not need to be inside
the container; the container has no golden datasets mounted (`/app/golden_dataset/`
holds eval OUTPUTS only). Its `--api-base` defaults to `http://localhost:8000`,
which is the local stack — confirm that is the intended target, since code
committed to git is not deployed to Render.

    cd ~/ledgermind/backend && python scripts/eval_runner.py \
      --model gemini-3.1-flash-lite \
      --dataset ../golden_dataset/q_paytm.json \
      --delay 25 --out ../golden_dataset/eval_paytm.json

---

## regression_check: run ONCE, tee, then grep the file

Each run re-parses all four corpus PDFs including the 371-page Zomato annual
report. Re-running it to view a different slice of the same output is what
exhausted WSL2's RAM on 2026-08-02.

    docker compose exec -T backend python -m scripts.regression_check 2>&1 \
      | tee /tmp/regcheck.log | tail -12

    grep -A 4 "Records extracted" /tmp/regcheck.log
    grep "FINANCIAL_STATEMENT pages" /tmp/regcheck.log
    grep "Derivation overwrites" -A 4 /tmp/regcheck.log

The long `Unknown metric: ... storing as-is` stream is the documented
as-is storage path, not errors.

---

## WSL2 DNS

The WSL2 DNS proxy (`nameserver 10.255.255.254`) intermittently fails to answer,
presenting EITHER as an instant `[Errno -5]/[Errno -3]` OR as an ~8s hang. Both
have been misread as application bugs: once as a retrieval regression, once as
120s read-timeouts.

**The signature:** `hybrid_search` catches the exception and returns `[]`, so the
user sees a low-confidence refusal indistinguishable from a genuine retrieval
miss. An EMPTY candidate set is a network signature; a LOW-SCORING one is a
retrieval signature. Check which before theorising. A second tell is
`UserWarning: Failed to obtain server version` from qdrant_client — the client
failed its construction-time probe and the next query in that process dies.

Fixed in two places, and **verified durable across an actual `wsl --shutdown` on
2026-08-02**:

- Containers: `dns: [8.8.8.8, 1.1.1.1]` on `backend` and `worker` in
  docker-compose.yml (scheduler untouched, Redis-only).
- Host: `/etc/wsl.conf` has `generateResolvConf=false`; `/etc/resolv.conf` is
  pinned to 8.8.8.8/1.1.1.1 and held immutable with `chattr +i`. A later `tee`
  to that file failing with "Operation not permitted" is EXPECTED, not a bug.

Verify after a reboot: `cat /etc/resolv.conf && lsattr /etc/resolv.conf` —
expect the two nameservers and the `i` flag. Container lookups should be
~0.06s; a 4s stall against generate_structured's 8s timeout consumes half the
budget before the request starts.

---

## A local semantic failure is not a defect until it reproduces on a WARM process

Every hand-probe via `docker compose exec` is a fresh process carrying ~30s of
cold fastembed/ONNX model load. That is expected first-call cost. Warm calls are
0.36-0.41s. Never judge performance or tune thresholds off local timings at all:
semantic_engine runs ~1283ms on Render (Cohere) against ~18233ms locally (ONNX
on WSL2).

---

## Eval quota

Gemini free tier is 5 RPM and 500/day PER MODEL. A semantic question makes TWO
calls (router + synthesis), so **`--delay 25`**. Runs at 15 are over budget by
construction (~8 RPM) — they have survived on luck and have also caused a
withheld sweep.

Run the LARGEST dataset first as a gate. Read **Providers, then Models served,
then the score** — in that order. If either gate fires the score is withheld;
stop rather than annotate. A full three-dataset sweep is ~165 calls.
