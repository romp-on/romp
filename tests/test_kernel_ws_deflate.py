#!/usr/bin/env python3
"""WebSocket per-message compression (RFC 7692 permessage-deflate) on the kernel's hand-rolled server.

The panes' view frames are JSON and deflate about seven to one (the whole feed frame of a 374-card board:
2.53 MB plain, 0.36 MB deflated), and until this every frame crossed plain: the upgrade ignored the
Sec-WebSocket-Extensions offer every browser and Node's `ws` make on every dial, so a tunnelled dashboard
fell megabytes behind on the feed and was dropped. Pinned here:

- negotiation: an offer is answered with the extension (no context takeover on either side; a server window
  the offer named echoed, at 15 too), an absent, foreign or malformed offer is not — a malformed window
  value declines rather than 500s — repeated header lines are read together, and the kill switch declines
  every offer; the environment reads and their defaults (on, level 3, the 1,024-byte floor);
- the wire: a message at or over the floor goes compressed with RSV1 set and inflates back to its text, the
  floor exactly where it is documented; a short one, and every message to a client without the extension,
  gets the plain frame it always did, byte for byte; the client's sender thread is what compresses;
- the reader: a client's compressed message (RSV1 on its first frame, fragments included, a 15-bit window
  of varied content) is inflated before it is parsed, and so is one ending in a final block, RFC 7692's own
  example included; bytes that are not a deflate stream, bytes after the stream's end other than that
  example's one 0x00 octet (a second stream, or one other octet), an inflate past the reassembly cap, RSV1
  from a client that negotiated nothing, and RSV1 on a continuation or control frame end the read as a dead
  connection, each naming a Close code; the cap bounds what a message costs (a deflate bomb is refused within
  one piece past it), not only what it returns; a plain message is untouched;
- end to end through the real Handler on a loopback server: both directions on one negotiated socket, a socket
  that offered nothing stays plain, and a kernel-decided end sends a Close frame with its code and logs once;
- the federated relay, executed: a browser dialing a remote kernel through the hub negotiates with the remote.

Synthetic only: invented frame text, no session data.
"""
import base64
import contextlib
import io
import json
import os
import random
import socket
import struct
import tempfile
import threading
import time
import tracemalloc
import unittest
import zlib
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ.pop("ROMP_WS_DEFLATE", None)          # the defaults are under test: load with neither knob set
os.environ.pop("ROMP_WS_DEFLATE_LEVEL", None)
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_wsdeflate", os.path.join(BIN, "romp-kernel"))

TAIL = b"\x00\x00\xff\xff"
MASK = b"\x11\x22\x33\x44"
ACCEPT = "permessage-deflate; server_no_context_takeover; client_no_context_takeover"


def deflate(data, level=6, wbits=15):
    """A client's compressed payload, as a browser makes it: one raw deflate stream, sync-flushed, tail stripped."""
    z = zlib.compressobj(level, zlib.DEFLATED, -wbits)
    out = z.compress(data) + z.flush(zlib.Z_SYNC_FLUSH)
    assert out.endswith(TAIL)
    return out[:-4]


def inflate(data):
    """What a browser does with an RSV1 frame's payload — independent of the kernel's own inflater."""
    return zlib.decompressobj(-15).decompress(data + TAIL)


def cframe(payload, opcode=0x1, fin=True, rsv1=False, mask=MASK):
    """One client (masked) frame, wire-encoded."""
    b0 = (0x80 if fin else 0x00) | (0x40 if rsv1 else 0x00) | opcode
    ln = len(payload)
    if ln < 126:
        hdr = bytes([b0, 0x80 | ln])
    elif ln < 65536:
        hdr = bytes([b0, 0x80 | 126]) + struct.pack(">H", ln)
    else:
        hdr = bytes([b0, 0x80 | 127]) + struct.pack(">Q", ln)
    return hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))


def cfragments(payload, chunk, rsv1=False):
    """A message split into continuation frames of `chunk` bytes; RSV1, when set, on the FIRST frame only (RFC 7692 §6.1)."""
    parts = [payload[i:i + chunk] for i in range(0, len(payload), chunk)] or [b""]
    out = b""
    for i, p in enumerate(parts):
        out += cframe(p, 0x1 if i == 0 else 0x0, fin=(i == len(parts) - 1), rsv1=(rsv1 and i == 0))
    return out


def recv_message(wire, inflate_=False):
    """(opcode, payload), and the (code, reason) the reader gave on_fail, if any."""
    fails = []
    got = km._ws_recv_message(io.BytesIO(wire), lambda p: None, inflate=inflate_, on_fail=lambda c, w: fails.append((c, w)))
    return got, fails


