"use client";

import { useState, useEffect, useCallback } from "react";
import { getSession, logout } from "@/lib/auth";
import { submitQueryStreaming, UnauthorizedError, fetchPendingUploads, type QueryResponse, type PendingUpload, type TraceEvent } from "@/lib/api";
import LoginForm from "@/components/LoginForm";
import { DocumentEnvironment } from "@/components/document/DocumentEnvironment";
import { DocumentPage, type ShiftPhase } from "@/components/document/DocumentPage";
import { WorkingPaperHeader } from "@/components/document/WorkingPaperHeader";
import { DocumentTitle } from "@/components/document/DocumentTitle";
import { SectionHeading } from "@/components/document/SectionHeading";
import { LedgerTable } from "@/components/document/LedgerTable";
import { EntityComparisonTable } from "@/components/document/EntityComparisonTable";
import { MetricCallout } from "@/components/document/MetricCallout";
import { AnalysisSection } from "@/components/document/AnalysisSection";
import { EvidenceList } from "@/components/document/EvidenceList";
import { CitationSummary } from "@/components/document/CitationSummary";
import { QueryDock } from "@/components/document/QueryDock";
import { Sidebar } from "@/components/document/Sidebar";
import { PageNavigator } from "@/components/document/PageNavigator";
import { AuditLogTable } from "@/components/document/AuditLogTable";
import { UploadPanel } from "@/components/document/UploadPanel";
import { UploadHistoryTable } from "@/components/document/UploadHistoryTable";
import { ExecutionTrace } from "@/components/document/ExecutionTrace";

const PEER_ENTITIES = ["Eternal", "Paytm"]; // Titan excluded: no annual-aggregate revenue in corpus, growth_comparison always fails for it (known limitation)

// "upload-history" is intentionally NOT part of Sidebar's SidebarView type —
// it's reached only via the in-page "View Full Upload History →" link inside
// UploadPanel, never as a standalone sidebar entry (deliberate design choice:
// it's a continuation of Intake, not a separate product area).
type ActiveView = "workbench" | "peer" | "audit" | "upload" | "upload-history";

function cleanProseText(text: string): string {
  return text
    .replace(/\n\nSources:[\s\S]*$/, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/^\s*\*\s+/gm, "— ")
    .trim();
}

function cleanBlockReason(reason: string): string {
  return reason.replace(/^[a-z_]+:\s*/i, "");
}

/**
 * One heading label from the issuer LIST. The join belongs at the render site:
 * a section heading has room for one line, `companies` may hold zero, one or
 * several, and each of those means something different.
 *
 * Returns null for the empty list rather than a stand-in string. Empty means no
 * issuer resolved and retrieval ran unfiltered across the tenant -- the caller
 * must omit the label, not print a name the system never produced.
 */
function issuerLabel(companies: string[] | null | undefined): string | null {
  if (!companies || companies.length === 0) return null;
  return companies.join(" / ");
}

function buildCitationItems(data: QueryResponse) {
  return (data.citations ?? []).map((c, i) => {
    // financial_type is UNKNOWN for every non-FINANCIAL_STATEMENT chunk by
    // design (see section_classifier.py) — risk disclosures and MD&A are not
    // scoped to standalone or consolidated. Rendering "(unknown)" reads as a
    // classification failure when it is a correct N/A, so the tag is omitted
    // entirely. Its presence then genuinely means "these are that entity's
    // numbers," rather than being noise on every citation.
    const ft = c.financial_type;
    const hasFinancialType = !!ft && ft.toLowerCase() !== "unknown";
    return {
      index: i + 1,
      label: hasFinancialType
        ? `${c.company} ${c.fiscal_year} (${ft})`
        : `${c.company} ${c.fiscal_year}`,
      page: c.page_number,
      relevance: c.reranker_score,
      id: `cite-${c.chunk_id}`,
    };
  });
}

