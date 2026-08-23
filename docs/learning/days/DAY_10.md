# Day 10 — Three Type Systems, On Purpose

**Phase 3 — Python as this codebase uses it · Weight: M (~90 min) · Prerequisites: Days 3, 5**

---

## 1. Today's goal

By tonight you can:

- Explain `TypedDict`, `dataclass` and Pydantic `BaseModel`: what each validates,
  *when*, and what it costs.
- Justify each of the three choices this codebase made, by naming the boundary
  each one guards.
- Explain `@dataclass(frozen=True)` and why the metric registry uses it while the
  ingestion records do not.
- Recognise the general principle: **validation belongs where trust changes**.

---

## 2. Why now

You have now met all three in the wild without being told they were different
things: `QueryState` (Day 3), `QueryRequest` (Day 5), and `ChunkResult` (Day 3).
Days 11 and 12 finish the Python foundations; Day 13 starts the database. This is
the moment to consolidate.

---

## 3. Concepts you must know first

| Concept | From | Why |
|---|---|---|
| `QueryState` is a dict at runtime | Day 3 | The first of the three |
| Pydantic validates the HTTP body | Day 5 | The second |
| `chunk["text"]`, never `getattr` | Day 3 | Today is *why* |

---

## 4. Concept lesson

### 4.1 The problem: Python does not check types

```python
def f(x: int) -> str:
    return x
f("not an int")     # runs fine, returns "not an int"
```

Annotations are **documentation the runtime ignores**. Useful to a reader and to
`mypy`; enforced by nothing at run time.

**So what do you do at a boundary** — where data arrives from a network, a
database, or a language model, and you cannot trust it?

Three answers exist in this codebase, and each is correct **somewhere**.

---

### 4.2 `TypedDict` — a contract with no runtime cost

```python
class ChunkResult(TypedDict):
    chunk_id: str
    text: str
    reranker_score: float
    reranker_backend: str
```

**What it is.** A dict, annotated. At runtime `ChunkResult(...)` returns a plain
`dict` — checked by `mypy`, ignored by Python.

**What it costs.** Nothing. Construction is dict construction.

**What it does not give you.** Any runtime guarantee at all:

```python
c = ChunkResult(chunk_id="x", text="hello", reranker_score=0.5, reranker_backend="cohere")
c["typo"] = 123          # allowed
c["reranker_score"] = "not a float"   # allowed
```

**Mental model.** A **form with printed field names** — the names help whoever
reads it; the paper does not stop you writing in the margin.

**When it is right.** When the data is *already trusted*, is mutated constantly,
and must stay cheap. `QueryState` is mutated by eight nodes, merged, streamed and
serialised. Any per-mutation validation would be paid eight times per request for
a value that never crosses a trust boundary after construction.

**The practical consequence you must remember:**

```python
chunk["text"]            # correct
chunk.get("text", "")    # correct
getattr(chunk, "text")   # WRONG — it is a dict, there is no attribute
```

This is in `CLAUDE.md` §7 because it has been got wrong.

---

### 4.3 `dataclass` — a real object, generated for you

```python
@dataclass
class PageBlock:
    page_number: int
    content: str
    block_type: str = BlockType.TEXT
```

**What it is.** A decorator that writes `__init__`, `__repr__` and `__eq__` from
the annotations. The result is a **real class** with **real attributes**.

**What it gives you:** attribute access (`block.content`), a readable `repr`,
value equality, and — with `frozen=True` — immutability.

**What it does not give you:** type validation.
`PageBlock(page_number="one", content=5)` constructs happily.

**Mental model.** A **labelled box**. The labels are on the outside; nobody
checks what you put in.

**When it is right.** When you want an object with named attributes rather than a
bag of keys, and the data originates inside your own code. The whole ingestion
pipeline passes dataclasses — and `ingestion/models.py` states the rule:

```python
"""
Rule: never pass raw dicts between pipeline stages. Use these types.
"""
```

**Why the ingestion pipeline chose objects where the query pipeline chose dicts:**

