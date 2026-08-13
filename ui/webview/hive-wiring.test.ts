// Pins the Hive pane's wiring across its three homes — kernel/kernel.py (route, page, shell
// landing), vscode-extension/esbuild.js (bundle entries), and the status palette shared with
// styles.css — so a refactor of any one of them can't silently unwire the pane (the
// prebuild.test.ts / fleet pattern: these files only meet at runtime, never at compile time).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const ESBUILD = fs.readFileSync(path.resolve(process.cwd(), "esbuild.js"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const HIVE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive.ts"), "utf8");

test("kernel serves /hive from _hive_page with the hive shim + bundle", () => {
  assert.ok(KERNEL.includes('if p == "/hive":'), "the /hive route exists");
  assert.ok(KERNEL.includes("def _hive_page():"), "_hive_page exists");
  assert.ok(KERNEL.includes('_shim("hive", v)'), "the page connects as app=hive");
  assert.ok(KERNEL.includes("/dist/hive.js"), "the page loads the hive bundle");
  assert.ok(KERNEL.includes('_pane_spin("hive-root")'), "the romp loader guards the empty root");
});

test("the push loop treats app=hive as a feed rider with the ledgers attach", () => {
  assert.ok(KERNEL.includes('any(c["app"] in ("fleet", "hive") for c in targets)'),
    "want_fleet counts hive (the ledgers attach)");
  assert.ok(KERNEL.includes('any(c["app"] in ("feed", "fleet", "hive", "chat") for c in targets)'),
    "want_feed counts hive");
  assert.ok(KERNEL.includes('c["app"] in ("feed", "fleet", "hive")'),
    "the send loop delivers the feed payload to hive clients");
});

test("the landing shell mounts the hive pane, gutter, rail button and toggles", () => {
  for (const frag of [
    "<div class=pane id=hive-pane><iframe id=f-hive src=/hive></iframe></div>",
    "<div class=gv id=gv-c></div>",
    "<div class=rail-btn data-pane=hive>Hive</div>",
    "body:not(.po-hive) #hive-pane{display:none}",
    "#hive-pane{flex:var(--g-hive,50) 1 0}",
    "'f-hive':'hive-pane'",
  ]) assert.ok(KERNEL.includes(frag), "landing carries: " + frag);
  assert.ok(KERNEL.includes("po={chat:true,fleet:false,feed:true,hive:false,timeline:true}"),
    "hive defaults OFF in the rail toggles");
  assert.ok(/PANES=\['chat-pane','fleet-pane','feed-pane','hive-pane'\]/.test(KERNEL),
    "the gutter controller knows the hive pane");
});

test("esbuild bundles hive.ts and hive-pane.css like the other panes", () => {
  assert.ok(ESBUILD.includes('"../ui/webview/hive.ts"'), "hive.ts entry");
  assert.ok(ESBUILD.includes('"../ui/webview/hive-pane.css"'), "hive-pane.css entry");
});

test("the talk path speaks the kernel's drive-op protocol", () => {
  // the card's Send posts the SAME sendMessage op the chat composer uses — _drive routes it
  // by sid to the owning backend from ANY app socket, and a foreign sid is refused loudly
  assert.ok(HIVE.includes('{ type: "sendMessage", id: sid, text }'), "hive posts sendMessage");
  assert.ok(KERNEL.includes('if t == "sendMessage" and msg.get("text"):'), "kernel still handles it");
  assert.ok(HIVE.includes('m.type === "err"'), "kernel refusals surface on the card, never vanish");
  assert.ok(HIVE.includes('{ type: "openSession", id: sid }'), "the card can jump to the full session");
});

test("cell lines trace the EXACT boundary, so used and empty cells are one connected web", () => {
  // The status line, the sonar ping and the ghost all draw hexLineGeo(PAD_R) — the same
  // corners and radius the lattice's edges use — never an inset copy floating inside the
  // cell (the user 2026-08-13: they should be connected).
  const loops = HIVE.match(/new THREE\.LineLoop\(hexLineGeo\([^)]*\)/g) || [];
  assert.ok(loops.length >= 3, "the line treatment is in use");
  for (const l of loops)
    assert.equal(l, "new THREE.LineLoop(hexLineGeo(PAD_R)", "a line loop is not on the boundary: " + l);
  assert.ok(!HIVE.includes("LINE_INSET"), "no inset constant survives");
});

test("clicking the bean opens their chat; the tile keeps opening the card", () => {
  // The bean is the direct line (the user 2026-08-13): an invisible capsule makes the
  // whole character clickable, the pop acknowledges on THEM, and openChat both opens the
  // session and reveals the chat pane — one path shared with the card's Open and dblclick.
  assert.match(HIVE, /new THREE\.CapsuleGeometry\(0\.55/, "the bean has a whole-body hit capsule");
  assert.match(HIVE, /colorWrite: false/, "…that draws nothing but still raycasts");
  assert.match(HIVE, /if \(pp\.bean && pad\) \{\s*\n\s*pad\.pokeBean\(\);\s*\n\s*this\.openChat\(pp\.sid\);/,
    "bean click: acknowledge on them, then straight to the chat (on the clean UP — a moved press is a pick-up)");
  assert.match(HIVE, /openChat\(sid: string\) \{/, "one shared open path");
  assert.match(HIVE, /this\.card\.onOpen = \(sid\) => this\.openChat\(sid\);/, "the card's Open uses it");
  assert.match(HIVE, /\{ romp: "reveal", pane: "chat" \}/, "…and it reveals the chat pane");
});

test("hive's WebGL palette matches the styles.css status tokens (one meaning per color)", () => {
  // hive draws in WebGL where CSS vars can't reach, so it carries the values — this pin is
  // what keeps them the SAME values. Each pair: [styles.css token, hive.ts literal].
  const pairs: [string, RegExp][] = [
    ["--st-working-bg: #e0b020", /working:\s*0xe0b020/],
    ["--st-ready-bg: #2b7fb8", /ready:\s*0x2b7fb8/],
    ["--st-awaiting-bg: #c0392b", /awaiting:\s*0xc0392b/],
    ["--st-blocked-bg: #e5484d", /blocked:\s*0xe5484d/],
    ["--st-awaitbg-bg: #54B204", /awaitingBg:\s*0x54b204/],
    ["--st-compacting-bg: #14b8a6", /compacting:\s*0x14b8a6/],
    ["--accent: #9cd2ff", /ACCENT\s*=\s*0x9cd2ff/],
  ];
  for (const [cssToken, hiveLit] of pairs) {
    assert.ok(CSS.includes(cssToken), "styles.css still declares: " + cssToken);
    assert.ok(hiveLit.test(HIVE), "hive.ts carries the same value: " + hiveLit);
  }
});
