// The Outline pane's per-goal PR chip: one glanceable chip on the goal row that shipped a PR, carrying
// that PR's live state, and marking the one being pushed to right now.
//
// Pure builders live here so every state and every placement decision is unit-testable without a DOM,
// and so the detail row and the hover card share ONE body and cannot drift apart.
//
// Content order is FIXED — #number · state · checks · review — so the eye lands in the same place on
// every row. The chip never shrinks; the goal TITLE ellipsizes instead, because a truncated title is
// recoverable from the hover card and a truncated PR state is not.

export interface PR {
  num: number; url: string; title: string; branch: string;
  state: "open" | "merged" | "closed"; draft: boolean;
  checksState: "pass" | "fail" | "running" | "none" | "unknown";
  checksFailing: string[]; reviewDecision: string;
  adds: number; dels: number; files: number; updatedT: number; live?: boolean;
}

export interface ChipParts { num: string; state: string; checks: string; review: string; cls: string }

const STATE_GLYPH: Record<string, string> = { open: "●", merged: "◆", closed: "✕" };

export function prChipParts(pr: PR): ChipParts {
  // A landed or closed PR's check and review history is not news — the outcome is. Both segments drop,
  // so a merged chip reads "#12 ◆" instead of relitigating a CI run nobody can act on.
  const terminal = pr.state === "merged" || pr.state === "closed";
  const state = terminal ? STATE_GLYPH[pr.state] : (pr.draft ? "◌" : STATE_GLYPH.open);
  const checks = terminal ? ""
    : pr.checksState === "fail" ? "✗" + Math.max(1, pr.checksFailing.length)
    : pr.checksState === "pass" ? "✓"
    : pr.checksState === "running" ? "◐" : "";
  const review = terminal ? ""
    : pr.reviewDecision === "approved" ? "✓"
    : pr.reviewDecision === "changes_requested" ? "✎"
    : pr.reviewDecision === "review_required" ? "⌛" : "";
  // "unknown" (checks not fetched yet — the list query leaves them out, see gitpr._GH_FIELDS) renders like
  // "none": no glyph, no colour class. It must never borrow the pass tick, which would invent a green CI
  // out of data we simply have not asked for.
  const cls = "fl-pr st-" + (terminal ? pr.state : pr.draft ? "draft" : "open")
    + (!terminal && pr.checksState !== "none" && pr.checksState !== "unknown" ? " ck-" + pr.checksState : "")
    + (pr.live ? " live" : "");
  return { num: "#" + pr.num, state, checks, review, cls };
}

// Worst state first, so a collapsed parent can never hide a red beneath its fold.
const ORDER = ["fail", "changes", "review", "running", "draft", "open", "merged", "closed"];

export function worstOf(prs: PR[]): string {
  let best = "closed";
  for (const pr of prs) {
    const k = pr.state === "merged" ? "merged"
      : pr.state === "closed" ? "closed"
      : pr.checksState === "fail" ? "fail"
      : pr.reviewDecision === "changes_requested" ? "changes"
      : pr.reviewDecision === "review_required" ? "review"
      : pr.checksState === "running" ? "running"
      : pr.draft ? "draft" : "open";
    if (ORDER.indexOf(k) < ORDER.indexOf(best)) best = k;
  }
  return best;
}

export function rollupParts(prs: PR[]): { label: string; worst: string; fails: number } {
  return {
    label: prs.length === 1 ? "1 PR" : prs.length + " PRs",
    worst: worstOf(prs),
    // Only an OPEN PR's failure is actionable; a closed PR that failed CI on its way out is noise.
    fails: prs.filter((p) => p.state === "open" && p.checksState === "fail").length,
  };
}

function age(secs: number): string {
  const s = Math.max(0, Math.round(secs));
  return s < 60 ? s + "s" : s < 3600 ? Math.round(s / 60) + "m"
    : s < 86400 ? Math.round(s / 3600) + "h" : Math.round(s / 86400) + "d";
}

