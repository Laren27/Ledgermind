# Day 38 — React and Next, Against This App

**Phase 11 · Weight: M (~90 min) · Prerequisites: Day 5**

**Textbook: no citation today.** `RAG_Complete_Textbook_v2` has no frontend
chapter. From here to Day 41 the repository is the only source, which is
itself worth noticing: the parts of a RAG system that a textbook covers and the
parts you actually have to build are not the same set.

---

## 1. Today's goal

By tonight you can:

- Say what a **component**, a **prop** and **JSX** are, using this repository's
  own files rather than a counter example.
- Explain what the **App Router** routes, and why `frontend/app/` containing
  exactly three files means there is exactly **one page**.
- Explain what `"use client"` marks — and why it is a **boundary**, not a
  per-file property.
- Name the **five views** this one page renders, and say where the variable that
  chooses between them lives.
- Explain why the working-paper UI needed Next.js rather than Streamlit
  (**ED-023**), in terms of what Streamlit's execution model cannot express.
- Read any component in this repository and say what it **receives** and what it
  **renders**.

You are **not** learning React in general today. You are learning the eleven
client components and eight server components that this application actually
contains.

---

## 2. Why now

Phase 11 is deliberately last among the implementation phases, and the ordering
argument in `00_LEARNING_MAP.md` §G is short:

> **11 Frontend · Days 38–41** — Needs to know what it is rendering (D30, D34).

That is literal. Day 40's central function, `composeDocumentBody()`, branches on
`data.path`, `data.sql_result[0]`, `data.error` and `data.is_blocked`. Every one
of those is a field you now know the producer of:

| Field | Produced by | Day |
|---|---|---|
| `path` | `router_node` | 36 |
| `sql_result`, `sql_verified` | `quant_engine_node` | 33–34 |
| `citations`, `confidence_tier` | `semantic_engine_node` | 29–30 |
| `contradictions` | `detect_contradictions` | 37 |
| `is_blocked`, `block_reason` | `prompt_shield_node` | 42 (read today as a field) |

Read the frontend before Day 30 and `composeDocumentBody` is a wall of
unexplained branches. Read it now and it is a **rendering table for a data
structure you already know**.

The second reason is Day 5. `lib/api.ts`'s `QueryResponse` interface is the same
contract you met as `role_filtered_response`'s output dict. Today you see the
**other end of it**, in a second language, checked by a second type system.

---

## 3. Concepts you must know first

| Concept | From | Why it is needed today |
|---|---|---|
| The response contract | Day 5 | `QueryResponse` in `lib/api.ts` is that contract, re-declared in TypeScript |
| `role_filtered_response` | Day 9 | Explains why fields are **optional** in the TS interface |
| HTTP, JSON, headers | Day 4 | The fetch calls are unremarkable HTTP once you know that |
| `QueryState` field names | Day 3 | The TS field names are the state's field names |
| Blocking vs SSE transport | Day 6 | Day 39 consumes the SSE side; today just know both exist |

If **any** of these is shaky, go back. Everything below assumes you can name
what a `QueryResponse` contains without opening a file.

---

## 4. Concept lesson

### 4.1 The problem the frontend actually has

Start from the requirement, not from React.

LedgerMind must show an answer that a financial analyst can **defend**. Not
"here is a paragraph" — here is a paragraph, with numbered superscripts, each
tied to a specific page of a specific filing, beside a verified figure marked
verified, beside a trace of which engine produced it, on a page that can be
paged back through like a stack of working papers.

That is not a chat interface. It is a **document**.

And a document has requirements a chat log does not:

1. **Layout is meaning.** A ledger table with a single rule above the current-year
   row and a double rule under a total is not decoration; it is how accountants
   read. The `rule: "none" | "single" | "double"` prop on `LedgerTable` exists
   for exactly that.
2. **Nothing may be asserted that was not computed.** A confidence badge that
   renders "low" when nothing scored the query is a lie told by a UI. This is
   the **Zero UI-Hallucination Mandate** (`CLAUDE.md` §6), and it drives more
   frontend code here than aesthetics does.
3. **State must survive across answers.** Page 3 of the working paper must still
   render when you are looking at page 5.

Hold those three. Every design choice in Days 38–41 answers one of them.

---

### 4.2 What a component is — from a file you have already read

Forget the tutorial definition. Open the smallest real one in this repository,
[`CitationSummary.tsx`](../../../frontend/components/document/CitationSummary.tsx):

```tsx
interface CitationSummaryProps {
  count: number;
}

export function CitationSummary({ count }: CitationSummaryProps) {
  if (count === 0) return null;

  return (
    <div
      className="py-2 px-3 text-xs font-medium"
      style={{ color: "var(--ink-metadata)", fontFamily: "var(--font-archival)" }}
    >
      {count} Supporting {count === 1 ? "Citation" : "Citations"}
    </div>
  );
}
```

**That is the whole model.**

- **A component is a function.** It takes one argument and returns markup.
- **Props are that argument.** `{ count }` is destructuring one field out of the
  single props object. The `interface` above it is the **contract**, checked at
  compile time — the TypeScript equivalent of a Pydantic model on a FastAPI
  endpoint (Day 5).
- **JSX is the return value.** `<div>…</div>` inside TypeScript is not a string
  and not HTML; it is an expression that compiles to a function call describing
  what to render. `{count}` switches back out of markup into TypeScript.
- **Returning `null` renders nothing.** Note *what* that line is doing:
  `if (count === 0) return null` is the **omit-rather-than-substitute** rule
  from §4.1 point 2, in its smallest possible form. There is no "0 Supporting
  Citations" state, because a zero-citation answer should not display a citation
  header at all.

