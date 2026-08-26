// The card's distiller sections (the user 2026-07-02; MUTUALLY EXCLUSIVE 2026-07-07): a returning reader
// often forgot what the thread was about, so the distiller writes a BACKGROUND section (re-orientation)
// alongside the takeaway (SUMMARY). The Background/Summary TOGGLES ride the time row (row3) and are
// mutually exclusive — clicking one shows its body, clicking the showing one hides it; ONE body shows at a
// time, or NEITHER. Default = summary open. State is a single per-card map (secChoice) so re-renders never
// snap a section shut. The MODAL always shows both, labeled. No jsdom — source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the section BODIES live in .fask-secs; the toggles moved to the time row (the user 2026-07-07)", () => {
  assert.match(FEED, /const secs = el\("div", "fask-secs"\); secs\.style\.display = "none";/);
  assert.match(FEED, /bgBtn\.textContent = "Background"/, "capitalized like Clear");
  assert.match(FEED, /takeBtn\.textContent = "Summary"/);
  assert.match(FEED, /secs\.append\(bgBody, distill, stallBody, artline\)/, "secs holds the BODIES only (incl. the stall body + the artifacts line) — the toggles ride row3 now");
  assert.match(FEED, /a\._secs = secs; a\._bgBtn = bgBtn; a\._bgBody = bgBody; a\._takeBtn = takeBtn;/);
  assert.match(FEED, /background\?: string \| null;/, "the AskItem carries the kernel's background field");
  // just a body container now (a plain block; one full-width body shows at a time)
  assert.match(CSS, /\.fask-secs \{ margin-top: 4px; \}/);
});

test("the buttons wear the Clear chrome with a neutral hover", () => {
  assert.match(CSS, /\.fask-secbtn \{[^}]*border: 1px solid var\(--card-border\)/);
  assert.match(CSS, /\.fask-secbtn \{[^}]*border-radius: 6px/);
  assert.match(CSS, /\.fask-secbtn:hover \{ color: var\(--fg\); border-color: var\(--fg\); \}/,
               "neutral hover — folding is not destructive, no Clear red");
});

test("the buttons ARE the toggles: pressed state reads at a glance, mutually exclusive", () => {
  // clicking Background/Summary shows/hides its body; the pressed button (.on) marks what's showing.
  assert.match(FEED, /a\._bgBtn\.classList\.toggle\("on", choice === "bg"\);/);
  assert.match(FEED, /a\._takeBtn\.classList\.toggle\("on", choice === "summary"\);/);
  assert.match(FEED, /a\._bgBtn\.setAttribute\("aria-pressed", choice === "bg" \? "true" : "false"\);/);
  // selected = the rail toggles' accent language: blue text, accent border, faint accent wash, bolder
  assert.match(CSS, /\.fask-secbtn\.on \{ color: var\(--accent\); border-color: var\(--accent\);\n  background: var\(--accent-wash\); font-weight: 600; \}/);
  assert.match(CSS, /--accent: #9cd2ff;/, "feed.css defines --accent in its own :root");
  assert.doesNotMatch(FEED, /fask-less/, "the less control is gone");
});

test("hovering the SELECTED (.on) toggle gives it a reverse highlight — the accent fills in (the user 2026-07-15)", () => {
  assert.match(CSS, /\.fask-secbtn\.on:hover \{ background: var\(--accent\); color: var\(--accent-fg\); border-color: var\(--accent\); \}/);
  assert.match(CSS, /--accent-fg: #0c1a2e;/, "feed.css defines --accent-fg for text on the accent fill");
  // the fill animates via a background transition on the base button
  assert.match(CSS, /\.fask-secbtn \{[^}]*transition:[^}]*background 0\.12s ease/);
});

test("state: a single mutually-exclusive secChoice (bg | summary | subgoals | tasks | none); default follows Collapsed", () => {
  // Sub-goals joined Background/Summary as the THIRD mutually-exclusive section (the user 2026-07-08);
  // the "Awaiting task" list is the FOURTH (the user 2026-07-13)
  assert.match(FEED, /const secChoice = new Map<string, "bg" \| "summary" \| "subgoals" \| "tasks" \| "stall" \| "none">\(\);/);
  // absent from the map → the DEFAULT, set by the footer "Collapsed" toggle (off → summary, on → none)
  assert.match(FEED, /return secChoice\.get\(id\) \?\? \(feedPrefs\(\)\.collapsed \? "none" : hasAwaitTasks \? "tasks" : "summary"\);/);
  // click the showing section → off; click another → switch (one at a time)
  assert.match(FEED, /secChoice\.set\(id, choice === want \? "none" : want\)/);
  assert.match(FEED, /a\._bgBtn\.onclick = pick\("bg"\);/);
  assert.match(FEED, /a\._takeBtn\.onclick = pick\("summary"\);/);
  assert.match(FEED, /subBtn\.onclick = pick\("subgoals"\);/);
  assert.doesNotMatch(FEED, /const bgOpen = new Set/, "the old two-set model is gone");
  assert.doesNotMatch(FEED, /subChoice/, "the separate sub-goals toggle map is gone — folded into secChoice");
});

test("only ONE body shows at a time (or neither); no between-sections gap", () => {
  assert.match(FEED, /a\._bgBody\.style\.display = choice === "bg" \? "" : "none";/);
  assert.match(FEED, /\(a\._distill as HTMLElement\)\.style\.display = choice === "summary" \? "" : "none";/);
  // the body container hides unless a body that LIVES in it is open (the sub-goal tree is a separate el);
  // "stall" joined 2026-07-23 — stallBody rides _secs too, and leaving it out was the dead-toggle bug
  assert.match(FEED, /a\._secs\.style\.display = \(choice === "bg" \|\| choice === "summary" \|\| choice === "stall"\) \? "" : "none";/);
  assert.doesNotMatch(FEED, /fask-gapend/, "the between-sections gap is gone — only one body ever shows");
  assert.doesNotMatch(CSS, /fask-gapend/);
});

test("the MODAL always shows BOTH sections, labeled background / summary", () => {
  assert.match(FEED, /const modalBg = node\.id === it\.itemId && nodeDistill && it\.background \? it\.background : null;/);
  assert.match(FEED, /bl\.textContent = "background"/);
  assert.match(FEED, /sl\.textContent = "summary"/);
  assert.match(CSS, /\.ftree-seclabel \{[^}]*var\(--dim\)/);
  // label size sits BETWEEN the section text (0.86em) and the tree/checklist lines (1em)
  assert.match(CSS, /\.ftree-seclabel \{ font-size: 0\.92em;/);
  // modal times match the checklist lines they correspond to, never bold (the user 2026-07-02)
  assert.match(CSS, /\.ftree-meta \{[^}]*font-size: 0\.9em; font-weight: 400;/);
});

test("background shows only alongside a produced takeaway, and the takeaway keeps its deep-link", () => {
  assert.match(FEED, /const bg = distillShown && it\.background \? it\.background : null;/);
  assert.match(FEED, /applySections\(a, it, distillShown\);/);   // the recursive re-apply inside pick()
  assert.match(FEED, /dl\.classList\.add\("fask-distill-link"\)/);
  // the background body stays typographically identical to the summary
  assert.match(CSS, /\.fask-bg-body, \.fask-stall-body \{[^}]*font-size: 0\.86em/);
  assert.match(CSS, /\.fask-bg-body, \.fask-stall-body \{[^}]*opacity: 0\.82/);
  // feed.css must define --box-border itself (border shorthand with an undefined var() is VOID)
  assert.match(CSS, /--box-border: rgba\(255, 255, 255, 0\.12\)/);
});
