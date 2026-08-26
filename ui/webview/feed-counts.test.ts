// ONE counting rule for every number on the board (the user 2026-08-26, verbatim intent): the
// number ALWAYS shows next to the Working/Blocked/Completed section headers AND next to collapsed
// thread headers; just the number, never the word 'cards'; zero shows nothing. The traced bug: the
// section chip counted a turn-group as ONE row while other reads spoke in cards, so expanded and
// collapsed states could disagree (the user's 34-vs-'27 cards' screenshot). Both counters now read
// entryCards — a turn-group is worth its members, a folded header the cards it hides — so the
// section number is INVARIANT under grouping, group expansion, and thread folds by construction.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const FEED = ui("webview", "feed.ts");

test("one counting rule: sections and folds read the SAME cards-not-rows helper", () => {
  assert.match(FEED, /function entryCards\(e: Entry\): number \{\s*\n\s*return e\.kind === "sess" \? e\.folded : e\.kind === "group" \? e\.group\.members\.length : 1;/,
    "a turn-group is worth its members; a folded header the cards it stands in for");
  assert.match(FEED, /const nCards = \(es: Entry\[\]\) => es\.reduce\(\(n, e\) => n \+ entryCards\(e\), 0\);/,
    "the section chip counts through the rule");
  assert.match(FEED, /if \(head\) head\.folded \+= entryCards\(e\); continue;/,
    "the fold accumulator counts through the SAME rule — expansion invariance by construction");
});

test("expansion invariance, executed: folding moves cards onto the header without changing the total", () => {
  // the rule as pure math, run on a synthetic bucket: 1 ask + a 3-member turn-group = 4 cards
  const entryCards = (e: { kind: string; folded?: number; members?: number }) =>
    e.kind === "sess" ? e.folded! : e.kind === "group" ? e.members! : 1;
  const run = [{ kind: "ask" }, { kind: "group", members: 3 }];
  const expanded = [{ kind: "sess", folded: 0 }, ...run];
  const total = (es: { kind: string; folded?: number; members?: number }[]) =>
    es.reduce((n, e) => n + entryCards(e), 0);
  const folded = [{ kind: "sess", folded: run.reduce((n, e) => n + entryCards(e), 0) }];
  assert.equal(total(expanded), 4);
  assert.equal(total(folded), 4, "the same number, expanded or collapsed — the user's 34 stays 34");
});

test("just the number — the word 'cards' never renders; words live on hover", () => {
  assert.match(FEED, /foldn\.textContent = String\(e\.folded\);/);
  assert.ok(!/foldn\.textContent = [^;]*card/.test(FEED), "no wording variant survives");
  assert.match(FEED, /foldn\.title = e\.folded === 1 \? "1 card folded under this session"/,
    "the tooltip keeps the words the visible chip dropped");
});

test("zero shows nothing at all — on the section chips and the fold stand-in alike", () => {
  assert.match(FEED, /const setCount = \(elc: HTMLElement, n: number\) => \{ elc\.textContent = n \? String\(n\) : ""; elc\.style\.display = n \? "" : "none"; \};/);
  assert.match(FEED, /foldn\.style\.display = shut && e\.folded \? "" : "none";/);
});