| | Query pipeline | Ingestion pipeline |
|---|---|---|
| Shape | **one** object, mutated by everything | **many** objects, flowing in lists |
| Mutation | constant, in place | mostly construct-and-pass |
| Serialisation | streamed as JSON per node | `asdict()` once, at the Qdrant write |
| So | `TypedDict` | `dataclass` |

---

### 4.4 `frozen=True` — immutability as a design statement

```python
@dataclass(frozen=True)
class MetricDefinition:
    canonical_name: str
    aliases: tuple[str, ...]
    metric_type: MetricType
    dsl_enabled: bool
    label: str
```

`frozen=True` generates a `__setattr__` that raises `FrozenInstanceError`. It also
makes the class hashable.

**Why `MetricDefinition` is frozen and `PageBlock` is not.**

`MetricDefinition` is **schema** — the definition of what "revenue" means in this
system. It is loaded once at import and read by five modules. Accidental mutation
would change the meaning of a metric **globally, at run time**, and the change
would be invisible until a wrong number appeared. `frozen=True` makes that
impossible.

`PageBlock` is **data in flight**. `section_classifier.classify_blocks()`
refines `block_type` in place, and its docstring says so explicitly:

```python
"""
Output:
  Same list with block_type and metadata refined in-place.
  No new objects created — downstream modules read the updated list.
"""
```

Freezing it would force a copy of every block on every refinement — for a
document producing thousands of blocks, on a 512 MB tier.

**Note `aliases: tuple[str, ...]`, not `list[str]`.** A frozen dataclass with a
mutable field is only shallowly frozen — you could not rebind `aliases`, but you
could still `.append()` to it. A tuple closes that.

---

### 4.5 Pydantic `BaseModel` — validation at a trust boundary

```python
class QueryRequest(BaseModel):
    query: str
    tenant_id: Optional[str] = None
```

**What it is.** A class that **validates and coerces on construction**. Wrong
type → `ValidationError`. Coercible type → coerced (`"5"` → `5` for an `int`).

**What it costs.** Real time per construction, and a dependency.

**What it uniquely gives you.** A runtime guarantee, plus:

- `model_json_schema()` — a JSON Schema document, which FastAPI turns into
  `/docs`;
- `model_validate_json()` — parse-and-validate in one step;
- **and, uniquely important here, a schema that can be handed to an LLM.**

**Mental model.** A **form with a clerk**. The clerk checks each box before
accepting the form and hands it back if a box is wrong.

**When it is right.** Exactly at a boundary where trust changes:

| Boundary | Model | Day |
|---|---|---|
| HTTP body → Python | `QueryRequest`, `LoginRequest` | 5 |
| Python → HTTP body | `TokenResponse`, `MetricsResponse` | 5, 44 |
| Environment → config | `Settings` | 12 |
| **LLM output → Python** | `RouterResponse`, `GeminiDSLResponse` | 18, 32 |

That last row is why Pydantic is not merely "the FastAPI thing" here. On Day 18
you will find that declaring a Pydantic field **changes the prompt** — because
the schema is sent to the model. A validation library became a prompt-engineering
tool.

---

### 4.6 The rule underneath

> **Validation belongs where trust changes.**

- Inside your own pipeline, trust does not change → `TypedDict` or `dataclass`.
- At the edge of your process, trust changes → Pydantic.

Validating everywhere is not "safer" — it is cost with no corresponding risk, and
it trains readers to ignore validation as noise.

---

## 5. The actual LedgerMind files

```
File:        backend/app/engines/state.py            → TypedDict × 5
Purpose:     the shared query state and its nested shapes

File:        backend/app/ingestion/models.py (212)   → dataclass × 7
Purpose:     the types that flow between ingestion stages
Rule:        "never pass raw dicts between pipeline stages"

File:        backend/app/metrics/registry.py (768)   → frozen dataclass
Purpose:     the single definition of every metric

File:        backend/app/auth/schemas.py (19)        → BaseModel × 3
File:        backend/app/api/query.py                → BaseModel (QueryRequest)
File:        backend/app/core/config.py (19)         → BaseSettings
File:        backend/app/engines/router.py           → BaseModel (RouterResponse)
File:        backend/app/engines/quant_engine.py     → BaseModel (GeminiDSLResponse)
```

