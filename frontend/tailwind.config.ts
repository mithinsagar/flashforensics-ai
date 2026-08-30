import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#08090c", 900: "#0d0f14", 850: "#12151c", 800: "#181c25", 700: "#232936", 600: "#323a4c" },
        signal: { recover: "#3ddc97", partial: "#f5a524", meta: "#7aa2f7", junk: "#5b6478" },
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
