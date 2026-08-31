"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Coverage } from "@/components/landing/Coverage";
import { Evidence } from "@/components/landing/Evidence";
import { PipelineFlow } from "@/components/landing/PipelineFlow";
import { SectorField } from "@/components/visuals/SectorField";
import { api, formatBytes } from "@/lib/api";
import type { DemoInfo, DetectedDevice, DevicesResponse, HealthResponse } from "@/lib/types";

/**
 * The first thing a user sees, and the screen that decides whether they get
 * anywhere at all.
 *
 * The ordering is deliberate. Detected hardware comes first because someone who
 * just pushed a card into a reader is looking for that card, not for a file
 * picker. The sample card comes second so a visitor with nothing to recover can
 * still see the tool work. Uploading a disk image comes last: it is the most
 * capable route and the one fewest people arrive already understanding.
 *
 * Polling rather than a socket. The question "is a card plugged in" has a cheap
 * answer, a two-second staleness is imperceptible to someone physically
 * inserting a card, and a dropped socket would leave the page silently blind to
 * the one event it exists to notice.
 */

const POLL_INTERVAL_MS = 2500;

interface Props {
  running: boolean;
  health: HealthResponse | null;
  onDevice: (device: DetectedDevice) => void;
  onDemo: () => void;
  onUpload: (file: File) => void;
  onPath: (path: string) => void;
}

export function StartPanel({ running, health, onDevice, onDemo, onUpload, onPath }: Props) {
  const [devices, setDevices] = useState<DevicesResponse | null>(null);
  const [demo, setDemo] = useState<DemoInfo | null>(null);
  const [path, setPath] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const seen = useRef<Set<string>>(new Set());
  const [justAppeared, setJustAppeared] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const response = await api.devices();
      setDevices(response);

      // Highlight a card that was not there a moment ago: the app noticing the
      // insertion is the moment worth showing, and it is easy to miss in a list.
      const cards = response.devices.filter((device) => device.likely_card);
      for (const card of cards) {
        if (!seen.current.has(card.path)) {
          seen.current.add(card.path);
          if (seen.current.size > cards.length) continue;
          setJustAppeared(card.path);
          window.setTimeout(() => setJustAppeared(null), 4000);
        }
      }
      seen.current = new Set(cards.map((card) => card.path));
    } catch {
      setDevices(null);
    }
  }, []);

  useEffect(() => {
    void poll();
    void api
      .demoInfo()
      .then(setDemo)
      .catch(() => setDemo(null));
    const timer = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [poll]);

  const cards = devices?.devices.filter((device) => device.likely_card) ?? [];
  const others = devices?.devices.filter((device) => !device.likely_card) ?? [];
  const detectionWorks = devices?.environment.detector_available ?? false;

  return (
    <>
      <section className="relative isolate overflow-hidden">
        <SectorField />
        {/* Scrim: the field stays visible but never competes with the words. */}
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(1000px_620px_at_18%_38%,rgba(6,5,5,0.94),rgba(6,5,5,0.55)_58%,transparent_78%)]" />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-[#060505] to-transparent" />

        <div className="relative mx-auto grid max-w-[1280px] items-center gap-14 px-6 pb-14 pt-14 lg:grid-cols-[1.02fr_0.98fr] lg:gap-10 lg:pt-16">
          <div className="animate-rise-slow">
            <div className="mb-6 flex flex-wrap items-center gap-2">
              <span className="chip border-gold-700/40 bg-gold-500/10 text-gold-300">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ring rounded-full bg-gold-400" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-gold-400" />
                </span>
                agentic recovery
              </span>
              <span className="chip text-dim">five agents · one card</span>
            </div>

            <h1 className="display text-[13vw] text-bone sm:text-[64px] lg:text-[76px] xl:text-[84px]">
              Your photos are{" "}
              <em className="text-salvage not-italic">still there</em>.
              <br />
              The card just forgot
              <br />
              where it put them.
            </h1>

            <p className="lede mt-7 max-w-[46ch]">
              FlashForensics reads a damaged card sector by sector, rebuilds the files its index lost,
              and tells you which ones will actually open — with the evidence behind every call it
              makes.
            </p>

            <dl className="mt-9 grid grid-cols-2 gap-x-6 gap-y-5 border-t border-white/[0.06] pt-6 sm:grid-cols-4">
              <Figure value={health ? String(health.signatures) : "77"} label="file signatures" />
              <Figure
                value={health ? String(health.knowledge_base.formats_indexed) : "69"}
                label="formats indexed"
              />
              <Figure value="5" label="agents" />
              <Figure value="100%" label="sample recall" tone="gold" />
            </dl>
          </div>

          <div className="flex flex-col gap-4 lg:animate-rise-slow">
            <DeviceBay
              cards={cards}
              others={others}
              devices={devices}
              detectionWorks={detectionWorks}
              running={running}
              justAppeared={justAppeared}
              onDevice={onDevice}
            />

            <SampleCard demo={demo} running={running} onDemo={onDemo} />

            <ImageDrop
              running={running}
              path={path}
              setPath={setPath}
              showAdvanced={showAdvanced}
              setShowAdvanced={setShowAdvanced}
              onUpload={onUpload}
              onPath={onPath}
            />
          </div>
        </div>
      </section>

      <Ticker />
      <PipelineFlow />
      <Evidence />
      <Coverage formats={health?.knowledge_base.formats_indexed ?? 69} />
    </>
  );
}

