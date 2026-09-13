// The settings panel in TABS (T379, the user 2026-09-12): the settings grouped by the surface they belong to, six pills
// under the title, one pane each, every existing key kept; the tab-widgets gear on the chat strip opens the Chat tab
// scrolled to its Tab widgets section (the user's amendment: no tab of their own), whose rows are the registered widgets (a live demo, a sliding switch, the widget's options); the last tab used is
// remembered per browser. gear.js builds its DOM from a markup string, so the inventory is read off that string (each
// control's id inside exactly one pane) and the behaviour pinned at the source; tests/test_tab_widgets_browser.py drives
// the served page.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const GEAR = fs.readFileSync(path.join(UI, "gear.js"), "utf8");
const GEAR_CSS = fs.readFileSync(path.join(UI, "gear.css"), "utf8");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

const TABS = ["chat", "feed", "sessions", "automatic", "appearance", "system"];
// the panes, cut from the markup string by their openers (each pane opens with the literal below and the next pane's opener ends it)
function panes(): Record<string, string> {
  const out: Record<string, string> = {};
  const opener = (k: string) => `'<div class=rs-pane data-pane=${k} hidden>' +`;
  for (let i = 0; i < TABS.length; i++) {
    const a = GEAR.indexOf(opener(TABS[i]));
    const b = i + 1 < TABS.length ? GEAR.indexOf(opener(TABS[i + 1])) : GEAR.indexOf("'<div id=rs-login-modal hidden>' +");
    assert.ok(a > 0 && b > a, "pane markers for " + TABS[i]);
    out[TABS[i]] = GEAR.slice(a, b);
  }
  return out;
}

test("six tabs, in the approved order, from ONE list the pills, the panes and selectTab read", () => {
  assert.match(GEAR, /^var RS_TABS = \[\['chat', 'Chat'\], \['feed', 'Feed'\], \['sessions', 'Sessions'\], \['automatic', 'Automatic'\], \['appearance', 'Appearance'\], \['system', 'System'\]\];/m);
  assert.match(GEAR, /'<div class=rs-tabs id=rs-tabs role=tablist>' \+ RS_TABS\.map\(function \(t\) \{ return '<button class=rs-tab type=button role=tab data-tab=' \+ t\[0\] \+ ' aria-selected=false>' \+ t\[1\] \+ '<\/button>'; \}\)\.join\(''\) \+ '<\/div>' \+/);
  const ps = panes();
  assert.deepEqual(Object.keys(ps), TABS, "one pane per tab, in the tab order");
  assert.ok(GEAR.indexOf("id=rs-tabs") < GEAR.indexOf("data-pane=chat"), "the pills come before the first pane");
});

