#!/usr/bin/env python3
# romp-postal-service — the Romp Postal Service.
#
# Peer messaging between romp (Claude Code) sessions, local and remote. One
# stdlib-only program (no deps, nothing to install) with several modes:
#
#   romp-postal-service serve            run the message bus (HTTP, singleton on 127.0.0.1:PORT)
#   romp-postal-service ensure           start the bus if it isn't running (race-safe; no-op on remote)
#   romp-postal-service mcp              run the per-session stdio MCP server (tool-native messaging)
#   romp-postal-service send <to> <txt>  deliver a message to a live romp session       [CLI]
#   romp-postal-service inbox            print + consume this session's mail             [CLI]
#   romp-postal-service peek             print this session's mail without consuming     [CLI]
#   romp-postal-service agents           list romp sessions (+ branch + what they're on)  [CLI]
#   romp-postal-service working <text>   publish what this session is working on          [CLI]
#   romp-postal-service sent             this session's sent messages + read status       [CLI]
#   romp-postal-service recall <to> [id] unsend an unread/parked message you sent          [CLI]
#   romp-postal-service drain --id <id>  loop-guarded consume, for the Stop hook
#
# Architecture: the bus is the single source of truth. Everything (the MCP
# server, the CLI, the Stop hook) talks to it over HTTP at 127.0.0.1:PORT, so
# local and remote sessions use the exact same address — a remote session just
# tunnels that port to the laptop with `ssh -R PORT:127.0.0.1:PORT`. The bus
# persists mailboxes to $XDG_STATE_HOME/romp/postal/mail/<session-id>/ (Maildir,
# atomic delivery), resolves recipient names against the live romp sessions
# (tmux) plus any heartbeating remote agents, and shuts itself down once no romp
# clients remain.
#
# Delivery has two paths. The backstop is the Stop hook: a recipient drains its
# mailbox at the next turn boundary (also check_inbox / `romp mail inbox`). On
# top of that, push-on-deliver (see _push) auto-wakes an IDLE local session by
# typing the mail straight into its prompt and submitting it — so a session
# sitting idle reacts immediately instead of only at its next turn. The push is
# careful never to clobber a draft (it stashes/restores via Ctrl+S) and stays
# clear of sessions at a permission prompt or mid-turn-with-a-draft, falling back
# to the drain whenever live injection isn't safe. Disable the push alone with
# ~/.claude/romp-postal-nopush; disable everything with ~/.claude/romp-postal-off.

import base64
import hashlib
import hmac
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("ROMP_POSTAL_PORT", "25302"))   # renumbered from 47100 alongside the kernel's port (the user 2026-07-24), same random draw. A bus that cannot bind degrades fleet messaging silently, rather than failing a URL someone is looking at, so a collision here is worth avoiding more, not less.
BASE = f"http://{HOST}:{PORT}"
KERNEL_BASE = "http://127.0.0.1:%s" % os.environ.get("ROMP_KERNEL_PORT", "29855")  # the dashboard kernel — it owns the backend session query (tmux + SDK)

STATE = Path(os.environ.get("ROMP_STATE_DIR")      # per-kernel state root override (plans/multi-kernel.md)
             or Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "romp") / "postal"
MAILROOT = STATE / "mail"
MAILPENDING = STATE / "mail-pending"   # touch <sid> here IFF that session has unread mail in new/
WARNED = STATE / "warned-undelivered"  # marker per msg-id we've already warned a sender is STILL UNDELIVERED (one-time)
LOG = STATE / "server.log"
PIDFILE = STATE / "server.pid"
NAMES_DIR = Path(os.environ.get("ROMP_STATE_DIR")
                 or Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "romp") / "names"
TLDIR = STATE.parent / "timeline"     # append-only logs for the timeline view (messages.jsonl)
SESSION_FLAGS = STATE.parent / "session-flags.json"   # the kernel's per-session view flags {sid:{flag:true}}; we honour postalServiceOff (legacy: postalOff)


# ── serve-token gate (Jupyter's model; the same 0600 file the kernel mints) ─────
# Loopback is reachable by EVERY local user on the machine, so the bus — which can wake sessions
# and inject mail straight into their prompts — requires the machine's serve token on every
# request except the /ping liveness probe. The 0600 file is the same-user trust boundary; kernel
# and bus share it (whichever daemon starts first mints it, identical logic). A peer bus dialing
# through an ssh forward authorizes with the DIALED machine's token (?token=), which rides the
# kernel's /peer notifies and /tunnels rows.
def _load_serve_token():
    t = (os.environ.get("ROMP_SERVE_TOKEN") or "").strip()
    if t:
        return t
    f = STATE.parent / "serve-token"              # ~/.local/state/romp/serve-token (STATE is romp/postal)
    try:
        v = f.read_text().strip()
        if v:
            return v
    except OSError:
        pass
    v = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v)
        os.chmod(f, 0o600)
    except OSError:
        pass
    return v


SERVE_TOKEN = _load_serve_token()


def _tok_eq(a, b):
    """Constant-time compare (no timing oracle on the serve token); never raises on odd input."""
    try:
        return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))
    except Exception:
        return False


POLL = int(os.environ.get("ROMP_POSTAL_POLL", "30"))        # autostop poll interval (seconds)
IDLE_GRACE = int(os.environ.get("ROMP_POSTAL_IDLE_GRACE", "2"))  # empty polls before the bus exits
HEARTBEAT_TTL = int(os.environ.get("ROMP_POSTAL_HEARTBEAT_TTL", "90"))  # remote-presence window after a heartbeat
WINDOW = 30        # loop-guard rolling window (seconds)
MAX = 6            # loop-guard: max auto-deliveries per window before pausing
RETRY_INTERVAL = int(os.environ.get("ROMP_POSTAL_RETRY", "5"))  # re-attempt deferred deliveries every N s
PICKER_GRACE = int(os.environ.get("ROMP_POSTAL_PICKER_GRACE", "10"))  # secs the kernel watches a revive for the resume picker (passed as the /picker-check timeout)
ORPHAN_GRACE = int(os.environ.get("ROMP_POSTAL_ORPHAN_GRACE", "900"))  # bounce unread mail to a dead recipient after N s
STUCK_GRACE = int(os.environ.get("ROMP_POSTAL_STUCK_GRACE", "600"))  # warn the SENDER when a LIVE-but-idle recipient still hasn't read after N s

REPLY_HINT = ('To reply (only if you have something substantive to add, not just to '
              'acknowledge): romp mail send --kind delegate|coordinate|question <name> "<text>" — '
              'put the whole point in your first sentence.')

# The bus no longer shells tmux: session enumeration, the working-note, mail delivery/wake, the resume-picker
# check, and the status-bar chrome all go through the kernel (the SessionBackend API), which owns the one tmux
# integration. Identity is the CLAUDE_CODE_SESSION_ID env. (the user 2026-06-26: tmux + SDK behind one API.)


def _self_id():
    """THIS session's fsid, from CLAUDE_CODE_SESSION_ID — the harness sets it for EVERY session (SDK and tmux
    alike), so it's the reliable identity, and the only one that's right for an SDK session (whose MCP may be
    parented under a leftover tmux pane and so resolve to a DIFFERENT session — the user 2026-06-24). None when
    not in a romp session. No tmux fallback: the bus never shells tmux; the env var IS the designed identity."""
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip() or None

def _self_row():
    """THIS session's live agent row. CLAUDE_CODE_SESSION_ID is the CURRENT transcript fsid, and a
    /clear or resume fork moves that off the stable romp sid every store is keyed by (names registry,
    mailboxes, working notes, session flags) — so a forked session that trusted the env var mailed as
    "unknown", published its working note under an id no peer could see, and read an EMPTY mailbox
    (the user 2026-07-27). Resolve through the kernel's sessions seam instead: an exact id match
    first, else the row whose lastSid is our fsid (the SDK registry's authoritative stable→current
    join, published on every /sessions row). None when not a romp session or the kernel is down."""
    sid = _self_id()
    if not sid:
        return None
    agents = local_agents(threads=True)   # a comment thread resolves to its OWN row/name (2026-08-22)
    return (next((a for a in agents if a.get("id") == sid), None)
            or next((a for a in agents if a.get("lastSid") == sid), None))

def my_id():
    row = _self_row()
    return row["id"] if row else _self_id()

def my_name():
    # The live agent row first (it tracks renames AND survives transcript forks); the names registry as
    # the kernel-down fallback. None when not in a romp session (no tmux fallback: the bus never shells
    # tmux).
    row = _self_row()
    if row and row.get("name"):
        return row["name"]
    sid = _self_id()
    if not sid:
        return None
    try:
        return (NAMES_DIR / sid).read_text().split("\t")[0].strip() or None
    except Exception:
        return None

# ───────────────────────── maildir store ─────────────────────────

def _iso_now():
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def _safe_id(s):
    """True iff `s` is safe as a single path component under the mail/names roots.
    Blocks path traversal (`..`, `/`, `\\`, NUL, leading dot, absolute paths) in
    any id/name that arrives over the (unauthenticated) bus. Session ids are
    UUIDs and names are sanitized to [A-Za-z0-9_-] at creation, so both match;
    anything else is a crafted reference (e.g. `../../../etc`) and is rejected."""
    if not s or len(s) > 128:
        return False
    if "/" in s or "\\" in s or "\x00" in s or s.startswith("."):
        return False
    return bool(_SAFE_ID_RE.match(s))

# Every character str.splitlines() treats as a line break — \n \r \v \f \x1c \x1d \x1e
# \x85 U+2028 U+2029 — plus the rest of the C0/C1 control range and NUL with them. Nothing
# printable is in here: spaces, punctuation, accents, CJK and emoji all live outside it.
_HDR_BREAK_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

def _hdr_val(v):
    """One value, made safe to write into a maildir header line (see deliver).

    The header block is FRAMED by newlines and read back by splitting on them: read_box
    ends the block at the first blank line and lets a later key overwrite an earlier one.
    So a line break inside any VALUE forges or overwrites every other header — including
    the From: line the recipient is shown — and a blank line promotes the rest of that
    value into the body. Five of the six values deliver() writes reach it over the bus:
    the sender's claimed name and id from /send, and the kind, origin host and relay mid
    a peer supplies on an inbound relay.

    A break is REPLACED (U+FFFD), never dropped: nothing goes missing silently, the value
    keeps its length and position, and the substitution is visible — a recipient looking
    at a tampered From: sees that it was tampered with rather than a plausible-looking
    name. Ordinary content is untouched, so a name with spaces or accents round-trips
    unchanged.

    This makes the values SAFE TO FRAME, not TRUSTWORTHY: from_id is still an
    unauthenticated claim the sender asserts about itself, exactly as before."""
    return _HDR_BREAK_RE.sub("\ufffd", "" if v is None else str(v))

def _mailbox(sid):
    if not _safe_id(sid):
        raise ValueError("unsafe session id")
    mb = MAILROOT / sid
    for d in ("tmp", "new", "cur"):
        (mb / d).mkdir(parents=True, exist_ok=True)
    return mb

def _unique():
    # self_host(), not raw gethostname: the mid is a path component under mail/ and the peer's
    # outbox (_safe_id-checked at outbox_put), so a stomped hostname baked in here silently killed
    # every OUTBOUND cross-host send too — same 2026-08-11 breakage as self_host's docstring.
    return f"{int(time.time())}.{os.getpid()}_{random.randint(0, 99999)}.{self_host()}"

def _mark_pending(sid):
    """Reconcile the on-disk pending-mail marker with reality: mail-pending/<sid>
    exists IFF that session has unread mail in new/. Call after ANY mutation of a
    new/ box (deliver, consuming read_box, recall, sweep). On-disk and tmux-free,
    so it's the ONE fact every view can agree on — including DEAD sessions (no tmux
    vars) and across a bus restart. Self-correcting + idempotent; never raises."""
    if not sid:
        return
    m = MAILPENDING / sid
    newd = MAILROOT / sid / "new"
    try:
        has = newd.is_dir() and any(newd.iterdir())
    except Exception:
        has = False
    try:
        if has:
            MAILPENDING.mkdir(parents=True, exist_ok=True)
            m.touch()
        elif m.exists():
            m.unlink()
    except Exception:
        pass

def _tl_append(fname, obj):
    """Append one JSON line to a timeline log (best-effort; never raises)."""
    try:
        TLDIR.mkdir(parents=True, exist_ok=True)
        with open(TLDIR / fname, "a") as fh:
            fh.write(json.dumps(obj) + "\n")
    except Exception:
        pass

def deliver(to_id, from_name, from_id, body, park=False, kind="", from_host="",
            relay_mid="", relay_via="", tracked=False):
    # park=True marks a HANDOFF parked for a session that's currently dead. The
    # maildir is keyed by the session UUID (which `romp resume` reuses), so the
    # message simply waits on disk until that session is revived — delivered then,
    # ignored forever if it never returns. Parked mail is also exempt from the
    # orphan sweep (see _sweep_orphans), so it persists indefinitely.
    # from_host: the sender's ORIGIN host for cross-host (federated) mail, "" for local. A foreign
    # from_id resolves to nothing in the recipient kernel's names registry, so this is the only
    # durable record of where the sender lives — the courier snapshots it into a planted goal's
    # origin so the "from" chip can say host:name instead of a bare sid prefix (the user 2026-07-26).
    # relay_mid/relay_via: for cross-host mail, the sender's message id and the DIRECT peer it arrived
    # from — stamped into headers so the read receipt can flow back when the recipient actually reads
    # it (read_box/restore queue it into the readbox). The maildir file is the durable record: the
    # receipt route survives a bus restart exactly as long as the unread mail does.
    mb = _mailbox(to_id)
    name = _unique()
    tmp = mb / "tmp" / name
    # THE header write point — every value that lands in a header line goes through _hdr_val
    # first, because a line break in any ONE of them rewrites all the others (see _hdr_val).
    # Doing it here and not at the callers covers all four of them at once: /send, an inbound
    # peer relay, a quarantine approval, and a deferred push putting mail back. Date is this
    # module's own strftime output, so it is written as-is, and the BODY is deliberately left
    # alone: it comes after the blank line and is never parsed as headers.
    raw = {"from": from_name, "from_id": from_id, "kind": kind,
           "from_host": from_host, "relay_mid": relay_mid, "relay_via": relay_via}
    h = {k: _hdr_val(v) for k, v in raw.items()}
    broke = sorted(k for k, v in raw.items() if h[k] != str(v or ""))
    if broke:
        # Say so — a header value with a line break in it is either a bug or an attempt — but
        # still deliver: the recipient needs the BODY, and dropping the mail over a malformed
        # attribution would lose more than it protects. Field names only, no attacker text.
        _log("deliver to %s: line breaks neutralized in header value(s) %s"
             % (to_id, ", ".join(broke)))
    hdr = "From: %s\nFrom-Id: %s\nDate: %s\n" % (h["from"], h["from_id"], _iso_now())
    if park:
        hdr += "X-Park: 1\n"
    if h["kind"]:
        hdr += "X-Kind: %s\n" % h["kind"]           # sender-declared delegate|coordinate|question (2026-07-08)
    if h["from_host"]:
        hdr += "X-From-Host: %s\n" % h["from_host"]
    if h["relay_mid"] and h["relay_via"]:
        hdr += "X-Peer-Mid: %s\nX-Peer-Via: %s\n" % (h["relay_mid"], h["relay_via"])
    tmp.write_text(hdr + "\n" + body + "\n")
    tmp.rename(mb / "new" / name)   # atomic within the same filesystem
    _mark_pending(to_id)            # new/ is now non-empty -> raise the marker (covers park + live)
    # Timeline log: a message was SENT (the matching exec event is logged when
    # the recipient consumes it in read_box). id = maildir filename joins the two.
    ev = {"t": int(time.time()), "ev": "sent", "id": name,
          "from": from_name, "from_id": from_id, "to_id": to_id, "body": body}
    if park:
        ev["park"] = True
    if kind:
        ev["kind"] = kind                            # additive (consumer contract above)
    if tracked:
        ev["tracked"] = True                         # additive (consumer contract above): report-back
        #                                              delegation — the row is the flag's ONE record;
        #                                              no header, no prose (the recipient reads nothing)
    if from_host:
        ev["from_host"] = from_host                  # additive (consumer contract above)
    _tl_append("messages.jsonl", ev)
    return name   # the message id (maildir filename); joins to the log + status-bar prefix

def read_box(sid, consume):
    if not _safe_id(sid):            # reject traversal in the id from /inbox, /drain
        return []
    if _postal_off(sid):             # isolated: hold mail — don't deliver while the mailbox is off (it waits in new/)
        return []
    mb = MAILROOT / sid
    newd = mb / "new"
    if not newd.is_dir():
        return []
    if consume:
        (mb / "cur").mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(newd.iterdir(), key=lambda p: p.name):   # oldest first
        if not f.is_file():
            continue
        text = f.read_text(errors="replace")
        head, _, body = text.partition("\n\n")
        meta = {}
        for line in head.splitlines():
            k, _, v = line.partition(": ")
            meta[k.lower()] = v
        out.append({"from": meta.get("from", "?"), "from_id": meta.get("from-id", ""),
                    "date": meta.get("date", ""), "body": body.rstrip("\n"), "id": f.name,
                    "park": bool(meta.get("x-park")), "kind": meta.get("x-kind", ""),
                    "from_host": meta.get("x-from-host", "")})
        if consume:
            f.rename(mb / "cur" / f.name)
            _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "exec", "id": f.name})
            _queue_read_receipt(meta, dmid=f.name)   # cross-host mail: the sender's host learns it was read
            #   dmid = THIS host's delivery mid — the id the recipient's transcript markers carry, so the
            #   sender's timeline can join the connector to the true process turn (the user 2026-08-06)
    if consume:
        _mark_pending(sid)         # cleared the box -> drop the marker (no-op if more arrived)
    return out

