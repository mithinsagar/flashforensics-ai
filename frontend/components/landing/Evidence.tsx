"use client";

/**
 * The three claims worth making, each shown as a small piece of the real thing.
 *
 * "Trustworthy" and "explainable" are words every tool puts on its landing page,
 * so none of them are used here. What is shown instead is an actual evidence
 * list, an actual row from the graded answer key, and the actual read-only
 * device path — three specifics that a tool without them could not fake.
 */
export function Evidence() {
  return (
    <section className="relative mx-auto max-w-[1280px] px-6 py-20">
      <header className="mb-12 max-w-2xl">
        <p className="eyebrow">why trust the answer</p>
        <h2 className="display mt-3 text-[40px] text-bone sm:text-[52px]">
          A recovery tool that says <em className="text-salvage not-italic">everything is fine</em> is
          worth nothing.
        </h2>
        <p className="lede mt-4">
          So this one shows the evidence for each call, grades itself against a card whose damage was
          written down first, and never touches the original.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-12">
        <Card
          className="lg:col-span-5"
          eyebrow="evidence, not confidence"
          title="Every verdict shows its work"
          body="Each file carries the checks it passed and the ones it failed — header, footer, internal structure, declared length against real length. The verdict is the conclusion of that list, and the list stays visible."
        >
          <div className="space-y-1.5 font-mono text-[10.5px]">
            <Line ok>JFIF header at 0x0004, valid</Line>
            <Line ok>dimensions 1920×1080 from SOF0</Line>
            <Line ok>EXIF: Canon EOS 6D</Line>
            <Line ok={false}>EOI marker absent — file ends mid-scan</Line>
            <Line ok={false}>declared 482 KB, recovered 311 KB</Line>
            <div className="!mt-3 flex items-center gap-2 pt-1">
              <span className="chip border-gold-600/40 bg-gold-500/12 text-gold-300">partly damaged</span>
              <span className="text-faint">confidence 0.86</span>
            </div>
          </div>
        </Card>

        <Card
          className="lg:col-span-4"
          eyebrow="the answer key"
          title="It grades itself in public"
          body="The sample card is built by this app, so it knows exactly which files were written and exactly how each one was broken. Every run is scored against that record — and a failure would be as visible as a success."
        >
          <div className="overflow-hidden rounded-md border border-white/[0.07]">
            {[
              ["holiday_01.jpg", "truncated", "partly damaged"],
              ["notes.pdf", "entry erased", "fully recovered"],
              ["archive.zip", "chain severed", "partly damaged"],
            ].map(([file, done, verdict]) => (
              <div key={file} className="flex items-center gap-2 border-b border-white/[0.05] px-2.5 py-1.5 font-mono text-[10px] last:border-b-0">
                <span className="text-signal-recover">✓</span>
                <span className="min-w-0 flex-[1.4] truncate text-ash">{file}</span>
                <span className="min-w-0 flex-1 truncate text-faint">{done}</span>
                <span className="min-w-0 flex-1 truncate text-bone/70">{verdict}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card
          className="lg:col-span-3"
          eyebrow="read only, always"
          title="It never writes to the card"
          body="The device is opened read-only and memory-mapped. Nothing is repaired in place, because the one irreversible mistake in data recovery is writing to the thing you are trying to recover."
        >
          <div className="overflow-x-auto rounded-md border border-white/[0.07] bg-black/40 px-3 py-2.5 font-mono text-[10px] leading-relaxed">
            <div className="text-scan-400">open(&quot;/dev/rdisk4&quot;, O_RDONLY)</div>
            <div className="mt-1 text-faint">mmap · PROT_READ</div>
            <div className="mt-1 text-dim">0 bytes written</div>
          </div>
        </Card>
      </div>
    </section>
  );
}

function Card({
  eyebrow,
  title,
  body,
  children,
  className = "",
}: {
  eyebrow: string;
  title: string;
  body: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <article className={`panel lift group flex flex-col p-6 ${className}`}>
      <p className="eyebrow text-dim">{eyebrow}</p>
      <h3 className="display mt-3 text-[26px] leading-tight text-bone">{title}</h3>
      <p className="mt-3 text-[12.5px] leading-relaxed text-dim">{body}</p>
      <div className="mt-6 flex-1 rounded-lg border border-white/[0.05] bg-black/25 p-3.5">{children}</div>
    </article>
  );
}

function Line({ ok = true, children }: { ok?: boolean; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className={ok ? "text-signal-recover" : "text-signal-partial"}>{ok ? "✓" : "✗"}</span>
      <span className={ok ? "text-ash" : "text-signal-partial/85"}>{children}</span>
    </div>
  );
}
