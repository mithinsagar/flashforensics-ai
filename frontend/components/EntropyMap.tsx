"use client";

import { useMemo, useState } from "react";
import { BAND_COLORS, formatBytes, formatHex } from "@/lib/api";
import type { Anomaly, EntropyPoint, Fragment } from "@/lib/types";

interface Props {
  points: EntropyPoint[];
  detail?: { start: number; end: number; points: EntropyPoint[] };
  anomalies: Anomaly[];
  fragments: Fragment[];
  imageSize: number;
  selectedId: string | null;
  onSelect: (fragmentId: string) => void;
}

const HEIGHT = 132;
const WIDTH = 1000;
const OVERVIEW_HEIGHT = 22;

/**
 * The volume drawn as an entropy profile, with a locator strip above it.
 *
 * A single full-width chart is the obvious design and it fails on exactly the
 * input that matters. Real cards are mostly empty: a 128 GB card holding 2 GB of
 * photos puts every interesting measurement into the first 1.5% of the width and
 * leaves the rest flat, which looks like a rendering bug and hides the only data
 * on the device.
 *
 * So there are two tiers. The locator strip is always the whole volume, drawn
 * as a solid band so the emptiness itself stays visible information, with a
 * bracket showing which part the detail chart is displaying. The detail chart
 * defaults to the occupied extent, where bar height is entropy and colour is the
 * content band. Fragment markers sit under the axis at their true offsets, which
 * is what makes the results a picture of the device rather than a list.
 */