def restore(sid, mid):
    """UNCLAIM a consumed message: move cur/<mid> back to new/ under its ORIGINAL id.

    The counterpart to read_box(consume=True). A consuming drain is a CLAIM, not a delivery — the
    claimer may fail to hand the mail over (the kernel can't inject safely), and then the claim has
    to be rolled back. Rolling it back by re-sending through deliver() mints a NEW id and logs a
    NEW "sent" event, which is what made a timeline message arc click land nowhere: the arc is drawn
    from the message log, so every deferred push drew ANOTHER arc for the same message, and only the
    id from the FINAL attempt was the one that reached the recipient's transcript. Every earlier
    arc pointed at an id no transcript would ever carry (the user 2026-07-23). Keeping the id makes
    one message one arc, and that arc lands. Restoring the FILE also keeps the original headers
    (X-Park, X-Kind, Date), which the re-send dropped.

    Returns True iff the message was put back."""
    if not _safe_id(sid) or not _safe_id(mid):
        return False
    src = MAILROOT / sid / "cur" / mid
    if not src.is_file():                # recalled/swept while we held it — nothing to put back
        return False
    try:
        head = src.read_text(errors="replace").partition("\n\n")[0]
    except OSError:
        head = ""
    try:
        (MAILROOT / sid / "new").mkdir(parents=True, exist_ok=True)
        src.rename(MAILROOT / sid / "new" / mid)
    except OSError:
        return False
    # The exec stamp said "the recipient read it"; it didn't. Retract it so the sender's receipt
    # reads pending again (_sent_receipts drops an exec that a later unexec retracts).
    _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "unexec", "id": mid})
    meta = {}
    for ln in head.splitlines():
        k, _, v = ln.partition(": "); meta[k.lower()] = v
    _queue_read_receipt(meta, unread=True)   # cross-host: retract the read the claim implied
    _mark_pending(sid)                   # new/ is non-empty again -> raise the marker
    return True

def _queue_read_receipt(meta, unread=False, dmid=""):
    """Cross-host read backflow: mail delivered over the peer bus carries X-Peer-Mid/X-Peer-Via
    (see deliver); consuming it queues {mid, t} into the readbox for the DIRECT peer it arrived
    from, so the sender's host can finally log the exec its receipt view joins on. An `origin`
    stamp rides along when the mail was forwarded (X-From-Host != the direct peer): the hop
    re-queues it one host backward (_read_arrived), mirroring how forwarded acks travel. A
    rolled-back claim (restore) queues unread=True — keyed by mid, it supersedes a still-parked
    read, and at the origin an unexec for a never-exec'd id is a harmless no-op."""
    pm, via = meta.get("x-peer-mid", ""), meta.get("x-peer-via", "")
    if not pm or not via:
        return
    rec = {"mid": pm, "t": int(time.time())}
    if dmid:
        rec["dmid"] = dmid   # the recipient-side delivery mid — the sender's timeline joins turns on it
    if unread:
        rec["unread"] = True
    oh = meta.get("x-from-host", "")
    if oh and oh != via:
        rec["origin"] = oh
    readbox_put(via, rec)

# ───────────────────────── formatting (shared) ─────────────────────────
#
# CONSUMER CONTRACT (stable — don't break without pinging dependents, e.g. vs_app2's
# romp-chat-view). Two machine-readable parts are relied on; keep them stable:
#   1. The `<!-- romp-msg-id: <id> -->` HTML-comment marker emitted after each
#      message body by format_inbox + format_push — <id> joins to messages.jsonl.
#   2. The messages.jsonl "sent" event schema written by deliver():
#      {ev:"sent", id, from, from_id, to_id, body, t, park?, kind?, from_host?, tracked?}
#      (park/kind/from_host/tracked are all additive; `tracked` marks a report-back delegation —
#      kind stays "delegate" — whose sender-side view is primary: the kernel courier reads it off
#      this row, never off the message prose).
# The HUMAN-FACING prose (banner text, headers, the "⏸ parked" tag, REPLY_HINT) is
# NOT a contract — consumers must not parse it, so it stays free to change.

def format_inbox(msgs, me_id=""):
    if not msgs:
        return ""
    out = ["\U0001F4EC New message(s) from your romp peers:"]
    for m in msgs:
        d = " (%s)" % m["date"] if m.get("date") else ""
        pk = "  ⏸ parked while you were offline — may be stale" if m.get("park") else ""
        # Mail from yourself is indistinguishable from a peer's reply once it is rendered, and
        # reading your own report back as an answer is worse than losing it (see
        # resolve_recipient, which now refuses to create these). Anything already on disk, or
        # looped in from a peer, says so.
        if me_id and m.get("from_id") == me_id:
            pk += "  (this is YOUR OWN message, arrived back in your inbox: not a reply)"
        mid = ("\n<!-- romp-msg-id: %s -->" % m["id"]) if m.get("id") else ""   # exact id for the timeline join
        if m.get("kind"):
            mid += "\n<!-- romp-msg-kind: %s -->" % m["kind"]   # sender-declared kind, read by the courier
        out.append("\n— from %s%s%s:\n%s%s" % (_from_disp(m), d, pk, m.get("body", ""), mid))
    out.append("\n" + REPLY_HINT)
    return "\n".join(out)

def _from_disp(m):
    """The sender name a banner shows. Never the literal "unknown"/"?" a broken sender minted
    (pre-2026-08-18 mail, or a peer bus older than the /send refusal): a canned body over
    "from unknown" reads as a greeting from a ghost. Say what is true instead."""
    nm = str(m.get("from") or "").strip()
    return nm if nm and nm.lower() != "unknown" and nm != "?" else "an unidentified session"

def format_agents(agents, me, me_id=""):
    if not agents:
        return "(no live romp sessions)"
    lines = []
    for a in agents:
        # '(you)' is an IDENTITY claim, so match on the session id when we have one. Matching on
        # the name alone marks every same-named session as you, which is precisely the case where
        # the reader most needs to know which row is theirs (see resolve_recipient).
        mine = (a.get("id") == me_id) if me_id else (a["name"] == me)
        tag = " (you)" if mine else (" [remote]" if a.get("remote") else "")
        if a.get("thread") and not mine:
            # a comment thread of one of these sessions: addressable for replies, but a minor player —
            # say whose it is so nobody mistakes it for a full peer (the user 2026-08-22)
            pn = next((x.get("name") for x in agents if x.get("id") == a.get("parent")), "")
            tag = " (thread of %s)" % (pn or "a session here")
        # host prefix + short stable id (the user 2026-08-24): a duplicate-name refusal lists its
        # candidates as host:name, and the uuid is the rename-proof address — without either on the
        # row, the reader matched an error message by guesswork. Short form: enough to disambiguate
        # AND to paste as a recipient (resolve_recipient matches an unambiguous id prefix of 8+
        # chars) — progressive disclosure, not a wall of hex.
        rid = str(a.get("id") or "")
        host = rid.split(":", 1)[0] if (a.get("remote") and ":" in rid) else ""
        disp = ("%s:%s" % (host, a["name"])) if (host and not str(a["name"]).startswith(host + ":")) else a["name"]
        short = (rid.rsplit(":", 1)[-1] if ":" in rid else rid)[:8]
        sid_tag = (" · %s" % short) if short else ""
        br = ("  [%s]" % a["branch"]) if a.get("branch") else ""
        wk = ""
        if a.get("working"):
            # The working-note is an ownership CLAIM, and it's only LIVE while the session is actively
            # WORKING. An idle/waiting session's note is a claim from a finished turn — flag it so a peer
            # discounts a stale claim by READING instead of waking the session to ask "still yours?" (the
            # user 2026-06-24). state "working" = live; anything else (idle/waiting/permission) = may be
            # stale. Remote agents carry no state → no flag.
            st = a.get("state", "")
            stale = "  (idle now — claim may be stale)" if st and st != "working" else ""
            wk = "  — %s%s" % (a["working"], stale)
        lines.append("  %s%s%s%s%s" % (disp, tag, sid_tag, br, wk))
    return "\n".join(lines)

def _hhmm_epoch(t):
    try: return datetime.fromtimestamp(int(t)).strftime("%H:%M")
    except Exception: return "?"

def format_receipts(recs):
    if not recs:
        return "No messages sent yet."
    out = ["Your recent sent messages:"]
    for r in recs[-15:]:
        if r.get("exec"):
            st = "read %s" % _hhmm_epoch(r["exec"])
        elif r.get("recalled"):
            st = "recalled %s" % _hhmm_epoch(r["recalled"])
        elif r.get("bounced"):
            st = "bounced %s — undeliverable, returned to you" % _hhmm_epoch(r["bounced"])
        elif r.get("parked"):                  # cross-host, still in the outbox awaiting relay
            # "(unreachable)" ONLY when the link is actually down (the user 2026-08-24): a healthy
            # queue is normal transit, not a failure. An older bus omits parkedUp — claim nothing.
            if r.get("parkedUp"):
                st = "queued for relay to %s · id %s" % (r["parked"], r.get("id", "?"))
            elif "parkedUp" in r:
                st = "parked for %s (unreachable) — delivers on reconnect · id %s" % (r["parked"], r.get("id", "?"))
            else:
                st = "parked for %s · id %s" % (r["parked"], r.get("id", "?"))
        elif r.get("relayed"):                 # landed on the peer host; its read receipt hasn't come back
            st = "delivered %s (not read yet) · id %s" % (_hhmm_epoch(r["relayed"]), r.get("id", "?"))
        else:                                  # still unread -> recallable; show the id to target it
            st = "pending (not read yet) · id %s" % r.get("id", "?")
        out.append("  → %-18s sent %s · %s" % (r.get("to", "?"), _hhmm_epoch(r["sent"]), st))
    return "\n".join(out)

# ───────────────────────── the bus (server) ─────────────────────────

HEARTBEATS = {}        # id -> (name, last_seen_epoch)   (remote presence)
STREAKS = {}           # id -> (count, last_epoch)        (loop guard)
_lock = threading.Lock()

def _kernel_sessions(threads=False):
    """LIVE romp sessions (tmux + SDK) from the kernel's unified GET /sessions — the kernel owns the backend
    query (TmuxBackend for tmux liveness + the SDK registry), so the bus enumerates sessions WITHOUT shelling
    tmux and WITHOUT reading the SDK registry directly: ONE source. Loopback, authorized with X-Romp-Token
    (the shared 0600 serve-token file — the kernel gates every request, loopback included). [] if the kernel
    is unreachable (rare — the manager supervises it); the bus then shows no local
    agents until it's back, rather than reaching past the abstraction to tmux.

    ROMP_SESSIONS_FILE is a test seam (like ROMP_*_BIN): a JSON file of the same rows, read instead of the
    live kernel so the bus is testable without one."""
    seam = os.environ.get("ROMP_SESSIONS_FILE")
    if seam:
        try:
            data = json.loads(Path(seam).read_text())
            if not isinstance(data, list):
                return []
            # the seam mirrors the route: thread rows ride only when asked (the user 2026-08-22)
            return data if threads else [r for r in data if not (isinstance(r, dict) and r.get("thread"))]
        except Exception:
            return []
    import urllib.request
    try:
        req = urllib.request.Request(KERNEL_BASE + "/sessions" + ("?threads=1" if threads else ""),
                                     headers={"X-Romp-Token": SERVE_TOKEN})
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def local_agents(threads=False):
    """LIVE local sessions (tmux + SDK) as postal agent rows, read from the kernel's unified GET /sessions.
    The kernel merges both backends, so an SDK session is a live agent here too — a send to an open SDK
    session delivers instead of parking as dead (the user via ui, 2026-06-26).

    `threads` (the user 2026-08-22): also include COMMENT-THREAD sessions — real forked sessions the
    kernel hides from tabs/lanes/cards until promotion. Opt-in per consumer so the default listing and
    every other reader stay exactly as they were: self-identity, recipient resolution, and the agents
    listing pass True (a thread mails its parent under its OWN name and is addressable for replies);
    everything else never sees them."""
    res = []
    for s in _kernel_sessions(threads=threads):
        sid = s.get("id")
        if not sid:
            continue
        row = {"name": s.get("name") or sid[:8], "id": sid, "remote": False,
               "working": s.get("working", ""), "dir": s.get("dir", ""),
               "lastSid": s.get("lastSid", ""),   # the session's CURRENT transcript fsid (self-identity join)
               "state": s.get("state", "")}   # state: working/idle/waiting/... → working-note freshness
        if s.get("thread"):
            row["thread"] = True
            row["parent"] = s.get("parent") or ""
        res.append(row)
    return res


def _kernel_post(path, body, timeout=2):
    """POST a small JSON body to the kernel (loopback, X-Romp-Token from the shared 0600 file) — the bus's
    one-way control channel for the
    ops the kernel owns now that the bus never shells tmux: the working-note, mail delivery/wake, the
    status-bar chrome, and the resume-picker check. Returns the parsed JSON response dict, or None
    (unreachable kernel / non-2xx / parse error → the caller degrades). No-op (None) under the
    ROMP_SESSIONS_FILE test seam, which signals a test running with no live kernel."""
    if os.environ.get("ROMP_SESSIONS_FILE"):
        return None
    import urllib.request
    try:
        req = urllib.request.Request(KERNEL_BASE + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "X-Romp-Token": SERVE_TOKEN}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if getattr(r, "status", 200) // 100 != 2:
                return None
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:
        return None


def _kernel_up():
    """True when THIS machine's kernel answers /healthz (auth-exempt, so no token dance). The
    autostop gate reads it: a machine whose kernel is up is a live romp installation — peer buses
    dial ITS bus for presence and INBOUND mail — so the bus must keep listening even with zero
    local sessions. A quiet hub's bus used to self-stop on local-session count alone, and every
    cross-host message through it silently parked until a manual `ensure` (verified twice,
    2026-08-12); local sessions are the wrong liveness signal for a hub. False under the
    ROMP_SESSIONS_FILE seam, like _kernel_post: that seam means a test with no live kernel."""
    if os.environ.get("ROMP_SESSIONS_FILE"):
        return False
    import urllib.request
    try:
        with urllib.request.urlopen(KERNEL_BASE + "/healthz", timeout=2) as r:
            return getattr(r, "status", 200) // 100 == 2
    except Exception:
        return False


def _publish_working(sid, text):
    """Publish/clear THIS session's working-note via the kernel's backend-agnostic store (POST /working) — no
    tmux. The kernel owns the store and both backends read it (it appears in GET /sessions' `working` field),
    so an SDK session can publish a note too."""
    return _kernel_post("/working", {"id": str(sid), "text": text}) is not None if sid else False

def all_agents(threads=False):
    agents = local_agents(threads=threads)
    local_ids = {a["id"] for a in agents}
    now = time.time()
    for sid, (name, ts) in list(HEARTBEATS.items()):
        if now - ts < HEARTBEAT_TTL and sid not in local_ids:
            agents.append({"name": name, "id": sid, "remote": True})
    return agents

def _record_heartbeat(sid, name):
    """Record remote-presence for an incoming heartbeat — but ONLY for a sid the local kernel does NOT already
    own. A local session is already visible via the kernel's /sessions, so recording its heartbeat would leave
    it lingering as a phantom [remote] peer for the TTL after it dies. A genuine REMOTE (federated) session,
    reaching us over an -R tunnel, is NOT in the local kernel — heartbeats are its only presence signal."""
    if sid and _safe_id(sid) and sid not in {a["id"] for a in local_agents()}:
        HEARTBEATS[sid] = (name or "?", time.time())

def present_count():
    return len(all_agents())

def _postal_off(sid):
    """True if the session toggled POSTAL ISOLATION on (the timeline lane's mailbox icon → postalServiceOff): it's
    invisible to list_agents, can't send, and can't receive — for working privately. Reads the kernel's
    shared session-flags.json. Back-compat: also honours the legacy `postalOff` key so sessions isolated
    before the rename stay isolated. Best-effort: any error → not isolated (fail OPEN, never wedge messaging)."""
    if not sid:
        return False
    try:
        f = json.loads(SESSION_FLAGS.read_text()).get(sid)
        return bool(isinstance(f, dict) and (f.get("postalServiceOff") or f.get("postalOff")))
    except Exception:
        return False

def _git_branch(d):
    """Current git branch of a dir (for the agent list — same-branch is what makes
    file overlap a real collision). '' if not a repo / on error."""
    if not d:
        return ""
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""

def _name_for_id(sid):
    if not sid:
        return "?"
    for a in local_agents():
        if a["id"] == sid:
            return a["name"]
    try:
        return (NAMES_DIR / sid).read_text().split("\t")[0].strip() or sid[:8]
    except Exception:
        return sid[:8]

def _recip_id_for(to):
    """A recipient reference (live name, or UUID with an existing mailbox) -> session
    UUID, or None. Addressing is LIVE-only: a name that isn't a currently-live session
    fails (no dead-session resurrection)."""
    for a in all_agents():
        if a["name"] == to:
            return a["id"]
    if _safe_id(to) and (MAILROOT / to).is_dir():  # already an id with a mailbox (in-flight mail)
        return to
    return None

