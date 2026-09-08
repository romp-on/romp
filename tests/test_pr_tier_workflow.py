#!/usr/bin/env python3
"""The "Exactly one tier label" check (.github/workflows/pr-tier.yml) judges the PR's CURRENT labels, read
from the API, and runs one job at a time per PR, keeping every run: none cancelled, none replaced.

The race this pins closed (pull 1039, 2026-09-08): `gh pr create --label` fires `opened` with an empty
label snapshot and `labeled` a second later; the opened run judged the snapshot and failed, and when its
job COMPLETED two seconds after the labeled run's, the merge box (which follows the newest same-named
check run by time) took that stale failure and the PR read blocked with auto-merge armed. Two things make
ordering irrelevant now: every run reads the labels the PR carries at the moment it looks (the API, with
the job's read-only token), and a concurrency group keyed on the PR number runs one job at a time, which
makes completion times monotonic in execution order, so the run that executes last is the newest.
`queue: max` keeps every pending run (GitHub's default keeps one and replaces it, leaving a "cancelled"
check run), so each event's run executes and the last has read the world after the last event.
Cancel-in-progress is deliberately off: cancelling the in-progress run risks its "cancelled" check run
completing after the survivor's, and a cancelled required check blocks; GitHub also refuses
`queue: max` beside `cancel-in-progress: true`.

The opened run's grace (2026-09-08, later the same day): live reads made the ORDER harmless, but the
opened run itself still failed whenever it read before `gh pr create --label` landed the label, and each
failure notified the author about a state that lasted seconds (eighteen that day, one per PR opened). So an
opened or reopened run that finds NO tier label re-reads the API every five seconds for up to a minute
(TIER_GRACE_STEP_SECS / TIER_GRACE_TRIES, so these tests run the wait in no time) and then judges; a PR
that stays unlabelled still fails, two labels never wait, and every other event's run judges at once.

The step's script is run for real, with `gh` replaced by a shim on PATH that prints a canned label list
(or fails, or answers each read with the next list in a sequence and counts the reads), so the zero /
one / two / alias / unreadable / waiting cases are behaviour, not a grep. Source pins
cover what the script cannot show: the trigger types, the read-only token (the workflow's one
permissions block, no job-level override), the concurrency stanza and the check's name, which the
ruleset requires by name and app and so must never change. The accepted label SET is read back from the
workflow, not pinned here: tests/test_tier_policy.py (the tier-policy change) pins it against the policy.

Synthetic only: an invented repository name and PR number, no network."""
import os
import re
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
WF = os.path.join(os.path.dirname(HERE), ".github", "workflows", "pr-tier.yml")


def _run_block(src):
    """The text of the step's `run: |` block, de-indented (no YAML library in the test deps; the
    workflow is small and its shape is pinned here)."""
    m = re.search(r"^( +)run: \|\n((?:\1  .*(?:\n|\Z)|\n)+)", src, re.M)
    if not m:
        raise AssertionError("no `run: |` block found")
    indent = len(m.group(1)) + 2
    script = "".join(line[indent:] if line.strip() else line for line in m.group(2).splitlines(True))
    # a file saved without its final newline used to drop `exit 1` and fail two tests as `0 != 1`,
    # pointing nowhere near the extractor (review find): the block's last line is pinned instead
    if script.rstrip().splitlines()[-1].strip() != "exit 1":
        raise AssertionError("the extracted script does not end in `exit 1`; the extractor lost a line")
    return script


def _accepted_labels(src):
    return set(re.findall(r'\. == "([a-z-]+)"', src))


