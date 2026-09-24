#!/usr/bin/env python3
"""PR attribution from a goal node's own segments (judge.goal_pr_refs) — the join key behind the Outline
pane's per-goal PR chip (the user 2026-08-17).

Two rules under test, both learned the hard way on live data:

1. FULL urls only. A bare `#8123` in prose is ambiguous by construction — an internal ticket or audit id
   wears exactly that shape — and a silently-wrong PR link is worse than none, the same call the kernel's
   path linkifier makes for shortened file mentions.
2. A goal's PR is one it ACTED ON, not one it mentioned (the user 2026-08-18). Without that gate, a goal
   that merely read a design note, or rotated an API token, carried someone else's PR number.
"""
import os
import tempfile
from romp_load import load_source
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.realpath(__file__))).parent
# Hermetic state BEFORE the load — judge resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
jd = load_source("romp_judge_prmine", str(ROOT / "kernel" / "judge.py"))

REPO = "notes-api-org/notes-api"
URL = "https://github.com/notes-api-org/notes-api/pull/%d"
CREATE = "gh pr create --draft --title 'notes: index rebuild'"


def _text(t):
    return {"message": {"content": [{"type": "text", "text": t}]}}


def _out(t):
    return {"message": {"content": [{"type": "tool_result", "content": t}]}}


def _bash(cmd):
    return {"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}


_seg_n = iter(range(1, 100000))


def _node(atoms):
    """One goal whose trail holds one segment carrying `atoms`. Each call mints a FRESH segment id: refs are
    memoized per (segment id, atom count), and reusing an id across cases would serve one case's scan to
    another — the way two real segments never share an id."""
    sid = "s%d" % next(_seg_n)
    store = {"nodes": {"g1": {"trail": [sid], "text": "R1 registry exclusions"}}, "placements": {}}
    return store, {sid: {"id": sid, "t": 100, "atoms": atoms}}


# ── the receipt gate ─────────────────────────────────────────────────────────────────────────────────

def test_a_creation_receipt_attributes_the_pr():
    """`gh pr create` prints the new PR's url into tool output — the agent's own receipt."""
    store, segs = _node([_bash(CREATE), _out(URL % 12 + "\n")])
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 12]]


def test_prose_alone_attributes_nothing():
    """The whole 2026-08-18 fix: a goal that only TALKED about a PR does not own it."""
    store, segs = _node([_text("Read the design note; it references %s and %s." % (URL % 501, URL % 502))])
    assert jd.goal_pr_refs(store, segs, "g1") == []


def test_a_read_only_gh_command_is_not_a_receipt():
    """Looking at a PR is not acting on it — `gh pr view` and `gh pr list` must not attribute."""
    for cmd in ("gh pr view 502", "gh pr list --state open", "gh pr checks 502"):
        store, segs = _node([_bash(cmd), _out(URL % 502)])
        assert jd.goal_pr_refs(store, segs, "g1") == [], cmd


def test_a_command_that_merely_mentions_a_push_is_not_a_receipt():
    store, segs = _node([_bash("grep -rn 'git push' docs/"), _out(URL % 12)])
    assert jd.goal_pr_refs(store, segs, "g1") == []


def test_a_push_is_a_receipt():
    """A push is how work reaches an existing PR, so the PR named in that segment is this goal's."""
    store, segs = _node([_bash("git push -u fork dev/notes-index"), _text("Updated %s" % (URL % 12))])
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 12]]


def test_given_a_receipt_prose_in_the_same_segment_counts():
    """The gate is per segment: once the goal demonstrably acted, every PR that segment names is its own."""
    store, segs = _node([_bash(CREATE), _out(URL % 12), _text("This supersedes %s." % (URL % 9))])
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 12], [REPO, 9]]


def test_a_bare_number_inside_a_pr_command_is_attributed_with_no_owner():
    """`gh pr merge 503` names a PR unambiguously. The owner is left EMPTY — the command acts on the
    checkout it ran in, and only the kernel knows which repo that is."""
    store, segs = _node([_bash("gh pr merge 503 --auto --merge")])
    assert jd.goal_pr_refs(store, segs, "g1") == [["", 503]]


def test_a_pr_command_naming_another_repo_is_not_inferred():
    """--repo points somewhere other than this checkout, so the empty-owner inference would be wrong."""
    store, segs = _node([_bash("gh pr merge 503 --repo someone-else/other")])
    assert jd.goal_pr_refs(store, segs, "g1") == []


