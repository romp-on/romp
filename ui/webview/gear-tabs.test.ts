// The settings panel in TABS (T379, the user 2026-09-12; re-cut T400: General, Chat, Feed, Sessions, Task tracking, Appearance, Debug):
// the settings grouped by the surface they belong to, seven pills
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
const FEED_CSS = fs.readFileSync(path.join(UI, "feed.css"), "utf8");

const TABS = ["general", "chat", "feed", "sessions", "automation", "tasks", "debug"];   // T404: Automation new, Appearance a General section
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

test("seven tabs, in the user's order (T400: General first, Debug last; T404: Automation new, Appearance folded into General), from ONE list the pills, the panes and selectTab read", () => {
  assert.match(GEAR, /^var RS_TABS = \[\['general', 'General'\], \['chat', 'Chat'\], \['feed', 'Feed'\], \['sessions', 'Sessions'\], \['automation', 'Automation'\], \['tasks', 'Task tracking'\], \['debug', 'Debug'\]\];/m);
  // older remembered tabs and older asks land on the tab that holds their rows now, never a blank card
  assert.match(GEAR, /^var TAB_ALIASES = \{ automatic: 'tasks', system: 'debug', tabs: 'chat', appearance: 'general' \};/m, "the former Appearance tab maps to General (T404)");
  assert.match(GEAR, /function knownTab\(t\) \{ t = TAB_ALIASES\[t\] \|\| t; return RS_TABS\.some\(function \(x\) \{ return x\[0\] === t; \}\) \? t : null; \}/);
  assert.match(GEAR, /'<div class=rs-tabs id=rs-tabs role=tablist>' \+ RS_TABS\.map\(function \(t\) \{ return '<button class=rs-tab type=button role=tab data-tab=' \+ t\[0\] \+ ' aria-selected=false>' \+ t\[1\] \+ '<\/button>'; \}\)\.join\(''\) \+ '<\/div>' \+/);
  const ps = panes();
  assert.deepEqual(Object.keys(ps), TABS, "one pane per tab, in the tab order");
  assert.ok(GEAR.indexOf("id=rs-tabs") < GEAR.indexOf("data-pane=general"), "the pills come before the first pane");
});

