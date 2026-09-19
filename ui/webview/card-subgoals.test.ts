// Feed cards show an inline sub-goal tree (applySubgoals): the top-level goal (= the card title) plus, when
// the per-card "Sub-goals" button is on, its ENTIRE subtree — as ✓ done / ⏸ blocked / ○ open rows indented by
// depth, WITH the outline's disclosure triangles (▶/▼) to fold branches (the user 2026-07-08). Same inclusion
// rules as the modal/outline tree (renderTreeNode): skip handoffs, dedup repeats. No jsdom — source-level pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("ask cards render the goal's WHOLE sub-goal tree (the 'subgoals' section), not just level 1", () => {
  assert.match(FEED, /a\._checklist/);                       // the card carries a checklist element
  assert.match(FEED, /el\("div", "fask-checklist"\)/);
  assert.match(FEED, /function applySections\(a: any, it: AskItem, distillShown: boolean\): void/);
  // the tree renders only when the 'subgoals' section is selected
  assert.match(FEED, /if \(choice !== "subgoals" \|\| !root\) \{ cl\.style\.display = "none"; return; \}/);
  // a RECURSIVE walk from the root's children, descending every level (was root.children only)
  assert.match(FEED, /const walk = \(nid: string, depth: number\) =>/);
  assert.match(FEED, /for \(const c of freshKids\) walk\(c, 0\);/);
  // …and the reviewed-earlier kids still walk the SAME whole-tree recursion when expanded
  // (the user 2026-08-19: collapsed behind one row, never dropped), one level UNDER the fold row (2026-09-18)
  assert.match(FEED, /if \(revOpen\) for \(const c of revKids\) walk\(c, 1\);/);
  assert.match(FEED, /for \(const c of n\.children \|\| \[\]\) walk\(c, depth \+ 1\)/);
  assert.doesNotMatch(FEED, /root\.children\.map/, "no longer capped at the direct children");
  assert.match(FEED, /s\.status === "done" \? "✓"/);         // ✓ done / ⏸ question(blocked) / empty-ring open mark
  assert.match(FEED, /s\.status === "question" \? "⏸"/);     // blocked → the red ⏸ (was an amber ?), the user 2026-06-24
});

test("the inline tree follows the SAME rules as the modal outline: handoffs, repeats, depth indent, collapse triangles", () => {
  assert.match(FEED, /n\.kind === "handoff"/);               // delegation nodes render in their own section
  assert.match(FEED, /const repeat = seen\.has\(n\.id\)/);   // a node reached under two parents...
  assert.match(FEED, /if \(repeat \|\| collapsed\) return;/); // ...renders once; a collapsed branch is not descended
  assert.match(FEED, /row\.style\.paddingLeft = \(depth \* TREE_INDENT_EM\) \+ "em"/);  // same per-level indent as the modal
  assert.match(FEED, /wireNodeZones\(it, s, mark, txt, null, !repeat\)/);   // a dim repeat is display-only
  assert.match(CSS, /\.fcheck\.repeat \{[^}]*opacity: 0\.5/);   // dim, mirroring .ftree-node.repeat
});

test("each expandable node carries the outline's disclosure triangle (▶/▼); the tree opens DEFAULT-COLLAPSED", () => {
  // per-node EXPAND state (default collapsed — a node is open only once its triangle was clicked, the user 2026-07-08)
  assert.match(FEED, /const cardTreeExpanded = new Set<string>\(\);/);
  assert.match(FEED, /const collapsed = expandable && !cardTreeExpanded\.has\(id \+ ":" \+ n\.id\);/);
  assert.match(FEED, /el\("span", "fcheck-tri" \+ \(expandable \? " nav" : " empty"\)\)/);
  assert.match(FEED, /tri\.textContent = expandable \? \(collapsed \? "▶" : "▼"\) : "";/);
  assert.match(FEED, /if \(cardTreeExpanded\.has\(k\)\) cardTreeExpanded\.delete\(k\); else cardTreeExpanded\.add\(k\);/);
  assert.match(FEED, /renderTree\(\);/, "a triangle toggle re-renders the tree in place");
  assert.match(FEED, /row\.append\(tri, mark, txt\)/);        // triangle leads the row, then mark + text
  // styled like the modal's .ftree-tri
  assert.match(CSS, /\.fcheck-tri \{[^}]*width: 1em/);
  assert.match(CSS, /\.fcheck-tri\.nav \{ cursor: pointer; \}/);
});

