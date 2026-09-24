// The Outline pane's PR chip: every state it can wear, the worst-state rollup, and the one shared detail
// body (the user 2026-08-17). Synthetic PRs only — invented numbers, the neutral notes-api demo repo.
import { test } from "node:test";
import assert from "node:assert";
import { prChipParts, worstOf, rollupParts, prDetailLines, prMatches, sessionPrs, subtreePrs, goalChip, headChip,
  prErrTitle, PR, PrNode } from "./pr-chip";

const base: PR = {
  num: 12, url: "https://github.com/notes-api-org/notes-api/pull/12", title: "notes: index rebuild",
  branch: "dev/notes-index", state: "open", draft: false, checksState: "pass", checksFailing: [],
  reviewDecision: "approved", adds: 64, dels: 7, files: 2, updatedT: 100,
};

test("open, passing, approved", () => {
  const p = prChipParts(base);
  assert.equal(p.num, "#12");
  assert.equal(p.state, "●");
  assert.equal(p.checks, "✓");
  assert.equal(p.review, "✓");
  assert.ok(p.cls.includes("st-open") && p.cls.includes("ck-pass"));
});

test("a draft with nothing to report omits both trailing segments", () => {
  const p = prChipParts({ ...base, draft: true, checksState: "none", reviewDecision: "none" });
  assert.equal(p.state, "◌");
  assert.equal(p.checks, "");
  assert.equal(p.review, "");
  assert.ok(p.cls.includes("st-draft"));
  assert.ok(!p.cls.includes("ck-"), "no check class when there are no checks");
});

test("failing checks carry their count", () => {
  assert.equal(prChipParts({ ...base, checksState: "fail", checksFailing: ["pytest", "mypy"] }).checks, "✗2");
});

test("a failing state with no names still shows at least one", () => {
  assert.equal(prChipParts({ ...base, checksState: "fail", checksFailing: [] }).checks, "✗1");
});

test("running checks show the working glyph", () => {
  assert.equal(prChipParts({ ...base, checksState: "running" }).checks, "◐");
});

test("review states", () => {
  assert.equal(prChipParts({ ...base, reviewDecision: "changes_requested" }).review, "✎");
  assert.equal(prChipParts({ ...base, reviewDecision: "review_required" }).review, "⌛");
  assert.equal(prChipParts({ ...base, reviewDecision: "none" }).review, "");
});

test("merged drops check and review talk — the outcome is the news", () => {
  const p = prChipParts({ ...base, state: "merged", checksState: "fail", checksFailing: ["x"],
                          reviewDecision: "changes_requested" });
  assert.equal(p.state, "◆");
  assert.equal(p.checks, "");
  assert.equal(p.review, "");
});

test("closed wears its own glyph", () => {
  assert.equal(prChipParts({ ...base, state: "closed" }).state, "✕");
});

test("live adds its own class, and only when live", () => {
  assert.ok(prChipParts({ ...base, live: true }).cls.includes("live"));
  assert.ok(!prChipParts(base).cls.includes("live"));
});

test("worst-state precedence: a failure outranks everything below it", () => {
  assert.equal(worstOf([base, { ...base, checksState: "fail", checksFailing: ["a"] }]), "fail");
  assert.equal(worstOf([base, { ...base, reviewDecision: "changes_requested" }]), "changes");
  assert.equal(worstOf([base, { ...base, reviewDecision: "review_required" }]), "review");
  assert.equal(worstOf([base, { ...base, checksState: "running" }]), "running");
  assert.equal(worstOf([base, { ...base, draft: true, checksState: "none" }]), "draft");
  assert.equal(worstOf([base]), "open");
  assert.equal(worstOf([{ ...base, state: "merged" }]), "merged");
});

test("a failure beats a merge, so a red never hides under a collapsed parent", () => {
  assert.equal(worstOf([{ ...base, state: "merged" },
                        { ...base, checksState: "fail", checksFailing: ["a"] }]), "fail");
});

test("rollup pluralises, carries the worst state, and counts only actionable failures", () => {
  const r = rollupParts([base, { ...base, num: 15, checksState: "fail", checksFailing: ["a"] }]);
  assert.equal(r.label, "2 PRs");
  assert.equal(r.worst, "fail");
  assert.equal(r.fails, 1);
  assert.equal(rollupParts([base]).label, "1 PR");
});

test("a closed PR's old CI failure is not counted as actionable", () => {
  const r = rollupParts([{ ...base, state: "closed", checksState: "fail", checksFailing: ["a"] }]);
  assert.equal(r.fails, 0);
});

test("detail lines name the failing checks, not just the count", () => {
  const lines = prDetailLines({ ...base, checksState: "fail", checksFailing: ["pytest", "mypy"] }, 400);
  assert.ok(lines.some((l) => l.includes("pytest, mypy")));
});

test("detail lines carry title, branch, diffstat and age", () => {
  const lines = prDetailLines(base, 400);
  assert.equal(lines[0], "notes: index rebuild");
  assert.ok(lines.some((l) => l.includes("dev/notes-index")));
  assert.ok(lines.some((l) => l.includes("+64 −7")));
  assert.ok(lines.some((l) => l.includes("2 files")));
  assert.ok(lines.some((l) => l.includes("5m")), "400 − 100 = 300s");
});

