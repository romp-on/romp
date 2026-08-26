#!/usr/bin/env python3
"""The mesh/origin-aware update notice (the user 2026-08-14): the release banner only watched TAGS, so
ordinary merges sat undeployed with every machine reading "in sync" — all equally stale. The kernel now
also compares origin/main vs the checkout vs the RUNNING build, fires the same banner (kind:"main"),
and converges on click or unattended per the update mode. Pure verdict unit-tested; the wiring pinned
by source (the rail-spend pattern). Synthetic only; hermetic state dir."""
import inspect
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ["ROMP_MANAGER_PORT"] = "1"   # dead port: an unstubbed converge dials nothing real
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_drift", os.path.join(BIN, "romp-kernel")).load_module()


class DriftVerdict(unittest.TestCase):
    def test_origin_ahead_of_the_checkout_wants_a_pull(self):
        self.assertEqual(km._main_drift_verdict("aaaa1111", "bbbb2222", "bbbb2222"), ("pull", "aaaa1111"))

    def test_checkout_ahead_of_the_running_kernel_wants_a_restart(self):
        # the hand-advanced case: updated code sits on disk, nothing booted it
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "cccc3333"), ("restart", "aaaa1111"))

    def test_pull_outranks_restart_when_both_hold(self):
        # origin ahead AND the running build stale: one pull converges both in a single bounce
        self.assertEqual(km._main_drift_verdict("aaaa1111", "bbbb2222", "cccc3333")[0], "pull")

    def test_in_sync_is_quiet(self):
        self.assertEqual(km._main_drift_verdict("aaaa1111", "aaaa1111", "aaaa1111"), ("", ""))

    def test_unknown_shas_never_invent_a_notice(self):
        # offline ls-remote, unreadable checkout, missing running sha: unknown, not a disagreement
        self.assertEqual(km._main_drift_verdict("", "bbbb2222", "bbbb2222"), ("", ""))
        self.assertEqual(km._main_drift_verdict("aaaa1111", "", "bbbb2222"), ("", ""))
        self.assertEqual(km._main_drift_verdict("", "", ""), ("", ""))


class DriftWiring(unittest.TestCase):
    def test_off_mode_silences_the_watcher_and_auto_converges_unattended(self):
        src = inspect.getsource(km._main_drift_check)
        self.assertIn('if _update_mode() == "off":', src)
        self.assertIn('if _update_mode() == "auto":', src)
        self.assertIn("_run_main_update(kind)", src)
        self.assertIn('"kind": "main"', src, "ask mode fires the shared banner with the drift variant")

    def test_a_dirty_shared_tree_refuses_loudly_and_rearms(self):
        src = inspect.getsource(km._run_main_update)
        self.assertIn('"status", "--porcelain"', src)
        self.assertIn("uncommitted work", src, "the refusal names the real problem")
        self.assertIn('_MAIN_DRIFT[0] = ""', src, "the notice re-fires once the tree is clean")
        self.assertIn('"checkout", "--detach", "origin/main"', src, "advance is the repo's own convention")

    def test_the_click_converges_immediately_and_auto_rides_the_quiet_window(self):
        src = inspect.getsource(km._run_main_update)
        self.assertIn('"" if immediate else "?when=quiet"', src)
        route = inspect.getsource(km)
        self.assertIn('threading.Thread(target=_run_main_update, args=(kind, True), daemon=True)', route,
                      "the banner click is the user's own deliberate cut")

    def test_auto_converges_batch_behind_the_cool_down(self):
        # the user 2026-08-15, after flipping auto: main took a merge every few minutes and auto mode
        # converged once per commit — 4+ restarts/hour, each cutting every in-flight turn. Behind the
        # cool-down, N merges inside the window become ONE restart to the LATEST sha.
        ran = []
        saved = (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
                 km._run_main_update, km._LAST_AUTO_CONVERGE[0], km._MAIN_DRIFT[0], km._MAIN_DRIFT[1])
        km._update_mode = lambda: "auto"
        km._checkout_sha = lambda: "aaa"
        km._kernel_sha = lambda: "aaa"
        km._run_main_update = lambda kind, immediate=False: ran.append(kind)
        try:
            km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
            km._LAST_AUTO_CONVERGE[0] = 0.0
            km._origin_main_sha = lambda: "bbb"
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "the first drift converges at once")
            km._origin_main_sha = lambda: "ccc"          # a new merge lands inside the window
            km._main_drift_check()
            self.assertEqual(ran, ["pull"], "inside the cool-down, no second restart")
            self.assertEqual(km._MAIN_DRIFT[0], "", "the deferred sha is NOT marked offered")
            km._LAST_AUTO_CONVERGE[0] = 0.0              # the window passes
            km._main_drift_check()
            self.assertEqual(ran, ["pull", "pull"], "past the window, one converge takes the LATEST sha")
        finally:
            (km._update_mode, km._origin_main_sha, km._checkout_sha, km._kernel_sha,
             km._run_main_update) = saved[:5]
            km._LAST_AUTO_CONVERGE[0] = saved[5]
            km._MAIN_DRIFT[0], km._MAIN_DRIFT[1] = saved[6], saved[7]

    def test_the_shell_banner_carries_the_drift_variants(self):
        src = inspect.getsource(km)
        self.assertIn("m.drift||''", src, "the shell relay forwards the drift kind")
        self.assertIn("new romp commits are on main", src)
        self.assertIn("is ready on disk", src)

    def test_the_route_acts_only_on_what_the_kernel_itself_found(self):
        src = inspect.getsource(km)
        self.assertIn('kind = "pull" if _MAIN_DRIFT[0] else ("restart" if _MAIN_DRIFT[1] else "")', src,
                      "no version or kind is ever taken from the client")


