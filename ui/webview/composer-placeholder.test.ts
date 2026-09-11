// The composer's resting placeholder names the session (the user 2026-09-09): "Message <name>…", the name
// bold in the session's identity colour, per pane — a split column names ITS active session. The split of the
// placeholder is a pure rule (composer-placeholder.ts), executed here; the overlay's wiring in render.ts and its
// styling are pinned at source (no jsdom for the renderer — the repo convention). Synthetic names only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { phParts, RESTING_PREFIX } from "./composer-placeholder";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const FULL = "Message this session…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)";
const SHORT = "Message this session…  (/ for commands)";
const PHONE = "Message this session…";

test("every resting form takes the session's name in place of 'this session', keeping the ellipsis and the hint", () => {
  assert.deepEqual(phParts(FULL, "web"), { kind: "named", before: "Message ", host: null, name: "web", after: "…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)" });
  assert.deepEqual(phParts(SHORT, "api"), { kind: "named", before: "Message ", host: null, name: "api", after: "…  (/ for commands)" });
  assert.deepEqual(phParts(PHONE, "tests"), { kind: "named", before: "Message ", host: null, name: "tests", after: "…" });
  assert.equal(RESTING_PREFIX, "Message this session", "the prefix the resting forms share (composerRestingPlaceholder)");
});

test("the name is trimmed; a remote session's host is split off as the tab label splits it (T328): the host quiet, only the name bold", () => {
  assert.equal((phParts(FULL, "  web ") as any).name, "web");
  const LOCAL = "aaaaaaaa-1111-2222-3333-444444444444", REMOTE = "TESTHOST:" + LOCAL;
  // the split is the sid's own prefix (host-prefix.ts hostPrefix): federation prefixes both the name and the sid
  assert.deepEqual(phParts(PHONE, "TESTHOST:api", REMOTE), { kind: "named", before: "Message ", host: "TESTHOST:", name: "api", after: "…" });
  assert.deepEqual(phParts(FULL, "TESTHOST:api", REMOTE).kind === "named" && (phParts(FULL, "TESTHOST:api", REMOTE) as any).host, "TESTHOST:");
  // a LOCAL session with a colon in its name is not a remote one: the sid carries no prefix, so the name stays whole
  assert.deepEqual(phParts(PHONE, "TESTHOST:api", LOCAL), { kind: "named", before: "Message ", host: null, name: "TESTHOST:api", after: "…" });
  assert.deepEqual(phParts(PHONE, "api", LOCAL), { kind: "named", before: "Message ", host: null, name: "api", after: "…" }, "a local session: unchanged");
  assert.deepEqual(phParts(PHONE, "api", REMOTE), { kind: "named", before: "Message ", host: null, name: "api", after: "…" }, "a remote sid whose name lacks the prefix (an older frame): whole, never a false host");
  assert.deepEqual(phParts(PHONE, "web"), { kind: "named", before: "Message ", host: null, name: "web", after: "…" }, "no sid at all (the pure callers): a local form");
});

test("other placeholders show as they are, and a session with no name yet keeps the plain resting text", () => {
  assert.deepEqual(phParts("Session closed — read-only", "web"), { kind: "plain", text: "Session closed — read-only" });
  assert.deepEqual(phParts("add your own answer…  (⏎ submit)", "web"), { kind: "plain", text: "add your own answer…  (⏎ submit)" });
  assert.deepEqual(phParts(FULL, ""), { kind: "plain", text: FULL });
  assert.deepEqual(phParts(FULL, null), { kind: "plain", text: FULL });
  assert.deepEqual(phParts(FULL, undefined), { kind: "plain", text: FULL });
});

