// THE PANE GRAB DETECTOR's pure decision, executed (plans/pane-docking.md section 3, the empty space inside a
// pane): a press counts as a grab only on the page's own empty background (the container itself, never a card,
// a row, a control or an SVG mark under the pointer), by app; the chat has no empty background here (its
// transcript is selection territory); the press must be the primary button with no modifier (Option is the
// shell's own path). Fake targets stand in for elements: matches() and closest() are all the decision reads.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { emptyPress, forwardable, EMPTY_BY_APP, CONTROL_SEL, GRAB_HOVER_CLASS, KIT_CLASS } from "./pane-grab";

/** A fake element: `self` is what it matches (its own selectors), `inside` what some ancestor (or itself) matches. */
const fake = (self: string[], inside: string[] = []) => ({
  matches: (sel: string) => sel.split(",").map((s) => s.trim()).some((s) => self.includes(s)),
  closest: (sel: string) => (sel.split(",").map((s) => s.trim()).some((s) => self.includes(s) || inside.includes(s)) ? {} : null),
});

test("the feed: its list, columns and card lists are empty background; a card, a chip or a button inside is not", () => {
  assert.equal(emptyPress("feed", fake(["#feed-list"])), true);
  assert.equal(emptyPress("feed", fake([".feed-col-list"])), true, "below the cards");
  assert.equal(emptyPress("feed", fake(["body"])), true);
  assert.equal(emptyPress("feed", fake([".fitem"])), false, "a card (a feed item) is content");
  assert.equal(emptyPress("feed", fake(["span"], [".fitem"])), false, "text inside a card");
  assert.equal(emptyPress("feed", fake(["div"], [".ftask-group"])), false, "a card group's own box");
  assert.equal(emptyPress("feed", fake(["button"])), false);
  assert.equal(emptyPress("feed", fake([".feed-col-list"], ["[role]"])), false, "a list inside a role'd region yields");
  assert.equal(emptyPress("feed", fake(["div"])), false, "an unnamed div is not the background");
});

test("the sessions band: the SVG root and its wrap are empty; every SVG child (a lane, a bar, a mark, a label) is not", () => {
  assert.equal(emptyPress("timeline", fake(["svg"])), true);
  assert.equal(emptyPress("timeline", fake([".romp-tl-wrap"])), true);
  assert.equal(emptyPress("timeline", fake(["#host"])), true);
  assert.equal(emptyPress("timeline", fake(["rect"], ["svg *"])), false, "a bar");
  assert.equal(emptyPress("timeline", fake(["text"], ["svg *"])), false, "a lane label");
  assert.equal(emptyPress("timeline", fake(["button"])), false, "the band's controls");
});

test("the outline and the files pane: the list below the rows and the empty state; a row yields", () => {
  assert.equal(emptyPress("fleet", fake(["#fleet-list"])), true);
  assert.equal(emptyPress("fleet", fake(["#fleet-foot"])), true);
  assert.equal(emptyPress("fleet", fake(["div"], ["[data-sid]"])), false, "a session row");
  assert.equal(emptyPress("fleet", fake(["input"])), false, "the search field");
  assert.equal(emptyPress("files", fake(["#files-empty"])), true);
  assert.equal(emptyPress("files", fake(["body"])), true);
  assert.equal(emptyPress("files", fake([".fileview-body"])), false, "a file's content is selection territory");
});

test("the chat has no empty background here; unknown apps and a null target never grab", () => {
  assert.equal(EMPTY_BY_APP.chat, undefined, "the chat's grab surface stays the strip's empty run");
  assert.equal(emptyPress("chat", fake(["#content"])), false);
  assert.equal(emptyPress("settings", fake(["body"])), false);
  assert.equal(emptyPress("feed", null), false);
  assert.equal(emptyPress("feed", { matches: () => { throw new Error("bad selector"); }, closest: () => null }), false, "a throwing target is no grab");
});

test("forwardable: the primary button with no modifier; Option is the shell's own path", () => {
  const base = { button: 0, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false };
  assert.equal(forwardable(base), true);
  assert.equal(forwardable({ ...base, button: 2 }), false);
  assert.equal(forwardable({ ...base, altKey: true }), false);
  assert.equal(forwardable({ ...base, shiftKey: true }), false, "a shift press is a selection gesture");
  assert.equal(forwardable({ ...base, metaKey: true }), false);
});

test("the names the shell and the pages share", () => {
  assert.equal(KIT_CLASS, "pane-docking");
  assert.equal(GRAB_HOVER_CLASS, "pd-grab-hover");
  assert.ok(CONTROL_SEL.includes("svg *") && CONTROL_SEL.includes(".fitem") && CONTROL_SEL.includes("[data-sid]"));
});
