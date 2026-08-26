// Sub-goals is a PER-CARD "Sub-goals" button (the user 2026-07-08, moved off the footer), whose default
// follows the Collapsed mode; the MODAL is never gated. The old "Explanations" toggle is GONE — cards show
// the distiller's summary as their one auto-line, not the planner's why. The feed reads the shared
// 'romp:settings' directly and re-renders when the gear / footer (same document) flips it. Source-level pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("feed prefs from romp:settings: newestFirst/collapsed default OFF, grouped default ON; the subgoals pref is gone", () => {
  assert.match(FEED, /function feedPrefs\(\)/);
  assert.match(FEED, /localStorage\.getItem\("romp:settings"\)/);
  // newestFirst + collapsed default OFF (=== true); grouped defaults ON (!== false — the user 2026-07-13:
  // by-session grouping is the feed's normal reading mode, the footer Group toggle opts OUT).
  // `subgoals` is no longer a feed-wide pref (per-card button now).
  assert.match(FEED, /return \{ newestFirst: s\.newestFirst === true, collapsed: s\.collapsed === true, grouped: s\.grouped !== false,\s*\n\s*stacked: s\.stacked === true \};/);
  assert.match(FEED, /catch \{ return \{ newestFirst: false, collapsed: false, grouped: true, stacked: false \}; \}/);
  assert.doesNotMatch(FEED, /s\.subgoals/, "no feed-wide subgoals pref — it's a per-card toggle now");
  assert.doesNotMatch(FEED, /explanations/);   // every trace of the old pref is gone from the feed
});

test("footer layout: view controls left, Clear all + Undo dock right (the user 2026-07-13)", () => {
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
  // flex `order` + margin-left:auto so the split holds whatever order the ensure* calls appended in
  assert.match(CSS, /#feed-clearall \{ order: 10; margin-left: auto; \}/);
  assert.match(CSS, /#feed-undoclear \{ order: 11; \}/);
});

test("the card shows the DISTILLER's line (summary/blockSummary) but NO why/generating placeholder (restored 2026-06-29)", () => {
  // the card's distiller line is wired through ./distiller-line — the BEHAVIOR is executed in
  // distiller-line.test.ts (completed→summary, blocked→blockSummary, hidden when empty). These pins just
  // confirm the card creates the element and routes through that single rule.
  assert.match(FEED, /const distill = el\("div", "fask-distill"\)/);
  assert.match(FEED, /import \{ distillText, distillInputs, applyDistillLine, distillPending, distillStaleNote \} from "\.\/distiller-line"/);
  // keyed on the GENUINE state (dCompleted/dBlocked from distillInputs), not the transient column, so a
  // still-blocked card keeps its brief through the recheck/rejudging Working flip (the user 2026-07-21)
  assert.match(FEED, /applyDistillLine\(a\._distill as HTMLElement, dCompleted, dBlocked,\s*\n?\s*it\.summary, it\.blockSummary\)/);
  // but the planner's why-rationale AND the stuck "(generating…)" placeholder stay GONE (the user 2026-06-27/29)
  assert.doesNotMatch(FEED, /const setAutoLine =/);
  assert.doesNotMatch(FEED, /"\(generating…\)"/, "no '(generating…)' placeholder anywhere");
  assert.doesNotMatch(FEED, /fask-blockwhy|fask-donewhy/, "the old why-tooltip auto-line stays removed");
  assert.doesNotMatch(FEED, /showWhy/);
});

test("Sub-goals is a PER-CARD button — the THIRD mutually-exclusive section; no footer checkbox", () => {
  // the per-card button (right of Summary) is wired through the SAME secChoice/pick as bg/summary (one at a time)
  assert.match(FEED, /const subBtn = el\("button", "fask-secbtn"\); subBtn\.textContent = "Sub-goals";/);
  assert.match(FEED, /subBtn\.classList\.toggle\("on", choice === "subgoals"\);/);
  assert.match(FEED, /subBtn\.onclick = pick\("subgoals"\);/);
  // the button hides when there are no sub-goals; the tree renders only when this section is selected
  assert.match(FEED, /subBtn\.style\.display = hasSubs \? "" : "none";/);
  assert.match(FEED, /if \(choice !== "subgoals" \|\| !root\) \{ cl\.style\.display = "none"; return; \}/);
  // no separate sub-goals state, no footer checkbox, no feed-wide pref
  assert.doesNotMatch(FEED, /subChoice|resolveSub/, "no separate sub-goals toggle map — folded into secChoice");
  assert.doesNotMatch(FEED, /makeSubgoalsToggle|ensureSubgoalsToggle/, "no footer sub-goals toggle");
  assert.doesNotMatch(FEED, /feedPrefs\(\)\.subgoals/, "the tree no longer gates on a feed-wide subgoals pref");
});

test("the feed re-renders when the prefs change (storage cross-pane + same-doc romp:settings event)", () => {
  assert.match(FEED, /window\.addEventListener\("storage", \(e\) => \{ if \(e\.key === "romp:settings"\) onSettingsChanged\(\); \}\)/);
  assert.match(FEED, /window\.addEventListener\("romp:settings", \(\) => onSettingsChanged\(\)\)/);
});
