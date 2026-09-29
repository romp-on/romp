// The Outline pane on a LIVE clock. fleet.ts read Date.now() inside render(), and render() ran only on a
// frame, a settings event or a click — so every "(Xm ago)", the current goal's elapsed time, the recency
// cutoff and the slider range were off by whatever skew sat between the browser's clock and the kernel's
// (the timestamps are the kernel's), and moved only when a frame arrived: on the delta path a quiet board
// sends one every 60 s. The pane now anchors on the kernel's clock (feed-age.ts liveNow: the frame's `now`
// plus the local time since its `nowAt` wire arrival) and re-renders every 15 s while visible.
//
// Run for real, not pinned at source: fleet.ts renders through document.createElement and reads frames off a
// window message, so the page is stood in for by a small tree of plain objects carrying the handful of DOM
// methods the render uses, under node:test's mock timers (Date and setInterval). The same harness runs the
// clock's two anchors (a frame stamped `nowAt` by federation; one without, off the VS Code pipe, anchored at
// its arrival), the refresh's visibility gate (a hidden document or a zero-size iframe skips the tick and the
// pane catches up once on the way back), and the unreassembled-delta guard: a raw {type:"delta"} reaching the
// pane is loud, asks for the whole slot, and applies nothing.
import { test, mock } from "node:test";
import * as assert from "node:assert/strict";

// ── a DOM stand-in: the subset fleet.ts and its imports touch at load and in render() ──────────────
class Txt {
  nodeType = 3;
  parentNode: El | null = null;
  constructor(public textContent: string) {}
}
class El {
  nodeType = 1;
  id = ""; title = ""; hidden = false; value = ""; type = ""; checked = false; min = ""; max = ""; step = "";
  innerHTML = ""; offsetWidth = 0; offsetHeight = 0;
  parentNode: El | null = null;
  childNodes: Array<El | Txt> = [];
  dataset: Record<string, string | undefined> = {};
  style: Record<string, string> = {};
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
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
  constructor(public tagName: string) {}
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get textContent(): string { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v: string) { this.replaceChildren(); if (v !== "") this.appendChild(new Txt(v)); }
  appendChild<T extends El | Txt>(c: T): T { c.parentNode?.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  append(...cs: Array<El | Txt | string>): void { for (const c of cs) this.appendChild(typeof c === "string" ? new Txt(c) : c); }
  replaceChildren(...cs: Array<El | Txt>): void { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; this.append(...cs); }
  removeChild(c: El | Txt): void { const i = this.childNodes.indexOf(c); if (i >= 0) { this.childNodes.splice(i, 1); c.parentNode = null; } }
  remove(): void { this.parentNode?.removeChild(this); }
  contains(x: El | Txt | null): boolean { for (let n: El | Txt | null = x; n; n = n.parentNode) if (n === this) return true; return false; }
  addEventListener(): void {}
  removeEventListener(): void {}
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  closest(): El | null { return null; }
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  focus(): void {}
  *walk(): Generator<El> { for (const c of this.childNodes) if (c instanceof El) { yield c; yield* c.walk(); } }
  byId(id: string): El | null { for (const e of this.walk()) if (e.id === id) return e; return null; }
  byClass(c: string): El[] { return [...this.walk()].filter((e) => e.classList.contains(c)); }
}

const posted: any[] = [];                 // what the pane sends the kernel (acquireVsCodeApi().postMessage)
const body = new El("body");
const list = new El("div"); list.id = "fleet-list";
const foot = new El("div"); foot.id = "fleet-foot";
const search = new El("input"); search.id = "fleet-search";
const clear = new El("button"); clear.id = "fleet-search-clear";
body.append(list, foot, search, clear);
const store = new Map<string, string>();
const win: any = new EventTarget();       // window: message/storage listeners dispatch through EventTarget
win.parent = win; win.top = win;          // not framed → no shell relays
win.location = { hash: "", search: "" };
win.innerWidth = 1200; win.innerHeight = 800;
win.setTimeout = (...a: Parameters<typeof setTimeout>) => setTimeout(...a);
win.clearTimeout = (t: ReturnType<typeof setTimeout>) => clearTimeout(t);
win.acquireVsCodeApi = () => ({ postMessage: (m: any) => posted.push(m) });
(globalThis as any).window = win;
const doc: any = new EventTarget();       // document: visibilitychange dispatches through EventTarget; `hidden` is the Page Visibility bit
Object.assign(doc, {
  body, documentElement: new El("html"), hidden: false,
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => new Txt(s),
  getElementById: (id: string) => body.byId(id),
  querySelectorAll: () => [],
  contains: (x: El) => body.contains(x),
});
(globalThis as any).document = doc;
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); },
  removeItem: (k: string) => { store.delete(k); },
};

