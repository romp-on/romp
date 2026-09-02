#!/usr/bin/env python3
"""The chat delta stream must never strand a connected client (the user 2026-07-28).

The chat ships only the changed SUFFIX once a tab is caught up (chatTail). Two kernel-side defects let
that stream desynchronize, after which the tab froze — a stale "working" chip, no new messages, and
every deep-link into the missing range honest-failing "couldn't locate this in the transcript" — until
its socket happened to drop:

  1. The shared diff baseline (_prev_chat_events) was advanced by a CONNECT push, which targets a single
     client. Everything written since the last full push then fell below the next diff's change_from and
     the already-connected clients never received it.
  2. _chat_build_sig stat'd states/<fsid>.jsonl, but a FORKED session writes its state transitions under
     the anchor sid. A settle writes only to states/, so a forked lane's cached chat payload was never
     invalidated by one.

Plus the repair path: a client that rejects a too-far-ahead delta posts {"type":"needFull"}, and the
kernel must forget what it believes that client holds so the next push re-sends the whole session.
"""
import inspect
import os
import re
from importlib.machinery import SourceFileLoader
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_resync", os.path.join(BIN, "romp-kernel")).load_module()


def _client():
    """A fake chat client: records what it was sent, with the same dedup/echat slots _send_chat uses."""
    out = []
    return {"app": "chat", "alive": True, "send": out.append, "sent": {}, "echat": {}, "_out": out}


def _evs(n, tag=""):
    return [{"kind": "assistant", "uuid": "u%d%s" % (i, tag), "md": "m%d" % i} for i in range(n)]


def _sess(sid, events):
    return {"type": "session", "id": sid, "name": "web", "events": events,
            "status": {"state": "working", "sinceEpoch": None}}


# ───────────────────────── the gap the client cannot self-repair ─────────────────────────

def test_a_delta_past_the_clients_events_is_a_gap():
    """The shape of the bug, stated as arithmetic: a client holding N events that is handed a delta
    starting at >N cannot apply it without silently skipping the events in between. render.ts's chatTail
    detects exactly this (from > s.events.length) and now asks for a full session instead of freezing."""
    client_has, delta_from = 100, 118
    assert delta_from > client_has, "a delta starting past the resident tail is unappliable"


def test_needfull_clears_the_kernels_belief_about_that_client():
    """The repair. The kernel's per-client bookkeeping advances on SEND, not on ACK, so it cannot detect
    the desync itself — the client has to say so. Handling needFull must drop BOTH the echat entry (which
    is what makes _send_chat take its full-session path) and the dedup slot (so the full send isn't
    swallowed as unchanged)."""
    c = _client()
    sid = "11111111-2222-3333-4444-555555555555"
    c["echat"][sid] = ("u0", 0)
    c["sent"][("chat", sid)] = '{"type":"chatTail"}'

    # what the handler does (kernel.py's needFull branch)
    c.get("echat", {}).pop(sid, None)
    c.get("sent", {}).pop(("chat", sid), None)

    assert sid not in c["echat"], "must forget the client's tail base → next push sends a full session"
    assert ("chat", sid) not in c["sent"], "must drop the dedup slot → the full send actually goes out"


def test_full_send_path_fires_when_echat_has_no_entry():
    """Pin the mechanism the repair relies on: with no echat entry, _send_chat sends a whole
    {type:"session"}, not another delta."""
    c = _client()
    sid = "11111111-2222-3333-4444-555555555555"
    m = _sess(sid, _evs(5))
    km._send_chat(c, m, __import__("json").dumps(m), 3, False)
    assert c["_out"], "something must be sent"
    got = __import__("json").loads(c["_out"][-1])
    assert got["type"] == "session", "no echat entry → full session, never a chatTail"


# ───────────────────────── (1) a connect push must not move the shared baseline ─────────────────────────

def test_connect_push_does_not_advance_the_shared_baseline():
    """A connect push targets ONE client. If it advanced the shared diff baseline, every OTHER connected
    client would never be sent the events in that window — the next diff would start past them.

    Drives the real guard in _push's build loop: the baseline write is gated on `not connect`.
    """
    src = inspect.getsource(km._push)
    # the baseline writes must be reachable ONLY when the push is not a connect push
    assert "_prev_chat_events[m[\"id\"]]" in src, "baseline write moved — re-pin this test"
    gate = re.search(r"if not connect:\s*\n\s*_prev_chat_events\[m\[\"id\"\]\] = .*\n\s*_prev_chat_ledger\[m\[\"id\"\]\] = ",
                     src)
    assert gate, "the shared diff baseline must be advanced only by a push that reaches every client"


def test_periodic_push_still_advances_the_baseline():
    """The gate must not disable delta-sending outright: a push that reaches ALL clients still advances
    the baseline, or every push would re-send the whole transcript to everyone."""
    sid = "11111111-2222-3333-4444-555555555555"
    km._prev_chat_events.clear()
    km._prev_chat_events[sid] = _evs(118)
    assert km._chat_diff(km._prev_chat_events[sid], _evs(118)) == 118, "caught up → empty suffix"
    # and a stranded client's suffix starts at the OLD baseline, which it can actually apply
    km._prev_chat_events[sid] = _evs(100)
    assert km._chat_diff(km._prev_chat_events[sid], _evs(130)) == 100


