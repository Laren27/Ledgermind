"use client";

import { useState } from "react";
import { login } from "@/lib/auth";

export default function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(email, password);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto mt-32 max-w-[380px]">
      <div className="mb-8 flex items-center gap-2 text-base font-semibold font-display">
        <span className="flex h-[22px] w-[22px] items-center justify-center rounded-md bg-gradient-to-br from-teal to-[#1f8b7a] text-[11px] font-bold text-[#06110E]">
          L
        </span>
        LedgerMind
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-card border border-hairline bg-card-solid p-7"
      >
        <div>
          <label className="mb-1.5 block text-xs text-text-secondary">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-hairline bg-white/[0.03] px-3.5 py-2.5 text-sm text-text-primary outline-none focus:border-teal/40 font-body"
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs text-text-secondary">Password</label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-hairline bg-white/[0.03] px-3.5 py-2.5 text-sm text-text-primary outline-none focus:border-teal/40 font-body"
          />
        </div>

        {error && <p className="text-xs text-coral">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="mt-1 rounded-xl bg-teal py-2.5 text-[13px] font-semibold text-[#06110E] font-display disabled:opacity-60"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
