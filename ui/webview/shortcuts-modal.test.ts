// The keyboard-shortcuts dialog (ui/webview/shortcuts-modal.ts): the configurable list the settings
// gear now links to instead of carrying a static table (the user 2026-08-09) — VS Code's grammar:
// click Change, press the chord, conflicts named with an explicit reassign, Backspace unbinds, Reset
// returns the default. DOM-heavy → source pins; the decision rules it renders are the REAL exported
// functions under test in keybindings.test.ts, not a replica.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const MODAL = fs.readFileSync(path.join(ROOT, "ui", "webview", "shortcuts-modal.ts"), "utf8");
const MAIN = fs.readFileSync(path.join(ROOT, "ui", "webview", "palette-main.ts"), "utf8");
const GEARJS = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const KEYB = fs.readFileSync(path.join(ROOT, "ui", "webview", "keybindings.ts"), "utf8");

test("wears the shared modal vocabulary at z300, over a 0.55 dim, dashboard untouched behind", () => {
  assert.match(MODAL, /#rkeys-back\{position:fixed;inset:0;z-index:300;/);
  assert.match(MODAL, /background:rgba\(0,0,0,0\.55\)/);
  assert.match(MODAL, /#rkeys\{width:min\(640px,94%\)/);
  assert.match(MODAL, /background:#252526;/);
});

test("recording is a captured card-level listener: chords never type into the filter or leak out", () => {
  assert.match(MODAL, /panel\.addEventListener\("keydown", onRecordKey, true\);/);
  assert.match(MODAL, /if \(!recId\) return;/);
  assert.match(MODAL, /e\.preventDefault\(\);\s*\n\s*e\.stopPropagation\(\);/);
  // a bare modifier is a chord still being built; an unbindable key keeps the recorder listening — and SAYS why on the
  // row (2026-09-10: a bare ` or 1 used to be swallowed silently, so the dialog looked dead)
  assert.match(MODAL, /if \(!ch\) return;\s*.*\n(?:\s*\/\/[^\n]*\n)*\s*if \(!bindable\(ch\)\) \{\n\s*refused = /);
});

test("a conflict is named and resolved explicitly — reassign unbinds the loser visibly", () => {
  assert.match(MODAL, /const other = conflictOf\(ch, recId, bindableCommands\(\), loadOverrides\(\), mac\);/);
  assert.match(MODAL, /is used by “/);
  assert.match(MODAL, /Enter reassigns it here, Esc keeps it there/);
  assert.match(MODAL, /saveOverride\(pendOther!, ""\);\s*\/\/ the loser is visibly unbound, never silently dead/);
});

test("Backspace unbinds, Reset returns the default, the chips read the EFFECTIVE binding", () => {
  assert.match(MODAL, /if \(e\.key === "Backspace" \|\| e\.key === "Delete"\) \{ commit\(recId, ""\); return; \}/);
  // a bound command's listening row offers the same unbind as a button (the user 2026-09-11: the tab menu's one
  // "Update hot key…" row covers changing and removing)
  assert.match(MODAL, /if \(effectiveChord\(c\.id, c\.chord, overrides, mac\)\) \{\s*const rm = doc\.createElement\("button"\);[\s\S]{0,200}?rm\.textContent = "Remove";\s*rm\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); commit\(c\.id, ""\); \}\);/);
  assert.match(MODAL, /saveOverride\(c\.id, null\); render\(\);/);
  assert.match(MODAL, /const eff = effectiveChord\(c\.id, c\.chord, overrides, mac\);/);
  assert.match(MODAL, /textContent = "not bound";/);
});

test("Escape is one level at a time and owned by the shell chain: recording → cancel, open → close", () => {
  // the recorder deliberately does NOT handle Escape; the shell's Escape chain calls close(),
  // whose first level cancels the recording and reports consumed
  assert.match(MODAL, /if \(e\.key === "Escape"\) return;\s*\/\/ the shell Escape chain routes it/);
  assert.match(MODAL, /if \(recId\) \{ cancelRecord\(\); return true; \}/);
  assert.match(MODAL, /if \(!back \|\| back\.hidden\) return false;/);
  // …and the kernel's chain asks this dialog FIRST (topmost, z300)
  assert.match(KERNEL, /if\(window\.__rompKeysClose&&window\.__rompKeysClose\(\)\)\{closed=true;\}/);
});

test("the built-in section keeps the non-command keys — without an Enter-to-send row — and the recorder refuses them", () => {
  // the list lives in the pure module now (2026-09-10): the dialog renders it AND refuses a chord it owns
  assert.match(KEYB, /export const BUILT_IN: Array<\[string, string\]> = \[/);
  assert.match(KEYB, /\["Shift\+Enter", "New line in the composer"\]/);
  assert.match(KEYB, /\["Ctrl\+C", "Interrupt the session \(composer\)"\]/);
  assert.match(KEYB, /\["Alt\+Arrows", "Move focus between panes"\]/);
  assert.doesNotMatch(KEYB, /Send message/);
  assert.doesNotMatch(MODAL, /const BUILT_IN/);
  assert.match(MODAL, /for \(const \[chord, what\] of BUILT_IN\) \{/, "the dialog renders the shared list");
  assert.match(MODAL, /const own = builtInOwner\(ch, mac\);\n\s*if \(own\) \{\n\s*refused = displayChord\(ch, mac\) \+ " is built in \(" \+ own\.charAt\(0\)\.toLowerCase\(\) \+ own\.slice\(1\) \+ "\) — pick another";\n\s*render\(\);\n\s*return;/,
    "a built-in chord is said on the row, never bound dead");
});

test("reachable from the palette, the gear's customize link, and __rompKeysOpen", () => {
  assert.match(MAIN, /registerCommand\(\{ id: "keys\.open", title: "Keyboard shortcuts", run: \(\) => keys\.open\(\) \}\);/);
  assert.match(MAIN, /w\.__rompKeysOpen = \(\) => keys\.open\(\);/);
  assert.match(MAIN, /if \(e\.data && e\.data\.romp === "openKeys"\) keys\.open\(\);/);
  // the gear's link: web shell only (VS Code points at its own Keyboard Shortcuts editor), and it
  // closes the settings modal first so the two never stack
  assert.match(GEARJS, /id=rs-keys-web hidden/);
  assert.match(GEARJS, /id=rs-keys-vsc hidden/);
  assert.match(GEARJS, /closeSettings\(\); try \{ window\.parent\.postMessage\(\{ romp: 'openKeys' \}, '\*'\); \}/);
  assert.match(GEARJS, /search "rompChat" in Keyboard Shortcuts/);
  // the static list is gone — the dialog is the one home for shortcuts
  assert.doesNotMatch(GEARJS, /<kbd>Enter<\/kbd><\/span><span class=rs-key-desc>Send message/);
  assert.doesNotMatch(GEARJS, /Jump to the session tabs/);
});

test("while the dialog is up the dispatcher stands down — browsing the list can't fire commands", () => {
  assert.match(MAIN, /if \(keys\.isOpen\(\)\) \{ keys\.feed\(e\); return; \}\s*.*never dispatch under it/);
});