export function EntropyMap({ points, detail, anomalies, fragments, imageSize, selectedId, onSelect }: Props) {
  const [hover, setHover] = useState<{ point: EntropyPoint; x: number } | null>(null);
  const [zoomed, setZoomed] = useState(true);

  const maxOffset = imageSize || (points.at(-1)?.offset ?? 1);

  /**
   * The occupied window comes from the backend when it sent one, because that
   * profile was re-bucketed over the same range at full block resolution.
   * Deriving the window here from the overview points instead would zoom into
   * data that had already been averaged down, which magnifies the bars without
   * adding any information.
   */
  const occupied = useMemo(() => {
    if (detail && detail.points.length > 0) {
      return { start: detail.start, end: detail.end, ratio: (detail.end - detail.start) / maxOffset };
    }
    const live = points.filter((point) => point.band !== "empty" || point.max > 0.5);
    if (live.length === 0) return { start: 0, end: maxOffset, ratio: 1 };

    const last = live.at(-1)!;
    const rawStart = live[0].offset;
    const rawEnd = last.offset + last.length;
    const pad = Math.max((rawEnd - rawStart) * 0.06, last.length * 2);
    const start = Math.max(0, rawStart - pad);
    const end = Math.min(maxOffset, rawEnd + pad);
    return { start, end, ratio: (end - start) / maxOffset };
  }, [points, detail, maxOffset]);

  const canZoom = occupied.ratio < 0.6;
  const view = zoomed && canZoom ? occupied : { start: 0, end: maxOffset, ratio: 1 };
  const span = Math.max(1, view.end - view.start);

  const sourcePoints = zoomed && canZoom && detail?.points.length ? detail.points : points;

  const visiblePoints = useMemo(
    () =>
      sourcePoints.filter(
        (point) => point.offset + point.length >= view.start && point.offset <= view.end,
      ),
    [sourcePoints, view.start, view.end],
  );

  const bars = useMemo(() => {
    if (visiblePoints.length === 0) return [];
    const barWidth = WIDTH / visiblePoints.length;
    return visiblePoints.map((point, index) => ({
      point,
      x: index * barWidth,
      w: Math.max(barWidth, 0.6),
      h: Math.max((point.mean / 8) * (HEIGHT - 26), point.mean > 0 ? 1.5 : 0),
    }));
  }, [visiblePoints]);

  const toX = (offset: number) => ((offset - view.start) / span) * WIDTH;

  const markers = useMemo(
    () =>
      fragments
        .filter((fragment) => fragment.offset + fragment.length >= view.start && fragment.offset <= view.end)
        .map((fragment) => ({
          fragment,
          x: toX(fragment.offset),
          w: Math.max((fragment.length / span) * WIDTH, 3),
        })),
    [fragments, view.start, view.end, span],
  );

  const anomalyMarks = useMemo(
    () =>
      anomalies
        .filter((anomaly) => anomaly.offset >= view.start && anomaly.offset <= view.end)
        .slice(0, 300)
        .map((anomaly) => ({ anomaly, x: toX(anomaly.offset) })),
    [anomalies, view.start, view.end, span],
  );

  const overviewBands = useMemo(() => {
    if (points.length === 0) return [];
    const barWidth = WIDTH / points.length;
    return points.map((point, index) => ({
      x: index * barWidth,
      w: Math.max(barWidth, 0.5),
      color: BAND_COLORS[point.band] ?? "#3d5a80",
    }));
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="flex h-[180px] items-center justify-center text-sm text-slate-600">
        The entropy map appears once the scan reaches the volume
      </div>
    );
  }

  return (
    <div className="relative">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-slate-600">Whole volume</span>
        <span className="font-mono text-[10px] text-slate-600">
          {(occupied.ratio * 100).toFixed(1)}% holds data
        </span>
        {canZoom && (
          <div className="ml-auto flex overflow-hidden rounded border border-ink-700">
            <button
              onClick={() => setZoomed(true)}
              className={`px-2 py-0.5 text-[10px] transition-colors ${
                zoomed ? "bg-ink-700 text-slate-200" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              Occupied region
            </button>
            <button
              onClick={() => setZoomed(false)}
              className={`px-2 py-0.5 text-[10px] transition-colors ${
                !zoomed ? "bg-ink-700 text-slate-200" : "text-slate-500 hover:text-slate-300"
              }`}
            >
              Full volume
            </button>
          </div>
        )}
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${OVERVIEW_HEIGHT}`}
        className="w-full cursor-pointer"
        preserveAspectRatio="none"
        onClick={() => canZoom && setZoomed((current) => !current)}
      >
        <rect x={0} y={0} width={WIDTH} height={OVERVIEW_HEIGHT} fill="#12151c" />
        {overviewBands.map((band, index) => (
          <rect key={index} x={band.x} y={0} width={band.w} height={OVERVIEW_HEIGHT} fill={band.color} />
        ))}
        {view.ratio < 1 && (
          <>
            <rect
              x={0}
              y={0}
              width={(view.start / maxOffset) * WIDTH}
              height={OVERVIEW_HEIGHT}
              fill="#08090c"
              opacity={0.66}
            />
            <rect
              x={(view.end / maxOffset) * WIDTH}
              y={0}
              width={WIDTH - (view.end / maxOffset) * WIDTH}
              height={OVERVIEW_HEIGHT}
              fill="#08090c"
              opacity={0.66}
            />
            <rect
              x={(view.start / maxOffset) * WIDTH}
              y={0.5}
              width={Math.max(((view.end - view.start) / maxOffset) * WIDTH, 2)}
              height={OVERVIEW_HEIGHT - 1}
              fill="none"
              stroke="#7aa2f7"
              strokeWidth={1.2}
            />
          </>
        )}
      </svg>

      <div className="mb-1 mt-2 flex items-baseline justify-between font-mono text-[10px] text-slate-600">
        <span>{formatHex(view.start)}</span>
        <span className="text-slate-500">
          showing {formatBytes(span)} of {formatBytes(maxOffset)}
        </span>
        <span>{formatHex(view.end)}</span>
      </div>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT + 12}`}
        className="w-full"
        preserveAspectRatio="none"
        onMouseLeave={() => setHover(null)}
      >
        {[2, 4, 6, 8].map((level) => (
          <line
            key={level}
            x1={0}
            x2={WIDTH}
            y1={HEIGHT - 26 - (level / 8) * (HEIGHT - 26)}
            y2={HEIGHT - 26 - (level / 8) * (HEIGHT - 26)}
            stroke="#1c2130"
            strokeWidth={1}
          />
        ))}

        {bars.map(({ point, x, w, h }, index) => (
          <rect
            key={index}
            x={x}
            y={HEIGHT - 26 - h}
            width={w}
            height={h}
            fill={BAND_COLORS[point.band] ?? "#3d5a80"}
            opacity={0.92}
            onMouseEnter={() => setHover({ point, x })}
          />
        ))}

        {anomalyMarks.map(({ anomaly, x }, index) => (
          <line
            key={`anomaly-${index}`}
            x1={x}
            x2={x}
            y1={0}
            y2={HEIGHT - 26}
            stroke={anomaly.severity === "high" ? "#f5a524" : "#7aa2f7"}
            strokeWidth={0.8}
            opacity={0.3}
          />
        ))}

        <line x1={0} x2={WIDTH} y1={HEIGHT - 26} y2={HEIGHT - 26} stroke="#323a4c" strokeWidth={1} />

        {markers.map(({ fragment, x, w }) => {
          const isSelected = fragment.fragment_id === selectedId;
          const color =
            fragment.verdict?.status === "RECOVERABLE"
              ? "#3ddc97"
              : fragment.verdict?.status === "PARTIAL"
                ? "#f5a524"
                : "#7aa2f7";
          return (
            <rect
              key={fragment.fragment_id}
              x={x}
              y={HEIGHT - 20}
              width={w}
              height={isSelected ? 14 : 9}
              rx={1.5}
              fill={color}
              opacity={isSelected ? 1 : 0.7}
              className="cursor-pointer"
              onClick={() => onSelect(fragment.fragment_id)}
            >
              <title>
                {fragment.source_path ?? `carved ${fragment.format_guess}`} — {formatBytes(fragment.length)} at{" "}
                {formatHex(fragment.offset)}
              </title>
            </rect>
          );
        })}
      </svg>

      {hover && (
        <div
          className="pointer-events-none absolute top-14 z-10 rounded border border-ink-600 bg-ink-850 px-2.5 py-1.5 font-mono text-[11px] shadow-xl"
          style={{ left: `${Math.min((hover.x / WIDTH) * 100, 82)}%` }}
        >
          <div className="text-slate-300">{formatHex(hover.point.offset)}</div>
          <div className="text-slate-500">
            {hover.point.mean.toFixed(2)} bits · {hover.point.band}
          </div>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-500">
        {Object.entries(BAND_COLORS).map(([band, color]) => (
          <span key={band} className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: color }} />
            {band}
          </span>
        ))}
        <span className="ml-auto text-slate-600">
          bar height is entropy, 0 to 8 bits per byte · markers below the axis are recovered files
        </span>
      </div>
    </div>
  );
}