function composeDocumentBody(data: QueryResponse) {
  if (data.is_blocked) {
    return (
      <div style={{ maxWidth: "74ch", margin: 0 }}>
        <MetricCallout label="Not Permitted" value="Policy Block" status="refused" />
        <AnalysisSection
          paragraphs={[{
            text: data.block_reason ? cleanBlockReason(data.block_reason) : "This question falls outside factual research scope.",
            citations: [],
          }]}
        />
      </div>
    );
  }

  if (data.error) {
    const errorCitations = buildCitationItems(data);
    const errorText = data.response_text
      ? cleanProseText(data.response_text)
      : "This could not be resolved from the indexed corpus.";
    return (
      <div style={{ maxWidth: "74ch", margin: 0 }}>
        <MetricCallout label={data.error.replace(/_/g, " ")} value="—" status="refused" />
        <CitationSummary count={errorCitations.length} />
        <AnalysisSection
          paragraphs={[{
            text: errorText,
            citations: errorCitations.map((c) => ({ index: c.index, anchorId: c.id })),
          }]}
        />
        <EvidenceList items={errorCitations} />
      </div>
    );
  }

  const citationItems = buildCitationItems(data);
  const isComparativeResult = data.sql_result?.[0] && "entity_a" in data.sql_result[0];
  const isPlainComparisonResult = data.sql_result?.[0] && "entity1" in data.sql_result[0];

  if (isPlainComparisonResult) {
    const row: any = data.sql_result![0];
    const v1 = Number(row.value1);
    const v2 = Number(row.value2);
    const unitLabel = row.unit === "crore_inr" ? "Cr" : row.unit ?? "";
    const winner: "a" | "b" = v1 > v2 ? "a" : "b";
    const higherEntity = winner === "a" ? row.entity1 : row.entity2;

    return (
      <div className="space-y-6">
        <div className="border-b pb-2" style={{ borderColor: "var(--ink-divider)" }}>
          <h3 style={{ fontFamily: "var(--font-editorial)", fontSize: 18, color: "var(--ink-primary)" }}>
            Comparative Metric Analysis
          </h3>
          <p style={{ fontFamily: "var(--font-archival)", fontSize: 12, color: "var(--ink-metadata)" }}>
            Automated multi-entity evaluation • Path: {(data.path ?? "QUANTITATIVE").toUpperCase()} (Deterministic Override)
          </p>
        </div>

        <SectionHeading sourceTable="audited_financials">
          {row.metric} — {data.fiscal_year ?? "Period"}
        </SectionHeading>

        <EntityComparisonTable
          entityA={row.entity1}
          entityB={row.entity2}
          rows={[{
            label: row.metric,
            valueA: `₹${v1.toLocaleString()} ${unitLabel}`,
            valueB: `₹${v2.toLocaleString()} ${unitLabel}`,
            winner,
          }]}
        />

        <MetricCallout label="Higher Reported Value" value={higherEntity} status="verified" />
        <AnalysisSection paragraphs={[{ text: cleanProseText(data.response_text ?? ""), citations: [] }]} />
      </div>
    );
  }

  if (isComparativeResult) {
    const row: any = data.sql_result![0];
    return (
      <div className="space-y-6">
        <div className="border-b pb-2" style={{ borderColor: "var(--ink-divider)" }}>
          <h3 style={{ fontFamily: "var(--font-editorial)", fontSize: 18, color: "var(--ink-primary)" }}>
            Comparative Growth & Performance Analysis
          </h3>
          <p style={{ fontFamily: "var(--font-archival)", fontSize: 12, color: "var(--ink-metadata)" }}>
            Automated multi-entity evaluation • Path: {(data.path ?? "QUANTITATIVE").toUpperCase()} (Deterministic Override)
          </p>
        </div>

        <SectionHeading sourceTable="audited_financials">
          {row.metric} — {row.fiscal_year}
        </SectionHeading>
        
        <EntityComparisonTable
          entityA={row.entity_a}
          entityB={row.entity_b}
          rows={[{
            label: "YoY Growth",
            valueA: `${row.yoy_a_pct > 0 ? "+" : ""}${row.yoy_a_pct}%`,
            valueB: `${row.yoy_b_pct > 0 ? "+" : ""}${row.yoy_b_pct}%`,
            winner: row.faster_growing_entity === row.entity_a ? "a" : "b",
          }]}
        />
        
        <MetricCallout label="Faster Growing" value={row.faster_growing_entity} status="verified" />
        <AnalysisSection paragraphs={[{ text: cleanProseText(data.response_text ?? ""), citations: [] }]} />
      </div>
    );
  }

  if (data.path === "quantitative" && data.sql_result?.[0]) {
    const row: any = data.sql_result[0];
    const rows = [];
    if ("current_fy" in row) {
      rows.push({ label: row.prior_fy, value: row.prior_value?.toLocaleString?.() ?? row.prior_value, rule: "none" as const });
      rows.push({
        label: row.current_fy,
        value: row.current_value?.toLocaleString?.() ?? row.current_value,
        delta: row.yoy_pct != null ? `${row.yoy_pct > 0 ? "+" : ""}${row.yoy_pct}%` : undefined,
        rule: "single" as const,
      });
    }
    const resultValue =
      "current_value" in row
        ? `₹${Number(row.current_value).toLocaleString()} Cr`
        : "value" in row
        ? `₹${Number(row.value).toLocaleString()} Cr`
        : "—";

    return (
      <>
        {rows.length > 0 && (
          <SectionHeading sourceTable="audited_financials">
            {[issuerLabel(data.companies), data.fiscal_year ?? "Period"]
              .filter(Boolean)
              .join(" — ")}
          </SectionHeading>
        )}
        {rows.length > 0 && <LedgerTable columns={["PERIOD", "VALUE (₹ Cr)", "Δ YoY"]} rows={rows} />}
        <MetricCallout
          label="Result"
          value={resultValue}
          status={data.sql_verified ? "verified" : "estimated"}
        />
        <AnalysisSection paragraphs={[{ text: cleanProseText(data.response_text ?? ""), citations: [] }]} />
      </>
    );
  }

  return (
    <div style={{ maxWidth: "74ch", margin: 0 }}>
      <CitationSummary count={citationItems.length} />
      <AnalysisSection
        paragraphs={[{
          text: cleanProseText(data.response_text ?? ""),
          citations: citationItems.map((c) => ({ index: c.index, anchorId: c.id })),
        }]}
      />
      <EvidenceList items={citationItems} />
    </div>
  );
}