**One function, one contract, one piece of output.** Everything else in
`components/` is this shape with more lines.

---

### 4.3 Composition — and where it stops

`CitationSummary` is used inside a larger function, which is used inside a
larger one:

```
page.tsx (Home)
  └─ DocumentEnvironment            desk background, lighting, objects
       └─ Sidebar                   role-gated navigation
       └─ DocumentPage              the paper sheet, its shadow, its animation
            └─ renderSheetContent() ─ WorkingPaperHeader
                                    ─ DocumentTitle
                                    ─ QueryDock
                                    ─ ExecutionTrace
                                    ─ composeDocumentBody(data)  ← Day 40
                                         ├─ MetricCallout
                                         ├─ LedgerTable | EntityComparisonTable
                                         ├─ AnalysisSection
                                         ├─ CitationSummary
                                         └─ EvidenceList
```

Read the indentation as **containment**: a component renders other components
between its own tags, or hands them in as the `children` prop.

**Now the important part — where composition stops.** `CitationSummary` takes a
`count: number`. It does not take a `QueryResponse`. It does not know what a
citation is, which engine produced it, or that there are three engines. Neither
does `LedgerTable`, `MetricCallout`, `EvidenceList` or `AnalysisSection`.

That is **ED-024**, stated as an invariant in `CLAUDE.md` §6:

> **Frontend document components must never know which engine produced the
> data.** `composeDocumentBody()` in `app/page.tsx` is the only function aware of
> path/engine internals.

You will spend all of Day 40 on that one function. Today, just register the
shape: **twenty dumb components, one smart function.** Adding a fourth engine
path touches the function, not the twenty.

---

### 4.4 The App Router — and why this is a one-page application

Next.js's App Router maps **directories under `app/`** to URLs. `app/page.tsx`
is `/`; `app/settings/page.tsx` would be `/settings`. Two filenames are special:

| File | Role |
|---|---|
| `page.tsx` | the route's own content |
| `layout.tsx` | wraps that content, and every route nested below it |

Now measure this repository:

```bash
find frontend/app -type f
# frontend/app/page.tsx
# frontend/app/layout.tsx
# frontend/app/globals.css
```

**Three files. One `page.tsx`. Therefore exactly one route: `/`.**

That single fact does a lot of work later:

- **It is half of CAVEAT-026's proof.** If a component is not reachable from
  `app/page.tsx`, there is no second entry point it could be mounted from —
  because there is no second page. (Day 40 completes the argument.)
- **It explains why `page.tsx` is 584 lines.** There is nowhere else for
  application state to live.
- **It explains why "navigation" is a state variable, not a URL.** Which brings
  us to the five views.

---

### 4.5 One page, five views

From [`page.tsx`](../../../frontend/app/page.tsx):

```tsx
// "upload-history" is intentionally NOT part of Sidebar's SidebarView type —
// it's reached only via the in-page "View Full Upload History →" link inside
// UploadPanel, never as a standalone sidebar entry (deliberate design choice:
// it's a continuation of Intake, not a separate product area).
type ActiveView = "workbench" | "peer" | "audit" | "upload" | "upload-history";
```

And in `Sidebar.tsx`:

```tsx
type SidebarView = "workbench" | "peer" | "audit" | "upload";
```

**Five views in the page; four in the sidebar.** The asymmetry is deliberate and
the comment says why. `upload-history` is a *continuation of* Intake rather than
a peer of it, so it exists as a state the page can be in but not as a
destination the navigation offers.

**Read what that costs and what it buys.** It buys a navigation list that
matches the product's structure. It costs a small dance in `page.tsx`:

```tsx
activeView={activeView === "upload-history" ? "upload" : activeView}
```

— the sidebar is told "upload" while the page is in `upload-history`, so the
Intake entry stays highlighted. **One narrowing at the boundary**, rather than a
fifth entry nobody should click.

**None of these five is a URL.** Switching views does not change the address bar,
does not push history, and is not shareable or bookmarkable. That is a real
trade-off and it is not written down anywhere in the repository — see §8.

---

### 4.6 `"use client"` — a boundary, not a label

This is the single most misread thing in a Next.js codebase, so read it slowly.

By default in the App Router, a component is a **Server Component**: it runs
during the render on the server, its code is *not* shipped to the browser, and
it may not use browser-only APIs or React state.

`"use client"` at the top of a file marks the **boundary** where the client
subtree begins. And here is the part people get wrong:

> **Everything imported *below* a `"use client"` file is part of the client
> bundle, whether or not it carries the directive itself.**

The directive marks the *entry* to the client, not the property of a file.

Measure it in this repository:

```bash
cd frontend
grep -l '"use client"' -r app components | sort
```

**Eleven files carry it:**

```
app/page.tsx
components/LoginForm.tsx
components/document/ArchiveStamp.tsx
components/document/AuditLogTable.tsx
components/document/DocumentPage.tsx
components/document/ExecutionTrace.tsx
components/document/PageNavigator.tsx
components/document/QueryDock.tsx
components/document/Sidebar.tsx
components/document/UploadHistoryTable.tsx
components/document/UploadPanel.tsx
```

**And `app/layout.tsx` does not.** It is the only genuine Server Component in the
application, because it is the only file above the boundary.

Now look at what the eleven have in common:

| File | Why it must be a client component |
|---|---|
| `page.tsx` | `useState`, `useEffect`, `useCallback`, event handlers |
| `DocumentPage.tsx` | `useState`, `useRef`, `useEffect`, `sheet.animate()` — the Web Animations API |
| `QueryDock.tsx` | `useState` for the input, `onSubmit` |
| `ExecutionTrace.tsx` | `useState` for expand/collapse |
| `Sidebar.tsx`, `PageNavigator.tsx`, `AuditLogTable.tsx` | `onClick` handlers |
| `UploadPanel.tsx`, `UploadHistoryTable.tsx` | `useState` for form and filter state |
| `ArchiveStamp.tsx` | `useEffect` |
| `LoginForm.tsx` | `useState`, form submission |

