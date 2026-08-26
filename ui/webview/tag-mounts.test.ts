// Per-surface tag lenses, PR-B (the user 2026-08-25): the chat tab strip and the outline pane
// mount the shared component; the reveal gesture keys on the chat lens additively; every pane's
// "Configure tags…" routes to THE dialog on the timeline pane. Executed model tests + source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { surfaceLens } from "./tag-lens";
import { revealIn } from "./session-views";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const FLEET = ui("webview", "fleet.ts");
const MENU = ui("webview", "tag-menu.ts");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("executed: surfaceLens reads the surface's lens, seeding from the legacy scalar", () => {
  assert.deepEqual(surfaceLens({ active: "all", tags: [] }, "chat"), { all: true });
  assert.deepEqual(surfaceLens({ active: "untagged", tags: [] }, "chat"), { none: true });
  assert.deepEqual(
    surfaceLens({ active: "g1", tags: [{ id: "g1", name: "pool", members: [] }] } as any, "chat"),
    { tags: ["pool"] });
  assert.deepEqual(
    surfaceLens({ active: "all", actives: { chat: { tags: ["x"] } }, tags: [] } as any, "chat"),
    { tags: ["x"] }, "an explicit lens wins over the scalar");
  assert.deepEqual(
    surfaceLens({ active: "all", actives: { chat: { tags: ["x"] } }, tags: [] } as any, "outline"),
    { all: true }, "surfaces are independent");
});

test("executed: revealIn is ADDITIVE on the chat lens — never a switch that hides the rest", () => {
  const views: any = { active: "all",
    actives: { chat: { tags: ["infra"] } },
    tags: [{ id: "g1", name: "infra", members: ["s1"] },
           { id: "g2", name: "workers", members: ["s2"] }] };
  const out = revealIn(views, "s2");
  assert.deepEqual(out.actives!.chat, { tags: ["infra", "workers"] },
    "the holder tag JOINS the selection — infra stays visible");
  const out2 = revealIn(views, "loose");
  assert.deepEqual(out2.actives!.chat, { none: true, tags: ["infra"] },
    "a session in no tag home adds the none bucket");
  assert.deepEqual(revealIn(views, "s1").actives!.chat, { tags: ["infra"] },
    "already visible → no move at all");
});

test("the chat strip and the outline both mount the shared component (source pins)", () => {
  assert.match(RENDER, /function chatVisible\(id: string\): boolean/);
  assert.match(RENDER, /lensVisible\(surfaceLens\(v, "chat"\), viewTagUnion\(v\), id\)/,
    "tabs + peeks decide through actives.chat");
  assert.match(RENDER, /tagMenuButton\("filter these tabs by tag"/,
    "the tooltip names the surface — the ONE scope carrier since the menu caption retired (2026-08-25)");
  assert.match(RENDER, /Object\.assign\(\{\}, v\.actives, \{ chat: l \}\)/, "writes land on chat's lens only");
  assert.match(FLEET, /tagMenuButton\("filter this outline by tag"/,
    "ditto — the outline tooltip names its surface");
  assert.match(FLEET, /Object\.assign\(\{\}, v\.actives, \{ outline: l \}\)/);
  assert.match(FLEET, /if \(!lensVisible\(outlineLens, outlineUnions, s\.sid\)\) continue;/);
  assert.match(FLEET, /fleetViews = m\.views as SessionViews/, "the outline reads views off the feed payload");
  assert.match(KERNEL, /"views": _views_client\(\),   # the rendered views blob — the outline \+ feed tag mounts read it/);
});

test("every pane's Configure tags… routes to THE dialog on the timeline (source pins)", () => {
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "openTagsDialog" \}\)/);
  assert.match(FLEET, /vscodeApi\?\.postMessage\(\{ type: "openTagsDialog" \}\)/);
  assert.match(KERNEL, /msg\.get\("type"\) == "openTagsDialog"/);
  assert.match(KERNEL, /_send_to_view\("timeline", \{"type": "openViewsDialog"\}, \(client or \{\}\)\.get\("wid"\) or ""\)/,
    "routed to the SAME dashboard's timeline, like tagEditFailed");
});

test("a wrapped tag-controls line reserves the + tab's exact box — the arithmetic, from the stylesheet", () => {
  // the user 2026-08-25: chips wrapping WITHOUT the + sat nearly flush under the tab row above,
  // because a flex line is only as tall as its tallest item and the pill is 18px to the +'s ~31.
  // The fix pins the +'s content box (line-height) and reserves the SAME total on the controls'
  // box, so line height never depends on which elements land on the line. This test recomputes
  // the equality from the stylesheet literals, so neither side can drift alone.
  const CSS = ui("webview", "styles.css");
  const addRule = CSS.match(/\.tab-add \{[^}]*\}/)![0];
  const lineH = Number(addRule.match(/line-height: (\d+)px/)![1]);
  const padV = Number(addRule.match(/padding: (\d+)px/)![1]);   // vertical padding, both sides
  const tabRule = CSS.match(/^\.tab \{[\s\S]*?\n\}/m)![0];
  const borderV = tabRule.includes("border: 1px solid") && tabRule.includes("border-bottom: none") ? 1 : 2;
  const boxRule = CSS.match(/\.tab-tagbox \{[^}]*\}/)![0];
  const minH = Number(boxRule.match(/min-height: (\d+)px/)![1]);
  assert.equal(minH, lineH + padV * 2 + borderV,
    "the .tab-tagbox floor equals the + tab's rendered box (content line + padding + border)");
  assert.match(boxRule, /align-items: center/, "…with the pill and chips centered inside it");
});

test("the shared component: toggles stay open, no scope caption, echo dismissal (source pins)", () => {
  assert.match(MENU, /opts\.onApply\(toggleLens\(lens, \{ tag: u\.name \}\), false\); build\(\);/,
    "tag rows toggle and repaint in place");
  assert.match(MENU, /opts\.onApply\(\{ all: true \}, true\)/, "All is a plain pick");
  assert.ok(!MENU.includes("scopeCaption"),
    "the scope caption retired 2026-08-25 (the user: the tooltip already says it) — the menu opens straight onto its rows");
  assert.match(MENU, /localStorage\.setItem\("romp:menu-echo"/, "every pane writes the pointerdown echo");
  assert.match(MENU, /e\.key === "romp:menu-echo" && e\.newValue/, "every open menu listens");
  assert.match(MENU, /btn\.addEventListener\("click", \(e\) => e\.stopPropagation\(\)\)/,
    "the opener swallows its own click (the click-and-hold rule)");
});
