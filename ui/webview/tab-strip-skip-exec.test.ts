// renderTabs EXECUTED: the unchanged-strip skip, its input list, and what still runs on the skip path — driven
// over a minimal fake DOM (no jsdom), the way models-rev.test.ts and thread-selection-scope.test.ts lift a
// render.ts function. tab-strip-skip.test.ts pins the SHAPE at the source; this file drives the BEHAVIOUR, so a
// change that keeps the pinned text but defeats the skip (a signature reset, a nonce in the signature, an input
// dropped from the per-tab array while its helper call stays) fails here. The strip's pure rules run for real:
// the plan (tab-groups.ts planStrip, over a stored tab-groups blob the test hands it) and the state → class rule
// (tab-state.ts); the group header builder is a stub that records what it was handed, since the signature — not
// the header's DOM — is what this file tests (tab-groups.test.ts executes the header).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { planStrip, parseTabGroups, headWords } from "./tab-groups";
import { tabStateClass, tabDotClass, tabDotTitle, sectionPip, sectionPipMembers, sectionPipTitle } from "./tab-state";
import { newSkeletonState, renderKind } from "./skeleton-tabs";
import { tabChord, miniChord, chordTitle } from "./tab-keys";
import type { TagUnion } from "./session-views";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

/** Enough of Element for the strip's paint: children, classList, dataset, style, listeners (fired by hand). */
class FakeEl {
  tag: string; className: string; children: FakeEl[] = []; parent: FakeEl | null = null;
  dataset: Record<string, string> = {}; styleProps: Record<string, string> = {}; attrs: Record<string, string> = {};
  listeners: Record<string, Function[]> = {}; textContent = ""; title = ""; tabIndex = -1; draggable = false; innerHTML = "";   // innerHTML: the pinned tab's pushpin is set as markup (2026-09-11)
  wipes = 0;   // replaceChildren() calls: the strip's rebuild count when this is #tabs
  style: any; classList: any;
  constructor(tag: string, cls = "") {
    this.tag = tag; this.className = cls;
    const self = this;
    this.style = { display: "", color: "", padding: "", height: "", background: "", animationDelay: "", borderColor: "",
                   setProperty(k: string, v: string) { self.styleProps[k] = v; } };
    this.classList = {
      add(...c: string[]) { for (const x of c) if (!self.has(x)) self.className = (self.className + " " + x).trim(); },
      remove(...c: string[]) { for (const x of c) self.className = self.className.split(/\s+/).filter((y) => y !== x).join(" "); },
      contains(x: string) { return self.has(x); },
      toggle(x: string, on?: boolean) { if (on) this.add(x); else this.remove(x); },
    };
  }
  has(x: string): boolean { return this.className.split(/\s+/).includes(x); }
  appendChild(c: FakeEl): FakeEl { c.parent = this; this.children.push(c); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs: FakeEl[]): void { this.wipes++; this.children = []; this.append(...cs); }
  get firstChild(): FakeEl | null { return this.children[0] ?? null; }
  get lastElementChild(): FakeEl | null { return this.children[this.children.length - 1] ?? null; }   // the dot's hover title lands on the slot just appended
  contains(n: unknown): boolean { return n === this || this.children.some((c) => c.contains(n)); }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; }
  addEventListener(t: string, f: Function): void { (this.listeners[t] ??= []).push(f); }
  fire(t: string, ev: any = {}): void { for (const f of this.listeners[t] ?? []) f(ev); }
  remove(): void { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  tabs(): FakeEl[] { return this.children.filter((c) => c.has("tab") && !c.has("tab-add")); }
  heads(): FakeEl[] { return this.children.filter((c) => c.has("tab-group-head")); }
}

/** A recorded call of the group-header stub: what renderTabs handed makeGroupHead for one section. */
type HeadCall = { name: string | null; color: string; ids: string[]; folded: boolean; active: boolean; hidden: string[] };

