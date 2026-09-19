// The feed applies every payload while the tab is hidden but paints none of them (the user 2026-09-07,
// whose dashboard froze on the return to its browser tab): each hidden render was a full paint — the FLIP
// pass's two forced layouts and a double rAF per moved card all piled onto the return frame. Now render()
// is owed while nobody can see the pane and settled ONCE, synchronously, on the event that shows it, with
// the flip skipped (cards that moved while away snap into place: motion without new information, the
// 2026-07-29 rule). State keeps flowing: the follow-move backstop must find its prediction already
// confirmed by a payload applied hidden, or it would revert a move the kernel had confirmed.
//
// feed.ts has import-time DOM side effects (the repo convention: no test imports it), so this has two
// halves — the harness below runs the PINNED lines against the pure modules they call (paint-gate.ts,
// feed-flip.ts), and the source pins hold feed.ts to exactly that wiring. The visibility wiring itself (the
// pane's hidden word for the kernel's pane shim) is lifted from feed.ts and run at the end.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { paintHeld, paintReleased, publishPaneHidden, type PaneHiddenHost } from "./paint-gate";
import { flipNeeded } from "./feed-flip";

const requireCjs = createRequire(__filename);

type Ask = { itemId: string; column: string };
type Payload = { asks: Ask[] };

// The harness: feed.ts's render() gate, flip decision, release path and the follow-move backstop, line for
// line (the pins below are what keep this honest), over a list whose "content" is its painted cards.
function feedHarness() {
  const st = {
    hidden: false, intersecting: true,
    asks: [] as Ask[], painted: 0,                     // painted = list.childElementCount after the last paint
    pendingFollowMove: new Map<string, true>(),
    paintDirty: false, skipFlipOnce: false,
    prevCols: new Map<string, string>(),
    paints: 0, flipChecks: 0, lastNeedFlip: null as boolean | null,
  };
  const columnsOf = (asks: Ask[]) => new Map(asks.map((a, i) => ["a:" + a.itemId, a.column + ":" + i] as const));
  const flipNeededSpy = (a: Map<string, string>, b: Map<string, string>) => { st.flipChecks++; return flipNeeded(a, b); };
  function render() {
    if (paintHeld(st.hidden, st.intersecting, st.painted > 0)) { st.paintDirty = true; return; }
    const nextCols = columnsOf(st.asks);
    const needFlip = !st.skipFlipOnce && flipNeededSpy(st.prevCols, nextCols);
    st.skipFlipOnce = false;
    st.prevCols = nextCols;
    st.lastNeedFlip = needFlip;
    st.paints++; st.painted = st.asks.length;
  }
  // applyFeedPayload: model swap + reconcileFollowMove (a prediction the kernel now lists as working is
  // confirmed → dropped) + render(); nothing here looks at the visibility
  function applyFeedPayload(m: Payload) {
    st.asks = m.asks;
    for (const id of Array.from(st.pendingFollowMove.keys())) {
      const a = m.asks.find((x) => x.itemId === id);
      if (!a || a.column === "working") st.pendingFollowMove.delete(id);
    }
    render();
  }
  // ackFollowMove's backstop timer body
  function backstop(itemId: string): "kept" | "reverted" {
    if (!st.pendingFollowMove.has(itemId)) return "kept";
    st.pendingFollowMove.delete(itemId); render(); return "reverted";
  }
  function releasePaint() {
    if (!paintReleased(st.paintDirty, st.hidden, st.intersecting)) return;
    st.paintDirty = false;
    st.skipFlipOnce = true;
    render();
  }
  return { st, render, applyFeedPayload, backstop, releasePaint, columnsOf };
}

test("hidden → ten payloads → zero paints, while the asks model and the follow-move bookkeeping keep updating", () => {
  const f = feedHarness();
  f.applyFeedPayload({ asks: [{ itemId: "g1", column: "asks" }, { itemId: "g2", column: "needsInput" }] });   // first content, visible
  assert.equal(f.st.paints, 1);
  f.st.pendingFollowMove.set("g2", true);          // the user replied to g2: predicted into Working
  f.st.hidden = true;                              // the tab goes to the background
  for (let i = 0; i < 10; i++) {
    f.applyFeedPayload({ asks: [{ itemId: "g1", column: "asks" }, { itemId: "g2", column: i >= 3 ? "working" : "needsInput" }, { itemId: "g" + (10 + i), column: "asks" }] });
  }
  assert.equal(f.st.paints, 1, "no paint while hidden");
  assert.equal(f.st.paintDirty, true, "a paint is owed");
  assert.equal(f.st.asks.length, 3, "the model is the newest payload's");
  assert.equal(f.st.asks[2].itemId, "g19");
  assert.equal(f.st.pendingFollowMove.size, 0, "the confirming payload (g2 working) retired the prediction while hidden");
});