test("a one-file PR says file, not files", () => {
  assert.ok(prDetailLines({ ...base, files: 1 }, 400).some((l) => l.includes("1 file ")
    || l.endsWith("1 file")));
});

test("a titleless PR falls back to its number rather than an empty first line", () => {
  assert.equal(prDetailLines({ ...base, title: "" }, 400)[0], "#12");
});

test("a merged PR states the outcome and drops check talk", () => {
  const lines = prDetailLines({ ...base, state: "merged", checksState: "fail", checksFailing: ["x"] }, 400);
  assert.ok(lines.some((l) => l === "merged"));
  assert.ok(!lines.some((l) => l.includes("failing")));
});

test("no updated time means no age line rather than a wrong one", () => {
  assert.ok(!prDetailLines({ ...base, updatedT: 0 }, 400).some((l) => l.includes("ago")));
});

test("search matches number, hash-prefixed number, title and branch", () => {
  assert.ok(prMatches(base, "12"));
  assert.ok(prMatches(base, "#12"));
  assert.ok(prMatches(base, "index rebuild"));
  assert.ok(prMatches(base, "dev/notes"));
  assert.ok(!prMatches(base, "unrelated"));
  assert.ok(!prMatches(base, "   "), "an empty query matches nothing, not everything");
});

test("checks not yet fetched render as nothing, never as a pass tick", () => {
  const p = prChipParts({ ...base, checksState: "unknown" });
  assert.equal(p.checks, "", "unknown must not borrow the pass glyph");
  assert.ok(!p.cls.includes("ck-"), "and must not colour the chip");
});

test("unknown checks add no claim to the detail body either", () => {
  const lines = prDetailLines({ ...base, checksState: "unknown", reviewDecision: "none" }, 400);
  assert.ok(!lines.some((l) => l.includes("checks")), lines.join(" | "));
});

test("unknown checks do not count toward the rollup's failures", () => {
  assert.equal(rollupParts([{ ...base, checksState: "unknown" }]).fails, 0);
  assert.equal(worstOf([{ ...base, checksState: "unknown" }]), "open");
});

// ── placement ──────────────────────────────────────────────────────────────────────────────────────

const pr = (num: number, over: Partial<PR> = {}): PR => ({ ...base, num, url: base.url.replace("/12", "/" + num), ...over });
const tree = (nodes: PrNode[]) => new Map(nodes.map((n) => [n.id, n]));

test("a goal's PRs resolve against the session map, in the goal's order, skipping unanswered numbers", () => {
  const prs = { "12": pr(12), "15": pr(15) };
  assert.deepEqual(sessionPrs(prs, [15, 99, 12]).map((p) => p.num), [15, 12]);
  assert.deepEqual(sessionPrs(null, [12]), []);
});

test("a goal with one PR of its own wears that PR's chip", () => {
  const prs = { "12": pr(12) };
  const g: PrNode = { id: "g1", prNums: [12] };
  const c = goalChip(prs, tree([g]), g);
  assert.equal(c.kind, "one");
  assert.deepEqual(c.prs.map((p) => p.num), [12]);
});

test("several own PRs, or a parent with none of its own, wear the rollup", () => {
  const prs = { "12": pr(12), "15": pr(15), "21": pr(21) };
  const kid: PrNode = { id: "g2", prNums: [15], children: ["g3"] };
  const grand: PrNode = { id: "g3", prNums: [21, 15] };
  const parent: PrNode = { id: "g1", prNums: [], children: ["g2"] };
  const byId = tree([parent, kid, grand]);
  assert.equal(goalChip(prs, byId, { id: "x", prNums: [12, 15] }).kind, "rollup");
  const up = goalChip(prs, byId, parent);
  assert.equal(up.kind, "rollup");
  assert.deepEqual(up.prs.map((p) => p.num).sort(), [15, 21], "descendants' PRs, deduped");
  assert.deepEqual(subtreePrs(prs, byId, grand), []);
});

test("a goal whose own PR exists shows it, not its subtree's", () => {
  const prs = { "12": pr(12), "15": pr(15) };
  const kid: PrNode = { id: "g2", prNums: [15] };
  const g: PrNode = { id: "g1", prNums: [12], children: ["g2"] };
  assert.deepEqual(goalChip(prs, tree([g, kid]), g).prs.map((p) => p.num), [12]);
});

test("no PR anywhere: no chip", () => {
  assert.deepEqual(goalChip({}, tree([]), { id: "g1" }), { kind: "none", prs: [] });
});

test("the session head carries its branch's PR and, independently, the failed read's reason", () => {
  const prs = { "12": pr(12) };
  assert.deepEqual(headChip({ prNum: 12, prs, prError: null }), { pr: prs["12"], err: "" });
  assert.deepEqual(headChip({ prNum: 12, prs, prError: "HTTP 502" }), { pr: prs["12"], err: "HTTP 502" },
    "a failed re-read keeps the snapshot beside the error");
  assert.deepEqual(headChip({ prNum: null, prs: null, prError: "gh auth login" }), { pr: null, err: "gh auth login" });
  assert.deepEqual(headChip({}), { pr: null, err: "" });
});

test("the error chip's title names the reason and the click", () => {
  const t = prErrTitle("gh auth login");
  assert.ok(t.includes("gh auth login") && t.includes("click to retry"));
});
