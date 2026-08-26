// The gear's judge MODEL pickers mirror the session pickers (the user 2026-08-25): families
// top-level, family click = the /models remembered default, a right-facing-caret side submenu of
// versions, right-preferred side (measured). The native select stays hidden as the value holder so
// fill()/mixed marks keep working, and version ids ride as options so any stored pick displays.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const GEAR = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.js"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the three judge model selects grow the version menu; the select stays the value holder", () => {
  assert.match(GEAR, /function versionMenu\(sel, extraFirst\)/);
  assert.match(GEAR, /versionMenu\(jm\);\s*\n\s*versionMenu\(im\);\s*\n\s*versionMenu\(dm, \[\{ value: 'triage', label: 'Follow triage', versions: \[\] \}\]\);/,
    "judge, index, and distill (with its Follow-triage sentinel first)");
  assert.match(GEAR, /sel\.style\.display = 'none';/, "the native select hides — still the value holder");
  assert.match(GEAR, /sel\.dispatchEvent\(new Event\('change'\)\)/, "picks flow through the existing change→post wiring");
  assert.match(GEAR, /versions ride as options too/, "a stored version id still displays and mixed-marks");
  assert.match(GEAR, /pick\(fam\.default \|\| fam\.value\)/, "family click sends the remembered default");
});

test("caret faces RIGHT everywhere; the submenu side is measured with the right preference", () => {
  assert.match(GEAR, /caret\.textContent = '\\u25B8'/);
  assert.ok(!GEAR.includes("\\u25C2"), "no left-facing caret");
  assert.match(GEAR, /if \(rr\.right \+ 4 \+ sw <= window\.innerWidth - 8\) sub\.style\.left = Math\.round\(rr\.right \+ 4\) \+ 'px';/,
    "right side whenever it fits");
  assert.match(GEAR, /else sub\.style\.left = Math\.max\(8, Math\.round\(rr\.left\) - sw - 4\) \+ 'px';/,
    "left only as the measured fallback");
  assert.match(GEAR, /e\.key === 'romp:menu-echo' && e\.newValue/, "cross-pane dismissal adopted");
});

test("the kernel accepts version ids on every judge tier", () => {
  assert.match(KERNEL, /_JUDGE_MODEL_VALUES = _MODEL_VALUES \| set\(_VERSION_FAMILY\)/);
  assert.match(KERNEL, /_set_judge_state\("distill-model", v, _JUDGE_MODEL_VALUES \| \{"triage"\}\)/);
});
