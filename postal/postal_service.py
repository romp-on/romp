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
import errno
import fcntl
import hashlib
import hmac
import http.client
import itertools
import json
import os
import random
import re
import shutil
import signal
import socket
import stat
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
             or Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "romp") / "postal"
# `or`, not a .get default, here and at NAMES_DIR: an empty XDG_STATE_HOME is unset (the note at
# kernel/event_model.py's STATE).
MAILROOT = STATE / "mail"
MAILPENDING = STATE / "mail-pending"   # touch <sid> here IFF that session has unread mail in new/
WARNED = STATE / "warned-undelivered"  # marker per msg-id we've already warned a sender is STILL UNDELIVERED (one-time)
LOG = STATE / "server.log"
PIDFILE = STATE / "server.pid"
NAMES_DIR = Path(os.environ.get("ROMP_STATE_DIR")
                 or Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "romp") / "names"
TLDIR = STATE.parent / "timeline"     # append-only logs for the timeline view (messages.jsonl)
SESSION_FLAGS = STATE.parent / "session-flags.json"   # the kernel's per-session view flags {sid:{flag:true}}; we honour postalServiceOff (legacy: postalOff)
USER_TODOS_SWITCH = STATE.parent / "user-todos-enabled.json"   # the kernel's per-install user-todos switch {"enabled": bool, "gt": ms} (kernel USER_TODOS_SWITCH_FILE); NOT user-todos.json, which is the todo STORE


# ── serve-token gate (Jupyter's model; the same 0600 file the kernel mints) ─────
# Loopback is reachable by EVERY local user on the machine, so the bus — which can wake sessions
# and inject mail straight into their prompts — requires the machine's serve token on every
# request except the /ping liveness probe. The 0600 file is the same-user trust boundary; kernel
# and bus share it (whichever daemon starts first mints it, identical logic). A peer bus dialing
# through an ssh forward authorizes with the DIALED machine's token (?token=), which rides the
# kernel's /peer notifies only (never /tunnels rows, which a page reads too; since 2026-09-08 a
# restarted bus gets every token re-notified, see peers_snapshot).
#
# _serve_token_read_or_mint is a COPY of the kernel's (kernel.py, same name; KEEP IN SYNC): the bus
# imports nothing from kernel/ by design, and the two daemons boot together, so they must agree on
# the whole contract, not just the path. Why it is shaped this way is in the kernel's docstring; in
# one line: FileNotFoundError is the only mint trigger, the mint lands by rename of a 0600 temp,
# and it all happens under serve-token.lock. A fault raises RuntimeError, which at import refuses to
# start the bus (or a session's MCP process): the old loader minted its OWN token on any read fault
# and every request it then made was a silent 403. The refusal is not one message: the bus is started
# again by whatever needs it (a kernel boot's _ensure_postal_bus, a session's MCP process running
# `ensure`), and the kernel's own copy of this refusal repeats on bin/romp-manager's respawn backoff
# (a traceback in manager.log every 10 s at the cap), so both repeat until the file is repaired and
# stop by themselves once it is, with the token every client holds untouched throughout (review
# find, 2026-09-08).
def _serve_token_read_or_mint(f, who):
    lock = f.with_name(f.name + ".lock")

    def fault(path, what, e, fix="Make the file yours and mode 0600 (or set ROMP_SERVE_TOKEN)"):
        code = getattr(e, "errno", None)
        why = ("%s, errno %s" % (errno.errorcode.get(code, type(e).__name__), code) if code is not None
               else type(e).__name__)
        raise RuntimeError(
            "%s: cannot %s (%s). romp did NOT replace the serve token, so every client holding it "
            "stays valid. %s, then start again." % (path, what, why, fix)) from e

    def read():
        try:
            if stat.S_ISLNK(os.lstat(f).st_mode):
                # refused BEFORE anything reads or chmods through it: both land on the target, some
                # other file (review find, 2026-09-08). lstat sees a dangling link too; read_text
                # would call that absent and mint over it.
                fault(f, "use it: the token path is a symlink, and romp reads or tightens no token "
                         "through a link", OSError(errno.ELOOP, os.strerror(errno.ELOOP)),
                      fix="Replace the link with a regular file that is yours and mode 0600 (or set "
                          "ROMP_SERVE_TOKEN)")
            return f.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:   # a token that is not text is a fault too, never a mint
            fault(f, "read it", e)

    def mode():
        try:
            return stat.S_IMODE(os.lstat(f).st_mode)   # lstat, like read(): the file's own mode, never a link target's
        except OSError as e:
            fault(f, "stat it", e)

    def loose(m):
        return m & ~0o600                    # any bit outside rw-------: group, other, execute, set-id, sticky

    def mint(why):
        if why:
            print("[%s] serve token %s: %s; minting a fresh one" % (who, f, why), file=sys.stderr)
        v = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
        tmp = f.with_name("%s.%d.tmp" % (f.name, os.getpid()))
        fd = None
        try:
            for stale in f.parent.glob(f.name + ".*.tmp"):
                try:
                    os.unlink(stale)         # under the lock, so any temp here is a crashed earlier attempt, any pid's
                except FileNotFoundError:
                    pass
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            data = v.encode()
            n = os.write(fd, data)
            if n != len(data):
                raise OSError(errno.EIO, "short write, %d of %d bytes" % (n, len(data)))
            os.fsync(fd)
            os.close(fd)
            fd = None
            os.replace(str(tmp), str(f))
        except OSError as e:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            fault(f, "mint it (via %s)" % tmp.name, e)
        return v

    lfd = None
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        lfd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # another starter (the kernel and the bus boot together) is reading or minting: say so
            # once, then wait for it. The blocking take is the right wait; it was just silent, and a
            # starter stuck behind a wedged holder looked hung (review find, 2026-09-08).
            print("[%s] serve token: waiting for the holder of %s (another starter is reading or "
                  "minting it)" % (who, lock), file=sys.stderr)
            fcntl.flock(lfd, fcntl.LOCK_EX)
    except OSError as e:
        if lfd is not None:
            try:
                os.close(lfd)
            except OSError:
                pass
        v = read()                           # a read fault is its own RuntimeError
        m = mode() if v else None
        if v and not loose(m):
            print("[%s] serve token: could not lock %s (%s); using the existing token as is (mode "
                  "%04o, nothing to tighten)" % (who, lock, e, m), file=sys.stderr)
            return v
        # the refusal names the LOCK, the fault, and what the lock was needed for. It used to send the
        # operator to make the token file 0600, also when no such file existed (review find, 2026-09-08).
        need = ("that minting a token needs; no token file exists to fall back on" if v is None else
                "that minting a token needs; the token file is empty, a torn earlier mint" if not v else
                "that tightening the token file from mode %04o needs" % m)
        fault(lock, "take the lock %s" % need, e,
              fix="Make the lock file yours, or remove it (or set ROMP_SERVE_TOKEN)")
    try:
        v = read()
        if v is None:
            return mint(None)
        if not v:
            return mint("the file is empty, a torn earlier mint that no client can be holding")
        m = mode()
        if loose(m):
            want = m & 0o600                 # strip what is loose, add nothing: 0640 -> 0600, 0444 -> 0400
            try:
                os.chmod(f, want)
            except OSError as e:
                fault(f, "tighten its mode from %04o to %04o" % (m, want), e)
            print("[%s] serve token %s was mode %04o; tightened to %04o" % (who, f, m, want), file=sys.stderr)
        return v
    finally:
        try:
            fcntl.flock(lfd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lfd)


def _load_serve_token():
    t = (os.environ.get("ROMP_SERVE_TOKEN") or "").strip()
    if t:
        return t
    # ~/.local/state/romp/serve-token (STATE is romp/postal): the kernel's file, shared.
    return _serve_token_read_or_mint(STATE.parent / "serve-token", "postal")


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

def _self_identity():
    """(id, name) of THIS session from ONE _self_row() resolution — one GET /sessions, where the
    `my_name(), my_id()` pair a caller used to write cost two (2026-09-06: with about 30 sessions
    beating every 30 s the heartbeat was nearly all of the kernel's GET /sessions traffic, and this
    pair two of the three fetches each beat cost). Both fallbacks kept: no row → the env fsid as the
    id (mail still routes by id) and the names registry for the name (kernel-down fallback; a
    comment-thread session withholds its names entry, which is why the row comes first). (None,
    None) when not in a romp session (no tmux fallback: the bus never shells tmux). Nothing is
    memoized: a resolution that missed (kernel mid-restart) is retried in full by the next call."""
    row = _self_row()
    sid = row["id"] if row else _self_id()
    if row and row.get("name"):
        return sid, row["name"]
    fsid = _self_id()
    if not fsid:
        return sid, None
    try:
        return sid, ((NAMES_DIR / fsid).read_text().split("\t")[0].strip() or None)
    except Exception:
        return sid, None

def my_id():
    return _self_identity()[0]

def my_name():
    # The live agent row first (it tracks renames AND survives transcript forks); the names registry as
    # the kernel-down fallback. A caller that needs BOTH halves calls _self_identity() once instead of
    # this pair — each of these is a full resolution.
    return _self_identity()[1]

# ───────────────────────── maildir store ─────────────────────────

def _iso_now():
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

def _safe_id(s):
    """True iff `s` is safe as a single path component under the mail/names roots.
    Blocks path traversal (`..`, `/`, `\\`, NUL, leading dot, absolute paths) in
    any id/name that arrives over the (unauthenticated) bus. Session ids are
    UUIDs and names are sanitized to [A-Za-z0-9_-] at creation, so both match;
    anything else is a crafted reference (e.g. `../../../etc`) and is rejected.
    Duplicated in the kernel (its _safe_id; tests/test_postal_self_host.py pins
    the two copies identical). fullmatch, never match against `^...$`: `$` also
    matches before ONE trailing newline, so "abc\\n" cleared the rule and
    self_host's override branch declared it verbatim (review find, 2026-09-08)."""
    if not s or len(s) > 128:
        return False
    if "/" in s or "\\" in s or "\x00" in s or s.startswith("."):
        return False
    return bool(_SAFE_ID_RE.fullmatch(s))

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
    # 128 bits from os.urandom, not random.randint(0, 99999) (2026-09-08): five decimal digits gave
    # 100k names per second per process, and deliver()'s publish renamed OVER a standing new/<name>
    # on a collision — somebody's unread mail replaced without a trace. The same id keys the outbox
    # record and the quarantine file, so it has to be unique ACROSS processes and hosts, not merely
    # within one process's second. Still digits, ".", "_", hex and the host, so _safe_id passes it
    # (a DNS label is at most 63 chars; the whole id stays under the 128 cap).
    return f"{int(time.time())}.{os.getpid()}_{os.urandom(16).hex()}.{self_host()}"

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

_TL_FAULT = [False]      # transition-only logging for _tl_append (the _PRESENCE_SERVE_WARNED idiom)

def _tl_append(fname, obj):
    """Append one JSON line to a timeline log. Returns True iff the row LANDED (the OS accepted the
    write), False on any fault — with ONE stderr line per fault episode (and one when it writes
    again), never per call. Never raises.

    The return matters to the writers whose accounting IS the row (2026-09-08): deliver()'s sent
    row, the relay park's sent row, and the outbox terminal rows (relayed / bounced). Before this the
    append was best-effort with no return, so mail was published with no row anybody could see, and
    an outbox record was deleted before its receipt existed. The exec / unexec / recall rows keep
    ignoring the return: each is an annotation on a message that already exists, not the record of
    whether it does."""
    try:
        TLDIR.mkdir(parents=True, exist_ok=True)
        with open(TLDIR / fname, "a") as fh:
            fh.write(json.dumps(obj) + "\n")
        if _TL_FAULT[0]:
            _TL_FAULT[0] = False
            _log("timeline log %s writes again" % fname)
        return True
    except Exception as e:
        if not _TL_FAULT[0]:
            _TL_FAULT[0] = True
            _log("timeline log %s: append failed (%s) — sends are refused until it writes again"
                 % (fname, e))
        return False

class DeliveryNotRecorded(Exception):
    """deliver() refused: nothing reached the recipient, and NO row names the attempt. Either the
    publish was refused — the name already stands in the inbox (a collision), or the filesystem said
    no — so the name was never this message's and nothing is written under it; or the publish landed
    and the sent row could not, and the mail was taken back out of new/ before anyone read it. The
    sender still holds the text and retries. The message text is written for the SENDER's eyes: the
    /send route answers it as the refusal body."""

NOT_RECORDED_TEXT = ("the send was not recorded (the message log could not be written), so it was not "
                     "delivered — nothing is lost; retry")

# The `why` of a terminal `bounced` row that records a REFUSAL — nothing left this machine and no
# return note exists — as opposed to a peer's refusal, which _bounce_apply returns to the sender as
# a note. format_receipts phrases the two apart (2026-09-08): a refusal must not promise a note.
# deliver() itself writes no WHY_NOT_PUBLISHED row: a publish it refuses records nothing (the name
# was never its own — see deliver). The prefix remains for the start sweep's close of a temp whose
# sent row stood open (WHY_STOPPED_BEFORE_PUBLISH).
WHY_NOT_PUBLISHED = "not published: "
WHY_NOT_PARKED = "not parked: the outbox record could not be written"
WHY_OUTBOX_UNREADABLE = "outbox record unreadable, moved aside"
WHY_INBOX_UNREADABLE = "inbox file unreadable, moved aside"    # read_box's move-aside (review find, 2026-09-08)
REFUSAL_WHYS = (WHY_NOT_PUBLISHED, "not parked:", WHY_OUTBOX_UNREADABLE, WHY_INBOX_UNREADABLE)

_DASHBOARD_MISSED = [False]   # transition-only logging for _refused_notice's kernel leg

def _refused_notice(text):
    """A fault the USER should see, not only whoever reads server.log (review find, 2026-09-08): the
    bus has no dashboard surface of its own, so the text goes to the kernel (POST /postal-notice),
    which files it as one bell row under the `refused` kind, the kind every state file the kernel
    could not read or write already wears, so a mute on machine-sync notices never hides it. The
    stderr line is written here too: a caller says a fault once, in one place, and both surfaces
    carry it. Best-effort toward the kernel: one that is down, or too old to know the route, leaves
    the log line and the ledger row standing, and the miss is said once per episode."""
    _log(text)
    r = _kernel_post("/postal-notice", {"text": text})
    if r is not None and r.get("ok"):
        if _DASHBOARD_MISSED[0]:
            _DASHBOARD_MISSED[0] = False
            _log("the dashboard hears the mail service's notices again")
        return
    if not _DASHBOARD_MISSED[0]:
        _DASHBOARD_MISSED[0] = True
        _log("the dashboard could not be told (the kernel %s); the log and the ledger still carry it"
             % ("refused the notice" if r else "did not answer"))

_REFUSAL_SAID = {}       # site -> True while that site's refusal episode is open (said once, not per pass)

def _say_refused_once(site, what, exc):
    """One _log line per refusal EPISODE at a periodic site (the orphan sweep, the stuck-mail warning):
    the pass that met the refusal says it, the passes that meet it again stay quiet, and the first
    pass whose send lands again (_refusal_over) re-arms it."""
    if _REFUSAL_SAID.get(site):
        return
    _REFUSAL_SAID[site] = True
    _log("%s: %s was refused (%s) — kept for the next pass" % (site, what, exc))

def _refusal_over(site):
    _REFUSAL_SAID.pop(site, None)

_LINK_FALLBACK_SAID = [False]

def _publish_new(tmp, dst):
    """Publish a finished temp as new/<name> WITHOUT ever replacing a standing message. rename()
    silently overwrites an existing target, so a name collision destroyed somebody's unread mail
    with no trace (2026-09-08). link() refuses an existing target atomically (FileExistsError) — the
    maildir protocol's own answer to this — and the temp is unlinked after, so the message is
    visible under exactly one name at every instant. A filesystem that refuses hard links falls
    back to an exists-check + rename, said once: that check is not atomic, but a collision there
    needs two writers minting the same 128-bit name in the same instant, so the protection is the
    same in practice. Raises OSError (FileExistsError on a collision); the caller refuses and
    records nothing — the name was never this delivery's (see deliver).

    EVERY link() refusal but a collision takes the fallback (review find, 2026-09-08). The first
    cut named six errnos as "no hard links here" (EPERM, EOPNOTSUPP, ENOTSUP, ENOSYS, EMLINK,
    EXDEV) and re-raised the rest as real faults, but filesystems answer that question in more
    ways than six (EACCES and EINVAL on some network and FUSE mounts), and an errno off the list
    turned EVERY send on such a machine into a refusal. The checked rename is the right answer to
    all of them: a real fault (EIO, ENOSPC) fails the rename the same way, so nothing is ever
    delivered over a fault, and the fallback line names the errno so a fault reads as one."""
    try:
        os.link(tmp, dst)
    except FileExistsError:
        raise
    except OSError as e:
        if not _LINK_FALLBACK_SAID[0]:
            _LINK_FALLBACK_SAID[0] = True
            _log("mail publish: hard links unavailable here (%s) — falling back to a checked rename" % e)
        if dst.exists():
            raise FileExistsError(str(dst))
        # os.rename, not Path.rename: on Python 3.10 pathlib binds os.rename at import, so a fault
        # staged on os.rename never reaches Path.rename there and a real fault would publish over it
        # (review find, 2026-09-08: the fallback test was green on 3.11+ and red on 3.10 for this alone).
        os.rename(tmp, dst)
        return
    try:
        tmp.unlink()
    except OSError as e:
        _log("mail publish: %s is out but its temp could not be removed (%s)" % (dst.name, e))

def _walk_root_record(frm_id):
    """The sending session's kernel-walked root-ask record, fetched at SEND time for a cross-host
    delegate (the user 2026-08-27, T126): the receiving kernel's chain walk rightly refuses
    foreign-kernel hops because the evidence (goal stores + transcripts) lives on THIS machine's
    disk — but at send time that evidence and the outgoing payload share a disk and the host is
    definitionally online, so the origin kernel walks its own chain and the proof rides the wire.
    The bus is stdlib-only by design, so the walk is a kernel HTTP ask (the /redial pattern):
    best-effort, and an unreachable kernel enriches nothing — mail never blocks on enrichment.
    The record is KERNEL-written (never agent prose), which is what earns it the walk-proved trust
    class on the receiving side."""
    r = _kernel_post("/walk-root", {"sid": frm_id}) or {}
    txt = str((r or {}).get("text") or "").strip()
    if not txt:
        return None
    return {"text": txt[:1200], "sid": str(r.get("sid") or frm_id), "host": self_host()}


def deliver(to_id, from_name, from_id, body, park=False, kind="", from_host="",
            relay_mid="", relay_via="", tracked=False, user_ask=None):
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
    ev["from_host"] = from_host or ""                # additive (consumer contract above): "" says LOCAL
    #                                                  outright — written only when set before, which left
    #                                                  a local row and a pre-field relayed row the same shape
    if relay_mid:
        ev["originMid"] = relay_mid                  # the SENDER-side id for relayed mail (2026-08-28,
        #                                              the dead-session round): local delivery mints its
        #                                              own maildir mid, so sender and recipient held
        #                                              DIFFERENT ids for one message and the delegation
        #                                              mirror join never formed — this is the durable
        #                                              join key the courier stamps into origin
    if isinstance(user_ask, dict) and str(user_ask.get("text") or "").strip():
        # the origin kernel's walked root-ask record (T126) — whitelisted copy, additive: the
        # receiving courier reads it off this row (_postal_row) as walk-proved provenance
        ev["userAsk"] = {"text": str(user_ask["text"])[:1200], "sid": str(user_ask.get("sid") or ""),
                         "host": str(user_ask.get("host") or "")}
    # The PUBLISH is the claim on the name, and the row follows it (2026-09-08). The row is the ONE
    # record the sender's receipts, the timeline and the kernel's courier read — and a row is a
    # statement about a NAME. Until link() has granted this delivery the name, the name may be
    # somebody else's: a row-first order under a collision filed the refused message's `sent` row
    # and then the refusal's `bounced` row under the STANDING message's id, and every reader of the
    # ledger saw a delivered message as bounced. link() refuses an existing target atomically, so
    # the publish is the one act that proves the name is ours; a refused publish therefore records
    # nothing (the retry lands under a fresh name with its own row). The sender-side rule "row
    # before park" is not contradicted: that row is the sender's receipt under the sender's OWN mid;
    # this one is the recipient's record of a message that landed. What keeps new/ honest while the
    # bus runs (before 2026-09-08 a best-effort append after the publish left mail in the inbox that
    # nothing else knew about) is the take-back below: a row that cannot land pulls the mail back out
    # before it is read, and the caller answers a retryable refusal while the sender still holds the
    # text. The one gap the take-back cannot reach is a bus killed between the publish and the row:
    # that message stands in new/ with no record until _sweep_unfinished_writes rebuilds its row from
    # the file's headers at the next start (review find, 2026-09-08).
    dst = mb / "new" / name
    try:
        _publish_new(tmp, dst)
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        # A collision — a standing new/<name> — is the one case named apart: impossible in practice
        # with 128-bit ids, so if it ever shows up it is evidence of something badly wrong, not a
        # tiebreak. Either way the name was never this message's, so nothing is written under it.
        # No row says this, so the log says it on every refusal, and the USER hears it once per
        # recipient episode as a bell row (review find, 2026-09-08: a refusal on the relay leg has
        # the dialer re-relay the message every exchange while the sender's receipt reads queued, so
        # a fault that lasted had no surface anyone watched); the next publish to that recipient
        # that lands re-arms it.
        why = ("a message with this id already stands in the recipient's inbox; refusing to replace it"
               if isinstance(e, FileExistsError) else str(e))
        text = "deliver to %s: %s was not published (%s): refused, nothing recorded" % (to_id, name, why)
        site = "deliver:%s" % to_id
        if _REFUSAL_SAID.get(site):
            _log(text)
        else:
            _REFUSAL_SAID[site] = True
            _refused_notice(text + "; the sender holds the text and retries until the inbox can be written")
        raise DeliveryNotRecorded("the message could not be placed in the recipient's inbox (%s) — "
                                  "nothing was delivered; retry" % why)
    _refusal_over("deliver:%s" % to_id)          # a publish landed: the next refusal here is a new episode
    if not _tl_append("messages.jsonl", ev):
        # The mail is out but its row is not: take it back, so nothing stands in new/ that the
        # ledger does not know, and refuse: the sender still holds the text. Two outcomes the
        # take-back cannot undo leave a DELIVERED message the ledger will never carry, and each
        # answers the id, because the message is in (or on its way to) the recipient's hands: a
        # reader claimed the file in the instant between the publish and the row (read_box renamed
        # it into cur/), or the unlink itself was refused (the file stands in new/ and the next read
        # gets it; the start sweep rebuilds that one's row if the bus restarts first). Both are said
        # by name on stderr AND as a bell row, since no row will ever say it (the log fault itself
        # was already said by _tl_append).
        try:
            dst.unlink()
        except FileNotFoundError:
            _refused_notice("deliver to %s: %s was read before its row could be written; delivered, and "
                            "the ledger has no record of it" % (to_id, name))
            _mark_pending(to_id)
            return name
        except OSError as e:
            _refused_notice("deliver to %s: %s stands in the inbox without its row and could not be taken "
                            "back (%s); delivered, and the ledger has no record of it" % (to_id, name, e))
            _mark_pending(to_id)
            return name
        _mark_pending(to_id)        # new/ may be empty again -> reconcile the marker
        raise DeliveryNotRecorded(NOT_RECORDED_TEXT)
    _mark_pending(to_id)            # new/ is now non-empty -> raise the marker (covers park + live)
    return name   # the message id (maildir filename); joins to the log + status-bar prefix

