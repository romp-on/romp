// The feed pane's per-card update gate, RUN: feed.ts booted under a DOM stand-in, fed synthetic frames through
// the window message it listens on, and watched for what each render REBUILT. The invariant these frames pin:
// a card repaints when the kernel sent it (a new object), when a board-level input it reads changed (the
// key), or when a gesture touched it; its column and order are re-applied on every render regardless; and
// the 15 s live pass moves its ages and durations on the kernel's clock, writing only the labels whose text
// changed. Two of the assertions are the regressions the paint key this gate replaced would
// fail: a re-dispatch of the same objects across a 15 s boundary rebuilds nothing (the key carried a
// fifteen-second clock term, so the first frame of every window repainted every card), and one session's
// `working` change repaints that session's cards alone (the key carried an epoch every status-set change
// bumped, so every card repainted). The last cases pin what the gate leaves to the animations themselves,
// now that no per-render className rewrite strips their classes: a fly ends on its own event or a backstop,
// skips a folded column's zero rect at either end, and one fly owns a card at a time; a reveal pulse ends;
// a session header's name nodes are minted only when what they show changes.
//
// The stand-in is the tree of plain objects the Outline pane's live-clock test boots its bundle under, grown to
// what feed.ts's boot and render paths touch: a small selector engine (descendant chains, classes, ids, attribute presence and
// equality; every pseudo-class, :hover included, matches nothing), insertBefore/sibling walks, dataset-backed
// data-* attributes, a style object with custom properties, EventTarget elements, and counters for the
// writes the gate is about (replaceChildren on a card's name node, textContent sets, getBoundingClientRect
// reads, scrollTop writes). gear.js's initGear returns at once when #rsettings already exists, so the
// settings modal never mounts. Synthetic only: the notes-api demo world, placeholder sids, hostname TESTHOST.
import { test, mock, after } from "node:test";
import * as assert from "node:assert/strict";

// ── a DOM stand-in ─────────────────────────────────────────────────────────────────────────────────
class Style {
  [key: string]: any;
  private props = new Map<string, string>();
  setProperty(k: string, v: string): void { this.props.set(k, v); }
  removeProperty(k: string): void { this.props.delete(k); }
  getPropertyValue(k: string): string { return this.props.get(k) ?? ""; }
}
class Txt {
  nodeType = 3;
  parentNode: El | null = null;
  constructor(public textContent: string) {}
  get nextSibling(): El | Txt | null { return sib(this, 1); }
  remove(): void { this.parentNode?.removeChild(this); }
}
function sib(n: El | Txt, d: number): El | Txt | null {
  const p = n.parentNode; if (!p) return null;
  const i = p.childNodes.indexOf(n); return p.childNodes[i + d] ?? null;
}
const camel = (s: string) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
type Compound = { tag: string | null; id: string | null; classes: string[]; attrs: { name: string; value: string | null }[]; pseudo: boolean };
function parseCompound(s: string): Compound {
  const c: Compound = { tag: null, id: null, classes: [], attrs: [], pseudo: false };
  const m = /^([a-zA-Z][\w-]*)?(.*)$/.exec(s)!;
  c.tag = m[1] ? m[1].toUpperCase() : null;
  const re = /\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]|:[\w-]+(?:\([^)]*\))?/g;
  let t: RegExpExecArray | null;
  while ((t = re.exec(m[2]))) {
    if (t[1]) c.classes.push(t[1]);
    else if (t[2]) c.id = t[2];
    else if (t[3]) c.attrs.push({ name: t[3], value: t[4] ?? null });
    else c.pseudo = true;
  }
  return c;
}
class El extends EventTarget {
  nodeType = 1;
  id = ""; title = ""; hidden = false; value = ""; type = ""; checked = false; disabled = false;
  offsetWidth = 0; offsetHeight = 0; clientWidth = 800; clientHeight = 600; isContentEditable = false;
  onclick: ((ev: any) => void) | null = null;
  parentNode: El | null = null;
  childNodes: Array<El | Txt> = [];
  dataset: Record<string, string | undefined> = {};
  style = new Style();
  rc = 0;                                   // replaceChildren calls (the name nodes' rebuild)
  tc = 0;                                   // textContent sets
  ac = 0;                                   // setAttribute calls (a same-value write still queues a mutation record in a browser)
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
  private _html = "";
  private _scrollTop = 0;
  classList = {
    add: (...c: string[]) => { for (const x of c) this.classes.add(x); },
    remove: (...c: string[]) => { for (const x of c) this.classes.delete(x); },
    toggle: (c: string, force?: boolean) => {
      const on = force === undefined ? !this.classes.has(c) : force;
      if (on) this.classes.add(c); else this.classes.delete(c);
      return on;
    },
    contains: (c: string) => this.classes.has(c),
  };
  constructor(public tagName: string) { super(); this.tagName = tagName.toUpperCase(); }
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get textContent(): string { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v: string | null) { this.tc++; this.detachAll(); if (v !== null && v !== "") this.appendChild(new Txt(String(v))); }
  get innerHTML(): string { return this._html; }
  set innerHTML(v: string) { this.detachAll(); this._html = v; }
  get scrollTop(): number { return this._scrollTop; }
  set scrollTop(v: number) { scrollWrites.push(v); this._scrollTop = v; }
  get children(): El[] { return this.childNodes.filter((c): c is El => c instanceof El); }
  get firstChild(): El | Txt | null { return this.childNodes[0] ?? null; }
  get nextSibling(): El | Txt | null { return sib(this, 1); }
  get parentElement(): El | null { return this.parentNode; }
  get previousElementSibling(): El | null { for (let n = sib(this, -1); n; n = sib(n, -1)) if (n instanceof El) return n; return null; }
  get nextElementSibling(): El | null { for (let n = sib(this, 1); n; n = sib(n, 1)) if (n instanceof El) return n; return null; }
  get isConnected(): boolean { return this === body || body.contains(this); }
  private detachAll(): void { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; }
  private adopt(c: El | Txt | string): El | Txt { const n = typeof c === "string" ? new Txt(c) : c; n.parentNode?.removeChild(n); n.parentNode = this; return n; }
  appendChild<T extends El | Txt>(c: T): T { this.childNodes.push(this.adopt(c) as T); return c; }
  append(...cs: Array<El | Txt | string>): void { for (const c of cs) this.childNodes.push(this.adopt(c)); }
  prepend(...cs: Array<El | Txt | string>): void { this.childNodes.unshift(...cs.map((c) => this.adopt(c))); }
  replaceChildren(...cs: Array<El | Txt | string>): void { this.rc++; this.detachAll(); this.append(...cs); }
  insertBefore<T extends El | Txt>(node: T, ref: El | Txt | null): T {
    const n = this.adopt(node);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return node;
  }
  removeChild(c: El | Txt): void { const i = this.childNodes.indexOf(c); if (i >= 0) { this.childNodes.splice(i, 1); c.parentNode = null; } }
  remove(): void { this.parentNode?.removeChild(this); }
  after(...cs: Array<El | Txt | string>): void { const p = this.parentNode; if (!p) return; const ref = sib(this, 1); for (const c of cs) p.insertBefore(typeof c === "string" ? new Txt(c) : c, ref); }
  before(...cs: Array<El | Txt | string>): void { const p = this.parentNode; if (!p) return; for (const c of cs) p.insertBefore(typeof c === "string" ? new Txt(c) : c, this); }
  replaceWith(c: El | Txt): void { const p = this.parentNode; if (!p) return; p.insertBefore(c, this); this.remove(); }
  get firstElementChild(): El | null { return this.children[0] ?? null; }
  get lastElementChild(): El | null { const c = this.children; return c[c.length - 1] ?? null; }
  get lastChild(): El | Txt | null { return this.childNodes[this.childNodes.length - 1] ?? null; }
  get childElementCount(): number { return this.children.length; }
  contains(x: El | Txt | null): boolean { for (let n: El | Txt | null = x; n; n = n.parentNode) if (n === this) return true; return false; }
  setAttribute(k: string, v: string): void { this.ac++; this.attrs.set(k, String(v)); if (k.startsWith("data-")) this.dataset[camel(k.slice(5))] = String(v); if (k === "id") this.id = v; }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? (k.startsWith("data-") ? this.dataset[camel(k.slice(5))] ?? null : k === "title" && this.title ? this.title : null); }
  hasAttribute(k: string): boolean { return this.getAttribute(k) !== null; }
  removeAttribute(k: string): void { this.attrs.delete(k); if (k === "title") this.title = ""; }
  getBoundingClientRect() {
    // a rendered element gets a rect from its place: a column's cards stack 100 px apart and each column sits
    // at its own left, so a card that changed column or slot has a different rect and one that did not has
    // the same; anything hidden (display:none on it or an ancestor) or detached is a zero rect, as in a browser
    rectReads++;
    for (let n: El | null = this; n; n = n.parentNode) if (n.style.display === "none") return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    if (!this.isConnected) return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    const p = this.parentNode!;
    const top = p.children.indexOf(this) * 100, left = p.id.length * 10;
    return { left, top, right: left + 300, bottom: top + 90, width: 300, height: 90 };
  }
  scrollIntoView(): void {}
  focus(): void {}
  blur(): void {}
  matchesCompound(c: Compound): boolean {
    if (c.pseudo) return false;
    if (c.tag && c.tag !== this.tagName) return false;
    if (c.id && c.id !== this.id) return false;
    for (const k of c.classes) if (!this.classes.has(k)) return false;
    for (const a of c.attrs) { const v = this.getAttribute(a.name); if (v === null) return false; if (a.value !== null && v !== a.value) return false; }
    return true;
  }
  matches(sel: string): boolean {
    return sel.split(",").some((one) => {
      const parts = one.trim().split(/\s+/).map(parseCompound);
      if (!this.matchesCompound(parts[parts.length - 1])) return false;
      let anc: El | null = this.parentNode;
      for (let i = parts.length - 2; i >= 0; i--) {
        while (anc && !anc.matchesCompound(parts[i])) anc = anc.parentNode;
        if (!anc) return false;
        anc = anc.parentNode;
      }
      return true;
    });
  }
  closest(sel: string): El | null { for (let n: El | null = this; n; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel: string): El[] { return [...this.walk()].filter((e) => e.matches(sel)); }
  querySelector(sel: string): El | null { for (const e of this.walk()) if (e.matches(sel)) return e; return null; }
  *walk(): Generator<El> { for (const c of this.childNodes) if (c instanceof El) { yield c; yield* c.walk(); } }
  byId(id: string): El | null { for (const e of this.walk()) if (e.id === id) return e; return null; }
}
let rectReads = 0;
const scrollWrites: number[] = [];
const posted: any[] = [];
const body = new El("body");
const head = new El("div"); head.id = "feed-head";
const list = new El("div"); list.id = "feed-list";
const foot = new El("div"); foot.id = "feed-foot";
const gearGuard = new El("div"); gearGuard.id = "rsettings";   // initGear's idempotence check: present → the modal never mounts
body.append(head, list, foot, gearGuard);
const stores = { local: new Map<string, string>(), session: new Map<string, string>() };
const storage = (m: Map<string, string>) => ({
  getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
  setItem: (k: string, v: string) => { m.set(k, String(v)); },
  removeItem: (k: string) => { m.delete(k); },
});
const win: any = new EventTarget();
win.parent = win; win.top = win;
win.location = { hash: "", search: "", protocol: "http:" };
win.innerWidth = 1200; win.innerHeight = 800;
win.setTimeout = (...a: Parameters<typeof setTimeout>) => setTimeout(...a);
win.clearTimeout = (t: ReturnType<typeof setTimeout>) => clearTimeout(t);
win.setInterval = (...a: Parameters<typeof setInterval>) => setInterval(...a);
win.requestAnimationFrame = (cb: () => void) => setTimeout(cb, 0);
win.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
win.getComputedStyle = () => ({ flexDirection: "row", order: "0" });
win.postMessage = () => {};
win.acquireVsCodeApi = () => ({ postMessage: (m: any) => posted.push(m) });
(globalThis as any).window = win;
(globalThis as any).requestAnimationFrame = win.requestAnimationFrame;
(globalThis as any).getComputedStyle = win.getComputedStyle;
(globalThis as any).MouseEvent = class MouseEvent extends Event { clientX = 0; clientY = 0; };
// the paint gate's second measure: render() observes #feed-list once; a test drives the callback by hand
const observers: { cb: (entries: { isIntersecting: boolean }[]) => void }[] = [];
(globalThis as any).IntersectionObserver = class { cb: any; constructor(cb: any) { this.cb = cb; observers.push(this); } observe() {} disconnect() {} };
const doc: any = new EventTarget();
Object.assign(doc, {
  body, head: new El("head"), documentElement: new El("html"), hidden: false, activeElement: body,
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => new Txt(s),
  getElementById: (id: string) => body.byId(id),
  querySelectorAll: (sel: string) => body.querySelectorAll(sel),
  querySelector: (sel: string) => body.querySelector(sel),
  contains: (x: El) => body.contains(x),
});
(globalThis as any).document = doc;
(globalThis as any).localStorage = storage(stores.local);
(globalThis as any).sessionStorage = storage(stores.session);

