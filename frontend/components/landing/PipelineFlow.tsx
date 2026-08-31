"use client";

/**
 * The five agents, drawn rather than listed.
 *
 * A bulleted list of stage names tells a visitor nothing they could not guess.
 * Each stage here carries a small diagram of the actual operation — the index
 * being read, a fragment bracketed out of raw bytes, candidates being narrowed,
 * evidence being weighed — so someone who never reads the paragraph still comes
 * away knowing the pipeline does five different kinds of work.
 */

const STAGES = [
  {
    n: "01",
    agent: "scanner",
    title: "Read the card",
    detail:
      "Parse the boot sector and the allocation table, map every cluster, and record the damage found on the way through.",
    tint: "#5fc9df",
    art: <ScannerArt />,
  },
  {
    n: "02",
    agent: "carver",
    title: "Pull the files out",
    detail:
      "Where the index is gone, find files by their own bytes: 77 header signatures, footers, and internal length fields.",
    tint: "#c74ac0",
    art: <CarverArt />,
  },
  {
    n: "03",
    agent: "classifier",
    title: "Work out what they are",
    detail:
      "Measure each fragment, retrieve the closest of 69 format descriptions, and settle the ones that look alike.",
    tint: "#f0a92b",
    art: <ClassifierArt />,
  },
  {
    n: "04",
    agent: "adjudicator",
    title: "Decide what survived",
    detail:
      "Validate structure end to end and rule on each file: whole, partly damaged, name only, or never a file at all.",
    tint: "#4bd894",
    art: <AdjudicatorArt />,
  },
  {
    n: "05",
    agent: "reporter",
    title: "Explain it plainly",
    detail:
      "Write what happened to this card in sentences, and index every fragment so you can ask questions about it.",
    tint: "#a99f95",
    art: <ReporterArt />,
  },
];

export function PipelineFlow() {
  return (
    <section className="relative mx-auto max-w-[1280px] px-6 py-20">
      <header className="mb-12 max-w-2xl">
        <p className="eyebrow">the pipeline</p>
        <h2 className="display mt-3 text-[40px] text-bone sm:text-[52px]">
          Five agents, in the only order that works.
        </h2>
        <p className="lede mt-4">
          Nothing can be carved before the scanner has isolated the orphaned regions, and nothing can
          be judged before it has been identified. The chain is linear because the dependencies
          genuinely are.
        </p>
      </header>

      <ol className="grid gap-px overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.055] md:grid-cols-3 lg:grid-cols-5">
        {STAGES.map((stage, index) => (
          <li
            key={stage.agent}
            className="group relative bg-[#0a0908] p-6 transition-colors duration-500 hover:bg-[#100d0b]"
          >
            {/* The light that runs down the chain, one stage after another. */}
            <span
              className="absolute inset-x-0 top-0 h-px opacity-0 transition-opacity duration-500 group-hover:opacity-100"
              style={{ background: `linear-gradient(90deg, transparent, ${stage.tint}, transparent)` }}
            />
            <span
              className="absolute left-0 top-0 h-px w-full animate-sweep"
              style={{
                background: `linear-gradient(90deg, transparent, ${stage.tint}88, transparent)`,
                animationDelay: `${index * 520}ms`,
                animationDuration: "3.4s",
              }}
            />

            <div className="flex items-start justify-between">
              <span className="font-mono text-[10px] tracking-widest text-faint">{stage.n}</span>
              <div className="opacity-70 transition-opacity duration-500 group-hover:opacity-100" style={{ color: stage.tint }}>
                {stage.art}
              </div>
            </div>

            <h3 className="mt-5 text-[15px] font-medium text-bone">{stage.title}</h3>
            <p className="mt-2 text-[12px] leading-relaxed text-dim">{stage.detail}</p>
            <p className="mt-4 font-mono text-[10px] uppercase tracking-widest" style={{ color: stage.tint }}>
              {stage.agent}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/* --- the small diagrams: 44×44, one idea each --------------------- */

function ScannerArt() {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
      <rect x="6" y="8" width="32" height="28" rx="2.5" stroke="currentColor" strokeWidth="1.1" opacity="0.5" />
      {[0, 1, 2, 3].map((row) =>
        [0, 1, 2, 3, 4].map((col) => (
          <rect
            key={`${row}-${col}`}
            x={9.5 + col * 5.6}
            y={11.5 + row * 5.6}
            width="4"
            height="4"
            rx="0.8"
            fill="currentColor"
            opacity={(row * 5 + col) % 3 === 0 ? 0.85 : 0.18}
          />
        )),
      )}
      <line x1="22" y1="6" x2="22" y2="38" stroke="currentColor" strokeWidth="1.1" opacity="0.9">
        <animate attributeName="x1" values="10;34;10" dur="3.6s" repeatCount="indefinite" />
        <animate attributeName="x2" values="10;34;10" dur="3.6s" repeatCount="indefinite" />
      </line>
    </svg>
  );
}

function CarverArt() {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
      {[0, 1, 2, 3, 4, 5, 6].map((index) => (
        <rect key={index} x={7} y={9 + index * 4} width={30} height="2.2" rx="1.1" fill="currentColor" opacity={index >= 2 && index <= 4 ? 0.85 : 0.16} />
      ))}
      {/* The bracket that says: this run of bytes is one file. */}
      <path d="M5 15.5v11M39 15.5v11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M5 15.5h3M5 26.5h3M39 15.5h-3M39 26.5h-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function ClassifierArt() {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
      <rect x="5" y="17" width="10" height="10" rx="1.6" fill="currentColor" opacity="0.85" />
      <path d="M16 22h6" stroke="currentColor" strokeWidth="1.1" opacity="0.45" />
      {[0, 1, 2].map((index) => (
        <g key={index} opacity={index === 1 ? 0.9 : 0.28}>
          <path d={`M22 22 L27 ${13 + index * 9}`} stroke="currentColor" strokeWidth="1.1" />
          <rect x="27" y={9 + index * 9} width="12" height="8" rx="1.6" stroke="currentColor" strokeWidth="1.1" />
        </g>
      ))}
    </svg>
  );
}

function AdjudicatorArt() {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
      <circle cx="22" cy="22" r="14" stroke="currentColor" strokeWidth="1.1" opacity="0.4" />
      <circle
        cx="22"
        cy="22"
        r="14"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeDasharray="88"
        strokeDashoffset="26"
        transform="rotate(-90 22 22)"
      />
      <path d="M16.5 22.3l4 4 7.5-8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ReporterArt() {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
      <rect x="9" y="7" width="26" height="30" rx="2.5" stroke="currentColor" strokeWidth="1.1" opacity="0.5" />
      {[0, 1, 2, 3, 4].map((index) => (
        <rect
          key={index}
          x="13"
          y={12.5 + index * 5}
          width={index === 4 ? 10 : index % 2 === 0 ? 18 : 14}
          height="2"
          rx="1"
          fill="currentColor"
          opacity={index === 0 ? 0.85 : 0.3}
        />
      ))}
    </svg>
  );
}
