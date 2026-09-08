#!/usr/bin/env python3
"""The PR tier POLICY as a pure function over synthetic PR fixtures: scripts/ci/tier_policy.py, the
repository owner's rules of 2026-09-08, by tier and by the AUTHOR's role. The workflow only fetches data
and calls it; every rule here is pinned on fixtures so the gate's meaning lives in tests, not in a YAML step.

Roles are read from the author's collaborator permission in the record's permissions map: admin is the
repository owner, write or maintain a member, anyone else (including an author the map lacks, as a fork
PR's author is) a contributor. Members and contributors are gated alike; admin is the role that changes a
gate.

Tiers: docs and fix are ONE tier under two labels (tests-only is the pre-rename spelling of docs) and merge
on green for every author: the check requires no approval. feature merges on green when the author is an
admin (the owner's features merge straight away); by anyone else it needs an APPROVED review by an admin
other than the author on the current head (the owner looks first). major-feature needs, for every author,
a linked issue that someone other than the author has commented on (the opener alone does not count); a
non-admin author additionally needs that admin approval. Any PR touching .github/ or scripts/ci/, the
gate's own workflow and code, needs an admin's approval regardless of tier when the author is not an admin
(the base-branch check cannot stop a PR-branch job from posting a same-named success on pull_request
events, and nobody but the owner may rewrite the policy through a PR the check cannot see); an admin author
is exempt. A standing CHANGES_REQUESTED by a maintainer (write, maintain or admin) other than the author
holds a PR of any tier until that reviewer lifts it. No tier has a time-based path: the record carries no
time field and nothing in the policy reads one. Zero or two tier labels fail here too (belt and braces
with the label check). An approval is a reviewer's STANDING, their latest APPROVED / CHANGES_REQUESTED /
DISMISSED review (comment-only reviews never change standing; a dismissed approval never counts, whoever
dismissed it; a dismissed objection clears only when the reviewer dismissed it THEMSELVES, so the author
cannot dismiss the peer's objection away), by an admin other than the author, APPROVED, on the CURRENT
head. A renamed file counts under both paths; a file listing the API truncated makes the unseen files
guarded. An opened or reopened run waits up to a minute for the PR's tier label before judging
(OpenedGrace): the wait is the driver's, never the policy's, and what it hands the policy is whatever the
API reported last.

Synthetic only: invented logins, placeholder shas, TESTHOST-free."""
import contextlib
import importlib.util
import io
import os
import re
import tempfile
import types
import unittest
import urllib.error

HERE = os.path.dirname(os.path.realpath(__file__))
# Hermetic state BEFORE any romp code loads (the repo-wide rule the state-isolation meta-test
# enforces): the policy module itself touches no state, but the rule is uniform on purpose.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SPEC = importlib.util.spec_from_file_location(
    "tier_policy", os.path.join(os.path.dirname(HERE), "scripts", "ci", "tier_policy.py"))
tp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tp)

HEAD = "1111111111111111111111111111111111111111"
OLD = "2222222222222222222222222222222222222222"
NOW = 1_800_000_000   # orders one reviewer's reviews (submitted_at); the policy has no clock to compare it to
DAY = 86400           # only for the stale clock keys NoTimePath feeds the policy, which must ignore them


def pr(**kw):
    """A synthetic PR fixture with sensible defaults; override per test. No time field: the fetcher
    records none (pinned in FetcherShapes) and the policy reads none (pinned in NoTimePath)."""
    base = {"number": 42, "author": "author-a", "labels": ["fix"], "head_sha": HEAD,
            "files": ["kernel/kernel.py"], "reviews": [], "permissions": {},
            "files_truncated": False, "body": "", "issues": {}}
    base.update(kw)
    return base


def review(user, state="APPROVED", sha=HEAD, submitted=NOW - 60, dismissed=False, dismissed_by=None):
    """`state` is the review's ORIGINAL state (the fetcher recovers it from the review_dismissed
    timeline event for a dismissed one); `dismissed_by` is the login that dismissed it."""
    return {"user": user, "state": state, "commit_id": sha, "submitted_at": submitted,
            "dismissed": dismissed, "dismissed_by": dismissed_by}


# Roles as the check reads them, from the collaborator permission the fetcher records: admin-c holds admin
# (the repository owner); maint-b holds write (a member). author-a, the default author, is absent from
# MAINTAINERS (a contributor, as a fork PR's author is); MEMBER_AUTHOR and ADMIN_AUTHOR are the same map
# with the default author given write or admin, so a test varies the author's role the way the check
# reads it, through the map, never through the login.
MAINTAINERS = {"maint-b": "write", "admin-c": "admin"}
MEMBER_AUTHOR = dict(MAINTAINERS, **{"author-a": "write"})
ADMIN_AUTHOR = dict(MAINTAINERS, **{"author-a": "admin"})
ROLES = (("a contributor", MAINTAINERS), ("a member", MEMBER_AUTHOR), ("an admin", ADMIN_AUTHOR))