// ── the world: three sessions of a notes-api project, three cards ─────────────────────────────────
const T0 = 1781100000;                      // the browser clock at boot
const K0 = T0 - 300;                        // the kernel clock, five minutes behind — the age label follows it
const WEB = "11111111-2222-3333-4444-555555555555", API = "11111111-2222-3333-4444-666666666666", TESTS = "11111111-2222-3333-4444-777777777777";
const node = (id: string, text: string, who: string, whoSid: string, children: string[] = []) =>
  ({ id, kind: "ask", text, who, whoSid, whoColor: null, status: "open", t: K0 - 240, last: K0 - 240, children });
const cardOf = (itemId: string, sid: string, name: string, bg: string, text: string, column: string, extra: Record<string, unknown> = {}) => ({
  itemId, sid, name, color: { bg, fg: "#ffffff" }, text, t: K0 - 240, trgb: [96, 128, 160], live: true, turnId: "turn-" + itemId, column,
  summary: null, blockSummary: null, tree: [node(itemId, text, name, sid)], ...extra,
});
const g1 = cardOf("g1", WEB, "web", "#3366cc", "Wire the notes-api health route", "working",
  { tree: [node("g1", "Wire the notes-api health route", "web", WEB, ["g1a"]), node("g1a", "Add the /health handler", "web", WEB)] });
const g2 = cardOf("g2", API, "api", "#cc6633", "Write the notes-api README", "working");
const g3 = cardOf("g3", TESTS, "tests", "#33cc66", "Run the notes-api test suite", "working",
  { awaiting: { why: "", kind: "agents", count: 2, since: K0 - 600 } });   // a wait ten minutes old: its duration must move without a frame
// the frame shape: the kernel's `now` and federation's `nowAt` (when the frame landed, the pane's clock anchor),
// per-card `trgb`, the status sets, the session order and list
const frame = (asks: any[], over: Record<string, unknown> = {}) => ({
  type: "feed", now: K0, nowAt: T0 * 1000, buildId: 1, asks,
  working: [], awaiting: [], stateUnknown: [], order: [WEB, API, TESTS], selfHost: "TESTHOST",
  sessions: [{ sid: WEB, name: "web", color: g1.color }, { sid: API, name: "api", color: g2.color }, { sid: TESTS, name: "tests", color: g3.color }],
  bgServices: {}, ...over,
});
const listenerErrors: Error[] = [];
process.on("uncaughtException", (e) => { listenerErrors.push(e); });
const settle = () => new Promise<void>((r) => setImmediate(r));   // let a deferred listener exception land before the assertions
const dispatch = async (f: any) => {
  win.dispatchEvent(new MessageEvent("message", { data: f }));
  await settle();
  if (listenerErrors.length) { const es = listenerErrors.splice(0); throw new Error("a listener threw: " + es.map((e) => e.stack || e.message).join("\n---\n")); }
};
after(() => { assert.deepEqual(listenerErrors.map((e) => e.stack || e.message), [], "no listener threw outside a dispatch (a gesture handler, a timer)"); });
const card = (id: string): any => body.querySelector(`[data-key="a:${id}"]`);
const colOf = (id: string) => card(id)?.parentNode?.id;
const nameRebuilds = () => ({ g1: card("g1")._name.rc, g2: card("g2")._name.rc, g3: card("g3")._name.rc });
const ev = { stopPropagation() {}, preventDefault() {} };

test("frame A: three cards are built once each, in the Working column", async () => {
  mock.timers.enable({ apis: ["Date", "setTimeout", "setInterval"], now: T0 * 1000 });
  await import("./feed");                   // module load: gear (a no-op here), listeners, the 15 s live pass
  assert.equal(posted.filter((m) => m.type === "ready").length, 1, "the ready handshake");
  await dispatch(frame([g1, g2, g3]));
  assert.deepEqual(nameRebuilds(), { g1: 1, g2: 1, g3: 1 }, "each name node was built exactly once");
  assert.deepEqual({ g1: colOf("g1"), g2: colOf("g2"), g3: colOf("g3") }, { g1: "col-asks-list", g2: "col-asks-list", g3: "col-asks-list" });
  assert.equal(card("g1")._time.textContent, "4m ago", "on the kernel's clock (the browser clock is five minutes ahead)");
  assert.equal(card("g3")._awaitWhy.textContent, "Awaiting agents · 10m", "the awaiting box, with the wait's duration on the kernel's clock");
  assert.equal(card("g3")._awaitWhy.querySelector(".fask-dur")?.dataset.ageFmt, "dur", "…as a stamped element the live pass can reach");
  // open card 1's Sub-goals section (a gesture): the section state must survive the renders below untouched
  card("g1")._subBtn.onclick(ev);
  assert.equal(card("g1")._subBtn.getAttribute("aria-pressed"), "true");
  assert.equal(card("g1")._checklist.children.length, 1, "one sub-goal row");
});

