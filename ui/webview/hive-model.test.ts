// Hive model invariants (plans/hive.md): the scene animates ONLY from diff events, so the
// board can never move without new information — an identical payload twice must yield zero
// events (CLAUDE.md ## Design), and a goal celebration fires exactly once per observed
// done-transition. All fixture data is synthetic (notes-api demo world, placeholder sids).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { buildSessions, diffSessions, finishedLine, foldSeenAsk, foldSeenDone, HiveSession, hiveAge, stateLine } from "./hive-model";

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

test("brief prefers the needs-input card's decision brief, then its boxed why", () => {
  // column values are the kernel's REAL vocabulary (working | needs_input | completed) —
  // the first cut of this file guessed "blocked", a value build_feed never emits
  const withBrief = buildSessions(payload({
    asks: [{ sid: SID_WEB, itemId: "g-ship", column: "needs_input", blockSummary: "Pick auth: cookie or token?" }],
  }))!;
  assert.equal(withBrief[0].brief, "Pick auth: cookie or token?");
  const withWhat = buildSessions(payload({
    asks: [{ sid: SID_WEB, itemId: "g-ship", column: "needs_input", blocked: { state: "apiError", what: "stopped on an API error" } }],
  }))!;
  assert.equal(withWhat[0].brief, "stopped on an API error");
});

test("a filed needs-you card lights the session: calm chips read awaiting", () => {
  const card = { sid: SID_WEB, itemId: "g-ship", column: "needs_input", blockSummary: "Cookie or token?" };
  for (const chip of ["ready", "awaitingBg", "working"]) {
    const out = buildSessions(payload({ webState: chip, asks: [card] }))!;
    assert.equal(out[0].state, "awaiting", `chip ${chip} + filed card → awaiting`);
    assert.equal(out[0].needsYou, true);
    assert.equal(out[0].brief, "Cookie or token?");
  }
  // chips that carry their own urgent story keep it — the card evidence outranks only calm
  for (const chip of ["blocked", "retrying", "compacting", "clearing", "interrupting", "opening"]) {
    const out = buildSessions(payload({ webState: chip, asks: [card] }))!;
    assert.equal(out[0].state, chip, `chip ${chip} keeps its own state`);
    assert.equal(out[0].needsYou, true, "…but the evidence still rides along");
  }
  // the synth placeholder for a live prompt with no goal is provisional AND needs_input —
  // it lights too, and its boxed why is the brief
  const synth = buildSessions(payload({
    webState: "ready",
    asks: [{ sid: SID_WEB, itemId: "blocked:" + SID_WEB, column: "needs_input", provisional: true,
             blocked: { state: "permission", what: "this session is stopped awaiting your approval" } }],
  }))!;
  assert.equal(synth[0].state, "awaiting");
  assert.equal(synth[0].brief, "this session is stopped awaiting your approval");
});

test("an answered card pending judgment does NOT light (rejudging/recheck)", () => {
  for (const flag of ["rejudging", "recheck"]) {
    const out = buildSessions(payload({
      webState: "working",
      asks: [{ sid: SID_WEB, itemId: "g-ship", column: "needs_input", [flag]: true, blockSummary: "answered already" }],
    }))!;
    assert.equal(out[0].state, "working", `${flag} card → chip state stands`);
    assert.equal(out[0].needsYou, false);
  }
});

test("a session the user STOPPED does not claim to be waiting on them", () => {
  // the interrupted card files under needs-you in the feed (re-engaging is the user's move,
  // the badge says why) — but its quiet is user-chosen, so the board must not wave
  // "waiting on your answer" at the person who just pressed stop
  for (const flag of ["interrupting", "interrupted"]) {
    const out = buildSessions(payload({
      webState: "ready",
      asks: [{ sid: SID_WEB, itemId: "g-ship", column: "needs_input", [flag]: true, blockSummary: "stopped by you" }],
    }))!;
    assert.equal(out[0].state, "ready", `${flag} card → chip state stands`);
    assert.equal(out[0].needsYou, false);
  }
});

