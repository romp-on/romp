// Tracked delegation on the feed (the user 2026-08-24): a report-back handoff reads as ONE card
// homed under the DELEGATOR. The kernel ships two additive keys — delegTracked (the primary card's
// recipient identities) and satellite (the recipient-side copy) — and the feed renders them with
// machinery it already has: the primary names its recipients on the origin slot with the board's
// own live dot; the satellite drops off the DEFAULT board only, with the session filter as the
// one-click path back (nothing runs in secret, the 2026-08-11 rule). Source pins, the feed-panel
// idiom; the kernel-side truth table lives in tests/test_tracked_delegation.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("the default board hides satellites; the session filter is the one-click path back", () => {
  // inside viewFiltered, so the hover-freeze churn badges count exactly what the board shows
  assert.match(SRC, /list\.filter\(\(a\) => a\.sid === feedOnlySid\) : list\.filter\(\(a\) => !a\.satellite\)/,
    "hidden ONLY on the unfiltered board — picking the worker's session still shows its copy");
});

test("the primary names its recipients with the board's own live dot, STACKING after any ↪ from", () => {
  const BLK = SRC.slice(SRC.indexOf("if (it.delegTracked && it.delegTracked.length) {"),
                        SRC.indexOf("a._time.textContent"));
  // an else-if hid a MIDDLEMAN's tracked handoff behind its own ↪ from badge (review 2026-08-24):
  // origin and delegTracked are different facts about one card, so they stack on the slot
  assert.match(BLK, /const hadOrigin = !!\(it\.origin && it\.origin\.peer\);/);
  assert.match(BLK, /pre\.textContent = \(hadOrigin \? " · " : ""\) \+ "↪ delegated to ";/);
  assert.match(BLK, /peer\.replaceChildren\(\.\.\.hostPartsNodes\(d\.host, d\.name\)\);/,
    "identity rendering matches every other session name (quiet host: prefix included)");
  assert.match(BLK, /setWorkDot\(peer, dotFor\(d\.name\)\);/,
    "the recipient's LIVE state rides the card — the dot language the board already speaks");
  assert.match(BLK, /openSession", id: d\.sid/, "each recipient span opens ITS session — ↪ from keeps its own click");
});

test("both keys are additive on the type — an untracked payload renders exactly as before", () => {
  assert.match(SRC, /satellite\?: boolean \| null;/);
  assert.match(SRC, /delegTracked\?: \{ name: string; host\?: string; sid: string; color\?: \{ bg: string; fg: string \} \| null \}\[\] \| null;/);
});
