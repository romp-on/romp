#!/usr/bin/env python3
"""An automatic compaction shows as compacting everywhere a manual one does (2026-09-19, the user: a session at its
context ceiling sat "unresponsive" while it compacted on its own). The SDK backend's bracket, SdkSession._compacting,
was set only when romp delivered a /compact; the CLI's stream says when ANY compaction starts and ends (the `status`
SystemMessage: "compacting", then null with compact_result), and the backend now reads it. Every surface reads the one
bracket through compacting(sid) and the kernel's shared chip derivation, so the chip says "compacting" from the stream
frame alone. Hermetic: a minted state root with the hosts off, synthetic sids, no CLI."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _f:
    _f.write("off")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_autocompact", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_autocompact", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-dddddddddd01"


class _Sys:
    """A stand-in for the SDK's SystemMessage (isinstance, subtype, data)."""
    def __init__(self, subtype, data=None):
        self.subtype = subtype; self.data = dict(data or {}, subtype=subtype, type="system")


class _Asst:
    pass


class _Res:
    pass


def _status(status, **fields):
    return _Sys("status", dict(fields, status=status))


class StreamBracket(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        Path(self.root, "session-hosts").write_text("off")
        self.be = sb.SdkBackend(self.root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        self.pokes = []
        self.be._poke = lambda: self.pokes.append(1)
        self.s = sb.SdkSession(self.be, {"sid": SID, "name": "web", "cwd": self.root})
        self.be.sessions[SID] = self.s

    def _feed(self, msg):
        self.s._on_message(msg, _Asst, _Res, _Sys)

    def _feed_boundary(self, msg):
        """The boundary branch schedules a context refresh on the loop; feed it inside one, as the CLI's stream does."""
        calls = []
        async def refresh(): calls.append(1)
        self.s._do_refresh_context = refresh
        async def run():
            self._feed(msg)
            await asyncio.sleep(0)
        asyncio.run(run())
        self.assertEqual(calls, [1], "the boundary re-pulls the context as before")

    def test_the_streams_compacting_status_sets_the_bracket_and_pokes_once(self):
        self.assertIs(self.be.compacting(SID), False, "quiet to begin with")
        self._feed(_status("compacting"))
        self.assertIs(self.s._compacting, True, "the stream's status frame opens the bracket (the base opened it only for a /compact romp delivered)")
        self.assertIs(self.be.compacting(SID), True, "compacting(sid), what every surface reads")
        self.assertEqual(len(self.pokes), 1, "one poke: every surface flips at once, as the init branch does for the model")
        self._feed(_status("compacting"))
        self.assertEqual((self.s._compacting, len(self.pokes)), (True, 1), "a repeated frame changes nothing and pokes nothing")

    def test_the_end_is_any_null_without_a_permission_mode(self):
        """The CLI 2.1.257 bundle, read by grep, ends a compaction with a null status in three shapes: carrying the result
        after a compaction that ran; bare after a PreCompact hook blocked it (the "compacting" status comes before the
        hooks run, and no boundary follows); bare after the reactive compaction of a too-long prompt ended. Its two
        permission-mode emitters both carry permissionMode and never a compaction field, and "requesting" marks each
        stream request. So the rule (round two of the review, which found the result-keyed clear leaving a hook-blocked
        compaction's bracket open for the rest of the turn, parking the drain): a null without permissionMode clears, a
        null with permissionMode never touches the bracket."""
        self._feed(_status("compacting"))
        self._feed(_status("requesting"))                        # the request start the CLI emits at each stream request
        self.assertIs(self.be.compacting(SID), True, "requesting is not a compaction's edge")
        self._feed(_status(None, permissionMode="default"))      # the CLI's permission-mode change: a null WITH the mode
        self.assertIs(self.be.compacting(SID), True, "a null carrying permissionMode is a mode change, not the compaction's end")
        self.assertEqual(len(self.pokes), 1)
        self._feed(_status(None, compact_result={"trigger": "auto", "preTokens": 180000}))
        self.assertIs(self.be.compacting(SID), False, "the null carrying the compaction's result closes the bracket")
        self.assertEqual(len(self.pokes), 2, "and pokes once more")
        self._feed(_status(None, compact_result={"trigger": "auto"}))
        self.assertEqual(len(self.pokes), 2, "closing a closed bracket pokes nothing")
        self._feed(_status("compacting")); self._feed(_status(None, compact_error="context too small"))
        self.assertIs(self.be.compacting(SID), False, "a compaction that failed ends too")
        self._feed(_status("compacting")); self._feed(_status(None))
        self.assertIs(self.be.compacting(SID), False, "a hook-blocked compaction ends with a BARE null and no boundary: cleared")
        self._feed(_status("compacting")); self._feed(_status(None, uuid="11111111-2222-3333-4444-eeeeeeeeee01", session_id=SID))
        self.assertIs(self.be.compacting(SID), False, "the reactive path's bare null (identity fields only) clears too")
        self.assertEqual(len(self.pokes), 8, "each edge poked once: four opens, four closes")

    def test_the_boundary_still_clears_and_a_manual_compact_is_unchanged(self):
        self._feed(_status("compacting"))
        self._feed_boundary(_Sys("compact_boundary", {"compact_metadata": {"trigger": "auto"}}))
        self.assertIs(self.be.compacting(SID), False, "the boundary record clears the bracket as before")
        # the manual road: send() sets the bracket at the delivery of a /compact; the stream then says the same thing
        self.assertTrue(sb._is_compact_cmd("/compact"))
        self.s._compacting = True                                # what send() does for a /compact (the enqueue-time bracket)
        self.pokes.clear()
        self._feed(_status("compacting"))
        self.assertEqual((self.be.compacting(SID), len(self.pokes)), (True, 0), "already open: the stream's frame adds nothing")
        self._feed(_status(None, permissionMode="default"))
        self.assertIs(self.be.compacting(SID), True, "a mode change mid-compaction leaves the manual bracket standing")
        self._feed(_status(None, permissionMode="default", compact_result={"trigger": "manual"}))
        self.assertIs(self.be.compacting(SID), False, "a null carrying a compaction field is a compaction's end even beside a mode "
                      "(no bundle emits the pair today; the mode emitters carry the mode alone)")
        self.s._compacting = True
        self._feed_boundary(_Sys("compact_boundary"))
        self.assertIs(self.be.compacting(SID), False)

    def test_the_kernels_chip_says_compacting_from_the_stream_frame_alone(self):
        """No UI change: the chip, the tab bar, the overlay card and the timeline stripe all read _session_chip, which reads
        the backend's bracket first. The frame on the stream is enough; no /compact was delivered and no row state moved."""
        saved = {n: getattr(km, n) for n in ("_clearing_now", "_session_awaiting", "_api_error", "_interrupting")}
        saved_backend = km.Sessions.backend_for
        km._clearing_now = lambda sid: False
        km._session_awaiting = lambda *a, **k: None
        km._api_error = lambda path: None
        km._interrupting = lambda *a, **k: False
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        try:
            chip = lambda: km._session_chip(SID, None, {"turns": []}, None, 1000)
            self.assertNotEqual(chip(), "compacting", "quiet to begin with")
            self._feed(_status("compacting"))
            self.assertEqual(chip(), "compacting", "the shared derivation reads the bracket the stream frame opened")
            self.assertIs(km._compacting_now(SID), True, "the drain's gate and the orphan check read the same")
            self._feed(_status(None, compact_result={"trigger": "auto"}))
            self.assertNotEqual(chip(), "compacting")
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
            km.Sessions.backend_for = saved_backend


if __name__ == "__main__":
    unittest.main()