**The rule they all obey: state or an event handler ⇒ client.** A Server
Component cannot hold `useState` (there is no re-render on the server to hold it
across) and cannot attach an `onClick` (the function cannot be serialised into
HTML).

**And now the subtle one.**
[`EntityComparisonTable.tsx`](../../../frontend/components/document/EntityComparisonTable.tsx)
has **no** `"use client"` — yet it contains:

```tsx
const link = document.createElement("a");
link.href = url;
link.download = buildFilename(entityA, entityB, "csv");
document.body.appendChild(link);
link.click();
```

`document` is a browser global. `onClick` is an event handler. This works only
because the file's **only importer is `page.tsx`**, which is a client component
— so `EntityComparisonTable` is already inside the boundary.

**It is correct today and correct by inheritance, not by declaration.** Import it
from a Server Component and it breaks. That is an observation about the code,
not a defect: nothing imports it from a Server Component, and there is only one
possible Server Component in this app. Hold it as the reason `"use client"` is a
*boundary*: the directive tells you where the client region starts, and file-by-
file inspection tells you nothing about whether a given file is inside it.

---

### 4.7 `layout.tsx` — the one Server Component

```tsx
import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import { SpeedInsights } from "@vercel/speed-insights/next";
import "./globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-fraunces",
});
```

Forty-four lines, and three of them are worth knowing.

**1. `export const metadata: Metadata`** — the `<title>` and `<meta
description>`. Next.js reads this export and emits the tags. **This is only
possible in a Server Component**: the HTML `<head>` is produced during the server
render, before any client code exists.

**2. `next/font/google`** — the fonts are downloaded **at build time** and
self-hosted, not requested from Google at page load. Each returns an object
carrying a `variable` name, applied to `<body>`:

```tsx
className={`${fraunces.variable} ${plexSans.variable} ${plexMono.variable} font-body`}
```

Those CSS custom properties are what the rest of the app reads as
`var(--font-editorial)`, `var(--font-archival)`, `var(--font-ui)` — mapped in
`globals.css`. **This is why every component styles fonts through a variable and
never names a font family**: the actual family is decided once, here.

**3. `{children}`** — the layout does not know what page it wraps. `page.tsx` is
passed in. That is the same containment idea from §4.3, applied at the
framework's outermost level.

---

### 4.8 `DocumentPage.tsx` — a component that is mostly not React

277 lines, `"use client"`, and the reason it is today's walkthrough rather than
`page.tsx` (which is Days 39–40) is that it demonstrates something specific:
**a component whose real work is done by a browser API, with React holding only
the trigger.**

The animation is not CSS transitions and not a React state machine. It is the
**Web Animations API**, called imperatively on a DOM node held in a ref:

```tsx
const sheetRef = useRef<HTMLDivElement>(null);

useEffect(() => {
  if (!sheetRef.current || !shiftPhase) return;
  const sheet = sheetRef.current;
  ...
  const anim = sheet.animate(keyframes, { duration: 660, fill: "forwards" });
```

Three React concepts appear here and each is doing a specific job:

- **`useRef`** — a handle on a real DOM node. State would be wrong: changing a
  ref does not re-render, and you do not want a re-render, you want to touch the
  element.
- **`useEffect`** — "run this after render, when `shiftPhase` changes." The
  dependency array `[shiftPhase, onSheetTransitionEnd]` is the trigger condition.
- **The cleanup return** — the function `useEffect` returns runs before the next
  effect and on unmount.

And the comments record two measurements you should read as engineering, not
decoration:

```tsx
// No plateau: shadow evolves continuously alongside position instead of
// freezing the transform to let shadow "catch up" — that identical-
// keyframe pattern is exactly what tested as a stutter previously.
```

```tsx
anim.onfinish = () => {
  // Do not cancel here — fill:"forwards" holds the old sheet locked
  // invisibly off-screen while React updates state and reconciles.
  onSheetTransitionEnd?.();
};
```

**The second one is the React lesson.** `fill: "forwards"` keeps the animation's
final frame applied after it finishes. Cancelling at `onfinish` would snap the
old sheet back to its resting position **for the one frame** between the
animation ending and React re-rendering with the new page's content. The
animation is deliberately left holding the element until the cleanup function
runs — which is *after* React has painted.

**That is a race between an imperative browser API and React's render cycle,
resolved by choosing where to clean up.** You will not learn that from a
tutorial's counter example, and it is why this file is worth reading in full.

---

### 4.9 Why not Streamlit

`ENGINEERING_DECISIONS.md` **ED-023**:

> **Decision.** The blueprint specified Streamlit; the shipped UI is Next.js.
> `streamlit_frontend_archive/` retains the original.
>
> **Why (as recorded in `IMPLEMENTATION_DELTAS.md` §C).** Deliberate override.
> **Likely rationale — inferred:** the SSE execution trace, the paged
> working-paper metaphor, and per-component role gating are not expressible in
> Streamlit's rerun-the-script model.
>
> **Trade-offs.** Gained: a real UI with real streaming. Sacrificed: a build
> step, a second language, and a second deploy target (Vercel).

**Note the word `inferred`.** The decision is recorded; the reasoning is marked
as reconstruction, not testimony. Day 40 makes a great deal of that distinction
— start noticing it now.

