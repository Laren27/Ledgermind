/**
 * app/layout.tsx — the root layout, and the application's ONLY Server Component.
 *
 * frontend/app/ holds exactly three files (this, page.tsx, globals.css), so the
 * App Router serves exactly ONE route. Everything else under components/ is
 * reached from page.tsx, which carries "use client" -- meaning this file is the
 * only module that runs solely on the server. Two things below depend on that:
 *
 *   `export const metadata`  Next.js turns this into <title> and <meta>, which
 *                            are produced during the server render, before any
 *                            client bundle exists. A Client Component cannot
 *                            export it.
 *
 *   next/font/google         Fonts are fetched AT BUILD TIME and self-hosted;
 *                            no request reaches Google at page load. The import
 *                            never ships to the browser.
 *
 * THE THREE FONT VARIABLES ARE THE POINT OF THIS FILE.
 * Each loader returns an object carrying a CSS custom property name, applied to
 * <body> below. globals.css maps those to the semantic tokens every component
 * actually reads -- var(--font-editorial), var(--font-archival), var(--font-ui).
 * No component names a font family anywhere, so changing a typeface is a
 * one-file change rather than a repository-wide search. If text renders in a
 * fallback face, the break is here or in globals.css, never in the component.
 */

import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import { SpeedInsights } from "@vercel/speed-insights/next";
import "./globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-fraunces",
});

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: "LedgerMind",
  description: "Financial intelligence, verified.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body
        className={`${fraunces.variable} ${plexSans.variable} ${plexMono.variable} font-body`}
      >
        {children}
        <SpeedInsights />
      </body>
    </html>
  );
}