test("render.ts: the overlay mirrors the placeholder, wears the identity colour, hides when the box holds text, and is re-synced at every writer", () => {
  assert.match(RENDER, /import \{ phParts \} from "\.\/composer-placeholder";/);
  const fn = RENDER.slice(RENDER.indexOf("function syncComposerPh(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function syncComposerPh(): void {")));
  assert.ok(fn.length > 0, "syncComposerPh exists");
  assert.match(fn, /const parts = phParts\(ta\.placeholder, live\?\.name \|\| meta\?\.name \|\| "", activeId\);/, "the sid rides along: a remote host's prefix is told from the name by the sid (host-prefix.ts)");
  // T328: the host is the tab label's own quiet span (hostNameNodes: .host-prefix, marked when the host is down), a
  // sibling of the bold name, never inside it
  assert.match(fn, /if \(parts\.host\) \{[\s\S]*?ph\.appendChild\(hostNameNodes\(parts\.host \+ parts\.name, activeId\)\[0\]\);\s*\n\s*\}\s*\n(\s*\/\/[^\n]*\n)*\s*const nm = el\("b", "composer-ph-name"\); nm\.textContent = parts\.name;/);
  assert.doesNotMatch(CSS, /#composer-ph \.host-prefix/, "no dress of the overlay's own for the host: the global .host-prefix rule, the tab label's, is the one");
  // the host's link dropping or returning flips the span's .off mark on the tab (the romp-hosts event, host-offline.test.ts);
  // the overlay's span is repainted on the same event, so the two never disagree until the next keystroke
  assert.match(RENDER, /window\.addEventListener\("romp-hosts", \(\) => \{ renderTabs\(\); syncComposerPh\(\); \}\)/);
  assert.match(fn, /parts\.kind === "named" && !ta\.value/, "the styled form only for the resting placeholder, only while the box is empty");
  assert.match(fn, /ta\.classList\.toggle\("ph-on", show\);/, "the native placeholder goes transparent beneath the overlay");
  assert.match(fn, /const colorBg = \(live\?\.color\?\.bg \|\| meta\?\.color\?\.bg\) \|\| null;/, "the identity colour: the live session's, else the strip's own word on the tab (a skeleton's), never a stale session (skeleton-tabs-wiring)");
  // T335 (the user 2026-09-10): the name is painted with the strip's own perceptual fade of the identity colour (fadedColor,
  // what an at-rest tab wears), so it sits with the faded placeholder text instead of outshining it; T341 (the user
  // 2026-09-11): at HALF strength, since the full fade read too faint; the weight stays; the host span fades in tandem
  // through the strip's .name-faded class carried by the overlay
  assert.match(fn, /if \(colorBg\) nm\.style\.color = fadedColor\(colorBg, PH_NAME_FADE\); else nm\.style\.removeProperty\("color"\);/, "the name wears the session's identity colour at half the at-rest tab label's fade");
  assert.match(fn, /ph\.classList\.add\("name-faded"\);/, "the overlay carries the strip's at-rest class, once, at its making");
  assert.match(RENDER, /^function fadedColor\(hex: string, amount = 1\): string \{/m, "…the one fade rule, the strip's (bright hues fade as far as dim ones), with a strength");
  // the fade reads the page background live, so the overlay re-syncs on the strip's REBUILD (the event that repaints every
  // at-rest label's fade), beside the tip hide: a host-rewritten background reaches the box when it reaches the strip
  const tabs = RENDER.slice(RENDER.indexOf("function renderTabs() {"), RENDER.indexOf("function stripAftermath("));
  assert.match(tabs, /tabStripSig = stripSig;\s*\n(\s*\/\/[^\n]*\n)*\s*hideTabTip\(\);\s*\n(\s*\/\/[^\n]*\n)*\s*syncComposerPh\(\);/, "the strip's rebuild re-syncs the overlay");
  assert.match(fn, /el\("b", "composer-ph-name"\)/, "…bold");
  // re-synced by the value and placeholder writers: growth after a value write, the active-tab show, a rename or
  // a recolour of the active session, the ask-mode placeholder swap, the width refit and every keystroke
  assert.match(RENDER, /function growComposer\(ta: HTMLTextAreaElement\) \{[\s\S]*?syncComposerPh\(\);[^\n]*\n\}/);
  assert.match(RENDER, /document\.body\.style\.removeProperty\("--active-accent"\);\n\s*syncComposerPh\(\);/);
  assert.match(RENDER, /s\.name = m\.name; renderTabs\(\); syncTabKeysWithStrip\(\); if \(m\.id === activeId\) \{ syncComposerPh\(\); updateStatusline\(\); \}/, "a rename of the active session renames the box and the badge (and re-titles its hot key, 2026-09-10)");
  assert.match(RENDER, /if \(meta\) meta\.color = color;\n\s*renderTabs\(\);\n\s*if \(id === activeId\) \{ syncComposerPh\(\); updateStatusline\(\); \}/, "a recolour of the active session recolours them on the click");
  assert.match(RENDER, /ta\.classList\.remove\("answering"\);\n\s*\}\n\s*syncComposerPh\(\);/);
  assert.match(RENDER, /ta\.placeholder = composerRestingPlaceholder\(\);\n\s*syncComposerPh\(\);\n\s*\}\)\.observe\(ta\);/);
  assert.match(RENDER, /clearComposerBox\?\.\(\);[^\n]*\n\s*syncComposerPh\(\);/, "the send's one clear path (main, 2026-09) is followed by the overlay's re-sync");
  // the box's LAYOUT moves the textarea too (the user 2026-09-10: a highlight's quote chip added a row above it and the
  // overlay sat on the chips): #composer's ResizeObserver re-places the overlay, and the three row renderers re-sync
  assert.match(fn, /try \{ new ResizeObserver\(\(\) => syncComposerPh\(\)\)\.observe\(box\); \}/, "observed once, when the overlay is made");
  for (const r of ["renderComposerChips", "renderStagedStrip", "renderComposerFiles"]) {
    // renderStagedStrip carries main's `opts` (a reveal of the last chip, 2026-09) through to its inner half
    assert.match(RENDER, new RegExp("function " + r + "\\(id: string \\| null(?:, opts\\?: \\{ reveal\\?: \"last\" \\})?\\): void \\{ " + r + "Inner\\(id(?:, opts)?\\); syncComposerPh\\(\\); \\}"), r + " re-places the overlay on every exit path");
  }
});