class Labels(unittest.TestCase):
    def test_zero_tier_labels_fail(self):
        v = tp.evaluate(pr(labels=[]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("exactly one", v["summary"].lower())

    def test_two_tier_labels_fail(self):
        v = tp.evaluate(pr(labels=["fix", "feature"]))
        self.assertEqual(v["conclusion"], "failure")

    def test_non_tier_labels_are_ignored(self):
        v = tp.evaluate(pr(labels=["docs", "good-first-issue"], files=["docs/guide.md"]))
        self.assertEqual(v["conclusion"], "success")


class Roles(unittest.TestCase):
    """The author's role is the collaborator permission the fetcher recorded for them; a login the map
    lacks is not an admin (the stricter gate applies, fail closed)."""

    def test_admin_is_read_from_the_permissions_map(self):
        self.assertTrue(tp._is_admin(pr(permissions=ADMIN_AUTHOR)))
        self.assertFalse(tp._is_admin(pr(permissions=MEMBER_AUTHOR)), "write is a member, not an admin")
        self.assertFalse(tp._is_admin(pr(permissions=dict(MAINTAINERS, **{"author-a": "maintain"}))),
                         "maintain is a member too")
        self.assertFalse(tp._is_admin(pr(permissions=MAINTAINERS)), "absent from the map: a contributor")
        self.assertFalse(tp._is_admin(pr(permissions={})), "an empty map makes nobody an admin")
        self.assertTrue(tp._is_admin(pr(permissions=MAINTAINERS), "admin-c"))
        self.assertFalse(tp._is_admin(pr(permissions=MAINTAINERS), "maint-b"))

    def test_members_and_contributors_are_gated_alike(self):
        # the gates below differ by admin or not; a member and a contributor grade the same on every
        # tier and on the guard, with and without an admin's approval
        for kw in (dict(labels=["feature"]),
                   dict(labels=["feature"], reviews=[review("admin-c")]),
                   dict(labels=["major-feature"], body="#7", issues=MajorFeature.ISSUE_OK),
                   dict(labels=["major-feature"], body="#7", issues=MajorFeature.ISSUE_OK, reviews=[review("admin-c")]),
                   dict(labels=["fix"], files=[".github/workflows/ci.yml"]),
                   dict(labels=["fix"], files=[".github/workflows/ci.yml"], reviews=[review("admin-c")])):
            a = tp.evaluate(pr(permissions=MAINTAINERS, **kw))
            b = tp.evaluate(pr(permissions=MEMBER_AUTHOR, **kw))
            self.assertEqual(a["conclusion"], b["conclusion"], kw)


class OnGreen(unittest.TestCase):
    """docs and fix: one tier under two labels, merging on green for every author (the owner, 2026-09-08).
    The check requires no approval; a standing change request by a maintainer other than the author is the
    one hold, and only that reviewer's next word lifts it."""

    def test_a_fix_passes_on_green_with_no_reviews(self):
        v = tp.evaluate(pr(labels=["fix"]))
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("merges on green", v["summary"])
        self.assertIn("no approval is required", v["summary"])

    def test_a_docs_pr_passes_the_same_way(self):
        v = tp.evaluate(pr(labels=["docs"], files=["docs/guide.md"]))
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("merges on green", v["summary"])

    def test_every_author_role_merges_on_green(self):
        for role, perms in ROLES:
            for tier in ("docs", "fix"):
                v = tp.evaluate(pr(labels=[tier], permissions=perms))
                self.assertEqual(v["conclusion"], "success", "%s by %s" % (tier, role))
                self.assertIn("every author", v["summary"])

    def test_docs_and_fix_are_one_tier(self):
        # the label names the tier; it does not filter files. The 2026-09-07 text made docs
        # documentation-only because it alone merged on green; with fix merging on green too the two
        # labels are one tier to the check, and the same record grades the same under either
        for files in (["docs/guide.md", "README.md"], ["kernel/kernel.py"], ["VERSION"]):
            a = tp.evaluate(pr(labels=["docs"], files=files))
            b = tp.evaluate(pr(labels=["fix"], files=files))
            self.assertEqual((a["conclusion"], b["conclusion"]), ("success", "success"), files)

    def test_the_pre_rename_label_is_the_same_tier(self):
        v = tp.evaluate(pr(labels=["tests-only"], files=["docs/guide.md"]))
        self.assertEqual(v["conclusion"], "success")
        v = tp.evaluate(pr(labels=["tests-only", "docs"], files=["docs/guide.md"]))
        self.assertEqual(v["conclusion"], "failure", "both spellings at once are two tier labels")
        v = tp.evaluate(pr(labels=["docs", "fix"]))
        self.assertEqual(v["conclusion"], "failure", "one tier, still two labels: the count rule is unchanged")

    def test_an_approval_is_not_required_and_changes_nothing(self):
        for who in ("maint-b", "admin-c"):
            v = tp.evaluate(pr(labels=["fix"], reviews=[review(who)], permissions=MAINTAINERS))
            self.assertEqual(v["conclusion"], "success")
            self.assertIn("merges on green", v["summary"])

    def test_a_standing_change_request_by_another_maintainer_holds_a_fix(self):
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("Changes requested by maint-b", v["summary"])
        self.assertIn("until they say otherwise", v["summary"])

    def test_the_same_objection_holds_a_docs_pr(self):
        v = tp.evaluate(pr(labels=["docs"], files=["docs/guide.md"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "failure")

    def test_the_objection_holds_an_admin_authors_fix_too(self):
        # a standing objection by a maintainer other than the author blocks every tier for every author
        v = tp.evaluate(pr(labels=["fix"], permissions=ADMIN_AUTHOR,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("maint-b", v["summary"])

    def test_an_objection_on_an_older_head_still_holds(self):
        # an objection asks "did a maintainer object?"; a push does not answer it, that reviewer does
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED", sha=OLD)]))
        self.assertEqual(v["conclusion"], "failure")

    def test_another_maintainers_approval_does_not_lift_the_objection(self):
        # the check requires no approval, so an approval has no role in this tier: an objection is an
        # objection until its reviewer says otherwise, an admin's approval included
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED"), review("admin-c")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("maint-b", v["summary"])

    def test_the_objectors_own_approval_lifts_it(self):
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED", submitted=NOW - 600),
                                    review("maint-b", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "success")

    def test_an_objection_by_a_reviewer_without_write_does_not_hold(self):
        v = tp.evaluate(pr(labels=["fix"], permissions={"drive-by-d": "read"},
                           reviews=[review("drive-by-d", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "success", "the rule names a maintainer other than the author")

    def test_the_authors_own_change_request_is_not_an_objection(self):
        v = tp.evaluate(pr(labels=["fix"], permissions=MEMBER_AUTHOR,
                           reviews=[review("author-a", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "success", "the rule names a maintainer OTHER than the author")


class Feature(unittest.TestCase):
    """feature: by an admin author, merges on green (the owner's features merge straight away); by a member
    or a contributor, one approval by an admin other than the author on the current head (the owner looks
    first). No issue, no discussion, no waiting period."""

    def test_an_admin_authors_feature_passes_with_no_reviews(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=ADMIN_AUTHOR))
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("admin author merges on green", v["summary"])

    def test_an_admin_authors_feature_is_still_held_by_a_standing_objection(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=ADMIN_AUTHOR,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("Changes requested by maint-b", v["summary"])

    def test_a_member_authors_feature_fails_without_an_admin_approval(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("no approval by an admin", v["summary"])

    def test_a_member_authors_feature_fails_with_a_write_holders_approval(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("maint-b")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("maint-b approved but does not hold admin", v["summary"])

    def test_a_member_authors_feature_passes_with_an_admin_approval_on_the_current_head(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("Approved by admin-c", v["summary"])

    def test_a_member_authors_feature_fails_with_an_admin_approval_on_an_older_head(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c", sha=OLD)]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("older head", v["summary"])

    def test_a_contributors_feature_is_gated_like_a_members(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MAINTAINERS))
        self.assertEqual(v["conclusion"], "failure")
        v = tp.evaluate(pr(labels=["feature"], permissions=MAINTAINERS, reviews=[review("maint-b")]))
        self.assertEqual(v["conclusion"], "failure", "a write-holder's approval meets no gate")
        v = tp.evaluate(pr(labels=["feature"], permissions=MAINTAINERS, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "success")

    def test_an_author_the_permissions_map_lacks_is_not_an_admin(self):
        # an older fetcher's record, or a hand-built one, without the author's permission: fail closed
        v = tp.evaluate(pr(labels=["feature"], permissions={}))
        self.assertEqual(v["conclusion"], "failure")

    def test_a_discussed_issue_is_not_asked_of_a_feature_and_does_not_stand_in_for_the_approval(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, body="#7", issues=MajorFeature.ISSUE_OK))
        self.assertEqual(v["conclusion"], "failure")
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "success", "no issue linked, and none needed")


class Approval(unittest.TestCase):
    """What counts as an approval, and how a reviewer's standing is read. Exercised on a member author's
    feature (which needs an admin's approval) and on fix (where only an objection matters)."""

    def test_an_admin_approval_on_the_current_head_counts(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "success")

    def test_the_author_cannot_approve_their_own_pr(self):
        # no gate asks an admin author for an approval, so this is pinned on the helper: were one added,
        # the author's own word still would not meet it
        ok, why = tp._approved_by_admin(pr(author="admin-c", permissions=MAINTAINERS, reviews=[review("admin-c")]))
        self.assertFalse(ok)
        self.assertIn("no approval by an admin other than the author", why)

    def test_a_reviewer_without_write_does_not_count(self):
        v = tp.evaluate(pr(labels=["feature"], reviews=[review("drive-by-d")],
                           permissions={"drive-by-d": "read"}))
        self.assertEqual(v["conclusion"], "failure")
        self.assertNotIn("drive-by-d", v["summary"], "a read-only reviewer is not the nearest miss to name")

    def test_only_the_LATEST_review_per_reviewer_counts(self):
        # approved, then changes requested: the latest word stands
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                           reviews=[review("admin-c", submitted=NOW - 600),
                                    review("admin-c", state="CHANGES_REQUESTED", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "failure")
        # changes requested, then approved: also the latest word
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                           reviews=[review("admin-c", state="CHANGES_REQUESTED", submitted=NOW - 600),
                                    review("admin-c", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "success")

    def test_dismissing_a_later_objection_does_not_revive_an_earlier_approval(self):
        # a DISMISSED review is the reviewer's latest word (a non-approval), never an erasure: anyone
        # with write can dismiss - the review's catch
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                           reviews=[review("admin-c", submitted=NOW - 600),
                                    review("admin-c", state="CHANGES_REQUESTED", submitted=NOW - 60, dismissed=True,
                                           dismissed_by="admin-c")]))
        self.assertEqual(v["conclusion"], "failure")

    # Dismissals read fail-closed in both directions. A dismissed APPROVED never counts, whoever dismissed
    # it: a review dismisses only once, so ignoring a third party's dismissal would let the author spend
    # it first and lock the approval in while the PR page shows it struck out (the review's catch
    # against the symmetric rule). A dismissed CHANGES_REQUESTED clears only when the reviewer dismissed it
    # themselves; an author with write access cannot dismiss the peer's objection to merge on green
    # (the maintainers' ruling of 2026-09-07, kept through the owner's rules of 2026-09-08).
    def test_a_reviewer_dismissing_their_own_objection_clears_it(self):
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED", dismissed=True, dismissed_by="maint-b")]))
        self.assertEqual(v["conclusion"], "success", "withdrawn by its author: not a standing objection")

    def test_the_author_cannot_dismiss_the_peers_objection_to_merge_on_green(self):
        for perms in (MEMBER_AUTHOR, ADMIN_AUTHOR):
            v = tp.evaluate(pr(labels=["fix"], permissions=perms,
                               reviews=[review("maint-b", state="CHANGES_REQUESTED", dismissed=True, dismissed_by="author-a")]))
            self.assertEqual(v["conclusion"], "failure")
            self.assertIn("Changes requested by maint-b", v["summary"])

    def test_a_reviewer_dismissing_their_own_approval_withdraws_it(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                           reviews=[review("admin-c", dismissed=True, dismissed_by="admin-c")]))
        self.assertEqual(v["conclusion"], "failure")

    def test_an_approval_dismissed_by_anyone_never_counts(self):
        for who in ("author-a", "maint-b"):          # the author; another maintainer
            v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                               reviews=[review("admin-c", dismissed=True, dismissed_by=who)]))
            self.assertEqual(v["conclusion"], "failure", "dismissed by %s: the PR page shows it struck out" % who)

    def test_a_reviewer_whose_objection_was_dismissed_by_another_lifts_it_by_approving(self):
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED", submitted=NOW - 600, dismissed=True,
                                           dismissed_by="author-a"),
                                    review("maint-b", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "success")

    def test_a_dismissal_of_unknown_actor_fails_closed_both_ways(self):
        # the fetcher raises before it builds such a record; the policy still fails closed on it
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c", dismissed=True)]))
        self.assertEqual(v["conclusion"], "failure", "an approval dismissed by nobody-knows-who is no approval")
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED", dismissed=True)]))
        self.assertEqual(v["conclusion"], "failure", "...and an objection dismissed by nobody-knows-who still stands")

    def test_a_comment_review_is_not_an_approval(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c", state="COMMENTED")]))
        self.assertEqual(v["conclusion"], "failure")

    def test_a_later_comment_review_does_not_erase_an_approval(self):
        # GitHub records every inline comment as a COMMENTED review; a reviewer's standing is their
        # latest APPROVED / CHANGES_REQUESTED / DISMISSED and comments never change it - the review's
        # catch: a maintainer who approved and then left one note read as "no approval"
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                           reviews=[review("admin-c", submitted=NOW - 600),
                                    review("admin-c", state="COMMENTED", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "success")

    def test_a_later_comment_review_does_not_lift_a_change_request(self):
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED", submitted=NOW - 600),
                                    review("maint-b", state="COMMENTED", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "failure", "the objection stands; a comment is not saying otherwise")

    def test_a_pending_review_changes_nothing(self):
        v = tp.evaluate(pr(labels=["feature"], permissions=MEMBER_AUTHOR,
                           reviews=[review("admin-c", submitted=NOW - 600),
                                    review("admin-c", state="PENDING", submitted=NOW - 60)]))
        self.assertEqual(v["conclusion"], "success")


class MajorFeature(unittest.TestCase):
    """major-feature: for every author, a linked issue someone other than the author commented on (the
    write-up and its discussion); a member or a contributor additionally needs an admin's approval on the
    current head."""
    ISSUE_OK = {7: {"exists": True, "is_pr": False, "user": "author-a", "comments": ["maint-b"]}}

    def test_an_admin_authors_major_feature_passes_on_a_discussed_issue_alone(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR,
                           body="Design discussion in #7.", issues=self.ISSUE_OK))
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("issue #7", v["summary"])

    def test_an_admin_authors_major_feature_fails_without_the_discussion(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("discussed linked issue", v["summary"])
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "failure", "another admin's approval does not stand in for the write-up")
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR, body="#7",
                           issues={7: {"exists": True, "is_pr": False, "user": "author-a", "comments": ["author-a"]}}))
        self.assertEqual(v["conclusion"], "failure", "the author's own comments are not a discussion")

    def test_a_member_authors_major_feature_needs_both(self):
        discussed = dict(labels=["major-feature"], permissions=MEMBER_AUTHOR, body="#7", issues=self.ISSUE_OK)
        v = tp.evaluate(pr(**discussed))
        self.assertEqual(v["conclusion"], "failure", "discussed, unapproved")
        self.assertIn("needs an admin's approval", v["summary"])
        v = tp.evaluate(pr(**dict(discussed, reviews=[review("maint-b")])))
        self.assertEqual(v["conclusion"], "failure", "a write-holder's approval meets no gate")
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "failure", "approved, undiscussed")
        self.assertIn("discussed linked issue is required", v["summary"])
        v = tp.evaluate(pr(**dict(discussed, reviews=[review("admin-c")])))
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("Approved by admin-c", v["summary"])
        self.assertIn("issue #7", v["summary"])

    def test_a_contributors_major_feature_needs_both_as_well(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MAINTAINERS, body="#7", issues=self.ISSUE_OK))
        self.assertEqual(v["conclusion"], "failure")
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MAINTAINERS, body="#7", issues=self.ISSUE_OK,
                           reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "success")

    def test_an_issue_url_counts_too(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR,
                           body="See https://github.com/romp-on/romp/issues/7 for the discussion.",
                           issues=self.ISSUE_OK))
        self.assertEqual(v["conclusion"], "success")

    def test_approval_without_a_linked_issue_fails(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("issue", v["summary"].lower())

    def test_a_linked_issue_with_only_the_authors_comments_is_not_a_discussion(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")],
                           body="#7", issues={7: {"exists": True, "is_pr": False, "user": "author-a",
                                                   "comments": ["author-a"]}}))
        self.assertEqual(v["conclusion"], "failure")

    def test_the_issue_opener_alone_is_not_a_discussion(self):
        # the maintainers' ruling (2026-09-07, the discussion issue's third point): discussion means a
        # COMMENT by someone other than the author; an issue a maintainer filed and the author answered
        # alone is not one, and the fetcher no longer records the opener
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c")],
                           body="#7", issues={7: {"exists": True, "is_pr": False, "user": "maint-b",
                                                   "comments": ["author-a"]}}))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("comment by someone other than the author", v["summary"])

    def test_a_linked_PR_number_is_not_an_issue(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR,
                           body="#7", issues={7: {"exists": True, "is_pr": True, "user": "maint-b",
                                                   "comments": ["maint-b"]}}))
        self.assertEqual(v["conclusion"], "failure")

    def test_an_approval_on_an_older_head_fails_here_too(self):
        v = tp.evaluate(pr(labels=["major-feature"], permissions=MEMBER_AUTHOR, reviews=[review("admin-c", sha=OLD)],
                           body="#7", issues=self.ISSUE_OK))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("older head", v["summary"])

    def test_a_standing_objection_holds_a_discussed_and_approved_major_feature(self):
        for perms in (MEMBER_AUTHOR, ADMIN_AUTHOR):
            v = tp.evaluate(pr(labels=["major-feature"], permissions=perms, body="#7", issues=self.ISSUE_OK,
                               reviews=[review("admin-c"), review("maint-b", state="CHANGES_REQUESTED")]))
            self.assertEqual(v["conclusion"], "failure")
            self.assertIn("maint-b", v["summary"])


class NoTimePath(unittest.TestCase):
    """No tier has a time-based path (the owner, 2026-09-08). Mutation guard in both directions: the
    seven-day clock the 2026-09-07 text gave fix is gone from the module, and a record still carrying the
    old clock keys (an older fetcher, a hand-built fixture) changes no verdict."""

    def test_the_module_has_no_clock(self):
        for gone in ("SEVEN_DAYS", "chain_start", "_clock_since", "_is_doc"):
            self.assertFalse(hasattr(tp, gone), gone)
        consts = " ".join(str(c) for f in vars(tp).values() if isinstance(f, types.FunctionType)
                          for c in f.__code__.co_consts)
        for key in ("first_check_at", "head_floor", "created_at", "now", "seven", "days",
                    "committer", "author_date", "commit_date"):
            self.assertIsNone(re.search(r"\b%s\b" % key, consts), key)

    def test_stale_clock_keys_on_a_record_change_nothing(self):
        stale = {"first_check_at": NOW - 30 * DAY, "head_floor": NOW - 30 * DAY,
                 "created_at": NOW - 30 * DAY, "now": NOW}
        for perms in (MAINTAINERS, MEMBER_AUTHOR):
            v = tp.evaluate(pr(labels=["feature"], permissions=perms, **stale))
            self.assertEqual(v["conclusion"], "failure", "a month-old unapproved feature still waits for its approval")
            v = tp.evaluate(pr(labels=["major-feature"], permissions=perms, body="#7", issues=MajorFeature.ISSUE_OK, **stale))
            self.assertEqual(v["conclusion"], "failure", "...and a month-old discussed one too")
            v = tp.evaluate(pr(labels=["fix"], files=["scripts/ci/tier_policy.py"], permissions=perms, **stale))
            self.assertEqual(v["conclusion"], "failure", "nor is the guard on the gate's own code outwaited")
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR, **stale))
        self.assertEqual(v["conclusion"], "failure", "an admin author's month-old undiscussed major feature still waits")
        v = tp.evaluate(pr(labels=["fix"], permissions=MAINTAINERS,
                           reviews=[review("maint-b", state="CHANGES_REQUESTED")], **stale))
        self.assertEqual(v["conclusion"], "failure", "an objection is not outwaited")
        for label in ("fix", "feature", "major-feature"):
            self.assertNotIn("seven", tp.evaluate(pr(labels=[label], **stale))["summary"].lower())


