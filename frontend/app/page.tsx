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
import { MetricCallout } from "@/components/document/MetricCallout";
import { KeyFinding } from "@/components/document/KeyFinding";
import { AnalysisSection } from "@/components/document/AnalysisSection";
import { EvidenceList } from "@/components/document/EvidenceList";
import { QueryDock } from "@/components/document/QueryDock";
import { Sidebar } from "@/components/document/Sidebar";

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
  const [answer, setAnswer] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [queryCount, setQueryCount] = useState(0);
  const [revisions, setRevisions] = useState<Record<string, number>>({});
  const [activeView, setActiveView] = useState<"workbench" | "peer" | "audit">("workbench");

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
      setAnswer(result);
      setQueryCount((n) => n + 1);
      setRevisions((r) => ({ ...r, [query]: (r[query] ?? 0) + 1 }));
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setSession(null);
        setAnswer(null);
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
          onViewChange={setActiveView}
          onSignOut={() => {
            logout();
            setSession(null);
            setAnswer(null);
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
            pageNumber={Math.max(queryCount, 1)}
            totalPages={Math.max(queryCount, 1)}
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

            <DocumentTitle>Query Workbench</DocumentTitle>

            <QueryDock onSubmit={handleSubmit} isLoading={isLoading} />

            {answer && composeDocumentBody(answer)}
            {error && <AnalysisSection paragraphs={[{ text: error, citations: [] }]} />}
          </DocumentPage>
        </div>
      </div>
    </DocumentEnvironment>
  );
}
