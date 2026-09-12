#!/usr/bin/env python3
"""The API-health signal (GET /api-health): ingestion, windows, derived state, labels, ledger.

The kernel already parses every frame the signal needs — the per-attempt api_retry SystemMessage, the
per-response AssistantMessage, the settling ResultMessage — and kept them per session, for one chat
card. The aggregator (sdk_backend.ApiHealth) folds them into one ring keyed by (auth label, model
family) and derives a thrash/degraded/recovering state from the ring, the last persisted (state,
stateSince) and the read time. These tests drive the REAL _on_message with duck-typed frames (the
test doubles the retry-detail and give-up suites use), then the pure functions with hand-built events.

Everything is synthetic: placeholder sids, TESTHOST, an invented key material that is not shaped
like any real credential (assembled, never a credential-shaped literal — the scanner reads this
repo too), and a rebuilt timeline of one rate-limit incident — its SHAPE, not its data. State is
redirected before the module loads.
"""
import asyncio
import ast
import inspect
import textwrap
import hashlib
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_apihealth", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-666666666666"
KEY_MATERIAL = "test-key-material-" + "q" * 28   # invented; not shaped like any provider's key
KEY_FP = hashlib.sha256(KEY_MATERIAL.encode()).hexdigest()[:12]   # a synthetic launch fingerprint for the pure label
# A resolved bucket label, as a session would cache it — DERIVED at run time from the real function,
# never written out: a literal `key:<hex>` is exactly what the credential scanner reads this repo for.
# The fingerprint arm of the label is exercised as a pure function below; since 2026-09-08 romp records
# no key identity at launch, so the session-level tests expect the source-word labels (key:env, key:helper).
LABEL = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="test-salt", key_fp=KEY_FP, launched_keyed=True)
T0 = 1_756_800_000.0                # a fixed synthetic epoch (the derivation is pure in `now`)
KEY = LABEL + "|fable"


# ---- duck-typed frames: _on_message matches on the CLASS objects passed in; msg_to_atom on the NAME ----

class FakeSystemMessage:
    def __init__(self, subtype, data, parent_tool_use_id=None):
        self.subtype, self.data = subtype, data
        self.parent_tool_use_id = parent_tool_use_id


class FakeAssistantMessage:
    def __init__(self, model="claude-fable-5-1", message_id="msg_aaaa", error=None,
                 parent_tool_use_id=None, uuid="u-1"):
        self.model, self.message_id, self.error = model, message_id, error
        self.parent_tool_use_id, self.uuid, self.content = parent_tool_use_id, uuid, []


FakeAssistantMessage.__name__ = "AssistantMessage"


class FakeResultMessage:
    def __init__(self, is_error=False, api_error_status=None, parent_tool_use_id=None):
        self.is_error, self.api_error_status = is_error, api_error_status
        self.parent_tool_use_id = parent_tool_use_id


# The wire frame the 2.1.257 CLI emits for one retry attempt (its field names; the values are
# invented). error_status is the int the aggregator classifies on; `error` the category.
def retry_frame(status=429, category="rate_limit", attempt=1, **extra):
    d = {"attempt": attempt, "max_retries": 10, "retry_delay_ms": 2000,
         "error_status": status, "error": category, "uuid": "u-retry-%s" % attempt, "session_id": SID}
    d.update(extra)
    return d


def _backend(log=None):
    return sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=log)


async def _noop_coro(*a, **k):
    return None


def _session(be, sid=SID, label=LABEL, model_id="claude-fable-5-1"):
    """The retry/settle state alone — SdkSession.__init__ builds a whole client/thread these don't need.
    _learn_model is stubbed: the response's model is read by the aggregator hook directly, and the
    real learner needs the model-pending machinery this double does not carry. The settle branch's
    own state is carried too, so a FakeResultMessage can travel the REAL branch end to end."""
    s = object.__new__(sb.SdkSession)
    s.backend, s.sid, s.name = be, sid, "web"
    s.resume_sid = None
    s.retrying, s.retry_count, s.retry_info = False, 0, None
    s.auth_label, s._ah_turn, s._ah_gaveup = label, 0, None
    s._model_id, s.model, s.chosen_model = model_id, "Fable 5", ""
    s._skill_tool_ids, s._cli_working = set(), True
    s.marks = []
    s._mark = lambda st: s.marks.append(st)
    s._learn_model = lambda pm, raw="": None
    # the settle branch (ResultMessage) reads and clears all of these
    s._last_cost_total, s._last_usage_totals = 0.0, {}
    s.inflight, s._inflight_texts = 1, []
    s._compacting = s._clearing = False
    s._rewind_to = s._rewind_leaf = ""
    s._rewind_bare = s._rewind_armed = s._rewind_wait = False
    s._input_wake, s._reconnect_when_idle, s.ended = None, False, False
    s._interrupted, s._intr_level = False, 0
    s.api_key_auth, s.thread_of = True, ""
    # what _options records at launch and the init's billing check reads bare (meant_key): a login
    # launch by default — the SaltedLabels tests set the keyed pair per case
    s._launched_keyed, s._launched_key_fp, s._launched_unkeyed_pick = False, "", False
    s._do_refresh_context = _noop_coro
    s._do_refresh_usage = _noop_coro
    return s


def _stub_settle(be):
    """The backend methods the settle branch calls after the aggregator hook — stubbed so a
    __new__-built session double can travel the whole branch."""
    be._record_spend = lambda *a, **k: None
    be._turn_completed = lambda sid: None
    be.retire_live_work = lambda sid: None
    be._deliver_rename_ping = lambda s: None
    be._forward = lambda s, m: None


def _feed(s, msg):
    s._on_message(msg, FakeAssistantMessage, FakeResultMessage, FakeSystemMessage)


def _feed_settle(s, msg):
    """A ResultMessage through the REAL branch: it schedules coroutines on the running loop, so it
    is fed from inside one."""
    async def drive():
        _feed(s, msg)
        await asyncio.sleep(0)
    asyncio.run(drive())


def _bucket(be, label=LABEL, family="fable", now=None):
    snap = be.api_health_snapshot(now or time.time())
    return snap["buckets"].get("%s|%s" % (label, family))


def _win(be, w="60", **kw):
    b = _bucket(be, **kw)
    return b["windows"][w] if b else None


