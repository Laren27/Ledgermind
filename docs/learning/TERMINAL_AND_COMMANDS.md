# LedgerMind — Terminal and Commands

**This is not a cheat sheet.** A list of commands that worked is what you
already have. The goal here is to understand *what the terminal is actually
doing* when you type one.

**How this file grows.** Commands are introduced on the course day that needs
them, then collected here. The schedule below is fixed; the entries fill in as
days land. Sections marked *(pending Day N)* are empty on purpose — an entry
written before its day would be a cheat sheet again.

Opened 2026-08-23. Every command recorded here has been **run against this
repository**, not copied from documentation.

---

# Part 0 — What the terminal actually is

Read this once, before Day 1. It is the part that makes the rest stop being
memorisation.

## The shell is a program that starts other programs

When you type `ls -la`, nothing magic happens. The shell:

1. **Parses** your line into a command (`ls`) and arguments (`-la`).
2. **Expands** anything expandable — `*` becomes matching filenames, `$VAR`
   becomes its value, `$(cmd)` becomes that command's output. **This happens
   before `ls` ever runs.** `ls` never sees a `*`.
3. **Finds** the program: searches each directory in `$PATH`, in order, for an
   executable named `ls`.
4. **Forks** — makes a copy of itself — and **execs** — replaces that copy's
   memory with the `ls` program.
5. **Waits** for it to exit, and collects its **exit code**: `0` means success,
   anything else means failure.

Almost every confusing terminal moment is one of those five steps doing
something you did not expect. Usually step 2 or step 3.

## Three streams, not one

Every program starts with three open channels:

| Stream | Number | Default | Purpose |
|---|---|---|---|
| stdin | 0 | keyboard | input |
| stdout | 1 | screen | **the output** |
| stderr | 2 | screen | **the complaints** |

They look identical on screen and are completely separate. This is why
`command > file.txt` can leave errors on screen (they went to stderr) and why
`2>/dev/null` silences errors while keeping output.

`|` connects one program's **stdout** to the next program's **stdin**. That is
all a pipe is.

```bash
docker compose logs backend | grep ERROR | head -20
#      stdout ────────────►  stdout ──►  stdout ──► screen
```

## Environment variables are inherited, not global

A process gets a **copy** of its parent's environment. Setting a variable in a
child never affects the parent. This is why:

- `docker compose exec backend printenv GEMINI_MODEL` tells you what the
  **container** sees, which may differ from your shell.
- A variable set in one `docker compose exec` is gone in the next — each is a
  new process.
- `.env` is read by *compose*, which passes values into containers. Your shell
  never sees them.

## Exit codes are the contract

`0` = success. Non-zero = failure. This is what `&&` (run next only on success)
and `||` (run next only on failure) test, and what CI reads. A script that
prints "ERROR" and exits `0` has lied to every tool above it.

## Where you are matters

A relative path (`backend/app/main.py`) resolves from the **current working
directory**. This project has been bitten by that: `eval_runner`'s old `--out`
default resolved differently depending on where it was invoked from, and
`os.makedirs` then *created* the phantom directory.

Absolute paths (`/app/tests`) do not have this problem. Prefer them in anything
you will not run interactively.

---

# Part 1 — Shell basics · *(Day 1–2)*

## Navigating and looking

| Command | What it means | What it actually does |
|---|---|---|
| `pwd` | print working directory | asks the kernel for this process's cwd |
| `ls -la` | list, long, all | reads a directory's entries, `stat`s each |
| `cd <dir>` | change directory | changes **this shell's** cwd; a builtin, not a program |
| `cat <file>` | concatenate | reads the whole file to stdout — bad on a 4 GB log |
| `less <file>` | pager | reads lazily; `q` quits, `/` searches |
| `head -20` / `tail -20` | first/last N lines | `tail -f` follows a growing file |
| `wc -l` | count lines | counts newline bytes |

## Finding

| Command | Use |
|---|---|
| `find . -name '*.py'` | by **filename** |
| `grep -rn 'pattern' <dir>` | by **content**, recursive, with line numbers |
| `grep -c` | count matches |
| `grep -A 5 -B 5` | with context |

