// COMPACT TABS AND AGENTS (the user 2026-09-08: on a phone, the tab strip and the background-work panel left
// about three lines of transcript in view): one boolean setting, off by default, applied as ONE body class by
// a pure applier and answered by a scoped block in styles.css. Density only: no render path changes, and every
// dense rule shadows a default that stays byte-identical. Executable where the logic is importable
// (settings.ts, dense-chrome.ts); pinned at the source where it lives in a foreign host (gear.js, render.ts,
// styles.css, docs/guide.md), the repo's convention (theme.test.ts, chat-scheme.test.ts); measured in a browser where
// one exists (dense-chrome-layout.test.ts), which is where the rendered heights the rules add up to are asserted.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { DEFAULT_SETTINGS, type RompSettings } from "./settings";
import { applyDenseChrome, DENSE_CHROME_CLASS } from "./dense-chrome";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const CSS = read("ui", "webview", "styles.css");
const FEED = read("ui", "webview", "feed.css");
const RENDER = read("ui", "webview", "render.ts");
const GEAR = read("ui", "webview", "gear.js");
const GUIDE = read("docs", "guide.md");

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// the FIRST rule for a selector at the start of a line: the default, written above the dense block
const defaultRule = (sel: string) => {
  const m = CSS.match(new RegExp("(?:^|\\n)" + esc(sel) + " \\{([^}]*)\\}"));
  assert.ok(m, sel + " has a default rule");
  return m![1];
};
const denseRule = (sel: string) => {
  const m = CSS.match(new RegExp("\\nbody\\." + DENSE_CHROME_CLASS + " " + esc(sel) + " \\{([^}]*)\\}"));
  assert.ok(m, sel + " has a dense rule under body." + DENSE_CHROME_CLASS);
  return m![1];
};
const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");
// a pin over a whole file asserts a boolean, so a red pin prints its message and the pattern, not the file
const has = (src: string, re: RegExp, what: string) => assert.ok(re.test(src), what + " (" + re.source + ")");
const lacks = (src: string, re: RegExp, what: string) => assert.ok(!re.test(src), what + " (" + re.source + ")");

test("executable: the applier toggles exactly one body class, on for true alone", () => {
  const classes = new Set<string>();
  const doc = { body: { classList: { toggle: (c: string, on: boolean) => { if (on) classes.add(c); else classes.delete(c); } } } } as unknown as Document;
  applyDenseChrome(doc, { ...DEFAULT_SETTINGS, denseChrome: true });
  assert.deepEqual([...classes], [DENSE_CHROME_CLASS], "on: the class is present");
  applyDenseChrome(doc, { ...DEFAULT_SETTINGS, denseChrome: false });
  assert.deepEqual([...classes], [], "off: the class is absent");
  applyDenseChrome(doc, { ...DEFAULT_SETTINGS, denseChrome: true });
  applyDenseChrome(doc, { ...DEFAULT_SETTINGS });
  assert.deepEqual([...classes], [], "the default is off");
  applyDenseChrome(doc, { ...DEFAULT_SETTINGS, denseChrome: "yes" as unknown as boolean });
  assert.deepEqual([...classes], [], "junk in the store never densifies: only true is on");
  const older = { ...DEFAULT_SETTINGS } as Partial<RompSettings>;
  delete older.denseChrome;
  classes.add(DENSE_CHROME_CLASS);
  applyDenseChrome(doc, older as RompSettings);
  assert.deepEqual([...classes], [], "a store from before the setting existed reads as off");
});

test("the chat applies the class beside the scheme and the theme, at startup and on every settings change", () => {
  has(RENDER, /import \{ applyDenseChrome \} from "\.\/dense-chrome";/, "render.ts imports the applier");
  const body = RENDER.slice(RENDER.indexOf("function applyChatScheme("), RENDER.indexOf("function setupSettings("));
  assert.match(body, /applyDenseChrome\(document, s\);/, "one applier call inside applyChatScheme");
  const theme = body.indexOf("applyTheme(document, s);"), dense = body.indexOf("applyDenseChrome(document, s);");
  assert.ok(theme >= 0 && dense > theme, "after the theme applier: the scheme, the theme, then the density");
  // the two moments applyChatScheme runs (chat-scheme.test.ts pins the same lines): the persisted pick at
  // startup, and the same-document / cross-tab / cross-webview settings signal, which is how a gear flip
  // repaints the strip and the panel at once (a body class, the cascade does the rest; no rebuild)
  has(RENDER, /applyChatScheme\(settings\);   \/\/ the persisted pick applies at startup/, "applied at startup");
  has(RENDER, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\);/, "applied on every settings change");
});

