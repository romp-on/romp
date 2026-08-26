// The PROVISIONAL placeholder (the user 2026-06-18): a session actively working an in-progress turn the
// planner hasn't classified yet shows NO card otherwise (every existing card is done/blocked/cleared). The
// kernel (_provisional_card) emits a dim, non-interactive WORKING card from the live prompt; this pins the
// render treatment — it must read as a placeholder (dim/italic), carry no curation affordance (no Clear /
// Nudge), and route every click to OPENING the session (it has no goal node / timeline anchor). Source-level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("AskItem carries the provisional flag (the placeholder marker)", () => {
  assert.match(FEED, /provisional\?: boolean;\s+\/\/ a LIVE-PROMPT placeholder/);
});

test("updateAskCard marks a provisional card dim + italic with a provisional class", () => {
  assert.match(FEED, /\(it\.provisional \? " provisional" : ""\)/);
  assert.match(FEED, /card\.style\.opacity = it\.provisional \? "\.62" : "";/);
  assert.match(FEED, /a\._title\.style\.fontStyle = it\.provisional \? "italic" : "";/);
});

test("a provisional 'ghost' card gets a DASHED outline (distinct from a real card's solid session-colour border)", () => {
  // the user 2026-06-19: a dashed border marks the placeholder as not-yet-real; reset to solid otherwise
  assert.match(FEED, /if \(it\.provisional\) \{[\s\S]*?card\.style\.borderStyle = "dashed";/);
  assert.match(FEED, /card\.style\.borderWidth = "1\.5px";/);
  // a real card resets to a solid border coloured by its own session (CSS-driven channels; recency fallback)
  assert.match(FEED, /\} else \{[\s\S]*?card\.style\.borderStyle = "";[\s\S]*?setCardChannels\(card, \(it\.color && hexToRgb/);
});

test("a placeholder has no curation affordances — Clear is hidden", () => {
  // (the manual Nudge button was removed 2026-06-30 — Auto Nudge replaces it, so there's no per-card nudge)
  assert.match(FEED, /a\._clr\.style\.display = it\.provisional \? "none" : "";/);
  assert.match(FEED, /a\._clr = clr;/);   // the ref must be stored for updateAskCard to reach it
});

test("every interaction on a placeholder routes to opening the session (no modal / timeline / pin)", () => {
  // body click → open the session, not the modal
  assert.match(FEED, /if \(it\.provisional\) \{ openOrReviveSession\(it\.sid, it\.live, it\.name\); return; \}\s+\/\/ placeholder → open the session/);
  // title click → open the session, not a timeline deep-link
  assert.match(FEED, /if \(it\.provisional\) \{ openOrReviveSession\(it\.sid, it\.live, it\.name\); return; \} focusEcho\(it\.sid\); vscodeApi\?\.postMessage\(\{ type: "showOnTimeline"/);
  // dblclick can't pin a node-less placeholder
  assert.match(FEED, /if \(it\.provisional\) return;\s+\/\/ a placeholder can't be pinned/);
  // hover path: a placeholder has no timeline journey to preview
  assert.match(FEED, /if \(it\.provisional\) return;\s+\/\/ no timeline path for a placeholder/);
});