test("every existing control keeps its id and sits in exactly one pane, by the approved grouping", () => {
  const ps = panes();
  const where: Record<string, string[]> = {
    chat: ["rs-compact", "rs-dense", "rs-badge", "rs-branch", "rs-filelink", "rs-filesctl", "rs-chatscheme", "rs-cmtmodel", "rs-cmteffort", "rs-cmtfast", "rs-widgets", "rs-striprows"],
    feed: ["rs-feedcollapsed", "rs-judges-index", "rs-judges-triage"],
    sessions: ["rs-defaultdir", "rs-backend", "rs-fileedit", "rs-panes-sec", "rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-activeonly", "rs-collapsegaps"],
    automatic: ["rs-autonudge", "rs-suggestcompact", "rs-conserve", "rs-thinksum", "rs-judgemodel", "rs-judgefast", "rs-judgeeffort", "rs-distillmodel", "rs-distillfast", "rs-distilleffort", "rs-indexmodel", "rs-indexfast", "rs-indexeffort", "rs-judgeconc"],
    appearance: ["rs-theme", "rs-cmap", "rs-pal"],
    system: ["rs-billing", "rs-login-acct", "rs-updates", "ra-open", "rs-log-open", "rsver"],
  };
  for (const [pane, ids] of Object.entries(where)) {
    for (const id of ids) {
      const homes = TABS.filter((t) => new RegExp("id=" + id + "\\b").test(ps[t]));
      assert.deepEqual(homes, [pane], id + " lives in " + pane + " and nowhere else (found in: " + homes.join(",") + ")");
    }
  }
  // the keyboard-shortcuts rows ride the SHORTCUT_ROWS variable, concatenated into the System pane
  assert.match(ps.system, /\+ SHORTCUT_ROWS \+/);
  assert.match(GEAR, /^var SHORTCUT_ROWS =\s*\n\s*'<div class=rs-key id=rs-keys-web hidden>/m);
  for (const t of TABS.filter((x) => x !== "system")) assert.doesNotMatch(ps[t], /SHORTCUT_ROWS/, t + " holds no shortcut rows");
  // the tab widgets are a SECTION of Chat (the user's amendment 2026-09-12), after the chat's own sections, then the strip's controls;
  // its head carries the data-section anchor the strip's gear asks for; there is no Tabs tab
  assert.match(ps.chat, /<div class='rs-sec' data-section=tabwidgets>Tab widgets<\/div>/);
  assert.ok(ps.chat.indexOf(">Text and comments<") < ps.chat.indexOf("data-section=tabwidgets") && ps.chat.indexOf("data-section=tabwidgets") < ps.chat.indexOf(">Strip<"), "Tab widgets after the chat's sections, Strip last");
  assert.doesNotMatch(GEAR, /data-pane=tabs\b/);
  assert.doesNotMatch(GEAR, /\['tabs', 'Tabs'\]/);
  // the old Context gauge row is gone: its WHEN is the Context bar widget's option in the Tab widgets section
  assert.doesNotMatch(GEAR, /id=rs-tabctx\b/);
  assert.doesNotMatch(GEAR, /Context gauge in tabs/);
  // section sub-heads: the first of each pane wears rs-sec-first (no rule above it), and every pane has one
  for (const t of TABS) assert.match(ps[t], /<div class='rs-sec rs-sec-first'>/, t + " opens with a first section head");
});

test("selectTab shows one pane, marks its pill, remembers it per browser; openSettings takes a tab and a section, and an open panel switches", () => {
  assert.match(GEAR, /var TAB_KEY = 'romp:settingsTab';/);
  assert.match(GEAR, /function selectTab\(t\) \{\s*\n\s*t = knownTab\(t\) \|\| knownTab\(\(function \(\) \{ try \{ return localStorage\.getItem\(TAB_KEY\); \} catch \(e\) \{ return null; \} \}\)\(\)\) \|\| 'chat';/,
    "the named tab, else the remembered one, else Chat");
  assert.match(GEAR, /b\.classList\.toggle\('on', on\); b\.setAttribute\('aria-selected', on \? 'true' : 'false'\);/);
  assert.match(GEAR, /pn\.hidden = pn\.getAttribute\('data-pane'\) !== t;/);
  assert.match(GEAR, /try \{ localStorage\.setItem\(TAB_KEY, t\); \} catch \(e\) \{\}/);
  assert.match(GEAR, /function openSettings\(tab, section\) \{\s*\n\s*if \(!p\.hidden\) \{ if \(knownTab\(tab\)\) \{ selectTab\(tab\); if \(section\) showSection\(section\); else clearSectionScroll\(\); return; \} closeSettings\(\); return; \}/,
    "a named tab on an open panel switches to it and scrolls to its section; a bare ask still toggles");
  assert.match(GEAR, /if \(e\.data && e\.data\.romp === 'openSettings'\) openSettings\(typeof e\.data\.tab === 'string' \? e\.data\.tab : undefined, typeof e\.data\.section === 'string' \? e\.data\.section : undefined\);/, "the tab and the section ride the message");
  // the SECTION anchor (the user's amendment 2026-09-12): looked up in the shown pane only; the card, the modal's one scroll box, scrolls so the head
  // sits under its padding (never scrollIntoView, which would scroll the host document too); after the panel is shown, since rects exist only then
  assert.match(GEAR, /function showSection\(section\) \{\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}\s*\n\s*sectionAsk = null;\s*\n\s*if \(typeof section !== 'string' \|\| !section\) return;\s*\n\s*var sec = document\.querySelector\('#rsettings \.rs-pane:not\(\[hidden\]\) \.rs-sec\[data-section="' \+ section \+ '"\]'\);/);
  assert.match(GEAR, /card\.scrollTop = card\.scrollTop \+ sec\.getBoundingClientRect\(\)\.top - card\.getBoundingClientRect\(\)\.top - padT;/);
  // round two, LOW 1: room at the pane's end so the head reaches the top even for the last section; LOW 2 and 7: one pending ask,
  // disconnected on close and before a new ask, and a plain open resets the card and the room
  assert.match(GEAR, /var go = function \(\) \{\s*\n\s*pane\.style\.paddingBottom = '';/, "the room is cleared before the measurement (a re-ask must not read its own earlier room)");
  // the follow-up's round one, MEDIUM: the room is sized to the card's CAP (max-height, a content box), since below the cap the card
  // grows under the room and the head stops short on tall windows; a second measurement after the write takes up rounding
  assert.match(GEAR, /var capH = parseFloat\(cs\.maxHeight\);\s*\n\s*var content = isFinite\(capH\) && capH > 0 \? capH : \(card\.clientHeight - padT - padB\);\s*\n\s*var missing = content - below\(\);\s*\n\s*pane\.style\.paddingBottom = missing > 0 \? Math\.ceil\(missing\) \+ 'px' : '';\s*\n\s*var short = \(card\.clientHeight - padT - padB\) - below\(\);\s*\n\s*if \(short > 0\) pane\.style\.paddingBottom = Math\.ceil\(Math\.max\(missing, 0\) \+ short\) \+ 'px';/);
  assert.match(GEAR_CSS, /\n\.rs-card \{ width: min\(560px, 94%\); max-height: 88vh; overflow: auto;/, "the cap the room is sized to");
  assert.doesNotMatch(GEAR_CSS.match(/\n\.rs-card \{[^}]*\}/)![0], /box-sizing/, "a content box: max-height caps the content, which is what the room fills");
  assert.match(GEAR, /selectTab\(b\.getAttribute\('data-tab'\)\); clearSectionScroll\(\); \}\); \}\);/, "a pill change clears the room and starts at the top (LOW 1)");
  assert.match(GEAR, /function clearSectionScroll\(\) \{\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}\s*\n\s*sectionAsk = null;\s*\n\s*var card = document\.querySelector\('#rsettings \.rs-card'\);\s*\n\s*if \(card\) \{ card\.scrollTop = 0; card\.removeAttribute\('data-section-landed'\); \}/);
  assert.match(GEAR, /function showSection\(section\) \{\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}/, "a new ask retires the pending one");
  assert.match(GEAR, /function closeSettings\(\) \{ clearSectionScroll\(\); p\.hidden = true; setModalCls\(false\); feedFull\(false\); \}/, "the reset before the hide: a hidden card ignores a scroll write");
  // the ask STANDS: the head re-lands on every size change of the card or the pane (a font arriving, a list filling), until the
  // user's own scroll, a pill change or the close ends it (CI 2026-09-13: the head landed neither at the top nor at the end)
  assert.match(GEAR, /sectionRO = new ResizeObserver\(function \(\) \{ if \(sectionAsk === ask && card\.clientHeight > 0\) go\(\); \}\);\s*\n\s*sectionRO\.observe\(card\);\s*\n\s*sectionRO\.observe\(pane\);/);
  assert.match(GEAR, /ask\.top = card\.scrollTop;/, "the ask remembers what it set, so its own scroll event is not the user's");
  assert.match(GEAR, /card\.setAttribute\('data-section-landed', section\);/, "a landing is marked on the card, so a lab waits for the event, never a delay");
  assert.match(GEAR, /if \(card\) \{ card\.scrollTop = 0; card\.removeAttribute\('data-section-landed'\); \}/, "…and the mark goes with the ask");
  assert.match(GEAR, /card\.addEventListener\('scroll', function \(\) \{ if \(sectionAsk && Math\.abs\(card\.scrollTop - sectionAsk\.top\) > 1\) \{ if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \} sectionAsk = null; \} \}\);/, "the user's scroll ends the ask");
  // the first open in the shell: this document has no layout until the shell lifts its iframe on the settings-open message, so a
  // scroll set then clamps to zero (measured); the card gaining a size is the event, observed once, never a timer
  assert.match(GEAR, /if \(card\.clientHeight > 0\) go\(\);   \/\/ laid out already/, "the first landing when the panel already has a layout; the ask then stands (no return: the observer still watches)");
  const showAt = GEAR.indexOf("function showSection(section) {");
  const showSec = GEAR.slice(showAt, GEAR.indexOf("\n  }\n", showAt) + 4);   // the function body alone
  assert.doesNotMatch(showSec, /setTimeout|requestAnimationFrame/, "no time-based guess at the shell's round trip");
  assert.doesNotMatch(showSec, /scrollIntoView/, "the section scroll is set on the card, never scrollIntoView (which scrolls the host document too)");
  assert.match(GEAR, /plFill\(\); fill\(\); if \(section\) showSection\(section\); else clearSectionScroll\(\); \}/, "the scroll (or the reset) is the opener's last act, after the panel is displayed");
  assert.match(GEAR_CSS, /#rsettings \.rs-pane\[hidden\] \{ display: none; \}/, "a hidden pane is out of the flow (the [hidden] rule the author display would beat)");
  assert.match(GEAR_CSS, /#rsettings \.rs-tab\.on \{ color: var\(--accent, #9cd2ff\); border-color: var\(--accent, #9cd2ff\); background: var\(--accent-wash, rgba\(156, 210, 255, 0\.12\)\); font-weight: 600; \}/);
});