if __name__ == "__main__":
    unittest.main()


class UiOnlyConverge(unittest.TestCase):
    """A drift whose commits touch nothing the running process executes converges by rebuilding dist
    in place — kernel left up, zero cut turns (the user 2026-08-23: most changes are UI-only, and
    every restart cuts every in-flight turn). Kernel-code drift keeps the restart path unchanged."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_main_drift_verdict", "_kernel_code_changed", "_rebuild_dist",
                        "_sync_notice", "_update_mode", "_send_to_app", "_kernel_sha")}
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._REBUILT_FOR[0] = ""
        self.notices, self.banners, self.rebuilds = [], [], []
        km._sync_notice = lambda msg, ok=True: self.notices.append((msg, ok))
        km._send_to_app = lambda app, payload: self.banners.append(payload)
        km._update_mode = lambda: "ask"
        km._kernel_sha = lambda: "cur-sha"

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""
        km._REBUILT_FOR[0] = ""

    def test_ui_only_restart_drift_rebuilds_in_place_and_latches(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-ui")
        km._kernel_code_changed = lambda a, b: False
        km._rebuild_dist = lambda: (self.rebuilds.append(1), (True, ""))[1]
        km._main_drift_check()
        self.assertEqual(len(self.rebuilds), 1)
        self.assertEqual(km._REBUILT_FOR[0], "tgt-ui", "the converge latches on the target sha")
        self.assertEqual(self.banners, [], "no restart banner for a rebuild that cuts nothing")
        self.assertTrue(any("no restart" in m for m, _ in self.notices))
        km._main_drift_check()   # same target again: already converged, no second build
        self.assertEqual(len(self.rebuilds), 1)

    def test_a_failed_build_falls_through_to_the_restart_path_loudly(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-ui")
        km._kernel_code_changed = lambda a, b: False
        km._rebuild_dist = lambda: (False, "esbuild boom")
        km._main_drift_check()
        self.assertEqual(km._REBUILT_FOR[0], "", "a failed build never latches")
        self.assertTrue(any(not ok and "esbuild boom" in m for m, ok in self.notices))
        self.assertEqual([b.get("drift") for b in self.banners], ["restart"],
                         "the normal restart offer still fires")

    def test_kernel_code_drift_keeps_the_restart_path(self):
        km._main_drift_verdict = lambda o, c, k: ("restart", "tgt-kern")
        km._kernel_code_changed = lambda a, b: True
        km._rebuild_dist = lambda: self.fail("a kernel-code drift must never rebuild in place")
        km._main_drift_check()
        self.assertEqual([b.get("drift") for b in self.banners], ["restart"])

    def test_the_classifier_reads_the_touched_paths(self):
        import subprocess as sp
        from unittest.mock import patch

        class R:
            def __init__(self, out, rc=0):
                self.stdout, self.returncode = out, rc
        with patch.object(km.subprocess, "run", return_value=R("ui/webview/feed.ts\ndocs/a.md\n")):
            self.assertFalse(km._kernel_code_changed("a1", "b2"), "UI + docs only: rebuild in place")
        with patch.object(km.subprocess, "run", return_value=R("ui/webview/feed.ts\nkernel/kernel.py\n")):
            self.assertTrue(km._kernel_code_changed("a1", "b2"))
        with patch.object(km.subprocess, "run", return_value=R("", rc=128)):
            self.assertTrue(km._kernel_code_changed("a1", "b2"), "git failure: the restart is the safe converge")
        self.assertTrue(km._kernel_code_changed("", "b2"), "unknown shas: the restart is the safe converge")

    def test_the_pull_path_carries_the_same_in_place_converge(self):
        src = inspect.getsource(km._run_main_update)
        self.assertIn('if kind == "pull" and not _kernel_code_changed(_kernel_sha(), _checkout_sha()):', src)
        self.assertIn("_rebuild_dist()", src)
        self.assertIn("_REBUILT_FOR[0] = _checkout_sha()", src)