type Hooks = {
  FakeEl: typeof FakeEl; bar: FakeEl; mslot: FakeEl | null; only: string; hidden: Set<string>; down: Set<string>;
  notes: Record<string, string>; keyHint: string; lens: unknown; unions: TagUnion[]; tips: unknown[];
  aftermaths: [number, number][]; rowPaints: number; tagSyncs: number; placeholders: number;
  groupsRaw: string | null;   // the stored tab-groups blob the plan reads (localStorage's, in the page)
  overrides: Record<string, string>;   // the bindings store (romp:keys) the badge reads: command id → chord
  pins: Map<string, number>;           // the pinned set (romp:tabpins, sid → slot): a pinned tab wears the pushpin and does not drag
  tabChord: typeof tabChord; miniChord: typeof miniChord; chordTitle: typeof chordTitle;
  phone: boolean;             // the phone layout: the plan is the flat strip there
  heads: HeadCall[];          // every group header the paint minted, in order
  planStrip: typeof planStrip; parseTabGroups: typeof parseTabGroups; headWords: typeof headWords;
  tabStateClass: typeof tabStateClass; tabDotClass: typeof tabDotClass; tabDotTitle: typeof tabDotTitle; sectionPip: typeof sectionPip; sectionPipMembers: typeof sectionPipMembers; sectionPipTitle: typeof sectionPipTitle;
  newSkeletonState: typeof newSkeletonState; renderKind: typeof renderKind;   // the skeleton strip (2026-09-07): empty here, so every listed id is a loaded tab or a placeholder
  skeletons: number;          // skeleton tabs minted (none expected: the set stays empty in these worlds)
};
type Api = {
  renderTabs: () => void; sig: () => string; folded: () => Set<string>;
  set: (patch: Record<string, unknown>) => void; pending: () => { renderPendingAfterRename: boolean; renderPendingWhilePressed: boolean };
};

