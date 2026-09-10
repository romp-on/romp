// The feed pane's per-card UPDATE GATE: reconcileCol calls updateAskCard only for a card whose inputs
// changed since it was last painted. The paint key this replaces serialised every card on every render
// (JSON.stringify per card) and carried two whole-board terms — a fifteen-second clock, and an epoch bumped
// by every change to the working/awaiting/unknown sets and by every settings write — so the first frame in
// each 15 s window, and every frame during activity, repainted EVERY card: the className rewrite, the tint,
// the name nodes minted anew, the delegation lines rebuilt (a whole-board style invalidation), and then the
// scroll restore at the end of render() forced a synchronous layout of all of it. This key is O(1) per
// card and names only what that card reads, so a change repaints the cards it reaches and no others.
//
// What a card's face depends on, and how each dependency reaches the gate:
//   - the ask object itself. An unchanged card keeps its OBJECT through the delivery path: the pane shim
//     reassembles a `{type:"delta"}` frame reusing every untouched item (kernel.py _shim, applyDelta), and
//     federation's merge pushes each host's items through by reference (federation.ts mergeHostFeeds) —
//     both pinned by tests, because a defensive copy anywhere on that path would silently turn this gate
//     into always-update. So a new object means the kernel re-sent this card: `card._it !== it`
//     (updateAskCard stashes `_it`).
//   - every board-level input updateAskCard reads OUTSIDE the ask object: cardInputsKey folds them into
//     one string, computed from an env the render builds once. The list below is the complete set (a
//     source scan of updateAskCard, applySections, quarWho, dotFor and prRepoOf); a missed input shows as
//     a stale badge on an unchanged card, so keep it complete.
//   - a local gesture (a section toggle, the bell, hover/pin): its handler writes the DOM directly and,
//     where a column could change, calls render(); hover/pin are also in the key.
//   - time: the 15 s tick rewrites every card's age label itself; a duration baked into a caption string
//     (the awaiting box, the waiting-on chip, a paragraph age) moves on the card's next repaint.
// The card's column and its place in the column are NOT gated — reconcileCol re-applies both every
// render — so a card whose column or sort key changed still moves (a column change always arrives as a
// new object; a follow-move prediction is a copy, see applyFollowMove).
//
// Deliberately NOT in the key: secChoice / cardTreeExpanded (their handlers repaint the card locally; the
// settings-driven reset rides prefs.collapsed), pendingDone (the modal's tree reads it, the card's checklist
// does not). Pure: node --test runs it without a DOM.

/** The slice of an ask the key reads. Structural, so tests pass plain objects. */
export interface GateItem {
  itemId: string;
  sid: string;
  name: string;
  color?: { bg: string } | null;
  blocked?: { state: string } | null;
  tree?: { kind: string; who: string; whoSid?: string }[] | null;
  delegTracked?: { name: string }[] | null;
}

/** The board-level inputs, resolved by the render once per pass. */
export interface GateEnv {
  /** dotFor: the working / awaiting / unknown state of a session by name — the card's own dot, the
   *  apiRecovered rule (working or awaiting), and each tracked delegation peer's dot. */
  dot: (name: string) => string;
  /** workingSet membership by name: the handoff delegation lines show only live-working recipients. */
  working: (name: string) => boolean;
  /** hoverAskId ?? pinnedAskId, and pinnedAskId: the .focused / .pinned classes. */
  focusId: string | null;
  pinnedId: string | null;
  /** cardNotifyOn: the bell's effective state (pendingNotify over the payload's notify). */
  notifyOn: (it: GateItem) => boolean;
  /** feedPrefs: grouped hides the name row, collapsed is the section default (resolveSec), and a
   *  colormap pick repaints every card once, as the settings epoch did (the tint is the kernel's trgb and
   *  follows on its rebuild; the term keeps the key complete for the one settings write about tints). */
  prefs: { grouped: boolean; collapsed: boolean; colormap: string };
  /** hostIsDown(sid): the struck "host:" prefix on a remote session's name. */
  hostDown: (sid: string) => boolean;
  /** feedSelfHost: the quarantine route's recipient host. */
  selfHost: string;
  /** prRepoOf(sid): the GitHub repository (owner/repo, or null) the card's session works in, read off the
   *  frame's session rows; the title, checklist, takeaway and tree link their `#123` to it (pr-links.ts). A
   *  session whose repository arrives or changes must relink an otherwise unchanged card. */
  repo: (sid: string) => string | null;
  /** userTodosMap[sid]: the session's OPEN user-todo count (plans/user-todos.md) — the quiet "waiting on you"
   *  marker every card of that session wears. The count is board-level (the frame's userTodos map), so a
   *  todo registered or withdrawn changes it and not the ask object; it reaches the gate through the key. */
  userTodos: (sid: string) => number;
  /** A per-render counter for cards that must never skip: a quarantine card reads sessionColors by
   *  name, a map the payload rebuilds every frame, so its key is unique per render. */
  seq: number;
}

/** One string of every board-level input this card's face reads. Two renders with equal inputs
 *  give equal keys; a change in any one of them changes the key. */
export function cardInputsKey(it: GateItem, env: GateEnv): string {
  const parts: string[] = [
    env.dot(it.name),
    it.itemId === env.focusId ? "f" : "",
    it.itemId === env.pinnedId ? "p" : "",
    env.notifyOn(it) ? "n" : "",
    env.prefs.grouped ? "g" : "",
    env.prefs.collapsed ? "c" : "",
    env.prefs.colormap,
    env.hostDown(it.sid) ? "d" : "",
    env.selfHost,
    env.repo(it.sid) || "",
    String(env.userTodos(it.sid) || ""),
    // the colour echo (feed.ts applyColorEcho) writes `a.color` IN PLACE — the one write into a shared
    // ask object — so identity cannot carry it; the colour rides the key instead
    (it.color && it.color.bg) || "",
  ];
  for (const n of it.tree || []) {
    if (n.kind !== "handoff" || !n.whoSid) continue;
    parts.push(n.whoSid + (env.working(n.who) ? "w" : ""));
  }
  for (const d of it.delegTracked || []) parts.push(env.dot(d.name));
  if (it.blocked && it.blocked.state === "quarantine") parts.push("q" + env.seq);
  return parts.join("|");
}

/** The card element's gate fields, as updateAskCard and reconcileCol keep them. */
export interface GatedCard { _it?: unknown; _ik?: string }

/** True when the card was last painted from a different object, or under different inputs. */
export function cardNeedsUpdate(card: GatedCard, it: unknown, key: string): boolean {
  return card._it !== it || card._ik !== key;
}