test("the gear row mirrors the other booleans: Chat section, save path, open-time fill, defaults mirror", () => {
  const at = GEAR.indexOf("id=rs-dense");
  assert.ok(at > 0, "the checkbox exists in the gear markup");
  assert.ok(GEAR.indexOf(">Chat<") < at, "in the Chat section");
  assert.ok(GEAR.indexOf("id=rs-compact") < at && at < GEAR.indexOf("id=rs-branch"), "between Compact transcript and Show git branch");
  assert.ok(GEAR.includes("<b>Compact tabs and agents</b>"), "the label");
  // the sub-line leads with the panel, which every layout has, then the strip, and says where the strip half
  // does not apply: under the phone layout's media rule (kernel.py _CHAT_MOBILE_CSS) the session picker
  // stands in for the strip, so a phone gets the panel half alone (the guide paragraph says the same)
  const sub = GEAR.slice(at, GEAR.indexOf("id=rs-branch")).match(/<span class=rs-sub>([^<]*)<\/span>/)![1];
  assert.ok(sub.indexOf("background-work panel") < sub.indexOf("tab strip"), "the panel first, then the strip");
  assert.match(sub, /about four rows/); assert.match(sub, /session picker stands in for the strip/); assert.match(sub, /Off by default\.$/);
  assert.ok(GEAR.includes("dn = document.getElementById('rs-dense')"), "the handle");
  assert.ok(GEAR.includes("if (dn) dn.addEventListener('change', function () { var s = load(); s.denseChrome = dn.checked; save(s); });"),
    "the save path: save() raises romp:settings and posts settingsSync, like every boolean here");
  assert.ok(GEAR.includes("if (dn) dn.checked = s.denseChrome === true;"), "the open-time fill reads the store");
  const load = GEAR.slice(GEAR.indexOf("function load()"), GEAR.indexOf("function tabCtxMode"));
  assert.equal((load.match(/denseChrome: false/g) || []).length, 2, "both copies of the defaults mirror carry the default");
  lacks(GEAR, /denseChrome: true/, "never on by default");
});

test("the dense rules exist under the class, with these exact values", () => {
  const tab = denseRule(".tab");
  assert.match(tab, /gap: 3px;/); assert.match(tab, /padding: 3px 5px;/); assert.match(tab, /font-size: 0\.86em;/);
  assert.match(denseRule(".tab .host-prefix"), /font-size: 0\.92em;/,
    "a federated tab's host prefix compensates for the smaller tab: 0.86 x 0.92 x 13 = 10.3px, the default's rendered size (the floor test below)");
  const add = denseRule(".tab-add");
  assert.match(add, /padding: 3px 8px;/);
  assert.match(add, /font-size: 1\.1em;/, "the + keeps its glyph size: the dense .tab rule outranks .tab-add's own 1.1em, so it is restated");
  assert.match(denseRule(".tab-tagbox"), /min-height: 25px;/, "the tag control's floor follows the dense + tab's rendered height (the arithmetic test below)");
  const head = denseRule(".tab-group-head");
  assert.match(head, /gap: 4px;/); assert.match(head, /padding: 3px 5px;/, 'the dense tab\'s own padding: the row keeps a tab\'s box (T322)');
  assert.doesNotMatch(head, /font-size/, "the group header keeps the surface's one sub-line size (0.82em); its count inherits it and stays legible");
  const sep = denseRule(".tab-group-sep:not(.tab-group-break)");
  assert.match(sep, /padding: 6px 6px;/, "the trail divider's gutters scale with the row, so its line keeps the default's half-row proportion");
  assert.doesNotMatch(sep, /width|height|background/, "the width stays the default's 13px (dragslot's virtual layout measures it); only the gutters change");
  lacks(CSS, /\nbody\.dense-chrome \.tab-group-break/, "the row break keeps no footprint, so no dense rule names it");
  assert.match(denseRule("#bg-tasks"), /margin: 4px 10px 4px;/);
  assert.match(denseRule(".bg-fold-head"), /padding: 5px 11px;/);
  const list = denseRule(".bg-list");
  assert.match(list, /padding: 2px 2px;/); assert.doesNotMatch(list, /max-height/, "the padding holds in both states; the cap is the guarded rule (the cap test below)");
  assert.match(denseRule(".bg-list:not(:has(.bg-task.open))"), /max-height: 100px;/, "about four rows while no row's details are open; the inner scroll is the default rule's");
  assert.match(denseRule(".bg-group-head"), /padding: 3px 9px 1px;/);
  const row = denseRule(".bg-head");
  assert.match(row, /gap: 6px;/); assert.match(row, /padding: 3px 9px;/); assert.match(row, /line-height: 1\.3;/);
  assert.match(denseRule(".bg-sum"), /font-size: 11px;/);
  assert.match(denseRule(".bg-since"), /font-size: 10px;/);
});