// renderTabs + stripAftermath, transpiled (TS → JS) with esbuild at run time — required dynamically so the test
// bundle does not try to bundle esbuild itself (models-rev.test.ts's pattern).
function lift(): (hooks: Hooks) => Api {
  const a = RENDER.indexOf("function renderTabs() {"), b = RENDER.indexOf("// Right-click context menu on a tab.", a);
  assert.ok(a > 0 && b > a, "anchors not found — renderTabs or the context-menu comment moved; re-anchor");
  // the chip, the drag listeners and the context gauge live in helpers above renderTabs, shared with the
  // skeleton tab (2026-09-07): lifted for real, so the paint wears the state class and a dragstart resets the signature
  const h0 = RENDER.indexOf("function applyTabStatus("), h1 = RENDER.indexOf("// SKELETON tab (2026-09-07)", h0);
  const g0 = RENDER.indexOf("function appendTabCtxGauge(", h1), g1 = RENDER.indexOf("// A loading PLACEHOLDER tab", g0);
  assert.ok(h0 > 0 && h1 > h0 && g0 > h1 && g1 > g0, "anchors not found — applyTabStatus / wireTabDrag / appendTabCtxGauge or the skeleton-tab / placeholder comments moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(h0, h1) + RENDER.slice(g0, g1) + RENDER.slice(a, b), { loader: "ts" }).code;
  const prelude = `
    let renameActive = false, renderPendingAfterRename = false, tabPointerHeld = false, renderPendingWhilePressed = false;
    let tabStripSig = "", activeId = null, peekId = null, allHiddenBlanked = false, draggedId = null, draggedEl = null, tabDragCommitted = false;
    let order = [], closingTabs = new Set(), tabMeta = new Map(), sessions = new Map(), views = new Map();
    let collapsedTabIds = new Set(), draggedGroup = null, provisionalId = null, provisionalTags = [];
    // stripGroupRows mirrors the default. Its break site is never reached here: FakeEl has no childElementCount,
    // so the gate's last operand is undefined whatever the setting, hence no makeRowBreak stub. Give FakeEl a
    // childElementCount and this prelude needs one.
    let settings = { tabCtx: "over50", stripGroupRows: true, theme: "classic", colormap: "aurora" };
    const H = HOOKS;
    const el = (tag, cls) => new H.FakeEl(tag, cls);
    const document = { activeElement: null,
      getElementById: (id) => id === "tabs" ? H.bar : id === "mtag-slot" ? H.mslot : null,
      createTextNode: (t) => { const n = new H.FakeEl("#text"); n.textContent = t; return n; } };
    const auditTabOrder = () => {}; const onlyTag = () => H.only; const matchesOnly = (name, only) => name.includes(only);
    const tabInView = (id) => id === peekId || !H.hidden.has(id);
    const setActive = () => {}; const setTimeout = () => 0;
    // the section-at-a-glance view's readers on the strip, inert: the plan the view reads (lastStripItems), the
    // section the pane shows (snapView, null: no view open, so stripAftermath's follow does nothing), and the
    // view's own painter and focus probe (never reached while snapView is null)
    let lastStripItems = [], snapView = null;
    const renderSnapshot = () => false; const snapshotHoldsFocus = () => false; const showActive = () => {};
    const titleWithKey = () => H.keyHint; const surfaceLens = () => H.lens; const effViews = () => null; const viewTagUnion = () => H.unions;
    const hostIsDown = (id) => H.down.has(id); const hostDownNote = (id) => H.notes[id] ?? "";
    function makePlaceholderTab(id) { const t = el("div", "tab tab-placeholder"); t.dataset.id = id; H.placeholders++; return t; }
    const skeletonTabs = H.newSkeletonState(); const renderKind = H.renderKind;
    function makeSkeletonTab(id) { const t = el("div", "tab tab-skeleton"); t.dataset.id = id; H.skeletons++; return t; }
    // the sectioned strip's pure rules, for real; the header builder a recorder
    const planStrip = H.planStrip; const headWords = H.headWords;
    const readTabGroups = (u) => H.parseTabGroups(H.groupsRaw, u);
    const tabGroups = () => readTabGroups(H.unions); const writeTabGroups = () => {};
    const phoneLayout = () => H.phone;
    const tabStateClass = H.tabStateClass, tabDotClass = H.tabDotClass, tabDotTitle = H.tabDotTitle, sectionPip = H.sectionPip, sectionPipMembers = H.sectionPipMembers, sectionPipTitle = H.sectionPipTitle;   // tabDotClass: the state-dot slot every tab carries (the tab-strip fix, 2026-09-08); tabDotTitle: what the slot says on hover
    const mentionRosterChanged = () => {};   // the @-mention roster hook at the top of renderTabs: not the strip's (composer-mention-pane.test.ts)
    function makeGroupHead(sec, folded, active, hidden) {
      const h = el("div", "tab-group-head" + (folded ? " collapsed" : ""));
      h.dataset.group = String(sec.name);
      H.heads.push({ name: sec.name, color: sec.color, ids: sec.ids.slice(), folded, active, hidden: hidden.slice() });
      return h;
    }
    const onTabKey = () => {}; const dragImageBlank = () => el("div"); const hideTabTip = () => {}; const syncComposerPh = () => {}; const snapshotDragGeometry = () => {};   // syncComposerPh: the rebuild re-syncs the composer's name overlay (T335), inert here
    const flipTabs = (f) => f(); const applyCompactSweep = () => {};
    const hostNameNodes = (name) => [document.createTextNode(name)]; const fadedColor = (h) => h;
    const tabCtxGauge = () => el("span", "tab-ctx"); const pickTone = (a, b) => b ?? a;
    const fedMissing = false;   // the page has its federation manager (render.ts fedMissing, 2026-09-10): tabs drag as before
    // the tab's hot key badge (2026-09-10): the bindings store the strip reads once per render, and the badge's REAL
    // helpers (tab-keys.ts) — so the node the paint mints is the one the pane paints
    const loadOverrides = () => H.overrides; const IS_MAC = false;
    const tabChord = H.tabChord, miniChord = H.miniChord, chordTitle = H.chordTitle;
    const loadTabPins = () => H.pins;   // the pinned set (2026-09-10), read once per render
    const pinSvg = (size) => '<svg data-pin="' + size + '"></svg>';   // the pushpin drawing the pinned tab wears (2026-09-11): a stand-in, its size recorded
    const localStorage = null;          // the store handle the paint passes to loadTabPins; the knob above answers instead
    const showTabTip = (tab, s) => { H.tips.push(s); }; const toggleLedgerCollapsed = () => {}; const showTabMenu = () => {}; const openPicker = () => {};
    const tagMenuButton = () => el("span", "tag-btn"); const openTagMenu = () => {}; const postLens = () => {}; const vscodeApi = null;
    const syncTagFilter = () => { H.tagSyncs++; }; const paintTabRowLines = () => { H.rowPaints++; }; const ensureTabRowObserver = () => {};
    const focusActiveTab = () => {}; const syncNoSessionsPlaceholder = (v, t) => { H.aftermaths.push([v, t]); };
  `;
  const epilogue = `
    return { renderTabs, sig: () => tabStripSig, folded: () => collapsedTabIds,
      set: (p) => { for (const k of Object.keys(p)) {
        if (k === "activeId") activeId = p[k]; else if (k === "peekId") peekId = p[k]; else if (k === "order") order = p[k];
        else if (k === "sessions") sessions = p[k]; else if (k === "tabMeta") tabMeta = p[k]; else if (k === "settings") settings = p[k];
        else if (k === "views") views = p[k]; else if (k === "renameActive") renameActive = p[k]; else if (k === "tabPointerHeld") tabPointerHeld = p[k];
        else if (k === "provisionalId") provisionalId = p[k]; else if (k === "provisionalTags") provisionalTags = p[k];
        else throw new Error("unknown knob " + k); } },
      pending: () => ({ renderPendingAfterRename, renderPendingWhilePressed }) };
  `;
  return new Function("HOOKS", prelude + js + epilogue) as (hooks: Hooks) => Api;
}

const session = (name: string, state: string, extra: Record<string, unknown> = {}) =>
  ({ name, color: { bg: "#336699", fg: "#ffffff" }, status: { state, ...extra } });
/** A tag union as viewTagUnion builds one: the plan reads name, color, members and the local id. */
const union = (name: string, color: string, members: string[]): TagUnion =>
  ({ name, color, members, ids: ["t-" + name], localId: "t-" + name, locals: [], remotes: [] });
const groups = (patch: Record<string, unknown>) => JSON.stringify({ on: true, collapsed: [], expanded: [], pinned: [], ...patch });

/** Two landed sessions and one placeholder, the first active; every knob at a quiet default (no tags: the
 *  flat strip). */
function world(): { H: Hooks; api: Api; sessions: Map<string, any>; tabMeta: Map<string, any>; settings: any } {
  const H: Hooks = { FakeEl, bar: new FakeEl("div"), mslot: null, only: "", hidden: new Set(), down: new Set(), notes: {},
                     keyHint: "Open a session (K)", lens: { all: true }, unions: [], tips: [], aftermaths: [], rowPaints: 0, tagSyncs: 0, placeholders: 0,
                     groupsRaw: null, phone: false, heads: [], overrides: {}, pins: new Map(),
                     planStrip, parseTabGroups, headWords, tabStateClass, tabDotClass, tabDotTitle, sectionPip, sectionPipMembers, sectionPipTitle,
                     newSkeletonState, renderKind, skeletons: 0, tabChord, miniChord, chordTitle };
  const api = lift()(H);
  const sessions = new Map<string, any>([["a", session("web", "ready")], ["b", session("api", "working")]]);
  const tabMeta = new Map<string, any>([["p", { name: "tests", color: { bg: "#112233", fg: "#ffffff" } }]]);
  const settings = { tabCtx: "over50", stripGroupRows: true, theme: "classic", colormap: "aurora" };
  api.set({ order: ["a", "b", "p"], sessions, tabMeta, settings, activeId: "a" });
  return { H, api, sessions, tabMeta, settings };
}

/** The same world sectioned: `a` and `b` home in the tag `backend`, the placeholder `p` untagged, and the
 *  UNTAGGED placeholder active — so the backend section may fold (the active tab's section never does). */
function sectionedWorld() {
  const w = world();
  w.H.unions = [union("backend", "#aa0000", ["a", "b"])];
  w.api.set({ activeId: "p" });
  return w;
}

/** Drive one change: it repaints the strip exactly once, and an unchanged render after it does not. */
function repaintsOnce(H: Hooks, api: Api, what: string, change: () => void): void {
  change();
  const before = H.bar.wipes;
  api.renderTabs();
  assert.equal(H.bar.wipes, before + 1, what + " changed: the strip repaints");
  api.renderTabs();
  assert.equal(H.bar.wipes, before + 1, what + " unchanged since: no repaint");
}

test("an unchanged strip is not rebuilt, and the aftermath runs on both paths", () => {
  const { H, api } = world();
  api.renderTabs();
  assert.equal(H.bar.wipes, 1);
  assert.equal(H.rowPaints, 1);
  assert.deepEqual(H.aftermaths, [[3, 3]]);
  const nodes = [...H.bar.children];
  assert.equal(H.bar.tabs().length, 3, "two sessions and a placeholder");
  assert.equal(H.heads.length, 0, "no tags: the flat strip, no header");
  api.renderTabs();
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "the second and third render found nothing changed: no wipe");
  assert.equal(H.rowPaints, 1, "no layout read either");
  assert.equal(H.placeholders, 1, "no node minted");
  assert.deepEqual(H.bar.children, nodes, "the same DOM nodes");
  assert.equal(H.aftermaths.length, 3, "the no-sessions placeholder reconciles on the skip path too");
});

