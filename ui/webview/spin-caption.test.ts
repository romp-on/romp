// EXECUTES the card's spin ladder (./spin-caption). The rule used to live inline in feed.ts and be pinned
// by source regex, which is exactly how it went wrong: the "keep the decision brief visible" fix gated the
// recheck/rejudging swirl on `!briefText`, the regex pins were updated to match, and every test stayed
// green while a live card (a blocked goal being re-judged) rendered as a bare summary sitting in the
// Working column with nothing saying it was in motion or still blocked (the user 2026-07-21).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { spinFor, kindWord } from "./spin-caption";
import { distillInputs, distillText, distillPending } from "./distiller-line";

// --- THE REGRESSION: a re-judging card always spins ----------------------------------------------
// A blocked goal you replied to on the thread. The kernel moves it to Working while the reply is in
// flight (build_feed: "The 'Re-judging…' swirl rides along in Working"). Since 2026-07-22 the decision
// brief is withheld for exactly that window (./distiller-line), which makes this swirl the ONLY thing on
// the card saying it is in motion and still blocked underneath. The `↩ re-judging` chip is recheck-only,
// so gating the swirl on anything would leave the card mute.
test("a rejudging card WITH a decision brief still spins (the brief never suppresses the swirl)", () => {
  const s = spinFor({ rejudging: true, column: "working" }, false, false);
  assert.equal(s.caption, "Analyzing…", "a re-judging card must say it is being re-judged");
  assert.match(s.tip, /replied on this thread/);
  assert.equal(s.awaitingBg, false, "only the AWAITING case wears the box");
});

test("a recheck card WITH a decision brief still spins", () => {
  const s = spinFor({ recheck: true, column: "working" }, false, false);
  assert.equal(s.caption, "Analyzing…");
  assert.match(s.tip, /followed up/);
});

// Without a brief it behaved correctly all along — pin both halves so the fix can't be half-reverted.
test("recheck/rejudging spin the same whether or not a brief exists", () => {
  for (const it of [{ recheck: true }, { rejudging: true }]) {
    // distillPending true = "resolved, distiller hasn't produced its line yet" (no brief on screen);
    // false = the brief has landed. The caption is identical either way.
    assert.equal(spinFor(it, true, false).caption, spinFor(it, false, false).caption,
      "a present brief must not change the spin caption");
  }
});

// --- the rest of the ladder, in precedence order ---------------------------------------------------
test("AWAITING outranks everything and wears the box", () => {
  const s = spinFor({ awaiting: { why: "" }, recheck: true, judging: true, column: "working" }, true, false);
  assert.equal(s.caption, "Awaiting agents");
  assert.equal(s.awaitingBg, true, "the awaiting case gets the rounded box (.await-paused)");
  assert.match(s.tip, /Not on you|not on you/);
});

test("the kind words the box: 'Awaiting watch' for an armed watch, per KIND_WORD", () => {
  // the wait's CLASS in the visible label (the user 2026-08-15) — tooltips are dead on the touch PWA.
  // The kernel's "job" key reads "watch" since slice 2 (2026-09-05): the plain word for what it is
  assert.equal(spinFor({ awaiting: { why: "", kind: "job" }, column: "working" }, false, false).caption,
               "Awaiting watch");
  assert.equal(spinFor({ awaiting: { why: "", kind: "timer" }, column: "working" }, false, false).caption,
               "Awaiting timer");
  assert.equal(spinFor({ awaiting: { why: "", kind: "banana" }, column: "working" }, false, false).caption,
               "Awaiting agents", "an unknown kind falls back to the box's historic default word");
});

test("AWAITING uses the kernel's why verbatim (capitalized) when it reads 'waiting on …'", () => {
  const s = spinFor({ awaiting: { why: "waiting on 3 subagents" } }, false, false);
  assert.equal(s.caption, "Waiting on 3 subagents");
  assert.match(s.tip, /^waiting on 3 subagents\. Not on you/);
});

