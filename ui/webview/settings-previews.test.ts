// THE SETTINGS CARD'S PREVIEWS ARE THE SURFACES' OWN (T415 part two, the user 2026-09-14, through romp_feature-review): (3) the
// demo tab in the Tab widgets preview and in every row wears the demo record's NAME, the placeholder session_name the user asked for, in
// its identity colour, the way a real tab does, from one source (status-widgets.ts DEMO_RECORD); (4) the word Preview never sits inside the previewed thing: a title above the
// box, for the widget previews and the status line's alike; (5) the status line preview draws its controls and its battery through
// the line's own renderer (status-controls.ts, extracted from render.ts, the chat keeping only its live hooks) over a demo status
// whose tints follow the kernel's rank rule on the selected colormap, and the settings sheet dresses them declaration for
// declaration as the chat's sheet dresses the line. Source pins here (the module's behaviour executes in status-controls.test.ts;
// tests/test_settings_previews_browser.py drives the served card beside the real line). Synthetic names only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const GEAR = read("gear.js"), GEAR_CSS = read("gear.css"), RENDER = read("render.ts"), CSS = read("styles.css");
const MODULE_PATH = path.join(UI, "status-controls.ts");
const MODULE = fs.existsSync(MODULE_PATH) ? fs.readFileSync(MODULE_PATH, "utf8") : "";
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SECTION = GEAR.slice(GEAR.indexOf("function widgetSection("), GEAR.indexOf("var tabSection = widgetSection({"));
const TAB_SECTION = GEAR.slice(GEAR.indexOf("var tabSection = widgetSection({"), GEAR.indexOf("function statusPrefs(s)"));
const STATUS_SECTION = GEAR.slice(GEAR.indexOf("var statusSection = widgetSection({"), GEAR.indexOf("function paintWidgets()"));

/** a rule's declarations with comments stripped and whitespace collapsed, for a byte-level compare across two sheets */
const norm = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ").trim().replace(/;?$/, ";");
const rule = (css: string, sel: string, name: string): string => {
  const i = css.indexOf("\n" + sel + " {");
  assert.ok(i >= 0, name + ": the rule " + sel + " is where the pin expects it");
  return norm(css.slice(i + sel.length + 3, css.indexOf("}", i)));
};

