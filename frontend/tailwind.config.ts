import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      // NO colours here. This project has ONE palette and it is the custom
      // properties in app/globals.css. A second one declared here disagreed
      // with the first on a shared name -- both defined `teal`, at two
      // unrelated colours -- and styled four components, three of which were
      // unreachable. Those three are deleted; the fourth reads the custom
      // properties now.
      //
      // Adding a colour back here re-opens that split. Add it to
      // app/globals.css instead, and run the definition-position intersection
      // scan afterwards.
      // Bound to the three variables layout.tsx actually registers through
      // next/font. The previous display/body bindings named variables that are
      // defined nowhere in this repo, so both utilities fell through to the
      // generic fallback -- including on <body className="... font-body">,
      // which is why the document's base type was the browser default.
      //
      // Both these utilities and the :root font tokens now resolve: 4B moved
      // next/font's variables up to the root element, so every scope can see
      // them. Before that move only utilities applied at or below the body box
      // could, which is why these worked while --font-editorial did not.
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
