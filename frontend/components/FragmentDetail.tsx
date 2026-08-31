"use client";

import { useState } from "react";
import { STATUS_STYLES, api, formatBytes, formatHex } from "@/lib/api";
import type { Fragment } from "@/lib/types";

/** Formats a browser can draw without help, so the recovered bytes can be shown. */
const VIEWABLE = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"]);

/**
 * The recovered photo itself.
 *
 * This is the only part of the evidence panel that needs no explaining: a person
 * looking for their pictures wants to see the picture, and a half-drawn image
 * with a grey band across the bottom communicates "partially damaged" better
 * than any verdict label can. The browser decoding it is also an independent
 * check on the carve — if the extent were wrong by a byte, this would not render.
 */
function Preview({ fragment, sessionId }: Props & { fragment: Fragment }) {
  const [failed, setFailed] = useState(false);
  const format = (fragment.classification?.format ?? fragment.format_guess ?? "").toLowerCase();

  if (failed || !VIEWABLE.has(format)) return null;

  return (
    <div className="overflow-hidden rounded border border-ink-700 bg-ink-950">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={api.downloadUrl(sessionId, fragment.fragment_id)}
        alt={fragment.source_path ?? `recovered ${format}`}
        onError={() => setFailed(true)}
        className="mx-auto max-h-52 w-auto object-contain"
      />
      <div className="border-t border-ink-800 px-2 py-1 text-center text-[10px] text-faint">
        the recovered bytes, drawn by your browser
      </div>
    </div>
  );
}

interface Props {
  fragment: Fragment | null;
  sessionId: string;
}

function HexPreview({ hex }: { hex: string }) {
  const bytes = hex.match(/.{1,2}/g) ?? [];
  const ascii = bytes
    .map((byte) => {
      const value = parseInt(byte, 16);
      return value >= 32 && value <= 126 ? String.fromCharCode(value) : ".";
    })
    .join("");
  return (
    <div className="rounded border border-ink-700 bg-ink-950 px-2.5 py-2 font-mono text-[11px]">
      <div className="tracking-wider text-ash">{bytes.join(" ").toUpperCase()}</div>
      <div className="mt-1 text-faint">|{ascii}|</div>
    </div>
  );
}

/**
 * Everything measured about one fragment, with the evidence shown separately
 * from the conclusion.
 *
 * The split is the point. A verdict a user cannot check is just an assertion, so
 * the findings that produced it are listed as findings, and the problems that
 * downgraded it are listed as problems. Someone who disagrees with the verdict
 * can see exactly which observation they disagree with.
 */