def parse_sframe(b):
    """One server frame from bytes → (byte0, payload, bytes consumed)."""
    b0, ln, at = b[0], b[1] & 0x7F, 2
    if ln == 126:
        ln, at = struct.unpack(">H", b[2:4])[0], 4
    elif ln == 127:
        ln, at = struct.unpack(">Q", b[2:10])[0], 10
    return b0, b[at:at + ln], at + ln


def big_text(n=300):
    return json.dumps({"type": "feed", "asks": [{"itemId": "item-%d" % i, "title": "invented card %d" % i,
                                                 "status": "completed", "why": "synthetic fixture text"} for i in range(n)]})


def varied_json(n=2000, seed=7):
    """A frame whose content does not repeat within a small window: hex noise per item, so a 12- to 14-bit inflate
    window fails on it where a byte-run fixture would pass. Deterministic (seeded), synthetic."""
    rnd = random.Random(seed)
    return json.dumps({"type": "feed", "asks": [{"itemId": "%016x" % rnd.getrandbits(64), "title": "%032x" % rnd.getrandbits(128),
                                                 "t": rnd.random()} for _ in range(n)]}).encode()


class Env(unittest.TestCase):
    def test_defaults_with_neither_knob_set(self):
        self.assertEqual(km._ws_deflate_env({}), (True, 3))
        self.assertTrue(km._WS_DEFLATE_ON); self.assertEqual(km._WS_DEFLATE_LEVEL, 3)   # this module loaded with both unset
        self.assertEqual(km._WS_DEFLATE_MIN, 1024)

    def test_the_knobs(self):
        self.assertEqual(km._ws_deflate_env({"ROMP_WS_DEFLATE": "0", "ROMP_WS_DEFLATE_LEVEL": "9"}), (False, 9))
        self.assertEqual(km._ws_deflate_env({"ROMP_WS_DEFLATE": "1"}), (True, 3))
        self.assertEqual(km._ws_deflate_env({"ROMP_WS_DEFLATE_LEVEL": "0"}), (True, 1), "clamped to zlib's range")
        self.assertEqual(km._ws_deflate_env({"ROMP_WS_DEFLATE_LEVEL": "12"}), (True, 9))
        self.assertEqual(km._ws_deflate_env({"ROMP_WS_DEFLATE_LEVEL": "abc"}), (True, 3), "a malformed level falls to the default, not a boot failure")


class Offer(unittest.TestCase):
    def test_offers_the_kernel_takes_and_the_window_it_keeps_to(self):
        for hdr, (wbits, bounded) in {
            "permessage-deflate": (15, False),
            "PerMessage-Deflate": (15, False),                                    # the token is case-insensitive
            "permessage-deflate; client_max_window_bits": (15, False),           # Chrome, Firefox, Node's ws
            "permessage-deflate; client_max_window_bits=15": (15, False),
            "permessage-deflate; server_no_context_takeover; client_no_context_takeover": (15, False),
            "permessage-deflate; server_max_window_bits=10": (10, True),         # a bounded server window is kept to
            'permessage-deflate; server_max_window_bits="12"': (12, True),       # quoted-string values are allowed
            "permessage-deflate; server_max_window_bits=15": (15, True),         # named at the default: still named
            "x-webkit-deflate-frame, permessage-deflate; client_max_window_bits": (15, False),   # a foreign extension first
            "permessage-deflate; bogus, permessage-deflate": (15, False),        # the first offer declined, the second taken
        }.items():
            self.assertEqual(km._ws_deflate_offer(hdr), {"wbits": wbits, "bounded": bounded}, hdr)

    def test_offers_a_server_must_decline(self):
        for hdr in (None, "", "x-webkit-deflate-frame",
                    "permessage-deflate; bogus",                               # an unknown parameter
                    "permessage-deflate; server_max_window_bits",              # the value is required in an offer
                    "permessage-deflate; server_max_window_bits=8",            # zlib cannot build a window that small
                    "permessage-deflate; server_max_window_bits=16",
                    "permessage-deflate; client_max_window_bits=7",
                    "permessage-deflate; server_no_context_takeover=1",        # a flag with a value
                    "permessage-deflate; client_max_window_bits; client_max_window_bits"):   # repeated
            self.assertIsNone(km._ws_deflate_offer(hdr), repr(hdr))

    def test_a_malformed_window_value_declines_instead_of_raising(self):
        # str.isdigit admits these and int() then refuses them: the Latin-1 superscripts a header decodes to, a digit run
        # past int()'s 4,300-digit limit, a leading zero, an exponent — each used to raise out of the upgrade as a 500
        for val in ("¹³", "²³", "0" * 5000, "09", "1e1", "+15", "15.0", "-12"):   # (whitespace around a value is tolerated, on purpose)
            for name in ("server_max_window_bits", "client_max_window_bits"):
                self.assertIsNone(km._ws_deflate_offer("permessage-deflate; %s=%s" % (name, val)), "%s=%r" % (name, val))

    def test_the_response_states_no_context_takeover_for_both_sides(self):
        self.assertEqual(km._ws_deflate_response({"wbits": 15, "bounded": False}), ACCEPT)
        self.assertEqual(km._ws_deflate_response({"wbits": 10, "bounded": True}), ACCEPT + "; server_max_window_bits=10")

    def test_a_server_window_the_offer_named_is_echoed_even_at_the_default(self):
        # RFC 7692 §7.1.2.1: an offer carrying server_max_window_bits is accepted only WITH the parameter in the
        # response; a client that asked for 15 (Python's websockets does when told to) refuses a response without it
        # (review find, 2026-09-23: it could not connect at all, where declining the offer had let it connect plain)
        terms = km._ws_deflate_offer("permessage-deflate; server_max_window_bits=15")
        self.assertEqual(km._ws_deflate_response(terms), ACCEPT + "; server_max_window_bits=15")