// ONE body, two frames (the detail row and the hover card), so the two can never disagree. First line is
// the title; the rest are the mechanics, one clause per line.
export function prDetailLines(pr: PR, now: number): string[] {
  const out = [pr.title || ("#" + pr.num)];
  if (pr.branch) out.push("branch " + pr.branch);
  if (pr.state === "merged") {
    out.push("merged");
  } else if (pr.state === "closed") {
    out.push("closed unmerged");
  } else {
    out.push(pr.draft ? "draft" : "open");
    // Name the FAILING CHECKS, not just the count: "which one broke" is the whole question a red chip asks.
    if (pr.checksState === "fail") out.push("✗ failing: " + pr.checksFailing.join(", "));
    else if (pr.checksState === "pass") out.push("✓ checks passed");
    else if (pr.checksState === "running") out.push("◐ checks running");
    if (pr.reviewDecision === "approved") out.push("✓ approved");
    else if (pr.reviewDecision === "changes_requested") out.push("✎ changes requested");
    else if (pr.reviewDecision === "review_required") out.push("⌛ review requested");
  }
  out.push("+" + pr.adds + " −" + pr.dels + " · " + pr.files + (pr.files === 1 ? " file" : " files"));
  if (pr.updatedT) out.push("updated " + age(now - pr.updatedT) + " ago");
  return out;
}

// The pane's search box also matches PRs, so typing a number reveals the goal that shipped it. A leading
// "#" is stripped: people paste PR refs that way.
export function prMatches(pr: PR, q: string): boolean {
  const s = (q || "").trim().toLowerCase().replace(/^#/, "");
  if (!s) return false;
  return String(pr.num).includes(s) || (pr.title || "").toLowerCase().includes(s)
    || (pr.branch || "").toLowerCase().includes(s);
}

// ── placement: which chip a row or a session head carries ──────────────────────────────────────────

export type PrMap = Record<string, PR> | null | undefined;

export interface PrNode { id: string; prNums?: number[] | null; children?: string[] }

// The PRs a goal cites, in its order, resolved against the session's map (a number the map lacks is a
// PR gh has not answered for yet, so it is left out rather than drawn blank).
export function sessionPrs(prs: PrMap, nums: number[] | null | undefined): PR[] {
  const out: PR[] = [];
  for (const num of nums || []) {
    const pr = prs?.[String(num)];
    if (pr) out.push(pr);
  }
  return out;
}

// A parent's rollup covers its DESCENDANTS' PRs, deduped. Only consulted when the node has none of its own.
export function subtreePrs(prs: PrMap, byId: Map<string, PrNode>, n: PrNode): PR[] {
  const out: PR[] = [], seen = new Set<number>(), stack = [...(n.children || [])];
  while (stack.length) {
    const c = byId.get(stack.pop()!);
    if (!c) continue;
    for (const pr of sessionPrs(prs, c.prNums)) if (!seen.has(pr.num)) { seen.add(pr.num); out.push(pr); }
    stack.push(...(c.children || []));
  }
  return out;
}

export interface GoalChip { kind: "none" | "one" | "rollup"; prs: PR[] }

// A goal with its own PR shows that PR; several show as a rollup; a parent with none of its own rolls up
// its subtree's. `prs` is also what the goal's detail row lists.
export function goalChip(prs: PrMap, byId: Map<string, PrNode>, n: PrNode): GoalChip {
  const own = sessionPrs(prs, n.prNums);
  if (own.length === 1) return { kind: "one", prs: own };
  if (own.length > 1) return { kind: "rollup", prs: own };
  const roll = subtreePrs(prs, byId, n);
  return roll.length ? { kind: "rollup", prs: roll } : { kind: "none", prs: [] };
}

export interface HeadChip { pr: PR | null; err: string }

// The session head: the PR on its current branch, and, independently, the reason the last read failed.
// Both can show at once: a failed re-read keeps the last snapshot beside the error.
export function headChip(s: { prNum?: number | null; prs?: PrMap; prError?: string | null }): HeadChip {
  const pr = s.prNum ? s.prs?.[String(s.prNum)] || null : null;
  return { pr, err: s.prError || "" };
}

// The error chip's hover text: the reason, and what the click does.
export function prErrTitle(reason: string): string {
  return "Could not read PR status: " + reason + ". Showing the last known state; click to retry now.";
}