test("frame B: only the card whose object changed repaints; it moves column; the other cards keep their DOM and their open section", async () => {
  const row = card("g1")._checklist.children[0];
  const readsBefore = rectReads;
  list.scrollTop = 120; scrollWrites.length = 0;
  const g2done = { ...g2, column: "completed" };
  await dispatch(frame([g1, g2done, g3]));
  assert.deepEqual(nameRebuilds(), { g1: 1, g2: 2, g3: 1 }, "exactly one name rebuild: the re-sent card");
  assert.equal(colOf("g2"), "col-completed-list", "the changed card moved to Completed");
  assert.equal(colOf("g1"), "col-asks-list"); assert.equal(colOf("g3"), "col-asks-list");
  assert.equal(body.byId("col-asks-count")!.textContent, "2"); assert.equal(body.byId("col-completed-count")!.textContent, "1");
  assert.equal(card("g1")._subBtn.getAttribute("aria-pressed"), "true", "the open section stayed open");
  assert.equal(card("g1")._checklist.children[0], row, "…and its rows are the same nodes, not a rebuild");
  assert.equal(scrollWrites[scrollWrites.length - 1], 120, "the scroll position is restored");
  assert.ok(rectReads > readsBefore, "a column changed, so the FLIP passes read rects");
  assert.ok(card("g2").classList.contains("fitem-flying"), "the card that crossed columns flies in the back layer");
  assert.match(card("g2").style.transform, /^translate\(/, "…inverted to its old spot first");
});

test("frame C: the same frame re-dispatched (a federation re-emit) rebuilds nothing and reads no rects", async () => {
  // frame B emptied api's run in Working, so its session header left as a ghost (re-keyed x:N until its exit
  // animation ends, or the 600 ms backstop) and g2 flew to Completed; 700 ms later both backstops have fired:
  // no transition ends under the stand-in, so the fly's own backstop is what takes the card out of the back
  // layer (before it, a card whose transitionend never came kept pointer-events:none until its next repaint)
  // (two ticks: a timer created inside a mock tick is stamped with the tick's END time, so one 700 ms tick
  // would run the nested animation frame AFTER the 650 ms backstop — the reverse of a browser's order)
  mock.timers.tick(20);
  assert.equal(card("g2").style.transform, "translate(0, 0)", "the frame after the invert releases the offset");
  mock.timers.tick(680);
  assert.equal(body.querySelectorAll(".sess-exit").length, 0, "the exited header is gone");
  assert.ok(!card("g2").classList.contains("fitem-flying"), "the fly ended by its backstop");
  assert.equal(card("g2").style.transform, "", "…and the card is back in normal flow");
  await dispatch(frame([g1, { ...g2, column: "completed" }, g3]));   // a fresh copy of g2 IS a new object: it repaints — that is the contract
  assert.deepEqual(nameRebuilds(), { g1: 1, g2: 3, g3: 1 });
  const readsBefore = rectReads;
  const tcBefore = { g1: card("g1")._title.tc, g2: card("g2")._title.tc, g3: card("g3")._title.tc };
  const same = frame([g1, card("g2")._it, g3]);              // now the very objects the cards were painted from
  await dispatch(same);
  await dispatch(same);
  assert.deepEqual(nameRebuilds(), { g1: 1, g2: 3, g3: 1 }, "zero rebuilds on identical objects under identical inputs");
  assert.deepEqual({ g1: card("g1")._title.tc, g2: card("g2")._title.tc, g3: card("g3")._title.tc }, tcBefore,
    "no title text was rewritten either");
  assert.equal(rectReads, readsBefore, "no card changed column or place: the FLIP passes read nothing");
});

test("…and across a 15 s boundary: the clock is not a paint input", async () => {
  // the key this gate replaced carried a fifteen-second term, so the first frame in every 15 s window
  // repainted every card. The live pass that fires inside this window moves the labels whose minute rolled
  // over (its own business, pinned below); it rebuilds no card.
  const before = nameRebuilds(), readsBefore = rectReads;
  const same = frame([g1, card("g2")._it, g3]);
  mock.timers.tick(16_000);
  await dispatch(same);
  assert.deepEqual(nameRebuilds(), before, "the same objects across a 15 s boundary: zero rebuilds");
  assert.equal(rectReads, readsBefore);
});

test("frame D: `working` naming card 1's session repaints card 1 (its dot) and leaves the other sessions' cards alone", async () => {
  // the key this gate replaced carried an epoch every status-set change bumped: every card repainted
  const g2now = card("g2")._it;
  await dispatch(frame([g1, g2now, g3], { working: ["web"] }));
  assert.deepEqual(nameRebuilds(), { g1: 2, g2: 3, g3: 1 }, "the key changed for web's card only");
  assert.ok(card("g1")._name.previousElementSibling?.classList.contains("fwork-dot"), "the working dot sits before the name");
  await dispatch(frame([g1, g2now, g3], { working: ["web"] }));
  assert.deepEqual(nameRebuilds(), { g1: 2, g2: 3, g3: 1 }, "…and the same inputs again change nothing");
});

test("column placement and the order walk are not gated: a changed session order moves the cards, and no card repaints", async () => {
  const same = frame([g1, card("g2")._it, g3], { working: ["web"] });
  await dispatch(same);                                          // settle on the objects the cards were painted from
  const before = nameRebuilds();
  const keys = () => body.byId("col-asks-list")!.children.map((c) => c.dataset.key).filter((k) => k && k.startsWith("a:"));
  assert.deepEqual(keys(), ["a:g1", "a:g3"], "web's run, then tests' — the kernel's session order");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"], order: [TESTS, API, WEB] }));
  assert.deepEqual(keys(), ["a:g3", "a:g1"], "the runs swapped places");
  assert.deepEqual(nameRebuilds(), before, "…and no card repainted: a card's column and slot are re-applied every render, outside the gate");
  await dispatch(same);
  assert.deepEqual(keys(), ["a:g1", "a:g3"]);
  assert.deepEqual(nameRebuilds(), before);
});

test("Revive latches on the click and re-arms on the kernel's reviveFailed for the revived session, its idle label restored and the reason toasted; a re-emit, another session's failure and an err for a different request leave the latch", async () => {
  // the kernel's parked-handoff card (build_feed): its sid IS the recipient the button revives (sid and blocked.toSid
  // are both the parked message's toId), so the kernel's reviveFailed, keyed by the revived id, names this card
  const p1 = cardOf("parked:m1", API, "api", "#cc6633", "Hand-off parked for api (offline)", "needs_input",
    { live: false, tree: [], blocked: { state: "parkedHandoff", toSid: API, toName: "api", what: "a handoff from web is parked: revive api to deliver it" } });
  await dispatch(frame([g1, card("g2")._it, g3, p1], { working: ["web"] }));
  const revive = card("parked:m1")._revive;
  assert.equal(revive.style.display, ""); assert.equal(revive.disabled, false); assert.equal(revive.textContent, "Revive api");
  const sent = posted.length;
  revive.onclick(ev);
  assert.deepEqual(posted.slice(sent).filter((m) => m.type === "reviveSession"), [{ type: "reviveSession", id: API }]);
  assert.equal(revive.disabled, true); assert.equal(revive.textContent, "Reviving…");
  await dispatch(frame([g1, card("g2")._it, g3, p1], { working: ["web"] }));   // the same objects again: nothing decided
  assert.equal(revive.disabled, true, "a re-emit is not a deciding event");
  const toastBefore = body.querySelector(".feed-toast")?.textContent ?? null;
  await dispatch({ type: "reviveFailed", id: WEB, name: "web", text: "the SDK backend could not resume it" });
  assert.equal(revive.disabled, true, "another session's revive failing is not this card's event");
  assert.equal(body.querySelector(".feed-toast")?.textContent ?? null, toastBefore, "…and nothing to say about it here");
  await dispatch({ type: "err", sid: API, op: "sendMessage", title: "That message was not delivered", text: "Nothing was sent." });
  assert.equal(revive.disabled, true, "a refusal of a DIFFERENT request on the same session is not this button's reply");
  await dispatch({ type: "reviveFailed", id: API, name: "api", text: "the SDK backend could not resume it (see the kernel log)" });
  assert.equal(revive.disabled, false, "the kernel's reply to the revive this page asked for re-arms it");
  assert.equal(revive.textContent, "Revive api", "…with the label it wore before the click");
  assert.equal(body.querySelector(".feed-toast")?.textContent, "Couldn't revive api: the SDK backend could not resume it (see the kernel log)",
    "…and says why here, where the button is (the chat pane shows the same failure in the session's own pane)");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));   // delivered or dismissed elsewhere: the parked card leaves
  assert.ok(!card("parked:m1"));
});

test("the bell: a click acknowledges at once and its optimistic state is a paint input, so that card alone repaints on the next frame; a refused toggle releases the latch and repaints that card alone", async () => {
  const before = nameRebuilds();
  const bell = card("g1")._bell;
  assert.equal(bell._bellOn, false);
  const sent = posted.length;
  bell.onclick(ev);
  assert.equal(bell._bellOn, true, "acknowledged before the round-trip");
  assert.deepEqual(posted.slice(sent).filter((m) => m.type === "cardNotify"), [{ type: "cardNotify", itemId: "g1", sid: WEB, value: true }]);
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));   // the same objects: the latch rides the key
  assert.equal(bell._bellOn, true, "the payload does not carry it yet; the latch holds");
  assert.deepEqual(nameRebuilds(), { ...before, g1: before.g1 + 1 }, "web's card repainted under its new key, the others did not");
  await dispatch({ type: "settingRefused", gesture: "bell", itemId: "g1", sid: WEB, text: "the settings store could not be read" });
  assert.equal(bell._bellOn, false, "the refusal ends the optimistic state: the bell shows what the payload holds");
  assert.deepEqual(nameRebuilds(), { ...before, g1: before.g1 + 2 }, "…by repainting that card, and no other");
});

test("the grouped pref is a paint input through feedPrefs: grouping off repaints every card once (the name row shows, the headers leave), and the same inputs again repaint nothing", async () => {
  const before = nameRebuilds();
  const nameRow = (id: string) => card(id)._name.parentNode.style.display;
  const heads = () => body.querySelectorAll(".feed-sess-head").filter((h) => !h.classList.contains("sess-exit")).length;
  assert.equal(nameRow("g1"), "none", "grouped: the session header carries the name"); assert.ok(heads() > 0);
  const setPrefs = (v: string) => { stores.local.set("romp:settings", v); win.dispatchEvent(Object.assign(new Event("storage"), { key: "romp:settings", newValue: v })); };
  setPrefs(JSON.stringify({ grouped: false }));                  // the gear in another pane
  assert.deepEqual(nameRebuilds(), { g1: before.g1 + 1, g2: before.g2 + 1, g3: before.g3 + 1 }, "every card reads the pref: each repainted once");
  assert.deepEqual([nameRow("g1"), nameRow("g2"), nameRow("g3")], ["", "", ""], "each card carries its own name again");
  assert.equal(heads(), 0, "no session headers");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));
  assert.deepEqual(nameRebuilds(), { g1: before.g1 + 1, g2: before.g2 + 1, g3: before.g3 + 1 }, "the same inputs again: nothing repainted");
  setPrefs("{}");
  assert.equal(nameRow("g1"), "none");
  assert.deepEqual(nameRebuilds(), { g1: before.g1 + 2, g2: before.g2 + 2, g3: before.g3 + 2 });
});

