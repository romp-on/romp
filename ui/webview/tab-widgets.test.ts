// The tab-title widget registry (T379, the user 2026-09-12): registration order is the default composition order, the
// stored prefs decide which widgets a tab carries and with which options, the older tabCtx mode is the context bar's
// MIRROR both ways, and the strip and the settings row draw a widget through the same render. Executed on a tiny DOM
// (document.createElement stubbed), so a widget's DOM is read, never inferred from source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
const STRIP_CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
import type { WidgetStatus, TabWidgetPrefs } from "./tab-widgets";

type El = { tag: string; className: string; textContent: string; title: string; attrs: Record<string, string>; children: El[]; style: Record<string, string>;
            appendChild: (c: El) => El; setAttribute: (k: string, v: string) => void };
function mkEl(tag: string): El {
  const e: El = { tag, className: "", textContent: "", title: "", attrs: {}, children: [], style: {},
    appendChild: (c) => { e.children.push(c); return c; }, setAttribute: (k, v) => { e.attrs[k] = v; } };
  return e;
}
const store = new Map<string, string>();
(globalThis as any).document = { createElement: mkEl };
(globalThis as any).localStorage = { getItem: (k: string) => store.has(k) ? store.get(k)! : null, setItem: (k: string, v: string) => { store.set(k, v); }, removeItem: (k: string) => { store.delete(k); } };
(globalThis as any).navigator = { platform: "Linux x86_64" };

// eslint-disable-next-line @typescript-eslint/no-var-requires
const W = require("./tab-widgets") as typeof import("./tab-widgets");
// eslint-disable-next-line @typescript-eslint/no-var-requires
const S = require("./settings") as typeof import("./settings");

const classes = (e: El) => e.className.split(/\s+/).filter(Boolean);
const compose = (slot: "before" | "after", status: WidgetStatus, prefs: TabWidgetPrefs, sid = "11111111-2222-3333-4444-555555555555") => {
  const tab = mkEl("div"); W.composeTabWidgets(tab as unknown as HTMLElement, slot, sid, status, prefs); return tab.children;
};
const P = (p: Partial<TabWidgetPrefs> = {}): TabWidgetPrefs => ({ on: {}, order: [], opts: {}, ...p });

test("the three built-in widgets register in order: the dot before the name, the context bar and the hot key after it; all on by default", () => {
  assert.deepEqual(W.tabWidgets().map((w) => [w.id, w.slot, w.defaultOn]), [["dot", "before", true], ["ctx", "after", true], ["hotkey", "after", true]]);
  assert.deepEqual(W.tabWidgets().map((w) => w.label), ["Status dot", "Context bar", "Hot key"]);
  assert.ok(W.tabWidgets().every((w) => w.description.length > 0 && !/\bfleet\b/i.test(w.description)));
});

test("registration by id replaces; a contributed widget lands after the built-ins in the default order", () => {
  W.registerTabWidget({ id: "mark", label: "Demo mark", description: "a synthetic mark", defaultOn: false, slot: "after", render: () => { const e = mkEl("span"); e.className = "tab-mark"; return e as unknown as HTMLElement; } });
  assert.deepEqual(W.tabWidgets().map((w) => w.id), ["dot", "ctx", "hotkey", "mark"]);
  W.registerTabWidget({ id: "mark", label: "Demo mark", description: "a synthetic mark, again", defaultOn: false, slot: "after", render: () => null });
  assert.equal(W.tabWidgets().length, 4, "the same id replaces, never duplicates");
  assert.equal(W.tabWidget("mark")!.description, "a synthetic mark, again");
});

