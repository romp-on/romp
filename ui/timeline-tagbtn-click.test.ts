// The corner tag button must OPEN ITS MENU under a host that installs only the three DOM helpers
// (createEl/createDiv/createSpan — all the browser and VS Code boots provide; timeline-boot.ts /
// the kernel's _TIMELINE_BOOT). The 2026-08-25 "can't click on it": the menu repaint called
// Obsidian's .empty(), which exists in neither host, so every press threw a TypeError before the
// menu appeared — the button looked fine and did nothing. This test EXECUTES the press over the
// house fake-DOM shim (which, like the real boots, has no Obsidian extras) and asserts the menu
// builds; the source scan below bans the whole class of Obsidian-only helper calls.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

// ---- the house fake-DOM shim (timeline-hidden-stub.test.ts's, + createTextNode: the menu rows
// write real text nodes, and the real hosts of course have it) ----
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) {   // real-DOM semantics: appending an attached node MOVES it (the menu is created on body, then re-appended to the menu host)
      if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); }
      c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 32, height: 18, left: 8, top: 400, right: 40, bottom: 418 }; },
    closest() { return null; }, focus() {},
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
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel } = createRequire(__filename)(viewPath);
const SRC = fs.readFileSync(viewPath, "utf8");

function drawnPanel(): any {
  const now = 1_781_000_000;
  const sess = (id: string, name: string, color: string) => ({
    id, name, color, state: "working", live: true, model: "Opus", effort: "high",
    context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
  });
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({
    now,
    sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    // turns present so draw() runs for real (an empty warm-up keeps the loader up and no corner draws)
    turns: { s1: [{ id: "t1", start: now - 400, end: now - 100, prompt: "do the thing", tid: "f1", mids: [] }] },
    messages: [], judging: [],
    views: { active: "all", tags: [{ id: "g1", name: "infra", color: "#DD42FF", members: ["s2"] }], actives: { timeline: { all: true } } },
  });
  return panel;
}

function cornerBtn(panel: any, txt: string): any {
  return (panel._cornerBar.children || []).find((c: any) => String(c.title || "").includes(txt)) || null;
}

test("executed: a tag-button press BUILDS the menu under a three-helper host (the 2026-08-25 dead button)", () => {
  const panel = drawnPanel();
  const btn = cornerBtn(panel, "filter these lanes by tag");
  assert.ok(btn, "the corner tag button rendered in the persistent HTML layer");
  assert.equal(btn.tag, "button", "a REAL button — round three moved the corner off the SVG");
  const before = g.document.body.children.length;
  btn._listeners.pointerdown({ preventDefault() {}, stopPropagation() {} });   // the press — must not throw
  assert.ok(panel._viewsMenu, "the views menu opened");
  assert.equal(g.document.body.children.length, before + 1, "the menu landed in the host body");
  const labels: string[] = [];
  (function walk(x: any) { for (const c of x.children || []) { if (c.tag === "#text" || c.tag === "span") labels.push(String(c.textContent)); walk(c); } })(panel._viewsMenu);
  for (const want of ["All", "(no tags)", "infra", "Configure tags…"])
    assert.ok(labels.some((l) => l.includes(want)), "menu row present: " + want);
  // a second press on the open menu's button toggles it shut, still without throwing
  btn._listeners.pointerdown({ preventDefault() {}, stopPropagation() {} });
  assert.equal(panel._viewsMenu, null, "re-press toggles shut");
  panel._closeViewsMenu();
});

test("executed: the whole button IS the hit target, wearing the feed's box by value", () => {
  const panel = drawnPanel();
  const btn = cornerBtn(panel, "filter these lanes by tag");
  // a native <button> — the entire box takes the press; no glyph-pad geometry to get wrong
  assert.equal(btn.tag, "button");
  assert.match(String(btn.className), /romp-tl-cbtn/);
  assert.match(String(btn.innerHTML), /svg width="14" height="14"/, "the shared 14px tag glyph, currentColor");
  // the injected corner CSS states the feed footer's values — the executed style node carries them
  const styles = (g.document.head.children || []).filter((c: any) => c.tag === "style").map((c: any) => c.textContent).join("");
  for (const lit of [
    ".romp-tl-cbtn{display:inline-flex;align-items:center;font:inherit;font-size:10.5px;padding:4px 6px;",
    "border:1px solid rgba(255,255,255,0.10);border-radius:6px;",
    "color:var(--vscode-descriptionForeground,#9a9a9a);",
  ]) assert.ok(styles.includes(lit), "corner CSS carries: " + lit);
});

test("executed: narrowed, the button wears the feed's .on dress and the chip renders beside it", () => {
  const panel = drawnPanel();
  const v = { active: "all", tags: [{ id: "g1", name: "infra", color: "#DD42FF", members: ["s2"] }], actives: { timeline: { tags: ["infra"] } } };
  panel.update(Object.assign({}, panel.data, { views: v }));
  const btn = cornerBtn(panel, "filter these lanes by tag");
  assert.match(String(btn.className), /\bon\b/, "the .on class — same mechanics as the feed mount");
  const styles = (g.document.head.children || []).filter((c: any) => c.tag === "style").map((c: any) => c.textContent).join("");
  assert.ok(styles.includes(".romp-tl-cbtn.on{color:var(--accent,#9cd2ff);border-color:var(--accent,#9cd2ff);background:rgba(156,210,255,0.12);opacity:1}"),
    "narrowed = accent border + the feed .on wash, full strength");
  const chip = (panel._cornerBar.children || []).find((c: any) => String(c.className || "") === "romp-tl-chip");
  assert.ok(chip, "the selection's chip renders beside the button");
  assert.equal(chip.style.borderColor, "#DD42FF", "in its tag's colour");
});

test("executed: the corner bar persists across redraws — same signature, same nodes (click-safe by construction)", () => {
  // the SVG is wiped per poll and per live-edge frame; the corner layer is NOT — it rebuilds only
  // when its content signature changes, so a press can never land on a node a redraw just replaced
  const panel = drawnPanel();
  const before = cornerBtn(panel, "filter these lanes by tag");
  panel.update(JSON.parse(JSON.stringify(panel.data)));   // same content, fresh poll
  const after = cornerBtn(panel, "filter these lanes by tag");
  assert.equal(before, after, "an unchanged poll leaves the very same button element in place");
});

test("the view speaks plain DOM: no Obsidian-only helper calls (the hosts install only the three)", () => {
  // .empty()/.setText()/.addClass()/.removeClass()/.toggleClass()/.detach() exist only under
  // Obsidian; a call to any of them is a crash on the web and VS Code timelines — exactly how the
  // tag button died. createEl/createDiv/createSpan stay fine: every boot installs those three.
  for (const bad of [/\.empty\(\)/, /\.setText\(/, /\.addClass\(/, /\.removeClass\(/, /\.toggleClass\(/, /\.detach\(\)/])
    assert.doesNotMatch(SRC, bad, "Obsidian-only helper in the shared view: " + bad);
});