**Why "rerun-the-script" is the crux.** Streamlit's execution model re-runs the
entire script top to bottom on every interaction. Three of this app's properties
fight that directly:

| Requirement | Streamlit's model |
|---|---|
| Nine SSE node events arriving over ~3 s, each updating one line | The script would have to re-run per event, redrawing everything |
| A stack of working papers you page back through, each holding its own trace | Requires持 persistent per-page state across reruns |
| `role_filtered_response` output rendered differently per role, per component | Expressible, but as branching in one long script |

**And the honest counterweight**: everything above is achievable in Streamlit
with enough `st.session_state` and `st.empty()` placeholder juggling. The real
claim ED-023 makes is about **layout control** — the working-paper metaphor with
its paper texture, its corner fold, its shadow that responds to hover — and that
one is not arguable. Streamlit does not offer it.

`streamlit_frontend_archive/` is still in the tree. Open it if you want the
comparison; nothing in this course depends on it.

---

## 5. The actual LedgerMind files

```
File:  frontend/app/layout.tsx (44 lines)                    SERVER COMPONENT
Why:   the HTML shell, <head> metadata, and the three font variables
       every other component reads through var(--font-*)
Imports: next/font/google, @vercel/speed-insights, ./globals.css
Exports: metadata (Next.js reads this), RootLayout (default)
In:    children — whatever page.tsx renders
Out:   <html><body className="...font vars...">{children}</body></html>

File:  frontend/app/page.tsx (584 lines)                     CLIENT BOUNDARY
Why:   the only route. Holds ALL application state; the only file that
       knows the five views exist.
Today: structure only — the state hooks are Day 39, composeDocumentBody
       is Day 40.
Imports: 21 components, lib/api, lib/auth
Out:   the whole application

File:  frontend/components/document/DocumentPage.tsx (277)   CLIENT
Why:   the paper sheet itself: two stacked canvases (active + underneath),
       the Web Animations sheet-shift, the footer with DOC ID and page
       number.
In:    docId, pageNumber, totalPages, confidential, isLoading, shiftPhase,
       onSheetTransitionEnd, underneathContent, children
Out:   the sheet, with children rendered inside it
Note:  the second canvas exists so the NEXT page is already painted
       underneath before the current one animates away.

Counts, measured (`ls frontend/components/**/*.tsx | wc -l`):
  components/           4   (AnswerCard, ConfidenceBadge, CorpusPanel, LoginForm)
  components/document/ 20
  components/environment/ 4
```

> **A note on counts.** `00_LEARNING_MAP.md` §F says "23 components" under
> `components/document/*`. The measured count is **20** `.tsx` files plus
> `globals.css`. Count from the tree, not from the map — that is the same rule
> the map itself states about README-vs-code disagreement, applied to the map.

---

## 6. Deep walkthrough — one render of `DocumentPage`

**STATE BEFORE.** `page.tsx` has just called:

```tsx
<DocumentPage
  docId={getDocId(ledgerCurrentPage)}
  pageNumber={ledgerCurrentPage}
  totalPages={ledgerTotalPages}
  confidential
  isLoading={isLoading}
  shiftPhase={shiftPhase}
  onSheetTransitionEnd={handleSheetTransitionEnd}
  underneathContent={pendingPageIndex !== null ? renderSheetContent(pendingPageIndex) : undefined}
  ...
>
  {renderSheetContent(ledgerCurrentPage)}
</DocumentPage>
```

**Note `confidential` with no value.** In JSX a bare prop name means `={true}`.
It reaches the component as `confidential: true`.

**Note that `children` is not written as a prop.** Anything between the opening
and closing tags arrives as `props.children`. `renderSheetContent(...)` is called
**by the parent**, at the call site, and its *result* is passed down. The child
does not call it.

**Step 1 — local state.**

```tsx
const [isHovered, setIsHovered] = useState(false);
const sheetRef = useRef<HTMLDivElement>(null);
```

