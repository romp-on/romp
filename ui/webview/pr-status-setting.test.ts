// The PR status switch, pinned at the source like the other gear rows (the gear has no jsdom harness): a per-install
// kernel-side checkbox in the Task tracking tab that turns the Outline's PR chips, and every git and gh read behind them,
// on or off. Gesture-stamped, filled from /version, named in the stale-gesture toast, never propagated, and never dimmed
// by the Task tracking switch, since it governs what reaches GitHub.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const GEAR = read("ui", "webview", "gear.js");
const FED = read("ui", "webview", "federation.ts");
const KERNEL = read("bin", "romp-kernel");

test("the gear has a PR status checkbox in the Task tracking tab, gesture-stamped, filled from /version", () => {
  const at = GEAR.indexOf("id=rs-prstatus");
  assert.ok(at > 0, "the checkbox exists");
  assert.ok(GEAR.indexOf("data-pane=tasks") < at && at < GEAR.indexOf("data-pane=debug"), "…in the Task tracking tab");
  const row = GEAR.slice(at, GEAR.indexOf("</label>", at) + 8);
  assert.match(row, /<b>PR status<\/b>/);
  assert.match(row, /Off, romp runs no git or gh command for them\./, "the sub-copy says what off stops");
  assert.equal((row.match(/<span/g) || []).length, (row.match(/<\/span>/g) || []).length, "every span the row opens it closes");
  assert.ok(GEAR.includes("post({ type: 'setPrStatus', enabled: prs.checked, gt: gclock.stamp('pr-status') })"),
    "the click posts the kernel's message with a gesture stamp");
  assert.ok(GEAR.includes("prs.checked = v.prStatus !== false"), "an absent field reads on, the kernel's default");
  assert.match(GEAR, /STALE_LABELS = \{[\s\S]*?'pr-status': 'PR status'/, "a stood-down gesture toasts under the row's name");
  assert.match(GEAR, /STALE_TYPE = \{[\s\S]*?'pr-status': 'setPrStatus'/, "…and Apply anyway re-issues this setting");
});

test("the Task tracking switch never dims the PR status row", () => {
  const at = GEAR.indexOf("function dressTracking(on)");
  assert.ok(at > 0);
  assert.ok(GEAR.slice(at, at + 400).includes("!r.querySelector('#rs-tasktrack, #rs-prstatus')"),
    "the row filter skips it with the switch's own row");
});

test("PR status is per-install: federation never carries it, and the kernel serves and applies it", () => {
  assert.ok(!FED.includes("setPrStatus"), "not a KERNEL_SETTING: the gh login is this machine's");
  assert.ok(KERNEL.includes('"prStatus": _pr_status_on(),'), "/version carries it for the fill");
  assert.ok(KERNEL.includes('msg.get("type") == "setPrStatus"'), "the WS op exists");
  assert.ok(/_GT_STORES = \([^)]*"pr-status"/.test(KERNEL), "gt-gated like the rest");
});