class Wire(unittest.TestCase):
    class Sock:
        def __init__(self):
            self.out = b""

        def sendall(self, b):
            self.out += b

    def _sent(self, text, deflate_):
        sock = self.Sock()
        km._ws_send(sock, threading.Lock(), text, deflate_)
        return parse_sframe(sock.out)

    def test_a_negotiated_clients_large_message_goes_compressed_with_rsv1(self):
        text = big_text()
        b0, payload, used = self._sent(text, {"wbits": 15, "bounded": False})
        self.assertEqual(b0, 0xC1, "FIN + RSV1 + text")
        self.assertEqual(inflate(payload).decode("utf-8"), text)
        self.assertLess(len(payload), len(text.encode()) // 4, "the JSON frame shrinks several-fold")

    def test_the_floor_is_exactly_the_documented_kilobyte(self):
        text = "ab" * 512                                                       # compressible, so only the floor decides
        self.assertEqual(len(text.encode()), 1024)
        self.assertEqual(self._sent(text[:1023], {"wbits": 15})[0], 0x81, "1,023 bytes: plain")
        self.assertEqual(self._sent(text, {"wbits": 15})[0], 0xC1, "1,024 bytes: compressed")

    def test_a_bounded_server_window_is_kept_to(self):
        text = big_text()
        b0, payload, _ = self._sent(text, {"wbits": 9, "bounded": True})
        self.assertEqual(b0, 0xC1)
        self.assertEqual(zlib.decompressobj(-9).decompress(payload + TAIL).decode("utf-8"), text,
                         "a client that asked for a 9-bit window can inflate with one")

    def test_a_short_message_goes_plain_even_when_negotiated(self):
        text = json.dumps({"type": "ka", "dv": "abc"})
        b0, payload, _ = self._sent(text, {"wbits": 15})
        self.assertEqual((b0, payload), (0x81, text.encode()))

    def test_a_client_without_the_extension_gets_the_frame_it_always_did(self):
        text = big_text()
        sock = self.Sock()
        km._ws_send(sock, threading.Lock(), text)                              # the old call shape, no terms
        data = text.encode()
        self.assertEqual(sock.out, bytes([0x81, 126]) + struct.pack(">H", len(data)) + data if len(data) < 65536
                         else bytes([0x81, 127]) + struct.pack(">Q", len(data)) + data)
        self.assertEqual(self._sent(text, None)[:2], (0x81, data))

    def test_incompressible_bytes_go_plain(self):
        noise = zlib.compress(os.urandom(4096))
        self.assertIsNone(km._ws_deflate(noise, 15), "deflate that does not shrink is not used")

    def test_the_clients_own_sender_thread_compresses(self):
        # the real client record and its sender thread over a socket pair: the terms on the client are what the thread
        # reads, so a client without them stays plain and one with them is compressed — no source text involved
        for terms, want in ((None, 0x81), ({"wbits": 15, "bounded": False}, 0xC1)):
            a, b = socket.socketpair()
            client, q, _lock = km._new_ws_client("feed", "w-sender", a)
            try:
                if terms:
                    client["deflate"] = terms
                text = big_text()
                client["send"](text)
                b0, payload = _Reader(b).frame()
                self.assertEqual(b0, want)
                self.assertEqual((inflate(payload) if want == 0xC1 else payload).decode("utf-8"), text)
            finally:
                q.put(None)
                a.close(); b.close()


class Reader(unittest.TestCase):
    BODY = json.dumps({"type": "invented", "pad": "y" * 3000}).encode()

    def test_a_compressed_message_is_inflated_before_it_is_parsed(self):
        self.assertEqual(recv_message(cframe(deflate(self.BODY), rsv1=True), inflate_=True), ((0x1, self.BODY), []))

    def test_rsv1_rides_the_first_fragment_only(self):
        wire = cfragments(deflate(self.BODY), chunk=7, rsv1=True)
        self.assertEqual(recv_message(wire, inflate_=True), ((0x1, self.BODY), []))

    def test_a_ping_between_compressed_fragments_is_still_answered(self):
        z = deflate(self.BODY)
        wire = cframe(z[:5], 0x1, fin=False, rsv1=True) + cframe(b"hi", 0x9) + cframe(z[5:], 0x0, fin=True)
        pings = []
        self.assertEqual(km._ws_recv_message(io.BytesIO(wire), pings.append, inflate=True), (0x1, self.BODY))
        self.assertEqual(pings, [b"hi"])

    def test_a_plain_message_is_untouched_when_the_extension_is_on(self):
        self.assertEqual(recv_message(cframe(self.BODY), inflate_=True), ((0x1, self.BODY), []))
        self.assertEqual(recv_message(cfragments(self.BODY, chunk=100), inflate_=True), ((0x1, self.BODY), []))

    def test_a_full_15_bit_window_of_varied_content_inflates(self):
        # every other fixture here repeats one byte, which a 12-bit inflate window would pass; this one does not
        body = varied_json()
        self.assertGreater(len(body), 120 * 1024)
        (op, got), fails = recv_message(cframe(deflate(body, wbits=15), rsv1=True), inflate_=True)
        self.assertEqual((op, got, fails), (0x1, body, []))

    def test_a_message_ending_in_a_final_block_inflates_past_one_piece(self):
        # RFC 7692 §7.2.3.4 lets a sender end a message in a BFINAL block, so the tail the reader appends lands after the
        # stream's end. Once an earlier piece has filled the step, CPython keeps those leftover bytes in unconsumed_tail
        # even after the stream ends, and a reader stopping only on an empty unconsumed_tail refused the message as one
        # that does not inflate (review of #2106, 2026-09-23). The sync-flushed shape is the control: it passed. The
        # RFC's own example, a final block and then one 0x00 octet, has its own test below
        body = varied_json(25000)
        self.assertGreater(len(body), 2 * km._WS_INFLATE_STEP)
        z = zlib.compressobj(6, zlib.DEFLATED, -15)
        shapes = {"sync-flushed, tail stripped": deflate(body),
                  "sync-flushed, then an empty final block": deflate(body) + TAIL + b"\x03\x00",
                  "one stream finished with Z_FINISH": z.compress(body) + z.flush(zlib.Z_FINISH)}
        for name, payload in shapes.items():
            (op, got), fails = recv_message(cframe(payload, rsv1=True), inflate_=True)
            # length and content compared apart: a failing equality on megabytes renders its diff for minutes
            self.assertEqual((op, fails), (0x1, []), name)
            self.assertEqual(len(got), len(body), name)
            self.assertTrue(got == body, name)

    def test_a_final_block_message_past_the_cap_is_still_refused(self):
        # the piece that reaches the stream's end is counted against the cap before the loop may stop on it
        saved = km._WS_MAX_MESSAGE
        km._WS_MAX_MESSAGE = 3 * km._WS_INFLATE_STEP // 2
        try:
            body = varied_json(17500)
            self.assertTrue(km._WS_MAX_MESSAGE < len(body) < 2 * km._WS_INFLATE_STEP, len(body))
            z = zlib.compressobj(6, zlib.DEFLATED, -15)
            got, fails = recv_message(cframe(z.compress(body) + z.flush(zlib.Z_FINISH), rsv1=True), inflate_=True)
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1009]))
        finally:
            km._WS_MAX_MESSAGE = saved

    @staticmethod
    def _finished(data):
        """One raw deflate stream ended with Z_FINISH, so its last block carries BFINAL. A compressor takes no input once
        finished, so each stream gets a fresh one."""
        z = zlib.compressobj(6, zlib.DEFLATED, -15)
        return z.compress(data) + z.flush(zlib.Z_FINISH)

    def test_bytes_after_the_deflate_stream_ends_end_the_read(self):
        # past the stream's end the reader expects the tail it appended and, before it, at most the one 0x00 octet RFC
        # 7692 §7.2.3.4's example carries. More is not this stream (a second one, here): stopping at the first stream's
        # end, the reader handed the handler that stream's text alone and dropped the rest unread, with no Close and no
        # log line, whether the first stream ended inside the first piece or past it (review of #2137, 2026-09-24)
        body = varied_json(25000)
        self.assertGreater(len(body), 2 * km._WS_INFLATE_STEP)
        for at in (km._WS_INFLATE_STEP // 2, 3 * km._WS_INFLATE_STEP // 2):   # the first stream ends in one piece, then past it
            payload = self._finished(body[:at]) + self._finished(body[at:])
            (op, got), fails = recv_message(cframe(payload, rsv1=True), inflate_=True)
            # a length, not the bytes: a failing equality on megabytes renders its diff for minutes
            self.assertEqual((op, got if got is None else len(got), [c for c, _ in fails]), (None, None, [1007]), at)
            self.assertIn("after its deflate stream ends", fails[0][1])
        # the bound's other side: two octets where the RFC's example has one, 6 bytes past the stream's end
        got, fails = recv_message(cframe(self._finished(self.BODY) + b"\x00\x00", rsv1=True), inflate_=True)
        self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1007]))

    def test_one_octet_after_the_stream_ends_must_be_the_rfcs_zero(self):
        # the one octet the RFC's example leaves past the stream's end is 0x00, an empty stored block's header, which
        # holds no data. Another single octet can hold some: 0x63 and the tail the reader appends make a whole second
        # stream holding one byte, which a bound on the leftover's length alone would let through, dropping it unread
        # (the bound the review of #2137, 2026-09-24, sketched)
        z = zlib.decompressobj(-15)
        self.assertEqual((z.decompress(b"\x63" + TAIL), z.eof), (b"\x00", True))
        got, fails = recv_message(cframe(self._finished(self.BODY) + b"\x63", rsv1=True), inflate_=True)
        self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1007]))
        self.assertIn("after its deflate stream ends", fails[0][1])

    def test_the_rfcs_own_final_block_example_inflates(self):
        # RFC 7692 §7.2.3.4: "Hello" in a BFINAL block, then one 0x00 octet, the header of the empty stored block whose
        # last 4 octets the sender stripped. That leaves 5 bytes past the stream's end where the other final-block shapes
        # leave the 4-byte tail, so a refusal of bytes after the end sized to the tail alone would reach it. The same
        # shape at 2.5 MB (past one piece) and empty
        rfc = bytes.fromhex("f348cdc9c9070000")
        self.assertEqual(recv_message(cframe(rfc, rsv1=True), inflate_=True), ((0x1, b"Hello"), []))
        self.assertEqual(recv_message(cframe(self._finished(b"") + b"\x00", rsv1=True), inflate_=True), ((0x1, b""), []))
        body = varied_json(25000)
        (op, got), fails = recv_message(cframe(self._finished(body) + b"\x00", rsv1=True), inflate_=True)
        self.assertEqual((op, fails), (0x1, []))
        self.assertEqual(len(got), len(body))
        self.assertTrue(got == body)

    def test_rsv1_from_a_client_that_negotiated_nothing_ends_the_read(self):
        got, fails = recv_message(cframe(deflate(self.BODY), rsv1=True), inflate_=False)
        self.assertEqual(got, (None, None))
        self.assertEqual([c for c, _ in fails], [1002])

    def test_rsv1_on_a_continuation_or_control_frame_ends_the_read(self):
        # RFC 7692 §6.1: the flag belongs to a message's first data frame; a ping or a continuation carrying it is a
        # protocol error, negotiated or not (the base ignored RSV1 everywhere; this narrows a leniency, review 2026-09-23)
        for negotiated in (True, False):
            got, fails = recv_message(cframe(b"hi", 0x9, rsv1=True) + cframe(self.BODY), inflate_=negotiated)
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1002]), "ping, negotiated=%s" % negotiated)
            z = deflate(self.BODY)
            wire = cframe(z[:5], 0x1, fin=False, rsv1=True) + cframe(z[5:], 0x0, fin=True, rsv1=True)
            got, fails = recv_message(wire, inflate_=negotiated)
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1002]), "continuation, negotiated=%s" % negotiated)

    def test_bytes_that_are_not_a_deflate_stream_end_the_read(self):
        got, fails = recv_message(cframe(b"\xff\xfe\xfd not deflate at all", rsv1=True), inflate_=True)
        self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1007]))
        self.assertIn("not a deflate stream", fails[0][1])

    def test_an_inflate_past_the_reassembly_cap_ends_the_read(self):
        saved = km._WS_MAX_MESSAGE
        km._WS_MAX_MESSAGE = 2048
        try:
            got, fails = recv_message(cframe(deflate(b"\0" * 10000), rsv1=True), inflate_=True)
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1009]))
            got, fails = recv_message(cframe(deflate(b"\0" * 2049), rsv1=True), inflate_=True)   # one byte over: the length check alone
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1009]))
            self.assertEqual(recv_message(cframe(deflate(b"\0" * 2048), rsv1=True), inflate_=True), ((0x1, b"\0" * 2048), []),
                             "exactly the cap is allowed")
        finally:
            km._WS_MAX_MESSAGE = saved

    def test_a_fragmented_plain_message_past_the_cap_names_its_code(self):
        saved = km._WS_MAX_MESSAGE
        km._WS_MAX_MESSAGE = 2048
        try:
            got, fails = recv_message(cfragments(b"x" * 5000, chunk=1000))
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1009]))
        finally:
            km._WS_MAX_MESSAGE = saved

    def test_the_cap_bounds_what_a_bomb_costs_not_only_what_it_returns(self):
        # a deflate bomb (64 MiB of zeros in ~64 KB) against a 4 MiB cap: the piecewise inflate refuses it one piece past
        # the cap, so the peak allocation stays a small multiple of the cap (measured: 160 MiB for a 160 MiB bomb against
        # the real 80 MiB cap in one bounded decompress call, 82 MiB piecewise; review, 2026-09-23). With no bound at
        # all the whole 64 MiB would be built before the length check
        saved = km._WS_MAX_MESSAGE
        cap = km._WS_MAX_MESSAGE = 4 * 1024 * 1024
        wire = cframe(deflate(b"\0" * (64 * 1024 * 1024), level=9), rsv1=True)     # built BEFORE the trace: the fixture is not the cost
        try:
            tracemalloc.start()
            try:
                tracemalloc.reset_peak()
                got, fails = recv_message(wire, inflate_=True)
                _cur, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual((got, [c for c, _ in fails]), ((None, None), [1009]))
            self.assertLess(peak, 4 * cap, "peak %.1f MiB for a %.0f MiB cap" % (peak / 2**20, cap / 2**20))
        finally:
            km._WS_MAX_MESSAGE = saved

    def test_the_frame_reader_carries_rsv1_above_the_opcode(self):
        op, payload, fin = km._ws_recv(io.BytesIO(cframe(b"x", 0x1, rsv1=True)))
        self.assertEqual((op, payload, fin), (0x41, b"x", True))
        op, payload, fin = km._ws_recv(io.BytesIO(cframe(b"x", 0x1)))
        self.assertEqual((op, payload, fin), (0x1, b"x", True), "a plain frame's opcode reads as before")


