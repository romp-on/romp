// Hive model — feed payload in, session snapshots + DIFF EVENTS out (pure; tested by
// hive-model.test.ts). The scene animates ONLY from these events, never by re-deriving per
// push: an identical payload twice yields zero events, so nothing on the board can move
// without new information (CLAUDE.md ## Design). The state values are the kernel's shared
// session chip (_session_chip) as it rides ledgers[].status.state.

export type HiveState =
  | "opening" | "working" | "awaiting" | "blocked" | "retrying"
  | "awaitingBg" | "compacting" | "clearing" | "interrupting" | "ready";

const STATES: ReadonlySet<string> = new Set<string>([
  "opening", "working", "awaiting", "blocked", "retrying",
  "awaitingBg", "compacting", "clearing", "interrupting", "ready",
]);

export interface HiveColor { bg: string; fg: string }

export interface HiveSession {
  sid: string;
  name: string;
  color: HiveColor | null;
  state: HiveState;
  faded: boolean;                 // ready for >1h — the doze cue
  goal: string | null;            // what it's on: the freshest open top goal, else the provisional gist
  brief: string | null;           // why it needs you: the blocked card's decision brief / boxed why
  narration: { since: number; toolUses: number } | null;   // open-turn progress (working cards)
  topIds: string[];               // every known top-level goal id (live + archived)
  doneTopIds: string[];           // the done subset — goalDone transition detection
  needsYou: boolean;              // a filed needs-you card exists (see buildSessions) — why state may read "awaiting" beyond the live-prompt chip
  doneT: number;                  // the LATEST completion event across its top goals (0 = none) — the unseen-finished watermark
}

export interface HiveDiff {
  added: string[];                          // sids that appeared
  removed: string[];                        // sids that left
  stateChanged: { sid: string; from: HiveState; to: HiveState }[];
  goalDone: string[];                       // sids where a KNOWN top goal newly completed
}

interface LedgerNodeIn {
  id?: string; depth?: number; done?: boolean; cleared?: boolean;
  text?: string; t?: number; mt?: number; current?: boolean;
}

function normState(v: unknown): HiveState {
  return (typeof v === "string" && STATES.has(v) ? v : "ready") as HiveState;
}

function topNodes(ledger: any): { live: LedgerNodeIn[]; archived: LedgerNodeIn[] } {
  const tree: LedgerNodeIn[] = Array.isArray(ledger?.tree) ? ledger.tree : [];
  const arch: LedgerNodeIn[] = Array.isArray(ledger?.archivedTops) ? ledger.archivedTops : [];
  return {
    live: tree.filter((n) => n && n.depth === 0 && typeof n.id === "string"),
    archived: arch.filter((n) => n && n.depth === 0 && typeof n.id === "string"),
  };
}

