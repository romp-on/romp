// Where a new session lands, from BOTH spawn surfaces (the user 2026-08-13, whose sessions all live on
// one remote machine — the + picker resetting to "this machine" on every open, and the hive tray always
// dropping locally, meant re-picking the same host all day and creating on the wrong box when they
// forgot). The default create host is the LOCAL kernel's persisted choice (~/.config/romp/default-host),
// served on its sessionList reply and on /models for the tray; "" means this machine.
//
// The routing is the existing federation contract: a `host` field on createSession wins outright and is
// stripped before the kernel sees it. That is EXECUTED here; the picker/tray plumbing is source-pinned,
// like the rest of render.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { routeOutbound } from "./federation";
import { DEFAULT_SETTINGS } from "./settings";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = W("render.ts");
const HIVE = W("hive.ts");

test("a create carrying a host routes to that kernel, with the field stripped", () => {
  const routes = routeOutbound({ type: "createSession", name: "api", backend: "tmux", host: "TESTHOST" });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "TESTHOST");
  assert.equal(routes[0].msg.host, undefined, "the kernel's handlers are host-blind");
  assert.equal(routes[0].msg.name, "api");
});

test("a create with no host still goes local", () => {
  const [r] = routeOutbound({ type: "createSession", name: "api", backend: "tmux" });
  assert.equal(r.host, "");
});

test("the picker preselects the default host ONLY when that machine is attached", () => {
  // a stored name whose host is detached must not select a button that isn't on the row — the create
  // would be aimed at a kernel this dashboard cannot reach
  assert.match(RENDER, /defaultCreateHost && hs\.includes\(defaultCreateHost\) \? defaultCreateHost : ""/);
});

test("the picker opens its dir prefill, Browse state and session list on that SAME host", () => {
  // all four used to be hardcoded to local, so a preselected remote host would have shown this
  // machine's folder completions and this machine's session list under it
  assert.match(RENDER, /applyBrowseState\(openHost\)/);
  assert.match(RENDER, /di\.value = dirPrefill\(openHost\)/);
  assert.match(RENDER, /requestSessionList\(openHost\)/);
});

test("the default host is adopted from the LOCAL kernel's reply only", () => {
  // a remote kernel's own default says nothing about where THIS dashboard creates
  assert.match(RENDER, /typeof m\.defaultHost === "string" && !from\) defaultCreateHost = m\.defaultHost/);
});

test("a tray drop carries the default host, and omits the field when it is this machine", () => {
  assert.match(HIVE, /const host = SPAWN_DEFAULTS\.host \|\| "";/);
  assert.match(HIVE, /\.\.\.\(host \? \{ host \} : \{\}\)/);
});

test("a tray drop uses the gear's backend, not a hardcoded one", () => {
  // it hardcoded "sdk", so a board set to terminal sessions still dropped SDK ones
  assert.match(HIVE, /backend: loadSettings\(\)\.backend/);
  assert.doesNotMatch(HIVE, /createSession",[^)]*backend: "sdk"/);
});

test("new sessions default to the terminal backend", () => {
  assert.equal(DEFAULT_SETTINGS.backend, "tmux");
});
