// TAG DRAG-TO-REORDER (the user 2026-08-25): grab a pill in the Sessions & tags dialog's tag
// table to put the tags in your order. The drop writes tagOrder — the union DISPLAY order,
// viewer-side, so a REMOTE-HOMED tag holds its dragged position without any cross-kernel write —
// and the posted local tags array re-sorts to match (over the socket the kernel orders the stored
// array by the write's tagOrder itself; on the Electron path the posted blob is the file). This
// EXECUTES the drag over the house fake-DOM shim: dialog open, pointer capture, cue math, drop,
// and asserts the posted blob + that a rebuild from that blob (the reload) keeps the order.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as path from "node:path";
import { createRequire } from "node:module";

function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) {
      if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); }
      c.parentNode = n; this.children.push(c); return c;
    },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener(t: string) { delete n._listeners[t]; },
    setPointerCapture() {}, releasePointerCapture() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return n._rect || { width: 200, height: 20, left: 0, top: 0, right: 200, bottom: 20 }; },
    closest() { return null; }, focus() {}, select() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  createTextNode(text: string) { const n = makeNode("#text"); n.textContent = text; return n; },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)", fontFamily: "sans-serif" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel, viewTagUnion } = createRequire(__filename)(viewPath);

const now = 1_781_000_000;
const sess = (id: string, name: string, color: string) => ({
  id, name, color, state: "working", live: true, model: "Opus", effort: "high",
  context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
});
// three local tags + one REMOTE-homed one (read-only union entry) — the drag must position it too
const VIEWS = {
  active: "all",
  tags: [
    { id: "g1", name: "alpha", color: "#DD42FF", members: ["s1"] },
    { id: "g2", name: "beta", color: "#4EC9B0", members: ["s2"] },
    { id: "g3", name: "gamma", color: "#e0af68", members: [] },
  ],
  remoteTags: [{ id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "remotepool", color: "#7aa2f7", members: ["m1"] }],
  actives: { timeline: { all: true } },
};

function drawnPanel(): any {
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    turns: { s1: [{ id: "t1", start: now - 400, end: now - 100, prompt: "do the thing", tid: "f1", mids: [] }] },
    messages: [], judging: [], views: JSON.parse(JSON.stringify(VIEWS)),
  });
  return panel;
}

test("executed: dragging a tag pill writes tagOrder + re-sorts the local array; a remote-homed tag holds its dragged spot", () => {
  const posted: any[] = [];
  g.__rompTimelineSetViews = (v: any) => posted.push(v);
  g.__rompTimelineEditTag = () => {};   // marks remote tags editable-capable; the drag itself never routes an edit
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  const dlg = panel._viewsDialog;
  assert.ok(dlg, "the dialog opened");
  // the pill cells, in render order — the union's order: alpha, beta, gamma, remotepool
  const cells: any[] = [];
  (function walk(x: any) { for (const c of x.children || []) { if (c._tname) cells.push(c); walk(c); } })(dlg);
  assert.deepEqual(cells.map((c) => c._tname), ["alpha", "beta", "gamma", "remotepool"], "table renders the union order");
  // seat each cell at a distinct y so the drop math has real geometry
  cells.forEach((c, i) => { c._rect = { top: i * 30, bottom: i * 30 + 28, left: 0, right: 200, width: 200, height: 28 }; });
  // ...inside the table's own box: since 2026-09-09 only rows inside it take the cue and the drop (the table
  // scrolls with many tags; timeline-tags-scale.test.ts covers a drag past its edge)
  cells[0].parentNode._rect = { top: 0, bottom: 200, left: 0, right: 800, width: 800, height: 200 };
  // grab REMOTEPOOL (index 3) and drop it between alpha and beta (index 1)
  const grab = cells[3];
  grab._listeners.pointerdown({ preventDefault() {}, pointerId: 7 });
  grab._listeners.pointermove({ clientY: 31 });   // over beta's slot
  assert.equal(cells[1].style.borderTop, "2px solid #9cd2ff", "the accent insertion cue rides the target, no rebuild mid-drag");
  grab._listeners.pointerup({});
  assert.equal(posted.length, 1, "the drop posts ONE views write");
  const blob = posted[0];
  assert.deepEqual(blob.tagOrder, ["alpha", "remotepool", "beta", "gamma"],
    "tagOrder carries the union display order — the remote-homed name holds its dragged spot, no cross-kernel write");
  assert.deepEqual(blob.tags.map((t: any) => t.name), ["alpha", "beta", "gamma"],
    "the local array re-sorts to the same order (remote names simply aren't in it)");
  // RELOAD: a rebuild from the posted blob renders the dragged order — the order survives
  const reloaded = JSON.parse(JSON.stringify(blob));
  reloaded.remoteTags = VIEWS.remoteTags;   // the kernel re-joins remoteTags on every push
  assert.deepEqual(viewTagUnion(reloaded).map((u: any) => u.name), ["alpha", "remotepool", "beta", "gamma"],
    "the union renders the persisted order after reload");
  delete g.__rompTimelineSetViews;
  delete g.__rompTimelineEditTag;
});