test("the dot: the state rule's classes, hidden when idle by default, a quiet grey dot on the option; compacting yields nothing (its bar takes the slot)", () => {
  assert.deepEqual(compose("before", { state: "working" }, P()).map(classes), [["tab-dot"]]);
  assert.deepEqual(compose("before", { state: "awaitingBg" }, P()).map(classes), [["tab-dot", "await"]]);
  assert.deepEqual(compose("before", {}, P()).map(classes), [["tab-dot", "unknown"]]);
  assert.deepEqual(compose("before", { state: "ready" }, P()).map(classes), [["tab-dot", "none"]], "the slot is laid out in every state (T262g), hidden");
  assert.deepEqual(compose("before", { state: "ready" }, P({ opts: { dot: { idle: "grey" } } })).map(classes), [["tab-dot", "idle"]], "the option: a quiet dot when idle");
  assert.equal(compose("before", { state: "ready" }, P({ opts: { dot: { idle: "grey" } } }))[0].title, "idle");
  assert.equal(compose("before", { state: "working" }, P())[0].title, "working — a turn is running right now", "the feed's words on hover");
  assert.deepEqual(compose("before", { state: "compacting" }, P()), []);
  assert.deepEqual(compose("before", { state: "working" }, P({ on: { dot: false } })), [], "switched off: no slot at all");
  assert.deepEqual(compose("before", { state: "ready" }, P({ opts: { dot: { idle: "purple" } } })).map(classes), [["tab-dot", "none"]], "an unknown stored option falls to the default");
});

test("the context bar: from 50 percent by default, always on the option, never while compacting or closed, off when switched off", () => {
  const bar = (status: WidgetStatus, prefs = P()) => compose("after", status, prefs).filter((c) => classes(c).includes("tab-ctx"));
  assert.equal(bar({ state: "working", ctx: "62%" }).length, 1);
  assert.equal(bar({ state: "working", ctx: "40%" }).length, 0, "under half full: quiet");
  assert.equal(bar({ state: "working", ctx: "40%" }, P({ opts: { ctx: { show: "always" } } })).length, 1);
  assert.equal(bar({ state: "compacting", ctx: "62%" }).length, 0);
  assert.equal(bar({ state: "closed", ctx: "62%" }).length, 0);
  assert.equal(bar({ state: "working" }).length, 0, "no ctx reading: no bar");
  assert.equal(bar({ state: "working", ctx: "62%" }, P({ on: { ctx: false } })).length, 0);
  const g = bar({ state: "working", ctx: "62%", ctxColor: [10, 20, 30] })[0];
  assert.equal(g.children[0].className, "tab-ctx-fill");
  assert.equal(g.children[0].style.height, "62%");
  assert.equal(g.children[0].style.background, "rgb(10,20,30)", "the kernel's colormap colour rides the fill");
  assert.equal(g.title, "context 62% used");
});

test("the hot key: nothing until a hot key is assigned; the tab hot-key store shape (a set of sids, a keybinding override per sid) yields the keycap at its shortest", () => {
  const sid = "11111111-2222-3333-4444-555555555555";
  assert.deepEqual(compose("after", { state: "working" }, P()).filter((c) => classes(c).includes("tab-key")), [], "no set: no keycap");
  store.set(W.TABKEYS_KEY, JSON.stringify({ [sid]: "web" }));
  assert.deepEqual(compose("after", { state: "working" }, P()).filter((c) => classes(c).includes("tab-key")), [], "in the set but no chord bound: no keycap");
  store.set("romp:keys", JSON.stringify({ [W.HOTKEY_PREFIX + sid]: "Ctrl+Shift+1" }));
  const keys = compose("after", { state: "working" }, P()).filter((c) => classes(c).includes("tab-key"));
  assert.equal(keys.length, 1);
  assert.equal(keys[0].textContent, "⌃⇧1", "symbols, no separators (Ctrl is control everywhere)");
  assert.match(keys[0].title, /^hot key Ctrl\+Shift\+1/);
  assert.equal(keys[0].attrs["aria-label"], keys[0].title);
  assert.deepEqual(compose("after", { state: "working" }, P({ on: { hotkey: false } })).filter((c) => classes(c).includes("tab-key")), [], "switched off");
  store.delete(W.TABKEYS_KEY); store.delete("romp:keys");
});