const CLAIMS = [
  "read-only — the card is never written to",
  "FAT32 and exFAT parsed directly",
  "entropy-guided carving",
  "77 header signatures",
  "every verdict carries its evidence",
  "graded against a known answer key",
  "no API key required",
  "runs with no network at all",
];

/** A moving hairline of facts, bridging the hero into the explanation below. */
function Ticker() {
  const doubled = [...CLAIMS, ...CLAIMS];
  return (
    <div className="mask-fade-r relative overflow-hidden border-y border-white/[0.06] bg-white/[0.015] py-3">
      <div className="flex w-max animate-marquee items-center gap-8" style={{ animationDuration: "38s" }}>
        {doubled.map((claim, index) => (
          <span key={index} className="flex items-center gap-8 font-mono text-[10.5px] uppercase tracking-widest text-dim">
            {claim}
            <span className="text-gold-600">◆</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Figure({ value, label, tone }: { value: string; label: string; tone?: "gold" }) {
  return (
    <div>
      <dt className="font-mono text-[22px] tabular-nums leading-none tracking-tight text-bone">
        <span className={tone === "gold" ? "text-salvage" : undefined}>{value}</span>
      </dt>
      <dd className="stat-label mt-1.5">{label}</dd>
    </div>
  );
}

/* ------------------------------------------------------------------ */

function DeviceBay({
  cards,
  others,
  devices,
  detectionWorks,
  running,
  justAppeared,
  onDevice,
}: {
  cards: DetectedDevice[];
  others: DetectedDevice[];
  devices: DevicesResponse | null;
  detectionWorks: boolean;
  running: boolean;
  justAppeared: string | null;
  onDevice: (device: DetectedDevice) => void;
}) {
  return (
    <section className="glass overflow-hidden">
      <div className="panel-header">
        <span>Cards plugged into this computer</span>
        <span className="flex items-center gap-1.5 text-scan-400">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ring rounded-full bg-scan-400" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-scan-400" />
          </span>
          watching
        </span>
      </div>

      <div className="p-4">
        {!detectionWorks && (
          <div className="rounded-lg border border-white/[0.06] bg-ink-990/60 p-4">
            <p className="text-[12.5px] leading-relaxed text-ash">
              This copy is running on a server, so it cannot see hardware plugged into{" "}
              <em className="not-italic text-bone">your</em> machine — no website can. The sample card
              below runs a genuine recovery right here. For a real card, run it locally:
            </p>
            <pre className="mt-3 overflow-x-auto rounded-md border border-white/[0.06] bg-black/50 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-gold-300/90">
              <span className="text-faint">$ </span>pip install -e &quot;backend[dev]&quot;{"\n"}
              <span className="text-faint">$ </span>flashforensics serve
            </pre>
          </div>
        )}

        {detectionWorks && cards.length === 0 && (
          <div className="relative overflow-hidden rounded-lg border border-dashed border-white/10 px-6 py-8 text-center">
            <div className="pointer-events-none absolute inset-x-0 top-0 h-px animate-sweep bg-gradient-to-r from-transparent via-scan-400/70 to-transparent" />
            <p className="text-[14px] text-bone">Insert an SD card or USB drive</p>
            <p className="mx-auto mt-2 max-w-sm text-[12px] leading-relaxed text-dim">
              It appears here on its own within a few seconds. If your computer offers to erase or
              initialise the card, say no — this tool never writes to it.
            </p>
          </div>
        )}

        <div className="space-y-2.5">
          {cards.map((device) => (
            <DeviceRow
              key={device.path}
              device={device}
              running={running}
              highlight={device.path === justAppeared}
              onSelect={() => onDevice(device)}
            />
          ))}
        </div>

        {others.length > 0 && (
          <details className="group mt-3">
            <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-widest text-faint transition-colors hover:text-ash">
              {others.length} other drive{others.length === 1 ? "" : "s"} on this machine
            </summary>
            <div className="mt-2.5 space-y-2.5">
              {others.map((device) => (
                <DeviceRow
                  key={device.path}
                  device={device}
                  running={running}
                  highlight={false}
                  onSelect={() => onDevice(device)}
                />
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}

function DeviceRow({
  device,
  running,
  highlight,
  onSelect,
}: {
  device: DetectedDevice;
  running: boolean;
  highlight: boolean;
  onSelect: () => void;
}) {
  const filesystems = device.filesystems.join(", ");
  return (
    <div
      className={`lift relative overflow-hidden rounded-lg border p-3.5 ${
        highlight
          ? "border-gold-600/50 bg-gold-500/[0.07]"
          : "border-white/[0.06] bg-white/[0.02]"
      }`}
    >
      {highlight && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px animate-sweep bg-gradient-to-r from-transparent via-gold-400 to-transparent" />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] text-bone">{device.label}</span>
            {highlight && (
              <span className="chip border-gold-600/40 bg-gold-500/15 py-0.5 text-[9px] text-gold-300">
                just inserted
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10px] text-faint">
            <span>{device.path}</span>
            <span>{device.size_human}</span>
            {filesystems && <span>{filesystems}</span>}
            {device.mount_points.length > 0 && <span>mounted</span>}
          </div>
        </div>

        <button onClick={onSelect} disabled={running || !device.readable} className="btn-gold shrink-0 !py-2 !text-[12px]">
          Recover this
        </button>
      </div>

      {!device.readable && (
        <div className="mt-3 rounded-md border border-ember-600/25 bg-ember-500/[0.06] p-2.5">
          <p className="text-[11.5px] text-ember-400">{device.reason}</p>
          {device.elevation_hint && (
            <p className="mt-1.5 font-mono text-[10px] leading-relaxed text-dim">{device.elevation_hint}</p>
          )}
        </div>
      )}

      {device.readable && !device.supported && (
        <p className="mt-2.5 text-[11.5px] leading-relaxed text-dim">
          Formatted as {filesystems || "something unfamiliar"}, which this tool cannot read file names
          from. It can still search the raw data for recoverable files, but they come back without
          their original names.
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

/** Colour per damage scenario, matching the verification table's vocabulary. */
const SCENARIO_TINT: Record<string, string> = {
  intact: "#4bd894",
  orphaned: "#5fc9df",
  deleted: "#7c8fa5",
  truncated: "#f0a92b",
  payload_corrupted: "#f2643a",
  chain_broken: "#c74ac0",
};

function SampleCard({
  demo,
  running,
  onDemo,
}: {
  demo: DemoInfo | null;
  running: boolean;
  onDemo: () => void;
}) {
  const scenarios = Object.entries(demo?.scenarios ?? {});
  const planted = demo?.planted_files ?? 0;

  return (
    <section className="glass relative overflow-hidden">
      <div className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full bg-gold-500/10 blur-3xl" />
      <div className="panel-header">
        <span className="text-gold-500">No card handy? Try the sample</span>
        <span className="text-faint">built on the spot</span>
      </div>

      <div className="relative p-4">
        <p className="text-[12.5px] leading-relaxed text-ash">
          {demo?.blurb ??
            "A small card is built on the spot, filled with real files, then deliberately damaged so you can watch a genuine recovery."}
        </p>

        {/* Every planted file as one cell, tinted by what was done to it. The
            sample card's whole point is that the damage is known in advance, so
            showing the shape of it beats describing it. */}
        {scenarios.length > 0 && (
          <div className="mt-4">
            <div className="flex flex-wrap gap-1">
              {scenarios.flatMap(([scenario, count], group) =>
                Array.from({ length: count }, (_, index) => (
                  <span
                    key={`${scenario}-${index}`}
                    title={scenario.replace(/_/g, " ")}
                    className="h-4 w-4 rounded-[3px] animate-rise"
                    style={{
                      background: SCENARIO_TINT[scenario] ?? "#6d635a",
                      opacity: 0.85,
                      animationDelay: `${(group * 4 + index) * 22}ms`,
                    }}
                  />
                )),
              )}
            </div>
            <div className="mt-3 flex flex-wrap gap-x-3.5 gap-y-1.5">
              {scenarios.map(([scenario, count]) => (
                <span key={scenario} className="flex items-center gap-1.5 font-mono text-[9.5px] uppercase tracking-wider text-dim">
                  <span
                    className="h-2 w-2 rounded-[2px]"
                    style={{ background: SCENARIO_TINT[scenario] ?? "#6d635a" }}
                  />
                  {scenario.replace(/_/g, " ")} ×{count}
                </span>
              ))}
            </div>
          </div>
        )}

        <button onClick={onDemo} disabled={running || demo?.available === false} className="btn-gold mt-5 w-full !py-3 !text-[14px]">
          {running ? "Working…" : "Recover the sample card"}
        </button>

        {demo?.available && (
          <div className="mt-3 flex flex-wrap justify-center gap-x-4 gap-y-1 font-mono text-[10px] text-faint">
            <span>{demo.filesystem}</span>
            <span>{formatBytes(demo.size_bytes ?? 0)}</span>
            <span>{planted} files planted</span>
            <span>graded against the answer key</span>
          </div>
        )}

        {demo?.available === false && <p className="mt-3 text-[11.5px] text-ember-400">{demo.reason}</p>}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */

function ImageDrop({
  running,
  path,
  setPath,
  showAdvanced,
  setShowAdvanced,
  onUpload,
  onPath,
}: {
  running: boolean;
  path: string;
  setPath: (value: string) => void;
  showAdvanced: boolean;
  setShowAdvanced: (updater: (value: boolean) => boolean) => void;
  onUpload: (file: File) => void;
  onPath: (path: string) => void;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <span>Already have a disk image?</span>
      </div>
      <div className="p-4">
        <label className="group flex cursor-pointer items-center justify-center gap-2.5 rounded-lg border border-dashed border-white/10 px-4 py-4 text-[12.5px] text-ash transition-all hover:border-gold-600/40 hover:bg-gold-500/[0.04] hover:text-bone">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" className="text-dim transition-colors group-hover:text-gold-500">
            <path d="M8 11V2m0 0L4.5 5.5M8 2l3.5 3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M2 10.5v2A1.5 1.5 0 0 0 3.5 14h9a1.5 1.5 0 0 0 1.5-1.5v-2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          Choose an image file (.img, .dd, .raw)
          <input
            type="file"
            className="hidden"
            disabled={running}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUpload(file);
            }}
          />
        </label>

        <button
          onClick={() => setShowAdvanced((value) => !value)}
          className="mt-2.5 font-mono text-[10px] uppercase tracking-widest text-faint transition-colors hover:text-ash"
        >
          {showAdvanced ? "hide" : "or"} analyse a file already on this machine
        </button>

        {showAdvanced && (
          <div className="mt-2.5 flex gap-2">
            <input
              value={path}
              onChange={(event) => setPath(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && path.trim() && onPath(path.trim())}
              placeholder="/path/to/card.img"
              disabled={running}
              className="field flex-1"
            />
            <button onClick={() => path.trim() && onPath(path.trim())} disabled={running || !path.trim()} className="btn-ghost">
              Open
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
