"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AgentTimeline } from "@/components/AgentTimeline";
import { AskPanel } from "@/components/AskPanel";
import { EntropyMap } from "@/components/EntropyMap";
import { FragmentDetail } from "@/components/FragmentDetail";
import { FragmentTable } from "@/components/FragmentTable";
import { api, formatBytes } from "@/lib/api";
import type { AgentEvent, Fragment, HealthResponse, SessionDetail } from "@/lib/types";

export default function Dashboard() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [fragments, setFragments] = useState<Fragment[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [imagePath, setImagePath] = useState("");
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setError("Backend is not reachable. Start the API on port 8000."));
    return () => sourceRef.current?.close();
  }, []);

  const loadResults = useCallback(async (id: string) => {
    const [session, list] = await Promise.all([api.getSession(id), api.fragments(id)]);
    setDetail(session);
    setFragments(list.fragments);
    setSelectedId((current) => current ?? list.fragments[0]?.fragment_id ?? null);
  }, []);

  /**
   * Opens the SSE stream, then starts the analysis.
   *
   * The order matters. Subscribing first means the first events cannot be missed
   * in the gap between the POST returning and the stream connecting, which on a
   * small image is where the entire scan stage would otherwise happen.
   */
  const startAnalysis = useCallback(
    async (id: string) => {
      setRunning(true);
      setEvents([]);
      setFragments([]);
      setDetail(null);
      setSelectedId(null);
      setError(null);

      sourceRef.current?.close();
      const source = new EventSource(api.streamUrl(id));
      sourceRef.current = source;

      source.onmessage = (message) => {
        const event = JSON.parse(message.data) as AgentEvent;
        setEvents((previous) => [...previous, event]);
        if (event.stage === "complete") {
          source.close();
          setRunning(false);
          void loadResults(id);
        } else if (event.stage === "failed") {
          source.close();
          setRunning(false);
          setError(event.message);
        }
      };

      source.onerror = () => {
        source.close();
        setRunning(false);
      };

      try {
        await api.analyze(id);
      } catch (caught) {
        source.close();
        setRunning(false);
        setError(caught instanceof Error ? caught.message : "could not start the analysis");
      }
    },
    [loadResults],
  );

  async function handleUpload(file: File) {
    try {
      setError(null);
      const session = await api.upload(file);
      setSessionId(session.session_id);
      await startAnalysis(session.session_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "upload failed");
    }
  }

  async function handlePath() {
    if (!imagePath.trim()) return;
    try {
      setError(null);
      const session = await api.createFromPath(imagePath.trim());
      setSessionId(session.session_id);
      await startAnalysis(session.session_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "could not open that path");
    }
  }

  const selected = fragments.find((fragment) => fragment.fragment_id === selectedId) ?? null;
  const percent = events.at(-1)?.percent ?? 0;
  const verdicts = detail?.verdict_stats;

  return (
    <main className="mx-auto max-w-[1560px] p-4">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-slate-100">FlashForensics AI</h1>
          <p className="text-[11px] text-slate-500">
            Agentic recovery for corrupted flash storage: filesystem parsing, entropy-guided carving,
            evidence-based verdicts
          </p>
        </div>
        {health && (
          <div className="flex items-center gap-3 font-mono text-[10px] text-slate-600">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
              v{health.version}
            </span>
            <span>{health.signatures} signatures</span>
            <span>{health.knowledge_base.formats_indexed} formats indexed</span>
            <span title={health.llm.note ?? ""}>llm: {health.llm.provider}</span>
            <span title={health.knowledge_base.note ?? ""}>
              embed: {health.knowledge_base.semantic ? "minilm" : "lexical"}
            </span>
          </div>
        )}
      </header>

      <section className="panel mb-4 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="cursor-pointer rounded border border-ink-600 bg-ink-800 px-3 py-1.5 text-[12px] font-medium text-slate-200 transition-colors hover:bg-ink-700">
            Upload a disk image
            <input
              type="file"
              className="hidden"
              disabled={running}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleUpload(file);
              }}
            />
          </label>

          <span className="text-[11px] text-slate-600">or analyse one already on the server</span>

          <input
            value={imagePath}
            onChange={(event) => setImagePath(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && handlePath()}
            placeholder="/path/to/card.img"
            disabled={running}
            className="min-w-[280px] flex-1 rounded border border-ink-700 bg-ink-950 px-2.5 py-1.5 font-mono text-[11px] outline-none placeholder:text-slate-600 focus:border-ink-600"
          />
          <button
            onClick={handlePath}
            disabled={running || !imagePath.trim()}
            className="rounded border border-ink-600 bg-ink-800 px-3 py-1.5 text-[12px] text-slate-200 transition-colors hover:bg-ink-700 disabled:opacity-40"
          >
            {running ? "Analysing…" : "Analyse"}
          </button>

          {detail?.status === "complete" && (
            <button
              onClick={() => sessionId && void api.exportAll(sessionId, "RECOVERABLE")}
              className="rounded border border-emerald-700/50 bg-emerald-600/10 px-3 py-1.5 text-[12px] text-emerald-300 transition-colors hover:bg-emerald-600/20"
            >
              Export recoverable
            </button>
          )}
        </div>

        {error && <div className="mt-2 text-[11px] text-red-400">{error}</div>}
      </section>

      {(running || events.length > 0) && (
        <section className="panel mb-4 p-3">
          <AgentTimeline events={events} running={running} percent={percent} />
        </section>
      )}

      {detail?.report && (
        <section className="panel mb-4 border-l-2 border-l-sky-600 p-4">
          <div className="stat-label mb-1.5">What happened to this device</div>
          <p className="text-[13px] leading-relaxed text-slate-300">{detail.report}</p>
        </section>
      )}

      {verdicts && (
        <section className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-6">
          <Stat label="Recoverable" value={verdicts.recoverable} tone="text-signal-recover" />
          <Stat label="Partial" value={verdicts.partial} tone="text-signal-partial" />
          <Stat label="Metadata only" value={verdicts.metadata_only} tone="text-signal-meta" />
          <Stat label="Junk" value={verdicts.junk} tone="text-signal-junk" />
          <Stat label="Data recovered" value={formatBytes(verdicts.bytes_recoverable)} tone="text-slate-200" />
          <Stat
            label="Elapsed"
            value={detail?.elapsed_seconds ? `${detail.elapsed_seconds}s` : "-"}
            tone="text-slate-200"
          />
        </section>
      )}

      {detail && detail.entropy.points.length > 0 && (
        <section className="panel mb-4">
          <div className="panel-header flex items-center justify-between">
            <span>Volume entropy map</span>
            <span className="font-mono normal-case tracking-normal text-slate-600">
              {detail.filesystem} · {formatBytes(detail.image_size)} ·{" "}
              {detail.entropy.anomalies.length} anomalies
            </span>
          </div>
          <div className="p-3">
            <EntropyMap
              points={detail.entropy.points}
              detail={detail.entropy.detail}
              anomalies={detail.entropy.anomalies}
              fragments={fragments}
              imageSize={detail.image_size}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </div>
        </section>
      )}

      {detail && detail.damage.length > 0 && (
        <section className="panel mb-4">
          <div className="panel-header">Damage recorded while parsing</div>
          <ul className="divide-y divide-ink-850">
            {detail.damage.slice(0, 8).map((item, index) => (
              <li key={index} className="flex gap-3 px-4 py-2 text-[12px]">
                <span className="w-44 shrink-0 font-mono text-[10px] uppercase text-amber-500/80">
                  {item.kind.replace(/_/g, " ")}
                </span>
                <span className="text-slate-400">{item.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {fragments.length > 0 && (
        <section className="grid gap-4 lg:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
          <div className="panel h-[560px] overflow-hidden">
            <FragmentTable fragments={fragments} selectedId={selectedId} onSelect={setSelectedId} />
          </div>

          <div className="flex h-[560px] flex-col gap-4">
            <div className="panel min-h-0 flex-1 overflow-hidden">
              <div className="panel-header">Evidence</div>
              <div className="h-[calc(100%-38px)]">
                <FragmentDetail fragment={selected} sessionId={sessionId ?? ""} />
              </div>
            </div>
            <div className="panel h-[240px] overflow-hidden">
              <div className="panel-header">Ask about the recovered files</div>
              <div className="h-[calc(100%-38px)]">
                <AskPanel
                  sessionId={sessionId ?? ""}
                  ready={detail?.status === "complete"}
                  onCite={setSelectedId}
                />
              </div>
            </div>
          </div>
        </section>
      )}

      {!running && fragments.length === 0 && !error && (
        <section className="panel p-10 text-center">
          <p className="text-sm text-slate-400">Upload a disk image, or point at one on the server, to begin</p>
          <p className="mx-auto mt-2 max-w-lg text-[11px] leading-relaxed text-slate-600">
            Generate a damaged test card with{" "}
            <code className="rounded bg-ink-850 px-1 py-0.5 font-mono">
              python tools/make_fixture.py --output fixtures/card.img
            </code>{" "}
            and paste that path above.
          </p>
        </section>
      )}
    </main>
  );
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div className="panel px-3 py-2.5">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone}`}>{value}</div>
    </div>
  );
}
