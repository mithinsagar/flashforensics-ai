"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Wordmark } from "@/components/brand/Logo";
import { AgentTimeline } from "@/components/AgentTimeline";
import { AskPanel } from "@/components/AskPanel";
import { CountUp } from "@/components/visuals/CountUp";
import { EntropyMap } from "@/components/EntropyMap";
import { FragmentDetail } from "@/components/FragmentDetail";
import { FragmentTable } from "@/components/FragmentTable";
import { StartPanel } from "@/components/StartPanel";
import { VerificationPanel } from "@/components/VerificationPanel";
import { api, formatBytes } from "@/lib/api";
import type {
  AgentEvent,
  DetectedDevice,
  Fragment,
  HealthResponse,
  SessionDetail,
  SessionSummary,
  VerificationResponse,
} from "@/lib/types";

export default function Dashboard() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sourceLabel, setSourceLabel] = useState<string>("");
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [fragments, setFragments] = useState<Fragment[]>([]);
  const [verification, setVerification] = useState<VerificationResponse | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [exported, setExported] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setError("The recovery engine is not running. Start it, then reload this page."));
    return () => sourceRef.current?.close();
  }, []);

  const loadResults = useCallback(async (id: string) => {
    const [session, list] = await Promise.all([api.getSession(id), api.fragments(id)]);
    setDetail(session);
    setFragments(list.fragments);
    setSelectedId((current) => current ?? list.fragments[0]?.fragment_id ?? null);
    try {
      const graded = await api.verification(id);
      setVerification(graded.available ? graded : null);
    } catch {
      setVerification(null);
    }
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
      setVerification(null);
      setSelectedId(null);
      setExported(null);
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

  const begin = useCallback(
    async (open: () => Promise<SessionSummary>, label: string, failure: string) => {
      try {
        setError(null);
        setSourceLabel(label);
        const session = await open();
        setSessionId(session.session_id);
        await startAnalysis(session.session_id);
      } catch (caught) {
        setSourceLabel("");
        setError(caught instanceof Error ? caught.message : failure);
      }
    },
    [startAnalysis],
  );

  const handleDevice = (device: DetectedDevice) =>
    begin(() => api.createFromDevice(device.path), device.label, "that card could not be opened");

  const handleDemo = () =>
    begin(() => api.createDemo(), "Sample damaged card", "the sample card could not be built");

  const handleUpload = (file: File) => begin(() => api.upload(file), file.name, "upload failed");

  const handlePath = (path: string) =>
    begin(() => api.createFromPath(path), path, "could not open that path");

  async function handleExport() {
    if (!sessionId) return;
    try {
      const result = await api.exportAll(sessionId, "RECOVERABLE");
      setExported(`${result.exported} files (${formatBytes(result.bytes)}) written to ${result.archive}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "export failed");
    }
  }

  function reset() {
    sourceRef.current?.close();
    setSessionId(null);
    setSourceLabel("");
    setDetail(null);
    setFragments([]);
    setVerification(null);
    setEvents([]);
    setSelectedId(null);
    setExported(null);
    setError(null);
    setRunning(false);
  }

  const selected = fragments.find((fragment) => fragment.fragment_id === selectedId) ?? null;
  const percent = events.at(-1)?.percent ?? 0;
  const verdicts = detail?.verdict_stats;
  const started = running || events.length > 0 || fragments.length > 0;

  return (
    <>
      <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-[#060505]/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1280px] items-center justify-between gap-4 px-6 py-3.5">
          <button onClick={started ? reset : undefined} className={started ? "cursor-pointer" : "cursor-default"}>
            <Wordmark animated />
          </button>

          <div className="flex items-center gap-3">
            {health && (
              <div className="hidden items-center gap-3 font-mono text-[10px] uppercase tracking-widest text-faint lg:flex">
                <span className="flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full bg-signal-recover shadow-[0_0_8px_rgba(75,216,148,0.8)]" />
                  v{health.version}
                </span>
                <span className="text-white/10">/</span>
                <span>{health.signatures} signatures</span>
                <span className="text-white/10">/</span>
                <span title={health.llm.note ?? ""}>{health.llm.provider} engine</span>
              </div>
            )}
            {started && (
              <button onClick={reset} disabled={running} className="btn-ghost">
                Start over
              </button>
            )}
            <a
              href="https://github.com/mithinsagar/flashforensics-ai"
              target="_blank"
              rel="noreferrer"
              className="text-dim transition-colors hover:text-bone"
              aria-label="Source on GitHub"
            >
              <svg width="17" height="17" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
              </svg>
            </a>
          </div>
        </div>
      </header>

      {error && (
        <div className="mx-auto max-w-[1280px] px-6 pt-4">
          <div className="panel animate-rise flex items-start gap-3 border-l-2 border-l-ember-500 p-4 text-[12.5px] text-ember-400">
            <span className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-ember-500">error</span>
            <span>{error}</span>
          </div>
        </div>
      )}

      {!started && (
        <StartPanel
          running={running}
          health={health}
          onDevice={handleDevice}
          onDemo={handleDemo}
          onUpload={handleUpload}
          onPath={handlePath}
        />
      )}

      {started && (
        <main className="mx-auto max-w-[1280px] space-y-4 px-6 py-6">
          <section className="panel animate-rise relative flex flex-wrap items-center justify-between gap-4 overflow-hidden p-4">
            {running && <div className="live-edge" />}
            <div className="flex min-w-0 items-center gap-4">
              <ProgressRing percent={percent} running={running} />
              <div className="min-w-0">
                <div className="stat-label">{running ? "Recovering" : "Recovered from"}</div>
                <div className="mt-1 truncate text-[16px] text-bone">{sourceLabel || detail?.image_name}</div>
                {detail && (
                  <div className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10px] text-faint">
                    <span>{detail.filesystem ?? "unknown filesystem"}</span>
                    <span>{formatBytes(detail.image_size)}</span>
                    {detail.elapsed_seconds != null && <span>{detail.elapsed_seconds}s elapsed</span>}
                  </div>
                )}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
              {detail?.status === "complete" && verdicts && verdicts.recoverable > 0 && (
                <button onClick={handleExport} className="btn-gold">
                  Save all {verdicts.recoverable} recovered files
                </button>
              )}
            </div>
            {exported && (
              <div className="w-full border-t border-white/[0.06] pt-3 font-mono text-[10.5px] text-signal-recover">
                {exported}
              </div>
            )}
          </section>

          <section className="panel animate-rise p-4">
            <AgentTimeline events={events} running={running} percent={percent} />
          </section>

          {detail?.report && (
            <section className="panel animate-rise relative grid gap-8 overflow-hidden p-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
              <div className="absolute inset-y-0 left-0 w-px bg-gradient-to-b from-transparent via-gold-500/60 to-transparent" />
              <div>
                <div className="stat-label">What happened to this device</div>
                <p className="display mt-3 text-[21px] leading-[1.45] text-bone/90">{detail.report}</p>
              </div>

              {Object.keys(verdicts?.formats ?? {}).length > 0 && (
                <div className="lg:border-l lg:border-white/[0.06] lg:pl-8">
                  <div className="stat-label">Formats found</div>
                  <div className="mt-3.5 flex flex-wrap gap-1.5">
                    {Object.entries(verdicts!.formats)
                      .sort((a, b) => b[1] - a[1])
                      .map(([format, count]) => (
                        <span
                          key={format}
                          className="rounded-md border border-white/[0.07] bg-white/[0.025] px-2 py-1 font-mono text-[10.5px] text-ash"
                        >
                          .{format}
                          <span className="ml-1.5 text-faint">{count}</span>
                        </span>
                      ))}
                  </div>
                </div>
              )}
            </section>
          )}

          {verdicts && (
            <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <Stat label="Fully recovered" value={verdicts.recoverable} tint="#4bd894" hint="These files came back complete and should open normally." />
              <Stat label="Partly damaged" value={verdicts.partial} tint="#f0a92b" hint="Some of the data survived; these may open with pieces missing." />
              <Stat label="Name only" value={verdicts.metadata_only} tint="#5fc9df" hint="The card remembers these existed, but the contents are gone." />
              <Stat label="Not real files" value={verdicts.junk} tint="#6d635a" hint="Byte patterns that looked like files but failed their structure checks." />
              <Stat label="Data recovered" value={formatBytes(verdicts.bytes_recoverable)} tint="#f4efe8" hint="Total size of everything marked fully recovered." />
              <Stat label="Time taken" value={detail?.elapsed_seconds ? `${detail.elapsed_seconds}s` : "—"} tint="#f4efe8" hint="Wall-clock time for the whole five-stage analysis." />
            </section>
          )}

          {verification && <VerificationPanel result={verification} />}

          {detail && detail.entropy.points.length > 0 && (
            <section className="panel animate-rise">
              <div className="panel-header">
                <span>Map of the card</span>
                <span className="normal-case tracking-normal text-faint">
                  {detail.filesystem} · {formatBytes(detail.image_size)} · {detail.entropy.anomalies.length} anomalies
                </span>
              </div>
              <div className="p-4">
                <p className="mb-4 max-w-[86ch] text-[12px] leading-relaxed text-dim">
                  The card drawn end to end. Colour shows what kind of data sits at each position:
                  empty space, plain text, structured records, or the dense look of a photo or
                  archive. Recovered files are marked underneath, so you can see where on the card
                  each one came from.
                </p>
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
            <section className="panel animate-rise overflow-hidden">
              <div className="panel-header">
                <span>Problems found while reading the card</span>
                <span className="normal-case tracking-normal text-faint">{detail.damage.length} recorded</span>
              </div>
              <ul>
                {detail.damage.slice(0, 8).map((item, index) => (
                  <li key={index} className="flex gap-4 border-b border-white/[0.04] px-4 py-2.5 text-[12.5px] last:border-b-0">
                    <span className="w-44 shrink-0 font-mono text-[10px] uppercase tracking-wider text-ember-400/90">
                      {item.kind.replace(/_/g, " ")}
                    </span>
                    <span className="text-ash">{item.detail}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {fragments.length > 0 && (
            <section className="grid gap-4 lg:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
              <div className="panel h-[600px] overflow-hidden">
                <FragmentTable fragments={fragments} selectedId={selectedId} onSelect={setSelectedId} />
              </div>

              <div className="flex h-[600px] flex-col gap-4">
                <div className="panel min-h-0 flex-1 overflow-hidden">
                  <div className="panel-header">
                    <span>Why this verdict</span>
                  </div>
                  <div className="h-[calc(100%-45px)]">
                    <FragmentDetail fragment={selected} sessionId={sessionId ?? ""} />
                  </div>
                </div>
                <div className="panel h-[252px] overflow-hidden">
                  <div className="panel-header">
                    <span>Ask about the recovered files</span>
                  </div>
                  <div className="h-[calc(100%-45px)]">
                    <AskPanel sessionId={sessionId ?? ""} ready={detail?.status === "complete"} onCite={setSelectedId} />
                  </div>
                </div>
              </div>
            </section>
          )}
        </main>
      )}
    </>
  );
}

/** The run's progress as a ring, because a bar already lives in the timeline. */
function ProgressRing({ percent, running }: { percent: number; running: boolean }) {
  const radius = 21;
  const circumference = 2 * Math.PI * radius;
  const done = !running && percent >= 100;

  return (
    <div className="relative h-[52px] w-[52px] shrink-0">
      <svg width="52" height="52" viewBox="0 0 52 52" className="-rotate-90">
        <circle cx="26" cy="26" r={radius} fill="none" stroke="rgba(255,236,214,0.08)" strokeWidth="2.5" />
        <circle
          cx="26"
          cy="26"
          r={radius}
          fill="none"
          stroke={done ? "#4bd894" : "#f0a92b"}
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - percent / 100)}
          className="transition-[stroke-dashoffset] duration-500 ease-out"
          style={{ filter: `drop-shadow(0 0 6px ${done ? "rgba(75,216,148,0.6)" : "rgba(240,169,43,0.6)"})` }}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center font-mono text-[11px] tabular-nums text-bone">
        {done ? <span className="text-signal-recover">✓</span> : `${percent}`}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tint,
  hint,
}: {
  label: string;
  value: string | number;
  tint: string;
  hint?: string;
}) {
  return (
    <div className="panel lift group relative overflow-hidden px-4 py-3.5" title={hint}>
      <span
        className="absolute inset-x-0 top-0 h-px opacity-60 transition-opacity duration-300 group-hover:opacity-100"
        style={{ background: `linear-gradient(90deg, transparent, ${tint}, transparent)` }}
      />
      <div className="stat-label">{label}</div>
      <div className="stat-value mt-1.5" style={{ color: tint }}>
        {typeof value === "number" ? <CountUp value={value} /> : value}
      </div>
    </div>
  );
}
