// The keybindings model (ui/webview/keybindings.ts) — real unit tests: chord normalization, platform
// resolution and display, the override/default/conflict rules the shortcuts dialog enforces, and the
// dispatch guard. Pure module, no DOM at import (the store functions guard their localStorage/window
// touches), so these run the REAL code — not a replica.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import {
  bindable, builtInOwner, chordMap, chordOf, conflictOf, dispatchable, displayChord, effectiveChord, resolveChord,
} from "./keybindings";

const ev = (key: string, m: Partial<{ ctrl: boolean; alt: boolean; shift: boolean; meta: boolean }> = {}) =>
  ({ key, ctrlKey: !!m.ctrl, altKey: !!m.alt, shiftKey: !!m.shift, metaKey: !!m.meta });

test("chordOf normalizes: fixed modifier order, uppercased letters, named keys kept, bare modifiers null", () => {
  assert.equal(chordOf(ev("o", { meta: true })), "Meta+O");
  assert.equal(chordOf(ev("O", { meta: true, shift: true })), "Shift+Meta+O", "canonical order: Ctrl, Alt, Shift, Meta — Apple's own \u2303\u2325\u21e7\u2318");
  assert.equal(chordOf(ev("p", { ctrl: true, alt: true, shift: true, meta: true })), "Ctrl+Alt+Shift+Meta+P");
  assert.equal(chordOf(ev("ArrowLeft", { alt: true })), "Alt+ArrowLeft");
  assert.equal(chordOf(ev("F5")), "F5");
  assert.equal(chordOf(ev("Meta", { meta: true })), null, "a held modifier is not a chord yet");
  assert.equal(chordOf(ev("Shift", { shift: true })), null);
});

test("Mod resolves per platform, only as a modifier prefix", () => {
  assert.equal(resolveChord("Mod+O", true), "Meta+O");
  assert.equal(resolveChord("Mod+O", false), "Ctrl+O");
  assert.equal(resolveChord("Mod+Shift+O", true), "Shift+Meta+O", "resolution NORMALIZES: a Shift-carrying default must equal the keydown's own spelling");
  assert.equal(resolveChord("Meta+O", false), "Meta+O", "concrete chords pass through untouched");
});

test("display: mac wears symbols with no separators, elsewhere spells the names", () => {
  assert.equal(displayChord("Mod+Shift+O", true), "⇧⌘O");
  assert.equal(displayChord("Mod+Shift+O", false), "Ctrl+Shift+O");
  assert.equal(displayChord("Alt+ArrowLeft", true), "⌥←");
  assert.equal(displayChord("Alt+ArrowLeft", false), "Alt+←");
});

test("bindable refuses what the panes own: bare typing keys and Escape in any dress", () => {
  for (const bad of ["Escape", "Enter", "Tab", "Backspace", "Delete", "A", "1"]) {
    assert.equal(bindable(bad), false, bad + " must not be bindable bare");
  }
  assert.equal(bindable("Ctrl+Enter"), true, "with a real modifier a typing key is a chord");
  assert.equal(bindable("Meta+Shift+Escape"), false, "Escape stays the universal close");
  assert.equal(bindable("F5"), true, "bare named keys are fine");
  assert.equal(bindable("Home"), true);
});

const CMDS = [
  { id: "a.one", chord: "Mod+O" },
  { id: "a.two", chord: "Mod+P" },
  { id: "a.free" },   // no default
];

test("effectiveChord: override beats default, empty override means deliberately unbound", () => {
  assert.equal(effectiveChord("a.one", "Mod+O", {}, true), "Meta+O");
  assert.equal(effectiveChord("a.one", "Mod+O", { "a.one": "Ctrl+J" }, true), "Ctrl+J");
  assert.equal(effectiveChord("a.one", "Mod+O", { "a.one": "" }, true), "", "unbound stays unbound");
  assert.equal(effectiveChord("a.free", undefined, {}, true), "", "no default, no override → nothing");
});

test("chordMap resolves the whole registry through the overrides", () => {
  const m = chordMap(CMDS, { "a.two": "Ctrl+K", "a.free": "F6" }, false);
  assert.equal(m.get("Ctrl+O"), "a.one");         // default, Mod resolved for the platform
  assert.equal(m.get("Ctrl+K"), "a.two");         // override wins
  assert.equal(m.get("Ctrl+P"), undefined, "the overridden default no longer answers");
  assert.equal(m.get("F6"), "a.free");
});

test("conflictOf names the command already holding a chord — and respects overrides", () => {
  assert.equal(conflictOf("Mod+P", "a.one", CMDS, {}, true), "a.two");
  assert.equal(conflictOf("Mod+P", "a.two", CMDS, {}, true), null, "a command never conflicts with itself");
  assert.equal(conflictOf("Mod+P", "a.one", CMDS, { "a.two": "" }, true), null,
    "an unbound command holds nothing to conflict with");
  assert.equal(conflictOf("F6", "a.one", CMDS, { "a.free": "F6" }, true), "a.free",
    "an override-held chord conflicts like a default");
});

test("dispatchable: never on repeat; modifier-less chords never fire while typing", () => {
  assert.equal(dispatchable({ ctrlKey: false, altKey: false, metaKey: false, repeat: true }, false), false);
  assert.equal(dispatchable({ ctrlKey: false, altKey: false, metaKey: false }, true), false,
    "a bare (or Shift-only) key in a composer is typing, not a command");
  assert.equal(dispatchable({ ctrlKey: false, altKey: false, metaKey: false }, false), true);
  assert.equal(dispatchable({ ctrlKey: false, altKey: false, metaKey: true }, true), true,
    "a real modifier fires regardless of focus");
});

test("builtInOwner names the behaviour a chord already belongs to — the four Alt+Arrows, both tab-bar arrows, literal Ctrl+C — and frees the rest", () => {
  for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
    assert.equal(builtInOwner("Alt+" + k, false), "Move focus between panes", k);
    assert.equal(builtInOwner("Alt+" + k, true), "Move focus between panes", k + " on a Mac");
  }
  assert.equal(builtInOwner("ArrowLeft", false), "Switch session (from the tab bar)");
  assert.equal(builtInOwner("ArrowRight", true), "Switch session (from the tab bar)");
  assert.equal(builtInOwner("Shift+Enter", false), "New line in the composer");
  assert.equal(builtInOwner("Escape", false), "Leave the composer / close a panel");
  assert.equal(builtInOwner("Ctrl+C", true), "Interrupt the session (composer)", "literal Ctrl, on a Mac too");
  assert.equal(builtInOwner("Meta+C", true), null, "⌘C is copy, not ours — and not built in");
  assert.equal(builtInOwner("Alt+Shift+ArrowLeft", false), null, "another modifier makes it a different chord");
  assert.equal(builtInOwner("Ctrl+Alt+ArrowLeft", false), null);
  assert.equal(builtInOwner("Alt+Shift+K", false), null);
  assert.equal(builtInOwner("Mod+1", true), null);
  assert.equal(builtInOwner("Shift+Alt+ArrowUp", false), null, "normalised before comparing: Shift is a real difference");
});