`isHovered` is genuinely local: no other component needs it, so it does not get
lifted (Day 39's topic). `sheetRef` starts `null` and is bound by
`ref={sheetRef}` on the active canvas below.

**Step 2 — the effect, keyed on `shiftPhase`.**

```tsx
useEffect(() => {
  if (!sheetRef.current || !shiftPhase) return;
```

The guard is the normal case: `shiftPhase` is `null` when nothing is animating,
so the effect returns immediately on almost every render.

**Step 3 — the exit animation, when `shiftPhase` is `exiting-next`/`exiting-prev`.**

```tsx
const w = sheet.offsetWidth || 1000;
const dx = (isNext ? 1 : -1) * (w * 0.30);
const dy = -(w * 0.19);
```

**Read `offsetWidth`.** The destination is computed from the element's *measured*
width at animation time, not from a constant — so the sheet flies off by the
same proportion at any viewport size. The `|| 1000` is a fallback for a
zero-width measurement (an element not yet laid out).

**Step 4 — four keyframes, and the offsets are fractions of the total.**

```tsx
{ ... easing: "ease-in", offset: 0 },
{ ... offset: 45 / 660 },
{ ... offset: 225 / 660 },
{ ... offset: 1 },
```

`45 / 660` is written as a division rather than `0.068` **so the timing is
readable as milliseconds against the stated 660 ms duration.** That is a comment
written in arithmetic.

**Step 5 — two animations, one element.**

```tsx
const anim = sheet.animate(keyframes, { duration: 660, fill: "forwards" });
const fadeAnim = sheet.animate([...opacity...], { duration: 660, easing: 'ease-out', fill: "forwards" });
```

Separate because the opacity curve is not the transform curve: the sheet stays
fully opaque until 88 % of the way through, then drops. Merging them would force
one easing on both.

**Step 6 — the handoff.**

```tsx
anim.onfinish = () => { onSheetTransitionEnd?.(); };

return () => {
  anim.cancel();
  fadeAnim.cancel();
  if (sheetRef.current) sheetRef.current.style.opacity = "1";
};
```

`onSheetTransitionEnd` is `page.tsx`'s `handleSheetTransitionEnd`, which sets
`currentPageIndex` to the pending index and moves `shiftPhase` to `"settling"`.
That state change re-runs this effect — and **before the new effect runs, React
runs the cleanup above**, which cancels the held animation and restores opacity.

**The order is the whole trick:**

```
anim.onfinish  →  parent setState  →  React re-render + paint (new content)
               →  effect cleanup (cancel, opacity = 1)  →  new effect ("settling")
```

**What breaks if you cancel in `onfinish` instead.** The old sheet snaps back to
its resting transform and full opacity **for one frame**, before React has
painted the new page — a visible flash of the previous answer in the sheet's
resting position. That is the class of bug the comment is preventing.

**Step 7 — the two canvases.**

The component returns **two** near-identical sheet `<div>`s:

- **Layer 4.5**, `z-[5]`, `pointer-events-none`, renders
  `underneathContent || children`.
- **Layer 5**, `z-10`, `ref={sheetRef}`, renders `children`.

**Why the duplication rather than a shared sub-component?** Read what differs:
the underneath layer has no `ref`, no `willChange`, no `minHeight`, no hover
shadow, and renders a different content source and a different footer
(`underneathDocId ?? docId`, `underneathPageNumber ?? pageNumber`). Factoring it
would need a component taking nine props, five of which exist only to switch
between these two configurations.

**That is a judgement call, and the repository does not state it.** It is worth
holding as an open question rather than a settled decision — see §12 Q20.

**STATE AFTER.** The DOM holds two stacked sheets; the top one may be mid-flight.
`page.tsx`'s `shiftPhase` will be `null` again within ~700 ms.

---

## 7. Data flow — from an HTTP response to a rendered superscript

```
role_filtered_response(final_state, role)          backend, Day 9
        │  JSON over HTTPS
        ▼
lib/api.ts : QueryResponse                          the TS side of the contract
        │  optional fields where the backend OMITS
        ▼
page.tsx : pages[] : { response, originView, trace }   Day 39
        │
        ▼
renderSheetContent(idx)
        │
        ├─► WorkingPaperHeader  companies · fiscal_year · quarter · financial_type
        ├─► DocumentTitle       a string
        ├─► QueryDock           onSubmit callback
        ├─► ExecutionTrace      TraceEvent[]                      Day 39
        └─► composeDocumentBody(data)                             Day 40
                 │  reads data.path / .sql_result / .error / .is_blocked
                 │  ← THE ONLY path-aware function
                 ▼
            AnalysisSection  paragraphs: [{ text, citations: [{index, anchorId}] }]
            EvidenceList     items: [{ index, label, page, relevance?, id }]
                 │
                 ▼
            <sup><a href="#cite-<chunk_id>">1</a></sup>  ...  <div id="cite-<chunk_id>">
```

**Notice the last arrow.** The superscript and the footnote are tied together by
`chunk_id` — an opaque UUID the backend deliberately keeps in the viewer payload:

```python
# chunk_id is included deliberately: it carries no information (opaque UUID)
# but the frontend needs it as a stable DOM anchor id to tie inline
# superscripts to their numbered footnotes. Scores stay stripped.
_VIEWER_CITATION_FIELDS = {"chunk_id", "doc_id", "page_number", "company", ...}
```

**A backend field that exists for a frontend anchor**, documented on the backend
side. That is what a contract looks like when both ends know about it.

---

## 8. Engineering decision — a component tree with one smart node

**Problem.** Render three structurally different answer shapes (a cited
narrative, a verified figure with a ledger table, a two-entity comparison), plus
a refusal and a policy block, without every component learning the pipeline.

**Decision.** Twenty presentational components taking plain props; **one**
function, `composeDocumentBody()`, that reads `path`/`sql_result`/`error` and
chooses. **ED-024.**

| Alternative | Why not |
|---|---|
| **Each component reads `QueryResponse`** | Twenty files change when a fourth path lands. And each becomes untestable without a full response object |
| **One giant `<Answer data={...}>`** | The path branching still exists, just less visibly — and layout logic and routing logic end up in one file |
| **A `path`-keyed component map** (`{ quantitative: QuantView, ... }`) | Cleaner-looking, and genuinely tempting. But the real branches are **not** on `path` alone: `composeDocumentBody` branches on `is_blocked`, `error`, `"entity_a" in sql_result[0]`, `"entity1" in sql_result[0]`, then `path`. A map keyed on `path` would need four of those five checks somewhere anyway |
| **Server Components for the answer** | The answer arrives over SSE after user interaction. There is no server render to attach it to |
| **Streamlit** (the blueprint's choice) | ED-023 — layout control, and a rerun-per-interaction model against a nine-event stream |

**Trade-offs accepted.**

- **`page.tsx` is 584 lines** and holds every piece of state. Deliberate — see
  Day 39 — but it is a real cost, and the file has no module-level comment
  explaining its own structure.
- **Views are state, not routes.** No deep links, no back button, no shareable
  URL for "the audit trail". The App Router could express these as real routes
  with almost no restructuring. **The repository does not record this as a
  decision at all** — it is simply how it was built. Treat it as an unrecorded
  trade-off, not a considered one.
- **Two hand-maintained copies of the sheet markup** in `DocumentPage`. See §6
  Step 7.
- **Inline `style={{...}}` alongside Tailwind classes**, throughout. Design
  tokens live in CSS variables, so the inline styles are mostly
  `var(--token)` lookups rather than hard-coded values — but the two systems do
  coexist in nearly every file.

**Current validity.** Sound for one route and one product surface. The pressure
point is `page.tsx`'s size, not the component design.

**At 10×.** Ten views, three roles and deep-linking would force the views into
real App Router routes, at which point `page.tsx`'s state has to move somewhere
shared. That is the change that ends the current design — not more components.

---

## 9. Failure modes

| Symptom | Cause |
|---|---|
| `Error: useState only works in a Client Component` | A `"use client"` file lost its directive, or a hook was added to a Server Component |
| `document is not defined` at build time | A browser global reached the server render — the `EntityComparisonTable` shape (§4.6) imported from above the boundary |
| Fonts fall back to Georgia / system sans | A `var(--font-*)` token is unset — `layout.tsx` did not apply the variable classes, or `globals.css` did not map them |
| A one-frame flash of the previous answer when paging | The sheet animation was cancelled at `onfinish` rather than in the effect cleanup (§6 Step 6) |
| The sheet animates but the content does not change | `onSheetTransitionEnd` not wired, so `currentPageIndex` never advances |
| Both sheets show the same content | `underneathContent` is `undefined`, so Layer 4.5 falls back to `children` — which is correct when nothing is pending |
| Hot reload does nothing | The bind mount is not live — `docker compose.yml` mounts `./frontend:/app` **and** `/app/.next` as an anonymous volume; a stale `.next` can shadow a rebuild |
| A component renders nothing and no error appears | It returned `null` on a guard — check the `if (…) return null` at the top |

---

## 10. Hands-on experiment

### Experiment 1 — prove there is exactly one route

```bash
find frontend/app -type f
```

Three files. Now count what they pull in:

```bash
grep -c "^import" frontend/app/page.tsx
grep -c "^import" frontend/app/layout.tsx
```

**One route, ~24 imports in `page.tsx`, 4 in `layout.tsx`.** That asymmetry is
the architecture.

### Experiment 2 — map the client boundary yourself

```bash
cd frontend
echo "── client entry points ──"
grep -l '"use client"' -r app components | sort
echo
echo "── no directive ──"
for f in $(find app components -name "*.tsx" | sort); do
  grep -q '"use client"' "$f" || echo "$f"
done
```

Now, for each file in the **second** list, answer: *is it inside the boundary?*
Trace its importers. You should find **exactly one** file that is genuinely
outside — and it is the one that exports `metadata`.

### Experiment 3 — hot reload, and where the string lives

The stack is already up (Day 1). Open `http://localhost:3000`.

```bash
grep -n "Ask the filing" frontend/components/document/QueryDock.tsx
```

Change that placeholder string, save, and watch the browser **without
reloading**. Then change it back.

```bash
git diff --stat frontend/
git checkout -- frontend/components/document/QueryDock.tsx
```

**What you just observed:** Fast Refresh replaced the component's code and
**preserved its state** — whatever you had typed in the box is still there. That
is not a page reload; it is a module swap.

### Experiment 4 — remove `"use client"` and read the error

```bash
cd frontend
cp components/document/QueryDock.tsx /tmp/QueryDock.tsx.bak
sed -i '1{/^"use client";$/d}' components/document/QueryDock.tsx
head -3 components/document/QueryDock.tsx
```

Now watch the dev server:

```bash
docker compose logs --tail 40 frontend
```

You should see a build error naming `useState`. **Read the whole message** — it
tells you which import chain crossed the boundary. Restore immediately:

```bash
cp /tmp/QueryDock.tsx.bak components/document/QueryDock.tsx
git diff --stat frontend/     # must be empty
```

> **`git diff --stat` must print nothing before you continue.** This experiment
> edits a real source file; leaving it edited is how a "docs-only" session
> quietly becomes a code change.

### Experiment 5 — typecheck the whole frontend

```bash
docker compose exec -T -w /app frontend node_modules/.bin/tsc --noEmit
```

Silence is a pass. **Remember what silence does *and does not* prove** —
`tsc` verifies that props match their interfaces. It does not verify that a
component is ever rendered. Day 40 depends on that distinction.

### Experiment 6 — read the props of every document component in one pass

```bash
cd frontend/components/document
for f in *.tsx; do
  echo "── $f"
  grep -n "^interface .*Props\|^export function\|^export const" "$f" | head -4
done
```

**Then answer, for five of them, without opening the file:** what does it
receive, and what does it render? That is today's stated capability.

---

## 11. Try this yourself

> **CLOSE THIS DOCUMENT.**

Open `frontend/app/layout.tsx`, `frontend/app/page.tsx` and
`frontend/components/document/DocumentPage.tsx`:

1. `layout.tsx` has no `"use client"`. Name **two** things it does that it could
   not do if it did.
2. `page.tsx` declares five views; `Sidebar.tsx` declares four. Find the line
   that reconciles them, and say what the fifth view is a continuation of.
3. In `DocumentPage`, find `fill: "forwards"` and the comment beside
   `anim.onfinish`. Explain the ordering it protects, and what the user would see
   if it were wrong.
4. `EntityComparisonTable.tsx` uses `document.createElement` and has no
   `"use client"`. Explain why it works, and name the single change that would
   break it.
5. Pick any three components in `components/document/`. For each, state its
   props and its output **without scrolling into the JSX**.

---

## 12. Self-check questions

**Basic**

1. What is a component, in one sentence, in terms of functions?
2. What are props?
3. Which directory does the App Router route from, and which two filenames are
   special?
4. How many routes does this application have, and how do you prove it?
5. What are the five values of `ActiveView`?

**Code**

6. Where does `children` come from, and who evaluates it?
7. What does `confidential` (with no `=`) pass to `DocumentPage`?
8. Which file is the only Server Component, and what does it export that only a
   Server Component can?
9. What does `useRef` give you that `useState` does not, and why is it the right
   choice in `DocumentPage`?
10. Why are there two sheet `<div>`s in `DocumentPage`?

**Why**

11. Why is `"use client"` a boundary rather than a per-file label?
12. Why does `CitationSummary` return `null` at zero rather than rendering "0
    Supporting Citations"?
13. Why do document components take plain props instead of a `QueryResponse`?
14. Why does `layout.tsx` define fonts as CSS variables instead of each component
    naming a family?
15. Why was Streamlit rejected, and which part of that reasoning is recorded as
    *inferred*?

**Debugging**

16. The dev server reports `useState only works in a Client Component`, naming a
    file that has never used a hook. What do you check?
17. Paging to the next sheet flashes the previous answer for a frame. Which
    line is wrong?
18. Every font renders as a fallback. Name the two files to check, in order.

**System design**

19. Views are state, not routes: no deep links, no back button. Convert them to
    App Router routes on paper — name every file that changes and what happens
    to `page.tsx`'s state.
20. `DocumentPage` holds two near-identical sheet markup blocks. Propose an
    extraction, and state honestly what it costs.

---

## 13. Answer key

> **Only read after attempting.**

### §11

1. **(a)** Export `metadata`, because the `<head>` is produced during the server
   render, before any client bundle exists. **(b)** Keep its imports out of the
   client bundle — `next/font/google` resolves at build time and is never
   shipped. (A third, if you found it: it can render `<html>` and `<body>` at
   all, which only the root layout does.)
2. `activeView={activeView === "upload-history" ? "upload" : activeView}` in the
   `<Sidebar …>` call. `upload-history` is a **continuation of Intake**, reached
   only from the "View Full Upload History →" link inside `UploadPanel` — the
   comment above `type ActiveView` says exactly this and calls it a deliberate
   design choice.
3. **Order:** `anim.onfinish` fires → it calls `onSheetTransitionEnd` →
   `page.tsx` sets `currentPageIndex` and `shiftPhase="settling"` → React
   re-renders and **paints the new content** → only then does the effect cleanup
   run and cancel the animation. `fill: "forwards"` is what holds the old sheet
   locked off-screen through that whole sequence. **If it were wrong** —
   cancelling inside `onfinish` — the old sheet would snap back to its resting
   transform and full opacity for one frame, so the user sees the **previous
   answer flash** in the sheet's normal position before the new one appears.
4. It works because its **only importer is `page.tsx`**, which carries
   `"use client"`, so the file is already inside the client boundary and its
   browser globals and handlers are fine. **The change that breaks it:**
   importing it from a Server Component — in practice, from `app/layout.tsx`, or
   from any new `app/*/page.tsx` that does not declare `"use client"`.
5. Marking scheme, for any three: you should be able to say the prop **names and
   types** and the **single element** rendered. E.g. `MetricCallout` takes
   `{ label, value, status? }` and renders a small uppercase label above a value
   with an optional ✓ / ! / ~ glyph; `LedgerTable` takes
   `{ columns: [string,string,string], rows: LedgerRow[] }` and renders a
   three-column table whose row borders come from `rule`; `EvidenceList` takes
   `{ items: EvidenceItem[] }` and renders numbered footnotes with anchor ids,
   omitting `relevance` when it is absent.

### §12 — Basic

1. A function that takes one object of props and returns markup describing what
   to render.
2. The single argument — the component's input contract, declared as a TypeScript
   `interface` and checked at compile time.
3. `frontend/app/`. `page.tsx` (the route's content) and `layout.tsx` (the
   wrapper).
4. **One.** `find frontend/app -type f` returns exactly three files, of which one
   is `page.tsx`.
5. `"workbench" | "peer" | "audit" | "upload" | "upload-history"`.

### §12 — Code

6. From whatever sits between the component's opening and closing tags. **The
   parent evaluates it** — `renderSheetContent(ledgerCurrentPage)` is called at
   the call site in `page.tsx` and its *result* is handed down.
7. `confidential={true}`. A bare prop name is shorthand for `true` in JSX.
8. `app/layout.tsx`. It exports **`metadata`**, which Next.js turns into
   `<title>` and `<meta>` during the server render.
9. A **mutable handle that does not trigger a re-render** when it changes, and a
   direct reference to the real DOM node. Right in `DocumentPage` because the
   animation must *touch the element* — a re-render is exactly what you do not
   want mid-animation.
10. So the **next** page's content is already painted underneath (Layer 4.5,
    `z-[5]`) before the active sheet (Layer 5, `z-10`) animates away. Without
    it, the sheet would fly off to reveal nothing.

### §12 — Why

11. Because it marks where the **client subtree begins**, and every module
    imported below it joins the client bundle whether or not it carries the
    directive. The proof in this repository is `EntityComparisonTable.tsx`: no
    directive, browser globals, works — because its importer is a client
    component.
12. **Omit rather than substitute** (Zero UI-Hallucination Mandate). A
    citation header on an answer with no citations asserts a section of evidence
    that does not exist.
13. **ED-024**: so that adding a fourth engine path touches **one function**
    rather than twenty components — and so each component is readable and
    testable from its own props alone.
14. Because the family is chosen **once**, in the one file that can load fonts at
    build time, and every consumer reads `var(--font-editorial)` etc. Changing a
    typeface is then a one-file change rather than a repository-wide search.
15. **ED-023.** Recorded reason: deliberate override of the blueprint. Recorded
    rationale: SSE trace, paged working-paper metaphor, per-component role
    gating — **and it is explicitly labelled "Likely rationale — inferred"**,
    meaning the repository establishes the decision but not the author's stated
    reason.

### §12 — Debugging

16. **The import chain, not the file.** The error names where the hook is used,
    but the cause is which entry point pulled it in — a `"use client"` directive
    was removed *somewhere above it*, so a whole subtree fell out of the client
    boundary. Run the Experiment 2 grep and find which entry point is missing its
    directive.
17. `anim.onfinish` is cancelling the animation instead of only calling
    `onSheetTransitionEnd`. The cancel belongs in the effect's **cleanup return**,
    which React runs after it has painted the new content.
18. **(1)** `app/layout.tsx` — are the three `.variable` class names still applied
    to `<body>`? **(2)** `app/globals.css` — are `--font-editorial` /
    `--font-archival` / `--font-ui` still mapped to `--font-fraunces` /
    `--font-plex-mono` / `--font-plex-sans`? Every component reads the *semantic*
    token, so a break in either file falls back silently.

### §12 — System design

19. **Files that change.** Create `app/workbench/page.tsx`, `app/peer/page.tsx`,
    `app/audit/page.tsx`, `app/upload/page.tsx` and
    `app/upload/history/page.tsx` — note the last one nests, which finally
    expresses "a continuation of Intake" in the structure instead of in a
    comment. Add `app/layout.tsx`-level or a new `app/(app)/layout.tsx` holding
    `Sidebar` and `DocumentEnvironment`, so the chrome is not re-rendered per
    route. `Sidebar` swaps `onViewChange` for `next/link` and reads
    `usePathname()` for its active state. `ActiveView` disappears.
    **What happens to state:** this is the hard part. `pages[]`,
    `currentPageIndex`, `traceEvents`, `revisions` and `pending` currently live
    in `page.tsx` and would be **destroyed on every navigation**, because each
    route is a different component tree. They must move up into a client
    component in the shared layout (a React Context provider), or out to a
    store, or be re-fetched — and LedgerMind has no endpoint that returns past
    answers, so re-fetching is not available. **The honest answer is that
    routing is cheap and the state lift is the whole cost.**
    **What it buys:** deep links, browser back, a shareable audit-trail URL, and
    per-route code splitting.
20. **The extraction.** A `<Sheet>` component taking `content`, `docId`,
    `pageNumber`, `totalPages`, `footerLabelOverride`, `confidential`,
    `interactive` (hover shadow + `willChange` + `minHeight`), `sheetRef?` and
    `zIndex`. `DocumentPage` then renders two `<Sheet>`s.
    **What it costs.** Nine props, of which **four exist only to distinguish the
    two call sites** — the classic sign that an abstraction is being derived from
    two instances rather than from a concept. The `ref` becomes optional, which
    weakens the type. And the underneath layer's `pointer-events-none` /
    absolute positioning is layout, not sheet identity, so it has to stay
    outside the component anyway.
    **What would actually justify it:** a *third* sheet. Two is where duplication
    is cheapest to tolerate; three is where it starts to drift. **Say that
    explicitly rather than "it depends"** — the criterion is a third call site,
    not a line count.

---

## 14. MUST REMEMBER

```text
- A component is a FUNCTION: props in, markup out
- frontend/app/ holds THREE files -> exactly ONE route. There is no second
  entry point (half of CAVEAT-026's proof)
- ONE page, FIVE views; Sidebar declares only FOUR. upload-history is a
  continuation of Intake, not a destination
- "use client" marks a BOUNDARY, not a file property. Eleven files carry it;
  app/layout.tsx is the only genuine Server Component
- Everything imported below a "use client" file is in the client bundle
  whether or not it declares the directive (EntityComparisonTable)
- State or an event handler => client component
- layout.tsx exports `metadata` and defines the THREE font variables every
  component reads as var(--font-editorial / --font-archival / --font-ui)
- TWENTY dumb components, ONE smart function (composeDocumentBody) — ED-024
- `confidential` with no value means `={true}`
- children is evaluated by the PARENT and passed down as a value
- tsc --noEmit proves props match interfaces. It proves NOTHING about whether
  a component is rendered
```

## 15. MUST UNDERSTAND

```text
- Why one route + all-state-in-page.tsx is a coherent design and where it ends
  (deep links, ten views, three roles)
- Why the client boundary is a property of the IMPORT GRAPH, so you cannot
  classify a file by looking at the file
- Why the document components must not know about paths: a fourth engine
  should change ONE function
- Why `return null` on an empty count is the omit-rather-than-substitute rule
  and not a micro-optimisation
- Why the sheet animation's cleanup placement is a RACE between an imperative
  browser API and React's paint, and why the fix is WHERE not WHAT
- Why ED-023's rationale is labelled `inferred`, and what that label costs the
  reader who ignores it
```

---

## 16. This connects to

```text
Day 30 — the semantic path, whole        (what gets rendered)
Day 34 — verification                    (what `verified` means)
Day 37 — cross-examination               ← END OF PHASE 10
   ↓
Day 38 — React and Next, against this app     ← PHASE 11 BEGINS
   ↓
Day 39 — state, effects, and the SSE consumer
```

Forward references:

- `page.tsx`'s state hooks and `submitQueryStreaming` → **Day 39**
- `composeDocumentBody()` in full, and the dead-code case study → **Day 40**
- `lib/auth.ts`, `LoginForm`, `UploadPanel` → **Day 41**
- Why `is_blocked` exists at all → **Day 42**
- The frontend has **no test runner** (CAVEAT-022) → **Day 43**
