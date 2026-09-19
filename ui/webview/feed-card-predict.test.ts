// EVERY context-carrying reply flips its feed card to Working instantly (the user 2026-07-20). The feed
// composer's follow-up was already optimistic (feed-followup-move), but the same reply from anywhere else —
// the chat's citation-chip follow-up, a picker/permission answer typed in the chat, another feed view's
// button — waited out the kernel rebuild+push round trip. The kernel now fans a tiny "cardPredict" frame to
// every feed client the instant the reply op arrives (kernel _predict_working, the hover-glow fan-back
// pattern), and the feed reuses the SAME optimisticFollowMove prediction machinery. The kernel push stays
// authoritative. Source pins on both sides + an executed replica of the reconcile semantics.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("predictions carry a KIND — followup / answer ('plain' died with Move to Working, 2026-07-25)", () => {
  assert.match(FEED, /type MoveKind = "followup" \| "answer";/);
  assert.match(FEED, /const pendingMoveKind = new Map<string, MoveKind>\(\);/);
  assert.match(FEED, /function optimisticFollowMove\(itemId: string, kind: MoveKind = "followup"\)/);
  assert.doesNotMatch(FEED, /pendingMovePlain/, "the plain boolean set is gone — kind covers it");
});

test("the feed handles the kernel's cardPredict fan-back with the same prediction machinery", () => {
  assert.match(FEED, /m\.type === "cardPredict" && Array\.isArray\(m\.ids\)/);
  // a sub-goal id (per-sub follow-up target) resolves to the visible top card that carries it in its tree
  assert.match(FEED, /asks\.find\(\(a\) => a\.itemId === raw\) \?\? asks\.find\(\(a\) => a\.tree\?\.some\(\(n\) => n\.id === raw\)\)/);
  // a card already in Working needs no prediction (and no pointless revert timer)
  assert.match(FEED, /if \(top && askColumn\(top\) !== "asks"\) \{ optimisticFollowMove\(top\.itemId, kind\); moved = true; \}/);   // Working through askColumn: the category first (card boards, the 1837 round-two read)
});

test("an ANSWER prediction yields to the FIRST authoritative payload and reverts silently", () => {
  // the kernel rebuilds right after retiring the picker (answerAsk → _mark_views_dirty), so a payload that
  // still shows the card out of Working is post-answer truth — e.g. the next permission prompt of a burst.
  // Holding the prediction would mask a genuine "needs you".
  assert.match(FEED, /if \(!a \|\| askColumn\(a\) === "asks" \|\| pendingMoveKind\.get\(id\) === "answer"\) \{/);   // Working by the kernel's category since the boards' phase two (askColumn reads it first, the column from an older kernel)
  // the backstop toasts for followup/plain (an answer romp never gave must be apparent) but never for an
  // ANSWER prediction — the re-shown ⏸ blocked card IS the signal. An answer is never acked either, so it
  // reaches neither branch of ackFollowMove; the reconcile above always retires it first (2026-07-21).
  assert.match(FEED, /if \(k !== "answer"\) feedToast\(/);
});

test("executed replica: reconcile keeps a followup prediction pending but drops an answer either way", () => {
  // mirrors reconcileFollowMove exactly (feed.ts) — kinds map + incoming authoritative payload
  const reconcile = (pending: Set<string>, kinds: Map<string, string>, incoming: { itemId: string; column: string; category?: string }[]) => {
    for (const id of Array.from(pending)) {
      const a = incoming.find((x) => x.itemId === id);
      if (!a || (a.category ?? a.column) === "working" || kinds.get(id) === "answer") { pending.delete(id); kinds.delete(id); }   // askColumn's read: the category first
    }
  };
  // a follow-up not yet confirmed stays predicted (the kernel hasn't caught up)
  let pending = new Set(["s:g1"]); let kinds = new Map([["s:g1", "followup"]]);
  reconcile(pending, kinds, [{ itemId: "s:g1", column: "needs_input" }]);
  assert.ok(pending.has("s:g1"), "unconfirmed follow-up keeps predicting");
  // …but confirms on working, and on disappearance
  reconcile(pending, kinds, [{ itemId: "s:g1", column: "working" }]);
  assert.equal(pending.size, 0);
  pending = new Set(["s:g2"]); kinds = new Map([["s:g2", "followup"]]);
  reconcile(pending, kinds, []);
  assert.equal(pending.size, 0, "a cleared/absorbed card confirms too");
  // an ANSWER prediction yields to the first payload even when the card is STILL blocked (burst re-block)
  pending = new Set(["s:g3"]); kinds = new Map([["s:g3", "answer"]]);
  reconcile(pending, kinds, [{ itemId: "s:g3", column: "needs_input" }]);
  assert.equal(pending.size, 0, "answer yields — a renewed block must show, not be masked");
});

test("kernel: every reply-shaped drive op fans the cardPredict frame; cancel does not", () => {
  assert.match(KERNEL, /def _predict_working\(flavor, ids=None, sid=None\):/);
  assert.match(KERNEL, /_send_to_app\("feed", \{"type": "cardPredict", "ids": ids, "flavor": flavor\}\)/);
  // follow-up (any surface — the chat citation chip lands on the same op) + Move to Working name their card
  assert.match(KERNEL, /_predict_working\("followup", ids=\[iid\]\)/);
  assert.doesNotMatch(KERNEL, /_predict_working\("plain"/, "the plain flavor died with the cardMove op");
  // the four answer-shaped picker ops resolve sid → live-blocked card(s); cancel answers nothing
  assert.equal((KERNEL.match(/_predict_working\("answer", sid=sid\)/g) || []).length, 4);
  assert.match(KERNEL, /be\.on_ask\(sid, "cancel"\); _mark_views_dirty\(\)\s+# a cancel answers nothing — no Working prediction/);
  // the sid → card map reads the LAST BUILT feed payload and skips apiError floors (an answer lifts no API error)
  assert.match(KERNEL, /a\.get\("column"\) == "needs_input"/);
  assert.match(KERNEL, /\(a\.get\("blocked"\) or \{\}\)\.get\("state"\) in _NEEDS_INPUT_STATES/);
});