test("a quarantine card never skips: its sender's colour, looked up by name in the per-frame colour map, follows a re-coloured sender card although its own object is unchanged", async () => {
  const q1 = cardOf("q1", WEB, "web", "#3366cc", "New message", "needs_input",
    { blocked: { state: "quarantine", mid: "m1", frm: "api", origin: "", to: "web", body: "the README draft is ready for a look", gist: "the README draft is ready" } });
  await dispatch(frame([g1, card("g2")._it, g3, q1], { working: ["web"] }));
  const sender = () => card("q1")._qBody.querySelectorAll(".fq-name")[0];
  assert.equal(sender().textContent, "api");
  assert.equal(sender().style.color, card("g2")._it.color.bg, "the sender's identity colour, read from api's card");
  const recoloured = { ...card("g2")._it, color: { bg: "#112233", fg: "#ffffff" } };
  await dispatch(frame([g1, recoloured, g3, q1], { working: ["web"] }));   // q1 is the very same object
  assert.equal(sender().style.color, "#112233", "the held-mail card repainted although nothing of its own changed");
  await dispatch(frame([g1, { ...recoloured, color: g2.color }, g3], { working: ["web"] }));   // decided elsewhere; api's colour as before
  assert.ok(!card("q1"));
});

test("Approve and Deny latch on the click and re-arm on the kernel's quarantineRefused for that held message, the reason toasted; another message's refusal leaves them", async () => {
  const q1 = cardOf("q1", WEB, "web", "#3366cc", "New message", "needs_input",
    { blocked: { state: "quarantine", mid: "m1", frm: "api", origin: "", to: "web", body: "the README draft is ready for a look", gist: "the README draft is ready" } });
  await dispatch(frame([g1, card("g2")._it, g3, q1], { working: ["web"] }));
  const approve = card("q1")._qApprove, deny = card("q1")._qDeny;
  assert.deepEqual([approve.disabled, deny.disabled, approve.textContent, deny.textContent], [false, false, "Approve", "Deny"]);
  const sent = posted.length;
  approve.onclick(ev);
  assert.deepEqual(posted.slice(sent).filter((m) => m.type === "quarantineDecision").map((m) => [m.mid, m.action, m.sid]), [["m1", "approve", WEB]]);
  assert.deepEqual([approve.disabled, deny.disabled, approve.textContent], [true, true, "Delivering…"], "both latch; the one clicked says what it is doing");
  await dispatch({ type: "quarantineRefused", mid: "m2", text: "quarantine: not that one" });
  assert.equal(approve.disabled, true, "another held message's refusal is not this card's reply");
  await dispatch({ type: "quarantineRefused", mid: "m1", text: "quarantine: the recipient is no longer live" });
  assert.deepEqual([approve.disabled, deny.disabled, approve.textContent, deny.textContent], [false, false, "Approve", "Deny"],
    "the kernel's reply for this message re-arms both");
  assert.equal(body.querySelector(".feed-toast")?.textContent, "quarantine: the recipient is no longer live", "…and says why (the bare warn it replaced had no handler here)");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));
  assert.ok(!card("q1"));
});

test("a handoff recipient's working state is a paint input: the delegating card repaints and its delegation line appears when the recipient starts working; an idle session's card does not repaint", async () => {
  const handoff = { id: API + ":h1", kind: "handoff", text: "write the README section", who: "api", whoSid: API, whoColor: null, status: "open", t: K0 - 200, last: K0 - 200, children: [] };
  const g1h = { ...g1, tree: [...g1.tree, handoff] };
  await dispatch(frame([g1h, card("g2")._it, g3], { working: ["web"] }));
  assert.equal(card("g1")._delegations.style.display, "none", "api is idle: no delegation line");
  const before = nameRebuilds();
  await dispatch(frame([g1h, card("g2")._it, g3], { working: ["web", "api"] }));   // the same objects; api starts working
  assert.equal(card("g1")._delegations.children.length, 1);
  assert.equal(card("g1")._delegations.querySelector(".fask-delegation")!.textContent, "api");
  assert.deepEqual(nameRebuilds(), { g1: before.g1 + 1, g2: before.g2 + 1, g3: before.g3 }, "web's card (its recipient's state) and api's own card (its dot); tests' card is untouched");
  await dispatch(frame([g1h, card("g2")._it, g3], { working: ["web"] }));
  assert.equal(card("g1")._delegations.style.display, "none", "idle again: the line is gone");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));   // the tree as before
});

test("a remote host going down is a paint input: that host's card repaints (its host prefix takes the off mark) and no other card does", async () => {
  const downs: string[] = [];
  (globalThis as any).__rompFed = { down: () => downs };                    // what hostIsDown reads (federation.js publishes it)
  const r1 = cardOf("r1", "remote:" + API, "remote:api", "#996633", "Draft the notes-api docs", "working");
  await dispatch(frame([g1, card("g2")._it, g3, r1], { working: ["web"] }));
  const prefix = () => card("r1")._name.querySelector(".host-prefix")!;
  assert.equal(prefix().textContent, "remote:"); assert.ok(!prefix().classList.contains("off"));
  const before = { ...nameRebuilds(), r1: card("r1")._name.rc };
  downs.push("remote");
  await dispatch(frame([g1, card("g2")._it, g3, r1], { working: ["web"] }));   // the same objects: only the host's reachability changed
  assert.ok(prefix().classList.contains("off"), "the link is marked down");
  assert.deepEqual({ ...nameRebuilds(), r1: card("r1")._name.rc }, { ...before, r1: before.r1 + 1 }, "the remote card alone repainted");
  delete (globalThis as any).__rompFed;
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));
  assert.ok(!card("r1"));
});

test("a colour echo (an in-place write into the shared objects) repaints that session's cards through the key, once", async () => {
  const before = nameRebuilds();
  win.dispatchEvent(Object.assign(new Event("storage"), { key: "romp:color-echo", newValue: JSON.stringify({ sid: API, bg: "#cc3366" }) }));
  assert.deepEqual(nameRebuilds(), { g1: before.g1, g2: before.g2 + 1, g3: before.g3 }, "api's card repainted, the others did not");
  assert.equal(card("g2")._name.style.color, "#cc3366");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));   // the same objects again, colour still echoed
  assert.deepEqual(nameRebuilds(), { g1: before.g1, g2: before.g2 + 1, g3: before.g3 }, "the echoed colour is in the key: no flap, no second rebuild");
});

test("the frame's user-todo map is a paint input: the flagged session's cards repaint (the quiet marker appears) and no other card does", async () => {
  // the marker reads userTodosMap[it.sid], outside the ask object (plans/user-todos.md), and the delivery path
  // keeps an unchanged card's object — so the count must reach the gate through the key, or a todo registered
  // or withdrawn leaves a stale marker until the kernel happens to re-send the card
  const before = nameRebuilds();
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"], userTodos: { [API]: 2 } }));
  assert.deepEqual(nameRebuilds(), { g1: before.g1, g2: before.g2 + 1, g3: before.g3 }, "api's card alone repainted");
  assert.equal(card("g2")._utMark.style.display, "", "the marker shows");
  assert.equal(card("g2")._utMark.textContent, "⚑ waiting on you · 2");
  assert.equal(card("g1")._utMark.style.display, "none", "no marker on a session with nothing open");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"], userTodos: { [API]: 2 } }));
  assert.deepEqual(nameRebuilds(), { g1: before.g1, g2: before.g2 + 1, g3: before.g3 }, "the same count again: no repaint");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));   // the todo withdrawn: the map empties, the same objects
  assert.deepEqual(nameRebuilds(), { g1: before.g1, g2: before.g2 + 2, g3: before.g3 }, "the marker leaves through the key too");
  assert.equal(card("g2")._utMark.style.display, "none");
});

test("the 15 s live pass moves ages and durations on cards no frame touched, writing only the labels whose text changed", () => {
  // 16.7 s have elapsed since boot (frame C's 700 ms, the 16 s boundary), so one pass has run, at 15 s. The pass
  // runs at every 15 s multiple on the kernel's clock — the frame's `now` plus the local time since its `nowAt`.
  // The cards are 240 s old at the frame and relAge rounds to the nearest minute, so "4m ago" becomes "5m ago" at
  // 270 s (30 s elapsed); the wait is 600 s old and workingFor floors, so "10m" becomes "11m" at 660 s (60 s).
  const before = nameRebuilds();
  const time1 = card("g1")._time, dur3 = card("g3")._awaitWhy.querySelector(".fask-dur")!;
  const t1 = time1.tc, d3 = dur3.tc;
  assert.equal(time1.textContent, "4m ago"); assert.equal(dur3.textContent, "10m");
  mock.timers.tick(15_000);                 // the pass at 30 s: the age label crossed its minute, the duration did not
  assert.equal(time1.textContent, "5m ago"); assert.equal(time1.tc, t1 + 1, "one write, at the minute it crossed");
  assert.equal(dur3.textContent, "10m"); assert.equal(dur3.tc, d3, "an unchanged label is not written");
  mock.timers.tick(30_000);                 // the passes at 45 s and 60 s: only the duration crossed, at 60 s
  assert.equal(dur3.textContent, "11m"); assert.equal(dur3.tc, d3 + 1);
  assert.equal(time1.textContent, "5m ago"); assert.equal(time1.tc, t1 + 1);
  mock.timers.tick(15_000);                 // the pass at 75 s: nothing crossed, nothing written
  assert.equal(time1.tc, t1 + 1); assert.equal(dur3.tc, d3 + 1);
  assert.deepEqual(nameRebuilds(), before, "the pass repaints labels, never cards");
  // a pane nobody can see skips the pass and catches up once when shown
  doc.hidden = true;
  mock.timers.tick(60_000);                 // the passes at 90-135 s: the age reads 6m from 90 s (330 s) on — nothing written while hidden
  assert.equal(time1.textContent, "5m ago"); assert.equal(time1.tc, t1 + 1);
  assert.equal(dur3.textContent, "11m"); assert.equal(dur3.tc, d3 + 1);
  doc.hidden = false;
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(time1.textContent, "6m ago", "shown: one catch-up pass"); assert.equal(time1.tc, t1 + 2);
  assert.equal(dur3.textContent, "12m"); assert.equal(dur3.tc, d3 + 1 + 1);   // 735 s
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(time1.tc, t1 + 2, "a second visibility flip with no skipped pass behind it runs nothing");
  // the other measure the paint gate reads: #feed-list off screen by the observer's word (the pane the shell has
  // display:none'd, for which document.hidden stays false) skips the pass the same way, and the observer's
  // callback is what catches it up — a same-size re-show fires no resize
  assert.equal(observers.length, 1, "render() observes #feed-list once");
  observers[0].cb([{ isIntersecting: false }]);
  mock.timers.tick(60_000);                 // the passes at 150-195 s: the age reads 7m from 150 s (390 s) on — nothing written off screen
  assert.equal(time1.textContent, "6m ago"); assert.equal(time1.tc, t1 + 2);
  assert.equal(dur3.textContent, "12m"); assert.equal(dur3.tc, d3 + 2);
  observers[0].cb([{ isIntersecting: true }]);
  assert.equal(time1.textContent, "7m ago", "on screen by the observer's word: one catch-up pass"); assert.equal(time1.tc, t1 + 3);
  assert.equal(dur3.textContent, "13m"); assert.equal(dur3.tc, d3 + 3);   // 795 s
  observers[0].cb([{ isIntersecting: true }]);
  assert.equal(time1.tc, t1 + 3, "a second callback with no skipped pass behind it runs nothing");
  assert.deepEqual(nameRebuilds(), before);
});

