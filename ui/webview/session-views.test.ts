// Session views on the chat side (the user 2026-08-18; TAG model 2026-08-23; ALL default
// 2026-08-24): the kernel's views blob — echoed on every tabOrder push — gates which sessions get
// TABS. Two built-in sentinels: "all" — the default — shows LITERALLY EVERYTHING (the hidden set retired 2026-08-24);
// "untagged" keeps the old default's meaning (a tag marks a specialized session, excluded from the
// untagged view and shown under its tag views). A tagged session is a BACKGROUND session: still running, judged,
// carded; the + picker lists it under "Hidden — reveal" and the timeline's corner panel counts it,
// so nothing runs in secret (the 2026-08-11 rule this feature deliberately carves an exception
// into, keeping its spirit). Executed tests on the pure module + source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewVisible, viewsKey, revealIn, viewTagUnion } from "../../ui/webview/session-views";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2"] };

test("executed: All shows literally everything (hidden retired 2026-08-24); untagged the tagless; a tag view its members", () => {
  assert.equal(viewVisible(null, "s1"), true);
  assert.equal(viewVisible({ active: "all", hidden: ["s1"] }, "s1"), true, "a legacy hidden entry is IGNORED — nothing hides from All (the user 2026-08-24)");
  assert.equal(viewVisible({ active: "all", tags: [G] }, "s2"), true, "TAGGED → All still shows it (2026-08-24)");
  assert.equal(viewVisible({ active: "all", tags: [G] }, "s1"), true, "untagged → shown");
  assert.equal(viewVisible({ active: "untagged", tags: [G] }, "s2"), false, "TAGGED → out of the untagged view");
  assert.equal(viewVisible({ active: "untagged", tags: [G] }, "s1"), true, "tagless → the untagged view shows it");
  assert.equal(viewVisible({ active: "untagged", hidden: ["s1"], tags: [G] }, "s1"), true, "…and a legacy hidden entry does not hide in untagged either — only membership excludes");
  assert.equal(viewVisible({ active: "g1", hidden: ["s2"], tags: [G] }, "s2"), true, "a tag view shows its members");
  assert.equal(viewVisible({ active: "g1", tags: [G] }, "s1"), false);
  assert.equal(viewVisible({ active: "gone", tags: [] }, "s1"), true, "an orphaned active fails open");
  // the pre-rename key an un-updated kernel still pushes reads identically
  assert.equal(viewVisible({ active: "untagged", groups: [G] }, "s2"), false, "legacy `groups` key honored");
  assert.equal(viewVisible({ active: "g1", groups: [G] }, "s2"), true);
});

test("executed: reveal ADDS to the chat lens, never mutates membership (per-surface, 2026-08-25)", () => {
  // the reveal keys on the CHAT surface's lens now: the minimal move is ADDITIVE — the holder tag
  // (or the none bucket) JOINS the selection, and what the user was looking at stays visible.
  // Membership is still never touched (2026-08-23 — a peek never strips a tag), and the legacy
  // scalar `active` is left alone entirely (old clients own it).
  const rev = revealIn({ active: "g1", tags: [G] }, "s1");
  assert.deepEqual(rev.actives!.chat, { none: true, tags: ["pool"] },
    "tagless from a tag view: the none bucket JOINS — the pool selection stays visible");
  assert.equal(rev.active, "g1", "the legacy scalar is never rewritten");
  const rev2 = revealIn({ active: "untagged", tags: [G] }, "s2");
  assert.deepEqual(rev2.actives!.chat, { none: true, tags: ["pool"] },
    "tagged: its holder tag joins the selection; the untagged pick stays");
  assert.deepEqual(rev2.tags![0].members, ["s2"], "membership untouched — the jump never strips a tag");
  const revAll = revealIn({ active: "all", tags: [G] }, "s2");
  assert.equal(revAll.actives?.chat, undefined, "everything visible under All: nothing changes at all");
  const rev4 = revealIn({ active: "g1", tags: [G] }, "s2");
  assert.equal(rev4.actives?.chat, undefined, "already visible in the active tag: nothing changes");
});

