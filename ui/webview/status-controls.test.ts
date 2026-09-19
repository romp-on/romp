// THE SHARED STATUS CONTROLS, EXECUTED (T415 part two): the module render.ts and the settings card both draw the status line's
// controls through. Against a stub document: with no hooks the badges are inert (no click, no tip) and read the demo's words and
// tints; with the chat's hooks a click reaches the picker; the battery fills to its percentage in the colour it is given, hides
// without one, and runs the compaction scan through the chat's sweep hook only. The colormap arithmetic equals the kernel's, and
// the demo's tints are the kernel's rank rule on the given stops. Synthetic values only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as path from "node:path";

type N = any;
function walk(root: N, f: (x: N) => void) { f(root); for (const k of root.kids || []) if (k.tag !== "#text") walk(k, f); }
function mk(tag: string): N {
  const n: N = {
    tag, attrs: {} as Record<string, string>, kids: [] as N[], style: {} as Record<string, string>, listeners: {} as Record<string, Function[]>,
    dataset: {} as Record<string, string>, title: "", id: "", _cls: new Set<string>(), _html: "", parentNode: null as N,
    get className(): string { return Array.from(n._cls).join(" "); }, set className(v: string) { n._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
    classList: { add: (...a: string[]) => a.forEach((c) => n._cls.add(c)), remove: (...a: string[]) => a.forEach((c) => n._cls.delete(c)),
      toggle: (c: string, f?: boolean) => { if (f === undefined ? !n._cls.has(c) : f) n._cls.add(c); else n._cls.delete(c); return n._cls.has(c); }, contains: (c: string) => n._cls.has(c) },
    get textContent(): string { return n.kids.map((k: N) => k.tag === "#text" ? k.text : k.textContent).join(""); },
    set textContent(v: string) { n.kids = v ? [{ tag: "#text", text: v }] : []; },
    get innerHTML(): string { return n._html; }, set innerHTML(v: string) { n._html = v; n.kids = []; },
    get children(): N[] { return n.kids.filter((k: N) => k.tag !== "#text"); },
    get firstElementChild(): N { return n.children[0] || null; },
    setAttribute(k: string, v: string) { n.attrs[k] = String(v); }, getAttribute(k: string) { return k in n.attrs ? n.attrs[k] : null; },
    removeAttribute(k: string) { delete n.attrs[k]; },
    appendChild(c: N) { c.parentNode = n; n.kids.push(c); return c; },
    replaceChildren(...cs: N[]) { n.kids = []; for (const c of cs) n.appendChild(c); },
    addEventListener(t: string, fn: Function) { (n.listeners[t] = n.listeners[t] || []).push(fn); },
    dispatch(t: string) { const e = { stopped: false, stopPropagation() { this.stopped = true; }, preventDefault() { /* noop */ } }; for (const fn of n.listeners[t] || []) fn.call(n, e); return e; },
    /** class selectors only (".a", ".a .b" as a descendant walk), what the renderer asks for */
    querySelectorAll(sel: string): N[] { const parts = sel.trim().split(/\s+/); let set: N[] = [n]; for (const part of parts) { const cls = part.slice(1); const next: N[] = []; for (const root of set) walk(root, (x) => { if (x !== root && x._cls && x._cls.has(cls)) next.push(x); }); set = next; } return set; },
    querySelector(sel: string): N { return n.querySelectorAll(sel)[0] || null; },
  };
  return n;
}
const g = globalThis as any;
g.document = { createElement: mk, createTextNode: (t: string) => ({ tag: "#text", text: t }), body: mk("body"), addEventListener() { /* tips */ }, querySelector: () => null };
g.window = { addEventListener() { /* tips */ }, innerWidth: 1200, innerHeight: 800 };
// eslint-disable-next-line @typescript-eslint/no-var-requires
const SC = require("./status-controls");
const AURORA: Array<[number, number, number]> = [[84, 178, 4], [0, 180, 115], [35, 175, 156], [66, 169, 176], [25, 168, 201], [14, 164, 227], [74, 155, 241], [113, 145, 244], [144, 136, 240]];
const rgb = (c: number[]) => `rgb(${c[0]},${c[1]},${c[2]})`;

const VIRIDIS: Array<[number, number, number]> = [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]];
test("rampOn is the kernel's ramp byte for byte: the ends, an interior sample, the clamp, and Python's half-to-even rounding at a tie (viridis at the demo's own 0.62: 172, not 173)", () => {
  assert.deepEqual(SC.rampOn(0, AURORA), [84, 178, 4]); assert.deepEqual(SC.rampOn(1, AURORA), [144, 136, 240]); assert.deepEqual(SC.rampOn(7, AURORA), [144, 136, 240], "clamped");
  // 0.62 on nine stops: x = 4.96, between stop 4 and stop 5 at 0.96
  assert.deepEqual(SC.rampOn(0.62, AURORA), [Math.round(25 + (14 - 25) * 0.96), Math.round(168 + (164 - 168) * 0.96), Math.round(201 + (227 - 201) * 0.96)]);
  // viridis at 0.62: x = 5.58, between stop 5 (31,158,137) and stop 6 (53,183,121) at 0.58: g = 158 + 25 * 0.58 = 172.5 exactly, which Python rounds to the even 172
  assert.deepEqual(SC.rampOn(0.62, VIRIDIS), [44, 172, 128], "the kernel's rgb(44,172,128) (round three, low a: Math.round gave 173)");
  assert.equal(SC.roundHalfEven(2.5), 2); assert.equal(SC.roundHalfEven(3.5), 4); assert.equal(SC.roundHalfEven(2.4999), 2); assert.equal(SC.roundHalfEven(2.5001), 3); assert.equal(SC.roundHalfEven(0.49999999999999994), 0, "no floor(x + 0.5) quirk");
});

