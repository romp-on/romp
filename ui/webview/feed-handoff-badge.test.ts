// Sender-side handoff provenance on the feed (the user 2026-08-24): a TOP-LEVEL "↪ delegated to
// <peer>" tracking node wore its provenance as the card TITLE, arrow and all. The kernel now titles
// the card with the WORK and ships the delegation as `handoffTo` — rendered on the origin slot as
// the exact mirror of the recipient's "↪ from <peer>" badge: identity color, quiet host: prefix for
// a federated recipient, click opens the recipient session, STACKING after an ↪ from badge (origin
// and handoffTo are different facts about one card). Source pins, the feed-panel idiom; the
// kernel-side truth table lives in tests/test_handoff_card_title.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const BLK = SRC.slice(SRC.indexOf("if (it.handoffTo && it.handoffTo.peerSid) {"),
                      SRC.indexOf("a._time.textContent"));

test("the badge is additive on the type — a payload without it renders exactly as before", () => {
  assert.match(SRC, /handoffTo\?: \{ peer: string; peerSid: string; peerHost\?: string; color\?: \{ bg: string; fg: string \} \| null \} \| null;/);
});

test("the title is the kernel-shipped text verbatim — the de-arrowing lives kernel-side", () => {
  assert.match(SRC, /a\._title\.textContent = it\.text;/,
    "no client-side munging: one place (kernel _handoff_card_fields) owns the derivation");
});

test("the badge mirrors ↪ from: identity rendering, recipient click, stacking after origin", () => {
  assert.ok(BLK.length > 0, "the handoffTo render block exists");
  // stacks after an ↪ from badge rather than replacing it — same rule the tracked slot pinned
  assert.match(BLK, /const hadOrigin = !!\(it\.origin && it\.origin\.peer\);/);
  assert.match(BLK, /pre\.textContent = \(hadOrigin \? " · " : ""\) \+ "↪ delegated to ";/);
  // identity rendering matches every other session name (quiet host: prefix included)
  assert.match(BLK, /peer\.replaceChildren\(\.\.\.hostPartsNodes\(it\.handoffTo\.peerHost, it\.handoffTo\.peer\)\);/);
  assert.match(BLK, /peer\.style\.color = it\.handoffTo\.color\.bg;/);
  // the click opens the RECIPIENT session — symmetric with the origin badge's click
  assert.match(BLK, /openSession", id: it\.handoffTo!\.peerSid/);
});