test("a peer wait (waitingOn chip) and a bg-TASK wait (pill) both defer — no generic awaiting box", () => {
  // the "Awaiting <peer>" chip / the "Awaiting task" pill already carry these; the box would double up.
  assert.equal(spinFor({ awaiting: { why: "x" }, waitingOn: "peer" }, false, false).caption, null);
  assert.equal(spinFor({ awaiting: { why: "x", tasks: ["t1"] } }, false, false).caption, null);
  const pw = spinFor({ awaiting: { why: "x" }, waitingOn: "peer", column: "working", sessState: "quiet" }, false, false);
  assert.ok(!pw.awaitingBg, "a peer wait never wears the awaiting box on any column — the chip carries it");
  assert.ok(!/task/.test(pw.caption || ""), "and never a bg-task caption");
});

// --- the wait's elapsed readout (the user 2026-08-23) -----------------------------------------------
// Working says how long it has been running; the awaiting states said nothing, so a wait stuck for
// hours read exactly like one seconds old (the local_misc card sat 2¾ hours with no visible age). The
// kernel now sends the wait's own event time (`since`) and the box appends the same compact duration
// the working narration wears.
test("AWAITING shows how long the wait has held when the kernel supplies its start", () => {
  const s = spinFor({ awaiting: { why: "", since: 1000 } }, false, false, 1000 + 42 * 60);
  assert.equal(s.caption, "Awaiting agents · 42m");
  // a verbatim "waiting on …" why carries it too, and past an hour it reads h+m like the narration
  const l = spinFor({ awaiting: { why: "waiting on 3 subagents", since: 1000 } }, false, false,
                    1000 + 3 * 3600 + 5 * 60);
  assert.equal(l.caption, "Waiting on 3 subagents · 3h 5m");
});

test("no since (an older kernel, an event-less wait) → no duration, never a guess", () => {
  assert.equal(spinFor({ awaiting: { why: "" } }, false, false, 5000).caption, "Awaiting agents");
  assert.equal(spinFor({ awaiting: { why: "", since: null } }, false, false, 5000).caption, "Awaiting agents");
  // and a caller that passed no clock (nowS) also stays bare — a duration needs both ends
  assert.equal(spinFor({ awaiting: { why: "", since: 1000 } }, false, false).caption, "Awaiting agents");
});

test("a PROVISIONAL working card tells the truth about its phase", () => {
  assert.equal(spinFor({ provisional: true, column: "working" }, false, false).caption, "Working…",
    "an OPEN turn is just working — the judge has nothing to classify yet");
  assert.equal(spinFor({ provisional: true, column: "working", judging: true }, false, false).caption,
    "Analyzing…", "once the turn settles the planner's pass is due");
});

test("a provisional AWAITING placeholder never reads a false 'Working…'", () => {
  // provisional + awaiting (a bg-task wait with no goal to floor) → the awaiting branch owns it
  const s = spinFor({ provisional: true, column: "working", awaiting: { why: "" } }, false, false);
  assert.equal(s.caption, "Awaiting agents");
});

test("the SETTLE GAP (turn done, verdict pending) spins on a working card", () => {
  assert.equal(spinFor({ judging: true, column: "working" }, false, false).caption, "Analyzing…");
  assert.equal(spinFor({ judging: true, column: "needs_input" }, false, false).caption, null,
    "judging only speaks for a card sitting in Working");
});

test("the SETTLE GAP tip carries the nudge hold the retired judging-stall chip used to tell", () => {
  // The user 2026-07-31: a goal held only because romp's own review is mid-flight is romp WORKING the
  // card, so the yellow Stalled chip no longer minted for it (jd.stall_why_stands screens WHY_JUDGING).
  // The story moved here, one hover deep — the tip must keep saying the hold exists and is not a stall,
  // or dropping the chip becomes dropping the information.
  const tip = spinFor({ judging: true, column: "working" }, false, false).tip;
  assert.match(tip, /[Nn]udges hold off/, "the hold is named");
  assert.match(tip, /not stuck/, "…and read as romp working, not a wedge");
});

test("DISTILLING names which line is being written", () => {
  assert.equal(spinFor({}, true, true).tip, "Writing the key takeaway…");
  assert.equal(spinFor({}, true, false).tip, "Writing the decision brief…");
  assert.equal(spinFor({}, true, true).caption, "Distilling…");
});

