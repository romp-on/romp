// The optimistic follow-up move must never WRITE into the payload it renders (the user 2026-08-02, who
// replied to a blocked card and watched it bounce working → blocked → working). On the federated page the
// ask objects in a `feed` payload ARE the FederationManager's cached per-host frames: mergeHostFeeds
// concatenates the cached arrays element-by-reference, and the merged frame reaches the pane by direct call
// from federation's emit (a same-realm window dispatch only when no handler is registered), so no structured
// clone severs the references. So applyFollowMove's old in-place
// `a.column = "working"` edited the manager's cache; the next merged re-emit — fired by ANY host's frame,
// seconds later — served the pane its own edit back as kernel truth, reconcileFollowMove read it as the
// kernel confirming the move ("confirmed") and dropped the prediction, and the next local build already in
// flight when the reply landed (honestly pre-reply) bounced the card back to Blocked with nothing left to
// hold it. Recorded end to end in client-diag.jsonl: predict:followup → payload flip back with
// predicted:false three seconds later → payload flip forward nine seconds after that.
//
// feed.ts has import-time DOM side effects, so per the established precedent this is source pins plus an
// executed replica of the copy-on-write decision (optimistic-send.test.ts / user-img-dedup.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const F = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

// the whole applyFollowMove body, so the pins below can't be satisfied by unrelated code
const body = (() => {
  const m = F.match(/function applyFollowMove\(list: AskItem\[\]\) \{[\s\S]*?\n\}/);
  assert.ok(m, "applyFollowMove not found in feed.ts");
  return m![0];
})();

test("applyFollowMove replaces the list slot with a copy — it never mutates the payload object", () => {
  // the copy idiom: spread into a fresh AskItem, column set on the copy, slot replaced
  assert.match(body, /const c: AskItem = \{ \.\.\.a, column: "working", category: "working" \};/, "the prediction names Working under both keys (the boards' phase two: askColumn reads the category first)");
  assert.doesNotMatch(body, /a\.column === "working"/, "the skip reads the card through askColumn, never the raw column");
  assert.match(body, /list\[i\] = c;/);
  // and no write lands on the shared object itself (assignment, not the `===` comparison in the guard)
  assert.doesNotMatch(body, /\ba\.(column|recheck|followupPending|t)\s*=[^=]/);
});

test("the shared-reference premise holds: the merge reuses cached frame elements, delivered same-realm", () => {
  // mergeHostFeeds concatenates the cached asks arrays by reference (no per-element copy)…
  assert.match(FED, /if \(Array\.isArray\(f\.asks\)\) merged\.asks\.push\(\.\.\.f\.asks\);/);
  // …and the merged frame is handed to the pane by direct call (emit: the registered handler, else a same-realm
  // window dispatch), so no structured clone severs the references
  assert.match(FED, /this\.emit\(mergeHostFeeds\(/);
});

// Executed replica: the exact scenario off the diagnosed trail. A cached host frame holds the blocked
// card; the merge serves its elements by reference; the pane predicts and renders. The cache must still
// read needs_input afterwards — else the next re-emit "confirms" the prediction the pane itself painted.
test("rendering a predicted card leaves the cached frame untouched, so a re-emit cannot false-confirm", () => {
  const pending = new Set(["s1:g1"]);
  const kind = new Map([["s1:g1", "followup"]]);
  const nowSec = 1000;
  const apply = (list: any[]) => {                       // replica of the pinned copy-on-write body
    for (let i = 0; i < list.length; i++) {
      const a = list[i];
      if (!pending.has(a.itemId) || (a.category ?? a.column) === "working") continue;
      const c: any = { ...a, column: "working", category: "working" };
      if ((kind.get(a.itemId) ?? "followup") === "followup") { c.recheck = true; c.followupPending = true; }
      if (c.t < nowSec) c.t = nowSec;
      list[i] = c;
    }
  };
  const cached: any[] = [{ itemId: "s1:g1", column: "needs_input", category: "needs_input", t: 900 }];   // the manager's stored local frame
  const merged: any[] = [...cached];                      // mergeHostFeeds: fresh array, shared elements
  apply(merged);
  // the render shows the prediction…
  assert.equal(merged[0].column, "working");
  assert.equal(merged[0].category, "working");
  assert.equal(merged[0].recheck, true);
  assert.equal(merged[0].t, nowSec);
  // …while the cache still holds exactly what the kernel sent, so the next merged re-emit still shows the
  // card blocked and reconcileFollowMove keeps waiting for the kernel's real answer
  assert.equal(cached[0].column, "needs_input");
  assert.equal(cached[0].category, "needs_input");
  assert.equal(cached[0].t, 900);
  assert.equal("recheck" in cached[0], false);
  // and a second render pass over a re-merge of the same cache predicts again without double-copying
  const remerged = [...cached];
  apply(remerged);
  assert.equal(remerged[0].column, "working");
  assert.equal(cached[0].column, "needs_input");
});
