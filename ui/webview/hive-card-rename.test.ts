// Renaming a hexagon (the user 2026-08-13): the fly-in card's name is a click-to-edit that
// posts the SAME renameSession op the chat tab strip uses, under the same contracts —
// the host prefix of a remote session is fixed chrome (tab-rename-host.test.ts), the label
// changes only when the kernel's push lands, and a refusal surfaces on the card instead of
// vanishing. Source-pinned like tab-rename-host.test.ts: card and kernel only meet at runtime.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const HIVE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive-pane.css"), "utf8");
const rename = HIVE.slice(HIVE.indexOf("private startRename()"), HIVE.indexOf("show(s: HiveSession"));

test("the card's name is a delegated rename action (click-safe by construction)", () => {
  assert.match(HIVE, /class="hc-name" data-act="rename"/, "the name carries the action");
  assert.match(HIVE, /rename: \(\) => this\.startRename\(\)/, "…dispatched off the stable card root");
});

test("the editor opens on the session name alone, with a remote's host beside it", () => {
  assert.match(rename, /const p = hostPrefix\(this\.curName, sid\);/);
  assert.match(rename, /const base = p \? p\.rest : this\.curName;/);
  assert.match(rename, /input\.value = base;/, "the field holds the part that is theirs to change");
  assert.match(rename, /fixed\.className = "host-prefix";/, "the host reads as the quiet fixed chrome");
  assert.match(rename, /input\.before\(fixed\)/, "sitting before the field, not inside it");
  assert.match(rename, /fixed\?\.remove\(\)/, "…and torn down with the editor");
});

test("commit posts the kernel's own renameSession op, never the display string", () => {
  assert.match(rename, /this\.onRename\(sid, v\)/);
  assert.match(rename, /v !== base/, "unchanged is measured against the name, not the display string");
  assert.match(HIVE, /\{ type: "renameSession", id: sid, name \}/, "the world posts the drive op");
  assert.ok(KERNEL.includes('elif t == "renameSession" and msg.get("name"):'), "the kernel still handles it");
});

test("the commit targets the sid the editor OPENED on, not whatever the card shows now", () => {
  assert.match(rename, /const sid = this\.sid;/, "captured at open — a blur can land after a switch");
  assert.doesNotMatch(rename, /onRename\(this\.sid/, "never the card's current sid at commit time");
});

test("Enter/blur commit, Esc cancels, and typing never leaks into the world", () => {
  assert.match(rename, /if \(e\.key === "Enter"\) \{ e\.preventDefault\(\); done\(true\); \}/);
  assert.match(rename, /else if \(e\.key === "Escape"\) \{ e\.preventDefault\(\); done\(false\); \}/);
  assert.match(rename, /addEventListener\("blur", \(\) => done\(true\)\)/);
  assert.match(rename, /e\.stopPropagation\(\)/, "keystrokes must not orbit the camera or close the card");
});

test("a live editor is abandoned when the card switches, hides, or the session ends", () => {
  assert.match(HIVE, /if \(fresh\) this\.endEdit\?\.\(false\);/, "show() on a new sid cancels");
  assert.match(HIVE, /hide\(\) \{ this\.endEdit\?\.\(false\);/, "hide() cancels");
  const gone = HIVE.slice(HIVE.indexOf("gone() {"), HIVE.indexOf("error(title"));
  assert.match(gone, /this\.endEdit\?\.\(false\);/, "gone() cancels");
});

test("an ended session stops offering rename; refresh() restores it for a live one", () => {
  assert.match(HIVE, /delete this\.name\.dataset\.act;/, "gone() revokes the affordance");
  assert.match(HIVE, /this\.name\.dataset\.act = "rename";/, "refresh() grants it");
});

test("a refused rename surfaces on the card (fail loudly), via the kernel's warn reply", () => {
  assert.ok(KERNEL.includes('"type": "warn", "text": "session names use letters'),
    "the kernel answers a bad name with a warn");
  assert.match(HIVE, /if \(world\.card\.sid\) world\.card\.error/, "an open card owns the warn");
  assert.match(HIVE, /else world\.note\(/, "…and with no card open, the board-level note shows it");
});

test("the editor's chrome lives in hive-pane.css (this page loads only its own stylesheet)", () => {
  for (const sel of ['.hc-name[data-act="rename"]', ".hc-rename", ".host-prefix"])
    assert.ok(CSS.includes(sel), "hive-pane.css styles " + sel);
});
