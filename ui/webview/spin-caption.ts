// The card's SPIN line — the romp swirl + a short caption in the card body saying what is in motion —
// computed in ONE place so a test can EXECUTE the ladder instead of regexing feed.ts's source.
//
// It lives here for the same reason distiller-line.ts does: this rule was pinned by source regex, the
// regex was updated alongside a wrong change, and nothing caught it. Concretely (the user 2026-07-21):
// the "keep the decision brief visible" fix (97ff203) gated the recheck/rejudging swirl on `!briefText`,
// so a blocked card that its session keeps re-judging showed its brief and NOTHING ELSE — sitting in the
// Working column with no sign it was in motion and no sign it was still blocked underneath. The card read
// as "a working card that inexplicably has a summary". The brief and the swirl are SIBLING elements
// (feed.ts appends `secs` then `awaitSpin`), so they were never in competition: the flicker the guard was
// aimed at came from keying the LINE on `column`, and distillState already fixed that. Both show now.
//
// THE CONTRACT (spin-caption.test.ts executes every branch, in order):
//   1. AWAITING      — held in Working on dispatched/delegated work (no peer chip). With tracked
//                      tasks there is NO caption at all (2026-08-23): the "Awaiting task" pill and
//                      its open-by-default list are the one awaiting read, and the quiet/unknown
//                      floors stand down under them (branch 8) — a caption here said the same wait
//                      a third time. A wait on PEERS renders their names in identity colour
//                      (feed.ts reads awaiting.peers; the caption is the colourless fallback).
//   2. PROVISIONAL   — a dashed live-prompt placeholder the planner hasn't classified yet
//   3. RE-CHECK      — a soft-block answered with a TARGETED follow-up, pending re-judge
//   4. RE-JUDGING    — a soft-block + a PLAIN thread reply, with the reply in flight
//   5. SETTLE GAP    — the turn finished, the closer's verdict hasn't landed
//   6. DISTILLING    — a resolved card whose takeaway/brief hasn't been written yet
//   7. NARRATION     — the ordinary working card with its turn open: live tool count + duration
//   8. THE FLOOR     — any OTHER working-column card, by sessState — except quiet/unknown under an
//                      Awaiting-task pill, which return NO caption (see 1): "open" (turn running, narration
//                      not reported — an older/disconnected kernel) spins a plain Working…; "quiet"
//                      (between turns) and "unknown" (no signal at all) render a STILLED glyph + a
//                      line saying exactly that. Total: a working-column card can never be mute
//                      (the user 2026-08-14 — two cards sat in Working with nothing on them).
//   … no spin only OUTSIDE the working column (briefs/takeaways/chips carry those cards).
// 3 and 4 do NOT depend on whether a brief exists. That independence matters more since 2026-07-22, when
// the brief stopped showing on a card displaced to Working at all (see ./distiller-line): these two are the
// only branches that fire in that window, so the swirl is the sole thing saying the card is in motion and
// still blocked underneath. Gating either on a brief would leave it silent.

/** The card fields the ladder reads. Structural, so the test can pass plain objects. */
export interface SpinItem {
  awaiting?: { why?: string | null; kind?: string | null; since?: number | null; tasks?: unknown[] | null } | null;   // since = the wait's own event time (kernel or-chain / _session_awaiting; the user 2026-08-23)
  waitingOn?: unknown;
  provisional?: boolean;
  column?: string;
  judging?: boolean;
  recheck?: boolean;
  rejudging?: boolean;
  blocked?: unknown;
  working?: { since?: number | null; toolUses?: number | null } | null;   // open-turn narration (kernel _open_turn_progress; the user 2026-08-13)
  sessState?: string | null;   // the kernel's floor disposition: "open" | "quiet" | "unknown" (the user 2026-08-14)
}

/** caption: the body line, or null for no spin. tip: the fuller hover explanation. awaitingBg: the
 *  AWAITING case, which additionally wears the rounded box (`.await-paused`) as its distinct read. */
export interface Spin {
  caption: string | null;
  tip: string;
  awaitingBg: boolean;
  still?: boolean;   // the at-rest floor: glyph present but NOT spinning — spin reads as in-flight, and quiet/unknown are states of rest
}

const NONE: Spin = { caption: null, tip: "", awaitingBg: false };

/** The awaiting KIND's one label word (kernel jd.AWAIT_KINDS; the user 2026-08-15). Kindless (an older
 *  kernel, an untyped legacy stamp) falls back to "agents" — the word this box has always defaulted to. */
export const KIND_WORD: Record<string, string> = {
  agents: "agents", task: "task", job: "job", peer: "peer", timer: "timer",
};

/** dCompleted/dBlocked come from distillInputs(distillState, column) — the GENUINE resolution state, not
 *  the transient column. distillPending is passed in (rather than recomputed) so the two modules keep one
 *  owner for the "is the distiller still working" rule. */