// ── the clocks ─────────────────────────────────────────────────────────────────────────────────────
const T0 = 1781100000;                    // the browser's clock at boot (synthetic epoch seconds)
const K0 = T0 - 300;                      // the KERNEL's clock: five minutes behind — every age must follow it, never Date.now()
const SID = "11111111-2222-3333-4444-555555555555";
const frame = () => ({
  type: "feed", now: K0, nowAt: T0 * 1000 - 10_000,   // arrived from the wire 10 s ago (a federation re-emit): the kernel clock reads K0 + 10 now
  buildId: 1, asks: [],
  ledgers: [{ sid: SID, name: "web", color: null, status: { state: "working" },
    ledger: { current: { t: K0 - 10 }, tree: [
      { id: "g1", text: "Wire the notes-api health route", depth: 0, done: false, blocked: false, t: K0 - 10, current: true },
      { id: "g2", text: "Write the notes-api README", depth: 0, done: true, blocked: false, t: K0 - 60, mt: K0 - 30 },
    ] } }],
});
const rows = () => Object.fromEntries(list.byClass("ledger-tnode").map((r) => [r.dataset.nid, r.byClass("ledger-ttime")[0]?.textContent ?? ""]));

test("ages and the recency cutoff move on the kernel's live clock between frames — no frame, no movement, and a skewed browser clock were the bug", async () => {
  mock.timers.enable({ apis: ["Date", "setInterval", "setTimeout"], now: T0 * 1000 });
  store.set("romp:fleetShowDone", "1");   // the done top shows (fleet-roots gates it behind the toggle)
  // the slider at its midpoint: cutoff = 60 s × (maxAge/60)^0.5, and maxAge floors at 120 s while every top is
  // younger than that → an 84.85 s window. The done top starts inside it and ages out; the current one stays.
  store.set("romp:fleetCutoffPos", "500");
  await import("./fleet");                // module load: mounts the controls, installs the message listener and the 15 s refresh

  win.dispatchEvent(new MessageEvent("message", { data: frame() }));
  assert.deepEqual(rows(), { g1: "(20s)", g2: "(40s ago)" },
    "anchored on the frame's `now` + its `nowAt` arrival: the browser clock (5 min ahead) would have read (5m)/(5m ago)");
  const before = posted.length;

  mock.timers.tick(15_000);               // one refresh, no frame
  assert.deepEqual(rows(), { g1: "(35s)", g2: "(55s ago)" }, "15 s later both ages moved with nothing on the wire");
  const g2 = list.byClass("ledger-tnode").find((r) => r.dataset.nid === "g2")!;
  assert.equal(g2.byClass("ledger-ttime")[0].style.color, g2.byClass("ledger-ttext")[0].style.color,
    "a done row's text takes its time's recency colour, repainted from the same clock");

  mock.timers.tick(45_000);               // three more refreshes: 60 s after the frame
  assert.deepEqual(rows(), { g1: "(1m)" },
    "the done top (now 100 s old) aged out of the 84.85 s cutoff window and left the list; the current top (80 s) stays");
  assert.equal(list.byClass("fl-session").length, 1, "its session stays: the filter is per top, not per session");
  assert.equal(posted.length, before, "…all of it on the local clock: the pane asked the kernel for nothing");
});

