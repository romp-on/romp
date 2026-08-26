// TAG DRAG-TO-REORDER (the user 2026-08-25): grab a pill in the Sessions & tags dialog's tag
// table to put the tags in your order. The drop writes tagOrder — the union DISPLAY order,
// viewer-side, so a REMOTE-HOMED tag holds its dragged position without any cross-kernel write —
// and re-sorts the local tags array to match (the natural store for local-only readers). This
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
