// Feed modal layout (the user 2026-06-16). The modal "card" is rearranged so the goal CHECKLIST is at
// the top and the chrome wraps it:
//   - TOP bar: the session name at the left, the ✕ at the right (no separate title for a single ask —
//     its top-level goal IS the tree root, a notch larger than its sub-items);
//   - the tree/checklist sits directly below, with per-node "(Xm ago)" times pulled in close to the
//     content (fit-content) and right-aligned, in parentheses;
//   - BOTTOM bar: the (recency-tinted) age + Follow up + Continue + Clear in one row, the composer dropping in below.
// No jsdom harness for the feed, so — like the other feed-*.test.ts — pin it at the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("single-ask modal: the top-level goal is the tree root, not a separate header title", () => {
  assert.match(FEED, /ttlEl\.style\.display = nt \? "" : "none";/);            // no header title for a single GOAL ask; a NOTICE card's modal shows its title (round four of PR 1831)
  assert.match(FEED, /renderTreeBody\(body, it, false\)/);          // root goal IS the first list line
  assert.doesNotMatch(FEED, /renderTreeBody\(body, it, true\)/, "the single-ask body no longer skips the root");
});

test("TOP bar = session name (left) + ✕ (right); age is no longer in the header", () => {
  assert.match(FEED, /head\.append\(ttl, agent, close\)/);
  assert.doesNotMatch(FEED, /head\.append\(ttl, agent, age/);
  assert.match(CSS, /\.feed-modal-close \{[^}]*margin-left: auto/);   // ✕ pinned far-right
});

test("BOTTOM bar = age + Follow up + Check status + Continue + Clear in one row, the checklist sitting above it", () => {
  assert.match(FEED, /footRow\.append\(age, fup, cs, cont, clr\)/);   // Nudge moved off the footer onto the card (the user 2026-06-18); Move to Working removed (the user 2026-07-25); cs the user 2026-07-20
  // the per-sub follow-up target label sits between the button row and the composer box
  assert.match(FEED, /foot\.append\(nudges, footRow, futgt, fubox\)/);
  assert.match(FEED, /inner\.append\(head, body, foot\)/);            // head, then tree, then footer
  assert.match(CSS, /\.feed-modal-foot-row \{[^}]*display: flex/);
  assert.match(CSS, /\.feed-modal-foot-row \.feed-modal-age \{[^}]*margin-right: auto/);   // age left, buttons right
});

test("the root goal reads larger; node times are parenthesized and pulled in (fit-content)", () => {
  assert.match(FEED, /\(depth === 0 \? " ftree-root" : ""\)/);
  assert.match(FEED, /"\(" \+ relAge\(hostNow - node\.last\) \+ "\)"/);
  assert.match(CSS, /\.ftree-node\.ftree-root \.ftree-text \{[^}]*font-size/);
  assert.match(CSS, /\.ftree \{[^}]*width: fit-content/);
});

test("on a phone the tree row wraps so the reply text keeps a full line, not a 1-char column (the user 2026-07-22)", () => {
  // .ftree-node is a no-wrap flex row: text (flex:1 1 auto; min-width:0) + the nowrap Done/Drop/Check-status/
  // Follow-up group. On a narrow modal the button group hogged its full width and the shrinkable text
  // collapsed to a 1ch-wide vertical stack. Coarse-pointer only: wrap the row and drop the acts onto their
  // own line under the full-width text; desktop's single-line layout is untouched.
  assert.match(CSS, /@media \(pointer: coarse\) \{\s*\n\s*\.ftree-node \{ flex-wrap: wrap; \}/);
  assert.match(CSS, /\.ftree-node-acts \{ flex-basis: 100%; margin-left: 0; margin-top: 3px; justify-content: flex-end; \}/);
  // the base row is still single-line (no flex-wrap) so desktop is unchanged
  assert.match(CSS, /\.ftree-node\{ display: flex; align-items: baseline; gap: 6px;/);
});

test("the age is recency-tinted in both modal variants (ask / group)", () => {
  assert.match(FEED, /ageEl\.style\.color = "rgb\(" \+ it\.trgb\.join\(","\) \+ "\)"/);
  assert.match(FEED, /ageEl\.style\.color = "rgb\(" \+ grp\.trgb\.join\(","\) \+ "\)"/);
});

test("modal marks: not-yet-done is a hollow RING the size of the ✓ disc; derived done is the OUTLINED ✓", () => {
  // an empty checkbox sized like the filled one (the user 2026-06-16), not a tiny ○ glyph
  assert.match(CSS, /\.st-open \.ftree-mark \{[^}]*width: 13px/);
  assert.match(CSS, /\.st-open \.ftree-mark \{[^}]*border-radius: 50%/);
  assert.match(CSS, /\.st-open \.ftree-mark \{[^}]*border: 1\.5px solid/);
  // derived done (kernel roll-up / roll-down) = the SAME one outlined ✓ as the ledger (blue ring + blue ✓
  // on transparent), NOT an opacity dim (the user 2026-06-17). wired via a .derived class.
  assert.match(FEED, /\(node\.derived \? " derived" : ""\)/);
  assert.match(CSS, /\.ftree-node\.st-done\.derived \.ftree-mark \{[^}]*background: transparent;[^}]*border: 1\.5px solid var\(--check-bg\)/);
  assert.match(CSS, /\.ftree-node\.st-done\.derived \.ftree-mark::before \{[^}]*color: var\(--check-bg\)/);
  assert.doesNotMatch(CSS, /\.ftree-node\.st-done\.derived \.ftree-mark \{[^}]*opacity: 0\.5/);
});

test("sending a follow-up (Send or ⏎) auto-closes the modal back to the feed (the user 2026-06-19)", () => {
  // the shared submit() — used by both the Send button and the Enter key, for single-ask AND group modals —
  // posts the follow-up, clears the composer, then closes the modal (fullscreenAskId = null; renderModal()).
  assert.match(FEED, /const submit = \(\) => \{ const txt = fuinEl\.value\.trim\(\); if \(!txt\) return; send\(txt\);[\s\S]*?fullscreenAskId = null; renderModal\(\); \};/);
  // both triggers route through submit
  assert.match(FEED, /fusendEl\.onclick = submit;/);
  assert.match(FEED, /if \(ev\.key === "Enter" && !ev\.shiftKey\) \{ ev\.preventDefault\(\); ev\.stopPropagation\(\); submit\(\); \}/);
});

test("the modal's one-level seeding folds by children, never the REMOVED n.rows (blank-modal fix, the user 2026-07-08)", () => {
  // reading `.length` off the removed AskTreeNode.rows threw and ABORTED renderModal → the modal opened blank.
  // Foldability is decided by children alone now.
  assert.match(FEED, /else if \(\(n\.children \|\| \[\]\)\.length\) collapsedNodes\.add\(key\);/);
  assert.doesNotMatch(FEED, /n\.rows\.length/, "the removed-field reader (n.rows.length) is gone");
});