// Build the per-session snapshots from one feed payload. Returns null when the payload
// carries no `ledgers` yet (cold build still running) — the caller keeps its loader up,
// exactly like the outline pane does.
export function buildSessions(msg: any): HiveSession[] | null {
  if (!msg || !Array.isArray(msg.ledgers)) return null;
  const asks: any[] = Array.isArray(msg.asks) ? msg.asks : [];
  const bySid = new Map<string, any[]>();
  for (const a of asks) {
    if (!a || typeof a.sid !== "string") continue;
    const l = bySid.get(a.sid); if (l) l.push(a); else bySid.set(a.sid, [a]);
  }
  const out: HiveSession[] = [];
  for (const m of msg.ledgers) {
    if (!m || typeof m.sid !== "string") continue;
    const cards = bySid.get(m.sid) || [];
    const { live, archived } = topNodes(m.ledger);
    // the freshest not-done, not-cleared top = "what it's on"; a session on a brand-new,
    // not-yet-classified prompt has no node — its provisional card carries the live gist
    let goal: string | null = null, goalT = -1;
    for (const n of live) {
      if (n.done || n.cleared) continue;
      const t = (n.current ? Number.MAX_SAFE_INTEGER : (n.mt ?? n.t ?? 0)) || 0;
      if (t > goalT) { goalT = t; goal = (n.text || "").trim() || null; }
    }
    if (goal === null) {
      const prov = cards.find((a) => a.provisional && typeof a.text === "string" && a.text.trim());
      if (prov) goal = prov.text.trim();
    }
    // needs-you: a card FILED under needs_input (a judge verdict, a floored live prompt, a
    // synth placeholder) — the feed's own column vocabulary, not the "blocked" guess this
    // code first shipped with (a column value the kernel never emits). rejudging/recheck
    // cards are excluded: the user already answered those, the judges own the next move —
    // counting them would wave ❗ at a person who owes nothing (and would clear again on
    // the verdict, a move with no new information for the user).
    let needsYou = false;
    // why it needs you — the blocked card's own copy, briefest honest form first
    let brief: string | null = null;
    for (const a of cards) {
      const filed = a.column === "needs_input" && !a.rejudging && !a.recheck;
      if (filed) needsYou = true;
      // provisional cards are placeholders EXCEPT the needs-input one (a live prompt with
      // no goal to floor) — its boxed why is exactly the brief for that state
      if (a.provisional && !filed) continue;
      const b = (typeof a.blockSummary === "string" && a.blockSummary.trim())
        || (a.blocked && typeof a.blocked.what === "string" && a.blocked.what.trim())
        || (a.awaiting && typeof a.awaiting.why === "string" && a.awaiting.why.trim()) || "";
      if (b && filed) { brief = b; break; }
      if (b && !brief) brief = b;
    }
    // open-turn narration rides the working card (kernel _open_turn_progress)
    let narration: { since: number; toolUses: number } | null = null;
    for (const a of cards) {
      const w = a && a.working;
      if (w && typeof w === "object" && typeof w.toolUses === "number") {
        narration = { since: Number(w.since) || 0, toolUses: w.toolUses };
        break;
      }
    }
    const topIds = [...live, ...archived].map((n) => n.id as string);
    const doneTopIds = [...live.filter((n) => !!n.done), ...archived].map((n) => n.id as string);
    // the newest completion event among its tops (archived tops are all done by definition):
    // mt is the judge's done-write, t the node's birth — the same freshness read the goal
    // pick above uses. This is the "finished working" watermark the unseen-done latch compares.
    let doneT = 0;
    for (const n of [...live.filter((n) => !!n.done), ...archived]) {
      doneT = Math.max(doneT, (n.mt ?? n.t ?? 0) || 0);
    }
    let state = normState(m.status && m.status.state);
    // The board must LIGHT UP whenever the session has a question filed for the user (the
    // user 2026-08-13: a session with a question showed a calm pad) — the chip alone only
    // covers the LIVE prompt ("awaiting"); a session that ended its turn by asking, or has
    // one goal blocked on you while it works another, reads ready/working there. The filed
    // card is the deciding event (a judge verdict / floor), so this can't flap between
    // builds: it latches "awaiting" across turn-boundary chip flips (working↔ready) until
    // the verdict that retires the card. Chip states with their own urgent story — blocked,
    // retrying, the context ops, opening — keep it; the card evidence outranks only calm.
    if (needsYou && (state === "ready" || state === "awaitingBg" || state === "working")) {
      state = "awaiting";
    }
    out.push({
      sid: m.sid,
      name: typeof m.name === "string" ? m.name : m.sid.slice(0, 8),
      color: m.color && typeof m.color.bg === "string" ? { bg: m.color.bg, fg: m.color.fg || "#000" } : null,
      state,
      faded: !!(m.status && m.status.faded),
      goal, brief, narration, needsYou, doneT,
      topIds: [...new Set(topIds)].sort(),
      doneTopIds: [...new Set(doneTopIds)].sort(),
    });
  }
  return out;
}

