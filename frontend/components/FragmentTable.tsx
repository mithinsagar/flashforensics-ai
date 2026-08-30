"use client";

import { useMemo, useState } from "react";
import { STATUS_STYLES, formatBytes, formatHex } from "@/lib/api";
import type { Fragment, VerdictStatus } from "@/lib/types";

interface Props {
  fragments: Fragment[];
  selectedId: string | null;
  onSelect: (fragmentId: string) => void;
}

const FILTERS: Array<{ key: VerdictStatus | "ALL"; label: string }> = [
  { key: "ALL", label: "All" },
  { key: "RECOVERABLE", label: "Recoverable" },
  { key: "PARTIAL", label: "Partial" },
  { key: "METADATA_ONLY", label: "Metadata only" },
  { key: "JUNK", label: "Junk" },
];

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
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 px-3 py-2.5">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            onClick={() => setFilter(option.key)}
            className={`rounded px-2 py-1 text-[11px] transition-colors ${
              filter === option.key
                ? "bg-ink-700 text-slate-100"
                : "text-slate-500 hover:bg-ink-850 hover:text-slate-300"
            }`}
          >
            {option.label}
            <span className="ml-1.5 tabular-nums text-slate-600">{counts[option.key] ?? 0}</span>
          </button>
        ))}
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter by name, format or id"
          className="ml-auto w-52 rounded border border-ink-700 bg-ink-950 px-2 py-1 text-[11px] outline-none placeholder:text-slate-600 focus:border-ink-600"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-left text-[12px]">
          <thead className="sticky top-0 z-10 bg-ink-900 text-[10px] uppercase tracking-wider text-slate-600">
            <tr className="border-b border-ink-700">
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Name / format</th>
              <th className="px-3 py-2 font-medium">Size</th>
              <th className="px-3 py-2 font-medium">Offset</th>
              <th className="px-3 py-2 font-medium">Source</th>
              <th className="px-3 py-2 font-medium">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((fragment) => {
              const status = fragment.verdict?.status ?? "JUNK";
              const style = STATUS_STYLES[status];
              const isSelected = fragment.fragment_id === selectedId;
              const name = fragment.source_path?.split("/").pop();
              return (
                <tr
                  key={fragment.fragment_id}
                  onClick={() => onSelect(fragment.fragment_id)}
                  className={`cursor-pointer border-b border-ink-850 transition-colors ${
                    isSelected ? "bg-ink-800" : "hover:bg-ink-850"
                  }`}
                >
                  <td className="px-3 py-2 font-mono tabular-nums text-slate-600">{fragment.rank ?? "-"}</td>
                  <td className="px-3 py-2">
                    <div className="truncate font-medium text-slate-200">
                      {name ?? `carved ${fragment.format_guess}`}
                    </div>
                    <div className="text-[10px] text-slate-600">
                      {fragment.format_guess} · {fragment.category}
                      {fragment.ambiguity_group && (
                        <span className="ml-1.5 text-amber-600/80">
                          header shared with {fragment.candidates.length - 1} others
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono tabular-nums text-slate-400">
                    {formatBytes(fragment.length)}
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-slate-600">
                    {formatHex(fragment.offset)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        fragment.source === "carved"
                          ? "bg-violet-500/10 text-violet-300"
                          : "bg-ink-700 text-slate-400"
                      }`}
                    >
                      {fragment.source === "carved" ? "carved" : "directory"}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] ${style.bg} ${style.border} ${style.color}`}
                    >
                      {style.label}
                    </span>
                  </td>
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-slate-600">
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