def resolve_recipient(to, frm_id=""):
    """Resolve a recipient reference to exactly ONE destination, or explain why it can't.

    Returns exactly one of:
      {"kind": "direct", "agent": row}             -> deliver into that session's mailbox here
      {"kind": "relay", "host": h, "agent": row}   -> hand to the peer bus on `host`
      {"kind": "error", "error": str, "status": n} -> refuse, and say why

    Addressing is by unqualified session NAME, and a name is unique only by convention: two live
    sessions can share one, on two hosts or on the same host. Taking the first match was silent,
    and its worst tiebreak was the SENDER itself. A session that mailed its own name had three
    substantive reports delivered straight back into its own inbox, rendered exactly like any
    peer's message, so the loopback CONFIRMED that the peer was reachable and answering, and the
    reports never went anywhere (reported by a session 2026-07-29). Nothing legitimate sends to
    self, so identity is checked FIRST; after that, more than one candidate is a refusal that
    names the alternatives rather than a pick. `host:name` is how the sender says which one.
    """
    if ":" in to:
        want_host, bare = to.split(":", 1)
    else:
        want_host, bare = "", to
    here = self_host()
    # Everything this bus can deliver to itself: local sessions plus heartbeating remotes. A
    # host qualifier naming somebody ELSE takes them all out of the running.
    # a uuid-shaped `to` addresses the STABLE session id (the user 2026-08-23, via the experiment
    # machinery's cost-out: names are labels that renames retire; the sid survives them). An id is
    # unique by construction, so the ambiguity arm below never fires for it; the self-send check
    # still does — mailing your own sid is the same loopback as mailing your own name.
    by_id = bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", bare))
    direct_all = ([] if (want_host and want_host != here)
                  else [a for a in all_agents(threads=True)
                        if (a.get("id") == bare if by_id else a["name"] == bare)])   # threads addressable for replies
    if not direct_all and not by_id and not want_host             and re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{7,35}", bare):
        # a SHORT id — the ` · <8-char>` form every list_agents row now carries (the user
        # 2026-08-24) — addresses by unambiguous id PREFIX, so the row is enough to act on. An
        # exact NAME match always wins first (a name may be hex-shaped); at least 8 characters so
        # a stray word can never catch a session by luck; a remote row's id ("host:uuid") matches
        # on its uuid part, the part the row shows. TWO prefix hits fall through to the standing
        # ambiguity refusal below, exactly like a duplicated name.
        direct_all = [a for a in all_agents(threads=True)
                      if str(a.get("id") or "").rsplit(":", 1)[-1].startswith(bare)]

    if frm_id and any(a["id"] == frm_id for a in direct_all):
        return {"kind": "error", "status": 409,
                "error": "'%s' is THIS session's own name. A message there lands in your OWN inbox "
                         "looking exactly like a reply from someone else, so nothing was sent. Run "
                         "list_agents: your own row is the one marked '(you)'. If you meant a peer "
                         "that happens to share your name, address it as host:name." % bare}

    direct = [a for a in direct_all if not _postal_off(a["id"])]
    peer_cands = []
    if peers_on():
        ph, hit = peer_route(to)
        peer_cands = [(ph, hit)] if ph else list(hit)

    if len(direct) + len(peer_cands) > 1:
        labels = []
        for a in direct:
            # Two live sessions HERE share the name: no address can separate them, so show the id
            # rather than print the same candidate twice and call it a choice.
            labels.append("%s:%s%s" % (here, a["name"],
                                       (" [%s]" % a["id"][:8]) if len(direct) > 1 else ""))
        labels += ["%s:%s" % (h, a.get("name") or bare) for h, a in peer_cands]
        hint = ("Address it as host:name to say which one you mean." if len(direct) <= 1 else
                "Two sessions on this host answer to that name, so no address distinguishes them. "
                "Ask the user which they meant, or have one renamed.")
        return {"kind": "error", "status": 409,
                "error": "'%s' is ambiguous: %d live sessions answer to it (%s). Nothing was sent. %s"
                         % (bare, len(direct) + len(peer_cands), ", ".join(sorted(labels)), hint)}

    if direct:
        return {"kind": "direct", "agent": direct[0]}
    if peer_cands:
        return {"kind": "relay", "host": peer_cands[0][0], "agent": peer_cands[0][1]}
    if direct_all:                        # live, but every candidate has its mailbox off
        return {"kind": "error", "status": 403,
                "error": "isolation: the RECIPIENT '%s' has its mailbox OFF (it's in "
                         "postal isolation — its mailbox icon is toggled off), so it can't receive "
                         "mail right now. YOUR mailbox is fine; nothing was sent. It'll "
                         "be reachable once the user toggles ITS mailbox back on." % to}
    # Addressing is LIVE-only: no dead-session resurrection, so anything left is a typo.
    return {"kind": "error", "status": 404, "error": "no live romp session named '%s'" % to}


def _recall(from_id, to, mid):
    """Unsend UNREAD mail: delete messages still sitting in a recipient's `new/`
    (queued or parked) that were sent BY from_id. With `to`, scope to that one
    recipient; with `mid` (and no `to`), find it across mailboxes; both narrows to
    one message. Only the original sender's own messages are touched. Already-read
    mail has left `new/` and can't be recalled. Returns [{to, id, body}] removed."""
    if not from_id:
        return []
    if to:
        rid = _recip_id_for(to)
        boxes = [rid] if rid else []
    elif MAILROOT.is_dir():
        boxes = [b.name for b in MAILROOT.iterdir() if b.is_dir()]
    else:
        boxes = []
    removed = []
    for rid in boxes:
        newd = MAILROOT / rid / "new"
        if not newd.is_dir():
            continue
        for f in list(newd.iterdir()):
            if not f.is_file() or (mid and f.name != mid):
                continue
            try:
                text = f.read_text(errors="replace")
            except Exception:
                continue
            meta = {}
            head, _, body = text.partition("\n\n")
            for ln in head.splitlines():
                k, _, v = ln.partition(": "); meta[k.lower()] = v
            if meta.get("from-id", "") != from_id:    # only the sender can recall their own
                continue
            try:
                f.unlink()
            except Exception:
                continue
            removed.append({"to": _name_for_id(rid), "id": f.name, "body": " ".join(body.split())[:120]})
            _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "recall", "id": f.name})
        _mark_pending(rid)         # recall may have emptied new/ -> reconcile the marker
    if peers_on() and OUTBOX.is_dir():
        # A recall that beats the truck always wins: parked cross-host mail is still local, so the
        # sender can unsend it right up until the exchange carries it. Forwarded mail (origin set)
        # belongs to a sender on another host — never touched here.
        for hostdir in OUTBOX.iterdir():
            if not hostdir.is_dir():
                continue
            for f in list(hostdir.glob("*.json")):
                if mid and f.stem != mid:
                    continue
                try:
                    msg = json.loads(f.read_text())
                except Exception:
                    continue
                if msg.get("frm_id") != from_id or msg.get("origin"):
                    continue
                if to and (msg.get("to") or "") != to and "%s:%s" % (hostdir.name, msg.get("to") or "") != to:
                    continue
                try:
                    f.unlink()
                except Exception:
                    continue
                removed.append({"to": "%s:%s" % (hostdir.name, msg.get("to") or "?"), "id": f.stem,
                                "body": " ".join((msg.get("body") or "").split())[:120]})
                _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "recall", "id": f.stem})
    return removed

def _sent_receipts(mid):
    """[{to, id, sent, exec, recalled}] for messages SENT by `mid`, joined by id,
    oldest first. exec is None until the recipient reads it; recalled is set if the
    sender later unsent it (so it shows 'recalled', not a permanent 'pending')."""
    log = TLDIR / "messages.jsonl"
    if not mid or not log.exists():
        return []
    sent, execs, recalls, relays, bounced = {}, {}, {}, {}, {}
    for line in log.read_text(errors="replace").splitlines():
        try: e = json.loads(line)
        except Exception: continue
        ev = e.get("ev")
        if ev == "sent" and e.get("from_id") == mid:
            sent[e["id"]] = e
        elif ev == "exec":
            execs[e["id"]] = e["t"]
        elif ev == "unexec":                         # a claimed-then-rolled-back drain (see restore):
            execs.pop(e["id"], None)                 # it was never read, so the receipt goes back to pending
        elif ev == "recall":
            recalls[e["id"]] = e["t"]
        elif ev == "relayed":                        # peer-bus: the far host's end-to-end delivery ack
            relays[e["id"]] = e["t"]
        elif ev == "bounced":                        # peer-bus: definitively undeliverable, returned
            bounced[e["id"]] = e["t"]

    def _parked(i, e):                               # still in the outbox → honestly parked, not lost
        tid = e.get("to_id", "")
        if tid.startswith("peer:") and not relays.get(i) and not bounced.get(i) and not recalls.get(i):
            h = tid[5:]
            if outbox_get(h, i):
                return h
        return None

    def _row(i, e):
        h = _parked(i, e)
        r = {"to": e.get("toName") or _name_for_id(e.get("to_id", "")), "id": i, "sent": e["t"],
             "exec": execs.get(i), "recalled": recalls.get(i),
             "relayed": relays.get(i), "bounced": bounced.get(i), "parked": h}
        if h:
            # the LINK state rides along (the user 2026-08-24): outbox residency alone is not
            # unreachability — a message queued ahead of the next exchange on a healthy link is
            # just in transit, and labeling it "(unreachable)" cried wolf on every normal relay.
            # PEERS is the authoritative dial state the send path already branches on.
            r["parkedUp"] = bool((PEERS.get(h) or {}).get("up"))
        return r

    out = [_row(i, e) for i, e in sent.items()]
    return sorted(out, key=lambda r: r["sent"])

def _drain(sid):
    # Loop guard: cap rapid auto-deliveries so two chatty agents can't volley
    # forever. Over the cap -> pause (don't consume); after a quiet window the
    # streak resets and delivery resumes.
    with _lock:
        peek = read_box(sid, consume=False)
        if not peek:
            return {"messages": [], "paused": False}
        now = time.time()
        count, last = STREAKS.get(sid, (0, 0))
        count = count + 1 if now - last <= WINDOW else 1
        if count > MAX:
            return {"messages": [], "paused": True}
        STREAKS[sid] = (count, now)
        return {"messages": read_box(sid, consume=True), "paused": False}

# ───────────────────────── push-on-deliver (auto-wake) ─────────────────────────
# When mail lands for a LOCAL romp session that's sitting idle, the bus wakes the recipient through the
# kernel (POST /deliver) so it sees the mail immediately instead of waiting for its next Stop-hook drain. The
# kernel owns the wake per backend — a tmux session gets the banner pasted into its prompt (draft-preserving),
# an SDK session gets it enqueued — so the BUS never shells tmux. The maildir drain stays as the backstop:
# whenever the kernel can't inject safely (a permission prompt, a draft it can't preserve, Claude mid-turn
# with a draft), it returns injected:false and the bus puts the mail back for the next-turn drain. Disable
# the live push with ~/.claude/romp-postal-nopush (or romp-postal-off, which also disables the drain).
PUSH_SENTINEL = "#" * 44                          # the banner's rule line (format_push)

def _push_disabled():
    h = Path.home() / ".claude"
    return (h / "romp-postal-off").exists() or (h / "romp-postal-nopush").exists()

def _sweep_orphans():
    """Bounce mail stuck UNREAD in a DEAD recipient's mailbox back to its (live)
    sender — "↩ UNDELIVERED …" — so the sender learns it never landed and can
    resend/route, then drop the orphaned copy. Only messages older than
    ORPHAN_GRACE are touched, so a session that closes and resumes (same id) within
    the grace still gets its mail. Run periodically by the bus monitor."""
    if not MAILROOT.is_dir():
        return
    live = local_agents()
    if not live:                                       # tmux hiccup, not "everyone died" — don't mass-bounce
        return
    live_ids = {a["id"] for a in live}
    by_name = {a["name"]: a for a in live}
    now = time.time()
    for box in MAILROOT.iterdir():
        if not box.is_dir() or box.name in live_ids:   # live recipient -> not orphaned
            continue
        newd = box / "new"
        if not newd.is_dir():
            continue
        recip = _name_for_id(box.name)
        for f in list(newd.iterdir()):
            if not f.is_file():
                continue
            try:
                text = f.read_text(errors="replace")
            except Exception:
                continue
            meta = {}
            head, _, body = text.partition("\n\n")
            for ln in head.splitlines():
                k, _, v = ln.partition(": "); meta[k.lower()] = v
            if meta.get("x-park"):                           # deliberate handoff -> lives until revival; never bounce/expire
                continue
            try:
                if now - f.stat().st_mtime < ORPHAN_GRACE:   # fresh -> let a resume claim it
                    continue
            except Exception:
                continue
            s = by_name.get(meta.get("from", ""))
            if s:                                      # bounce to the live sender + wake it
                bounce = ("↩ UNDELIVERED — your message to '%s' was never read; that session "
                          "has exited. Resend or route elsewhere.\nOriginal: %s"
                          % (recip, " ".join(body.split())[:160]))
                try:
                    deliver(s["id"], "Romp Postal Service", "", bounce)
                    threading.Thread(target=_push, args=(s["id"], s), daemon=True).start()
                except Exception as e:
                    _log("bounce to %s failed: %s" % (s["name"], e))
            try:
                f.unlink()
                # the destroy is the message's TERMINAL EVENT — record it on the original mid, the
                # way _bounce_apply records a peer's refusal (the user 2026-08-24): without this row
                # the ledger's last word stayed "sent", and the timeline's pending flag had to lean
                # on an age window / recipient liveness — which a same-sid REVIVAL then flips back
                # to pending for mail that no longer exists. The ledger is now terminal-complete.
                _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": f.name,
                                              "to": recip or "?",
                                              "why": "recipient exited; unread mail destroyed by the orphan sweep"})
            except Exception:
                pass
        _mark_pending(box.name)                         # bounced orphans may have emptied new/
        try:                                            # tidy: drop the mailbox if nothing's left
            if all(not any((box / d).iterdir()) for d in ("new", "cur", "tmp") if (box / d).is_dir()):
                shutil.rmtree(box, ignore_errors=True)
        except Exception:
            pass