test("every input the strip paints repaints it, once, when it changes", () => {
  const { H, api, sessions, tabMeta, settings } = world();
  api.renderTabs();
  const a = sessions.get("a");
  const changes: [string, () => void][] = [
    ["a session's state", () => { a.status.state = "working"; }],
    ["a state's tab class with the state unchanged (blocked: transient → on-you)", () => { a.status = { state: "blocked" }; api.renderTabs(); a.status = { state: "blocked", apiRefusal: true }; }],
    ["a session's name", () => { a.name = "web2"; }],
    ["a session's color", () => { a.color = { bg: "#993366", fg: "#ffffff" }; }],
    ["faded", () => { a.status.faded = true; }],
    ["context percent", () => { a.status.ctx = "72"; }],
    ["context color", () => { a.status.ctxColor = [200, 100, 0]; }],
    ["context tone", () => { a.status.ctxTone = [20, 20, 220]; }],
    ["the viewer flag", () => { a.sub = true; }],
    ["the host-down mark", () => { H.down.add("a"); }],
    ["the host-down note while the mark stands", () => { H.notes.a = "web-host is disconnected, last reached 10:00"; }],
    ["the active tab", () => { api.set({ activeId: "b" }); }],
    ["the peek tab", () => { api.set({ peekId: "b" }); }],
    ["the order", () => { api.set({ order: ["b", "a", "p"] }); }],
    ["a tab hidden by the views filter", () => { H.hidden.add("p"); }],
    ["the context-gauge setting", () => { settings.tabCtx = "always"; }],
    ["the one-group-per-row setting", () => { settings.stripGroupRows = false; }],
    ["the theme", () => { settings.theme = "yatharth"; }],
    ["the colormap", () => { settings.colormap = "hawaii"; }],
    ["the + tab's key hint", () => { H.keyHint = "Open a session (J)"; }],
    ["the tag lens", () => { H.lens = { all: false, tags: ["t1"] }; }],
    ["the tag unions (a tag holding no visible tab: the filter chips read it)", () => { H.unions = [union("u", "#000000", ["zz"])]; }],
    ["a placeholder's name", () => { H.hidden.delete("p"); api.renderTabs(); tabMeta.get("p").name = "tests2"; }],
    ["a placeholder's color", () => { tabMeta.get("p").color = { bg: "#445566", fg: "#000000" }; }],
    ["a placeholder's session landing", () => { sessions.set("p", session("tests2", "opening")); }],
    // the hot key badge (2026-09-10): bound, rebound, and removed (a deliberate "" unbind, which paints no badge)
    ["a tab's hot key", () => { H.overrides = { "session.hotkey.a": "Ctrl+1" }; }],
    ["the hot key rebound", () => { H.overrides = { "session.hotkey.a": "Alt+Shift+K" }; }],
    ["the hot key removed (a deliberate unbind)", () => { H.overrides = { "session.hotkey.a": "" }; }],
    // a pin (2026-09-10): on, and off again
    ["a tab pinned", () => { H.pins = new Map([["a", 0]]); }],
    ["the tab unpinned", () => { H.pins = new Map(); }],
  ];
  for (const [what, change] of changes) repaintsOnce(H, api, what, change);
});