test("the after slot composes in registration order: the bar, then the keycap; the stored order can put the keycap first", () => {
  const sid = "11111111-2222-3333-4444-555555555555";
  store.set(W.TABKEYS_KEY, JSON.stringify({ [sid]: "web" })); store.set("romp:keys", JSON.stringify({ [W.HOTKEY_PREFIX + sid]: "Ctrl+1" }));
  assert.deepEqual(compose("after", { state: "working", ctx: "70%" }, P()).map((c) => classes(c)[0]), ["tab-ctx", "tab-key"]);
  assert.deepEqual(compose("after", { state: "working", ctx: "70%" }, P({ order: ["hotkey", "ctx"] })).map((c) => classes(c)[0]), ["tab-key", "tab-ctx"]);
  assert.deepEqual(compose("after", { state: "working", ctx: "70%" }, P({ order: ["nosuch", "hotkey"] })).map((c) => classes(c)[0]), ["tab-key", "tab-ctx"], "an unknown id in the order is skipped");
  store.delete(W.TABKEYS_KEY); store.delete("romp:keys");
});

test("a contributed widget draws in its slot after the built-ins once switched on (off by default, nothing), and a widget that throws costs nothing", () => {
  W.registerTabWidget({ id: "mark", label: "Demo mark", description: "a synthetic mark", defaultOn: false, slot: "after", render: () => { const e = mkEl("span"); e.className = "tab-mark"; return e as unknown as HTMLElement; } });
  const after = compose("after", { state: "working", ctx: "70%" }, P({ on: { mark: true } })).map((c) => classes(c)[0]);
  assert.equal(after[0], "tab-ctx", "the built-ins first (registration order)");
  assert.equal(after[after.length - 1], "tab-mark", "the contributed widget last, in its slot, with no wrapper of its own");
  assert.ok(!compose("after", { state: "working", ctx: "70%" }, P()).map((c) => classes(c)[0]).includes("tab-mark"), "off by default: not drawn");
  W.registerTabWidget({ id: "boom", label: "Boom", description: "throws", defaultOn: true, slot: "before", render: () => { throw new Error("no"); } });
  assert.deepEqual(compose("before", { state: "working" }, P()).map(classes), [["tab-dot"]], "the throwing widget is skipped, the dot still drawn");
});

test("prefs normalize: junk dropped, every field present; with no stored object the context bar derives from the older tabCtx mode", () => {
  assert.deepEqual(W.tabWidgetPrefs(undefined), { on: {}, order: [], opts: {} });
  assert.deepEqual(W.tabWidgetPrefs(undefined, "never"), { on: { ctx: false }, order: [], opts: {} });
  assert.deepEqual(W.tabWidgetPrefs(undefined, "always"), { on: {}, order: [], opts: { ctx: { show: "always" } } });
  assert.deepEqual(W.tabWidgetPrefs(undefined, "over50"), { on: {}, order: [], opts: {} });
  assert.deepEqual(W.tabWidgetPrefs({ on: { dot: false, ctx: "yes" }, order: ["ctx", 3], opts: { dot: { idle: "grey", n: 1 }, ctx: "x" } }),
                   { on: { dot: false }, order: ["ctx"], opts: { dot: { idle: "grey" } } });
  assert.deepEqual(W.tabWidgetPrefs("junk", "never"), { on: { ctx: false }, order: [], opts: {} }, "a non-object store reads as absent");
});

test("the mirror: the prefs read back as the older tabCtx mode", () => {
  assert.equal(W.tabCtxOfPrefs(P()), "over50");
  assert.equal(W.tabCtxOfPrefs(P({ on: { ctx: false } })), "never");
  assert.equal(W.tabCtxOfPrefs(P({ opts: { ctx: { show: "always" } } })), "always");
  assert.equal(W.tabCtxOfPrefs(P({ on: { ctx: false }, opts: { ctx: { show: "always" } } })), "never", "off wins");
});

