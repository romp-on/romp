// Re-review churn on multi-goal cards (the user 2026-08-19): a re-completed card presents only the NEW
// work — sub-goals reviewed before the follow-up collapse behind one "N reviewed earlier" row, and a
// takeaway the user has replied past says so instead of posing as current. The boundary is the SAME
// jd.review_boundary the distiller scopes with, so the fold and the summary can never disagree.
// The stale-note rule is EXECUTED (distiller-line's design); the wiring is source-pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { distillStaleNote } from "../../ui/webview/distiller-line";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const FEED = W("feed.ts");
const FEEDCSS = W("feed.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");
const JUDGE = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-judge"), "utf8");

test("the stale-takeaway note: EXECUTED rule — completed + stale + shown text, nothing otherwise", () => {
  assert.equal(distillStaleNote(true, true, "the takeaway"),
    "You replied after this summary was written. It refreshes when the new work lands.");
  assert.doesNotMatch(distillStaleNote(true, true, "the takeaway"), /\u2014|followed up/, "two sentences, no dash; a quoted reply is a reply too");
  assert.equal(distillStaleNote(false, true, "the takeaway"), "", "not stale → silent");
  assert.equal(distillStaleNote(true, false, "the takeaway"), "", "not completed → the brief owns blocked");
  assert.equal(distillStaleNote(true, true, "  "), "", "no shown takeaway → nothing to annotate");
});

test("kernel: summaryStale = followupAt postdates the read summary; self-clears on re-distill", () => {
  assert.match(KERNEL, /"summaryStale": bool\(\(nodes\[nid\]\.get\("followupAt"\) or 0\) > \(nodes\[nid\]\.get\("distilledMt"\) or 0\)/);
  assert.match(FEED, /summaryStale\?: boolean;/);
  assert.match(FEED, /distillStaleNote\(!!it\.summaryStale, dCompleted, distillShown\)/);
  assert.match(FEEDCSS, /\.fsum-stale \{ font-style: italic;/);
});

test("kernel: reviewedEarlier keys on the SHARED review boundary, never a second derivation", () => {
  assert.match(KERNEL, /"tree": flatten\(nid, \[\], boundary=jd\.review_boundary\(nodes\[nid\]\)\)\}/);
  assert.match(KERNEL, /"reviewedEarlier": bool\(boundary and out and done\s*\n\s*and jd\._done_since\(nd\) <= boundary\) or None,/);
  // the shared helper: settle boundary advanced to the summary watermark on a reopen past it
  assert.match(JUDGE, /def review_boundary\(nd\):/);
  assert.match(JUDGE, /and \(b is None or dm > b\):/);
});

test("feed: reviewed-earlier sub-goals collapse behind ONE toggle row, fresh rows first", () => {
  assert.match(FEED, /reviewedEarlier\?: boolean;/);
  // the fold's label counts what its walk RENDERS: a handoff child (walk skips it) is out of both lists, so the count never
  // exceeds the rows the open fold shows (2026-09-18: "3 reviewed earlier" opened to two rows)
  assert.match(FEED, /const shown = \(c: string\) => \{ const n = byId\.get\(c\); return !!n && n\.kind !== "handoff"; \};/);
  assert.match(FEED, /const revKids = \(root\.children \|\| \[\]\)\.filter\(\(c\) => shown\(c\) && !!byId\.get\(c\)\?\.reviewedEarlier\);/);
  assert.match(FEED, /const freshKids = \(root\.children \|\| \[\]\)\.filter\(\(c\) => shown\(c\) && !byId\.get\(c\)\?\.reviewedEarlier\);/);
  assert.match(FEED, /const revOpen = cardTreeExpanded\.has\(id \+ ":reviewed"\);/);
  assert.match(FEED, /rows\.slice\(0, freshEnd\)\.forEach\(paintRow\);/);
  assert.match(FEED, /txt\.textContent = revKids\.length \+ " reviewed earlier";/);
  // the fold expands in place (state survives re-renders) and never dead-ends: progressive disclosure; its kids walk at
  // depth 1, one level under the fold row that is their visual parent, where the fresh kids walk at depth 0 (2026-09-18:
  // walked at 0, they rendered flush with the fresh rows), and paintRow indents a row by its depth
  assert.match(FEED, /for \(const c of freshKids\) walk\(c, 0\);/);
  assert.match(FEED, /if \(revOpen\) for \(const c of revKids\) walk\(c, 1\);/);
  assert.match(FEED, /if \(depth\) row\.style\.paddingLeft = \(depth \* TREE_INDENT_EM\) \+ "em";/);
  assert.match(FEEDCSS, /\.fcheck\.freviewed \{ opacity: 0\.6; cursor: pointer; \}/);
  // the fold row's expanded state is its own class, never the not-done STATUS class "open": .fcheck.open .fcheck-mark
  // draws the hollow ring, and the open fold wore it with its ✓ dropped low inside (the user's 2026-09-18 screenshot)
  assert.match(FEED, /el\("div", "fcheck freviewed" \+ \(revOpen \? " expanded" : ""\)\)/);
  assert.doesNotMatch(FEED, /"fcheck freviewed" \+ \(revOpen \? " open"/);
  assert.match(FEEDCSS, /\.fcheck\.open \.fcheck-mark \{/, "the status rule the fold row must not match");
});