export default function Home() {
  const [session, setSession] = useState<ReturnType<typeof getSession>>(null);
  const [sessionChecked, setSessionChecked] = useState(false);

  useEffect(() => {
    setSession(getSession());
    setSessionChecked(true);
  }, []);

  // dispatchedAt: the client clock when this query was SENT, kept per page so
  // the working paper carries a real generation time instead of a literal.
  // Client-side because the response has no time field; the header labels it
  // LOCAL so it is never mistaken for a server timestamp.
  interface Page { response: QueryResponse; originView: "workbench" | "peer"; trace: TraceEvent[]; dispatchedAt: number; }
  const [pages, setPages] = useState<Page[]>([]);
  const [currentPageIndex, setCurrentPageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [revisions, setRevisions] = useState<Record<string, number>>({});
  const [activeView, setActiveView] = useState<ActiveView>("workbench");

  const [shiftPhase, setShiftPhase] = useState<ShiftPhase>(null);
  const [pendingPageIndex, setPendingPageIndex] = useState<number | null>(null);

  // Upload state lifted here (was previously local to UploadPanel) so both
  // Archive Intake's capped preview and the new Upload History page read
  // from the same fetch — no duplicate requests, no drift between the two.
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [loadingPending, setLoadingPending] = useState(false);

  const loadPending = useCallback(async () => {
    setLoadingPending(true);
    try {
      const rows = await fetchPendingUploads();
      const sorted = [...rows].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
      setPending(sorted);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setSession(null);
      }
      // otherwise silent — this list is a convenience view, not critical path
    } finally {
      setLoadingPending(false);
    }
  }, []);

  useEffect(() => {
    if (session) {
      loadPending();
    }
  }, [session, loadPending]);

  const currentPage = currentPageIndex > 0 && currentPageIndex <= pages.length ? pages[currentPageIndex - 1] : null;
  const answer = currentPage?.response ?? null;
  const totalPages = pages.length;

  const pageTitle = activeView === "audit"
    ? "Audit Trail"
    : activeView === "upload-history"
    ? "Upload History"
    : currentPage
    ? (currentPage.originView === "peer" ? "Peer Comparison" : "Query Workbench")
    : (activeView === "peer" ? "Peer Comparison" : "Query Workbench");

  const isStaticView = activeView === "audit" || activeView === "upload-history";

  const ledgerTotalPages = isStaticView
    ? totalPages
    : totalPages + 1;

  const ledgerCurrentPage = isStaticView
    ? totalPages
    : (currentPageIndex > 0 && currentPageIndex <= totalPages)
      ? currentPageIndex
      : ledgerTotalPages;

  function handleNavigate(targetPage: number) {
    if (shiftPhase !== null || targetPage === currentPageIndex) return;
    const dir = targetPage > currentPageIndex ? "next" : "prev";
    setPendingPageIndex(targetPage);
    setShiftPhase(`exiting-${dir}`);
  }

  function handleSheetTransitionEnd() {
    if (shiftPhase === "exiting-next" || shiftPhase === "exiting-prev") {
      if (pendingPageIndex !== null) setCurrentPageIndex(pendingPageIndex);
      setPendingPageIndex(null);
      setShiftPhase("settling");
    } else if (shiftPhase === "settling") {
      setShiftPhase(null);
    }
  }

  if (!sessionChecked) return null;
  if (!session) return <LoginForm onSuccess={() => setSession(getSession())} />;

  async function handleSubmit(query: string) {
    setIsLoading(true);
    setError(null);

    const executionContext = activeView === "peer" ? {
      workspace_view: "peer_comparison",
      intended_path: "quantitative",
      intended_operation: "growth_comparison",
      enforce_path: true,
      financial_type: "consolidated"
    } : undefined;

    // Collected locally as well as in state: setState is async, so the array
    // attached to the finished page must not depend on a flush having happened.
    const collected: TraceEvent[] = [];
    setTraceEvents([]);

    // Read BEFORE the await, not after: this is the moment the question was
    // asked, and a semantic query can spend 20s in the pipeline before the
    // page object is built.
    const dispatchedAt = Date.now();

    try {
      const result = await submitQueryStreaming(
        query,
        (ev) => {
          collected.push(ev);
          setTraceEvents([...collected]);
        },
        executionContext as any
      );
      setPages((prev) => {
        const next = [...prev, { response: result, originView: activeView === "peer" ? "peer" as const : "workbench" as const, trace: collected, dispatchedAt }];
        setCurrentPageIndex(next.length);
        return next;
      });
      setRevisions((r) => ({ ...r, [query]: (r[query] ?? 0) + 1 }));
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setSession(null);
        setPages([]);
        setCurrentPageIndex(0);
        setError(null);
        return;
      }
      setError(err instanceof Error ? err.message : "Query failed");
    } finally {
      setIsLoading(false);
    }
  }

  function getDocId(idx: number) {
    const targetPage = idx > 0 && idx <= pages.length ? pages[idx - 1] : null;
    const targetAnswer = targetPage?.response ?? null;
    return targetAnswer ? `LM-WP-${targetAnswer.request_id.slice(0, 6).toUpperCase()}` : "LM-WP-PENDING";
  }

  function renderSheetContent(idx: number) {
    const targetPage = idx > 0 && idx <= pages.length ? pages[idx - 1] : null;
    const targetAnswer = targetPage?.response ?? null;
    const targetTitle = activeView === "audit"
      ? "Audit Trail"
      : activeView === "upload-history"
      ? "Upload History"
      : targetPage
      ? (targetPage.originView === "peer" ? "Peer Comparison" : "Query Workbench")
      : (activeView === "peer" ? "Peer Comparison" : "Query Workbench");

    return (
      <div key={`sheet-tree-${idx}-${targetAnswer?.request_id ?? "pending"}`} className="flex-1 flex flex-col justify-between space-y-[var(--rhythm-major,72px)]">
        <div>
          <WorkingPaperHeader
            companies={targetAnswer ? targetAnswer.companies : null}
            fiscalYear={targetAnswer?.fiscal_year ?? null}
            quarter={targetAnswer?.quarter ?? null}
            financialType={targetAnswer?.financial_type ?? null}
            wpRef={targetAnswer ? `WP-${(targetAnswer.path ?? "GEN").toUpperCase()}-${targetAnswer.request_id.slice(0, 4)}` : "WP-PENDING"}
            revision={targetAnswer ? revisions[targetAnswer.query] ?? 1 : 1}
            preparer={session?.role ?? ""}
            generatedAt={targetPage?.dispatchedAt ?? null}
          />

          <DocumentTitle>{targetTitle}</DocumentTitle>

          {activeView !== "audit" && activeView !== "upload-history" && (
            <QueryDock
              key={`query-dock-${idx}-${targetAnswer?.request_id ?? "pending"}`}
              onSubmit={handleSubmit}
              isLoading={isLoading && idx === ledgerCurrentPage}
              entityOptions={activeView === "peer" ? PEER_ENTITIES : undefined}
            />
          )}

          {activeView === "audit" ? (
            <AuditLogTable
              entries={pages.map((p, i) => ({
                pageNumber: i + 1,
                query: p.response.query,
                path: p.response.path,
                confidenceTier: p.response.confidence_tier,
                latencyMs: p.response.latency_ms,
                isSuccess: !p.response.error && !p.response.is_blocked,
              }))}
              onJump={(n) => { setCurrentPageIndex(n); setActiveView("workbench"); }}
            />
          ) : activeView === "upload-history" ? (
            <UploadHistoryTable
              uploads={pending}
              loading={loadingPending}
              onRefresh={loadPending}
              onBack={() => setActiveView("upload")}
            />
          ) : isLoading && idx === ledgerCurrentPage ? (
            <ExecutionTrace events={traceEvents} isComplete={false} />
          ) : (
            <>
              {targetPage && targetPage.trace.length > 0 && (
                <ExecutionTrace events={targetPage.trace} isComplete />
              )}
              {targetAnswer && composeDocumentBody(targetAnswer)}
              {error && idx === ledgerCurrentPage && <AnalysisSection paragraphs={[{ text: error, citations: [] }]} />}
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <DocumentEnvironment surface="desk">
      <div className="flex h-screen w-full overflow-hidden">
        {/* No indexedFilings prop. It was three literals rendered as the live
            corpus index, with ETERNAL flagged `active` and coloured green
            against no backend state -- identical output after a successful
            ingest, a failed one, and against a nine-document database or an
            eleven-document one.

            Nothing produces this. No endpoint returns
            documents.ingestion_state, and GET /api/documents/pending reads a
            different table with a different value domain and is admin-only, so
            it is not a substitute. Sidebar renders an explicit "not available"
            naming that reason; the prop is still in its signature, so
            restoring the panel is one line here. */}
        <Sidebar
          userRole={session?.role ?? ""}
          tenantId={session?.tenantId ?? ""}
          activeView={activeView === "upload-history" ? "upload" : activeView}
          onViewChange={(view) => {
            setActiveView(view);
            if (view !== "audit" && view !== "upload") setCurrentPageIndex(pages.length + 1);
          }}
          onSignOut={() => {
            logout();
            setSession(null);
            setPages([]);
            setCurrentPageIndex(0);
            setError(null);
          }}
        />

        {activeView === "upload" ? (
          /* Same pattern as every other tab: flex-1 h-full overflow-y-auto is the
             ONE scrollbar for this whole pane. Sidebar (above) is a flex sibling
             outside this div entirely, so it never scrolls. */
          <div className="flex-1 flex justify-center py-10 px-6 h-full overflow-y-auto" style={{ background: "#1a120b" }}>
            {/* Workspace container: photo + paper are both children of this ONE
                element, positioned by percentage — so as this container scrolls
                as a single unit (photo included), the paper can never drift from
                the photo. aspectRatio locked to the photo's real 1536x1024
                dimensions so background-size can be 100% 100% (no cropping from
                `cover`). shrink-0 keeps it from being squashed by the flex parent. */}
            <div
              className="relative w-full self-start shrink-0"
              style={{
                maxWidth: 1650,
                aspectRatio: "1536 / 1024",
                backgroundImage: "url(/assets/environment/office-desk.png)",
                backgroundSize: "100% 100%",
                backgroundPosition: "top left",
                borderRadius: 4,
                boxShadow: "0 20px 60px rgba(0,0,0,0.6)",
              }}
            >
              {/* Scrim for header legibility against the wooden filing boxes */}
              <div
                className="absolute top-0 left-0 right-0 pointer-events-none z-[5] rounded-t"
                style={{
                  height: "16%",
                  background: "linear-gradient(180deg, rgba(20,16,12,0.75) 0%, rgba(20,16,12,0.0) 100%)",
                }}
              />

              {/* Paper: positioned by percentage of THIS container (measured against
                  the current desk photo). NO independent scroll here — the whole
                  workspace above scrolls together as one unit, so the photo and
                  paper never drift apart. Registration history inside UploadPanel
                  is now permanently capped at 3 rows, so this box's height never
                  needs to exceed the photographed paper's real bounds. */}
              <div
                className="absolute z-10"
                style={{
                  top: "16.5%",
                  left: "13.7%",
                  width: "37%",
                  transform: "rotate(0deg)",
                  transformOrigin: "top left",
                }}
              >
                <UploadPanel
                  pending={pending}
                  loadingPending={loadingPending}
                  onRefresh={loadPending}
                  onViewHistory={() => setActiveView("upload-history")}
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 py-12 h-full overflow-y-auto">
            <DocumentPage
              docId={getDocId(ledgerCurrentPage)}
              pageNumber={ledgerCurrentPage}
              totalPages={ledgerTotalPages}
              footerLabelOverride={
                activeView === "audit"
                  ? `${totalPages} ${totalPages === 1 ? "ENTRY" : "ENTRIES"} LOGGED`
                  : activeView === "upload-history"
                  ? `${pending.length} ${pending.length === 1 ? "UPLOAD" : "UPLOADS"} LOGGED`
                  : undefined
              }
              confidential
              isLoading={isLoading}
              shiftPhase={shiftPhase}
              onSheetTransitionEnd={handleSheetTransitionEnd}
              underneathContent={pendingPageIndex !== null ? renderSheetContent(pendingPageIndex) : undefined}
              underneathPageNumber={pendingPageIndex !== null ? pendingPageIndex : undefined}
              underneathDocId={pendingPageIndex !== null ? getDocId(pendingPageIndex) : undefined}
            >
              {renderSheetContent(ledgerCurrentPage)}
            </DocumentPage>

            {activeView !== "audit" && activeView !== "upload-history" && (
              <PageNavigator
                current={ledgerCurrentPage}
                total={ledgerTotalPages}
                onNavigate={handleNavigate}
                disabled={shiftPhase !== null}
              />
            )}
          </div>
        )}
      </div>
    </DocumentEnvironment>
  );
}
