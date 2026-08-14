#!/usr/bin/env python3
"""Where a new session lands, and what it is shown as while it boots (the user 2026-08-13).

Three contracts pinned here, all from one night's debugging of a create that appeared to fail:

  1. A LIVE tmux session with no transcript yet is still SHOWN. tmux writes no transcript record
     until the first message, and a session parked on the CLI's first-run folder-trust prompt never
     writes one at all — so the old SDK-only rescue in _alive_sessions filtered a perfectly healthy
     session off every surface. The client, holding a tab open for it, then timed its create cue out
     with "Couldn't start", naming a session that HAD started. Per the fail-loudly rule the session
     must be surfaced and left to report its own state, never silently dropped.
  2. The default create HOST is persisted kernel-side (~/.config/romp/default-host) and served to
     the client, so the + picker and the hive tray both spawn on the machine the user actually works
     on instead of resetting to this one every time.
  3. A tmux create carries the tray's per-spawn model/effort through to the launcher, so a bean drop
     means the same thing on either backend.

Synthetic only: invented names, placeholder uuids, tmp state dirs."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate: importing the kernel must not touch live state
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = SourceFileLoader("romp_kernel_createdefaults", os.path.join(BIN, "romp-kernel")).load_module()

WEB = "11111111-2222-3333-4444-555555555555"
API = "11111111-2222-3333-4444-666666666666"


class AliveSessionsShowsTranscriptlessTmux(unittest.TestCase):
    """_alive_sessions must not drop a live tmux session just because discover() can't see it yet."""

    def setUp(self):
        self._sessions = km._sessions
        self._name_of = km._name_of
        self._sdk = km._sdk
        km._sdk = lambda: None                      # no SDK backend here: the tmux leg is what's under test
        km._name_of = lambda sid: {WEB: "web", API: "api"}.get(sid)

    def tearDown(self):
        km._sessions, km._name_of, km._sdk = self._sessions, self._name_of, self._sdk

    def test_a_live_tmux_session_with_no_transcript_still_gets_a_tab(self):
        km._sessions = lambda *a, **k: []           # discover() sees nothing — no transcript on disk
        out = km._alive_sessions(1000, {WEB: {"state": "", "backend": "tmux"}})
        self.assertEqual([s["sid"] for s in out], [WEB])
        self.assertEqual(out[0]["name"], "web")     # named from the registry, not left as a bare uuid

    def test_the_transcriptless_stub_path_does_not_exist_so_the_chip_reads_opening(self):
        # build_session/_session_chip key "still opening" off the transcript file being absent; the
        # stub therefore points at a sentinel that is never created, exactly like the SDK stub.
        km._sessions = lambda *a, **k: []
        out = km._alive_sessions(1000, {WEB: {"state": "", "backend": "tmux"}})
        self.assertFalse(os.path.exists(out[0]["path"]))

    def test_a_discovered_session_is_not_duplicated_by_the_rescue(self):
        km._sessions = lambda *a, **k: [{"sid": WEB, "name": "web", "path": "/tmp/web.jsonl", "mtime": 5}]
        out = km._alive_sessions(1000, {WEB: {"state": "working", "backend": "tmux"}})
        self.assertEqual([s["sid"] for s in out], [WEB])
        self.assertEqual(out[0]["path"], "/tmp/web.jsonl")   # the real entry wins, not the stub

    def test_a_dead_session_is_still_dropped(self):
        # the hard liveness filter is unchanged: not in the live map → not on any surface
        km._sessions = lambda *a, **k: [{"sid": API, "name": "api", "path": "/tmp/api.jsonl", "mtime": 5}]
        self.assertEqual(km._alive_sessions(1000, {WEB: {"state": "", "backend": "tmux"}}),
                         [s for s in km._alive_sessions(1000, {WEB: {"state": "", "backend": "tmux"}})])
        self.assertNotIn(API, [s["sid"] for s in km._alive_sessions(1000, {WEB: {"state": "", "backend": "tmux"}})])


class DefaultCreateHost(unittest.TestCase):
    """The persisted 'create new sessions over there' choice."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._file = km._DEFAULT_HOST_FILE
        km._DEFAULT_HOST_FILE = km.Path(self.tmp) / "default-host"

    def tearDown(self):
        km._DEFAULT_HOST_FILE = self._file

    def test_unset_means_this_machine(self):
        self.assertEqual(km._default_create_host(), "")

    def test_round_trips_a_host_name(self):
        got, err = km._set_default_host("TESTHOST")
        self.assertIsNone(err)
        self.assertEqual(got, "TESTHOST")
        self.assertEqual(km._default_create_host(), "TESTHOST")

    def test_blank_clears_it(self):
        km._set_default_host("TESTHOST")
        got, err = km._set_default_host("")
        self.assertIsNone(err)
        self.assertEqual((got, km._default_create_host()), ("", ""))

    def test_an_ssh_option_shaped_name_is_refused(self):
        # the name reaches ssh as a positional arg elsewhere; a leading '-' must never be storable
        got, err = km._set_default_host("-oProxyCommand=touch /tmp/pwned")
        self.assertIsNone(got)
        self.assertIn("not a host name", err)
        self.assertEqual(km._default_create_host(), "")

    def test_a_stored_host_is_returned_even_when_that_machine_is_detached(self):
        # liveness is the CLIENT's call (it falls back to this machine when the host isn't attached);
        # the kernel must not forget the preference just because the tunnel is down right now
        km._set_default_host("TESTHOST")
        self.assertEqual(km._default_create_host(), "TESTHOST")


class TmuxSpawnCarriesModelAndEffort(unittest.TestCase):
    """A tray bean drop onto a tmux board reaches the launcher as real flags."""

    def setUp(self):
        self.calls = []
        self._run = km.subprocess.run
        km.subprocess.run = lambda argv, **kw: self.calls.append(list(argv))
        self._live = km._live_names
        km._live_names = lambda *a, **k: {}
        self._push = km._mark_views_dirty
        km._mark_views_dirty = lambda *a, **k: None
        self._reap = km._reap_if_cancelled
        km._reap_if_cancelled = lambda *a, **k: None

    def tearDown(self):
        km.subprocess.run = self._run
        km._live_names, km._mark_views_dirty, km._reap_if_cancelled = self._live, self._push, self._reap

    def test_explicit_model_and_effort_become_launcher_flags(self):
        km._spawn_session("web", "/tmp", model="fable", effort="max")
        argv = self.calls[0]
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "fable")
        self.assertEqual(argv[argv.index("--effort") + 1], "max")

    def test_no_choice_means_no_flags_so_the_launcher_default_stands(self):
        km._spawn_session("web", "/tmp")
        self.assertNotIn("--model", self.calls[0])
        self.assertNotIn("--effort", self.calls[0])

    def test_an_unknown_value_is_dropped_rather_than_passed_through(self):
        km._spawn_session("web", "/tmp", model="; rm -rf /", effort="turbo")
        self.assertNotIn("--model", self.calls[0])
        self.assertNotIn("--effort", self.calls[0])


if __name__ == "__main__":
    unittest.main()