test("executed: the canonical key ignores list order AND which key the kernel used", () => {
  const a = { active: "g1", hidden: ["b", "a"], tags: [{ id: "g1", members: ["y", "x"] }] };
  const b = { active: "g1", hidden: ["a", "b"], groups: [{ id: "g1", members: ["x", "y"] }] };
  assert.equal(viewsKey(a), viewsKey(b), "a legacy-keyed echo still reconciles an optimistic edit");
  assert.notEqual(viewsKey(a), viewsKey({ active: "all", hidden: ["a", "b"], tags: [] }));
});

test("the tabOrder frame carries the blob and the strip filters on it, composing with #only", () => {
  assert.match(RENDER, /else if \(m\.type === "tabOrder"\) \{ captureViews\(m\.views \|\| null\); applyTabOrder\(m\.order, m\.tabs\); \}/,
    "echo-less frames still reach captureViews — an older kernel must age out a pending edit");
  assert.match(RENDER, /const inViewIds = ids\.filter\(tabInView\);/);
  assert.match(RENDER, /const visibleIds = only \? inViewIds\.filter\(\(id\) => matchesOnly\(nameOf\(id\), only\)\) : inViewIds;/);
  // the active-tab re-point covers BOTH filters — but only for a session that still EXISTS: a
  // torn-down session's reselect belongs to the teardown path's MRU logic, not to us
  assert.match(RENDER, /if \(activeId && ids\.includes\(activeId\) && !visibleIds\.includes\(activeId\) && visibleIds\.length\) \{/);
});

test("every cycling path walks the VISIBLE order — keyboard can never land on a hidden session", () => {
  const hits = RENDER.match(/const ord = visibleOrder\(\);\s*(?:\/\/[^\n]*)?\n/g) || [];
  assert.equal(hits.length, 3, "focused-tab arrows, window arrows, and the shell's cycleTab");
  assert.doesNotMatch(RENDER, /setActive\(order\[\(i \+ dir \+ order\.length\) % order\.length\]\)/,
    "no raw-order cycle survives");
});

test("optimistic edits hold sticky and yield to the kernel after three silent pushes", () => {
  assert.match(RENDER, /function captureViews\(v: SessionViews \| null\) \{[\s\S]{0,600}\+\+pendingViewsAge >= 3/);
  assert.match(RENDER, /function postViews\(v: SessionViews\) \{[\s\S]{0,300}setTimelineViews/);
});

test("a view-filtered session keeps one visible home: the picker's other-view section, and picking jumps views", () => {
  // (the label was "Hidden — reveal" until the hidden set retired, the user 2026-08-24 — the
  // section now serves tag-filtered sessions, same no-secret rule from 2026-08-11)
  assert.match(RENDER, /isOpenTab\(it\.id\) && !tabInView\(it\.id\)/);
  assert.match(RENDER, /label\("In another view — open"\)/);
  assert.match(RENDER, /time\.textContent = "other view";/);
  assert.match(RENDER, /revealSession\(it\.id\);\s*\/\/ its tab already exists[\s\S]{0,120}setActive\(it\.id\);/);
});

test("the hide MECHANISM is fully retired (the user 2026-08-24) — reveal survives as the view jump", () => {
  assert.doesNotMatch(RENDER, /Hide from chat & timeline/);
  assert.doesNotMatch(RENDER, /hideIn\(/, "no hide gesture anywhere");
  assert.match(RENDER, /function revealSession\(id: string\) \{ postViews\(revealIn\(effViews\(\), id\)\); \}/);
});

test("federation carries the LOCAL kernel's views blob through merged tabOrder re-emits", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \(host === LOCAL && m\.views && typeof m\.views === "object"\) this\.localViews = m\.views;/);
  assert.match(FED, /\{ type: "tabOrder", order, tabs, views: this\.localViews \?\? undefined \}/,
    "without this the browser dashboard's chat never receives the blob at all");
});

test("view edits are INTENT ops — they survive a kernel-restart reconnect instead of dropping", () => {
  const PIPE = fs.readFileSync(path.resolve(process.cwd(), "src", "pipe-intent.ts"), "utf8");
  assert.match(PIPE, /"setTimelineViews"/);
});

test("hiding the last visible session blanks its transcript and the placeholder says why", () => {
  assert.match(RENDER, /const blank = !visibleIds\.length && ids\.length > 0 && !tabInView\(activeId\);/);
  assert.match(RENDER, /Every session is hidden from this view\./);
  assert.match(RENDER, /allHiddenBlanked = false;/, "…and the transcript restores when anything is visible again");
});

test("executed: a REMOTE tag is a view too — resolved, labeled by its host, gone-falls-open (federation v0)", () => {
  // the kernel joins attached kernels' tags into remoteTags (read-only, id = "host:tagid", members
  // already respelled viewer-relative); picking one filters exactly like a local tag
  const rt = { active: "alpha:g9", tags: [], remoteTags: [{ id: "alpha:g9", host: "alpha", members: ["alpha:rs1", "x"] }] };
  assert.equal(viewVisible(rt, "alpha:rs1"), true);
  assert.equal(viewVisible(rt, "y"), false);
  // its kernel detached → the view falls OPEN, never trapping the viewer in an empty view
  assert.equal(viewVisible({ active: "alpha:g9", tags: [] }, "y"), true);
  // the echo key EXCLUDES remoteTags: derived kernel state, not an edit — two blobs differing only
  // there must reconcile the same optimistic edit
  const a = { active: "all", hidden: [], tags: [] };
  const b = { active: "all", hidden: [], tags: [], remoteTags: [{ id: "alpha:g9" }] };
  assert.equal(viewsKey(a), viewsKey(b));
});

test("executed: a tag view is the NAME-KEYED union (user ruling 2026-08-24) — kernels are plumbing", () => {
  const both = { active: "gL",
                 tags: [{ id: "gL", name: "team", members: ["local1"] }],
                 remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", members: ["m1"] }] };
  assert.equal(viewVisible(both, "local1"), true);
  assert.equal(viewVisible(both, "m1"), true, "the remote store's member joins the local id's view");
  assert.equal(viewVisible(both, "other"), false);
  assert.equal(viewVisible({ ...both, active: "TESTHOST-A:g1" }, "local1"), true, "either id, same union");
});

test("executed: untagged excludes by the UNION — a remote-homed tag counts (the user 2026-08-24)", () => {
  // the reported bug: tagging from the chat left the session in the untagged view, because only
  // LOCAL tags excluded there. Held by any kernel's tag = tagged, on every surface.
  const v = { active: "untagged", hidden: [], tags: [],
              remoteTags: [{ id: "alpha:g1", host: "alpha", name: "workers", members: ["cards1"] }] };
  assert.equal(viewVisible(v, "cards1"), false, "remote-homed tag → out of untagged");
  assert.equal(viewVisible(v, "other"), true);
});


test("executed: the union renders in the USER'S order — tagOrder governs, remote-homed names hold position (the user 2026-08-25)", () => {
  const v = {
    active: "all",
    tags: [
      { id: "g1", name: "alpha", color: "#DD42FF", members: [] },
      { id: "g2", name: "beta", color: "#4EC9B0", members: [] },
    ],
    remoteTags: [{ id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "remotepool", color: "#7aa2f7", members: [] }],
  };
  assert.deepEqual(viewTagUnion(v).map((u) => u.name), ["alpha", "beta", "remotepool"],
    "no order yet → natural (array, then remotes) order");
  assert.deepEqual(viewTagUnion({ ...v, tagOrder: ["remotepool", "alpha"] }).map((u) => u.name),
    ["remotepool", "alpha", "beta"],
    "the dragged order governs — the remote-homed name holds its position, viewer-side; unlisted names follow naturally");
  // the echo key carries the order, so an optimistic drag clears on the kernel's echo and never flaps
  assert.notEqual(viewsKey({ ...v, tagOrder: ["remotepool", "alpha"] }), viewsKey(v),
    "tagOrder is part of the canonical echo compare");
  // a user-typed name CAN be a prototype key (adversarial review 2026-08-25): the name-keyed
  // builder crashed on {}["constructor"] resolving to Function — null-prototype lookups now
  assert.deepEqual(viewTagUnion({ tags: [{ id: "p1", name: "constructor", members: [] }], tagOrder: ["constructor"] })
    .map((u) => u.name), ["constructor"], "a prototype-key tag name builds and orders cleanly");
});
