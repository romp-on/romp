// The feed's LOCAL tag lens (the user 2026-08-25, T70): the shared multi-select model (tag-lens.ts)
// and menu (tag-menu.ts) mounted as THIS board's own deliberate narrowing — independent of the
// shared session view (the decoupling ruling stands; the payload's blob feeds tag DEFINITIONS
// only). The model's own semantics (All exclusive, union visibility, last-off→All) execute in
// tag-lens's suite; here the COMPOSITION executes and the mount is source-pinned (the repo
// convention). Disclosure lineage: the 665 outside-view treatments, revived under the user's own
// lens. Synthetic fixtures only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { lensVisible, lensAll, toggleLens } from "./tag-lens";
import type { TagUnion } from "./session-views";

const ROOT = path.resolve(process.cwd(), "..");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.css"), "utf8");
const PIPE = fs.readFileSync(path.join(ROOT, "vscode-extension", "src", "pipe-intent.ts"), "utf8");

const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";
const unions: TagUnion[] = [
  { name: "infra", color: "", members: [U], ids: ["t1"], localId: "t1", remotes: [] },
  { name: "web", color: "", members: [V], ids: ["TESTHOST:t2"], localId: null, remotes: [] },
];

test("arbitrary combinations union-filter; All is exclusive and the default board is byte-identical", () => {
  // the model executes in tag-lens's own suite; this is the FEED's contract with it
  const both = toggleLens(toggleLens({ all: true }, { tag: "infra" }), "none");
  assert.equal(lensVisible(both, unions, U), true, "tagged infra — in the union");
  assert.equal(lensVisible(both, unions, "77777777-0000-0000-0000-000000000000"), true, "untagged — the none bucket");
  assert.equal(lensVisible(both, unions, V), false, "tagged web only — outside the combination");
  assert.equal(lensAll(toggleLens(both, "all")), true, "All is exclusive — one pick clears the rest");
  // default All short-circuits before any union math — today's board, byte-identical
  assert.match(FEED, /if \(lensAll\(feedLens\)\) return s;   \/\/ default All = today's board, byte-identical/);
});

test("the lens is its own slot in the view family; needs-you passes (the family's interrupt rule)", () => {
  assert.match(FEED, /function viewScope\(list: AskItem\[\]\): AskItem\[\]/,
    "the combobox/search scoping kept its own layer");
  assert.match(FEED, /return s\.filter\(\(a\) => lensVisible\(feedLens, u, a\.sid\) \|\| a\.column === "needs_input"\);/,
    "the same breakthrough the satellite and internals lens wear");
  // hover-freeze counts through viewFiltered = viewBase — the badges stay honest for free (the
  // team-internals slot retired 2026-08-25 on the user's verdict; the slot family stands)
  assert.match(FEED, /return viewBase\(list\);/);
  assert.match(FEED, /function outsideLensCount\(list: AskItem\[\]\): number \{\s*\n\s*return lensAll\(feedLens\) \? 0 : viewScope\(list\)\.length - viewBase\(list\)\.length;/,
    "the disclosure counts exactly what the lens alone hides — breakthroughs already show");
});

test("persistence: sessionStorage round-trip, All clears the key, the dialog's set-for-all adopts", () => {
  assert.match(FEED, /sessionStorage\.getItem\("romp:feedTags"\)/);
  assert.match(FEED, /if \(lensAll\(l\)\) sessionStorage\.removeItem\("romp:feedTags"\);/,
    "All is the default — never persisted as a filter");
  assert.match(FEED, /sessionStorage\.setItem\("romp:feedTags", JSON\.stringify\(l\)\);/);
  // the PR-B adoption contract: the tags dialog's set-for-all lands via a localStorage write
  assert.match(FEED, /if \(e\.key !== "romp:feedTags-set" \|\| !e\.newValue\) return;/);
  assert.match(FEED, /if \(v && v\.lens\) \{ setFeedLens\(v\.lens as TagLens\); render\(\); \}/);
});

test("the mount is the SHARED component: tag glyph button, stay-open menu, Configure routes to the dialog", () => {
  assert.match(FEED, /b = tagMenuButton\("filter this board by tag — combinations union; All shows everything", \(btn\) => \{/,
    "the shared monochrome tag glyph — identical across surfaces");
  assert.match(FEED, /openTagMenu\(btn, \{/);
  assert.ok(!FEED.includes("scopeCaption"), "the menu scope caption retired 2026-08-25 — the button tooltip carries the scope");
  assert.match(FEED, /onApply: \(l\) => \{ setFeedLens\(l\); render\(\); \},/);
  assert.match(FEED, /onConfigure: \(\) => vscodeApi\?\.postMessage\(\{ type: "openTagsDialog" \}\),/,
    "one management entry; creation lives in the dialog");
  assert.match(PIPE, /"openTagsDialog"/, "the VS Code pipe whitelists the route the kernel already carries");
  assert.match(FEED, /ensureTagLensBtn\(\)\.style\.display = showCA \? "" : "none";/, "footer-gated like its siblings");
  // resting state = the SIBLINGS' computed style by construction (the user 2026-08-25, round two:
  // the shared component's inline dress made rest blue-outlined and All off-black): the inline
  // style is stripped and the className IS the sibling vocabulary — equality pinned, so it can't
  // drift dark again; active is the standard .on class only, no inline colour anywhere.
  assert.match(FEED, /b\.removeAttribute\("style"\);/);
  assert.match(FEED, /b\.className = "fdismiss ffollow feed-modetoggle";/,
    "the exact class string the Sessions/View buttons wear");
  // the 696 instance toggle subsumed by the SHARED convention renderer (the user 2026-08-25):
  // class mode keeps the footer's .on mechanics, and the selection chips render beside the button
  assert.match(FEED, /syncTagFilter\(b, ch, feedLens, lensUnions\(feedTagViews\) as never, \(l\) => \{ setFeedLens\(l\); render\(\); \}, "class"\);/,
    "accent ONLY while the lens narrows — via the shared renderer");
  const mnt = FEED.slice(FEED.indexOf("function ensureTagLensBtn"), FEED.indexOf("// The footer VIEW MENU"));
  assert.ok(!mnt.includes("style.color"), "no inline colour survives in the mount");
});

test("what the lens hides stays one glance away: whisper, promoted banner, click-to-All (665 lineage)", () => {
  assert.match(FEED, /const lensOutN = outsideLensCount\(asks\);/);
  assert.match(FEED, /lmore\.classList\.toggle\("prominent", lensOutN > lensShownN\);/,
    "the exact promotion rule: the lens hides more than the board shows");
  assert.match(FEED, /"Showing \\u201c" \+ lensLabel\(feedLens\) \+ "\\u201d \\u2014 " \+ lensOutN/,
    "the promoted line NAMES the lens");
  assert.match(FEED, /lmore\.onclick = \(\) => \{ setFeedLens\(\{ all: true \}\); render\(\); \};/,
    "the way out is purely local — no kernel round-trip");
  assert.match(CSS, /#feed-lensmore\.prominent \{ margin: 6px 8px 2px; padding: 10px 14px; background: #252526;/,
    "the judge-limit banner's card chrome — neutral, a narrowed board is a choice");
});