// THE CROSS-LANGUAGE SWEEP (round four): the module claims the kernel's functions byte for byte, so the kernel's own module answers for a grid
// of inputs and every sample must agree: the HSL conversion over 72 hues by 11 saturations by 11 lightnesses plus the two triples the read
// cited (hslToRgb(5, 0.9, 0.4) gave 194,25,10 against the kernel's 194,26,10: the old normalisation roundtripped t through +1 and lost low
// bits), the three tone families at 101 ranks, the context ramp at every percent, and every colormap's ramp at 1001 positions.
test("the kernel's module answers for a grid: hslToRgb, toneRgb, contextRgb and rampOn agree with bin/romp_colormap.py sample for sample", () => {
  const script = [
    "import json, sys; sys.path.insert(0, 'bin'); import romp_colormap as cm",
    "hsl = [[h, s / 10, l / 10] for h in range(0, 360, 5) for s in range(0, 11) for l in range(0, 11)] + [[5, 0.9, 0.4], [359.5, 0.55, 0.45], [720, 0.3, 0.7], [-30, 0.8, 0.5], [180, 1.0, 0.0], [90, 0.0, 1.0]]",
    "out = {'hsl': [[h, s, l] + list(cm._hsl_to_rgb(h, s, l)) for h, s, l in hsl],",
    "       'tones': [[fam, v / 100] + list(cm.tone_rgb(fam, v / 100)) for fam in ('model', 'effort', 'context') for v in range(0, 101)],",
    "       'ctx': [[p] + list(cm.context_rgb(p)) for p in range(0, 101)],",
    "       'ramps': {name: [[v / 1000] + list(cm.ramp(v / 1000, stops)) for v in range(0, 1001)] for name, stops in cm.COLORMAPS.items()},",
    "       'stops': {name: [list(s) for s in stops] for name, stops in cm.COLORMAPS.items()}}",
    "print(json.dumps(out))",
  ].join("\n");
  const py = JSON.parse(execFileSync("python3", ["-c", script], { cwd: path.resolve(process.cwd(), ".."), encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }));
  const badHsl: string[] = [], badRest: string[] = [];   // two lists: an assertion on the first would narrow one shared list to the empty tuple for the compiler
  for (const [h, s, l, r, g, b] of py.hsl) { const got = SC.hslToRgb(h, s, l); if (got[0] !== r || got[1] !== g || got[2] !== b) badHsl.push(`hsl(${h}, ${s}, ${l}): ${got} vs ${[r, g, b]}`); }
  for (const [fam, v, r, g, b] of py.tones) { const got = SC.toneRgb(fam, v); if (got[0] !== r || got[1] !== g || got[2] !== b) badRest.push(`tone(${fam}, ${v}): ${got} vs ${[r, g, b]}`); }
  for (const [p, r, g, b] of py.ctx) { const got = SC.contextRgb(p); if (got[0] !== r || got[1] !== g || got[2] !== b) badRest.push(`context(${p}): ${got} vs ${[r, g, b]}`); }
  for (const name of Object.keys(py.ramps)) for (const [v, r, g, b] of py.ramps[name]) { const got = SC.rampOn(v, py.stops[name]); if (got[0] !== r || got[1] !== g || got[2] !== b) badRest.push(`ramp(${name}, ${v}): ${got} vs ${[r, g, b]}`); }
  assert.deepEqual(badHsl, [], "hslToRgb differs from the kernel's _hsl_to_rgb on " + badHsl.length + " of " + py.hsl.length + " triples");
  assert.deepEqual(badRest, [], "the tones, the context ramp or a colormap's ramp differ from the kernel's");
  assert.ok(py.hsl.length > 8700 && Object.keys(py.ramps).length >= 7, "the grid is the size it claims: " + py.hsl.length + " triples, " + Object.keys(py.ramps).length + " maps");
});