class IngestsTheRealBranches(unittest.TestCase):
    """The hooks live IN the branches _on_message already runs — one parse, two consumers."""

    def test_an_api_retry_frame_counts_one_attempt_in_the_sessions_bucket(self):
        be = _backend()
        s = _session(be)
        _feed(s, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit")))
        w = _win(be)
        self.assertIsNotNone(w, "the bucket is keyed (auth label, family) from the session")
        self.assertEqual((w["requests"], w["rateLimited"], w["retries"]), (1, 1, 1))
        self.assertEqual((w["sessionsRetrying"], w["turnsRetrying"]), (1, 1))
        self.assertEqual(w["rate429"], 1.0)
        # …and the retry-detail consumer still saw the same frame (the branch was not forked)
        self.assertTrue(s.retrying)
        self.assertEqual(s.marks, ["retrying"])

    def test_the_hook_sits_in_the_existing_api_retry_branch_not_a_second_subscription(self):
        src = inspect.getsource(sb.SdkSession._on_message)
        i_branch = src.index('msg.subtype == "api_retry"')
        i_hook = src.index("self._ah_note_retry(d, msg)")
        i_mark = src.index('self._mark("retrying")')
        self.assertTrue(i_branch < i_hook < i_mark, "the aggregator is fed from the same branch, before the mark")
        self.assertNotIn("retriesRecovered", inspect.getsource(sb.ApiHealth),
                         "the settle-time recovery ledger has no per-attempt status — never a source")

    def test_the_retry_hook_reads_only_error_status_and_error_from_the_frame(self):
        """The aggregator reads `error_status` and `error` from the wire frame, nothing else —
        not attempt / max_retries (no field of the signal uses them; the one-shot sorted(msg.data)
        line shows they are there) and not the chat card's retry_info alternates."""
        fn = sb.SdkSession._ah_note_retry
        # the code, not its docstring — cut by the docstring node's line range, not by text-replacing
        # fn.__doc__: Python 3.13 dedents docstring constants at compile time, so __doc__ no longer
        # matches the raw source and a replace() leaves the docstring (and its word "attempt") in place
        raw = textwrap.dedent(inspect.getsource(fn))
        lines = raw.splitlines()
        first = ast.parse(raw).body[0].body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            del lines[first.lineno - 1:first.end_lineno]
        src = "\n".join(lines)
        self.assertIn('d.get("error_status")', src)
        self.assertIn('d.get("error")', src)
        for forbidden in ("attempt", "max_retries", "retry_info", "_pick(", "status_code"):
            self.assertNotIn(forbidden, src, "the hook must not read %r" % forbidden)
        be = _backend()
        s = _session(be)
        # a frame with ONLY the two fields counts; bizarre values in the others change nothing
        _feed(s, FakeSystemMessage("api_retry", {"error_status": 529, "error": "overloaded"}))
        _feed(s, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit", attempt="x", max_retries=None)))
        w = _win(be)
        self.assertEqual((w["overloaded"], w["rateLimited"], w["retries"]), (1, 1, 2))

    def test_statuses_classify_into_their_counters(self):
        be = _backend()
        s = _session(be)
        for st, cat in ((429, "rate_limit"), (529, "overloaded"), (503, "server_error"),
                        (400, "unknown"), (None, "unknown")):
            _feed(s, FakeSystemMessage("api_retry", retry_frame(st, cat)))
        w = _win(be)
        self.assertEqual(w["rateLimited"], 1)
        self.assertEqual(w["overloaded"], 1)
        self.assertEqual(w["serverErrors"], 1)
        self.assertEqual(w["otherErrors"], 1)
        self.assertEqual(w["noStatus"], 1, "a status-less attempt is reported…")
        self.assertEqual(w["requests"], 4, "…and excluded from the denominator")
        self.assertEqual(w["retries"], 5)

    def test_a_category_alone_classifies_when_the_status_is_null(self):
        self.assertEqual(sb.api_health_status_class(None, "overloaded"), "529")
        self.assertEqual(sb.api_health_status_class(None, "rate_limit"), "429")
        self.assertEqual(sb.api_health_status_class(None, "server_error"), "5xx")
        self.assertEqual(sb.api_health_status_class(None, "authentication_failed"), "other")
        self.assertEqual(sb.api_health_status_class(None, "unknown"), "none")
        self.assertEqual(sb.api_health_status_class("529", ""), "529", "a digit string is a status too")
        self.assertEqual(sb.api_health_status_class(True, "rate_limit"), "429", "a bool is not a status")

    def test_an_assistant_response_counts_one_ok_per_message_id(self):
        be = _backend()
        s = _session(be)
        # the CLI emits one frame per content block; every frame of one response carries the same id
        for _ in range(3):
            _feed(s, FakeAssistantMessage(message_id="msg_one"))
        _feed(s, FakeAssistantMessage(message_id="msg_two"))
        w = _win(be)
        self.assertEqual(w["ok"], 2, "three frames of one response are ONE ok")
        self.assertEqual(w["requests"], 2)

    def test_a_synthetic_assistant_record_is_not_a_response(self):
        be = _backend()
        s = _session(be)
        _feed(s, FakeAssistantMessage(model="<synthetic>", message_id="msg_syn"))
        _feed(s, FakeAssistantMessage(model="", message_id="msg_empty"))
        self.assertIsNone(_bucket(be), "nothing counted → no bucket")

    def test_sidechain_frames_are_ignored_on_both_sides(self):
        """A subagent's retries never reach the SDK (the CLI folds them into a tool_progress frame it
        drops), so its responses must not count either — symmetric exclusion, or every storm's rate
        is diluted by subagent oks that carried no retries."""
        be = _backend()
        s = _session(be)
        _feed(s, FakeAssistantMessage(message_id="msg_sub", parent_tool_use_id="toolu_01"))
        _feed(s, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit"), parent_tool_use_id="toolu_01"))
        _feed(s, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit", parent_tool_use_id="toolu_01")))
        self.assertIsNone(_bucket(be), "no sidechain frame reaches the ring")
        snap = be.api_health_snapshot()
        self.assertIs(snap["coverage"]["sidechainExcluded"], True, "the payload says so")

    def test_the_ok_hook_checks_parent_tool_use_id_itself(self):
        # the recovery-marker writes sit ABOVE the branch's sidechain guard (m = None); the aggregator
        # must not inherit that false trigger — its own check comes first
        src = inspect.getsource(sb.SdkSession._on_message)
        i_hook = src.index("self._ah_note_assistant(msg)")
        i_guard = src.index('if getattr(msg, "parent_tool_use_id", None):\n                m = None')
        self.assertLess(i_hook, i_guard)
        self.assertIn('if getattr(msg, "parent_tool_use_id", None):\n            return',
                      inspect.getsource(sb.SdkSession._ah_note_assistant))

    def test_a_give_up_pairs_the_error_frame_with_the_settles_status(self):
        """The error-stamped AssistantMessage arrives BEFORE the ResultMessage that carries
        api_error_status, so the frame parks a marker and the settle files the give-up with the status.
        The settle travels the REAL branch: a regression that moves the hook below one of
        the branch's early returns goes red here, not only in the source-position pin."""
        be = _backend()
        _stub_settle(be)
        s = _session(be)
        _feed(s, FakeAssistantMessage(model="<synthetic>", message_id="msg_err", error="rate_limit"))
        self.assertIsNotNone(s._ah_gaveup, "the marker is pending")
        self.assertIsNone(_bucket(be), "…and nothing is filed until the settle names the status")
        _feed_settle(s, FakeResultMessage(is_error=True, api_error_status=429))
        w = _win(be)
        self.assertEqual(w["gaveUp"], 1)
        self.assertEqual(w["rateLimited"], 1, "the give-up IS the turn's last failed attempt — status-counted too")
        self.assertEqual(w["ok"], 0)
        self.assertIsNone(s._ah_gaveup, "the marker is spent")
        self.assertEqual(s._ah_turn, 1, "the settle advanced the turn counter")
        self.assertEqual(_bucket(be)["lastError"]["status"], 429)
        self.assertEqual(_bucket(be)["lastError"]["category"], "rate_limit")
        # …and the branch's own settle work still ran after the hook
        self.assertEqual(s.marks[-1], "waiting")
        self.assertEqual(s.inflight, 0)

    def test_the_settle_branch_calls_the_hook(self):
        """At the top of the branch, before the settle's own work: between the branch head and the hook
        there is nothing but the try that contains the whole branch (its finally IS the settle, so the
        hook sits inside it) and comments — no bookkeeping step, no early return."""
        src = inspect.getsource(sb.SdkSession._on_message)
        i_branch = src.index("elif isinstance(msg, ResultMessage):")
        i_hook = src.index("self._ah_note_result(msg)")
        self.assertLess(i_branch, i_hook)
        between = src[i_branch:i_hook].splitlines()[1:]
        code = [ln.strip() for ln in between
                if ln.strip() and not ln.strip().startswith("#") and ln.strip() != "try:"]
        self.assertEqual(code, [], "a statement runs ahead of the hook: %r" % (code,))

    def test_is_error_gates_the_status_read(self):
        """api_error_status is defined only when is_error is true (SDK types.py:1247-1249).
        A clean settle carrying a stray status files nothing; an error settle with a status and no
        marker files a give-up in that status counter; an error settle with a null status and no
        marker is not an API failure (max turns, budget, execution) and files nothing."""
        be = _backend()
        _stub_settle(be)
        s = _session(be)
        _feed_settle(s, FakeResultMessage(is_error=False, api_error_status=429))
        self.assertIsNone(_bucket(be), "is_error false: the status is not read")
        _feed_settle(s, FakeResultMessage(is_error=True, api_error_status=None))
        self.assertIsNone(_bucket(be), "is_error true, null status, no marker: not an API failure")
        _feed_settle(s, FakeResultMessage(is_error=True, api_error_status=529))
        w = _win(be)
        self.assertEqual((w["gaveUp"], w["overloaded"]), (1, 1), "is_error true + status: the status counter")
        self.assertEqual(s._ah_turn, 3, "every settle ends a turn, filed or not")

    def test_a_give_up_without_a_status_falls_back_to_its_category(self):
        be = _backend()
        _stub_settle(be)
        s = _session(be)
        _feed(s, FakeAssistantMessage(model="<synthetic>", message_id="msg_err", error="server_error"))
        _feed_settle(s, FakeResultMessage(is_error=True, api_error_status=None))
        w = _win(be)
        self.assertEqual((w["gaveUp"], w["serverErrors"]), (1, 1))
        # the 'unknown' category with no status is the noStatus counter
        _feed(s, FakeAssistantMessage(model="<synthetic>", message_id="msg_err2", error="unknown"))
        _feed_settle(s, FakeResultMessage(is_error=True, api_error_status=None))
        w = _win(be)
        self.assertEqual((w["gaveUp"], w["noStatus"]), (2, 1))

    def test_a_give_up_is_counted_without_a_storm(self):
        # the existing retriesGaveUp chat marker is gated on self.retrying and has never fired for the
        # give-ups that arrive with no retry frame before them; the signal counts every one
        be = _backend()
        _stub_settle(be)
        s = _session(be)
        self.assertFalse(s.retrying)
        _feed(s, FakeAssistantMessage(model="<synthetic>", message_id="msg_err", error="billing_error"))
        _feed_settle(s, FakeResultMessage(is_error=True, api_error_status=402))
        self.assertEqual(_win(be)["gaveUp"], 1)
        self.assertEqual(_win(be)["otherErrors"], 1)

    def test_an_error_settle_with_a_status_but_no_frame_still_counts_once(self):
        be = _backend()
        s = _session(be)
        s._ah_note_result(FakeResultMessage(is_error=True, api_error_status=500))
        self.assertEqual(_win(be)["gaveUp"], 1)
        s._ah_note_result(FakeResultMessage(is_error=False, api_error_status=None))
        self.assertEqual(_win(be)["gaveUp"], 1, "a clean settle files nothing")

    def test_turns_and_sessions_retrying_are_distinct_counts(self):
        be = _backend()
        a, b = _session(be, sid=SID), _session(be, sid=SID2)
        for i in range(4):
            _feed(a, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit", attempt=i + 1)))
        a._ah_note_result(FakeResultMessage())            # the turn settles → the next storm is a new turn
        for i in range(3):
            _feed(a, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit", attempt=i + 1)))
        _feed(b, FakeSystemMessage("api_retry", retry_frame(429, "rate_limit")))
        w = _win(be)
        self.assertEqual(w["retries"], 8, "attempts")
        self.assertEqual(w["turnsRetrying"], 3, "two turns of one session + one of the other")
        self.assertEqual(w["sessionsRetrying"], 2)
        self.assertEqual(be.api_health_snapshot()["rate429Basis"], "attempts")

    def test_retries_use_the_sessions_last_model_and_oks_the_responses_own(self):
        be = _backend()
        s = _session(be, model_id="claude-opus-4-8")
        _feed(s, FakeSystemMessage("api_retry", retry_frame(529, "overloaded")))
        _feed(s, FakeAssistantMessage(model="claude-haiku-4-5-20251001", message_id="msg_h"))
        snap = be.api_health_snapshot()
        self.assertIn(LABEL + "|opus", snap["buckets"], "the frame carries no model → the session's")
        self.assertIn(LABEL + "|haiku", snap["buckets"], "the response names its own")
        self.assertEqual(snap["buckets"][LABEL + "|haiku"]["windows"]["60"]["ok"], 1)

    def test_coverage_counts_the_backends_live_sessions(self):
        be = _backend()
        a, b, c = _session(be, sid=SID), _session(be, sid=SID2), _session(be, sid="11111111-2222-3333-4444-777777777777")
        a.retrying, a.inflight = True, 1          # in a retry storm (in turn)
        b.retrying, b.inflight = False, 1         # working
        c.retrying, c.inflight, c.ended = False, 0, True   # gone
        be.sessions = {s.sid: s for s in (a, b, c)}
        cov = be.api_health_snapshot()["coverage"]
        self.assertEqual((cov["sdkSessionsLive"], cov["inTurn"], cov["retrying"]), (2, 2, 1))
        self.assertIs(cov["sidechainExcluded"], True)

    def test_doubles_without_the_aggregator_pass_through(self):
        # the retry-detail suite's _Backend has no api_health; the hooks must not require it
        class _B:
            def _poke(self): pass
            def _forward(self, s, m): pass
        s = object.__new__(sb.SdkSession)
        s.backend, s.sid = _B(), SID
        s.retrying, s.retry_count, s.retry_info = False, 0, None
        s._mark = lambda st: None
        _feed(s, FakeSystemMessage("api_retry", retry_frame()))
        s._ah_note_result(FakeResultMessage(is_error=True, api_error_status=500))
        self.assertEqual(s._ah_turn, 1)


class FamilyAttribution(unittest.TestCase):
    def test_ah_family_prefers_the_raw_id_and_falls_back_to_the_display_name(self):
        """A retry carries no model, so it is filed under the session's current one: the raw id the
        CLI reported when there is one, else the display badge (`Fable 5`), else `unknown`."""
        be = _backend()
        s = _session(be, model_id="claude-haiku-4-5-20251001")
        s.model = "Fable 5"
        self.assertEqual(s._ah_family(), "haiku", "the raw id wins over the badge")
        s._model_id = ""
        self.assertEqual(s._ah_family(), "fable", "no raw id yet: the badge names the family")
        s.model = ""
        self.assertEqual(s._ah_family(), "unknown", "nothing learned yet")

    def test_a_retry_on_a_generation_first_id_files_under_its_family(self):
        be = _backend()
        s = _session(be, model_id="claude-3-5-sonnet-20241022")
        _feed(s, FakeSystemMessage("api_retry", retry_frame()))
        self.assertIsNotNone(_bucket(be, family="sonnet"), "the family, not the catch-all")
        self.assertIsNone(_bucket(be, family="other"))


class Windows(unittest.TestCase):
    """api_health_counts over hand-built events: (now - w, now], the three windows, completeness."""

    def _ev(self, t, cls="ok", kind=None, sid="s1", turn=0):
        kind = kind or ("ok" if cls == "ok" else "retry")
        return sb.AhEvent(t, LABEL, "fable", kind, cls, 429 if cls == "429" else None, sid, turn)

    def test_each_window_sees_only_its_own_span(self):
        evs = [self._ev(T0 - 30), self._ev(T0 - 200, "429"), self._ev(T0 - 800), self._ev(T0 - 1000)]
        self.assertEqual(sb.api_health_counts(evs, T0, 60)["requests"], 1)
        self.assertEqual(sb.api_health_counts(evs, T0, 300)["requests"], 2)
        self.assertEqual(sb.api_health_counts(evs, T0, 900)["requests"], 3)
        self.assertEqual(sb.api_health_counts(evs, T0, 300)["rate429"], 0.5)

    def test_the_boundary_is_half_open(self):
        evs = [self._ev(T0 - 60), self._ev(T0)]
        self.assertEqual(sb.api_health_counts(evs, T0, 60)["requests"], 1,
                         "an event exactly one window old has left; one at `now` is in")

    def test_rates_are_null_at_zero_requests(self):
        w = sb.api_health_counts([self._ev(T0 - 5, "none")], T0, 60)
        self.assertEqual((w["requests"], w["noStatus"]), (0, 1))
        self.assertIsNone(w["rate429"])
        self.assertIsNone(w["rate5xx"])

    def test_completeness_follows_uptime(self):
        self.assertFalse(sb.api_health_counts([], T0, 900, uptime_s=100)["complete"])
        self.assertTrue(sb.api_health_counts([], T0, 60, uptime_s=100)["complete"])
        self.assertTrue(sb.api_health_counts([], T0, 900)["complete"], "unknown uptime is not a claim of incompleteness")

    def test_5xx_rate_pools_overloaded_and_server_errors(self):
        evs = [self._ev(T0 - 1, "529"), self._ev(T0 - 2, "5xx"), self._ev(T0 - 3), self._ev(T0 - 4)]
        self.assertEqual(sb.api_health_counts(evs, T0, 60)["rate5xx"], 0.5)


def _storm(t_from, t_to, step=15.0, share=(3, 4), label=LABEL, family="fable", sid="s1"):
    """Attempts every `step` seconds; the pattern positions in `share` (of 5) are 429s — 40% by default."""
    out, t, i = [], t_from, 0
    while t < t_to:
        is429 = (i % 5) in share
        out.append(sb.AhEvent(t, label, family, "retry" if is429 else "ok", "429" if is429 else "ok",
                              429 if is429 else None, sid, i // 20))
        t += step
        i += 1
    return out


def _clean(t_from, t_to, step=15.0, label=LABEL, family="fable", sid="s1"):
    return _storm(t_from, t_to, step, share=(), label=label, family=family, sid=sid)


def _pattern(t_from, t_to, step, pat, label=LABEL, family="fable", sid="s1"):
    """Attempts every `step` seconds following `pat`: 'x' a 429, 'f' a 5xx (529), 'o' a success."""
    out, t, i = [], t_from, 0
    while t < t_to:
        ch = pat[i % len(pat)]
        if ch == "x":
            out.append(sb.AhEvent(t, label, family, "retry", "429", 429, sid, i // 20))
        elif ch == "f":
            out.append(sb.AhEvent(t, label, family, "retry", "529", 529, sid, i // 20))
        else:
            out.append(sb.AhEvent(t, label, family, "ok", "ok", None, sid, 0))
        t += step
        i += 1
    return out


def _poll(evs, t_from, t_to, step=5, cfg=None, prev=None):
    """A reader polling every `step` s, carrying (state, since) between reads the way snapshot() does.
    Returns (runs, prev): the distinct-state runs [(minutes from t_from, state)] and the final prev."""
    cfg = cfg or sb.api_health_config()
    seq, last = [], None
    for now in range(int(t_from), int(t_to), step):
        st = sb.api_health_state(evs, now, prev, cfg)
        prev = (st["state"], st["since"])
        if st["state"] != last:
            seq.append((round((now - t_from) / 60.0, 2), st["state"]))
            last = st["state"]
    return seq, prev


def _runs(evs, t_from, t_to, step=5, cfg=None):
    return _poll(evs, t_from, t_to, step, cfg)[0]


class DerivedState(unittest.TestCase):
    """api_health_state is a pure function of the events, the previous (state, stateSince) and `now`."""

    def setUp(self):
        self.cfg = sb.api_health_config()

    def test_no_evidence_is_unknown_never_a_held_claim(self):
        st = sb.api_health_state([], T0, None, self.cfg)
        self.assertEqual(st["state"], "unknown")
        self.assertEqual(st["evidence"], {"window": None, "rate429": None, "rate5xx": None, "n": 0})
        self.assertEqual(st["since"], T0, "the read that found it so")
        few = _storm(T0 - 135, T0, share=(0, 1, 2, 3, 4))         # nine 429s: under minRequests
        st = sb.api_health_state(few, T0, ("thrashing", T0 - 600), self.cfg)
        self.assertEqual(st["state"], "unknown", "from ANY state: thin evidence is no evidence")
        self.assertEqual([(t["from"], t["to"]) for t in st["transitions"]], [("thrashing", "unknown")])
        self.assertEqual(st["evidence"]["n"], 9, "n for unknown is requests over the slow window at read time")

    def test_a_clean_bucket_with_evidence_is_healthy(self):
        st = sb.api_health_state(_clean(T0 - 290, T0), T0, None, self.cfg)
        self.assertEqual(st["state"], "healthy")
        self.assertEqual([(t["from"], t["to"]) for t in st["transitions"]], [("unknown", "healthy")])
        self.assertEqual(st["evidence"]["window"], 300)
        self.assertEqual(st["evidence"]["n"], 20)

    def test_the_mid_window_enters_thrashing_at_the_threshold(self):
        evs = _storm(T0 - 290, T0, step=29, share=(3, 4))       # 10 attempts, 4 of them 429 → 0.40
        st = sb.api_health_state(evs, T0, None, self.cfg)
        self.assertEqual(st["state"], "thrashing")
        self.assertIn("rate429 over 300 s", st["why"])
        self.assertIn("attempts", st["why"], "the basis is named in the reason")
        self.assertEqual(st["since"], T0)

    def test_the_slow_window_enters_on_a_lower_share(self):
        # 0.16 over 900 s with the 300 s window clean and insufficient: the slow path alone
        evs = [sb.AhEvent(T0 - 800 + i * 4, LABEL, "fable", "retry" if i < 4 else "ok",
                          "429" if i < 4 else "ok", 429 if i < 4 else None, "s1", 0) for i in range(25)]
        st = sb.api_health_state(evs, T0, ("healthy", T0 - 1000), self.cfg)
        self.assertEqual(st["state"], "thrashing")
        self.assertIn("over 900 s", st["why"])

    def test_the_fast_path_needs_its_own_larger_minimum(self):
        ten = _storm(T0 - 50, T0, step=5, share=(0, 1, 2, 3, 4))[:10]     # 10 attempts, all 429, in 60 s
        st = sb.api_health_state(ten, T0, None, self.cfg)
        self.assertEqual(st["state"], "thrashing", "the 300 s window also holds these ten → its path fires")
        # isolate the fast path: heavy clean traffic keeps the 300 s and 900 s shares under their
        # thresholds, then a burst of 429s in the last minute
        heavy = _clean(T0 - 890, T0 - 120, step=1)                    # 770 clean attempts
        evs = heavy + _storm(T0 - 59, T0, step=4, share=(0, 1, 2, 3, 4))      # 15 × 429 in 60 s
        st = sb.api_health_state(evs, T0, ("healthy", T0 - 1000), self.cfg)
        self.assertEqual(st["state"], "healthy", "15 < fastMinRequests; 300 s ≈ 0.08, 900 s ≈ 0.02")
        evs = heavy + _storm(T0 - 59, T0, step=2.5, share=(0, 1, 2, 3, 4))    # 24 × 429 in 60 s
        st = sb.api_health_state(evs, T0, ("healthy", T0 - 1000), self.cfg)
        self.assertEqual(st["state"], "thrashing", "24 ≥ fastMinRequests at 100%: the fast path alone fires")
        self.assertIn("over 60 s", st["why"])
        self.assertEqual(st["evidence"]["window"], 60, "the evidence is the DECIDING window's")
        self.assertEqual(st["evidence"]["n"], 24)

    def test_degraded_on_server_errors_and_thrashing_wins_when_both(self):
        evs = [sb.AhEvent(T0 - 200 + i * 10, LABEL, "fable", "retry" if i < 4 else "ok",
                          "529" if i < 2 else ("5xx" if i < 4 else "ok"), None, "s1", 0) for i in range(12)]
        st = sb.api_health_state(evs, T0, None, self.cfg)
        self.assertEqual(st["state"], "degraded")
        self.assertIn("rate5xx", st["why"])
        both = evs + [sb.AhEvent(T0 - 100 + i, LABEL, "fable", "retry", "429", 429, "s1", 0) for i in range(4)]
        self.assertEqual(sb.api_health_state(both, T0, None, self.cfg)["state"], "thrashing")

    def test_degraded_goes_to_thrashing_at_once_when_the_429_condition_holds(self):
        """Derived state: thrashing takes precedence over degraded whenever the 429 enter condition holds —
        on entry and afterwards. No exit, no hold: the move is immediate."""
        # 12 attempts over 300 s: 3 × 5xx (0.25) AND 3 × 429 (0.25)
        evs = _pattern(T0 - 290, T0, 25, "ffxxfxoooooo")
        st = sb.api_health_state(evs, T0, ("degraded", T0 - 60), self.cfg)
        self.assertEqual(st["state"], "thrashing")
        self.assertEqual([(t["from"], t["to"]) for t in st["transitions"]], [("degraded", "thrashing")])
        self.assertIn("rate429", st["why"])
        # …while degraded with only the 5xx condition holding stays degraded
        st = sb.api_health_state(_pattern(T0 - 290, T0, 25, "fffooooooooo"), T0, ("degraded", T0 - 60), self.cfg)
        self.assertEqual((st["state"], st["transitions"]), ("degraded", []))

    def test_thrashing_never_goes_straight_to_degraded(self):
        """Derived state: there is no thrashing → degraded. Leaving thrashing goes through recovering (the
        429 exit held for holdS), and recovering → degraded fires IN THE SAME READ when the 5xx
        condition holds — two rows, one `at`."""
        storm = _storm(T0 - 900, T0 - 600)                        # 429s until 10 minutes ago
        fives = _pattern(T0 - 600, T0 + 600, 15, "fooo")          # then 25% 5xx, no 429s
        evs = storm + fives
        # at T0 the slow window still holds the storm's 429s (8 of 60 = 0.13 > exit 0.10): thrashing holds
        st = sb.api_health_state(evs, T0, ("thrashing", T0 - 900), self.cfg)
        self.assertEqual((st["state"], st["transitions"]), ("thrashing", []),
                         "5xx enter holds but the 429 exit has not: no direct move to degraded")
        # at T0 + 400 the 429 exit has held on both windows for well over holdS: recovering, then degraded
        st = sb.api_health_state(evs, T0 + 400, ("thrashing", T0 - 900), self.cfg)
        self.assertEqual(st["state"], "degraded")
        self.assertEqual([(t["from"], t["to"]) for t in st["transitions"]],
                         [("thrashing", "recovering"), ("recovering", "degraded")])
        self.assertIn("rate429 over 300 s and 900 s <= 0.10 throughout the last 120 s", st["transitions"][0]["why"])
        self.assertIn("rate5xx over 300 s", st["transitions"][1]["why"])
        self.assertEqual(st["since"], T0 + 400)

    def test_recovering_to_healthy_needs_both_exits_and_the_hold(self):
        """Derived state: recovering → healthy requires BOTH exit conditions held throughout the last holdS
        AND now − stateSince ≥ holdS. The function does not know which state recovering came from
        (its input is (state, stateSince), never the transitions list), so a bucket with one rate
        between its exit and enter thresholds stays recovering — the accurate label."""
        # attempts every 10 s for 15 min; 5xx at one in eight → ~0.12 on both windows, no 429s
        between = _pattern(T0 - 900, T0, 10, "fooooooo")
        st = sb.api_health_state(between, T0, ("recovering", T0 - 300), self.cfg)
        self.assertEqual((st["state"], st["transitions"]), ("recovering", []),
                         "rate5xx between exit (0.10) and enter (0.15): stays recovering")
        # a clean 5xx rate too (one in 25 → 0.04): both exits hold; the hold decides
        clean = _pattern(T0 - 900, T0, 10, "f" + "o" * 24)
        st = sb.api_health_state(clean, T0, ("recovering", T0 - 60), self.cfg)
        self.assertEqual((st["state"], st["transitions"]), ("recovering", []), "60 s in recovering < holdS")
        st = sb.api_health_state(clean, T0, ("recovering", T0 - 120), self.cfg)
        self.assertEqual(st["state"], "healthy")
        self.assertEqual([(t["from"], t["to"]) for t in st["transitions"]], [("recovering", "healthy")])
        self.assertIn("recovering for 120 s", st["why"])
        self.assertIn("throughout the last 120 s", st["why"], "the run length that decided it")

    def test_a_break_inside_the_hold_keeps_the_state(self):
        # clean for 15 min except ONE 429-heavy minute 60 s ago that pushed the mid window over exit:
        # 'held throughout the last holdS' is false at that breakpoint → thrashing holds
        evs = _clean(T0 - 900, T0 - 90) + _storm(T0 - 90, T0 - 60, step=5, share=(0, 1, 2, 3, 4)) + _clean(T0 - 60, T0, step=5)
        st = sb.api_health_state(evs, T0, ("thrashing", T0 - 1000), self.cfg)
        self.assertEqual(st["state"], "thrashing")
        # the same shape with the burst 200 s ago (outside the hold) but still in the mid window:
        # rate429(300 s) = 6/(6+~50) ≈ 0.11 > exit → still thrashing (the condition, not the timer)
        evs = _clean(T0 - 900, T0 - 230) + _storm(T0 - 230, T0 - 200, step=5, share=(0, 1, 2, 3, 4)) + _clean(T0 - 200, T0, step=5)
        st = sb.api_health_state(evs, T0, ("thrashing", T0 - 1000), self.cfg)
        self.assertEqual(st["state"], "thrashing")

    def test_exit_needs_both_windows_clean_and_holds_then_recovers(self):
        # a storm long enough to fill the slow window, then clean traffic
        evs = _storm(T0, T0 + 1800) + _clean(T0 + 1800, T0 + 1800 + 1500)
        runs = _runs(evs, T0, T0 + 1800 + 1500, cfg=self.cfg)
        states = [s for _, s in runs]
        self.assertEqual(states, ["unknown", "thrashing", "recovering", "healthy"])
        t_rec = [m for m, s in runs if s == "recovering"][0]
        t_ok = [m for m, s in runs if s == "healthy"][0]
        # the 900 s window (60 attempts) is under exit429 only once clean traffic has pushed all but
        # six 429s out — 11.25 min at this rate — then the hold: recovering ~13 min after the storm
        # ended, never before 11…
        self.assertGreaterEqual(t_rec, 30 + 11)
        self.assertLessEqual(t_rec, 30 + 15)
        # …and healthy one hold after recovering (a 5 s poller sees it within one poll)
        self.assertAlmostEqual(t_ok - t_rec, self.cfg["holdS"] / 60.0, delta=0.1)

    def test_a_dead_band_reading_moves_nothing(self):
        """Between exit (0.10) and enter (0.20) the state HOLDS — the hysteresis. A true dead band:
        one 429 in eight (0.125) sustained for 75 minutes after the storm, long past the point where
        the storm has left every window (a sweep that restarted from healthy on each read flipped
        thrashing → healthy at 44.5 min with no exit condition met)."""
        evs = _storm(T0, T0 + 900) + _pattern(T0 + 900, T0 + 5400, 15, "ooooooox")
        runs, prev = _poll(evs, T0, T0 + 5400, cfg=self.cfg)
        self.assertEqual([s for _, s in runs], ["unknown", "thrashing"])
        self.assertEqual(prev[0], "thrashing")
        st = sb.api_health_state(evs, T0 + 5400, prev, self.cfg)
        self.assertEqual((st["state"], st["transitions"]), ("thrashing", []))
        # …and a share just under enter429Slow (one in seven, 0.143) holds the same way
        evs = _storm(T0, T0 + 900) + _pattern(T0 + 900, T0 + 5400, 15, "oooooox")
        self.assertEqual([s for _, s in _runs(evs, T0, T0 + 5400, cfg=self.cfg)], ["unknown", "thrashing"])

    def test_thin_traffic_holds_the_state(self):
        """Clean traffic too thin to qualify the 300 s window (one attempt per 40 s → 7 < minRequests)
        cannot satisfy an exit, which needs both windows qualifying; the slow window still qualifies,
        so the bucket is not unknown either. It stays thrashing — the state is held by rule, and the
        windows beside it show every request succeeding."""
        evs = _storm(T0, T0 + 900) + _pattern(T0 + 900, T0 + 5400, 40, "o")
        runs, prev = _poll(evs, T0, T0 + 5400, cfg=self.cfg)
        self.assertEqual([s for _, s in runs], ["unknown", "thrashing"])

    def test_alternating_storm_and_dead_band_does_not_flap(self):
        evs = (_storm(T0, T0 + 900) + _pattern(T0 + 900, T0 + 3600, 15, "ooooooox")
               + _storm(T0 + 3600, T0 + 4500) + _pattern(T0 + 4500, T0 + 7200, 15, "ooooooox"))
        runs = _runs(evs, T0, T0 + 7200, cfg=self.cfg)
        self.assertEqual([s for _, s in runs], ["unknown", "thrashing"], "one episode, no flap")

    def test_re_entry_from_recovering_is_immediate(self):
        # clean for 14 min: recovering lands ~13.25 min in (see above); a dense 100% burst then starts
        # 45 s later, before the second hold could have completed, and enters at once
        evs = (_storm(T0, T0 + 1800) + _clean(T0 + 1800, T0 + 2640)
               + _storm(T0 + 2640, T0 + 2940, step=3, share=(0, 1, 2, 3, 4)))
        runs = _runs(evs, T0, T0 + 2940, cfg=self.cfg)
        self.assertEqual([s for _, s in runs], ["unknown", "thrashing", "recovering", "thrashing"],
                         "no healthy: the storm came back inside the second hold — re-entry is immediate")
        t_rec = [m for m, s in runs if s == "recovering"][0]
        t_re = [m for m, s in runs if s == "thrashing"][1]
        self.assertLess(t_rec, 44.0)
        self.assertLess(t_re - 44.0, 1.5, "within a minute of the burst starting")

    def test_unknown_keeps_no_memory(self):
        """From unknown the first qualifying read classifies afresh: an enter condition, else healthy —
        even when the state before the gap was thrashing. A consumer joining an incident across a gap
        reads `transitions`."""
        evs = _clean(T0 - 290, T0)
        st = sb.api_health_state(evs, T0, ("unknown", T0 - 30), self.cfg)
        self.assertEqual(st["state"], "healthy")
        self.assertEqual([(t["from"], t["to"]) for t in st["transitions"]], [("unknown", "healthy")])
        self.assertIn("no enter condition", st["why"])

    def test_the_state_is_a_pure_function_of_its_three_inputs(self):
        evs = _storm(T0 - 600, T0)
        prev = ("healthy", T0 - 1000)
        a = sb.api_health_state(evs, T0, prev, self.cfg)
        b = sb.api_health_state(list(reversed(evs)), T0, prev, self.cfg)     # order of the input is irrelevant
        self.assertEqual(a, b)
        self.assertEqual(sb.api_health_state(evs, T0, prev, self.cfg), a, "repeatable — no hidden state")
        # the same ring read from a different previous state is a different answer — the input matters
        c = sb.api_health_state(evs, T0, ("thrashing", T0 - 500), self.cfg)
        self.assertEqual((c["state"], c["since"], c["transitions"]), ("thrashing", T0 - 500, []))
        self.assertEqual((a["state"], a["since"]), ("thrashing", T0))

    def test_no_transition_reports_nothing_to_record(self):
        # the caller keeps the recorded why/evidence when this read moved nothing (except for unknown,
        # whose n is a read-time number)
        st = sb.api_health_state(_storm(T0 - 600, T0), T0, ("thrashing", T0 - 500), self.cfg)
        self.assertIsNone(st["why"])
        self.assertIsNone(st["evidence"])
        st = sb.api_health_state([], T0, ("unknown", T0 - 500), self.cfg)
        self.assertEqual(st["evidence"]["n"], 0)
        self.assertEqual(st["since"], T0 - 500)

    def test_the_backtested_incident_shape(self):
        """One rate-limit incident's per-bucket shape, rebuilt SYNTHETICALLY: a ~40% 429 share on one
        family for ~2h23m, a 21-minute clean lull, the storm again for ~63 minutes, then clean. The
        backtest on the real traffic ran: thrashing at +1:25 on n = 10; recovering
        ~15 minutes into the lull and healthy two minutes later; thrashing again ~2 minutes into the
        second storm; recovering ~15 minutes after it ended, healthy two minutes later — two entries,
        no flap. The timings here follow from the same rules on the rebuilt traffic."""
        A, B, C, D = T0, T0 + 143 * 60, T0 + 164 * 60, T0 + 227 * 60
        END = T0 + 250 * 60
        evs = _storm(A, B) + _clean(B, C) + _storm(C, D) + _clean(D, END)
        runs = _runs(evs, A, END, cfg=self.cfg)
        self.assertEqual([s for _, s in runs],
                         ["unknown", "thrashing", "recovering", "healthy", "thrashing", "recovering", "healthy"])
        m = dict()
        for minute, s in runs:
            m.setdefault(s, []).append(minute)
        self.assertLessEqual(m["thrashing"][0], 3.0, "entry on the first ten attempts")
        # no recovery until the slow window is clean enough (≥ 11 min of clean traffic at this rate)
        # and the hold has passed — the backtest saw ~15 min on the real, burstier traffic
        self.assertGreaterEqual(m["recovering"][0], 143 + 11)
        self.assertLessEqual(m["recovering"][0], 143 + 17)
        self.assertAlmostEqual(m["healthy"][0] - m["recovering"][0], 2.0, delta=0.1)
        self.assertGreaterEqual(m["thrashing"][1], 164, "the second entry is the second storm")
        self.assertLessEqual(m["thrashing"][1], 164 + 4)
        self.assertGreaterEqual(m["recovering"][1], 227 + 11)
        self.assertLessEqual(m["recovering"][1], 227 + 17)
        self.assertAlmostEqual(m["healthy"][1] - m["recovering"][1], 2.0, delta=0.1)

    def test_per_bucket_keying_is_load_bearing(self):
        """Pooled with a clean family's heavier traffic, the incident's share vanishes; keyed per
        (auth, family) the affected bucket thrashes while the other stays healthy."""
        storm = _storm(T0 - 900, T0)                                      # 60 attempts, 0.40
        other = _clean(T0 - 900, T0, step=5, family="haiku")              # 180 clean attempts elsewhere
        pooled = sb.api_health_state(storm + other, T0, None, self.cfg)
        self.assertEqual(pooled["state"], "healthy", "pooled share ≈ 0.10 — the incident disappears")
        ah = sb.ApiHealth(tempfile.mkdtemp())
        for e in storm + other:
            ah._push(e)
        snap = ah.snapshot(T0)
        self.assertEqual(snap["buckets"][KEY]["state"], "thrashing")
        self.assertEqual(snap["buckets"][LABEL + "|haiku"]["state"], "healthy")
        self.assertEqual(snap["overall"]["state"], "thrashing")
        self.assertEqual(snap["overall"]["worstBucket"], KEY)

    def test_constants_live_in_one_place_with_an_env_override(self):
        self.assertEqual(tuple(self.cfg["windows"]), (60, 300, 900))
        self.assertEqual((self.cfg["minRequests"], self.cfg["fastMinRequests"], self.cfg["holdS"]), (10, 20, 120))
        self.assertEqual((self.cfg["enter429"], self.cfg["enter429Slow"], self.cfg["enter429Fast"], self.cfg["exit429"]),
                         (0.20, 0.15, 0.50, 0.10))
        self.assertEqual(self.cfg["retentionS"], 1020,
                         "retention is the slow window plus the hold — the 900 s window at asOf − 120 s reaches back 1020 s")
        os.environ["ROMP_API_HEALTH_MIN_REQUESTS"] = "3"
        os.environ["ROMP_API_HEALTH_HOLD_S"] = "10"
        os.environ["ROMP_API_HEALTH_ENTER_429"] = "0.5"
        try:
            c = sb.api_health_config()
            self.assertEqual((c["minRequests"], c["holdS"], c["enter429"]), (3, 10, 0.5))
            self.assertEqual(c["retentionS"], 910, "the retention follows the hold")
            three = _storm(T0 - 60, T0, step=20, share=(0, 1))[:3]    # 3 attempts, 2 of them 429
            self.assertEqual(sb.api_health_state(three, T0, None, c)["state"], "thrashing")
            os.environ["ROMP_API_HEALTH_MIN_REQUESTS"] = "not-a-number"
            self.assertEqual(sb.api_health_config()["minRequests"], 10, "a malformed override is ignored")
        finally:
            for k in ("MIN_REQUESTS", "HOLD_S", "ENTER_429"):
                os.environ.pop("ROMP_API_HEALTH_" + k, None)
        self.assertIn("CLAUDE_CODE_RETRY_WATCHDOG", open(os.path.join(BIN, "romp_sdk_backend.py")).read(),
                      "the persistent-retry overcount caveat is recorded beside the constants")

    def test_the_ring_retains_exactly_what_the_hold_needs(self):
        ah = sb.ApiHealth(tempfile.mkdtemp())
        for e in _clean(T0 - 1500, T0, step=1):      # 1500 events: eviction runs every 256 pushes
            ah._push(e)
        ah.snapshot(T0)
        oldest = min(e.t for e in ah._ring)
        self.assertGreaterEqual(oldest, T0 - 1020)
        self.assertLess(oldest, T0 - 1000, "…and nothing the 900 s window at asOf − holdS needs is gone")

    def test_the_payload_echoes_the_config(self):
        snap = sb.ApiHealth(tempfile.mkdtemp()).snapshot(T0)
        self.assertEqual(snap["config"]["windows"], [60, 300, 900])
        self.assertEqual(snap["config"]["holdS"], 120)
        self.assertEqual(snap["config"]["retentionS"], 1020)
        self.assertEqual(snap["schema"], 1)


class OverallState(unittest.TestCase):
    def _ah_with(self, *bucket_states):
        ah = sb.ApiHealth(tempfile.mkdtemp())
        for i, st in enumerate(bucket_states):
            fam = "fam%d" % i
            if st == "thrashing":
                evs = _storm(T0 - 600, T0, family=fam)
            elif st == "healthy":
                evs = _clean(T0 - 600, T0, family=fam)
            else:   # unknown: a few events, under minRequests
                evs = _clean(T0 - 60, T0, step=20, family=fam)
            for e in evs:
                ah._push(e)
        return ah.snapshot(T0)

    def test_the_most_severe_bucket_sets_the_overall_state(self):
        snap = self._ah_with("healthy", "thrashing", "healthy")
        self.assertEqual(snap["overall"]["state"], "thrashing", "a healthy fallback bucket must not mask a thrashing one")
        self.assertEqual(snap["overall"]["worstBucket"], LABEL + "|fam1")

    def test_unknown_ranks_below_healthy(self):
        self.assertEqual(self._ah_with("unknown", "healthy")["overall"]["state"], "healthy")
        self.assertEqual(self._ah_with("unknown", "unknown")["overall"]["state"], "unknown")
        self.assertEqual(self._ah_with()["overall"]["state"], "unknown", "no buckets at all reads unknown")
        self.assertIsNone(self._ah_with()["overall"]["worstBucket"])


class SaltedLabels(unittest.TestCase):
    def test_same_material_same_label_within_one_install(self):
        a = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="salt-1", key_fp=KEY_FP, launched_keyed=True)
        b = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="salt-1", key_fp=KEY_FP, launched_keyed=True)
        self.assertEqual(a, b)
        self.assertRegex(a, r"^key:[0-9a-f]{12}$")

    def test_a_different_salt_gives_a_different_label(self):
        a = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="salt-1", key_fp=KEY_FP, launched_keyed=True)
        b = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="salt-2", key_fp=KEY_FP, launched_keyed=True)
        self.assertNotEqual(a, b, "the label is not a cross-install equality oracle")
        # …and the empty salt is the documented switch to the identity the kernel prints elsewhere: the
        # key's fingerprint (the kernel log, `romp keyswap`), so an operator can match the bucket to it
        plain = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="", key_fp=KEY_FP, launched_keyed=True)
        self.assertEqual(plain, "key:" + KEY_FP)
        self.assertEqual(sb.api_health_auth_label("none", salt="", acct="0123456789ab"), "login:0123456789ab",
                         "the login arm the same way: the account digest the usage bars stamp")

    def test_no_fragment_of_the_key_is_in_the_label(self):
        lab = sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="s", key_fp=KEY_FP, launched_keyed=True)
        for i in range(len(KEY_MATERIAL) - 4):
            self.assertNotIn(KEY_MATERIAL[i:i + 5], lab)
        # a salted label is not the fingerprint either: the salt is what keeps the bucket name from being
        # the identity the log prints (an empty salt is the operator's choice to make it so)
        self.assertNotEqual(lab, "key:" + KEY_FP)
        self.assertNotIn(KEY_FP, lab)

    def test_the_source_words_map_to_constant_labels(self):
        self.assertEqual(sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="s", key_fp="", launched_keyed=False),
                         "key:env", "the kernel injected nothing → no material to digest")
        self.assertEqual(sb.api_health_auth_label("ANTHROPIC_API_KEY", salt="s", key_fp=KEY_FP, launched_keyed=False),
                         "key:env", "a key the CLI found on its own is not the kernel's material")
        self.assertEqual(sb.api_health_auth_label("apiKeyHelper", salt="s"), "key:helper")
        self.assertEqual(sb.api_health_auth_label("/login managed key", salt="s"), "key:managed")
        self.assertEqual(sb.api_health_auth_label("oauth", salt="s"), "key:oauth")
        self.assertEqual(sb.api_health_auth_label("none", salt="s", acct="0123456789ab"),
                         "login:" + sb._api_health_digest("s", "0123456789ab"))
        self.assertEqual(sb.api_health_auth_label(None, salt="s", acct=""), "login:unknown")
        self.assertEqual(sb.api_health_auth_label("", salt="s", acct=""), "login:unknown")

    def test_the_salt_is_minted_once_at_0600_and_an_empty_file_means_unsalted(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        self.assertFalse(os.path.exists(os.path.join(d, sb.API_HEALTH_SALT_FILE)), "nothing written until a label is needed")
        s1 = ah.salt()
        p = os.path.join(d, sb.API_HEALTH_SALT_FILE)
        self.assertTrue(s1 and os.path.exists(p))
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(sb.ApiHealth(d).salt(), s1, "one salt per install: a new aggregator reads the same file")
        self.assertEqual(os.listdir(d), [sb.API_HEALTH_SALT_FILE], "no temp file left behind")
        open(p, "w").close()
        self.assertEqual(sb.ApiHealth(d).salt(), "", "the empty file is the switch")

    def test_two_instances_minting_at_once_get_one_salt(self):
        """A mint that creates the file (O_EXCL) and then writes it lets a reader in between see an
        EMPTY file — the documented unsalted switch — cache '' and label the same key differently.
        The write is widened here to make the gap certain.

        The reader is a PEER instance over the same state dir (a second kernel process, or
        a sibling aggregator in this one), not a second thread on one instance. One instance's
        `_salt_lock` serialises nothing across instances, so only the atomic publish (bytes to a
        temp, the name taken by link) keeps the peer from ever seeing the file empty — a mutant
        that keeps the lock but creates-then-writes passes the one-instance form of this test."""
        d = tempfile.mkdtemp()
        ah, peer = sb.ApiHealth(d), sb.ApiHealth(d)
        real_write = os.write
        started = threading.Event()

        def slow_write(fd, data):
            started.set()
            time.sleep(0.3)
            return real_write(fd, data)

        got = {}
        sb.os.write = slow_write
        try:
            def a():
                got["a"] = ah.salt()

            def b():
                started.wait(2.0)
                time.sleep(0.05)          # squarely inside the widened write
                got["b"] = peer.salt()
            ta, tb = threading.Thread(target=a), threading.Thread(target=b)
            ta.start()
            tb.start()
            ta.join(5)
            tb.join(5)
        finally:
            sb.os.write = real_write
        self.assertTrue(got.get("a") and got.get("b"), "both instances got a salt: %r" % (got,))
        self.assertEqual(got["a"], got["b"], "one salt — never '' for the loser of the race")
        self.assertEqual(sb.ApiHealth(d).salt(), got["a"], "…and the file holds it")
        self.assertEqual(os.listdir(d), [sb.API_HEALTH_SALT_FILE], "the loser's temp is gone too")

    def test_a_writer_that_dies_mid_mint_leaves_no_salt_file_and_the_next_boot_mints(self):
        """A kernel that dies between creating its temp and publishing it must
        leave NOTHING at the salt path — the next boot then mints a real salt. A create-then-write
        mutant leaves an EMPTY salt file behind, which every later reader takes for the unsalted
        switch, for the life of the install."""
        d = tempfile.mkdtemp()

        class Died(BaseException):        # not Exception: nothing in the mint may swallow it
            pass

        real_write = os.write

        def die(fd, data):
            raise Died()
        sb.os.write = die
        try:
            with self.assertRaises(Died):
                sb.ApiHealth(d).salt()
        finally:
            sb.os.write = real_write
        self.assertFalse(os.path.exists(os.path.join(d, sb.API_HEALTH_SALT_FILE)), "nothing half-published")
        s = sb.ApiHealth(d).salt()
        self.assertTrue(s, "the next boot mints a real salt, not the unsalted switch")
        self.assertEqual(sb.ApiHealth(d).salt(), s)

    def test_a_dead_writers_leftover_temp_is_swept_at_mint_and_a_live_writers_is_not(self):
        """The crash above leaves `api-health-salt.<pid>.<hex>.tmp` behind. The next mint sweeps
        temps whose writer pid is dead. A temp whose writer is ALIVE is a mint in progress and stays:
        unlinking it would turn that writer's link into FileNotFoundError and leave it unsalted."""
        d = tempfile.mkdtemp()
        dead = 2 ** 22 - 7
        while True:
            try:
                os.kill(dead, 0)
            except ProcessLookupError:
                break
            except OSError:
                pass
            dead -= 1
        stale = "%s.%d.deadbeef.tmp" % (sb.API_HEALTH_SALT_FILE, dead)
        live = "%s.%d.cafef00d.tmp" % (sb.API_HEALTH_SALT_FILE, os.getpid())
        for name in (stale, live):
            open(os.path.join(d, name), "w").close()
        s = sb.ApiHealth(d).salt()
        self.assertTrue(s)
        self.assertEqual(sorted(os.listdir(d)), sorted([sb.API_HEALTH_SALT_FILE, live]))

    def test_the_init_resolves_the_label_once_onto_the_session(self):
        """romp records no key identity at launch (2026-09-08: it holds no key), so a CLI-found key labels
        by its source word: key:env for ANTHROPIC_API_KEY, key:helper for apiKeyHelper."""
        be = _backend()
        s = _session(be, label="unknown")
        s._launched_keyed = True
        s.auth = ""
        s.api_key_auth = False
        s.auth_live = ""
        be._note_auth_source(s, "ANTHROPIC_API_KEY")
        self.assertEqual(s.auth_label, "key:env")
        s_h = _session(be, label="unknown")
        s_h._launched_keyed, s_h.auth, s_h.api_key_auth, s_h.auth_live = True, "", False, ""
        be._note_auth_source(s_h, "apiKeyHelper")
        self.assertEqual(s_h.auth_label, "key:helper")
        # the frames that follow file under it
        _feed(s, FakeSystemMessage("api_retry", retry_frame()))
        snap = be.api_health_snapshot()
        self.assertIn(s.auth_label + "|fable", snap["buckets"])
        # …and no fragment of the material is anywhere in the payload or the state file (nine more
        # attempts make the bucket sufficient, so a transition is written)
        for i in range(9):
            _feed(s, FakeSystemMessage("api_retry", retry_frame(attempt=i + 2)))
        snap = be.api_health_snapshot()
        self.assertEqual(snap["buckets"][s.auth_label + "|fable"]["state"], "thrashing")
        blob = json.dumps(snap) + open(os.path.join(be.state_dir, sb.API_HEALTH_STATE_FILE)).read()
        for i in range(len(KEY_MATERIAL) - 4):
            self.assertNotIn(KEY_MATERIAL[i:i + 5], blob)
        self.assertNotIn(KEY_FP, blob, "salted: the fingerprint the log prints is not the bucket's name")

    def test_the_init_never_runs_the_helper(self):
        """The label comes from the init's own source word; the hook resolves no credential (2026-09-08: the
        kernel's helper runner is for its two API calls only, never an init)."""
        be = _backend()
        s = _session(be, label="unknown")
        s._launched_keyed = True
        s.auth, s.api_key_auth, s.auth_live = "", False, ""
        runs = []
        real = sb._cred.helper_key
        sb._cred.helper_key = lambda *a, **k: runs.append(1) or ""
        try:
            be._note_auth_source(s, "ANTHROPIC_API_KEY")
        finally:
            sb._cred.helper_key = real
        self.assertEqual(runs, [], "the hook never ran the helper")
        self.assertEqual(s.auth_label, "key:env")
        s2 = _session(be, label="unknown")
        s2._launched_keyed = False
        s2.auth, s2.api_key_auth, s2.auth_live = "", False, ""
        be._note_auth_source(s2, "ANTHROPIC_API_KEY")
        self.assertEqual(s2.auth_label, "key:env", "a login launch that landed on a key labels the same way")

    def test_the_label_source_reuses_the_existing_auth_knowledge(self):
        # not a re-derivation: the same init word _note_auth_source already judges, and the usage bars'
        # account digest; no key material and no helper run
        src = inspect.getsource(sb.SdkBackend._note_auth_source)
        self.assertIn("self.api_health.auth_label(source, login_id=_lid,", src)   # T346: a stored login's id rides beside the source word
        self.assertNotIn("helper_key(", src, "never a credential resolution at init time")
        self.assertNotIn("_launched_key_fp", src, "romp records no key identity")
        self.assertIn("acct_digest()", inspect.getsource(sb.ApiHealth.auth_label))

    def test_a_login_init_labels_from_the_account_digest_it_reads(self):
        """The login arm EXECUTES acct_digest: an init whose CLI reports no key (the field absent, or
        the literal 'none' in any casing and spacing) labels as login:<salted digest of the account the
        usage bars stamp>, and the frames that follow file under it. The source-text pin above is not
        enough on its own: with the call dropped, every login labelled login:unknown and the suite
        stayed green."""
        be = _backend()
        real = sb.acct_digest
        sb.acct_digest = lambda: "0123456789ab"
        try:
            want = "login:" + sb._api_health_digest(be.api_health.salt(), "0123456789ab")
            for src in ("none", "", None, " None "):
                s = _session(be, label="unknown")
                s._launched_keyed = False
                s._launched_key_fp = ""             # a login launch records no fingerprint
                s.auth, s.api_key_auth, s.auth_live = "", False, ""
                be._note_auth_source(s, src)
                self.assertEqual(s.auth_label, want, repr(src))
                self.assertRegex(s.auth_label, r"^login:[0-9a-f]{12}$")
            _feed(s, FakeSystemMessage("api_retry", retry_frame()))
            self.assertIn(want + "|fable", be.api_health_snapshot()["buckets"])
            # no readable account: the arm still runs, and says so rather than inventing a name
            sb.acct_digest = lambda: ""
            s2 = _session(be, label="unknown")
            s2._launched_keyed = False
            s2._launched_key_fp = ""
            s2.auth, s2.api_key_auth, s2.auth_live = "", False, ""
            be._note_auth_source(s2, "none")
            self.assertEqual(s2.auth_label, "login:unknown")
        finally:
            sb.acct_digest = real


def _doc(d):
    """The persisted state file, STATE/api-health.json."""
    with open(os.path.join(d, sb.API_HEALTH_STATE_FILE)) as f:
        return json.load(f)


def _rows(d):
    """The persisted global transition tail (the state file's `transitions`, newest last)."""
    return _doc(d)["transitions"]


class TransitionLedger(unittest.TestCase):
    def test_a_read_that_observes_a_change_appends_one_row(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        snap = ah.snapshot(T0)
        rows = _rows(d)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["from"], rows[0]["to"], rows[0]["bucket"]), ("unknown", "thrashing", KEY))
        self.assertIn("rate429 over", rows[0]["why"])
        self.assertEqual(rows[0]["evidence"]["window"], 300, "the deciding window rides the row")
        self.assertEqual(rows[0]["t"], T0)
        b = snap["buckets"][KEY]
        self.assertEqual(b["stateSince"], rows[0]["t"], "stateSince IS the row's time — one clock")
        self.assertEqual(b["evidence"], rows[0]["evidence"])
        self.assertEqual(b["why"], rows[0]["why"])
        self.assertEqual(b["transitions"], rows, "the bucket carries its own transitions")
        ah.snapshot(T0 + 5)
        self.assertEqual(len(_rows(d)), 1, "no change → no row")
        snap = ah.snapshot(T0 + 1500)      # the ring has emptied past retention: unknown
        self.assertEqual(snap["buckets"][KEY]["state"], "unknown")
        self.assertEqual(snap["buckets"][KEY]["stateSince"], T0 + 1500)
        rows = _rows(d)
        self.assertEqual([r["to"] for r in rows], ["thrashing", "unknown"])
        self.assertEqual(snap["transitions"], rows, "the payload carries the ledger's tail")

    def test_a_bucket_whose_events_all_aged_out_between_reads_closes_its_episode(self):
        """A read that iterates only the buckets present in the ring lets a bucket whose events
        were all evicted between two reads keep its last state forever — and when its traffic came
        back, the first row was misdated. Every known bucket is derived on every read: absent from
        the ring is `unknown`, filed at the read that found it so."""
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        ah.snapshot(T0)
        snap = ah.snapshot(T0 + 2000)            # > retention: the ring is empty
        self.assertIn(KEY, snap["buckets"], "a known bucket stays in the payload while its ring is empty")
        b = snap["buckets"][KEY]
        self.assertEqual((b["state"], b["stateSince"]), ("unknown", T0 + 2000))
        self.assertEqual(b["windows"]["900"]["requests"], 0)
        self.assertEqual([(r["from"], r["to"]) for r in _rows(d)], [("unknown", "thrashing"), ("thrashing", "unknown")])
        self.assertEqual(snap["overall"]["state"], "unknown")
        ah.snapshot(T0 + 5000)
        self.assertEqual(len(_rows(d)), 2, "unknown stays unknown: no second row")
        # traffic resumes clean: the row is dated at the read that saw it, from `unknown`
        for e in _clean(T0 + 5000, T0 + 5300):
            ah._push(e)
        ah.snapshot(T0 + 5300)
        rows = _rows(d)
        self.assertEqual((rows[-1]["from"], rows[-1]["to"], rows[-1]["t"]), ("unknown", "healthy", T0 + 5300))

    def test_a_restart_sets_every_bucket_unknown_at_boot_and_the_first_qualifying_read_classifies_afresh(self):
        """The persistence rule: the ring is lost at restart, so the reload sets every
        bucket to unknown with stateSince = the boot time, filing `<state> -> unknown` for each bucket
        whose persisted state was not already unknown — the transitions list is continuous across the
        restart — and the first read with enough evidence records `unknown -> <state>` after it. No
        held pre-restart state: an empty ring is no evidence, and the state says so."""
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        ah.snapshot(T0)
        ah2 = sb.ApiHealth(d, boot_at=T0 + 30)
        rows = _rows(d)
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in rows],
                         [("unknown", "thrashing", T0), ("thrashing", "unknown", T0 + 30)])
        self.assertIn("restart", rows[-1]["why"])
        snap = ah2.snapshot(T0 + 31)
        self.assertEqual(snap["buckets"][KEY]["state"], "unknown", "an empty ring is no evidence")
        self.assertEqual(snap["buckets"][KEY]["stateSince"], T0 + 30)
        self.assertEqual(len(snap["transitions"]), 2, "the payload carries the pre-restart history")
        for e in _storm(T0 - 600, T0 + 60):      # the storm is still on as the ring refills
            ah2._push(e)
        snap = ah2.snapshot(T0 + 60)
        self.assertEqual(snap["buckets"][KEY]["state"], "thrashing")
        self.assertEqual([(r["from"], r["to"]) for r in _rows(d)][-1], ("unknown", "thrashing"))
        self.assertEqual(snap["buckets"][KEY]["stateSince"], T0 + 60)
        # a second restart with the bucket already unknown files nothing new
        ah3 = sb.ApiHealth(d, boot_at=T0 + 100)
        ah3.snapshot(T0 + 100)
        n = len(_rows(d))
        sb.ApiHealth(d, boot_at=T0 + 200)
        self.assertEqual(len(_rows(d)), n, "already unknown: no row")

    def test_unknown_after_a_restart_keeps_no_memory_of_the_incident(self):
        """The same rule from the other side: a restart mid-incident followed by dead-band traffic
        (0.125, between exit and every enter threshold) classifies afresh as healthy — unknown keeps
        no memory of the state before it, by design. The transitions list is where a consumer joins
        the two. The no-restart control on the same traffic holds thrashing by hysteresis."""
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 900, T0):
            ah._push(e)
        self.assertEqual(ah.snapshot(T0)["buckets"][KEY]["state"], "thrashing")
        ah2 = sb.ApiHealth(d, boot_at=T0 + 30)
        dead = _pattern(T0 + 30, T0 + 1230, 15, "ooooooox")       # 80 attempts, 10 of them 429
        for e in dead:
            ah2._push(e)
        b = ah2.snapshot(T0 + 1230, uptime_s=1200)["buckets"][KEY]
        self.assertEqual(b["state"], "healthy")
        self.assertTrue(0.10 < b["windows"]["900"]["rate429"] < 0.15, b["windows"]["900"])
        self.assertTrue(0.10 < b["windows"]["300"]["rate429"] < 0.20, b["windows"]["300"])
        self.assertEqual([(r["from"], r["to"]) for r in _rows(d)],
                         [("unknown", "thrashing"), ("thrashing", "unknown"), ("unknown", "healthy")])
        ah3 = sb.ApiHealth(tempfile.mkdtemp())
        for e in _storm(T0 - 900, T0) + dead:
            ah3._push(e)
        ah3.snapshot(T0)
        b = ah3.snapshot(T0 + 1230)["buckets"][KEY]
        self.assertEqual(b["state"], "thrashing", "no restart: the same reading holds thrashing by hysteresis")
        self.assertTrue(0.10 < b["windows"]["900"]["rate429"] < 0.15, b["windows"]["900"])

    def test_state_since_is_the_first_read_that_observed_it(self):
        ah = sb.ApiHealth(tempfile.mkdtemp())
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        a = ah.snapshot(T0)["buckets"][KEY]["stateSince"]
        b = ah.snapshot(T0 + 40)["buckets"][KEY]["stateSince"]
        self.assertEqual(a, b, "unchanged state, unchanged since")
        self.assertEqual(a, T0)

    def test_the_derivation_runs_with_the_ingest_lock_released(self):
        """snapshot() used to hold the ring lock across the whole derivation, and every session
        thread's _push takes the same lock — measured 70–376 ms stalls at 15k–40k ring events. The
        ring is copied under the lock, derived outside it, and the lock is re-taken only to file.

        The probe covers the WHOLE phase-2 derivation — the state function AND the window
        counts of every bucket — not the state function alone. A mutant that re-took the lock around
        the counts (the bulk of the per-bucket work) passed the narrower probe."""
        ah = sb.ApiHealth(tempfile.mkdtemp())
        for e in _storm(T0 - 600, T0) + _clean(T0 - 600, T0, family="haiku"):
            ah._push(e)
        real_state, real_counts = sb.api_health_state, sb.api_health_counts
        seen = {"state": [], "counts": []}

        def lock_free():
            free = ah._lock.acquire(blocking=False)
            if free:
                ah._lock.release()
            return free

        def probe_state(*a, **k):
            seen["state"].append(lock_free())
            return real_state(*a, **k)

        def probe_counts(*a, **k):
            seen["counts"].append(lock_free())
            return real_counts(*a, **k)
        sb.api_health_state, sb.api_health_counts = probe_state, probe_counts
        try:
            snap = ah.snapshot(T0)
        finally:
            sb.api_health_state, sb.api_health_counts = real_state, real_counts
        self.assertEqual(len(seen["state"]), 2, "both buckets derived")
        self.assertEqual(len(seen["counts"]), 2 * len(sb.api_health_config()["windows"]), "every window of both")
        self.assertTrue(all(seen["state"]), "the lock was held during a state derivation")
        self.assertTrue(all(seen["counts"]), "the lock was held during a window count")
        self.assertEqual(snap["buckets"][KEY]["state"], "thrashing", "…and the result still files")
        self.assertEqual(len(ah._transitions), 2)

    def test_a_concurrent_read_that_filed_first_stands(self):
        # two reads derive from the same previous state; the second to file adopts the first's
        # record rather than filing a duplicate row
        ah = sb.ApiHealth(tempfile.mkdtemp())
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        real = sb.api_health_state
        raced = []

        def interleave(*a, **k):
            out = real(*a, **k)
            if not raced:
                raced.append(True)
                sb.api_health_state = real
                # another read files first, from the same prev — only possible with the lock
                # released here (a build that holds it would deadlock, so that shape fails instead)
                if ah._lock.acquire(blocking=False):
                    ah._lock.release()
                    ah.snapshot(T0 + 1)
            return out
        sb.api_health_state = interleave
        try:
            snap = ah.snapshot(T0)
        finally:
            sb.api_health_state = real
        self.assertEqual(len(ah._transitions), 1, "one row, not two")
        self.assertEqual(snap["buckets"][KEY]["stateSince"], T0 + 1, "the record that was filed stands")

    def test_the_ring_is_memory_only_and_the_code_says_why(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        # T316: the ledger's rollover write puts the file there on the first event of a minute, but it holds per-bin
        # COUNTS only: no event, no transition yet (nothing has been observed by a read)
        self.assertEqual(os.listdir(d), [sb.API_HEALTH_STATE_FILE])
        doc = json.load(open(os.path.join(d, sb.API_HEALTH_STATE_FILE)))
        self.assertEqual((doc["transitions"], doc["buckets"]), ([], {}), "per-request events are never persisted; no transition yet")
        self.assertEqual(set(doc), {"schema", "transitions", "buckets", "ledger"})
        for tier in doc["ledger"][KEY].values():
            for row in tier.values():
                self.assertEqual((len(row), all(isinstance(v, int) for v in row)), (5, True), "five counters per bin, nothing per attempt")
        ah.snapshot(T0)
        self.assertEqual(os.listdir(d), [sb.API_HEALTH_STATE_FILE], "the observed transition joins it")
        head = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        self.assertIn("Only STATE TRANSITIONS a read observes are\n# written to disk", head)
        self.assertIn("persisting every attempt would add a write per API call", head, "the why is written down")
        self.assertIn("The T316 ledger persists per-BIN counts", head, "and the ledger's exception to it")


class StateFile(unittest.TestCase):
    """Persistence: one bounded STATE/api-health.json — the per-bucket (state, stateSince, why,
    evidence) and the transition tail — rewritten atomically on every transition and reloaded at
    boot. Per-request events are never written."""

    def test_the_state_file_round_trips_the_bucket_state_and_the_tail(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        ah.snapshot(T0)
        doc = _doc(d)
        self.assertEqual(doc["schema"], sb.API_HEALTH_SCHEMA)
        b = doc["buckets"][KEY]
        self.assertEqual((b["state"], b["stateSince"], b["auth"], b["family"]), ("thrashing", T0, LABEL, "fable"))
        self.assertEqual(b["evidence"]["window"], 300)
        self.assertIn("rate429 over", b["why"])
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in doc["transitions"]], [("unknown", "thrashing", T0)])
        # a new aggregator seeds from it: the bucket is known (unknown at boot, the continuity row filed
        # and written) and the tail continues
        ah2 = sb.ApiHealth(d, boot_at=T0 + 30)
        self.assertEqual((ah2._last_state[KEY]["state"], ah2._last_state[KEY]["auth"]), ("unknown", LABEL))
        self.assertEqual([(r["from"], r["to"], r["t"]) for r in _rows(d)],
                         [("unknown", "thrashing", T0), ("thrashing", "unknown", T0 + 30)])
        self.assertEqual(_doc(d)["buckets"][KEY]["state"], "unknown")
        self.assertEqual(ah2.snapshot(T0 + 31)["transitions"], _rows(d))

    def test_the_state_file_is_published_whole_from_a_temp_in_the_same_dir(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        real, seen = os.replace, []

        def spy(src, dst):
            with open(src) as f:
                seen.append((str(src), str(dst), f.read()))
            return real(src, dst)
        sb.os.replace = spy
        try:
            ah.snapshot(T0)
            ah.snapshot(T0 + 5)
        finally:
            sb.os.replace = real
        self.assertEqual(len(seen), 1, "one publish per read that filed something; none for a read that did not")
        src, dst, body = seen[0]
        self.assertEqual(dst, os.path.join(d, sb.API_HEALTH_STATE_FILE))
        self.assertEqual(os.path.dirname(src), d, "the temp is in the state dir: a rename, never a copy")
        self.assertNotEqual(src, dst)
        self.assertEqual(json.loads(body)["buckets"][KEY]["state"], "thrashing", "the temp held the whole document")
        self.assertEqual(os.listdir(d), [sb.API_HEALTH_STATE_FILE], "no temp left behind")

    def test_the_state_file_is_bounded_however_many_transitions_pass(self):
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        sizes = []
        N = 200                                  # 200 cycles of 5000 s: 11.6 days, past the ledger's longest tier (7 days)
        for i in range(N):                       # two transitions per cycle: unknown -> thrashing -> unknown
            base = T0 + i * 5000
            for e in _storm(base - 600, base):
                ah._push(e)
            ah.snapshot(base)
            ah.snapshot(base + 2000)             # past retention: the ring is empty
            sizes.append(os.path.getsize(os.path.join(d, sb.API_HEALTH_STATE_FILE)))
        self.assertEqual(len(_rows(d)), sb.API_HEALTH_TRANSITIONS_KEEP)
        self.assertEqual(_rows(d)[-1]["t"], T0 + (N - 1) * 5000 + 2000, "newest last")
        # T316: the ledger grows with the bins a day and a week hold, then its tiers are full and the file plateaus (its
        # size wobbles by a few bytes with the digits and the bins a cycle straddles, never climbs); every tier bounded
        self.assertLess(abs(sizes[-1] - sizes[-30]), 600, "saturated tiers and a full tail: the file stopped growing")
        self.assertLessEqual(sum(len(bins) for bins in ah._ledger[KEY].values()), 60 + 288 + 168, "516 bins at most per bucket")
        self.assertLess(sizes[-1], 96 * 1024)

    def test_a_malformed_state_file_is_logged_and_never_the_backends_death(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, sb.API_HEALTH_STATE_FILE)
        with open(p, "w") as f:
            f.write("not json")
        lines = []
        ah = sb.ApiHealth(d, log=lines.append, boot_at=T0)
        self.assertEqual(ah._last_state, {})
        self.assertTrue(any("unreadable" in l for l in lines), lines)
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        self.assertEqual(be.api_health_snapshot(T0 + 1)["overall"]["state"], "unknown")
        # a well-formed document with bad entries in it: the bad ones are skipped, logged once
        with open(p, "w") as f:
            json.dump({"schema": 1, "transitions": [{"t": "yesterday", "bucket": KEY, "to": "x"}, 7],
                       "buckets": {KEY: {"state": "thrashing", "stateSince": T0 - 5, "auth": LABEL, "family": "fable"},
                                   "bad|one": {"state": None}, "worse": "not a record"}}, f)
        lines = []
        ah = sb.ApiHealth(d, log=lines.append, boot_at=T0)
        self.assertEqual(list(ah._last_state), [KEY])
        hits = [l for l in lines if "malformed" in l]
        self.assertEqual(len(hits), 1, lines)
        self.assertIn("4", hits[0])
        self.assertEqual([(r["from"], r["to"]) for r in _rows(d)], [("thrashing", "unknown")])

    def test_a_churning_bucket_does_not_truncate_a_quiet_neighbours_history(self):
        """A per-bucket `transitions` that is a filter of the GLOBAL 50-row tail lets a neighbour
        churning through fifty transitions erase a quiet bucket's history from its own payload. The
        rule: per-bucket `transitions` is that bucket's own last 50; the top-level
        list is the global last 50, each stamped with its bucket. Both tails persist."""
        d = tempfile.mkdtemp()
        ah = sb.ApiHealth(d)
        qk = LABEL + "|haiku"
        for e in _storm(T0 - 600, T0, family="haiku"):
            ah._push(e)
        for i in range(30):                      # the fable bucket churns: 60 transitions
            base = T0 + i * 5000
            for e in _storm(base - 600, base):
                ah._push(e)
            ah.snapshot(base)
            ah.snapshot(base + 2000)             # past retention: both buckets unknown
        end = T0 + 30 * 5000
        snap = ah.snapshot(end)
        self.assertEqual([(r["from"], r["to"]) for r in snap["buckets"][qk]["transitions"]],
                         [("unknown", "thrashing"), ("thrashing", "unknown")], "the quiet bucket keeps its own history")
        self.assertEqual(len(snap["buckets"][KEY]["transitions"]), sb.API_HEALTH_TRANSITIONS_KEEP)
        self.assertTrue(all(r["bucket"] == KEY for r in snap["buckets"][KEY]["transitions"]))
        self.assertEqual(len(snap["transitions"]), sb.API_HEALTH_TRANSITIONS_KEEP)
        self.assertTrue(all(r["bucket"] == KEY for r in snap["transitions"]), "the global tail is the churner's")
        # …and both tails survive a restart through the state file (both buckets are already unknown, so the
        # boot files nothing new)
        doc = _doc(d)
        self.assertEqual(len(doc["buckets"][qk]["transitions"]), 2)
        self.assertEqual(len(doc["buckets"][KEY]["transitions"]), sb.API_HEALTH_TRANSITIONS_KEEP)
        snap2 = sb.ApiHealth(d, boot_at=end + 1).snapshot(end + 2)
        self.assertEqual(snap2["buckets"][qk]["transitions"], snap["buckets"][qk]["transitions"])
        self.assertEqual(snap2["buckets"][KEY]["transitions"], snap["buckets"][KEY]["transitions"])
        self.assertEqual(snap2["transitions"], snap["transitions"])

    def test_every_transition_logs_one_line_in_the_kernel_log(self):
        """A read that finds a transition appends it, writes the state file and logs one stderr line
        in the existing `retry-pause:` style. The line names the bucket, the move and the why, so the
        kernel log reconstructs an incident as a polling reader observed it (a transition is derived
        only by a read of the signal; nothing derives while nobody reads)."""
        d = tempfile.mkdtemp()
        lines = []
        ah = sb.ApiHealth(d, log=lines.append)
        for e in _storm(T0 - 600, T0):
            ah._push(e)
        ah.snapshot(T0)

        def moves(ls):
            return [l for l in ls if l.startswith("api-health: ") and " -> " in l]
        self.assertEqual(len(moves(lines)), 1, lines)
        self.assertIn(KEY + " unknown -> thrashing", moves(lines)[0])
        self.assertIn("rate429 over", moves(lines)[0], "the why rides the line")
        ah.snapshot(T0 + 5)
        self.assertEqual(len(moves(lines)), 1, "no change, no line")
        ah.snapshot(T0 + 2000)
        self.assertEqual(len(moves(lines)), 2)
        self.assertIn(KEY + " thrashing -> unknown", moves(lines)[1])
        # the boot's continuity row is a transition too, and logs; a bucket already unknown files nothing
        boot = []
        sb.ApiHealth(d, log=boot.append, boot_at=T0 + 3000)
        self.assertEqual(moves(boot), [])
        d2 = tempfile.mkdtemp()
        ah2 = sb.ApiHealth(d2)
        for e in _storm(T0 - 600, T0):
            ah2._push(e)
        ah2.snapshot(T0)
        boot = []
        sb.ApiHealth(d2, log=boot.append, boot_at=T0 + 30)
        self.assertEqual(len(moves(boot)), 1, boot)
        self.assertIn(KEY + " thrashing -> unknown", moves(boot)[0])
        self.assertIn("restart", moves(boot)[0])


class Diagnostics(unittest.TestCase):
    def setUp(self):
        sb.SdkSession._retry_shape_logged = False
        sb.SdkSession._sys_subtypes_seen = set()

    def tearDown(self):
        sb.SdkSession._retry_shape_logged = False
        sb.SdkSession._sys_subtypes_seen = set()

    def test_the_first_api_retry_frame_logs_its_sorted_keys_once(self):
        lines = []
        be = _backend(log=lines.append)
        s = _session(be)
        for i in range(3):
            _feed(s, FakeSystemMessage("api_retry", retry_frame(attempt=i + 1)))
        hits = [l for l in lines if "first api_retry frame" in l]
        self.assertEqual(len(hits), 1)
        self.assertIn("keys=['attempt', 'error', 'error_status', 'max_retries', 'retry_delay_ms', "
                      "'session_id', 'uuid']", hits[0], "sorted, keys only")
        self.assertNotIn("rate_limit", hits[0], "values never ride the line (they would carry error text)")

    def test_an_unhandled_system_subtype_logs_once_per_subtype(self):
        lines = []
        be = _backend(log=lines.append)
        s = _session(be)
        for st in ("memory_recall", "memory_recall", "thinking_tokens"):
            _feed(s, FakeSystemMessage(st, {"k1": 1, "k2": 2}))
        hits = [l for l in lines if "unhandled SystemMessage subtype" in l]
        self.assertEqual(len(hits), 2)
        self.assertIn("'memory_recall'", hits[0])
        self.assertIn("['k1', 'k2']", hits[0])
        self.assertIn("'thinking_tokens'", hits[1])

    def test_handled_subtypes_are_never_reported_as_unhandled(self):
        lines = []
        be = _backend(log=lines.append)
        s = _session(be)
        _feed(s, FakeSystemMessage("api_retry", retry_frame()))
        self.assertFalse(any("unhandled SystemMessage" in l for l in lines))
        src = inspect.getsource(sb.SdkSession._on_message)
        i_tasks = src.index('"task_started", "task_progress", "task_updated", "task_notification"')
        i_trail = src.index("elif isinstance(msg, SystemMessage):\n            # A subtype no branch above handles")
        i_asst = src.index("elif isinstance(msg, AssistantMessage):")
        self.assertTrue(i_tasks < i_trail < i_asst, "the trailing branch sits right after the task events")


if __name__ == "__main__":
    unittest.main(verbosity=2)
