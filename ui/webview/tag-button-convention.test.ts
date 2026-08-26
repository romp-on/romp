// THE TAG BUTTON CONVENTION (the user 2026-08-25): at rest (All) the tag icon is GRAY and stands
// alone; narrowed, it wears the ACCENT and the chips of everything selected — each tag in its
// color, the no-tags bucket as its own chip — identical across the timeline, chat, outline, and
// feed mounts. Equality is COMPUTED, not class-shared (the 678 lesson): the executed model is one
// function; the color constants are asserted equal across every home that states them.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { lensChips } from "./tag-lens";
import { TAG_BTN_GRAY, TAG_BTN_ACCENT, TAG_BTN_BORDER, TAG_BTN_WASH } from "./tag-menu";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const FLEET = ui("webview", "fleet.ts");
const FEED = ui("webview", "feed.ts");
const FEEDCSS = ui("webview", "feed.css");
const TL = ui("romp-timeline-view.js");
const TAGMENU = ui("webview", "tag-menu.ts");

const UNIONS = [
  { name: "infra", color: "#DD42FF", members: [] },
  { name: "workers", color: "#4EC9B0", members: [] },
];

test("executed: lensChips — All bare; no-tags LEFTMOST; tags follow in the unions' (user) order", () => {
  assert.deepEqual(lensChips({ all: true }, UNIONS as never), [], "All → the button stands alone");
  assert.deepEqual(lensChips({ tags: ["infra"] }, UNIONS as never),
    [{ label: "infra", color: "#DD42FF", pick: { tag: "infra" } }]);
  assert.deepEqual(lensChips({ none: true, tags: ["workers"] }, UNIONS as never),
    [{ label: "no tags", color: null, pick: "none" },
     { label: "workers", color: "#4EC9B0", pick: { tag: "workers" } }],
    "no-tags sits LEFTMOST in every selection render (the user 2026-08-25, superseding the none-last form)");
  // the tags render in the UNIONS' order (the user's dragged order), not the selection's insertion order
  assert.deepEqual(lensChips({ tags: ["workers", "infra"] }, UNIONS as never).map((c) => c.label),
    ["infra", "workers"], "selection insertion order yields to the user's tag order");
  // a stale selected name (no union) still shows, dressless, after the ordered ones
  assert.deepEqual(lensChips({ tags: ["ghost", "infra"] }, UNIONS as never).map((c) => c.label),
    ["infra", "ghost"]);
});