test("the dense list cap lifts while a row's details are open, so a command's output is never read through two nested scrolls", () => {
  // the list is the panel's inner scroll (bg-tasks-layout.test.ts: flex 1 1 auto, overflow-y auto, bounded by
  // the panel's own max-height). The dense cap holds it to about four rows while every row is closed; the
  // :not(:has(.bg-task.open)) guard drops the cap the moment a row's details open, so the panel's own
  // min(50vh, 340px) is the only bound on the open row's command and output blocks.
  const dense = CSS.match(new RegExp("\\nbody\\." + DENSE_CHROME_CLASS + " \\.bg-list(:not\\(:has\\([^)]*\\)\\)) \\{ max-height: (\\d+)px; \\}"));
  assert.ok(dense, "the dense list's cap is guarded");
  assert.equal(dense![1], ":not(:has(.bg-task.open))", "the guard names the class bgRow sets on an open row with details");
  assert.equal(dense![2], "100", "four dense rows (24px with the arrow) plus the list's 4px of padding");
  has(RENDER, /\(tOpen && foldable \? " open" : ""\)/, "the open class is the renderer's, set only on a row with a command or output to show");
  has(RENDER, /const rh = el\("div", "bg-head" \+ \(foldable \? "" : " bg-flat"\)\);/, "the row head");
  has(RENDER, /row\.appendChild\(rh\);/, "the head is the row's direct child, the shape the status-word selectors read");
  has(CSS, /\n#bg-tasks \{[^}]*max-height: min\(50vh, 340px\);/, "the panel's own cap bounds an open row's details");
  assert.doesNotMatch(defaultRule(".bg-list"), /max-height/, "the default list carries no cap of its own; the panel's is the bound");
});

test("the dense tag control reserves the dense + tab's exact height: the arithmetic, from the stylesheet", () => {
  // tag-mounts.test.ts recomputes the DEFAULT pair (the .tab-tagbox floor equals the + tab's line + padding +
  // border) from the first .tab-add and .tab-tagbox rules; the dense pair is recomputed here from the dense
  // padding, so neither floor can drift from its + tab alone. #tabs stretches every item on a row to the
  // tallest, so a floor left at 31px would hold the row with the + and the tag control 6px above the 25px rows.
  const addDefault = CSS.match(/\n\.tab-add \{[^}]*\}/)![0];
  const lineH = Number(addDefault.match(/line-height: (\d+)px/)![1]);
  assert.doesNotMatch(denseRule(".tab-add"), /line-height/, "the dense + keeps the default's line-height");
  const padV = Number(denseRule(".tab-add").match(/padding: (\d+)px/)![1]);
  const tabRule = CSS.match(/^\.tab \{[\s\S]*?\n\}/m)![0];
  const borderV = tabRule.includes("border: 1px solid") && tabRule.includes("border-bottom: none") ? 1 : 2;
  assert.doesNotMatch(denseRule(".tab"), /border/, "the dense tab keeps the default's border");
  has(RENDER, /const add = el\("div", "tab tab-add"\);/, "the + is a .tab, so it wears the tab's border and the dense .tab rule");
  const minH = Number(denseRule(".tab-tagbox").match(/min-height: (\d+)px/)![1]);
  assert.equal(minH, lineH + padV * 2 + borderV, "the dense .tab-tagbox floor equals the dense + tab's rendered height");
  assert.equal(minH, 25);
});

