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
      // Bound to the three variables layout.tsx actually registers through
      // next/font. The previous display/body bindings named variables that are
      // defined nowhere in this repo, so both utilities fell through to the
      // generic fallback -- including on <body className="... font-body">,
      // which is why the document's base type was the browser default.
      //
      // These resolve where the old :root font tokens do not: next/font puts
      // its variables on <body>, and a utility class applied to body or below
      // can see them. A token declared on :root cannot.
      fontFamily: {
        display: ["var(--font-fraunces)", "serif"],
        body: ["var(--font-plex-sans)", "sans-serif"],
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
