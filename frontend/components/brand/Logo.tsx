"use client";

/**
 * The mark: an SD card, read top to bottom as a recovery.
 *
 * The silhouette is the one everybody already recognises — a rectangle with a
 * clipped corner — and inside it sit five rows of data. The top rows are broken
 * into pieces with gaps between them and drawn in grey; each row down is more
 * whole and more gold, until the bottom rows are single unbroken bars. So the
 * mark states the product's claim in its own geometry: fragments at the top,
 * files at the bottom. It survives being 20 pixels tall because that reading
 * does not depend on any detail smaller than a bar.
 */

interface Props {
  size?: number;
  animated?: boolean;
  className?: string;
}

/** x-start, width, y, fill, and the order each bar assembles in. */
const BARS: Array<{ x: number; w: number; y: number; fill: string; step: number }> = [
  { x: 7.4, w: 2.9, y: 7.6, fill: "#6d635a", step: 0 },
  { x: 11.6, w: 2.4, y: 7.6, fill: "#6d635a", step: 1 },
  { x: 15.3, w: 4.3, y: 7.6, fill: "#6d635a", step: 2 },

  { x: 7.4, w: 5.6, y: 12.1, fill: "#9a6209", step: 3 },
  { x: 14.3, w: 5.3, y: 12.1, fill: "#9a6209", step: 4 },

  { x: 7.4, w: 12.2, y: 16.6, fill: "#cf8712", step: 5 },
  { x: 7.4, w: 12.2, y: 21.1, fill: "#f0a92b", step: 6 },
  { x: 7.4, w: 8.6, y: 25.6, fill: "#ffd98a", step: 7 },
];

export function Logo({ size = 30, animated = false, className = "" }: Props) {
  return (
    <svg
      width={(size * 26) / 32}
      height={size}
      viewBox="0 0 26 32"
      fill="none"
      className={`shrink-0 ${className}`}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="ff-card" x1="3" y1="1" x2="23" y2="31" gradientUnits="userSpaceOnUse">
          <stop stopColor="#ffd98a" stopOpacity="0.85" />
          <stop offset="0.55" stopColor="#f0a92b" stopOpacity="0.5" />
          <stop offset="1" stopColor="#9a6209" stopOpacity="0.55" />
        </linearGradient>
        <linearGradient id="ff-fill" x1="13" y1="1" x2="13" y2="31" gradientUnits="userSpaceOnUse">
          <stop stopColor="#241d17" />
          <stop offset="1" stopColor="#0c0a09" />
        </linearGradient>
      </defs>

      {/* The card itself: clipped top-right corner, rounded base. */}
      <path
        d="M6 1.1h11.9L23.1 6.3V28.6c0 1.3-1 2.3-2.3 2.3H5.2c-1.3 0-2.3-1-2.3-2.3V3.4c0-1.3 1-2.3 2.3-2.3Z"
        fill="url(#ff-fill)"
        stroke="url(#ff-card)"
        strokeWidth="1.15"
      />

      {BARS.map((bar) => (
        <rect
          key={`${bar.x}-${bar.y}`}
          x={bar.x}
          y={bar.y}
          width={bar.w}
          height={2.5}
          rx={1.25}
          fill={bar.fill}
          style={
            animated
              ? { animation: `settle 0.5s cubic-bezier(0.22,1,0.36,1) ${bar.step * 0.07}s both`, transformOrigin: "left center" }
              : undefined
          }
        />
      ))}
    </svg>
  );
}

/** The mark set with the name, for the page header. */
export function Wordmark({ animated = false }: { animated?: boolean }) {
  return (
    <div className="group flex items-center gap-2.5">
      <Logo size={30} animated={animated} className="transition-transform duration-500 group-hover:-translate-y-0.5" />
      <div className="leading-none">
        <div className="display text-[19px] text-bone">
          FlashForensics
          <span className="ml-1 align-super font-mono text-[9px] tracking-widest text-gold-500">AI</span>
        </div>
      </div>
    </div>
  );
}