test("the needs-you latch cannot flap across turn-boundary chip flips", () => {
  const card = { sid: SID_WEB, itemId: "g-ship", column: "needs_input", blockSummary: "Cookie or token?" };
  const a = buildSessions(payload({ webState: "working", asks: [card] }))!;
  const b = buildSessions(payload({ webState: "ready", asks: [card] }))!;
  assert.deepEqual(diffSessions(a, b).stateChanged, [],
    "chip working→ready under a filed card is NOT an event — awaiting holds");
  const answered = buildSessions(payload({ webState: "ready", asks: [{ ...card, rejudging: true }] }))!;
  assert.equal(diffSessions(b, answered).stateChanged.length, 1,
    "…the reply (rejudging) IS the event that releases it");
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

test("the card's state line speaks the user's terms for every chip", () => {
  const base = buildSessions(payload())![0];
  const at = (state: string, extra?: Partial<HiveSession>) =>
    stateLine({ ...base, state: state as HiveSession["state"], narration: null, ...extra }, 1000);
  assert.equal(at("working"), "working");
  assert.equal(
    stateLine({ ...base, state: "working", narration: { since: 860, toolUses: 7 } }, 1000),
    "working — 7 tools in, 2m");
  assert.equal(
    stateLine({ ...base, state: "working", narration: { since: 990, toolUses: 1 } }, 1000),
    "working — 1 tool in, 10s");
  assert.equal(at("awaiting"), "needs you — waiting on your answer");
  assert.equal(at("blocked"), "stopped on an API error");
  assert.equal(at("retrying"), "hitting API errors, retrying");
  assert.equal(at("awaitingBg"), "idle, waiting on background work");
  assert.equal(at("compacting"), "compacting its context");
  assert.equal(at("clearing"), "clearing its context");
  assert.equal(at("interrupting"), "stopping…");
  assert.equal(at("opening"), "starting up");
  assert.equal(at("ready"), "ready");
  assert.equal(at("ready", { faded: true }), "idle for a while");
});

test("hiveAge compacts like the outline's ages", () => {
  assert.equal(hiveAge(42), "42s");
  assert.equal(hiveAge(180), "3m");
  assert.equal(hiveAge(7200), "2h");
  assert.equal(hiveAge(200000), "2d");
  assert.equal(hiveAge(-5), "0s");
});

test("doneT is the newest completion event across live + archived tops", () => {
  const out = buildSessions(payload())!;
  assert.equal(out[0].doneT, 120, "g-old done at mt 120 beats archived g-arch at 60");
  assert.equal(out[1].doneT, 0, "api has no completions");
  const after = buildSessions(payload({ webDone: true }))!;
  assert.equal(after[0].doneT, 400, "g-ship completing moves the watermark to its mt");
});

test("finishedLine says what the ✓ means, with the completion's age", () => {
  const s = buildSessions(payload({ webDone: true }))![0];
  assert.equal(finishedLine(s, 520), "finished working — 2m ago");
  assert.equal(finishedLine(s, 400), "finished working — 0s ago");
});

test("foldSeenDone: history is not an event; a NEW completion latches until acked", () => {
  const before = buildSessions(payload({ webDone: false }))!;
  // first sight seeds watermarks to the current doneT — a board opening onto old
  // completions must not celebrate history (the goalDone rule, applied to the latch)
  const f0 = foldSeenDone({}, before);
  assert.deepEqual([...f0.unseen], []);
  assert.equal(f0.seen[SID_WEB], 120);
  assert.equal(f0.changed, true, "the seeding is a persistable change");
  // a completion after the seed latches…
  const after = buildSessions(payload({ webDone: true }))!;
  const f1 = foldSeenDone(f0.seen, after);
  assert.deepEqual([...f1.unseen], [SID_WEB]);
  assert.equal(f1.changed, false, "deriving the unseen set writes nothing by itself");
  // …and holds across a reload (the persisted record is the whole latch)
  const f2 = foldSeenDone(f1.seen, after);
  assert.deepEqual([...f2.unseen], [SID_WEB], "reload cannot swallow an unseen finish");
  // the ack (the user's look gesture advances the watermark) clears it for good
  const acked = { ...f1.seen, [SID_WEB]: 400 };
  assert.deepEqual([...foldSeenDone(acked, after).unseen], []);
});

test("filed asks carry their time and the live-prompt bit rides separately", () => {
  const card = { sid: SID_WEB, itemId: "g-ship", column: "needs_input", t: 900, blockSummary: "Cookie or token?" };
  const out = buildSessions(payload({ webState: "ready", asks: [card] }))!;
  assert.equal(out[0].needsYouT, 900, "the newest filed card's own time is the watermark evidence");
  assert.equal(out[0].liveAsk, false, "a filed question is not a live prompt");
  const live = buildSessions(payload({ webState: "awaiting" }))!;
  assert.equal(live[0].liveAsk, true);
  assert.equal(live[0].needsYouT, 0);
});

test("the awaiting line: a live prompt speaks in the present, a filed ask wears its age", () => {
  const card = { sid: SID_WEB, itemId: "g-ship", column: "needs_input", t: 900 };
  const filed = buildSessions(payload({ webState: "ready", asks: [card] }))![0];
  assert.equal(stateLine(filed, 900 + 7200), "needs you — asked 2h ago",
    "last night's question must not read as being asked right now");
  const live = buildSessions(payload({ webState: "awaiting" }))![0];
  assert.equal(stateLine(live, 1000), "needs you — waiting on your answer");
});

test("foldSeenAsk: a filed question is a DEBT — no first-sight seeding, shouts until looked at", () => {
  const card = { sid: SID_WEB, itemId: "g-ship", column: "needs_input", t: 900 };
  const sessions = buildSessions(payload({ webState: "ready", asks: [card] }))!;
  // unlike foldSeenDone, an unknown sid does NOT seed away: a fresh browser/reload still owes the shout
  const f0 = foldSeenAsk({}, sessions);
  assert.deepEqual([...f0.unseen], [SID_WEB], "a standing question survives any reload");
  assert.equal(f0.seen[SID_WEB], undefined, "…and nothing is written until the user actually looks");
  // the look (the ack writes the card's time) quiets it…
  const acked = { [SID_WEB]: 900 };
  assert.deepEqual([...foldSeenAsk(acked, sessions).unseen], []);
  // …until a NEWER filed ask arrives (a fresh judge verdict = new information)
  const newer = buildSessions(payload({ webState: "ready", asks: [{ ...card, t: 950 }] }))!;
  assert.deepEqual([...foldSeenAsk(acked, newer).unseen], [SID_WEB], "a new question re-arms the shout");
});

test("foldSeenDone keeps absentees for revival, bounded like the slot map", () => {
  const sessions = buildSessions(payload())!;
  const seed: Record<string, number> = { "dead-1": 50 };
  const f = foldSeenDone(seed, sessions);
  assert.equal(f.seen["dead-1"], 50, "a departed sid keeps its stamp — revival must not celebrate its past");
  const big: Record<string, number> = {};
  for (let i = 0; i < 220; i++) big["gone-" + i] = i;
  const pruned = foldSeenDone(big, sessions).seen;
  assert.ok(Object.keys(pruned).length <= 202, "absentees drop once the record outgrows the memory cap");
  assert.equal(pruned[SID_WEB], 120, "live sids always survive the prune");
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