export function FragmentDetail({ fragment, sessionId }: Props) {
  if (!fragment) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-faint">
        Select a fragment to see the evidence behind its verdict
      </div>
    );
  }

  const validation = fragment.validation;
  const verdict = fragment.verdict;
  const classification = fragment.classification;
  const style = STATUS_STYLES[verdict?.status ?? "JUNK"];
  const metadata = (validation?.metadata ?? {}) as Record<string, unknown>;

  const interesting = [
    ["width", "height"].every((key) => key in metadata)
      ? ["Dimensions", `${metadata.width} x ${metadata.height}`]
      : null,
    metadata.entries_seen ? ["Archive entries", String(metadata.entries_seen)] : null,
    metadata.page_objects ? ["Pages", String(metadata.page_objects)] : null,
    metadata.duration_seconds ? ["Duration", `${metadata.duration_seconds}s`] : null,
    metadata.frames ? ["Audio frames", String(metadata.frames)] : null,
    metadata.page_count ? ["Database pages", String(metadata.page_count)] : null,
    metadata.major_brand ? ["Container brand", String(metadata.major_brand)] : null,
    metadata.crc_failures !== undefined ? ["CRC failures", String(metadata.crc_failures)] : null,
    metadata.lines ? ["Lines", String(metadata.lines)] : null,
  ].filter(Boolean) as Array<[string, string]>;

  return (
    <div className="h-full overflow-y-auto">
      <div className="space-y-4 p-4">
        <div>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-bone">
                {fragment.source_path ?? `Carved ${fragment.format_guess}`}
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-faint">{fragment.fragment_id}</div>
            </div>
            <span className={`shrink-0 rounded border px-2 py-1 text-[11px] ${style.bg} ${style.border} ${style.color}`}>
              {style.label}
            </span>
          </div>

          {verdict?.explanation && (
            <p className="mt-2.5 text-[12px] leading-relaxed text-ash">{verdict.explanation}</p>
          )}
        </div>

        <Preview fragment={fragment} sessionId={sessionId} />

        <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-y border-ink-800 py-3 text-[11px]">
          <Field label="Format" value={`${classification?.format ?? fragment.format_guess} (${fragment.category})`} />
          <Field label="Size" value={formatBytes(fragment.length)} />
          <Field label="Offset" value={formatHex(fragment.offset)} mono />
          <Field label="Sectors" value={`${fragment.sector_start} – ${fragment.sector_end}`} mono />
          <Field
            label="Clusters"
            value={fragment.cluster_start ? `${fragment.cluster_start} – ${fragment.cluster_end}` : "n/a"}
            mono
          />
          <Field label="Entropy" value={`${fragment.entropy} bits/byte`} mono />
          {interesting.map(([label, value]) => (
            <Field key={label} label={label} value={value} />
          ))}
        </div>

        {classification?.reasoning && (
          <Section title={`Identification (${classification.method}, confidence ${classification.confidence})`}>
            <p className="text-[12px] leading-relaxed text-ash">{classification.reasoning}</p>
            {classification.alternatives?.length > 0 && (
              <p className="mt-1.5 text-[11px] text-faint">
                Ruled out: {classification.alternatives.join(", ")}
              </p>
            )}
          </Section>
        )}

        {validation && validation.evidence.length > 0 && (
          <Section title="Structural findings">
            <ul className="space-y-1">
              {validation.evidence.map((item, index) => (
                <li key={index} className="flex gap-2 text-[11px] leading-relaxed text-ash">
                  <span className="mt-[3px] shrink-0 text-signal-recover">▸</span>
                  {item}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {validation && validation.problems.length > 0 && (
          <Section title="Problems found">
            <ul className="space-y-1">
              {validation.problems.map((item, index) => (
                <li key={index} className="flex gap-2 text-[11px] leading-relaxed text-amber-200/70">
                  <span className="mt-[3px] shrink-0 text-gold-500">▸</span>
                  {item}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {Array.isArray(metadata.entry_names) && metadata.entry_names.length > 0 && (
          <Section title="Archive contents">
            <div className="max-h-32 overflow-y-auto rounded border border-ink-700 bg-ink-950 p-2 font-mono text-[10px] text-ash">
              {(metadata.entry_names as string[]).map((name, index) => (
                <div key={index} className="truncate">
                  {name}
                </div>
              ))}
            </div>
          </Section>
        )}

        {Array.isArray(metadata.exif_strings) && metadata.exif_strings.length > 0 && (
          <Section title="EXIF">
            <div className="flex flex-wrap gap-1.5">
              {(metadata.exif_strings as string[]).map((value, index) => (
                <span key={index} className="rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-ash">
                  {value}
                </span>
              ))}
            </div>
          </Section>
        )}

        <Section title="First 16 bytes">
          <HexPreview hex={fragment.header_hex} />
        </Section>

        <Section title="Integrity">
          <div className="break-all font-mono text-[10px] text-dim">sha256 {fragment.sha256}</div>
        </Section>

        <a
          href={api.downloadUrl(sessionId, fragment.fragment_id)}
          className="block rounded border border-ink-600 bg-ink-800 py-2 text-center text-[12px] font-medium text-bone transition-colors hover:bg-ink-700"
        >
          Download this file
        </a>
      </div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-faint">{label}</div>
      <div className={`text-ash ${mono ? "font-mono text-[11px]" : ""}`}>{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-faint">
        {title}
      </div>
      {children}
    </div>
  );
}
