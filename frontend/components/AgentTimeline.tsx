"use client";

import { useEffect, useRef } from "react";
import type { AgentEvent } from "@/lib/types";

const AGENT_COLORS: Record<string, string> = {
  scanner: "text-sky-400",
  carver: "text-violet-400",
  classifier: "text-amber-400",
  adjudicator: "text-emerald-400",
  reporter: "text-slate-300",
  system: "text-slate-500",
};

const PIPELINE = [
  { agent: "scanner", label: "Read the card", detail: "Find the index and map where data sits" },
  { agent: "carver", label: "Pull files out", detail: "Recover data the index has lost" },
  { agent: "classifier", label: "Identify them", detail: "Work out what each recovered file is" },
  { agent: "adjudicator", label: "Check condition", detail: "Decide what survived and what did not" },
  { agent: "reporter", label: "Summarise", detail: "Explain what happened to this card" },
];

interface Props {
  events: AgentEvent[];
  running: boolean;
  percent: number;
}

/**
 * Live view of the pipeline: which agent is working now, and the running log.
 *
 * Worth showing rather than a spinner because the stages are genuinely different
 * kinds of work, and a run that stalls does so in a specific stage. Seeing that
 * the carver has been going for two minutes on a 64 GB card is information; a
 * spinner is not.
 */
export function AgentTimeline({ events, running, percent }: Props) {
  const logRef = useRef<HTMLDivElement>(null);
  const activeAgent = events.at(-1)?.agent ?? null;
  const seenAgents = new Set(events.map((event) => event.agent));

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events.length]);

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-5 gap-1.5">
        {PIPELINE.map((stage) => {
          const isActive = running && activeAgent === stage.agent;
          const isDone = seenAgents.has(stage.agent) && !isActive;
          return (
            <div
              key={stage.agent}
              className={`rounded border px-2.5 py-2 transition-colors ${
                isActive
                  ? "border-sky-500/60 bg-sky-500/10"
                  : isDone
                    ? "border-ink-600 bg-ink-850"
                    : "border-ink-700 bg-ink-900/50"
              }`}
              title={stage.detail}
            >
              <div
                className={`flex items-center gap-1.5 text-[11px] font-semibold ${
                  isActive ? "text-sky-300" : isDone ? "text-slate-300" : "text-slate-600"
                }`}
              >
                {isActive && (
                  <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />
                )}
                {isDone && <span className="text-emerald-500">✓</span>}
                {stage.label}
              </div>
              <div className="mt-0.5 truncate text-[10px] text-slate-600">{stage.detail}</div>
            </div>
          );
        })}
      </div>

      <div className="h-1 overflow-hidden rounded-full bg-ink-800">
        <div
          className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400 transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div
        ref={logRef}
        className="h-40 overflow-y-auto rounded border border-ink-700 bg-ink-950 p-2.5 font-mono text-[11px] leading-relaxed"
      >
        {events.length === 0 ? (
          <div className="text-slate-600">Waiting for the analysis to start</div>
        ) : (
          events.map((event, index) => (
            <div key={index} className="flex gap-2">
              <span className="w-9 shrink-0 text-right text-slate-600">{event.percent}%</span>
              <span className={`w-[86px] shrink-0 ${AGENT_COLORS[event.agent] ?? "text-slate-500"}`}>
                {event.agent}
              </span>
              <span className="text-slate-400">{event.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