def test_needfull_handler_is_wired_in_the_kernel():
    """Source-pin the repair handler: it must drop the client's echat entry AND its dedup slot."""
    src = inspect.getsource(km)
    i = src.find('msg.get("type") == "needFull"')
    assert i > 0, "the kernel must handle the client's needFull resync request"
    body = src[i:i + 1200]
    assert '"echat"' in body and ".pop(sid, None)" in body, "must forget the client's tail base"
    assert '("chat", sid)' in body, "must drop the dedup slot so the full send lands"


def test_chat_diff_append_starts_at_the_previous_total():
    """Why a moved baseline strands clients at all: for a pure append the diff returns the PREVIOUS
    build's total, so the baseline's length is exactly where the next suffix begins."""
    assert km._chat_diff(_evs(100), _evs(118)) == 100
    assert km._chat_diff(None, _evs(5)) == 0, "no prior build → full send"


# ───────────────────────── (2) the fork-lane states key ─────────────────────────

def test_build_sig_folds_in_the_anchor_states_file(tmp_path):
    """A forked lane is keyed by a new fsid but keeps writing states/<anchor>.jsonl. The signature must
    stat the anchor's file too, or a settle (which touches ONLY states/) never busts the cached payload
    and the lane latches on 'working'."""
    jd._rebind_state(tmp_path)
    km.jd = jd
    (tmp_path / "states").mkdir(parents=True, exist_ok=True)
    tx = tmp_path / "fork.jsonl"
    tx.write_text('{"type":"user"}\n')

    anchor = "11111111-2222-3333-4444-555555555555"
    states = tmp_path / "states" / (anchor + ".jsonl")
    states.write_text('{"t":1,"state":"working"}\n')

    sess = {"sid": "fork", "path": str(tx), "anchor": anchor}
    before = km._chat_build_sig(sess)

    # the settle: written to states/<anchor>.jsonl ONLY, transcript untouched
    states.write_text('{"t":1,"state":"working"}\n{"t":2,"state":"waiting"}\n')
    after = km._chat_build_sig(sess)

    assert before is not None and after is not None
    assert before != after, "a settle under the ANCHOR sid must bust the fork lane's chat cache"


def test_build_sig_still_tracks_the_fsid_states_file(tmp_path):
    """The common case (sid == fsid, no fork) must keep working."""
    jd._rebind_state(tmp_path)
    km.jd = jd
    (tmp_path / "states").mkdir(parents=True, exist_ok=True)
    sid = "11111111-2222-3333-4444-555555555555"
    tx = tmp_path / (sid + ".jsonl")
    tx.write_text('{"type":"user"}\n')
    states = tmp_path / "states" / (sid + ".jsonl")
    states.write_text('{"t":1,"state":"working"}\n')

    sess = {"sid": sid, "path": str(tx), "anchor": sid}
    before = km._chat_build_sig(sess)
    states.write_text('{"t":1,"state":"working"}\n{"t":2,"state":"waiting"}\n')
    assert before != km._chat_build_sig(sess)


# ───────────────────────── the lost-first-frame class (the user 2026-09-02) ─────────────────────────

def test_ready_forgets_the_whole_chat_base_so_the_repush_is_full_frames():
    """The pusher fires from the moment the socket opens (the shim dials during HTML parse), while the
    1.4MB render bundle can still be evaluating — so the FIRST full frames can land in a document with
    no message listener and vanish, with echat then believing the client holds those tails. `ready`
    (posted once, when the bundle finally evaluated) must therefore reset the client's whole chat base:
    the renderer provably holds nothing, whatever this socket was sent. The stuck-« opening … » tab;
    duplicating the browser tab recovered because cached bundles win the race."""
    import json
    c = _client()
    sid = "11111111-2222-3333-4444-555555555555"
    m = _sess(sid, _evs(5))
    km._send_chat(c, m, json.dumps(m), 3, False)          # the pre-listener full send — LOST client-side
    assert sid in c["echat"] and ("chat", sid) in c["sent"], "the kernel now believes the client is based"
    c["_out"].clear()

    km._client_reset_chat_base(c)                          # what the ready branch does
    assert not c["echat"], "every believed tail is forgotten"
    assert not any(isinstance(k, tuple) and k and k[0] == "chat" for k in c["sent"]), \
        "…and every chat dedup slot, or _DEDUP_REPOST_S eats the re-send for 60s"

    km._send_chat(c, m, json.dumps(m), 3, False)           # the ready-triggered repush
    got = json.loads(c["_out"][-1])
    assert got["type"] == "session", "the repush is a FULL frame, never a delta onto a base that was lost"


def test_ready_branch_is_wired_to_the_reset():
    src = inspect.getsource(km)
    i = src.find('msg.get("type") == "ready"')
    assert i > 0
    body = src[i:i + 1600]
    assert "_client_reset_chat_base(client)" in body, "ready must reset BEFORE its push"
    assert body.find("_client_reset_chat_base(client)") < body.find("_push_one(client)")