test("the Tab widgets section's rows come from the strip's own module: built once, painted in place, a sliding switch, house pickers for the options", () => {
  assert.match(GEAR, /var TW = require\('\.\/tab-widgets\.ts'\);/);
  assert.match(GEAR, /function buildWidgets\(\) \{\s*\n\s*if \(!wHost \|\| wHost\.children\.length\) return;\s*\n\s*TW\.tabWidgets\(\)\.forEach\(function \(w\) \{/, "one row per registered widget, built once");
  assert.match(GEAR, /sw\.className = 'rs-switch'; sw\.setAttribute\('role', 'switch'\);/, "the sliding toggle (the user's pick), a switch to the accessibility tree");
  assert.match(GEAR, /prefs\.on\[w\.id\] = !TW\.widgetOn\(prefs, w\); saveWidgets\(prefs\);/, "the switch flips the widget's own flag");
  assert.match(GEAR, /var drop = housePick\(wrap, 'wopt-' \+ w\.id \+ '-' \+ o\.key, widgetOptRowHTML,/, "an option is the panel's house picker");
  assert.match(GEAR, /var node = TW\.renderWidgetDemo\(w, prefs\);/, "the live demo is the widget's OWN render over the demo status");
  assert.match(GEAR, /if \(w\.slot === 'before'\) \{ if \(node\) tab\.appendChild\(node\); tab\.appendChild\(label\); \}\s*\n\s*else \{ tab\.appendChild\(label\); if \(node\) tab\.appendChild\(node\); \}/, "the demo places the node in the widget's slot: before the name or after it (no corner slot: pinning is gone, the user 2026-09-12)");
  assert.match(GEAR, /r\.sw\.classList\.toggle\('on', on\); r\.sw\.setAttribute\('aria-checked', on \? 'true' : 'false'\);/);
  assert.match(GEAR, /r\.demo\.replaceChildren\(tab\);/, "the demo is re-filled in place: the row's controls are never rebuilt (click-safe)");
  assert.match(GEAR, /function saveWidgets\(prefs\) \{ var s = load\(\); s\.tabWidgets = prefs; s\.tabCtx = TW\.tabCtxOfPrefs\(prefs\); save\(s\); paintWidgets\(\); \}/, "the prefs and the tabCtx mirror, through the one save()");
  // round one, HIGH: NO injected default for tabWidgets. An empty object in load()'s defaults won over a pre-widgets store's
  // tabCtx (the derivation runs only with no object), and a save of any setting wrote it and rewrote the mirror. The prefs
  // derive from tabCtx at read time, as settings.ts does, and only a widget change writes the key.
  assert.doesNotMatch(GEAR, /tabWidgets: \{ on: \{\}, order: \[\], opts: \{\} \}/);
  assert.doesNotMatch(GEAR.slice(GEAR.indexOf("function load() {"), GEAR.indexOf("function save(s) {")), /tabWidgets/, "load() neither defaults nor touches the key");
  assert.match(GEAR, /function widgetPrefs\(s\) \{ return TW\.tabWidgetPrefs\(s\.tabWidgets, s\.tabCtx\); \}/, "read-time derivation from the mirror when the store has no prefs");
  assert.equal((GEAR.match(/s\.tabWidgets = /g) || []).length, 1, "one writer of the key: saveWidgets");
  assert.match(GEAR, /d\.className = 'rs-sub'; d\.textContent = w\.description;/, "the description is the row's hover popover, the panel's idiom (round one, LOW 2)");
  assert.match(GEAR_CSS, /#rsettings \.rs-switch\.on::after \{ left: 18px;/, "the knob slides");
  assert.match(GEAR_CSS, /#rsettings \.rs-widgets \{ display: grid; grid-template-columns: 96px 1fr auto auto; column-gap: 10px; \}\s*\n#rsettings \.rs-widget \{ display: grid; grid-template-columns: subgrid; grid-column: 1 \/ -1;/, "one grid across the rows, each row a subgrid of it (round one, LOW 2)");
  assert.match(GEAR_CSS, /#rsettings \.rs-row:hover \.rs-sub, #rsettings \.rs-widget:hover \.rs-sub \{ display: block; position: absolute;/, "the widget rows share the panel's hover popover rule");
  assert.doesNotMatch(GEAR_CSS, /#rsettings \.rs-widget-name span \{/, "no always-painted description rule");
  // round two, LOW 3: grid-template-columns: subgrid needs Chromium 117 and the stylesheet's oklch(from) 119; the extension's declared
  // VS Code floor is 1.88 (Electron 28, Chromium 120), the first release that ships both
  const pkg = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "vscode-extension", "package.json"), "utf8"));
  assert.equal(pkg.engines.vscode, "^1.88.0", "the declared floor carries subgrid (117) and oklch(from) (119)");
  const lock = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "vscode-extension", "package-lock.json"), "utf8"));
  assert.equal(lock.packages[""].engines.vscode, "^1.88.0", "the lockfile's root agrees, so the first install after the merge rewrites nothing (LOW 2)");
  assert.match(GEAR_CSS, /#rsettings \.rs-widget-demo \.tab-dot \{ flex: 0 0 auto; width: 7px; height: 7px; border-radius: 50%; background: var\(--st-working-bg, #e0b020\); \}/, "the demo wears the strip's vocabulary in this sheet's fallbacks");
});

test("the strip's gear glyph opens the Chat tab at its Tab widgets section through the shell (or this window's own gear), and the shell relays the tab and the section", () => {
  assert.match(RENDER, /function openSettingsOn\(tab: string, section\?: string\): void \{\s*\n\s*const m: \{ romp: string; tab: string; section\?: string \} = \{ romp: "openSettings", tab \};\s*\n\s*if \(section\) m\.section = section;\s*\n\s*if \(inRompShell\(\)\) \{ try \{ window\.parent\.postMessage\(m, "\*"\); \} catch \{[^}]*\} return; \}\s*\n\s*window\.postMessage\(m, "\*"\);/, "through the shell when in one, else to this window; the section only when given");
  assert.match(RENDER, /if \(\(window as any\)\.__rompShowStrip \|\| inRompShell\(\)\) \{\s*\n\s*const gear = el\("button", "tab-widgets-gear"\) as HTMLButtonElement;/, "the glyph only where a gear can be reached: an honest absence elsewhere");
  assert.match(RENDER, /gear\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); openSettingsOn\("chat", "tabwidgets"\); \}\);/, "the glyph asks for Chat at its Tab widgets section");
  assert.ok(RENDER.indexOf('el("button", "tab-widgets-gear")') > RENDER.indexOf("tagBox.appendChild(tagChipsHost);") && RENDER.indexOf('el("button", "tab-widgets-gear")') < RENDER.indexOf("bar.appendChild(tagBox);"), "inside the tag box, so it takes no extra height");
  assert.match(KERNEL, /window\.__rompOpenSettings=function\(tab,section\)\{var f=document\.getElementById\('f-settings'\);if\(!f\)return;/);
  assert.match(KERNEL, /var msg=\{romp:'openSettings'\};if\(typeof tab==='string'&&tab\)msg\.tab=tab;if\(typeof section==='string'&&section\)msg\.section=section;\s*\n\s*var open=function\(\)\{try\{f\.contentWindow&&f\.contentWindow\.postMessage\(msg,'\*'\);\}catch\(e\)\{\}\};/, "the shell forwards the tab and the section into the settings iframe; a bare ask stays bare");
  assert.match(KERNEL, /if\(m\.romp==='openSettings'\)window\.__rompOpenSettings\(m\.tab,m\.section\);/, "a pane's ask carries its tab and section through");
});
