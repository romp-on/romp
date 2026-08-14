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
const CSS2 = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive-pane.css"), "utf8");

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

test("a click's whole answer is the chat on the left; the card stays out of the way", () => {
  // The bean is the direct line (the user 2026-08-13): an invisible capsule makes the
  // whole character clickable, the pop acknowledges on THEM, and openChat both opens the
  // session and reveals the chat pane — one path shared with the card's Open and dblclick.
  assert.match(HIVE, /new THREE\.CapsuleGeometry\(0\.55/, "the bean has a whole-body hit capsule");
  assert.match(HIVE, /colorWrite: false/, "…that draws nothing but still raycasts");
  assert.match(HIVE, /if \(pad && !hit\.name\) \{\s*\n\s*if \(hit\.bean\) pad\.pokeBean\(\);\s*\n\s*this\.openChat\(sid\);/,
    "ANY press on an occupied cell switches chat ON THE DOWN — except the nameplate, which edits");
  // the fly-in card + camera zoom on every click read as noise (the user 2026-08-13):
  // a click does NOTHING more on the up, and select() serves only the deep-link jump
  assert.match(HIVE, /if \(pp\.name && Math\.hypot[^\n]+this\.beginBoardRename\(pp\.sid\);\s*\n\s*return;/,
    "the up resolves ONLY the nameplate's edit — no card, no fly-in");
  assert.ok(!/this\.select\(pp\.sid\)/.test(HIVE), "no click path reaches select()");
  assert.match(HIVE, /openChat\(sid: string\) \{/, "one shared open path");
  assert.match(HIVE, /this\.card\.onOpen = \(sid\) => this\.openChat\(sid\);/, "the card's Open uses it");
  assert.match(HIVE, /\{ romp: "reveal", pane: "chat" \}/, "…and it reveals the chat pane");
  // the INSTANT leg: the shell hands the chat its focus directly — the tab flips with no
  // kernel round trip in the way; the kernel op follows for its side effects
  assert.ok(HIVE.indexOf('{ romp: "focusChat", id: sid }') < HIVE.indexOf('{ type: "openSession", id: sid }'),
    "the client-side flip is posted BEFORE the kernel op");
  assert.ok(KERNEL.includes("m.romp!=='focusChat'"), "the shell carries the focusChat relay");
  assert.ok(KERNEL.includes("f.contentWindow.postMessage({type:'focus',id:m.id},'*')"),
    "…which injects the same focus frame the kernel would send");
});

test("hovering a session floats its live status over the bean (the card's own stateLine)", () => {
  assert.match(HIVE, /this\.tipEl\.id = "hive-tip";/, "the tip element exists");
  assert.match(HIVE, /stateLine\(tipPad\.sess, Math\.floor\(Date\.now\(\) \/ 1000\)\)/,
    "the text is the SAME line the card's state row shows — one vocabulary for status");
  assert.match(HIVE, /finishedLine\(tipPad\.sess, Math\.floor\(Date\.now\(\) \/ 1000\)\)/,
    "…except an unseen finish, which says what the ✓ is holding for you");
  assert.match(HIVE, /this\.tipEl\.dataset\.state = done \? "done" : tipPad\.sess\.state;/,
    "state drives the color (an unseen finish wears the done hue)");
  assert.match(HIVE, /!this\.dragSession\s*\n\s*\? this\.pads\.get\(this\.hovered\) : null;/,
    "no tip mid-carry — the dock speaks then");
  for (const frag of ['#hive-tip {', "pointer-events: none;", '#hive-tip[data-state="working"] { color: #e0b020; }',
    '#hive-tip[data-state="awaiting"], #hive-tip[data-state="blocked"] { color: #ff8589; }',
    '#hive-tip[data-state="done"] { color: #4db9f2; }'])
    assert.ok(CSS2.includes(frag), "hive-pane.css carries: " + frag);
});

test("a finished session holds its ✓ until the user goes to look (the unseen-done latch)", () => {
  assert.match(HIVE, /makeTextSprite\("✓", "#ffffff", "#1EA1EB"\)/,
    "the note is the app's done-check: white ✓ on --check-bg, the ledger/feed mark");
  assert.match(HIVE, /st === "ready" && this\.unseenDone/,
    "shown only over a READY pad — a new turn hides it, needs-you (the bang) outranks it");
  assert.match(HIVE, /foldSeenDone\(this\.seenDone, sessions\)/, "the latch derives per payload");
  for (const frag of ["this.lookedAt(sid);", "loadSeen(SEEN_DONE_KEY)", "saveSeen(SEEN_DONE_KEY"]) {
    assert.ok(HIVE.includes(frag), "hive.ts carries: " + frag);
  }
  assert.ok(HIVE.indexOf("this.lookedAt(sid);") !== HIVE.lastIndexOf("this.lookedAt(sid);"),
    "BOTH look gestures ack: the chat click-through and the deep-link select");
});

test("a filed question shouts only until looked at; a live prompt always shouts", () => {
  assert.match(HIVE, /st === "awaiting" && !this\.askAck/,
    "the bang/sonar shout is gated on the ask being unseen");
  assert.match(HIVE, /foldSeenAsk\(this\.seenAsk, sessions\)/, "the ask latch derives per payload");
  assert.match(HIVE, /s\.state === "awaiting" && !s\.liveAsk && !ask\.unseen\.has\(s\.sid\)/,
    "acked = filed (never live) and looked at — the red ring stays, the shout stops");
  for (const frag of ["loadSeen(SEEN_ASK_KEY)", "saveSeen(SEEN_ASK_KEY"]) {
    assert.ok(HIVE.includes(frag), "hive.ts carries: " + frag);
  }
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