def test_flags_before_the_number_are_tolerated():
    store, segs = _node([_bash("gh pr edit --add-label ready 503")])
    assert jd.goal_pr_refs(store, segs, "g1") == [["", 503]]


# ── url shape ────────────────────────────────────────────────────────────────────────────────────────

def test_a_bare_hash_number_is_never_mined_even_with_a_receipt():
    store, segs = _node([_bash(CREATE), _text("Resolve audit #8123 failing rows")])
    assert jd.goal_pr_refs(store, segs, "g1") == []


def test_a_files_deep_link_still_names_its_pr():
    store, segs = _node([_bash(CREATE), _out(URL % 12 + "/files")])
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 12]]


def test_a_trailing_suffix_is_not_a_pr_number():
    store, segs = _node([_bash(CREATE), _out(URL % 12 + "x")])
    assert jd.goal_pr_refs(store, segs, "g1") == []


def test_duplicates_collapse_and_order_is_kept():
    store, segs = _node([_bash(CREATE), _out("%s\n%s\n%s" % (URL % 7, URL % 5, URL % 7))])
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 7], [REPO, 5]]


def test_refs_are_unfiltered_by_repo():
    """This side has the atoms but not the session's checkout, so it records what it saw; the kernel, which
    knows the remote, keeps only what belongs to the session's own repo."""
    store, segs = _node([_bash(CREATE),
                         _out("https://github.com/someone-else/other/pull/9\n" + URL % 3)])
    assert jd.goal_pr_refs(store, segs, "g1") == [["someone-else/other", 9], [REPO, 3]]


# ── gathering ────────────────────────────────────────────────────────────────────────────────────────

def test_a_subtree_s_segments_are_not_the_parent_s():
    """The pane rolls a parent up by walking the tree it already draws. Mining the subtree here too made
    every ancestor accumulate every descendant's PRs — five unrelated numbers on one top goal, on live
    data (2026-08-18)."""
    store = {"nodes": {"g1": {"trail": ["p1"], "parentId": None},
                       "g2": {"trail": ["p2"], "parentId": "g1"}},
             "placements": {}}
    segs = {"p1": {"id": "p1", "t": 1, "atoms": []},
            "p2": {"id": "p2", "t": 2, "atoms": [_bash(CREATE), _out(URL % 21)]}}
    assert jd.goal_pr_refs(store, segs, "g1") == []
    assert jd.goal_pr_refs(store, segs, "g2") == [[REPO, 21]], "it belongs to the child that did it"


def test_the_placement_fallback_finds_an_orphaned_trail_key():
    """A trail key can orphan for good; placements are the second, drift-proof route to the same history
    (the same fallback _goal_work_text carries)."""
    store = {"nodes": {"g1": {"trail": [], "parentId": None}}, "placements": {"q9#d": "g1"}}
    segs = {"q9": {"id": "q9", "t": 3, "atoms": [_bash(CREATE), _out(URL % 44)]}}
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 44]]


def test_no_segments_yields_nothing():
    store = {"nodes": {"g1": {"trail": []}}, "placements": {}}
    assert jd.goal_pr_refs(store, {}, "g1") == []


def test_a_growing_segment_is_rescanned():
    """An open segment gains atoms while its turn runs; pinning the refs to the first scan would lose a url
    printed later in that same segment."""
    store, segs = _node([_bash(CREATE)])
    sid = next(iter(segs))
    assert jd.goal_pr_refs(store, segs, "g1") == []
    segs[sid]["atoms"].append(_out(URL % 77))
    assert jd.goal_pr_refs(store, segs, "g1") == [[REPO, 77]]


def test_the_memo_is_keyed_by_segment_not_by_its_refs():
    """Regression: the scan loop shadowed the cache key with the last matched ref, so the entry landed under
    that ref instead of (id, size) — the memo never hit and filled with junk keys."""
    jd._SEG_PR_CACHE.clear()
    store, segs = _node([_bash(CREATE), _out(URL % 12)])
    jd.goal_pr_refs(store, segs, "g1")
    sid = next(iter(segs))
    assert (sid, 2) in jd._SEG_PR_CACHE
    assert all(isinstance(k[0], str) and isinstance(k[1], int) and k[0].startswith("s")
               for k in jd._SEG_PR_CACHE), sorted(jd._SEG_PR_CACHE)