_UNREADABLE_SAID = set()   # (path, errno) already logged this bus run — said once, not per poll

def _say_unreadable_once(path, exc, where):
    """One _log line per (file, errno) per bus run for a file a listing had to skip AND could not move
    aside (the fallback under _mail_unreadable / _list_json_records). A poll loop (/inbox, /drain,
    every exchange) re-meets the same file every pass; the registry keeps the fact loud and the log
    readable."""
    key = (str(path), getattr(exc, "errno", None))
    if key in _UNREADABLE_SAID:
        return
    _UNREADABLE_SAID.add(key)
    _log("%s: %s is unreadable (errno %s: %s) and could not be moved aside: skipped and left in place; "
         "the rest is served" % (where, path.name, exc.errno, exc.strerror or exc))

def _aside_name(f):
    """`<name>.corrupt-<utc stamp>[-n]` beside `f`: the kernel stores' quarantine naming, so one
    convention reads across every store romp moves a bad file aside in."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    aside, n = f.with_name("%s.corrupt-%s" % (f.name, stamp)), 0
    while aside.exists():                            # a second one in the same second
        n += 1
        aside = f.with_name("%s.corrupt-%s-%d" % (f.name, stamp, n))
    return aside

def _mail_unreadable(f, sid, exc):
    """One inbox file in new/ that cannot be read (EACCES, EIO): moved ASIDE, once, to
    `<mailbox>/<name>.corrupt-<utc stamp>`, beside new/, so no listing (read_box, the sweeps,
    restore) meets it again, and never deleted, so the evidence survives; with a terminal
    `bounced` row (WHY_INBOX_UNREADABLE) closing the sender's receipt as refused, one stderr line
    and one bell row on the dashboard (review find, 2026-09-08). The first cut skipped the file in
    place and said it once in the log: that left the pending marker latched (the retry pass
    re-pushed a no-op every interval), the sender's receipt pending forever, the stuck-mail
    warning and the orphan sweep unable to touch it, and nothing the user could see. A file that
    cannot be moved either is the fallback: skipped, said once per (file, errno)."""
    aside = _aside_name(f.parent.parent / f.name)
    try:
        os.replace(f, aside)
    except FileNotFoundError:
        return                                       # consumed or recalled meanwhile: nothing to account
    except OSError:
        _say_unreadable_once(f, exc, "inbox %s" % sid)
        return
    who = _name_for_id(sid) or sid[:8]
    _refused_notice("mail for %s: %s could not be read (errno %s: %s); moved aside to %s; the sender's "
                    "receipt reads refused" % (who, f.name, exc.errno, exc.strerror or exc, aside.name))
    _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": f.name, "to_id": sid,
                                  "why": "%s (errno %s)" % (WHY_INBOX_UNREADABLE, exc.errno)})
    _mark_pending(sid)                               # new/ may be empty now → drop the marker

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
        try:
            text = f.read_text(errors="replace")
        except FileNotFoundError:
            continue                                 # consumed or recalled between the listing and the read
        except OSError as e:
            # One unreadable file (EACCES, EIO) used to raise out of the whole read — and do_GET
            # has no handler, so /inbox and /drain answered NOTHING, on every poll, and the session
            # got no mail at all while the Stop-hook drain hid the traceback (2026-09-08). The file
            # is moved aside once, said once, and its ledger closed (_mail_unreadable); the rest of
            # the box is served.
            _mail_unreadable(f, sid, e)
            continue
        head, _, body = text.partition("\n\n")
        meta = {}
        for line in head.splitlines():
            k, _, v = line.partition(": ")
            meta[k.lower()] = v
        if consume:
            try:
                f.rename(mb / "cur" / f.name)
            except FileNotFoundError:
                # gone between the read and the claim: recalled, taken back by a deliver() whose row
                # could not land, or claimed by a second reader (2026-09-08). Nobody's to hand over,
                # so no exec row and no entry; the rest of the box is served (an unguarded rename
                # here raised out of the whole read, and /inbox and /drain answered nothing).
                continue
            _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "exec", "id": f.name})
            _queue_read_receipt(meta, dmid=f.name)   # cross-host mail: the sender's host learns it was read
            #   dmid = THIS host's delivery mid — the id the recipient's transcript markers carry, so the
            #   sender's timeline can join the connector to the true process turn (the user 2026-08-06)
        out.append({"from": meta.get("from", "?"), "from_id": meta.get("from-id", ""),
                    "date": meta.get("date", ""), "body": body.rstrip("\n"), "id": f.name,
                    "park": bool(meta.get("x-park")), "kind": meta.get("x-kind", ""),
                    "from_host": meta.get("x-from-host", "")})
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
#      this row, never off the message prose). A CROSS-HOST relay row has to_id "peer:<host>" and
#      adds toName ("<host>:<name>") + to_sid (the recipient's stable id — the wait readers key on
#      it; rows from before 2026-09-08 lack it and fall back to the name alias). from_host is written
#      on EVERY row since 2026-09-06 — "" for local delivery, the origin host for relayed mail — so a
#      row WITHOUT the key is one from before that, whose sender may be either; a reader that needs
#      the distinction (the kernel's postal card, for the sender's repository) treats absence as
#      unknown, not as local.
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


def _when_words(t, now=None):
    """An epoch as a phrase a reader can place without a date table: " at 14:05 today",
    " at 14:05 yesterday", or " on 2026-09-05 at 14:05" (local time, leading space so it drops
    into a sentence); "" for no time (None, 0, junk). `now` is the reference epoch (tests pin it).
    Bare "at 14:05" is ambiguous the moment a day boundary passes."""
    try:
        t = int(t)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    try:
        d = datetime.fromtimestamp(t)
        ref = datetime.fromtimestamp(time.time() if now is None else now)
    except (OverflowError, OSError, ValueError):     # an epoch no calendar holds: say nothing about the time
        return ""
    days = (ref.date() - d.date()).days
    if days == 0:
        return " at %s today" % d.strftime("%H:%M")
    if days == 1:
        return " at %s yesterday" % d.strftime("%H:%M")
    return " on %s at %s" % (d.strftime("%Y-%m-%d"), d.strftime("%H:%M"))

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
            why = str(r.get("bouncedWhy") or "")
            if why.startswith(REFUSAL_WHYS):
                # a REFUSAL (2026-09-08): nothing left this machine and no return note exists, so the
                # refusal is the whole story — never promise a note that is not coming
                st = "bounced %s — refused — %s" % (_hhmm_epoch(r["bounced"]), why)
            else:
                st = "bounced %s — undeliverable, returned to you" % _hhmm_epoch(r["bounced"])
        elif r.get("parked"):                  # cross-host, still in the outbox awaiting relay
            # "(unreachable)" ONLY when the link is actually down (the user 2026-08-24): a healthy
            # queue is normal transit, not a failure. An older bus omits parkedUp — claim nothing.
            if r.get("carried"):               # it left with an exchange; the far side's ack is not back
                st = "left for %s %s — awaiting delivery confirmation · id %s" % (
                    r["parked"], _hhmm_epoch(r["carried"]), r.get("id", "?"))
            elif r.get("parkedUp"):
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
    tmux and, for ADDRESSING, without reading the SDK registry directly: ONE source. (One deliberate
    exception since 2026-08-31: _durable_session reads a per-session reg file on the REFUSAL path only,
    to corroborate a suspected listing blink before ruling a session dead — never to resolve delivery.)
    Loopback, authorized with X-Romp-Token
    (the shared 0600 serve-token file — the kernel gates every request, loopback included). [] if the kernel
    is unreachable (rare — the manager supervises it); the bus then shows no local
    agents until it's back, rather than reaching past the abstraction to tmux.

    ROMP_SESSIONS_FILE is a test seam (like ROMP_*_BIN): a JSON file of the same rows, read instead of the
    live kernel so the bus is testable without one."""
    return _kernel_sessions_checked(threads=threads)[0]


def _kernel_sessions_checked(threads=False):
    """(rows, answered) — answered False means the liveness source DID NOT ANSWER (the kernel is
    unreachable, typically mid-restart), which is different information from an empty listing.
    Callers that are about to make a CLAIM about liveness (resolve_recipient's refusal) must
    check `answered` — an unanswered fetch collapsed to [] read exactly like universal deadness,
    and the refusal it produced blamed a demonstrably live peer (sighting 2026-08-29: a send was
    refused as not-live mid-restart; the retry 101 seconds later delivered)."""
    seam = os.environ.get("ROMP_SESSIONS_FILE")
    if seam:
        try:
            data = json.loads(Path(seam).read_text())
            if not isinstance(data, list):
                return [], True
            # the seam mirrors the route: thread rows ride only when asked (the user 2026-08-22)
            return (data if threads else
                    [r for r in data if not (isinstance(r, dict) and r.get("thread"))]), True
        except Exception:
            return [], True
    import urllib.request
    try:
        req = urllib.request.Request(KERNEL_BASE + "/sessions" + ("?threads=1" if threads else ""),
                                     headers={"X-Romp-Token": SERVE_TOKEN})
        # 6s and _http's 15s are ONE BUDGET PAIR: a refusal-bound send makes two sequential
        # fetches (the pool + the corroboration probe), and the honest refusal must reach the
        # sender inside the client cap — 6+6=12 < 15. Raise them TOGETHER or not at all (the
        # 2026-08-31 review reproduced the failure mode: a fetch cap raised past the client cap
        # made the refusal die on a closed socket, blaming the bus). 6s is measured, not guessed:
        # the live /sessions route on a loaded 30-session kernel sampled p50 0.6s / p90 3.5s /
        # max 4.9s (2026-08-31) — the old 2s cap failed a fifth of fetches, and each failure
        # started the refusal chain the blink specimens rode.
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data if isinstance(data, list) else []), True
    except Exception:
        return [], False


def local_agents(threads=False):
    """LIVE local sessions (tmux + SDK) as postal agent rows, read from the kernel's unified GET /sessions.
    The kernel merges both backends, so an SDK session is a live agent here too — a send to an open SDK
    session delivers instead of parking as dead (the user via ui, 2026-06-26).

    `threads` (the user 2026-08-22): also include COMMENT-THREAD sessions — real forked sessions the
    kernel hides from tabs/lanes/cards until promotion. Opt-in per consumer so the default listing and
    every other reader stay exactly as they were: self-identity, recipient resolution, and the agents
    listing pass True (a thread mails its parent under its OWN name and is addressable for replies),
    and so does every reader that judges a MAILBOX live or dead — the heartbeat (2026-09-06), the
    orphan sweep, the stuck-mail warning, the revive wake and the retry pass (2026-09-10: those four
    read the default listing, so a live thread's box was dead to them — the sweep destroyed a parent's
    reply to its own thread after ORPHAN_GRACE and told the sender the thread had exited, and the
    retry and wake never delivered it). Readers that only count or show presence keep the default."""
    return _agent_rows(_kernel_sessions(threads=threads))


def _agent_rows(sessions):
    res = []
    for s in sessions:
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


def local_agents_checked(threads=False):
    """(local_agents rows, answered) — the honesty-arm variant for consumers whose REFUSALS ride the
    listing (the inbound relay's bounces, the presence producer): 'answered' distinguishes a kernel
    that said "no sessions" from one that couldn't answer at all (mid-restart), the same bit the
    sending resolver has carried since the answered-but-absent round (2026-08-31)."""
    rows, answered = _kernel_sessions_checked(threads=threads)
    return _agent_rows(rows), answered


def _kernel_post(path, body, timeout=2):
    """POST a small JSON body to the kernel (loopback, X-Romp-Token from the shared 0600 file) — the bus's
    one-way control channel for the
    ops the kernel owns now that the bus never shells tmux: the working-note, mail delivery/wake, the
    status-bar chrome, and the resume-picker check. Returns the parsed JSON response dict; None when
    the kernel could not be reached or its answer could not be parsed (the caller degrades); or, for a
    kernel that REFUSED the request (a 4xx/5xx), {"ok": False, "status": <code>, "error": <its text>},
    logged here by status with a bounded slice of the kernel's own reason, so a refusal never reads as
    a dead kernel (review find, 2026-09-08: every non-2xx came back as None, and the kernel's 413 on an
    oversize /deliver body was filed as "deferred" and re-posted forever). A caller that tests
    `resp.get("ok")` or `resp.get("injected")` sees a refusal as the failure it is. No-op (None) under
    the ROMP_SESSIONS_FILE test seam, which signals a test running with no live kernel."""
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
    except urllib.error.HTTPError as e:
        text = ""
        try:
            text = " ".join((e.read(512) or b"").decode("utf-8", "replace").split())   # a bounded slice: the
        except Exception:                                                                # kernel's reason, never
            pass                                                                         # a whole error page
        _log("kernel refused POST %s: HTTP %d %s" % (path, e.code, text))
        return {"ok": False, "status": int(e.code), "error": text}
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
    if not sid:
        return False
    r = _kernel_post("/working", {"id": str(sid), "text": text})
    return r is not None and r.get("ok") is not False        # None: unreachable; ok false: the kernel refused

def _with_remote_presence(agents):
    """The local rows plus every heartbeat-known REMOTE session (within HEARTBEAT_TTL, not already local)."""
    local_ids = {a["id"] for a in agents}
    now = time.time()
    for sid, (name, ts) in list(HEARTBEATS.items()):
        if now - ts < HEARTBEAT_TTL and sid not in local_ids:
            agents.append({"name": name, "id": sid, "remote": True})
    return agents

def all_agents(threads=False):
    return _with_remote_presence(local_agents(threads=threads))

def _record_heartbeat(sid, name):
    """Record remote-presence for an incoming heartbeat — but ONLY for a sid the local kernel does NOT already
    own. A local session is already visible via the kernel's /sessions, so recording its heartbeat would leave
    it lingering as a phantom [remote] peer for the TTL after it dies. A genuine REMOTE (federated) session,
    reaching us over an -R tunnel, is NOT in the local kernel — heartbeats are its only presence signal.

    Returns True iff the sid is LOCAL: the bus's own listing ANSWERED and contains it (thread rows
    included — a comment thread heartbeats under its own row, and until 2026-09-06 the thread-less
    listing read here filed every live thread as REMOTE presence, a phantom that also reached
    STATE/remote-sids; the by-name consumers that used to find a thread through that phantom,
    _recip_id_for for recall, now read the listing with thread rows). The /heartbeat route hands that bit
    back so a local session's MCP can stop heartbeating (2026-09-06: every local beat cost the kernel
    three GET /sessions for a no-op). An UNANSWERED listing (kernel mid-restart) is False and records
    the beat exactly as before — the answer is derived from the listing only, never from the
    client's claim, so a remote session never hears "local" and never stops."""
    local = False
    if sid and _safe_id(sid):
        rows, answered = local_agents_checked(threads=True)
        if answered and any(a["id"] == sid for a in rows):
            local = True
        else:
            HEARTBEATS[sid] = (name or "?", time.time())
    _write_remote_sids()                           # presence changed → refresh the deadness mirror
    return local

def present_count_checked():
    """(present count, answered) for the autostop gate: local rows plus heartbeat presence, and whether
    the kernel's listing ANSWERED. An answered listing is also remembered (_remember_presence: the
    in-memory last-good rows and their disk twin), which is the evidence _idle_tick reads when a later
    listing does not answer."""
    rows, answered = local_agents_checked()
    if answered:
        _remember_presence(rows)
    return len(_with_remote_presence(list(rows))), answered

def present_count():
    return present_count_checked()[0]

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

_user_todos_switch_bad = {}   # str(path) -> (mtime_ns, size) of a switch-file version already reported (below)
# The kernel's bounds on a note (_USER_TODO_TEXT_CAP / _USER_TODO_DETAIL_CAP, kernel.py), mirrored by
# name: the route answers 400 over them, but _kernel_post reads every non-2xx as None, so the tool
# checks first and words the refusal itself — over the cap is refused, never trimmed (2026-09-07).
USER_TODO_TEXT_CAP = 500
USER_TODO_DETAIL_CAP = 4000

def _user_todos_on():
    """The kernel's per-install USER TODOS switch (the user 2026-09-03: the feature is off by default
    and per machine). Read from the file on EVERY call — the bus is a separate long-lived process, so
    the file is the seam, and a gear flip must take effect at the next tools/list or call with no
    restart. Absent, unreadable or malformed all read False (the opt-in must be provable), the
    kernel's own _user_todos_on rule; reading never creates the file. Absent is the shipped default
    and silent; a file that is not a {"enabled": …} object is said on stderr once per file version,
    as the kernel says it (2026-09-07), so a hand-edit that turned the tools off is not a mystery."""
    try:
        d = json.loads(USER_TODOS_SWITCH.read_text())
    except FileNotFoundError:
        return False
    except Exception as e:
        d, why = None, "unparsable (%s)" % e
    else:
        why = None if isinstance(d, dict) else "a JSON %s, not an object" % type(d).__name__
    if why is None:
        return bool(d.get("enabled"))
    try:
        st = USER_TODOS_SWITCH.stat(); key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if _user_todos_switch_bad.get(str(USER_TODOS_SWITCH)) != key:
        _user_todos_switch_bad[str(USER_TODOS_SWITCH)] = key
        sys.stderr.write("user-todos: %s is not a switch file (%s); reading it as OFF\n" % (USER_TODOS_SWITCH, why))
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

def _name_for_id(sid, rows=None):
    """A session's display name: its live row (thread rows included — a comment thread's name lives
    only on its row, no names entry), else the names registry, else the short id. `rows` is a listing
    the caller already holds, so an operation that names many ids fetches GET /sessions once, not once
    per id (2026-09-06: the orphan sweep named every dead mailbox with its own fetch, 28 per 30 s poll
    on a box with 58 mailboxes; a recall by a dead name fetched once per mailbox). None fetches."""
    if not sid:
        return "?"
    if rows is None:
        rows = local_agents(threads=True)
    for a in rows:
        if a["id"] == sid:
            return a["name"]
    try:
        return (NAMES_DIR / sid).read_text().split("\t")[0].strip() or sid[:8]
    except Exception:
        return sid[:8]

def _recip_id_for(to, rows=None):
    """A recipient reference (live name, or UUID with an existing mailbox) -> session
    UUID, or None. Addressing is LIVE-only: a name that isn't a currently-live session
    fails (no dead-session resurrection). Thread rows included, the 2026-08-22 rule every
    by-name resolver follows (resolve_recipient, GET /agents): a comment thread is addressable
    by its own name, and recall of mail parked for it must find it (2026-09-06: it used to be
    found through a phantom remote-presence row its heartbeat left; that phantom is gone).
    `rows`: a listing the caller already holds (recall shares one across its lookups)."""
    agents = _with_remote_presence(list(rows)) if rows is not None else all_agents(threads=True)
    for a in agents:
        if a["name"] == to:
            return a["id"]
    if _safe_id(to) and (MAILROOT / to).is_dir():  # already an id with a mailbox (in-flight mail)
        return to
    return None

