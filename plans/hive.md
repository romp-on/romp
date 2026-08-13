# Hive — a game-feel command view of your sessions

A fifth dashboard pane: a 3D honeycomb diorama, one hex pad per session, a small
friendly character on each pad acting out what that session is doing right now.
Halo-Wars-style tactical camera; click a hex to fly in, read the gist, and talk
to that session.

**Art direction (the user, 2026-08-13):** the world is TRON — near-black glossy
ground with a faint accent grid, pads as dark slabs whose status color is their
glowing rim, bloom doing the neon work — and the characters are CUTE AND BLOBBY
(Fall Guys / PEAK): session-colored bean bodies with squash-and-stretch, dark
glossy visors, glowing eyes, stubby arms. Not angular low-poly fantasy (a KayKit
adventurers direction was tried and rejected). Free CC0 assets are allowed if
one genuinely matches this look; the procedural bean is the current build. The aim is the Philosophy's "spend attention, don't drain it"
rendered literally: the whole board is glanceable posture + color, and every
mechanic is one click deeper.

## Why a game view earns its place

- **Glanceable by default.** Posture is pre-attentive: a slumped character reads
  as "broken" faster than a red badge. State is DOUBLE-encoded (silhouette +
  status color) so it survives color-blindness and small sizes.
- **Interrupt only when the human is the bottleneck.** Exactly one state gets a
  look-at-me animation (waving/jumping + ❗): `awaiting` — a live permission or
  picker prompt, the one state that IS the user's bottleneck. Everything else is
  calm ambient motion.
- **Make re-engagement cheap.** The fly-in card leads with the outcome language
  the feed already computes (goal text, decision brief, open-turn narration) —
  never play-by-play.
- **Never lose the thread.** Hex slots are stable: a session keeps its hex for
  its whole life (persisted), so spatial memory works like a real base layout.

## Architecture (all decisions verified against the tree, 2026-08-13)

- **Pane, not page.** `#hive-pane` + `<iframe id=f-hive src=/hive>` joins the
  landing's flex row (chat | outline | feed | hive | timeline order TBD at the
  gutter chain), with a `Hive` rail button, `po-hive` visibility class, grow var,
  gutter, and an entry in the shell's `PANE` wsState map. Desktop first; the
  mobile tab bar keeps its current three panes (the hex world is a poor phone
  fit until proven otherwise).
- **Data: the existing feed/ledgers channel, zero push-loop changes.** The page
  boots `_shim("fleet")`; the kernel already treats `app=fleet` as a feed rider
  (`c["app"] in ("feed","fleet")` in the push loop) and attaches `ledgers`
  whenever such a client is connected. (`app=fleet` is the existing wire name of
  that channel — reused, not new vocabulary.) Everything the hive needs is
  already in that payload:
  - `ledgers[]` → sid, name, identity color, `status.state` (the shared chip:
    clearing / compacting / interrupting / blocked / awaiting / retrying /
    working / awaitingBg / ready, plus opening and faded), goal tree.
  - `asks[]` → per-goal cards: column, goal text, blockSummary (decision brief),
    background, `working` (open-turn narration), awaiting {why, tasks},
    waitingOn (peer), provisional.
  - `working[]` / `awaiting[]` name lists ride along.
- **Renderer: three.js**, installed as a dependency of `vscode-extension/`
  (which owns `node_modules`; esbuild's `nodePaths` already resolves from
  there), bundled into `dist/hive.js` like every other pane bundle. No external
  assets at runtime: all geometry is procedural primitives, all textures are
  generated (canvas gradients) — nothing to ship, nothing to leak.
- **Files.** `ui/webview/hive.ts` (boot + scene), pure logic split for node
  tests: `hive-layout.ts` (axial spiral, stable slot assignment),
  `hive-model.ts` (payload → HiveSession[] + DIFF events). `hive-pane.css` for
  the page frame + overlay card. esbuild entries: `hive.ts`, `hive-pane.css`.

## The model: state in, events out (CLAUDE.md "cards move on new information")

`hive-model.ts` consumes each feed push and emits a minimal event stream the
scene animates from — the scene is NEVER rebuilt per push:

- `added(sid, slot)` / `removed(sid)` — session appeared / left the ledgers.
- `stateChanged(sid, from, to)` — the chip moved. Identical re-pushes emit
  NOTHING (tested), so animations can't flap without new information.
- `goalDone(sid)` — a top goal for that sid transitioned to done → one-shot
  celebration. Keyed on the observed done-transition (the fleet's `seenDone`
  pattern), never re-derived per build.
- Slot assignment: sid→slot map in localStorage (`romp:hiveSlots`); a new
  session takes the lowest free spiral slot; a removed session frees its slot
  only after the departure animation lands. The board never reshuffles.

## State → performance (the whole cast)