**The distribution is the lesson.** Pydantic appears only at process edges —
HTTP in, HTTP out, environment in, LLM out. Everything internal is a dict or a
dataclass.

---

## 6. Deep code walkthrough

### 6.1 `ingestion/models.py` — seven dataclasses and one function

```python
@dataclass
class PageBlock:
    page_number: int
    content: str
    block_type: str = BlockType.TEXT
    table: Optional[RawTable] = None
    ...

@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: ChunkMetadata
    ...

@dataclass
class EmbeddedChunk:
    chunk: Chunk
    dense_vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]
```

**The shape of the pipeline is visible in the types:**

```
pdf_parser          → list[PageBlock]
document_classifier → list[DocSection]
section_classifier  → the same list[PageBlock], block_type refined IN PLACE
chunker             → list[Chunk]
embedder            → list[EmbeddedChunk]
qdrant_writer       → consumes EmbeddedChunk
financial_extractor → list[FinancialRecord]
db_loader           → consumes FinancialRecord
```

**`EmbeddedChunk` wraps rather than extends.** It holds a `Chunk` as a field
instead of subclassing it. Composition means `ec.chunk` is unambiguously the same
object the chunker produced — no slicing, no partial copies, and `asdict()`
produces a nested structure that maps cleanly onto a Qdrant payload.

**`BlockType` and `FinancialType` are plain classes of constants, not `Enum`:**

```python
class BlockType:
    TEXT = "TEXT"
    TABLE = "TABLE"
    FINANCIAL_STATEMENT = "FINANCIAL_STATEMENT"
```

**Why not `Enum`?** Because these values are **written to a database and to a
Qdrant payload as strings**. With an `Enum` every write site needs `.value` and
every read site needs `BlockType(raw)` — and a value in the store that no longer
exists in the enum raises on *read*. A constants class means the stored value and
the Python value are the same object, and an unknown legacy value simply compares
unequal instead of exploding. `GateDecision` **is** a `str, Enum` — because it
never leaves the process.

---

### 6.2 `state.py` — five `TypedDict`s and why they nest

```python
class ChunkResult(TypedDict): ...
class Citation(TypedDict): ...
class DSLObject(TypedDict): ...
class ContradictionFlag(TypedDict): ...

class QueryState(TypedDict):
    retrieved_chunks: List[ChunkResult]
    citations: List[Citation]
    dsl_object: Optional[DSLObject]
    contradictions: List[ContradictionFlag]
```

**STATE BEFORE.** `make_initial_state` runs; `retrieved_chunks` is `[]`.

**Execute** `semantic_engine_node`.

**STATE AFTER.** `retrieved_chunks` holds up to five `ChunkResult` dicts —
**still plain dicts**, nested inside the outer dict, entirely serialisable.

**Why that matters concretely.** `api/query.py` streams partial state to the
client as JSON. Nested dataclasses would need `asdict()` at every boundary;
nested Pydantic models would need `.model_dump()`. Nested `TypedDict`s need
nothing:

```python
await queue.put(("node", node_name, partial or {}))   # already JSON-serialisable
```

**`Citation` versus `ChunkResult` — why two shapes for one thing.**

| `ChunkResult` (16 fields) | `Citation` (9 fields) |
|---|---|
| everything retrieval knows | what the answer cites |
| full `text` | `text_preview` (first 200 chars) |
| `dense_score`, `sparse_score`, `rrf_score` | dropped |
| `chunk_type`, `speaker_role`, `quarter` | dropped |

`_build_citations` is the deliberate narrowing. Two types because they answer
different questions — *what did we retrieve* and *what are we standing behind* —
and merging them would push retrieval internals into the response.

---

### 6.3 `metrics/registry.py` — frozen, and read five ways

```python
@dataclass(frozen=True)
class MetricDefinition:
    canonical_name: str
    aliases: tuple[str, ...]
    metric_type: MetricType          # Literal["raw", "derived"]
    dsl_enabled: bool
    label: str
    prompt_aliases: str = ""
    prompt_warning: str | None = None
    derivation_formula: str | None = None

ALL_METRICS: tuple[MetricDefinition, ...] = (...)
```

