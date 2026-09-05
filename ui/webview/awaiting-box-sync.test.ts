// T225 (the user 2026-09-02): the awaiting box must be there the moment the chip says "Awaiting".
//
// Convicted at the source, twice over. The kernel assembles the chip's state AND the box's fields
// (awaitingWhy / awaitingKind / awaitingCount / awaitingTasks / awaitingTaskIds / awaitingPeers) in the
// SAME status payload — and ships a status-only change to a caught-up client as a chatTail DELTA: an
// empty event suffix plus the full status (kernel _send_chat). The client's chatTail handler assigned
// the status and repainted tabs + statusline (the chip), but the box (renderBgTasks → renderAwaitWhy)
// rendered only from the FULL-session frame path, which a quiet session never sends. So the chip read
// "Awaiting agents" for 31s+ with no box (the user's screenshot); in the served lab, without the fix the
// box never appeared in 40s; with it, the same frame. (A first attempt hooked only the host-side
// "status" frame — which the served page never receives; the WebSocket sniff in the lab showed the flip
// riding a chatTail. That handler, its `update` sibling and statusOnly now all render the box.)
//
// The fix is one call per handler, keyed on the awaiting fields CHANGING — never on the per-second ticks
// that touch nothing the box shows. No provisional box is needed: the descriptions are available at chip
// time (same payload), and the gist ("Awaiting agents · 2 background agents still working") IS the
// one-line version.
//
// Rider (the user, same morning): the chip agrees in NUMBER — "Awaiting agent" for exactly one,
// "Awaiting agents" only for two or more — from ONE count the kernel ships (awaitingCount), the same
// count the box gist, the feed pill and the spin caption derive their word from (kindWord).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const SPIN = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "spin-caption.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("a status-only frame re-renders the awaiting box when its fields change — the chip's own flip included", () => {
  // the chatTail delta is the frame a status-only change actually rides (kernel _send_chat)
  const tail = RENDER.split("function chatTail(msg: any) {")[1].split("\n}")[0];
  assert.match(tail, /const before = awaitKey\(s\.status\);\s*\n\s*if \(msg\.status\) s\.status = msg\.status;/);
  assert.match(tail, /appendActive\(\);\s*\n\s*renderLedger\(\);[\s\S]{0,900}?if \(awaitKey\(s\.status\) !== before\) renderBgTasks\(\);/,
    "the box renders from the SAME chatTail frame that flipped the chip");
  // the `update` delta and the host-side status frame render it the same way
  const upd = RENDER.split("function update(msg: any) {")[1].split("\n}")[0];
  assert.match(upd, /const before = awaitKey\(s\.status\);\s*\n\s*s\.status = msg\.status \|\| s\.status;/);
  assert.match(upd, /if \(awaitKey\(s\.status\) !== before\) renderBgTasks\(\);/);
  const body = RENDER.split("function statusOnly(msg: any) {")[1].split("\n}")[0];
  assert.match(body, /const before = awaitKey\(s\.status\);/);
  assert.match(body, /if \(msg\.id === activeId\) \{\s*\n\s*updateStatusline\(\);/, "the chip repaint stays");
  assert.match(body, /if \(awaitKey\(s\.status\) !== before\) renderBgTasks\(\);/);
  // …and the kernel's delta carries the full status the box reads from
  assert.match(KERNEL, /tail = \{"type": "chatTail", "id": sid, "from": change_from,\s*\n\s*"events": evs\[change_from:\], "total": total, "status": m\.get\("status"\)\}/);
});

test("the await key covers every field the box renders from, and nothing that ticks per second", () => {
  const key = RENDER.split("function awaitKey(")[1].split("\n}")[0];
  for (const f of ["st.state", "st.awaitingWhy", "st.awaitingKind", "st.awaitingCount", "st.awaitingTasks",
                   "st.awaitingTaskIds", "st.awaitingPeers", "st.awaitingItems"]) {   // awaitingItems: the rows the box groups (slice 2)
    assert.ok(key.includes(f), f + " must be in the key");
  }
  assert.doesNotMatch(key, /sinceEpoch|ctx\b|modelPending|effortPending/, "timer/ctx ticks must not rebuild the box");
});