def _warn_stuck_mail():
    """BACKSTOP sender feedback for a LIVE-but-unreachable recipient (the user 2026-06-29). The orphan sweep
    only bounces mail to a DEAD recipient; but mail can also strand UNREAD in a LIVE recipient's box — the
    recipient is idle yet never drains it (the stale-bus bug, or a wedged backend). The retry loop delivers to
    an idle session in seconds, so a message STILL unread after STUCK_GRACE while the recipient sits
    idle/waiting means something is wrong. Warn the (live) sender ONCE — '↩ STILL UNDELIVERED …' — and LEAVE
    the message in new/ (unlike the orphan bounce: a live recipient may yet receive it). Gated on the recipient
    being idle/waiting, NOT working — a message to a session mid-turn legitimately waits for its next turn, so
    that must never trip a false alarm. One-time via a persisted WARNED/<msg-id> marker; markers for delivered
    messages are pruned so WARNED stays bounded to currently-pending mail."""
    if not MAILROOT.is_dir():
        return
    live = local_agents()
    if not live:                                       # kernel hiccup, not "everyone's stuck" — don't warn
        return
    by_id = {a["id"]: a for a in live}
    by_name = {a["name"]: a for a in live}
    now = time.time()
    seen_ids = set()                                   # every msg id still pending anywhere → prune stale markers after
    for box in MAILROOT.iterdir():
        if not box.is_dir():
            continue
        newd = box / "new"
        if not newd.is_dir():
            continue
        recip = by_id.get(box.name)
        recip_settled = bool(recip and recip.get("state", "") in ("idle", "waiting"))
        for f in list(newd.iterdir()):
            if not f.is_file():
                continue
            seen_ids.add(f.name)
            if not recip_settled:                      # dead / unknown / working recipient → not (yet) provably stuck
                continue
            try:
                if now - f.stat().st_mtime < STUCK_GRACE:   # still within the normal delivery window
                    continue
            except Exception:
                continue
            marker = WARNED / f.name
            if marker.exists():                        # already warned the sender about this one
                continue
            try:
                text = f.read_text(errors="replace")
            except Exception:
                continue
            meta = {}
            head, _, body = text.partition("\n\n")
            for ln in head.splitlines():
                k, _, v = ln.partition(": "); meta[k.lower()] = v
            if meta.get("x-park"):                       # deliberate handoff → waits for revival, never 'stuck'
                continue
            s = by_name.get(meta.get("from", ""))
            if s and s["id"] != box.name:               # warn the live sender (never self)
                warn = ("↩ STILL UNDELIVERED — '%s' is live but hasn't read your message after %d min; it may "
                        "be stuck. Check on it or resend.\nOriginal: %s"
                        % (recip.get("name") or box.name[:8], max(1, STUCK_GRACE // 60),
                           " ".join(body.split())[:160]))
                try:
                    deliver(s["id"], "Romp Postal Service", "", warn)
                    threading.Thread(target=_push, args=(s["id"], s), daemon=True).start()
                except Exception as e:
                    _log("stuck-warn to %s failed: %s" % (s.get("name", "?"), e))
            try:                                        # mark one-time even if the sender was dead/absent → no re-scan churn
                WARNED.mkdir(parents=True, exist_ok=True)
                marker.touch()
            except Exception:
                pass
    try:                                                # prune markers whose message finally delivered (left new/)
        if WARNED.is_dir():
            for mk in WARNED.iterdir():
                if mk.name not in seen_ids:
                    mk.unlink(missing_ok=True)
    except Exception:
        pass


def _hhmm(iso):
    # _iso_now() -> "2026-06-05T14:23:45-0700"; pull HH:MM, else fall back to now.
    if iso and len(iso) >= 16 and iso[10:11] == "T":
        return iso[11:16]
    return datetime.now().astimezone().strftime("%H:%M")

def format_push(msgs):
    bar = PUSH_SENTINEL
    out = []
    for m in msgs:
        pk = " · ⏸ parked (you were offline)" if m.get("park") else ""
        head = "## \U0001F4EC from %s · %s%s" % (_from_disp(m), _hhmm(m.get("date", "")), pk)
        out += [bar, head, bar, m.get("body", "")]
        if m.get("id"):
            out.append("<!-- romp-msg-id: %s -->" % m["id"])   # exact id for the timeline join
        if m.get("kind"):
            out.append("<!-- romp-msg-kind: %s -->" % m["kind"])   # sender-declared kind, read by the courier
        out.append(bar)
    out.append('(to reply, only if substantive: romp mail send --kind delegate|coordinate|question %s "...")'
               % msgs[0].get("from", ""))
    return "\n".join(out)

def _push(sid, agent):
    """Live-deliver pending mail to a session by WAKING it through the kernel (POST /deliver) — the kernel
    injects the banner into the pane (tmux, draft-preserving) or enqueues it (SDK); the bus never shells tmux.
    Coarse-skip a clearly not-ready session (remote / not idle-or-working) to avoid a needless drain; the
    kernel does the fine pane-safety (at a ❯ prompt, out of copy-mode, a draft it can safely stash) and tells
    us whether it injected. Not injected → put the mail back for the maildir-drain backstop. Returns True iff
    injected (so a revive poll knows to stop). `agent` is the GET /sessions row (id, state, backend, remote)."""
    if _push_disabled() or not agent:
        return False
    if os.environ.get("ROMP_SESSIONS_FILE"):                  # test seam: no live kernel → leave it for the drain (don't churn the maildir)
        return False
    # A REMOTE (heartbeat) peer on an attached host: we don't have its live state here, so skip the local
    # state gate and POST /deliver anyway — the local kernel's wake-router forwards it over the host's -L
    # tunnel, and the OWNING kernel does the pane-safety and tells us whether it injected.
    if not agent.get("remote") and agent.get("state", "") not in ("waiting", "idle", "working"):
        return False                                          # permission / unknown / picker → drain later
    try:
        res = _drain(sid)                                     # claim mail (guarded + consuming)
        msgs = res.get("messages", [])
        if not msgs:
            return False                                      # nothing, or loop-guard paused
        resp = _kernel_post("/deliver", {"id": sid, "text": format_push(msgs)}, timeout=12)
        if resp and resp.get("injected"):
            return True
        # Not injected → UNCLAIM: put each message back under its ORIGINAL id (restore), so a
        # deferred push doesn't mint a second identity for the same message. Only if the file is
        # gone (recalled/swept mid-push) do we fall back to a re-send, which costs a new id but
        # never loses the mail.
        for m in msgs:
            if not restore(sid, m.get("id", "")):
                deliver(sid, m.get("from", "?"), m.get("from_id", ""), m.get("body", ""),
                        park=m.get("park", False), kind=m.get("kind", ""),
                        from_host=m.get("from_host", ""))
        _log("push to %s deferred; %d msg(s) restored for the drain backstop" % (sid, len(msgs)))
        return False
    except Exception as e:
        _log("push error for %s: %s" % (sid, e))
        return False

WAKE_TIMEOUT = int(os.environ.get("ROMP_POSTAL_WAKE_TIMEOUT", "45"))

def _wake_when_ready(sid):
    """Force-deliver pending mail to a REVIVING session once it's ready.

    A resumed session loads its transcript before its prompt box is interactive, and a SessionStart hook can
    only inject PASSIVE context (it cannot force a turn), so a session revived with parked handoffs would just
    sit idle on un-acted mail. Instead we poll until the session is live, then _push — which (via the kernel's
    /deliver) injects AND submits so the session takes a turn (waiting→working→acts→waiting) and shows WORKING
    in every existing view. The kernel returns injected:false while the prompt isn't live yet, so we just retry
    until it lands; if it never does within WAKE_TIMEOUT (a huge transcript), the mail stays in new/ for the
    Stop-hook drain — delivered on the first turn, just not force-acted. Runs off the /wake handler so the
    revive hook returns instantly."""
    try:
        deadline = time.time() + WAKE_TIMEOUT
        while time.time() < deadline:
            newd = MAILROOT / sid / "new"
            if not (newd.is_dir() and any(newd.iterdir())):
                return                                        # nothing pending (or already delivered)
            agent = next((a for a in local_agents() if a["id"] == sid), None)
            if not agent:
                return                                        # session died during load
            if _push(sid, agent):                             # injected (drain + submit → forces a turn) → done
                return
            time.sleep(0.5)
    except Exception as e:
        _log("wake wait failed for %s: %s" % (sid, e))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass   # keep stdout/stderr clean; the bus log is for real events only

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def _authorized(self):
        """Serve-token gate, every route but /ping (see the SERVE_TOKEN block). Local clients send
        X-Romp-Token read from the 0600 file; a peer bus dials through the ssh forward with ?token=
        (this machine's token, which its kernel learned at attach/checkin). No cookie form — no
        browser ever talks to the bus."""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if SERVE_TOKEN and _tok_eq((q.get("token") or [""])[0], SERVE_TOKEN):
            return True
        return bool(SERVE_TOKEN) and _tok_eq(self.headers.get("X-Romp-Token") or "", SERVE_TOKEN)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/ping":
            return self._send({"ok": True})
        if not self._authorized():
            return self._send({"error": "token required (serve token, loopback included — "
                               "~/.local/state/romp/serve-token)"}, 403)
        if u.path == "/peers":                     # peer-bus mode introspection (tests + the popover later)
            return self._send(peers_snapshot())
        if u.path == "/agents":
            me = (q.get("me") or [""])[0]
            agents = [a for a in all_agents(threads=True) if not _postal_off(a["id"])]   # isolated sessions are invisible to peers
            for a in agents:                       # enrich with branch for display only
                a["branch"] = _git_branch(a.get("dir", ""))
            if peers_on():
                # Peer-bus fleet view (DISPLAY only — all_agents() itself stays local so the delivery
                # paths can never mistake a peer entry for a local maildir): each peer's last-gossiped
                # presence, with honest staleness. Address cross-host with 'host:name' on collisions.
                # Gossip that duplicates a direct peer's row is folded (_via_duplicate), and a session
                # already listed never lists again under a second path — the doubled '[remote]' rows
                # (the user 2026-08-12).
                now = time.time()
                direct_bus = _direct_bus_ids()
                listed = {a["id"] for a in agents if a.get("id")}
                for host, st in PEER_STATE.items():
                    age = int(now - (st.get("seenAt") or 0))
                    for pa in st.get("presence") or []:
                        if _via_duplicate(pa, direct_bus):
                            continue
                        sid = pa.get("id") or ""
                        if sid and sid in listed:
                            continue
                        if sid:
                            listed.add(sid)
                        agents.append({"name": pa.get("name") or "?", "id": sid,
                                       "remote": True, "peer": host, "seenAgo": age})
            return self._send({"agents": agents, "me": me})
        if u.path == "/sent":
            sid = (q.get("id") or [""])[0]
            if not _safe_id(sid):
                return self._send({"error": "missing or invalid id"}, 400)
            return self._send({"sent": _sent_receipts(sid)})
        if u.path == "/inbox":
            sid = (q.get("id") or [""])[0]
            peek = (q.get("peek") or ["0"])[0] == "1"
            if not _safe_id(sid):
                return self._send({"error": "missing or invalid id"}, 400)
            return self._send({"messages": read_box(sid, consume=not peek)})
        if u.path == "/drain":
            sid = (q.get("id") or [""])[0]
            if not _safe_id(sid):
                return self._send({"error": "missing or invalid id"}, 400)
            return self._send(_drain(sid))
        if u.path == "/quarantine":                # held inbound mail from directed peers (kernel reads the
            return self._send({"held": quarantine_list()})   # dir directly for cards; this is for introspection/tests
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._authorized():
            return self._send({"error": "token required (serve token, loopback included — "
                               "~/.local/state/romp/serve-token)"}, 403)
        try:
            data = self._body()
        except Exception:
            return self._send({"error": "bad json"}, 400)
        if u.path == "/peer":                      # the kernel's tunnel-transition notify (peer-bus mode)
            payload, status = peer_update(data)
            return self._send(payload, status)
        if u.path == "/peer-exchange":             # a peer bus dialing us through the kernel's -L forward
            payload, status = peer_exchange_handle(data)
            return self._send(payload, status)
        if u.path == "/send":
            to = data.get("to", "")
            frm, frm_id = data.get("from", "unknown"), data.get("from_id", "")
            body = data.get("body", "")
            if not frm_id:
                # An unidentifiable sender's mail arrives literally "from unknown" — a canned-sounding
                # greeting the recipient can neither place nor answer (the user 2026-08-18, who met one
                # on their laptop; the 2026-07-27 clear-fork minted the same ghost). Refuse LOUDLY at
                # the one door every sender uses: the breakage is the SENDER's identity resolution, and
                # a visible error there beats ghost mail here (fail loudly, 2026-07-03). Cross-host
                # relays are unaffected — they arrive on the peer routes with identity in their headers.
                return self._send({"error": "sender identity required: this send carried no from_id, so "
                                   "it would arrive as mail 'from unknown' that the recipient cannot "
                                   "place or answer. The sender should know its own session id — fix "
                                   "that resolution and resend."}, 400)
            kind = str(data.get("kind", "")).strip().lower()
            if kind not in ("delegate", "coordinate", "question"):
                kind = ""                              # legacy/CLI mail may be undeclared; never invent one
            tracked = bool(data.get("tracked")) and kind == "delegate"   # report-back delegation
            #   (the user 2026-08-24): only a delegate can be tracked; wire metadata only — nothing
            #   about the flag ever appears in message prose (the injected-voice rule)
            if _postal_off(frm_id):                # the sender is in isolation → sending is disabled
                return self._send({"error": "isolation: YOUR OWN mailbox is OFF. This session is in postal "
                                   "isolation (its mailbox icon is toggled off on its timeline lane), so it "
                                   "can't send OR receive any mail. This is NOT the recipient's mailbox — the "
                                   "recipient is fine; nothing was sent. To fix, ask the USER to toggle THIS "
                                   "session's mailbox back on in the timeline, then retry. When you relay this, "
                                   "say it's YOUR mailbox that's off, not theirs."}, 403)
            # ONE resolution step for every case (self, ambiguous, isolated, relayed, unknown) —
            # see resolve_recipient. A name that answers to more than one live session is refused
            # here, not tiebroken.
            res = resolve_recipient(to, frm_id)
            if res["kind"] == "error":
                return self._send({"error": res["error"]}, res["status"])
            if res["kind"] == "relay":
                # Peer-bus relay: the name lives on a peer host → park in its outbox; the exchange
                # (or the next reconnect) carries it, and a definitive refusal bounces back to the
                # sender.
                phost, hit = res["host"], res["agent"]
                # `tracked` deliberately does NOT ride the relay: the primary view lives on the
                # SENDER's kernel, which the recipient's courier can never reach across hosts — a
                # satellite with no primary would hide work, so a cross-host tracked send degrades
                # to a plain delegate (revisit with federation) — and the NOTE says so: the sender
                # asked for a report-back and must hear it degraded (fail loudly, 2026-07-03).
                tnote = (" — report-back tracking does not cross hosts yet; sent as a plain handoff"
                         if tracked else "")
                mid = "px-" + _unique()
                outbox_put(phost, {"mid": mid, "to": hit.get("name") or to, "frm": frm,
                                   "frm_id": frm_id, "body": body, "kind": kind,
                                   "t": int(time.time())})
                _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "sent", "id": mid,
                                              "from": frm, "from_id": frm_id,
                                              "to_id": "peer:%s" % phost,
                                              "toName": "%s:%s" % (phost, hit.get("name") or to),
                                              "body": body, "kind": kind})
                if PEERS.get(phost, {}).get("up"):
                    return self._send({"ok": True, "id": mid,
                                       "note": "relaying to '%s' on %s%s" % (hit.get("name") or to, phost, tnote)})
                _kernel_post("/redial", {"host": phost})   # parking IS demand: ask the kernel to
                #                                             re-dial the host's tunnel now instead of
                #                                             waiting out its backoff (the user 2026-08-16)
                return self._send({"ok": True, "id": mid, "parked": phost,
                                   "note": ("parked for %s (unreachable) — delivers on reconnect, "
                                            "or bounces back to you" % phost) + tnote})
            a0 = res["agent"]
            mid = deliver(a0["id"], frm, frm_id, body, kind=kind, tracked=tracked)
            if not a0.get("remote", False):
                # All through the kernel (it owns the tmux status bar + the wake), off-thread so send latency
                # stays low: paint the recipient's "📬 from X" badge; record correspondence (peer chips) + the
                # directional top-line indicator on both ends; and auto-wake the recipient if it's idle.
                threading.Thread(target=_kernel_post, daemon=True,
                                 args=("/mail-badge", {"id": a0["id"], "from_name": frm, "from_id": frm_id})).start()
                threading.Thread(target=_kernel_post, daemon=True,
                                 args=("/deliver-chrome", {"recip_id": a0["id"], "recip_name": a0["name"],
                                       "sender_id": frm_id, "sender_name": frm, "body": data.get("body", ""), "mid": mid})).start()
                threading.Thread(target=_push, args=(a0["id"], a0), daemon=True).start()
            else:
                # A REMOTE peer on an attached host: still WAKE it — _push POSTs /deliver to the local kernel,
                # whose wake-router forwards it over the host's -L tunnel to the owning kernel (which injects
                # into the pane). The tmux status chrome above is local-only, so it's skipped for remotes.
                threading.Thread(target=_push, args=(a0["id"], a0), daemon=True).start()
            return self._send({"ok": True, "to": to})
        if u.path == "/recall":
            frm_id = data.get("from_id", "")
            if not frm_id:
                return self._send({"error": "missing from_id"}, 400)
            removed = _recall(frm_id, data.get("to", ""), data.get("id", ""))
            return self._send({"ok": True, "removed": removed})
        if u.path == "/wake":
            sid = data.get("id", "")
            if sid and _safe_id(sid):                         # force-deliver on revive once the prompt is live
                threading.Thread(target=_wake_when_ready, args=(sid,), daemon=True).start()
            return self._send({"ok": True})
        if u.path == "/heartbeat":
            _record_heartbeat(data.get("id"), data.get("name", "?"))
            return self._send({"ok": True})
        if u.path == "/quarantine/act":            # human verdict on a held message (approve/deny), from the
            mid = str(data.get("mid") or "")       # blocked card via the kernel; approve delivers, deny drops
            action = str(data.get("action") or "").strip().lower()
            text = data.get("text")                # optional human-edited body for approve
            ok, err = quarantine_decide(mid, action, text, feedback=data.get("feedback"))
            return self._send({"ok": ok} if ok else {"ok": False, "error": err}, 200 if ok else 400)
        self._send({"error": "not found"}, 404)