test("the hot key badge is painted after the gauge and before the close ×, minified, its full spelling as the tooltip and the accessible name", () => {
  const { H, api, sessions, settings } = world();
  settings.tabCtx = "always"; sessions.get("a").status.ctx = "40";   // a gauge on tab a, so the badge has something to sit right of
  const a = () => H.bar.tabs().find((t) => t.dataset.id === "a")!;
  api.renderTabs();
  assert.equal(a().children.filter((c) => c.has("tab-key")).length, 0, "no hot key, no badge");
  H.overrides = { "session.hotkey.a": "Alt+Shift+K" };
  api.renderTabs();
  const kids = a().children.map((c) => c.className.split(/\s+/)[0]);
  const ki = kids.indexOf("tab-key");
  assert.ok(ki > 0, "a badge: " + kids.join(","));
  assert.equal(kids[ki - 1], "tab-ctx", "right of the gauge");
  assert.equal(kids[ki + 1], "tab-close", "left of the close ×");
  const k = a().children[ki];
  assert.equal(k.textContent, "⌥⇧K");
  assert.equal(k.title, "hot key: Alt+Shift+K");
  assert.equal(k.attrs["aria-label"], "hot key: Alt+Shift+K", "the glyphs read aloud as the full spelling");
  assert.equal(H.bar.tabs().find((t) => t.dataset.id === "b")!.children.filter((c) => c.has("tab-key")).length, 0, "only the bound tab wears one");
  H.overrides = { "session.hotkey.a": "" };
  api.renderTabs();
  assert.equal(a().children.filter((c) => c.has("tab-key")).length, 0, "an unbind takes the badge away");
});