test("a working card carrying NO signal at all reads as unknown, never as silence", () => {
  // this pin once asserted the mute card ({caption: null}) as the ordinary case — twice updated,
  // twice wrong: first the narration (2026-08-13) gave the open turn a voice, then the floor
  // (2026-08-14) gave every remaining working-column shape one. The empty payload is the floor's
  // last resort: unknown, stilled glyph, said out loud.
  const s = spinFor({ column: "working" }, false, false);
  assert.match(s.caption || "", /unknown/);
  assert.equal(s.still, true);
});

test("recheck/rejudging outrank the settle gap and the distiller", () => {
  assert.match(spinFor({ rejudging: true, judging: true, column: "working" }, true, false).tip,
    /replied on this thread/);
  assert.match(spinFor({ recheck: true, judging: true, column: "working" }, true, false).tip,
    /followed up/);
});

// --- NEVER MUTE: withholding the line must not leave a silent card ---------------------------------
// The 2026-07-22 change rests on one claim: a card only reaches the Working column while settled if
// recheck or rejudging put it there, and both raise a caption. Execute the pair together — the exact
// composition feed.ts runs — so the claim cannot rot into a blank card.
test("a settled card displaced to Working loses its line but never its caption", () => {
  for (const displaced of [{ recheck: true }, { rejudging: true }]) {
    for (const state of ["blocked", "completed"] as const) {
      const { completed, blocked } = distillInputs(state, "working");
      assert.equal(distillText(completed, blocked, "a takeaway", "a decision brief"), "",
        `${state} + ${JSON.stringify(displaced)}: the stale line is withheld`);
      const s = spinFor({ ...displaced, column: "working" }, distillPending(completed, blocked, null, null), completed);
      assert.equal(s.caption, "Analyzing…",
        `${state} + ${JSON.stringify(displaced)}: ...and the card still says it is in motion`);
    }
  }
});