test("settings.ts: a store from before the widgets derives them from tabCtx; a store with them writes tabCtx back; a legacy tabCtx patch moves the widget", () => {
  store.set("romp:settings", JSON.stringify({ tabCtx: "never" }));
  let s = S.loadSettings();
  assert.deepEqual(s.tabWidgets, { on: { ctx: false }, order: [], opts: {} });
  assert.equal(s.tabCtx, "never");
  store.set("romp:settings", JSON.stringify({ tabCtx: "never", tabWidgets: { on: { ctx: true }, order: [], opts: { ctx: { show: "always" } } } }));
  s = S.loadSettings();
  assert.equal(s.tabCtx, "always", "the widgets win; tabCtx is their mirror");
  s = S.saveSettings({ tabWidgets: { on: { ctx: false }, order: [], opts: {} } });
  assert.equal(s.tabCtx, "never");
  assert.equal(JSON.parse(store.get("romp:settings")!).tabCtx, "never", "the mirror is written");
  s = S.saveSettings({ tabCtx: "always" });   // an older writer: the widget follows
  assert.deepEqual([s.tabWidgets.on.ctx, s.tabWidgets.opts.ctx.show, s.tabCtx], [true, "always", "always"]);
  assert.deepEqual(S.DEFAULT_SETTINGS.tabWidgets, { on: {}, order: [], opts: {} });
  store.delete("romp:settings");
  assert.deepEqual(S.loadSettings(), S.DEFAULT_SETTINGS);
});

test("the settings row's live rendering is the strip's own render over the demo status", () => {
  const dot = W.renderWidgetDemo(W.tabWidget("dot")!, P()) as unknown as El;
  assert.deepEqual(classes(dot), ["tab-dot"], "the demo is a working session: the gold dot");
  const bar = W.renderWidgetDemo(W.tabWidget("ctx")!, P()) as unknown as El;
  assert.deepEqual(classes(bar), ["tab-ctx"]);
  assert.equal(bar.children[0].style.height, "62%");
  const key = W.renderWidgetDemo(W.tabWidget("hotkey")!, P()) as unknown as El;
  assert.deepEqual(classes(key), ["tab-key"]);
  assert.equal(key.textContent, "⌃⇧1", "the demo keycap, with no store behind it");
});

test("source: the strip and the gear draw from this ONE module; the dot rule has its one site here", () => {
  const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tab-widgets.ts"), "utf8");
  assert.equal((SRC.match(/tabDotClass\(status\.state\)/g) || []).length, 1, "the dot slot's one site (tab-dot-slot.test.ts's rule)");
  assert.match(SRC, /^export function tabCtxGauge\(ctxStr: string, ctxColor\?: number\[\]\): HTMLElement \{/m, "the gauge builder lives here now (the ctx widget calls it)");
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /^import \{ composeTabWidgets, tabHotkey \} from "\.\/tab-widgets";/m);
  assert.doesNotMatch(RENDER, /^function tabCtxGauge\(/m, "one builder, not two");
  assert.equal((RENDER.match(/const dotCls = tabDotClass\(st\);/g) || []).length, 0, "render.ts no longer appends the dot itself");
  const GEAR = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.js"), "utf8");
  assert.match(GEAR, /var TW = require\('\.\/tab-widgets\.ts'\);/, "the gear renders the rows' live demos from the same module");
});

test("ONE .tab-key rule in the strip's sheet: this side owns it, so a merge with the tab hot keys pull request's copy cannot leave two identical blocks silently", () => {
  assert.equal((STRIP_CSS.match(/^\.tab-key \{/gm) || []).length, 1);
  assert.match(STRIP_CSS, /^\.tab-key \{ flex: 0 0 auto; font: 600 calc\(0\.82em \/ 0\.92\) ui-monospace, SFMono-Regular, Menlo, monospace; color: var\(--dim\); border: 1px solid var\(--box-border\);/m, "the agreed rule text");
});