test("the not-done OPEN mark is a 13px hollow ring the same size as the done ✓ disc (the user 2026-07-08)", () => {
  // the ○ glyph read too small next to the checkbox → an empty element the CSS draws as a 13px ring
  assert.match(FEED, /s\.status === "done" \? "✓" : s\.status === "question" \? "⏸" : "";/);
  assert.match(CSS, /\.fcheck\.open \.fcheck-mark \{[^}]*width: 13px; height: 13px/);
  assert.match(CSS, /\.fcheck\.open \.fcheck-mark \{[^}]*border-radius: 50%; border: 1\.5px solid var\(--dim\)/);
});

test("the sub-goal checklist is styled (done = blue ✓ disc, dimmed but NOT struck; question = red ⏸)", () => {
  assert.match(CSS, /\.fask-checklist \{/);
  // done mark = the chat view's blue ✓ disc (--check-bg + round), matching .todo-completed .todo-mark
  assert.match(CSS, /\.fcheck\.done \.fcheck-mark \{[^}]*var\(--check-bg\)/);
  assert.match(CSS, /\.fcheck\.done \.fcheck-mark \{[^}]*border-radius: 50%/);
  // the sub-goal text dims to recede but is NOT struck through (the user 2026-06-16)
  assert.match(CSS, /\.fcheck\.done \.fcheck-text \{[^}]*var\(--dim\)/);
  assert.doesNotMatch(CSS, /\.fcheck\.done \.fcheck-text \{[^}]*line-through/);
  // question(blocked) mark = the red ⏸ (var(--err)), not the old amber #d8a657 (the user 2026-06-24)
  assert.match(CSS, /\.fcheck\.question \.fcheck-mark \{[^}]*var\(--err\)/);
  assert.doesNotMatch(CSS, /\.fcheck\.question \.fcheck-mark \{[^}]*#d8a657/);
  // ...AND a RED RING around it (the user 2026-06-25): the same 13px hollow circle as the done ✓ disc and
  // the modal's .st-question ⏸-ring, so the card's blocked mark isn't a bare glyph missing its ring.
  assert.match(CSS, /\.fcheck\.question \.fcheck-mark \{[^}]*border: 1\.5px solid var\(--err\)/);
  assert.match(CSS, /\.fcheck\.question \.fcheck-mark \{[^}]*border-radius: 50%/);
});

test("the Sub-goals button counts only DIRECT children — one level below (the user 2026-07-15)", () => {
  assert.match(FEED, /let subCount = 0;/);
  // a single pass over the root's OWN children — not a stack that descends into grandchildren
  assert.match(FEED, /for \(const cid of \(root\.children \|\| \[\]\)\) \{/);
  assert.doesNotMatch(FEED, /stack\.push\(\.\.\.\(n\.children \|\| \[\]\)\);/, "no longer descends the whole subtree for the count");
  assert.match(FEED, /const hasSubs = subCount > 0;/);
  assert.match(FEED, /subBtn\.textContent = subCount === 1 \? "1 sub-goal" : subCount \+ " sub-goals";/);
});

// executed replica: the headline count is the goal's immediate, non-handoff children — grandchildren don't
// inflate it, though the tree still lets the user expand into them.
test("direct-child count ignores grandchildren and delegation handoffs", () => {
  type N = { id: string; kind?: string; children?: string[] };
  const tree: N[] = [
    { id: "root", children: ["a", "b", "h"] },
    { id: "a", children: ["a1", "a2"] },   // grandchildren under a
    { id: "b", children: [] },
    { id: "h", kind: "handoff" },          // a delegation → excluded
    { id: "a1" }, { id: "a2" },
  ];
  const byId = new Map(tree.map((n) => [n.id, n] as const));
  const root = byId.get("root")!;
  let subCount = 0;
  const seenC = new Set<string>([root.id]);
  for (const cid of (root.children || [])) {
    if (seenC.has(cid)) continue;
    seenC.add(cid);
    const n = byId.get(cid);
    if (!n || n.kind === "handoff") continue;
    subCount++;
  }
  assert.equal(subCount, 2, "a + b only — not a1/a2 (grandchildren) and not h (handoff)");
});
