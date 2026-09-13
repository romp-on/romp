// The proto-2 client's DOM-side rules, EXECUTED (T323 stage 4b, review round 3): functions lifted from render.ts by anchor,
// transpiled with esbuild at run time and run over a Proxy scope that answers every free identifier the function reaches
// for with a stub, plus the real chat-window rules where the function uses them. Driven: the paused strip shows for a
// detached proto-2 session and hides for an attached one; the edge check after a window asks for the next page directly
// when a detached run's content does not overflow and runs the viewport check otherwise; renderEvent stamps an orphan
// note's record uuid on its turn; upsert merges a full frame into the held run only when it answers this client's own
// re-attach ask, carrying the merged run's count, and replaces otherwise. Synthetic events only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { mergeWindow, keyOf, fullFrameMerges, windowDetached, afterMore, livePausedText, windowLanding } from "./chat-window";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors moved: ${startAnchor.slice(0, 40)} / ${endAnchor.slice(0, 40)}`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

/** Run lifted code with `scope` as the world: every identifier the code reads that `scope` lacks resolves to a no-op
 *  function (a Proxy behind `with`), so a function can be executed for the ONE rule under test without its whole world. */
function liftWith(js: string, scope: Record<string, unknown>, names: string[]): Record<string, any> {
  const proxy = new Proxy(scope, {
    has: (t, k) => k in t || !(k in globalThis),   // a real global (Math, Map, JSON) stays itself; the rest is the scope's or a stub
    get: (t, k) => (k in t ? (t as any)[k] : (typeof k === "symbol" ? undefined : (() => undefined))),
    set: (t, k, v) => { (t as any)[k] = v; return true; },
  });
  const body = `with (SCOPE) { ${js}\n return { ${names.join(", ")} }; }`;
  return (new Function("SCOPE", body) as (s: unknown) => Record<string, any>)(proxy);
}

function fakeEl(): any {
  const el: any = { hidden: false, style: {}, className: "", id: "", textContent: "", children: [] as any[], dataset: {}, type: "",
                    querySelector: () => null, querySelectorAll: () => [], setAttribute() {}, removeAttribute() {}, addEventListener() {},
                    classList: { add() {}, remove() {}, toggle() {} },
                    appendChild(c: any) { el.children.push(c); return c; }, getBoundingClientRect: () => ({ bottom: 500, top: 0 }) };
  return el;
}

// ── the paused strip ─────────────────────────────────────────────────────────────────────────────
function liftPaused(sessions: Map<string, any>, activeId: string | null) {
  const body: any = fakeEl();
  const document = { body, createElement: () => fakeEl(), getElementById: (id: string) => (id === "content" ? fakeEl() : null) };
  const scope: Record<string, unknown> = {
    sessions, activeId, document, window: { innerHeight: 800 },
    liveSession: (id: string | null) => (id ? sessions.get(id) : undefined),
    el: (_tag: string, cls: string) => { const e = fakeEl(); e.className = cls; return e; },
    livePausedEl: null, livePausedTxt: null,
    livePausedText, clockOf: (t: number) => "clock-" + t,   // the real sentence rule; a stand-in clock (T366)
  };
  const js = liftBetween("function updateLivePaused(): void {", "function reattachLive(sid: string, force = false): void {");
  const api = liftWith(js, scope, ["updateLivePaused"]);
  return { api, scope, body };
}

test("the paused strip shows for a detached proto-2 session and hides for an attached one", () => {
  const sessions = new Map<string, any>([["A", { id: "A", proto: 2, detached: true, events: [] }]]);
  const { api, scope, body } = liftPaused(sessions, "A");
  api.updateLivePaused();
  const strip = (scope as any).livePausedEl;
  assert.ok(strip && strip.id === "live-paused", "the strip is built on first need");
  assert.equal(strip.hidden, false, "…and shown");
  assert.ok(body.children.includes(strip), "…in the document");
  assert.equal(strip.children[0].textContent, "Live updates are paused while you read older history.");
  assert.equal(strip.children[1].textContent, "Return to live");
  sessions.get("A").detached = false;                  // the re-attach frame landed (upsert calls updateLivePaused for the active tab)
  api.updateLivePaused();
  assert.equal(strip.hidden, true, "attached again: hidden");
  sessions.set("B", { id: "B", proto: 2, detached: true, events: [] });
  (scope as any).activeId = "B";                        // a tab switch (setActive calls it after activeId moves)
  api.updateLivePaused();
  assert.equal(strip.hidden, false, "the entering tab's own state shows it");
  (scope as any).activeId = "A";
  api.updateLivePaused();
  assert.equal(strip.hidden, true, "…and back to the attached tab hides it");
});