def _log(msg):
    try:
        sys.stderr.write("[postal] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass

# ── code-staleness self-restart ─────────────────────────────────────────────────────────────────────────
# The bus is a long-lived SINGLETON keyed on its port: `ensure` is a no-op while the old process answers, and
# `romp refresh` restarts the KERNEL, not the bus. So a bus started before a code change keeps serving STALE
# in-memory code indefinitely — which silently stranded mail to SDK sessions: a bus from before the "deliver
# via the kernel, not by pasting into a tmux pane" refactor literally couldn't reach a pane-less SDK recipient,
# and the message sat unread forever with no bounce (the user 2026-06-29). Guard: the bus fingerprints its own
# source at boot and the monitor re-execs into the new code the moment the file on disk changes. Pending mail
# lives in the maildir, so nothing is lost across the swap.
_SRC = os.path.abspath(__file__)

def _source_fingerprint(path=None):
    """sha1 of the bus's own source bytes, or "" if unreadable. "" never counts as a change (see
    _should_restart_for_code) so a transient read error can't trigger a spurious restart."""
    try:
        return hashlib.sha1(Path(path or _SRC).read_bytes()).hexdigest()
    except Exception:
        return ""

def _should_restart_for_code(boot_fp, cur_fp):
    """True iff the source changed since boot: BOTH fingerprints present AND different. An empty fp fails
    safe (stay up). After a re-exec the new boot fp equals the on-disk fp, so this can't loop."""
    return bool(boot_fp and cur_fp and boot_fp != cur_fp)

def _restart_self():
    """Re-exec the bus so it loads the new on-disk code. The listening socket is close-on-exec (Python
    default) so the port frees and the fresh image re-binds (HTTPServer.allow_reuse_address); stdout/stderr
    stay pointed at server.log. Does not return on success."""
    _log("source changed on disk — re-exec'ing into the new code (pid %d)" % os.getpid())
    try:
        os.execv(sys.executable, [sys.executable, _SRC, "serve"])
    except Exception as e:
        _log("re-exec failed (%s); staying on the old code" % e)

def _maybe_restart_for_code(boot_fp):
    """One monitor tick's staleness check. Returns True (and re-execs, not returning) when the source
    changed; False otherwise. Factored out so the decision is unit-testable without the execv."""
    if _should_restart_for_code(boot_fp, _source_fingerprint()):
        _restart_self()        # does not return on success
        return True
    return False

def _idle_tick(n, idle):
    """One autostop decision: (new_idle, stop). Factored out so the gate is unit-testable (the
    monitor loop sleeps). Local clients OR a live local kernel reset the count — a hub with zero
    local sessions still serves inbound peer exchanges as long as its kernel runs (_kernel_up)."""
    if n > 0 or _kernel_up():
        return 0, False
    idle += 1
    return idle, idle >= IDLE_GRACE


def _monitor(httpd, boot_fp=""):
    idle = 0
    while True:
        time.sleep(POLL)
        _maybe_restart_for_code(boot_fp)   # reload if the on-disk code changed under us (does not return on restart)
        try:
            _sweep_orphans()    # bounce mail stuck unread in dead recipients' mailboxes
        except Exception:
            pass
        try:
            _warn_stuck_mail()  # warn the sender when a LIVE-but-idle recipient still hasn't read (backstop)
        except Exception:
            pass
        try:
            n = present_count()
        except Exception:
            n = 1   # on error, err on the side of staying up
        idle, stop = _idle_tick(n, idle)
        if stop:
            _log("no romp clients remain (and no local kernel); shutting down")
            threading.Thread(target=httpd.shutdown, daemon=True).start()
            return

def _retry_pending():
    """RETRY deferred deliveries — the fix for stranded mail. _push (and the revive
    wake) are single-shot: when they can't safely inject (a resume-from-summary
    picker, a permission dialog, a prompt not yet at ❯), they correctly DEFER and
    leave the mail in new/. But an IDLE recipient then has no Stop hook to trigger
    the drain, so the mail strands until something else happens to arrive. Here the
    bus periodically re-attempts delivery for every session that still has pending
    mail (marker present) and is live; _push re-checks safety each pass and injects
    the moment the block clears and the session is at a clean ❯ prompt. This makes
    delivery EVENTUAL rather than single-shot — covering both the revive-picker race
    and the live-idle-behind-a-permission-dialog case. Honors 'don't wake unless
    needed': only sessions that actually hold mail are touched, and _push still only
    injects at a safe idle/working ❯ prompt (never mid permission dialog or over a
    draft it can't preserve). A dead session's marker is skipped (its mail waits for
    revival); a stale marker (new/ already empty) is reconciled away."""
    if not MAILPENDING.is_dir():
        return
    live = {a["id"]: a for a in local_agents()}
    for m in list(MAILPENDING.iterdir()):
        if not m.is_file():
            continue
        sid = m.name
        newd = MAILROOT / sid / "new"
        if not (newd.is_dir() and any(newd.iterdir())):
            _mark_pending(sid)                 # stale marker -> clear it
            continue
        if sid in live:
            try:
                _push(sid, live[sid])          # re-attempt; the kernel defers again if still unsafe
            except Exception as e:
                _log("retry push to %s failed: %s" % (sid, e))

def _retry_loop():
    while True:
        time.sleep(RETRY_INTERVAL)
        try:
            _retry_pending()
        except Exception:
            pass

def _reconcile_markers():
    """One-time sync of every pending-mail marker to the actual new/ boxes — so mail
    that predates the marker feature (or any drift) is corrected on bus startup."""
    if not MAILROOT.is_dir():
        return
    try:
        for box in MAILROOT.iterdir():
            if box.is_dir():
                _mark_pending(box.name)
    except Exception as e:
        _log("marker reconcile failed: %s" % e)

def serve():
    STATE.mkdir(parents=True, exist_ok=True)
    MAILROOT.mkdir(parents=True, exist_ok=True)
    _reconcile_markers()
    if peers_on():
        _seed_peers_from_kernel()          # a restarted bus re-learns its peers without waiting for a transition
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        _log("bus already running on %d (%s)" % (PORT, e))
        return 0
    try:
        PIDFILE.write_text(str(os.getpid()))
    except Exception:
        pass
    _log("bus up on %s (pid %d)" % (BASE, os.getpid()))
    boot_fp = _source_fingerprint()                              # so the monitor can reload if the code changes under us
    threading.Thread(target=_monitor, args=(httpd, boot_fp), daemon=True).start()
    threading.Thread(target=_retry_loop, daemon=True).start()   # re-deliver deferred/stranded mail
    try:
        httpd.serve_forever()
    finally:
        try:
            if PIDFILE.exists() and PIDFILE.read_text().strip() == str(os.getpid()):
                PIDFILE.unlink()
        except Exception:
            pass
    return 0

# ───────────────────────── client (talks to the bus) ─────────────────────────

class BusError(Exception):
    pass

def _http(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    headers["X-Romp-Token"] = SERVE_TOKEN            # same-machine client: the 0600 file is the credential
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", str(e))
        except Exception:
            msg = str(e)
        raise BusError(msg)
    except urllib.error.URLError as e:
        raise BusError("can't reach the Romp Postal Service bus at %s (%s)" % (BASE, getattr(e, "reason", e)))
    except Exception as e:
        raise BusError(str(e))

def ping():
    try:
        _http("GET", "/ping")
        return True
    except Exception:
        return False

CLIENT_ONLY = Path.home() / ".config/romp-postal/client-only"

def peers_on():
    """Peer-bus mode (plans/postal-peer-buses.md) — the DEFAULT since 2026-07-20 (the user's
    activation call): every machine runs its OWN bus; cross-host mail is bus peering over
    kernel-owned tunnels. ROMP_POSTAL_PEERS=0/off/false selects the legacy singleton scheme.
    Read at call time (test seam). KEEP IN SYNC with the kernel's _postal_peers_on."""
    v = os.environ.get("ROMP_POSTAL_PEERS")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "off", "false", "")

def is_client_only():
    if peers_on():
        return False       # peer mode retires client-only: nobody relies on a forwarded singleton bus
    return bool(os.environ.get("ROMP_POSTAL_CLIENT_ONLY")) or CLIENT_ONLY.exists()

# ── peer table (peer-bus mode, stage 1) ─────────────────────────────────────────
# host -> {"port": int, "up": bool, "at": epoch}. Written ONLY by the local kernel's /peer notifies
# (its tunnel supervisor owns link state; transitions are the events). Stage 2's peering protocol
# reads it to dial RELAYs; until then it is inert bookkeeping, visible at GET /peers.
PEERS = {}

def peer_update(data):
    """Apply one kernel notify. Returns (payload, status). `token` is the PEER machine's serve token
    (the kernel learned it at attach/checkin) — the dialer needs it because the peer's bus is
    token-gated too. `trust` is the per-host federation level (trusted|directed|isolated) the inbound
    gate reads. A token-less/trust-less notify (e.g. a down transition) keeps the last known values.

    ORIGIN-ONLY rows (the user 2026-07-25): {"host", "trust", "originOnly": true} with NO port sets a
    tier for a host this machine has no tunnel to — its mail arrives RELAYED through a hub, and the
    inbound gate judges by TRUE ORIGIN, so the tier needs a row here with nothing to dial. Portless,
    never given a dialer; applied to a CONNECTED row it touches only the trust."""
    host = str(data.get("host") or "").strip()
    if data.get("originOnly"):
        trust = str(data.get("trust") or "").strip()
        if not host or trust not in ("trusted", "directed", "isolated"):
            return {"error": "host and trust (trusted|directed|isolated) required"}, 400
        prev = PEERS.get(host) or {}
        row = {"port": prev.get("port"), "up": bool(prev.get("up")), "at": int(time.time()),
               "token": prev.get("token") or "", "trust": trust}
        if not prev.get("port"):
            row["originOnly"] = True
        PEERS[host] = row
        return {"ok": True, "originOnly": True}, 200
    port = data.get("port")
    if not host or not isinstance(port, int) or isinstance(port, bool) or not (0 < port < 65536):
        return {"error": "host and port required"}, 400
    prev = PEERS.get(host) or {}
    tok = str(data.get("token") or "") or prev.get("token") or ""
    trust = str(data.get("trust") or "") or prev.get("trust") or "directed"
    PEERS[host] = {"port": port, "up": bool(data.get("up")), "at": int(time.time()),
                   "token": tok, "trust": trust}
    _peer_threads_reconcile(host)                    # an up peer gets its dialer; a down one is woken to exit
    return {"ok": True, "up": sum(1 for p in PEERS.values() if p["up"])}, 200

def _direct_bus_ids():
    """The bus ids of every DIRECTLY-peered bus (a dialable PEERS row): the identity set the
    via-row consumers test gossip against. The id — proven by the peer exchange itself — is what
    "same box" means; a NICKNAME can't say it, because the same machine wears different ssh
    aliases on different hosts (the user 2026-08-12, whose directly connected box was also listed
    "reachable via relay" under the hub's name for it, and whose mail could hop the hub)."""
    out = set()
    for h, st in PEER_STATE.items():
        if (PEERS.get(h) or {}).get("port") and st.get("busId"):
            out.add(st["busId"])
    return out


def _via_duplicate(pa, direct_bus):
    """True when a GOSSIPED presence row (via set) names a box that is also a direct peer here —
    by bus id when the hub gossips it (viaBus, the nickname-proof identity), by name for a hub
    that predates the field. Duplicates are folded everywhere gossip is consumed: display
    (via_reach), addressing (peer_route), and the agent list — a direct link always wins over a
    relay hop, and never renders beside it."""
    far = pa.get("via")
    if not far:
        return False
    if (PEERS.get(far) or {}).get("port"):
        return True
    return bool(pa.get("viaBus")) and pa.get("viaBus") in direct_bus


def via_reach():
    """Hosts reachable only THROUGH a directly-peered hub: the far spokes whose sessions a hub
    gossips with `via` labels (fleet_presence — one hop, never re-gossiped). One row per far host:
    {"host", "via", "agents", "seenAgo", "trust"} — the popover's "reachable via relay" section, and
    the hook a trust-by-origin tier hangs on even though no tunnel to that host exists here.
    A spoke we ALSO hold a direct link to is folded (_via_duplicate: bus-id identity, so a nickname
    difference can't sneak the duplicate back in)."""
    now, out = int(time.time()), {}
    direct_bus = _direct_bus_ids()
    for hub, st in PEER_STATE.items():
        age = int(now - (st.get("seenAt") or 0))
        for pa in st.get("presence") or []:
            far = pa.get("via")
            if not far:      # a hub's exchange never gossips OUR sessions back (fleet_presence
                continue     # excludes the asking host), so no self-row can appear here
            if _via_duplicate(pa, direct_bus):        # directly peered here → its own row, not via
                continue
            e = out.setdefault(far, {"host": far, "via": hub, "agents": 0, "seenAgo": age,
                                     "trust": (PEERS.get(far) or {}).get("trust") or "directed"})
            e["agents"] += 1
            e["seenAgo"] = min(e["seenAgo"], age)
    return sorted(out.values(), key=lambda e: e["host"])

def _hold_rows():
    """This bus's OWN quarantine holds, summarized for gossip: enough for a peer's popover to say who
    is waiting where (mid, from -> to, true origin, a one-line gist) without shipping bodies around.
    Bounded — past 20 the COUNT is the story and the holder's own dashboard has the rest."""
    out = []
    try:
        for f in sorted(QUARANTINE.glob("*.json")):
            try:
                m = json.loads(f.read_text())
            except Exception:
                continue
            out.append({"mid": m.get("mid"), "frm": m.get("frm") or "?", "to": m.get("to") or "?",
                        "origin": m.get("origin") or "", "at": m.get("at") or 0,
                        "gist": " ".join(str(m.get("body") or "").split())[:90]})
    except OSError:
        pass
    return out[:20]

def holds_payload(exclude_host):
    """Quarantine-hold summaries for an exchange payload: our own + ONE hop from our other peers,
    labeled `via` and never re-gossiped — the same shape as fleet_presence, so a spoke can SEE the
    mail held for approval on the far spoke (the user 2026-07-25: a hold two machines away used to
    be invisible everywhere but on that machine's own dashboard)."""
    out = list(_hold_rows())
    for h, st in PEER_STATE.items():
        if h == exclude_host:
            continue
        for hd in st.get("holds") or []:
            if hd.get("via"):
                continue
            out.append(dict(hd, via=h))
    return out

def remote_holds():
    """Every hold we know about on OTHER machines, stamped with the machine that HOLDS it (`atHost`
    = the via label when relayed, else the direct peer). The kernel proxies this to the popover."""
    out = []
    for h, st in PEER_STATE.items():
        for hd in st.get("holds") or []:
            out.append(dict(hd, atHost=hd.get("via") or h))
    return out

_TRUST_RANK = {"isolated": 0, "directed": 1, "trusted": 2}


def least_trust(a, b):
    """The more restrictive of two tiers. Used to cap a FORWARDED message at its forwarder's tier:
    trust must never be assembled from a claim the claimant wrote about itself."""
    return a if _TRUST_RANK.get(a, 1) <= _TRUST_RANK.get(b, 1) else b


def my_tier_of(host):
    """The trust tier THIS bus applies to `host`'s direct mail — what _relay_in resolves for a
    token-proven direct relay (an exchange partner has, by definition, shown our serve token): an
    explicit row wins; a row without a level reads directed; no row at all reads trusted (the
    token-proven default). Declared to the peer in every exchange so each side can SHOW how the other
    holds it (the user 2026-07-26: a half-open pair was invisible until mail quarantined) — display
    and mirroring ride this; the GATE itself stays _relay_in's, receiver-evaluated, always."""
    row = PEERS.get(host)
    if row is None:
        return "trusted"
    return row.get("trust") or "directed"


def peers_snapshot():
    peers = {}
    for h, p in PEERS.items():
        d = dict(p)
        tt = (PEER_STATE.get(h) or {}).get("theirTier")
        if tt:
            d["theirTier"] = tt        # how that host holds US, from its last exchange declaration
        peers[h] = d
    return {"peers": peers, "viaReach": via_reach(), "remoteHolds": remote_holds()}

# ── peering protocol (peer-bus mode, stage 2) ───────────────────────────────────
# One EXCHANGE carries both directions (plans/postal-peer-buses.md): the dialer POSTs
# {host, epoch, proto, presence, relays, acks, bounces, wait} to the peer's /peer-exchange
# through the kernel's -L forward, and the response carries the peer's own presence, its
# relays for the dialer, and acks/bounces for the dialer's relays. `wait` long-polls: an
# empty-handed response parks on the per-host wake (set by outbox_put) up to EXCHANGE_WAIT,
# so mail crosses the instant it exists in EITHER direction with no polling cadence.
# Delivery is at-least-once (outbox until acked) + idempotent receipt (mid dedupe) —
# effectively exactly-once. A relay whose recipient is dead/unknown/isolated BOUNCES back
# to the sender as a bus-authored postal note: parking is only ever for a LINK being down,
# never for a dead session (live-only at delivery, loudly).

PEER_PROTO = 1
BUS_EPOCH = int(time.time())               # this bus process's boot — peers key cached presence on it
# This bus process's IDENTITY, carried on every exchange (additive, like `tier`; older peers omit it).
# One machine reaches a fleet under TWO names — the alias its kernel dials (the ssh-config name) and
# the hostname the machine declares about itself (self_host on ITS inbound dials) — and name-keyed
# PEER_STATE then holds two rows for one bus: every remote session listed twice, bare names ambiguous,
# and inbound relays trust-judged under the self-declared name instead of the alias the user tiered
# (the user 2026-07-27, whose box showed each session twice as <hostname>:<name> and <alias>:<name>).
# busId lets the receiver recognize "same bus, second name" and fold onto the dialable alias.
BUS_ID = os.urandom(16).hex()
OUTBOX = STATE / "outbox"                  # outbox/<host>/<mid>.json — cross-host mail awaiting its ACK
READBOX = STATE / "readbox"                # readbox/<host>/<mid>.json — read receipts awaiting their peer
PEER_SEEN = STATE / "peer-seen.jsonl"      # append-only receipt log — the idempotence window
_SEEN_CAP = 4000
_seen_ids = None                           # lazy in-memory mirror of PEER_SEEN's tail
EXCHANGE_WAIT = int(os.environ.get("ROMP_POSTAL_EXCHANGE_WAIT", "20"))
PEER_STATE = {}                            # host -> {"presence": [...], "epoch": int, "seenAt": t, "drift": str}
_peer_wakes = {}                           # host -> threading.Event (long-poll release + dialer poke)
_peer_threads = {}                         # host -> Thread (one dialer loop per up peer)
_peer_pending = {}                         # host -> {"acks": [mid], "bounces": [{mid, why}]} for the NEXT request
_peer_lock = threading.Lock()

def _host_name_candidates():
    """Raw machine-name candidates for the self_host fallback, most meaningful first. macOS keeps
    user-set names in scutil (LocalHostName is mDNS-restricted, ComputerName is free-form);
    elsewhere there is no second authority — the minted id below is the fallback."""
    if sys.platform != "darwin":
        return []
    out = []
    for key in ("LocalHostName", "ComputerName"):
        try:
            r = subprocess.run(["scutil", "--get", key], capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                out.append(r.stdout.strip())
        except Exception:
            pass
    return out

def _sanitize_host_name(name):
    """A candidate machine name reduced to a _safe_id-safe label: first dot-label, runs of unsafe
    chars folded to '-', trimmed. "" when nothing meaningful survives (a 1-char remnant of junk is
    an unstable identity, not a name)."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "").split(".")[0]).strip("-._")
    return name if len(name) >= 2 and _safe_id(name) else ""

_HOST_ID_FILE = STATE.parent / "self-host"   # minted-once stable identity; the kernel's _self_host shares it

def _minted_host_id():
    """Last-resort stable identity: mint once, persist, reuse. O_EXCL so two processes (bus and
    kernel) racing to mint converge on whoever wrote first."""
    try:
        name = _HOST_ID_FILE.read_text().strip()
        if _safe_id(name):
            return name
    except OSError:
        pass
    name = "host-%08x" % random.getrandbits(32)
    try:
        _HOST_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_HOST_ID_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, (name + "\n").encode())
        os.close(fd)
    except FileExistsError:
        try:
            prior = _HOST_ID_FILE.read_text().strip()
            if _safe_id(prior):
                return prior
        except OSError:
            pass
    except OSError:
        pass                                 # unwritable state root: stable per-process only, still safe
    return name

_self_host_fb = None                         # resolved fallback identity, cached after the first (logged) resolve

def self_host():
    """This machine's postal identity: short hostname (each side keys the OTHER by its own name for
    it, so exact agreement across machines is not required). ROMP_POSTAL_HOST overrides (tests).
    The name MUST clear _safe_id: peers key the outbox that holds mail FOR us by it, as a path
    component, so an unkeyable kernel hostname half-works — presence still crosses (PEER_STATE is a
    dict), but outbox_put on the peer refuses every message back, parked "unreachable" forever with
    the only trace a server-log line (2026-08-11, a kern.hostname stomped with control bytes). An
    unsafe name falls back, loudly: the platform's user-set machine name, else a minted persisted
    id. gethostname stays first and live, so fixing the machine's hostname takes effect on the next
    call with no restart."""
    env = os.environ.get("ROMP_POSTAL_HOST")
    if env:
        return env
    name = socket.gethostname().split(".")[0]
    if _safe_id(name):
        return name
    global _self_host_fb
    if _self_host_fb is None:
        _self_host_fb = next((s for s in map(_sanitize_host_name, _host_name_candidates()) if s),
                             "") or _minted_host_id()
        _log("self_host: kernel hostname %r fails path-safety; declaring %r to peers instead "
             "(fix the machine's hostname to control the name)" % (name, _self_host_fb))
    return _self_host_fb

def _peer_wake(host):
    with _peer_lock:
        ev = _peer_wakes.get(host)
        if ev is None:
            ev = _peer_wakes[host] = threading.Event()
        return ev

def _seen_load():
    global _seen_ids
    if _seen_ids is None:
        try:
            _seen_ids = set(PEER_SEEN.read_text().split()[-_SEEN_CAP:])
        except Exception:
            _seen_ids = set()
    return _seen_ids

def peer_seen_check(mid):
    return mid in _seen_load()

def peer_seen_add(mid):
    _seen_load().add(mid)
    try:
        PEER_SEEN.parent.mkdir(parents=True, exist_ok=True)
        with PEER_SEEN.open("a") as f:
            f.write(mid + "\n")
    except Exception as e:
        _log("peer-seen append failed: %s" % e)     # dedupe degrades to the in-memory window

def outbox_put(host, msg):
    """Park one cross-host message for `host` and poke its exchange (long-poll release + dialer).
    `host` and the message `mid` become path components, so both MUST clear _safe_id first: a
    peer-crafted `mid` like `../../../foo` over the unauthenticated bus would otherwise write
    outside OUTBOX (arbitrary-file-write). Legit ids (short hostnames, `_unique()` mids) pass."""
    mid = (msg or {}).get("mid") or ""
    if not (_safe_id(host) and _safe_id(mid)):
        _log("outbox_put: refusing unsafe host/mid %r/%r" % (host, mid))
        return
    d = OUTBOX / host
    d.mkdir(parents=True, exist_ok=True)
    (d / (mid + ".json")).write_text(json.dumps(msg))
    _peer_wake(host).set()

def outbox_list(host):
    if not _safe_id(host):
        return []
    try:
        out = []
        for f in sorted((OUTBOX / host).glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except Exception:
                pass
        return out
    except Exception:
        return []

def outbox_get(host, mid):
    if not (_safe_id(host) and _safe_id(mid)):   # host/mid are path components — block traversal
        return None
    try:
        return json.loads((OUTBOX / host / (mid + ".json")).read_text())
    except Exception:
        return None

def outbox_del(host, mid):
    if not (_safe_id(host) and _safe_id(mid)):   # host/mid are path components — block traversal
        return False
    try:
        (OUTBOX / host / (mid + ".json")).unlink()
        return True
    except Exception:
        return False

def readbox_put(host, rec):
    """Park one read receipt for `host` (readbox/<host>/<mid>.json) and poke its exchange. Keyed by
    the RELAY mid, so the latest state wins: a read superseded by a rolled-back claim (unread=True)
    leaves one file carrying the retraction, never both. Same at-least-once + idempotent-apply
    contract as the outbox — it survives a bus restart and re-sends until the peer confirms."""
    mid = (rec or {}).get("mid") or ""
    if not (_safe_id(host) and _safe_id(mid)):   # host/mid are path components — block traversal
        _log("readbox_put: refusing unsafe host/mid %r/%r" % (host, mid))
        return
    d = READBOX / host
    d.mkdir(parents=True, exist_ok=True)
    (d / (mid + ".json")).write_text(json.dumps(rec))
    _peer_wake(host).set()

def readbox_list(host):
    if not _safe_id(host):
        return []
    try:
        out = []
        for f in sorted((READBOX / host).glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except Exception:
                pass
        return out
    except Exception:
        return []

def readbox_del(host, rec):
    """Clear one CONFIRMED receipt — only if the file still says what the peer confirmed (the unread
    flag matches), so an ack for a read never deletes the retraction that superseded it mid-flight."""
    mid = (rec or {}).get("mid") or ""
    if not (_safe_id(host) and _safe_id(mid)):   # host/mid are path components — block traversal
        return
    f = READBOX / host / (mid + ".json")
    try:
        cur = json.loads(f.read_text())
        if bool(cur.get("unread")) == bool((rec or {}).get("unread")):
            f.unlink()
    except Exception:
        pass

def _read_arrived(host, r):
    """One read receipt from a peer: ours → log the exec (or its unexec retraction) into
    messages.jsonl, where _sent_receipts joins it to the cross-host sent event by the relay mid.
    Origin-stamped (the mail was forwarded through us) → re-queue one hop backward with the stamp
    stripped, so it can never loop — the same one-hop-max rule relays live by."""
    mid = (r or {}).get("mid") or ""
    if not _safe_id(mid):
        return
    origin = str((r or {}).get("origin") or "")
    if origin and origin != self_host():
        if PEERS.get(origin):                    # only toward a peer the kernel told us about
            fwd = {"mid": mid, "t": r.get("t")}
            if r.get("dmid"):
                fwd["dmid"] = r.get("dmid")
            if r.get("unread"):
                fwd["unread"] = True
            readbox_put(origin, fwd)
        return
    ev = "unexec" if r.get("unread") else "exec"
    row = {"t": int(r.get("t") or time.time()), "ev": ev, "id": mid}
    d = str((r or {}).get("dmid") or "")
    if _safe_id(d):
        row["dmid"] = d   # the recipient's own delivery mid → the timeline's exact turn join (2026-08-06)
    _tl_append("messages.jsonl", row)

def _bounce_apply(host, b):
    """A peer refused one of our parked messages — return it to the SENDER as a bus-authored note,
    loudly, and drop it from the outbox. Parking never outlives a definitive refusal."""
    mid = (b or {}).get("mid") or ""
    msg = outbox_get(host, mid)
    outbox_del(host, mid)
    if not msg:
        return
    note = ("undeliverable to '%s' on %s: %s\n\n(your message follows)\n%s"
            % (msg.get("to") or "?", host, (b or {}).get("why") or "refused", msg.get("body") or ""))
    if msg.get("frm_id"):
        deliver(msg["frm_id"], "romp-postal", "", note, kind="coordinate")
    _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": mid,
                                  "to": msg.get("to") or "?", "host": host,
                                  "why": (b or {}).get("why") or "refused"})

def fleet_presence(exclude_host):
    """Presence for an exchange payload: local agents + ONE hop of gossip from our other peers, each
    labeled `via` (plans/postal-peer-buses.md 3b) — so a spoke can address the far spoke through the
    hub. A via-entry is never re-gossiped (one-hop reach only; a topology needing two hops should
    check the second spoke in to the hub directly). Each via row also carries the far bus's own id
    (`viaBus`): the receiver folds gossip about a box it ALREADY peers with directly, and the id is
    the identity that survives nickname drift — the same machine wears different ssh aliases on
    different hosts, so a name can't say "same box" (the user 2026-08-12; see _via_duplicate)."""
    out = list(local_agents())
    for h, st in PEER_STATE.items():
        if h == exclude_host:
            continue
        for pa in st.get("presence") or []:
            if pa.get("via"):
                continue
            out.append(dict(pa, via=h, viaBus=st.get("busId") or ""))
    return out

# ── quarantine (per-host trust model) ───────────────────────────────────────────
# Mail from a DIRECTED peer is HELD here, one file per message, instead of injecting into the target
# session. The human approves/denies/edits it (a blocked card in the feed/chat); approve replays the
# same deliver() a TRUSTED peer's mail would have run. The kernel reads this dir directly to build the
# cards (fast, bus-down-resilient); mutations go through the bus routes below (delivery is postal's).
QUARANTINE = STATE / "quarantine"

def _quarantine_put(origin, m, to_id, via=""):
    """Hold one inbound relay from a directed host: quarantine/<mid>.json with everything approve needs
    to replay deliver(). Idempotent by mid (a resend overwrites the same file, never double-holds).
    `via` is the DIRECT peer it arrived from — kept so an approved delivery still carries the
    read-receipt route (older held records lack it; approve falls back to the origin)."""
    mid = m.get("mid") or ""
    if not _safe_id(mid):
        return False
    rec = {"mid": mid, "to": m.get("to") or "", "toId": to_id, "frm": m.get("frm") or "?",
           "frmId": m.get("frm_id") or "", "body": m.get("body") or "", "kind": m.get("kind") or "",
           "origin": origin, "via": via or origin, "at": int(time.time())}
    try:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        tmp = QUARANTINE / (mid + ".tmp")
        tmp.write_text(json.dumps(rec))
        tmp.rename(QUARANTINE / (mid + ".json"))      # atomic publish (the kernel may be reading the dir)
        _log("quarantine: held %s from %s -> %s (directed)" % (mid, origin, rec["to"]))
        return True
    except OSError:
        return False

def quarantine_list():
    """All held messages, newest first — the kernel's card source + the approve/deny UI."""
    out = []
    try:
        for f in QUARANTINE.glob("*.json"):
            try:
                out.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue
    except OSError:
        return []
    out.sort(key=lambda r: r.get("at") or 0, reverse=True)
    return out

def quarantine_get(mid):
    if not _safe_id(mid):
        return None
    try:
        return json.loads((QUARANTINE / (mid + ".json")).read_text())
    except (OSError, ValueError):
        return None

def quarantine_del(mid):
    if not _safe_id(mid):
        return False
    try:
        (QUARANTINE / (mid + ".json")).unlink()
        return True
    except OSError:
        return False

def quarantine_decide(mid, action, text=None, feedback=None):
    """Approve (deliver, optionally with human-edited text) or deny (drop) a held message. Returns
    (ok, error). Approve replays the deliver() the gate would have run for a trusted peer, so the
    message lands as normal postal mail (from-attribution intact). The mid was already peer_seen'd at
    hold time, so the sender never resends regardless of the verdict.

    `feedback` (the user 2026-07-26): an optional note back to the SENDER on a deny — parked in the
    origin host's outbox as ordinary store-and-forward mail from the postal service (so the sender's
    agent learns why instead of waiting forever), delivered on the next exchange and judged by the
    origin host's own trust gate like any inbound mail."""
    rec = quarantine_get(mid)
    if rec is None:
        return False, "no held message '%s'" % mid
    if action == "deny":
        quarantine_del(mid)
        note = " ".join(str(feedback or "").split())
        if note and rec.get("origin") and rec.get("frm"):
            gist = " ".join(str(rec.get("body") or "").split())[:60]
            body = ('Your message to %s ("%s%s") was reviewed there and declined — it was not '
                    "delivered. Note from the reviewer: %s"
                    % (rec.get("to") or "?", gist, "…" if len(gist) == 60 else "", note))
            fb_mid = _unique()
            outbox_put(rec["origin"], {"mid": fb_mid, "to": rec["frm"], "frm": "Romp Postal Service",
                                       "frm_id": "", "body": body, "kind": "coordinate"})
            _peer_wake(rec["origin"]).set()
        return True, None
    if action == "approve":
        body = str(text) if (text is not None and str(text).strip()) else (rec.get("body") or "")
        to_id = rec.get("toId") or ""
        live = {a["id"] for a in local_agents()}
        if to_id not in live:                         # session renamed/revived since it was held → re-match by name
            match = [a for a in local_agents() if a["name"] == rec.get("to") and not _postal_off(a["id"])]
            if not match:
                return False, "recipient '%s' is no longer a live local session" % (rec.get("to") or "?")
            to_id = match[0]["id"]
        deliver(to_id, rec.get("frm") or "?", rec.get("frmId") or "", body, kind=rec.get("kind") or "",
                from_host=rec.get("origin") or "",
                relay_mid=rec.get("mid") or "", relay_via=rec.get("via") or rec.get("origin") or "")
        quarantine_del(mid)
        return True, None
    return False, "unknown action '%s' (approve|deny)" % action


def _relay_in(host, m, token_proven=False):
    """One incoming relay: deliver locally, FORWARD one hop to a peer that owns the recipient, or
    bounce. Returns (verdict, bounce): 'ack' (delivered/held/deduped), 'hold' (forwarded — the
    END-TO-END ack comes back through us later; the sender keeps it parked meanwhile), 'bounce'
    (definitive refusal), or 'drop' (unidentifiable: no mid to ack or bounce). Per-host trust gates the
    local-delivery branch: trusted injects, directed holds for approval, isolated silently drops.

    `token_proven` — True on the DIALED side (peer_exchange_handle): every request past the HTTP gate
    presented THIS machine's serve token, and token possession already means full control here (the
    kernel is gated by the same token — a holder can inject into any session directly). So holding the
    dialer's OWN mail protects nothing and only strands the user's outgoing mail on a machine they
    attached (the user 2026-07-26, whose delegation to a fresh box sat quarantined on it). The proof
    covers only the direct dialer: mail it FORWARDED (origin-stamped) is judged by the origin's tier
    CAPPED at the forwarder's own (least_trust — a relay can never hand its cargo more trust than it
    holds itself), and an EXPLICIT tier the user set for the dialer (directed/isolated) still wins —
    the exemption replaces only the unknown-origin default. The dialer side (peer_exchange_apply) proves
    nothing: whatever answers the tunnel port never showed our token, so tiers gate it as before."""
    mid = m.get("mid") or ""
    if not mid:
        return "drop", None
    if peer_seen_check(mid):
        return "ack", None                           # duplicate → re-ack, deliver nothing
    to = m.get("to") or ""
    match = [a for a in local_agents() if a["name"] == to and not _postal_off(a["id"])]
    if match:
        # Trust key = the true ORIGIN (the forwarding host stamps m["origin"]; else the direct peer).
        # Unknown host (a race before the kernel's notify lands) defaults to directed — never auto-inject.
        origin = m.get("origin") or host
        prow = PEERS.get(origin)
        trust = (prow or {}).get("trust") or "directed"
        if prow is None and token_proven and not m.get("origin"):
            trust = "trusted"                        # token-proven direct dialer, no explicit tier → deliver (see docstring)
        if m.get("origin") and origin != host:
            # A FORWARDED message can never outrank the host that forwarded it. m["origin"] is
            # written BY the forwarder, so keying trust on the origin alone let any peer we dial
            # stamp the name of a host tiered `trusted` and have its mail auto-injected into a
            # session — precisely the attacker `directed` exists to hold for approval, and the
            # names to guess are handed out by our own presence gossip. Cap at the forwarder's
            # own tier so a directed relay stays directed however it labels its cargo.
            hrow = PEERS.get(host)
            htrust = (hrow or {}).get("trust") or "directed"
            if hrow is None and token_proven:
                htrust = "trusted"                   # token possession is already full control here (see docstring)
            trust = least_trust(trust, htrust)
        if trust == "trusted":
            deliver(match[0]["id"], m.get("frm") or "?", m.get("frm_id") or "", m.get("body") or "",
                    kind=m.get("kind") or "", from_host=origin,
                    relay_mid=mid, relay_via=host)       # read-receipt route: back through the direct peer
        elif trust == "directed":
            _quarantine_put(origin, m, match[0]["id"], via=host)   # HELD for human approve/deny/edit; never injects
        # else isolated → drop: ack so the sender stops resending, but deliver nothing (no communication).
        # An isolated host normally never peers at all (the kernel forces its notify down), so this is a
        # defensive backstop for the checkin-peer path where the mobile dials our /peer-exchange.
        peer_seen_add(mid)
        return "ack", None
    if any(a["name"] == to for a in local_agents()):
        return "bounce", {"mid": mid, "why": "recipient '%s' has its mailbox off (postal isolation)" % to}
    if not m.get("origin"):                          # one hop MAX: a message that already hopped never re-forwards
        fh, hit = peer_route(to)
        if fh and fh != host and not (hit or {}).get("via"):
            if outbox_get(fh, mid) is None:          # a resend while we hold it forwards nothing twice
                outbox_put(fh, dict(m, origin=host))
            return "hold", None
    return "bounce", {"mid": mid, "why": "no live session named '%s' on %s" % (to, self_host())}

def _ack_arrived(host, mid):
    """An end-to-end ack for outbox/<host>/<mid>: clear it. If we only FORWARDED it, relay the ack
    backward to the origin host; if it was ours, log the delivered receipt."""
    msg = outbox_get(host, mid)
    outbox_del(host, mid)
    if not msg:
        return
    if msg.get("origin"):
        p = _pending(msg["origin"])
        with _peer_lock:
            p["acks"].append(mid)
        _peer_wake(msg["origin"]).set()
    else:
        _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "relayed", "id": mid, "host": host})

