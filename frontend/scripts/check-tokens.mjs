#!/usr/bin/env node
/**
 * check-tokens — two standing checks on the CSS custom-property layer.
 *
 *   docker compose exec -T -w /app frontend node scripts/check-tokens.mjs
 *   docker compose exec -T -w /app frontend node scripts/check-tokens.mjs --self-test
 *
 * SCAN A — definition-position intersection.
 *   The two :root stylesheets (app/globals.css and components/document/globals.css)
 *   are both imported globally at equal specificity, so the later import wins any
 *   name they share. Exactly that happened once: both declared --font-ui, one of
 *   them bound to a face next/font never registers. Adding any unprefixed token
 *   to either file re-opens it. Fails when the intersection is non-empty.
 *
 * SCAN B — undeclared var() sweep.
 *   A var() reference to a name nothing declares is not a soft failure. With no
 *   fallback the whole declaration is invalid at computed-value time and the
 *   property falls back to inheritance — the rule is deleted, not degraded. Two
 *   live instances were found this way (--text-primary, --space-12), one of them
 *   silently inheriting the right answer and therefore invisible.
 *
 * COMMENTS ARE STRIPPED BEFORE EITHER SCAN. Both scans previously produced false
 * results from comment prose: Scan A missed 25 declarations by anchoring on the
 * preceding semicolon (invisible when the predecessor ended in a trailing
 * comment), and Scan B reported `var(--paper-*)` written inside an explanatory
 * comment as an undeclared token.
 *
 * No dependencies, no network, no writes outside os.tmpdir() in --self-test.
 */

import { readFileSync, readdirSync, statSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { tmpdir } from "node:os";

const STYLESHEETS = ["app/globals.css", "components/document/globals.css"];
const SOURCE_DIRS = ["app", "components", "lib"];
const SOURCE_EXT = [".ts", ".tsx", ".css"];

// ---------------------------------------------------------------------------
// Comment stripping
// ---------------------------------------------------------------------------

/** Blank out block comments, preserving newlines so line numbers survive. */
function stripBlockComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
}

/**
 * Blank out `//` line comments. A `//` preceded by `:` is left alone so a URL
 * inside a string is not mistaken for a comment; that is a heuristic, and it is
 * the only one in this file.
 */
function stripLineComments(text) {
  return text
    .split("\n")
    .map((line) => {
      for (let i = 0; i < line.length - 1; i++) {
        if (line[i] === "/" && line[i + 1] === "/" && line[i - 1] !== ":") {
          return line.slice(0, i);
        }
      }
      return line;
    })
    .join("\n");
}

const stripAll = (text) => stripLineComments(stripBlockComments(text));

// ---------------------------------------------------------------------------
// Collection
// ---------------------------------------------------------------------------

/** Declarations, matched at declaration position only — never inside a var(). */
function declarationsIn(path) {
  const text = stripBlockComments(readFileSync(path, "utf8"));
  const out = new Map();
  const re = /^[ \t]*(--[A-Za-z0-9_-]+)[ \t]*:/gm;
  let m;
  while ((m = re.exec(text)) !== null) {
    const line = text.slice(0, m.index).split("\n").length;
    if (!out.has(m[1])) out.set(m[1], []);
    out.get(m[1]).push(line);
  }
  return out;
}

function walk(dir, acc = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return acc;
  }
  for (const e of entries) {
    if (e === "node_modules" || e === ".next") continue;
    const full = join(dir, e);
    if (statSync(full).isDirectory()) walk(full, acc);
    else if (SOURCE_EXT.some((x) => e.endsWith(x))) acc.push(full);
  }
  return acc;
}

