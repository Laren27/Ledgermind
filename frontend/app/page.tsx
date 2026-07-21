"use client";

import { useEffect, useState } from "react";
import SearchBar from "@/components/SearchBar";
import AnswerCard from "@/components/AnswerCard";
import PipelineTrack from "@/components/PipelineTrack";
import CorpusPanel from "@/components/CorpusPanel";
import LoginForm from "@/components/LoginForm";
import { submitQuery, UnauthorizedError, type QueryResponse } from "@/lib/api";
import { getSession, logout, type StoredSession } from "@/lib/auth";

export default function Home() {
  const [session, setSession] = useState<StoredSession | null | undefined>(undefined);
  const [answer, setAnswer] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSession(getSession());
  }, []);

  async function handleQuery(question: string) {
    setLoading(true);
    setError(null);
    try {
      const result = await submitQuery(question);
      setAnswer(result);
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setSession(null); // bounce back to login — token expired/invalid
        setAnswer(null);
        setError(null);
        return;
      }
      setError(
        err instanceof Error
          ? err.message
          : "Something went wrong reaching the backend."
      );
    } finally {
      setLoading(false);
    }
  }

  // Avoid a flash of the login form before localStorage has been checked.
  if (session === undefined) {
    return null;
  }

  if (!session) {
    return <LoginForm onSuccess={() => setSession(getSession())} />;
  }

  return (
    <main className="mx-auto max-w-[1080px] px-8 py-10 pb-24">
      <div className="mb-24 flex items-center justify-between">
        <div className="flex items-center gap-2 text-base font-semibold font-display">
          <span className="flex h-[22px] w-[22px] items-center justify-center rounded-md bg-gradient-to-br from-teal to-[#1f8b7a] text-[11px] font-bold text-[#06110E]">
            L
          </span>
          LedgerMind
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center rounded-full border border-hairline bg-white/[0.04] px-3.5 py-1.5 font-mono text-[11px] text-text-secondary">
            <span className="mr-1.5 text-[8px] text-teal">●</span>
            {session.role} · {session.tenantId.slice(0, 8)}
          </div>
          <button
            onClick={() => {
              logout();
              setSession(null);
              setAnswer(null);
              setError(null);
            }}
            className="text-[11px] text-text-muted hover:text-text-secondary"
          >
            Sign out
          </button>
        </div>
      </div>

      <div className="mb-28 grid grid-cols-1 items-start gap-6 md:grid-cols-[1.05fr_0.95fr]">
        <div className="pt-6">
          <p className="mb-5 font-mono text-[11px] uppercase tracking-wide text-sky">
            SEBI filings · Indian capital markets
          </p>
          <h1 className="mb-4.5 font-display text-[52px] font-bold leading-[1.1] tracking-tight">
            Not a{" "}
            <span className="font-normal text-text-muted line-through decoration-text-muted/50">
              guess.
            </span>
            <br />
            A{" "}
            <span className="text-teal drop-shadow-[0_0_28px_rgba(62,217,192,0.4)]">
              citation
              <span className="ml-0.5 align-super font-mono text-lg">[1]</span>
              .
            </span>
          </h1>
          <p className="mb-8 max-w-[440px] border-t border-hairline pt-3 font-mono text-xs text-text-muted">
            [1] Every answer below is backed by real retrieved chunks and
            verified SQL — not this line, this one&apos;s just the pitch.
          </p>

          <SearchBar onSubmit={handleQuery} loading={loading} />
          {error && <p className="mt-3 max-w-[440px] text-xs text-coral">{error}</p>}

          <CorpusPanel />
        </div>

        <div className="relative mt-2">
          {answer ? (
            <AnswerCard data={answer} />
          ) : (
            <div className="rounded-card border border-dashed border-hairline p-10 text-center text-sm text-text-muted">
              Ask a question to see a verified answer here.
            </div>
          )}
        </div>
      </div>

      <div className="mb-16">
        <PipelineTrack />
      </div>
    </main>
  );
}
