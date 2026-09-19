#!/usr/bin/env python3
"""client-diag.jsonl tells a declared redial from one the shim's dial term gated off (2026-09-10).

The pane shim declares a redial (`?reconnect=1&proto=N` on the /ws URL) only when `everConnected && bundleReady
&& readyAcked && !readyQueued` all hold, and its one drop breadcrumb, the `wsclose` row, recorded `everConnected`
alone, which is true on every such row (the same onopen sets both), so the kernel's log could not say which kind
of redial followed a close: a declared one, a drop before the bundle said ready, a ready that reached the shim
while its socket was going down and rode the redial as the bundle's own, or a ready that left on the socket and
no caps frame answered. The accept files a `wsopen` row per socket with the dial's term, but clientDiag rows
carry the dashboard id alone, so the queued `wsclose` row cannot be joined to its redial's `wsopen` row. Two
records fix that, each made where it is known:
- the kernel's clientDiag handler stamps EVERY row with `reconnect`, whether the socket that carried the row
  declared the redial: the socket's dial record (`redial`, set at accept beside the consumable `reconnect` and
  never consumed), so every row a socket carries reads the same value on every pane, before and after the strip
  has consumed the flag, and a skeleton column, whose accept arms `reconnect` for a dial that declared no redial,
  reads false. The shim queues the `wsclose` row while its socket is down and flushes it onto the redial;
- the shim's `wsclose` row carries `bundleReady`, `readyAcked` and `readyQueued`, the dial term's inputs at the
  close (everConnected was already there).
The readings: the stamp true, a declared redial; false with bundleReady false, the socket died before the bundle
said ready and the redial dialed fresh; false with readyQueued true, the ready queued during the close and rode the
redial as the bundle's own; false with bundleReady true and the other two false, the ready left but no caps frame
answered it, so the redial dialed fresh and re-posted the ready. A ready that lands after onclose and before the
redial (READY_AFTER_CLOSE below) leaves bundleReady false on the row, which was built at the close, and reads as
the gated shape, correctly.

Three legs: the handler alone (a client dict with and without the dial record), the REAL shim core under node
(the row's bits in each shape, and the frames the redial carried in order: the flushed row alone on the declared
shape, since an acked ready is not re-sent; the queued ready ahead of the row when it queued during the close;
the row ahead of a re-posted or queued ready otherwise), and the two joined (the real handshake dialed with the
URL the shim built, the shim's own row dispatched on that client, the stamp and the bits read back from the file,
and the wsopen row the accept filed agreeing with the stamp; a chat socket after the real strip's consumption and
a feed socket, which has no strip, read alike). Synthetic only: placeholder UUIDs, TESTHOST. Never run raw: a
raw run skips conftest's floor and can reach the live kernel.
"""
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_cdiag_reconnect", os.path.join(BIN, "romp-kernel"))

WID = "11111111-2222-3333-4444-555555555556"
S1 = "11111111-2222-3333-4444-555555555551"   # the tab the page persisted as active

# The browser the shim thinks it runs in (tests/test_pane_shim_return.py's HARNESS, the same shape): `var` at module
# scope shadows node's own WebSocket / setTimeout / MessageChannel for the core that follows in the same file.
_SHIM_HARNESS = r"""
var NOW=1000000;Date.now=function(){return NOW;};
var timers=[];var setTimeout=function(fn,ms){timers.push({fn:fn,ms:ms,live:true});return timers.length;};
var clearTimeout=function(id){if(id&&timers[id-1])timers[id-1].live=false;};
var setInterval=function(fn,ms){return 1;};
var docL={};var document={visibilityState:"visible",wasDiscarded:false,
addEventListener:function(t,f){(docL[t]=docL[t]||[]).push(f);},getElementById:function(){return null;}};
var window={innerWidth:800,innerHeight:600,parent:{postMessage:function(m){}},
dispatchEvent:function(e){return true;},sessionStorage:{getItem:function(){return "";}},__rompFed:{inbound:function(h,m){}}};
var location={protocol:"http:",host:"TESTHOST",search:""};
var localStorage={getItem:function(){return JSON.stringify({activeId:"%s"});},setItem:function(){}};
var sockets=[];function WebSocket(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this);}
WebSocket.prototype.send=function(s){this.sent.push(s);};WebSocket.prototype.close=function(){this.readyState=3;};
function MessageChannel(){this.port1={onmessage:null};this.port2={postMessage:function(d){}};}
function sock(){return sockets[sockets.length-1];}
function open(){var s=sock();s.readyState=1;s.onopen();return s;}
function redial(){var live=timers.filter(function(t){return t.live&&t.fn.name==="connect";});live[live.length-1].fn();}
function drop(){sock().readyState=3;sock().onclose();redial();}
function ready(){window.__rompLocalSend({type:"ready"});}
function caps(){sock().onmessage({data:JSON.stringify({type:"caps",caps:[],viewsSeq:null})});}   // the kernel's answer to a ready it processed (_send_caps), in the shape it sends: the shim's readyAcked latch
""" % S1

