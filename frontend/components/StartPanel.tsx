"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes } from "@/lib/api";
import type { DemoInfo, DetectedDevice, DevicesResponse } from "@/lib/types";

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
  onDevice: (device: DetectedDevice) => void;
  onDemo: () => void;
  onUpload: (file: File) => void;
  onPath: (path: string) => void;
}

export function StartPanel({ running, onDevice, onDemo, onUpload, onPath }: Props) {
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
    void api.demoInfo().then(setDemo).catch(() => setDemo(null));
    const timer = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [poll]);

  const cards = devices?.devices.filter((device) => device.likely_card) ?? [];
  const others = devices?.devices.filter((device) => !device.likely_card) ?? [];
  const detectionWorks = devices?.environment.detector_available ?? false;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
      <section className="panel overflow-hidden">
        <div className="panel-header flex items-center justify-between">
          <span>Cards plugged into this computer</span>
          <span className="flex items-center gap-1.5 normal-case tracking-normal text-slate-600">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-500 opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-sky-500" />
            </span>
            watching
          </span>
        </div>

        <div className="p-3">
          {!detectionWorks && (
            <p className="rounded border border-ink-700 bg-ink-850 p-3 text-[12px] text-slate-400">
              This server cannot see attached hardware
              {devices ? ` on ${devices.environment.platform}` : ""}. That is normal when the app is
              running on a hosted server rather than on your own machine — the sample card and file
              upload both still work.
            </p>
          )}

          {detectionWorks && cards.length === 0 && (
            <div className="rounded border border-dashed border-ink-700 p-6 text-center">
              <p className="text-[13px] text-slate-300">Insert an SD card or USB drive</p>
              <p className="mx-auto mt-1.5 max-w-sm text-[11px] leading-relaxed text-slate-500">
                It will appear here on its own, usually within a few seconds. If your computer offers
                to erase or initialise the card, say no — this tool never writes to it.
              </p>
            </div>
          )}

          <div className="space-y-2">
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
            <details className="mt-3">
              <summary className="cursor-pointer text-[11px] text-slate-600 hover:text-slate-400">
                {others.length} other drive{others.length === 1 ? "" : "s"} on this machine
              </summary>
              <div className="mt-2 space-y-2">
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

      <div className="flex flex-col gap-4">
        <section className="panel overflow-hidden">
          <div className="panel-header">No card handy? Try the sample</div>
          <div className="p-3">
            <p className="text-[12px] leading-relaxed text-slate-400">
              {demo?.blurb ??
                "A small card is built on the spot, filled with real files, then deliberately damaged so you can watch a genuine recovery."}
            </p>
            {demo?.available && (
              <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-slate-600">
                <span>{demo.filesystem}</span>
                <span>{formatBytes(demo.size_bytes ?? 0)}</span>
                <span>{demo.planted_files} files planted</span>
                <span>{Object.keys(demo.scenarios ?? {}).length} kinds of damage</span>
              </div>
            )}
            <button
              onClick={onDemo}
              disabled={running || demo?.available === false}
              className="mt-3 w-full rounded border border-sky-700/50 bg-sky-600/15 px-3 py-2 text-[12px] font-medium text-sky-200 transition-colors hover:bg-sky-600/25 disabled:opacity-40"
            >
              {running ? "Working…" : "Recover the sample card"}
            </button>
            {demo?.available === false && (
              <p className="mt-2 text-[11px] text-amber-500/80">{demo.reason}</p>
            )}
          </div>
        </section>

        <section className="panel overflow-hidden">
          <div className="panel-header">Already have a disk image?</div>
          <div className="p-3">
            <label className="block cursor-pointer rounded border border-dashed border-ink-700 px-3 py-4 text-center text-[12px] text-slate-300 transition-colors hover:border-ink-600 hover:bg-ink-850">
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
              className="mt-2 text-[11px] text-slate-600 hover:text-slate-400"
            >
              {showAdvanced ? "hide" : "or"} analyse a file already on this machine
            </button>

            {showAdvanced && (
              <div className="mt-2 flex gap-2">
                <input
                  value={path}
                  onChange={(event) => setPath(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && path.trim() && onPath(path.trim())}
                  placeholder="/path/to/card.img"
                  disabled={running}
                  className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-950 px-2.5 py-1.5 font-mono text-[11px] outline-none placeholder:text-slate-600 focus:border-ink-600"
                />
                <button
                  onClick={() => path.trim() && onPath(path.trim())}
                  disabled={running || !path.trim()}
                  className="rounded border border-ink-600 bg-ink-800 px-3 py-1.5 text-[12px] text-slate-200 hover:bg-ink-700 disabled:opacity-40"
                >
                  Open
                </button>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
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
      className={`rounded border p-3 transition-colors ${
        highlight ? "border-sky-600/60 bg-sky-600/10" : "border-ink-700 bg-ink-850"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-[13px] font-medium text-slate-200">{device.label}</span>
            {highlight && (
              <span className="rounded bg-sky-600/20 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-sky-300">
                just inserted
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 font-mono text-[10px] text-slate-500">
            <span>{device.path}</span>
            <span>{device.size_human}</span>
            {filesystems && <span>{filesystems}</span>}
            {device.mount_points.length > 0 && <span>mounted</span>}
          </div>
        </div>

        <button
          onClick={onSelect}
          disabled={running || !device.readable}
          className="shrink-0 rounded border border-emerald-700/50 bg-emerald-600/15 px-3 py-1.5 text-[12px] font-medium text-emerald-200 transition-colors hover:bg-emerald-600/25 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Recover this
        </button>
      </div>

      {!device.readable && (
        <div className="mt-2 rounded border border-amber-700/40 bg-amber-600/5 p-2">
          <p className="text-[11px] text-amber-300/90">{device.reason}</p>
          {device.elevation_hint && (
            <p className="mt-1 font-mono text-[10px] leading-relaxed text-slate-500">
              {device.elevation_hint}
            </p>
          )}
        </div>
      )}

      {device.readable && !device.supported && (
        <p className="mt-2 text-[11px] text-slate-500">
          This card is formatted as {filesystems || "something unfamiliar"}, which this tool cannot
          read the file names from. It can still search the raw data for recoverable files, but they
          will come back without their original names.
        </p>
      )}
    </div>
  );
}