def _bounce_arrived(host, b):
    """A bounce for outbox/<host>/<mid>: relay it backward if we only forwarded the message, else
    return it to our local sender."""
    mid = (b or {}).get("mid") or ""
    msg = outbox_get(host, mid)
    if msg and msg.get("origin"):
        outbox_del(host, mid)
        p = _pending(msg["origin"])
        with _peer_lock:
            p["bounces"].append(b)
        _peer_wake(msg["origin"]).set()
    else:
        _bounce_apply(host, b)

def _pending(host):
    with _peer_lock:
        p = _peer_pending.get(host)
        if p is None:
            p = _peer_pending[host] = {"acks": [], "bounces": [], "readAcks": []}
        return p

def _canon_peer_name(host, bus_id):
    """The name to file a peer's exchange under: the DIALABLE alias when `bus_id` proves this is a bus
    we already peer with under another name (see BUS_ID above). The alias row is the one the kernel
    notifies, the dialer runs on, and the user tiered — so it wins over a self-declared hostname. No
    bus_id (older peer) → the declared name stands, exactly as before."""
    if not bus_id or (PEERS.get(host) or {}).get("port"):
        return host   # a dialable name stands as itself; two dialable names is the kernel's dedupe
    for k, st in PEER_STATE.items():
        if k != host and st.get("busId") == bus_id and (PEERS.get(k) or {}).get("port"):
            return k
    return host


def _drop_peer_name_dupes(host, bus_id):
    """Forget PEER_STATE rows that are the SAME bus as `host` under another, non-dialable name — the
    stale half of a fold (e.g. the self-declared hostname row left from before the alias attached).
    Never drops a dialable row: two dialable names for one bus is a kernel-level duplicate with its
    own fix (attach_remote's token dedupe), and dropping either here would fight the kernel."""
    if not bus_id:
        return
    for k in [k for k, st in PEER_STATE.items()
              if k != host and st.get("busId") == bus_id and not (PEERS.get(k) or {}).get("port")]:
        PEER_STATE.pop(k, None)


def peer_exchange_handle(data):
    """The DIALED side of one exchange. Returns (payload, status)."""
    host = str((data or {}).get("host") or "").strip()
    if not host:
        return {"error": "host required"}, 400
    if (data or {}).get("proto") != PEER_PROTO:
        return {"error": "peer protocol drift (theirs %r, ours %r) — update romp on one side"
                % ((data or {}).get("proto"), PEER_PROTO), "proto": PEER_PROTO}, 409
    # Canonicalize BEFORE anything keys on the name: presence files under the alias (no duplicate
    # session rows), and the relays below are trust-judged under the alias the user actually tiered.
    bus_id = str((data or {}).get("busId") or "")
    host = _canon_peer_name(host, bus_id)
    if not _safe_id(host):
        # An unkeyable name would HALF-work: presence lands (PEER_STATE is a dict), but outbox_put
        # refuses it as a path component, so every reply parks "unreachable" with no error anywhere
        # (2026-08-11). Refuse the whole exchange instead — loud on the dialer's side (_peer_loop
        # logs the refusal) — unless the canonicalization above already folded the junk into a
        # checked-in alias, which is the existing self-heal and still works. Updated dialers never
        # declare such a name (self_host falls back); this guards against un-updated ones.
        return {"error": "unsafe host name %r — this machine's hostname fails path-safety; fix its "
                         "hostname (or set ROMP_POSTAL_HOST) and redial" % host}, 400
    PEER_STATE[host] = {"presence": data.get("presence") or [], "epoch": data.get("epoch"),
                        "holds": data.get("holds") or [], "seenAt": int(time.time())}
    if bus_id:
        PEER_STATE[host]["busId"] = bus_id
        _drop_peer_name_dupes(host, bus_id)
    if data.get("tier"):                             # the dialer's declared tier-of-us (additive; older peers omit it)
        PEER_STATE[host]["theirTier"] = str(data["tier"])
    for mid in data.get("acks") or []:               # the dialer confirmed relays landed — end-to-end:
        _ack_arrived(host, mid)                      # a forwarded one relays its ack back to the origin
    for b in data.get("bounces") or []:              # ...or refused them → backward, or to our sender
        _bounce_arrived(host, b)
    for ra in data.get("readAcks") or []:            # the dialer confirmed response-carried receipts
        readbox_del(host, ra)
    for r in data.get("reads") or []:                # read receipts flowing back — ours or one hop onward
        _read_arrived(host, r)
    acks, bounces = [], []
    for m in data.get("relays") or []:
        verdict, bounce = _relay_in(host, m, token_proven=True)   # past the HTTP gate = showed OUR serve token
        if verdict == "ack":
            acks.append(m.get("mid"))
        elif verdict == "bounce" and bounce:
            bounces.append(bounce)                   # 'hold': forwarded — its ack comes back later

    def _drain_backflow():                           # acks/bounces relayed BACK through us for this host
        p = _pending(host)
        with _peer_lock:
            a2, b2 = p["acks"], p["bounces"]
            p["acks"], p["bounces"] = [], []
        return a2, b2

    a2, b2 = _drain_backflow()
    acks, bounces = acks + a2, bounces + b2
    rel, reads = outbox_list(host), readbox_list(host)
    if not rel and not reads and data.get("wait") and not acks and not bounces:
        # nothing to hand back → park on the wake so anything we accept mid-wait crosses instantly
        _peer_wake(host).clear()
        _peer_wake(host).wait(EXCHANGE_WAIT)
        rel, reads = outbox_list(host), readbox_list(host)
        a2, b2 = _drain_backflow()
        acks, bounces = acks + a2, bounces + b2
    # `reads` stay parked until the dialer's NEXT request readAcks them — a response can vanish
    # after we send it, so the dialed side never clears on send. Re-applying a duplicate is a no-op.
    return {"host": self_host(), "epoch": BUS_EPOCH, "proto": PEER_PROTO, "busId": BUS_ID,
            "presence": fleet_presence(host), "holds": holds_payload(host),
            "tier": my_tier_of(host),                # how WE hold the dialer's mail (display/mirror, never the gate)
            "relays": rel, "acks": acks, "bounces": bounces, "reads": reads}, 200

