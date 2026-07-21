import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        "bg-base": "#08090C",
        "bg-elevated": "#101318",
        "card-solid": "#14171D",
        "hairline": "rgba(255,255,255,0.07)",
        "text-primary": "#ECEDEF",
        "text-secondary": "#A8AEB8",
        "text-muted": "#7B8290",
        teal: "#3ED9C0",
        "teal-dim": "rgba(62,217,192,0.12)",
        sky: "#4FB8E8",
        "sky-dim": "rgba(79,184,232,0.12)",
        amber: "#E8A93B",
        coral: "#E2665A",
      },
      fontFamily: {
        display: ["var(--font-instrument)", "sans-serif"],
        body: ["var(--font-manrope)", "sans-serif"],
        mono: ["var(--font-plex-mono)", "monospace"],
      },
      borderRadius: {
        card: "18px",
      },
      boxShadow: {
        floating: "0 30px 70px rgba(0,0,0,0.5)",
      },
    },
  },
  plugins: [],
};
export default config;