test("the follow-move backstop that fires after a confirming payload was applied hidden does not revert", () => {
  const f = feedHarness();
  f.applyFeedPayload({ asks: [{ itemId: "g2", column: "needsInput" }] });
  f.st.pendingFollowMove.set("g2", true);
  f.st.hidden = true;
  f.applyFeedPayload({ asks: [{ itemId: "g2", column: "working" }] });   // the kernel confirms while the tab is hidden
  assert.equal(f.backstop("g2"), "kept", "MOVE_ACK_MS elapses: the prediction is already confirmed, nothing to revert");
  assert.equal(f.st.paints, 1, "and the backstop's render() is held like any other");
});

test("release → exactly one synchronous paint, the flip decision skipped, prevCols = the painted columns", () => {
  const f = feedHarness();
  f.applyFeedPayload({ asks: [{ itemId: "g1", column: "asks" }, { itemId: "g2", column: "needsInput" }] });
  f.st.hidden = true;
  f.applyFeedPayload({ asks: [{ itemId: "g2", column: "completed" }, { itemId: "g1", column: "working" }] });   // both moved while away
  f.applyFeedPayload({ asks: [{ itemId: "g2", column: "completed" }, { itemId: "g1", column: "working" }, { itemId: "g3", column: "asks" }] });
  const checksBefore = f.st.flipChecks;
  f.st.hidden = false; f.releasePaint();           // visibilitychange → visible
  assert.equal(f.st.paints, 2, "one paint for the whole hidden stretch");
  assert.equal(f.st.lastNeedFlip, false, "cards snap into place");
  assert.equal(f.st.flipChecks, checksBefore, "flipNeeded was not even consulted");
  assert.deepEqual(f.st.prevCols, f.columnsOf(f.st.asks), "the flip baseline is what was painted");
  assert.equal(f.st.paintDirty, false);
  f.releasePaint();                                // a second release event (the observer's callback) owes nothing
  assert.equal(f.st.paints, 2);
  // the NEXT move, seen live, glides again: the skip was one-shot
  f.applyFeedPayload({ asks: [{ itemId: "g1", column: "working" }, { itemId: "g2", column: "completed" }, { itemId: "g3", column: "completed" }] });
  assert.equal(f.st.paints, 3);
  assert.equal(f.st.lastNeedFlip, true);
});

test("a display:none pane holds too, and the tab's return alone does not release it — the observer does", () => {
  const f = feedHarness();
  f.applyFeedPayload({ asks: [{ itemId: "g1", column: "asks" }] });
  f.st.intersecting = false; f.st.hidden = true;
  f.applyFeedPayload({ asks: [{ itemId: "g1", column: "working" }] });
  f.st.hidden = false; f.releasePaint();
  assert.equal(f.st.paints, 1, "tab back, pane still display:none");
  f.st.intersecting = true; f.releasePaint();
  assert.equal(f.st.paints, 2);
});

// ── source pins: feed.ts wires exactly the lines the harness ran ──
const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const body = (name: string) => new RegExp("^function " + name + "\\([\\s\\S]*?\\n\\}", "m").exec(SRC)![0];