class LabelCountBehaviour(unittest.TestCase):
    """The script against a shimmed `gh`."""

    def setUp(self):
        self.src = open(WF).read()
        self.script = _run_block(self.src)
        self.bin = tempfile.mkdtemp()
        self.accepted = sorted(_accepted_labels(self.src))
        self.assertTrue(self.accepted, "the jq filter names the tier labels")

    def _gh(self, body, fail=False):
        p = os.path.join(self.bin, "gh")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\n" + ("exit 1\n" if fail else "printf '%s' '" + body + "'\n"))
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)

    def _run(self, **extra):
        """The step's script as the workflow runs it; `extra` is the step env a case adds (ACTION,
        TIER_GRACE_TRIES). No ACTION means a run that belongs to no event, which judges at once."""
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""),
                   GITHUB_REPOSITORY="example/repo", PR_NUMBER="7", GH_TOKEN="synthetic",
                   TIER_GRACE_STEP_SECS="0")
        for k in ("ACTION", "TIER_GRACE_TRIES"):
            env.pop(k, None)               # hermetic: the caller's shell lends the step nothing
        env.update(extra)
        return subprocess.run(["bash", "-e", "-c", self.script], env=env, capture_output=True, text=True, timeout=60)

    def _gh_sequence(self, bodies):
        """A `gh` that answers each call with the next canned body (the last one repeats), counting calls
        in a file beside it: the opened run's world, in which the label lands a moment after the read."""
        p = os.path.join(self.bin, "gh")
        counter = os.path.join(self.bin, "calls")
        if os.path.exists(counter):
            os.unlink(counter)             # a fresh shim starts its count at zero
        lines = ["#!/bin/sh", "n=$(cat '%s' 2>/dev/null || echo 0)" % counter, "n=$((n + 1))",
                 "printf '%%s' \"$n\" > '%s'" % counter, "case \"$n\" in"]
        for i, b in enumerate(bodies[:-1]):
            lines.append("  %d) printf '%%s' '%s' ;;" % (i + 1, b))
        lines.append("  *) printf '%%s' '%s' ;;" % bodies[-1])
        lines.append("esac")
        with open(p, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        return counter

    def _calls(self, counter):
        with open(counter) as fh:
            return int(fh.read().strip() or 0)

    def test_exactly_one_tier_label_passes(self):
        self._gh('["%s"]' % self.accepted[0])
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_non_tier_label_beside_the_tier_label_is_ignored(self):
        self._gh('["good-first-issue", "%s"]' % self.accepted[0])
        self.assertEqual(self._run().returncode, 0)

    def test_zero_tier_labels_fail(self):
        # the opened event's world, as the API reports it BEFORE the label call lands
        self._gh('[]')
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("0 tier labels", r.stdout)

    def test_two_tier_labels_fail(self):
        self._gh('["%s", "%s"]' % (self.accepted[0], self.accepted[1]))
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("2 tier labels", r.stdout)

    def test_a_just_opened_pr_waits_for_its_label_and_then_passes(self):
        # `gh pr create --label` fires `opened` before the label lands: the first two reads see none,
        # the third sees the label. The opened run waits and judges the sorted PR.
        counter = self._gh_sequence(['[]', '[]', '["%s"]' % self.accepted[0]])
        r = self._run(ACTION="opened", TIER_GRACE_TRIES="5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._calls(counter), 3)
        self.assertIn("waiting for one", r.stdout)

    def test_a_just_opened_pr_that_stays_unlabelled_still_fails_after_the_wait(self):
        counter = self._gh_sequence(['[]'])
        r = self._run(ACTION="reopened", TIER_GRACE_TRIES="3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("0 tier labels", r.stdout)
        self.assertEqual(self._calls(counter), 4, "the first read plus three bounded retries")

    def test_a_label_event_with_no_label_fails_at_once_and_two_labels_never_wait(self):
        counter = self._gh_sequence(['[]', '["%s"]' % self.accepted[0]])
        r = self._run(ACTION="unlabeled", TIER_GRACE_TRIES="5")
        self.assertEqual(r.returncode, 1, "a label was removed: the PR is unsorted now, no race to wait out")
        self.assertEqual(self._calls(counter), 1)
        counter = self._gh_sequence(['["%s", "%s"]' % (self.accepted[0], self.accepted[1]), '["%s"]' % self.accepted[0]])
        r = self._run(ACTION="opened", TIER_GRACE_TRIES="5")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._calls(counter), 1, "two labels is a sort error, never a race")

    def test_the_opened_run_reads_the_event_action_from_the_workflow(self):
        self.assertIn("ACTION: ${{ github.event.action }}", self.src)
        self.assertNotIn("github.event.pull_request.labels", self.src)

    def test_an_unreadable_api_is_a_failed_check_not_a_guess(self):
        self._gh("", fail=True)
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Could not read", r.stdout)

    def test_the_script_reads_the_pulls_endpoint_not_the_payload(self):
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER"', self.script)
        self.assertNotIn("github.event.pull_request.labels", self.src, "the payload snapshot is never consulted")


class WorkflowPins(unittest.TestCase):
    def setUp(self):
        self.src = open(WF).read()

    def test_the_check_name_is_unchanged(self):
        # the ruleset requires the check by this name and the Actions app; renaming it would silently
        # un-require it
        self.assertIn("    name: Exactly one tier label\n", self.src)

    def test_every_label_changing_event_reruns_it(self):
        m = re.search(r"pull_request:\n\s+types: \[([^\]]+)\]", self.src)
        types = {t.strip() for t in m.group(1).split(",")}
        self.assertTrue({"opened", "reopened", "labeled", "unlabeled", "synchronize"} <= types, types)

    def test_runs_for_one_pr_are_kept_and_never_cancelled(self):
        self.assertIn("concurrency:\n  group: pr-tier-${{ github.event.pull_request.number }}\n"
                      "  cancel-in-progress: false\n  queue: max\n", self.src)

    def test_the_token_is_read_only_and_passed_to_gh(self):
        self.assertIn("permissions:\n  pull-requests: read\n", self.src)
        # ONE permissions block, the workflow's: a job-level `permissions:` replaces the workflow's grant
        # for that job, so a write there would hand gh a write token past a pin that read only the top
        # (review find); and no non-comment line grants a write anywhere
        self.assertEqual(self.src.count("permissions:"), 1)
        code = "\n".join(l for l in self.src.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("write", code)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.src)
        self.assertNotIn("actions/checkout", self.src, "no checkout: nothing from the PR's tree runs")
        self.assertNotIn("uses:", self.src, "no third-party action")


if __name__ == "__main__":
    unittest.main()