test("every existing control keeps its id and sits in exactly one pane, by the approved grouping", () => {
  const ps = panes();
  const where: Record<string, string[]> = {
    general: ["rs-billing", "rs-login-acct", "rs-login-btn", "rs-panes-sec", "rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-filesctl", "rs-theme", "rs-cmap", "rs-pal", "rs-fileedit", "rs-conserve", "rs-updates"],
    chat: ["rs-compact", "rs-dense", "rs-chatscheme", "rs-striprows", "rs-cmtmodel", "rs-cmteffort", "rs-cmtfast", "rs-thinksum", "rs-wholechat", "rs-widgets", "rs-rings", "rs-swidgets"],   // rs-rings: the ring widgets' rows (2026-09-14), under the title widgets' rows in the same section
    feed: ["rs-feedcollapsed"],
    sessions: ["rs-defaultdir", "rs-backend"],
    automation: ["rs-autonudge", "rs-suggestcompact", "rs-alwaysfast", "rs-retryupgrade"],   // the two model switches: kernel policies applied to sessions on the kernel's own initiative (2026-09-17)
    tasks: ["rs-tasktrack", "rs-judgemodel", "rs-judgefast", "rs-judgeeffort", "rs-distillmodel", "rs-distillfast", "rs-distilleffort", "rs-indexmodel", "rs-indexfast", "rs-indexeffort", "rs-judgeconc"],
    debug: ["rs-judges-index", "rs-judges-triage", "ra-open", "rs-log-open", "rsver"],
  };
  for (const [pane, ids] of Object.entries(where)) {
    for (const id of ids) {
      const homes = TABS.filter((t) => new RegExp("id=" + id + "\\b").test(ps[t]));
      assert.deepEqual(homes, [pane], id + " lives in " + pane + " and nowhere else (found in: " + homes.join(",") + ")");
    }
  }
  // the keyboard-shortcuts rows ride the SHORTCUT_ROWS variable, concatenated into the General pane (T400)
  assert.match(ps.general, /\+ SHORTCUT_ROWS \+/);
  assert.match(GEAR, /^var SHORTCUT_ROWS =\s*\n\s*'<div class=rs-key id=rs-keys-web hidden>/m);
  for (const t of TABS.filter((x) => x !== "general")) assert.doesNotMatch(ps[t], /SHORTCUT_ROWS/, t + " holds no shortcut rows");
  // T404: General opens with the account, then the panes (the Files control among them), Appearance, Permissions, This machine, the
  // shortcuts; Debug opens with the judges' debug views, then the diagnostics (Updates went to General)
  const G = ps.general;
  assert.ok(G.indexOf(">Account<") < G.indexOf("id=rs-panes-sec") && G.indexOf("id=rs-panes-sec") < G.indexOf("id=rs-filesctl") && G.indexOf("id=rs-filesctl") < G.indexOf("data-section=appearance>Appearance<")
            && G.indexOf("data-section=appearance>Appearance<") < G.indexOf(">Permissions<") && G.indexOf(">Permissions<") < G.indexOf("id=rs-fileedit") && G.indexOf("id=rs-fileedit") < G.indexOf(">This machine<")
            && G.indexOf(">This machine<") < G.indexOf("id=rs-conserve") && G.indexOf("id=rs-conserve") < G.indexOf("id=rs-updates") && G.indexOf("id=rs-updates") < G.indexOf(">Keyboard shortcuts<"),
            "General: Account, Panes (with the Files control), Appearance, Permissions, This machine, Keyboard shortcuts");
  assert.match(G, /<b>Allow file editing<\/b>/, "the permission row's name (T404)");
  assert.match(G, /<label class="rs-row rs-panes-row"><input type=checkbox id=rs-filesctl>' \+[^\n]*\n\s*'<span><b>Files<\/b>'/, "the Files row reads Files, like Sessions, Outline and Feed above it (T407), and is a Panes row like them, so the off-dashboard hide takes it (T404's tidy)");
  assert.match(GEAR, /delete o\.filesControl; delete o\.fileLinkPane;/, "load() drops both dead keys, so neither survives a gear save (T404's tidy)");
  assert.match(ps.chat, /The summaries are output tokens the session pays for, which is why this row sits under Chat and not Display\./, "the Thinking row says why it is Chat's (T404's tidy)");
  // the popover stays inside the card (the T408 read): a row whose popover would run past the card's bottom opens it above
  const CSS2 = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.css"), "utf8");
  assert.match(GEAR, /var HOSTS = '#rsettings \.rs-fastin, #rsettings \.rs-row, #rsettings \.rs-widget';/, "a Fast mode box is a host of its own (round two, the medium)");
  assert.match(GEAR, /function ownSub\(host\) \{[^}]*if \(subs\[i\]\.closest\(HOSTS\) === host\) return subs\[i\];/, "the popover the host owns, never a nested box's");
  assert.match(GEAR, /var anchor = host\.classList\.contains\('rs-fastin'\) \? \(host\.closest\('#rsettings \.rs-row'\) \|\| host\) : host;\s*\n\s*var ar = anchor\.getBoundingClientRect\(\), above = ar\.top - cr\.top;/,
    "the room above is measured from the popover's containing block, the row (round three, low 3)");
  assert.match(GEAR, /if \(above >= sr\.height \+ 2\) host\.classList\.add\('rs-up'\);/, "up only when it fits above; neither side fitting, it stays below, where the card's scroll reaches the clip (round three, the ruling)");
  assert.doesNotMatch(GEAR, /above > below/, "no roomier-side clause");
  assert.match(GEAR, /pcard\.addEventListener\('mouseover', function \(e\) \{ var host = hostOf\(e\.target\); if \(host\) placeSub\(host\); \}\);/);
  assert.match(GEAR, /pcard\.addEventListener\('mouseout', function \(e\) \{ var host = hostOf\(e\.target\); if \(host && !\(e\.relatedTarget && host\.contains\(e\.relatedTarget\)\)\) host\.classList\.remove\('rs-up'\); \}\);/, "the class goes with the pointer, so the next hover measures afresh");
  assert.match(CSS2, /#rsettings \.rs-row:hover \.rs-fastin\.rs-up \.rs-sub \{ top: auto; bottom: 100%; margin-top: 0; margin-bottom: 2px; \}/, "the box's own up rule, (1,5,0): above the hover rule and the row's up rule whatever the order");
  assert.match(CSS2, /#rsettings \.rs-row\.rs-up:hover \.rs-sub, #rsettings \.rs-widget\.rs-up:hover \.rs-sub \{ top: auto; bottom: 100%; margin-top: 0; margin-bottom: 2px; \}/, "the up rule outranks the hover rule by one class");
  assert.doesNotMatch(GEAR, /Files control in the dashboard bar/, "the old words are gone from the gear");
  assert.match(G, /<b>Updates install automatically <span class=rs-mixed hidden><\/span><\/b>/, "the updates row's name (T404)");
  assert.doesNotMatch(GEAR, /id=rs-filelink\b|File links open in/, "the file-links setting is gone: the route follows the open Files pane (T404)");
  assert.equal((GEAR.match(/fileLinkPane/g) || []).length, 1, "the dead key is named once, where load() drops it");
  assert.doesNotMatch(GEAR, /id=rs-activeonly\b|id=rs-collapsegaps\b|>Sessions pane</, "the Sessions-pane rows left settings: the pane carries them (T404)");
  assert.doesNotMatch(GEAR, /data-pane=appearance\b|\['appearance', 'Appearance'\]/, "no Appearance pane or pill remains");
  // Chat: Display (the transcript rows, the text scheme, the strip's one-group-per-row), Comments, Thinking, Tab widgets
  const C = ps.chat;
  assert.ok(C.indexOf(">Display<") < C.indexOf("id=rs-compact") && C.indexOf("id=rs-dense") < C.indexOf("id=rs-chatscheme") && C.indexOf("id=rs-chatscheme") < C.indexOf("id=rs-striprows")
            && C.indexOf("id=rs-striprows") < C.indexOf(">Comments<") && C.indexOf(">Comments<") < C.indexOf("id=rs-cmtmodel") && C.indexOf("id=rs-cmtfast") < C.indexOf(">Thinking<")
            && C.indexOf(">Thinking<") < C.indexOf("id=rs-thinksum") && C.indexOf("id=rs-thinksum") < C.indexOf(">Chat history<")
            && C.indexOf(">Chat history<") < C.indexOf("id=rs-wholechat") && C.indexOf("id=rs-wholechat") < C.indexOf("data-section=tabwidgets")
            && C.indexOf("data-section=tabwidgets") < C.indexOf("data-section=statusline"), "Chat: Display, Comments, Thinking, Chat history, Tab widgets, Status line (T409)");
  assert.doesNotMatch(C, /id=rs-badge|id=rs-branch/, "the badge and branch checkboxes left the Display section: the Status line section's rows are the controls (T409)");
  assert.doesNotMatch(C, />Transcript<|>Text and comments<|>Files<|>Strip</, "the old Chat heads are gone");
  // Automation: the nudges; Task tracking: the judges alone; Debug: the judges' views then the diagnostics
  assert.ok(ps.automation.indexOf(">Nudges<") < ps.automation.indexOf("id=rs-autonudge") && ps.automation.indexOf("id=rs-autonudge") < ps.automation.indexOf("id=rs-suggestcompact")
            && ps.automation.indexOf("id=rs-suggestcompact") < ps.automation.indexOf("<div class='rs-sec'>Model</div>") && ps.automation.indexOf(">Model<") < ps.automation.indexOf("id=rs-alwaysfast")
            && ps.automation.indexOf("id=rs-alwaysfast") < ps.automation.indexOf("id=rs-retryupgrade"), "Automation: Nudges, Auto Nudge, Suggest /compact; then Model, Always fast, Retry upgrades after downgrades (2026-09-17)");
  assert.ok(ps.tasks.indexOf("<div class='rs-sec rs-sec-first'>Task tracking</div>") === ps.tasks.indexOf("<div class='rs-sec") && ps.tasks.indexOf("id=rs-tasktrack") < ps.tasks.indexOf(">Judges<") && ps.tasks.indexOf(">Judges<") < ps.tasks.indexOf("id=rs-judgemodel"), "Task tracking opens with the master switch, then the Judges (T404 PR 2)");
  // T408: the two Automation rows carry a permanent one-sentence line in place of a hover tooltip, and no title attribute
  assert.match(ps.automation, /<span class=rs-line id=rs-autonudge-sub>' \+ AUTONUDGE_SUB \+ '<\/span>'/, "Auto Nudge's line, the var fillAutoNudge appends the mixed hosts to");
  assert.match(GEAR, /var AUTONUDGE_SUB = "When a session goes idle with its work still in progress and nothing awaited, nudge it once for a status update, on every connected machine\.";/);
  assert.match(ps.automation, /<span class=rs-line>When a session has sat idle for an hour with a lot of context built up, suggest one \/compact at a natural point, once per fill-up, on every connected machine\.<\/span>/);
  assert.doesNotMatch(ps.automation, /rs-sub/, "no hover tooltip in the Automation pane: a popup under a two-row pane ran past the card and scrolled it");
  assert.doesNotMatch(ps.automation, /title=/, "no title attribute either");
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.css"), "utf8");
  assert.match(CSS, /#rsettings \.rs-line \{ display: block; color: var\(--text-muted, #9aa0a6\); font-size: 0\.92em; line-height: 1\.35; margin-top: 1px; \}/, "the note's dress, on its own line, in the flow");
  assert.ok(ps.debug.indexOf(">Judging bands<") < ps.debug.indexOf(">Diagnostics<") && ps.debug.indexOf(">Diagnostics<") < ps.debug.indexOf("id=rsver") && ps.debug.indexOf(">Updates<") < 0, "Debug: Judging bands, Diagnostics, the version; no Updates");
  assert.doesNotMatch(GEAR, /data-pane=(automatic|system)\b/, "no Automatic or System pane remains");
  // the tab widgets are a SECTION of Chat (the user's amendment 2026-09-12), after the chat's own sections, then the strip's controls;
  // its head carries the data-section anchor the strip's gear asks for; there is no Tabs tab
  assert.match(ps.chat, /<div class='rs-sec' data-section=tabwidgets>Tab widgets<\/div>/);
  assert.ok(ps.chat.indexOf(">Thinking<") < ps.chat.indexOf("data-section=tabwidgets"), "Tab widgets after the chat's sections, last (the Strip section folded into Display, T404)");
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
  assert.match(GEAR, /function openSettings\(tab, section\) \{\s*\n\s*if \(raBack && !raBack\.hidden\) raHide\(\);[^\n]*\n\s*if \(tab === 'appearance' && !section\) section = 'appearance';[^\n]*\n\s*if \(!p\.hidden\) \{ if \(knownTab\(tab\)\) \{ selectTab\(tab\); if \(section\) showSection\(section\); else clearSectionScroll\(\); return; \} closeSettings\(\); return; \}/,
    "a named tab on an open panel switches to it and scrolls to its section; a bare ask still toggles");
  assert.match(GEAR, /if \(e\.data && e\.data\.romp === 'openSettings'\) openSettings\(typeof e\.data\.tab === 'string' \? e\.data\.tab : undefined, typeof e\.data\.section === 'string' \? e\.data\.section : undefined\);/, "the tab and the section ride the message");
  // the SECTION anchor (the user's amendment 2026-09-12): looked up in the shown pane only; the card, the modal's one scroll box, scrolls so the head
  // sits under its padding (never scrollIntoView, which would scroll the host document too); after the panel is shown, since rects exist only then
  assert.match(GEAR, /function showSection\(section\) \{\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}\s*\n\s*sectionAsk = null;\s*\n\s*var card0 = [^\n]*\n\s*if \(card0\) card0\.removeAttribute\('data-section-landed'\);[^\n]*\n\s*if \(typeof section !== 'string' \|\| !section\) return;\s*\n\s*var sec = document\.querySelector\('#rsettings \.rs-pane:not\(\[hidden\]\) \.rs-sec\[data-section="' \+ section \+ '"\]'\);/);
  assert.match(GEAR, /writeCard\(card, card\.scrollTop \+ sec\.getBoundingClientRect\(\)\.top - card\.getBoundingClientRect\(\)\.top - padT, top0\);/);
  // round two, LOW 1: room at the pane's end so the head reaches the top even for the last section; LOW 2 and 7: one pending ask,
  // disconnected on close and before a new ask, and a plain open resets the card and the room
  assert.match(GEAR, /var go = function \(\) \{\s*\n\s*var top0 = card\.scrollTop;[^\n]*\n\s*pane\.style\.paddingBottom = '';/, "the room is cleared before the measurement (a re-ask must not read its own earlier room)");
  // the follow-up's round one, MEDIUM: the room is sized to the card's CAP (max-height, a content box), since below the cap the card
  // grows under the room and the head stops short on tall windows; a second measurement after the write takes up rounding
  // the follow-up's round two: the cap is a BORDER box on the served page (feed.css makes every element one), so the room fills the cap
  // less paddings and borders; no cap means nothing to room for (the fallback that doubled the room is gone); one measurement after the write
  assert.match(FEED_CSS, /^\* \{ box-sizing: border-box; \}/m, "the served page's rule the arithmetic answers to");
  assert.match(GEAR, /var capH = parseFloat\(cs\.maxHeight\);\s*\n\s*if \(!isFinite\(capH\) \|\| capH <= 0\) \{ pane\.style\.paddingBottom = ''; land\(\); return; \}/);
  assert.match(GEAR, /var edges = cs\.boxSizing === 'border-box' \? padT \+ padB \+ \(parseFloat\(cs\.borderTopWidth\) \|\| 0\) \+ \(parseFloat\(cs\.borderBottomWidth\) \|\| 0\) : 0;\s*\n\s*var content = capH - edges;\s*\n\s*var missing = content - below\(\);\s*\n\s*pane\.style\.paddingBottom = missing > 0 \? Math\.ceil\(missing\) \+ 'px' : '';\s*\n\s*var short = \(card\.clientHeight - padT - padB\) - below\(\);[^\n]*\n\s*if \(short > 0\) pane\.style\.paddingBottom = Math\.ceil\(Math\.max\(missing, 0\) \+ short\) \+ 'px';\s*\n\s*land\(\);/);
  assert.match(GEAR_CSS, /\n\.rs-card \{ width: min\(560px, 94%\); max-height: 88vh; overflow: auto;/, "the cap the room is sized to");
  assert.match(GEAR, /selectTab\(b\.getAttribute\('data-tab'\)\); clearSectionScroll\(\); \}\); \}\);/, "a pill change clears the room and starts at the top (LOW 1)");
  assert.match(GEAR, /function clearSectionScroll\(\) \{\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}\s*\n\s*sectionAsk = null;\s*\n\s*var card = document\.querySelector\('#rsettings \.rs-card'\);\s*\n\s*if \(card\) \{ writeCard\(card, 0\); card\.removeAttribute\('data-section-landed'\); \}/);
  assert.match(GEAR, /function showSection\(section\) \{\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}/, "a new ask retires the pending one");
  assert.match(GEAR, /function closeSettings\(\) \{ endDrags\(\); if \(raBack && !raBack\.hidden\) raHide\(\); clearSectionScroll\(\); p\.hidden = true; setModalCls\(false\); feedFull\(false\); \}/, "the reset before the hide: a hidden card ignores a scroll write");
  // the ask STANDS: the head re-lands on every size change of the card or the pane (a font arriving, a list filling), until the
  // user's own scroll, a pill change or the close ends it (CI 2026-09-13: the head landed neither at the top nor at the end)
  assert.match(GEAR, /sectionRO = new ResizeObserver\(function \(\) \{ if \(sectionAsk === ask && card\.clientHeight > 0\) go\(\); \}\);\s*\n\s*sectionRO\.observe\(card\);\s*\n\s*sectionRO\.observe\(pane\);/);
  assert.match(GEAR, /card\.setAttribute\('data-section-landed', section\);/, "a landing is marked on the card, so a lab waits for the event, never a delay");
  assert.match(GEAR, /if \(card\) \{ writeCard\(card, 0\); card\.removeAttribute\('data-section-landed'\); \}/, "…and the mark goes with the ask (the reset writes through the ledger, round three)");
  // the follow-up's round two, MEDIUM: only the user's INPUT ends the ask (the chat's gestureEvidence rule: a wheel, key or touch within
  // the window before the scroll, or a pointer holding the scroller); a scroll delta is no evidence (the browser's anchoring and a
  // taller window's clamp both moved the card and were read as the user's)
  assert.match(GEAR, /^var LS = require\('\.\/landing-settle\.ts'\);/m, "one rule, the chat's");
  // round three, MEDIUM: the inputs are read on the WINDOW with capture (the chat's settleInput), never on the card, which has no
  // tabindex: a key scroll after a click in the card targets BODY, so a card listener never saw it and the next size change threw
  // the user's scroll away. A wheel, a touch or a press counts by its target's containment; a key by the card being the scroll
  // focus (the last press in it, or the focus in it) and no field holding the focus
  assert.match(GEAR, /\['wheel', 'keydown', 'touchstart', 'pointerdown'\]\.forEach\(function \(k\) \{ window\.addEventListener\(k, onInput, \{ capture: true, passive: true \}\); \}\);/);
  assert.doesNotMatch(GEAR, /card\.addEventListener\('(wheel|keydown|touchstart|pointerdown)'/, "no input listener on the card itself");
  assert.match(GEAR, /if \(e\.type === 'keydown'\) \{ if \(inField\(\) \|\| !keyFocus\(\)\) return; \}/);
  assert.match(GEAR, /var keyFocus = function \(\) \{ var a = document\.activeElement; return pressedIn \|\| \(!!a && a !== document\.body && card\.contains\(a\)\); \};/);
  assert.match(GEAR, /var inCard = function \(e\) \{ return e\.target instanceof Node && card\.contains\(e\.target\); \};/);
  assert.match(GEAR, /pressedIn = inCard\(e\);\s*\n\s*if \(!pressedIn\) return;/, "a press outside the card ends the card's scroll focus");
  assert.match(GEAR, /else if \(!inCard\(e\)\) return;\s*\n\s*inputAt = performance\.now\(\);/, "a wheel or a touch counts by containment");
  // round three, LOW 2: the hold latches on the gutter only (landing-settle's scrollerGrab by the offsets, never by the target being
  // the card, whose padding takes a press as its own target), and a lost release ends with the page's focus or visibility
  assert.match(GEAR, /var r = card\.getBoundingClientRect\(\);\s*\n(\s*\/\/[^\n]*\n)*\s*if \(LS\.scrollerGrab\(false, e\.clientX - r\.left - card\.clientLeft, e\.clientY - r\.top - card\.clientTop, card\.clientWidth, card\.clientHeight\)\) held = true;/, "offsets from the padding box (round four, LOW 2)");
  // round four, MEDIUM 2: a field is an element that consumes the scroll keys; a checkbox, radio, button or range input is none
  assert.match(GEAR, /var NOT_FIELDS = \{ checkbox: 1, radio: 1, button: 1, submit: 1, reset: 1, range: 1, color: 1, file: 1, image: 1 \};/);
  assert.match(GEAR, /var inField = function \(\) \{ var a = document\.activeElement; if \(!a\) return false;\s*\n\s*if \(a\.tagName === 'INPUT'\) return !NOT_FIELDS\[String\(a\.getAttribute\('type'\) \|\| 'text'\)\.toLowerCase\(\)\];\s*\n\s*return a\.tagName === 'TEXTAREA' \|\| a\.tagName === 'SELECT' \|\| !!a\.isContentEditable; \};/);
  assert.doesNotMatch(GEAR, /a\.tagName === 'INPUT' \|\| a\.tagName === 'SELECT'/, "no blanket INPUT rule");
  assert.match(GEAR, /window\.addEventListener\('pointerup', function \(\) \{ held = false; \}\);\s*\n\s*window\.addEventListener\('pointercancel', function \(\) \{ held = false; \}\);/);
  assert.match(GEAR, /window\.addEventListener\('blur', function \(\) \{ held = false; \}\);\s*\n\s*document\.addEventListener\('visibilitychange', function \(\) \{ held = false; \}\);/);
  // round three, LOW 1: the ask's own writes are marked and their echo is consumed before the evidence is read; no unmarked write
  // of the card's scrollTop anywhere in the ask's code
  assert.match(GEAR, /var sectionRO = null, sectionAsk = null, sectionWrote = false;/);
  // round four, MEDIUM 1: the move is the FRAME's (the caller's start value against the value after the write; go() reads it before
  // the room drop that clamps the card), and a debt no scroll event pays is forgiven two frames on
  assert.match(GEAR, /function writeCard\(card, top, from\) \{\s*\n\s*var before = from === undefined \? card\.scrollTop : from;\s*\n\s*card\.scrollTop = top;\s*\n\s*if \(card\.scrollTop === before\) return;\s*\n\s*sectionWrote = true;\s*\n\s*if \(typeof requestAnimationFrame === 'function'\) requestAnimationFrame\(function \(\) \{ requestAnimationFrame\(function \(\) \{ sectionWrote = false; \}\); \}\);\s*\n\s*\}/, "a write that moved the frame owes one echo, forgiven two frames on if unpaid; one that did not owes none");
  assert.match(GEAR, /var go = function \(\) \{\s*\n\s*var top0 = card\.scrollTop;[^\n]*\n\s*pane\.style\.paddingBottom = '';/, "the frame's start is read before the room drop");
  assert.match(GEAR, /writeCard\(card, card\.scrollTop \+ sec\.getBoundingClientRect\(\)\.top - card\.getBoundingClientRect\(\)\.top - padT, top0\);/, "the landing writes through the ledger");
  assert.match(GEAR, /card\.addEventListener\('scroll', function \(\) \{\s*\n\s*if \(sectionWrote\) \{ sectionWrote = false; return; \}[^\n]*\n\s*if \(!sectionAsk \|\| !LS\.gestureEvidence\(inputAt, performance\.now\(\), held\)\) return;\s*\n\s*if \(sectionRO\) \{ sectionRO\.disconnect\(\); sectionRO = null; \}\s*\n\s*sectionAsk = null;\s*\n\s*card\.removeAttribute\('data-section-landed'\);/);
  const ASK_CODE = GEAR.slice(GEAR.indexOf("var sectionRO = null"), GEAR.indexOf("THE WIDGET ROWS"));
  assert.equal((ASK_CODE.match(/card\.scrollTop = /g) || []).length, 1, "one scrollTop write in the ask's code: writeCard's");
  assert.match(ASK_CODE, /function writeCard\(card, top, from\) \{\s*\n\s*var before = from === undefined \? card\.scrollTop : from;\s*\n\s*card\.scrollTop = top;/);
  assert.doesNotMatch(GEAR.slice(GEAR.indexOf("function showSection(section) {"), GEAR.indexOf("function closeSettings()")), /sectionAsk\.top|Math\.abs\(card\.scrollTop/, "no scroll-delta reading anywhere in the ask");
  assert.match(GEAR, /var card0 = document\.querySelector\('#rsettings \.rs-card'\);\s*\n\s*if \(card0\) card0\.removeAttribute\('data-section-landed'\);/, "a new ask clears the earlier landing's mark (LOW 3)");
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
  assert.match(GEAR_CSS, /#rsettings \.rs-tab \{ font: inherit; font-size: 12px; padding: 3px 8px; border-radius: 999px;/, "8px of side padding: seven pills on the card's one row (T400 round one)");
});

test("the Tab widgets section's rows come from the strip's own module: built once, painted in place, a sliding switch, house pickers for the options", () => {
  assert.match(GEAR, /var TW = require\('\.\/tab-widgets\.ts'\);/);
  // ONE builder serves both sections since T409 (the status line's widgets): each section hands it its host, registry, prefs
  // reader, saver, switch and option readers, demo and picker prefix
  assert.match(GEAR, /function widgetSection\(cfg\) \{[\s\S]*?function build\(\) \{\s*\n\s*if \(!cfg\.host \|\| cfg\.host\.children\.length\) return;\s*\n\s*cfg\.list\(\)\.forEach\(function \(w\) \{/, "one row per registered widget, built once");
  assert.match(GEAR, /sw\.className = 'rs-switch'; sw\.setAttribute\('role', 'switch'\);/, "the sliding toggle (the user's pick), a switch to the accessibility tree");
  assert.match(GEAR, /prefs\.on\[w\.id\] = !cfg\.on\(prefs, w\); cfg\.save\(prefs\);/, "the switch flips the widget's own flag");
  assert.match(GEAR, /var drop = housePick\(wrap, cfg\.pickPrefix \+ w\.id \+ '-' \+ o\.key, widgetOptRowHTML,/, "an option is the panel's house picker");
  assert.match(GEAR, /host: document\.getElementById\('rs-widgets'\), list: TW\.titleWidgets, prefs: widgetPrefs, pickPrefix: 'wopt-',/, "the tab widgets' section keeps its picker ids; its rows are the widgets that render INTO the title (the rings have rows of their own, 2026-09-14)");
  assert.match(GEAR, /var node = TW\.renderWidgetDemo\(w, prefs\);/, "the live demo is the widget's OWN render over the demo status");
  assert.match(GEAR, /if \(w\.slot === 'before'\) \{ if \(node\) tab\.appendChild\(node\); tab\.appendChild\(label\); \}\s*\n\s*else \{ tab\.appendChild\(label\); if \(node\) tab\.appendChild\(node\); \}/, "the demo places the node in the widget's slot: before the name or after it (no corner slot: pinning is gone, the user 2026-09-12)");
  assert.match(GEAR, /r\.sw\.classList\.toggle\('on', on\); r\.sw\.setAttribute\('aria-checked', on \? 'true' : 'false'\);/);
  assert.match(GEAR, /var node = cfg\.demo\(w, prefs\);\s*\n\s*if \(node\) r\.demo\.replaceChildren\(node\); else r\.demo\.replaceChildren\(\);/, "the demo is re-filled in place: the row's controls are never rebuilt (click-safe)");
  assert.match(GEAR, /save: function \(prefs\) \{ var s = load\(\); s\.tabWidgets = prefs; s\.tabCtx = TW\.tabCtxOfPrefs\(prefs\); save\(s\); paintWidgets\(\); \},/, "the prefs and the tabCtx mirror, through the one save()");
  // the STATUS LINE section (T409): the same builder over the status registry, its prefs read through the two legacy keys as
  // mirrors, its save writing both mirrors back, its pickers under their own prefix; no injected default for any of the three
  assert.match(GEAR, /var SW = require\('\.\/status-widgets\.ts'\);/);
  assert.match(GEAR, /function statusPrefs\(s\) \{ return SW\.statusWidgetPrefs\(s\.statusWidgets\); \}/);
  assert.match(GEAR, /host: document\.getElementById\('rs-swidgets'\), list: SW\.statusWidgets, prefs: statusPrefs, pickPrefix: 'swopt-',/);
  assert.match(GEAR, /save: function \(prefs\) \{ var s = load\(\); s\.statusWidgets = prefs; var m = SW\.legacyOfStatusPrefs\(prefs\); s\.showBranch = m\.showBranch; s\.showSessionBadge = m\.showSessionBadge; save\(s\); paintWidgets\(\); \},/);
  assert.match(GEAR, /demo: function \(w, prefs\) \{ return SW\.renderStatusWidgetDemo\(w, prefs\); \},/, "the status demo is the widget alone, as the line draws it");
  assert.match(GEAR, /function paintWidgets\(\) \{ tabSection\.paint\(\); ringSection\.paint\(\); statusSection\.paint\(\); \}/, "one repaint covers the three sections");
  // THE RINGS (2026-09-14): a third section of the same builder over the registry's rings, under the title rows and their
  // preview, sharing the tab section's store and save (settings.tabWidgets, the tabCtx mirror, the repaint); no divider,
  // no grip and no drag (reorder: false — the order is the precedence, red over yellow over amber, the registry's); the
  // demo is a miniature tab wearing the ring its predicate lights on its demo status, a plain tab once switched off
  const chatPane = panes().chat;
  assert.ok(chatPane.indexOf("id=rs-widgets") < chatPane.indexOf("Rings around the tab. One at a time: the first that applies wins, in this order.") && chatPane.indexOf("Rings around the tab.") < chatPane.indexOf("id=rs-rings") && chatPane.indexOf("id=rs-rings") < chatPane.indexOf("data-section=statusline"), "the hint and the rings' host follow the title rows, before the Status line section");
  assert.match(GEAR, /host: document\.getElementById\('rs-rings'\), list: TW\.ringWidgets, prefs: widgetPrefs, pickPrefix: 'wopt-',/);
  assert.match(GEAR, /order: function \(\) \{ return TW\.ringWidgets\(\)\.map\(function \(w\) \{ return w\.id; \}\); \}, divider: null, group: null, groupLabel: null, reorder: false,/, "the rows' order is the registry's; nothing to drag");
  assert.match(GEAR, /var ringSection = widgetSection\(\{[\s\S]*?save: tabSection\.save,/, "the same store through the tab section's own save (no third writer of settings.tabWidgets)");
  assert.match(GEAR, /return \{ paint: paint, save: cfg\.save \};/);
  assert.match(GEAR, /var cls = TW\.ringDemoClass\(w, prefs\); if \(cls\) tab\.classList\.add\(cls\);/, "the demo wears the ring its predicate lights on its demo status, through the registry");
  assert.match(GEAR, /if \(cfg\.reorder === false\) \{ grip = document\.createElement\('span'\); grip\.className = 'rs-grip-none'; \}/, "no grip: an empty cell keeps the grid's columns");
  assert.match(GEAR, /if \(cfg\.reorder !== false\) wireGrip\(grip, row, w\.id\);/, "…and no drag or arrow keys");
  assert.match(GEAR_CSS, /#rsettings \.rs-grip-none \{ width: 18px; height: 22px; \}/);
  assert.equal((GEAR.match(/s\.statusWidgets = /g) || []).length, 2, "the section's save, and save() normalizing a key the store already carries (round two; still no injection)");
  assert.doesNotMatch(GEAR.slice(GEAR.indexOf("function load() {"), GEAR.indexOf("function save(s) {")), /statusWidgets|showBranch|showSessionBadge/, "load() neither defaults nor touches the status key or its mirrors (the fresh-key rule)");
  // round one, HIGH: NO injected default for tabWidgets. An empty object in load()'s defaults won over a pre-widgets store's
  // tabCtx (the derivation runs only with no object), and a save of any setting wrote it and rewrote the mirror. The prefs
  // derive from tabCtx at read time, as settings.ts does, and only a widget change writes the key.
  assert.doesNotMatch(GEAR, /tabWidgets: \{ on: \{\}, order: \[\], opts: \{\} \}/);
  assert.doesNotMatch(GEAR.slice(GEAR.indexOf("function load() {"), GEAR.indexOf("function save(s) {")), /tabWidgets/, "load() neither defaults nor touches the key");
  assert.match(GEAR, /function widgetPrefs\(s\) \{ return TW\.tabWidgetPrefs\(s\.tabWidgets, s\.tabCtx\); \}/, "read-time derivation from the mirror when the store has no prefs");
  assert.equal((GEAR.match(/s\.tabWidgets = /g) || []).length, 2, "two assignments: the section's save, and save() normalizing a key the store already carries (T409 round two; still no injection)");
  assert.match(GEAR, /d\.className = 'rs-sub'; d\.textContent = w\.description;/, "the description is the row's hover popover, the panel's idiom (round one, LOW 2)");
  assert.match(GEAR_CSS, /#rsettings \.rs-switch\.on::after \{ left: 18px;/, "the knob slides");
  assert.match(GEAR_CSS, /#rsettings \.rs-widgets \{ display: grid; grid-template-columns: 18px 168px 1fr auto auto; column-gap: 10px; \}[^\n]*\n#rsettings \.rs-widget \{ display: grid; grid-template-columns: subgrid; grid-column: 1 \/ -1;/, "one grid across the rows (the grip's column leads since the reorder, T409), each row a subgrid of it (round one, LOW 2)");
  assert.match(GEAR_CSS, /#rsettings \.rs-row:hover \.rs-sub, #rsettings \.rs-widget:hover \.rs-sub \{ display: block; position: absolute;/, "the widget rows share the panel's hover popover rule");
  assert.doesNotMatch(GEAR_CSS, /#rsettings \.rs-widget-name span \{/, "no always-painted description rule");
  // round two, LOW 3: grid-template-columns: subgrid needs Chromium 117 and the stylesheet's oklch(from) 119; the extension's declared
  // VS Code floor is 1.88 (Electron 28, Chromium 120), the first release that ships both
  const pkg = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "vscode-extension", "package.json"), "utf8"));
  assert.equal(pkg.engines.vscode, "^1.88.0", "the declared floor carries subgrid (117) and oklch(from) (119)");
  const lock = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "vscode-extension", "package-lock.json"), "utf8"));
  assert.equal(lock.packages[""].engines.vscode, "^1.88.0", "the lockfile's root agrees, so the first install after the merge rewrites nothing (LOW 2)");
  assert.match(GEAR_CSS, /#rsettings \.rs-widget-demo \.tab-dot, #rsettings \.rs-preview \.tab-dot \{ flex: 0 0 auto; width: 7px; height: 7px; border-radius: 50%; background: var\(--st-working-bg, #e0b020\); \}/, "the demo wears the strip's vocabulary in this sheet's fallbacks (the section's preview tab beside it, T409)");
});

test("the strip's gear glyph opens the Chat tab at its Tab strip section through the shell (or this window's own gear), and the shell relays the tab and the section", () => {
  assert.match(RENDER, /function openSettingsOn\(tab: string, section\?: string\): void \{\s*\n\s*const m: \{ romp: string; tab: string; section\?: string \} = \{ romp: "openSettings", tab \};\s*\n\s*if \(section\) m\.section = section;\s*\n\s*if \(inRompShell\(\)\) \{ try \{ window\.parent\.postMessage\(m, "\*"\); \} catch \{[^}]*\} return; \}\s*\n\s*window\.postMessage\(m, "\*"\);/, "through the shell when in one, else to this window; the section only when given");
  assert.match(RENDER, /const settingsReachable = !!\(\(window as any\)\.__rompShowStrip \|\| inRompShell\(\)\);/, "the settings ask only where a gear can be reached: an honest absence elsewhere (T405: the strip's gear itself is everywhere, its Tab widgets row asks)");
  assert.match(RENDER, /gear\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); openSettingsOn\("chat", "tabstrip"\); \}\);/, "the gear's click asks for Chat at its Tab strip section (T415: no menu on the way; the section holds the lock, Tab widgets follows it)");
  assert.ok(RENDER.indexOf('el("button", "tab-widgets-gear")') > RENDER.indexOf("  end.appendChild(tagBox);") && RENDER.indexOf("bar.appendChild(end);") > RENDER.indexOf('el("button", "tab-widgets-gear")'), "after the tag box in the strip's right-end wrapper, the wrapper the last thing on the bar (T405, T412)");
  assert.match(KERNEL, /window\.__rompOpenSettings=function\(tab,section\)\{var f=document\.getElementById\('f-settings'\);if\(!f\)return;/);
  assert.match(KERNEL, /var msg=\{romp:'openSettings'\};if\(typeof tab==='string'&&tab\)msg\.tab=tab;if\(typeof section==='string'&&section\)msg\.section=section;\s*\n\s*var open=function\(\)\{try\{f\.contentWindow&&f\.contentWindow\.postMessage\(msg,'\*'\);\}catch\(e\)\{\}\};/, "the shell forwards the tab and the section into the settings iframe; a bare ask stays bare");
  assert.match(KERNEL, /if\(m\.romp==='openSettings'\)window\.__rompOpenSettings\(m\.tab,m\.section\);/, "a pane's ask carries its tab and section through");
});

test("the Token usage panel's every close returns to the settings card (the T409 tidy's read): one function, three callers, no bare hide of the layer", () => {
  // a bare hide left the card hidden with the shell's transparent full-window frame still over the page; the served settings lab
  // presses the close, then Escape, then a click that must land
  assert.match(GEAR, /function raHide\(e\) \{ if \(e && e\.stopPropagation\) e\.stopPropagation\(\); raBack\.hidden = true; p\.hidden = false; \}/, "the close's click stops before the card's click-outside listener, which would close the settings outright");
  assert.match(GEAR, /if \(raClose\) raClose\.onclick = raHide;/);
  assert.match(GEAR, /if \(e\.target === raBack\) raHide\(e\); \}\);/, "the backdrop");
  assert.match(GEAR, /if \(e\.key === 'Escape' && raBack && !raBack\.hidden\) raHide\(\); \}\);/, "the panel's Escape");
  assert.equal((GEAR.match(/raBack\.hidden = true/g) || []).length, 1, "the one hide of the layer is raHide's");
  // the read of that fix (three pre-existing lows): the shell's Escape chain asks the page's close answer whenever settings-open
  // stands, so the answer takes the layer down first, one level; every open and close of the settings resets the layer the same way
  assert.match(GEAR, /window\.__rompSettingsClose = function \(\) \{ if \(raBack && !raBack\.hidden\) \{ raHide\(\); return true; \}/, "a press with the keyboard in the shell document reaches the panel");
  assert.match(GEAR, /function openSettings\(tab, section\) \{\s*\n\s*if \(raBack && !raBack\.hidden\) raHide\(\);/, "an open while the panel is up lands on the card, never under the layer");
});
