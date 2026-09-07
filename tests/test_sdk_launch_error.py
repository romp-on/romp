#!/usr/bin/env python3
"""A session that CANNOT START says why — instead of swallowing every message sent to it.

The failure this closes (the user 2026-07-28, on a fresh install): romp-sdk-setup had bailed on that
machine (a python3 with no ensurepip, so the venv was never built), the kernel logged ONE stderr line and
built the SDK backend anyway, and every SDK session then accepted messages it could never run. From the
user's side: a message sent to a session did nothing at all — no flip to working, no error — a brand-new
session behaved the same, and the model/effort readouts and the usage bars stayed blank (all three publish
only AFTER a connect that could never happen). tmux sessions worked throughout, so it read as an outage at
Anthropic rather than a missing local dependency.

What is pinned here:
  1. the backend detects its own missing dependency ONCE, up front, and reports EVERY session as unable to
     start — no session has to die first for the user to be told;
  2. the text names the REMEDY (bin/romp-sdk-setup), not the symptom, and never a bare ModuleNotFoundError;
  3. a launch failure recorded on a session survives on the registry (the thread that saw it is dying) and
     is cleared by the connect that DISPROVES it, never by a timer;
  4. queued messages do NOT vanish when the session's thread dies — the persisted queue answers
     pending_queued, so what the user typed stays on screen;
  5. the account-out-of-usage flavor is classified apart, because that queue is parked, not broken.

SYNTHETIC fixtures only (placeholder ids, hostname TESTHOST).
"""
import json
import os
import sys
import tempfile
import types
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = SourceFileLoader("romp_sdk_backend_launcherr", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class _FakeSess:
    """What _record_launch_error reads off a session: its identity, plus the stderr the CLI wrote
    before it died (a real SdkSession buffers that in _stderr_tail — see _on_cli_stderr)."""

    def __init__(self, sid=SID, name="api", stderr=()):
        self.sid = sid
        self.name = name
        self._stderr_tail = list(stderr)

    def stderr_tail(self):
        return "\n".join(self._stderr_tail)


def _sess_for_options(state=None):
    """A REAL SdkSession — the stderr buffer and its callback are the things under test, so a stub
    would pin nothing. Construction is plain attribute setup; no event loop is needed."""
    td = state or tempfile.mkdtemp()
    be = _backend(td)
    return sb.SdkSession(be, {"sid": SID, "name": "api", "cwd": td, "mode": "acceptEdits"})


def _backend(state, missing=False):
    saved = sb.sdk_importable
    sb.sdk_importable = lambda: not missing
    try:
        return sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
    finally:
        sb.sdk_importable = saved


class MissingDependencyIsReportedForEverySession(unittest.TestCase):
    """The dep is checked at construction, so the report needs no session to have crashed first."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def test_every_session_reports_unable_to_start(self):
        be = _backend(self.state, missing=True)
        for sid in (SID, OTHER):
            err = be.launch_error(sid)
            self.assertIsNotNone(err, "a session that cannot possibly run must not read as fine")
            self.assertFalse(err["limit"], "a missing dependency is not a usage limit")

    def test_the_text_names_the_remedy_not_the_symptom(self):
        err = _backend(self.state, missing=True).launch_error(SID)
        self.assertIn("romp-sdk-setup", err["text"],
                      "the user needs the command to run, not the name of a python module")
        self.assertNotIn("ModuleNotFoundError", err["text"])
        self.assertIn("tmux", err["text"], "say what still works — tmux sessions are unaffected")

    def test_a_healthy_install_reports_nothing(self):
        self.assertIsNone(_backend(self.state, missing=False).launch_error(SID))


class RecordedLaunchFailures(unittest.TestCase):
    """A failure the thread saw on its way out has to outlive the thread — so it lands on the registry."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name, missing=False)

    def tearDown(self):
        self.td.cleanup()

    def _reg(self, sid=SID):
        return sb.read_reg(Path(self.td.name), sid) or {}

    def test_an_import_failure_records_the_remedy(self):
        self.be._record_launch_error(_FakeSess(), ImportError("No module named 'claude_agent_sdk'"))
        rec = self._reg()["launchError"]
        self.assertIn("romp-sdk-setup", rec["text"])
        self.assertTrue(rec["dep"])
        self.assertEqual(self.be.launch_error(SID)["text"], rec["text"])

    def test_an_ordinary_failure_keeps_its_own_text(self):
        self.be._record_launch_error(_FakeSess(), RuntimeError("claude exited with code 1"))
        rec = self._reg()["launchError"]
        self.assertIn("claude exited with code 1", rec["text"])
        self.assertFalse(rec["limit"], "a plain crash is not a usage limit")

    def test_a_fallback_launch_that_then_fails_records_the_clis_reason(self):
        # the scope wrapper's fallback notice is the first stderr line of such a launch, and it was
        # logged when it arrived (tests/test_cli_scope.py FallbackNotice); the card keeps the CLI's line
        sess = _FakeSess(stderr=[LaunchFailureText.NOTICE,
                                 "Error: No conversation found with session ID: " + SID])
        self.be._record_launch_error(sess, RuntimeError("Command failed with exit code 1"))
        text = self._reg()["launchError"]["text"]
        self.assertTrue(text.startswith("Error: No conversation found"), text)
        self.assertNotIn("romp-cli-scope", text)

    def test_the_connect_that_disproves_it_clears_it(self):
        self.be._record_launch_error(_FakeSess(), RuntimeError("transport closed"))
        self.assertIsNotNone(self.be.launch_error(SID))
        self.be._clear_launch_error(SID)
        self.assertIsNone(self.be.launch_error(SID),
                          "the record clears on the connect that disproves it, never on a timer")

    def test_clearing_a_session_that_never_failed_is_a_no_op(self):
        self.be._clear_launch_error(SID)
        self.assertIsNone(self.be.launch_error(SID))


class LaunchFailureText(unittest.TestCase):
    """Pick the line that actually names the cause, and know a usage limit when the CLI states one."""

    def test_the_clis_own_stderr_wins_over_the_exception_repr(self):
        exc = RuntimeError("Command failed")
        exc.stderr = "You've hit your session limit · resets 4:00pm (America/Los_Angeles)"
        self.assertIn("session limit", sb.launch_failure_text(exc))

    def test_a_long_stderr_dump_is_bounded(self):
        exc = RuntimeError("boom")
        exc.stderr = "x" * 5000
        self.assertLessEqual(len(sb.launch_failure_text(exc)), 601, "this text lands in a chat card")

    def test_a_bare_exception_still_yields_text(self):
        self.assertIn("ValueError", sb.launch_failure_text(ValueError("no executable found")))

    def test_the_sdks_placeholder_is_never_shown_as_the_reason(self):
        """"Check stderr output for details" is what the SDK substitutes when nobody piped the
        child's stderr. Showing it sends the user to read an output romp never captured — and it
        used to OUTRANK the exception text, so the card said strictly less than nothing."""
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        text = sb.launch_failure_text(exc)
        self.assertNotIn("Check stderr output", text)
        self.assertIn("exit code 1", text, "fall through to the text that at least names the failure")

    def test_the_captured_tail_answers_when_the_exception_only_has_the_placeholder(self):
        """The whole point of piping stderr: the CLI's own line becomes the reason shown."""
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        tail = "No conversation found with session ID: 11111111-2222-3333-4444-555555555555"
        self.assertIn("No conversation found", sb.launch_failure_text(exc, tail))

    def test_a_real_stderr_still_outranks_the_captured_tail(self):
        exc = RuntimeError("Command failed")
        exc.stderr = "claude: command not found"
        self.assertIn("command not found", sb.launch_failure_text(exc, "some older noise"))

    # bin/romp-cli-scope's fallback notice (2026-09-05): on a launch whose pre-flight scope failed it is
    # the FIRST line of the CLI's stderr, about 230 characters, and the kernel logs it the moment it
    # arrives (_note_cli_scope_fallback). Left in the tail, it led the card text of a CLI that then
    # failed at start and pushed the CLI's own reason past the 600-character cut.
    NOTICE = (sb.CLI_SCOPE_FALLBACK_PREFIX + " systemd-run cannot start a transient scope (Failed to connect to "
              "bus: No such file or directory) — running the CLI directly, outside a scope; a service restart "
              "will take its background work down")

    def test_the_scope_wrappers_fallback_notice_is_dropped_from_the_tail(self):
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        tail = self.NOTICE + "\n" + "y" * 500 + "\nNo conversation found with session ID: " + SID
        text = sb.launch_failure_text(exc, tail)
        self.assertNotIn("romp-cli-scope", text)
        self.assertTrue(text.startswith("y" * 500), "the CLI's own stderr leads the card: %r" % text[:60])
        self.assertIn("No conversation found", text, "and its reason fits inside the cut")

    def test_the_notice_is_dropped_from_the_exceptions_own_stderr_too(self):
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = self.NOTICE + "\nclaude: the CLI's own reason"
        text = sb.launch_failure_text(exc)
        self.assertNotIn("romp-cli-scope", text)
        self.assertTrue(text.startswith("claude: the CLI's own reason"), text)

    def test_a_tail_that_was_only_the_notice_falls_through_to_the_exception(self):
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        text = sb.launch_failure_text(exc, self.NOTICE)
        self.assertNotIn("romp-cli-scope", text)
        self.assertIn("exit code 1", text)

    def test_the_wrappers_ignored_line_is_dropped_from_the_tail_too(self):
        # the third form (2026-09-06): a per-session limit not applied; the CLI starts in its scope and
        # the kernel logged the line at arrival (_note_cli_scope_ignored), so on the card it is noise
        ignored = (sb.CLI_SCOPE_IGNORED_PREFIX + " ROMP_CLI_SCOPE_MEMORY_MAX is not a size (digits with an optional "
                   "K, M, G or T suffix, or infinity) — the CLI runs in its scope without it")
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        text = sb.launch_failure_text(exc, ignored + "\n" + self.NOTICE + "\nclaude: the CLI's own reason")
        self.assertNotIn("romp-cli-scope", text)
        self.assertTrue(text.startswith("claude: the CLI's own reason"), text)

    def test_the_wrappers_refusal_stays_on_the_card(self):
        # ROMP_CLI_REAL unset: the wrapper exits 127 before any CLI runs, so its line IS the reason and
        # nothing else reports it (the kernel logs no fallback for it: tests/test_cli_scope.py)
        exc = RuntimeError("Command failed with exit code 127")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        refusal = sb.CLI_SCOPE_REFUSAL_PREFIX + " ROMP_CLI_REAL is unset or empty; it must name the real claude CLI"
        text = sb.launch_failure_text(exc, refusal)
        self.assertIn("ROMP_CLI_REAL", text)
        self.assertTrue(text.startswith(sb.CLI_SCOPE_REFUSAL_PREFIX), text)

    def test_usage_limits_are_classified_apart_from_breakage(self):
        self.assertTrue(sb.is_launch_limit("You've hit your session limit · resets 4:00pm"))
        self.assertTrue(sb.is_launch_limit("usage limit reached"))
        self.assertFalse(sb.is_launch_limit("claude: command not found"))
        self.assertFalse(sb.is_launch_limit(""))


class TheClisStderrIsCaptured(unittest.TestCase):
    """The CLI's stderr has to be PIPED to exist at all, and the reason has to reach the log.

    The failure this closes (the user 2026-07-29): every SDK session in the fleet died at launch and
    each card said only "Check stderr output for details". There was no stderr to check — romp had
    never registered options.stderr, so the SDK handed the child romp's own stderr and dropped the
    line the CLI printed on its way out. The cause (a moved repo, so every --resume looked for a
    conversation under a path that no longer held it) was knowable the whole time and shown nowhere.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_options_register_the_stderr_callback(self):
        """Without this the transport never pipes the child's stderr — the whole failure above."""
        captured = {}

        class _FakeOptions:
            def __init__(self, **kw):
                captured.update(kw)

        sess = _sess_for_options()
        fake_sdk = types.ModuleType("claude_agent_sdk")
        fake_sdk.HookMatcher = lambda **kw: kw
        saved = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = fake_sdk
        try:
            self.be._options(sess, _FakeOptions)
        finally:
            if saved is None:
                del sys.modules["claude_agent_sdk"]
            else:
                sys.modules["claude_agent_sdk"] = saved
        cb = captured.get("stderr")
        self.assertIsNotNone(cb, "options.stderr must be registered or the CLI's stderr is discarded")
        self.assertIs(getattr(cb, "__func__", None), sb.SdkSession._on_cli_stderr)
        self.assertIs(getattr(cb, "__self__", None), sess, "and bound to THIS session's buffer")

    def test_the_tail_keeps_the_last_lines_and_is_bounded(self):
        sess = _sess_for_options()
        for i in range(sb.STDERR_TAIL_LINES * 3):
            sess._on_cli_stderr("line %d\n" % i)
        tail = sess.stderr_tail().splitlines()
        self.assertEqual(len(tail), sb.STDERR_TAIL_LINES, "a chatty CLI must not grow this forever")
        self.assertEqual(tail[-1], "line %d" % (sb.STDERR_TAIL_LINES * 3 - 1),
                         "the LAST lines are the ones that name the exit")

    def test_blank_stderr_lines_are_not_kept(self):
        sess = _sess_for_options()
        for line in ("\n", "   ", "real trouble\n", ""):
            sess._on_cli_stderr(line)
        self.assertEqual(sess.stderr_tail(), "real trouble")

    def test_the_recorded_failure_shows_what_the_cli_said(self):
        sess = _FakeSess(stderr=["No conversation found with session ID: %s" % SID])
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        self.be._record_launch_error(sess, exc)
        text = (sb.read_reg(Path(self.td.name), SID) or {})["launchError"]["text"]
        self.assertIn("No conversation found", text)
        self.assertNotIn("Check stderr output", text)

    def test_the_full_stderr_reaches_the_kernel_log(self):
        """The card gets one glanceable line; the log is where the user goes looking, so it gets
        everything the CLI said."""
        lines = []
        be = sb.SdkBackend(self.td.name, "/bin/true", lambda *a, **k: None,
                           log=lambda m, *a, **k: lines.append(m))
        sess = _FakeSess(stderr=["first complaint", "No conversation found with session ID: %s" % SID])
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        be._record_launch_error(sess, exc)
        blob = "\n".join(lines)
        self.assertIn("first complaint", blob, "the whole tail belongs in the log, not just the last line")
        self.assertIn("No conversation found", blob)

    def test_a_dependency_failure_still_reports_the_remedy(self):
        """The tail must not displace the one text that names what to run."""
        sess = _FakeSess(stderr=["irrelevant chatter"])
        self.be._record_launch_error(sess, ImportError("No module named 'claude_agent_sdk'"))
        rec = (sb.read_reg(Path(self.td.name), SID) or {})["launchError"]
        self.assertIn("romp-sdk-setup", rec["text"])
        self.assertNotIn("irrelevant chatter", rec["text"])


class QueuedMessagesSurviveTheSessionsDeath(unittest.TestCase):
    """What the user typed must stay on screen when the CLI dies — it is still owed to them."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name, missing=False)

    def tearDown(self):
        self.td.cleanup()

    def test_the_persisted_queue_answers_when_no_session_is_running(self):
        typed = "set up the deploy script for the notes-api"
        self.be._update_reg(SID, queue=[typed])
        self.assertEqual(self.be.pending_queued(SID), [typed],
                         "a dead thread must not make the user's message vanish from the chat")

    def test_a_session_with_no_queue_reports_nothing(self):
        self.be._update_reg(SID, queue=[])
        self.assertEqual(self.be.pending_queued(SID), [])
        self.assertEqual(self.be.pending_queued(OTHER), [])

    def test_a_corrupt_queue_mirror_does_not_crash_the_chat(self):
        self.be._update_reg(SID, queue="not a list")
        self.assertEqual(self.be.pending_queued(SID), [])
        self.be._update_reg(OTHER, queue=[None, "", "keep me", 7])
        self.assertEqual(self.be.pending_queued(OTHER), ["keep me"])


class ContractConformance(unittest.TestCase):
    """launch_error is part of the backend contract, with a None default for tmux."""

    def test_the_abc_defaults_to_no_known_failure(self):
        mod = SourceFileLoader(
            "romp_session_backend_launcherr",
            os.path.join(BIN, "romp_session_backend.py")).load_module()
        self.assertIsNone(mod.SessionBackend.launch_error(object(), SID),
                          "a backend whose CLI launches into a visible pane reports nothing here")


class KernelSurfaces(unittest.TestCase):
    """The kernel side: the error reaches the chat, and a usage-limit launch parks the queue instead."""

    @classmethod
    def setUpClass(cls):
        cls.kernel_src = Path(BIN).parent.joinpath("kernel", "kernel.py").read_text()

    def test_the_chat_raises_a_card_for_a_launch_failure(self):
        self.assertIn("_lerr = _launch_error(sid)", self.kernel_src,
                      "the chat build must ask the backend why the session could not start")
        self.assertIn('"This session\'s claude process could not start — %s" % _lerr["text"]',
                      self.kernel_src)
        self.assertIn('_lerr["text"] if _lerr.get("dep")', self.kernel_src,
                      "a missing dependency already reads as a sentence — don't wrap it in a second one")

    def test_a_usage_limit_launch_parks_the_queue_instead_of_erroring(self):
        self.assertIn('if _lerr and not _lerr.get("limit")', self.kernel_src,
                      "a parked queue is a wait, not damage — it must not also raise a red card")
        self.assertIn('_le = _launch_error(sid)', self.kernel_src,
                      "_limit_hold reads the launch that the limit refused — usage.json cannot see it")


if __name__ == "__main__":
    unittest.main()


class SessionCreationRefusesWhenTheSdkCannotRun(unittest.TestCase):
    """The gap the user actually hit: they created a session in the BROWSER and got no error at all.

    Both creation paths (the WS createSession op and POST /new for `romp new`) already carried the
    right refusal — never silently hand back something that can't work — and both asked `_sdk()`.
    But the backend object is built even with the dependency missing, on purpose, so it can keep
    owning the registry and the chat. `_sdk()` therefore answered "yes" and the refusal never fired:
    a session was created that could never run, silently (the user 2026-07-28)."""

    def test_the_backend_reports_its_own_unavailability(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.assertFalse(_backend(td.name, missing=True).available(),
                         "a backend that cannot import its SDK must not answer 'ready'")
        self.assertTrue(_backend(td.name, missing=False).available())

    def test_both_creation_paths_gate_on_ready_not_on_the_object(self):
        src = Path(BIN).parent.joinpath("kernel", "kernel.py").read_text()
        self.assertIn("def _sdk_ready():", src)
        self.assertIn("if _sdk_ready():", src, "the WS createSession op")
        self.assertIn("if not _sdk_ready():", src, "POST /new, the `romp new` path")
        self.assertNotIn("if _sdk():\n                        _create_sdk_session", src,
                         "the old check took a dependency-less backend as a yes")

    def test_the_refusal_names_the_remedy_and_says_nothing_was_created(self):
        src = Path(BIN).parent.joinpath("kernel", "kernel.py").read_text()
        self.assertIn("SDK_SETUP_HINT = ", src)
        i = src.index("SDK_SETUP_HINT = ")
        hint = src[i:i + 400]
        self.assertIn("Session not created", hint, "say plainly that nothing was made")
        self.assertIn("romp-sdk-setup", hint, "name the one command that fixes it")
