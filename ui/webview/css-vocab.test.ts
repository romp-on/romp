// The UI's color/metric vocabulary — pins the dedupes of 2026-08-26 so near-twin values cannot
// creep back in. Each block names ONE vocabulary decision and the drift it retired; a failure here
// means a new rule re-introduced a value the vocabulary already names (use the token / the named
// hex instead). CLAUDE.md Design is the spec these instances serve.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const CHAT = read("styles.css");
const FEED = read("feed.css");
const GEAR = read("gear.css");
const STRIP = read("strip.css");
const SHORTCUTS = read("shortcuts-modal.ts");

test("--warn is a real token, one heads-up amber: defined in BOTH self-sufficient :roots", () => {
  // it was a phantom for months — referenced with fallbacks, defined nowhere — while two more
  // ambers one hex digit apart (#e0a020, #e0b341) grew nine lines from each other in feed.css
  assert.match(CHAT, /--warn: #d7a23a;/);
  assert.match(FEED, /--warn: #d7a23a;/);
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.doesNotMatch(css, /#e0a020|#e0b341|#d29922/i, name + " uses var(--warn), not a near-twin amber");
  }
  assert.doesNotMatch(SHORTCUTS, /#d29922/, "the shortcuts dialog's conflict amber is the warn amber");
});

test("one elevated small-button gray (#2a2a2a) and one dropdown shadow alpha (#000000aa)", () => {
  // #2a2a2b differed from its sibling #2a2a2a by one digit across gear/strip; the two settings
  // dropdowns (colormap / session palette, 11 lines apart) wore #000000aa vs #00000088
  for (const [name, css] of [["gear.css", GEAR], ["strip.css", STRIP]] as const) {
    assert.doesNotMatch(css, /#2a2a2b/, name);
    assert.doesNotMatch(css, /#00000088/, name);
  }
});

test("gear.css accent chrome references var(--accent) — never a re-hardcoded bare hex (CLAUDE.md)", () => {
  assert.doesNotMatch(GEAR, /outline: 2px solid #9cd2ff/);
  assert.match(GEAR, /outline: 2px solid var\(--accent, #9cd2ff\)/);
});

test("menus wear ONE vocabulary (CLAUDE.md): --radius-menu 6px + --shadow-menu on every dropdown", () => {
  // .meta-menu and .tab-tip had drifted onto 0 4px 16px/0.45; .slash-pop onto 8px + 0 6px 22px;
  // .feed-sessmenu onto 0 6px 24px. All resolve through the tokens now. .tab-tip keeps its mono
  // (a statusline tooltip, not a dropdown); .meta-menu joins the menus' sans per the spec.
  assert.match(CHAT, /--radius-menu: 6px;\n  --shadow-menu: 0 4px 12px rgba\(0, 0, 0, 0\.35\);/);
  assert.match(FEED, /--radius-menu: 6px;/);
  assert.match(FEED, /--shadow-menu: 0 4px 12px rgba\(0, 0, 0, 0\.35\);/);
  for (const sel of [".ctx-menu", ".meta-menu", ".tab-tip", ".slash-pop"]) {
    const at = CHAT.indexOf(sel + " {");
    const rule = CHAT.slice(at, CHAT.indexOf("}", at));
    assert.ok(rule.includes("var(--radius-menu)"), sel + " radius through the token");
    assert.ok(rule.includes("var(--shadow-menu)"), sel + " shadow through the token");
  }
  for (const sel of [".ctx-menu", ".feed-sessmenu"]) {
    const at = FEED.indexOf(sel + " {");
    const rule = FEED.slice(at, FEED.indexOf("}", at));
    assert.ok(rule.includes("var(--radius-menu)"), sel + " radius through the token (feed)");
    assert.ok(rule.includes("var(--shadow-menu)"), sel + " shadow through the token (feed)");
  }
  // the retired drift shadows appear nowhere in the sheets
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.doesNotMatch(css, /0 4px 16px rgba\(0, 0, 0, 0\.45\)|0 6px 2[24]px rgba\(0, 0, 0, 0\.45\)/, name);
  }
  assert.match(CHAT, /\.meta-menu \{[^}]*font-family: var\(--sans\)/s, "the meta menus read in the menus' sans");
});

test("transient notices wear ONE treatment: --surface-raised + --radius-toast + --shadow-toast", () => {
  // five toast variants had drifted (6/7/8/10px radii, four backgrounds, shadows from none to
  // 0 10px 30px); the semantic BORDER colors stay per notice (locate amber, warn red). The
  // kernel's #rstale/#rupd/#rdrift banners carry the same values as literals (no :root there).
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.match(css, /--surface-raised: #252526;/, name);
    assert.match(css, /--radius-toast: 8px;/, name);
    assert.match(css, /--shadow-toast: 0 8px 28px rgba\(0, 0, 0, 0\.45\);/, name);
  }
  for (const sel of [".locate-toast", ".warn-toast", "#seek-note"]) {
    const at = CHAT.indexOf(sel + " {");
    const rule = CHAT.slice(at, CHAT.indexOf("}", at));
    assert.ok(rule.includes("var(--radius-toast)"), sel + " radius through the token");
    assert.ok(rule.includes("var(--shadow-toast)"), sel + " shadow through the token");
  }
  {
    const at = FEED.indexOf(".feed-toast {");
    const rule = FEED.slice(at, FEED.indexOf("}", at));
    assert.ok(rule.includes("var(--radius-toast)"), ".feed-toast radius through the token");
    assert.ok(rule.includes("var(--shadow-toast)"), ".feed-toast shadow through the token");
    assert.ok(rule.includes("var(--vscode-menu-background, var(--surface-raised))"), ".feed-toast surface");
  }
  // the retired one-off toast surfaces are gone from the sheets
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.doesNotMatch(css, /rgba\(28, 28, 28, 0\.9[46]\)|#26262a/, name);
  }
});

test("centered modals wear ONE card (CLAUDE.md): --radius-modal 10px + --shadow-modal + --overlay-dim 0.55", () => {
  // the picker card wore 8px + its own shadow, the feed's card modal 12px, the confirm and resume
  // dialogs their own shadows and a 0.5 dim, the file viewer 8px + 0 12px 44px, the analytics
  // modal a 0.73 dim + 40px blur. All resolve through the vocabulary now (gear.css keeps unified
  // literals — it loads standalone). The lightbox's 0.72 dim is a LIGHTBOX, deliberately darker.
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.match(css, /--radius-modal: 10px;/, name);
    assert.match(css, /--shadow-modal: 0 12px 36px #000000aa;/, name);
    assert.match(css, /--overlay-dim: rgba\(0, 0, 0, 0\.55\);/, name);
    assert.doesNotMatch(css, /0 10px 36px|0 10px 40px|0 12px 44px/, name + " retired modal shadows gone");
  }
  for (const [sel, css, name] of [
    [".picker-box", CHAT, "styles.css"], [".fileview", CHAT, "styles.css"],
    [".fileview", FEED, "feed.css"], [".fconfirm-box", FEED, "feed.css"],
    [".feed-modal-inner", FEED, "feed.css"], [".pickdlg-box", FEED, "feed.css"],
  ] as const) {
    // regex, not indexOf: a selector may have a second (e.g. mobile) rule that skips the box chrome
    const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(css, new RegExp(esc + " \\{[^}]*var\\(--radius-modal\\)"), sel + " radius through the token (" + name + ")");
    assert.match(css, new RegExp(esc + " \\{[^}]*var\\(--shadow-modal\\)"), sel + " shadow through the token (" + name + ")");
  }
  assert.match(GEAR, /#ranalytics-back \{[^}]*background: rgba\(0, 0, 0, 0\.55\)/, "analytics dim joins 0.55");
  assert.match(GEAR, /#ranalytics \{[^}]*box-shadow: 0 12px 36px #000000aa/s, "analytics shadow joins the card");
});

test("same-row badges wear the SAME metric set; micro-labels wear the section-header spec", () => {
  // an ask card's status badges sit in ONE row and their comments claim 'same information type' —
  // yet .fask-blocked alone wore 0.72em/700/6px/1px 8px against its siblings' 0.7em/800/5px/1px 6px
  for (const sel of [".fask-blocked", ".fask-apierror", ".fask-retrying", ".fask-jauth"]) {
    const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    assert.match(FEED, new RegExp(esc + " \\{[^}]*font-size: 0\\.7em; font-weight: 800;"), sel + " size/weight");
    assert.match(FEED, new RegExp(esc + " \\{[^}]*border-radius: 5px; padding: 1px 6px;"), sel + " box");
  }
  // .fcol-chip's comment says it reproduces the chat .chip — now its padding does too
  assert.match(FEED, /\.fcol-chip \{[^}]*padding: 3px 10px;/s);
  assert.match(CHAT, /\.chip \{[^}]*padding: 3px 10px;/s);
  // uppercase section micro-labels: 10.5px/700/.08em (the .rs-sec spec, documented at the kernel's
  // .rnet-khead) — .sn-khead is the same feature in the VS Code strip and had drifted off it
  assert.match(STRIP, /\.sn-khead \{[^}]*font-weight: 700; letter-spacing: 0\.08em;/);
  assert.match(GEAR, /\.rs-sec \{[^}]*font-weight: 700; letter-spacing: 0\.08em;/s);
  // no px letter-spacing (an em value scales with its label; 0.4px was the one outlier)
  const SESSIONS = read("fleet-pane.css");   // the Sessions pane sheet (file keeps its legacy name)
  assert.doesNotMatch(SESSIONS, /letter-spacing:0\.4px/);
  // true pills resolve through --radius-pill (the .fitem:hover inset-999px fill trick is not a radius)
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.match(css, /--radius-pill: 999px;/, name);
    assert.doesNotMatch(css, /border-radius: 999px/, name + " pills use the token");
  }
});

test("the transcript's rhythm: 7px turns, 11px user turns, ONE bubble padding, dots on the first line", () => {
  // slightly-more-compact pass (the user 2026-08-26): turn padding 9→7 and user turns 14→11, with
  // the rail dots/time markers moved the same distance so they keep sitting on the first line;
  // the outgoing-bubble family (user / romp / follow-up / queued) had three paddings for one
  // shape (9px 14px / 7px 14px / 7px 13px) and now wears one.
  assert.match(CHAT, /\.turn \{ position: relative; padding-left: 24px; padding-top: 7px; padding-bottom: 7px;/);
  assert.match(CHAT, /\.dot \{ position: absolute; left: 6px; top: 13px;/);
  assert.match(CHAT, /\.time-marker \{\n  position: absolute; top: 13px;/);
  assert.match(CHAT, /\.turn-user \{ padding-top: 11px; padding-bottom: 11px; \}/);
  assert.match(CHAT, /\.turn-user \.dot, \.turn-user \.time-marker \{ top: 17px; \}/);
  assert.equal((CHAT.match(/border-radius: 14px; padding: 7px 12px;/g) || []).length, 4,
    "user, follow-up, romp, queued bubbles share the one padding");
});

test("the sessions-pane header is ONE flex row: search shrinks, the tag button never drops alone", () => {
  const SESSIONS = read("fleet-pane.css");   // the Sessions pane sheet (file keeps its legacy name)
  assert.match(SESSIONS, /#fleet-search-bar\{flex:0 0 auto;display:flex;align-items:center;flex-wrap:wrap;gap:6px;/);
  assert.match(SESSIONS, /#fleet-search-wrap\{position:relative;flex:1 1 140px;min-width:140px\}/);
});

test("ONE accent wash: every selected/hovered accent chrome resolves through --accent-wash at 0.12", () => {
  // 0.10 and 0.14 washes had drifted in beside the dominant 0.12 (three alphas for one meaning);
  // the token is declared in both self-sufficient :roots, and no bare wash literal remains in the
  // sheets. The CodeMirror .cm-selectionMatch (editor-chunk.ts) keeps 0.14 — a text-match marker,
  // not chrome — and shell-page inline CSS keeps unified 0.12 literals (no :root to share).
  assert.match(CHAT, /--accent-wash: rgba\(156, 210, 255, 0\.12\);/);
  assert.match(FEED, /--accent-wash: rgba\(156, 210, 255, 0\.12\);/);
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    const bare = css.replace(/--accent-wash: rgba\(156, 210, 255, 0\.12\);/g, "");
    assert.doesNotMatch(bare, /rgba\(156, ?210, ?255, ?0?\.1[024]\)/, name + " has no bare wash literal");
  }
  // the selected fileview toggle wears the app's ONE selected language in BOTH sheets: wash at
  // rest, reverse-highlight on hover (styles.css's copy had drifted to a wash hover, 2026-08-25)
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.match(css, /\.fileview-btn\.on:hover \{ background: var\(--accent\); color: var\(--accent-fg\); border-color: var\(--accent\); \}/,
      name + " reverse-highlights the selected viewer toggle");
  }
});
