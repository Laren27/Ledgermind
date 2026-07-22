"use client";

import { useState, useEffect } from "react";
import { getSession, logout } from "@/lib/auth";
import { submitQuery, UnauthorizedError, type QueryResponse } from "@/lib/api";
import LoginForm from "@/components/LoginForm";
import { DocumentEnvironment } from "@/components/document/DocumentEnvironment";
import { DocumentPage } from "@/components/document/DocumentPage";
import { WorkingPaperHeader } from "@/components/document/WorkingPaperHeader";
import { DocumentTitle } from "@/components/document/DocumentTitle";
import { SectionHeading } from "@/components/document/SectionHeading";
import { LedgerTable } from "@/components/document/LedgerTable";
import { EntityComparisonTable } from "@/components/document/EntityComparisonTable";
import { MetricCallout } from "@/components/document/MetricCallout";
import { KeyFinding } from "@/components/document/KeyFinding";
import { AnalysisSection } from "@/components/document/AnalysisSection";
import { EvidenceList } from "@/components/document/EvidenceList";
import { QueryDock } from "@/components/document/QueryDock";
import { Sidebar } from "@/components/document/Sidebar";
import { PageNavigator } from "@/components/document/PageNavigator";
import { AuditLogTable } from "@/components/document/AuditLogTable";

// Strips markdown bold markers and the backend's appended "Sources:" suffix.
// Gemini's raw prose sometimes includes **bold** and the response_generator
// appends a plain-text citations block — neither should reach the DOM as-is.
function cleanProseText(text: string): string {
  return text
    .replace(/\n\nSources:[\s\S]*$/, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/^\s*\*\s+/gm, "— ")
    .trim();
}

// Strips internal category prefixes like "trading_advice: " that the
// Prompt Shield includes for its own logging/audit purposes but that
// shouldn't leak into user-facing text.
function cleanBlockReason(reason: string): string {
  return reason.replace(/^[a-z_]+:\s*/i, "");
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
        <KeyFinding label="Not Permitted">Query declined under research-tool policy</KeyFinding>
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

  if (data.path === "quantitative" && data.sql_result?.[0]) {
    const row: any = data.sql_result[0];

    // growth_comparison operation — side-by-side entity table, distinct
    // from the period-over-period YoY shape below.
    if ("entity_a" in row) {
      return (
        <>
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
          <AnalysisSection paragraphs={[{ text: data.response_text ?? "", citations: [] }]} />
        </>
      );
    }

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
        <AnalysisSection paragraphs={[{ text: data.response_text ?? "", citations: [] }]} />
      </>
    );
  }

  return (
    <>
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
  const [currentPageIndex, setCurrentPageIndex] = useState(0); // 1-indexed; 0 = no pages yet
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [revisions, setRevisions] = useState<Record<string, number>>({});
  const [activeView, setActiveView] = useState<"workbench" | "peer" | "audit">("workbench");

  const currentPage = currentPageIndex > 0 ? pages[currentPageIndex - 1] : null;
  const answer = currentPage?.response ?? null;
  const totalPages = pages.length;
  // The document title reflects what this SPECIFIC page is, not whatever
  // sidebar tab happens to be selected right now — same principle as a
  // real document's title never changing based on which folder you browse from.
  const pageTitle = activeView === "audit"
    ? "Audit Trail"
    : currentPage
    ? (currentPage.originView === "peer" ? "Peer Comparison" : "Query Workbench")
    : (activeView === "peer" ? "Peer Comparison" : "Query Workbench");

  // Draft state (no page yet on this tab) previews the UPCOMING page slot
  // ("3 of 3") rather than resetting to "1 of N" — flipping to a blank tab
  // should feel like turning to the next fresh sheet, not going backward.
  // Audit Trail is a meta-view OF the page sequence, not a page within it —
  // it shows the existing total as-is, with no "preview the next slot" logic.
  const displayPageNumber = activeView === "audit"
    ? totalPages
    : currentPageIndex > 0 ? currentPageIndex : totalPages + 1;
  const displayTotalPages = activeView === "audit"
    ? totalPages
    : currentPageIndex > 0 ? totalPages : totalPages + 1;

  if (!sessionChecked) {
    return null; // matches server's initial render — avoids hydration mismatch
  }

  if (!session) {
    return <LoginForm onSuccess={() => setSession(getSession())} />;
  }

  async function handleSubmit(query: string) {
    setIsLoading(true);
    setError(null);
    try {
      const result = await submitQuery(query);
      setPages((prev) => {
        const next = [...prev, { response: result, originView: activeView === "peer" ? "peer" as const : "workbench" as const }];
        setCurrentPageIndex(next.length); // jump to the newly-appended page
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

  return (
    <DocumentEnvironment surface="desk">
      <div className="flex min-h-screen">
        <Sidebar
          userRole={session.role}
          tenantId={session.tenantId}
          activeView={activeView}
          onViewChange={(view) => {
            setActiveView(view);
            // Switching workspace tabs starts a fresh draft page matching
            // that tab, rather than continuing to show whatever page was
            // open under the previous tab — title/dock update immediately.
            if (view !== "audit") setCurrentPageIndex(0);
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

        <div className="flex-1 py-12">
          <DocumentPage
            docId={answer ? `LM-WP-${answer.request_id.slice(0, 6).toUpperCase()}` : "LM-WP-PENDING"}
            pageNumber={displayPageNumber}
            totalPages={displayTotalPages}
            footerLabelOverride={activeView === "audit" ? `${totalPages} ${totalPages === 1 ? "ENTRY" : "ENTRIES"} LOGGED` : undefined}
            confidential
            isLoading={isLoading}
          >
            <WorkingPaperHeader
              company={answer?.company ?? null}
              fiscalYear={answer?.fiscal_year ?? null}
              quarter={answer?.quarter ?? null}
              financialType={answer?.financial_type ?? null}
              wpRef={answer ? `WP-${(answer.path ?? "GEN").toUpperCase()}-${answer.request_id.slice(0, 4)}` : "WP-PENDING"}
              revision={answer ? revisions[answer.query] ?? 1 : 1}
              preparer={session.role}
            />

            <DocumentTitle>{pageTitle}</DocumentTitle>

            <QueryDock
              onSubmit={handleSubmit}
              isLoading={isLoading}
              suggestions={
                activeView === "peer"
                  ? [
                      "Who grew revenue faster in FY26, Eternal or Paytm?",
                      "Compare Eternal's and Paytm's PAT for FY26",
                    ]
                  : undefined
              }
            />

            {activeView === "audit" ? (
              <AuditLogTable
                entries={pages.map((p, i) => ({
                  pageNumber: i + 1,
                  query: p.response.query,
                  path: p.response.path,
                  confidenceTier: p.response.confidence_tier,
                  latencyMs: p.response.latency_ms,
                }))}
                onJump={(n) => { setCurrentPageIndex(n); setActiveView("workbench"); }}
              />
            ) : (
              <>
                {answer && composeDocumentBody(answer)}
                {error && <AnalysisSection paragraphs={[{ text: error, citations: [] }]} />}
              </>
            )}
          </DocumentPage>

          {activeView !== "audit" && totalPages > 0 && (
            <PageNavigator current={currentPageIndex} total={totalPages} onNavigate={setCurrentPageIndex} />
          )}
        </div>
      </div>
    </DocumentEnvironment>
  );
}