class GithubDir(unittest.TestCase):
    """The gate's own files: a member or a contributor needs an admin's approval whatever the tier (merging
    on green never clears them, and a write-holder's approval meets no gate); an admin author, the owner,
    is exempt."""

    def test_a_file_listing_the_api_truncated_makes_the_unseen_files_guarded(self):
        # the files endpoint returns at most 3000 entries; when the PR's changed_files says there are
        # more, the unseen files are assumed guarded
        v = tp.evaluate(pr(labels=["docs"], files=["docs/a.md"], files_truncated=True))
        self.assertEqual(v["conclusion"], "failure", "docs merges on green, but not with unseen files")
        self.assertIn("3000", v["summary"])
        v = tp.evaluate(pr(labels=["fix"], files_truncated=True))
        self.assertEqual(v["conclusion"], "failure", "the same for fix")
        v = tp.evaluate(pr(labels=["fix"], files_truncated=True, reviews=[review("maint-b")], permissions=MAINTAINERS))
        self.assertEqual(v["conclusion"], "failure", "a write-holder's approval does not clear it")
        v = tp.evaluate(pr(labels=["fix"], files_truncated=True, reviews=[review("admin-c")], permissions=MAINTAINERS))
        self.assertEqual(v["conclusion"], "success", "an admin's approval clears it")
        v = tp.evaluate(pr(labels=["fix"], files_truncated=True, permissions=ADMIN_AUTHOR))
        self.assertEqual(v["conclusion"], "success", "an admin author is exempt: the unseen files are the owner's own")

    def test_the_gates_own_code_needs_an_admins_approval_from_a_non_admin_author(self):
        # the policy is checked out from main and run with checks:write, so a PR rewriting
        # scripts/ci/tier_policy.py and merging on green would have graded itself
        for perms in (MAINTAINERS, MEMBER_AUTHOR):
            for tier in ("docs", "fix"):
                v = tp.evaluate(pr(labels=[tier], files=["scripts/ci/tier_policy.py"], permissions=perms))
                self.assertEqual(v["conclusion"], "failure", tier)
                self.assertIn("scripts/ci/tier_policy.py", v["summary"])
                self.assertIn("admin's approval", v["summary"])
            v = tp.evaluate(pr(labels=["fix"], files=["scripts/ci/tier_policy.py"], reviews=[review("maint-b")],
                               permissions=perms))
            self.assertEqual(v["conclusion"], "failure", "a write-holder's approval does not meet the guard")
            v = tp.evaluate(pr(labels=["fix"], files=["scripts/ci/tier_policy.py"], reviews=[review("admin-c")],
                               permissions=perms))
            self.assertEqual(v["conclusion"], "success")

    def test_touching_github_requires_an_admins_approval_regardless_of_tier(self):
        v = tp.evaluate(pr(labels=["fix"], files=[".github/workflows/ci.yml"], permissions=MEMBER_AUTHOR))
        self.assertEqual(v["conclusion"], "failure", "merging on green never clears a .github change")
        self.assertIn(".github", v["summary"])
        v = tp.evaluate(pr(labels=["docs"], files=[".github/PULL_REQUEST_TEMPLATE.md"], permissions=MEMBER_AUTHOR))
        self.assertEqual(v["conclusion"], "failure", "markdown under .github is still .github")
        v = tp.evaluate(pr(labels=["fix"], files=[".github/workflows/ci.yml"], reviews=[review("admin-c")],
                           permissions=MEMBER_AUTHOR))
        self.assertEqual(v["conclusion"], "success")
        v = tp.evaluate(pr(labels=["feature"], files=[".github/workflows/ci.yml"], reviews=[review("admin-c")],
                           permissions=MEMBER_AUTHOR))
        self.assertEqual(v["conclusion"], "success", "one admin approval meets the guard and the feature gate")

    def test_an_admin_author_is_exempt_from_the_guard(self):
        # the owner may change the gate; the guard exists so that nobody else rewrites it unread
        for tier in ("docs", "fix", "feature"):
            v = tp.evaluate(pr(labels=[tier], permissions=ADMIN_AUTHOR,
                               files=[".github/workflows/tier-policy.yml", "scripts/ci/tier_policy.py"]))
            self.assertEqual(v["conclusion"], "success", tier)
        v = tp.evaluate(pr(labels=["major-feature"], permissions=ADMIN_AUTHOR, files=[".github/workflows/ci.yml"],
                           body="#7", issues=MajorFeature.ISSUE_OK))
        self.assertEqual(v["conclusion"], "success", "the tier's own gate still applies, the guard does not")

    def test_an_objection_holds_a_guarded_fix_even_when_an_admin_approved(self):
        # the approval meets the guard; the objection still holds the tier
        v = tp.evaluate(pr(labels=["fix"], files=[".github/workflows/ci.yml"], permissions=MAINTAINERS,
                           reviews=[review("admin-c"), review("maint-b", state="CHANGES_REQUESTED")]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("maint-b", v["summary"])


class Verdict(unittest.TestCase):
    def test_the_verdict_names_the_tier_and_says_why(self):
        v = tp.evaluate(pr(labels=["feature"]))
        self.assertEqual(v["conclusion"], "failure")
        self.assertIn("feature", v["title"])
        self.assertTrue(v["summary"], "a failing verdict always says what would clear it")


class WorkflowPins(unittest.TestCase):
    """The workflow is the policy's only door; its trust-model facts are source-pinned."""

    def setUp(self):
        self.wf = open(os.path.join(os.path.dirname(HERE), ".github", "workflows", "tier-policy.yml")).read()
        self.fetch = open(os.path.join(os.path.dirname(HERE), "scripts", "ci", "tier_policy_check.py")).read()

    def test_the_check_run_is_named_tier_policy(self):
        self.assertIn("name: Tier policy", self.wf)
        self.assertIn('CHECK_NAME = "Tier policy"', self.fetch)

    def test_the_header_states_the_rules_by_the_authors_role(self):
        head = self.wf[:self.wf.index("\non:")]
        for phrase in ("author's role", "admin", "merge on green for every author"):
            self.assertIn(phrase, head, phrase)

    def _triggers(self):
        # the trigger MAPPING, not a grep of the file: the comments deliberately name the events they
        # exclude, and a grep would trip on its own explanation
        on = [l for l in self.wf.split("\n") if l and not l.startswith("#")]
        block, inside = [], False
        for l in on:
            if l.startswith("on:"):
                inside = True; continue
            if inside and l and not l.startswith(" "):
                break
            if inside and l.startswith("  ") and not l.startswith("   ") and l.strip().endswith(":"):
                block.append(l.strip().rstrip(":"))
        return block

    def test_the_gate_runs_from_the_base_branch_never_the_pr(self):
        trig = self._triggers()
        self.assertIn("pull_request_target", trig)
        self.assertNotIn("pull_request", trig, "a pull_request trigger would run the PR's copy")
        self.assertNotIn("pull_request_review", trig,
                         "the review event runs in the PR's merge-commit context - approvals ride the schedule")
        self.assertIn("schedule", trig)
        self.assertIn("workflow_dispatch", trig)

    def test_the_schedule_exists_for_approval_propagation_only(self):
        m = re.search(r'- cron: "[^"]+"\s*#\s*(.*)', self.wf)
        self.assertTrue(m, "the schedule line carries its reason")
        self.assertIn("approval propagation", m.group(1))
        self.assertNotIn("seven", self.wf.lower(), "no clock rides the schedule")

    def test_the_token_holds_only_what_the_verdict_needs(self):
        self.assertIn("checks: write", self.wf)
        for line in ("pull-requests: read", "issues: read", "contents: read"):
            self.assertIn(line, self.wf)
        self.assertNotIn("contents: write", self.wf)
        self.assertNotIn("pull-requests: write", self.wf)

    def test_the_job_never_runs_pr_code(self):
        # the only checkout is the base tree; no ref: pointing at the PR head, and the fetcher is
        # API reads plus one check-run POST
        self.assertNotIn("ref:", self.wf)
        self.assertNotIn("head.sha", self.wf)
        self.assertEqual(self.fetch.count('_req("POST"'), 1, "exactly one write: the verdict")
        self.assertIn('"/repos/%s/check-runs"', self.fetch)

    def test_the_fetcher_reads_no_clock_input(self):
        # nothing in the policy is timed, so the fetcher lists no check-run history, no timeline and
        # never the commit itself (whose author/committer dates are the author's to set)
        for gone in ("/commits/", "/timeline", "chain_start", "first_check_at", "head_floor",
                     "RESET_EVENTS", "VERDICT_GAP", "import time", '["committer"]', '["author"]["date"]'):
            self.assertNotIn(gone, self.fetch, gone)
        self.assertNotIn("seven", self.fetch.lower())

    def test_the_job_name_is_NOT_the_check_name(self):
        # the job's own check run must not share the required check's name: two same-named runs per
        # head (the job's, frozen at push time, and the API-posted verdict the hourly sweep moves) leave
        # it undocumented which one the ruleset honors - so only the API-posted verdict carries the name
        jobs = self.wf[self.wf.index("\njobs:"):]
        self.assertIn("    name: Tier policy evaluation", jobs)
        self.assertNotRegex(jobs, r"name: Tier policy[ \t]*\n")

    def test_the_step_hands_the_fetcher_the_event_action(self):
        # the opened/reopened grace keys on the EVENT; the payload's labels are never read (the API is)
        self.assertIn("ACTION: ${{ github.event.action }}", self.wf)
        self.assertNotIn("github.event.pull_request.labels", self.wf)
        self.assertIn('os.environ.get("ACTION")', self.fetch)

    def test_the_three_tier_label_lists_agree(self):
        wf = open(os.path.join(os.path.dirname(HERE), ".github", "workflows", "pr-tier.yml")).read()
        tmpl = open(os.path.join(os.path.dirname(HERE), ".github", "PULL_REQUEST_TEMPLATE.md")).read()
        in_jq = set(re.findall(r'\. == "([a-z-]+)"', wf))
        expected = set(tp.TIERS) | set(tp.TIER_ALIASES)
        self.assertEqual(in_jq, expected, "the label check and the policy name the same tiers")
        for t in tp.TIERS:
            self.assertIn("`%s`" % t, tmpl, "the PR template lists every tier")


class FetcherShapes(unittest.TestCase):
    """build_record against the DOCUMENTED response shapes, with _req stubbed - no network. A request the
    stub does not know (a check-run listing, a timeline, the commit itself) is an AssertionError: the
    fetcher reads nothing the policy would time. Collaborator permissions are served per login (admin-c
    admin, maint-b and author-a write); an unknown login is the API's 404, a non-collaborator."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "tier_policy_check", os.path.join(os.path.dirname(HERE), "scripts", "ci", "tier_policy_check.py"))
        self.tc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tc)
        self.calls = []
        test = self

        def paged(pages, route, query):
            # pages are served BY URL (the review's catch: a stub serving by call count let a fetcher
            # that never follows the Link URL pass); a page asked for twice is a looping fetcher
            import urllib.parse as up
            n = int((up.parse_qs(query).get("page") or ["1"])[0])
            test.assertNotIn((route, n), test.served, "page requested twice: not following Link")
            test.served.add((route, n))
            more = n < len(pages)
            nxt = "%s/repos/romp-on/romp/pulls/42/%s?per_page=100&page=%d" % (test.tc.API, route, n + 1)
            last = "%s/repos/romp-on/romp/pulls/42/%s?per_page=100&page=%d" % (test.tc.API, route, len(pages))
            hdrs = {"Link": '<%s>; rel="next", <%s>; rel="last"' % (nxt, last)} if more else {}
            return pages[n - 1], hdrs

        def fake_req(method, path, token, body=None):
            test.calls.append((method, path))
            path, _, query = path.partition("?")       # _get_all appends per_page; match the route
            if path.endswith("/pulls/42"):
                return {"head": {"sha": HEAD}, "user": {"login": test.author}, "labels": [{"name": l} for l in test.labels],
                        "created_at": "2026-08-30T00:00:00Z", "body": "fixes #7",
                        "changed_files": test.changed_files}, {}
            if "/files" in path:
                if test.files_error:
                    raise urllib.error.HTTPError(path, test.files_error, "x", {}, None)
                return paged(test.files_pages, "files", query)
            if "/reviews" in path:
                return test.reviews, {}
            if "/collaborators/" in path:
                if test.perm_error:
                    raise urllib.error.HTTPError(path, test.perm_error, "x", {}, None)
                login = path.split("/collaborators/")[1].split("/")[0]
                if login not in test.perms:
                    raise urllib.error.HTTPError(path, 404, "x", {}, None)
                return {"permission": test.perms[login]}, {}
            if path.endswith("/events"):
                return test.events, {}
            if path.endswith("/issues/7/comments"):
                return [{"user": {"login": "maint-b"}}, {"user": {"login": "stale[bot]", "type": "Bot"}}], {}
            if path.endswith("/issues/7"):
                return {"number": 7, "user": {"login": "author-a"}}, {}
            if "/issues/" in path and path.endswith("/comments"):
                return [], {}
            if "/issues/" in path:
                test.issue_fetches.append(path)
                return {"number": 0, "user": {"login": "author-a"}}, {}
            raise AssertionError("unexpected request " + path)
        self.perm_error = None
        self.files_error = None
        self.perms = {"author-a": "write", "maint-b": "write", "admin-c": "admin"}
        self.author = "author-a"
        self.labels = ["fix"]
        self.files_pages = [[{"filename": "kernel/kernel.py", "status": "modified"}]]
        self.served = set()
        self.changed_files = 1
        self.reviews = [{"id": 1, "user": {"login": "admin-c"}, "state": "APPROVED", "commit_id": HEAD,
                         "submitted_at": "2026-09-02T00:00:00Z"}]
        self.events = []
        self.issue_fetches = []
        self.tc._req = fake_req

    def test_build_record_survives_the_documented_shapes_and_has_no_time_field(self):
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertEqual(set(rec), {"number", "author", "labels", "head_sha", "files", "files_truncated", "reviews",
                                    "permissions", "body", "issues"},
                         "the record has exactly the documented keys: no time field, no commit date")
        self.assertEqual(rec["permissions"], {"admin-c": "admin", "author-a": "write"},
                         "every reviewer AND the author: the policy reads the author's role from this map")
        self.assertEqual(rec["issues"], {7: {"exists": True, "is_pr": False, "comments": ["maint-b"]}},
                         "the bot commenter is filtered; the opener is not recorded (they do not count)")
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "success")
        self.assertFalse(any("/commits/" in p or p.endswith("/timeline") for _, p in self.calls),
                         "no check-run history, no timeline, no commit: nothing the policy would time")

    def test_an_unreviewed_fix_passes_through_the_fetcher_too(self):
        self.reviews = []
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        v = self.tc.evaluate(rec)
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("merges on green", v["summary"])

    def test_the_authors_role_reaches_the_policy(self):
        # a member's feature waits for the admin's approval; the same PR by an admin merges on green
        self.labels = ["feature"]
        self.reviews = []
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertEqual(rec["permissions"], {"author-a": "write"}, "no reviewer: the author alone is looked up")
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "failure")
        self.perms["author-a"] = "admin"
        self.served = set()                  # a second, independent build: the page ledger starts over
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertEqual(rec["permissions"], {"author-a": "admin"})
        v = self.tc.evaluate(rec)
        self.assertEqual(v["conclusion"], "success")
        self.assertIn("admin author merges on green", v["summary"])

    def test_a_non_collaborator_author_reads_as_none(self):
        # a fork PR by an outside contributor: the permission endpoint 404s, recorded as "none"
        self.author = "outsider-x"
        self.labels = ["feature"]
        self.reviews = []
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertEqual(rec["permissions"], {"outsider-x": "none"})
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "failure", "a contributor's feature waits for the admin")
        rec["labels"] = ["fix"]
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "success", "a contributor's fix merges on green")

    def test_a_permission_lookup_error_other_than_404_raises(self):
        # a 403 or 5xx mapped to "none" would deny every approval (and read every author as a contributor)
        # while posting a normal-looking verdict; the fetcher fails loudly instead
        self.perm_error = 403
        with self.assertRaises(urllib.error.HTTPError):
            self.tc.build_record("romp-on/romp", 42, "tok")

    def test_a_push_during_evaluation_raises_instead_of_grading_a_mixed_record(self):
        real = self.tc._req
        heads = iter([HEAD, OLD])

        def moving(method, path, token, body=None):
            if path.split("?")[0].endswith("/pulls/42"):
                return {"head": {"sha": next(heads)}, "user": {"login": "author-a"}, "labels": [{"name": "fix"}],
                        "created_at": "2026-08-30T00:00:00Z", "body": "", "changed_files": 1}, {}
            return real(method, path, token, body)
        self.tc._req = moving
        with self.assertRaises(RuntimeError):
            self.tc.build_record("romp-on/romp", 42, "tok")

    def test_a_rename_out_of_github_carries_both_paths(self):
        # the review's HIGH: `git mv .github/workflows/tier-policy.yml docs/gate-notes.md` in a docs PR
        # read as documentation-only and would have merged on green, removing the gate from main
        self.files_pages = [[{"filename": "docs/gate-notes.md", "status": "renamed",
                              "previous_filename": ".github/workflows/tier-policy.yml"}]]
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertEqual(rec["files"], ["docs/gate-notes.md", ".github/workflows/tier-policy.yml"])
        self.assertFalse(rec["files_truncated"], "one entry, two paths: truncation counts entries, not paths")
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "success", "approved by an admin: the guard is met")
        rec["reviews"] = []
        for tier in ("docs", "fix"):
            rec["labels"] = [tier]
            self.assertEqual(self.tc.evaluate(rec)["conclusion"], "failure",
                             "a .github change wearing a docs destination does not merge on green as %s" % tier)

    def test_a_two_page_file_listing_is_read_whole(self):
        self.files_pages = [[{"filename": "a.py", "status": "modified"}], [{"filename": "b.py", "status": "added"}]]
        self.changed_files = 2
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertEqual(rec["files"], ["a.py", "b.py"])
        self.assertFalse(rec["files_truncated"])
        self.assertTrue(any("/files" in p and "page=2" in p for _, p in self.calls), "the Link URL was followed")

    def test_a_listing_shorter_than_changed_files_is_flagged_truncated(self):
        self.changed_files = 3001
        rec = self.tc.build_record("romp-on/romp", 42, "tok")
        self.assertTrue(rec["files_truncated"])
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "success", "an admin-approved fix still passes")
        rec["reviews"] = []
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "failure", "unapproved, the unseen files hold it")
        rec["labels"] = ["docs"]
        self.assertEqual(self.tc.evaluate(rec)["conclusion"], "failure", "docs cannot vouch for unseen files either")


class OpenedGrace(unittest.TestCase):
    """The opened/reopened run's wait for a label - wait_for_tier_label, run_one's first step - against a
    _req stub that serves the PR's labels BY READ COUNT: the world in which `gh pr create --label` lands the
    label a moment after the run's first read (the label check failed eighteen PRs that way on 2026-09-08;
    this check raced the same way). The policy stays pure: the wait is the driver's, and what it hands
    evaluate() is whatever the API reported last. TIER_GRACE_STEP_SECS=0 runs every wait in no time; the
    default knobs are pinned once, through a recording pause, as a minute. As in FetcherShapes, a request
    the stub does not know is an AssertionError: the wait re-reads the PR and nothing else."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "tier_policy_check", os.path.join(os.path.dirname(HERE), "scripts", "ci", "tier_policy_check.py"))
        self.tc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tc)
        self.reads = 0                      # GETs of the PR itself; the record's other reads are not counted
        self.posted = []
        self.labels_by_read = [[]]          # the labels served on read 1, 2, ...; the last entry repeats
        self.files = [{"filename": "kernel/kernel.py", "status": "modified"}]
        test = self

        def fake_req(method, path, token, body=None):
            p = path.split("?")[0]
            if method == "POST":
                test.posted.append(body)
                return {}, {}
            if p.endswith("/pulls"):
                return [{"number": 42}], {}
            if p.endswith("/pulls/42"):
                test.reads += 1
                labels = test.labels_by_read[min(test.reads, len(test.labels_by_read)) - 1]
                return {"head": {"sha": HEAD}, "user": {"login": "author-a"}, "labels": [{"name": l} for l in labels],
                        "created_at": "2026-08-30T00:00:00Z", "body": "", "changed_files": len(test.files)}, {}
            if p.endswith("/files"):
                return test.files, {}
            if p.endswith("/reviews") or p.endswith("/events"):
                return [], {}
            if p.endswith("/collaborators/author-a/permission"):
                return {"permission": "write"}, {}          # a member: docs and fix merge on green
            raise AssertionError("unexpected request " + path)
        self.tc._req = fake_req
        self.saved = {k: os.environ.get(k) for k in ("TIER_GRACE_STEP_SECS", "TIER_GRACE_TRIES", "ACTION", "GITHUB_TOKEN")}
        os.environ["TIER_GRACE_STEP_SECS"] = "0"
        for k in ("TIER_GRACE_TRIES", "ACTION", "GITHUB_TOKEN"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run_one(self, action, labels_by_read, tries=None):
        """run_one for PR 42 with the labels the API will report on each read: (verdict, how many times the
        driver said it was waiting)."""
        self.labels_by_read = labels_by_read
        self.reads, self.posted = 0, []
        if tries is not None:
            os.environ["TIER_GRACE_TRIES"] = str(tries)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            v = self.tc.run_one("romp-on/romp", 42, "tok", action=action)
        return v, out.getvalue().count("waiting for one")

    # the label lands on the FOURTH read of the PR: a run that waits sees it; a run that judges at once
    # reads the PR three times (the head, the record, the closing head check) and never does
    LANDS_LATE = [[], [], [], ["fix"]]

    def test_a_just_opened_pr_waits_for_its_label_and_is_judged_on_it(self):
        v, waits = self._run_one("opened", self.LANDS_LATE)
        self.assertEqual((v["conclusion"], v["title"]), ("success", "Tier policy: fix"), v)
        self.assertIn("merges on green", v["summary"])
        self.assertEqual(waits, 3, "one wait per empty re-read until the label landed")
        self.assertEqual(self.posted[-1]["conclusion"], "success", "the verdict posted is the sorted PR's")

    def test_a_docs_pr_whose_label_lands_on_the_second_read_is_judged_as_docs(self):
        self.files = [{"filename": "docs/pr-tiers.md", "status": "modified"}]
        v, waits = self._run_one("opened", [[], ["docs"]])
        self.assertEqual((v["conclusion"], v["title"]), ("success", "Tier policy: docs"), v)
        self.assertEqual(waits, 1)
        v, waits = self._run_one("reopened", [[], ["tests-only"]])
        self.assertEqual((v["conclusion"], v["title"]), ("success", "Tier policy: docs"),
                         "the pre-rename spelling ends the wait too")
        self.assertEqual(waits, 1)

    def test_a_pr_that_stays_unlabelled_still_fails_after_the_bounded_wait(self):
        v, waits = self._run_one("reopened", [[]], tries=3)
        self.assertEqual((v["conclusion"], v["title"]), ("failure", "Tier policy: 0 tier labels"), v)
        self.assertEqual(waits, 3, "the bounded retries, then the verdict")
        self.assertEqual(self.posted[-1]["conclusion"], "failure")

    def test_every_other_event_judges_at_once(self):
        for action in ("labeled", "unlabeled", "synchronize", "edited", "", None):
            v, waits = self._run_one(action, self.LANDS_LATE)
            self.assertEqual((v["conclusion"], v["title"]), ("failure", "Tier policy: 0 tier labels"),
                             "%r: a label removed, or a push to an unsorted PR, is not a race to wait out" % (action,))
            self.assertEqual(waits, 0, action)

    def test_two_labels_on_an_opened_pr_never_wait(self):
        v, waits = self._run_one("opened", [["fix", "feature"]])
        self.assertEqual((v["conclusion"], v["title"]), ("failure", "Tier policy: 2 tier labels"), v)
        self.assertEqual(waits, 0, "a doubly sorted PR is a sort error, never a race")

    def test_the_wait_reads_exactly_until_the_label_lands_and_no_further(self):
        # the helper itself, with a counting fetcher: the first read is the caller's; the wait's own reads
        # stop on the read that shows the label
        self.labels_by_read = [[], [], ["fix"]]
        pr = self.tc.wait_for_tier_label("romp-on/romp", 42, "tok", {"labels": []}, "opened")
        self.assertEqual(self.reads, 3)
        self.assertEqual([l["name"] for l in pr["labels"]], ["fix"], "the PR object as last read comes back")
        self.reads, self.labels_by_read = 0, [[]]       # a PR that never gets its label
        os.environ["TIER_GRACE_TRIES"] = "4"
        pr = self.tc.wait_for_tier_label("romp-on/romp", 42, "tok", {"labels": []}, "reopened")
        self.assertEqual(self.reads, 4, "bounded: the retries, no more")
        self.assertEqual(pr["labels"], [], "...and what comes back is the unlabelled PR, for the caller to fail")
        self.reads = 0
        bare, two = {"labels": []}, {"labels": [{"name": "fix"}, {"name": "docs"}]}
        for action in ("labeled", "unlabeled", "synchronize", "edited", "", None):
            self.assertIs(self.tc.wait_for_tier_label("romp-on/romp", 42, "tok", bare, action), bare)
        self.assertIs(self.tc.wait_for_tier_label("romp-on/romp", 42, "tok", two, "opened"), two)
        self.assertEqual(self.reads, 0, "no other event, and no PR with a label, reads again")

    def test_the_default_wait_is_a_minute_of_five_second_pauses(self):
        os.environ.pop("TIER_GRACE_STEP_SECS", None)
        paused = []
        self.tc.wait_for_tier_label("romp-on/romp", 42, "tok", {"labels": []}, "opened", pause=paused.append)
        self.assertEqual(paused, [5.0] * 12, "twelve five-second pauses: a minute, then the verdict")
        self.assertEqual(self.reads, 12)

    def test_main_hands_run_one_the_events_action_and_the_sweep_never_waits(self):
        os.environ["GITHUB_TOKEN"] = "tok"
        os.environ["ACTION"] = "opened"
        self.labels_by_read = self.LANDS_LATE
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.tc.main(["--pr", "42"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(out.getvalue().count("waiting for one"), 3, "the --pr run read ACTION and waited")
        self.reads, self.posted = 0, []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tc.main(["--all-open"])
        self.assertEqual(self.posted[-1]["output"]["title"], "Tier policy: 0 tier labels",
                         "the sweep judged the unlabelled PR at once...")
        self.assertEqual(out.getvalue().count("waiting for one"), 0, "...and never waited, whatever ACTION says")