def _durable_session(bare, by_id):
    """Does `bare` name a session the DURABLE per-session registry knows as live-or-resumable?
    The corroboration read behind the answered-but-absent refusal arm (2026-08-31): the kernel's
    listing transiently omitted live sessions (a restart-settle blink), and the bus converted an
    incomplete-but-200 listing into a hard "not live" for both address forms — one specimen
    mis-routed a warning mail. A reg with alive=true is a session romp WILL list (running or
    dormant-resumable), so its absence from one listing is a listing gap, never evidence of death.
    tmux sessions have no reg — their liveness is tmux's own, and this read stays honestly silent
    for them. Reads the registry file directly (same box, kernel-owned): the designed API is the
    listing itself, which is exactly the thing being second-guessed here."""
    root = STATE.parent
    if by_id:
        sids = [bare]
    elif re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{7,35}", bare):
        # the short-id form (the ` · <8-char>` every list_agents row shows) — the third address
        # form, blink-protected like the other two: prefix-match the registry files themselves
        try:
            sids = [f.stem for f in (root / "sdk").glob("*.json") if f.stem.startswith(bare)]
        except OSError:
            sids = []
        if len(sids) != 1:
            sids = []          # ambiguity is the standing refusal's call, never a guess here
    else:
        sids = []
    if not by_id and not sids:
        try:
            for f in NAMES_DIR.iterdir():
                try:
                    if f.read_text().split("\t")[0].strip() == bare:
                        sids.append(f.name)
                except OSError:
                    continue
        except OSError:
            pass
    for sid in sids:
        try:
            reg = json.loads((root / "sdk" / (sid + ".json")).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(reg, dict) and reg.get("alive"):
            return True
    return False


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
    pool = all_agents(threads=True)                                   # threads addressable for replies
    direct_all = ([] if (want_host and want_host != here)
                  else [a for a in pool
                        if (a.get("id") == bare if by_id else a["name"] == bare)])
    if not direct_all and not by_id and not want_host             and re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{7,35}", bare):
        # a SHORT id — the ` · <8-char>` form every list_agents row now carries (the user
        # 2026-08-24) — addresses by unambiguous id PREFIX, so the row is enough to act on. An
        # exact NAME match always wins first (a name may be hex-shaped); at least 8 characters so
        # a stray word can never catch a session by luck; a remote row's id ("host:uuid") matches
        # on its uuid part, the part the row shows. TWO prefix hits fall through to the standing
        # ambiguity refusal below, exactly like a duplicated name.
        direct_all = [a for a in pool
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
    # Addressing is LIVE-only: no dead-session resurrection — but "not live" is a claim about the
    # world, and the kernel is its authoritative source (fail loudly, never degrade silently, the
    # user 2026-07-03). Before making the claim, ask whether the source ANSWERED: a mid-restart
    # kernel yields an empty listing that reads exactly like universal deadness. One loopback GET,
    # paid only on this refusal path — and even an ANSWERED listing is interrogated before the
    # death ruling (2026-08-31: two verified specimens of a 200 listing omitting a LIVE session,
    # by id and by name — a restart-settle blink; the refusal mis-routed a warning mail). The
    # fresh fetch's own rows and the answered bit travel together from ONE call, so a kernel
    # coming back between two fetches can't convert unreachable into a false death ruling; the
    # durable per-session registry (_durable_session) corroborates what one listing missed.
    if want_host and want_host != here:
        # a host qualifier naming somebody ELSE took every local out of the running by DESIGN —
        # the local listing and registry can say nothing about that host's sessions, and blink
        # arms firing on a same-named LOCAL row turned a typo'd host into an endless retry-shortly
        # (review find, 2026-08-31)
        return {"kind": "error", "status": 404, "error": "no live romp session named '%s'" % to}
    rows, answered = _kernel_sessions_checked(threads=True)
    if not answered and not any(not a.get("remote") for a in pool):
        # only an EMPTY local world raises the unreachable question — a pool with live locals in
        # it proves the source was answering (and heartbeating remotes can't vouch for LOCALS)
        return {"kind": "error", "status": 503,
                "error": "can't tell whether '%s' is live right now: the liveness source (the romp "
                         "kernel) didn't answer — likely mid-restart. Nothing was sent, and this is "
                         "NOT a claim that '%s' is dead. Retry shortly." % (bare, bare)}
    fresh = set()
    for r in rows:
        if isinstance(r, dict):
            fresh.add(str(r.get("id") or ""))
            fresh.add(str(r.get("name") or ""))
    blinked = bare in fresh or _durable_session(bare, by_id)
    if not blinked and not by_id and re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{7,35}", bare):
        # the short-id form prefix-matches the fresh rows too, like the pool arm it mirrors
        hits = {i for i in fresh if i.startswith(bare)}
        blinked = len(hits) == 1
    if blinked:
        return {"kind": "error", "status": 503,
                "error": "'%s' looks live (its durable registry entry stands) but the session "
                         "listing came back without it — likely a restart-settle blink. Nothing "
                         "was sent, and this is NOT a claim that '%s' is dead. Retry shortly."
                         % (bare, bare)}
    return {"kind": "error", "status": 404, "error": "no live romp session named '%s'" % to}


WHY_CARRIED = "already left for %s and can no longer be withdrawn"        # _recall: a carried outbox record
WHY_IN_FLIGHT = "is on its way to %s right now (try again in a moment)"    # _recall: a record an exchange is carrying

def _recall(from_id, to, mid, kept=None):
    """Unsend UNREAD mail: delete messages still sitting in a recipient's `new/`
    (queued or parked) that were sent BY from_id. With `to`, scope to that one
    recipient; with `mid` (and no `to`), find it across mailboxes; both narrows to
    one message. Only the original sender's own messages are touched. Already-read
    mail has left `new/` and can't be recalled. Returns [{to, id, body}] removed.

    Cross-host mail parked in the OUTBOX is recallable only until an exchange CARRIES it
    (2026-09-08). The record does not leave the outbox when it rides — it leaves on the
    end-to-end ack, one round trip later at best and a whole outage later when a response was
    lost — and the far bus delivers on arrival, so in that window the recipient already holds
    the message and may be answering it. Unlinking the record then, as this arm used to, and
    filing a terminal recall row for it, told the sender's receipts (and every reader that
    treats a recall as final) that a read message had been withdrawn. Two states refuse here
    now, both exact events of the exchange, and a refused record stays, no row is written, and
    the refusal is appended to `kept` (a list the caller passes; None drops them) as {to, id,
    host, carried, why, body} so the sender is told in plain words:
    - CARRIED (`carried` on the record, durable): the exchange's OUTCOME said the record reached
      the far bus — the dialer got a response, or its dial failed only after the request went
      out; the dialed side's response write returned (_mark_carried has the event per side).
      "already left for <host> and can no longer be withdrawn".
    - IN FLIGHT (_inflight, in memory): an open exchange — any of them; two run at once for one
      host — listed the record and its outcome is not in yet: the bytes are on the wire or about
      to be, so a recall can neither be granted (the
      recipient may already hold them) nor told they left (a refused dial means they never
      will). "is on its way to <host> right now (try again in a moment)": the outcome, seconds
      away, turns it into the carried refusal or frees the record for the recall.
    The check and the unlink here share _outbox_lock with the listing that puts a record in
    flight (_relays_for) and the outcome that marks or frees it (_flight_done), so a recall
    either unlinks before the listing sees the record, or meets one of the two refusals; never
    a granted recall for a record an exchange is carrying.

    Every recall row names the box it came from — `box: 'new'` (a recipient's maildir) or
    `box: 'outbox'` (a parked cross-host record, with `host`) — so a reader can tell the two
    apart by an explicit field instead of an id's shape."""
    if not from_id:
        return []
    listing = []                                 # ONE listing per recall, at first need; every name lookup reads it

    def _rows():
        if not listing:
            listing.append(local_agents(threads=True))
        return listing[0]

    if to:
        rid = _recip_id_for(to, rows=_rows())
        if not rid and MAILROOT.is_dir():
            # Addressing is live-only, but RECALL is not addressing: the sender is unsending their
            # own bytes, not raising the dead. Mail parked for a session that has since died sits
            # right here in its new/ — a name that no longer resolves live falls back to the
            # durable name map so parked mail stays recallable (sighting 2026-08-29: a handoff
            # parked for a dead session could not be unsent by name; only the raw id worked).
            hits = [b.name for b in MAILROOT.iterdir()
                    if b.is_dir() and _name_for_id(b.name, rows=_rows()) == to]
            rid = hits[0] if len(hits) == 1 else None    # two dead boxes, one name: refuse, stay scoped
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
            removed.append({"to": _name_for_id(rid, rows=_rows()), "id": f.name, "body": " ".join(body.split())[:120]})
            _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "recall", "id": f.name, "box": "new"})
        _mark_pending(rid)         # recall may have emptied new/ -> reconcile the marker
    if peers_on() and OUTBOX.is_dir():
        # A recall that beats the truck wins: parked cross-host mail is still local, so the sender
        # can unsend it right up until an exchange carries it — and not one moment after (the
        # `carried` mark; see the docstring). Forwarded mail (origin set) belongs to a sender on
        # another host — never touched here.
        for hostdir in OUTBOX.iterdir():
            if not hostdir.is_dir():
                continue
            for f in list(hostdir.glob("*.json")):
                if mid and f.stem != mid:
                    continue
                with _outbox_lock:                   # the listing, the mark and this unlink share it: no
                    try:                             # unlink of a record an exchange is carrying right now
                        msg = json.loads(f.read_text())
                    except Exception:
                        continue
                    if msg.get("frm_id") != from_id or msg.get("origin"):
                        continue
                    if to and (msg.get("to") or "") != to and "%s:%s" % (hostdir.name, msg.get("to") or "") != to:
                        continue
                    riding = any(f.stem in mids for mids in (_inflight.get(hostdir.name) or {}).values())
                    if msg.get("carried") or riding:
                        if kept is not None:
                            kept.append({"to": "%s:%s" % (hostdir.name, msg.get("to") or "?"), "id": f.stem,
                                         "host": hostdir.name, "carried": msg.get("carried"),
                                         "why": (WHY_CARRIED if msg.get("carried") else WHY_IN_FLIGHT) % hostdir.name,
                                         "body": " ".join((msg.get("body") or "").split())[:120]})
                        continue                     # it stays; no row: nothing about it changed
                    try:
                        f.unlink()
                    except Exception:
                        continue
                removed.append({"to": "%s:%s" % (hostdir.name, msg.get("to") or "?"), "id": f.stem,
                                "body": " ".join((msg.get("body") or "").split())[:120]})
                _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "recall", "id": f.stem,
                                              "box": "outbox", "host": hostdir.name})
    return removed

def _sent_receipts(mid):
    """[{to, id, sent, exec, recalled}] for messages SENT by `mid`, joined by id,
    oldest first. exec is None until the recipient reads it; recalled is set if the
    sender later unsent it (so it shows 'recalled', not a permanent 'pending')."""
    log = TLDIR / "messages.jsonl"
    if not mid or not log.exists():
        return []
    sent, execs, recalls, relays, bounced, bounced_why = {}, {}, {}, {}, {}, {}
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
            bounced_why[e["id"]] = str(e.get("why") or "")   # a REFUSAL's why renders apart (format_receipts)

    def _parked(i, e):                               # still in the outbox → honestly parked, not lost:
        tid = e.get("to_id", "")                     # (host, record) — the record carries the carry mark
        if tid.startswith("peer:") and not relays.get(i) and not bounced.get(i) and not recalls.get(i):
            h = tid[5:]
            rec = outbox_get(h, i)
            if rec:
                return h, rec
        return None, None

    listing = []                                 # one GET /sessions for every row without toName, at first need

    def _name(sid):
        if not listing:
            listing.append(local_agents(threads=True))
        return _name_for_id(sid, rows=listing[0])

    def _row(i, e):
        h, rec = _parked(i, e)
        r = {"to": e.get("toName") or _name(e.get("to_id", "")), "id": i, "sent": e["t"],
             "exec": execs.get(i), "recalled": recalls.get(i),
             "relayed": relays.get(i), "bounced": bounced.get(i), "parked": h}
        if r["bounced"]:
            r["bouncedWhy"] = bounced_why.get(i, "")   # additive: an older client ignores it
        if h:
            # the LINK state rides along (the user 2026-08-24): outbox residency alone is not
            # unreachability — a message queued ahead of the next exchange on a healthy link is
            # just in transit, and labeling it "(unreachable)" cried wolf on every normal relay.
            # PEERS is the authoritative dial state the send path already branches on.
            r["parkedUp"] = bool((PEERS.get(h) or {}).get("up"))
            if rec.get("carried"):
                # an exchange CARRIED it and the ack is not back (2026-09-08): the same fact the
                # recall refuses on, so the two surfaces agree (additive: an older client ignores it)
                r["carried"] = rec["carried"]
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
    live = local_agents(threads=True)                 # a comment thread's box is live while its row is (2026-09-10)
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
        recip = _name_for_id(box.name, rows=live)  # dead by construction: the registry names it, no fetch
        for f in list(newd.iterdir()):
            if not f.is_file():
                continue
            try:
                text = f.read_text(errors="replace")
            except FileNotFoundError:
                continue
            except OSError as e:
                # a dead recipient's box has no drain to meet an unreadable file, so the sweep moves
                # it aside the way read_box does (review find, 2026-09-08); before, it skipped the
                # file on every pass and the marker stayed latched for a session that will never read
                _mail_unreadable(f, box.name, e)
                continue
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
                except DeliveryNotRecorded as e:
                    # The bounce note was REFUSED (its row could not land). Destroying the orphan now
                    # would lose the mail with no notice and no row — deliver() never refused before
                    # 2026-09-08, so this arm is new. Keep the file; the next sweep retries.
                    _say_refused_once("orphan sweep", "the bounce note for %s" % f.name, e)
                    continue
                except Exception as e:
                    _log("bounce to %s failed: %s" % (s["name"], e))
                _refusal_over("orphan sweep")
            # the destroy is the message's TERMINAL EVENT — record it on the original mid, the
            # way _bounce_apply records a peer's refusal (the user 2026-08-24): without this row
            # the ledger's last word stayed "sent", and the timeline's pending flag had to lean
            # on an age window / recipient liveness — which a same-sid REVIVAL then flips back
            # to pending for mail that no longer exists. The ledger is now terminal-complete.
            # The row lands BEFORE the unlink (2026-09-08), the batch's rule: a destroy that could
            # not be recorded does not happen; the file waits for the next sweep.
            if not _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": f.name,
                                                 "to": recip or "?",
                                                 "why": "recipient exited; unread mail destroyed by the orphan sweep"}):
                continue
            try:
                f.unlink()
            except Exception:
                pass
        _mark_pending(box.name)                         # bounced orphans may have emptied new/
        try:                                            # tidy: drop the mailbox if nothing's left
            if all(not any((box / d).iterdir()) for d in ("new", "cur", "tmp") if (box / d).is_dir()) \
                    and not any(p.is_file() for p in box.iterdir()):
                # …and no `.corrupt-*` sidecar beside the three dirs: a file moved aside is evidence
                # the tidy must not sweep away with the empty box (review find, 2026-09-08)
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
    live = local_agents(threads=True)                 # thread rows too: an idle thread can be stuck like any session
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
                except DeliveryNotRecorded as e:
                    # REFUSED (its row could not land): touching the one-time marker now would mean
                    # the sender never hears the warning. Leave it; the next pass fires it.
                    _say_refused_once("stuck-mail warning", "the warning for %s" % f.name, e)
                    continue
                except Exception as e:
                    _log("stuck-warn to %s failed: %s" % (s.get("name", "?"), e))
                _refusal_over("stuck-mail warning")
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

def _deliver_body_bytes(sid, msgs):
    """The exact wire size of the /deliver POST carrying `msgs`: the banner in its JSON envelope,
    serialized as _kernel_post serializes it (ensure_ascii on, so non-ASCII text and every newline
    inflate past the banner's own length)."""
    return len(json.dumps({"id": sid, "text": format_push(msgs)}).encode("utf-8"))


def _push_chunks(sid, msgs):
    """Split a recipient's pending mail into /deliver bodies that fit under _PUSH_MAX_BYTES, oldest
    first -> (chunks, oversize). Each chunk is a non-empty run of consecutive messages whose whole body
    fits; `oversize` are the messages whose body ALONE does not, which no chunk can carry. The banner
    used to be one body for the whole box (review find, 2026-09-08): past the kernel's cap it was
    refused, and the refusal re-posted on every retry pass."""
    chunks, cur, oversize = [], [], []
    for m in msgs:
        if cur and _deliver_body_bytes(sid, cur + [m]) <= _PUSH_MAX_BYTES:
            cur.append(m)
            continue
        if cur:
            chunks.append(cur)
            cur = []
        if _deliver_body_bytes(sid, [m]) <= _PUSH_MAX_BYTES:
            cur = [m]
        else:
            oversize.append(m)
    if cur:
        chunks.append(cur)
    return chunks, oversize


_OVERSIZE_NAMED = set()   # mids of oversize mail already named in the log: a message left for the drain
#                           is re-claimed by every retry pass, and one line per message is the record


def _bounce_oversize(sid, m):
    """One message whose /deliver body alone exceeds _PUSH_MAX_BYTES: it can never ride the live wake
    (the kernel refuses the body before reading it), so it is not posted, and never was going to land
    however often the retry pass re-posted it. A LOCAL sender hears it, the way a peer's refusal reaches
    a sender through _bounce_apply: the message leaves the recipient's box (the drain already claimed it)
    and a bus-authored note names the size and the limit, without echoing the body, which would make
    the note itself oversize. A message with no local sender to tell (a bus-authored note; relayed mail,
    whose sender lives on another host and was acked at relay time; a `--from <label>` script, whose
    `ext:<label>` id names no mailbox: _safe_id has no ':', so deliver() to it raises ValueError, which
    before 2026-09-10 escaped into _push's catch-all and stranded in cur/ every message the drain had
    claimed) stays in new/ for the turn-end drain and check_inbox, which have no size cap, and is named
    in the log once."""
    n = _deliver_body_bytes(sid, [m])
    mid, frm_id = m.get("id", ""), m.get("from_id", "")
    if frm_id and not m.get("from_host") and frm_id != sid and _safe_id(frm_id):
        to = _name_for_id(sid) or sid
        why = ("your message is %d bytes as delivered, over the %d-byte limit for delivery into a session"
               % (n, _PUSH_MAX_BYTES))
        try:
            deliver(frm_id, "romp-postal", "", "undeliverable to '%s': %s. Send a shorter message, or write "
                    "the text to a file and send its path. (The message is not echoed here because of its "
                    "size.)" % (to, why), kind="coordinate")
        except DeliveryNotRecorded as e:
            # The note was REFUSED (its row could not land). The drain already claimed the message, so
            # letting the refusal out here (into _push's catch-all) would leave it in cur/ with no note
            # and no row — the arm the orphan sweep grew the same day (2026-09-08). Put it back under
            # its own id; the next pass re-claims it and retries the bounce once the log writes again.
            restore(sid, mid)
            _say_refused_once("oversize bounce", "the note for %s" % mid, e)
            return
        _refusal_over("oversize bounce")
        _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": mid, "to": to,
                                      "host": "", "why": why})
        _log("push to %s: message %s is %d bytes, over the %d-byte /deliver limit; bounced to its sender %s"
             % (sid, mid, n, _PUSH_MAX_BYTES, frm_id))
        return
    if not restore(sid, mid):
        deliver(sid, m.get("from", "?"), frm_id, m.get("body", ""), park=m.get("park", False),
                kind=m.get("kind", ""), from_host=m.get("from_host", ""))
    if mid not in _OVERSIZE_NAMED:
        _OVERSIZE_NAMED.add(mid)
        _log("push to %s: message %s is %d bytes, over the %d-byte /deliver limit, and has no local sender "
             "to bounce to; it waits in new/ for the turn-end drain" % (sid, mid, n, _PUSH_MAX_BYTES))


def _push(sid, agent):
    """Live-deliver pending mail to a session by WAKING it through the kernel (POST /deliver) — the kernel
    injects the banner into the pane (tmux, draft-preserving) or enqueues it (SDK); the bus never shells tmux.
    Coarse-skip a clearly not-ready session (remote / not idle-or-working) to avoid a needless drain; the
    kernel does the fine pane-safety (at a ❯ prompt, out of copy-mode, a draft it can safely stash) and tells
    us whether it injected. Not injected → put the mail back for the maildir-drain backstop. Returns True iff
    every message that can ride the wake was injected (so a revive poll knows to stop). `agent` is the GET
    /sessions row (id, state, backend, remote).

    The box goes over in CHUNKS under _PUSH_MAX_BYTES (review find, 2026-09-08): the kernel reads a POST
    body only up to _POST_MAX_BYTES, and one banner for the whole box crossed that once enough mail had
    piled up for one recipient, refused with 413 before a byte was read, filed here as "deferred", and
    re-posted identically on every retry pass, so the live wake for that recipient never came. Chunks
    post oldest first; the first that does not land stops the run, and it and everything after it are
    restored. A single message too large for any chunk is handled by _bounce_oversize. The log line
    names the cause (a kernel that could not be reached, one that answered a status, or a pane that
    was not safe) where every deferral used to read the same."""
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
        chunks, oversize = _push_chunks(sid, msgs)
        for m in oversize:
            _bounce_oversize(sid, m)
        landed, held, cause = 0, [], ""
        for i, chunk in enumerate(chunks):
            resp = _kernel_post("/deliver", {"id": sid, "text": format_push(chunk)}, timeout=12)
            if resp and resp.get("injected"):
                landed += len(chunk)
                continue
            if resp is None:
                cause = "kernel unreachable"
            elif resp.get("status"):
                cause = "kernel answered HTTP %s" % resp["status"]   # the reason is in _kernel_post's line
            else:
                cause = "not injected"                        # the pane was not safe to paste into
            held = [m for c in chunks[i:] for m in c]
            break
        if not held:
            return bool(landed)
        # Not injected → UNCLAIM: put each message back under its ORIGINAL id (restore), so a
        # deferred push doesn't mint a second identity for the same message. Only if the file is
        # gone (recalled/swept mid-push) do we fall back to a re-send, which costs a new id but
        # never loses the mail.
        for m in held:
            if not restore(sid, m.get("id", "")):
                deliver(sid, m.get("from", "?"), m.get("from_id", ""), m.get("body", ""),
                        park=m.get("park", False), kind=m.get("kind", ""),
                        from_host=m.get("from_host", ""))
        _log("push to %s deferred (%s); %d msg(s) restored for the drain backstop%s"
             % (sid, cause, len(held), (" after %d landed" % landed) if landed else ""))
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
            agent = next((a for a in local_agents(threads=True) if a["id"] == sid), None)   # a reviving thread is a live row
            if not agent:
                return                                        # session died during load
            if _push(sid, agent):                             # injected (drain + submit → forces a turn) → done
                return
            time.sleep(0.5)
    except Exception as e:
        _log("wake wait failed for %s: %s" % (sid, e))

