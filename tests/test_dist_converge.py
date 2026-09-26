#!/usr/bin/env python3
"""The kernel serves bundles that MATCH its checkout, or says loudly that it cannot (T119, the user
2026-08-27: a restart onto real UI changes served the OLD dist for 15 minutes — a restart execs new
kernel code but nothing on that path rebuilds the bundles, and the sha-based drift check sees
checkout == running == origin, so `dv` stayed equal to every open page's LOADEDV and no raiser owed
the reload banner). _dist_converge_check runs at boot and every drift pass: dist older than the
checkout's UI sources → one in-place rebuild per distinct source state, an ok notice on success and
a LOUD one on failure — never a silent stale serve, never a retry storm. All fixtures SYNTHETIC."""
import math
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ.pop("ROMP_DIST_DIR", None)   # the seam under test — must be absent at load
km = load_source("romp_kernel_distcv", os.path.join(BIN, "romp-kernel"))


class DistConverge(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.UI, km.DIST, km.CHAT_VIEW, km._rebuild_dist, km._sync_notice)
        km.UI = td / "ui"; km.UI.mkdir()
        km.CHAT_VIEW = td / "ext"; km.CHAT_VIEW.mkdir()
        km.DIST = km.CHAT_VIEW / "dist"; km.DIST.mkdir()
        self.rebuilds, self.notices = [], []
        self.rebuild_ok = True
        def fake_rebuild():
            self.rebuilds.append(1)
            if self.rebuild_ok:
                # a real rebuild rewrites the bundles — their mtimes move past the sources
                (km.DIST / "feed.js").write_text("fresh")
                now = time.time() + 5
                os.utime(km.DIST / "feed.js", (now, now))
                return True, ""
            return False, "esbuild exploded"
        km._rebuild_dist = fake_rebuild
        km._sync_notice = lambda text, ok=True: self.notices.append((ok, text))
        km._DIST_CONVERGE_TRIED[0] = 0.0

    def tearDown(self):
        (km.UI, km.DIST, km.CHAT_VIEW, km._rebuild_dist, km._sync_notice) = self._saved
        km._DIST_CONVERGE_TRIED[0] = 0.0
        self.td.cleanup()

    def _dist(self, age=100):
        (km.DIST / "feed.js").write_text("old")
        t = time.time() - age
        os.utime(km.DIST / "feed.js", (t, t))

    def _src(self, age=50):
        (km.UI / "feed.ts").write_text("newer source")
        t = time.time() - age
        os.utime(km.UI / "feed.ts", (t, t))

    def test_stale_dist_rebuilds_in_place_with_the_ok_notice(self):
        self._dist(age=100); self._src(age=50)          # sources newer than the served bundles
        km._dist_converge_check()
        self.assertEqual(len(self.rebuilds), 1)
        self.assertTrue(self.notices and self.notices[0][0] is True)
        self.assertIn("restart doesn't rebuild", self.notices[0][1])

    def test_fresh_dist_is_left_alone(self):
        self._src(age=100); self._dist(age=50)          # bundles newer than every source
        km._dist_converge_check()
        self.assertEqual(self.rebuilds, [])
        self.assertEqual(self.notices, [])

    def test_failure_is_loud_and_never_a_retry_storm(self):
        self.rebuild_ok = False
        self._dist(age=100); self._src(age=50)
        km._dist_converge_check()
        self.assertEqual(len(self.rebuilds), 1)
        self.assertTrue(self.notices and self.notices[0][0] is False, "a failed build surfaces, never silent")
        self.assertIn("stale UI", self.notices[0][1])
        km._dist_converge_check()                        # same source state → one attempt only
        self.assertEqual(len(self.rebuilds), 1)
        self._src(age=1)                                 # the sources CHANGED → a fresh attempt is owed
        km._dist_converge_check()
        self.assertEqual(len(self.rebuilds), 2)

    def test_success_self_clears_no_second_rebuild(self):
        self._dist(age=100); self._src(age=50)
        km._dist_converge_check()
        km._dist_converge_check()
        self.assertEqual(len(self.rebuilds), 1, "the rebuilt dist is newer than the sources — settled")

    def test_a_source_saved_in_the_same_second_before_the_build_began_is_not_newer(self):
        # esbuild.js stamps every output with the build's START, fraction and all. A checkout followed at once by
        # the converge's rebuild puts the sources and that start in ONE second, the sources first; compared
        # against the whole-second token (_dist_ver rounds down) they read newer, and a current dist was rebuilt
        # once more, announced, and its dv lifted over the VSIX just installed from it (2026-09-26).
        start = math.floor(time.time() - 100) + 0.75            # the build began at .75 of a second, 100 s ago
        (km.DIST / "feed.js").write_text("built at start"); os.utime(km.DIST / "feed.js", (start, start))
        (km.UI / "feed.ts").write_text("saved just before"); os.utime(km.UI / "feed.ts", (start - 0.25, start - 0.25))
        self.assertGreater(km._dist_src_newest(), km._dist_ver(), "against the whole-second token the source reads newer")
        km._dist_converge_check()
        self.assertEqual(self.rebuilds, [], "a source saved before the build began was built: not stale")
        self.assertEqual(self.notices, [])
        # saved AFTER the build began, in the same second: it may not have been read, and it IS rebuilt
        os.utime(km.UI / "feed.ts", (start + 0.2, start + 0.2))
        km._dist_converge_check()
        self.assertEqual(len(self.rebuilds), 1)

    def test_the_lab_seam_stands_the_converge_down(self):
        self._dist(age=100); self._src(age=50)
        os.environ["ROMP_DIST_DIR"] = self.td.name
        try:
            km._dist_converge_check()
        finally:
            os.environ.pop("ROMP_DIST_DIR", None)
        self.assertEqual(self.rebuilds, [], "a redirected dist is the test's own to control")


