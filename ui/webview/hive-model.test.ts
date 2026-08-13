// Hive model invariants (plans/hive.md): the scene animates ONLY from diff events, so the
// board can never move without new information — an identical payload twice must yield zero
// events (CLAUDE.md ## Design), and a goal celebration fires exactly once per observed
// done-transition. All fixture data is synthetic (notes-api demo world, placeholder sids).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { buildSessions, diffSessions, HiveSession } from "./hive-model";

const SID_WEB = "11111111-2222-3333-4444-555555555555";
const SID_API = "66666666-7777-8888-9999-aaaaaaaaaaaa";

function payload(over?: {
  webState?: string; webDone?: boolean; webFaded?: boolean;
  asks?: any[]; ledgers?: any[];
}) {
  const ledgers = over?.ledgers ?? [
    {
      sid: SID_WEB, name: "web", color: { bg: "#88ccff", fg: "#001122" },
      status: { state: over?.webState ?? "working", faded: !!over?.webFaded },
      ledger: {
        tree: [
          { id: "g-ship", depth: 0, done: !!over?.webDone, text: "Ship the notes list page", t: 300, mt: 400 },
          { id: "g-sub", depth: 1, done: false, text: "wire the fetch", t: 320 },
          { id: "g-old", depth: 0, done: true, text: "Set up the repo", t: 100, mt: 120 },
        ],
        archivedTops: [{ id: "g-arch", depth: 0, done: true, text: "Pick a framework", t: 50, mt: 60 }],
      },
    },
    {
      sid: SID_API, name: "api", color: { bg: "#ffcc88", fg: "#221100" },
      status: { state: "ready", faded: false },
      ledger: { tree: [], archivedTops: [] },
    },
  ];
  return { type: "feed", ledgers, asks: over?.asks ?? [] };
}

test("no ledgers key → null (loader stays up); empty ledgers → empty board", () => {
  assert.equal(buildSessions({ type: "feed" }), null);
  assert.equal(buildSessions(null), null);
  assert.deepEqual(buildSessions({ type: "feed", ledgers: [] }), []);
});

test("chip states pass through; unknown or missing states fall to ready", () => {
  for (const s of ["working", "awaiting", "blocked", "retrying", "awaitingBg",
                   "compacting", "clearing", "interrupting", "opening", "ready"]) {
    const out = buildSessions(payload({ webState: s }))!;
    assert.equal(out[0].state, s);
  }
  assert.equal(buildSessions(payload({ webState: "someFutureChip" }))![0].state, "ready");
  const noStatus = payload();
  (noStatus.ledgers as any)[0].status = null;
  assert.equal(buildSessions(noStatus)![0].state, "ready");
});

test("goal = the freshest not-done top; provisional gist when no node exists yet", () => {
  const out = buildSessions(payload())!;
  assert.equal(out[0].goal, "Ship the notes list page");
  assert.equal(out[1].goal, null, "api has no open work and no provisional card");
  const prov = buildSessions(payload({
    asks: [{ sid: SID_API, provisional: true, text: "Refactor the auth flow" }],
  }))!;
  assert.equal(prov[1].goal, "Refactor the auth flow");
});

test("brief prefers the blocked card's decision brief, then its boxed why", () => {
  const withBrief = buildSessions(payload({
    asks: [{ sid: SID_WEB, itemId: "g-ship", column: "blocked", blockSummary: "Pick auth: cookie or token?" }],
  }))!;
  assert.equal(withBrief[0].brief, "Pick auth: cookie or token?");
  const withWhat = buildSessions(payload({
    asks: [{ sid: SID_WEB, itemId: "g-ship", column: "blocked", blocked: { state: "apiError", what: "stopped on an API error" } }],
  }))!;
  assert.equal(withWhat[0].brief, "stopped on an API error");
});

test("open-turn narration rides through from the working card", () => {
  const out = buildSessions(payload({
    asks: [{ sid: SID_WEB, itemId: "g-ship", column: "working", working: { since: 1234, toolUses: 7 } }],
  }))!;
  assert.deepEqual(out[0].narration, { since: 1234, toolUses: 7 });
  assert.equal(out[1].narration, null);
});

test("identical payload twice → ZERO events (the no-flap invariant)", () => {
  const a = buildSessions(payload())!;
  const b = buildSessions(payload())!;
  const d = diffSessions(a, b);
  assert.deepEqual(d, { added: [], removed: [], stateChanged: [], goalDone: [] });
});

test("first payload: everything is added, nothing 'changed' against a void", () => {
  const d = diffSessions(null, buildSessions(payload())!);
  assert.deepEqual(d.added.sort(), [SID_API, SID_WEB].sort());
  assert.equal(d.stateChanged.length, 0);
  assert.equal(d.goalDone.length, 0);
});

test("state changes carry from→to; arrivals and departures name their sid", () => {
  const a = buildSessions(payload())!;
  const b = buildSessions(payload({ webState: "awaiting" }))!;
  const d = diffSessions(a, b);
  assert.deepEqual(d.stateChanged, [{ sid: SID_WEB, from: "working", to: "awaiting" }]);
  const gone = diffSessions(b, b.filter((s: HiveSession) => s.sid !== SID_API));
  assert.deepEqual(gone.removed, [SID_API]);
});

test("goalDone fires once per observed done-transition, never for history", () => {
  const before = buildSessions(payload({ webDone: false }))!;
  const after = buildSessions(payload({ webDone: true }))!;
  assert.deepEqual(diffSessions(before, after).goalDone, [SID_WEB], "the transition fires");
  assert.deepEqual(diffSessions(after, after).goalDone, [], "…exactly once");
  // a session ARRIVING with done goals is history, not an event
  assert.deepEqual(diffSessions(null, after).goalDone, []);
});

test("a top completing straight into the archive still fires (known id, newly done)", () => {
  const before = buildSessions(payload({ webDone: false }))!;
  const archived = payload({ webDone: false });
  const tree = (archived.ledgers as any)[0].ledger;
  // the compaction sweep moved g-ship out of the live tree into archivedTops, now done
  tree.tree = tree.tree.filter((n: any) => n.id !== "g-ship" && n.id !== "g-sub");
  tree.archivedTops.push({ id: "g-ship", depth: 0, done: true, text: "Ship the notes list page", t: 300, mt: 500 });
  const after = buildSessions(archived)!;
  assert.deepEqual(diffSessions(before, after).goalDone, [SID_WEB]);
});