| chip | pad ring | the little guy |
|---|---|---|
| working | soft yellow pulse | sits at a tiny desk, typing bursts, screen glow, keystroke sparks |
| awaiting (needs YOU) | strong red pulse | stands, faces camera, waves/jumps, bobbing ❗ overhead |
| blocked (API error) | steady red | slumped, head in hands, laptop smoking |
| retrying | amber flicker | paces the pad, glancing back at the laptop |
| awaitingBg | straw/green | leans back, watches a slowly-spinning hourglass overhead |
| compacting / clearing | teal swirl orbit | levitating cross-legged meditation |
| interrupting | dims briefly | freeze mid-pose + squash |
| ready | calm green | breathing idle, look-arounds, stretch; `faded` (>1h) → asleep, zzz motes |
| opening | pale shimmer | an egg wobbles, cracks, the character pops out (one-shot) |
| (session gone) | — | waves goodbye, hops off, pad sinks + fades (one-shot, then slot frees) |
| goalDone event | — | jump + arms up + confetti burst over the pad (one-shot overlay on any state) |

Ambient life for everyone: blink (3–7s), 2% breath scale, occasional head turns.
Status colors reuse the standing meanings (working `--st-working-bg` yellow,
blocked/awaiting red family, ready `--st-ready-bg`, compacting teal); the romp
accent `#9cd2ff` is reserved for selection/hover/focus chrome, never status.

## Camera & feel (the game-design checklist)

- Perspective tactical view, ~40° elevation, auto-framed to the occupied hexes;
  slow idle orbit drift. Wheel = dolly, drag = orbit/pan, double-click empty
  ground = re-frame all.
- Every motion is a critically-damped spring (position, ring color, pad lift) —
  overshoot on arrivals, no linear lerps, nothing teleports.
- Hover: pad lifts ~0.1u + rim brightens inside 100ms; cursor pointer.
- Click: immediate press-dip on the pad (acknowledge FIRST, per CLAUDE.md
  click-safety rule), then the ~0.8s fly-to; ESC / ✕ flies back.
- Reward moments are honest: confetti only on a real goalDone event.
- Empty ghost hex at the spiral frontier (SHIPPED): a hairline outline on the
  first free slot, deliberately quieter than any real pad; hover wakes its "+",
  click asks the shell (`{romp:"openPicker"}`) to reveal chat + open the same
  new-session picker the + tab uses.
- Perf: DPR ≤ 2; ≤ 32 fully-animated characters, LOD bob beyond; rAF gated by
  IntersectionObserver (pane hidden = display:none iframe → not intersecting)
  AND document.hidden; pause = zero GPU work. The romp loader (`_pane_spin`)
  holds until the first ledgers land, exactly like the outline pane.

## The fly-in card (progressive disclosure, pane-local)

DOM overlay (not WebGL text), anchored to the pane's right edge, one card:

1. **Gist line**: color dot + name + plain state ("working — 3 tools in, 2m" /
   "needs you" / "ready").
2. **Now**: current goal text; when blocked/awaiting, the decision brief
   (blockSummary / awaiting.why) — the same words the feed card shows.
3. **Sub-goals**: top few checklist rows, "…and N more" behind an expand.
4. **Talk**: a composer. Send posts the user's text into that session over the
   pane's own channel (the kernel's existing text-delivery message; verify the
   exact type at the `_send_or_park` call site ~kernel:5008 — if it's gated to
   app=chat sockets, accept it from this channel too as a small kernel change).
   Acknowledge instantly: composer clears + a paper-plane particle arcs from
   the card to the hex. Delivery semantics are the kernel's normal park/forward.
5. **Open session** → `openSession` postMessage + shell reveal of the chat pane.

The card is a pane-local panel (CLAUDE.md: pane-local dialogs stay pane-local);
it does NOT use the full-window modal lift. Menus, if any, wear the standard
menu vocabulary from styles.css.

## Tests (land with the feature, per CLAUDE.md)

- `hive-layout.test.ts` — spiral coords, lowest-free-slot assignment, stability
  across pushes, slot release after departure.
- `hive-model.test.ts` — chip mapping for EVERY state above; diff events;
  the no-flap invariant (identical payload twice → zero events); goalDone fires
  once per done-transition.
- `hive-wiring.test.ts` — pins the kernel landing/route strings + esbuild
  entries (the prebuild.test.ts pattern) so the pane can't silently unwire.
- All fixtures synthetic (`web`/`api`/`tests`, TESTHOST, placeholder UUIDs).

## Deliberately later

- VS Code extension surface (needs its own webview registration; the bundle
  already ships in the VSIX harmlessly).
- Mobile tab-bar entry; sound design (off unless asked); subagent satellite
  mini-hexes budding off a session's pad; per-hex postal/teammate visual links.
