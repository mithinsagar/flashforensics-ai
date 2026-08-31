"use client";

import { useEffect, useRef } from "react";

/**
 * The card itself, drawn as the thing the software actually sees.
 *
 * Flash storage is a grid of sectors, most of them empty, with data sitting in
 * clusters. So this is that grid: value noise decides which cells hold something,
 * which produces the uneven clumping a real volume has rather than an even
 * scatter of dots. A read head sweeps across it, and in its wake occupied cells
 * flare gold and fade — the scanner finding a file. The cursor lights the sectors
 * near it, so moving the mouse feels like holding a lamp over the surface.
 *
 * It is decorative in the sense that no measurement is plotted here, and not
 * decorative in the sense that every part of it means the thing it looks like.
 *
 * Cost is kept to two passes: every cell gets one `fillRect` for its resting
 * state, and only the small subset near the sweep, the cursor or a flare gets a
 * second additive one. Colour strings come from precomputed tables so a frame
 * allocates nothing.
 */

interface Props {
  className?: string;
  /** A run is in progress: the head moves faster and finds more. */
  active?: boolean;
  /** Overall brightness, 0 to 1. */
  intensity?: number;
}

const STEP = 19;
const CELL = 15;
const BUCKETS = 64;
const MAX_FLARES = 140;

function table(rgb: string, ceiling: number): string[] {
  return Array.from({ length: BUCKETS + 1 }, (_, index) => `rgba(${rgb},${((index / BUCKETS) * ceiling).toFixed(3)})`);
}

const WARM = table("255,236,214", 1);
const GOLD = table("240,169,43", 1);
const HOT = table("255,229,178", 1);
const SCAN = table("63,211,216", 1);

