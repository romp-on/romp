// The feed search filter (the user 2026-08-23): type-to-filter the board by session name, HOST PREFIX
// INCLUDED — a machine name keeps every session on it, and host:name narrows to one. Pure rule
// executed here; the feed wiring and the expandable footer control pinned by source.
import { test } from "node:test";
import assert from "node:assert";
import * as fs from "node:fs";
import * as path from "node:path";
import { searchMatches, searchSids } from "./feed-search";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("substring match over the full display name, case-insensitive, host prefix included", () => {
  assert.ok(searchMatches("TESTHOST", "TESTHOST:notes-api"), "a machine name finds its sessions");
  assert.ok(searchMatches("notes", "TESTHOST:notes-api"));
  assert.ok(searchMatches("TESTHOST:NOTES", "TESTHOST:notes-api"), "case-insensitive");
  assert.ok(searchMatches("t:n", "TESTHOST:notes-api"), "plain substring — spans the host separator");
  assert.ok(!searchMatches("otherbox", "TESTHOST:notes-api"));
  assert.ok(searchMatches("  api ", "api"), "the query is trimmed");
});

test("a blank query is the filter OFF, never matched-none", () => {
  assert.ok(searchMatches("", "anything"));
  assert.ok(searchMatches("   ", "anything"));
  assert.ok(searchMatches(null, "anything"));
  assert.equal(searchSids("", [{ sid: "a", name: "web" }]), null, "null = no filter, distinct from an empty set");
});

test("searchSids collects the matching sids; a query matching nothing yields an EMPTY set", () => {
  const metas = [{ sid: "a", name: "TESTHOST:web" }, { sid: "b", name: "otherbox:web" }, { sid: "c", name: "otherbox:api" }];
  assert.deepEqual([...searchSids("web", metas)!].sort(), ["a", "b"]);
  assert.deepEqual([...searchSids("otherbox", metas)!].sort(), ["b", "c"]);
  assert.equal(searchSids("zzz", metas)!.size, 0, "an empty board is the honest answer, not filter-off");
});

test("the feed composes search WITH the session filter, and cards fall back to their own label", () => {
  assert.match(FEED, /const sMatch = searchSids\(feedSearchQ, sessionsMeta\);/);
  assert.match(FEED, /sMatch\.has\(a\.sid\) \|\| searchMatches\(feedSearchQ, \(a as \{ name\?: string \}\)\.name\)/,
               "a just-died session's cards keep matching by their per-card label");
});

test("the footer control is ensure-once, expandable, and an active filter can never be hidden", () => {
  // merged into the session COMBOBOX (the user 2026-08-24): one control, both filters, same rules
  assert.match(FEED, /function ensureSessionBox\(\): HTMLElement/);
  assert.match(FEED, /const active = !!feedSearchQ\.trim\(\) \|\| !!feedOnlySid;/,
               "EITHER live filter counts as active");
  assert.match(FEED, /if \(active\) wrap\.classList\.add\("open"\);/,
               "an active filter forces the bar open — the compact state never hides a live filter");
  // folding with anything live CLEARS it (both kinds), never hides it
  assert.match(FEED, /if \(feedSearchQ\.trim\(\)\) \{ setFeedSearch\(""\); changed = true; \}/);
  assert.match(FEED, /if \(feedOnlySid\) \{ setFeedOnly\(null\); changed = true; \}/);
  assert.match(FEED, /sessionStorage\.getItem\("romp:feedSearch"\)/,
               "same storage lifetime as the session filter: reload-proof, never a fresh window");
  // typing narrows the open list AND applies the live board filter in the same keystroke
  assert.match(FEED, /inp\.oninput = \(\) => \{ setFeedSearch\(inp\.value\); filterSessList\(inp\.value\); render\(\); \};/);
  // an away-click closes the LIST only — clicking a card must never wipe a live filter; the
  // clear-on-fold rule belongs to EXPLICIT folds (Escape, the button)
  assert.match(FEED, /if \(!feedSearchQ\.trim\(\) && !feedOnlySid\) document\.getElementById\("feed-search"\)\?\.classList\.remove\("open"\);/);
  const away = FEED.slice(FEED.indexOf("function sessListAway"), FEED.indexOf("function sessListKey"));
  assert.ok(!away.includes("foldSessBox"), "away never routes through the clearing fold");
  // an idle empty bar folds quietly on blur once the list is gone (the parent search box's rule)
  assert.match(FEED, /if \(!sessListEl && !inp\.value\.trim\(\) && !feedOnlySid\) wrap!\.classList\.remove\("open"\);/);
});

test("the expanded bar wears the top search bar's look, footer-scaled, and FLEXES instead of a fixed width", () => {
  // the user 2026-08-24: the open wrap takes the row's spare space with a floor and a cap — a narrow
  // pane wraps it onto its own row (the footer wraps) instead of overflowing or squeezing to nothing
  assert.match(CSS, /#feed-search\.open \{ flex: 1 1 150px; min-width: 110px; max-width: 340px; \}/);
  assert.match(CSS, /#feed-search input \{ flex: 1 1 auto; min-width: 0;/);
  assert.doesNotMatch(CSS, /#feed-search\.open input \{ width: \d+px/, "no fixed-width expansion survives");
  // the top bar's vocabulary, scaled to the footer's 10.5px rhythm — never the outline bar's 12.5px verbatim
  assert.match(CSS, /#feed-search input \{[^}]*font-size: 10\.5px/);
  assert.match(CSS, /#feed-search input \{[^}]*background: var\(--vscode-input-background, #3c3c3c\)/);
  assert.match(CSS, /#feed-search\.open input:focus \{ border-color: var\(--accent\); \}/);
  // the inner ✕ clear: hidden while empty, clearing REFOCUSES (the top bar's semantics)
  assert.match(FEED, /clr\.id = "feed-search-clear";/);
  assert.match(FEED, /inp\.value = ""; setFeedSearch\(""\); filterSessList\(""\); inp\.focus\(\); render\(\);/);
  assert.match(FEED, /clrBtn\.hidden = !inp\.value\.trim\(\);/,
    "trimmed, like the fold and the filter — a whitespace-only value must not strand a floating ✕");
  assert.match(CSS, /#feed-search-clear\[hidden\] \{ display: none; \}/);
  assert.match(CSS, /#feed-search:not\(\.open\) #feed-search-clear \{ display: none; \}/,
    "the ✕ exists only on an open box — a folded control can never wear a stray ×");
});
