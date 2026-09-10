// The per-card update gate (feed-card-gate.ts), EXECUTED: every board-level input updateAskCard reads
// outside the ask object must flip the key, equal inputs must give equal keys, a quarantine card must
// never skip, and cardNeedsUpdate must fire on a new object OR a new key. A missed input here is a stale
// badge on an unchanged card, so the test walks the inputs one at a time. The gate's premise — an unchanged
// card keeps its OBJECT through the delivery path — is run against the pane shim's delta reassembly in the
// lane that owns the shim (tests/test_view_deltas.py, under node) and pinned here on federation's merge.
// Synthetic notes-api world.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { cardInputsKey, cardNeedsUpdate, type GateEnv, type GateItem } from "./feed-card-gate";

const WEB = "11111111-2222-3333-4444-555555555555";
const API = "11111111-2222-3333-4444-666666666666";
const card = (over: Partial<GateItem> = {}): GateItem => ({
  itemId: "g1", sid: WEB, name: "web", color: { bg: "#3366cc" },
  tree: [
    { kind: "ask", who: "web", whoSid: WEB },
    { kind: "handoff", who: "api", whoSid: API },
  ],
  delegTracked: [{ name: "tests" }],
  ...over,
});
const env = (over: Partial<GateEnv> = {}): GateEnv => ({
  dot: () => "",
  working: () => false,
  focusId: null,
  pinnedId: null,
  notifyOn: () => false,
  prefs: { grouped: true, collapsed: false, colormap: "aurora" },
  hostDown: () => false,
  selfHost: "TESTHOST",
  repo: () => null,
  userTodos: () => 0,
  seq: 1,
  ...over,
});

test("identical inputs give an identical key, across renders and across a fresh env object", () => {
  const it = card();
  assert.equal(cardInputsKey(it, env()), cardInputsKey(it, env()));
  assert.equal(cardInputsKey(it, env({ seq: 7 })), cardInputsKey(it, env({ seq: 9 })),
    "the per-render counter reaches only quarantine cards");
});

test("each board-level input flips the key on its own", () => {
  const it = card();
  const base = cardInputsKey(it, env());
  const flips: Record<string, Partial<GateEnv>> = {
    "the card's own working dot":      { dot: (n) => (n === "web" ? "work" : "") },
    "the card's own awaiting dot":     { dot: (n) => (n === "web" ? "await" : "") },
    "the card's own unknown ring":     { dot: (n) => (n === "web" ? "unknown" : "") },
    "a tracked delegation peer's dot": { dot: (n) => (n === "tests" ? "work" : "") },
    "a handoff recipient working":     { working: (n) => n === "api" },
    "hover focus on this card":        { focusId: "g1" },
    "a pin on this card":              { pinnedId: "g1" },
    "the bell":                        { notifyOn: () => true },
    "grouped mode":                    { prefs: { grouped: false, collapsed: false, colormap: "aurora" } },
    "the collapsed default":           { prefs: { grouped: true, collapsed: true, colormap: "aurora" } },
    "the colormap":                    { prefs: { grouped: true, collapsed: false, colormap: "viridis" } },
    "the session's host going down":   { hostDown: (sid) => sid === WEB },
    "this machine's own name":         { selfHost: "OTHERHOST" },
    "the session's GitHub repository": { repo: (sid) => (sid === WEB ? "example/notes-api" : null) },
    "the session's open user todos":   { userTodos: (sid) => (sid === WEB ? 2 : 0) },
  };
  const seen = new Set<string>([base]);
  for (const [what, over] of Object.entries(flips)) {
    const k = cardInputsKey(it, env(over));
    assert.notEqual(k, base, what + " must flip the key");
    assert.ok(!seen.has(k), what + " must not collide with another input's key");
    seen.add(k);
  }
});

test("inputs that belong to OTHER sessions leave this card's key alone", () => {
  const it = card();
  const base = cardInputsKey(it, env());
  assert.equal(cardInputsKey(it, env({ dot: (n) => (n === "api" ? "work" : "") })), base,
    "api working: not this card's session, not a tracked peer — the handoff line reads workingSet, tested separately");
  assert.equal(cardInputsKey(it, env({ focusId: "g2", pinnedId: "g2" })), base, "focus and pin on another card");
  assert.equal(cardInputsKey(it, env({ hostDown: (sid) => sid === API })), base, "another host down");
  assert.equal(cardInputsKey(it, env({ repo: (sid) => (sid === API ? "example/notes-api" : null) })), base,
    "another session's repository");
  assert.equal(cardInputsKey(it, env({ userTodos: (sid) => (sid === API ? 2 : 0) })), base,
    "another session's open user todos");
});

test("the colour echo's in-place write reaches the gate through the key (the object identity cannot carry it)", () => {
  const it = card();
  const before = cardInputsKey(it, env());
  it.color = { bg: "#cc3366" };   // feed.ts applyColorEcho writes the shared ask object in place, by design
  assert.notEqual(cardInputsKey(it, env()), before);
  assert.equal(cardNeedsUpdate({ _it: it, _ik: before }, it, cardInputsKey(it, env())), true,
    "same object, echoed colour: the card repaints");
});

test("a quarantine card never yields an equal key across renders", () => {
  const q = card({ blocked: { state: "quarantine" } });
  assert.notEqual(cardInputsKey(q, env({ seq: 1 })), cardInputsKey(q, env({ seq: 2 })));
  assert.equal(cardInputsKey(q, env({ seq: 3 })), cardInputsKey(q, env({ seq: 3 })),
    "…within one render it is stable (the counter is per render, not per call)");
  const plain = card({ blocked: { state: "permission" } });
  assert.equal(cardInputsKey(plain, env({ seq: 1 })), cardInputsKey(plain, env({ seq: 2 })),
    "an ordinary block reads no per-name colour map: it skips like any card");
});

test("cardNeedsUpdate: false for the same object under the same key; true for a copy, or for a new key", () => {
  const it = card();
  const k = cardInputsKey(it, env());
  assert.equal(cardNeedsUpdate({ _it: it, _ik: k }, it, k), false, "nothing changed: skip");
  assert.equal(cardNeedsUpdate({ _it: it, _ik: k }, { ...it }, k), true, "a new object (the kernel re-sent it): update");
  assert.equal(cardNeedsUpdate({ _it: it, _ik: k }, it, k + "|x"), true, "a board-level input changed: update");
  assert.equal(cardNeedsUpdate({}, it, k), true, "a freshly minted card has neither: update");
});

// --- the gate's premise: an unchanged card keeps its OBJECT through the delivery path -----------------
// The pane shim (kernel.py _shim) applies a `{type:"delta"}` frame itself and hands the bundle a full message,
// reusing every untouched card object and minting a new one only for a card the delta set. That is the shim's
// behaviour, so its own lane holds it: tests/test_view_deltas.py runs the kernel's shim JavaScript under node
// against the kernel's own frames (review find, 2026-09-08: a kernel edit must not break this lane, and a test
// that lifts kernel.py by source text is one that can). Federation's merge, the other hop, is pinned below.
test("federation's merge pushes each host's cards by reference (a defensive copy there would silently turn the gate into always-update)", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \(Array\.isArray\(f\.asks\)\) merged\.asks\.push\(\.\.\.f\.asks\);/);
});