test("the trailing status word hides only where the dot already says it; the label and the arrow stay", () => {
  const m = CSS.match(/\n(body\.dense-chrome \.bg-task\.bg-[a-z]+ > \.bg-head > \.bg-status,?\n?)+ \{ display: none; \}/);
  assert.ok(m, "one rule hides .bg-status by row status");
  const statuses = Array.from(m![0].matchAll(/\.bg-task\.bg-([a-z]+) >/g)).map((x) => x[1]).sort();
  assert.deepEqual(statuses, ["armed", "completed", "failed", "running"],
    "the four captions that repeat the dot (taskRowSpec / awaitRowSpec: caption === status); a timer's caption stays, its dot says only waiting");
  assert.doesNotMatch(m![0], /bg-sum|bg-open-agent|tool-open-agent/, "the label and the open-transcript arrow are not touched");
  // the captions are the row specs': each of the four is the row's own status word, and the timer's is not
  has(RENDER, /return \{ id: t\.id, status, caption: status,/, "a tracked task's caption is its status");
  has(RENDER, /status: "running", caption: "running",/, "an agent row's");
  has(RENDER, /status: "armed", caption: "armed",/, "a watch row's");
  has(RENDER, /status: "waiting", caption: it\.kind === "timer" \? "timer" : null,/, "a timer's caption is not its status");
  // the render path is unchanged: the caption is still built for every row that has one
  has(RENDER, /if \(t\.caption\) \{ const st = el\("span", "bg-status"\); st\.textContent = t\.caption; rh\.appendChild\(st\); \}/, "the caption is still built");
});

test("every dense rule is scoped to the body class, and the sheet's defaults are untouched", () => {
  const code = stripComments(CSS);
  for (const line of code.split("\n")) {
    if (!line.includes(DENSE_CHROME_CLASS)) continue;
    assert.match(line, new RegExp("^body\\." + DENSE_CHROME_CLASS + " "), "a dense line is a body-scoped selector: " + line.trim());
  }
  assert.doesNotMatch(FEED, /dense-chrome/, "the strip and the panel live in styles.css alone; the feed's sheet has no dense rule");
  // the defaults, byte for byte the values the dense rules shadow
  const tab = defaultRule(".tab");
  assert.match(tab, /gap: 4px;/); assert.match(tab, /padding: 6px 7px; font-size: 0\.92em;/);
  assert.match(defaultRule(".tab-add"), /font-size: 1\.1em; padding: 6px 10px;/);
  assert.match(defaultRule(".host-prefix"), /font-size: 0\.86em;/);
  assert.match(defaultRule(".tab-tagbox"), /min-height: 31px;/);
  assert.match(defaultRule(".tab-group-sep:not(.tab-group-break)"), /width: 13px; padding: 8px 6px;/);
  const head = defaultRule(".tab-group-head");
  assert.match(head, /gap: 5px; padding: 6px 7px;/);   // a tab's box of space (T322)
  assert.match(head, /font-size: 0\.82em;/);
  assert.match(defaultRule("#bg-tasks"), /margin: 8px 10px 6px;/);
  assert.match(defaultRule(".bg-fold-head"), /padding: 7px 11px;/);
  assert.match(defaultRule(".bg-list"), /padding: 4px 2px;/);
  assert.match(defaultRule(".bg-group-head"), /padding: 6px 9px 2px; font-size: 0\.72em;/);
  assert.match(defaultRule(".bg-head"), /gap: 8px; padding: 5px 9px;/);
  assert.match(defaultRule(".bg-sum"), /font-size: 0\.92em;/);
  assert.match(defaultRule(".bg-since"), /font-size: 0\.82em;/);
  // the defaults come first, so the tests that read a selector's first rule keep reading the default
  // (tag-mounts.test.ts: .tab-add, .tab-tagbox; bg-tasks-layout.test.ts: #bg-tasks, .bg-fold-head, .bg-sum, .bg-head)
  for (const sel of [".tab", ".tab-add", ".tab-tagbox", ".tab-lockbox", ".tab-group-head", ".tab-group-sep:not(.tab-group-break)", "#bg-tasks", ".bg-fold-head", ".bg-list", ".bg-group-head", ".bg-head", ".bg-sum", ".bg-since"]) {
    assert.ok(CSS.indexOf("\n" + sel + " {") < CSS.indexOf("\nbody." + DENSE_CHROME_CLASS + " " + sel + " {"), sel + ": default before dense");
  }
  assert.ok(CSS.indexOf("\n.host-prefix {") < CSS.indexOf("\nbody." + DENSE_CHROME_CLASS + " .tab .host-prefix {"), ".host-prefix: default before dense");
  assert.ok(CSS.indexOf("\n.bg-status {") < CSS.indexOf("\nbody." + DENSE_CHROME_CLASS + " .bg-task.bg-running"), ".bg-status: default before the dense hide");
});

test("the dense sizes are the sheet's own rungs and none is under the 10px floor", () => {
  const block = stripComments(CSS.slice(CSS.indexOf("body." + DENSE_CHROME_CLASS + " .tab {")));
  const sizes = Array.from(block.matchAll(/font-size: ([^;]+);/g)).map((m) => m[1]);
  assert.deepEqual([...new Set(sizes)].sort(), ["0.86em", "0.92em", "1.1em", "10px", "11px"],
    "0.86em and 0.92em are on the em ladder (css-vocab.test.ts); 1.1em is the +'s own, restated; 11px and 10px sit one step under the panel's 0.92em and 0.82em (11.96px and 10.66px at the 13px base)");
  for (const s of sizes) {
    const px = s.endsWith("em") ? parseFloat(s) * 13 : parseFloat(s);
    assert.ok(px >= 10, s + " is at or above the 10px floor");
  }
  // nested em compounds: a child of the tab with its own em size renders at the dense tab's em times its own,
  // which the declared sizes above never show. A federated tab's host prefix at its default 0.86em would render
  // at 9.6px under the bare 0.86em tab. Every em-sized child render.ts puts inside a .tab: the label's host
  // prefix (host-prefix.ts hostNameNodes) and the close glyph.
  has(RENDER, /label\.replaceChildren\(\.\.\.hostNameNodes\(s\.name, id\)\);/, "the label's host prefix");
  has(RENDER, /const close = el\("span", "tab-close"\);/, "the close glyph");
  has(read("ui", "webview", "host-prefix.ts"), /h\.className = off \? "host-prefix off" : "host-prefix";/, "the prefix's class");
  const em = (rule: string, what: string) => { const m = rule.match(/font-size: ([\d.]+)em;/); assert.ok(m, what + " has an em size"); return parseFloat(m![1]); };
  const tabEm = em(denseRule(".tab"), "the dense tab");
  for (const child of [".host-prefix", ".tab-close"]) {
    const dense = CSS.match(new RegExp("\\nbody\\." + DENSE_CHROME_CLASS + " \\.tab " + esc(child) + " \\{([^}]*)\\}"));
    const childEm = em(dense ? dense[1] : defaultRule(child), child);
    const px = tabEm * childEm * 13;
    assert.ok(px >= 10, child + " renders at " + px.toFixed(2) + "px inside a dense tab, at or above the 10px floor");
  }
  lacks(stripComments(CSS), /\n\.tab(?:[.:][^ {\n]*)? [^{\n]+\{[^}]*font-size: [\d.]+em/, "no other em-sized .tab descendant rule exists in the sheet (add it to the list above if one appears)");
});

test("the guide names the setting by its gear label, and its paragraph leads with the panel and qualifies the strip", () => {
  has(GUIDE, /\*\*Compact tabs and agents\*\*/, "the gear label, bold like the other settings named there");
  has(GUIDE, /about four/, "the cap in rows");
  const at = GUIDE.indexOf("**Compact tabs and agents**");
  assert.ok(GUIDE.indexOf("### The chat") < at && at < GUIDE.indexOf("### The feed"), "in the chat section, with the other chat settings");
  const para = GUIDE.slice(GUIDE.lastIndexOf("\n\n", at), GUIDE.indexOf("\n\n", at));
  assert.ok(para.indexOf("background-work panel") < para.indexOf("tab strip"),
    "the panel first (every layout has it), then the strip, which the phone layout replaces with the session picker");
  assert.match(para, /session picker stands in for the strip/);
  assert.match(para, /lifts while a row's details are open/, "the cap's open-row lift is stated where the four rows are");
});