test("the sectioned strip: every input a group header paints repaints it, once, when it changes", () => {
  const { H, api, sessions } = sectionedWorld();
  api.renderTabs();
  assert.equal(H.bar.wipes, 1);
  assert.deepEqual(H.heads.map((h) => [h.name, h.folded, h.active, h.hidden]), [["backend", false, false, []], [null, false, true, []]],
    "a backend header, open, then the untagged separator (which holds the active placeholder)");
  const headNodes = H.bar.heads();
  assert.equal(headNodes.length, 2);
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "equal plan, equal tabs: skipped");
  assert.deepEqual(H.bar.heads(), headNodes, "the header nodes were kept");
  const changes: [string, () => void][] = [
    ["a section folds", () => { H.groupsRaw = groups({ collapsed: ["backend"] }); }],
    ["a hidden member's state (the folded header's pip)", () => { sessions.get("a").status.state = "blocked"; }],
    ["a hidden member's name (the pip's tooltip)", () => { sessions.get("a").name = "web-renamed"; }],
    ["a member pinned to show under its folded header", () => { H.groupsRaw = groups({ collapsed: ["backend"], pinned: [{ sid: "b", name: "backend", id: "t-backend" }] }); }],
    ["the section unfolds", () => { H.groupsRaw = groups({}); }],
    ["the tag's color (the header's chip)", () => { H.unions = [union("backend", "#00aa00", ["a", "b"])]; }],
    ["a tab's home tag (membership)", () => { H.unions = [union("backend", "#00aa00", ["a"])]; }],
    ["the group order", () => { H.unions = [union("frontend", "#0000aa", ["b"]), union("backend", "#00aa00", ["a"])]; api.renderTabs(); H.unions = [union("backend", "#00aa00", ["a"]), union("frontend", "#0000aa", ["b"])]; }],
    ["the active tab's section (unfoldable while it holds it)", () => { H.groupsRaw = groups({ collapsed: ["backend"] }); api.renderTabs(); api.set({ activeId: "a" }); }],
    ["a provisional tab's tags (it sections under its future home)", () => { api.set({ provisionalId: "p", provisionalTags: ["frontend"] }); }],
    ["sectioning switched off", () => { H.groupsRaw = groups({ on: false }); }],
    ["the phone layout (the flat strip there)", () => { H.groupsRaw = groups({}); api.renderTabs(); H.phone = true; }],
  ];
  for (const [what, change] of changes) repaintsOnce(H, api, what, change);
});

test("the folded set the plan yields is published on the skip path too (keyboard cycling reads it)", () => {
  const { H, api } = sectionedWorld();
  H.groupsRaw = groups({ collapsed: ["backend"] });
  api.renderTabs();
  assert.equal(H.bar.wipes, 1);
  assert.deepEqual([...api.folded()].sort(), ["a", "b"], "both members hidden under the folded header");
  assert.deepEqual(H.heads.map((h) => [h.name, h.folded, h.hidden]), [["backend", true, ["a", "b"]], [null, false, []]]);
  assert.equal(H.bar.tabs().length, 1, "only the untagged placeholder is a tab node");
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "skipped");
  assert.deepEqual([...api.folded()].sort(), ["a", "b"], "the set stands across the skip");
});