test("a frame without `nowAt` (the VS Code pipe hands frames straight to the pane) anchors the clock at its arrival", () => {
  // Federation stamps `nowAt` on the frames it re-emits; the extension's pipe hands the kernel's frame over
  // unstamped, so the pane records the arrival itself and the ages count from the frame's own `now` from
  // that moment. Still under the first test's mock timers: the browser clock reads T0 + 60 s here.
  assert.equal(Date.now(), T0 * 1000 + 60_000, "the mock clock where the first test left it");
  win.dispatchEvent(new MessageEvent("message", { data: { ...frame(), nowAt: undefined } }));
  assert.deepEqual(rows(), { g1: "(10s)", g2: "(30s ago)" },
    "anchored at arrival: the frame's `now` IS the kernel clock right now; the browser clock (6 min ahead) never enters");
  mock.timers.tick(15_000);
  assert.deepEqual(rows(), { g1: "(25s)", g2: "(45s ago)" }, "15 s later both ages moved 15 s, counted from the frame's `now`");
});

test("the 15 s refresh skips a hidden document and catches up once it is visible again", () => {
  win.dispatchEvent(new MessageEvent("message", { data: { ...frame(), nowAt: Date.now() } }));   // a fresh anchor: (10s) / (30s ago)
  const rebuilt = mock.method(list, "replaceChildren");   // render() starts with #fleet-list.replaceChildren(): every rebuild shows here
  const shown = rows();
  doc.hidden = true;
  mock.timers.tick(30_000);               // two refreshes fall while hidden
  assert.equal(rebuilt.mock.callCount(), 0, "no rebuild for a pane nobody can see");
  assert.deepEqual(rows(), shown);
  doc.hidden = false;
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(rebuilt.mock.callCount(), 1, "one catch-up rebuild on the way back");
  assert.deepEqual(rows(), { g1: "(40s)", g2: "(1m ago)" }, "…with the ages moved by the hidden 30 s");
  mock.timers.tick(15_000);               // the cadence resumes
  assert.equal(rebuilt.mock.callCount(), 2);
  assert.deepEqual(rows(), { g1: "(55s)", g2: "(1m ago)" });
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(rebuilt.mock.callCount(), 2, "a flip with no skipped tick owes nothing: the catch-up is once, not per flip");
  rebuilt.mock.restore();
});

test("a pane the shell has hidden (a zero-size iframe, the shim's paneHidden test) skips too, and the resize it gets when shown catches up", () => {
  win.dispatchEvent(new MessageEvent("message", { data: { ...frame(), nowAt: Date.now() } }));   // (10s) / (30s ago)
  const rebuilt = mock.method(list, "replaceChildren");
  win.innerWidth = 0;                     // display:none on the pane iframe: document.hidden stays false, the viewport is 0×0
  mock.timers.tick(15_000);
  assert.equal(rebuilt.mock.callCount(), 0, "no rebuild while the iframe has no size");
  assert.deepEqual(rows(), { g1: "(10s)", g2: "(30s ago)" });
  win.innerWidth = 1200;
  win.dispatchEvent(new Event("resize"));  // what the iframe's window gets when the shell shows it again
  assert.equal(rebuilt.mock.callCount(), 1);
  assert.deepEqual(rows(), { g1: "(25s)", g2: "(45s ago)" }, "caught up by the hidden 15 s");
  win.dispatchEvent(new Event("resize"));
  assert.equal(rebuilt.mock.callCount(), 1, "an ordinary resize rebuilds nothing");
  rebuilt.mock.restore();
});