test("the tones are the kernel's tone ramps (bin/romp_colormap.py tone_rgb, context_rgb): the family hues, the saturation and lightness by rank, the amber and red status overrides", () => {
  // tone_rgb(family, v) = hsl(hue, 0.42 + 0.48 v, l0 + l1 v) with model (28, 0.52, 0.16), effort (258, 0.66, 0.12), context (200, 0.52, 0.16); the kernel's comments name the ends
  assert.deepEqual(SC.toneRgb("model", 0), SC.hslToRgb(28, 0.42, 0.52)); assert.deepEqual(SC.toneRgb("model", 1), SC.hslToRgb(28, 0.90, 0.68));
  assert.deepEqual(SC.toneRgb("effort", 0), SC.hslToRgb(258, 0.42, 0.66)); assert.deepEqual(SC.toneRgb("context", 0.5), SC.hslToRgb(200, 0.66, 0.60));
  assert.deepEqual(SC.hslToRgb(0, 0, 0.5), [128, 128, 128], "grey: no saturation (Python's round of 127.5 is 128, even)");
  assert.deepEqual(SC.contextRgb(62), SC.toneRgb("context", 0.62), "below the warn line: the teal tone by fullness");
  assert.deepEqual(SC.contextRgb(70), [215, 162, 58], "from the warn line: the shared amber"); assert.deepEqual(SC.contextRgb(88), [192, 57, 43], "from the danger line: the shared red");
  const d = SC.demoStatus(AURORA);
  assert.deepEqual(d.modelTone, SC.toneRgb("model", 2 / 3), "the demo ships the model tone at Opus's rank"); assert.deepEqual(d.effortTone, SC.toneRgb("effort", 0.4), "…the effort tone at high's rank");
  assert.deepEqual(d.ctxTone, SC.contextRgb(62), "…and the context tone at 62 percent, so pickTone follows the page's theme as the line's does");
});

test("the rank rule: families descending, efforts ascending, the first family word the model name contains; unknown = null", () => {
  assert.equal(SC.modelRank("Opus 5"), 2 / 3); assert.equal(SC.modelRank("claude-fable-5-1"), 1); assert.equal(SC.modelRank("haiku"), 0); assert.equal(SC.modelRank("gpt-5"), null);
  assert.equal(SC.effortRank("low"), 0); assert.equal(SC.effortRank("high"), 0.4); assert.equal(SC.effortRank(" ULTRACODE "), 1); assert.equal(SC.effortRank("turbo"), null);
  const d = SC.demoStatus(AURORA);
  assert.deepEqual([d.mode, d.model, d.effort, d.ctx], ["auto", "Opus 5", "high", "62%"]);
  assert.deepEqual(d.modelColor, SC.rampOn(2 / 3, AURORA)); assert.deepEqual(d.effortColor, SC.rampOn(0.4, AURORA)); assert.deepEqual(d.ctxColor, SC.rampOn(0.62, AURORA));
});

