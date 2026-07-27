"use client";

import { useState, useEffect } from "react";
import { getSession, logout } from "@/lib/auth";
import { submitQuery, UnauthorizedError, type QueryResponse } from "@/lib/api";
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

const PEER_ENTITIES = ["Eternal", "Paytm"]; // Titan excluded: no annual-aggregate revenue in corpus, growth_comparison always fails for it (known limitation)

type ActiveView = "workbench" | "peer" | "audit" | "upload";

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

function DocumentBodySkeleton() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="h-6 w-1/2 rounded" style={{ background: "var(--ink-divider)" }} />
      <div className="h-4 w-full rounded" style={{ background: "var(--ink-divider)" }} />
      <div className="h-4 w-5/6 rounded" style={{ background: "var(--ink-divider)" }} />
    </div>
  );
}

function buildCitationItems(data: QueryResponse) {
  return (data.citations ?? []).map((c, i) => ({
    index: i + 1,
    label: `${c.company} ${c.fiscal_year} (${c.financial_type})`,
    page: c.page_number,
    relevance: c.reranker_score,
    id: `cite-${c.chunk_id}`,
  }));
}

function composeDocumentBody(data: QueryResponse) {
  if (data.is_blocked) {
    return (
      <>
        <MetricCallout label="Not Permitted" value="Policy Block" status="refused" />
        <AnalysisSection
          paragraphs={[{
            text: data.block_reason ? cleanBlockReason(data.block_reason) : "This question falls outside factual research scope.",
            citations: [],
          }]}
        />
      </>
    );
  }

  if (data.error) {
    const errorCitations = buildCitationItems(data);
    const errorText = data.response_text
      ? cleanProseText(data.response_text)
      : "This could not be resolved from the indexed corpus.";
    return (
      <>
        <MetricCallout label={data.error.replace(/_/g, " ")} value="—" status="refused" />
        <CitationSummary count={errorCitations.length} />
        <AnalysisSection
          paragraphs={[{
            text: errorText,
            citations: errorCitations.map((c) => ({ index: c.index, anchorId: c.id })),
          }]}
        />
        <EvidenceList items={errorCitations} />
      </>
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
            {data.company} — {data.fiscal_year ?? "Period"}
          </SectionHeading>
        )}
        {rows.length > 0 && <LedgerTable columns={["PERIOD", "VALUE", "Δ YoY"]} rows={rows} />}
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
    <>
      <CitationSummary count={citationItems.length} />
      <AnalysisSection
        paragraphs={[{
          text: cleanProseText(data.response_text ?? ""),
          citations: citationItems.map((c) => ({ index: c.index, anchorId: c.id })),
        }]}
      />
      <EvidenceList items={citationItems} />
    </>
  );
}

export default function Home() {
  const [session, setSession] = useState<ReturnType<typeof getSession>>(null);
  const [sessionChecked, setSessionChecked] = useState(false);

  useEffect(() => {
    setSession(getSession());
    setSessionChecked(true);
  }, []);

  interface Page { response: QueryResponse; originView: "workbench" | "peer"; }
  const [pages, setPages] = useState<Page[]>([]);
  const [currentPageIndex, setCurrentPageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [revisions, setRevisions] = useState<Record<string, number>>({});
  const [activeView, setActiveView] = useState<ActiveView>("workbench");

  const [shiftPhase, setShiftPhase] = useState<ShiftPhase>(null);
  const [pendingPageIndex, setPendingPageIndex] = useState<number | null>(null);

  const currentPage = currentPageIndex > 0 && currentPageIndex <= pages.length ? pages[currentPageIndex - 1] : null;
  const answer = currentPage?.response ?? null;
  const totalPages = pages.length;

  const pageTitle = activeView === "audit"
    ? "Audit Trail"
    : currentPage
    ? (currentPage.originView === "peer" ? "Peer Comparison" : "Query Workbench")
    : (activeView === "peer" ? "Peer Comparison" : "Query Workbench");

  const ledgerTotalPages = activeView === "audit"
    ? totalPages
    : totalPages + 1;

  const ledgerCurrentPage = activeView === "audit"
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

    try {
      const result = await submitQuery(query, executionContext as any);
      setPages((prev) => {
        const next = [...prev, { response: result, originView: activeView === "peer" ? "peer" as const : "workbench" as const }];
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
      : targetPage
      ? (targetPage.originView === "peer" ? "Peer Comparison" : "Query Workbench")
      : (activeView === "peer" ? "Peer Comparison" : "Query Workbench");

    return (
      <div key={`sheet-tree-${idx}-${targetAnswer?.request_id ?? "pending"}`} className="flex-1 flex flex-col justify-between space-y-[var(--rhythm-major,72px)]">
        <div>
          <WorkingPaperHeader
            company={targetAnswer?.company ?? null}
            fiscalYear={targetAnswer?.fiscal_year ?? null}
            quarter={targetAnswer?.quarter ?? null}
            financialType={targetAnswer?.financial_type ?? null}
            wpRef={targetAnswer ? `WP-${(targetAnswer.path ?? "GEN").toUpperCase()}-${targetAnswer.request_id.slice(0, 4)}` : "WP-PENDING"}
            revision={targetAnswer ? revisions[targetAnswer.query] ?? 1 : 1}
            preparer={session?.role ?? ""}
          />

          <DocumentTitle>{targetTitle}</DocumentTitle>

          {activeView !== "audit" && (
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
          ) : isLoading && idx === ledgerCurrentPage ? (
            <DocumentBodySkeleton />
          ) : (
            <>
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
        <Sidebar
          userRole={session?.role ?? ""}
          tenantId={session?.tenantId ?? ""}
          activeView={activeView}
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
          indexedFilings={[
            { company: "ETERNAL", period: "FY26", active: true },
            { company: "PAYTM", period: "FY26" },
            { company: "TITAN", period: "Q1FY26" },
          ]}
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

              {/* Paper: positioned by percentage of THIS container (measured from
                  the photo's grid: top-left ~235,258 to bottom-right ~790,955 of
                  1536x1024). Slight rotation matches the photo's own paper tilt.
                  NO independent scroll here — the whole workspace above scrolls
                  together as one unit, so the photo and paper never drift apart. */}
              <div
                className="absolute z-10"
                style={{
                  top: "14%",
                  left: "13%",
                  width: "39%",
                  transform: "rotate(0deg)",
                  transformOrigin: "top left",
                }}
              >
                <UploadPanel />
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 py-12 h-full overflow-y-auto">
            <DocumentPage
              docId={getDocId(ledgerCurrentPage)}
              pageNumber={ledgerCurrentPage}
              totalPages={ledgerTotalPages}
              footerLabelOverride={activeView === "audit" ? `${totalPages} ${totalPages === 1 ? "ENTRY" : "ENTRIES"} LOGGED` : undefined}
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

            {activeView !== "audit" && (
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