# the five shapes, as the scenario that drives the first socket to its drop and leaves the redial dialed
DECLARED = "open();ready();caps();drop();"                            # the bundle said ready on the socket that died, and the kernel's caps frame answered it
GATED = "open();drop();"                                              # the socket died before the bundle said ready
CLOSING = "open();sock().readyState=2;ready();sock().readyState=3;sock().onclose();redial();"   # the ready landed while the socket was closing
READY_AFTER_CLOSE = "open();sock().readyState=3;sock().onclose();ready();redial();"             # the ready landed after the close, before the redial
UNACKED = "open();ready();drop();"                                    # the ready left on the socket and no caps frame came back before the drop

# the wsclose row's data keys, all ten: an eleventh under any spelling would keep the row literal's source-text pin
# (tests/test_kernel_disconnect_banner.py) green, so every test that holds the executed row asserts the whole list
ROW_KEYS = ["app", "bundleReady", "code", "everConnected", "quietMs", "readyAcked", "readyQueued", "reason",
            "sinceOpenMs", "wasClean"]


def _bits(data):
    """The row's dial-term inputs at the close, in the order the docs read them."""
    return (data["bundleReady"], data["readyAcked"], data["readyQueued"])


def _shim_close(scenario, app="chat"):
    """Run the REAL shim core under node, built for `app`, through `scenario`, then open the redial socket (its
    onopen flushes the queue). Returns the redial's URL query minus the page identity, the frames the redial socket
    carried in order (each clientDiag row as its `what`, every other frame as its type), and the `wsclose` rows it
    carried."""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    with tempfile.TemporaryDirectory() as fx:
        path = os.path.join(fx, "run.js")
        with open(path, "w") as f:
            f.write(_SHIM_HARNESS + km._shim_core_js(app=app) + "\n" + scenario
                    + "\nvar url=sock().url;open();var fr=sock().sent.map(function(x){return JSON.parse(x);});"
                    + "process.stdout.write(JSON.stringify({url:url,"
                    + "kinds:fr.map(function(m){return m.type==='clientDiag'?m.what:m.type;}),"
                    + "closes:fr.filter(function(m){return m.type==='clientDiag'&&m.what==='wsclose';})}));")
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError("node failed:\n" + r.stderr)
    out = json.loads(r.stdout)
    pairs = urllib.parse.parse_qsl(out["url"].split("?", 1)[1], keep_blank_values=True)
    query = "&".join("%s=%s" % (k, v) for k, v in pairs if k not in ("app", "delta", "iid"))
    return query, out["kinds"], out["closes"]


def _fake_self(path):
    """A connect handler with a peer that closes at once (the tests/test_chat_skeleton_reconnect.py shape)."""
    class FakeSelf:
        headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        rfile = io.BytesIO(); wfile = io.BytesIO()
        connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
        close_connection = False
        def send_response(self, *a): pass
        def send_header(self, *a): pass
        def end_headers(self): pass
    FakeSelf.path = path
    return FakeSelf()