# The most a POST body may carry -- the kernel's bound, mirrored: every bus route takes a JSON control
# request (one mail, a peer table, an exchange of presence and acks), tens of KB at most.
_POST_MAX_BYTES = 1024 * 1024

# The longest the bus waits for an announced, in-bounds body to ARRIVE -- the kernel's _POST_BODY_TIMEOUT,
# mirrored (review find, 2026-09-08): with no timeout on the socket, a handler thread was pinned for as
# long as an authorized client cared to trickle its body. Every client sends its body in one write, so
# 30 s is generous; a read that stalls past it answers 408 and closes.
_POST_BODY_TIMEOUT = 30.0

# The most one /deliver body the bus BUILDS may carry (review find, 2026-09-08): the kernel reads a POST
# only up to _POST_MAX_BYTES, and this is what _push measures each chunk of a recipient's box against,
# the exact wire size, envelope and escaping included, so no chunk is ever refused on size. The gap
# under the cap is headroom, not a measurement allowance.
_PUSH_MAX_BYTES = 900 * 1024


def _clip_json(v, n=60):
    """A BOUNDED, WELL-FORMED echo of an offending request value for an error message: a large body must
    never come back as a large error. Every string is cut INSIDE its quotes and marked, at any depth, so
    a long string still echoes as one complete quoted thing; a container is cut at the ELEMENT level,
    its first few members and a marker member, and nests no deeper than four levels, so the echo is
    valid JSON whatever arrived (review find, 2026-09-08: a slice of the serialized text cut a container
    mid-token, and ["xxx... came back with its quote and bracket open); a number past n digits echoes as
    a marked string, since a cut decimal is a mid-token cut too. Members are dropped until the whole
    echo fits about 4n characters; ensure_ascii is off, or the marker itself comes back as \\u2026. The
    kernel's shape."""
    mark = "\u2026"

    def cut(x, keep, depth):
        if isinstance(x, str):
            return x[:n] + mark if len(x) > n else x
        if isinstance(x, dict):
            if not x:
                return {}
            if depth >= 4 or keep == 0:
                return {mark: mark}
            items = list(x.items())
            out = {cut(str(k), keep, depth + 1): cut(val, keep, depth + 1) for k, val in items[:keep]}
            if len(items) > keep:
                out[mark] = mark
            return out
        if isinstance(x, (list, tuple)):
            if not x:
                return []
            if depth >= 4 or keep == 0:
                return [mark]
            out = [cut(val, keep, depth + 1) for val in x[:keep]]
            if len(x) > keep:
                out.append(mark)
            return out
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            s = str(x)                                   # a number past n digits (json.loads takes an int of
            return x if len(s) <= n else s[:n] + mark    # up to 4300) echoes as a marked STRING: a cut
        return x                                         # decimal would be a mid-token cut

    try:
        for keep in (8, 4, 2, 1, 0):
            s = json.dumps(cut(v, keep, 0), ensure_ascii=False)
            if len(s) <= 4 * n or keep == 0:
                return s
    except (TypeError, ValueError):
        pass
    s = repr(v)
    return s if len(s) <= n else s[:n] + mark


def _as_bool(v, field, default=False):
    """A request flag as the boolean it claims to be -> (value, error). Absent is `default`, and so is an
    EXPLICIT JSON null: the routes read their fields with .get(), under which the two are one value, and
    null is the absent case spelled out, not a third kind of flag (review find, 2026-09-08; the kernel's
    _as_bool states the same rule). JSON true/false pass through; anything else -- the string "false",
    0/1, "no" -- is refused with `error` naming the field, and the caller must NOT act. Never coerces:
    bool("false") is True, which is how a string used to arm a tracked delegation and mark a down peer
    bus up."""
    if v is None:
        return default, None
    if v is True or v is False:
        return v, None
    return None, "'%s' must be true or false, got %s" % (field, _clip_json(v))


class _RefusedBody(Exception):
    """A request body Handler._body refused before or instead of parsing it: `status` and `error` are the
    answer, and the connection closes with it (the body is unread or unusable, so the socket cannot
    carry a next request)."""

    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


def _parked_note(phost, frm_id):
    """The /send answer for a message parked for an unreachable host, read back by the sender from the
    CLI or the tool. A session sender hears the two ways the park ends: delivery on reconnect, or the
    peer's refusal returned to its mailbox as a note (_bounce_apply). A `--from <label>` sender mails
    under `ext:<label>` (cli_send), an id _safe_id refuses, so it has no mailbox for that note: the
    bounced row is written to messages.jsonl as for any sender, but /sent refuses the same id, so the
    bus log is the only record of the refusal that sender can read, and the single text used to
    promise it too that a refusal "bounces back to you" (2026-09-10). The session sender's text is
    unchanged."""
    if _safe_id(frm_id):
        return "parked for %s (unreachable) — delivers on reconnect, or bounces back to you" % phost
    return ("parked for %s (unreachable) — delivers on reconnect; if %s refuses it, the only record of the "
            "refusal you can read is the mail service's log (%s): a --from sender has no mailbox for a note "
            "to return to" % (phost, phost, LOG))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass   # keep stdout/stderr clean; the bus log is for real events only

    def _send(self, obj, code=200, close=False):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        """The request body as the JSON object every route expects, read only within bounds -- the
        kernel's _read_post_body, mirrored. Refused as _RefusedBody(status, error), each before or
        instead of parsing: a Transfer-Encoding request (411; bodies are delimited by Content-Length
        alone), more than one Content-Length (400), a non-decimal one or a digit run past 20 (400), a
        length past _POST_MAX_BYTES (413, before a byte is read), a body shorter than announced (400,
        naming the shortfall), one that stalls past _POST_BODY_TIMEOUT (408; the read runs under a socket
        timeout, so a trickling client cannot pin a handler thread), and a decodable body that is not an
        object (400, naming what arrived). It
        used to be `json.loads(rfile.read(int(Content-Length)))` unchecked: the first of two lengths was
        trusted, nothing was capped, and a non-object came back parsed so the route's first `.get`
        raised AttributeError out of the handler -- the connection dropped with a traceback on stderr
        instead of the 400 do_POST answers for undecodable JSON."""
        if self.headers.get("Transfer-Encoding"):
            raise _RefusedBody(411, "Transfer-Encoding is not accepted; send the body with a Content-Length")
        get_all = getattr(self.headers, "get_all", None)   # an HTTPMessage in service; a plain dict in unit tests
        if callable(get_all):
            lengths = get_all("Content-Length") or []
        else:
            lengths = [] if self.headers.get("Content-Length") is None else [self.headers.get("Content-Length")]
        if len(lengths) > 1:
            raise _RefusedBody(400, "body could not be read: more than one Content-Length header")
        cl = str(lengths[0]).strip() if lengths else "0"
        if not re.fullmatch(r"[0-9]{1,20}", cl):
            raise _RefusedBody(400, "body could not be read: Content-Length %s is an invalid literal for a byte count (decimal digits only)" % _clip_json(cl))
        n = int(cl)
        if n > _POST_MAX_BYTES:
            raise _RefusedBody(413, "request body of %d bytes exceeds the %d-byte limit" % (n, _POST_MAX_BYTES))
        raw = b""
        if n:
            # The read runs under a socket timeout (_POST_BODY_TIMEOUT), put back afterwards; a unit-test
            # handler over a BytesIO has no connection, and a fake rfile that stalls raises the same
            # socket.timeout the real one would.
            sock = getattr(self, "connection", None)
            prior = sock.gettimeout() if sock is not None else None
            try:
                if sock is not None:
                    sock.settimeout(_POST_BODY_TIMEOUT)
                raw = self.rfile.read(n)
            except socket.timeout:
                raise _RefusedBody(408, "body could not be read: %d bytes announced, not all of it arrived within %g s"
                                   % (n, _POST_BODY_TIMEOUT))
            finally:
                if sock is not None:
                    try:
                        sock.settimeout(prior)
                    except OSError:
                        pass                             # the socket is already gone; the refusal closes it anyway
        if len(raw) < n:
            raise _RefusedBody(400, "body could not be read: read %d of the %d bytes Content-Length announced" % (len(raw), n))
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise _RefusedBody(400, "body must be a JSON object, got %s" % _clip_json(data))
        return data

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
        except _RefusedBody as e:
            self.close_connection = True
            return self._send({"error": e.error}, e.status, close=True)
        except Exception:
            return self._send({"error": "bad json"}, 400)
        if u.path == "/peer":                      # the kernel's tunnel-transition notify (peer-bus mode)
            payload, status = peer_update(data)
            return self._send(payload, status)
        if u.path == "/peer-exchange":             # a peer bus dialing us through the kernel's -L forward
            flight = []                            # the ids of the flights the handler's listings registered
            payload, status = peer_exchange_handle(data, flight=flight)
            # The relays in a 200 leave with this write (2026-09-08): _send writes through the handler's
            # unbuffered wfile (wbufsize 0 — sendall), so a return means the socket took the bytes and a
            # dead socket raises here. Returned → the flight ends carried; raised → freed, nothing left,
            # they ride the next exchange (a re-relay is deduped over there).
            host = _exchange_peer_name(data)
            try:
                self._send(payload, status)
            except Exception:
                for fid in flight:
                    _flight_done(host, fid, carried=False)
                raise
            for fid in flight:
                _flight_done(host, fid, carried=(status == 200))
            return
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
            tracked, terr = _as_bool(data.get("tracked"), "tracked")   # report-back delegation
            if terr:                                   # a string here armed tracking on a plain send
                return self._send({"error": terr}, 400)
            tracked = tracked and kind == "delegate"
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
                # sender as a note — to a session sender; a --from sender has no mailbox, so its
                # refusal is recorded only (_bounce_apply, _parked_note).
                phost, hit = res["host"], res["agent"]
                # `tracked` deliberately does NOT ride the relay: the primary view lives on the
                # SENDER's kernel, which the recipient's courier can never reach across hosts — a
                # satellite with no primary would hide work, so a cross-host tracked send degrades
                # to a plain delegate (revisit with federation) — and the NOTE says so: the sender
                # asked for a report-back and must hear it degraded (fail loudly, 2026-07-03).
                tnote = (" — report-back tracking does not cross hosts yet; sent as a plain handoff"
                         if tracked else "")
                mid = "px-" + _unique()
                # `to` carries the display name (an old-code far side matches names only), and
                # `toId` carries the resolved row's SID (review find 2026-09-01): parking the name
                # ALONE discarded exactly what an id-addressed send chose the sid for — a rename
                # during the park window bounced a rename-proof address as no-live, and two
                # same-named far sessions could silently swap deliveries. A new far side matches
                # toId exactly first; an old one ignores the extra key. Compatible both ways.
                relay_msg = {"mid": mid, "to": hit.get("name") or to,
                             "toId": str(hit.get("id") or ""), "frm": frm,
                             "frm_id": frm_id, "body": body, "kind": kind,
                             "t": int(time.time())}
                if kind == "delegate":
                    # kernel-walked-at-relay provenance (the user 2026-08-27, T126): park time IS
                    # send time (this write), so the walk always runs where the evidence is local
                    ua = _walk_root_record(frm_id)
                    if ua:
                        relay_msg["userAsk"] = ua
                # `to_sid` (2026-09-08): the recipient's STABLE id, the same value the wire's toId
                # carries. The row used to name the recipient only ("<host>:<name>"), so every
                # reader of the wait (the kernel's wait maps, the judge's ask maps) had to join it
                # back to a sid through a name→sid alias learned from the peer's own rows — and a
                # name reused by a NEW session re-keyed every OLD message to the new sid. With the
                # sid on the row the join is exact; the alias stays the fallback for older rows.
                # The sent row lands BEFORE the park, and the park's own failure is answered
                # (2026-09-08): the row is what the sender's receipts and the timeline read, so a
                # park with no row was mail nobody could see, and answering ok regardless of the
                # append was a "sent" the sender could not trust. A row that can't land refuses the
                # send; a park that fails after the row closes the ledger on the id and refuses —
                # either way the sender still holds the text. 503 + ok:false, because _http raises
                # BusError only on a non-2xx: a 200 would read as "Delivered" in the tool and CLI.
                if not _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "sent", "id": mid,
                                                     "from": frm, "from_id": frm_id,
                                                     "to_id": "peer:%s" % phost,
                                                     "toName": "%s:%s" % (phost, hit.get("name") or to),
                                                     "to_sid": str(hit.get("id") or ""),
                                                     "body": body, "kind": kind}):
                    return self._send({"ok": False, "error": NOT_RECORDED_TEXT}, 503)
                if not outbox_put(phost, relay_msg):
                    _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": mid,
                                                  "to": hit.get("name") or to, "host": phost,
                                                  "why": WHY_NOT_PARKED})
                    return self._send({"ok": False, "error": "the message could not be parked for %s "
                                       "(the outbox could not be written), so it was not sent — "
                                       "nothing is lost; retry" % phost}, 503)
                if PEERS.get(phost, {}).get("up"):
                    return self._send({"ok": True, "id": mid,
                                       "note": "relaying to '%s' on %s%s" % (hit.get("name") or to, phost, tnote)})
                _kernel_post("/redial", {"host": phost})   # parking IS demand: ask the kernel to
                #                                             re-dial the host's tunnel now instead of
                #                                             waiting out its backoff (the user 2026-08-16)
                return self._send({"ok": True, "id": mid, "parked": phost,
                                   "note": _parked_note(phost, frm_id) + tnote})
            a0 = res["agent"]
            try:
                mid = deliver(a0["id"], frm, frm_id, body, kind=kind, tracked=tracked)
            except DeliveryNotRecorded as e:
                # 503 + ok:false (see the relay leg): nothing was published; the sender retries.
                return self._send({"ok": False, "error": str(e)}, 503)
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
            kept = []                                # carried outbox items the recall refused (additive:
            removed = _recall(frm_id, data.get("to", ""), data.get("id", ""), kept=kept)   # an older client
            return self._send({"ok": True, "removed": removed, "kept": kept})               # ignores the key)
        if u.path == "/wake":
            sid = data.get("id", "")
            if sid and _safe_id(sid):                         # force-deliver on revive once the prompt is live
                threading.Thread(target=_wake_when_ready, args=(sid,), daemon=True).start()
            return self._send({"ok": True})
        if u.path == "/heartbeat":
            local = _record_heartbeat(data.get("id"), data.get("name", "?"))
            return self._send({"ok": True, "local": local})
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

def _idle_tick(n, idle, answered=True):
    """One autostop decision: (new_idle, stop). Factored out so the gate is unit-testable (the
    monitor loop sleeps). Local clients OR a live local kernel reset the count — a hub with zero
    local sessions still serves inbound peer exchanges as long as its kernel runs (_kernel_up).

    `answered` False means the kernel's listing did not answer. That HOLDS the count only when the
    bus has evidence that sessions existed: the last ANSWERED listing — in memory, or its disk twin
    for a bus that started during the blink — was non-empty (_sessions_were_listed). The outage then
    protects the sessions that were listed before it: the kernel does not own a tmux session's life
    and lists them again when it returns. Until 2026-09-06 local sessions' heartbeats masked an
    outage as presence; with those loops ended in peer mode the gate reads the bit itself. A bus
    that never saw an answered non-empty listing — a kernel-less `romp mail` bus, a box whose kernel
    stopped after its sessions had all gone — keeps the autostop: the count advances every poll and
    stops at IDLE_GRACE. The kernel-less case holds only on a box whose twin holds no rows: a twin left
    by an earlier kernel primes the hold in a bus spawned later the same way. The hold has ONE release,
    the next answered listing (which rewrites the twin): a kernel gone for good after listing sessions
    leaves the bus up until a manual stop or the returning kernel's first answer, and a code-staleness
    re-exec does not end it (the fresh process re-primes the hold from the twin, which outlives the
    re-exec by design) (review find, 2026-09-08; pinned by tests/test_postal_bus_lifetime.py). In
    production an answered listing implies a live kernel (both are the same process), so `answered and
    n == 0` with the kernel down is reached only through the ROMP_SESSIONS_FILE seam; the stop that
    matters is the unanswered one without evidence."""
    if n > 0 or _kernel_up():
        return 0, False
    if not answered and _sessions_were_listed():
        return idle, False
    idle += 1
    return idle, idle >= IDLE_GRACE


def _sessions_were_listed():
    """True iff the last ANSWERED local listing this bus knows of was non-empty: the evidence the
    autostop gate needs before an unanswered listing counts as an outage rather than an absence.
    Primed from the disk twin (_PRESENCE_GOOD_FILE) when this process has not answered yet, so a
    bus that started during the blink is covered."""
    _presence_good_load()
    return bool(_LOCAL_PRESENCE_GOOD[1] and _LOCAL_PRESENCE_GOOD[0])


def _monitor_tick(idle):
    """One poll of _monitor after its sleep: the mail sweeps, the deadness-mirror refresh, and the
    autostop decision. Returns (idle, stop). Factored out so a tick is unit-testable."""
    try:
        _sweep_orphans()    # bounce mail stuck unread in dead recipients' mailboxes
    except Exception:
        pass
    try:
        _warn_stuck_mail()  # warn the sender when a LIVE-but-idle recipient still hasn't read (backstop)
    except Exception:
        pass
    _write_remote_sids()    # the TTL prunes only at write time; the poll is the write that needs no beat to arrive
    try:
        n, answered = present_count_checked()
    except Exception:
        n, answered = 1, True   # on error, err on the side of staying up
    return _idle_tick(n, idle, answered)


def _monitor(httpd, boot_fp=""):
    idle = 0
    while True:
        time.sleep(POLL)
        _maybe_restart_for_code(boot_fp)   # reload if the on-disk code changed under us (does not return on restart)
        idle, stop = _monitor_tick(idle)
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
    markers = [m for m in MAILPENDING.iterdir() if m.is_file()]
    if not markers:
        return                                 # nothing pending: no GET /sessions this pass (2026-09-06)
    live = None                                # fetched once, and only for a marker that still holds mail
    for m in markers:
        sid = m.name
        newd = MAILROOT / sid / "new"
        if not (newd.is_dir() and any(newd.iterdir())):
            _mark_pending(sid)                 # stale marker -> clear it
            continue
        if live is None:
            live = {a["id"]: a for a in local_agents(threads=True)}   # a thread's marker retries like any live session's
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

WHY_STOPPED_BEFORE_PUBLISH = WHY_NOT_PUBLISHED + "the mail service stopped before the message reached the inbox"
WHY_STOPPED_BEFORE_PARK = "not parked: the mail service stopped before the outbox record was written"

def _rebuild_rows_for_rowless_mail(box, sent, ended):
    """The start sweep's pass over <box>/new/ (see _sweep_unfinished_writes): every file whose id the
    ledger has never heard of gets its sent row now, rebuilt from the headers deliver() wrote (From,
    From-Id, Date, X-Park, X-Kind, X-From-Host, X-Peer-Mid) and the body after the blank line, and
    marked `recovered`. The file is never removed and never bounced. Returns how many rows landed."""
    newd = box / "new"
    try:
        files = sorted((f for f in newd.iterdir() if f.is_file()), key=lambda p: p.name) if newd.is_dir() else []
    except OSError:
        return 0
    n = 0
    for f in files:
        if f.name in sent or f.name in ended:
            continue
        try:
            text = f.read_text(errors="replace")
        except FileNotFoundError:
            continue
        except OSError as e:
            _mail_unreadable(f, box.name, e)         # its fields cannot be recovered: aside, said, closed
            continue
        head, _, body = text.partition("\n\n")
        meta = {}
        for ln in head.splitlines():
            k, _, v = ln.partition(": ")
            meta[k.lower()] = v
        try:
            t = int(datetime.strptime(meta.get("date", ""), "%Y-%m-%dT%H:%M:%S%z").timestamp())
        except (ValueError, TypeError, OverflowError):
            try:
                t = int(f.stat().st_mtime)
            except OSError:
                t = int(time.time())
        row = {"t": t, "ev": "sent", "id": f.name, "from": meta.get("from", "?"),
               "from_id": meta.get("from-id", ""), "to_id": box.name,
               "body": body[:-1] if body.endswith("\n") else body,   # deliver() adds exactly one newline
               "recovered": True}                                    # written at start, not by the send
        if meta.get("x-park"):
            row["park"] = True
        if meta.get("x-kind"):
            row["kind"] = meta["x-kind"]
        row["from_host"] = meta.get("x-from-host", "")
        if meta.get("x-peer-mid"):
            row["originMid"] = meta["x-peer-mid"]
        if not _tl_append("messages.jsonl", row):
            _log("mail %s for %s stands in the inbox with no record of it and its row could not be "
                 "written at start; it stays for the next start" % (f.name, box.name))
            continue
        sent.add(f.name)
        n += 1
        if row.get("originMid"):
            peer_seen_add(row["originMid"])         # the dialer's re-relay of the unacked mid: a duplicate
        _log("mail %s for %s stood in the inbox with no record of it (the restart cut the delivery "
             "between the publish and the row); sent row written at start from its headers (a tracked "
             "flag or a user-ask record lived only on the row and cannot be recovered)" % (f.name, box.name))
    return n

