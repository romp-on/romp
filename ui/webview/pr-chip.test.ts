// The Outline pane's PR chip: every state it can wear, the worst-state rollup, and the one shared detail
// body (the user 2026-08-17). Synthetic PRs only — invented numbers, the neutral notes-api demo repo.
import { test } from "node:test";
import assert from "node:assert";
import { prChipParts, worstOf, rollupParts, prDetailLines, prMatches, sessionPrs, subtreePrs, goalChip, headChip,
  prErrTitle, headOnlyShown, markRetry, retryPending, settleRetries, RetryPending, PR, PrNode } from "./pr-chip";

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
  assert.deepEqual(headChip({ prNum: 12, prs, prError: null }), { pr: prs["12"], err: "", snapshot: true, retry: true });
  assert.deepEqual(headChip({ prNum: 12, prs, prError: "HTTP 502" }), { pr: prs["12"], err: "HTTP 502", snapshot: true, retry: true },
    "a failed re-read keeps the snapshot beside the error");
  assert.deepEqual(headChip({ prNum: null, prs: null, prError: "gh auth login" }),
    { pr: null, err: "gh auth login", snapshot: false, retry: true });
  assert.deepEqual(headChip({}), { pr: null, err: "", snapshot: false, retry: true });
});

test("the head has a snapshot when any PR state is on screen: its branch's number or a goal's PR", () => {
  assert.equal(headChip({ prNum: 12, prs: null, prError: "HTTP 502" }).snapshot, true);
  assert.equal(headChip({ prNum: null, prs: { "15": pr(15) }, prError: "HTTP 502" }).snapshot, true);
  assert.equal(headChip({ prNum: null, prs: {}, prError: "HTTP 502" }).snapshot, false, "an empty map is no snapshot");
});

test("the error chip's title names the reason and the click", () => {
  const t = prErrTitle("gh auth login", true, true);
  assert.ok(t.includes("gh auth login") && t.includes("Click to retry"));
});

test("the error chip's title promises the last known state only when there is one", () => {
  assert.ok(prErrTitle("HTTP 502", true, true).includes("last known state"));
  const bare = prErrTitle("HTTP 502", false, true);
  assert.ok(!bare.includes("last known state"), bare);
  assert.ok(bare.startsWith("Could not read PR status: HTTP 502."), bare);
});

test("a failure the kernel says a re-read cannot fix offers no retry: the title only names the error", () => {
  assert.equal(headChip({ prError: "PR status could not be built: KeyError", prErrorRetry: false }).retry, false);
  assert.equal(headChip({ prError: "HTTP 502", prErrorRetry: true }).retry, true);
  assert.equal(headChip({ prError: "HTTP 502" }).retry, true, "absent keeps the retry");
  const t = prErrTitle("PR status could not be built: KeyError", false, false);
  assert.equal(t, "Could not read PR status: PR status could not be built: KeyError.");
  assert.ok(!/retry|click/i.test(t), t);
});

// ── a session shown by its head alone ─────────────────────────────────────────────────────────────

const NOW = 100000, WIDE = 1e9;
const sess = (over: Record<string, unknown> = {}) => ({ name: "api", prNum: 12, prs: { "12": pr(12, { updatedT: NOW - 60 }) },
  prError: null, ...over }) as Parameters<typeof headOnlyShown>[0];

test("a head with an open or draft branch PR, or a failed read, is shown alone", () => {
  assert.equal(headOnlyShown(sess(), NOW, WIDE, ""), true, "open");
  assert.equal(headOnlyShown(sess({ prs: { "12": pr(12, { draft: true, updatedT: NOW }) } }), NOW, WIDE, ""), true, "draft");
  assert.equal(headOnlyShown(sess({ prNum: null, prs: null, prError: "gh auth login" }), NOW, WIDE, ""), true, "error, no PR");
  assert.equal(headOnlyShown(sess({ prs: { "12": pr(12, { state: "merged" }) } }), NOW, WIDE, ""), false, "merged is not news");
  assert.equal(headOnlyShown(sess({ prs: { "12": pr(12, { state: "closed" }) } }), NOW, WIDE, ""), false, "closed is not news");
  assert.equal(headOnlyShown(sess({ prNum: null, prs: null }), NOW, WIDE, ""), false, "nothing at all");
});

test("a head shown alone passes the search box on its name or its PR, and the slider on the PR's age", () => {
  assert.equal(headOnlyShown(sess(), NOW, WIDE, "api"), true, "name");
  assert.equal(headOnlyShown(sess(), NOW, WIDE, "#12"), true, "the PR number");
  assert.equal(headOnlyShown(sess(), NOW, WIDE, "unrelated"), false, "no match");
  assert.equal(headOnlyShown(sess({ prError: "HTTP 502" }), NOW, WIDE, "unrelated"), false, "an error does not bypass the search");
  assert.equal(headOnlyShown(sess(), NOW, 30, ""), false, "updated 60s ago, window 30s");
  assert.equal(headOnlyShown(sess(), NOW, 120, ""), true, "inside a 120s window");
  assert.equal(headOnlyShown(sess({ prError: "HTTP 502" }), NOW, 30, ""), true, "a failed read is news as of now");
});

// ── the retry chip's pending state ───────────────────────────────────────────────────────────────

test("a pending retry holds across renders until a payload brings a different reason or none", () => {
  const m: RetryPending = new Map();
  markRetry(m, "s1", "HTTP 502", 1000);
  assert.equal(retryPending(m, "s1", "HTTP 502", 10), true, "every render before the answer reads it disabled");
  assert.equal(retryPending(m, "s1", "HTTP 502", 20), true);
  settleRetries(m, [{ sid: "s1", prError: "HTTP 502" }], 30);
  assert.equal(retryPending(m, "s1", "HTTP 502", 40), true, "the same reason is not yet an answer");
  settleRetries(m, [{ sid: "s1", prError: "gh auth login" }], 50);
  assert.equal(m.has("s1"), false, "a new reason settles it");
  markRetry(m, "s1", "HTTP 502", 1000);
  settleRetries(m, [{ sid: "s1", prError: null }], 60);
  assert.equal(m.has("s1"), false, "a cleared error settles it");
});

test("a pending retry is per session, per reason, and lifts at its backstop", () => {
  const m: RetryPending = new Map();
  markRetry(m, "s1", "HTTP 502", 1000);
  assert.equal(retryPending(m, "s2", "HTTP 502", 10), false, "another session's chip stays enabled");
  assert.equal(retryPending(m, "s1", "gh auth login", 10), false, "a different reason on screen is a new chip");
  assert.equal(retryPending(m, "s1", "HTTP 502", 1000), false, "the backstop re-enables it");
  settleRetries(m, [{ sid: "s1", prError: "HTTP 502" }], 1000);
  assert.equal(m.has("s1"), false, "and a payload past it drops the entry");
  markRetry(m, "s3", "HTTP 502", 1000);
  settleRetries(m, [], 10);
  assert.equal(m.has("s3"), false, "a session gone from the payload drops its entry");
});