**`MetricType = Literal["raw", "derived"]`.** A `Literal` restricts to exact
values — `mypy` rejects `metric_type="derivd"`. Free at run time, and it
documents the closed set in the type.

**One definition, five derived views.** The module exposes functions that each
project the registry for one consumer:

```python
all_alias_pairs()             # ingestion: raw label → canonical
dsl_registry()                # dsl_compiler: canonical → {label, available}
dsl_alias_pairs()             # dsl_compiler: alias → canonical
prompt_metric_lines()         # quant_engine: prose for the LLM prompt
derived_metric_aliases()      # quant_engine Stage 0 guard
unqueryable_metric_aliases()  # quant_engine Stage 0b guard
metric_anchor_phrases()       # cross_engine Stage 0c guard
```

**Read the docstring's history — this is the argument for the whole design:**

```python
"""
Prior to this refactor, metric definitions were split across three independently
hand-maintained dicts:
  - entity_resolver.py  METRIC_ALIASES   (ingestion-side)
  - dsl_compiler.py     METRIC_REGISTRY  (query-side)
  - quant_engine.py     ALIASES          (prompt-side)

Every one of the following real, shipped bugs was a direct consequence:
  - profit_before_tax was entirely absent from dsl_compiler's METRIC_REGISTRY,
    so Gemini had no correct option and silently substituted "pat" instead.
  - exceptional_items collapsed three distinct line items ... causing a
    genuinely-blank cell to be silently backfilled by an unrelated row's value.
  - Titan's segment revenue ... had no canonical home in any registry.
"""
```

**Three copies of one fact caused three shipped bugs.** The fix was not "keep
them in sync more carefully" — it was to make the second and third copies
*derived* rather than *maintained*.

**And notice the `frozen=True` is what makes derivation safe.** Five modules
receive projections of the same immutable objects. If a consumer could mutate a
`MetricDefinition`, one module's edit would silently change another module's
behaviour — reintroducing the drift by a different route.

---

### 6.4 `core/config.py` — Pydantic at the environment boundary

```python
class Settings(BaseSettings):
    database_url: str = "postgresql://ledger:ledger_dev_pass@postgres:5432/ledgermind"
    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    environment: str = "development"
    JWT_SECRET: str                       # ← NO DEFAULT
    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()
```

**`JWT_SECRET: str` has no default, and that is the security decision.** The
application **refuses to start** without it — the same discipline as
`GEMINI_MODEL` (Day 19). A default secret would be worse than no secret: it would
work, and every deployment would share it.

**`extra="ignore"`** lets `.env` carry variables `Settings` does not declare
(`COHERE_API_KEY`, `SUPABASE_URL`) without erroring. Which produced this
cleanup, recorded in the file:

```python
# gemini_api_key / groq_api_key removed 2026-08-03: nothing read either
# attribute. Every LLM consumer reads the environment directly via
# os.getenv (app/llm/client.py), so these were a second, silently unused
# declaration of the same configuration.
```

**A declared-but-unread setting is the same failure shape as
`preferred_operation`** (`CAVEAT-002`, Day 5): a field that looks wired and is
not. Here it was noticed and removed; there it was not.

**`settings = Settings()` at module scope** means validation happens at **import**
— so a missing `JWT_SECRET` crashes on startup rather than on the first login.
Fail early, fail loudly.

---

## 7. Data flow — where each type system sits

