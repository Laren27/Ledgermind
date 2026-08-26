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
 *   next/font/local          The ten WOFF2 faces are committed under
 *                            frontend/fonts/ and read off disk. Nothing is
 *                            fetched during the build, and nothing is fetched at
 *                            page load. The import never ships to the browser.
 *
 *                            This replaced next/font/google, which fetched all
 *                            ten files from fonts.gstatic.com AT BUILD TIME with
 *                            no fallback. On this machine that failed
 *                            intermittently -- socket hang up / ETIMEDOUT on
 *                            individual font assets while the same host's root
 *                            path answered in under a second -- and a failed
 *                            fetch fails the build outright, which blocked every
 *                            frontend commit behind it. Vercel's builders carry
 *                            the identical dependency. Build-time fetching was
 *                            never the safe half of "fetched at build time and
 *                            self-hosted"; it was the failing half.
 *
 * THE THREE FONT VARIABLES ARE THE POINT OF THIS FILE.
 * Each loader returns an object carrying a CSS custom property name, applied to
 * the ROOT element below -- not to <body>, which is where this paragraph used
 * to say they went, and where they genuinely did until 55047c5 moved them up.
 *
 * No component names a font family. That much is true, and SCAN C2 of
 * scripts/check-tokens.mjs now holds it true rather than leaving it to whoever
 * greps next.
 *
 * WHAT USED TO FOLLOW WAS FALSE: "changing a typeface is a one-file change."
 * Components read semantic tokens, but those tokens are declared across THREE
 * files, and two of them named families outright instead of referencing the
 * variables registered here:
 *
 *   app/globals.css                  --font-editorial / --font-ui /
 *                                    --font-archival. Correctly routed.
 *   components/document/globals.css  --font-body and --font-document-title.
 *                                    BOTH named a family literally, so they
 *                                    resolved to whatever the viewer's machine
 *                                    had installed and never to a face
 *                                    registered here. --font-body carries 17
 *                                    references across eight components,
 *                                    including every surface that prints a
 *                                    financial figure; measured live, its
 *                                    leading entry supplied not one glyph on
 *                                    this machine, plain 'A' included.
 *   tailwind.config.ts               display / body / mono / sans. `sans` was
 *                                    unbound, so `font-sans` fell through to
 *                                    Tailwind's default stack at four sites.
 *
 * All three are routed now, and SCAN C1 keeps them that way. So the honest
 * version of the old claim: changing a typeface means changing the loader
 * HERE, and nowhere else -- provided the variable name is kept. Renaming a
 * variable is a three-file change, and those three files are the list above.
 *
 * If text renders in a fallback face, the break is in one of those three, or
 * here. Never in the component.
 */

import type { Metadata } from "next";
import localFont from "next/font/local";
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

// The ten files in ../fonts are the latin, normal-style WOFF2s shipped by
// @fontsource/fraunces, @fontsource/ibm-plex-sans and @fontsource/ibm-plex-mono
// (all 5.3.0), obtained with `npm pack` and copied in. Fontsource is not a
// dependency of this project -- nothing was added to package.json -- because
// only the binaries are wanted, not its CSS or its version resolution. The
// weight is in the filename rather than in a hash, so a wrong mapping here is
// readable rather than invisible.
//
// `display: "swap"` is stated rather than assumed. It is also next/font's
// default, so it was in force under next/font/google without appearing in this
// file; writing it out keeps the behaviour identical AND visible, so a future
// default change cannot move it silently.
//
// There is no `subsets` key: that option tells the Google loader which subset to
// FETCH. The subsetting already happened -- these files are latin-only on disk.

const fraunces = localFont({
  src: [
    { path: "../fonts/fraunces-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../fonts/fraunces-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "../fonts/fraunces-latin-600-normal.woff2", weight: "600", style: "normal" },
    { path: "../fonts/fraunces-latin-700-normal.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-fraunces",
});

const plexSans = localFont({
  src: [
    { path: "../fonts/ibm-plex-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../fonts/ibm-plex-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "../fonts/ibm-plex-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
  display: "swap",
  variable: "--font-plex-sans",
});

const plexMono = localFont({
  src: [
    { path: "../fonts/ibm-plex-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../fonts/ibm-plex-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "../fonts/ibm-plex-mono-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
  display: "swap",
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