class BannerRaiserPins(unittest.TestCase):
    """The two raiser invariants the T119 trace verified, pinned so they hold: the shim's per-frame
    dv compare, and the shell's wsFresh handler PRESERVING a latched build prompt (a resync delivers
    state, never new code)."""
    KERNEL = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()

    def test_shim_compares_dv_on_every_keepalive(self):
        self.assertIn('if(LOADEDV&&msg.dv&&msg.dv>LOADEDV)raiseBuild(msg.dv);', self.KERNEL)   # the dv rides in to the reload core, which OFFERS (2026-09-16)

    def test_wsfresh_keeps_a_latched_build_prompt(self):
        self.assertIn("else if(m&&m.romp==='wsFresh'){connStale=false;paint();}", self.KERNEL)   # the painter shows a standing build offer again (2026-09-16)
        self.assertIn("else if(offer){box.classList.add('offer');dm.textContent='Not now';show(offer.text);}", self.KERNEL)

    def test_dist_seam_is_env_keyed(self):
        self.assertIn('Path(os.environ["ROMP_DIST_DIR"]) if os.environ.get("ROMP_DIST_DIR")', self.KERNEL)


class DraggableBannerPins(BannerRaiserPins):
    """T132 (the user 2026-08-27): the banner drags out of the way so it can STAY up. Source pins on
    the shell's inline JS/CSS; the behavior itself (moves, clamps, never dismisses, holds across
    pushes, Reload after any number of moves) runs live in the lab's banner phase."""

    def test_buttons_never_start_a_drag(self):
        self.assertIn("if(e.button!==0||e.target.tagName==='BUTTON')return;", self.KERNEL)

    def test_drag_is_clamped_and_reclamped_on_resize(self):
        self.assertIn("Math.max(0,Math.min(x,window.innerWidth-r.width))", self.KERNEL)
        self.assertIn("window.addEventListener('resize',function(){if(!box.style.left)return;", self.KERNEL)

    def test_the_affordance_is_the_house_grab_pair(self):
        self.assertIn("cursor:grab;touch-action:none;", self.KERNEL)
        self.assertIn("#rstale.dragging{cursor:grabbing;user-select:none}", self.KERNEL)

    def test_dragging_never_dismisses(self):
        # the drag handlers touch style/class only — the ONLY dismiss writers stay the two buttons
        self.assertNotIn("classList.remove('show');}   # drag", self.KERNEL)
        drag = self.KERNEL[self.KERNEL.index("var drag=null;"):self.KERNEL.index("rl.onclick=")]
        self.assertNotIn("remove('show')", drag)
        self.assertNotIn("dismissed=", drag)


if __name__ == "__main__":
    unittest.main()