class _State(unittest.TestCase):
    """A private state root per test (km.jd.STATE is shared by every module that loads the judge)."""
    def setUp(self):
        self._saved_state = km.jd.STATE
        self._td = tempfile.TemporaryDirectory()
        km.jd._rebind_state(pathlib.Path(self._td.name))
        self.fp = km.jd.STATE / "client-diag.jsonl"

    def tearDown(self):
        km.jd._rebind_state(self._saved_state)
        self._td.cleanup()

    def rows(self, what=None):
        rows = [json.loads(line) for line in self.fp.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows if what is None else [r for r in rows if r["what"] == what]

    def post(self, client, what="wsclose", data=None):
        """One breadcrumb through the real dispatch, on `client`."""
        km.Handler._dispatch_ws(None, {"type": "clientDiag", "surface": "pane-shim", "what": what,
                                       "data": data if data is not None else {"app": "chat", "code": 1006}}, client)


class TheHandlerStampsTheCarryingSocket(_State):
    def test_a_row_on_a_declared_redial_is_stamped_true(self):
        self.post({"wid": WID, "reconnect": True, "redial": True})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["reconnect"], True)
        self.assertEqual(sorted(rows[0]), ["data", "reconnect", "surface", "t", "what", "wid"])

    def test_a_row_on_a_fresh_socket_is_stamped_false(self):
        self.post({"wid": WID})                             # a first socket, or a redial that carried no term
        self.post({"wid": WID, "reconnect": False})
        self.assertEqual([r["reconnect"] for r in self.rows()], [False, False])

    def test_the_stamp_is_the_dial_record_not_the_consumable_flag(self):
        # `redial` is set at accept beside `reconnect` and never consumed; the strip's _resolve_reconnect pops
        # `reconnect` on a chat socket, nothing pops it on the other panes, and the skeleton arm sets it for a column
        # that declared no redial, so only the record reads alike on every row of every pane
        self.post({"wid": WID, "redial": True})             # a declared redial after its strip consumed the flag
        self.post({"wid": WID, "reconnect": True})          # the consumable flag alone is not a dial record
        self.assertEqual([r["reconnect"] for r in self.rows()], [True, False])

    def test_the_stamp_is_a_bool_whatever_the_record_holds(self):
        self.post({"wid": WID, "redial": 1})
        self.post({"wid": WID, "redial": None})
        rows = self.rows()
        self.assertIs(rows[0]["reconnect"], True)          # assertIs: 1 == True would pass a stamp that copied the record
        self.assertIs(rows[1]["reconnect"], False)