test("a raw delta frame reaching the pane is loud, asks for the whole slot, and applies nothing", () => {
  const err = mock.method(console, "error", () => {});
  const shown = rows();
  win.dispatchEvent(new MessageEvent("message", { data: { type: "delta", slot: "feed", base: 3, rev: 4,
    rest: { now: K0 + 500, buildId: 9 }, coll: { asks: { set: {} } } } }));
  assert.equal(err.mock.callCount(), 1);
  assert.match(String(err.mock.calls[0].arguments[0]), /delta frame reached the pane unreassembled/);
  assert.deepEqual(posted.filter((m) => m.type === "clientDiag"),
    [{ type: "clientDiag", surface: "outline", what: "delta-unapplied", data: { slot: "feed", rev: 4 } }]);
  assert.deepEqual(posted.filter((m) => m.type === "needSlot"), [{ type: "needSlot", slot: "feed" }],
    "the re-base request the kernel answers with the whole slot — the shim's own message for a delta it cannot apply");
  assert.deepEqual(rows(), shown, "the rows are as the last full frame left them: this pane applies no delta itself");
  err.mock.restore();
  mock.timers.reset();
});

// The master note (the session-flags `*` key isolating every session with no key of its own) counts the rows
// this render SHOWS, not every session in the payload, and marks each row it counts; a hand-toggled off keeps
// its own chip. Same harness, after the clock tests: the mock timers are reset, so ages read the real clock.
test("the master note counts only the shown rows, marks them in both views, and goes with the last of them", () => {
  const MA = "11111111-2222-3333-4444-5555555555a1", MB = "11111111-2222-3333-4444-5555555555b2", MC = "11111111-2222-3333-4444-5555555555c3";
  const top = (id: string, done: boolean) => ({ id, text: "Tidy the notes-api " + id, depth: 0, done, blocked: false, t: K0 - 10, current: !done });
  const row = (sid: string, name: string, why: string, done: boolean) => ({ sid, name, color: null, status: { state: "idle" },
    postalServiceOff: !!why, mailOffWhy: why, ledger: { current: { t: K0 - 10 }, tree: [top("g-" + name, done)] } });
  const push = (aWhy: string) => win.dispatchEvent(new MessageEvent("message", { data: { type: "feed", now: K0, buildId: 2, asks: [],
    ledgers: [row(MA, "web", aWhy, false), row(MB, "api", "master", true), row(MC, "tests", "isolation", false)] } }));
  const note = () => list.byClass("fl-master-mail").map((n) => n.textContent);
  store.set("romp:fleetShowDone", "0");      // api's only top is done: its row is not shown
  store.delete("romp:fleetGroupBySession");
  push("master");
  assert.deepEqual(note(), ["* Mail off by the master default for 1 session"], "api is master-held too, but not shown");
  const marks = list.byClass("fl-mail-master");
  assert.equal(marks.length, 1);
  assert.equal(marks[0].textContent, "*");
  assert.equal(marks[0].title, "mail off by the master default: listed for peers, but not reachable");
  assert.ok(marks[0].parentNode?.byClass("fl-name")[0]?.textContent === "web", "on web's own row");
  assert.deepEqual(list.byClass("fl-mail-off").map((c) => c.textContent), ["mail off"], "the hand-toggled session keeps its chip");
  assert.equal(list.byClass("fl-mail-off")[0].parentNode, list.byClass("fl-session")[1].byClass("fl-head")[0], "…on tests' row, not web's");
  store.set("romp:fleetShowDone", "1");      // api's row shows now
  push("master");
  assert.deepEqual(note(), ["* Mail off by the master default for 2 sessions"]);
  store.set("romp:fleetGroupBySession", "0");   // the flat view marks the session label on each counted row
  push("master");
  assert.equal(list.byClass("fl-mail-master").length, 2);
  assert.ok(list.byClass("fl-mail-master").every((m) => m.parentNode?.classList.contains("fl-sesslabel")));
  store.delete("romp:fleetGroupBySession");
  store.set("romp:fleetShowDone", "0");
  push("");                                   // web opted in: no shown row is master-held
  assert.deepEqual(note(), [], "no note without a shown master-held row");
  assert.equal(list.byClass("fl-mail-master").length, 0);
});