```
        ┌──── PROCESS EDGE — TRUST CHANGES → PYDANTIC ────┐
        │                                                  │
HTTP ──►│ QueryRequest / LoginRequest                      │
.env ──►│ Settings                                         │
LLM  ──►│ RouterResponse / GeminiDSLResponse               │
        └──────────────────────┬───────────────────────────┘
                               ▼
              ┌──── INSIDE: TRUST DOES NOT CHANGE ────┐
              │                                        │
   query path │  QueryState (TypedDict)                │  mutated by 8 nodes,
              │   ├─ ChunkResult                       │  streamed as JSON
              │   ├─ Citation                          │
              │   └─ DSLObject                         │
              │                                        │
 ingestion    │  PageBlock → Chunk → EmbeddedChunk     │  constructed and passed
              │  FinancialRecord   (dataclass)         │  in lists
              │                                        │
   schema     │  MetricDefinition (FROZEN dataclass)   │  loaded once, read by 5
              └────────────────────────┬───────────────┘
                                       ▼
        ┌──── PROCESS EDGE ────┐
HTTP ◄──│ TokenResponse        │
        │ MetricsResponse      │
        │ role_filtered_response (plain dict — Day 9)
        └──────────────────────┘
```

**One anomaly worth naming.** `role_filtered_response` returns a **plain dict**,
not a Pydantic model, at an outward boundary. The docstring explains why it is
fragile — a mistyped key returns `None` silently — and Day 9 covered it. It is the
one place the rule is not followed, and the file says so rather than hiding it.

---

## 8. Engineering decision — three, deliberately

**Problem.** Structure data at very different boundaries with very different
trust and mutation profiles.

**Decision.** `TypedDict` inside the query pipeline; `dataclass` inside
ingestion; frozen `dataclass` for schema; Pydantic at every process edge.

| Alternative | Why not |
|---|---|
| **Pydantic everywhere** | Validation cost on every mutation — paid eight times per request for data that never crosses a boundary after construction. And `QueryState` would need `.model_dump()` at every streaming boundary |
| **Dicts everywhere** | No contract anywhere. A typo creates a key; `mypy` cannot help; the ingestion pipeline's stage-to-stage guarantees vanish. `models.py`'s "never pass raw dicts" rule exists because this was tried |
| **Dataclasses everywhere** | `QueryState` would need `asdict()` at every stream boundary, and LangGraph's node contract expects mappings |
| **`attrs`** | Roughly equivalent to dataclasses, plus a dependency |

**Trade-offs accepted.** Three idioms to learn. A reader must know which is which
to know what `x["y"]` versus `x.y` means. Mitigated by consistency: query path is
always dicts, ingestion is always objects.

**Current validity.** Sound, and the boundaries are clean. The one crack is
`role_filtered_response`'s untyped output, documented in the file itself.

**At 10×.** The pressure point is `role_filtered_response` — with more roles or
fields, its silent-`None` failure mode gets more expensive, and a Pydantic
response model per role becomes worth its cost.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `AttributeError: 'dict' object has no attribute 'text'` | `getattr` on a `TypedDict` |
| `TypeError: 'ChunkResult' object is not callable` | Treating a `TypedDict` as a class |
| A typo'd state key reads `None` forever | `TypedDict` does not check keys at run time |
| `FrozenInstanceError` | Mutating a `MetricDefinition` — **working as intended** |
| A frozen object's list still mutated | Shallow freeze. Hence `aliases: tuple[...]` |
| App starts, first login 500s | A setting validated too late — avoided by `settings = Settings()` at module scope |
| A declared setting silently unused | `extra="ignore"` hides it; only a grep finds it |
| A metric means one thing here, another there | Two registries. The reason there is now one |

---

## 10. Hands-on experiment

### Experiment 1 — a `TypedDict` is a dict

```bash
docker compose exec -T backend python -c "
from app.engines.state import ChunkResult
c = ChunkResult(chunk_id='x', doc_id='d', text='hello', page_number=1,
                company='ETERNAL', fiscal_year='FY26', quarter=None,
                financial_type='consolidated', chunk_type='TEXT',
                filing_date='2026-05-15', dense_score=0.0, sparse_score=0.0,
                rrf_score=0.0, reranker_score=0.9, reranker_backend='cohere',
                speaker_role='unknown')
print('type      :', type(c))
print('is a dict :', isinstance(c, dict))
print('c[\"text\"] :', c['text'])
try:
    print(getattr(c, 'text'))
except AttributeError as e:
    print('getattr   : AttributeError ->', e)
c['not_a_field'] = 42
print('typo key accepted:', c['not_a_field'], '  <- no runtime checking')
"
```

