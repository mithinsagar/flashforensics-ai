"use client";

/**
 * The measured result, then the breadth, then the way out of the page.
 *
 * The three numbers are the ones the benchmark actually produces on the sample
 * card, which is why they are stated as "on the sample card" rather than as a
 * general claim: a real card has no answer key, and a number quoted without the
 * conditions it was measured under is marketing.
 */

const FORMATS = [
  "jpg", "png", "gif", "bmp", "tif", "webp", "heic", "avif", "cr2", "nef", "dng", "psd", "svg", "ico",
  "pdf", "docx", "xlsx", "pptx", "doc", "xls", "ppt", "rtf", "epub", "odt",
  "zip", "apk", "jar", "ipa", "rar", "7z", "gz", "bz2", "xz", "zst", "tar", "cab",
  "mp4", "mov", "m4a", "3gp", "avi", "mkv", "webm", "flv",
  "mp3", "wav", "flac", "ogg", "mid",
  "exe", "elf", "dex", "class", "wasm", "sqlite", "pcap",
  "json", "xml", "html", "csv", "txt", "ttf", "otf", "woff2", "iso", "vmdk", "luks", "kdbx",
];

const HALF = Math.ceil(FORMATS.length / 2);

export function Coverage({ formats }: { formats: number }) {
  return (
    <section className="relative border-t border-white/[0.06] py-20">
      <div className="mx-auto max-w-[1280px] px-6">
        <div className="grid gap-10 border-b border-white/[0.06] pb-16 lg:grid-cols-[0.9fr_1.1fr] lg:items-end">
          <div>
            <p className="eyebrow">measured, not asserted</p>
            <h2 className="display mt-3 text-[40px] text-bone sm:text-[50px]">
              Graded on every run,
              <br />
              in front of you.
            </h2>
            <p className="lede mt-4 max-w-[42ch]">
              These come from the sample card, where the damage was applied deliberately and recorded
              before the analysis ever ran. Click the button and the same table regenerates itself.
            </p>
          </div>

          <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.055]">
            <Number value="100%" label="files found" note="25 of 25 planted" />
            <Number value="100%" label="type identified" note="format called right" />
            <Number value="0" label="false alarms" note="no invented files" />
          </dl>
        </div>

        <div className="mt-16">
          <div className="flex flex-wrap items-baseline justify-between gap-4">
            <h3 className="display text-[30px] text-bone">
              {formats} formats, described rather than guessed
            </h3>
            <p className="max-w-[46ch] text-[12.5px] leading-relaxed text-dim">
              Each one is a written description in a vector index, so a fragment is matched against
              what a format actually looks like — not against a filename that no longer exists.
            </p>
          </div>
        </div>
      </div>

      {/* Two rows drifting in opposite directions: breadth you can feel at a
          glance without reading a single chip. */}
      <div className="mask-fade-r mt-10 space-y-3 overflow-hidden">
        <Marquee items={FORMATS.slice(0, HALF)} />
        <Marquee items={FORMATS.slice(HALF)} reverse />
      </div>

      <footer className="mx-auto mt-20 max-w-[1280px] px-6">
        <div className="panel relative overflow-hidden p-8 text-center sm:p-12">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-px animate-sweep bg-gradient-to-r from-transparent via-gold-400/80 to-transparent" />
          <h3 className="display text-[34px] text-bone sm:text-[44px]">
            The card is not blank. It is just <em className="text-salvage not-italic">unlabelled</em>.
          </h3>
          <p className="lede mx-auto mt-4 max-w-[52ch]">
            Run the sample card above to watch a real recovery end to end, or install it locally and
            point it at the card sitting in your reader.
          </p>
          <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
            <a
              href="https://github.com/mithinsagar/flashforensics-ai"
              target="_blank"
              rel="noreferrer"
              className="btn-ghost"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
              </svg>
              Read the source
            </a>
            <span className="font-mono text-[10px] uppercase tracking-widest text-faint">
              MIT licensed · no API key required
            </span>
          </div>
        </div>

        <p className="mt-8 text-center font-mono text-[10px] uppercase tracking-widest text-faint">
          FlashForensics AI — built by Mithin Sagar S
        </p>
      </footer>
    </section>
  );
}

function Number({ value, label, note }: { value: string; label: string; note: string }) {
  return (
    <div className="bg-[#0a0908] px-5 py-7 text-center">
      <dt className="display text-[46px] leading-none text-salvage sm:text-[54px]">{value}</dt>
      <dd className="mt-3">
        <span className="block text-[12.5px] text-bone">{label}</span>
        <span className="mt-1 block font-mono text-[10px] tracking-wider text-faint">{note}</span>
      </dd>
    </div>
  );
}

function Marquee({ items, reverse = false }: { items: string[]; reverse?: boolean }) {
  const doubled = [...items, ...items];
  return (
    <div
      className="flex w-max animate-marquee gap-2.5"
      style={reverse ? { animationDirection: "reverse", animationDuration: "58s" } : undefined}
    >
      {doubled.map((format, index) => (
        <span
          key={`${format}-${index}`}
          className="rounded-md border border-white/[0.07] bg-white/[0.02] px-3 py-1.5 font-mono text-[11px] text-dim transition-colors hover:border-gold-600/40 hover:text-gold-300"
        >
          .{format}
        </span>
      ))}
    </div>
  );
}