> **In this repo.** `grep -n` is the required verification after **every** edit.
> `CLAUDE.md` §3: an AST parse proves a file loads, not that an edit landed —
> and it does not compile regexes, so use `python -c "import <module>"` for any
> file containing them.

## Redirects and pipes

```bash
cmd > file        # stdout to file, overwriting
cmd >> file       # stdout to file, appending
cmd 2>/dev/null   # discard stderr
cmd 2>&1          # merge stderr into stdout
cmd | tee file    # to screen AND to file
```

> **In this repo.** `regression_check` must be run **once**, teed to `/tmp`, and
> the file grepped. Running it twice parses the corpus PDFs twice and exhausts
> WSL RAM, restarting the distro.

*(Entries expand on Days 1–2.)*

---

# Part 2 — Git · *(Day 2)*

*(pending — see `TECH_STACK_FOUNDATIONS.md` §2 for the command list that will
be expanded here)*

---

# Part 3 — Docker · *(Day 1, expanded Day 45)*

*(pending — `up -d --build`, `ps`, `logs -f`, `exec -T`, `--force-recreate`,
and the `MSYS_NO_PATHCONV=1` prefix)*

### One signature to record now

```
exec failed: current working directory is outside of container mount
namespace root -- possible container breakout detected
```

**This is not a security event and not a cwd problem.** The container's mount
namespace went stale, usually after a `--force-recreate`. `-w /app` does not
help and no `cd` helps, because *every* exec fails. Confirm with:

```bash
docker compose exec -T backend echo alive
```

then `docker compose up -d --force-recreate backend` and poll `/health`.

---

# Part 4 — HTTP and curl · *(Day 4–6)*

*(pending — `-i`, `-X POST`, `-H`, `-d`, `-N` for streams, and reading status
codes)*

---

# Part 5 — Python · *(Day 10–12)*

*(pending)*

### Two facts to record now

- Scripts run as `python -m scripts.X`, **not** `python scripts/X.py`.
- `eval_runner` runs from the **host**, in `backend/`, with `../golden_dataset/`
  paths.

---

# Part 6 — PostgreSQL · *(Day 13–16)*

*(pending)*

### One fact to record now

**There is no `psql` in the backend image** — it is a Python container. Query
through `python -c` with psycopg2, and **always `SET app.tenant_id` first** or
RLS returns 0 rows.

---

# Part 7 — Testing and evaluation · *(Day 43)*

*(pending)*

### The baseline to record now

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T -w /app backend \
  env PYTHONPATH=/app python -m pytest tests/ -q
```

**Expect `218 passed, 25 errors`** — not green. See `CAVEAT-025`. Compare
against that baseline, not against zero.

---

# Part 8 — Pre-flight · *(Day 1, reinforced every measurement day)*

The four checks that come before trusting **any** measurement. From
`CLAUDE.md` §4 — environment-vs-code confusion has cost more time on this
project than application defects have.

```bash
# (a) which code is actually running
docker compose exec -T backend python -c \
  "import app.engines.retriever as m; print(m.__file__)"

# (b) which environment
docker compose exec -T backend printenv GEMINI_MODEL
docker compose exec -T backend printenv QDRANT_URL   # must be the Cloud https URL

# (c) warm the process — a fresh exec costs ~4s cold; loop 5, read the later ones

# (d) for anything reading reranker_score:
#     read reranker_backend from the SAME response. A score without its
#     backend is meaningless.
```

---

## The entry template

Every command added below Part 0 uses this shape:

```
### <command>

**What it means.**      the words, expanded
**What runs it.**       shell builtin? binary on $PATH? container?
**What it changes.**    filesystem? process state? nothing?
**What the output means.**  including the failure output
**LedgerMind use.**     the actual invocation from this repo
**Common mistake.**     the one that has actually happened here
```

If an entry cannot fill "LedgerMind use" with a real invocation, it does not
belong in this file.