def build_exchange_request(host, wait=True):
    p = _pending(host)
    with _peer_lock:
        acks, bounces, read_acks = list(p["acks"]), list(p["bounces"]), list(p.get("readAcks") or [])
    return {"host": self_host(), "epoch": BUS_EPOCH, "proto": PEER_PROTO, "busId": BUS_ID,
            "presence": fleet_presence(host), "holds": holds_payload(host),
            "tier": my_tier_of(host),                # how WE hold the dialed host's mail
            "relays": outbox_list(host),
            "acks": acks, "bounces": bounces,
            "reads": readbox_list(host), "readAcks": read_acks, "wait": bool(wait)}

def peer_exchange_apply(host, req_sent, resp):
    """The DIALER's half: fold one exchange response in. `req_sent` is the request that produced it —
    its included acks/bounces/readAcks are now delivered and leave the pending queue (kept on send
    failure), and its reads leave the readbox: a response means the dialed side processed the whole
    request before answering, so request-carried receipts need no explicit ack."""
    p = _pending(host)
    with _peer_lock:
        p["acks"] = [a for a in p["acks"] if a not in (req_sent.get("acks") or [])]
        p["bounces"] = [b for b in p["bounces"] if b not in (req_sent.get("bounces") or [])]
        p["readAcks"] = [a for a in p.get("readAcks") or [] if a not in (req_sent.get("readAcks") or [])]
    for r in req_sent.get("reads") or []:
        readbox_del(host, r)
    PEER_STATE[host] = {"presence": resp.get("presence") or [], "epoch": resp.get("epoch"),
                        "holds": resp.get("holds") or [], "seenAt": int(time.time())}
    bus_id = str(resp.get("busId") or "")
    if bus_id:                                       # the dialed alias is canonical for this bus: fold any
        PEER_STATE[host]["busId"] = bus_id           # row it left under its self-declared hostname
        _drop_peer_name_dupes(host, bus_id)
    if resp.get("tier"):                             # the dialed side's declared tier-of-us
        PEER_STATE[host]["theirTier"] = str(resp["tier"])
    for mid in resp.get("acks") or []:
        _ack_arrived(host, mid)
    for b in resp.get("bounces") or []:
        _bounce_arrived(host, b)
    for r in resp.get("reads") or []:                # response-carried receipts: apply, ack on the NEXT dial
        _read_arrived(host, r)
        with _peer_lock:
            p["readAcks"].append({"mid": r.get("mid"), "unread": bool(r.get("unread"))})
    for m in resp.get("relays") or []:
        verdict, bounce = _relay_in(host, m)
        with _peer_lock:
            if verdict == "ack":
                p["acks"].append(m.get("mid"))
            elif verdict == "bounce" and bounce:
                p["bounces"].append(bounce)          # 'hold': forwarded — its ack comes back later

def peer_route(to):
    """Where a non-local name lives: (host, agent) for exactly ONE peer match; (None, candidates) on
    ambiguity; (None, []) when unknown. Accepts the explicit 'host:name' form to break ties.
    Gossiped duplicates are folded BEFORE the ambiguity count: a session on a directly-peered box
    also arrives via a hub's gossip (under the hub's nickname for that box), and counting both read
    as two sessions — a false ambiguity — or, picked, would relay mail through the hub a direct
    link already covers (the user 2026-08-12). Same session seen from two hubs folds too (one id,
    one candidate); the direct row always wins."""
    want_host = None
    if ":" in to:
        want_host, to = to.split(":", 1)
    direct_bus = _direct_bus_ids()
    hits, seen_ids = [], {}
    for host, st in PEER_STATE.items():
        if want_host and host != want_host:
            continue
        for a in st.get("presence") or []:
            if a.get("name") != to or _via_duplicate(a, direct_bus):
                continue
            sid = a.get("id") or ""
            if sid and sid in seen_ids:                # one session, two gossip paths → one candidate;
                if a.get("via") and not hits[seen_ids[sid]][1].get("via"):
                    continue                           # …and a direct row beats a relayed one
                if not a.get("via") and hits[seen_ids[sid]][1].get("via"):
                    hits[seen_ids[sid]] = (host, a)
                    continue
                continue
            if sid:
                seen_ids[sid] = len(hits)
            hits.append((host, a))
    if len(hits) == 1:
        return hits[0]
    return None, hits

def _peer_http(port, payload, token=""):
    """Dial a peer bus through the kernel's -L forward. `token` is the PEER machine's serve token
    (?token= — the dialed bus validates against its own 0600 file); our X-Romp-Token would mean
    nothing over there."""
    path = "/peer-exchange" + (("?token=" + urllib.parse.quote(token)) if token else "")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=EXCHANGE_WAIT + 10) as r:
        return json.loads(r.read().decode() or "{}")

def _peer_loop(host):
    """One dialer per up peer: exchange, fold the response in, repeat — the dialed side's long-poll
    sets the pace, so a healthy loop is one parked request, not a poll. Exponential backoff (capped
    30s) on connection errors only; exits when the kernel marks the peer down or the flag drops."""
    fails = 0
    while peers_on():
        p = PEERS.get(host)
        if not p or not p.get("up"):
            break
        req = build_exchange_request(host, wait=True)
        try:
            resp = _peer_http(p["port"], req, p.get("token") or "")
        except urllib.error.HTTPError as e:
            if e.code == 409:
                st = PEER_STATE.setdefault(host, {})
                if st.get("drift") != "proto":
                    st["drift"] = "proto"
                    _log("peer %s: protocol drift — update romp on one side" % host)
                _peer_wake(host).clear()
                _peer_wake(host).wait(60)
                continue
            body = ""
            try:
                body = " ".join((e.read() or b"").decode("utf-8", "replace").split())[:200]
            except Exception:
                pass
            st = PEER_STATE.setdefault(host, {})
            if st.get("refused") != (e.code, body):      # each DISTINCT refusal once, not per retry —
                st["refused"] = (e.code, body)           # a 4xx (e.g. the unsafe-host gate) otherwise
                _log("peer %s: exchange refused (HTTP %s) %s" % (host, e.code, body))   # retries silently forever
            fails += 1
            _peer_wake(host).clear()
            _peer_wake(host).wait(min(30, 2 ** min(fails, 5)))
            continue
        except Exception:
            fails += 1
            _peer_wake(host).clear()
            _peer_wake(host).wait(min(30, 2 ** min(fails, 5)))
            continue
        fails = 0
        try:
            peer_exchange_apply(host, req, resp)
        except Exception as e:
            _log("peer %s: apply failed: %s" % (host, e))
    with _peer_lock:
        _peer_threads.pop(host, None)

def _peer_threads_reconcile(host):
    """Called on every kernel notify: an UP peer gets a dialer loop if none runs; a DOWN one gets its
    wake poked so the loop notices and exits."""
    up = bool(PEERS.get(host, {}).get("up"))
    with _peer_lock:
        t = _peer_threads.get(host)
        if up and (t is None or not t.is_alive()):
            t = threading.Thread(target=_peer_loop, args=(host,), name="peer:%s" % host, daemon=True)
            _peer_threads[host] = t
            t.start()
    if not up:
        _peer_wake(host).set()

def _seed_peers_from_kernel():
    """A restarted bus starts with an empty peer table (the kernel notifies on TRANSITIONS). Best-effort
    seed from the kernel's /tunnels so peering resumes without waiting for the next transition."""
    try:
        req = urllib.request.Request(KERNEL_BASE + "/tunnels", headers={"X-Romp-Token": SERVE_TOKEN})
        with urllib.request.urlopen(req, timeout=3) as r:
            payload = json.loads(r.read().decode() or "{}") or {}
        rows = payload.get("tunnels") or []
        for row in rows:
            port = row.get("busPort")
            if row.get("host") and isinstance(port, int) and port:
                peer_update({"host": row["host"], "port": port, "up": row.get("status") == "up",
                             "token": row.get("token") or "",   # the peer's serve token — its bus is gated too
                             "trust": row.get("trust") or "directed"})   # per-host trust for the inbound gate
        # Origin-only trust heals on restart too: the kernel's remembered-hosts list (`known` in the
        # same payload) carries the tier for every UNATTACHED host the user has set one on; without
        # this a bus bounce would silently drop a relayed origin back to `directed` (the user
        # 2026-07-25, trust-by-origin).
        attached = {row.get("host") for row in rows}
        for k in payload.get("known") or []:
            if k.get("host") and k["host"] not in attached:
                peer_update({"host": k["host"], "trust": k.get("trust") or "directed",
                             "originOnly": True})
    except Exception:
        pass                                         # no kernel yet → the notify path fills the table

def looks_remote():
    # Heuristic: this shell reached the machine over SSH. Used only for advisory
    # nudges and `romp mail remote` role detection — never to change delivery.
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))

def _unreachable_hint():
    if is_client_only() or looks_remote():
        return "can't reach your laptop's Romp Postal Service bus — is the SSH tunnel up? run: romp mail remote"
    return "can't reach the Romp Postal Service bus (see ~/.local/state/romp/postal/server.log)"

def _remote_nudge():
    # On a remote machine not yet pointed at the laptop's bus, the local bus is
    # isolated; nudge toward `romp mail remote` (advisory, on stderr only).
    # Peer mode (the default): every machine runs its own bus and peering carries
    # cross-host mail, so there is no laptop bus to point at — the nudge is moot.
    if peers_on():
        return
    if looks_remote() and not is_client_only():
        sys.stderr.write("[romp mail] you look like a remote machine on a local-only "
                         "Romp Postal Service — run `romp mail remote` to reach your laptop's sessions.\n")

def ensure():
    """Make sure the bus is reachable. On a designated client-only host (remote),
    rely on the ssh tunnel rather than starting a local bus."""
    if ping():
        return True
    if is_client_only():
        return ping()
    STATE.mkdir(parents=True, exist_ok=True)
    logf = open(LOG, "a")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"],
                     stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):           # ~4s
        if ping():
            return True
        time.sleep(0.1)
    return ping()

def restart():
    """Force a FRESH bus process so 'restart everything' (`romp refresh`) actually includes the bus, not
    just the kernels (the user 2026-06-29). The bus is a port-keyed singleton, so `ensure` alone is a no-op
    while the old one answers; SIGTERM the running pid, wait for the port to free, then re-ensure. Pending
    mail lives in the maildir, so the fresh bus just re-delivers it. On a client-only host the real bus is
    remote — don't kill anything, just re-ensure the tunnel."""
    if not is_client_only():
        try:
            pid = int(PIDFILE.read_text().strip())
        except Exception:
            pid = 0
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception as e:
                _log("restart: could not signal bus pid %d (%s)" % (pid, e))
            for _ in range(50):       # ~5s for the old bus to exit + release the port so ensure() can bind
                if not ping():
                    break
                time.sleep(0.1)
    return ensure()

def _heartbeat(sid, name):
    if sid:
        try:
            _http("POST", "/heartbeat", {"id": sid, "name": name or "?"})
        except Exception:
            pass

def _heartbeat_loop():
    """Keep THIS session present to the bus while it's alive, so an IDLE session that hasn't touched a postal
    tool is still addressable. This is essential for a REMOTE (federated) session: it appears to the laptop's
    bus ONLY via heartbeats over the -R tunnel (a local session is already visible through the kernel's
    /sessions). Cadence well under HEARTBEAT_TTL. The bus ignores heartbeats from LOCAL sids, so this costs a
    local session nothing and is the presence mechanism for remote ones. Runs from the stdio MCP server, which
    lives exactly as long as the Claude session."""
    while True:
        try:
            sid = my_id()
            if sid:
                _heartbeat(sid, my_name())
        except Exception:
            pass
        time.sleep(max(15, HEARTBEAT_TTL // 3))

# ───────────────────────── stdio MCP server ─────────────────────────

# Server-level instructions, surfaced to the model by MCP clients at initialize.
# This is the self-contained copy of the messaging norms: it ships with the
# software, so sessions get them even without any global CLAUDE.md.
MCP_INSTRUCTIONS = """\
Messaging peer romp sessions. A peer shares none of your context, only the bytes you send.

Message a peer only for something substantive: a question, information they need, or a result worth sharing. A message wakes the recipient and costs it a turn, so never send just to acknowledge, and stop once the exchange is done.

Write so the recipient can act from your first line:
- Declare the message kind via the required `kind` parameter: delegate (the recipient owns this now), coordinate (aligning/heads-up, reply optional), or question (reply required).
- First sentence is the whole point (the ask or conclusion), not how you got there.
- Name things exactly: files by path, sessions by name. Mark verified vs. suspected, and whose ask it is.
- End with the reply you need, or that none is. One point per message.

Before editing a shared repo, run list_agents and read peers' branches + working-notes (overlap only collides on the SAME branch), and publish yours with set_working. Resolve ownership by reading that state, never by messaging "do you still own this?": an idle peer's note may be stale, and a peer with no note holds nothing. Declare what you own in your first line. Never wake an idle session just to coordinate.

Addressing is live-only: you can message only currently-live sessions (list_agents). Dead names error, with no parked mail or reviving. A session's stable id (the uuid in list_agents) also works as the recipient — rename-proof, unique by construction.

A name is not guaranteed unique. When more than one live session answers to it the send is refused and the candidates are listed as `host:name`: pick one and resend rather than assuming the first. Your OWN name is refused outright, because a message there lands in your own inbox looking exactly like a reply from someone else. Your row in list_agents is the one marked `(you)`.

An isolation refusal is FINAL. A mailbox toggled off is a boundary the user drew: if send_message refuses for isolation, do NOT reroute the content through any other door (the kernel's /send route, tmux keystrokes, shared files, another peer as relay). Report the refusal to the user and stop — only they lift the isolation.

Claude Code ships its own cross-session messaging (SendMessage / ListAgents). For peer romp sessions, use these postal tools instead: postal mail declares a kind, is tracked until answered, respects the user's per-host trust boundaries, and is visible to them; a native cross-session send has none of that, so it is invisible to the user and unaccountable. Native SendMessage remains the right tool for your own subagents and teammates inside this session — just not for peer sessions.
"""

MCP_TOOLS = [
    {"name": "send_message",
     "description": "Message a live romp session by name; it arrives at the end of the recipient's current turn. They share none of your context, so put the whole point in your first sentence. Live-only (see list_agents).",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "recipient romp session name"},
                                    "body": {"type": "string", "description": "message text"},
                                    "kind": {"type": "string", "enum": ["delegate", "coordinate", "question"],
                                             "description": "what this message does: delegate = the recipient owns the work now; coordinate = aligning or a heads-up, reply optional; question = you need an answer"},
                                    "tracked": {"type": "boolean",
                                                "description": "delegate only: a report-back handoff — the work stays tracked under YOU as the one view, with the recipient's live progress; their copy files as its satellite. Omit for a plain handoff the recipient owns outright."}},
                     "required": ["to", "body", "kind"]}},
    {"name": "check_inbox",
     "description": "Read and clear any messages other romp sessions have sent you. Messages are also delivered automatically at the end of each turn, so you rarely need to call this.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_agents",
     "description": "List live romp sessions you can message (yours marked), each with its git branch and working-note. Check before editing shared files to avoid collisions; discount a note flagged '(idle now, claim may be stale)' and never wake an idle peer to ask if it still owns a file.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_working",
     "description": "Publish what you're working on (files/surface) so peers steer clear; your branch shows automatically. Empty text clears it (romp also auto-clears once your work is done and the session idles).",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string", "description": "short note, e.g. 'editing scripts/romp-postal + tmux.conf'"}}}},
    {"name": "check_sent",
     "description": "See your recently sent messages and whether each was read/acted on by the recipient yet, or is still pending — instead of asking 'did you get it?'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "recall_message",
     "description": "Unsend a still-unread (queued) message when the ask went moot. Give 'to' to recall your unread message(s) to them, or add 'id' (from check_sent) for one. Only your own unread messages; anything already read is gone.",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "recipient session name (or UUID) whose queued message(s) from you to cancel"},
                                    "id": {"type": "string", "description": "optional specific message id (from check_sent) to recall just that one"}}}},
]