test("with no hooks the badges are the line's DOM, inert: mode, model, effort in order, the words and the tints, no click, no tip", () => {
  const meta = mk("span"); meta.className = "spinner-meta";
  const st = SC.demoStatus(AURORA);
  SC.syncMetaControls(meta, st, null, {});
  const btns = meta.querySelectorAll(".meta-btn");
  assert.deepEqual(btns.map((b: N) => b.dataset.kind), ["mode", "model", "effort"], "no fast badge: the demo reports none");
  assert.deepEqual(btns.map((b: N) => b.querySelector(".meta-label").textContent), ["Auto", "Opus 5", "high"]);
  assert.deepEqual(btns.map((b: N) => b.querySelector(".meta-label").style.color), ["", rgb(st.modelColor), rgb(st.effortColor)], "mode untinted; model and effort by rank");
  assert.ok(btns[0].querySelector(".mode-ico") && btns[0].querySelector(".mode-ico").innerHTML.includes("<svg"), "the permission glyph beside its word");
  for (const b of btns) { assert.equal((b.listeners.click || []).length, 0, b.dataset.kind + ": no click"); assert.equal(b._tipText, undefined, b.dataset.kind + ": no tip"); assert.equal(b.querySelector(".meta-caret").textContent, "▾"); }
  SC.syncMetaControls(meta, st, null, {});
  assert.equal(meta.querySelectorAll(".meta-btn").length, 3, "a second sync refreshes in place");
});

test("with the chat's hooks a click reaches the picker with the kind, the badge and the session; the pending hook shows the dots", () => {
  const meta = mk("span"); const presses: any[] = [];
  const hooks = { onPress: (kind: string, btn: N, forSid: string | null) => presses.push([kind, btn.dataset.kind, forSid]), pending: (kind: string) => kind === "model" };
  SC.syncMetaControls(meta, { mode: "plan", model: "Opus 5", effort: "max" }, "s-1", hooks);
  const btns = meta.querySelectorAll(".meta-btn");
  const e = btns[2].dispatch("click");
  assert.deepEqual(presses, [["effort", "effort", "s-1"]]); assert.ok(e.stopped, "the click is the badge's");
  assert.ok(btns[1].querySelector(".meta-dots"), "model pending: the switching dots in the badge"); assert.equal(btns[1]._cls.has("meta-pending"), true);
  assert.equal(btns[2].querySelector(".meta-label").textContent, "max"); assert.equal(btns[0].querySelector(".meta-label").textContent, "Plan");
  assert.equal(typeof btns[0]._tipText, "string", "with a picker the badge wears its tip");
});

test("a Codex session with no effort picked yet gets the effort badge, reading the bare kind until a level lands; an SDK session without one gets none", () => {
  // A Codex session is born with no effort of its own (the registry holds "" and the engine applies its default), and the
  // badge appeared only once a level was set, so the menu that would set one had no button to open it (2026-09-16).
  const meta = mk("span"); const presses: any[] = [];
  const hooks = { onPress: (kind: string, btn: N, forSid: string | null) => presses.push([kind, forSid]) };
  const codex: any = { mode: "auto", model: "GPT-5 Test", effort: "", backend: "codex" };
  SC.syncMetaControls(meta, codex, "s-2", hooks);
  let btns = meta.querySelectorAll(".meta-btn");
  assert.deepEqual(btns.map((b: N) => b.dataset.kind), ["mode", "model", "effort"], "the effort badge stands before any pick");
  assert.equal(btns[2].querySelector(".meta-label").textContent, "effort", "it reads the bare kind, not a guessed level");
  assert.equal(btns[2].querySelector(".meta-label").style.color, "", "no level, no rank tint");
  btns[2].dispatch("click");
  assert.deepEqual(presses, [["effort", "s-2"]], "a click reaches the picker as any badge's does");
  codex.effort = "high"; codex.effortColor = [1, 2, 3];
  SC.syncMetaControls(meta, codex, "s-2", hooks);
  btns = meta.querySelectorAll(".meta-btn");
  assert.equal(btns.length, 3, "the pick refreshes the badge in place");
  assert.equal(btns[2].querySelector(".meta-label").textContent, "high", "the level lands on the same badge");
  assert.equal(btns[2].querySelector(".meta-label").style.color, rgb([1, 2, 3]));
  assert.equal(SC.effortBadgeText(codex), "high"); assert.equal(SC.effortBadgeText({ effort: "", backend: "codex" }), "effort");
  const sdk = mk("span");
  SC.syncMetaControls(sdk, { mode: "auto", model: "Opus 5", effort: "", backend: "sdk" }, "s-3", hooks);
  assert.deepEqual(sdk.querySelectorAll(".meta-btn").map((b: N) => b.dataset.kind), ["mode", "model"], "an SDK session's effort arrives with the CLI's init; no badge before it");
  assert.equal(SC.effortBadgeText({ effort: "", backend: "sdk" }), ""); assert.equal(SC.effortBadgeText({ effort: "" }), "");
});