test("render() is gated first, on the shared pure decision, and nothing else in feed.ts is", () => {
  assert.match(SRC, /import \{ paintHeld, paintReleased, publishPaneHidden \} from "\.\/paint-gate";/);
  assert.match(SRC, /function render\(\) \{\n  const list = document\.getElementById\("feed-list"\)!;\n  if \(!feedWatching\) \{ feedWatching = true; watchFeedVisibility\(list\); \}\n  if \(paintHeld\(document\.hidden, feedIntersecting, list\.childElementCount > 0\)\) \{ paintDirty = true; return; \}\n  pruneTip\(\);/,
    "the gate precedes every paint-side step (pruneTip, applyFollowMove, the footer, the columns)");
  // two gates, both PAINTS: render(), and the 15 s age pass (feed-age.ts liveRefresher) that rewrites the stamped
  // labels on the cards render() did not repaint — it reads the same decision, so the feed has one meaning of
  // "hidden"; no state path is withheld
  assert.equal(SRC.split("paintHeld(").length - 1, 2, "two gates: render() and the age pass; no other path is withheld");
  assert.match(SRC, /const live = liveRefresher\(\{ hidden: \(\) => paintHeld\(document\.hidden, feedIntersecting, true\), pass: livePass \}\);/);
  assert.match(SRC, /let feedIntersecting: boolean \| null = null;/, "the observer's word, null until it speaks: the gate reads null as on screen (no observer → the tab alone gates), and nothing is published for it");
});

test("the flip is skipped exactly once after a release, and prevCols still records what was painted", () => {
  assert.match(SRC, /const needFlip = !skipFlipOnce && flipNeeded\(prevCols, nextCols\);\n\s*skipFlipOnce = false;\n\s*prevCols = nextCols;/);
  assert.match(SRC, /askEls\.clear\(\); groupEls\.clear\(\);\n\s*skipFlipOnce = false;/, "the empty-board paint spends the snap too");
  const rel = body("releasePaint");
  assert.match(rel, /function releasePaint\(\): void \{\n\s*publishPaneHidden\(document\.hidden, feedIntersecting\);\n\s*if \(!paintReleased\(paintDirty, document\.hidden, feedIntersecting\)\) return;\n\s*paintDirty = false;\n\s*skipFlipOnce = true;\n\s*render\(\);/,
    "the release publishes the pane's word for the kernel's pane shim first (paint-gate.ts publishPaneHidden), then settles the owed paint");
  assert.doesNotMatch(rel, /requestAnimationFrame|setTimeout|queueMicrotask/, "the release paints synchronously: the earliest fresh frame after the compositor's cached one");
});

test("BOTH release events run the same release: visibilitychange→visible and the observer's callback", () => {
  assert.match(SRC, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(!document\.hidden\) releasePaint\(\); \}\);/);
  assert.match(SRC, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(document\.hidden\) publishPaneHidden\(true, feedIntersecting\); \}\);/,
    "the hidden arm releases nothing, so it publishes the pane's word itself");
  assert.equal(SRC.split("publishPaneHidden(").length - 1, 2, "two publish sites, both on the gate's events; no timer");
  const watch = body("watchFeedVisibility");
  assert.match(watch, /new IntersectionObserver\(\(entries\) => \{\n\s*feedIntersecting = entries\.some\(\(e\) => e\.isIntersecting\);\n\s*releasePaint\(\);\n\s*live\.catchUp\(\);[^\n]*\n\s*\}\)\.observe\(list\);/);
  assert.match(watch, /if \(typeof IntersectionObserver === "undefined"\) return;/);
});