def _sweep_unfinished_writes():
    """Bus start: the one event at which no writer of ours is running, so what a crash left on disk
    is reconciled with the ledger (review find, 2026-09-08). Three passes.

    A maildir temp is a message that stopped before its publish, or the leftover of one that landed.
    deliver() publishes first (link, then the temp's unlink) and writes its row second, so a temp
    with no row is a message that never reached the inbox and is simply removed. A temp beside a
    message that STANDS in new/ or cur/ is a publish that landed whose temp could not be removed
    (_publish_new tolerates that unlink's failure): the temp goes and the ledger is left alone. The
    first cut closed such an id as refused, so a message the recipient had already read read refused
    to its sender after every restart. A temp beside a sent row with the message nowhere (a bus that
    wrote row-first) is closed with a terminal `bounced` row, so no receipt reads pending forever for
    a message nothing lists.

    A message in new/ with NO sent row is the residual of the publish-first order: a bus killed
    between the publish and the row, or a take-back whose unlink was refused. The mail is legitimate
    and the next read delivers it, so it is never removed and never bounced (a bounced row with no
    sent row is invisible to the sender's receipts): its sent row is rebuilt from the file's headers
    (_rebuild_rows_for_rowless_mail), marked `recovered`, so the sender's receipt, the timeline and
    the courier see it. A tracked flag or a user-ask record lived only on the row and cannot be
    recovered. A relayed message's origin mid is marked seen, so the dialer's re-relay of the unacked
    mid is acked as a duplicate instead of delivered twice. A file that cannot be read goes aside the
    way read_box sends one (_mail_unreadable). A row that cannot be written now leaves the file for
    the next start.

    The relay leg (row, then outbox_put) has its window still, leaving a `<mid>.json.tmp-*` temp and
    no record; _atomic_json_put's own failure path removes its temp, so only a crash leaves one. Each
    temp is removed (never published: it may be half-written), its id's ledger is closed when a sent
    row stands with no terminal row yet, each is said on stderr, and one bell row reports the sweep.
    A readbox temp is removed and said; a receipt is not a message. The `.corrupt-*` sidecars are
    evidence and are never touched."""
    sent, ended = set(), set()
    try:
        for line in (TLDIR / "messages.jsonl").read_text(errors="replace").splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict) or not o.get("id"):
                continue
            if o.get("ev") == "sent":
                sent.add(str(o["id"]))
            elif o.get("ev") in ("bounced", "recall"):
                ended.add(str(o["id"]))
    except OSError:
        pass

    def _close(mid, row):
        if mid in sent and mid not in ended:
            row.update({"t": int(time.time()), "ev": "bounced", "id": mid})
            if _tl_append("messages.jsonl", row):
                ended.add(mid)
                return True
        return False

    removed, closed, standing, recovered, standing_records = 0, 0, 0, 0, 0
    try:
        boxes = [b for b in MAILROOT.iterdir() if b.is_dir()] if MAILROOT.is_dir() else []
    except OSError:
        boxes = []
    for box in boxes:
        tmpd = box / "tmp"
        try:
            temps = [f for f in tmpd.iterdir() if f.is_file()] if tmpd.is_dir() else []
        except OSError:
            temps = []
        for f in temps:
            published = (box / "new" / f.name).exists() or (box / "cur" / f.name).exists()
            try:
                f.unlink()
            except OSError as e:
                _log("unfinished mail %s for %s could not be removed (%s)" % (f.name, box.name, e))
                continue
            removed += 1
            if published:
                standing += 1
                _log("unfinished mail %s for %s removed at start; the message itself had reached the inbox "
                     "(a temp its publish could not remove), its ledger left alone" % (f.name, box.name))
                continue
            done = _close(f.name, {"to_id": box.name, "why": WHY_STOPPED_BEFORE_PUBLISH})
            closed += done
            _log("unfinished mail %s for %s removed at start%s"
                 % (f.name, box.name, "; its sent row stood with no message published, now closed as refused"
                    if done else ""))
        recovered += _rebuild_rows_for_rowless_mail(box, sent, ended)
    for store, why in ((OUTBOX, WHY_STOPPED_BEFORE_PARK), (READBOX, None)):
        try:
            hostdirs = [h for h in store.iterdir() if h.is_dir()] if store.is_dir() else []
        except OSError:
            continue
        for hostdir in hostdirs:
            try:
                temps = sorted(hostdir.glob("*.json.tmp-*"))
            except OSError:
                continue
            for f in temps:
                mid = f.name.split(".json.tmp-", 1)[0]
                record_stands = (hostdir / (mid + ".json")).exists()
                try:
                    f.unlink()
                except OSError as e:
                    _log("unfinished %s record %s could not be removed (%s)" % (store.name, f.name, e))
                    continue
                removed += 1
                if record_stands:
                    # the temp of a REWRITE of a standing record (_mark_carried publishes the carry mark
                    # through the same temp-and-replace), cut short: the record itself stands and its
                    # message is parked, so its ledger is left alone (2026-09-08). Closing it as never
                    # parked — the reading when outbox_put was the only temp writer — read a parked
                    # message as refused to its sender.
                    standing_records += 1
                    _log("unfinished %s record %s for %s removed at start; the record itself stands (the temp of "
                         "a rewrite the stop cut short), its ledger left alone" % (store.name, f.name, hostdir.name))
                    continue
                if why:
                    closed += _close(mid, {"host": hostdir.name, "why": why})
                _log("unfinished %s record %s for %s removed at start" % (store.name, f.name, hostdir.name))
    parts = []
    if removed:
        parts.append("%d unfinished mail write(s) from before the restart removed; %d sender receipt(s) now "
                     "read refused" % (removed, closed))
        if standing:
            parts.append("%d of them the temp of a message that had reached the inbox, its ledger left alone"
                         % standing)
        if standing_records:
            parts.append("%d of them the temp of a record that stands, its ledger left alone"
                         % standing_records)
    if recovered:
        parts.append("%d delivered message(s) stood in an inbox with no sent row (the restart cut the delivery "
                     "after the publish); each has its row now" % recovered)
    if parts:
        _refused_notice("mail service start: %s (the log names each)" % "; ".join(parts))

def serve():
    STATE.mkdir(parents=True, exist_ok=True)
    MAILROOT.mkdir(parents=True, exist_ok=True)
    _reconcile_markers()
    _sweep_unfinished_writes()             # temps a crash left: removed, said, their ledgers closed (2026-09-08)
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
        with urllib.request.urlopen(req, timeout=15) as r:   # the fetch-budget pair's client half — see _kernel_sessions_checked
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
    up, uerr = _as_bool(data.get("up"), "up")
    if uerr:                                           # a string here marked a DOWN tunnel up
        return {"error": uerr}, 400
    PEERS[host] = {"port": port, "up": up, "at": int(time.time()),
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


def _write_remote_sids():
    """STATE/remote-sids — every session id this box knows to be LIVE on another host (federated
    presence gossip + legacy heartbeats), one per line, atomically. A best-effort mirror for the
    kernel/judge DEADNESS rule (2026-08-28, the dead-session round): a sid absent from the local
    registry but present here is a live REMOTE session whose local mirror store must never be
    presumed closed. The FILE's existence means the bus has spoken; readers treat a missing file
    as "cannot determine" and stay conservative.

    The TTL is applied at WRITE time, so an expired heartbeat leaves the file only when something
    writes it: every recorded heartbeat, every peer exchange, and (since 2026-09-06) every _monitor
    poll. In peer mode (the default) a local session's beats end once its bus confirms it local, and
    remote presence arrives through the peer exchange, which writes here. In legacy singleton mode
    the beats continue; the poll-time write is the backstop for a hub whose local sessions have all
    gone quiet while a dead remote's sid waits out its TTL."""
    try:
        now = time.time()
        ids = {sid for sid, (_nm, ts) in HEARTBEATS.items() if now - ts < HEARTBEAT_TTL}
        for st in PEER_STATE.values():
            for pa in st.get("presence") or []:
                if pa.get("id"):
                    ids.add(str(pa["id"]))
        tmp = STATE / "remote-sids.tmp"
        tmp.write_text("\n".join(sorted(ids)) + ("\n" if ids else ""))
        os.replace(tmp, STATE / "remote-sids")
    except Exception:
        pass


def peers_snapshot():
    """GET /peers: the table, plus WHICH BUS PROCESS is answering (busId, minted per process; epoch, its
    boot second). The kernel compares that pair across its supervisor passes: a change means this bus
    restarted, so it re-tells every peer with its token (kernel _note_bus_incarnation). The /tunnels seed
    below cannot learn tokens (that payload carries none since 2026-09-08), so without this a bus that
    restarted under a running kernel dialed every peer credential-less until a tunnel bounced
    (review find, 2026-09-08)."""
    peers = {}
    for h, p in PEERS.items():
        d = dict(p)
        tt = (PEER_STATE.get(h) or {}).get("theirTier")
        if tt:
            d["theirTier"] = tt        # how that host holds US, from its last exchange declaration
        peers[h] = d
    return {"peers": peers, "viaReach": via_reach(), "remoteHolds": remote_holds(),
            "busId": BUS_ID, "epoch": BUS_EPOCH}

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
_outbox_lock = threading.Lock()            # serializes an outbox record's listing-into-flight, carry mark and
#                                            unlink (recall, ack, bounce): none interleaves (_relays_for, _flight_done)
_inflight = {}                             # host -> {flight id: {mid}}: the records each OPEN exchange is carrying, from
#                                            its listing to its outcome; a recall meanwhile is "on its way" (_recall). Two
#                                            exchanges for one host run at once by design (our dialer and their dial of
#                                            us), so each listing is its own flight and ending one releases nothing another
#                                            still holds. Memory only: a restart ends every exchange, so it empties itself
_flight_seq = itertools.count(1)           # flight ids, minted under _outbox_lock (_relays_for)

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
_postal_host_env_warned = set()              # ROMP_POSTAL_HOST values already said to be unusable — once per value

def self_host():
    """This machine's postal identity: short hostname (each side keys the OTHER by its own name for
    it, so exact agreement across machines is not required). ROMP_POSTAL_HOST overrides (tests) WHEN
    it clears the same rule. The name MUST clear _safe_id: peers key the outbox that holds mail FOR
    us by it, as a path component, so an unkeyable kernel hostname half-works — presence still
    crosses (PEER_STATE is a dict), but outbox_put on the peer refuses every message back, parked
    "unreachable" forever with the only trace a server-log line (2026-08-11, a kern.hostname stomped
    with control bytes). An unsafe name falls back, loudly: the platform's user-set machine name,
    else a minted persisted id. An unsafe OVERRIDE is set aside the same way — said once, naming the
    name used instead — and the derived name is declared: the override used to come back exactly as
    set, the one branch that dodged the rule (2026-09-08; the kernel's _self_host had the same and
    was fixed the same day). gethostname stays first and live, so fixing the machine's hostname
    takes effect on the next call with no restart."""
    global _self_host_fb
    env = os.environ.get("ROMP_POSTAL_HOST")
    if env and _safe_id(env):
        return env
    name = socket.gethostname().split(".")[0]
    if _safe_id(name):
        chosen = name
    else:
        if _self_host_fb is None:
            _self_host_fb = next((s for s in map(_sanitize_host_name, _host_name_candidates()) if s),
                                 "") or _minted_host_id()
            _log("self_host: kernel hostname %r fails path-safety; declaring %r to peers instead "
                 "(fix the machine's hostname to control the name)" % (name, _self_host_fb))
        chosen = _self_host_fb
    if env and env not in _postal_host_env_warned:
        _postal_host_env_warned.add(env)
        shown = env if len(env) <= 60 else env[:57] + "..."
        _log("self_host: ROMP_POSTAL_HOST=%r is not usable as a machine name (letters, digits, dots, hyphens "
             "or underscores, starting with a letter or digit, at most 128 characters); declaring %r to peers "
             "instead. Fix or unset ROMP_POSTAL_HOST to control the name." % (shown, chosen))
    return chosen

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

def _atomic_json_put(path, obj):
    """Publish one JSON store record atomically: a uniquely named temp in the SAME directory (never
    a `*.json`, so the listings' glob cannot see it) → write → fsync → os.replace → best-effort
    directory fsync. A reader (outbox_list on every exchange, the kernel reading the dir) sees the
    old bytes or the new, never a torn file; a crash mid-write leaves a stray temp, not a record the
    listing has to refuse. (2026-09-08: the plain write_text the stores used was the one writer
    that could leave a half-record, and the listings then skipped it silently, every pass, forever.)
    Raises OSError; the caller says so."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp-%d-%s" % (path.name, os.getpid(), os.urandom(4).hex()))
    try:
        with open(tmp, "w") as fh:
            fh.write(json.dumps(obj))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass

def _list_json_records(d, where, close_ledger_for=None):
    """Every parseable `*.json` record under `d`, sorted by name. A record that cannot be parsed
    (or is not an object) is moved aside ONCE to `<name>.corrupt-<utc stamp>[-n]` — the kernel
    stores' quarantine naming, so the `*.json` glob never sees it again — with one _log line, and the
    listing goes on with the rest. It used to skip such a file silently on every pass, forever: a torn
    outbox record was mail parked for nobody with no trace anywhere (2026-09-08). The file's stat is
    fingerprinted BEFORE the read: if it changed by the time the parse failed, a writer's atomic
    rewrite raced the read and the file is left alone for the next pass — a torn read of a healthy
    record is never moved aside. An unreadable file (EACCES, EIO) is moved aside the same way
    (review find, 2026-09-08): the first cut skipped it in place, said once, which re-skipped it
    on every exchange with its mail parked for nobody, the shape the quarantine exists to end. Only
    a file that cannot be moved either stays, skipped and said once per (file, errno).

    `close_ledger_for` (the OUTBOX's host): a parked message's filename IS its mid, and moving the
    record aside is that message's terminal event — without a row the sender's receipt reads
    "pending (not read yet)" forever. A terminal `bounced` row (WHY_OUTBOX_UNREADABLE) is appended
    best-effort after the move; the quarantine itself never depends on the row landing. The move is
    said on stderr and as one bell row on the dashboard (_refused_notice)."""
    out = []
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        st = None
        try:
            st = f.stat()
            rec = json.loads(f.read_text())
            if not isinstance(rec, dict):
                raise ValueError("not a JSON object")
            out.append(rec)
            continue
        except FileNotFoundError:
            continue                                 # gone between the glob and the read (deleted, moved)
        except OSError as e:
            if st is None:                           # the stat itself failed: no fingerprint to move by
                _say_unreadable_once(f, e, where)
                continue
            reason = "unreadable, errno %s: %s" % (e.errno, e.strerror or e)
            fault = e
        except ValueError as e:
            reason, fault = e, None
        try:
            cur = f.stat()
            if (cur.st_ino, cur.st_mtime_ns, cur.st_size) != (st.st_ino, st.st_mtime_ns, st.st_size):
                continue                             # rewritten under us: a torn read, not a torn file
            aside = _aside_name(f)
            os.replace(f, aside)
        except FileNotFoundError:
            continue                                 # a sibling lister moved it first
        except OSError as e:
            if fault is not None:
                _say_unreadable_once(f, fault, where)
            else:
                _say_unreadable_once(f, e, where)    # said once per (file, errno), not per pass
            continue
        _refused_notice("%s: %s could not be %s (%s); moved aside to %s; the rest is served%s"
                        % (where, f.name, "read" if fault is not None else "parsed", reason, aside.name,
                           "; the sender's receipt reads refused" if close_ledger_for else ""))
        if close_ledger_for:
            _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": f.stem,
                                          "host": close_ledger_for, "why": WHY_OUTBOX_UNREADABLE})
    return out

def outbox_put(host, msg):
    """Park one cross-host message for `host` and poke its exchange (long-poll release + dialer).
    Returns True iff the record is on disk (atomic publish); False, said in the log, when it is not.
    `host` and the message `mid` become path components, so both MUST clear _safe_id first: a
    peer-crafted `mid` like `../../../foo` over the unauthenticated bus would otherwise write
    outside OUTBOX (arbitrary-file-write). Legit ids (short hostnames, `_unique()` mids) pass."""
    mid = (msg or {}).get("mid") or ""
    if not (_safe_id(host) and _safe_id(mid)):
        _log("outbox_put: refusing unsafe host/mid %r/%r" % (host, mid))
        return False
    try:
        _atomic_json_put(OUTBOX / host / (mid + ".json"), msg)
    except OSError as e:
        _log("outbox_put %s/%s: the record could not be written (%s) — nothing parked" % (host, mid, e))
        return False
    _peer_wake(host).set()
    return True

def outbox_list(host):
    if not _safe_id(host):
        return []
    return _list_json_records(OUTBOX / host, "outbox %s" % host, close_ledger_for=host)

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
    with _outbox_lock:                           # never interleaved with a listing or a carry mark (_flight_done)
        try:
            (OUTBOX / host / (mid + ".json")).unlink()
            return True
        except Exception:
            return False

_CARRY_KEYS = ("carried", "carriedVia")        # the record's carry mark; never on the wire (_wire_form)

def _mark_carried(host, mid):
    """Record on outbox/<host>/<mid> that an exchange CARRIED it: `carried` (epoch of the first
    departure) and `carriedVia` (the host it left for), published by the same atomic rewrite every
    store record gets (_atomic_json_put), so the mark stands across a bus restart — the ack that
    ends the record may arrive after one. True iff the record stands and is marked (2026-09-08).

    The mark lands AFTER the departure, keyed on the exchange's outcome, never on the request
    being built: a mark at build time survived every failed dial (protocol drift waits 60 s; a
    refused connection during a restart), refusing a recall for bytes that never left. The
    events, per side: the DIALER marks when its dial returns a response (peer_exchange_apply,
    after the acks and bounces, so a record the ack just deleted is simply not there to mark) or
    fails only AFTER the request went out (a read timeout, an undecodable body): the far bus may
    have taken the relays and answered into the void (_peer_exchange_once); the DIALED side marks
    once its response write returned (the /peer-exchange route). A dial the far bus refused with a
    status (any HTTP error precedes relay intake), that never connected (urllib.error.URLError), or
    whose socket closed before any status line came back (through the ssh forward, a far bus not
    listening; review find 2026-09-09) marks nothing. From the listing to that outcome the record is IN FLIGHT
    (_inflight), which the recall refuses as on its way — so no recall is granted for a record on
    the wire, and none is told "left" for one that never did.

    Why an exact mark and not the outbox's residency: a record stays in the outbox until the
    END-TO-END ack, one round trip normally and a whole outage when a response was lost — and the
    far side delivers on arrival; nothing distinguished a carried record from a parked one, so a
    recall in that window unlinked a message the recipient already held (_recall has the rest). A
    re-relay after a lost response finds the mark and writes nothing: the first departure is the
    fact. False when the record is gone (acked, bounced or recalled first) or when the mark could
    not be written — then the record RODE and its departure is not recorded, so a recall can still
    withdraw it (the behaviour before this mark); said in the log by id. Takes _outbox_lock; the
    _locked form is for callers already holding it (_flight_done)."""
    with _outbox_lock:
        return _mark_carried_locked(host, mid)

def _mark_carried_locked(host, mid):
    if not (_safe_id(host) and _safe_id(mid)):
        return False
    rec = outbox_get(host, mid)
    if not rec:
        return False
    if rec.get("carried"):
        return True
    rec["carried"], rec["carriedVia"] = int(time.time()), host
    try:
        _atomic_json_put(OUTBOX / host / (mid + ".json"), rec)
    except OSError as e:
        _log("outbox %s/%s: it rode an exchange but its departure could not be recorded (%s); a recall "
             "can still withdraw it" % (host, mid, e))
        return False
    return True

def _wire_form(m):
    """The record as it rides an exchange: the carry mark stays home. It is this bus's bookkeeping
    (a forwarding hub would otherwise inherit a `carried` it never carried and re-mark it on its own
    departure), and keeping it off the wire keeps a record's measured size under the relay budget
    the same before and after its first departure."""
    return {k: v for k, v in m.items() if k not in _CARRY_KEYS}

def _flight_done(host, flight_id, carried):
    """The OUTCOME of one exchange for the flight its listing registered (_relays_for): the flight is
    popped, and when `carried` — the bytes reached the far bus (the events are in _mark_carried) —
    each of its records takes the durable mark first, under the one lock, so no instant exists in
    which a record is neither in flight nor marked. `carried` False frees the flight's records: the
    request never arrived, and a recall wins again exactly as before the listing — unless another
    OPEN flight for the host still holds the record (two exchanges for one host run at once by
    design), in which case it stays on its way until that one ends. Ending one flight never
    releases what another holds: the first cut kept one set per host, and the first outcome to
    land released a record the other exchange was still carrying (review find, 2026-09-08). A
    second call for the same flight, or an unknown id, is a no-op: the fold-then-exception path in
    _peer_exchange_once relies on that."""
    with _outbox_lock:
        mids = (_inflight.get(host) or {}).pop(flight_id, None)
        if not mids:
            return
        if carried:
            for mid in mids:
                _mark_carried_locked(host, mid)

def _mark_relays_carried(host, relays):
    """Mark wire `relays` (anything with a mid) carried, for a fold that runs without a flight to end
    (peer_exchange_apply driven directly — the tests' build → handle → apply): a response still means
    the relays reached the far bus."""
    with _outbox_lock:
        for m in relays or []:
            mid = str((m or {}).get("mid") or "")
            if mid:
                _mark_carried_locked(host, mid)

def readbox_put(host, rec):
    """Park one read receipt for `host` (readbox/<host>/<mid>.json) and poke its exchange. Keyed by
    the RELAY mid, so the latest state wins: a read superseded by a rolled-back claim (unread=True)
    leaves one file carrying the retraction, never both. Same at-least-once + idempotent-apply
    contract as the outbox — it survives a bus restart and re-sends until the peer confirms."""
    mid = (rec or {}).get("mid") or ""
    if not (_safe_id(host) and _safe_id(mid)):   # host/mid are path components — block traversal
        _log("readbox_put: refusing unsafe host/mid %r/%r" % (host, mid))
        return False
    try:
        _atomic_json_put(READBOX / host / (mid + ".json"), rec)
    except OSError as e:
        _log("readbox_put %s/%s: the receipt could not be written (%s) — not parked" % (host, mid, e))
        return False
    _peer_wake(host).set()
    return True

def readbox_list(host):
    if not _safe_id(host):
        return []
    return _list_json_records(READBOX / host, "readbox %s" % host)

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
    stripped, so it can never loop — the same one-hop-max rule relays live by.

    Returns True iff the receipt was APPLIED (the row landed, or the forward is parked) and False
    when it was not, so the caller keeps it ON THE WIRE (review find, 2026-09-08): the dialer
    withholds its readAck, the dialed side names it in the response's `readsKept`, and the peer
    re-sends it next exchange. Before this readbox_put's new False was ignored: a forwarded receipt
    the store could not take was acked and gone, where the base's raised OSError had aborted the
    exchange and left it to retry. A receipt that can NEVER apply (an unaddressable mid, an origin
    no peer of ours owns) reads as applied: there is nothing a retry could change."""
    mid = (r or {}).get("mid") or ""
    if not _safe_id(mid):
        return True
    origin = str((r or {}).get("origin") or "")
    if origin and origin != self_host():
        if PEERS.get(origin):                    # only toward a peer the kernel told us about
            fwd = {"mid": mid, "t": r.get("t")}
            if r.get("dmid"):
                fwd["dmid"] = r.get("dmid")
            if r.get("unread"):
                fwd["unread"] = True
            return readbox_put(origin, fwd)      # False (said by readbox_put) → the peer re-sends it
        return True
    ev = "unexec" if r.get("unread") else "exec"
    row = {"t": int(r.get("t") or time.time()), "ev": ev, "id": mid}
    d = str((r or {}).get("dmid") or "")
    if _safe_id(d):
        row["dmid"] = d   # the recipient's own delivery mid → the timeline's exact turn join (2026-08-06)
    return _tl_append("messages.jsonl", row)

def _bounce_apply(host, b):
    """A peer refused one of our parked messages — return it to the SENDER as a bus-authored note,
    loudly, and drop it from the outbox. Parking never outlives a definitive refusal.

    Order (2026-09-08): the terminal row, then the return note, then the delete. The record leaves
    the outbox only once the ledger holds its last word and the sender holds the note; before this
    the delete came FIRST and a crash between it and the row lost the accounting entirely. If either
    step fails the record stays: the next exchange re-relays it, the peer's dedupe re-bounces it,
    and the attempt repeats (a repeated terminal row is harmless — _sent_receipts keys by id).

    Any OTHER failure of the note is bounded the same way (review find, 2026-09-08): a mailbox that
    cannot be made, a temp that cannot be written (ENOSPC lands here, before the row): each used to
    escape this function and abort the WHOLE exchange, and with the record now kept until it is
    accounted the peer re-bounced it next exchange and the abort recurred forever, every other
    relay, ack and receipt in that exchange lost with it. The record stays, the exchange goes on,
    the next one retries the note; said once per message, on stderr and as a bell row, since a
    fault that recurs on every exchange is one the user should see.

    A sender id that is NO mailbox gets no note and holds nothing up (2026-09-10): `romp mail send
    --from <label>` mails under the synthetic id `ext:<label>` (cli_send), which _safe_id refuses
    (no ':'), so deliver() to it raises ValueError — not a fault that clears next exchange but the
    shape of the id. Counted as a fault above, the record stayed parked, the next exchange
    re-relayed it, the peer re-bounced it, and every round wrote another `bounced` row and cost the
    peer a listing, for as long as the two buses talked. The record retires on the bounced row, and
    the log line is the one place a person can see the refusal: the row stands in messages.jsonl,
    but no reader reaches it for such a sender (/sent refuses an id _safe_id refuses, cli_sent and
    cli_recall read the session's own id, the dashboard's mail view needs a local lane). Whether
    /sent should answer ext: ids is a separate decision, not taken here."""
    mid = (b or {}).get("mid") or ""
    msg = outbox_get(host, mid)
    if not msg:
        return                                       # nothing parked under that id (recalled, or a torn
        #                                              record the listing moves aside) → nothing to account
    why = (b or {}).get("why") or "refused"
    if not _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "bounced", "id": mid,
                                          "to": msg.get("to") or "?", "host": host, "why": why}):
        _log("bounce for %s from %s: the terminal row did not land — the record stays parked" % (mid, host))
        return
    if msg.get("frm_id") and not _safe_id(msg["frm_id"]):
        _log("bounce for %s from %s: no return note — the sender %s mailed under the id %s, which is no "
             "mailbox (a --from label mails this way); the bounced row stands in the ledger and this line "
             "is the one record of the refusal a person can read"
             % (mid, host, msg.get("frm") or "?", str(msg["frm_id"])[:40]))
    elif msg.get("frm_id"):
        note = "undeliverable to '%s' on %s: %s" % (msg.get("to") or "?", host, why)
        if not (b or {}).get("omitBody"):   # a SIZE bounce (_budget_relays) names the problem instead of repeating it
            note += "\n\n(your message follows)\n%s" % (msg.get("body") or "")
        try:
            deliver(msg["frm_id"], "romp-postal", "", note, kind="coordinate")
        except DeliveryNotRecorded as e:
            _log("bounce for %s from %s: the return note was refused (%s) — the record stays parked" % (mid, host, e))
            return
        except Exception as e:
            if mid not in _NOTE_FAILED_SAID:
                _NOTE_FAILED_SAID.add(mid)
                _refused_notice("bounce for %s from %s: the return note to %s could not be delivered (%s: %s); "
                                "the record stays parked and the note is retried next exchange"
                                % (mid, host, _name_for_id(msg["frm_id"]) or str(msg["frm_id"])[:8],
                                   type(e).__name__, e))
            return
        _NOTE_FAILED_SAID.discard(mid)
    outbox_del(host, mid)

_NOTE_FAILED_SAID = set()   # mids whose return note failed outright (not a refusal); said once each

_LOCAL_PRESENCE_GOOD = [[], False]   # [rows, ever_answered] — the last ANSWERED local listing
_PRESENCE_SERVE_WARNED = [False]     # transition-only logging, the _REG_SERVE_WARNED idiom
_PRESENCE_GOOD_FILE = STATE / "local-presence-good.json"   # …and its DISK twin: a bus restart
#   overlapping a kernel restart (the normal self-update path — both restart on code staleness)
#   would otherwise boot with an empty cache and gossip the blink as authoritative emptiness
#   (review find 2026-09-01); persisting rides the cache across bus restarts, like remote-sids.


def _presence_good_load():
    """Prime the in-memory last-answered cache from its disk twin, once, at first need."""
    if _LOCAL_PRESENCE_GOOD[1]:
        return
    try:
        rows = json.loads(_PRESENCE_GOOD_FILE.read_text())
        if isinstance(rows, list):
            _LOCAL_PRESENCE_GOOD[0], _LOCAL_PRESENCE_GOOD[1] = rows, True
    except Exception:
        pass                                         # no twin yet (fresh box) → nothing to prime


def _remember_presence(rows):
    """Record an ANSWERED local listing as the last-good rows, in memory and in the disk twin
    (_PRESENCE_GOOD_FILE). Read by _local_presence while the kernel does not answer, and by the
    autostop gate's _sessions_were_listed."""
    _LOCAL_PRESENCE_GOOD[0], _LOCAL_PRESENCE_GOOD[1] = rows, True
    try:
        tmp = _PRESENCE_GOOD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows))
        os.replace(tmp, _PRESENCE_GOOD_FILE)     # the disk twin follows every answered read
    except Exception:
        pass