/** Every var(--name) reference, with whether it carries a fallback. */
function referencesIn(root) {
  const refs = [];
  for (const dir of SOURCE_DIRS) {
    for (const file of walk(join(root, dir))) {
      const text = stripAll(readFileSync(file, "utf8"));
      text.split("\n").forEach((line, i) => {
        const re = /var\(\s*(--[A-Za-z0-9_-]+)\s*(,)?/g;
        let m;
        while ((m = re.exec(line)) !== null) {
          refs.push({
            name: m[1],
            file: relative(root, file).split(sep).join("/"),
            line: i + 1,
            hasFallback: Boolean(m[2]),
          });
        }
      });
    }
  }
  return refs;
}

/**
 * next/font injects its variables onto the root element at runtime, so they are
 * legitimately referenced without ever appearing in a stylesheet. Read from
 * layout.tsx rather than hardcoded, so adding a face cannot produce a false
 * positive here.
 */
function runtimeInjected(root) {
  const names = new Set();
  try {
    const text = stripAll(readFileSync(join(root, "app/layout.tsx"), "utf8"));
    const re = /variable:\s*["'`](--[A-Za-z0-9_-]+)["'`]/g;
    let m;
    while ((m = re.exec(text)) !== null) names.add(m[1]);
  } catch {
    /* absent in a fixture tree; an empty set is correct there */
  }
  return names;
}

// ---------------------------------------------------------------------------
// Scans
// ---------------------------------------------------------------------------

function scanA(root, log) {
  const perFile = STYLESHEETS.map((rel) => {
    try {
      return { rel, decls: declarationsIn(join(root, rel)) };
    } catch {
      return { rel, decls: new Map() };
    }
  });

  log("SCAN A — definition-position intersection");
  for (const { rel, decls } of perFile) {
    log(`  ${rel}: ${decls.size} declarations`);
  }

  const [a, b] = perFile;
  const shared = [...a.decls.keys()].filter((n) => b.decls.has(n)).sort();

  if (shared.length === 0) {
    log("  intersection: EMPTY — the two layers can coexist on :root");
    return true;
  }
  log(`  intersection: ${shared.length} COLLIDING TOKEN(S)`);
  for (const n of shared) {
    log(`    ${n}`);
    log(`      ${a.rel}:${a.decls.get(n).join(",")}`);
    log(`      ${b.rel}:${b.decls.get(n).join(",")}`);
  }
  log("  FAIL: one name, two values, equal specificity — the later import wins.");
  return false;
}

function scanB(root, log) {
  const declared = new Set();
  for (const rel of STYLESHEETS) {
    try {
      for (const n of declarationsIn(join(root, rel)).keys()) declared.add(n);
    } catch {
      /* a fixture tree may omit one */
    }
  }
  const runtime = runtimeInjected(root);
  const refs = referencesIn(root);
  const undeclared = refs.filter((r) => !declared.has(r.name) && !runtime.has(r.name));

  log("SCAN B — undeclared var() sweep");
  log(`  ${declared.size} declared, ${runtime.size} runtime-injected, ${refs.length} references`);

  if (undeclared.length === 0) {
    log("  every reference resolves to a declaration");
    return true;
  }

  const severe = undeclared.filter((r) => !r.hasFallback);
  const covered = undeclared.filter((r) => r.hasFallback);

  log(`  ${undeclared.length} UNDECLARED REFERENCE(S)`);
  if (severe.length) {
    log(`  ${severe.length} with NO FALLBACK — these delete the rule, not degrade it:`);
    for (const r of severe) log(`    ${r.file}:${r.line}  var(${r.name})`);
  }
  if (covered.length) {
    log(`  ${covered.length} fallback-covered — currently harmless, silently unwired:`);
    for (const r of covered) log(`    ${r.file}:${r.line}  var(${r.name}, ...)`);
  }
  log("  FAIL: a reference with no declaration behind it.");
  return false;
}

// ---------------------------------------------------------------------------
// Self-test — negative controls
// ---------------------------------------------------------------------------

function fixture(files) {
  const dir = mkdtempSync(join(tmpdir(), "lm-tokens-"));
  for (const [rel, body] of Object.entries(files)) {
    const full = join(dir, rel);
    mkdirSync(full.slice(0, full.lastIndexOf(sep)), { recursive: true });
    writeFileSync(full, body, "utf8");
  }
  return dir;
}

const CLEAN = {
  "app/globals.css": ":root {\n  --ink-primary: #2A241E;\n  --rhythm-major: 72px;\n}\n",
  "components/document/globals.css": ":root {\n  --paper-text: #2A2622;\n}\n",
  "app/layout.tsx": 'const f = { variable: "--font-fraunces" };\n',
  "components/Probe.tsx": 'const s = { color: "var(--ink-primary)", fontFamily: "var(--font-fraunces)" };\n',
};

function selfTest() {
  const cases = [
    {
      name: "A: a token declared in BOTH stylesheets must fail",
      scan: scanA,
      files: {
        ...CLEAN,
        // The real historical collision, reproduced.
        "components/document/globals.css": ":root {\n  --paper-text: #2A2622;\n  --rhythm-major: 48px;\n}\n",
      },
      expect: ["--rhythm-major", "COLLIDING"],
    },
    {
      name: "A: a declaration after a TRAILING COMMENT is still seen",
      scan: scanA,
      files: {
        ...CLEAN,
        // Anchoring on the preceding ';' made this invisible once.
        "app/globals.css": ":root {\n  --ink-primary: #2A241E; /* body ink */\n  --paper-text: #111;\n}\n",
      },
      expect: ["--paper-text", "COLLIDING"],
    },
    {
      name: "B: a var() with no declaration must fail",
      scan: scanB,
      files: { ...CLEAN, "components/Probe.tsx": 'const s = { color: "var(--nonexistent, red)" };\n' },
      expect: ["--nonexistent", "fallback-covered"],
    },
    {
      name: "B: no fallback is flagged as the more severe class",
      scan: scanB,
      files: { ...CLEAN, "components/Probe.tsx": 'const s = { color: "var(--nonexistent)" };\n' },
      expect: ["--nonexistent", "NO FALLBACK"],
    },
    {
      name: "B: a token named only inside a COMMENT is not a finding",
      scan: scanB,
      files: {
        ...CLEAN,
        "components/Probe.tsx":
          '// explains var(--never-declared) in prose\n/* and var(--also-never) here */\nconst s = { color: "var(--ink-primary)" };\n',
      },
      expect: null, // must PASS
    },
  ];

  let failures = 0;
  for (const c of cases) {
    const dir = fixture(c.files);
    const lines = [];
    const ok = c.scan(dir, (l) => lines.push(l));
    rmSync(dir, { recursive: true, force: true });

    const out = lines.join("\n");
    if (c.expect === null) {
      const good = ok === true;
      console.log(`${good ? "PASS" : "FAIL"}  ${c.name}`);
      if (!good) {
        failures++;
        console.log(out.replace(/^/gm, "        "));
      }
      continue;
    }
    const named = c.expect.every((token) => out.includes(token));
    const good = ok === false && named;
    console.log(`${good ? "PASS" : "FAIL"}  ${c.name}`);
    console.log(out.replace(/^/gm, "        "));
    if (!good) {
      failures++;
      if (ok !== false) console.log("        ^ CONTROL DID NOT FIRE: the scan passed when it should have failed");
      if (!named) console.log(`        ^ output did not name: ${c.expect.join(", ")}`);
    }
  }

  console.log(
    failures === 0
      ? `\nSELF-TEST PASS — ${cases.length} controls, every one observed behaving as specified`
      : `\nSELF-TEST FAIL — ${failures}/${cases.length}`
  );
  return failures === 0;
}

// ---------------------------------------------------------------------------

const argv = process.argv.slice(2);
const rootFlag = argv.indexOf("--root");
const root = rootFlag !== -1 ? argv[rootFlag + 1] : process.cwd();

if (argv.includes("--self-test")) {
  process.exit(selfTest() ? 0 : 1);
}

const log = (l) => console.log(l);
const a = scanA(root, log);
log("");
const b = scanB(root, log);
log("");
if (a && b) {
  log("check-tokens: PASS");
  process.exit(0);
}
log(`check-tokens: FAIL (${!a ? "scan A" : ""}${!a && !b ? " and " : ""}${!b ? "scan B" : ""})`);
process.exit(1);