### Experiment 2 — frozen means frozen

```bash
docker compose exec -T backend python -c "
from app.metrics.registry import get_metric
from dataclasses import FrozenInstanceError
m = get_metric('revenue')
print('label   :', m.label)
print('aliases :', type(m.aliases).__name__, '<- tuple, not list')
try:
    m.label = 'Turnover'
except FrozenInstanceError as e:
    print('mutation: FrozenInstanceError ->', e)
try:
    m.aliases.append('x')
except AttributeError as e:
    print('append  : AttributeError ->', e, ' <- why aliases is a tuple')
"
```

### Experiment 3 — Pydantic actually validates

```bash
docker compose exec -T backend python -c "
from app.api.query import QueryRequest
from pydantic import ValidationError
print(QueryRequest(query='hello'))
try:
    QueryRequest(tenant_id='t')
except ValidationError as e:
    print('missing query ->'); print(e)
print()
print('coercion:', QueryRequest(query=12345))   # int -> str? see what happens
" 2>&1 | head -25
```

### Experiment 4 — a dataclass does not validate

```bash
docker compose exec -T backend python -c "
from app.ingestion.models import PageBlock
b = PageBlock(page_number='not a number', content=12345)
print(b)
print()
print('page_number type:', type(b.page_number).__name__, '<- accepted anyway')
"
```

Compare with Experiment 3. **This is the whole difference**, in two commands.

### Experiment 5 — one registry, five projections

```bash
docker compose exec -T backend python -c "
from app.metrics import registry as r
print('ALL_METRICS       :', len(r.ALL_METRICS))
print('all_alias_pairs   :', len(r.all_alias_pairs()),   '(ingestion)')
print('dsl_registry      :', len(r.dsl_registry()),      '(dsl_compiler)')
print('dsl_alias_pairs   :', len(r.dsl_alias_pairs()),   '(dsl_compiler)')
print('prompt_metric_...  :', len(r.prompt_metric_lines()), '(LLM prompt)')
print('derived_aliases   :', len(r.derived_metric_aliases()), '(Stage 0 guard)')
print('anchor_phrases    :', len(r.metric_anchor_phrases()), '(Stage 0c guard)')
print()
print('Six views. One definition. That is the point.')
"
```

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `backend/app/ingestion/models.py`, `backend/app/engines/state.py` and
`backend/app/metrics/registry.py`:

1. `models.py` has a one-line rule in its docstring. What is it, and what breaks
   if you ignore it?
2. Why is `EmbeddedChunk` composed of a `Chunk` rather than subclassing it?
3. `BlockType` is a plain class of string constants, not an `Enum`. Give a
   concrete reason, involving something outside the Python process.
4. `MetricDefinition.aliases` is `tuple[str, ...]`. What would break if it were
   `list[str]`, even with `frozen=True`?
5. `Settings.JWT_SECRET` has no default. What happens on startup without it, and
   why is that better than a default?

---

## 12. Self-check questions

**Basic**
1. What is a `TypedDict` at run time?
2. What does `@dataclass` generate?
3. What does Pydantic do that the other two do not?
4. What does `frozen=True` add?
5. Which one is used for `QueryState`?

**Code**
6. How do you read `text` from a `ChunkResult`?
7. Which class in this codebase is a frozen dataclass, and why that one?
8. Where does Pydantic appear outside FastAPI?
9. What does `extra="ignore"` do in `Settings`?
10. What is `Literal["raw", "derived"]` for, and when is it checked?

**Why**
11. Why not Pydantic everywhere?
12. Why does ingestion use dataclasses when the query path uses dicts?
13. Why is `MetricDefinition` frozen but `PageBlock` is not?
14. Why is `Citation` a separate type from `ChunkResult`?
15. Why are `BlockType`'s values plain strings rather than an `Enum`?

**Debugging**
16. `AttributeError: 'dict' object has no attribute 'reranker_score'`. What did
    the caller assume?