test("executed: the ordering rule, both mirrors — tagOrder governs, unlisted names follow naturally", () => {
  const v = JSON.parse(JSON.stringify(VIEWS));
  v.tagOrder = ["remotepool", "gamma"];
  assert.deepEqual(viewTagUnion(v).map((u: any) => u.name), ["remotepool", "gamma", "alpha", "beta"],
    "listed names lead in order; unlisted keep natural order after (stable sort)");
  // a user-typed name CAN be a prototype key — the lookup must be null-prototype (found in
  // adversarial review 2026-08-25: `"constructor" in {}` is true via the chain, so a tag named
  // constructor read as always-listed/always-picked through a plain object)
  const proto = { active: "all", tags: [
    { id: "p1", name: "constructor", color: "#DD42FF", members: [] },
    { id: "p2", name: "zed", color: "#4EC9B0", members: [] },
  ], tagOrder: ["zed"] };
  assert.deepEqual(viewTagUnion(proto).map((u: any) => u.name), ["zed", "constructor"],
    "a prototype-key name sorts as UNLISTED (after the ordered), never as index-of-Function");
});

// THE TAB LOCK (T395 round two, LOW 2): the pane reads the strip's store key at each gesture; locked, a pill drag never starts
// (no listeners are armed, nothing posts), a drag whose store flipped to locked mid-gesture posts nothing at its drop, and the
// dialog's row drag is held the same way. Executed here, over the same shim the pill drag above runs on.
function withStore(value: any, fn: () => void) {
  const real = g.localStorage.getItem;
  g.localStorage.getItem = (k: string) => (k === "romp:settings" ? JSON.stringify(value) : null);
  try { fn(); } finally { g.localStorage.getItem = real; }
}
function cellsOf(dlg: any, key: string): any[] {
  const out: any[] = [];
  (function walk(x: any) { for (const c of x.children || []) { if (c[key]) out.push(c); walk(c); } })(dlg);
  return out;
}

test("executed: with the tabs locked a pill drag never starts and posts nothing; the pill says why", () => {
  const posted: any[] = [];
  g.__rompTimelineSetViews = (v: any) => posted.push(v);
  g.__rompTimelineEditTag = () => {};
  withStore({ tabsLocked: true }, () => {
    const panel = drawnPanel();
    panel._openViewsDialog(null);
    const cells = cellsOf(panel._viewsDialog, "_tname");
    assert.equal(cells.length, 4);
    assert.equal(cells[3].style.cursor, "default", "no grab cursor while locked");
    assert.match(String(cells[3].title), /the tabs are locked/, "the pill says why");
    cells[3]._listeners.pointerdown({ preventDefault() {}, pointerId: 7 });
    assert.equal(cells[3]._listeners.pointermove, undefined, "no drag listeners armed: the gesture never began");
    assert.deepEqual(posted, [], "nothing posted");
  });
  delete g.__rompTimelineSetViews; delete g.__rompTimelineEditTag;
});

test("executed: a pill drag whose store flipped to locked mid-gesture posts nothing at its drop", () => {
  const posted: any[] = [];
  g.__rompTimelineSetViews = (v: any) => posted.push(v);
  g.__rompTimelineEditTag = () => {};
  const panel = drawnPanel();
  panel._openViewsDialog(null);
  const cells = cellsOf(panel._viewsDialog, "_tname");
  cells.forEach((c, i) => { c._rect = { top: i * 30, bottom: i * 30 + 28, left: 0, right: 200, width: 200, height: 28 }; });
  cells[0].parentNode._rect = { top: 0, bottom: 200, left: 0, right: 800, width: 800, height: 200 };
  const grab = cells[3];
  grab._listeners.pointerdown({ preventDefault() {}, pointerId: 7 });   // unlocked: the drag begins
  grab._listeners.pointermove({ clientY: 31 });
  withStore({ tabsLocked: true }, () => { grab._listeners.pointerup({}); });   // another window locked before the drop
  assert.deepEqual(posted, [], "the drop writes nothing");
  delete g.__rompTimelineSetViews; delete g.__rompTimelineEditTag;
});

test("executed: with the tabs locked the dialog's row drag never starts, and the row says why", () => {
  const orders: any[] = [];
  g.__rompTimelineWriteOrder = (o: any) => orders.push(o);
  withStore({ tabsLocked: true }, () => {
    const panel = drawnPanel();
    panel._openViewsDialog(null);
    const rows = cellsOf(panel._viewsDialog, "_sid");
    assert.ok(rows.length >= 2, "the membership rows: " + rows.length);
    assert.match(String(rows[0].getAttribute ? rows[0].getAttribute("style") : rows[0].style.cssText || ""), /cursor:default/, "no grab cursor while locked");
    assert.match(String(rows[0].title), /the tabs are locked/, "the row says why");
    rows[1]._listeners.pointerdown({ preventDefault() {}, pointerId: 9 });
    assert.equal(rows[1]._listeners.pointermove, undefined, "no drag listeners armed");
    assert.deepEqual(orders, [], "no order written");
  });
  delete g.__rompTimelineWriteOrder;
});