test("the paint wears the shared state → class rule (tab-state.ts), and the signature reads the same rule", () => {
  const { H, api, sessions } = world();
  sessions.get("a").status = { state: "blocked", apiSpendLimit: true };
  api.renderTabs();
  const tab = H.bar.tabs().find((t) => t.dataset.id === "a")!;
  assert.ok(tab.has("tab-blocked") && !tab.has("tab-retrying"), "an on-you block: alarm-red");
  assert.equal(tabStateClass(sessions.get("a").status), "tab-blocked");
  // the same state under a different class (the transient API error) is a different signature
  sessions.get("a").status = { state: "blocked" };
  api.renderTabs();
  assert.equal(H.bar.wipes, 2);
  const tab2 = H.bar.tabs().find((t) => t.dataset.id === "a")!;
  assert.ok(tab2.has("tab-retrying") && !tab2.has("tab-blocked"), "a transient API error auto-retries: amber");
});

test("the dot slot explains its state on hover: a visible dot carries the feed's phrase for that state, the hidden slot says nothing", () => {
  // the phrases are the feed's (feed.ts DOT_TIP), read from its source so the two surfaces cannot drift apart
  const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
  const at = FEED.indexOf("const DOT_TIP");
  const tip: Record<string, string> = Object.fromEntries([...FEED.slice(at, FEED.indexOf("};", at)).matchAll(/^\s*(work|await|unknown): "([^"]+)",/gm)].map((m) => [m[1], m[2]]));
  assert.deepEqual(Object.keys(tip).sort(), ["await", "unknown", "work"]);
  const sep = /^working(\s\S\s)/.exec(tip.work)![1];   // the feed's separator: the opening phrase (no feed twin) keeps the feed's shape
  const { H, api, sessions } = world();
  api.renderTabs();
  // the dot is found BY CLASS: the label, the context gauge and the close button follow it, so once the paint is
  // done it is not the tab's last child (it is at the moment applyTabStatus writes the title)
  const dot = (id: string): FakeEl => {
    const d = H.bar.tabs().find((t) => t.dataset.id === id)!.children.filter((c) => c.has("tab-dot"));
    assert.equal(d.length, 1, id + ": one dot slot");
    return d[0];
  };
  assert.ok(!dot("b").has("none"), "working: a visible dot");
  assert.equal(dot("b").title, tip.work, "the working dot speaks the feed's working phrase");
  assert.ok(dot("a").has("none"), "ready: the hidden slot");
  assert.equal(dot("a").title, "", "the hidden slot says nothing");
  const a = sessions.get("a");
  a.status = { state: "awaitingBg" }; api.renderTabs();
  assert.ok(dot("a").has("await")); assert.equal(dot("a").title, tip.await, "awaiting background work: the feed's awaiting phrase");
  a.status = {}; api.renderTabs();
  assert.ok(dot("a").has("unknown")); assert.equal(dot("a").title, tip.unknown, "no state: the feed's unknown phrase");
  a.status = { state: "opening" }; api.renderTabs();
  assert.ok(dot("a").has("opening")); assert.equal(dot("a").title, "opening" + sep + "this session is still starting up", "opening: the strip's own phrase (the feed draws no opening pip)");
  a.status = { state: "compacting" }; api.renderTabs();
  const tabA = H.bar.tabs().find((t) => t.dataset.id === "a")!;
  assert.equal(tabA.children.filter((c) => c.has("tab-dot")).length, 0, "compacting: no dot");
  assert.ok(tabA.children.find((c) => c.has("tab-compacting-bar"))!.title.startsWith("compacting"), "the bar carries its own title");
});

test("a render held by the pressed-tab or rename guard is not lost to the skip", () => {
  const { H, api, sessions } = world();
  api.renderTabs();
  sessions.get("a").name = "renamed";
  api.set({ tabPointerHeld: true });
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "held: no rebuild mid-click");
  assert.equal(api.pending().renderPendingWhilePressed, true);
  api.set({ tabPointerHeld: false });
  api.renderTabs();
  assert.equal(H.bar.wipes, 2, "released: the change that arrived while held is painted");
  sessions.get("b").name = "renamed too";
  api.set({ renameActive: true });
  api.renderTabs();
  assert.equal(H.bar.wipes, 2);
  assert.equal(api.pending().renderPendingAfterRename, true);
  api.set({ renameActive: false });
  api.renderTabs();
  assert.equal(H.bar.wipes, 3);
});