// --- wiring: feed.ts must actually call the module (the rule is useless unbound) --------------------
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("feed.ts routes the card's swirl through spinFor and keeps no inline copy of the ladder", () => {
  assert.match(FEED, /import \{ spinFor, waitedSuffix, awaitWord, groupRows, GROUP_TITLE, ROW_KIND_OF_LEGACY, type AwaitRow \} from "\.\/spin-caption";/);   // slice 2: the rows' vocabulary rides the same import
  // the elapsed readout reaches the OTHER two awaiting surfaces through the same helper: the
  // "Awaiting task" pill and the "Awaiting <peer>" chip (the user 2026-08-23)
  assert.match(FEED, /const pillWaited = waitedSuffix\(it\.awaiting && it\.awaiting\.since, Date\.now\(\) \/ 1000\);/);
  assert.match(FEED, /const woWaited = waitedSuffix\(wo\.since, Date\.now\(\) \/ 1000\);/);
  assert.match(FEED, /const spin = spinFor\(it, distillPending\(/);
  assert.match(FEED, /const spinCaption = spin\.caption, spinTip = spin\.tip, awaitingBg = spin\.awaitingBg;/);
  // the inline ladder is gone — no second, drifting copy of the rule
  assert.doesNotMatch(FEED, /spinCaption = "Analyzing…";/);
  assert.doesNotMatch(FEED, /it\.rejudging && !briefText/);
  assert.doesNotMatch(FEED, /it\.recheck && !briefText/);
  // …and it still drives the same DOM
  assert.match(FEED, /a\._awaitSpin\.style\.display = spinCaption \? "" : "none";/);
  assert.match(FEED, /a\._awaitSpin\.classList\.toggle\("await-paused", awaitingBg\);/);
  assert.match(FEED, /\} else a\._awaitWhy\.textContent = spinCaption;\n\s*a\._awaitSpin\.title = spinTip \|\| spinCaption;/);
});

// --- the working narration (the user 2026-08-13): the previously-mute ordinary working card ---------
test("an ordinary working card with its turn open narrates: tool count + running time", () => {
  const s = spinFor({ column: "working", working: { since: 1000, toolUses: 23 } },
                    false, false, 1000 + 8 * 60);
  assert.equal(s.caption, "Working — 23 tool uses · 8m");
  const one = spinFor({ column: "working", working: { since: 1000, toolUses: 1 } },
                      false, false, 1030);
  assert.equal(one.caption, "Working — 1 tool use · 0m", "singular reads as English");
  const long = spinFor({ column: "working", working: { since: 1000, toolUses: 400 } },
                       false, false, 1000 + 95 * 60);
  assert.equal(long.caption, "Working — 400 tool uses · 1h 35m", "hours split out past sixty minutes");
});

test("zero tool uses says nothing — the timer alone narrates until the first call", () => {
  // "0 tool uses" was noise (the user 2026-08-13): the count earns its place at one
  const z = spinFor({ column: "working", working: { since: 1000, toolUses: 0 } },
                    false, false, 1000 + 3 * 60);
  assert.equal(z.caption, "Working — 3m");
  const bare = spinFor({ column: "working", working: { since: null, toolUses: 0 } }, false, false);
  assert.equal(bare.caption, "Working…", "no count, no clock → the plain swirl still says in-motion");
});

test("the narration is the FLOOR — every richer story still wins", () => {
  const w = { since: 1000, toolUses: 5 };
  assert.equal(spinFor({ column: "working", working: w, judging: true }, false, false, 2000).caption,
               "Analyzing…", "the settle gap outranks narration");
  assert.equal(spinFor({ column: "working", working: w, recheck: true }, false, false, 2000).caption,
               "Analyzing…", "re-check outranks narration");
  assert.equal(spinFor({ column: "working", working: w, awaiting: { why: null } }, false, false, 2000).caption,
               "Awaiting agents", "awaiting outranks narration");
  assert.equal(spinFor({ column: "working", working: w }, true, false, 2000).caption,
               "Distilling…", "a pending distill outranks narration");
});

test("no spin off the working column — briefs/takeaways/chips carry those cards", () => {
  assert.equal(spinFor({ column: "needs_input", working: { since: 1, toolUses: 2 } }, false, false, 100).caption,
               null);
});

test("THE FLOOR IS TOTAL — a working-column card can never be mute (the user 2026-08-14)", () => {
  // Two cards sat in Working with nothing on them — a session quietly between turns — and the old
  // contract here BLESSED it ("a cache-cold card paints plain"). Every working-column shape now
  // yields a caption; when nothing is in motion the glyph stills instead of lying with a spin.
  const open = spinFor({ column: "working", sessState: "open" }, false, false, 100);
  assert.equal(open.caption, "Working…");
  assert.ok(!open.still, "an open turn spins — it IS in flight");
  const quiet = spinFor({ column: "working", sessState: "quiet" }, false, false, 100);
  assert.match(quiet.caption || "", /^Paused — nothing running right now; the session picks this back up$/);
  // plain truth, both rounds pinned: no NEXT-turn promise (2026-08-24 — the next turn may work
  // another thread) and no implied inspectable "wait" either (2026-08-29 — the user went looking
  // in the chat for one). The tooltip may promise the chase: the memo re-arm + dead-man backstop
  // landed 2026-08-27, so "romp nudges the session if this stays parked" is now simply true.
  assert.ok(!/wait/.test(quiet.caption || ""), "no invented wait to go looking for");
  assert.match(quiet.tip || "", /romp nudges the session about it/, "the chase promise — true since the dead-session round");
  assert.equal(quiet.still, true, "nothing in motion → the glyph stills (a spin would lie)");
  const unk = spinFor({ column: "working", sessState: "unknown" }, false, false, 100);
  assert.match(unk.caption || "", /unknown — this machine isn't reporting/);
  assert.equal(unk.still, true);
  assert.match(spinFor({ column: "working" }, false, false, 100).caption || "",
               /unknown/, "even a payload with NO floor field reads as unknown, never as silence");
  // the totality sweep: every combination of the ladder's inputs with column=working → a caption.
  // The ONE documented exception is awaiting-with-TASKS (not in this matrix): its pill + open list
  // carry the card, so the spin line alone goes quiet — the card is never mute (see the test above).
  const bools: (true | undefined)[] = [undefined, true];
  for (const judging of bools) for (const recheck of bools) for (const rejudging of bools)
    for (const provisional of bools) for (const dp of [false, true])
      for (const working of [undefined, { since: 50, toolUses: 2 }])
        for (const sessState of [undefined, "open", "quiet", "unknown"])
          for (const awaiting of [undefined, { why: "" }]) {
            const s = spinFor({ column: "working", judging, recheck, rejudging, provisional,
                                working, sessState, awaiting }, dp, false, 100);
            assert.notEqual(s.caption, null,
              "mute working card: " + JSON.stringify({ judging, recheck, rejudging, provisional, dp,
                                                       working: !!working, sessState, awaiting: !!awaiting }));
          }
});


test("awaiting WITH tracked tasks says it ONCE — no caption, and never the paused floor (2026-08-23)", () => {
  // Two screenshots, one day. The first: the pill above a "Paused — nothing is in motion" floor —
  // fixed by naming the first task in a caption. The second: pill + that caption + the now
  // open-by-default task list saying one wait three times. The pill and its list ARE the awaiting
  // read; the ladder stays silent for it, floors included.
  const s = spinFor({ awaiting: { why: "waiting on 2 background tasks", kind: "task",
                                  tasks: ["Notify when the release PRs settle", "suite run"] },
                      column: "working", sessState: "quiet" }, false, false);
  assert.equal(s.caption, null, "the pill + open task list are the one awaiting read");
  assert.equal(spinFor({ awaiting: { tasks: ["t"] }, column: "working", sessState: "unknown" },
                       false, false).caption, null, "the unknown floor stands down under the pill too");
  // an OPEN turn still narrates: the session working WHILE tasks run is new information
  const open = spinFor({ awaiting: { tasks: ["t"] }, column: "working", sessState: "open" }, false, false);
  assert.equal(open.caption, "Working…");
  // …and so do the richer in-motion stories (a re-judge under an awaiting pill is worth a line)
  const rj = spinFor({ awaiting: { tasks: ["t"] }, rejudging: true, column: "working" }, false, false);
  assert.equal(rj.caption, "Analyzing…");
});

// --- T225 rider (the user 2026-09-02): the kind word agrees in NUMBER, from one count -------------
test("kindWord: exactly one agent is 'agent'; two or more are 'agents'", () => {
  assert.equal(kindWord("agents", 1), "agent");
  assert.equal(kindWord("agents", 2), "agents");
  assert.equal(kindWord("agents", 7), "agents");
});

test("kindWord: the other kinds pluralize by count too — in the plain words of slice 2 (2026-09-05)", () => {
  // the kernel's KEYS stay (task / job); the WORDS are what the thing is: a background command, an armed
  // watch. "watch" pluralizes to "watches", not "watchs".
  assert.equal(kindWord("task", 1), "command");
  assert.equal(kindWord("task", 3), "commands");
  assert.equal(kindWord("job", 1), "watch");
  assert.equal(kindWord("job", 2), "watches");
  assert.equal(kindWord("peer", 1), "peer");
  assert.equal(kindWord("peer", 2), "peers");
  assert.equal(kindWord("timer", 4), "timers");
  assert.equal(kindWord("mixed", 4), "", "several kinds at once have no word — the number is the label");
});

test("kindWord: an unknown count keeps the surfaces' historic default; an unknown kind stays agents", () => {
  assert.equal(kindWord("agents", null), "agents");
  assert.equal(kindWord("agents", undefined), "agents");
  assert.equal(kindWord("task", null), "command");
  assert.equal(kindWord(null, 1), "agent");
  assert.equal(kindWord("", 2), "agents");
  assert.equal(kindWord("nonsense", 2), "agents");
});

test("the spin caption derives its word from the kernel's count", () => {
  const one = spinFor({ awaiting: { why: "", kind: "agents", count: 1 }, column: "working" }, false, false);
  assert.equal(one.caption, "Awaiting agent");
  const two = spinFor({ awaiting: { why: "", kind: "agents", count: 2 }, column: "working" }, false, false);
  assert.equal(two.caption, "Awaiting agents");
  const legacy = spinFor({ awaiting: { why: "", kind: "agents" }, column: "working" }, false, false);
  assert.equal(legacy.caption, "Awaiting agents", "an older kernel with no count reads as before");
});