def _local_presence():
    """Local agent rows for an exchange payload, blink-honest: an UNANSWERED kernel listing (a
    mid-restart blink) must never gossip as "nobody lives here" — the far boxes' PEER_STATE flaps
    empty and their resolvers mint hard no-live refusals for sessions that never died (specimen
    2026-08-31: three sends refused across a far restart while the target stayed live throughout).
    Serve the last ANSWERED rows instead (the registry's serve-last-good idiom, 2026-08-31); an
    ANSWERED-empty listing is the truth — it serves and caches as such. This protects every peer,
    including ones running older code, because the honesty lands in the payload itself."""
    rows, answered = local_agents_checked()
    if answered:
        _remember_presence(rows)
        if _PRESENCE_SERVE_WARNED[0]:
            _PRESENCE_SERVE_WARNED[0] = False
            sys.stderr.write("postal: the local listing answers again — presence serves live rows\n")
        return rows
    _presence_good_load()                            # a fresh bus process primes from the disk twin
    if _LOCAL_PRESENCE_GOOD[1]:
        if not _PRESENCE_SERVE_WARNED[0]:
            _PRESENCE_SERVE_WARNED[0] = True
            sys.stderr.write("postal: the local listing didn't answer — presence serves the last "
                             "answered rows until it does\n")
        return list(_LOCAL_PRESENCE_GOOD[0])
    return rows                                     # never answered ANYWHERE yet → claim nothing either way


def fleet_presence(exclude_host):
    """Presence for an exchange payload: local agents + ONE hop of gossip from our other peers, each
    labeled `via` (plans/postal-peer-buses.md 3b) — so a spoke can address the far spoke through the
    hub. A via-entry is never re-gossiped (one-hop reach only; a topology needing two hops should
    check the second spoke in to the hub directly). Each via row also carries the far bus's own id
    (`viaBus`): the receiver folds gossip about a box it ALREADY peers with directly, and the id is
    the identity that survives nickname drift — the same machine wears different ssh aliases on
    different hosts, so a name can't say "same box" (the user 2026-08-12; see _via_duplicate)."""
    out = list(_local_presence())
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

def _quarantine_put(origin, m, to_id, via="", wire_id=None):
    """Hold one inbound relay from a directed host: quarantine/<mid>.json with everything approve needs
    to replay deliver(). Idempotent by mid (a resend overwrites the same file, never double-holds).
    `via` is the DIRECT peer it arrived from — kept so an approved delivery still carries the
    read-receipt route (older held records lack it; approve falls back to the origin).
    `wire_id` (2026-09-08) is the sid the WIRE message was addressed to (intake's sanitized toId),
    stored as `toWireId` so approve knows whether the sender chose a sid or a name: the record used
    to keep only the RESOLVED local sid, and once that session ended approve re-matched by NAME —
    handing id-addressed mail to whatever session wore the name, the very swap intake refuses. None
    reads the wire toId off `m`; either way a malformed shape is dropped (never a path, never a
    match key), exactly as intake blanks it."""
    mid = m.get("mid") or ""
    if not _safe_id(mid):
        return False
    wire = str((m.get("toId") if wire_id is None else wire_id) or "")
    if wire and _ID_FORM_RE.fullmatch(wire) is None:
        wire = ""
    rec = {"mid": mid, "to": m.get("to") or "", "toId": to_id, "frm": m.get("frm") or "?",
           "frmId": m.get("frm_id") or "", "body": m.get("body") or "", "kind": m.get("kind") or "",
           "origin": origin, "via": via or origin, "at": int(time.time())}
    if wire:
        rec["toWireId"] = wire
    if isinstance(m.get("userAsk"), dict):
        rec["userAsk"] = m["userAsk"]                # held with its provenance; approve replays it (T126)
    try:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        tmp = QUARANTINE / (mid + ".tmp")
        tmp.write_text(json.dumps(rec))
        tmp.rename(QUARANTINE / (mid + ".json"))      # atomic publish (the kernel may be reading the dir)
        _refusal_over("quarantine")                   # a hold landed: the next refusal here is a new episode
        _log("quarantine: held %s from %s -> %s (directed)" % (mid, origin, rec["to"]))
        return True
    except OSError as e:
        # No card says this (the kernel only reads the dir), so the log says it on every refusal, and
        # the USER hears it once per episode as a bell row, the way deliver() says a refused publish:
        # the directed arm answers 'retry', so the sender re-relays the message every exchange while
        # its receipt reads carried, and a store that stays unwritable would otherwise be a lasting
        # fault with no surface anyone watches. Keyed on the one store; the next hold that lands re-arms it.
        text = "quarantine %s from %s: the hold could not be written (%s) — nothing held" % (mid, origin, e)
        if _REFUSAL_SAID.get("quarantine"):
            _log(text)
        else:
            _REFUSAL_SAID["quarantine"] = True
            _refused_notice(text + "; the sender holds the text and re-relays until the store can be written")
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
        note = " ".join(str(feedback or "").split())
        if note and rec.get("origin") and rec.get("frm"):
            gist = " ".join(str(rec.get("body") or "").split())[:60]
            body = ('Your message to %s ("%s%s") was reviewed there and declined — it was not '
                    "delivered. Note from the reviewer: %s"
                    % (rec.get("to") or "?", gist, "…" if len(gist) == 60 else "", note))
            fb_mid = _unique()
            # The note parks BEFORE the hold is dropped, and a park that fails refuses the deny
            # (review find, 2026-09-08): outbox_put's False was ignored here, so a deny whose note
            # the outbox could not take dropped the held message, answered ok, and the reviewer's
            # words went nowhere with nothing saying so. The hold stands; the user retries, or
            # denies without a note.
            if not outbox_put(rec["origin"], {"mid": fb_mid, "to": rec["frm"], "frm": "Romp Postal Service",
                                              "frm_id": "", "body": body, "kind": "coordinate"}):
                return False, ("the note to the sender could not be parked for %s (the outbox could not be "
                               "written), so nothing was done: the held message is untouched; retry, or "
                               "deny without a note" % rec["origin"])
            _peer_wake(rec["origin"]).set()
        quarantine_del(mid)
        return True, None
    if action == "approve":
        body = str(text) if (text is not None and str(text).strip()) else (rec.get("body") or "")
        to_id = rec.get("toId") or ""
        # ONE checked snapshot (2026-09-01): this arm paid TWO kernel fetches — its own fetch-pair
        # TOCTOU, and the pair alone (2×6s) could outlast the kernel's client cap on /quarantine/act
        # (the 830 budget-pair discipline: the halves move together — see _bus_quarantine_act). And
        # an approve clicked during a kernel restart read the blink as "no longer live": unanswered
        # is not absence, so it refuses retryably instead (the held message stays put).
        agents, answered = local_agents_checked()
        if not answered:
            return False, ("the liveness source (the romp kernel) didn't answer — likely "
                           "mid-restart. The held message is untouched; retry the approve shortly.")
        live = {a["id"] for a in agents}
        if to_id not in live:                         # the held sid is gone: renamed/revived, or ended
            wire = str(rec.get("toWireId") or "")
            if wire:
                # ID-ADDRESSED mail (2026-09-08): the sender chose a sid, so only that sid may take
                # it — the same id-strict rule intake applies (_relay_in: "never a name fallback,
                # which could hand the mail to a same-named sibling"). Before this, approve
                # re-matched by NAME unconditionally, and id-addressed mail whose recipient had
                # ended went to whatever session now wore the name. The held sid IS the wire id
                # (intake matched the wire's toId exactly and held that match), so a held sid the
                # listing no longer carries means nothing live answers to the id the sender chose:
                # there is no second candidate to look for (review find, 2026-09-08: a re-match on
                # the wire id here could never succeed). Refuse loudly; the record stays held (deny
                # carries a note back to the sender). Worded on what the listing proved, no live
                # session by that id, never "ended": a dormant session is absent from it too.
                return False, ("no live session carries the id this message was addressed to ('%s', "
                               "id %s), and a session that now wears the name is not the one the "
                               "sender chose, so it was not delivered. It stays held: deny it (with a "
                               "note, so the sender hears) or leave it."
                               % (rec.get("to") or "?", wire[:8]))
            # NAME-addressed (an older sender): the name still rules. A record held BEFORE
            # 2026-09-08 lands here too even when its wire chose a sid, the hold kept only the
            # resolved sid then, so nothing on the record can tell the two apart; that name
            # re-match is a known residual for holds from before the upgrade, and it drains with
            # their next approve/deny (review find, 2026-09-08).
            match = [a for a in agents if a["name"] == rec.get("to") and not _postal_off(a["id"])]
            if not match:
                return False, "recipient '%s' is no longer a live local session" % (rec.get("to") or "?")
            to_id = match[0]["id"]
        try:
            deliver(to_id, rec.get("frm") or "?", rec.get("frmId") or "", body, kind=rec.get("kind") or "",
                    from_host=rec.get("origin") or "",
                    relay_mid=rec.get("mid") or "", relay_via=rec.get("via") or rec.get("origin") or "",
                    user_ask=rec.get("userAsk"))
        except DeliveryNotRecorded as e:
            return False, "%s — the held message is untouched" % e
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
    # ONE listing snapshot for every ruling below. The isolation bounce used to re-fetch the
    # listing after the match filtered it — and a blink between the two fetches (agent absent,
    # then healed) minted a FALSE isolation, final by norm, with no flag involved (specimen
    # 2026-08-30: a delegate bounced "isolation" with no flag set on either kernel and the
    # maildir delivering minutes either side). Same fetch-pair race the sending resolver's
    # one-fetch fix killed (2026-08-31); `answered` feeds the death-ruling gate at the tail.
    agents, listing_answered = local_agents_checked()
    to_id = str(m.get("toId") or "")
    if to_id and _ID_FORM_RE.fullmatch(to_id) is None:
        to_id = ""            # a malformed wire toId degrades to name matching — it must NEVER reach
        #                       the durable-registry read below (a crafted "../" would turn that stat
        #                       into a path-traversal alive-oracle; skeptic find 2026-09-01)
    # toId (a sender-resolved SID) matches EXACTLY when present — never a name fallback, which
    # could hand the mail to a same-named sibling, the ambiguity the sid exists to bypass. Mail
    # from an older sender has no toId and matches by name/id-shape as before.
    named = ([a for a in agents if str(a.get("id") or "") == to_id] if to_id
             else [a for a in agents if _addr_matches(a, to)])
    match = [a for a in named if not _postal_off(a["id"])]
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
            try:
                deliver(match[0]["id"], m.get("frm") or "?", m.get("frm_id") or "", m.get("body") or "",
                        kind=m.get("kind") or "", from_host=origin,
                        relay_mid=mid, relay_via=host,       # read-receipt route: back through the direct peer
                        user_ask=m.get("userAsk"))           # origin-kernel walked record rides through (T126)
            except DeliveryNotRecorded as e:
                # nothing landed → NOT acked and not marked seen: silence crosses the wire as
                # 'retry', the sender's outbox keeps it parked and re-relays it next exchange
                _log("relay %s from %s: local delivery refused (%s) — the sender re-relays" % (mid, host, e))
                return "retry", None
        elif trust == "directed":
            # HELD for human approve/deny/edit; never injects; remembers whether the wire chose a sid
            # (approve is id-strict then). The hold is a file named by the mid, so an id that cannot
            # name one is refused for good: 'retry' would have the sender re-relay it every exchange.
            if not _safe_id(mid):
                return "bounce", {"mid": mid, "why": "the message id is malformed; it cannot be held for approval"}
            if not _quarantine_put(origin, m, match[0]["id"], via=host, wire_id=to_id):
                # the hold did not land (said by _quarantine_put, with the OSError's cause).
                # Acking here told the sender 'delivered' for mail nothing holds, and marking the mid
                # seen deduped its re-relay away: lost on both ends, no record. Silence instead, as
                # the trusted arm's refused delivery: the sender's outbox keeps it parked and
                # re-relays it next exchange, and the hold lands once the store writes again.
                _log("relay %s from %s: the hold could not be written — the sender re-relays" % (mid, host))
                return "retry", None
        # else isolated → drop: ack so the sender stops resending, but deliver nothing (no communication).
        # An isolated host normally never peers at all (the kernel forces its notify down), so this is a
        # defensive backstop for the checkin-peer path where the mobile dials our /peer-exchange.
        peer_seen_add(mid)
        return "ack", None
    if named:
        # every live candidate's mailbox flag read TRUE in THIS snapshot — a genuine flag ruling,
        # the only thing allowed to mint an isolation bounce (finality makes this arm zero-tolerance)
        return "bounce", {"mid": mid, "why": "recipient '%s' has its mailbox off (postal isolation)" % to}
    if not m.get("origin"):                          # one hop MAX: a message that already hopped never re-forwards
        # route by the SID when the mail carries one, by name otherwise (skeptic finds
        # 2026-09-01, both rounds): the hub's name-only forward final-bounced a sid-addressed
        # message whose session renamed during the park window — gossip rows carry ids, and
        # _addr_matches routes them. Pinned mail routes by sid OR NOT AT ALL: a name fallback
        # here forwarded the same mid to a NAMESAKE's host when gossip blinked the sid out
        # (per-host hold dedupe let both parks stand), and the wrong host's final bounce could
        # beat the right host's delivery ack back to the sender.
        fh, hit = (peer_route(to_id) if to_id else peer_route(to))
        if fh and fh != host and not (hit or {}).get("via"):
            if outbox_get(fh, mid) is None:          # a resend while we hold it forwards nothing twice
                if not outbox_put(fh, dict(m, origin=host)):
                    # not parked (said by outbox_put): 'hold' would tell the sender its mail is on
                    # its way when nothing holds it (review find, 2026-09-08); silence instead, so
                    # the sender's outbox keeps it and re-relays it next exchange
                    return "retry", None
            return "hold", None
    # death-ruling gate — the relay leg's honesty arms (2026-08-31; the sending resolver got these
    # in the answered-but-absent round, this inbound leg never did): an UNANSWERED listing proves
    # nothing (a mid-restart kernel yields exactly this), and an answered listing that omits a name
    # whose durable registry entry stands is a blink, never a death. Both read as 'retry' — a
    # verdict neither ack'd nor bounced crosses the wire as SILENCE, so the sender's outbox keeps
    # the mail parked and re-relays it next exchange: the honest retry-shortly, where a bounce is
    # final. The mid stays out of peer_seen, so the re-relay is processed in full.
    # …and when the mail PINS a sid, the corroboration is by that id ALONE: the name arm read a
    # same-named REPLACEMENT session's standing reg as evidence and held a genuinely-gone sid in
    # never-healing silent retry — the sender parked forever, never told (skeptic repro
    # 2026-09-01). A pinned sid whose reg is gone everywhere bounces FINAL and honestly: the
    # session it named no longer exists, however many namesakes live on.
    if not listing_answered or (_durable_session(to_id, True) if to_id else _durable_session(to, False)):
        return "retry", None
    return "bounce", {"mid": mid, "why": "no live session named '%s' on %s" % (to, self_host())}