/** Deterministic hash in [0,1) — the same field every load, no allocation. */
function hash2(x: number, y: number): number {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

function smooth(t: number): number {
  return t * t * (3 - 2 * t);
}

/** Two octaves of value noise: big clusters with smaller structure inside them. */
function noise(x: number, y: number): number {
  let total = 0;
  let amplitude = 1;
  let frequency = 1;
  for (let octave = 0; octave < 2; octave += 1) {
    const px = x * frequency;
    const py = y * frequency;
    const x0 = Math.floor(px);
    const y0 = Math.floor(py);
    const fx = smooth(px - x0);
    const fy = smooth(py - y0);
    const top = hash2(x0, y0) * (1 - fx) + hash2(x0 + 1, y0) * fx;
    const bottom = hash2(x0, y0 + 1) * (1 - fx) + hash2(x0 + 1, y0 + 1) * fx;
    total += (top * (1 - fy) + bottom * fy) * amplitude;
    amplitude *= 0.45;
    frequency *= 2.7;
  }
  return total / 1.45;
}

export function SectorField({ className = "", active = false, intensity = 1 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const activeRef = useRef(active);
  const intensityRef = useRef(intensity);

  activeRef.current = active;
  intensityRef.current = intensity;

  useEffect(() => {
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;

    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let width = 0;
    let height = 0;
    let cols = 0;
    let rows = 0;
    let field = new Float32Array(0);

    // Pointer, and the lagging value that actually gets drawn, so the light
    // trails the cursor slightly instead of snapping to it.
    let pointerX = -9999;
    let pointerY = -9999;
    let lightX = -9999;
    let lightY = -9999;

    const flareCell = new Int32Array(MAX_FLARES).fill(-1);
    const flareBorn = new Float64Array(MAX_FLARES);
    let flareCursor = 0;

    function build() {
      const rect = parent!.getBoundingClientRect();
      width = Math.max(1, Math.ceil(rect.width));
      height = Math.max(1, Math.ceil(rect.height));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      canvas!.width = Math.ceil(width * dpr);
      canvas!.height = Math.ceil(height * dpr);
      canvas!.style.width = `${width}px`;
      canvas!.style.height = `${height}px`;
      context!.setTransform(dpr, 0, 0, dpr, 0, 0);

      cols = Math.ceil(width / STEP) + 1;
      rows = Math.ceil(height / STEP) + 1;
      field = new Float32Array(cols * rows);

      for (let row = 0; row < rows; row += 1) {
        for (let col = 0; col < cols; col += 1) {
          const value = noise(col * 0.085, row * 0.115);
          // Below the threshold the sector is empty, which is most of a real
          // card; above it, remap so clusters have internal variation.
          field[row * cols + col] = value < 0.46 ? 0 : Math.min(1, (value - 0.46) / 0.4);
        }
      }
    }

    function frame(now: number) {
      const time = now / 1000;
      const boost = intensityRef.current;
      const speed = activeRef.current ? 420 : 190;
      const span = width + 520;
      const head = reduced ? width * 0.42 : ((time * speed) % span) - 260;

      context!.clearRect(0, 0, width, height);

      // Resting state: every cell, one flat fill. Empty sectors stay barely
      // visible so the shape of the occupied regions is what the eye picks up.
      for (let row = 0; row < rows; row += 1) {
        const y = row * STEP;
        for (let col = 0; col < cols; col += 1) {
          const value = field[row * cols + col];
          const alpha = (value === 0 ? 0.02 : 0.032 + value * 0.135) * boost;
          if (alpha < 0.008) continue;
          context!.fillStyle = WARM[(alpha * BUCKETS) | 0] ?? WARM[0];
          context!.fillRect(col * STEP, y, CELL, CELL);
        }
      }

      context!.globalCompositeOperation = "lighter";

      // The read head: a bright leading edge with a short trail behind it. The
      // trail is kept tight on purpose — a wide one washes the whole field cold
      // and loses the warm/cold distinction the palette is built on.
      const headCol = Math.round(head / STEP);
      const reach = 9;
      for (let col = Math.max(0, headCol - reach); col < Math.min(cols, headCol + 3); col += 1) {
        const distance = head - col * STEP;
        if (distance < -STEP * 2) continue;
        const falloff = distance >= 0 ? Math.exp(-distance / 58) : Math.exp(distance / 18);
        if (falloff < 0.02) continue;
        for (let row = 0; row < rows; row += 1) {
          const value = field[row * cols + col];
          const lit = (0.028 + value * 0.32) * falloff * boost;
          if (lit < 0.012) continue;
          context!.fillStyle = SCAN[Math.min(BUCKETS, (lit * BUCKETS) | 0)];
          context!.fillRect(col * STEP, row * STEP, CELL, CELL);
        }
      }

      // Anything the head just passed over may turn out to be a file.
      if (!reduced) {
        const chance = activeRef.current ? 0.5 : 0.22;
        if (Math.random() < chance && cols > 0) {
          const col = Math.max(0, Math.min(cols - 1, headCol - 1));
          for (let attempt = 0; attempt < 6; attempt += 1) {
            const row = (Math.random() * rows) | 0;
            if (field[row * cols + col] > 0.45) {
              flareCell[flareCursor] = row * cols + col;
              flareBorn[flareCursor] = time;
              flareCursor = (flareCursor + 1) % MAX_FLARES;
              break;
            }
          }
        }
      }

      // Recovered sectors: a gold bloom that fades over a couple of seconds.
      const life = 2.1;
      for (let index = 0; index < MAX_FLARES; index += 1) {
        const cell = flareCell[index];
        if (cell < 0) continue;
        const age = time - flareBorn[index];
        if (age > life) {
          flareCell[index] = -1;
          continue;
        }
        const decay = 1 - age / life;
        const alpha = decay * decay * 0.85 * boost;
        const col = cell % cols;
        const row = (cell / cols) | 0;
        const x = col * STEP;
        const y = row * STEP;
        context!.fillStyle = HOT[Math.min(BUCKETS, (alpha * BUCKETS) | 0)];
        context!.fillRect(x, y, CELL, CELL);
        // A soft halo on the four neighbours, so a find reads as an event.
        const halo = alpha * 0.22;
        if (halo > 0.012) {
          context!.fillStyle = GOLD[Math.min(BUCKETS, (halo * BUCKETS) | 0)];
          context!.fillRect(x - STEP, y, CELL, CELL);
          context!.fillRect(x + STEP, y, CELL, CELL);
          context!.fillRect(x, y - STEP, CELL, CELL);
          context!.fillRect(x, y + STEP, CELL, CELL);
        }
      }

      // The lamp under the cursor.
      lightX += (pointerX - lightX) * 0.12;
      lightY += (pointerY - lightY) * 0.12;
      if (lightX > -2000) {
        const radius = 148;
        const minCol = Math.max(0, ((lightX - radius) / STEP) | 0);
        const maxCol = Math.min(cols - 1, ((lightX + radius) / STEP) | 0);
        const minRow = Math.max(0, ((lightY - radius) / STEP) | 0);
        const maxRow = Math.min(rows - 1, ((lightY + radius) / STEP) | 0);
        for (let row = minRow; row <= maxRow; row += 1) {
          for (let col = minCol; col <= maxCol; col += 1) {
            const dx = col * STEP + CELL / 2 - lightX;
            const dy = row * STEP + CELL / 2 - lightY;
            const distance = Math.sqrt(dx * dx + dy * dy);
            if (distance > radius) continue;
            const falloff = 1 - distance / radius;
            const value = field[row * cols + col];
            const alpha = falloff * falloff * (0.05 + value * 0.34) * boost;
            if (alpha < 0.012) continue;
            context!.fillStyle = GOLD[Math.min(BUCKETS, (alpha * BUCKETS) | 0)];
            context!.fillRect(col * STEP, row * STEP, CELL, CELL);
          }
        }
      }

      context!.globalCompositeOperation = "source-over";
    }

    let raf = 0;
    function loop(now: number) {
      frame(now);
      raf = requestAnimationFrame(loop);
    }

    function onPointer(event: PointerEvent) {
      const rect = canvas!.getBoundingClientRect();
      pointerX = event.clientX - rect.left;
      pointerY = event.clientY - rect.top;
      if (lightX < -2000) {
        lightX = pointerX;
        lightY = pointerY;
      }
    }

    function onLeave() {
      pointerX = -9999;
      pointerY = -9999;
    }

    function onVisibility() {
      cancelAnimationFrame(raf);
      if (!document.hidden && !reduced) raf = requestAnimationFrame(loop);
    }

    const observer = new ResizeObserver(() => {
      build();
      if (reduced) frame(performance.now());
    });
    observer.observe(parent);

    build();
    if (reduced) {
      frame(performance.now());
    } else {
      raf = requestAnimationFrame(loop);
      window.addEventListener("pointermove", onPointer, { passive: true });
      window.addEventListener("pointerleave", onLeave);
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={canvasRef} className={`pointer-events-none absolute inset-0 ${className}`} />;
}