test("(4) the Preview caption is a title ABOVE the box, in both sections, never inside the previewed thing", () => {
  assert.match(SECTION, /var title = document\.createElement\('div'\); title\.className = 'rs-preview-title'; title\.textContent = 'Preview';/, "the title, built with the box");
  assert.match(SECTION, /cfg\.host\.parentNode\.insertBefore\(title, cfg\.host\.nextSibling\);\s*\n\s*cfg\.host\.parentNode\.insertBefore\(box, title\.nextSibling\);/, "the title right under the rows, the box under the title");
  assert.doesNotMatch(SECTION, /rs-preview-label|box\.appendChild\(cap\)/, "no caption inside the box");
  assert.doesNotMatch(GEAR, /rs-preview-label/, "the old class is gone from the card");
  assert.match(GEAR_CSS, /#rsettings \.rs-preview-title \{[^}]*color: var\(--accent, #9cd2ff\); font-size: 11px; font-weight: 600;/, "the title wears the section-label dress: sentence case, the accent, never uppercase (the user 2026-09-18)");
  assert.doesNotMatch(GEAR_CSS, /\.rs-preview-title \{[^}]*(uppercase|letter-spacing)/);
  assert.doesNotMatch(GEAR_CSS, /rs-preview-label/, "…and the old rule is gone from the sheet");
  assert.match(GEAR_CSS, /#rsettings \.rs-preview \{ margin-top: 4px;/, "the box closes up under its title");
});

test("(3) the demo tab reads the demo record's name in its identity colour, the way a real tab does, from one source, in the preview and in every row", () => {
  assert.match(GEAR, /function demoTab\(\) \{ var tab = document\.createElement\('span'\); tab\.className = 'tab colored'; tab\.style\.setProperty\('--chip-bg', SW\.DEMO_RECORD\.color\.bg\); return tab; \}/, "the tab: coloured, the record's colour as its --chip-bg (the strip's own variable)");
  assert.match(GEAR, /function demoLabel\(\) \{ var label = document\.createElement\('span'\); label\.className = 'tab-label'; label\.textContent = SW\.DEMO_RECORD\.name; return label; \}/, "the label: the record's name");
  assert.doesNotMatch(GEAR, /textContent = 'web'|textContent = 'session_name'/, "no second copy of the name: the record is the one source");
  assert.equal((TAB_SECTION.match(/demoTab\(\)/g) || []).length, 3, "the preview, the widget rows' demos and the ring rows' demos build the same tab");
  assert.equal((TAB_SECTION.match(/demoLabel\(\)/g) || []).length, 3, "…and the same label");
  const real = rule(CSS, ".tab.colored .tab-label", "styles.css");
  assert.equal(rule(GEAR_CSS, "#rsettings .rs-widget-demo .tab.colored .tab-label, #rsettings .rs-preview .tab.colored .tab-label", "gear.css"), real, "the card's sheet mirrors the strip's label rule");
  assert.match(fs.readFileSync(path.join(UI, "status-widgets.ts"), "utf8"), /export const DEMO_RECORD: StatusRecord = \{\s*\n\s*id: "demo", name: "session_name", color: \{ bg: "#9cd2ff" \}/, "the one demo record: the placeholder session_name (the user's copy), in the accent; the demo sid kept");
});

test("(5) the status line preview draws its controls and battery through the line's own renderer over a demo status", () => {
  assert.match(GEAR, /var SC = require\('\.\/status-controls\.ts'\);/, "the shared module rides the card");
  assert.match(STATUS_SECTION, /var st = SC\.demoStatus\(cmStops\(load\(\)\.colormap\)\);/, "the demo status on the SELECTED colormap");
  assert.match(STATUS_SECTION, /var meta = document\.createElement\('span'\); meta\.className = 'spinner-meta'; SC\.syncMetaControls\(meta, st, null, \{\}\); right\.appendChild\(meta\);/, "the badges, no hooks: inert");
  assert.match(STATUS_SECTION, /var bar = SC\.ctxBar\(\); SC\.setCtxBar\(bar, st\.ctx, false, SC\.pickTone\(st\.ctxColor, st\.ctxTone\), false\); right\.appendChild\(bar\);/, "the battery, filled and coloured by its percentage, the tone on the yatharth themes as the line picks it (round three)");
  assert.match(GEAR, /var TW = require\('\.\/tab-widgets\.ts'\);   \/\/ the tab-title widgets \(T379\)/, "the tab-widgets require keeps its comment (round three, low c)");
  assert.match(GEAR, /var SC = require\('\.\/status-controls\.ts'\);   \/\/ the status line's controls \(T415 part two\)[^\n]*\n/, "…and the status-controls require its own, on its own line");
  assert.doesNotMatch(GEAR, /rs-sl-ctl|rs-sl-batt|Auto · Opus 5 · high|textContent = '62%'/, "no words and no boxed number standing in for the controls");
  assert.doesNotMatch(GEAR_CSS, /rs-sl-ctl|rs-sl-batt/, "…and no rules for them");
  assert.match(GEAR, /function cmStops\(name\) \{ return CMAPS\[\(name \|\| ''\)\.toLowerCase\(\)\] \|\| CMAPS\.aurora; \}/, "the card's stops by name, aurora the default as in the chat");
});

test("(5) the renderer is one module: render.ts imports it and keeps only the chat's live hooks", () => {
  assert.ok(MODULE, "ui/webview/status-controls.ts exists");
  for (const sig of ["export type MetaKind = \"mode\" | \"model\" | \"effort\" | \"fast\";", "export function metaButton(kind: MetaKind, text: string, forSid: string | null | undefined, hooks: MetaHooks): HTMLElement {",
    "export function syncMetaControls(meta: HTMLElement, st: MetaStatus, forSid: string | null | undefined, hooks: MetaHooks): void {", "export function metaColor(kind: MetaKind, st: MetaStatus): string {",
    "export function ctxBar(onClick?: (bar: HTMLElement) => void): HTMLElement {", "export function setCtxBar(bar: HTMLElement, ctxStr: string | undefined, compacting = false, ctxColor?: number[] | null, ctxOver = false, sweep?: (scan: HTMLElement, fresh: boolean) => void): void {",
    "export function rampOn(v: number, stops: ReadonlyArray<readonly [number, number, number]>): [number, number, number] {", "export function demoStatus(stops: ReadonlyArray<readonly [number, number, number]>): DemoStatus {",
    "export function roundHalfEven(x: number): number {", "export function toneRgb(family: ToneFamily, v: number): [number, number, number] {", "export function contextRgb(pct: number): [number, number, number] {", "export { pickTone };"])
    assert.ok(MODULE.includes(sig), "the module exports: " + sig);
  assert.match(RENDER, /^import \{[^}]*\bsyncMetaControls as syncMetaControlsWith\b[^}]*\} from "\.\/status-controls";/m, "the chat imports the renderer");
  for (const gone of [/^const MODE_ICONS: Record<string, string> = \{/m, /^function modeIconSvg\(/m, /^function riskyMode\(/m, /^function metaColor\(/m, /^function prettyMode\(/m, /^function prettyFast\(/m, /^function fastAvailable\(/m, /^function metaCurrent\(/m, /^function metaDots\(/m, /^type MetaKind = /m])
    assert.doesNotMatch(RENDER, gone, "moved out of render.ts: " + gone.source);
  assert.match(RENDER, /^const META_HOOKS: MetaHooks = \{ onPress: \(kind, btn, forSid\) => toggleMetaMenu\(kind, btn, forSid\), pending: \(kind, st\) => isMetaPending\(kind, st as Status\) \};/m, "the chat's hooks: the picker and the pending heuristic");
  assert.match(RENDER, /^function metaButton\(kind: MetaKind, text: string, forSid\?: string \| null\): HTMLElement \{ return buildMetaButton\(kind, text, forSid, META_HOOKS\); \}/m);
  assert.match(RENDER, /^function syncMetaControls\(meta: HTMLElement, st: Status, forSid\?: string \| null\): void \{ syncMetaControlsWith\(meta, st, forSid, META_HOOKS\); \}/m);
  assert.match(RENDER, /^function ctxBar\(\): HTMLElement \{ const bar = buildCtxBar\(compactActiveSession\); bar\.id = "ctx-bar"; return bar; \}/m, "the chat's battery keeps its id (the in-place refresh finds it) and its /compact click");
  assert.match(RENDER, /^function setCtxBar\(bar: HTMLElement, ctxStr: string \| undefined, compacting = false, ctxColor\?: number\[\], ctxOver = false, st\?: Status\): void \{\s*\n\s*if \(st\) markCtxBarFor\(bar, st\);\s*\n\s*setCtxBarWith\(bar, ctxStr, compacting, ctxColor, ctxOver, \(scan, fresh\) => applyCompactSweep\(scan, 3200, fresh\)\);\s*\n\}/m, "the compaction sweep stays the chat's (applyCompactSweep) and rides the hook; the status the chat hands in marks a Codex bar inert before the fill (2026-09-19)");
  assert.match(RENDER, /^function ramp\(v: number\): \[number, number, number\] \{ return rampOn\(v, selectedStops\(\)\); \}/m, "one ramp arithmetic");
});

test("(5) the demo's tints follow the kernel's rank rule: the module's family and effort lists are kernel.py's choice lists, in order", () => {
  assert.ok(MODULE, "the module exists");
  const kernelModels = Array.from(KERNEL.slice(KERNEL.indexOf("\nMODEL_CHOICES = ["), KERNEL.indexOf("]\n", KERNEL.indexOf("\nMODEL_CHOICES = ["))).matchAll(/\{"value": "([a-z]+)"/g)).map((m) => m[1]);
  const kernelEfforts = /EFFORT_CHOICES = \[\{"value": v, "label": v\} for v in \(([^)]*)\)\]/.exec(KERNEL)![1].split(",").map((s) => s.trim().replace(/"/g, "")).filter(Boolean);
  const modFamilies = /export const MODEL_FAMILIES = \[([^\]]*)\];/.exec(MODULE), modEfforts = /export const EFFORT_LEVELS = \[([^\]]*)\];/.exec(MODULE);
  assert.ok(modFamilies && modEfforts, "the module states both lists");
  const list = (s: string) => s.split(",").map((x) => x.trim().replace(/"/g, "")).filter(Boolean);
  assert.deepEqual(list(modFamilies![1]), kernelModels, "the model families, most capable first (fable 1.0 … haiku 0.0)");
  assert.deepEqual(list(modEfforts![1]), kernelEfforts, "the effort levels, low 0.0 … the last 1.0");
  assert.match(KERNEL, /_MODEL_RANK = _ramp_ranks\(MODEL_CHOICES, ascending=False\)/); assert.match(KERNEL, /_EFFORT_RANK = dict\(_ramp_ranks\(EFFORT_CHOICES, ascending=True\)\)/);
  assert.match(MODULE, /export function modelRank\(model: string\): number \| null \{/, "a family's rank: the first family word the model name contains, as the kernel's _model_color reads it");
  assert.match(MODULE, /export function effortRank\(effort: string\): number \| null \{/);
  assert.match(MODULE, /const modelV = modelRank\("Opus 5"\), effortV = effortRank\("high"\), ctxPct = 62;\s*\n\s*return \{ mode: "auto", model: "Opus 5", effort: "high", ctx: ctxPct \+ "%",/, "the demo: what the card's words said, now drawn, its tones beside its colours (round three)");
  assert.match(MODULE, /modelTone: modelV === null \? null : toneRgb\("model", modelV\), effortTone: effortV === null \? null : toneRgb\("effort", effortV\), ctxTone: contextRgb\(ctxPct\)/, "the kernel's tone pairs, so the yatharth themes read the tones as the line does");
});

test("(5) the settings sheet dresses the preview's controls as the chat's sheet dresses the line, declaration for declaration", () => {
  for (const sel of [".spinner-meta", ".meta-btn", ".meta-caret", ".meta-ico", ".meta-ico svg", ".ctx-bar", ".ctx-fill", ".ctx-text"])
    assert.equal(rule(GEAR_CSS, "#rsettings .rs-sl " + sel, "gear.css"), rule(CSS, sel, "styles.css"), sel + ": the same declarations on the card");
  assert.match(GEAR_CSS, /\n#rsettings \.rs-sl \.ctx-scan \{ display: none; \}/, "the compaction scan never shows in a preview");
  assert.match(GEAR_CSS, /\n#rsettings \.rs-sl \.meta-btn, #rsettings \.rs-sl \.ctx-bar \{ cursor: default; \}\n#rsettings \.rs-sl \.spinner-meta \{ flex-wrap: nowrap; margin-left: 0; \}\n#rsettings \.rs-sl-right \{ flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; max-width: 100%; row-gap: 6px; \}\n#rsettings \.rs-sl \{ flex-wrap: wrap; row-gap: 6px; \}/,
    "the departures, after the copies: nothing here opens or compacts; the badges never wrap in the card's width; no lead-in margin; the cluster keeps its width, drops below as one row when the card is too narrow, and under 440 px wraps its battery under the badges rather than leave the card (round three, low e)");
  assert.doesNotMatch(GEAR_CSS, /#rsettings \.rs-sl \.meta-btn:hover/, "no hover lift on an inert badge");
});
