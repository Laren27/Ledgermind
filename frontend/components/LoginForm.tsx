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
        <span
          className="flex h-[22px] w-[22px] items-center justify-center rounded-md text-[11px] font-bold"
          style={{
            background:
              "linear-gradient(to bottom right, var(--color-turquoise-500), var(--color-turquoise-700))",
            color: "#06110E",
          }}
        >
          L
        </span>
        LedgerMind
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-card border p-7"
        style={{
          background: "var(--theme-surface-card)",
          borderColor: "var(--theme-border-hairline)",
        }}
      >
        <div>
          <label className="mb-1.5 block text-xs" style={{ color: "var(--color-slate-300)" }}>
            Email
          </label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-[color:var(--theme-border-hairline)] bg-white/[0.03] px-3.5 py-2.5 text-sm outline-none focus:border-[color:var(--theme-border-focus)] font-body"
            style={{ color: "var(--color-slate-100)" }}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs" style={{ color: "var(--color-slate-300)" }}>
            Password
          </label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-[color:var(--theme-border-hairline)] bg-white/[0.03] px-3.5 py-2.5 text-sm outline-none focus:border-[color:var(--theme-border-focus)] font-body"
            style={{ color: "var(--color-slate-100)" }}
          />
        </div>

        {error && <p className="text-xs" style={{ color: "var(--color-coral-500)" }}>{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="mt-1 rounded-xl py-2.5 text-[13px] font-semibold font-display disabled:opacity-60"
          style={{ background: "var(--color-turquoise-500)", color: "#06110E" }}
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