class _Reader:
    """Frames off a real socket, with the bytes that arrived behind the handshake head."""

    def __init__(self, sock, rest=b""):
        self.sock, self.buf = sock, rest

    def rd(self, n):
        while len(self.buf) < n:
            c = self.sock.recv(65536)
            if not c:
                raise EOFError("socket closed")
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def frame(self):
        h = self.rd(2)
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self.rd(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self.rd(8))[0]
        return h[0], self.rd(ln)

    def eof(self, timeout=5.0):
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(1) == b""
        except OSError:
            return False


def upgrade(port, extensions=None, wid="w-deflate", app="feed", path=None):
    """One raw upgrade with the token (an absent Origin passes with it) → (status, headers, socket, bytes after the head).
    `extensions`: one header value, or a list of values sent as SEPARATE header lines."""
    key = base64.b64encode(os.urandom(16)).decode()
    lines = ["GET %s HTTP/1.1" % (path or "/ws?app=%s&wid=%s&token=%s" % (app, wid, km.TOKEN)), "Host: 127.0.0.1:%d" % port,
             "Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Key: %s" % key, "Sec-WebSocket-Version: 13"]
    for ext in ([extensions] if isinstance(extensions, str) else (extensions or [])):
        lines.append("Sec-WebSocket-Extensions: %s" % ext)
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        c = s.recv(4096)
        if not c:
            break
        buf += c
    head, _, rest = buf.partition(b"\r\n\r\n")
    hl = head.decode("latin-1").split("\r\n")
    status = int(hl[0].split(" ")[1]) if len(hl[0].split(" ")) > 1 else -1
    headers = {}
    for line in hl[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    return status, headers, s, rest


def kernel_client(wid, timeout=5.0):
    """The kernel's record of the pane that dialed with `wid`, once the handler has registered it."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        with km._clients_lock:
            for c in km._clients:
                if c.get("wid") == wid and c.get("alive"):
                    return c
        time.sleep(0.02)
    raise AssertionError("the handler never registered the client %r" % wid)


class EndToEnd(unittest.TestCase):
    """The real Handler on a loopback ThreadingHTTPServer, driven by raw sockets."""

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.socks = []

    def tearDown(self):
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass
        self.srv.shutdown()
        self.srv.server_close()

    def test_a_browsers_offer_is_taken_and_both_directions_are_compressed(self):
        status, headers, s, rest = upgrade(self.port, "permessage-deflate; client_max_window_bits", wid="w-both")
        self.socks.append(s)
        self.assertEqual(status, 101)
        self.assertEqual(headers.get("sec-websocket-extensions"), ACCEPT)
        client = kernel_client("w-both")
        self.assertEqual(client.get("deflate"), {"wbits": 15, "bounded": False})
        rd = _Reader(s, rest)
        # kernel → client: a frame the size of a view goes compressed, a keepalive-sized one plain
        text = big_text()
        client["send"](text)
        b0, payload = rd.frame()
        self.assertEqual(b0, 0xC1)
        self.assertEqual(inflate(payload).decode("utf-8"), text)
        self.assertLess(len(payload), len(text) // 4)
        client["send"]('{"type":"ka"}')
        self.assertEqual(rd.frame(), (0x81, b'{"type":"ka"}'))
        # client → kernel: a compressed message reaches the dispatcher as the text it was
        seen = []
        real = km.Handler._dispatch_ws
        km.Handler._dispatch_ws = lambda self_, msg, c: seen.append(msg)
        try:
            body = json.dumps({"type": "invented-op", "pad": "z" * 5000, "n": 1})
            s.sendall(cframe(deflate(body.encode()), rsv1=True))
            body2 = json.dumps({"type": "invented-op", "pad": "z" * 5000, "n": 2})
            s.sendall(cfragments(deflate(body2.encode()), chunk=64, rsv1=True))   # as Chrome fragments a large send
            s.sendall(cframe(b'{"type":"invented-op","n":3}'))                       # …and a plain one still lands
            t0 = time.time()
            while len(seen) < 3 and time.time() - t0 < 5:
                time.sleep(0.02)
        finally:
            km.Handler._dispatch_ws = real
        self.assertEqual([m.get("n") for m in seen], [1, 2, 3])
        self.assertEqual(seen[0], json.loads(body))
        self.assertEqual(seen[1], json.loads(body2))

    def test_a_client_that_offered_nothing_stays_plain(self):
        status, headers, s, rest = upgrade(self.port, None, wid="w-plain")
        self.socks.append(s)
        self.assertEqual(status, 101)
        self.assertNotIn("sec-websocket-extensions", headers)
        client = kernel_client("w-plain")
        self.assertIsNone(client.get("deflate"))
        text = big_text()
        client["send"](text)
        self.assertEqual(_Reader(s, rest).frame(), (0x81, text.encode()))

    def test_an_offer_naming_the_default_window_gets_it_echoed(self):
        status, headers, s, _ = upgrade(self.port, "permessage-deflate; server_max_window_bits=15", wid="w-named15")
        self.socks.append(s)
        self.assertEqual(status, 101)
        self.assertEqual(headers.get("sec-websocket-extensions"), ACCEPT + "; server_max_window_bits=15")

    def test_repeated_extension_header_lines_are_read_together(self):
        # RFC 7230 lets a client send the list as several header lines; the offer on the second line must be seen
        status, headers, s, rest = upgrade(self.port, ["x-webkit-deflate-frame", "permessage-deflate; client_max_window_bits"], wid="w-twolines")
        self.socks.append(s)
        self.assertEqual(status, 101)
        self.assertEqual(headers.get("sec-websocket-extensions"), ACCEPT)
        self.assertEqual(kernel_client("w-twolines").get("deflate"), {"wbits": 15, "bounded": False})

    def test_a_malformed_offer_is_declined_not_refused(self):
        for ext in ("permessage-deflate; bogus_param", "permessage-deflate; server_max_window_bits=¹µ",
                    "permessage-deflate; client_max_window_bits=" + "0" * 5000):
            status, headers, s, _ = upgrade(self.port, ext, wid="w-bogus")
            self.socks.append(s)
            self.assertEqual(status, 101, "the upgrade still happens (no 500); only the extension is declined: %r" % ext[:60])
            self.assertNotIn("sec-websocket-extensions", headers, ext[:60])

    def test_the_kill_switch_declines_every_offer(self):
        saved = km._WS_DEFLATE_ON
        km._WS_DEFLATE_ON = False
        try:
            status, headers, s, _ = upgrade(self.port, "permessage-deflate; client_max_window_bits", wid="w-off")
            self.socks.append(s)
            self.assertEqual(status, 101)
            self.assertNotIn("sec-websocket-extensions", headers)
            self.assertIsNone(kernel_client("w-off").get("deflate"))
        finally:
            km._WS_DEFLATE_ON = saved

    def test_a_kernel_decided_end_sends_a_close_frame_and_logs_once(self):
        # a negotiated client whose compressed message is not a deflate stream: the pane reads Close 1007, not the 1006 of
        # a network drop, and the kernel log names the client and the cause, once
        status, headers, s, rest = upgrade(self.port, "permessage-deflate", wid="w-close")
        self.socks.append(s)
        self.assertEqual(headers.get("sec-websocket-extensions"), ACCEPT)
        kernel_client("w-close")
        rd = _Reader(s, rest)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            s.sendall(cframe(b"\xff\xfe\xfd not deflate at all", rsv1=True))
            b0, payload = rd.frame()
            self.assertTrue(rd.eof(), "the socket closes after the Close frame")
        self.assertEqual(b0, 0x88, "a Close frame")
        self.assertEqual(struct.unpack(">H", payload[:2])[0], 1007)
        self.assertIn("not a deflate stream", payload[2:].decode("utf-8"))
        lines = [l for l in err.getvalue().splitlines() if "ws: dropping feed client" in l]
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("(close 1007)", lines[0])
        # …and a client that negotiated nothing but set RSV1 reads 1002
        status, headers, s2, rest2 = upgrade(self.port, None, wid="w-close2")
        self.socks.append(s2)
        kernel_client("w-close2")
        rd2 = _Reader(s2, rest2)
        with contextlib.redirect_stderr(io.StringIO()):
            s2.sendall(cframe(deflate(b"{}" * 600), rsv1=True))
            b0, payload = rd2.frame()
        self.assertEqual((b0, struct.unpack(">H", payload[:2])[0]), (0x88, 1002))


class RelayExecuted(unittest.TestCase):
    """The federated relay, run: a second real Handler stands in for the remote kernel behind the hub's ssh -L port.
    The hub splices bytes without parsing frames, so it is the REMOTE that negotiates with the browser — which needs
    the hub to forward the offer header and relay the remote's answer back in the head. A relay that dropped the
    header would still upgrade, plain; this test then sees no extension header and a 0x81 frame."""

    def setUp(self):
        self.hub = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.remote = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        for srv in (self.hub, self.remote):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        self._saved = dict(km._remotes)
        with km._remotes_lock:
            km._remotes["gpu1"] = {"host": "gpu1", "kernel_port": 29855, "local_port": self.remote.server_address[1],
                                   "token": km.TOKEN, "status": "up"}
        self.socks = []

    def tearDown(self):
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved)
        for srv in (self.hub, self.remote):
            srv.shutdown()
            srv.server_close()

    def test_a_browser_negotiates_with_the_remote_kernel_through_the_hub(self):
        for ext, want_hdr, want_b0 in (("permessage-deflate; client_max_window_bits", ACCEPT, 0xC1), (None, None, 0x81)):
            wid = "w-relay-%d" % want_b0
            status, headers, s, rest = upgrade(self.hub.server_address[1], ext, path="/remote/gpu1/ws?app=feed&wid=%s&token=%s" % (wid, km.TOKEN))
            self.socks.append(s)
            self.assertEqual(status, 101)
            self.assertEqual(headers.get("sec-websocket-extensions"), want_hdr)
            remote_client = kernel_client(wid)                    # the remote kernel's record of this browser (one process, one _clients)
            self.assertEqual(remote_client.get("kind"), "relay")
            text = big_text()
            remote_client["send"](text)
            b0, payload = _Reader(s, rest).frame()
            self.assertEqual(b0, want_b0)
            self.assertEqual((inflate(payload) if want_b0 == 0xC1 else payload).decode("utf-8"), text)

    def test_an_offer_on_a_second_extension_line_reaches_the_remote(self):
        # RFC 6455 §11.3.2 lets the list come as several header lines, which the direct handshake reads together
        # (EndToEnd's repeated-lines test); the relay forwarded the first line only, so this offer upgraded plain through
        # the hub where it negotiated direct (review of #2106, 2026-09-23)
        wid = "w-relay-twolines"
        status, headers, s, rest = upgrade(self.hub.server_address[1], ["x-webkit-deflate-frame", "permessage-deflate; client_max_window_bits"],
                                           path="/remote/gpu1/ws?app=feed&wid=%s&token=%s" % (wid, km.TOKEN))
        self.socks.append(s)
        self.assertEqual(status, 101)
        self.assertEqual(headers.get("sec-websocket-extensions"), ACCEPT)
        remote_client = kernel_client(wid)
        self.assertEqual(remote_client.get("deflate"), {"wbits": 15, "bounded": False})
        text = big_text()
        remote_client["send"](text)
        b0, payload = _Reader(s, rest).frame()
        self.assertEqual(b0, 0xC1)
        self.assertEqual(inflate(payload).decode("utf-8"), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