// ── the edge check after a window ─────────────────────────────────────────────────────────────────
test("a detached run whose content does not overflow asks for its next page directly; an overflowing one runs the viewport check", () => {
  const calls: string[] = [];
  const mk = (scrollHeight: number, clientHeight: number, detached: boolean) => {
    const sessions = new Map<string, any>([["A", { id: "A", proto: 2, detached, events: [] }]]);
    const scope: Record<string, unknown> = {
      sessions, document: { getElementById: (id: string) => (id === "content" ? { scrollHeight, clientHeight } : null) },
      requestNewer: (sid: string) => calls.push("newer:" + sid), virtualizeToViewport: () => calls.push("virt"),
      landSettling: null, afterSettle: [],   // no landing settling (T386): the check runs at once
    };
    const js = liftBetween("function edgeCheckAfterWindow(sid: string): void {", "// ── the detached client's way back");
    return liftWith(js, scope, ["edgeCheckAfterWindow"]);
  };
  mk(300, 600, true).edgeCheckAfterWindow("A");
  assert.deepEqual(calls, ["newer:A"], "fits the viewport and detached: the next page, directly");
  calls.length = 0;
  mk(3000, 600, true).edgeCheckAfterWindow("A");
  assert.deepEqual(calls, ["virt"], "overflows: the scroll-driven check decides at the edges");
  calls.length = 0;
  mk(300, 600, false).edgeCheckAfterWindow("A");
  assert.deepEqual(calls, ["virt"], "attached: nothing to walk to");
});

// ── renderEvent: the orphan note's record uuid ───────────────────────────────────────────────────
test("renderEvent stamps an orphan note's record uuid on its turn beside the note's own key", () => {
  const scope: Record<string, unknown> = {
    renderEventInner: () => fakeEl(), isOptimistic: () => false, eventEpoch: () => 1700000000,
    el: (_t: string, cls: string) => { const e = fakeEl(); e.className = cls; return e; },
  };
  const js = liftBetween("function renderEvent(ev: ChatEvent, prevEpoch?: number | null, worked?: number | null): HTMLElement {", "\nfunction renderEventInner(");
  const api = liftWith(js, scope, ["renderEvent"]);
  const turn = api.renderEvent({ kind: "assistant", uuid: "orphan:1700000000:1", orphaned: true, orphanOf: "rec-9", md: "salvaged" });
  assert.equal(turn.dataset.uuid, "orphan:1700000000:1", "the note's own key lands the walks");
  assert.equal(turn.dataset.orphanOf, "rec-9", "…and the record uuid lands a deep link by the reply's uuid");
  const plain = api.renderEvent({ kind: "assistant", uuid: "a1", md: "hi" });
  assert.equal(plain.dataset.orphanOf, undefined, "an ordinary reply carries none");
});

// ── upsert: merge only on this client's re-attach, with the merged run's count ───────────────────
function liftUpsert(sessions: Map<string, any>, pendingFullWhy: Map<string, string>, activeId: string | null) {
  const scope: Record<string, unknown> = {
    sessions, pendingFullWhy, activeId, awaitingFull: new Set<string>(), skeletonTabs: { ids: new Set() }, tabMeta: new Map(), pendingTabMeta: new Map(),
    emptyFrameDiagSent: new Set(), ledgers: new Map(), views: new Map(),
    document: { getElementById: () => null, createElement: () => fakeEl(), body: fakeEl(), querySelector: () => null },
    window: { innerHeight: 800, requestAnimationFrame: () => 0 },
    keepResidentEvents: () => false, onFull: () => false, hostOf: () => "", mergeWindow, keyOf, fullFrameMerges, stripOptimistic: () => {},
  };
  const js = liftBetween("function upsert(msg: any) {", "\nfunction ");
  return liftWith(js, scope, ["upsert"]);
}

