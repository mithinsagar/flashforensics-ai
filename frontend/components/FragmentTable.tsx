"use client";

import { useMemo, useState } from "react";
import { STATUS_STYLES, formatBytes, formatHex } from "@/lib/api";
import type { Fragment, VerdictStatus } from "@/lib/types";

interface Props {
  fragments: Fragment[];
  selectedId: string | null;
  onSelect: (fragmentId: string) => void;
}

const FILTERS: Array<{ key: VerdictStatus | "ALL"; label: string; tint: string }> = [
  { key: "ALL", label: "All", tint: "#a99f95" },
  { key: "RECOVERABLE", label: "Recovered", tint: "#4bd894" },
  { key: "PARTIAL", label: "Damaged", tint: "#f0a92b" },
  { key: "METADATA_ONLY", label: "Name only", tint: "#5fc9df" },
  { key: "JUNK", label: "Junk", tint: "#6d635a" },
];

const CATEGORY_GLYPH: Record<string, string> = {
  image: "▣",
  video: "▶",
  audio: "◍",
  document: "▤",
  archive: "▦",
  database: "▩",
  application: "◆",
};

/**
 * The ranked results list.
 *
 * Ordered by the adjudicator's verdict and priority rather than by disk offset,
 * because the person reading this wants their photos first, not sector zero
 * first. The source column is what separates a file the filesystem could still
 * name from one that only exists because it was carved out of lost space.
 */
export function FragmentTable({ fragments, selectedId, onSelect }: Props) {
  const [filter, setFilter] = useState<VerdictStatus | "ALL">("ALL");
  const [query, setQuery] = useState("");

  const counts = useMemo(() => {
    const tally: Record<string, number> = { ALL: fragments.length };
    for (const fragment of fragments) {
      const status = fragment.verdict?.status ?? "JUNK";
      tally[status] = (tally[status] ?? 0) + 1;
    }
    return tally;
  }, [fragments]);

  const largest = useMemo(
    () => fragments.reduce((max, fragment) => Math.max(max, fragment.length), 1),
    [fragments],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return fragments.filter((fragment) => {
      if (filter !== "ALL" && fragment.verdict?.status !== filter) return false;
      if (!needle) return true;
      return (
        fragment.format_guess.toLowerCase().includes(needle) ||
        (fragment.source_path ?? "").toLowerCase().includes(needle) ||
        fragment.category.toLowerCase().includes(needle) ||
        fragment.fragment_id.includes(needle)
      );
    });
  }, [fragments, filter, query]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-white/[0.06] px-3 py-2.5">
        {FILTERS.map((option) => {
          const isOn = filter === option.key;
          return (
            <button
              key={option.key}
              onClick={() => setFilter(option.key)}
              className="rounded-md px-2.5 py-1.5 text-[11.5px] transition-all duration-200"
              style={{
                background: isOn ? `${option.tint}1a` : "transparent",
                color: isOn ? option.tint : "#736a62",
                boxShadow: isOn ? `inset 0 0 0 1px ${option.tint}44` : "none",
              }}
            >
              {option.label}
              <span className="ml-1.5 font-mono text-[10px] tabular-nums opacity-70">
                {counts[option.key] ?? 0}
              </span>
            </button>
          );
        })}
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter by name, format or id"
          className="field ml-auto w-56 !py-1.5 !text-[11px]"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-left text-[12px]">
          <thead className="sticky top-0 z-10 bg-[#0c0b0a]/95 font-mono text-[9.5px] uppercase tracking-widest text-faint backdrop-blur">
            <tr className="border-b border-white/[0.06]">
              <th className="px-3 py-2.5 font-normal">#</th>
              <th className="px-3 py-2.5 font-normal">Name / format</th>
              <th className="px-3 py-2.5 font-normal">Size</th>
              <th className="px-3 py-2.5 font-normal">Offset</th>
              <th className="px-3 py-2.5 font-normal">Source</th>
              <th className="px-3 py-2.5 font-normal">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((fragment) => {
              const status = fragment.verdict?.status ?? "JUNK";
              const style = STATUS_STYLES[status];
              const isSelected = fragment.fragment_id === selectedId;
              const name = fragment.source_path?.split("/").pop();
              const tint =
                status === "RECOVERABLE"
                  ? "#4bd894"
                  : status === "PARTIAL"
                    ? "#f0a92b"
                    : status === "METADATA_ONLY"
                      ? "#5fc9df"
                      : "#6d635a";

              return (
                <tr
                  key={fragment.fragment_id}
                  onClick={() => onSelect(fragment.fragment_id)}
                  className={`group relative cursor-pointer border-b border-white/[0.035] transition-colors ${
                    isSelected ? "bg-white/[0.045]" : "hover:bg-white/[0.025]"
                  }`}
                >
                  <td className="relative px-3 py-2.5 font-mono tabular-nums text-faint">
                    {isSelected && (
                      <span className="absolute inset-y-0 left-0 w-[2px]" style={{ background: tint }} />
                    )}
                    {fragment.rank ?? "—"}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px]" style={{ color: tint, opacity: 0.75 }}>
                        {CATEGORY_GLYPH[fragment.category] ?? "◇"}
                      </span>
                      <span className="truncate text-[12.5px] text-bone">
                        {name ?? `carved ${fragment.format_guess}`}
                      </span>
                    </div>
                    <div className="mt-0.5 pl-[22px] font-mono text-[10px] text-faint">
                      {fragment.format_guess} · {fragment.category}
                      {fragment.ambiguity_group && (
                        <span className="ml-1.5 text-gold-600">
                          header shared with {fragment.candidates.length - 1} others
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="font-mono tabular-nums text-ash">{formatBytes(fragment.length)}</div>
                    {/* Relative size, so the eye finds the big files without reading. */}
                    <div className="mt-1 h-[2px] w-14 overflow-hidden rounded-full bg-white/[0.06]">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${Math.max(3, (fragment.length / largest) * 100)}%`,
                          background: tint,
                          opacity: 0.65,
                        }}
                      />
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-faint">{formatHex(fragment.offset)}</td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`rounded px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-wider ${
                        fragment.source === "carved"
                          ? "bg-[#c74ac0]/12 text-[#d68ad2]"
                          : "bg-white/[0.05] text-dim"
                      }`}
                    >
                      {fragment.source === "carved" ? "carved" : "directory"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span
                      className="rounded-md px-2 py-1 text-[10.5px] transition-all"
                      style={{
                        background: `${tint}14`,
                        color: tint,
                        boxShadow: `inset 0 0 0 1px ${tint}33`,
                      }}
                    >
                      {style.label}
                    </span>
                  </td>
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-12 text-center text-[12px] text-faint">
                  Nothing matches this filter
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
