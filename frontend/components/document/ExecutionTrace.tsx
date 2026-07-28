"use client";

import { useState } from "react";
import type { TraceEvent } from "@/lib/api";

/**
 * Live pipeline trace, driven entirely by real LangGraph node-completion
 * events streamed from POST /api/query/stream.
 *
 * Every stage shown here corresponds to an actual node in app/engines/graph.py
 * and every detail line is a value read out of real backend state -- nothing
 * is inferred, estimated, or animated on a timer (Zero UI-Hallucination
 * Mandate). Timing appears only when the backend actually sent it, which it
 * does for admin only.
 */

interface Slot {
  key: string;
  label: string | null; // null = resolved at runtime by the router event
}

// Mirrors the real graph topology. The engine slot is deliberately one slot,
// not three: semantic / quantitative / cross are mutually exclusive.
const SLOTS: Slot[] = [
  { key: "prompt_shield", label: "PROMPT SHIELD" },
  { key: "router", label: "ROUTER" },
  { key: "__engine__", label: null },
  { key: "confidence", label: "CONFIDENCE" },
  { key: "response_generator", label: "RESPONSE" },
  { key: "audit_writer", label: "AUDIT" },
];

const ENGINE_NODES = new Set(["semantic_engine", "quant_engine", "cross_engine"]);

function slotKeyFor(node: string): string {
  return ENGINE_NODES.has(node) ? "__engine__" : node;
}

export function ExecutionTrace({
  events,
  isComplete,
}: {
  events: TraceEvent[];
  isComplete: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  const byKey = new Map<string, TraceEvent>();
  for (const e of events) byKey.set(slotKeyFor(e.node), e);

  // A blocked query goes prompt_shield -> audit_writer directly, so the four
  // middle slots never fire. Once AUDIT lands, anything still unseen was
  // genuinely skipped, not pending.
  const auditDone = byKey.has("audit_writer");
  const lastDoneIndex = SLOTS.reduce(
    (acc, s, i) => (byKey.has(s.key) ? i : acc),
    -1
  );

  const totalMs = events.reduce((sum, e) => sum + (e.duration_ms ?? 0), 0);
  const hasTiming = events.some((e) => e.duration_ms !== undefined);
  const engineEvent = byKey.get("__engine__");

  // ── Collapsed summary, shown once the answer has arrived ────────────────
  if (isComplete && !expanded) {
    const parts = [
      engineEvent?.label ?? "PIPELINE",
      `${byKey.size} ${byKey.size === 1 ? "STAGE" : "STAGES"}`,
    ];
    if (hasTiming) parts.push(`${(totalMs / 1000).toFixed(1)}s`);

    return (
      <button
        onClick={() => setExpanded(true)}
        className="mb-6 block text-left"
        style={{
          fontFamily: "var(--font-body)",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--paper-text-muted)",
        }}
      >
        {parts.join(" · ")}
        <span style={{ marginLeft: 8, color: "var(--paper-verified)" }}>✓</span>
        <span style={{ marginLeft: 8, opacity: 0.6 }}>▸ trace</span>
      </button>
    );
  }

  return (
    <div className="mb-8">
      <div
        className="mb-3 flex items-baseline justify-between"
        style={{
          fontFamily: "var(--font-body)",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--paper-text-muted)",
        }}
      >
        <span>Execution Trace</span>
        {isComplete && (
          <button onClick={() => setExpanded(false)} style={{ opacity: 0.6 }}>
            ▾ collapse
          </button>
        )}
      </div>

      <div>
        {SLOTS.map((slot, i) => {
          const event = byKey.get(slot.key);
          const done = event !== undefined;
          const skipped = !done && auditDone;
          const active = !done && !skipped && i === lastDoneIndex + 1;

          const label =
            event?.label ??
            slot.label ??
            (active ? "RESOLVING ROUTE" : "ENGINE");

          return (
            <div
              key={slot.key}
              className="flex items-baseline justify-between py-1.5"
              style={{
                fontFamily: "var(--font-body)",
                fontSize: 12,
                color: done
                  ? "var(--paper-text)"
                  : "var(--paper-text-muted)",
                opacity: skipped ? 0.35 : done || active ? 1 : 0.45,
              }}
            >
              <span className="flex items-baseline gap-2">
                <span
                  style={{
                    fontSize: 11,
                    width: 12,
                    display: "inline-block",
                    color: done ? "var(--paper-verified)" : "inherit",
                  }}
                >
                  {done ? "✓" : skipped ? "—" : active ? "›" : "·"}
                </span>
                <span style={{ letterSpacing: "0.04em" }}>{label}</span>
              </span>

              <span style={{ fontSize: 11, color: "var(--paper-text-muted)" }}>
                {skipped
                  ? "not executed"
                  : event?.detail ?? (active ? "working…" : "")}
                {event?.duration_ms !== undefined && (
                  <span style={{ marginLeft: 10, opacity: 0.7 }}>
                    {event.duration_ms}ms
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