// The kernel clock as a card painted NOW reads it: the frame's `now` plus the local seconds since its `nowAt`
// (feed-age.ts liveNow). The tests below stamp their fixtures relative to it and tick 15 s at a time, one pass
// per tick (the pass runs at every 15 s of local time since the module loaded). Under the mock a pass fired
// inside a tick reads the clock at the tick's END (frame C's note), so each 15 s tick moves every label's clock
// by 15 s. Ages round to the minute (relAge), durations floor (workingFor).
const kernelNow = () => K0 + Math.floor((Date.now() - T0 * 1000) / 1000);
const dur = (el: any) => el.querySelector(".fask-dur");

test("the grouped-mode headers write their labels compare-first: three renders of one frame write no text; a background process appearing writes that header's chip alone", async () => {
  const same = frame([g1, card("g2")._it, g3], { working: ["web"] });
  await dispatch(same);
  const heads = () => body.querySelectorAll(".feed-sess-head").filter((h) => !h.classList.contains("sess-exit"));
  const writes = () => Object.fromEntries(heads().map((h: any) => [h.getAttribute("data-fsid"), h._fold.tc + h._foldn.tc + h._svc.tc]));
  const before = writes();
  assert.equal(Object.keys(before).length, 3, "one header per session run");
  await dispatch(same); await dispatch(same);
  assert.deepEqual(writes(), before, "the caret, the folded count and the process chip: compared and skipped");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"], bgServices: { web: ["dev server on :3000"] } }));
  const webHead = heads().find((h: any) => h.getAttribute("data-fsid") === WEB) as any;
  assert.equal(webHead._svc.textContent, "background process"); assert.equal(webHead._svc.style.display, "");
  assert.deepEqual(writes(), { ...before, [WEB]: before[WEB] + 1 }, "one write, on the header whose chip changed");
  await dispatch(same);                                          // the process is gone: the chip's count text and display change back
  assert.equal(webHead._svc.style.display, "none");
  assert.deepEqual(writes(), { ...before, [WEB]: before[WEB] + 2 });
});

test("the Awaiting-task pill's waited time and the waiting-on chip's elapsed time are stamped durations the pass moves, writing once at the minute they cross; a wait with no `since` carries no duration", async () => {
  const kNow = kernelNow();
  const g4 = cardOf("g4", TESTS, "tests", "#33cc66", "Run the lint pass", "working",
    { awaiting: { why: "", kind: "tasks", tasks: ["run the suite"], count: 1, since: kNow - 595 } });   // 9m 55s into the wait
  const g5 = cardOf("g5", API, "api", "#cc6633", "Ask web for the route list", "working",
    { waitingOn: { peerSid: WEB, name: "web", color: null, inCycle: false, since: kNow - 595 },
      awaiting: { why: "", kind: "tasks", tasks: ["lint"], count: 1 } });                             // a wait with no since
  await dispatch(frame([g1, card("g2")._it, g3, g4, g5], { working: ["web"] }));
  const pill = card("g4")._taskLbl, pillDur = dur(pill);
  assert.equal(card("g4")._taskBtn.style.display, "", "live tasks: the pill shows");
  assert.ok(pillDur, "…with the wait's duration as a stamped element");
  assert.equal(pillDur.dataset.ageFmt, "dur"); assert.equal(pillDur.dataset.ageT, String(kNow - 595));
  assert.equal(pillDur.textContent, "9m"); assert.match(pill.textContent, /^Awaiting .* · 9m$/);
  const chip = card("g5")._waitOn, chipDur = chip.querySelector(".fask-waiton-dur .fask-dur"), chipName = chip.querySelector(".fask-waiton-name");
  assert.equal(chipDur.textContent, "9m"); assert.equal(chipDur.dataset.ageT, String(kNow - 595));
  assert.equal(chipName.textContent, "web");
  assert.ok(!dur(card("g5")._taskLbl), "no since: no duration node, no guess");
  assert.doesNotMatch(card("g5")._taskLbl.textContent, / · /, "the label ends with the word alone");
  const w0 = { pill: pillDur.tc, chip: chipDur.tc, name: chipName.tc };
  mock.timers.tick(15_000);                                      // 610 s into both waits → "10m"
  assert.equal(pillDur.textContent, "10m"); assert.equal(chipDur.textContent, "10m");
  assert.deepEqual({ pill: pillDur.tc, chip: chipDur.tc, name: chipName.tc }, { pill: w0.pill + 1, chip: w0.chip + 1, name: w0.name }, "one write each; the peer's name node untouched");
  mock.timers.tick(15_000);                                      // 625 s → still "10m"
  assert.deepEqual({ pill: pillDur.tc, chip: chipDur.tc, name: chipName.tc }, { pill: w0.pill + 1, chip: w0.chip + 1, name: w0.name }, "no crossing, no write");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));
  assert.ok(!card("g4") && !card("g5"), "the fixtures left with the frame");
});

let g7: any;   // the needs-you card the next two tests share
test("per-paragraph ages of a multi-item brief are stamps the pass moves; a paragraph with no event time is the static '<1m ago' chip", async () => {
  const kNow = kernelNow();
  g7 = cardOf("g7", WEB, "web", "#3366cc", "Decide the auth scheme", "needs_input",
    { blockSummary: "Pick between sessions and tokens.\n\nName the cookie domain.\n\nStill open: the refresh interval.",
      briefParts: [{ id: "g7a", since: kNow - 260 }, { id: "g7b", since: kNow - 600 }, { id: "g7c", since: null }] });
  await dispatch(frame([g1, card("g2")._it, g3, g7], { working: ["web"] }));
  const ages = (): any[] => card("g7")._distill.querySelectorAll(".fask-para-age");
  assert.equal(ages().length, 3, "one chip per paragraph");
  assert.deepEqual(ages().map((a) => a.textContent), ["4m ago", "10m ago", "<1m ago"]);
  assert.deepEqual(ages().map((a) => a.dataset.ageT), [String(kNow - 260), String(kNow - 600), undefined], "the third carries no stamp: nothing to count from");
  const w0 = ages().map((a) => a.tc);
  mock.timers.tick(15_000);                                      // 275 s rounds to 5m; 615 s stays 10m
  assert.deepEqual(ages().map((a) => a.textContent), ["5m ago", "10m ago", "<1m ago"]);
  assert.deepEqual(ages().map((a) => a.tc), [w0[0] + 1, w0[1], w0[2]]);
  mock.timers.tick(30_000);                                      // 305 s stays 5m; 645 s rounds to 11m
  assert.deepEqual(ages().map((a) => a.textContent), ["5m ago", "11m ago", "<1m ago"]);
  assert.deepEqual(ages().map((a) => a.tc), [w0[0] + 1, w0[1] + 1, w0[2]], "the unstamped chip is never written");
});

test("the latched Continue's hover title is refreshed by the pass once the payload carries the latch, compare-then-write", async () => {
  const cont = card("g7")._cont;
  assert.equal(cont.style.display, "", "a live needs-you card offers Continue");
  let sets = 0, held = "";
  const watch = () => { held = cont.title; Object.defineProperty(cont, "title", { get: () => held, set: () => { sets++; }, configurable: true }); };
  const unwatch = () => { delete cont.title; cont.title = held; };
  cont.onclick(ev);                                              // posts the gesture, latches the button, predicts the move to Working
  assert.equal(cont.disabled, true);
  assert.match(cont.title, /^a continue sent — /); assert.doesNotMatch(cont.title, /ago/, "no age: the click's own object carries no followupAt");
  watch(); mock.timers.tick(14_000); unwatch();                  // one pass, inside the prediction's 15 s ack window
  assert.equal(sets, 0, "the payload does not carry the latch yet: the pass has nothing to move and writes nothing");
  const kNow = kernelNow();
  await dispatch(frame([g1, card("g2")._it, g3, { ...g7, column: "working", followupPending: true, followupAt: kNow - 100 }], { working: ["web"] }));   // the kernel confirms, stamping the follow-up 100 s ago
  assert.equal(cont.disabled, true, "still latched: the judge has not ruled");
  assert.match(cont.title, /^a continue sent 2m ago — /, "the payload's stamp is the title's age now");
  watch(); mock.timers.tick(15_000); mock.timers.tick(15_000); unwatch();   // 115 s, 130 s: both round to 2m
  assert.equal(sets, 0, "two passes, no crossing: the title is compared and left alone");
  mock.timers.tick(30_000);                                      // 160 s rounds to 3m
  assert.match(cont.title, /^a continue sent 3m ago — /, "the pass moved the title's age");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));
  assert.ok(!card("g7"));
});