// Compact age for the card's state line ("2m", "1h") — mirrors the outline's agehms.
export function hiveAge(secs: number): string {
  secs = Math.max(0, Math.floor(secs));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

// The card's one-line state, in the user's terms (never romp nouns): what the session is
// doing and, when it matters, for how long. Pure so every phrasing is tested.
export function stateLine(s: HiveSession, now: number): string {
  switch (s.state) {
    case "working": {
      const n = s.narration;
      return n ? `working — ${n.toolUses} tool${n.toolUses === 1 ? "" : "s"} in, ${hiveAge(now - n.since)}`
               : "working";
    }
    // covers the live prompt AND a filed question (needsYou): the session may even still be
    // working a sibling goal, so "waiting on your answer" — never "stopped" — stays true
    case "awaiting": return "needs you — waiting on your answer";
    case "blocked": return "stopped on an API error";
    case "retrying": return "hitting API errors, retrying";
    case "awaitingBg": return "idle, waiting on background work";
    case "compacting": return "compacting its context";
    case "clearing": return "clearing its context";
    case "interrupting": return "stopping…";
    case "opening": return "starting up";
    default: return s.faded ? "idle for a while" : "ready";
  }
}

// The unseen-finished tip/card line: what the ✓ over the bean means, with the age of the
// completion it is holding for you (the user 2026-08-14, who wanted "finished working"
// feedback that waits until they have looked).
export function finishedLine(s: HiveSession, now: number): string {
  return `finished working — ${hiveAge(now - s.doneT)} ago`;
}

// ── the unseen-finished latch ─────────────────────────────────────────────────────────────
// A finished session used to drop straight to a calm blue pad — indistinguishable from one
// idle all day — with only the one-shot goalDone confetti marking the moment (missed unless
// you were watching). The latch: per sid, the completion watermark the user has LOOKED at;
// a session whose doneT is past it wears the finished cue until an actual look gesture
// (their click through to it) advances the watermark. Every transition is an event — a judge
// filing done (doneT moves) arms it, the user's own gesture clears it — so it cannot flap,
// and the record persists (the caller keeps it in localStorage) so a reload can't silently
// swallow a finish. First sight of a sid seeds its watermark to the current doneT: arriving
// with old completions is HISTORY, not an event — the exact rule diffSessions applies to
// goalDone. Absent sids keep their stamps (a revived session must not celebrate its past)
// until the record outgrows the same 200-entry memory the slot map keeps.
export type SeenDone = Record<string, number>;

export function foldSeenDone(prev: SeenDone, sessions: HiveSession[]):
    { seen: SeenDone; unseen: Set<string>; changed: boolean } {
  const seen: SeenDone = { ...prev };
  const unseen = new Set<string>();
  let changed = false;
  const live = new Set<string>();
  for (const s of sessions) {
    live.add(s.sid);
    if (!(s.sid in seen)) { seen[s.sid] = s.doneT; changed = true; }
    else if (s.doneT > seen[s.sid]) unseen.add(s.sid);
  }
  let extra = Object.keys(seen).length - 200;
  if (extra > 0) {
    for (const k of Object.keys(seen)) {
      if (extra <= 0) break;
      if (!live.has(k)) { delete seen[k]; changed = true; extra--; }
    }
  }
  return { seen, unseen, changed };
}

// The event stream between two snapshots. `prev` null means "first payload": everything is
// `added`, and no state/goal events fire (there is no earlier world to compare against).
export function diffSessions(prev: HiveSession[] | null, next: HiveSession[]): HiveDiff {
  const d: HiveDiff = { added: [], removed: [], stateChanged: [], goalDone: [] };
  const pm = new Map((prev || []).map((s) => [s.sid, s] as const));
  const nm = new Map(next.map((s) => [s.sid, s] as const));
  for (const s of next) if (!pm.has(s.sid)) d.added.push(s.sid);
  if (prev) {
    for (const s of prev) if (!nm.has(s.sid)) d.removed.push(s.sid);
    for (const s of next) {
      const p = pm.get(s.sid);
      if (!p) continue;
      if (p.state !== s.state) d.stateChanged.push({ sid: s.sid, from: p.state, to: s.state });
      // goalDone: a top the previous world already KNEW (id in p.topIds) moved into the done
      // set — the observed transition, once per completion, never re-derived (the outline's
      // seenDone pattern). A brand-new id arriving already-done is history, not an event.
      const prevDone = new Set(p.doneTopIds), prevKnown = new Set(p.topIds);
      if (s.doneTopIds.some((id) => !prevDone.has(id) && prevKnown.has(id))) d.goalDone.push(s.sid);
    }
  }
  return d;
}
