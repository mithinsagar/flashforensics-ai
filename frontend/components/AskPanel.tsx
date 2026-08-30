"use client";

import { useState } from "react";
import { api, formatBytes } from "@/lib/api";
import type { AskResponse } from "@/lib/types";

interface Props {
  sessionId: string;
  ready: boolean;
  onCite: (fragmentId: string) => void;
}

const SUGGESTIONS = [
  "Which photos are fully recoverable?",
  "What documents were found?",
  "Is anything only partially recoverable?",
  "What was deleted from this card?",
];

interface Turn {
  question: string;
  response: AskResponse | null;
  error?: string;
}

/**
 * Natural-language questions over this session's fragments.
 *
 * The citations are the feature. Every answer lists the fragments it was drawn
 * from and each one jumps to that fragment's evidence, so a claim can be checked
 * against the bytes rather than believed. An answer with no citations is
 * visibly an answer with no support.
 */
export function AskPanel({ sessionId, ready, onCite }: Props) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  async function submit(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy || !ready) return;

    setBusy(true);
    setQuestion("");
    setTurns((previous) => [...previous, { question: trimmed, response: null }]);

    try {
      const response = await api.ask(sessionId, trimmed);
      setTurns((previous) =>
        previous.map((turn, index) => (index === previous.length - 1 ? { ...turn, response } : turn)),
      );
    } catch (error) {
      setTurns((previous) =>
        previous.map((turn, index) =>
          index === previous.length - 1
            ? { ...turn, error: error instanceof Error ? error.message : "request failed" }
            : turn,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {turns.length === 0 && (
          <div className="space-y-2">
            <p className="text-[11px] leading-relaxed text-slate-500">
              Ask about what was recovered. Answers cite the fragments they came from.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  disabled={!ready}
                  onClick={() => submit(suggestion)}
                  className="rounded border border-ink-700 px-2 py-1 text-[11px] text-slate-400 transition-colors hover:border-ink-600 hover:text-slate-200 disabled:opacity-40"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, index) => (
          <div key={index} className="space-y-1.5">
            <div className="text-[12px] font-medium text-slate-200">{turn.question}</div>
            {turn.error ? (
              <div className="text-[11px] text-red-400">{turn.error}</div>
            ) : turn.response ? (
              <>
                <div className="whitespace-pre-wrap text-[12px] leading-relaxed text-slate-400">
                  {turn.response.answer}
                </div>
                {turn.response.citations.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {turn.response.citations.map((citation) => (
                      <button
                        key={citation.fragment_id}
                        onClick={() => onCite(citation.fragment_id)}
                        className="rounded border border-ink-700 bg-ink-850 px-1.5 py-0.5 font-mono text-[10px] text-slate-400 transition-colors hover:border-sky-600 hover:text-sky-300"
                        title={`${citation.verdict} · similarity ${citation.similarity}`}
                      >
                        {citation.format} {formatBytes(citation.length)}
                      </button>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="text-[11px] text-slate-600">Searching this session's fragments…</div>
            )}
          </div>
        ))}
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit(question);
        }}
        className="border-t border-ink-700 p-2.5"
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={!ready || busy}
          placeholder={ready ? "Ask about the recovered files" : "Finish an analysis first"}
          className="w-full rounded border border-ink-700 bg-ink-950 px-2.5 py-2 text-[12px] outline-none placeholder:text-slate-600 focus:border-ink-600 disabled:opacity-50"
        />
      </form>
    </div>
  );
}