test("a tab drag resets the signature: the next render rebuilds even with nothing else changed", () => {
  const { H, api } = world();
  api.renderTabs();
  api.renderTabs();
  assert.equal(H.bar.wipes, 1);
  H.bar.tabs().find((t) => t.dataset.id === "a")!.fire("dragstart", { dataTransfer: null });
  api.renderTabs();
  assert.equal(H.bar.wipes, 2, "the drag live-reordered the DOM outside renderTabs");
});

test("the hover tooltip reads the session fresh: a tab node outlives a frame that replaced the session object", () => {
  const { H, api, sessions } = world();
  api.renderTabs();
  const fresh = session("web", "ready");   // same painted fields, new object (a kernel frame's upsert)
  sessions.set("a", fresh);
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "equal signature: the node was kept");
  H.bar.tabs().find((t) => t.dataset.id === "a")!.fire("mouseenter");
  assert.equal(H.tips.length, 1);
  assert.equal(H.tips[0], fresh, "the tooltip got the session that is current, not the one the node was built from");
});

test("the mobile tag slot mounts once, and an empty slot is a reason to rebuild", () => {
  const { H, api } = world();
  H.mslot = new FakeEl("div");
  api.renderTabs();
  assert.equal(H.mslot.children.length, 2, "button + chips mounted");
  assert.equal(H.bar.wipes, 1);
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "mounted slot, equal strip: skipped");
  assert.equal(H.mslot.children.length, 2, "not mounted twice");
  H.mslot.children = [];
  api.renderTabs();
  assert.equal(H.bar.wipes, 2, "an empty slot forces the rebuild that mounts it");
  assert.equal(H.mslot.children.length, 2);
});

test("the all-hidden blank lands on the skip path when the active view appears between two equal strips", () => {
  const { H, api } = world();
  H.hidden.add("a"); H.hidden.add("b"); H.hidden.add("p");
  const views = new Map<string, any>();
  api.set({ views });
  api.renderTabs();
  assert.equal(H.bar.wipes, 1);
  assert.deepEqual(H.aftermaths.at(-1), [0, 3], "no visible tab, three sessions");
  const av = { el: new FakeEl("div") };
  views.set("a", av);   // the lazily built active view
  api.renderTabs();
  assert.equal(H.bar.wipes, 1, "equal strip: skipped");
  assert.equal(av.el.style.display, "none", "the transcript is blanked from the aftermath, not the rebuild");
  H.hidden.delete("a");
  api.renderTabs();
  assert.equal(H.bar.wipes, 2);
  assert.equal(av.el.style.display, "", "restored once anything is visible");
});

test("a pinned tab wears the pushpin after its hot key, the pinned class and no draggable flag; the others drag as before", () => {
  const { H, api } = world();
  const tab = (id: string) => H.bar.tabs().find((t) => t.dataset.id === id)!;
  api.renderTabs();
  assert.equal(tab("a").draggable, true); assert.equal(tab("a").has("pinned"), false);
  assert.equal(tab("a").children.filter((c) => c.has("tab-pin")).length, 0, "no pin, no glyph");
  H.pins = new Map([["a", 0]]);
  H.overrides = { "session.hotkey.a": "Alt+Shift+K" };   // a hot key too: the pin sits AFTER the badge
  api.renderTabs();
  assert.equal(tab("a").has("pinned"), true);
  assert.equal(tab("a").draggable, false, "a pinned tab starts no drag");
  const pin = tab("a").children.filter((c) => c.has("tab-pin"));
  assert.equal(pin.length, 1);
  assert.equal(pin[0].title, "Pinned — it stays where it is");
  assert.equal(pin[0].innerHTML, '<svg data-pin="11"></svg>', "the menu's pushpin, at the tab's size");
  const kids = tab("a").children.map((c) => c.className);
  assert.ok(kids.findIndex((k) => k.includes("tab-pin")) > kids.findIndex((k) => k.includes("tab-key")), "after the hot key badge: " + kids.join(","));
  assert.equal(tab("b").draggable, true, "the other tab is untouched"); assert.equal(tab("b").has("pinned"), false);
  H.pins = new Map(); H.overrides = {};
  api.renderTabs();
  assert.equal(tab("a").has("pinned"), false); assert.equal(tab("a").draggable, true);
  assert.equal(tab("a").children.filter((c) => c.has("tab-pin")).length, 0, "unpinned: the glyph goes");
});