test("the battery: filled to its percentage in the colour given, the number inside; hidden without a percentage; the scan only through the sweep hook; the click-to-compact tooltip only on a bar that compacts", () => {
  const bar = SC.ctxBar();
  assert.equal(bar.id, "", "no id from the module: the chat's wrapper names its one live bar");
  assert.deepEqual(bar.children.map((c: N) => c.className), ["ctx-fill", "ctx-text", "ctx-scan"]);
  assert.equal((bar.listeners.click || []).length, 0, "no click without the chat's hook");
  SC.setCtxBar(bar, "62%", false, [20, 168, 208], false);
  assert.equal(bar.querySelector(".ctx-fill").style.width, "62%"); assert.equal(bar.querySelector(".ctx-fill").style.background, "rgb(20,168,208)"); assert.equal(bar.querySelector(".ctx-text").textContent, "62%");
  assert.equal(bar.style.display, "");
  assert.equal(bar.title, "", "an inert battery says nothing about clicking to compact (round three, low b)");
  const live0 = SC.ctxBar(() => { /* the chat's compact */ }); SC.setCtxBar(live0, "62%", false, [20, 168, 208], false);
  assert.equal(live0.title, "context 62% used — click to /compact", "the chat's bar keeps the line's tooltip"); SC.setCtxBar(live0, "100%", false, null, true); assert.match(live0.title, /exceeds this model's window/);
  SC.setCtxBar(bar, "100%", false, [1, 2, 3], true); assert.equal(bar.querySelector(".ctx-text").textContent, "100%+", "past the window: the plus");
  SC.setCtxBar(bar, undefined, false, null, false); assert.equal(bar.style.display, "none", "no percentage: hidden");
  const swept: any[] = [];
  SC.setCtxBar(bar, "40%", true, null, false, (scan: N, fresh: boolean) => swept.push([scan.className, fresh]));
  assert.deepEqual(swept, [["ctx-scan", true]], "compacting: the chat's sweep, phase-synced on a fresh scan"); assert.equal(bar._cls.has("ctx-compacting"), true);
  SC.setCtxBar(bar, "40%", true, null, false, (scan: N, fresh: boolean) => swept.push([scan.className, fresh]));
  assert.deepEqual(swept[1], ["ctx-scan", false], "a reused scan keeps its phase");
  const clicked: N[] = []; const live = SC.ctxBar((b: N) => clicked.push(b)); live.dispatch("click"); assert.equal(clicked[0], live, "the chat's hook gets the bar");
});

test("a bar that cannot compact says why in its tooltip (a Codex session's battery, 2026-09-19); without the reason an inert bar still says nothing", () => {
  const cx = SC.ctxBar();
  cx.dataset.inertWhy = "this session runs in Codex, which has no /compact";
  SC.setCtxBar(cx, "62%", false, [1, 2, 3], false);
  assert.equal(cx.title, "context 62% used — this session runs in Codex, which has no /compact");
  SC.setCtxBar(cx, "100%", false, null, true);
  assert.match(cx.title, /exceeds this model's window — this session runs in Codex/);
  const plain = SC.ctxBar(); SC.setCtxBar(plain, "62%", false, [1, 2, 3], false);
  assert.equal(plain.title, "", "no reason, no click: the inert bar keeps no tooltip");
});
