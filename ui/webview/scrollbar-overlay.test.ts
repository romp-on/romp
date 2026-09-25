// Scrollbars follow the platform (the user 2026-09-25, on macOS in the light theme, who could not see the chat's scrollbar
// even while scrolling and wants the platform's show-while-scrolling bars): any ::-webkit-scrollbar styling makes WebKit and
// Blink drop the platform's overlay scrollbars for classic, always-on ones, so every custom scrollbar rule is scoped under
// html:not(.overlay-scrollbars), and overlay-scrollbars.ts sets that class where the platform overlays. Where it does not,
// the styled bars stay, in tokens both themes define (theme-parity.test.ts holds their contrast). The native bars follow the
// theme through color-scheme on the BODY, never the root: a pane document whose root scheme differs from its iframe
// element's gets an opaque backdrop, and the lifted panels need that document transparent (ui/CLAUDE.md).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { OVERLAY_SCROLLBARS, applyOverlayScrollbars, watchOverlayScrollbars } from "./overlay-scrollbars";

const WEBVIEW = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(WEBVIEW, f), "utf8");
const SHEETS = fs.readdirSync(WEBVIEW).filter((f) => f.endsWith(".css")).sort();

/** Every innermost rule of a sheet (an @media block's rules included), comments stripped. */
function rules(css: string): Array<{ sel: string; body: string }> {
  const out: Array<{ sel: string; body: string }> = [];
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)) out.push({ sel: m[1].trim(), body: m[2].trim() });
  return out;
}
/** A selector list split on its top-level commas (a :not(a, b) stays whole). */
function selectors(list: string): string[] {
  const out: string[] = []; let depth = 0, cur = "";
  for (const ch of list) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (ch === "," && depth === 0) { out.push(cur.trim()); cur = ""; } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
const isScrollbarRule = (r: { sel: string; body: string }) => /::-webkit-scrollbar/.test(r.sel) || /(^|;)\s*scrollbar-(width|color)\s*:/.test(r.body);
/** A rule that HIDES a scrollbar outright is not a style the platform's own bars should replace: it holds on every platform. */
const isHide = (body: string) => body.split(";").map((d) => d.trim()).filter(Boolean)
  .every((d) => /^(scrollbar-width:\s*none|display:\s*none|width:\s*0(px)?|height:\s*0(px)?)$/.test(d));

test("every custom scrollbar rule in the sheets is scoped off under the overlay class", () => {
  let found = 0;
  for (const sheet of SHEETS) {
    for (const r of rules(read(sheet))) {
      if (!isScrollbarRule(r) || isHide(r.body)) continue;
      for (const s of selectors(r.sel)) {
        found++;
        assert.ok(s.startsWith("html:not(." + OVERLAY_SCROLLBARS + ")"), `${sheet}: "${s}" styles a scrollbar outside html:not(.${OVERLAY_SCROLLBARS})`);
      }
    }
  }
  // the chat's thumb, track and hover, the composer's thin bar, and the page-wide width and thumb (the root's own and every
  // element's): a parser that suddenly sees fewer is broken, not a tidier sheet
  assert.ok(found >= 8, "found only " + found + " scoped scrollbar selectors: parser broken?");
});

test("no scrollbar rule paints a raw colour: every colour is a theme token (a white alpha is invisible on the light page)", () => {
  for (const sheet of SHEETS) {
    for (const r of rules(read(sheet))) {
      if (!isScrollbarRule(r)) continue;
      for (const d of r.body.split(";").map((x) => x.trim()).filter(Boolean)) {
        const [prop, ...rest] = d.split(":"); const v = rest.join(":").trim();
        if (!/^(background(-color)?|scrollbar-color)$/.test(prop.trim())) continue;
        const bare = v.replace(/var\(--[a-zA-Z-]+(,\s*var\(--[a-zA-Z-]+\))?\)/g, "").replace(/\btransparent\b/g, "").trim();
        assert.equal(bare, "", `${sheet}: "${r.sel}" paints ${prop.trim()}: ${v}, a raw colour outside the tokens`);
      }
    }
  }
  const css = read("styles.css");
  assert.match(css, /html:not\(\.overlay-scrollbars\) #content::-webkit-scrollbar-track \{ background: var\(--scroll-track\); \}/);
  assert.match(css, /html:not\(\.overlay-scrollbars\) #content::-webkit-scrollbar-thumb \{ background: var\(--scroll-thumb\); border-radius: 5px; min-height: 36px; \}/,
    "the chat thumb keeps its radius and its 36px floor");
  assert.match(css, /html:not\(\.overlay-scrollbars\) #content::-webkit-scrollbar-thumb:hover \{ background: var\(--scroll-thumb-hover\); \}/);
  assert.match(css, /html:not\(\.overlay-scrollbars\) #composer-input \{ scrollbar-width: thin; scrollbar-color: var\(--scroll-thumb-thin\) transparent; \}/);
  // the page-wide thumb keeps the editor's slider colour where a host defines one (VS Code; the kernel's served sheet)
  assert.match(css, /::-webkit-scrollbar-thumb \{ background: var\(--vscode-scrollbarSlider-background, var\(--scroll-thumb\)\); border-radius: 5px; \}/);
});

test("the root's own scrollbar is scoped too: the page-wide rule names html's pseudo AND every descendant's", () => {
  const css = read("styles.css");
  assert.match(css, /html:not\(\.overlay-scrollbars\)::-webkit-scrollbar, html:not\(\.overlay-scrollbars\) ::-webkit-scrollbar \{ width: 10px; height: 10px; \}/);
  assert.match(css, /html:not\(\.overlay-scrollbars\)::-webkit-scrollbar-thumb, html:not\(\.overlay-scrollbars\) ::-webkit-scrollbar-thumb \{/);
});

test("the kernel's inline sheets style no scrollbar: the one rule there HIDES the rail's, which holds on every platform", () => {
  const K = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const webkit = [...K.matchAll(/::-webkit-scrollbar[a-z-]*\{([^}]*)\}/g)].map((m) => m[1]);
  assert.ok(webkit.length >= 1, "the rail's hide is where this looks");
  for (const b of webkit) assert.ok(isHide(b), "a styled scrollbar in the kernel's inline css: {" + b + "}");
  for (const m of K.matchAll(/scrollbar-width:([a-z]+)/g)) assert.equal(m[1], "none", "a scrollbar-width other than none in the kernel's inline css");
  assert.doesNotMatch(K, /scrollbar-color:/, "a scrollbar colour in the kernel's inline css");
});

test("the native scrollbars follow the theme: color-scheme on the body in each themed sheet, never on the root", () => {
  for (const sheet of ["styles.css", "feed.css"]) {
    const css = read(sheet);
    assert.match(css, /\n\s*--color-scheme: dark;/, sheet + ": the dark scheme token");
    assert.match(css.slice(css.indexOf("body.theme-light {")), /\n\s*--color-scheme: light;/, sheet + ": the light block re-points it");
    const body = rules(css).filter((r) => selectors(r.sel).includes("body") && /color-scheme:/.test(r.body));
    assert.equal(body.length, 1, sheet + ": one body rule carries the scheme");
    assert.match(body[0].body, /(^|;)\s*color-scheme: var\(--color-scheme\)/, sheet + ": the body reads the token");
  }
  const tl = read("timeline-pane.css");
  assert.match(tl, /\nbody\{color-scheme:dark;\}/, "timeline-pane.css: the dark scheme on the body");
  assert.match(tl.slice(tl.indexOf("body.theme-light {")), /\n\s*color-scheme: light;/, "timeline-pane.css: the light block's scheme");
  // the root: an iframe whose document's ROOT scheme differs from the embedding element's (the shell's, normal) paints an
  // opaque backdrop behind the page, which blacks out a lifted panel's transparent dim (measured 2026-09-25 in Chromium:
  // a dark root showed #121212 where a dark body let the parent through)
  for (const sheet of SHEETS) {
    for (const r of rules(read(sheet))) {
      if (!/(^|;)\s*color-scheme:/.test(r.body)) continue;
      for (const s of selectors(r.sel)) {
        assert.doesNotMatch(s, /^(html|:root)$|^html[.:[]|^:root[.:[]/, `${sheet}: "${s}" puts color-scheme on the root`);
      }
    }
  }
});

// ── the probe's logic, on a stubbed document and window ────────────────────────────────────────────────────────────
function stubPage(visible = true) {
  const cls = new Set<string>();
  const doc = Object.assign(new EventTarget(), {
    visibilityState: visible ? "visible" : "hidden",
    documentElement: { classList: {
      toggle: (c: string, on?: boolean) => { const want = on === undefined ? !cls.has(c) : on; if (want) cls.add(c); else cls.delete(c); return want; },
      contains: (c: string) => cls.has(c),
    } },
  });
  const win = new EventTarget();
  return { doc: doc as unknown as Document & { visibilityState: string }, win: win as unknown as Window, on: () => cls.has(OVERLAY_SCROLLBARS) };
}

test("one measurement sets or clears the class: 0 is overlay, a width is classic, unknown leaves the class alone", () => {
  const p = stubPage();
  assert.equal(applyOverlayScrollbars(p.doc, 0), true); assert.ok(p.on(), "a 0px bar is an overlay bar");
  assert.equal(applyOverlayScrollbars(p.doc, null), null); assert.ok(p.on(), "a probe with no box (a hidden pane) says nothing");
  assert.equal(applyOverlayScrollbars(p.doc, 15), false); assert.ok(!p.on(), "a bar that takes width is classic");
  assert.equal(applyOverlayScrollbars(p.doc, null), null); assert.ok(!p.on());
});

test("the watcher measures at once and again on the events that can follow a preference change; nothing else", () => {
  const p = stubPage();
  let width: number | null = 0, calls = 0;
  watchOverlayScrollbars(p.doc, p.win, () => { calls++; return width; });
  assert.equal(calls, 1, "measured at install"); assert.ok(p.on());
  // the user switches macOS to always-show scroll bars, then comes back to the window
  width = 15;
  p.win.dispatchEvent(new Event("focus"));
  assert.equal(calls, 2); assert.ok(!p.on(), "focus re-measured: classic now");
  // and back to show-when-scrolling, seen when the page comes back into view
  width = 0;
  p.doc.visibilityState = "hidden";
  p.doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(calls, 2, "going hidden is not a moment to measure");
  p.doc.visibilityState = "visible";
  p.doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal(calls, 3); assert.ok(p.on(), "visible again re-measured: overlay now");
  // a resize is no reason to measure once the verdict is known (a window drag fires it per frame)
  p.win.dispatchEvent(new Event("resize"));
  assert.equal(calls, 3, "no measurement on resize while the verdict stands");
  // a second install on the same document adds no listeners
  watchOverlayScrollbars(p.doc, p.win, () => { calls++; return width; });
  const after = calls;
  p.win.dispatchEvent(new Event("focus"));
  assert.equal(calls, after + 1, "one listener per event, however often the watcher is installed");
});

test("a pane hidden at boot measures when it is first laid out (its window's resize), then stops listening to resizes", () => {
  const p = stubPage();
  let width: number | null = null, calls = 0;
  watchOverlayScrollbars(p.doc, p.win, () => { calls++; return width; });
  assert.equal(calls, 1); assert.ok(!p.on(), "unknown at boot: the styled bars stand, as before this change");
  p.win.dispatchEvent(new Event("resize"));
  assert.equal(calls, 2, "still unknown: a resize measures");
  width = 0;
  p.win.dispatchEvent(new Event("resize"));
  assert.equal(calls, 3); assert.ok(p.on(), "the shown pane measured overlay");
  p.win.dispatchEvent(new Event("resize"));
  assert.equal(calls, 3, "known now: resizes cost nothing");
});

test("every page that loads styles.css installs the watcher at boot", () => {
  for (const f of ["render.ts", "fleet.ts", "files.ts", "artifacts.ts"]) {
    assert.match(read(f), /watchOverlayScrollbars\(document, window\);/, f + " installs the watcher");
  }
});