test("styles.css: the overlay sits over the box, dim like a placeholder, the name bold; the native placeholder is transparent while it shows", () => {
  assert.match(CSS, /#composer-ph \{ position: absolute; pointer-events: none; color: var\(--dim\);/);
  assert.match(CSS, /#composer-ph \.composer-ph-name \{ font-weight: 600; \}/);
  assert.match(CSS, /^\.name-faded \.host-prefix, \.name-faded \.host-prefix\.off \{ opacity: var\(--host-fade, 0\.5\); \}/m, "the host prefix fades by the strip's own rule wherever the class is carried (T335), at a strength the carrier may set (T341)");
  assert.doesNotMatch(CSS, /#composer-ph[^\n]*opacity/, "no opacity rule of the overlay's own: the fade is the strip's rule at the overlay's strength");
  assert.match(CSS, /#composer-input\.ph-on::placeholder \{ color: transparent; \}/);
  // the question flow (the user 2026-09-10): the answering tint rule sits at that rule's specificity and came later, so
  // both texts showed — the overlay takes the tint, the native placeholder stays transparent under it
  assert.match(CSS, /#composer-input\.ph-on\.answering::placeholder \{ color: transparent; \}/);
  assert.match(CSS, /#composer-input\.answering ~ #composer-ph \{ color: color-mix\(in srgb, var\(--accent\) 65%, var\(--dim\)\); \}/);
});

test("render.ts: the placeholder and the answering tint have one owner — the renders that reset the box go through setComposerAskMode", () => {
  // a render that wrote the resting form and left the tint is how the overlay came to draw over a tinted native text
  assert.match(RENDER, /if \(ta\) \{ ta\.disabled = false; setComposerAskMode\(\); \}/);
  assert.match(RENDER, /if \(closed\) \{ composer\.placeholder = "Session closed — read-only"; composer\.classList\.remove\("answering"\); syncComposerPh\(\); \}\n\s*else setComposerAskMode\(\);/);
  const writers = RENDER.match(/\.placeholder = composerRestingPlaceholder\(\)/g) || [];
  assert.equal(writers.length, 2, "setComposerAskMode's own write and the phone's resize shortening; nothing else writes the resting form");
});

// T341 (the user 2026-09-11, looking at the shipped T335): the name in the box read TOO faded at the strip's at-rest level;
// it sits halfway between the full identity colour and that fade. One fade rule with a STRENGTH, never a second colour
// formula: the strip at 1 (unchanged), the overlay's name at 0.5, its host prefix at the matching midpoint of the opacities
// (1 at the full colour, 0.5 at the strip's fade: 0.75). The served test proves the midpoint against a real at-rest label.
test("one fade rule, two strengths (T341): the strip at 1, the composer's name at 0.5, its host at the midpoint 0.75", () => {
  const fade = RENDER.slice(RENDER.indexOf("function fadedColor("), RENDER.indexOf("function fadedColor(") + 1200);
  assert.match(fade, /^function fadedColor\(hex: string, amount = 1\): string \{/m, "the strength defaults to the strip's");
  assert.match(fade, /if \(Lc <= Lt\) return hex;/, "the dim-hue early return stays, ahead of the strength");
  assert.match(fade, /const t = Math\.min\(0\.85, \(Lc - Lt\) \/ \(Lc - Lb\)\) \* scale \* amount;/, "the strength scales the one blend");
  assert.equal((fade.match(/amount/g) || []).length, 2, "…and nothing else reads it: no second formula");
  assert.match(RENDER, /^const PH_NAME_FADE = 0\.5;/m, "the overlay's strength: half the way");
  assert.match(RENDER, /label\.style\.color = fadedColor\(full\);/, "the strip's labels at the default strength, unchanged");
  assert.doesNotMatch(RENDER, /fadedColor\(full, /, "…no second strength for the strip");
  assert.equal((RENDER.match(/(?<!function )fadedColor\([^)]*, /g) || []).length, 1, "one call names a strength: the overlay's name");
  // the host prefix: the strip's rule reads a variable whose default is the strip's own 0.5; the overlay alone sets 0.75
  assert.match(CSS, /^\.name-faded \.host-prefix, \.name-faded \.host-prefix\.off \{ opacity: var\(--host-fade, 0\.5\); \}/m);
  assert.match(CSS, /^#composer-ph \{[^\n]*; --host-fade: 0\.75; \}/m, "the overlay's own strength, on the overlay's rule");
  assert.equal((CSS.match(/--host-fade:/g) || []).length, 1, "one setter: the tab label keeps the default");
  assert.equal((CSS.match(/var\(--host-fade/g) || []).length, 1, "one reader: the strip's rule");
  // the two midpoints agree, read off the source: the host's opacity sits where the name's strength puts it between the
  // full colour (1) and the strip's host fade, 1 - strength × (1 - the strip's fade) = 1 - 0.5 × (1 - 0.5) = 0.75; moving
  // one constant without the other fails here
  const nameFade = Number(/^const PH_NAME_FADE = ([\d.]+);/m.exec(RENDER)![1]);
  const hostDefault = Number(/\.name-faded \.host-prefix\.off \{ opacity: var\(--host-fade, ([\d.]+)\); \}/.exec(CSS)![1]);
  const hostFade = Number(/^#composer-ph \{[^\n]*; --host-fade: ([\d.]+); \}/m.exec(CSS)![1]);
  assert.equal(hostFade, 1 - nameFade * (1 - hostDefault), "the two midpoints agree: the host's opacity sits where the name's strength puts it");
});

// T345 (the user 2026-09-11): the thin border around the FOCUSED message box is the colour of the session you are
// messaging, at the same 1px geometry the accent ring had. The colour is the placeholder's own source (the live session's
// colour, else the strip's word on the tab), published once per sync as --composer-identity on #composer; the focus rule
// reads it with the accent as the fallback, for a session with no colour or one too close to the page's luminance to read
// as a ring (identityReadable: the strip's luminance margin, applied on both sides of the page since a light page sits
// above every colour). The live picker's free-text tint keeps winning by construction: same specificity, later.
test("the focused box's border is the session's identity colour, the accent its fallback; the answering tint still wins (T345)", () => {
  const fn = RENDER.slice(RENDER.indexOf("function syncComposerPh("), RENDER.indexOf("function syncComposerPh(") + 4000);
  assert.match(fn, /const ring = colorBg && identityReadable\(colorBg\) \? colorBg : "";/, "the placeholder's colour source, gated by the readability test");
  assert.match(fn, /if \(box\.style\.getPropertyValue\("--composer-identity"\) !== ring\) \{\s*\n\s*if \(ring\) box\.style\.setProperty\("--composer-identity", ring\); else box\.style\.removeProperty\("--composer-identity"\);/, "published on the box, written only on a change, cleared when there is nothing to publish");
  assert.ok(fn.indexOf("const ring = ") < fn.indexOf('const show = parts.kind === "named"'), "…before the overlay's own early returns, so a box with text or a picker up still wears the ring");
  // the readability test shares the strip's numbers: the one luminance function and the one margin
  assert.match(RENDER, /^const LUM_MARGIN = 38;/m);
  assert.match(RENDER, /^function identityReadable\(hex: string\): boolean \{[\s\S]{0,400}?return Math\.abs\(lum\(c\[0\], c\[1\], c\[2\]\) - lum\(br, bgc, bb\)\) > LUM_MARGIN;/m, "both sides of the page's luminance");
  assert.match(RENDER, /const Lc = lum\(r, g, b\), Lb = lum\(br, bgc, bb\), Lt = Lb \+ LUM_MARGIN;/, "the fade's target is the same margin");
  // the page's colour under the picker's LIFT: the body is transparent then and its ::before backing carries var(--bg); read
  // as black, a light page's yellow session passed the test and wore an invisible ring after the picker closed (the review)
  const bg = RENDER.slice(RENDER.indexOf("function bgRgb("), RENDER.indexOf("function bgRgb(") + 900);
  assert.match(bg, /const transparent = own === "transparent" \|\| \/\^rgba\\\(\[\^\)\]\*,\\s\*0\\\)\$\/\.test\(own\);/, "a transparent body is recognised");
  assert.match(bg, /parse\(transparent \? getComputedStyle\(document\.body, "::before"\)\.backgroundColor : own\)/, "…and read as its backing");
  assert.match(CSS, /^body\.picker-lifted::before \{ content: ""; position: absolute; inset: 0; background: var\(--bg\); z-index: -1; \}/m, "the backing the reader falls to");
  assert.match(RENDER, /signalPickerOverlay\(false\);[^\n]*\n\s*syncComposerPh\(\);/, "the picker's close re-verdicts the ring against the page the box is back on");
  assert.equal((RENDER.match(/0\.2126 \* /g) || []).length, 1, "one luminance formula in the file");
  // the focus rule: the variable with the accent as its fallback, the geometry untouched (a fill of the existing 1px border)
  assert.match(CSS, /^#composer-input:focus \{ border-color: var\(--composer-identity, var\(--accent\)\); \}$/m);
  assert.doesNotMatch(CSS, /#composer-input:focus \{[^}]*(box-shadow|outline|border-width)/, "no glow, no layout shift: the ring is the border's colour alone");
  // the live picker's free-text tint keeps winning while it applies: the same specificity (one id, one class or pseudo-class), later in the sheet
  assert.match(CSS, /^#composer-input\.answering \{ border-color: var\(--accent\);/m);
  assert.ok(CSS.indexOf("#composer-input:focus { border-color:") < CSS.indexOf("#composer-input.answering { border-color: var(--accent);"), "the answering rule follows the focus rule");
  assert.doesNotMatch(CSS, /#composer-input:focus:not\(\.answering\)|#composer:focus-within/, "no second ring: the one border, one rule for its colour");
});