def _mcp_call(name, args):
    me, mid = my_name(), my_id()
    _heartbeat(mid, me)
    if name == "send_message":
        to, body = args.get("to", ""), args.get("body", "")
        kind = str(args.get("kind", "")).strip().lower()
        if not to or not body:
            return "Need both 'to' and 'body'.", True
        if kind not in ("delegate", "coordinate", "question"):
            return ("Need 'kind': one of delegate (the recipient owns the work now), "
                    "coordinate (aligning/heads-up), or question (you need an answer).", True)
        if not mid:
            # the bus would refuse this anyway (anonymous mail arrives "from unknown"); say it
            # HERE with the actionable half — the sender's own identity is what's broken
            return ("Cannot send: this session's own identity did not resolve (no session id), so "
                    "the mail would arrive anonymously and the recipient could not place or answer "
                    "it. This is a session-identity bug worth surfacing to the user.", True)
        tracked = bool(args.get("tracked")) and kind == "delegate"
        try:
            payload = {"to": to, "from": me or "unknown", "from_id": mid, "body": body, "kind": kind}
            if tracked:
                payload["tracked"] = True
            resp = _http("POST", "/send", payload)
            # "Delivered" has to MEAN delivered. A cross-host send is only relaying (or parked for
            # an unreachable host, or held for the human on the far side), and the bus says so in
            # `note` — which this dropped on the floor, so every one of those read as delivered and
            # the sender had no way to tell. cli_send has echoed the note since 2026-07-27; this is
            # the same honesty on the tool surface.
            note = (resp or {}).get("note")
            if note:
                return "Message to '%s': %s" % (to, note), False
            # Echo what the DECLARATION did, not just that bytes moved (the user 2026-07-26): a question
            # records the SENDER as waiting on the recipient — a real hold that a mis-declared
            # kind creates by accident (a "question" whose prose said no reply was needed parked its
            # sender for a day). Reading the cost back lets the sender self-correct on the spot, while
            # recall_message still works.
            if kind == "question":
                return ("Delivered to '%s' as a question — you are now recorded as waiting on their "
                        "reply until they answer. If you don't actually need a reply, recall this "
                        "message and resend it as coordinate." % to, False)
            if kind == "delegate" and tracked:
                return ("Delivered to '%s' as a tracked handoff — they do the work, and it stays "
                        "tracked under you as the one view with their live progress. You are NOT "
                        "recorded as waiting; their completion checks it off." % to, False)
            if kind == "delegate":
                return ("Delivered to '%s' as a handoff — they own it now; you are NOT recorded as "
                        "waiting (the user 2026-08-15: ownership transferred is not a dependency). "
                        "If you genuinely need their report before you can proceed, send a question "
                        "instead." % to, False)
            return "Delivered to '%s'." % to, False
        except BusError as e:
            return str(e), True
    if name == "check_inbox":
        if not mid:
            return "Not inside a romp session.", True
        msgs = _http("GET", "/inbox?id=%s" % urllib.parse.quote(mid)).get("messages", [])
        return (format_inbox(msgs, mid) or "No new messages."), False
    if name == "list_agents":
        res = _http("GET", "/agents?me=%s" % urllib.parse.quote(me or ""))
        return format_agents(res.get("agents", []), me, mid), False
    if name == "set_working":
        if not mid:
            return "Not inside a romp session.", True
        text = args.get("text", "")
        _publish_working(mid, text)        # backend-agnostic kernel store (POST /working), not the @romp-working var
        return ("Cleared your 'working on' note." if not text.strip()
                else "Published — others see: working on '%s'." % text), False
    if name == "check_sent":
        if not mid:
            return "Not inside a romp session.", True
        recs = _http("GET", "/sent?id=%s" % urllib.parse.quote(mid)).get("sent", [])
        return format_receipts(recs), False
    if name == "recall_message":
        to, rid = args.get("to", ""), args.get("id", "")
        if not to and not rid:
            return "Give 'to' (the recipient) and/or 'id' to recall.", True
        if not mid:
            return "Not inside a romp session.", True
        removed = _http("POST", "/recall", {"from_id": mid, "to": to, "id": rid}).get("removed", [])
        if not removed:
            return ("Nothing to recall — no unread message from you matched "
                    "(it was already read, or nothing's queued there).", False)
        lines = ["Recalled %d message(s) before they were read:" % len(removed)]
        for r in removed:
            lines.append("  ✕ to %s: %s" % (r["to"], r["body"]))
        return "\n".join(lines), False
    return "Unknown tool: %s" % name, True

def mcp():
    """Hand-rolled stdio MCP server (newline-delimited JSON-RPC). stdout carries
    ONLY protocol messages; everything else goes to stderr."""
    ensure()
    # Heartbeat presence while this session lives, so an idle REMOTE (federated) session stays addressable
    # over the -R tunnel even before it uses a postal tool. No-op for local sids (the bus ignores those).
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    out = sys.stdout

    def reply(obj):
        out.write(json.dumps(obj) + "\n")
        out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid_ = msg.get("id")
        method = msg.get("method")
        try:
            if method == "initialize":
                pv = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
                reply({"jsonrpc": "2.0", "id": mid_, "result": {
                    "protocolVersion": pv,
                    "capabilities": {"tools": {}},
                    "instructions": MCP_INSTRUCTIONS,
                    "serverInfo": {"name": "romp-postal-service", "version": "1.0"}}})
            elif method == "notifications/initialized":
                pass   # notification: no response
            elif method == "ping":
                reply({"jsonrpc": "2.0", "id": mid_, "result": {}})
            elif method == "tools/list":
                reply({"jsonrpc": "2.0", "id": mid_, "result": {"tools": MCP_TOOLS}})
            elif method == "tools/call":
                params = msg.get("params") or {}
                text, is_err = _mcp_call(params.get("name", ""), params.get("arguments") or {})
                reply({"jsonrpc": "2.0", "id": mid_, "result": {
                    "content": [{"type": "text", "text": text}], "isError": is_err}})
            elif mid_ is not None:
                reply({"jsonrpc": "2.0", "id": mid_, "error": {"code": -32601, "message": "method not found: %s" % method}})
        except Exception as e:
            _log("mcp error on %s: %s" % (method, e))
            if mid_ is not None:
                reply({"jsonrpc": "2.0", "id": mid_, "result": {
                    "content": [{"type": "text", "text": "internal error: %s" % e}], "isError": True}})
    return 0

# ───────────────────────── CLI client modes ─────────────────────────

def cli_send(argv):
    kind = frm_label = ""
    tracked = False
    while argv and argv[0] in ("--kind", "--from", "--tracked"):
        if argv[0] == "--tracked":
            tracked = True
            argv = argv[1:]
        elif argv[0] == "--kind":
            kind = (argv[1].strip().lower() if len(argv) > 1 else "")
            argv = argv[2:]
            if kind not in ("delegate", "coordinate", "question"):
                sys.stderr.write("[romp mail] --kind must be delegate, coordinate, or question\n"); return 2
        else:
            # --from <label>: an EXPLICIT identity for a non-session caller (a launchd/cron script, a
            # bare shell — 2026-08-19, after the anonymous-send refusal broke a morning script that
            # had been mailing as "unknown"). Not anonymity restored: the mail arrives placeable,
            # from <label> with a stable synthetic id, and recipients can tell scripts apart.
            frm_label = (argv[1].strip() if len(argv) > 1 else "")
            argv = argv[2:]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", frm_label or ""):
                sys.stderr.write("[romp mail] --from must be one word (letters/digits/dash/underscore, <=32 chars)\n"); return 2
    if tracked and kind != "delegate":
        sys.stderr.write("[romp mail] --tracked is for delegations only (add --kind delegate)\n"); return 2
    if len(argv) < 2:
        sys.stderr.write('usage: romp mail send [--kind delegate|coordinate|question] [--tracked] [--from <label>] <session> <text>\n'); return 2
    to, body = argv[0], " ".join(argv[1:])
    if not body.strip():
        sys.stderr.write("[romp mail] refusing to send an empty message\n"); return 2
    if not ensure():
        sys.stderr.write("[romp mail] %s\n" % _unreachable_hint()); return 1
    me, mid = my_name(), my_id()
    if frm_label:
        me, mid = frm_label, "ext:" + frm_label
    if not mid:
        # the bus refuses anonymous sends; say it here with BOTH actionable halves: a broken
        # session identity is a bug to surface, and a deliberate non-session caller has a door
        sys.stderr.write("[romp mail] cannot send: no session identity resolved, and anonymous "
                         "mail is refused (it arrives as an unplaceable ghost). Inside a romp "
                         "session, surface this to the user as a session-identity bug. From a "
                         "script or bare shell, pass --from <label> to send under an explicit "
                         "name.\n")
        return 1
    try:
        payload = {"to": to, "from": me or "unknown", "from_id": mid, "body": body, "kind": kind}
        if tracked:
            payload["tracked"] = True
        resp = _http("POST", "/send", payload)
    except BusError as e:
        sys.stderr.write("[romp mail] %s\n" % e); return 1
    # Echo what actually happened, not a blanket "delivered": a cross-host send is only RELAYING (or
    # parked for an unreachable host), and the receiving bus may still hold it for the human's
    # approval — the 2026-07-27 shakedown had this print "delivered" for a quarantined message.
    note = (resp or {}).get("note")
    print("[romp mail] %s" % (note or ("delivered to '%s'" % to)))
    return 0

def cli_inbox(peek=False):
    if not ensure():
        sys.stderr.write("[romp mail] %s\n" % _unreachable_hint()); return 1
    mid = my_id()
    if not mid:
        sys.stderr.write("[romp mail] can't tell which session this is (are you in a romp session?)\n"); return 1
    try:
        res = _http("GET", "/inbox?id=%s&peek=%d" % (urllib.parse.quote(mid), 1 if peek else 0))
    except BusError as e:
        sys.stderr.write("[romp mail] %s\n" % e); return 1
    text = format_inbox(res.get("messages", []), mid)
    if text:
        print(text)
    return 0

def cli_agents():
    if not ensure():
        sys.stderr.write("[romp mail] %s\n" % _unreachable_hint()); return 1
    me, mid = my_name(), my_id()
    try:
        res = _http("GET", "/agents?me=%s" % urllib.parse.quote(me or ""))
    except BusError as e:
        sys.stderr.write("[romp mail] %s\n" % e); return 1
    print(format_agents(res.get("agents", []), me, mid))
    return 0

def cli_working(argv):
    sid = my_id()
    if not sid:
        sys.stderr.write("[romp mail] not in a romp session\n"); return 1
    text = " ".join(argv)
    _publish_working(sid, text)        # backend-agnostic kernel store (POST /working), not the @romp-working var
    print("[romp mail] working: %s" % (text or "(cleared)"))
    return 0

def cli_sent():
    if not ensure():
        sys.stderr.write("[romp mail] %s\n" % _unreachable_hint()); return 1
    mid = my_id()
    if not mid:
        sys.stderr.write("[romp mail] not in a romp session\n"); return 1
    try:
        recs = _http("GET", "/sent?id=%s" % urllib.parse.quote(mid)).get("sent", [])
    except BusError as e:
        sys.stderr.write("[romp mail] %s\n" % e); return 1
    print(format_receipts(recs))
    return 0

def cli_recall(argv):
    if not argv:
        sys.stderr.write("usage: romp mail recall <to> [id]\n"); return 2
    to, rid = argv[0], (argv[1] if len(argv) > 1 else "")
    if not ensure():
        sys.stderr.write("[romp mail] %s\n" % _unreachable_hint()); return 1
    try:
        removed = _http("POST", "/recall", {"from_id": my_id() or "", "to": to, "id": rid}).get("removed", [])
    except BusError as e:
        sys.stderr.write("[romp mail] %s\n" % e); return 1
    if not removed:
        print("[romp mail] nothing to recall (already read, or none queued to '%s')" % to)
    else:
        print("[romp mail] recalled %d message(s) to '%s' before they were read" % (len(removed), to))
    return 0

def cli_wake(argv):
    # For the SessionStart revive hook: ask the bus to force-deliver pending mail
    # once this reviving session's prompt is live. Non-blocking (bus does the wait).
    sid = None
    if "--id" in argv:
        i = argv.index("--id")
        sid = argv[i + 1] if i + 1 < len(argv) else None
    sid = sid or my_id()
    if not sid or not ensure():
        return 0
    try:
        _http("POST", "/wake", {"id": sid})
    except Exception:
        pass
    return 0

def _argval(argv, flag):
    if flag in argv:
        i = argv.index(flag)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None

def cli_picker_check(argv):
    """Backgrounded by `romp` on RESUME (romp-postal-service picker-check --name N --id S). Claude's "resume
    as-is / from summary" PICKER blocks before the session starts, so NO Claude hook fires while it's up — an
    external watcher is the only way to surface it. Routed through the kernel (POST /picker-check): the kernel
    polls the pane + @claude-state for up to PICKER_GRACE and, if the picker is confirmed up, marks
    @claude-state=picker + appends a 'picker' state event so the feed shows NEEDS INPUT. The bus never shells tmux."""
    sid = _argval(argv, "--id")
    if not sid:
        return 0
    _kernel_post("/picker-check", {"id": sid}, timeout=PICKER_GRACE + 5)
    return 0

def cli_drain(argv):
    # For the Stop hook. --id is authoritative (from Claude's hook payload).
    sid = None
    if "--id" in argv:
        i = argv.index("--id")
        sid = argv[i + 1] if i + 1 < len(argv) else None
    sid = sid or my_id()
    if not sid:
        return 0
    if not ensure():
        return 0
    try:
        res = _http("GET", "/drain?id=%s" % urllib.parse.quote(sid))
    except Exception:
        return 0
    text = format_inbox(res.get("messages", []), sid)
    if text:
        print(text)
    return 0

def setup_remote(force=False):
    """Point THIS machine at the laptop's Romp Postal Service over an SSH reverse
    tunnel: configure the client side automatically, then guide + verify the one
    manual step (the tunnel, which can only be opened from the laptop)."""
    if not force and not looks_remote():
        print("This looks like your Romp Postal Service host (no SSH session detected);")
        print("the bus runs here automatically, so there's nothing to set up.")
        print("")
        print("On a REMOTE machine, run `romp mail remote` there instead.")
        print("To make every SSH hop auto-tunnel, add to your ~/.ssh/config:")
        print("    Host <remote-host>      # or: Host *")
        print("        RemoteForward %d 127.0.0.1:%d" % (PORT, PORT))
        print("(re-run with --force if this really is a remote machine.)")
        return 0
    import signal as _sig
    if PIDFILE.exists():
        try:
            os.kill(int(PIDFILE.read_text().strip()), _sig.SIGTERM)
            print("Stopped the local-only bus that was running here (frees the port for the tunnel).")
        except Exception:
            pass
    CLIENT_ONLY.parent.mkdir(parents=True, exist_ok=True)
    CLIENT_ONLY.touch()
    print("Configured this machine as a Romp Postal Service client (it won't run its own bus).")
    if ping():
        print("Already connected to your laptop's bus at %s." % BASE)
        return cli_agents()
    host = socket.gethostname()
    print("")
    print("Now open a reverse tunnel FROM YOUR LAPTOP so this machine can reach the bus:")
    print("")
    print("  - Already SSH'd in? In that terminal press Enter, then type:  ~C")
    print("      at the `ssh>` prompt enter:  -R %d:127.0.0.1:%d" % (PORT, PORT))
    print("      (the ~ escape only works right after a newline)")
    print("  - Or reconnect with:  ssh -R %d:127.0.0.1:%d %s" % (PORT, PORT, host))
    print("  - Permanent (never again): on your LAPTOP add to ~/.ssh/config:")
    print("        Host %s" % host)
    print("            RemoteForward %d 127.0.0.1:%d" % (PORT, PORT))
    print("")
    waits = int(os.environ.get("ROMP_POSTAL_REMOTE_WAIT", "120"))
    print("Waiting for the tunnel... (Ctrl-C to stop; you can re-run anytime)")
    for _ in range(waits):
        if ping():
            print("\nConnected! You can message now:")
            return cli_agents()
        time.sleep(0.5)
    print("\nStill not connected. Open the tunnel above, then run `romp mail remote` again.")
    return 1

USAGE = """romp-postal-service — the Romp Postal Service
  romp mail send <session> <text>   message a live romp session
  romp mail inbox                   read + clear messages sent to this session
  romp mail peek                    show messages without clearing them
  romp mail agents                  list romp sessions (with branch + what they're working on)
  romp mail working <text>          publish what you're working on (empty to clear)
  romp mail sent                    show your sent messages + whether each was read
  romp mail recall <to> [id]        unsend an unread message you sent to <to>
  romp mail remote                  connect this (remote) machine to your laptop's bus
(internal: serve | ensure | restart | mcp | drain --id <id> | wake --id <id> | picker-check --name <n> --id <id>)"""

def main(argv):
    if not argv:
        print(USAGE); return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "serve":   return serve()
    if cmd == "ensure":  return 0 if ensure() else 1
    if cmd == "restart": return 0 if restart() else 1   # `romp refresh` bounces the bus too, not just the kernels
    if cmd == "mcp":     return mcp()
    if cmd == "remote":  return setup_remote(force=("--force" in rest or "-f" in rest))
    if cmd == "drain":   return cli_drain(rest)
    if cmd == "wake":    return cli_wake(rest)        # SessionStart revive hook: force-deliver on resume
    if cmd == "picker-check": return cli_picker_check(rest)   # romp resume: surface a session stuck on the resume picker
    if cmd == "prune":   _kernel_post("/reconcile-peers", {}); return 0   # tmux session-closed + after-rename hooks → kernel reconciles the chips
    if cmd == "sweep":   _sweep_orphans(); return 0      # bounce orphaned mail (also runs in the monitor)
    if cmd == "retry":   _retry_pending(); return 0      # re-deliver deferred/stranded mail (also runs every RETRY_INTERVAL)
    if cmd in ("-h", "--help", "help"):
        print(USAGE); return 0
    if cmd == "send":              rc = cli_send(rest)
    elif cmd in ("inbox", "recv"): rc = cli_inbox(peek=False)
    elif cmd == "peek":            rc = cli_inbox(peek=True)
    elif cmd in ("agents", "ls"):  rc = cli_agents()
    elif cmd == "working":         rc = cli_working(rest)
    elif cmd == "sent":            rc = cli_sent()
    elif cmd == "recall":          rc = cli_recall(rest)
    else:
        sys.stderr.write("unknown command: %s\n%s\n" % (cmd, USAGE)); return 2
    _remote_nudge()
    return rc

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