def _ack_arrived(host, mid):
    """An end-to-end ack for outbox/<host>/<mid>: clear it. If we only FORWARDED it, relay the ack
    backward to the origin host; if it was ours, log the delivered receipt. The receipt (or the
    backward ack) comes FIRST and the delete last (2026-09-08): a record that leaves the outbox
    before its receipt exists is a delivery the sender never hears of if the row then fails. A row
    that does not land keeps the record; the next exchange re-relays it, the peer's dedupe re-acks
    it, and the receipt is retried."""
    msg = outbox_get(host, mid)
    if not msg:
        return                                       # nothing parked under that id → nothing to account
    if msg.get("origin"):
        p = _pending(msg["origin"])
        with _peer_lock:
            p["acks"].append(mid)
        _peer_wake(msg["origin"]).set()
    elif not _tl_append("messages.jsonl", {"t": int(time.time()), "ev": "relayed", "id": mid, "host": host}):
        _log("ack for %s from %s: the delivered receipt did not land — the record stays parked" % (mid, host))
        return
    outbox_del(host, mid)

def _bounce_arrived(host, b):
    """A bounce for outbox/<host>/<mid>: relay it backward if we only forwarded the message, else
    return it to our local sender. The backward bounce is queued BEFORE the delete (2026-09-08)."""
    mid = (b or {}).get("mid") or ""
    msg = outbox_get(host, mid)
    if msg and msg.get("origin"):
        p = _pending(msg["origin"])
        with _peer_lock:
            p["bounces"].append(b)
        _peer_wake(msg["origin"]).set()
        outbox_del(host, mid)
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


# The most of one host's outbox a single exchange carries (review find, 2026-09-08). The dialed bus reads
# the request only up to _POST_MAX_BYTES, and the request also carries presence, holds, reads and acks,
# so the relays get half of it. build_exchange_request used to send the WHOLE outbox: a backlog past the
# cap (a dozen 100 KB reports parked through one tunnel outage) was 413'd, and the dialer re-sent the
# identical request on every backoff, forever: every message to that peer parked on a healthy link,
# against the parking contract above (a link being DOWN is the only reason to park).
_RELAY_BUDGET_BYTES = _POST_MAX_BYTES // 2


def _budget_relays(host, msgs, budget=None):
    """The oldest-first prefix of `msgs` that fits `budget` bytes serialized -> the relays for ONE
    exchange; the rest ride the next round (the dialed side answers at once when it has acks to return,
    so nothing waits out a long-poll). The first relay that fits always rides, so every round makes
    progress. A relay that alone exceeds the budget can never cross, so it is bounced through the path a
    peer's refusal takes (_bounce_arrived: to our local sender, or backward to the origin that forwarded
    it) as a note naming the size, without the body, and leaves the outbox instead of being retried
    forever."""
    budget = _RELAY_BUDGET_BYTES if budget is None else budget
    out, used = [], 0
    for m in msgs:
        m = _wire_form(m)                            # measured and emitted as it rides: the carry mark stays home
        n = len(json.dumps(m).encode("utf-8"))
        if n > budget:
            _log("peer %s: relay %s is %d bytes, over the %d-byte exchange limit; bounced to its sender"
                 % (host, m.get("mid"), n, budget))
            _bounce_arrived(host, {"mid": m.get("mid"), "omitBody": True,
                                   "why": "message of %d bytes exceeds the %d-byte relay limit" % (n, budget)})
            continue
        if used + n > budget:
            break
        out.append(m)
        used += n
    return out

def _relays_for(host, flight=None):
    """The relays ONE exchange carries for `host`, from either side of it: the outbox's budgeted prefix
    (_budget_relays), in wire form, registered under _outbox_lock as ONE flight of this exchange's own
    — a record a recall removed between the listing and here is left out, so nothing rides that the
    ledger has closed, and from here to the exchange's outcome (_flight_done) a recall meets the
    on-its-way refusal instead of unlinking a record whose bytes may already be delivered. `flight`
    is a list the caller passes to receive the flight's id (an out-parameter, so the wire dict gains
    nothing); the caller ends it at the outcome. A listing that finds nothing registers no flight, and
    neither does one with no list to hand the id to: a listing with no flight to end is a read, not a
    departure (the tests' direct build → handle → apply; the fold then marks by mid), and an entry
    nobody can end must never be left in _inflight. A record another open flight already holds is
    listed all the same: skipping it would hold a message
    back for up to a dial timeout when only one direction of the link works. Nothing durable is
    written here: the carry mark waits for the outcome (_mark_carried)."""
    rel = _budget_relays(host, outbox_list(host))     # outside the lock: an oversize relay bounces from here
    out, mids = [], set()
    with _outbox_lock:
        for m in rel:
            mid = m.get("mid") or ""
            if not mid or outbox_get(host, mid) is None:
                continue                             # gone since the listing (recalled, acked, bounced)
            mids.add(mid)
            out.append(m)
        if mids and flight is not None:
            fid = next(_flight_seq)
            _inflight.setdefault(host, {})[fid] = mids
            flight.append(fid)
    return out


def _exchange_peer_name(data):
    """The name the dialed side files a dialer under: its declared host, canonicalized to the alias we
    already peer with when its busId proves it is that bus (_canon_peer_name). ONE function for the
    handler and for the route that marks the response's relays carried after the write (2026-09-08):
    the handler's own canonicalization files the busId row this lookup reads, so the two agree."""
    host = str((data or {}).get("host") or "").strip()
    return _canon_peer_name(host, str((data or {}).get("busId") or ""))

def peer_exchange_handle(data, flight=None):
    """The DIALED side of one exchange. Returns (payload, status). Relays ride under _RELAY_BUDGET_BYTES
    (_budget_relays), the request side's bound mirrored, so the response is bounded the same way. The
    relays it hands out are IN FLIGHT from the listing (_relays_for, which appends the flight's id to
    `flight`, a list the route passes); the departure event is the response write, so the route ends
    the flight carried once _send returned (2026-09-08), and freed when it raised — nothing here
    touches the record or the wire form."""
    host = str((data or {}).get("host") or "").strip()
    if not host:
        return {"error": "host required"}, 400
    if (data or {}).get("proto") != PEER_PROTO:
        return {"error": "peer protocol drift (theirs %r, ours %r) — update romp on one side"
                % ((data or {}).get("proto"), PEER_PROTO), "proto": PEER_PROTO}, 409
    # Canonicalize BEFORE anything keys on the name: presence files under the alias (no duplicate
    # session rows), and the relays below are trust-judged under the alias the user actually tiered.
    bus_id = str((data or {}).get("busId") or "")
    host = _exchange_peer_name(data)
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
    _write_remote_sids()                           # presence changed → refresh the deadness mirror
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
    reads_kept = []                                  # request-carried receipts we could NOT apply: named in
    for r in data.get("reads") or []:                # the response so the dialer keeps them for its next
        if not _read_arrived(host, r):               # request (additive; an older dialer ignores the key
            reads_kept.append({"mid": (r or {}).get("mid"), "unread": bool((r or {}).get("unread"))})
    acks, bounces = [], []                           # and drops them, exactly as before)
    for m in data.get("relays") or []:
        verdict, bounce = _relay_in(host, m, token_proven=True)   # past the HTTP gate = showed OUR serve token
        if verdict == "ack":
            acks.append(m.get("mid"))
        elif verdict == "bounce" and bounce:
            bounces.append(bounce)                   # 'hold': forwarded — its ack comes back later;
        #                                              'retry': silence on purpose — the sender re-relays

    def _drain_backflow():                           # acks/bounces relayed BACK through us for this host
        p = _pending(host)
        with _peer_lock:
            a2, b2 = p["acks"], p["bounces"]
            p["acks"], p["bounces"] = [], []
        return a2, b2

    a2, b2 = _drain_backflow()
    acks, bounces = acks + a2, bounces + b2
    reads, rel = readbox_list(host), _relays_for(host, flight)   # the listing last: it puts records in flight
    if not rel and not reads and data.get("wait") and not acks and not bounces:
        # nothing to hand back → park on the wake so anything we accept mid-wait crosses instantly
        _peer_wake(host).clear()
        _peer_wake(host).wait(EXCHANGE_WAIT)
        reads, rel = readbox_list(host), _relays_for(host, flight)
        a2, b2 = _drain_backflow()
        acks, bounces = acks + a2, bounces + b2
    # `reads` stay parked until the dialer's NEXT request readAcks them — a response can vanish
    # after we send it, so the dialed side never clears on send. Re-applying a duplicate is a no-op.
    try:
        return {"host": self_host(), "epoch": BUS_EPOCH, "proto": PEER_PROTO, "busId": BUS_ID,
                "presence": fleet_presence(host), "holds": holds_payload(host),
                "tier": my_tier_of(host),            # how WE hold the dialer's mail (display/mirror, never the gate)
                "relays": rel, "acks": acks, "bounces": bounces, "reads": reads,
                "readsKept": reads_kept}, 200        # see _read_arrived (2026-09-08)
    except Exception:
        for fid in flight or []:
            _flight_done(host, fid, carried=False)   # no response will carry them: freed, they ride next time
        raise

def build_exchange_request(host, wait=True, flight=None):
    """The DIALER's request for one exchange. The relays are the outbox's oldest-first prefix under
    _RELAY_BUDGET_BYTES (_budget_relays); the rest ride the next round, so a backlog drains over a few
    exchanges instead of being refused whole."""
    p = _pending(host)
    with _peer_lock:
        acks, bounces, read_acks = list(p["acks"]), list(p["bounces"]), list(p.get("readAcks") or [])
    req = {"host": self_host(), "epoch": BUS_EPOCH, "proto": PEER_PROTO, "busId": BUS_ID,
           "presence": fleet_presence(host), "holds": holds_payload(host),
           "tier": my_tier_of(host),                 # how WE hold the dialed host's mail
           "acks": acks, "bounces": bounces,
           "reads": readbox_list(host), "readAcks": read_acks, "wait": bool(wait)}
    req["relays"] = _relays_for(host, flight)    # may bounce (a backward bounce joins the origin's pending
    #                                              queue): after the snapshot. Each relay is IN FLIGHT from
    #                                              here (a recall is told it is on its way) until the dial's
    #                                              outcome marks it carried or frees it (_peer_exchange_once,
    #                                              through the flight id `flight` receives; the wire gains nothing).
    #                                              The LAST step on purpose: nothing after it can raise and
    #                                              leave a record in flight with no outcome to free it
    return req

def peer_exchange_apply(host, req_sent, resp, flight=None):
    """The DIALER's half: fold one exchange response in. `req_sent` is the request that produced it —
    its included acks/bounces/readAcks are now delivered and leave the pending queue (kept on send
    failure), and its reads leave the readbox: a response means the dialed side processed the whole
    request before answering, so request-carried receipts need no explicit ack, except the ones the
    dialed side names in `readsKept` (review find, 2026-09-08): those it could not apply, and they
    stay parked for the next request. Likewise a response-carried read WE could not apply gets no
    readAck, so the dialed side keeps it and re-sends it."""
    p = _pending(host)
    with _peer_lock:
        p["acks"] = [a for a in p["acks"] if a not in (req_sent.get("acks") or [])]
        p["bounces"] = [b for b in p["bounces"] if b not in (req_sent.get("bounces") or [])]
        p["readAcks"] = [a for a in p.get("readAcks") or [] if a not in (req_sent.get("readAcks") or [])]
    kept = {(str(k.get("mid") or ""), bool(k.get("unread")))
            for k in (resp.get("readsKept") or []) if isinstance(k, dict)}
    for r in req_sent.get("reads") or []:
        if (str((r or {}).get("mid") or ""), bool((r or {}).get("unread"))) in kept:
            continue                                 # the dialed side could not take it: it rides again
        readbox_del(host, r)
    PEER_STATE[host] = {"presence": resp.get("presence") or [], "epoch": resp.get("epoch"),
                        "holds": resp.get("holds") or [], "seenAt": int(time.time())}
    _write_remote_sids()                           # presence changed → refresh the deadness mirror
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
    # A response means the dialed side processed the whole request: the relays it carried have
    # LEFT (2026-09-08). Marked after the acks and bounces above, so a record they just closed is
    # simply not there to mark, and no temp is ever written beside a record about to be deleted.
    # `flight`: the ids the request's build registered (_peer_exchange_once passes them); a caller
    # without one (build → handle → apply driven directly) still marks the relays it carried.
    if flight:
        for fid in flight:
            _flight_done(host, fid, carried=True)
    else:
        _mark_relays_carried(host, req_sent.get("relays") or [])
    for r in resp.get("reads") or []:                # response-carried receipts: apply, ack on the NEXT dial
        if not _read_arrived(host, r):
            continue                                 # not applied → no readAck: the dialed side re-sends it
        with _peer_lock:
            p["readAcks"].append({"mid": r.get("mid"), "unread": bool(r.get("unread"))})
    for m in resp.get("relays") or []:
        verdict, bounce = _relay_in(host, m)
        with _peer_lock:
            if verdict == "ack":
                p["acks"].append(m.get("mid"))
            elif verdict == "bounce" and bounce:
                p["bounces"].append(bounce)          # 'hold': forwarded — its ack comes back later;
            #                                          'retry': silence on purpose — the sender re-relays

_ID_FORM_RE = re.compile(r"[0-9a-fA-F][0-9a-fA-F-]{7,35}")   # uuid / short-id address shapes


def _addr_matches(a, to):
    """Does agent row `a` answer to address `to` — by name, by full session id, or by the short-id
    prefix form every list_agents row prints (`· <8-char>`)? Review find 2026-09-01: presence rows
    have always CARRIED ids, but the route matched names only, so a uuid send to a live far-host
    session had no route at all — it fell through to the refusal tail and read as deadness (and a
    first-cut mirror arm here turned that into a never-healing retry; routing is the honest cure).
    Prefix collisions surface as multiple candidates: peer_route's standing ambiguity machinery
    refuses multi-hits; _relay_in prefers the exact toId a new-code sender parks (see the relay
    message), so its name matching is the old-sender compatibility path only."""
    if a.get("name") == to:
        return True
    sid = str(a.get("id") or "")
    if not sid:
        return False
    return sid == to or (_ID_FORM_RE.fullmatch(to) is not None and sid.startswith(to))


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
            if not _addr_matches(a, to) or _via_duplicate(a, direct_bus):
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

def _peer_exchange_once(host, port, token):
    """One exchange with `host`, from the request to the folded-in response. Returns the OUTCOME, which
    is also the carry event for the request's relays (2026-09-08; _flight_done):
    - 'ok': a response. peer_exchange_apply folds it in and marks the relays carried after the acks
      and bounces; if the fold itself fails the relays are marked here (the far bus answered, so it
      processed them) and the failure is said.
    - 'lost': raised AFTER the request went out (a read timeout, an undecodable body, anything else
      urllib does not wrap): the far bus may have taken the relays and answered into the void, so
      they are marked carried; the re-relay next round is deduped over there.
    - 'unsent': urllib.error.URLError — the connect or the send failed (refused, unresolved, a connect
      timeout, a pipe broken mid-send): the request never arrived; nothing is marked. Also a socket
      that closed before any status line came back (RemoteDisconnected, a reset): through the ssh
      forward that is a far bus not listening, not an answer lost (the arm below has the shape).
    - 'drift': HTTP 409, the protocol handshake refused it before any relay was read; nothing marked.
    - 'refused': any other HTTP status — the token gate, the unsafe-host check, a bad body — all of
      which precede relay intake (a handler exception over there yields no response at all, not a
      status); nothing marked. Each distinct refusal is said once.
    _peer_loop keys its waits on the return."""
    flight = []                                  # the id of the flight the build registers (out-parameter)
    req = build_exchange_request(host, wait=True, flight=flight)

    def _end(carried):
        for fid in flight:
            _flight_done(host, fid, carried=carried)
    try:
        resp = _peer_http(port, req, token)
    except urllib.error.HTTPError as e:
        _end(False)                                  # answered before intake: nothing left
        if e.code == 409:
            st = PEER_STATE.setdefault(host, {})
            if st.get("drift") != "proto":
                st["drift"] = "proto"
                _log("peer %s: protocol drift — update romp on one side" % host)
            return "drift"
        body = ""
        try:
            body = " ".join((e.read() or b"").decode("utf-8", "replace").split())[:200]
        except Exception:
            pass
        st = PEER_STATE.setdefault(host, {})
        if st.get("refused") != (e.code, body):      # each DISTINCT refusal once, not per retry —
            st["refused"] = (e.code, body)           # a 4xx (e.g. the unsafe-host gate) otherwise
            _log("peer %s: exchange refused (HTTP %s) %s" % (host, e.code, body))   # retries silently forever
        return "refused"
    except urllib.error.URLError:
        _end(False)                                  # the request never arrived
        return "unsent"
    except (http.client.RemoteDisconnected, http.client.BadStatusLine, ConnectionResetError, BrokenPipeError):
        # The socket closed before any status line came back. urllib wraps only the connect and the
        # send (h.request) in URLError; a failure in getresponse propagates raw, and through the
        # kernel's ssh -L forward that is the shape of a far bus that is not listening (its restart
        # window, a crash): the LOCAL ssh listener accepts the TCP connection, the request goes out
        # to it, ssh fails the channel and closes the socket, and no bus ever read a byte. The generic
        # arm below took that for a lost answer and marked every record in the flight carried, a
        # durable mark, so through every far-bus restart the sender was refused a recall for messages
        # still in its own outbox (review find, 2026-09-09). A far handler that died AFTER delivering
        # also closes without a status line, but the re-relay next round is deduped over there by id:
        # re-sending is safe where a false carried mark is not, so nothing is marked.
        _end(False)
        return "unsent"
    except Exception:
        _end(True)                                   # it went out; the answer was lost
        return "lost"
    try:
        peer_exchange_apply(host, req, resp, flight=flight)
    except Exception as e:
        _log("peer %s: apply failed: %s" % (host, e))
        _end(True)                                   # the far bus answered: the relays reached it (a no-op
        #                                              when the fold got as far as ending the flight itself)
    return "ok"

