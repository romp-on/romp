// The browser's overlay dress in the CHAT document (the user 2026-08-24, near-verbatim: "the browse
// file system thing now is totally messed up — showing at the bottom, below the message box. It's
// supposed to be a giant thing that goes on top of the entire chat area"). The 642 move made the
// browser pane-local in the chat, but its CSS lived only in feed.css — the chat page loads
// styles.css alone, so the browser mounted as UNSTYLED block flow at the document bottom. The
// overlay family now mirrors in styles.css (the .romp-acted two-sheets precedent) and this test
// pins the two byte-equal so they cannot drift.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const CHAT = read("styles.css");
const FEED = read("feed.css");

const RULES = [
  ".filebrowse {", "body.filebrowse-open {", ".fb-bar {", ".fb-crumbs {", ".fb-crumb {",
  ".fb-list {", ".fb-row {", ".fb-name {", ".fb-size {", ".fb-more {", "#fb-ctx {", ".fb-crumb-up {",
];

function ruleOf(css: string, head: string): string {
  const at = css.indexOf(head);
  assert.ok(at >= 0, head + " present");
  return css.slice(at, css.indexOf("}", at) + 1);
}

test("every browser overlay rule exists in BOTH sheets, byte-equal — the chat page loads styles.css alone", () => {
  for (const head of RULES) {
    assert.equal(ruleOf(CHAT, head), ruleOf(FEED, head), head + " mirrors exactly");
  }
});

test("the overlay geometry: pane-filling, viewer stays one layer above, scroll locked while open", () => {
  assert.match(CHAT, /\.filebrowse \{ position: fixed; inset: 0; z-index: 890;/,
    "GIANT — fixed inset-0 fills the pane, thread AND composer beneath");
  assert.match(CHAT, /body\.filebrowse-open \{ overflow: hidden; \}/, "the thread cannot scroll behind it");
  // the viewer overlays the listing (the one-directional stack): 1200 > 890
  const vz = Number((CHAT.match(/#romp-fileview \{[^}]*z-index: (\d+)/s) || [])[1]);
  assert.ok(vz > 890, "the viewer's backdrop sits above the browser (got " + vz + ")");
});

test("the overlay stays GIANT; the LISTING reads at a centered measure on wide panes", () => {
  // the 2026-08-24 ruling stands (fixed inset-0 over the whole chat area) — the 2026-09-02 fix caps
  // only the rows' measure: full-width rows put a file's size ~800px from its name on desktop.
  // min(720px, 100%) keeps narrow panes and phones pixel-identical.
  assert.match(CHAT, /\.fb-list \{[^}]*width: min\(720px, 100%\); margin-inline: auto; \}/);
});