test("the box's render condition is unchanged: awaited content present ⇒ box; absent ⇒ hidden", () => {
  const why = RENDER.split("function renderAwaitWhy(")[1].split("\n}")[0];
  assert.match(why, /if \(!why \|\| !activeId\) \{ host\.style\.display = "none"; return; \}/,
    "chip cleared (awaitingWhy gone) ⇒ the same status frame hides the box");
  assert.match(why, /host\.style\.display = "";/);
});

test("the chip and the box gist take the kind word from ONE count (T225 rider)", () => {
  // Since slice 2 (2026-09-05) the ONE rule is awaitWord (spin-caption.ts): it words the chip, the box
  // gist and the feed pill from the kernel's kind + count + the awaited ROWS — "agent" for one, "3
  // agents" for several, the bare number when the kinds are mixed. kindWord stays underneath for a
  // payload with no rows (an older kernel), so the count still decides the number there.
  assert.match(RENDER, /import \{ awaitWord, awaitBreakdown, groupRows, GROUP_TITLE, workingFor, type AwaitRow \} from "\.\/spin-caption";/);
  assert.match(RENDER, /awaitingCount\?: number \| null;/, "the Status shape carries the kernel's count");
  assert.match(RENDER, /awaitingItems\?: AwaitRow\[\];/, "…and the rows (slice 2)");
  assert.match(RENDER, /const chipWord = awaitWord\(s\.status\.awaitingKind, s\.status\.awaitingCount, chipItems\);/);
  assert.match(RENDER, /chip\.textContent = CHIP_LABEL\.awaitingBg \+ \(chipWord \? " " \+ chipWord : ""\);/);
  assert.match(RENDER, /const word = awaitWord\(s!\.status\.awaitingKind, s!\.status\.awaitingCount, items\);/);
  assert.match(RENDER, /lab\.textContent = "Awaiting" \+ \(word \? " " \+ word : ""\) \+ " · " \+ why\.replace/);
  // the feed pill and the spin caption derive their word the same way
  assert.match(FEED, /import \{ spinFor, waitedSuffix, awaitWord, groupRows, GROUP_TITLE, ROW_KIND_OF_LEGACY, type AwaitRow \} from "\.\/spin-caption";/);
  assert.match(FEED, /const pillWord = awaitWord\(awKind, \(it\.awaiting && it\.awaiting\.count\) \?\? taskRows\.length, taskRows\);/);
  assert.match(SPIN, /const word = kindWord\(aw\.kind, aw\.count\);/);
  assert.match(SPIN, /export function kindWord\(kind: string \| null \| undefined, count: number \| null \| undefined\): string \{/);
  assert.match(SPIN, /export function awaitWord\(kind: string \| null \| undefined, count: number \| null \| undefined,\s*\n\s*items: readonly AwaitRow\[\] \| null \| undefined\): string \{/);
});

test("the kernel ships the count beside the kind, from every awaiting source", () => {
  assert.match(KERNEL, /"awaitingCount": \(\(_aw or \{\}\)\.get\("count"\) if isinstance\(\(_aw or \{\}\)\.get\("count"\), int\) else None\),/);
  // since slice 2 (2026-09-05) the live sources are COMBINED into rows and the one count is derived
  // from them — every row counted, whatever its kind (_awaiting_from_items); the watches' own read
  // still counts one per row and now ships the rows too
  assert.match(KERNEL, /why = "%d background agent%s still working" % \(n, "" if n == 1 else "s"\)/,
    "live subagents: the sentence agrees in number with the row count");
  assert.match(KERNEL, /return \{"kind": kind, "why": why, "since": since, "count": n, "items": items,/, "the count IS the row count");
  assert.match(KERNEL, /kind = _AWAIT_ITEM_LEGACY_KIND\[kinds\[0\]\] if len\(kinds\) == 1 else "mixed"/, "one kind → its word; several → mixed");
  assert.match(KERNEL, /"tasks": descs, "count": len\(descs\), "items": items\}/, "armed watches: one per row");
  assert.match(KERNEL, /"count": ov\["count"\] if isinstance\(ov\.get\("count"\), int\) and ov\["count"\] > 0 else None,/,
    "an overlay row carries a count only when its producer said so — never parsed from the why");
});
