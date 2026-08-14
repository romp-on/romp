// Switching Host in the + picker lists THAT machine's sessions (the user 2026-07-29), so a remote
// session can be reopened — or revived, if it has aged out — without going to that machine's own
// dashboard. Before this the list was always local, and a remote session that was not currently a tab
// was reachable from nowhere in this UI.
//
// The mechanism is the prefix. The rows come back with host-prefixed ids, so the click posts
// openSession with that id and routeOutbound sends it to the kernel that owns the session; the revive
// confirmation rides back the same way (`id` is a generic scalar-id field). prefixInbound is EXECUTED
// here; the picker plumbing is source-pinned, like the rest of render.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { prefixInbound, routeOutbound } from "./federation";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const list = (items: any[]) => ({ type: "sessionList", items, defaultDir: "~/somewhere" });

test("a remote session list comes back with prefixed ids and names, stamped with its host", () => {
  const out = prefixInbound("TESTHOST", list([
    { id: "1111-2222", name: "api", running: true, time: "running" },
    { id: "3333-4444", name: "tests", running: false, time: "2h ago" }]));
  assert.deepEqual(out.items.map((i: any) => i.id), ["TESTHOST:1111-2222", "TESTHOST:3333-4444"]);
  assert.deepEqual(out.items.map((i: any) => i.name), ["TESTHOST:api", "TESTHOST:tests"]);
  assert.equal(out.host, "TESTHOST", "so the picker can tell whose answer this is");
  assert.equal(out.items[0].running, true, "everything else is left alone");
});

test("the LOCAL list is untouched, host and all", () => {
  const m = list([{ id: "1111-2222", name: "web" }]);
  assert.deepEqual(prefixInbound("", m), m, "no prefix, no host stamp, byte-identical");
});

test("a malformed row is passed through rather than mangled", () => {
  const out = prefixInbound("TESTHOST", list([null, "nope", { name: "no id" }]));
  assert.deepEqual(out.items, [null, "nope", { name: "no id" }]);
});

test("a click on a remote row routes to the kernel that owns the session", () => {
  // this is the whole point of prefixing the ids: the row posts the id it was given
  const [route] = routeOutbound({ type: "openSession", id: "TESTHOST:1111-2222" });
  assert.equal(route.host, "TESTHOST");
  assert.equal(route.msg.id, "1111-2222", "the kernel is host-blind — it gets its own bare id");
  // and reviving a dead one takes the same path
  const [rev] = routeOutbound({ type: "reviveSession", id: "TESTHOST:1111-2222" });
  assert.deepEqual([rev.host, rev.msg.id], ["TESTHOST", "1111-2222"]);
});

test("the request names the host, and every open starts from the host it opened on", () => {
  assert.match(RENDER, /function requestSessionList\(host: string\): void \{\s*\n\s*pickerListHost = host;/);
  assert.match(RENDER, /postMessage\(\{ type: "requestSessions", host \}\)/);
  // was hardcoded to "" (always the local list). It now follows the Host row's opening selection —
  // the kernel's default create host when that machine is attached, else this one (the user
  // 2026-08-13); see create-defaults.test.ts for the selection rule itself.
  assert.match(RENDER, /requestSessionList\(openHost\);/);
});

test("a reply for a host the picker has moved on from is dropped, not painted", () => {
  // two kernels answer at their own speeds, so arrival order proves nothing about which is current
  assert.match(RENDER, /const from = typeof m\.host === "string" \? m\.host : "";/);
  assert.match(RENDER, /if \(from !== pickerListHost\) return;/);
  // and only the LOCAL reply's defaultDir is adopted — a remote's default belongs to that machine
  assert.match(RENDER, /if \(typeof m\.defaultDir === "string" && !from\) kernelDefaultDir = m\.defaultDir;/);
});

test("switching host swaps the list, with something on screen while it loads", () => {
  assert.match(RENDER, /requestSessionList\(h\);/);
  assert.match(RENDER, /loading \$\{h\}'s sessions…/);
});

test("a reachable host with no sessions says so, instead of looking like a failed search", () => {
  assert.match(RENDER, /if \(!list\.children\.length && pickerListHost\)/);
  assert.match(RENDER, /no sessions on \$\{pickerListHost\} in the last 30 days/);
});

// ── the picker is a dialog over the dashboard, not the chat pane blown up (the user 2026-07-29) ──────
// The shell lifts this iframe full-window so the session list gets the whole height. While lifted the
// page pins its BODY to the chat pane's old screen rect and keeps painting (2026-08-08: hiding the
// content instead left a black hole where the pane was), so the whole dashboard — the transcript
// included — stays visible behind the dim. Details pinned in palette.test.ts.
test("the page keeps painting in place while the shell has it lifted", () => {
  const STYLES = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(RENDER, /document\.body\.classList\.toggle\("picker-lifted", on\);/);
  assert.match(RENDER, /window\.parent\.postMessage\(\{ romp: "picker", on \}, "\*"\);/, "the shell still lifts it");
  assert.match(STYLES, /html\.picker-lifted \{ background: transparent !important; \}/);
  assert.match(STYLES, /body\.picker-lifted \{\s*\n\s*position: fixed;/);
  // only an unmeasurable pane (hidden / cross-origin parent) hides the content instead
  assert.match(STYLES, /body\.picker-lifted\.pane-gone > \* \{ visibility: hidden; \}/);
  assert.match(STYLES, /body\.picker-lifted\.pane-gone > #picker \{ visibility: visible; \}/);
  // visibility, not display: nothing reflows, so the chat is exactly where it was when the picker closes
  assert.doesNotMatch(STYLES, /body\.picker-lifted\.pane-gone > \* \{ display: none/);
  // …and it TOP-ANCHORS (the user 2026-08-12, superseding the 2026-08-08 centering): vertical
  // centering re-centered the card every time typing re-filtered the resume list, sliding the create
  // controls mid-use. Pinned top → the card only grows/collapses at its bottom edge, the list's edge.
  assert.match(STYLES, /body\.picker-lifted > #picker \{ align-items: flex-start; padding: 56px 16px 16px; \}/);
  // two thirds of the window: the list is what you came to read
  assert.match(STYLES, /width: min\(66vw, 900px\); min-width: min\(560px, 96vw\);/);
});
