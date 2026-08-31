"use client";

import { useEffect, useRef } from "react";
import type { AgentEvent } from "@/lib/types";

const AGENT_TINT: Record<string, string> = {
  scanner: "#5fc9df",
  carver: "#c74ac0",
  classifier: "#f0a92b",
  adjudicator: "#4bd894",
  reporter: "#a99f95",
  system: "#736a62",
};

const PIPELINE = [
  { agent: "scanner", label: "Read the card", detail: "Find the index, map where data sits" },
  { agent: "carver", label: "Pull files out", detail: "Recover data the index has lost" },
  { agent: "classifier", label: "Identify them", detail: "Work out what each file is" },
  { agent: "adjudicator", label: "Check condition", detail: "Decide what survived" },
  { agent: "reporter", label: "Summarise", detail: "Explain what happened" },
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
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="space-y-4">
      {/* The chain, with the connector between two stages carrying the light. */}
      <div className="grid grid-cols-2 gap-y-4 sm:grid-cols-3 lg:grid-cols-5 lg:gap-y-0">
        {PIPELINE.map((stage, index) => {
          const isActive = running && activeAgent === stage.agent;
          const isDone = seenAgents.has(stage.agent) && !isActive;
          const tint = AGENT_TINT[stage.agent];

          return (
            <div key={stage.agent} className="relative pr-4">
              {index < PIPELINE.length - 1 && (
                <span className="absolute right-2 top-[7px] hidden h-px w-[calc(100%-2.6rem)] translate-x-full bg-white/[0.08] lg:block">
                  {isDone && <span className="block h-full w-full" style={{ background: `${tint}55` }} />}
                </span>
              )}

              <div className="flex items-center gap-2">
                <span className="relative flex h-3.5 w-3.5 items-center justify-center">
                  {isActive && (
                    <span
                      className="absolute inline-flex h-full w-full animate-ring rounded-full"
                      style={{ background: tint }}
                    />
                  )}
                  <span
                    className="relative h-2.5 w-2.5 rounded-full transition-all duration-300"
                    style={{
                      background: isActive || isDone ? tint : "transparent",
                      border: isActive || isDone ? "none" : "1.5px solid rgba(255,236,214,0.16)",
                      boxShadow: isActive ? `0 0 10px ${tint}` : "none",
                    }}
                  />
                </span>
                <span
                  className="text-[12.5px] transition-colors duration-300"
                  style={{ color: isActive ? tint : isDone ? "#f4efe8" : "#4b443e" }}
                >
                  {stage.label}
                </span>
              </div>
              <div className="mt-1 pl-[22px] text-[10.5px] leading-relaxed text-faint">{stage.detail}</div>
            </div>
          );
        })}
      </div>

      <div className="relative h-[3px] overflow-hidden rounded-full bg-white/[0.05]">
        <div
          className="h-full rounded-full transition-[width] duration-500 ease-out"
          style={{
            width: `${percent}%`,
            background: "linear-gradient(90deg, #5fc9df, #f0a92b 55%, #4bd894)",
            boxShadow: "0 0 12px rgba(240,169,43,0.5)",
          }}
        />
        {running && (
          <div className="absolute inset-y-0 w-1/3 animate-sweep bg-gradient-to-r from-transparent via-white/25 to-transparent" />
        )}
      </div>

      <div
        ref={logRef}
        className="h-44 overflow-y-auto rounded-lg border border-white/[0.06] bg-black/45 p-3 font-mono text-[11px] leading-[1.7]"
      >
        {events.length === 0 ? (
          <div className="text-faint">
            <span className="text-gold-500">▍</span> waiting for the analysis to start
          </div>
        ) : (
          events.map((event, index) => (
            <div key={index} className="flex gap-3 hover:bg-white/[0.02]">
              <span className="w-9 shrink-0 text-right tabular-nums text-faint">{event.percent}%</span>
              <span className="w-[84px] shrink-0" style={{ color: AGENT_TINT[event.agent] ?? "#736a62" }}>
                {event.agent}
              </span>
              <span className="text-ash">{event.message}</span>
            </div>
          ))
        )}
        {running && <span className="ml-[124px] inline-block animate-flicker text-gold-500">▍</span>}
      </div>
    </div>
  );
}