test("state is applied whatever the visibility: the payload path and its bookkeeping never look at the gate", () => {
  const apply = body("applyFeedPayload");
  assert.doesNotMatch(apply, /document\.hidden|paintDirty|feedIntersecting|paintHeld/);
  for (const must of ["reconcileFollowMove(", "mirrorBadges(", "clearUndoBusy();", "pendingCleared", "pendingRestored", "reconcilePendingDone("]) {
    assert.ok(apply.includes(must), "applyFeedPayload still runs " + must);
  }
  assert.equal((apply.match(/\brender\(\);/g) || []).length, 1, "ONE gated render");
  assert.match(apply, /\n  render\(\);\n  if \(!feedAnnounced\) \{/, "…followed only by the shell's first-content announcement");
  // the message handler applies live unless a card is hovered/keyed — the hover-freeze holder is not
  // widened into a hidden holder (it withholds the payload itself, which the backstop test above forbids)
  assert.match(SRC, /if \(m\.type === "feed"\) \{[\s\S]*?if \(freezeKey \|\| tabScopeKey\) \{ pendingFeedPayload = m; paintFreezeBadges\(\); return; \}\n\s*applyFeedPayload\(m\);/);
  assert.doesNotMatch(SRC, /freezeKey \|\| tabScopeKey \|\| document\.hidden|document\.hidden \|\| freezeKey/);
});

test("the follow-move backstop yields to a prediction a payload already retired; the payload retires it on `working`", () => {
  const ack = body("ackFollowMove");
  const guard = ack.indexOf('if (!pendingFollowMove.has(itemId)) return;\n    clearFollowMove(itemId, "backstop-noconfirm"); render();');
  assert.ok(guard > 0, "the backstop checks the prediction still stands before it reverts");
  assert.match(body("reconcileFollowMove"), /if \(!a \|\| askColumn\(a\) === "asks" \|\| pendingMoveKind\.get\(id\) === "answer"\) \{\n\s*clearFollowMove\(/);   // Working through askColumn (the boards' phase two)
});

test("a bell jump settles the owed paint on the shell's word before it looks for the card", () => {
  // the shell shows the pane and posts revealCard in the same task, before the observer re-measures
  assert.match(SRC, /if \(m\.romp === "revealCard"\) \{[\s\S]*?if \(paintDirty\) \{ feedIntersecting = true; releasePaint\(\); \}\n\s*const key = "a:" \+ String\(m\.itemId \|\| ""\);/);
});

// ── the wiring, run: feed.ts's own visibility lines over the pure module ──
// The pins above hold the text; this lifts it. The slice from `let feedIntersecting` through render()'s gate (the
// line before `pruneTip();`) is feed.ts's text, transpiled with esbuild at run time and closed with a stand-in for
// the rest of the paint (the chat-exact-tail-exec.test.ts pattern). The page is stood in: `document` (hidden, the
// two visibilitychange listeners, the list by id), `IntersectionObserver` (its callback kept for the test to fire),
// `live` (the age pass's catch-up, counted) and the publisher bound to `host`, the window stand-in the pane's word
// lands on (feed.ts passes two arguments, so the page's window is the host). What the harness above cannot show:
// the word feed.ts publishes, on feed.ts's own events, and nothing before the observer has spoken.
type Cb = (entries: { isIntersecting: boolean }[]) => void;
function feedWiring() {
  const start = "let feedIntersecting: boolean | null = null;", end = "  pruneTip();";
  const a = SRC.indexOf(start), b = SRC.indexOf(end, a);
  assert.ok(a > 0 && b > a, "the wiring's anchors moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(SRC.slice(a, b) + "  paint();\n}\n", { loader: "ts" }).code;
  const st = { hidden: false, model: 0, painted: 0, paints: 0, catchUps: 0, host: {} as PaneHiddenHost };
  const listeners: Array<() => void> = [];
  let observerCb: Cb | null = null;
  const list = { get childElementCount() { return st.painted; } };
  const fakeDocument = {
    get hidden() { return st.hidden; },
    addEventListener(_type: string, fn: () => void) { listeners.push(fn); },
    getElementById(id: string) { return id === "feed-list" ? list : null; },
  };
  class FakeObserver { constructor(cb: Cb) { observerCb = cb; } observe(_target: unknown) {} }
  const prelude = `
    const paintHeld = P.paintHeld, paintReleased = P.paintReleased;
    const publishPaneHidden = (docHidden, intersecting) => P.publishPaneHidden(docHidden, intersecting, S.host);
    const live = { catchUp() { S.catchUps++; } };
    const paint = () => { S.paints++; S.painted = S.model; };
  `;
  const api = new Function("P", "S", "document", "IntersectionObserver", prelude + js + "\nreturn { render, releasePaint };")(
    { paintHeld, paintReleased, publishPaneHidden }, st, fakeDocument, FakeObserver) as { render(): void; releasePaint(): void };
  return {
    st,
    /** the payload path's one gated render() */
    frame(n: number) { st.model = n; api.render(); },
    /** the IntersectionObserver's callback over #feed-list */
    observer(intersecting: boolean) { assert.ok(observerCb, "render() installed the observer"); observerCb!([{ isIntersecting: intersecting }]); },
    /** the tab's visibilitychange: feed.ts's two listeners run, the visible arm's release and the hidden arm's publish */
    tab(state: "hidden" | "visible") { st.hidden = state === "hidden"; for (const fn of listeners) fn(); },
  };
}

test("run: feed.ts's own wiring publishes the pane's word on its events, nothing before the observer's first word, and the hidden arm publishes without a release", () => {
  const f = feedWiring();
  f.frame(1);                                          // the first payload: paints through and installs the observer
  assert.equal(f.st.paints, 1);
  assert.equal(typeof f.st.host.__rompPaneHidden, "undefined", "a frame is not a gate event, and the observer has not spoken: the shim's probe decides");
  f.tab("hidden"); f.tab("visible");
  assert.equal(typeof f.st.host.__rompPaneHidden, "undefined", "a visibilitychange on either arm before the observer's first entry publishes nothing");
  f.observer(true);
  assert.equal(f.st.host.__rompPaneHidden, false, "the observer's first word, published from releasePaint");
  assert.equal(f.st.catchUps, 1, "the observer's callback still runs the age pass's catch-up");
  f.observer(false);                                   // the shell hides the pane after the show; the viewport would still read its size
  assert.equal(f.st.host.__rompPaneHidden, true, "hidden after a first show: the case the probe misses");
  f.frame(2);
  assert.equal(f.st.paints, 1, "the paint is held while the pane is hidden");
  f.tab("hidden");
  assert.equal(f.st.host.__rompPaneHidden, true, "the hidden arm publishes (it releases nothing)");
  f.tab("visible");
  assert.equal(f.st.host.__rompPaneHidden, true, "the tab's return is not a show for a display:none pane");
  assert.equal(f.st.paints, 1, "and nothing paints for it");
  f.observer(true);
  assert.equal(f.st.host.__rompPaneHidden, false, "the re-show publishes on the observer's callback");
  assert.equal(f.st.paints, 2, "and settles the owed paint");
  f.tab("hidden"); assert.equal(f.st.host.__rompPaneHidden, true, "the tab hidden with the pane on screen");
  f.tab("visible"); assert.equal(f.st.host.__rompPaneHidden, false, "the return publishes on visibilitychange");
  assert.equal(typeof f.st.host.__rompPaneHidden, "boolean", "a boolean, the type the shim tests for");
});
