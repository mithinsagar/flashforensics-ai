"use client";

import { STATUS_STYLES, formatBytes } from "@/lib/api";
import type { VerdictStatus, VerificationResponse } from "@/lib/types";

/** The internal verdict names, spelled the way the rest of the UI spells them. */
function verdictLabel(status: string | null): string {
  if (!status) return "none";
  return STATUS_STYLES[status as VerdictStatus]?.label ?? status;
}

/**
 * The demo's answer key, shown rather than summarised.
 *
 * A recovery tool claiming it recovered everything is worth nothing on its own,
 * so when the card was built by this app it knows exactly what was written and
 * exactly what was broken, and every row here puts the pipeline's answer beside
 * the truth. The failures would be as visible as the successes, which is the
 * only reason the successes mean anything.
 */
export function VerificationPanel({ result }: { result: VerificationResponse }) {
  if (!result.available || !result.rows) return null;

  const perfect =
    result.recall === 1 &&
    result.format_accuracy === 1 &&
    result.extent_accuracy === 1 &&
    result.verdict_accuracy === 1 &&
    result.false_positives === 0;

  return (
    <section className="panel animate-rise overflow-hidden">
      <div className="panel-header">
        <span>Checked against what was actually done to the card</span>
        <span
          className={`flex items-center gap-1.5 normal-case tracking-normal ${
            perfect ? "text-signal-recover" : "text-signal-partial"
          }`}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: perfect ? "#4bd894" : "#f0a92b",
              boxShadow: `0 0 8px ${perfect ? "rgba(75,216,148,0.8)" : "rgba(240,169,43,0.8)"}`,
            }}
          />
          {perfect ? "every check passed" : "some checks failed"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-px bg-white/[0.055] md:grid-cols-5">
        <Metric label="Files found" value={`${result.found}/${result.planted}`} ratio={result.recall} />
        <Metric
          label="Type identified"
          value={`${result.format_correct}/${result.found}`}
          ratio={result.format_accuracy}
        />
        <Metric
          label="Size exact"
          value={`${result.extent_exact}/${result.extent_scored}`}
          ratio={result.extent_accuracy}
        />
        <Metric
          label="Damage called right"
          value={`${result.verdict_correct}/${result.found}`}
          ratio={result.verdict_accuracy}
        />
        <Metric
          label="False alarms"
          value={String(result.false_positives ?? 0)}
          ratio={result.false_positives === 0 ? 1 : 0}
        />
      </div>

      <div className="max-h-[340px] overflow-auto">
        <table className="w-full text-left text-[11.5px]">
          <thead className="sticky top-0 bg-[#0c0b0a] font-mono text-[9.5px] uppercase tracking-widest text-faint backdrop-blur">
            <tr>
              <th className="border-b border-white/[0.06] px-3.5 py-2.5 font-normal">File</th>
              <th className="border-b border-white/[0.06] px-3.5 py-2.5 font-normal">What was done to it</th>
              <th className="border-b border-white/[0.06] px-3.5 py-2.5 font-normal">Type</th>
              <th className="border-b border-white/[0.06] px-3.5 py-2.5 font-normal">Size</th>
              <th className="border-b border-white/[0.06] px-3.5 py-2.5 font-normal">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row) => (
              <tr key={row.path} className="border-b border-white/[0.035] text-ash transition-colors last:border-b-0 hover:bg-white/[0.025]">
                <td className="px-3.5 py-2 font-mono text-[10px] text-bone">{row.path}</td>
                <td className="px-3.5 py-2">{SCENARIO_LABELS[row.scenario] ?? row.scenario}</td>
                <td className="px-3.5 py-2">
                  <Check ok={row.format_ok}>
                    {row.detected_format ?? "not found"}
                    {!row.format_ok && ` (expected ${row.expected_format})`}
                  </Check>
                </td>
                <td className="px-3.5 py-2">
                  {row.extent_ok === null ? (
                    <span className="text-faint">not measurable</span>
                  ) : (
                    <Check ok={row.extent_ok}>
                      {formatBytes(row.detected_size ?? 0)}
                      {!row.extent_ok && ` (expected ${formatBytes(row.expected_size)})`}
                    </Check>
                  )}
                </td>
                <td className="px-3.5 py-2">
                  <Check ok={row.verdict_ok}>
                    {verdictLabel(row.detected_status)}
                    {!row.verdict_ok && ` (expected ${verdictLabel(row.expected_status)})`}
                  </Check>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="border-t border-white/[0.06] px-3.5 py-3 text-[11.5px] leading-relaxed text-faint">
        Only the sample card can be graded like this: it is the one card where the damage was applied
        on purpose and written down first. A real card has no answer key, which is exactly why this
        one exists.
      </p>
    </section>
  );
}

const SCENARIO_LABELS: Record<string, string> = {
  intact: "left alone",
  orphaned: "directory entry erased",
  deleted: "deleted",
  truncated: "cut off mid-write",
  payload_corrupted: "data scribbled over",
  chain_broken: "allocation chain severed",
};

function Metric({ label, value, ratio }: { label: string; value: string; ratio?: number }) {
  const ok = (ratio ?? 0) >= 1;
  return (
    <div className="relative overflow-hidden bg-[#0a0908] px-4 py-3.5">
      <span
        className="absolute inset-x-0 top-0 h-px"
        style={{ background: `linear-gradient(90deg, transparent, ${ok ? "#4bd894" : "#f0a92b"}, transparent)`, opacity: 0.7 }}
      />
      <div className="stat-label">{label}</div>
      <div className={`stat-value mt-1.5 ${ok ? "text-signal-recover" : "text-signal-partial"}`}>{value}</div>
    </div>
  );
}

function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span className={ok ? "text-signal-recover" : "text-signal-partial"}>
      {ok ? "✓ " : "✗ "}
      {children}
    </span>
  );
}
