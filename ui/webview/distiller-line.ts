// The distiller's display line, computed in ONE place so a test can EXECUTE the rule — not just regex the
// source, which is what let it get silently removed before (the user 2026-06-29, who asked to make sure the distiller
// captions never turn off again). The whole saga: the captions were dropped, and the source-pin tests were
// simply rewritten to assert the removal, so nothing caught it. distiller-line.test.ts runs THIS instead.
//
// THE CONTRACT (the test pins it behaviorally):
//   - a COMPLETED item shows the distiller's takeaway (summary)
//   - a BLOCKED item shows its decision brief (blockSummary)
//   - a card sitting in the WORKING column shows neither, even when it is holding one
//   - anything else shows nothing
//   - it is shown ONLY when the distiller has produced a non-empty value (trimmed) — never a "(generating…)"
//     placeholder (which used to stick)
//   - it NEVER takes — and therefore can NEVER show — the planner's why-created/why-blocked/why-done
//     rationale (the user dropped those, esp. under subgoals). The signature has no `why` by design.

/** The (completed, blocked) inputs the distiller line keys on for a CARD.
 *
 *  WORKING WINS. A card in the Working column shows no distilled line at all, whatever `distillState` says
 *  underneath. A summary describes work that has stopped; the moment the card is working again it describes
 *  a past that may no longer hold, and a card that reads "in motion" while displaying a settled takeaway is
 *  just contradicting itself (the user 2026-07-22). Often nothing will have changed and the same brief comes
 *  straight back when the card settles — that is fine. It is withheld while it cannot be vouched for, not
 *  discarded: `summary`/`blockSummary` stay in the payload and re-render untouched on the way back.
 *
 *  This supersedes the 2026-07-21 rule that kept the brief pinned through the flip. That fix was aimed at a
 *  real problem — recheck/rejudging drop a still-blocked card to Working every time its session takes a turn,
 *  and the line, then keyed on `column`, blanked on each one, so a busy session's blocked card read as
 *  "unblocked, no summary" (the docs thread). Pinning the brief cured the blanking but produced a card in
 *  Working inexplicably wearing a summary. The card is not left mute in that window: the displacement only
 *  ever happens under recheck/rejudging, and both raise the "Analyzing…" swirl (see ./spin-caption), so it
 *  reads as in motion and being looked at rather than as unblocked.
 *
 *  `distillState` still earns its keep below: it is the GENUINE resolution state, so a card that is settled
 *  but momentarily mis-columned by an older payload still resolves correctly.
 *
 *  Fallback: a payload with no `distillState` (an older build, or a remote kernel that predates the field →
 *  null/undefined) reads the old `column` meaning, so federation and cache-warm frames still render correctly. */
export function distillInputs(
  distillState: "completed" | "blocked" | null | undefined,
  column: string,
): { completed: boolean; blocked: boolean } {
  if (column === "working") return { completed: false, blocked: false };
  if (distillState === "completed") return { completed: true, blocked: false };
  if (distillState === "blocked") return { completed: false, blocked: true };
  return { completed: column === "completed", blocked: column === "needs_input" };
}

/** The distiller line's text for an item/node, or "" when there's nothing to show. */
export function distillText(
  completed: boolean,
  blocked: boolean,
  summary?: string | null,
  blockSummary?: string | null,
): string {
  return (completed ? (summary || "") : blocked ? (blockSummary || "") : "").trim();
}

/** True when the distiller is still PENDING for a RESOLVED card — a completed goal whose `summary` hasn't been
 *  produced yet, or a blocked goal whose `blockSummary` hasn't — so the card should show the spinning
 *  "Distilling…" swirl in the distiller-line spot until the takeaway/brief lands (the user 2026-06-29).
 *
 *  The kernel's three states are distinguished EXACTLY: `null`/`undefined` = not produced yet (PENDING → spin);
 *  `""` = the distiller ran and gave up (NOT pending — nothing to say, no spin, no line); a non-empty string =
 *  produced (NOT pending — the line shows instead). So `== null` (which excludes "") is the precise test, the
 *  complement of distillText's `|| ""` show-rule.
 *
 *  liveBlocked excludes a card stopped on a live permission/picker prompt: that's ON YOU (its ⏸ badge is the
 *  message), not a "in motion, waiting on the distiller" state. */
export function distillPending(
  completed: boolean,
  blocked: boolean,
  summary?: string | null,
  blockSummary?: string | null,
  liveBlocked?: boolean,
): boolean {
  if (completed) return summary == null;
  if (blocked && !liveBlocked) return blockSummary == null;
  return false;
}

/** Populate a card's distiller line element: set its text and show it ONLY when non-empty. Returns the text.
 *  Takes a minimal element shape so it runs under `node --test` without a DOM (the real DOM node satisfies it). */
export function applyDistillLine(
  el: { textContent: string; style: { display: string } },
  completed: boolean,
  blocked: boolean,
  summary?: string | null,
  blockSummary?: string | null,
): string {
  const t = distillText(completed, blocked, summary, blockSummary);
  el.textContent = t;
  el.style.display = t ? "" : "none";   // hidden until the distiller produces — no stuck placeholder
  return t;
}

/** The STALE-takeaway note (the user 2026-08-19): shown above a COMPLETED card's takeaway when the user
 *  replied AFTER the summary they read (kernel summaryStale: followupAt postdates what distilledMt
 *  covers), an old takeaway must never present as current beside a newer reply. "" when there is
 *  nothing to say: not stale, not completed, or no takeaway shown to annotate. Self-clearing by
 *  construction: the re-distill stamps a newer distilledMt and the kernel stops sending the flag.
 *  The copy says "replied", which covers a quoted reply as well as a follow-up (the 2026-09-18 read:
 *  "followed up" was wrong for a quoted reply), in two sentences with no dash. When the note shows and
 *  whether the card moves are unchanged: that call is the user's. */
export function distillStaleNote(summaryStale: boolean, completed: boolean, shownText: string): string {
  if (!summaryStale || !completed || !shownText.trim()) return "";
  return "You replied after this summary was written. It refreshes when the new work lands.";
}