def _peer_loop(host):
    """One dialer per up peer: exchange, fold the response in, repeat — the dialed side's long-poll
    sets the pace, so a healthy loop is one parked request, not a poll. Exponential backoff (capped
    30s) on connection errors only; exits when the kernel marks the peer down or the flag drops.
    The body is _peer_exchange_once; this loop only paces it."""
    fails = 0
    while peers_on():
        p = PEERS.get(host)
        if not p or not p.get("up"):
            break
        outcome = _peer_exchange_once(host, p["port"], p.get("token") or "")
        if outcome == "ok":
            fails = 0
            continue
        if outcome == "drift":
            _peer_wake(host).clear()
            _peer_wake(host).wait(60)
            continue
        fails += 1
        _peer_wake(host).clear()
        _peer_wake(host).wait(min(30, 2 ** min(fails, 5)))
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
    seed from the kernel's /tunnels so peering resumes without waiting for the next transition. The seed
    learns each peer's port, up-state and trust; the peer's TOKEN is not in that payload (a page reads it
    too, 2026-09-08), so a seeded row cannot dial yet. The kernel supplies it: its next supervisor pass
    sees a new busId/epoch on GET /peers and re-notifies every peer (peer_update fills the token in)."""
    try:
        req = urllib.request.Request(KERNEL_BASE + "/tunnels", headers={"X-Romp-Token": SERVE_TOKEN})
        with urllib.request.urlopen(req, timeout=3) as r:
            payload = json.loads(r.read().decode() or "{}") or {}
        rows = payload.get("tunnels") or []
        for row in rows:
            port = row.get("busPort")
            if row.get("host") and isinstance(port, int) and port:
                peer_update({"host": row["host"], "port": port, "up": row.get("status") == "up",
                             "trust": row.get("trust") or "directed"})   # per-host trust for the inbound gate
                # no "token": /tunnels has not carried one since 2026-09-08; the kernel's re-notify brings it
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
    # the tunnel hint belongs to the legacy singleton scheme: in peer mode (the default) an SSH'd box runs
    # its own bus and `romp mail remote` refuses, so pointing at it would be a dead end (review find, 2026-09-08)
    if not peers_on() and (is_client_only() or looks_remote()):
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
    """POST this session's presence to the bus. Returns the bus's answer ({ok, local}) or None when
    there was none (bus unreachable, no sid); callers that only want the side effect ignore it."""
    if not sid:
        return None
    try:
        return _http("POST", "/heartbeat", {"id": sid, "name": name or "?"})
    except Exception:
        return None

_LOCAL_CONFIRMED = [False]   # this MCP's session is local to its bus, per a peer-mode `local: true` answer

def _heartbeat_once():
    """One beat of _heartbeat_loop: ONE identity resolution (one GET /sessions) and one POST /heartbeat.
    Returns True iff the bus answered `local: true` — its own answered listing holds this sid, so the
    kernel already publishes this session's presence. A bus that did not answer, answered without the
    bit (an older bus), or said local=false (a remote session, or a kernel mid-restart that left the
    listing unanswered) is False: keep beating.

    In peer mode a true answer also latches _LOCAL_CONFIRMED, which _mcp_call reads to skip its
    per-call beat: there the bus behind BASE is this box's own for the life of the process, so the
    verdict cannot go stale. It is never latched from an unanswered or false answer, and never in
    legacy singleton mode (ROMP_POSTAL_PEERS=0), where `romp mail remote` can replace the local bus
    with the hub's over an -R tunnel under a running session — the beats are then its only presence.
    The peer-mode premise, that `romp mail remote` never swaps the bus behind BASE there, is enforced:
    setup_remote refuses in peer mode, --force included (review find, 2026-09-08: run after the latch
    it silenced the session on the hub), as _remote_nudge already treated the command as moot."""
    sid, name = _self_identity()
    if not sid:
        return False
    resp = _heartbeat(sid, name)
    local = bool(isinstance(resp, dict) and resp.get("local") is True)
    if local and peers_on():
        _LOCAL_CONFIRMED[0] = True
    return local

def _heartbeat_loop(interval=None, stop=None):
    """Keep THIS session present to the bus while it's alive, so an IDLE session that hasn't touched a postal
    tool is still addressable. This is essential for a REMOTE (federated) session: it appears to the laptop's
    bus ONLY via heartbeats over the -R tunnel (a local session is already visible through the kernel's
    /sessions). Cadence well under HEARTBEAT_TTL. Runs from the stdio MCP server, which lives exactly as long
    as the Claude session.

    The bus ignores heartbeats from LOCAL sids, and since 2026-09-06 says so in its answer. In PEER mode
    (the default) the loop ends on the first `local: true`: every box runs its own bus, a session stays
    local to it for the life of the process, and every further beat would be a no-op that cost the kernel
    GET /sessions calls (about 30 sessions beating every 30 s were 3 requests per second on a saturated
    kernel). In legacy singleton mode (ROMP_POSTAL_PEERS=0) the loop never ends: `romp mail remote`
    replaces the local bus with the hub's over an -R tunnel while sessions run, and from then on these
    beats are the only presence the hub sees. A remote (client-only box) session never hears "local"
    from the hub's bus in either mode. `interval` and `stop` (a threading.Event) are test seams; the
    defaults are the production cadence and no external stop.

    What the ended loop changes for a peer's send during a KERNEL BLINK (review find, 2026-09-08): a
    local session's beat that landed while the listing was unanswered used to be filed as remote
    presence (the bus could not call it local), so a send to its name during the blink resolved to that
    row and delivered into its mailbox with no wake, and the mail sat there unannounced until the
    kernel returned. With the loop ended and the per-call beat skipped no such row appears, and
    resolve_recipient's standing blink refusal (503, retry shortly, never a death ruling) covers every
    local peer alike: the mail stays with the sender, who is told so. Pinned by
    tests/test_postal_heartbeat_fetches.py."""
    if interval is None:
        interval = max(15, HEARTBEAT_TTL // 3)
    while not (stop is not None and stop.is_set()):
        try:
            if _heartbeat_once() and peers_on():
                return
        except Exception:
            pass
        if stop is not None:
            stop.wait(interval)
        else:
            time.sleep(interval)
SWITCH_POLL = max(0.05, float(os.environ.get("ROMP_POSTAL_SWITCH_POLL", "2")))   # seconds between stats of the user-todos switch file (_SwitchWatch); the tools/list_changed latency a connected session sees. Floored: never a busy loop

class _SwitchWatch:
    """Change detector for the user-todos switch, behind the stdio server's tools/list_changed
    poll (2026-09-07). tools/list reads the file live, so a NEW connection always sees the right
    list; a session ALREADY connected keeps the list it was given until told to re-list, and the
    kernel writes the file from another process, so the file is the only seam — polled, cheaply:
    flipped() is one stat per call, reads the file only when its (mtime, size) moved, and answers
    True only when the OFFERED list changed (_user_todos_on flipped). A rewrite that keeps the
    value — the kernel restamping `gt` — is not a list change. The first call baselines."""

    def __init__(self):
        self.sig = self._sig()
        self.on = _user_todos_on()

    @staticmethod
    def _sig():
        try:
            st = USER_TODOS_SWITCH.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None                        # absent (the shipped default) is a signature too

    def flipped(self):
        sig = self._sig()
        if sig == self.sig:
            return False
        self.sig = sig
        on = _user_todos_on()
        if on == self.on:
            return False
        self.on = on
        return True

def _switch_poll_loop(on_switch_flip, stop=None):
    """The user-todos switch poll (2026-09-07), on a thread of its own: every SWITCH_POLL seconds stat the
    switch file (_SwitchWatch) and call `on_switch_flip` once per flip of the OFFERED tool list, so a session
    connected while the gear is flipped gains or loses the pair within seconds in both directions. Its own
    thread, not the heartbeat's: in peer mode _heartbeat_loop ENDS on the bus's first `local: true`, while
    this poll must run for the life of the stdio server. `stop` (a threading.Event) is a test seam."""
    watch = _SwitchWatch()
    while not (stop is not None and stop.is_set()):
        try:
            if watch.flipped():
                on_switch_flip()
        except Exception as e:
            _log("switch poll: %s" % e)
        if stop is not None:
            stop.wait(SWITCH_POLL)
        else:
            time.sleep(SWITCH_POLL)


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
    # The two user-todo tools (plans/user-todos.md) describe an obligation to the PERSON THE AGENT
    # WORKS FOR, so unlike the peer-mail tools above their descriptions follow the veil: no romp
    # machinery named (test_injected_voice.py scans them). Note the caller-identity caveat: postal
    # resolves who's calling from the CLI process env, so a SUBAGENT's call registers the todo as
    # its parent session — the right behavior (the need belongs to the session the user talks to),
    # it just means "who filed this" is always the session, never an individual subagent.
    {"name": "add_user_todo",
     "description": "Flag something you need from the person you work for — a decision, an input, or an action only they can provide — while you keep working on what you can. Give one short line saying what you need and why; add detail only if the line can't carry it. Returns an id: withdraw it (withdraw_user_todo) the moment the need is met or moot. Not for status updates or FYIs — only things you are waiting on them for.",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string", "description": "one short line: what you need from them and why"},
                                    "detail": {"type": "string", "description": "optional longer context, only when the short line can't carry it"}},
                     "required": ["text"]}},
    {"name": "withdraw_user_todo",
     "description": "Take back a need you flagged (by id) once it's met, answered some other way, or no longer applies — so the person you work for doesn't act on a stale request.",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "string", "description": "the id add_user_todo returned"}},
                     "required": ["id"]}},
    {"name": "check_sent",
     "description": "See your recently sent messages and whether each was read/acted on by the recipient yet, or is still pending — instead of asking 'did you get it?'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "recall_message",
     "description": "Withdraw a message that is still here when the ask went moot: unread mail to a session on this machine, or mail to another machine that has not left for it yet (check_sent shows which). Give 'to' to withdraw your message(s) to them, or add 'id' (from check_sent) for one. Only your own; anything already read, or already on its way to another machine, is gone.",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "recipient session name (or UUID) whose queued message(s) from you to cancel"},
                                    "id": {"type": "string", "description": "optional specific message id (from check_sent) to recall just that one"}}}},
]

USER_TODO_TOOLS = ("add_user_todo", "withdraw_user_todo")   # the pair the user-todos switch governs
# What a call anyway hears while the switch is off (a session that connected while it was on still
# holds the tool). Plain and LOUD — the agent must not believe the need was filed — and in the voice
# the descriptions use (test_injected_voice.py scans it): no tracking-system nouns.
USER_TODOS_OFF_ADD = ("User todos are turned off on this machine, so this was not saved and the person you "
                      "work for will NOT see it. Say what you need in your next reply instead.")
USER_TODOS_OFF_WITHDRAW = ("User todos are turned off on this machine, so there is nothing to withdraw. "
                           "Nothing changed.")


def _tools_offered():
    """The tools/list answer: MCP_TOOLS, minus the two user-todo tools while the kernel's per-install
    switch is off (the user 2026-09-03) — a tool that cannot succeed is not offered, so no session
    learns a capability this machine has turned off. Read at LIST time, per call (_user_todos_on)."""
    if _user_todos_on():
        return MCP_TOOLS
    return [t for t in MCP_TOOLS if t["name"] not in USER_TODO_TOOLS]

def _mcp_call(name, args):
    mid, me = _self_identity()               # one GET /sessions for both halves, not one each
    if not _LOCAL_CONFIRMED[0]:              # a confirmed-local session's beat is a no-op the bus pays a fetch for
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
        tracked, terr = _as_bool(args.get("tracked"), "tracked")
        if terr:
            return ("Cannot send: %s. Pass a JSON boolean (tracked: true), not a string." % terr, True)
        tracked = tracked and kind == "delegate"
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
        if "text" not in args or args.get("text") is None:
            # a MISSING param is never a clear command (fold-in 2026-08-31: a malformed call
            # silently wiped the published note); the documented clear stays text=''
            return ("set_working needs its `text` argument — nothing was changed. "
                    "Pass text='' if you mean to clear your published note."), True
        text = args.get("text", "")
        _publish_working(mid, text)        # backend-agnostic kernel store (POST /working), not the @romp-working var
        return ("Cleared your 'working on' note." if not text.strip()
                else "Published — others see: working on '%s'." % text), False
    if name == "add_user_todo":
        # Register a need with the person the agent works for (plans/user-todos.md) — the kernel
        # owns the store (POST /usertodo, the set_working/_publish_working shape) and mints the id.
        # `mid` is the calling SESSION (a subagent's call files under its parent — see MCP_TOOLS).
        if not _user_todos_on():
            # the per-install switch (the user 2026-09-03): refused BEFORE any post, plainly — the
            # kernel's route would answer 409 anyway, but the agent hears why, not "try again"
            return USER_TODOS_OFF_ADD, True
        if not mid:
            return "Not inside a romp session.", True
        text = str(args.get("text") or "").strip()
        if not text:
            return "Need 'text' — one short line: what you need from them and why.", True
        detail = str(args.get("detail") or "").strip()
        if len(text) > USER_TODO_TEXT_CAP or len(detail) > USER_TODO_DETAIL_CAP:
            # the kernel's caps, refused HERE so the agent hears why (its 400 would reach this
            # tool as None): the note is one short line, the rest belongs in the reply
            return ("Not saved — that is too long for a note (the line holds %d characters, the "
                    "detail %d). Keep the note to one line and put the rest in your reply."
                    % (USER_TODO_TEXT_CAP, USER_TODO_DETAIL_CAP)), True
        res = _kernel_post("/usertodo", {"id": mid, "text": text, "detail": detail})
        tid = res.get("todoId") if isinstance(res, dict) else None
        if not tid:
            # LOUD, never a silent drop: an unsaved need the agent believes is filed is exactly
            # the vanishing this tool exists to stop. _kernel_post answers None for every non-2xx,
            # so the one refusal this tool can tell apart is the kernel's 409: the switch flipped
            # between the check above and the post — a re-read of the same file names it, and
            # spares the agent a retry that would refuse forever (2026-09-07).
            if not _user_todos_on():
                return USER_TODOS_OFF_ADD, True
            return ("Couldn't save that — the person you work for will NOT see it. Say what you "
                    "need directly in your next reply instead, or try again shortly."), True
        return ("Noted (id %s) — the person you work for will see it. Withdraw it "
                "(withdraw_user_todo) the moment the need is met or moot." % tid), False
    if name == "withdraw_user_todo":
        # Take back a flagged need, by id. An unknown or already-cleared id is a LOUD, plain
        # answer — never a silent success (plans/user-todos.md).
        if not _user_todos_on():
            return USER_TODOS_OFF_WITHDRAW, True     # the switch, as for add_user_todo above
        if not mid:
            return "Not inside a romp session.", True
        tid = str(args.get("id") or "").strip()
        if not tid:
            return "Need 'id' — the one add_user_todo returned when you flagged the need.", True
        res = _kernel_post("/usertodo/withdraw", {"id": mid, "todoId": tid})
        if not isinstance(res, dict):
            return "Couldn't withdraw '%s' — it still stands. Try again shortly." % tid, True
        if res.get("ok"):
            return "Withdrawn — '%s' no longer stands." % tid, False
        # ok:false: the kernel's ACCOUNT (state / at / owner, 2026-09-07) says which kind of
        # nothing-to-do this was, and only one is the agent's error. A row the person already
        # answered or dismissed, or one this session already withdrew, means the need no longer
        # stands, which is what the caller wanted: a plain answer, said in full (never a silent
        # success), but NOT flagged as an error. Two sessions read the old one-size error as a
        # failure and folded a met need into an error path. An id that is not this session's own,
        # or unknown, stays the error it always was.
        state = str(res.get("state") or "")
        if state == "unknown" and "owner" in res and res["owner"] is None:
            # the kernel could not LOOK: the store on disk is not one it can read (its shape
            # guard flagged the file, 2026-09-07). Neither "not yours" nor closed — the row, if
            # there is one, still stands, and nothing was stamped.
            return ("Couldn't read the store that holds these notes, so '%s' was not withdrawn. "
                    "Nothing changed; if the need is met, say so in your next reply." % tid), True
        if state == "unknown" and res.get("owner") is True:
            # the asker's OWN row, in a shape the kernel could not read (a damaged or hand-edited
            # record): neither "not yours" nor closed. The kernel's error names the part it could
            # not read; the agent's move is to say the need aloud.
            return ("Couldn't read the record of '%s' (%s). Nothing changed; if the need still "
                    "stands, say it directly in your next reply."
                    % (tid, res.get("error") or "its closing record is unreadable")), True
        if res.get("owner") is False or state == "unknown":
            return "No note '%s' of yours. Nothing changed." % tid, True
        when = _when_words(res.get("at"))
        if state in ("answered", "dismissed"):
            return ("Already closed: the person you work for %s '%s'%s. Nothing to withdraw."
                    % (state, tid, when)), False
        if state == "withdrawn":
            return "Already withdrawn: '%s' was taken back%s. Nothing changed." % (tid, when), False
        # a kernel that predates the account answers ok:false alone: the old one-size answer
        return ("No open note '%s' of yours — it was already answered, dismissed, or "
                "withdrawn. Nothing changed." % tid), True
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
        res = _http("POST", "/recall", {"from_id": mid, "to": to, "id": rid})
        removed, kept = res.get("removed", []), res.get("kept", [])
        if not removed and not kept:
            return ("Nothing to recall — no unread message from you matched "
                    "(it was already read or delivered, or nothing's queued there).", False)
        lines = []
        if removed:
            lines.append("Recalled %d message(s) before they were read:" % len(removed))
            for r in removed:
                lines.append("  ✕ to %s: %s" % (r["to"], r["body"]))
        for k in kept:                           # an outbox item the bus refused to withdraw (2026-09-08): carried
            #                                      (it left; the tail says what that may mean) or in flight (try
            #                                      again in a moment — never "too late" in the same breath)
            why = k.get("why") or (WHY_CARRIED % k.get("host", "?"))
            tail = "; they may already have read it" if k.get("carried") else ""
            lines.append('  ✗ NOT recalled — to %s (id %s): it %s%s. "%s"'
                         % (k.get("to", "?"), k.get("id", "?"), why, tail, k.get("body", "")))
        return "\n".join(lines), False
    return "Unknown tool: %s" % name, True

def mcp():
    """Hand-rolled stdio MCP server (newline-delimited JSON-RPC). stdout carries
    ONLY protocol messages; everything else goes to stderr. Two threads write it — the
    request loop's replies and the poll thread's unsolicited tools/list_changed — so every
    write goes through reply(), one whole line per acquisition of out_lock."""
    ensure()
    # Heartbeat presence while this session lives, so an idle REMOTE (federated) session stays addressable
    # over the -R tunnel even before it uses a postal tool. A LOCAL session's loop ends on the bus's first
    # `local: true` answer (the bus ignores local beats anyway; see _heartbeat_loop).
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    out = sys.stdout
    out_lock = threading.Lock()
    ready = threading.Event()      # the client finished initializing; unsolicited notices wait for it

    def reply(obj):
        with out_lock:
            out.write(json.dumps(obj) + "\n")
            out.flush()

    def list_changed():
        # The user-todos switch flipped under a connected session: tell the client to re-list
        # (the listChanged capability declared at initialize). Before the handshake completes
        # the flip needs no notice — the first tools/list reads the file live — and _SwitchWatch
        # has already consumed it, so it is not replayed later.
        if ready.is_set():
            reply({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    # The user-todos switch poll: fires list_changed on a flip (_switch_poll_loop). Its own thread — the
    # heartbeat's ends once the bus calls this session local, and the poll must outlive it.
    threading.Thread(target=_switch_poll_loop, args=(list_changed,), daemon=True).start()

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
                    "capabilities": {"tools": {"listChanged": True}},   # the switch poll's notification (list_changed)
                    "instructions": MCP_INSTRUCTIONS,
                    "serverInfo": {"name": "romp-postal-service", "version": "1.0"}}})
            elif method == "notifications/initialized":
                ready.set()   # notification: no response; unsolicited notices may flow from here on
            elif method == "ping":
                reply({"jsonrpc": "2.0", "id": mid_, "result": {}})
            elif method == "tools/list":
                ready.set()   # a client that lists has initialized (set BEFORE the read: a flip the poller
                              # sees from here on is notified; one it saw earlier is in this answer)
                reply({"jsonrpc": "2.0", "id": mid_, "result": {"tools": _tools_offered()}})   # the user-todo pair only while the switch is on
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
    mid, me = _self_identity()
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
    mid, me = _self_identity()
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
        res = _http("POST", "/recall", {"from_id": my_id() or "", "to": to, "id": rid})
    except BusError as e:
        sys.stderr.write("[romp mail] %s\n" % e); return 1
    removed, kept = res.get("removed", []), res.get("kept", [])
    for k in kept:                               # an outbox item the bus refused to withdraw (2026-09-08); the
        why = k.get("why") or (WHY_CARRIED % k.get("host", "?"))   # "too late" tail only for one that has left
        tail = "; they may already have read it" if k.get("carried") else ""
        print("[romp mail] not recalled: the message to '%s' (id %s) %s%s" % (k.get("to", "?"), k.get("id", "?"), why, tail))
    if removed:
        print("[romp mail] recalled %d message(s) to '%s' before they were read" % (len(removed), to))
    elif not kept:
        print("[romp mail] nothing to recall (already read or delivered, or none queued to '%s')" % to)
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
    manual step (the tunnel, which can only be opened from the laptop). Legacy
    singleton scheme only: peer mode refuses, --force included (see below)."""
    if peers_on():
        # Peer mode (the default since 2026-07-20) has no laptop bus to point at, and since the
        # heartbeat latch (2026-09-06) the command would do harm here: a local session's MCP stops
        # beating for good once its own bus confirms it local, so a hub's bus swapped in behind the
        # port would never hear of this box's sessions, and the local bus this command stops is the
        # one carrying their mail. Refuse before any side effect, --force included, and say why
        # (review find, 2026-09-08: `romp mail remote --force` after the latch silenced the session
        # on the hub). _remote_nudge already treats the command as moot in peer mode.
        print("romp mail remote is off in peer mode (the default): every machine runs its own Romp")
        print("Postal Service bus and cross-host mail rides the kernel's peer tunnels, so there is no")
        print("laptop bus to point this machine at. Nothing was changed.")
        print("")
        print("--force does not override this: a session's heartbeats end for good once its own bus")
        print("confirms it local, so a hub's bus swapped in behind the port would never see this box's")
        print("sessions, and the local bus this command would stop is what carries their mail.")
        print("This command belongs to the legacy singleton scheme (ROMP_POSTAL_PEERS=0).")
        return 2
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
  romp mail remote                  connect this (remote) machine to your laptop's bus (legacy scheme, ROMP_POSTAL_PEERS=0)
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