test("cross-mount computed equality: one gray, one accent, everywhere", () => {
  assert.equal(TAG_BTN_GRAY, "#9aa0a6");
  assert.equal(TAG_BTN_ACCENT, "#9cd2ff");
  // the timeline inlines the same values (it loads no modules — Obsidian host). Round three: the
  // corner buttons' rest gray is the FEED INSTANCE's own computed chain (var(--dim) resolves this),
  // narrowed is the .on accent — stated in the injected corner CSS, currentColor carries the glyph
  assert.match(TL, /const MODEL_FG = '#9aa0a6';/, "the timeline's text gray (chip fallback) is the convention gray");
  assert.match(TL, /color:var\(--vscode-descriptionForeground,#9a9a9a\)/, "rest = the feed's exact color chain");
  assert.match(TL, /\.romp-tl-cbtn\.on\{color:var\(--accent,#9cd2ff\);/, "narrowed = the .on accent");
  // the feed's class mode resolves to the same accent (its own :root states the literal)
  assert.match(FEEDCSS, /--accent: #9cd2ff;/, "feed.css's accent equals the convention accent");
  assert.match(FEED, /"class"\);/, "the feed mounts in class mode — its .on carries that accent");
});

test("every JS mount renders through the ONE convention function", () => {
  for (const [name, src] of [["render", RENDER], ["fleet", FLEET], ["feed", FEED]] as const)
    assert.match(src, /syncTagFilter\(/, name + " mounts the shared renderer");
  assert.match(RENDER, /const tagBox = el\("span", "tab-tagbox"\);/,
    "the chat button + chips ride the .tab-tagbox, which reserves the + tab's box and centers them (the user 2026-08-25 — a wrapped controls line sat flush under the row above)");
});

test("THE BUTTON OUTLINE (the user 2026-08-25, round two): every mount wears the feed word-button's box", () => {
  // the box, by value: 1px hairline in the feed's --card-border, 6px radius, the footer's 1px 9px
  // padding; narrowed = accent border + the .on wash. COMPUTED equality (the 678 lesson): the feed
  // states these through classes, the other mounts through the shared literals — assert the values
  // equal, never share the class.
  const flat = FEEDCSS.replace(/\s+/g, "");
  assert.equal(TAG_BTN_BORDER, "rgba(255,255,255,0.10)");
  assert.equal(TAG_BTN_WASH, "rgba(156,210,255,0.12)");
  assert.ok(flat.includes("--card-border:rgba(255,255,255,0.10)"), "the feed's hairline is the shared border literal");
  // the feed's .on rules resolve through var(--accent-wash) since 2026-08-26 — its :root literal
  // is what must equal the shared constant
  assert.ok(flat.includes("--accent-wash:rgba(156,210,255,0.12)"), "the feed's wash token is the shared wash literal");
  assert.ok(flat.includes("background:var(--accent-wash)"), "the feed .on's wash resolves through the token");
  assert.ok(flat.includes("border-radius:6px"), "the feed's radius");
  assert.ok(flat.includes("#feed-foot.fdismiss{font-size:10.5px;padding:1px9px"), "the feed footer instance's padding");
  // the chat/outline builder states the same box inline (inline beats classes, so it must carry it itself)
  // the GLYPH button keeps the outline dress (border/radius/colors) but wears an ICON box —
  // 4px 6px, taller and narrower than the word-buttons' 1px 9px, which read wide-and-short
  // around the 14px glyph next to inputs and tabs (the user 2026-08-26)
  assert.match(TAGMENU, /border:1px solid " \+ TAG_BTN_BORDER \+ ";"\s*\n\s*\+ "border-radius:6px;padding:4px 6px;/,
    "tagMenuButton wears the outline dress with the icon box");
  assert.match(TAGMENU, /btn\.style\.borderColor = narrowed \? TAG_BTN_ACCENT : TAG_BTN_BORDER;/,
    "narrowed = accent border, at rest the hairline");
  assert.match(TAGMENU, /btn\.style\.background = narrowed \? TAG_BTN_WASH : "transparent";/,
    "narrowed = the .on wash");
  // the timeline mounts REAL HTML buttons in the corner layer (round three — the SVG imitation
  // of the same numbers still rendered differently; parity is the rendered image now, checked by
  // side-by-side crops in review), wearing the identical literals:
  assert.match(TL, /border:1px solid rgba\(255,255,255,0\.10\);border-radius:6px;cursor:pointer;white-space:nowrap;opacity:0\.95;/,
    "the corner button carries the feed's hairline, radius, and footer opacity");
  assert.match(TL, /font:inherit;font-size:10\.5px;padding:4px 6px;/,
    "…and the footer's font scale with the icon box (glyph buttons, 2026-08-26)");
  assert.match(TL, /\.romp-tl-cbtn\.on\{[^}]*background:rgba\(156,210,255,0\.12\);opacity:1\}/,
    "narrowed = the .on wash at full strength");
});

test("timeline spacing grew (the user 2026-08-25: the corner controls were cramped)", () => {
  assert.match(TL, /const GAP = 8, BTNW = 28;/,
    "round three: the button slot IS the icon button's measured box (28px since the 2026-08-26 icon box), the bar's flex gap 8");
  assert.match(TL, /if \(hidden > 0\)/, "overflow chips collapse into a +N, one click from the menu");
});