# ── the store stamp ──────────────────────────────────────────────────────────────────────────────────

def test_record_pr_refs_stamps_the_store_and_only_on_change():
    """The read side has no seg_by_id, so the fact is produced here and merely read there — and an unchanged
    session must leave the store byte-identical."""
    store, segs = _node([_bash(CREATE), _out(URL % 8)])
    assert jd._record_pr_refs(store, segs) is True
    assert store["nodes"]["g1"]["prRefs"] == [[REPO, 8]]
    assert jd._record_pr_refs(store, segs) is False, "no change → no write"


def test_record_pr_refs_writes_no_key_for_a_prless_goal():
    """Most goals ship no PR; none of them should grow a key for it."""
    store, segs = _node([_text("no links here")])
    assert jd._record_pr_refs(store, segs) is False
    assert "prRefs" not in store["nodes"]["g1"]


def test_record_pr_refs_keeps_refs_when_only_part_of_the_goal_resolves():
    """A goal continued after a /clear has a segment before it (carrying the PR) and one after. The
    earlier one being outside the parse is no evidence the PR went away, so its ref stays and the later
    segment's refs join it."""
    store, segs = _node([_bash(CREATE), _out(URL % 8)])
    later = "s%d" % next(_seg_n)
    store["nodes"]["g1"]["trail"].append(later)
    segs[later] = {"id": later, "t": 200, "atoms": [_bash(CREATE), _out(URL % 9)]}
    jd._record_pr_refs(store, segs)
    assert store["nodes"]["g1"]["prRefs"] == [[REPO, 8], [REPO, 9]]
    jd._record_pr_refs(store, {later: segs[later]})
    assert store["nodes"]["g1"]["prRefs"] == [[REPO, 8], [REPO, 9]]


def test_record_pr_refs_clears_refs_whose_segments_resolve_without_them():
    """Every recorded segment resolves and none carries a receipt: the stamp goes."""
    store, segs = _node([_text("no receipt")])
    store["nodes"]["g1"]["prRefs"] = [[REPO, 8]]
    assert jd._record_pr_refs(store, segs) is True
    assert store["nodes"]["g1"]["prRefs"] is None


def test_record_pr_refs_keeps_a_goal_whose_segments_are_not_in_this_parse():
    """After a /clear the walk stops at the new transcript's root, so no earlier goal's segment resolves:
    that is "not in this parse", not "has no PR", and the stamp stays."""
    store, segs = _node([_bash(CREATE), _out(URL % 8)])
    jd._record_pr_refs(store, segs)
    assert jd._record_pr_refs(store, {}) is False
    assert store["nodes"]["g1"]["prRefs"] == [[REPO, 8]]


def test_record_pr_refs_indexes_the_parse_once_per_pass(monkeypatch):
    """Each goal resolved its segments through a fresh index over the whole parse, nodes x segments key
    computations per pass. The index is now built once, so the count grows with nodes + segments."""
    nodes, segs = {}, {}
    for i in range(40):
        sid = "s%d" % next(_seg_n)
        nodes["g%d" % i] = {"trail": [sid], "text": "goal %d" % i}
        segs[sid] = {"id": sid, "t": i, "atoms": [_text("no links")]}
    calls = []
    real = jd._seg_key
    monkeypatch.setattr(jd, "_seg_key", lambda k: (calls.append(k), real(k))[1])
    jd._record_pr_refs({"nodes": nodes, "placements": {}}, segs)
    assert len(calls) <= 2 * (len(nodes) + len(segs)), len(calls)


def test_a_bare_number_is_read_only_from_its_own_command_and_past_flag_values():
    """Chained commands, flag values and redirections never lend their numbers to the PR."""
    for cmd in ("gh pr merge --auto --merge && sleep 30", 'gh pr edit --title "Fix 3 bugs" --add-label fix',
                "gh pr merge --squash --delete-branch 2>&1 | tail -n 5", "gh pr merge -R other/repo 503"):
        store, segs = _node([_bash(cmd)])
        assert jd.goal_pr_refs(store, segs, "g1") == [], cmd
    store, segs = _node([_bash("gh pr edit --add-label ready 503")])
    assert jd.goal_pr_refs(store, segs, "g1") == [["", 503]]
