// The file viewer's quote anchoring: highlight a rendered span, and the composer's quote chip
// must carry the honest path:line origin. These pin the pure half — where an anchor lands, when it
// honestly refuses to guess, and the label the chip wears either way.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { anchorFor, quoteSrcLabel } from "./docreview";

const DOC = [
  "# Rollout plan",                                            // 1
  "",                                                          // 2
  "The judge files a verdict per build, then the card moves.", // 3
  "",                                                          // 4
  "- **Stage one** ships behind `access-read-facts-from-ddb`", // 5
  "- See [the tracker](https://example.invalid/t) for dates",  // 6
  "",                                                          // 7
  "Values live in cache_key_prefix and never expire.",         // 8
].join("\n");

test("a plain selection anchors on its source line", () => {
  const a = anchorFor(DOC, "files a verdict per build");
  assert.equal(a.line, 3);
  assert.equal(a.quote, "files a verdict per build");
});

test("a span selected out of RENDERED text still finds its marked-up source line", () => {
  // The reader shows "Stage one ships behind access-read-facts-from-ddb" — no asterisks, no backticks.
  const a = anchorFor(DOC, "Stage one ships behind access-read-facts-from-ddb");
  assert.equal(a.line, 5);
});

test("a link's label anchors on the line carrying the link", () => {
  const a = anchorFor(DOC, "See the tracker for dates");
  assert.equal(a.line, 6);
});

test("a heading anchors despite its # marker", () => {
  assert.equal(anchorFor(DOC, "Rollout plan").line, 1);
});

test("snake_case survives: underscores are not stripped from the source", () => {
  assert.equal(anchorFor(DOC, "cache_key_prefix").line, 8);
});

test("the selection's newlines and indentation are collapsed before matching", () => {
  const a = anchorFor(DOC, "  The judge\n   files a verdict  ");
  assert.equal(a.line, 3);
  assert.equal(a.quote, "The judge files a verdict");   // normalized, single-spaced
});

test("no match returns a text-only anchor, never a guessed line", () => {
  const a = anchorFor(DOC, "a sentence that is nowhere in this document at all");
  assert.equal(a.line, null);
  assert.equal(a.quote, "a sentence that is nowhere in this document at all");
});

test("an empty selection anchors nothing", () => {
  assert.deepEqual(anchorFor(DOC, "   \n  "), { quote: "", line: null });
});

test("a repeated span takes the first occurrence", () => {
  const src = "alpha beta\ngamma\nalpha beta";
  assert.equal(anchorFor(src, "alpha beta").line, 1);
});

test("a long selection falls back to its first words", () => {
  const src = "The judge files a verdict per build and then the card_id moves along.";
  // tail differs from the source (the user selected across a rendered footnote), head does not
  const a = anchorFor(src, "The judge files a verdict per build and then something else entirely happens");
  assert.equal(a.line, 1);
});

test("the chip label carries the anchored line", () => {
  assert.equal(quoteSrcLabel("docs/plan.md", DOC, "files a verdict per build"), "docs/plan.md:3");
});

test("an unanchorable selection labels the bare path — a wrong line beats none is FALSE here", () => {
  assert.equal(quoteSrcLabel("docs/plan.md", DOC, "text that appears nowhere in the doc at all zz"), "docs/plan.md");
});

test("before the file text lands there is no line to name", () => {
  assert.equal(quoteSrcLabel("docs/plan.md", null, "anything"), "docs/plan.md");
});