class TheShimRowCarriesItsReadyState(unittest.TestCase):
    def test_declared_the_bundle_had_said_ready_and_the_kernel_had_answered(self):
        q, kinds, closes = _shim_close(DECLARED)
        self.assertTrue(q.endswith("&reconnect=1&proto=1"), q)   # the redial names the acked ready's wire protocol too
        self.assertEqual(len(closes), 1)
        self.assertEqual(sorted(closes[0]["data"]), ROW_KEYS)
        self.assertEqual(_bits(closes[0]["data"]), (True, True, False))
        self.assertIs(closes[0]["data"]["everConnected"], True)
        self.assertEqual(kinds, ["wsclose"], "the queued row flushes; an acked ready is not re-sent (the kernel remembers it)")

    def test_gated_off_the_bundle_had_not_said_ready(self):
        q, kinds, closes = _shim_close(GATED)
        self.assertNotIn("reconnect", q, q)
        self.assertEqual(len(closes), 1)
        self.assertEqual(sorted(closes[0]["data"]), ROW_KEYS)
        self.assertEqual(_bits(closes[0]["data"]), (False, False, False))
        self.assertIs(closes[0]["data"]["everConnected"], True, "everConnected alone could not tell this shape from the declared one")
        self.assertEqual(kinds, ["wsclose"], "no ready to re-send: the bundle has not sent its own")

    def test_a_ready_during_the_close_is_queued_with_no_term(self):
        q, kinds, closes = _shim_close(CLOSING)
        self.assertNotIn("reconnect", q, "the ready is still queued for this open, so the redial carries no term")
        self.assertEqual(len(closes), 1)
        self.assertEqual(sorted(closes[0]["data"]), ROW_KEYS)
        self.assertEqual(_bits(closes[0]["data"]), (True, False, True))
        self.assertEqual(kinds, ["ready", "wsclose"], "the bundle's own ready queued first (during the close), once, then the row")

    def test_a_ready_after_the_close_is_the_gated_shapes_corollary(self):
        # the row was built at the close, before the ready, so it reads like the gated shape; the shim half (the
        # redial dials as a fresh page, the queued ready goes out once, behind the row) is the frame-order check here
        q, kinds, closes = _shim_close(READY_AFTER_CLOSE)
        self.assertNotIn("reconnect", q, q)
        self.assertEqual(len(closes), 1)
        self.assertEqual(sorted(closes[0]["data"]), ROW_KEYS)
        self.assertEqual(_bits(closes[0]["data"]), (False, False, False), "the shim's state at the close, not at the flush")
        self.assertIs(closes[0]["data"]["everConnected"], True)
        self.assertEqual(kinds, ["wsclose", "ready"], "the row queued at the close, the ready behind it, both once")

    def test_an_unacked_ready_left_on_the_socket_and_is_re_posted_after_the_row(self):
        # the ready went out on the socket that died and no caps frame came back: bundleReady true, readyQueued
        # false (it left) and readyAcked false, so the redial dials fresh and its onopen posts the ready again behind
        # the flushed row; without readyAcked and readyQueued on the row this shape and the one above read alike
        q, kinds, closes = _shim_close(UNACKED)
        self.assertNotIn("reconnect", q, "no caps frame answered the ready, so the redial carries no term")
        self.assertEqual(len(closes), 1)
        self.assertEqual(sorted(closes[0]["data"]), ROW_KEYS)
        self.assertEqual(_bits(closes[0]["data"]), (True, False, False))
        self.assertEqual(kinds, ["wsclose", "ready"], "the flushed row, then onopen's re-post of the unanswered ready")


