// The feed UI uses the term "delegation", not "handoff" — the backend's courier verdict is
// DELEGATING and the user wants one term repo-wide (rompinfra, the user 2026-06-16). User-facing text
// + CSS class names are renamed; the internal kind data VALUE ("handoff") is intentionally left as-is
// (not user-facing, and changing it would touch the data contract). Source-level pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("user-facing provenance text says 'delegated', not 'handed off'", () => {
  assert.match(FEED, /delegated from another session/);
  assert.doesNotMatch(FEED, /handed off from another session/);
});

test("the lane + its rows use fask-delegation* classes, defined in feed.css", () => {
  assert.match(FEED, /"fask-delegations"/);
  assert.match(FEED, /"fask-delegation-line"/);
  assert.match(FEED, /"fask-delegation"/);
  assert.doesNotMatch(FEED, /"fask-handoff/);           // no stale class applications
  assert.match(CSS, /\.fask-delegations \{/);
  assert.match(CSS, /\.fask-delegation \{/);
  assert.doesNotMatch(CSS, /\.fask-handoff/);           // no stale rules
});

test("the kind data VALUE stays 'handoff' (not user-facing; keeps the node logic stable)", () => {
  assert.match(FEED, /kind: "ask" \| "handoff"/);
  assert.match(FEED, /n\.kind !== "handoff"/);
});

test("the name row keeps the session name on one line and pushes '↪ from' to the right edge", () => {
  // the user 2026-06-16: the name was wrapping mid-word while the origin crowded it; instead the row
  // fills its width, the name stays one line (ellipsis only if truly too long), origin goes right.
  // the user 2026-06-20: .fask-id's flex-grow does the right-push now (was margin-left:auto on origin).
  assert.match(CSS, /\.fask-id \{[^}]*flex: 1 1 auto/);
  assert.match(CSS, /\.fask-id \.fname \{[^}]*white-space: nowrap/);
  assert.match(CSS, /\.fask-origin \{[^}]*white-space: nowrap/);
});

test("a narrow card WRAPS the '↪ from' provenance under the name instead of overlapping the chips (the user 2026-06-20)", () => {
  // the bug: on a narrow delegation card that was ALSO reopened/followed-up, "↪ from <peer>" (nowrap,
  // margin-left:auto, nested in the shrinking idwrap) spilled out of idwrap on top of the "↻ Followed up"
  // chip. Fix: row2 wraps, and origin is a direct row2 child (sibling of the chips) so it drops to a
  // second line rather than overlapping when name + provenance + chips don't all fit.
  assert.match(CSS, /\.fask-row2 \{[^}]*flex-wrap: wrap/, "the name row wraps so trailing items never overlap");
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/, "origin is a row2 sibling of the chips");
  assert.doesNotMatch(FEED, /idwrap\.append\(name, origin\)/, "origin is no longer nested in the shrinking idwrap");
  // the old overflow mechanism is gone — flex-grow on idwrap right-aligns it instead of an auto margin
  assert.doesNotMatch(CSS, /\.fask-origin \{[^}]*margin-left: auto/);
});

test("a delegation card's title anchors on 'work' (not 'prompt') so it doesn't jump to an unrelated user msg", () => {
  // a delegation card has no originating user prompt; anchor:"prompt" landed on the nearest user turn
  // in time (wrong). origin cards anchor on "work" → land where the delegation was processed (rompinfra).
  assert.match(FEED, /let titleAnchor = it\.origin \? "work" : "prompt"/);
  assert.match(FEED, /anchor: titleAnchor/);
});

test("the '↪ from' badge is provenance for the card's LIFE: dimmed once absorbed, never removed", () => {
  // the user 2026-08-16: the badge used to vanish the moment the recipient finished (run_propagate
  // closes the sender's entry instantly), so a completed card never showed where its work came from
  // — and a propagated clear read as one card mysteriously taking another with it.
  assert.match(FEED, /live\?: boolean/);
  assert.match(FEED, /og\.classList\.toggle\("fask-origin-absorbed", it\.origin\.live === false\);/);
  assert.match(FEED, /their linked entry closed with this card/);
  assert.match(FEED, /clearing this card also clears their linked entry/,
    "the standing link is explained before the user discovers it by surprise");
  assert.match(CSS, /\.fask-origin-absorbed \{ opacity: 0\.55; \}/);
});

test("flatten finally feeds the delegations section: kind 'handoff' with the recipient's identity", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNEL, /"kind": "handoff" if _ho_sid else "ask"/);
  assert.match(KERNEL, /_name_of\(_ho_sid\) or _ho_sid\[:8\]/, "recipient name from the recorded handoff.peer");
});