17. A `QueryState` field is always `None` though a node "sets" it. Two causes.
18. `FrozenInstanceError` in a script that worked yesterday. What changed, and is
    it a bug?

**System design**
19. Add `chunk_hash` to a retrieved chunk end to end. Which files change, and
    which of the three type systems make you edit more than one place?
20. `role_filtered_response` returns an unvalidated dict at an outward boundary.
    Argue both sides, then say what you would do and at what point.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. *"Never pass raw dicts between pipeline stages. Use these types."* Ignoring it
   loses every stage-to-stage guarantee: `mypy` can no longer tell you the
   chunker's output matches the embedder's input, and a renamed field becomes a
   silent `KeyError` several stages later — or worse, a `.get()` returning `None`.
2. Composition means `ec.chunk` **is** the object the chunker produced —
   same identity, no slicing, no partial copy. Subclassing would create a new
   object that *is* a `Chunk`, making it ambiguous whether downstream code is
   holding the original or a derived thing. It also gives `asdict()` a nested
   structure that maps cleanly onto a Qdrant payload.
3. Because the values are **written to Postgres and to a Qdrant payload as
   strings**. With an `Enum`, every write needs `.value`, every read needs
   `BlockType(raw)`, and a legacy value in the store that no longer exists in the
   enum **raises on read**. With constants, the stored value and the Python value
   are the same object, and an unknown value simply compares unequal. Note
   `GateDecision` *is* a `str, Enum` — because it never leaves the process.
4. `frozen=True` is a **shallow** freeze: it prevents rebinding the attribute, not
   mutating the object it points at. With `list[str]` you could still
   `m.aliases.append("turnover")` and change what "revenue" matches — globally,
   at run time, for all five consumers. A tuple has no `append`.
5. `Settings()` runs at **module import**, so a missing `JWT_SECRET` raises a
   `ValidationError` and the process **fails to start**. Better than a default
   because a default would *work* — and every deployment would share the same
   signing key, so anyone who read the source could mint an admin token for any
   tenant. Same discipline as `GEMINI_MODEL`: a plausible-but-wrong value is
   worse than a crash.

### §12 — Basic

1. A plain `dict`. The annotations are for static checkers only.
2. `__init__`, `__repr__`, `__eq__` (and more with options) from the annotations.
3. **Validates and coerces at run time**, and can emit a JSON Schema.
4. Immutability (`__setattr__` raises) and hashability.
5. `TypedDict`.

### §12 — Code

6. `chunk["text"]` or `chunk.get("text")`. Never `getattr`.
7. `MetricDefinition`. Because it is **schema** — the definition of what a metric
   means — loaded once and read by five modules, where accidental mutation would
   silently change behaviour system-wide.
8. `Settings` (`BaseSettings`, environment boundary), and `RouterResponse` /
   `GeminiDSLResponse` (LLM output boundary — where the schema is also *sent to
   the model*).
9. Allows `.env` to contain variables `Settings` does not declare without raising
   — necessary because `.env` carries `COHERE_API_KEY`, `SUPABASE_URL` and others
   that no `Settings` field mirrors.
10. It restricts the value to an exact closed set, checked by `mypy` at **type-check
    time**, not at run time. It documents the closed set inside the type.

### §12 — Why

11. Validation cost is paid on every construction. `QueryState` is mutated by
    eight nodes per request and streamed as JSON at each boundary; Pydantic would
    add validation eight times for data that never crosses a trust boundary after
    construction, plus `.model_dump()` at every stream point.
12. Different profiles. The query path is **one** object mutated constantly and
    serialised repeatedly → dict-native. Ingestion is **many** objects
    constructed and passed in lists, where attribute access and a readable `repr`
    matter more than serialisation.
13. `MetricDefinition` is schema, read by five modules, where mutation would
    change meaning globally and invisibly. `PageBlock` is data in flight, and
    `section_classifier` refines `block_type` **in place by design** — freezing
    it would force a copy of every block on every refinement, for thousands of
    blocks, on a 512 MB tier.
14. They answer different questions: *what did we retrieve* (16 fields, full
    text, three scores) versus *what are we standing behind* (9 fields, a
    200-char preview, one score). Merging them would push retrieval internals
    into the user-facing response.