/** Compact duration for the working narration AND the awaiting box: minutes under an hour, then h+m. */
export function workingFor(secs: number): string {
  const m = Math.max(0, Math.floor(secs / 60));
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** " · 42m" for a wait with a known start, "" otherwise — the awaiting counterpart of the working
 *  narration's duration (the user 2026-08-23: Working says how long it's been running, the awaiting
 *  states said nothing, so a wait stuck for hours read the same as one seconds old). Kernel-supplied
 *  event time only — no since, no duration, never a guess. */
export function waitedSuffix(since: number | null | undefined, nowS: number | undefined): string {
  return nowS && since && since > 0 ? " · " + workingFor(nowS - since) : "";
}


export function spinFor(it: SpinItem, distillPending: boolean, dCompleted: boolean, nowS?: number): Spin {
  const aw = it.awaiting;
  // a bg-TASK wait no longer boxes its why here (the user 2026-07-13): the compact "Awaiting task" pill
  // on the toggles row carries it (with the task list one click away, like Sub-goals) — see applySections
  const awTasks = ((aw && aw.tasks) || []).filter(Boolean);
  // A bg-TASK wait says it ONCE (the user 2026-08-23, second screenshot of the day): the "Awaiting
  // task" pill + its open-by-default list are the whole read. The first cut of today's fix named the
  // first task in a caption here — and with the list now open by default, one card said the same
  // wait three times (pill, caption, list row). No caption, then; the floor guards below keep the
  // quiet/unknown lines from contradicting the pill instead (that contradiction is what the caption
  // was for).
  if (aw && !it.waitingOn && !awTasks.length) {
    // AWAITING — the session is held, waiting on work it dispatched. It keeps its own read: a boxed
    // "Awaiting <kind-word>" label, the kind carried as DATA from the kernel (the user 2026-08-15) so
    // the box says WHAT is awaited — agents, a job on a cluster, a timer — not one word for five
    // states. The romp swirl SPINS here too (the user 2026-07-04: a spin reads as "in flight, not
    // stalled", which is exactly the awaiting state — the box already distinguishes it from the
    // actively-working cases, so the glyph needn't also freeze). A why that already leads with
    // "waiting on" is shown verbatim (capitalized); the kind word is the fallback frame.
    const why = aw.why || "";
    const word = KIND_WORD[aw.kind || ""] || "agents";   // kindless = the box's historic default
    // how long the wait has held, from the kernel's event time — the same live readout the working
    // narration wears, so a stuck wait is visible at a glance (the user 2026-08-23)
    const waited = waitedSuffix(aw.since, nowS);
    return {
      caption: (/^waiting on/i.test(why) ? why.charAt(0).toUpperCase() + why.slice(1)
                                         : "Awaiting " + word) + waited,
      tip: why ? why + ". Not on you; paused until the background work lands."
               : "Paused, waiting on background work it dispatched (not on you). Clears when the result lands.",
      awaitingBg: true,
    };
  }
  if (it.provisional && it.column === "working" && !aw) {
    // the chip tells the truth about the phase (the user 2026-07-12): an OPEN turn is just Working — the
    // judge has nothing to classify yet; once the turn settles (kernel `judging`) the planner's pass is
    // due/in flight and only THEN does the chip say Analyzing…. An AWAITING placeholder (a bg-task wait with
    // no goal to floor, the user 2026-07-13) is provisional too but NOT working: !aw defers it to the boxed
    // why (branch above) or, when tasks exist, to the "Awaiting task" pill — never a false "Working…".
    return {
      caption: it.judging ? "Analyzing…" : "Working…",
      tip: it.judging
        ? "This stretch of work finished; the judge is sorting it into a goal."
        : "A new prompt, still running. Sorted into a goal once this stretch of work finishes.",
      awaitingBg: false,
    };
  }
  if (it.recheck) {
    // RE-CHECK — a soft-block you answered with a TARGETED follow-up, moved to Working and de-urgented
    // (dashed) until the judge re-judges. It rides ALONGSIDE the decision brief, which stays on screen
    // the whole time (the user 2026-07-21): the brief says what it is blocked on, the swirl says the
    // judge is looking at it again. Suppressing one for the other left the card unreadable.
    return {
      caption: "Analyzing…",
      tip: "You followed up. Reopened to Working; the judge will resolve it or re-block it.",
      awaitingBg: false,
    };
  }
  if (it.rejudging) {
    // RE-JUDGING — a soft-block + a PLAIN thread reply, with a turn now in flight. The kernel moves this
    // card to Working the instant you hit send (kernel build_feed: "The 'Re-judging…' swirl rides along in
    // Working"), so the swirl is the ONLY thing telling you the card is still blocked underneath and that
    // the block is being re-evaluated — the `↩ re-judging` chip covers `recheck` only.
    return {
      caption: "Analyzing…",
      tip: "You replied on this thread. Moved to Working while the reply runs; it comes back if the judge re-confirms the block.",
      awaitingBg: false,
    };
  }
  if (it.judging && it.column === "working") {
    // SETTLE GAP (the user 2026-07-13) — the session FINISHED its turn but the closer's verdict hasn't
    // landed, so the card would sit inertly in Working ("the session is done, why is its card still
    // working?"). The swirl says what's actually happening; it hands off to the column move (and then
    // Distilling…) when the verdict files the work. The tip also carries the story the retired
    // judging-stall chip used to tell (the user 2026-07-31): auto-nudges hold off while the review
    // runs, and that hold is romp working the card, not a stall — so it lives here, one hover deep,
    // instead of as a yellow chip pulling the eye to a state nobody needs to act on.
    return {
      caption: "Analyzing…",
      tip: "This stretch of work finished; the judge is deciding whether it completed or blocked this goal. "
         + "Nudges hold off while the review runs — romp is working this card, not stuck on it.",
      awaitingBg: false,
    };
  }
  if (distillPending) {
    // DISTILLING (the user 2026-06-29) — a resolved card whose distiller hasn't produced its line yet:
    // a completed goal awaiting its takeaway (summary), or a blocked goal awaiting its decision brief
    // (blockSummary). The same swirl spins in the distiller-line spot until the line lands, so a card that
    // "is in motion" (the distiller LLM is running) reads as busy rather than blank. Excludes a live
    // permission/picker block (on YOU) — see distillPending in ./distiller-line.
    return {
      caption: "Distilling…",
      tip: dCompleted ? "Writing the key takeaway…" : "Writing the decision brief…",
      awaitingBg: false,
    };
  }
  if (it.working && it.column === "working") {
    // WORKING NARRATION (the user 2026-08-13) — the ordinary working card with its turn open used to
    // be the ONE mute case ("no spin"). Now it says what is actually happening: the open turn's tool
    // count and how long it has been running, both live — a frozen count under a climbing timer is
    // how a silent regression becomes visible at a glance. Every richer story above (awaiting,
    // provisional, re-check, re-judging, the settle gap, distilling) still wins; this is the floor.
    const n = it.working.toolUses || 0;
    const dur = nowS && it.working.since ? workingFor(nowS - it.working.since) : "";
    // zero tool uses says nothing worth reading ("0 tool uses" was noise — the user 2026-08-13):
    // the count appears once there is one, and until then the timer alone carries the narration
    const parts = [n >= 1 ? `${n} tool ${n === 1 ? "use" : "uses"}` : "", dur].filter(Boolean);
    return {
      caption: parts.length ? `Working — ${parts.join(" · ")}` : "Working…",
      tip: "The open turn's live progress: tool calls made so far, and how long this stretch has been "
         + "running. If the count freezes while the timer climbs, something is worth a look.",
      awaitingBg: false,
    };
  }
  if (it.column === "working") {
    // THE FLOOR IS TOTAL (the user 2026-08-14): two cards sat in Working with nothing on them.
    // The narration above only rides when the kernel parsed an OPEN turn, so a session quietly
    // between turns, or a machine that isn't reporting at all (an older or briefly disconnected
    // kernel, a cold parse cache), rendered a MUTE card. Every working-column card now says its
    // state; when nothing is in motion the glyph STILLS (`still`) — a spinning swirl on a quiet
    // card would claim work that isn't happening.
    // …except under an Awaiting-task pill (the user 2026-08-23): the pill + its open list already
    // say what this card is doing, and the quiet/unknown lines below would either contradict them
    // ("Paused — nothing is in motion" under in-flight tasks — the morning's screenshot) or repeat
    // them. An OPEN turn still narrates: the session working WHILE tasks run is new information.
    if (awTasks.length && it.sessState !== "open") return NONE;
    if (it.sessState === "open") {
      return {
        caption: "Working…",
        tip: "The turn is running, but this machine isn't reporting live progress (an older or "
           + "briefly disconnected kernel sends none). The card updates the moment it does.",
        awaitingBg: false,
      };
    }
    if (it.sessState === "quiet") {
      return {
        // plain truth (the user 2026-08-24): the old line promised the session's NEXT turn, but the
        // next turn may work another thread entirely — this goal resumes when its own wait ends
        caption: "Paused — resumes when its wait ends",
        tip: "Nothing is in motion right now: the session is between turns and this goal stays open. "
           + "It picks back up when the session returns to this thread — not necessarily on its very "
           + "next turn.",
        awaitingBg: false,
        still: true,
      };
    }
    return {
      caption: "State unknown — this machine isn't reporting",
      tip: "No live signal from this session's kernel (a cold cache, or a machine that is offline or "
         + "reconnecting). The card updates the moment a signal lands.",
      awaitingBg: false,
      still: true,
    };
  }
  return NONE;
}
