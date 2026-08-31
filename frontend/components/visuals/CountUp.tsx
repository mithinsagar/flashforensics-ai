"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Counts a result up to its value once, on arrival.
 *
 * Worth the few lines because these numbers land all at once when a run
 * finishes, and a number that moves is a number the eye goes to. It settles
 * quickly and never re-runs on re-render, so it reads as the result arriving
 * rather than as an animation playing.
 */
export function CountUp({ value, duration = 850 }: { value: number; duration?: number }) {
  const [display, setDisplay] = useState(value);
  const previous = useRef(value);

  useEffect(() => {
    const from = previous.current;
    previous.current = value;

    if (from === value) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDisplay(value);
      return;
    }

    const start = performance.now();
    let raf = 0;

    const step = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - progress) ** 3;
      setDisplay(Math.round(from + (value - from) * eased));
      if (progress < 1) raf = requestAnimationFrame(step);
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);

  return <>{display}</>;
}