15. Because they are written to Postgres and Qdrant **as strings** — see §11 Q3.

### §12 — Debugging

16. The caller assumed a `ChunkResult` was an object with attributes. It is a
    `TypedDict`, which is a `dict` at run time. Fix: `chunk["reranker_score"]`.
17. (a) The node returned early on an error path before writing it — check
    `error` and `error_node`. (b) The node wrote a **different key** (a typo), and
    `TypedDict` does not check keys at run time, so the write silently created a
    new key. Only `mypy` or a grep against `state.py` finds the second.
18. Someone made a dataclass `frozen=True` — or the script was mutating a
    `MetricDefinition`, which has always been frozen and the script simply never
    hit that path before. **Not a bug**: it is the protection working. The script
    should build a new object, or — more likely — should not be mutating shared
    schema at all.

### §12 — System design

19. `ChunkResult` in `state.py` (add the key); `retriever.hybrid_search` (populate
    it from the Qdrant payload); `qdrant_writer._metadata_to_payload` and
    `ChunkMetadata` in `models.py` (write it at ingest); `chunker` (compute it);
    optionally `Citation` and `_build_citations` if it should surface; and
    `role_filtered_response` if it should reach the client. **The type systems
    that make you edit more than one place:** `TypedDict` and `dataclass` both
    require the field to be declared *and* populated separately, and neither will
    tell you at run time if you did only one — `mypy` would, if run. The frozen
    registry is unaffected. The lesson: the number of edit sites is set by the
    **pipeline's length**, not by the type system; what the type system decides is
    whether a missed site fails loudly or silently.
20. **For leaving it:** the function is a projection of `QueryState`, whose keys
    are already declared once; a Pydantic model would be a fourth copy of the
    field list, and per-role models would be three. **Against:** it is an outward
    boundary where a mistyped key returns `None` silently, and the docstring
    itself records that an earlier version of *the docstring* caused a wrong
    prediction about where a field had to be threaded. **What I would do:** leave
    it at three roles, and convert when a fourth role or a second response shape
    appears — because that is the point at which the silent-`None` failure gets
    multiplied rather than merely tolerated. Record the trigger now, in
    `CAVEATS.md`, so the decision is deliberate rather than deferred by inertia.

---

## 14. MUST REMEMBER

```text
- TypedDict → a dict at run time. No validation. chunk["text"], never getattr
- dataclass → a real object with real attributes. Still no validation
- frozen=True → immutable AND hashable, but SHALLOW (hence tuple, not list)
- Pydantic → validates and coerces at run time; can emit a JSON Schema
- Query path = dicts. Ingestion = dataclasses. Schema = frozen. Edges = Pydantic
- "Never pass raw dicts between pipeline stages" — models.py
- JWT_SECRET has NO default; the app refuses to start without it
- app/metrics/registry.py is the SINGLE metric registry. Never add a second
```

## 15. MUST UNDERSTAND

```text
- The rule underneath all of it: VALIDATION BELONGS WHERE TRUST CHANGES
- Why validating everywhere is cost without risk, and trains readers to
  ignore validation as noise
- Why three copies of one fact caused three shipped bugs, and why the fix was
  to make copies DERIVED rather than maintained
- Why frozen=True is what makes derivation safe
- Why Enum is wrong for a value that is stored outside the process
```

---

## 16. This connects to

```text
Day 9 — the response, shaped by role
   ↓
Day 10 — the type systems those shapes are made of     ← you are here
   ↓
Day 11 — context managers, generators, async
   ↓
Day 12 — module-level state and lazy loading
```

Forward references:

- `PageBlock`, `Chunk`, `EmbeddedChunk` in motion → **Days 22–24**
- `MetricDefinition` and its five projections → **Day 31**
- `RouterResponse` — where a Pydantic schema **becomes prompt input** → **Day 18**
- `GeminiDSLResponse` and what its required fields force → **Day 32**
- `Settings` and configuration discipline → **Day 12**
- `FinancialRecord` → `db_loader` → **Day 15**