class ThePairInTheLog(_State):
    """The two halves joined: the real handshake dialed with the URL the shim built, the shim's own wsclose row
    dispatched on the client it registered, and the stamp and the bits read back from client-diag.jsonl."""
    def setUp(self):
        super().setUp()
        self._clients = list(km._clients)
        del km._clients[:]

    def tearDown(self):
        del km._clients[:]
        km._clients.extend(self._clients)
        super().tearDown()

    def _dial(self, query, app="chat"):
        """The real handshake for one `app` socket whose peer closes at once; returns the client dict it registered."""
        got = []
        real_reg, real_recv = km._register_ws_client, km._ws_recv
        km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
        km._ws_recv = lambda rfile: (0x8, b"", True)              # the peer closes at once
        km._pusher_wake.clear()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._ws(_fake_self("/ws?app=%s&delta=1&iid=page-c&wid=%s&" % (app, WID) + query))
        finally:
            km._register_ws_client, km._ws_recv = real_reg, real_recv
            for c in got:
                if c in km._clients:
                    km._clients.remove(c)
        self.assertEqual(len(got), 1, query)
        return got[0]

    def _triple(self, scenario):
        """The stamp and the row's bits for one shape: (reconnect, (bundleReady, readyAcked, readyQueued))."""
        q, kinds, closes = _shim_close(scenario)
        c = self._dial(q)
        self.assertIn("wsclose", kinds, "the row rides the redial")
        km.Handler._dispatch_ws(None, closes[0], c)
        opens = self.rows("wsopen")                          # the accept files its own row per socket (_note_ws_open)
        self.assertEqual(len(opens), 1)
        rows = self.rows("wsclose")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wid"], WID)
        self.assertEqual(sorted(rows[0]["data"]), ROW_KEYS)
        self.assertIs(opens[0]["data"]["reconnect"], rows[0]["reconnect"],
                      "the stamp is the value the accept filed on this socket's wsopen row: the two agree by construction")
        return rows[0]["reconnect"], _bits(rows[0]["data"])

    def test_declared(self):
        self.assertEqual(self._triple(DECLARED), (True, (True, True, False)))

    def test_gated_off(self):
        self.assertEqual(self._triple(GATED), (False, (False, False, False)))

    def test_ready_during_the_close(self):
        self.assertEqual(self._triple(CLOSING), (False, (True, False, True)))

    def test_a_ready_after_the_close_reads_as_the_gated_shape(self):
        self.assertEqual(self._triple(READY_AFTER_CLOSE), (False, (False, False, False)))

    def test_unacked(self):
        self.assertEqual(self._triple(UNACKED), (False, (True, False, False)))

    def test_the_stamp_is_the_dial_record_unchanged_by_the_strip(self):
        # on a declared chat redial the first strip sender's _resolve_reconnect consumes `reconnect` and fixes the
        # skeleton set; `redial`, set beside it at accept, is never consumed, and the stamp reads that, so a row
        # dispatched after the real consumption reads True like the wsclose row before it
        q, kinds, closes = _shim_close(DECLARED)
        c = self._dial(q)
        self.assertIs(c.get("reconnect"), True)
        self.assertIs(c.get("redial"), True)
        km.Handler._dispatch_ws(None, closes[0], c)
        km._resolve_reconnect(c, [])                         # the first strip sender's consumption, for real
        self.assertIsNone(c.get("reconnect"), "the strip consumed the flag")
        self.assertIs(c.get("redial"), True, "and left the dial record")
        self.assertEqual(c.get("skeleton"), set(), "the whole path ran: the set exists, empty with no sessions")
        self.assertEqual(c.get("skeletonOrder"), [])
        self.post(c, what="stale-raise", data={"app": "chat", "why": "reconnect"})
        self.assertEqual([(r["what"], r["reconnect"]) for r in self.rows() if r["what"] != "wsopen"],
                         [("wsclose", True), ("stale-raise", True)])

    def test_a_skeleton_column_that_declared_no_redial_is_stamped_false(self):
        # a later chat column dials ?skeleton=1 with no redial term, and the accept arms the consumable `reconnect`
        # for it (the redial's diet fits a fresh column), so a stamp of that flag would read True for a socket that
        # declared nothing; the dial record is left unset and the stamp reads False
        c = self._dial("active=%s&skeleton=1" % S1)
        self.assertIs(c.get("reconnect"), True, "the skeleton arm armed the consumable flag")
        self.assertIsNone(c.get("redial"), "and left no dial record: the column declared no redial")
        self.post(c, data={"app": "chat", "code": 1006})
        self.assertEqual([(r["what"], r["reconnect"]) for r in self.rows() if r["what"] != "wsopen"], [("wsclose", False)])
        self.assertIs(self.rows("wsopen")[0]["data"]["reconnect"], False, "the accept's own row agrees")

    def test_a_feed_socket_reads_its_dial_record_too(self):
        # the by-app symmetry: no strip ever runs on a feed socket, so nothing consumes its `reconnect`; the stamp
        # reads the same record there as on chat, declared True and fresh False, so the field means one thing
        # on every pane
        q, kinds, closes = _shim_close(DECLARED, app="feed")
        c = self._dial(q, app="feed")
        self.assertIs(c.get("redial"), True)
        km.Handler._dispatch_ws(None, closes[0], c)
        self.post(c, what="stale-raise", data={"app": "feed", "why": "reconnect"})
        q2, kinds2, closes2 = _shim_close(GATED, app="feed")
        c2 = self._dial(q2, app="feed")
        self.assertIsNone(c2.get("redial"), "a fresh dial leaves no record")
        km.Handler._dispatch_ws(None, closes2[0], c2)
        self.assertEqual([(r["what"], r["reconnect"]) for r in self.rows() if r["what"] != "wsopen"],
                         [("wsclose", True), ("stale-raise", True), ("wsclose", False)])


if __name__ == "__main__":
    raise SystemExit("run under pytest: a raw run skips conftest and can reach the live kernel")
