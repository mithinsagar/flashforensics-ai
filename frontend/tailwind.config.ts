import type { Config } from "tailwindcss";

/**
 * The palette is a deliberate argument, not a theme picker's output.
 *
 * Recovery tools default to cold blue because that is what "technical" looks
 * like, and the result is that every one of them looks like every other one.
 * This one is built on the opposite tension: the surfaces are a warm near-black,
 * the colour of a darkroom rather than a terminal, and the primary accent is the
 * gold of the contacts on the underside of an SD card — the salvage colour, used
 * only for the things a person actually gets back. Cold cyan is reserved for the
 * machine: scanning, watching, working. So warm means your data, cold means the
 * software, and the two never borrow each other's meaning.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm charcoal surfaces, darkest first.
        ink: {
          990: "#050404",
          950: "#080706",
          900: "#0d0b0a",
          850: "#131110",
          800: "#1a1715",
          750: "#221d1a",
          700: "#2a2421",
          600: "#3b332d",
          500: "#4f453d",
        },
        // Salvage gold: the brand, and the colour of anything recovered.
        gold: {
          200: "#ffe9b8",
          300: "#ffd98a",
          400: "#fbc55e",
          500: "#f0a92b",
          600: "#cf8712",
          700: "#9a6209",
        },
        ember: { 400: "#ff8a5c", 500: "#f2643a", 600: "#cf4a24" },
        // Cold cyan: the machine looking at something.
        scan: { 300: "#7ee8ea", 400: "#3fd3d8", 500: "#1fb2ba", 600: "#128089" },
        // Warm-tinted greys for type.
        bone: "#f4efe8",
        ash: "#a99f95",
        dim: "#736a62",
        faint: "#4b443e",
        signal: {
          recover: "#4bd894",
          partial: "#f0a92b",
          meta: "#5fc9df",
          junk: "#6d635a",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "Georgia", "serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      letterSpacing: {
        widest: "0.24em",
      },
      keyframes: {
        // A card being read: a bar of light travelling across a surface.
        sweep: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(200%)" },
        },
        rise: {
          "0%": { opacity: "0", transform: "translateY(14px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        riseSlow: {
          "0%": { opacity: "0", transform: "translateY(26px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        ring: {
          "0%": { transform: "scale(0.7)", opacity: "0.7" },
          "100%": { transform: "scale(2.4)", opacity: "0" },
        },
        flicker: {
          "0%, 100%": { opacity: "1" },
          "45%": { opacity: "0.55" },
        },
        drift: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-7px)" },
        },
        marquee: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        // The logo mark assembling itself out of scattered blocks.
        settle: {
          "0%": { opacity: "0", transform: "translateX(6px) scaleX(0.3)" },
          "60%": { opacity: "1" },
          "100%": { opacity: "1", transform: "translateX(0) scaleX(1)" },
        },
        glow: {
          "0%, 100%": { opacity: "0.35" },
          "50%": { opacity: "0.75" },
        },
      },
      animation: {
        sweep: "sweep 2.6s cubic-bezier(0.4, 0, 0.2, 1) infinite",
        rise: "rise 0.5s cubic-bezier(0.22, 1, 0.36, 1) both",
        "rise-slow": "riseSlow 0.8s cubic-bezier(0.22, 1, 0.36, 1) both",
        ring: "ring 2.4s cubic-bezier(0.4, 0, 0.2, 1) infinite",
        flicker: "flicker 3s ease-in-out infinite",
        drift: "drift 7s ease-in-out infinite",
        marquee: "marquee 46s linear infinite",
        settle: "settle 0.6s cubic-bezier(0.22, 1, 0.36, 1) both",
        glow: "glow 4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
