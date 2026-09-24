#!/usr/bin/env python3
"""The kernel's half of the Codex postal tools (2026-09-19): `_codex_postal_call(tool, sid, name, args)` maps the six
tools a Codex thread carries as Codex dynamic tools onto the postal bus's routes over loopback, as the kernel (the serve
token never enters the sandbox), with the sender fixed to the calling session's sid; it is the callable kernel.py hands
CodexBackend as `postal=`. Pinned here against a fake bus socket: the /send payload (from_id the sid, from the name,
tracked only on a delegate), the client-side checks the bus's own MCP tool makes (to/body/kind/tracked), a bus refusal
echoed as a failed result, a written-but-unanswered send said as MAYBE delivered, the inbox rendered with the tool
named for the reply (never the shell command the sandbox refuses), set_working landing in the kernel's own store, and
the sentences copied from the bus pinned against the bus module itself, loaded by path the way
tests/test_postal_live_only.py loads it (the kernel never imports it: the bus is its own process). The bus's own half is
here too: the push banner names the tool for a Codex recipient, and `romp mail send` from a Codex shell points at it.
Synthetic ids and the notes-api demo's session names only; the fakes carry no token."""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from romp_load import load_source

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)                    # a live kernel's export outranks the XDG floor
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")      # the seam every kernel test uses: no token file is minted
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_codex_postal", os.path.join(BIN, "romp-kernel"))
jd = km.jd
cb = load_source("romp_codex_backend_codex_postal", os.path.join(ROOT, "kernel", "codex_backend.py"))
pm = load_source("romp_postal_codex_postal", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-3333-4444-555555555555"     # the Codex session (api) making the calls
WEB = "11111111-2222-3333-4444-666666666666"     # its peer
OTHER = "99999999-8888-7777-6666-555555555555"   # a sender a model might forge


class _Conn:
    """One scripted loopback connection: records what the kernel sent, answers the next step of the script."""

    def __init__(self, script, log, host, port, timeout=None):
        self.script, self.log, self.host, self.port, self.timeout = script, log, host, port, timeout

    def request(self, method, path, body=None, headers=None):
        step = self.script[0] if self.script else {}
        if step.get("refuse"):
            raise ConnectionRefusedError(111, "Connection refused")
        self.log.append({"method": method, "path": path, "body": json.loads(body) if body else None,
                         "headers": dict(headers or {}), "timeout": self.timeout, "host": self.host})

    def getresponse(self):
        step = self.script.pop(0) if self.script else {}
        if step.get("hang"):
            raise TimeoutError("timed out")
        data = json.dumps(step.get("body", {})).encode("utf-8")
        return SimpleNamespace(status=step.get("status", 200), read=lambda: data)

    def close(self):
        pass


@contextlib.contextmanager
def bus(*steps):
    script, log = list(steps), []
    with mock.patch.object(km.http.client, "HTTPConnection",
                           lambda host, port, timeout=None: _Conn(script, log, host, port, timeout)):
        yield log


def call(tool, args, name="api"):
    return km._codex_postal_call(tool, SID, name, args)


class SendMessage(unittest.TestCase):
    def test_posts_as_the_calling_session_and_reads_the_kind_back(self):
        with bus({"body": {"ok": True, "to": "web"}}) as log:
            ok, text = call("send_message", {"to": "web", "body": "the tests are green", "kind": "coordinate",
                                             "from_id": OTHER})
        self.assertEqual((ok, text), (True, "Delivered to 'web'."))
        self.assertEqual(len(log), 1)
        req = log[0]
        self.assertEqual((req["method"], req["path"], req["host"]), ("POST", "/send", "127.0.0.1"))
        # the sender is the SESSION, whatever the arguments claimed; no tracked flag on a coordinate
        self.assertEqual(req["body"], {"to": "web", "from": "api", "from_id": SID,
                                       "body": "the tests are green", "kind": "coordinate"})
        self.assertEqual(req["headers"]["X-Romp-Token"], km.TOKEN)
        self.assertEqual(req["timeout"], km.CODEX_POSTAL_TIMEOUT_S)
        self.assertEqual(km.CODEX_POSTAL_TIMEOUT_S, 3.0, "the reader-thread budget the design names")

    def test_tracked_rides_a_delegate_only_and_each_kind_reads_its_sentence(self):
        with bus({"body": {"ok": True}}) as log:
            ok, text = call("send_message", {"to": "web", "body": "take the exporter", "kind": "delegate",
                                             "tracked": True})
        self.assertTrue(ok)
        self.assertIn("tracked handoff", text)
        self.assertIs(log[0]["body"]["tracked"], True)
        with bus({"body": {"ok": True}}) as log:
            ok, text = call("send_message", {"to": "web", "body": "take the exporter", "kind": "delegate"})
        self.assertTrue(ok)
        self.assertIn("handoff", text)
        self.assertNotIn("tracked", log[0]["body"])
        with bus({"body": {"ok": True}}) as log:
            ok, text = call("send_message", {"to": "web", "body": "which port?", "kind": "question", "tracked": True})
        self.assertTrue(ok)
        self.assertIn("as a question", text)
        self.assertNotIn("tracked", log[0]["body"], "tracked is a delegate's flag only")
        # the bus's note wins over the kind sentence: a relay or a park is not "delivered"
        with bus({"body": {"ok": True, "id": "px-1", "note": "relaying to 'web' on TESTHOST"}}):
            ok, text = call("send_message", {"to": "web", "body": "hi", "kind": "coordinate"})
        self.assertEqual((ok, text), (True, "Message to 'web': relaying to 'web' on TESTHOST"))

    def test_refuses_what_the_bus_tool_refuses_before_any_request(self):
        cases = [({"body": "x", "kind": "coordinate"}, "to"),
                 ({"to": "web", "kind": "coordinate"}, "body"),
                 ({"to": "web", "body": "x", "kind": "fyi"}, "kind"),
                 ({"to": "web", "body": "x"}, "kind"),
                 ({"to": "web", "body": "x", "kind": "delegate", "tracked": "yes"}, "tracked")]
        for args, word in cases:
            with bus({"body": {"ok": True}}) as log:
                ok, text = call("send_message", args)
            self.assertFalse(ok, args)
            self.assertIn(word, text)
            self.assertEqual(log, [], "refused client-side, as the bus's MCP tool does: %r" % (args,))

    def test_a_bus_refusal_is_a_failed_result_carrying_its_text(self):
        why = "this session's mailbox is off: the user toggled it"
        with bus({"status": 403, "body": {"error": why}}):
            ok, text = call("send_message", {"to": "web", "body": "hi", "kind": "coordinate"})
        self.assertEqual((ok, text), (False, why))
        with bus({"status": 503, "body": {"ok": False, "error": "the message could not be recorded"}}):
            ok, text = call("send_message", {"to": "web", "body": "hi", "kind": "coordinate"})
        self.assertEqual((ok, text), (False, "the message could not be recorded"))

    def test_an_unanswered_send_says_it_may_have_gone_through(self):
        # written with no answer inside the budget: the bus may have delivered, so the sentence says maybe and
        # names check_sent, or a delegate gets sent twice (the refuter's amendment 3)
        with bus({"hang": True}):
            ok, text = call("send_message", {"to": "web", "body": "take it", "kind": "delegate"})
        self.assertFalse(ok)
        self.assertIn("may", text)
        self.assertIn("check_sent", text)
        # never written (the bus is down): nothing went through, and the text does not say maybe
        with bus({"refuse": True}):
            ok, text = call("send_message", {"to": "web", "body": "take it", "kind": "delegate"})
        self.assertFalse(ok)
        self.assertNotIn("check_sent", text)
        self.assertIn("could not be reached", text)


class OtherTools(unittest.TestCase):
    def test_check_inbox_consumes_and_names_the_tool_for_the_reply(self):
        msgs = [{"id": "m-1", "from": "web", "from_id": WEB, "body": "ready for review", "kind": "question",
                 "date": "09:15"},
                {"id": "m-2", "from": "api", "from_id": SID, "body": "my own report", "kind": "coordinate"}]
        with bus({"body": {"messages": msgs}}) as log:
            ok, text = call("check_inbox", {})
        self.assertTrue(ok)
        self.assertEqual(log[0]["path"], "/inbox?id=%s" % SID, "a consuming read, as the bus's tool makes")
        for piece in ("from web", "ready for review", "<!-- romp-msg-id: m-1 -->", "<!-- romp-msg-kind: question -->",
                      "YOUR OWN message", "send_message"):
            self.assertIn(piece, text)
        self.assertNotIn("romp mail send", text, "the shell command the sandbox refuses (amendment 5)")
        with bus({"body": {"messages": []}}):
            self.assertEqual(call("check_inbox", {}), (True, "No new messages."))
        with bus({"status": 503, "body": {"error": "new/ cannot be listed", "unreadable": "x", "messages": []}}):
            ok, text = call("check_inbox", {})
        self.assertFalse(ok)
        self.assertIn("cannot be read", text)
        # the fault branches (the review, 2026-09-19): a hang is a WRITTEN request the bus may have acted on, so
        # the sentence warns that mail may now count as read without having been shown; a refused connection
        # wrote nothing, and says only that the next check retries
        with bus({"hang": True}) as log:
            ok, text = call("check_inbox", {})
        self.assertFalse(ok)
        self.assertEqual(len(log), 1, "the request was written")
        self.assertIn("count as read", text)
        with bus({"refuse": True}) as log:
            ok, text = call("check_inbox", {})
        self.assertFalse(ok)
        self.assertEqual(log, [], "nothing was written")
        self.assertNotIn("count as read", text)

    def test_list_agents_marks_you_by_id_and_flags_a_stale_claim(self):
        rows = [{"id": SID, "name": "api", "dir": "/TESTDIR/api", "state": "working", "working": "the exporter",
                 "branch": "api"},
                {"id": WEB, "name": "web", "dir": "/TESTDIR/web", "state": "waiting", "working": "the fixtures",
                 "branch": "web"},
                {"id": "TESTHOST:" + OTHER, "name": "tests", "remote": True}]
        with bus({"body": {"agents": rows, "me": "api"}}) as log:
            ok, text = call("list_agents", {})
        self.assertTrue(ok)
        self.assertEqual(log[0]["path"], "/agents?me=api")
        lines = text.splitlines()
        self.assertIn("api (you)", lines[0])
        self.assertIn("[api]", lines[0])
        self.assertIn("the exporter", lines[0])
        self.assertNotIn("stale", lines[0])
        self.assertIn("the fixtures", lines[1])
        self.assertIn("claim may be stale", lines[1], "an idle peer's note is a claim from a finished turn")
        self.assertIn("TESTHOST:tests", lines[2])
        self.assertIn("[remote]", lines[2])
        with bus({"body": {"agents": []}}):
            ok, text = call("list_agents", {})
        self.assertTrue(ok)
        self.assertIn("own mailbox is off is not listed", text)

    def test_set_working_writes_the_kernels_own_store_without_the_bus(self):
        with bus() as log:
            ok, text = call("set_working", {"text": "editing the exporter"})
            self.assertEqual((ok, text), (True, "Published — others see: working on 'editing the exporter'."))
            self.assertEqual(km._working_notes().get(SID), "editing the exporter")
            # a MISSING text is never a clear (the bus's rule): refused, the note stands
            ok, text = call("set_working", {})
            self.assertFalse(ok)
            self.assertEqual(km._working_notes().get(SID), "editing the exporter")
            ok, text = call("set_working", {"text": ""})
            self.assertEqual((ok, text), (True, "Cleared your 'working on' note."))
            self.assertNotIn(SID, km._working_notes())
        self.assertEqual(log, [], "the kernel owns the store; no loopback round trip")

    def test_check_sent_and_recall_message(self):
        recs = [{"id": "m-1", "to": "web", "sent": 1781100000, "exec": 1781100600},
                {"id": "m-2", "to": "web", "sent": 1781100000},
                {"id": "m-3", "to": "tests", "sent": 1781100000, "parked": "TESTHOST", "parkedUp": False}]
        with bus({"body": {"sent": recs}}) as log:
            ok, text = call("check_sent", {})
        self.assertTrue(ok)
        self.assertEqual(log[0]["path"], "/sent?id=%s" % SID)
        self.assertIn("read ", text)
        self.assertIn("pending (not read yet) · id m-2", text)
        self.assertIn("unreachable", text)
        with bus({"body": {"sent": []}}):
            self.assertEqual(call("check_sent", {}), (True, "No messages sent yet."))
        with bus({"body": {"ok": True, "removed": [{"to": "web", "body": "take it"}], "kept": []}}) as log:
            ok, text = call("recall_message", {"to": "web"})
        self.assertTrue(ok)
        self.assertEqual(log[0]["body"], {"from_id": SID, "to": "web", "id": ""})
        self.assertIn("Recalled 1 message(s)", text)
        with bus({"body": {"ok": True, "removed": [], "kept": [{"to": "tests", "id": "m-3", "carried": True,
                                                                  "host": "TESTHOST", "body": "take it"}]}}):
            ok, text = call("recall_message", {"id": "m-3"})
        self.assertTrue(ok)
        self.assertIn("NOT recalled", text)
        with bus({"body": {"ok": True, "removed": [], "kept": []}}):
            ok, text = call("recall_message", {"to": "web"})
        self.assertIn("Nothing to recall", text)
        with bus({"body": {"ok": True}}) as log:
            ok, text = call("recall_message", {})
        self.assertFalse(ok)
        self.assertEqual(log, [])

    def test_the_fault_branches_of_the_read_tools_tell_a_written_request_from_an_unwritten_one(self):
        # list_agents, check_sent and recall_message (the review, 2026-09-19): a hang is a written request with no
        # answer inside the budget, said as "No answer"; a refused connection wrote nothing and says the bus could
        # not be reached, the distinction _codex_postal_http draws with its -1 and 0 statuses
        for tool, args in (("list_agents", {}), ("check_sent", {}), ("recall_message", {"to": "web"})):
            with bus({"hang": True}) as log:
                ok, text = call(tool, args)
            self.assertFalse(ok, tool)
            self.assertEqual(len(log), 1, "%s: the request was written" % tool)
            self.assertIn("No answer", text, tool)
            with bus({"refuse": True}) as log:
                ok, text = call(tool, args)
            self.assertFalse(ok, tool)
            self.assertEqual(log, [], "%s: nothing was written" % tool)
            self.assertIn("could not be reached", text, tool)

    def test_an_unknown_tool_is_refused(self):
        with bus() as log:
            self.assertEqual(call("read_secrets", {}), (False, "Unknown tool: read_secrets"))
        self.assertEqual(log, [])

    def test_the_kernel_hands_the_callable_to_the_codex_backend(self):
        saved = km._codex_backend
        km._codex_backend = None
        try:
            be = km._codex()
            self.assertIsNotNone(be, "the backend module is loadable here")
            self.assertIs(be.postal, km._codex_postal_call)
        finally:
            km._codex_backend = saved

    def test_the_setting_file_turns_the_registration_off(self):
        # STATE/codex-postal-tools `off` builds the backend with no callable (no tools registered, said once by the
        # backend); absent or any other value is on. Read where the backend is built, so it applies at the next
        # kernel start (the refuter's amendment 9, default on)
        f = jd.STATE / "codex-postal-tools"
        saved = km._codex_backend
        try:
            f.write_text("off\n")
            self.assertFalse(km._codex_postal_tools_on())
            km._codex_backend = None
            self.assertIsNone(km._codex().postal)
            f.write_text("on\n")
            self.assertTrue(km._codex_postal_tools_on())
            f.unlink()
            self.assertTrue(km._codex_postal_tools_on(), "absent is on")
            km._codex_backend = None
            self.assertIs(km._codex().postal, km._codex_postal_call)
        finally:
            f.unlink(missing_ok=True)
            km._codex_backend = saved


class ParityWithTheBus(unittest.TestCase):
    """The backend's copy of the bus's tool table and instructions, and the kernel's copies of the bus's result
    sentences, pinned against the bus module itself (a KEEP-IN-SYNC copy is only honest with this test beside it)."""

    def setUp(self):
        self._saved = (pm._self_identity, pm._http, pm._publish_working, pm._LOCAL_CONFIRMED[0])
        pm._self_identity = lambda: (SID, "api")
        pm._LOCAL_CONFIRMED[0] = True                    # a confirmed-local session's beat is a no-op the tool skips
        pm._publish_working = lambda sid, text: True

    def tearDown(self):
        pm._self_identity, pm._http, pm._publish_working, pm._LOCAL_CONFIRMED[0] = self._saved

    def test_the_tool_specs_match_name_for_name_and_schema_for_schema(self):
        self.assertEqual([t["name"] for t in cb.POSTAL_TOOL_SPECS], [t["name"] for t in pm.MCP_TOOLS])
        for mine, theirs in zip(cb.POSTAL_TOOL_SPECS, pm.MCP_TOOLS):
            self.assertEqual(mine, {"type": "function", "name": theirs["name"],
                                    "description": theirs["description"], "inputSchema": theirs["inputSchema"]})

    def test_the_instructions_match_paragraph_for_paragraph_minus_the_claude_code_note(self):
        # the last paragraph of the bus's instructions is about Claude Code's own cross-session messaging, which a
        # Codex model has no such thing to be steered away from; every other paragraph is carried verbatim
        theirs = [p for p in pm.MCP_INSTRUCTIONS.strip().split("\n\n") if not p.startswith("Claude Code ships")]
        self.assertEqual(cb.POSTAL_INSTRUCTIONS.strip().split("\n\n"), theirs)
        self.assertEqual(len(theirs) + 1, len(pm.MCP_INSTRUCTIONS.strip().split("\n\n")),
                         "exactly one paragraph is left out, the Claude Code one")

    def _set_working_both(self, why):
        saved = (pm._mail_off_why, km._mail_off_why_k)
        pm._mail_off_why, km._mail_off_why_k = (lambda sid: why), (lambda sid: why)
        try:
            theirs = pm._mcp_call("set_working", {"text": "editing the exporter"})
            with bus():
                mine = call("set_working", {"text": "editing the exporter"})
        finally:
            pm._mail_off_why, km._mail_off_why_k = saved
        self.assertEqual(mine, (not theirs[1], theirs[0]))
        return mine[1]

    def test_a_mail_off_callers_note_is_saved_but_not_claimed_seen_by_both_copies(self):
        self.assertIn("no peer sees it", self._set_working_both("isolation"))
        self.assertEqual(km._codex_agents_text([], SID), pm.format_agents([], "api", SID))

    def test_a_master_isolated_callers_note_is_published_by_both_copies(self):
        self.assertIn("Published", self._set_working_both("master"))

    def test_both_listings_mark_a_master_isolated_row_alike(self):
        rows = [{"id": SID, "name": "api"}, {"id": "b" * 36, "name": "peer", "mailOff": "master", "branch": "dev"}]
        self.assertEqual(km._codex_agents_text(rows, SID), pm.format_agents(rows, "api", SID))
        self.assertIn("peer  (mail off by the master default: not reachable) · bbbbbbbb", pm.format_agents(rows, "api", SID))
        self.assertNotIn("not reachable", pm.format_agents(rows, "api", SID).splitlines()[0])

    def test_the_send_sentences_and_wire_payload_match_the_bus_tool(self):
        for args, resp in ((dict(to="web", body="hi", kind="coordinate"), {"ok": True}),
                           (dict(to="web", body="hi", kind="question"), {"ok": True}),
                           (dict(to="web", body="hi", kind="delegate"), {"ok": True}),
                           (dict(to="web", body="hi", kind="delegate", tracked=True), {"ok": True}),
                           (dict(to="web", body="hi", kind="question", tracked=True), {"ok": True}),
                           (dict(to="web", body="hi", kind="coordinate"), {"ok": True, "note": "relaying to 'web' on TESTHOST"}),
                           (dict(body="hi", kind="coordinate"), {"ok": True}),
                           (dict(to="web", body="hi", kind="fyi"), {"ok": True}),
                           (dict(to="web", body="hi", kind="delegate", tracked="yes"), {"ok": True})):
            theirs_wire = []
            pm._http = lambda method, path, payload=None, _r=resp, _w=theirs_wire: _w.append((method, path, payload)) or dict(_r)
            theirs = pm._mcp_call("send_message", dict(args))
            with bus({"body": resp}) as log:
                mine = call("send_message", dict(args))
            self.assertEqual(mine, (not theirs[1], theirs[0]), args)
            # the KEEP-IN-SYNC claim covers the wire shape too: what the bus tool POSTs is what the kernel POSTs
            self.assertEqual([(r["method"], r["path"], r["body"]) for r in log], theirs_wire, args)

    def test_the_receipts_agents_and_inbox_renderers_match_the_bus(self):
        t = 1781100000
        recs = [{"id": "m-1", "to": "web", "sent": t, "exec": t + 60},
                {"id": "m-2", "to": "web", "sent": t, "recalled": t + 60},
                {"id": "m-3", "to": "web", "sent": t, "bounced": t + 60, "bouncedWhy": pm.WHY_NOT_PUBLISHED + "x"},
                {"id": "m-4", "to": "web", "sent": t, "bounced": t + 60, "bouncedWhy": ""},
                {"id": "m-5", "to": "web", "sent": t, "bounced": t + 60, "bouncedWhy": "some other reason"},
                {"id": "m-6", "to": "tests", "sent": t, "parked": "TESTHOST", "carried": t + 60},
                {"id": "m-7", "to": "tests", "sent": t, "parked": "TESTHOST", "parkedUp": True},
                {"id": "m-8", "to": "tests", "sent": t, "parked": "TESTHOST", "parkedUp": False},
                {"id": "m-9", "to": "tests", "sent": t, "parked": "TESTHOST"},
                {"id": "m-10", "to": "tests", "sent": t, "relayed": t + 60},
                {"id": "m-11", "to": "web", "sent": t},
                {"id": "m-12", "to": "web", "sent": t, "onBehalf": True}]
        for why in pm.REFUSAL_WHYS:                       # every refusal prefix the bus knows is one the kernel knows
            self.assertTrue(why.startswith(km._CODEX_REFUSAL_WHYS), why)
        self.assertEqual(km._codex_receipts_text(recs), pm.format_receipts(recs))
        self.assertEqual(km._codex_receipts_text([]), pm.format_receipts([]))
        agents = [{"id": SID, "name": "api", "dir": "/TESTDIR/api", "state": "working", "working": "the exporter", "branch": "api"},
                  {"id": WEB, "name": "web", "dir": "/TESTDIR/web", "state": "waiting", "working": "the fixtures", "branch": "web"},
                  {"id": WEB + ":t1", "name": "web-t1", "thread": True, "parent": WEB},
                  {"id": "TESTHOST:" + OTHER, "name": "tests", "remote": True},
                  {"id": "TESTHOST:" + SID, "name": "TESTHOST:api", "remote": True, "working": "x"}]
        self.assertEqual(km._codex_agents_text(agents, SID), pm.format_agents(agents, "api", SID))
        self.assertEqual(km._codex_agents_text([], SID), pm.format_agents([], "api", SID))
        msgs = [{"id": "m-1", "from": "web", "from_id": WEB, "body": "ready for review", "kind": "question", "date": "09:15"},
                {"id": "m-2", "from": "api", "from_id": SID, "body": "my own report", "kind": "coordinate", "park": True},
                {"from": "unknown", "body": "a ghost"}]
        mine, theirs = km._codex_inbox_text(msgs, SID).splitlines(), pm.format_inbox(msgs, SID).splitlines()
        self.assertEqual(mine[:-1], theirs[:-1], "every line but the reply hint is the bus's")
        self.assertIn("send_message", mine[-1])
        self.assertIn("romp mail send", theirs[-1])

    def test_the_recall_sentences_match_the_bus_tool(self):
        # the kernel's KEEP-IN-SYNC copy of the bus tool's recall branch (the review, 2026-09-19): every shape
        # through both, the empty pair's "Nothing to recall" included. A carried kept row WITHOUT its own why is
        # the only path through the kernel's inlined WHY_CARRIED literal (the bus server fills why itself), and
        # every removed row carries both to and body, which the bus tool indexes directly where the kernel uses
        # .get (a missing key raises on the bus side instead of yielding a comparable string)
        shapes = [
            {"removed": [], "kept": []},
            {"removed": [{"to": "web", "body": "take it"}], "kept": []},
            {"removed": [{"to": "web", "body": "take it"}, {"to": "web", "body": "and the fixtures"}], "kept": []},
            {"removed": [], "kept": [{"to": "tests", "id": "m-3", "carried": True, "host": "TESTHOST", "body": "take it"}]},
            {"removed": [], "kept": [{"to": "tests", "id": "m-4", "why": "is still queued for relay to TESTHOST",
                                      "body": "x"}]},
            {"removed": [{"to": "web", "body": "a"}],
             "kept": [{"to": "tests", "id": "m-5", "carried": True, "why": "left for TESTHOST", "body": "b"},
                      {"to": "tests", "id": "m-6", "body": "c"}]},
        ]
        for resp in shapes:
            pm._http = lambda method, path, payload=None, _r=resp: dict(_r, ok=True)
            theirs, err = pm._mcp_call("recall_message", {"to": "web"})
            self.assertFalse(err, resp)
            self.assertEqual(km._codex_recall_text(resp["removed"], resp["kept"]), theirs, resp)

    def test_the_inbox_fault_and_working_note_sentences_match_the_bus_tool(self):
        def raising(status, text):
            def _http(method, path, payload=None):
                err = pm.BusError(text)
                if status:
                    err.status = status
                raise err
            return _http
        # the bus's own 503 for a box whose new/ cannot be listed (its _inbox_fault reads the status AND the text)
        pm._http = raising(503, "new/ cannot be listed")
        with bus({"status": 503, "body": {"error": "new/ cannot be listed", "unreadable": "x", "messages": []}}):
            self.assertEqual(call("check_inbox", {}), (False, pm._mcp_call("check_inbox", {})[0]))
        pm._http = raising(None, "can't reach the bus")
        with bus({"refuse": True}):
            self.assertEqual(call("check_inbox", {}), (False, pm._mcp_call("check_inbox", {})[0]))
        for args in ({"text": "editing the exporter"}, {"text": ""}, {}):
            theirs = pm._mcp_call("set_working", dict(args))
            self.assertEqual(call("set_working", dict(args)), (not theirs[1], theirs[0]), args)


class _DeadStderr(io.TextIOBase):
    """A stderr that cannot be written (ENOSPC): every write raises."""

    def write(self, s):
        raise OSError(28, "No space left on device")


class TheKernelLogNamesAFault(unittest.TestCase):
    """The kernel log's line for a failed call (the review, 2026-09-19): the callable never raises, so the backend's
    raise-only log line never fires, and before this a refused bus, a hung bus and a store raise each reached the
    session as a failed result and left the kernel log, the one cross-session surface, empty. One line naming the
    tool, the session and the cause: the two bus faults once per fault spell per cause, re-armed by the bus's next
    answer (the _INTR_MARKS_WRITE_SAID shape, keyed by cause), a store raise every time; the sentence to the session
    is unchanged."""

    def setUp(self):
        km._CODEX_POSTAL_SAID.clear()
        # the bus-port dial's census line (_bus_port_census) lands on stderr on a process's FIRST dial, and the dial
        # sits outside the request's try; a capture of all of stderr then counts it (2 lines, not 1) or, with stderr
        # dead, takes its raise before the request (the second review, 2026-09-19: each test alone red, in file order
        # green, a worker collecting this class first red). One dial here says it before any capture; the census's
        # own wrap is pinned below by re-creating a first dial inside the dead capture.
        km._bus_port()

    def _stderr_of(self, fn):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = fn()
        return out, err.getvalue().splitlines()

    def test_a_bus_fault_is_said_once_per_spell_and_a_store_raise_every_time(self):
        def two_refused():
            with bus({"refuse": True}):
                return call("check_inbox", {}), call("list_agents", {})
        (first, second), lines = self._stderr_of(two_refused)
        self.assertEqual((first[0], second[0]), (False, False))
        self.assertIn("could not be reached", first[1], "the sentence to the session is the per-fault one")
        self.assertEqual(len(lines), 1, "said once for the spell, not per call: %r" % lines)
        for piece in ("check_inbox", SID, "ConnectionRefusedError"):
            self.assertIn(piece, lines[0])
        self.assertNotIn("list_agents", lines[0], "the second call of the spell adds no line")
        # an answer from the bus (any status) ends the spell and re-arms the latch
        with bus({"status": 403, "body": {"error": "mailbox off"}}):
            call("check_sent", {})

        def two_hung():
            with bus({"hang": True}, {"hang": True}):
                return call("check_sent", {}), call("send_message", {"to": "web", "body": "hi", "kind": "coordinate"})
        (first, second), lines = self._stderr_of(two_hung)
        self.assertEqual((first[0], second[0]), (False, False))
        self.assertIn("No answer", first[1])
        self.assertEqual(len(lines), 1, lines)
        for piece in ("check_sent", SID, "TimeoutError"):
            self.assertIn(piece, lines[0])

        # a store raise is no spell: every one is said, and the sentence carries the exception's text
        def two_raises():
            with mock.patch.object(km, "_set_working_note", side_effect=OSError(28, "No space left on device")):
                return call("set_working", {"text": "a"}), call("set_working", {"text": "b"})
        (first, second), lines = self._stderr_of(two_raises)
        self.assertFalse(first[0])
        self.assertIn("No space left", first[1])
        self.assertEqual(len(lines), 2, lines)
        for line in lines:
            for piece in ("set_working", SID, "No space left"):
                self.assertIn(piece, line)

    def test_a_stderr_that_cannot_be_written_does_not_swallow_the_sentence(self):
        with contextlib.redirect_stderr(_DeadStderr()):
            with bus({"refuse": True}):
                out = call("check_inbox", {})
            with mock.patch.object(km, "_set_working_note", side_effect=OSError(28, "No space left on device")):
                out2 = call("set_working", {"text": "a"})
        self.assertEqual(out, (False, "The mail service could not be reached just now; your mail waits and the next "
                                      "check retries."))
        self.assertEqual(out2[0], False)
        self.assertIn("No space left", out2[1], "the store's own raise, not the log write's")

    def test_a_first_dials_census_line_on_a_dead_stderr_does_not_swallow_the_sentence_either(self):
        # the census write in _bus_port_census reached production unwrapped (the second review, 2026-09-19): a port
        # or source change after boot makes a Codex call's dial say the census, and with stderr unwritable the raise
        # reached _codex_postal_call's catch-all, so the session got the generic sentence, not the per-fault one, and
        # the bus was never dialed. Resetting the census's memory re-creates a first dial inside the dead capture.
        km._BUS_PORT_SAID[0] = None
        with contextlib.redirect_stderr(_DeadStderr()):
            with bus({"refuse": True}):
                out = call("check_inbox", {})
        self.assertEqual(out, (False, "The mail service could not be reached just now; your mail waits and the next "
                                      "check retries."), "the per-fault sentence: the census write raised out of the dial")

    def test_a_spell_whose_cause_changes_with_no_answer_between_is_said_once_per_cause(self):
        # one slot for two causes (the second review, 2026-09-19): the latch was one boolean for both bus faults,
        # re-armed only by an answer, so a bus that refused and then hung, with no answer between, logged only the
        # refusal, and the log read "could not be reached" while requests were being written and left unanswered.
        # Keyed by cause: one line per cause per spell, the same cause again adding none, an answer clearing both.
        def refuse_then_hang():
            with bus({"refuse": True}):
                a = call("check_inbox", {})
            with bus({"hang": True}, {"hang": True}):
                return a, call("check_sent", {}), call("list_agents", {})
        (a, b, c), lines = self._stderr_of(refuse_then_hang)
        self.assertEqual((a[0], b[0], c[0]), (False, False, False))
        self.assertEqual(len(lines), 2, "one line per cause, the second hang adding none: %r" % lines)
        self.assertIn("could not be reached", lines[0])
        self.assertIn("check_inbox", lines[0])
        self.assertIn("no answer came", lines[1])
        self.assertIn("check_sent", lines[1])
        self.assertNotIn("list_agents", "\n".join(lines), "the second hang of the spell adds no line")
        for line in lines:
            # the suffix names the latch's grain (the third review, 2026-09-19): with the latch per cause, a suffix
            # claiming "once per fault spell" made the two lines of one spell each claim to be the spell's one
            self.assertIn("said once per cause per fault spell", line)
        # an answer of any status ends the spell for BOTH causes: the next refusal and the next hang are each said
        # again (the third review, 2026-09-19: asserting only the refusal's re-arm let a kernel that clears only the
        # unreachable cause on an answer pass the module)
        with bus({"status": 200, "body": {"agents": []}}):
            call("list_agents", {})

        def refused_then_hung_again():
            with bus({"refuse": True}):
                a = call("check_inbox", {})
            with bus({"hang": True}):
                return a, call("check_sent", {})
        (a, b), lines = self._stderr_of(refused_then_hung_again)
        self.assertEqual((a[0], b[0]), (False, False))
        self.assertEqual(len(lines), 2, "both causes re-armed by the answer, one no-answer line beside the re-said "
                                        "refusal: %r" % lines)
        self.assertIn("could not be reached", lines[0])
        self.assertIn("check_inbox", lines[0])
        self.assertIn("no answer came", lines[1])
        self.assertIn("check_sent", lines[1])
        for line in lines:
            self.assertIn("said once per cause per fault spell", line)


class TheBusNamesTheToolForCodex(unittest.TestCase):
    """The bus's half (the refuter's amendment 5): the push banner's reply hint and `romp mail send` used to name the
    very shell command the sandbox refuses; for a Codex recipient the banner names the send_message tool, and the CLI's
    Codex refusal points at it."""

    def test_the_push_banner_names_the_tool_for_a_codex_recipient_and_the_shell_for_the_rest(self):
        msgs = [{"id": "m-1", "from": "web", "from_id": WEB, "body": "ready for review", "kind": "question"}]
        shell = pm.format_push(msgs)
        self.assertIn("romp mail send", shell.splitlines()[-1], "the default banner is unchanged")
        tool = pm.format_push(msgs, reply_tool=True)
        self.assertEqual(tool.splitlines()[:-1], shell.splitlines()[:-1], "only the reply hint differs")
        self.assertIn("send_message", tool.splitlines()[-1])
        self.assertIn("web", tool.splitlines()[-1])
        self.assertNotIn("romp mail", tool)
        # the chunking measures the banner it will send
        self.assertEqual(pm._deliver_body_bytes(SID, msgs, reply_tool=True),
                         len(json.dumps({"id": SID, "text": tool}).encode("utf-8")))

    def test_push_picks_the_hint_from_the_recipients_backend(self):
        # the rows _push is handed are _agent_rows' (both callers: the wake wait and the retry pass, over
        # local_agents), built from the kernel's GET /sessions rows; a field _agent_rows does not copy never
        # reaches _push, so the rows here come from the REAL producer over kernel-shaped rows (the first cut of
        # this test hand-built the dict with a backend key and was green while the banner in production still
        # named the shell, 2026-09-19)
        msgs = [{"id": "m-1", "from": "web", "from_id": WEB, "body": "ready for review", "kind": "question"}]
        rows = {a["id"]: a for a in pm._agent_rows([
            {"id": SID, "name": "api", "state": "waiting", "backend": "codex", "dir": "/TESTDIR/api", "lastSid": SID},
            {"id": WEB, "name": "web", "state": "waiting", "backend": "sdk", "dir": "/TESTDIR/web", "lastSid": WEB}])}
        posted = []
        box = {"messages": list(msgs)}
        saved = (pm._drain, pm._kernel_post, pm._push_disabled, pm._name_for_id, pm._log,
                 os.environ.get("ROMP_SESSIONS_FILE"))
        pm._drain = lambda sid: {"messages": list(box["messages"])}
        pm._kernel_post = lambda path, body, timeout=None: posted.append((path, body)) or {"injected": True}
        pm._push_disabled = lambda: False
        pm._name_for_id = lambda sid, rows=None: {SID: "api", WEB: "web"}.get(sid, sid)   # no kernel to ask; the oversize note names the recipient
        pm._log = lambda m: None
        os.environ.pop("ROMP_SESSIONS_FILE", None)
        try:
            self.assertTrue(pm._push(SID, rows[SID]))
            self.assertTrue(pm._push(WEB, rows[WEB]))
            self.assertEqual([p for p, _ in posted], ["/deliver", "/deliver"])
            self.assertIn("send_message", posted[0][1]["text"].splitlines()[-1])
            self.assertIn("romp mail send", posted[1][1]["text"].splitlines()[-1])
            # the oversize bounce measures the banner the recipient would have got (the review, 2026-09-19): the
            # two bodies differ by 2 wire bytes (json.dumps escapes the shell hint's two double quotes), so a
            # message sized to sit exactly on the cap as a SHELL body crosses it only as a TOOL body. To a Codex
            # recipient it is bounced, with the tool body's size in the note, over the cap; to a shell recipient
            # the same message still rides the wake. The real deliver lands the note in the sender's box.
            big = dict(msgs[0], id="m-big")
            big["body"] = "x" * (pm._PUSH_MAX_BYTES - pm._deliver_body_bytes(SID, [dict(big, body="")]))
            self.assertEqual(pm._deliver_body_bytes(SID, [big]), pm._PUSH_MAX_BYTES)
            n_tool = pm._deliver_body_bytes(SID, [big], reply_tool=True)
            self.assertGreater(n_tool, pm._PUSH_MAX_BYTES)
            box["messages"] = [big]
            del posted[:]
            self.assertFalse(pm._push(SID, rows[SID]), "nothing rode the wake: the one message was oversize")
            self.assertEqual(posted, [], "no /deliver post for the Codex row")
            notes = pm.read_box(WEB, consume=False)
            self.assertEqual(len(notes), 1, notes)
            self.assertIn("undeliverable to 'api'", notes[0]["body"])
            said = int(re.search(r"is (\d+) bytes as delivered", notes[0]["body"]).group(1))
            self.assertEqual(said, n_tool, "the note reports the size as delivered to THIS recipient")
            self.assertGreater(said, pm._PUSH_MAX_BYTES, "a bounce naming a size under the limit explains nothing")
            self.assertTrue(pm._push(WEB, rows[WEB]), "the same message fits a shell recipient's banner")
            self.assertEqual([p for p, _ in posted], ["/deliver"])
            # the shell recipient's own bounce measurement was unasserted (the second review, 2026-09-19): the message
            # above sits ON the cap as a shell body, rides the wake and never reaches _bounce_oversize, so a bounce
            # measuring with the tool banner unconditionally stayed green. A message from the Codex session to the
            # shell recipient (a sender equal to the recipient gets no note, so the sender is the peer), one byte
            # over as a shell body, is bounced with the shell body's size in the note; measured with the tool banner
            # the note would read 2 bytes more.
            over = {"id": "m-over", "from": "api", "from_id": SID, "body": "", "kind": "coordinate"}
            over["body"] = "x" * (pm._PUSH_MAX_BYTES - pm._deliver_body_bytes(WEB, [over]) + 1)
            n_shell = pm._deliver_body_bytes(WEB, [over])
            self.assertEqual(n_shell, pm._PUSH_MAX_BYTES + 1)
            box["messages"] = [over]
            del posted[:]
            self.assertFalse(pm._push(WEB, rows[WEB]), "nothing rode the wake: one byte over as a shell body")
            self.assertEqual(posted, [], "no /deliver post for the shell row")
            notes = [n for n in pm.read_box(SID, consume=False) if "undeliverable to 'web'" in n["body"]]
            self.assertEqual(len(notes), 1, notes)
            said = int(re.search(r"is (\d+) bytes as delivered", notes[0]["body"]).group(1))
            self.assertEqual(said, n_shell, "the note reports the shell body's size, the banner THIS recipient gets")
        finally:
            pm._drain, pm._kernel_post, pm._push_disabled, pm._name_for_id, pm._log = saved[:5]
            restore_env("ROMP_SESSIONS_FILE", saved[5])

    def test_a_codex_shell_refused_by_mail_send_is_pointed_at_the_tool(self):
        saved = (pm.ensure, os.environ.get("CODEX_THREAD_ID"), os.environ.get("CLAUDE_CODE_SESSION_ID"))
        pm.ensure = lambda: True
        os.environ["CODEX_THREAD_ID"] = "01911111-2222-7333-8444-555555555555"   # no registry row: the lookup fails
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = pm.cli_send(["web", "hello"])
        finally:
            pm.ensure = saved[0]
            restore_env("CODEX_THREAD_ID", saved[1])
            restore_env("CLAUDE_CODE_SESSION_ID", saved[2])
        self.assertEqual(rc, 1)
        self.assertIn("send_message", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
