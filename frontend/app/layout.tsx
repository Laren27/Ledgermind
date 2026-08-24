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
// AFTER app/globals.css, deliberately and necessarily. This file had never
// been imported -- Next does not auto-load CSS by proximity -- so its 26
// tokens were dead and 37 var(--paper-*) references across ten mounted
// components resolved to nothing at all.
//
// Order matters because both files declare on bare :root, where specificity
// ties and the later import wins. That is safe only because the two
// declaration sets are now disjoint; the one name they shared was removed in
// the commit before this one. Re-run the definition-position intersection scan
// before adding any unprefixed token to either file.
import "../components/document/globals.css";

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
    // The next/font variables live on the ROOT element now, not on the body
    // element.
    //
    // globals.css declares --font-editorial / --font-ui / --font-archival on
    // :root, and each is `var(--font-<face>), <stack>` with no fallback inside
    // the var(). While the face variables sat one level lower, that reference
    // was unresolvable AT :root, so all three tokens computed to the
    // guaranteed-invalid value and inherited that way down the whole tree.
    // Measured 2026-08-23: all three read invalid at both levels, and every
    // consumer was silently serving its literal fallback -- Georgia,
    // monospace, sans-serif -- while three self-hosted faces sat loaded and
    // unused.
    //
    // Moved the classes UP rather than moving the declarations down. :root is
    // the highest scope there is, so no descendant can render outside it. The
    // alternative leaves anything above the body box unstyled, and it would
    // also split the token layer, parking three font tokens somewhere other
    // than where the other 77 live.
    //
    // font-body stays put: it is a Tailwind utility, not a variable
    // definition, and it wants to set the base face on the body box.
    <html lang="en" className={`${fraunces.variable} ${plexSans.variable} ${plexMono.variable}`}>
      <body className="font-body">
        {children}
        <SpeedInsights />
      </body>
    </html>
  );
}