test("the group card's time label is a stamp the pass moves: the newest member's time", async () => {
  const kNow = kernelNow();
  const h1 = cardOf("h1", API, "api", "#cc6633", "Ship the README", "working", { turnId: "turn-shared", groupTitle: "Ship the README and the CHANGELOG", t: kNow - 260 });
  const h2 = cardOf("h2", API, "api", "#cc6633", "Ship the CHANGELOG", "working", { turnId: "turn-shared", groupTitle: "Ship the README and the CHANGELOG", t: kNow - 300 });
  await dispatch(frame([g1, card("g2")._it, g3, h1, h2], { working: ["web"] }));
  const group = body.querySelector('[data-key="g:turn-shared"]') as any;
  assert.ok(group, "two asks of one typed turn fold into a group card"); assert.ok(!card("h1"), "the members are folded into it");
  assert.equal(group._time.textContent, "4m ago"); assert.equal(group._time.dataset.ageT, String(kNow - 260));
  const w0 = group._time.tc;
  mock.timers.tick(15_000);                                      // 275 s → 5m
  assert.equal(group._time.textContent, "5m ago"); assert.equal(group._time.tc, w0 + 1);
  mock.timers.tick(15_000);                                      // 290 s → still 5m
  assert.equal(group._time.tc, w0 + 1, "no crossing, no write");
  await dispatch(frame([g1, card("g2")._it, g3], { working: ["web"] }));
  assert.ok(!body.querySelector('[data-key="g:turn-shared"]'));
  mock.timers.tick(700);                                         // the in-place glides this frame started end (their backstop) before the fly cases below
});

test("Undo inside a card's 180 ms collapse keeps the restored card: the gesture strips .dismissing, which the class rewrite used to do", () => {
  const c3 = card("g3");
  c3._clr.onclick(ev);                      // Clear: .dismissing + a 180 ms removal timer, the id held back from pushes
  assert.ok(c3.classList.contains("dismissing"));
  assert.equal(posted.filter((m) => m.type === "askClear").length, 1);
  body.byId("feed-undoclear")!.onclick!(ev);   // Undo before the collapse ends: the same object, the same key
  assert.ok(!c3.classList.contains("dismissing"), "Undo took the class off");
  mock.timers.tick(200);                    // the collapse timer fires and finds nothing to remove
  assert.equal(card("g3"), c3, "the restored card is still on the board, the same element");
  assert.equal(colOf("g3"), "col-asks-list");
});

test("a card moving into a FOLDED column (display:none, a zero rect) gets no fly: nothing to glide to, and the class it would wear turns the pointer off", async () => {
  body.byId("col-completed-list")!.style.display = "none";   // the Completed section folded to its header
  const g3done = { ...card("g3")._it, column: "completed" };
  await dispatch(frame([g1, card("g2")._it, g3done], { working: ["web"] }));
  assert.equal(colOf("g3"), "col-completed-list", "the card moved");
  assert.ok(!card("g3").classList.contains("fitem-flying"), "no fly into a column nobody can see");
  assert.equal(card("g3").style.transform ?? "", "", "no inverted transform left on it");
  body.byId("col-completed-list")!.style.display = "";
});

test("…and a card LEAVING a folded column (a zero First rect) gets no fly either: nothing to glide from", async () => {
  body.byId("col-completed-list")!.style.display = "none";   // g3 sits in the folded Completed section
  await dispatch(frame([g1, card("g2")._it, { ...card("g3")._it, column: "working" }], { working: ["web"] }));
  assert.equal(colOf("g3"), "col-asks-list", "the card moved back to Working");
  assert.ok(!card("g3").classList.contains("fitem-flying"), "no fly from a spot nobody could see");
  assert.equal(card("g3").style.transform ?? "", "", "no inverted transform from the pane's corner");
  body.byId("col-completed-list")!.style.display = "";
});