test("upsert merges a proto-2 full frame into the held run only when it answers this client's re-attach ask, and counts the merged run", () => {
  const ev = (u: string) => ({ uuid: u, kind: "user", md: u });
  const held = { id: "A", name: "web", events: [ev("a"), ev("b"), ev("c")], status: { state: "idle" }, proto: 2, headKnown: true, headTotal: 3,
                 firstUuid: "a", lastUuid: "c", detached: true, events0: null };
  // the re-attach answer: merges, the run keeps its older first, the count is the merged run's
  let sessions = new Map<string, any>([["A", { ...held, events: [...held.events] }]]);
  let pending = new Map<string, string>([["A", "reattach"]]);
  let api = liftUpsert(sessions, pending, "A");
  api.upsert({ type: "session", id: "A", name: "web", proto: 2, events: [ev("c"), ev("d")], headKnown: false, headTotal: null, firstUuid: "c", lastUuid: "d", status: { state: "idle" } });
  let s = sessions.get("A");
  assert.deepEqual(s.events.map((e: any) => e.uuid), ["a", "b", "c", "d"], "merged into one run");
  assert.equal(s.firstUuid, "a", "the run's older first edge stands");
  assert.equal(s.lastUuid, "d"); assert.equal(s.detached, false, "the re-attach frame attaches");
  assert.equal(s.headKnown, true, "the head the run knew stays known");
  assert.equal(s.headTotal, 4, "the merged run's own count (round 2, item 9)");
  assert.equal(pending.has("A"), false, "the reason is consumed by its answer");
  // a change-driven full frame (no ask of this client's): replaces, its in-list events the fresh copies
  sessions = new Map<string, any>([["A", { ...held, events: [...held.events], detached: false }]]);
  pending = new Map<string, string>();
  api = liftUpsert(sessions, pending, "A");
  api.upsert({ type: "session", id: "A", name: "web", proto: 2, events: [ev("c"), ev("d")], headKnown: false, headTotal: null, firstUuid: "c", lastUuid: "d", status: { state: "idle" } });
  s = sessions.get("A");
  assert.deepEqual(s.events.map((e: any) => e.uuid), ["c", "d"], "replaced");
  assert.equal(s.firstUuid, "c"); assert.equal(s.headKnown, false); assert.equal(s.headTotal, null, "no count while the head is unknown");
  // a gap's answer replaces too (the client's copy was inconsistent)
  sessions = new Map<string, any>([["A", { ...held, events: [...held.events], detached: false }]]);
  pending = new Map<string, string>([["A", "gap"]]);
  api = liftUpsert(sessions, pending, "A");
  api.upsert({ type: "session", id: "A", name: "web", proto: 2, events: [ev("c"), ev("d")], headKnown: false, headTotal: null, firstUuid: "c", lastUuid: "d", status: { state: "idle" } });
  assert.deepEqual(sessions.get("A").events.map((e: any) => e.uuid), ["c", "d"]);
  // the three pure rules the frames route through, once more beside the DOM paths
  assert.equal(windowDetached(true, false, true, "merge", "w", "w"), true);
  assert.deepEqual(afterMore(false, true, 4), { detached: false, headTotal: 4 });
});

