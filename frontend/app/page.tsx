"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AgentTimeline } from "@/components/AgentTimeline";
import { AskPanel } from "@/components/AskPanel";
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
    <main className="mx-auto max-w-[1560px] p-4">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-slate-100">FlashForensics AI</h1>
          <p className="text-[11px] text-slate-500">
            Plug in a damaged card and get your files back, with the evidence for every call it makes
          </p>
        </div>
        <div className="flex items-center gap-3">
          {started && (
            <button
              onClick={reset}
              disabled={running}
              className="rounded border border-ink-600 bg-ink-800 px-3 py-1.5 text-[12px] text-slate-200 transition-colors hover:bg-ink-700 disabled:opacity-40"
            >
              Start over
            </button>
          )}
          {health && (
            <div className="hidden items-center gap-3 font-mono text-[10px] text-slate-600 md:flex">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
                v{health.version}
              </span>
              <span>{health.signatures} signatures</span>
              <span>{health.knowledge_base.formats_indexed} formats indexed</span>
              <span title={health.llm.note ?? ""}>llm: {health.llm.provider}</span>
            </div>
          )}
        </div>
      </header>

      {error && (
        <div className="panel mb-4 border-l-2 border-l-red-600 p-3 text-[12px] text-red-300">{error}</div>
      )}

      {!started && (
        <StartPanel
          running={running}
          onDevice={handleDevice}
          onDemo={handleDemo}
          onUpload={handleUpload}
          onPath={handlePath}
        />
      )}

      {started && (
        <section className="panel mb-4 flex flex-wrap items-center justify-between gap-3 p-3">
          <div className="min-w-0">
            <div className="stat-label">Looking at</div>
            <div className="truncate text-[13px] text-slate-200">{sourceLabel || detail?.image_name}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {detail?.status === "complete" && verdicts && verdicts.recoverable > 0 && (
              <button
                onClick={handleExport}
                className="rounded border border-emerald-700/50 bg-emerald-600/15 px-3 py-1.5 text-[12px] text-emerald-200 transition-colors hover:bg-emerald-600/25"
              >
                Save all recovered files
              </button>
            )}
          </div>
          {exported && <div className="w-full font-mono text-[10px] text-emerald-400/80">{exported}</div>}
        </section>
      )}

      {started && (
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
          <Stat
            label="Fully recovered"
            value={verdicts.recoverable}
            tone="text-signal-recover"
            hint="These files came back complete and should open normally."
          />
          <Stat
            label="Partly damaged"
            value={verdicts.partial}
            tone="text-signal-partial"
            hint="Some of the data survived; these may open with pieces missing."
          />
          <Stat
            label="Name only"
            value={verdicts.metadata_only}
            tone="text-signal-meta"
            hint="The card remembers these existed, but the contents are gone."
          />
          <Stat
            label="Not real files"
            value={verdicts.junk}
            tone="text-signal-junk"
            hint="Byte patterns that looked like files but failed their structure checks."
          />
          <Stat
            label="Data recovered"
            value={formatBytes(verdicts.bytes_recoverable)}
            tone="text-slate-200"
            hint="Total size of everything marked fully recovered."
          />
          <Stat
            label="Time taken"
            value={detail?.elapsed_seconds ? `${detail.elapsed_seconds}s` : "-"}
            tone="text-slate-200"
            hint="Wall-clock time for the whole five-stage analysis."
          />
        </section>
      )}

      {verification && <VerificationPanel result={verification} />}

      {detail && detail.entropy.points.length > 0 && (
        <section className="panel mb-4">
          <div className="panel-header flex items-center justify-between">
            <span>Map of the card</span>
            <span className="font-mono normal-case tracking-normal text-slate-600">
              {detail.filesystem} · {formatBytes(detail.image_size)} ·{" "}
              {detail.entropy.anomalies.length} anomalies
            </span>
          </div>
          <div className="p-3">
            <p className="mb-2.5 text-[11px] leading-relaxed text-slate-500">
              The card drawn end to end. Colour shows what kind of data sits at each position: empty
              space, plain text, structured records, or the dense look of a photo or archive. Recovered
              files are marked underneath, so you can see where on the card each one came from.
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
        <section className="panel mb-4">
          <div className="panel-header">Problems found while reading the card</div>
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
              <div className="panel-header">Why this verdict</div>
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
    </main>
  );
}

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: string | number;
  tone: string;
  hint?: string;
}) {
  return (
    <div className="panel px-3 py-2.5" title={hint}>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone}`}>{value}</div>
    </div>
  );
}