test("a second fly of the same card while the first still runs keeps its own Invert through the first fly's cancel, and the first fly's backstop leaves it alone", async () => {
  const c2 = card("g2");
  await dispatch(frame([g1, { ...c2._it, column: "working" }, card("g3")._it], { working: ["web"] }));   // fly 1: Completed → Working
  assert.ok(c2.classList.contains("fitem-flying"));
  mock.timers.tick(20);                                        // fly 1 plays: its transition is running
  assert.equal(c2.style.transform, "translate(0, 0)");
  await dispatch(frame([g1, { ...c2._it, column: "completed" }, card("g3")._it], { working: ["web"] }));   // fly 2, mid-flight: back to Completed
  const inverted = c2.style.transform;
  assert.match(inverted, /^translate\(-?\d/, "fly 2 inverted the card to its old spot");
  assert.notEqual(inverted, "translate(0, 0)");
  // the browser cancels fly 1's transition on that write and tells EVERY listener before fly 2's Play frame
  c2.dispatchEvent(Object.assign(new Event("transitioncancel"), { propertyName: "transform" }));
  assert.equal(c2.style.transform, inverted, "fly 1's cancel handler is superseded; fly 2's ignores an event before its own Play — the Invert survives");
  assert.ok(c2.classList.contains("fitem-flying"), "…and the back layer stays on for the crossing");
  mock.timers.tick(20);                                        // fly 2 plays
  assert.equal(c2.style.transform, "translate(0, 0)");
  assert.match(c2.style.transition, /transform \.42s/);
  mock.timers.tick(610);                                       // fly 1's 650 ms backstop falls due: superseded, a no-op
  assert.match(c2.style.transition, /transform \.42s/, "fly 2 is still in flight");
  assert.ok(c2.classList.contains("fitem-flying"));
  mock.timers.tick(20);                                        // fly 2's own backstop ends it
  assert.equal(c2.style.transform, "");
  assert.equal(c2.style.transition, "");
  assert.ok(!c2.classList.contains("fitem-flying"));
});

test("a fly ends on its own transitionend; another property's transitionend is not this fly's", async () => {
  const c2 = card("g2");
  await dispatch(frame([g1, { ...c2._it, column: "working" }, card("g3")._it], { working: ["web"] }));   // Completed → Working
  assert.ok(c2.classList.contains("fitem-flying"));
  mock.timers.tick(20);                                        // played
  assert.equal(c2.style.transform, "translate(0, 0)");
  c2.dispatchEvent(Object.assign(new Event("transitionend"), { propertyName: "opacity" }));
  assert.ok(c2.classList.contains("fitem-flying"), "another property's end is not this fly's");
  assert.equal(c2.style.transform, "translate(0, 0)");
  c2.dispatchEvent(Object.assign(new Event("transitionend"), { propertyName: "transform" }));
  assert.ok(!c2.classList.contains("fitem-flying"), "the transform's end takes the card out of the back layer");
  assert.equal(c2.style.transform, ""); assert.equal(c2.style.transition, "");
  mock.timers.tick(700);
  assert.equal(c2.style.transform, "", "…and the backstop that follows has nothing to do");
});

test("a fly ends on a transitioncancel AFTER its Play: a card hidden or re-inserted mid-flight gets cancel, never end", async () => {
  const c2 = card("g2");
  await dispatch(frame([g1, { ...c2._it, column: "completed" }, card("g3")._it], { working: ["web"] }));   // Working → Completed
  assert.ok(c2.classList.contains("fitem-flying"));
  mock.timers.tick(20);                                        // played: the cancel is now this fly's own
  c2.dispatchEvent(Object.assign(new Event("transitioncancel"), { propertyName: "transform" }));
  assert.ok(!c2.classList.contains("fitem-flying"), "the cancel of its own transition ends the fly");
  assert.equal(c2.style.transform, ""); assert.equal(c2.style.transition, "");
});

test("the release frame stands down when the backstop already ended the fly: no identity transform is left on a settled card", async () => {
  const c2 = card("g2");
  await dispatch(frame([g1, { ...c2._it, column: "working" }, card("g3")._it], { working: ["web"] }));   // Completed → Working
  assert.ok(c2.classList.contains("fitem-flying"));
  mock.timers.tick(700);   // ONE tick: a timer created inside a tick is stamped at its end, so the nested animation frame runs AFTER the 650 ms backstop — a hidden tab's order
  assert.ok(!c2.classList.contains("fitem-flying"), "the backstop ended the fly");
  assert.equal(c2.style.transform, "", "the release frame found it ended and wrote nothing");
  assert.equal(c2.style.transition, "");
});

test("the back-layer class comes off whichever fly added it: a crossing fly superseded by an in-place shift of the same card", async () => {
  const c2 = card("g2");
  await dispatch(frame([g1, { ...c2._it, column: "completed" }, card("g3")._it], { working: ["web"] }));   // fly 1: Working → Completed, crossing
  assert.ok(c2.classList.contains("fitem-flying"));
  mock.timers.tick(20);                                        // fly 1 plays
  // fly 2: web's card lands in Completed above api's run, so g2 shifts within its column — no crossing
  await dispatch(frame([{ ...g1, column: "completed" }, card("g2")._it, card("g3")._it], { working: ["web"] }));
  assert.equal(colOf("g2"), "col-completed-list");
  assert.match(c2.style.transform, /^translate\(-?\d/, "fly 2 inverted the shift");
  c2.dispatchEvent(Object.assign(new Event("transitioncancel"), { propertyName: "transform" }));   // fly 1's transition was interrupted: its cancel
  assert.ok(c2.classList.contains("fitem-flying"), "fly 1 is superseded and touches nothing: the class stays for the fly that owns the card");
  mock.timers.tick(20);                                        // fly 2 plays
  mock.timers.tick(650);                                       // fly 2's backstop
  assert.ok(!c2.classList.contains("fitem-flying"), "fly 2 did not cross, and still takes the class off: whichever fly added it");
  assert.equal(c2.style.transform, ""); assert.equal(c2.style.transition, "");
  await dispatch(frame([g1, card("g2")._it, card("g3")._it], { working: ["web"] }));   // g1 back to Working
  mock.timers.tick(700);
});

test("Retry latches on the click and re-arms only on a deciding event: the kernel's reply frame, or a repaint of the card", async () => {
  const blockedG1 = { ...g1, blocked: { state: "apiError", what: "the API returned 529", status: 529 } };
  await dispatch(frame([blockedG1, card("g2")._it, card("g3")._it]));   // web is NOT working: the API-error unit shows
  const retry = card("g1")._apiRetry;
  assert.equal(retry.style.display, ""); assert.equal(retry.disabled, false); assert.equal(retry.textContent, "Retry");
  const sent = posted.length;
  retry.onclick(ev);
  assert.deepEqual(posted.slice(sent).filter((m) => m.type === "apiRetry"), [{ type: "apiRetry", id: WEB, manual: true }],
    "a MANUAL retry: the kernel fires it past every auto gate, as the chat pane's button does");
  assert.equal(retry.disabled, true); assert.equal(retry.textContent, "Retrying…");
  await dispatch(frame([blockedG1, card("g2")._it, card("g3")._it]));   // the same objects again: nothing decided
  assert.equal(retry.disabled, true, "a re-emit is not a deciding event");
  await dispatch({ type: "err", sid: API, op: "apiRetry", text: "another session's business" });
  assert.equal(retry.disabled, true, "another session's reply is not this card's event");
  await dispatch({ type: "err", text: "a reply that names no session" });
  assert.equal(retry.disabled, true, "a reply naming no session answers no request: nothing re-arms");
  await dispatch({ type: "err", sid: WEB, op: "askFollowUp", itemId: "g1", text: "this session's reply to a DIFFERENT request" });
  assert.equal(retry.disabled, true, "the reply to another request of this session is not this button's");
  await dispatch({ type: "err", sid: WEB, op: "apiRetry", text: "the retry was not delivered" });
  assert.equal(retry.disabled, false, "the kernel's refusal of THIS session's retry re-arms it");
  assert.equal(retry.textContent, "Retry");
  retry.onclick(ev);
  assert.equal(retry.disabled, true);
  await dispatch({ type: "retryRefused", sid: WEB, text: "Couldn't retry: the session isn't connected right now." });
  assert.equal(retry.disabled, false, "the backend's refusal of the manual retry (the kernel's retryRefused) re-arms it");
  assert.equal(body.querySelector(".feed-toast")?.textContent, "Couldn't retry: the session isn't connected right now.", "…and says why");
  retry.onclick(ev);
  assert.equal(retry.disabled, true);
  await dispatch({ type: "err", sid: WEB, text: "an older kernel's refusal names the session and no request" });
  assert.equal(retry.disabled, false, "a reply naming the session but no request releases the session's Retry, as before the op field");
  retry.onclick(ev);
  assert.equal(retry.disabled, true);
  await dispatch(frame([{ ...blockedG1 }, card("g2")._it, card("g3")._it]));   // a new object for the card: it repaints
  assert.equal(retry.disabled, false, "a repaint re-arms it too");
  await dispatch(frame([g1, card("g2")._it, card("g3")._it]));   // the block is gone: the unit hides
  assert.equal(card("g1")._apiRetry.style.display, "none");
});

test("Continue latches on the click and predicts the move; the kernel's refusal of THAT post re-arms it and returns the card, a refusal of another card's post leaves both", async () => {
  const needsG1 = { ...g1, column: "needs_input" };
  await dispatch(frame([needsG1, card("g2")._it, card("g3")._it], { working: ["api"] }));   // web is live and waiting on you, no live ask
  const cont = card("g1")._cont;
  assert.equal(cont.style.display, ""); assert.equal(cont.disabled, false); assert.equal(cont.textContent, "Continue");
  assert.equal(colOf("g1"), "col-needsInput-list");
  const sent = posted.length;
  cont.onclick(ev);
  assert.deepEqual(posted.slice(sent).filter((m) => m.type === "askFollowUp"), [{ type: "askFollowUp", itemId: "g1", sid: WEB, cont: true }]);
  assert.equal(cont.disabled, true); assert.equal(cont.textContent, "Sent");
  assert.equal(colOf("g1"), "col-asks-list", "the predicted move: the card goes to Working ahead of the kernel");
  await dispatch(frame([needsG1, card("g2")._it, card("g3")._it], { working: ["api"] }));   // the same objects: nothing decided
  assert.equal(cont.disabled, true, "a re-emit is not a deciding event");
  assert.equal(colOf("g1"), "col-asks-list", "…and the prediction holds");
  await dispatch({ type: "err", sid: WEB, op: "askFollowUp", itemId: "g1a", title: "That reply was not delivered", text: "Nothing was sent." });
  assert.equal(cont.disabled, true, "a refusal of another card's post is not this button's reply");
  await dispatch({ type: "err", sid: WEB, op: "askFollowUp", itemId: "g1", title: "That reply was not delivered", text: "Nothing was sent." });
  assert.equal(cont.disabled, false, "the kernel's refusal of this card's post re-arms it");
  assert.equal(cont.textContent, "Continue");
  assert.equal(colOf("g1"), "col-needsInput-list", "…and the predicted move yields to the kernel's answer: the card is back where the payload says");
  await dispatch(frame([g1, card("g2")._it, card("g3")._it]));   // web back to Working
  assert.equal(colOf("g1"), "col-asks-list");
});

test("a reveal pulse comes off when its animation ends, and a child's animation ending inside the card does not end it", async () => {
  const c1 = card("g1");
  await dispatch({ type: "revealCards", keys: ["g1"] });
  assert.ok(c1.classList.contains("card-pulse"));
  // animationend bubbles: a button's acted flash inside the card reaches the card's listener too
  c1.dispatchEvent(Object.assign(new Event("animationend"), { animationName: "romp-acted-pulse" }));
  assert.ok(c1.classList.contains("card-pulse"), "another animation's end is not this pulse's");
  c1.dispatchEvent(Object.assign(new Event("animationend"), { animationName: "romp-card-pulse" }));
  assert.ok(!c1.classList.contains("card-pulse"), "the pulse's own end takes the class off");
  mock.timers.tick(1600);
  assert.ok(!c1.classList.contains("card-pulse"), "…and the backstop that follows has nothing to do");
});

test("a second reveal pulse inside the first's window is not cut short by the first's backstop", async () => {
  const c1 = card("g1");
  await dispatch({ type: "revealCards", keys: ["g1"] });
  assert.ok(c1.classList.contains("card-pulse"));
  mock.timers.tick(600);
  await dispatch({ type: "revealCards", keys: ["g1"] });        // pulse again, 600 ms in
  mock.timers.tick(1000);                                       // the FIRST backstop's moment (1500 ms after it armed)
  assert.ok(c1.classList.contains("card-pulse"), "the second pulse still shows: one handle per element");
  mock.timers.tick(600);                                        // the second backstop
  assert.ok(!c1.classList.contains("card-pulse"), "…and it comes off at its own end");
});

test("a pulse ended by its animationend leaves no backstop behind: a later reveal inside 1500 ms is not cut short by it", async () => {
  const c1 = card("g1");
  await dispatch({ type: "revealCards", keys: ["g1"] });
  c1.dispatchEvent(Object.assign(new Event("animationend"), { animationName: "romp-card-pulse" }));
  assert.ok(!c1.classList.contains("card-pulse"));
  mock.timers.tick(600);
  await dispatch({ type: "revealCards", keys: ["g1"] });        // pulse again
  mock.timers.tick(1000);                                       // the first backstop's moment, had it survived the end
  assert.ok(c1.classList.contains("card-pulse"), "the second pulse still shows");
  mock.timers.tick(600);
  assert.ok(!c1.classList.contains("card-pulse"), "…and ends at its own backstop");
});

test("a session header's name nodes are minted only when what they show changes: an unchanged header re-mints nothing", async () => {
  // grouped mode's headers are not behind the per-card gate: updateSessHead runs for every header on every
  // render, and minted the name nodes each time (a Text-node replacement per header per render)
  const heads = () => Object.fromEntries(body.querySelectorAll(".feed-sess-head").filter((h: any) => !h.classList.contains("sess-exit"))
    .map((h: any) => [h.getAttribute("data-fsid"), h._name.rc]));
  const same = frame([g1, card("g2")._it, card("g3")._it]);   // the objects the cards were painted from
  await dispatch(same);
  const before = heads();
  assert.equal(Object.keys(before).length, 3, "one header per session run");
  await dispatch(same);
  await dispatch(same);
  assert.deepEqual(heads(), before, "an unchanged header re-mints nothing");
  await dispatch(frame([{ ...g1, name: "web-2" }, card("g2")._it, card("g3")._it]));   // web's card renamed: its header follows
  assert.deepEqual(heads(), { ...before, [WEB]: before[WEB] + 1 }, "the renamed session's header re-minted once; the others did not");
  assert.equal((body.querySelector(`.feed-sess-head[data-fsid="${WEB}"]`) as any)._name.textContent, "web-2");
});

test("a header's data-fsid and data-fcol are written only when they differ", async () => {
  const heads = () => body.querySelectorAll(".feed-sess-head").filter((h) => !h.classList.contains("sess-exit"));
  const same = frame([{ ...g1, name: "web-2" }, card("g2")._it, card("g3")._it]);   // the objects the cards were painted from
  await dispatch(same);
  const stamps = () => heads().map((h) => [h.getAttribute("data-fsid"), h.getAttribute("data-fcol"), h.ac]);
  const before = stamps();
  assert.equal(before.length, 3);
  assert.deepEqual(before.map((s) => s[1]), heads().map((h) => h.parentNode!.id.replace(/^col-|-list$/g, "")), "each header is stamped with the column it heads");
  await dispatch(same); await dispatch(same);
  assert.deepEqual(stamps(), before, "an unchanged sid or column is compared and not re-set");
});

test("a hovered session header is one (column, session) row: the same session's header in another column keeps its in-row badge and the floating hint sits under the hovered row", async () => {
  // grouped mode keys a header per (column, session), so a session with cards in two columns has two header
  // rows, both stamped with its sid. The hover-freeze painter found the hovered row by sid alone, so the twin in
  // the other column took the hovered branch too: its in-row badge was stripped, and the one floating hint was
  // re-placed under it (the last twin in document order), away from the row the pointer is on.
  const g1b = cardOf("g1b", WEB, "web", "#3366cc", "Check the notes-api health route", "needs_input");
  const g1c = cardOf("g1c", WEB, "web", "#3366cc", "Ship the notes-api health route", "completed");
  const four = [g1, g1b, card("g2")._it, card("g3")._it];
  await dispatch(frame(four));
  const heads = () => body.querySelectorAll(`.feed-sess-head[data-fsid="${WEB}"]`).filter((h) => !h.classList.contains("sess-exit"));
  assert.deepEqual(heads().map((h) => h.parentNode!.id), ["col-asks-list", "col-needsInput-list"], "web heads a run in Working and one in Blocked");
  const [hw, hb] = heads();
  const badged = (h: El) => h.children.some((c) => c.classList.contains("freeze-badge"));
  // the stand-in's rects derive from the parent list's id, so the two rows sit at different rights; the hint's
  // position says which row it was placed under
  const rightOf = (h: El) => (win.innerWidth - h.getBoundingClientRect().right) + "px";
  assert.notEqual(rightOf(hw), rightOf(hb));
  hw.dispatchEvent(new Event("mouseenter"));                     // the pointer rests on the Working row
  try {
    await dispatch(frame([...four, g1c]));                       // a push while it is held: one more web card, in Completed
    assert.equal(card("g1c"), null, "the payload is queued, not rendered");
    assert.ok(badged(hb), "the same session's Blocked header carries its in-row badge");
    assert.ok(!badged(hw), "never inside the hovered row");
    const note = body.byId("freeze-headnote");
    assert.ok(note, "the hovered row's hint floats");
    assert.equal(note!.style.right, rightOf(hw), "right-aligned under the hovered row, not under its twin");
  } finally {
    hw.dispatchEvent(new Event("mouseleave"));                   // release: the queued payload applies (on a failure too,
    await settle();                                              // so the next test never inherits a held gate)
  }
  assert.ok(card("g1c"), "the queued frame applied on release");
  assert.equal(body.byId("freeze-headnote"), null, "nothing pending: the hint comes off with the badges");
  await dispatch(frame([g1, card("g2")._it, card("g3")._it]));   // the three-card world back
  mock.timers.tick(700);                                          // the Blocked and Completed headers finish their exit
});

test("a hovered header's gate releases through its ghost: a Clear that takes the row out from under the pointer re-keys it, and the ghost's mouseleave still applies the queued frame", async () => {
  // a Clear on a run's last card starts the header's exit in the same click, with no render in between: the
  // element is re-keyed to a tombstone (x:N) while the pointer is still on it, and no render heal has run.
  // Its mouseleave must compute the key its mouseenter stored, or the gate stays held until a later local
  // render or a window blur. The stamps the key is read from survive the re-key; the data-key does not.
  const ha = body.querySelector(`.feed-sess-head[data-fsid="${API}"]`)!;   // api heads one run: its one card, g2
  assert.ok(!ha.classList.contains("sess-exit"));
  const stamps = () => [ha.getAttribute("data-fcol"), ha.getAttribute("data-fsid")];
  const before = stamps();
  assert.equal(before[0], ha.parentNode!.id.replace(/^col-|-list$/g, ""), "stamped with the column it heads");
  const g2it = card("g2")._it;                                   // the object g2 was painted from (its column too)
  const g1d = cardOf("g1d", WEB, "web", "#3366cc", "Document the notes-api health route", "working");
  ha.dispatchEvent(new Event("mouseenter"));                     // the pointer rests on api's header row
  try {
    await dispatch(frame([g1, g2it, card("g3")._it, g1d]));      // a push while it is held: one more web card
    assert.equal(card("g1d"), null, "the payload is queued");
    card("g2")._clr.onclick(ev);                                 // Clear api's last card: its header leaves with it
    assert.ok(ha.classList.contains("sess-exit"), "the header ghosts in the same click");
    assert.match(ha.dataset.key!, /^x:\d+$/, "re-keyed to a tombstone while still under the pointer");
    assert.deepEqual(stamps(), before, "the stamps stay");
    assert.equal(card("g1d"), null, "the clear itself is not a release");
    ha.dispatchEvent(new Event("mouseleave"));                   // the pointer leaves the ghost
    await settle();
    assert.ok(card("g1d"), "the ghost's mouseleave released the gate: the queued frame applied");
  } finally {                                                    // on a failure too, so the next test inherits neither
    win.dispatchEvent(new Event("blur"));                        // a held gate (the backstop release) nor a cleared g2
    await settle();
    mock.timers.tick(700);                                        // the cleared card's collapse and the ghost's exit end
    await dispatch(frame([g1, card("g3")._it]));                  // the kernel confirms the clear (g2 absent)
    await dispatch(frame([g1, g2it, card("g3")._it]));            // g2 back where it was, under a fresh api header
  }
  assert.equal(body.querySelectorAll(".sess-exit").length, 0);
  assert.ok(card("g2"));
});

test("a session header's Clear all releases the gate its row holds: the queued frame applies from the click, with no pointer leave and no render", async () => {
  // Clear all sits on the header row, so the pointer that clicks it is resting on the row, and the row holds the
  // hover-freeze gate from its mouseenter. The click turns the row into a pointer-inert ghost (reduced motion
  // removes it outright), and neither fires a mouseleave of its own; a card's Clear dispatches a synthetic
  // mouseleave for exactly this reason. Without the header's own dispatch, the kernel's confirmation of the
  // clear and every push behind it stayed queued until a later local render healed the stale hold, or a
  // window blur. No timer advances before the release is asserted: the 180 ms finalize's render would heal
  // the hold on its own (the stand-in's :hover matches nothing), and the point is the click, not the heal.
  const ha = body.querySelector(`.feed-sess-head[data-fsid="${API}"]`)!;   // api heads one run: its one card, g2
  assert.ok(!ha.classList.contains("sess-exit"));
  const g2it = card("g2")._it;                                   // the object g2 was painted from (its column too)
  const g1d = cardOf("g1d", WEB, "web", "#3366cc", "Document the notes-api health route", "working");
  ha.dispatchEvent(new Event("mouseenter"));                     // the pointer rests on api's header row, over its Clear all
  try {
    await dispatch(frame([g1, g2it, card("g3")._it, g1d]));      // a push while it is held: one more web card
    assert.equal(card("g1d"), null, "the payload is queued");
    // the click takes the path a real one takes: the delegate on the stable columns root, with the row's Clear
    // all as its target. The stand-in's events do not bubble, so the click is dispatched on the root with the
    // button shadowing its target; the delegate resolves the button by its data-act and reads its data-fsid.
    const cols = body.byId("feed-cols")!;
    const btn = (ha as any)._clear as El;
    const postedBefore = posted.length;
    const click = new Event("click");
    Object.defineProperty(click, "target", { value: btn });
    cols.dispatchEvent(click);
    const clears = posted.slice(postedBefore).filter((m) => m.type === "askClearMany");
    assert.deepEqual(clears.map((m) => [m.sid, m.itemIds]), [[API, ["g2"]]], "the click cleared the session's one card");
    assert.ok(card("g2").classList.contains("dismissing"));
    assert.ok(ha.classList.contains("sess-exit"), "the header ghosts in the same click");
    assert.equal(card("g1d"), null, "the release waits for the click's own handlers to finish");
    await settle();                                                // the flush is a microtask after the click's handlers
    assert.ok(card("g1d"), "the click released the gate: the queued frame applied with no pointer leave and no render");
    assert.equal(body.byId("freeze-headnote"), null, "nothing pending: the hint came off");
  } finally {                                                    // on a failure too, so the next test inherits neither
    win.dispatchEvent(new Event("blur"));                        // a held gate (the backstop release) nor a cleared g2
    await settle();
    mock.timers.tick(700);                                        // the cleared card's collapse and the ghost's exit end
    await dispatch(frame([g1, card("g3")._it]));                  // the kernel confirms the clear (g2 absent)
    await dispatch(frame([g1, g2it, card("g3")._it]));            // g2 back where it was, under a fresh api header
  }
  assert.equal(body.querySelectorAll(".sess-exit").length, 0);
  assert.ok(card("g2"));
});

test("a header re-mints its name nodes when its host's link goes down or comes back, and only then", async () => {
  let downList: string[] = [];
  (globalThis as any).__rompFed = { down: () => downList };                 // what hostIsDown reads (federation.js publishes it)
  const REMOTE = "remote:11111111-2222-3333-4444-888888888888";
  const g4 = cardOf("g4", REMOTE, "remote:docs", "#996633", "Draft the notes-api docs", "working");
  const four = () => frame([{ ...g1, name: "web-2" }, card("g2")._it, card("g3")._it, g4],
    { order: [WEB, API, TESTS, REMOTE], sessions: [{ sid: WEB, name: "web-2", color: g1.color }, { sid: API, name: "api", color: g2.color }, { sid: TESTS, name: "tests", color: g3.color }, { sid: REMOTE, name: "remote:docs", color: g4.color }] });
  await dispatch(four());
  const head = () => body.querySelector(`.feed-sess-head[data-fsid="${REMOTE}"]`) as any;
  assert.equal(head()._name.rc, 1, "minted once");
  assert.equal(head()._name.querySelector(".host-prefix").textContent, "remote:");
  assert.ok(!head()._name.querySelector(".host-prefix").classList.contains("off"));
  await dispatch(four());
  assert.equal(head()._name.rc, 1, "the same name, sid and link state: nothing re-minted");
  downList = ["remote"];
  await dispatch(four());
  assert.equal(head()._name.rc, 2, "the link went down: re-minted once…");
  assert.ok(head()._name.querySelector(".host-prefix").classList.contains("off"), "…with the off mark");
  await dispatch(four());
  assert.equal(head()._name.rc, 2, "still down: nothing");
  downList = [];
  await dispatch(four());
  assert.equal(head()._name.rc, 3, "back up: re-minted once…");
  assert.ok(!head()._name.querySelector(".host-prefix").classList.contains("off"), "…the mark gone");
  delete (globalThis as any).__rompFed;
  await dispatch(frame([g1, card("g2")._it, card("g3")._it]));
  mock.timers.tick(700);
  mock.timers.reset();
});