// ── the window ask's mark (T366, verifier medium 1) ───────────────────────────────────────────────────────────────────
// requestAround marks each ask with whether a NAVIGATION made it. The one ask that is no navigation is the keep-offset
// re-land of the reader's own row across a rebuild (relandAsk, raised only around keepPlaceAcrossWindow's landing). The
// page-reload restore of a reader's saved place arms the same keep offset, so a rule that read the keep offset refused
// the restore's window too and a reader reloaded while reading older history lost their place: that ask must land.
function liftAsk(relandAsk: boolean, keepY: number | null, anchorT: number | null, kind: string | null) {
  const rows: any[] = [], posted: any[] = [];
  const scope: Record<string, unknown> = {
    sessions: new Map<string, any>([["A", { id: "A", proto: 2, detached: false, events: [] }]]),
    loadingOlder: new Set<string>(), relandAsk, pendingAnchorKeepY: keepY, pendingAnchorT: anchorT, pendingAnchorKind: kind, pendingAnchorIntent: null,
    document: { getElementById: () => null }, atBottom: () => false, landTrail: ["pointer-fetch-window"],
    scrollDiagRow: (k: string, d: any) => rows.push({ k, d }), pendingOlderAnchor: new Map(), pendingOlderKeepY: new Map(),
    showLoadingPill: () => undefined, vscodeApi: { postMessage: (m: any) => posted.push(m) },
  };
  const js = liftBetween("const pendingWindowNav = new Map<string, { nav: boolean; named: boolean; t: number | null; kind: string | null }>();", "// The page after a DETACHED window's newest event");
  const api = liftWith(js, scope, ["requestAround", "pendingWindowNav"]);
  return { api, rows, posted };
}

test("the reload restore's window ask (a keep offset, no re-land) is a navigation and lands; the re-land's is refused; a card's carries its time (T366)", () => {
  const restore = liftAsk(false, 12, null, null);
  assert.equal(restore.api.requestAround("A", "u1"), true);
  assert.deepEqual(restore.api.pendingWindowNav.get("A"), { nav: true, named: false, t: null, kind: null }, "the reader's saved place is theirs to get back: a navigation, and no click (the plain strip sentence)");
  assert.equal(windowLanding(true, true, restore.api.pendingWindowNav.get("A").nav), "detach", "…so a detaching window lands (no forced re-attach)");
  const reland = liftAsk(true, 12, null, null);
  reland.api.requestAround("A", "u2");
  assert.deepEqual(reland.api.pendingWindowNav.get("A"), { nav: false, named: false, t: null, kind: null }, "the re-land of the reader's own row is the one ask that is no navigation");
  assert.equal(windowLanding(true, true, reland.api.pendingWindowNav.get("A").nav), "reattach", "…so its detaching window is refused and the client re-attached");
  const card = liftAsk(false, null, 1700000000, null);
  card.api.requestAround("A", "u3");
  assert.deepEqual(card.api.pendingWindowNav.get("A"), { nav: true, named: true, t: 1700000000, kind: null }, "a card's or lane's frame carried the message's time: named, and the strip says the clock; the time rides to the adoption (T386)");
  const kindOnly = liftAsk(false, null, null, "prompt");
  kindOnly.api.requestAround("A", "u4");
  assert.deepEqual(kindOnly.api.pendingWindowNav.get("A"), { nav: true, named: true, t: null, kind: "prompt" }, "a kind without a time: named, and the strip says the message was opened without a clock; the kind rides to the adoption (T386)");
  const notch = liftAsk(false, null, null, null);
  notch.api.requestAround("A", "u5");
  assert.deepEqual(notch.api.pendingWindowNav.get("A"), { nav: true, named: true, t: null, kind: null }, "a notch, a reply chip or a comment tick arm neither kind, time nor keep offset: still a click the strip names (verifier low, round two)");
  assert.equal(restore.rows.length, 1); assert.equal(restore.rows[0].k, "windowask");
  assert.deepEqual({ nav: restore.rows[0].d.nav, keep: restore.rows[0].d.keep, reland: restore.rows[0].d.reland, trail: restore.rows[0].d.trail }, { nav: true, keep: true, reland: false, trail: ["pointer-fetch-window"] }, "the ask's diagnostic row names the keep offset and the re-land flag apart, under the scroll rows' budget");
  assert.deepEqual(restore.posted, [{ type: "loadAround", id: "A", uuid: "u1" }], "the ask itself goes out after the mark");
});
