#!/usr/bin/env python3
"""Stored Claude logins (T346, the user 2026-09-11: a personal and an enterprise Claude account under one
email, and a session billed to either, the way the Billing row offers Login vs API key).

The registry of the logins a romp install knows BESIDE the machine's own signed-in account, so a session
can be billed to any one of them (docs/reference.md, "Several Claude logins"). One record per login under
STATE/logins/<id>.json, metadata only:

    {id, label, email?, org?, kind?, addedAt, acctDigest?, tokenCmd, refused?, refusedAt?}

`label` is the user's word at add time (romp cannot read an account out of a token, so the label is all it
knows about one it never sees); `email`, `org` and `kind` ("personal" | "enterprise") ride only when the
add flow could read them from the CLI's own record. The credential itself, a `claude setup-token` bearer,
lives WHEREVER the user keeps it and nowhere in romp (the user 2026-09-11): `tokenCmd` is a shell command
that prints the token on demand (1Password's `op read op://vault/item/field` is the documented example;
romp never imports or assumes 1Password, it only runs the command), and a session billed to the login gets,
in its per-session settings layer, an apiKeyHelper naming bin/romp-login-helper with the record id, which
runs that command for the CLI per request. Nothing in this module reads, logs, returns or formats a token;
a record's availability is its metadata alone (a command recorded, not refused, not a year old).

stdlib only, loaded by path as `romp_logins` from the kernel and the SDK backend."""
import json
import os
import re
import secrets
import shlex
import time
from pathlib import Path

LOGINS_DIR = "logins"
ID_RE = re.compile(r"^[0-9a-f]{12}$")
# a 1Password secret reference, the documented example of a token command's target: op://<vault>/<item>/<field>,
# or with a section op://<vault>/<item>/<section>/<field> (`romp login add --op <ref>` writes `op read` for it)
OP_REF_RE = re.compile(r"^op://[^/\r\n\t]+/[^/\r\n\t]+/[^/\r\n\t]+(?:/[^/\r\n\t]+)?$")   # item titles may carry spaces
TOKEN_CMD_MAX = 500                 # one shell line: the command the helper runs to print the token
# A credential's SHAPE inside a command's text, an ADD-TIME rule only (a stored record is never re-read against it:
# the rule may tighten later, and a record's sessions must not fall off their login for it): a setup-token's prefix;
# a plain run of forty or more token characters outside a path (a key pasted in place of a command that reads one);
# or a JWT-shaped bearer, three dot-joined base64url segments of sixteen or more characters each carrying a digit or
# a capital. Dotted NAMES (a secret manager's key path, a host, a file name) are not credentials: a dot splits a run
# into segments judged one by one (review 2026-09-11). Such a command would ride /bin/sh's argv on every refresh,
# readable to every process of the same user through ps. A forty-digit HEX segment is also a gpg key fingerprint:
# one inside a gpg command, or right after --recipient/-r, passes.
SETUP_TOKEN_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
TOKEN_RUN_RE = re.compile(r"(?<![A-Za-z0-9_/.\-])[A-Za-z0-9_.\-]+(?![A-Za-z0-9_/.\-])")
HEX_RUN_RE = re.compile(r"^[0-9A-Fa-f]{40}$")
GPG_CMD_RE = re.compile(r"(?:^|[\s;&|(`$])gpg2?(?:\s|$)")
RECIPIENT_RE = re.compile(r"(?:--recipient|-r)(?:\s+|=)$")
CREDENTIAL_RUN_LEN = 40
JWT_SEGMENT_LEN = 16


def _jwt_shaped(run: str) -> bool:
    # exactly three segments, the JWT's header.payload.signature: a four-segment run (a JWE has five) is not one and
    # passes here, its segments still judged one by one by the plain rule (accepted, review 2026-09-11)
    segs = run.split(".")
    return len(segs) == 3 and all(len(seg) >= JWT_SEGMENT_LEN and re.search(r"[A-Z0-9]", seg) for seg in segs)


def credential_shaped(cmd: str) -> bool:
    """Whether `cmd` carries a credential-shaped run: a setup-token prefix, a JWT-shaped dotted run, or a plain
    segment of CREDENTIAL_RUN_LEN token characters (a gpg fingerprint excepted). Dotted names pass."""
    if SETUP_TOKEN_RE.search(cmd):
        return True
    for m in TOKEN_RUN_RE.finditer(cmd):
        run = m.group(0)
        if "." in run and _jwt_shaped(run):
            return True
        for seg in run.split("."):
            if len(seg) < CREDENTIAL_RUN_LEN:
                continue
            if HEX_RUN_RE.match(seg) and (GPG_CMD_RE.search(cmd) or RECIPIENT_RE.search(cmd[:m.start()])):
                continue
            return True
    return False


def has_token_cmd(rec) -> bool:
    """Whether a stored record carries a token command at all: presence, the only read-time rule. The shape check
    (token_cmd_error) is applied at add time and never to a stored record (review 2026-09-11: a rule that tightened
    read an existing record as 'no token command recorded' and its sessions fell off the login)."""
    cmd = rec.get("tokenCmd") if isinstance(rec, dict) else None
    return isinstance(cmd, str) and bool(cmd.strip())   # a non-string is no command (never coerced into one)


def token_cmd_error(cmd) -> str:
    """Why `cmd` is not a usable token command, or "": a non-empty single shell line within TOKEN_CMD_MAX
    characters, no control characters. The command's text is never checked for what it does."""
    if not isinstance(cmd, str) or not cmd.strip():
        return "a token command (a shell line that prints the token) is required"
    if len(cmd) > TOKEN_CMD_MAX:
        return "the token command must be at most %d characters" % TOKEN_CMD_MAX
    if any(ord(ch) < 32 for ch in cmd):
        return "the token command must be one line with no control characters"
    if credential_shaped(cmd):
        # said at the moment the value has already reached the shell's history and this command's argument list
        return ("the command text looks like it carries the credential itself (a token-shaped run); if it does, that "
                "value is already exposed (the shell's history, the command's argument list): rotate it, then keep the "
                "new token in a store and have the command read it (--op <reference>, or --cmd 'cat <private file>')")
    return ""


def op_read_command(ref) -> str:
    """The token command for a 1Password secret reference (the documented example): `op read --no-newline <ref>`,
    shell-quoted. "" for anything that is not a reference."""
    ref = str(ref or "").strip()
    if not OP_REF_RE.match(ref):
        return ""
    return "op read --no-newline %s" % shlex.quote(ref)
# The CLI's subscriptionType words, folded to the one distinction the user draws (design decision 1):
# "team"/"enterprise" read enterprise, "pro"/"max" read personal. Any other word, or none, is NO kind
# word: the kind is never guessed from an organisation's presence.
KINDS = {"pro": "personal", "max": "personal", "team": "enterprise", "enterprise": "enterprise"}
TOKEN_LIFE_S = 365 * 86400          # the docs' one-year life of a setup-token
EXPIRY_WARN_S = 335 * 86400         # eleven months: the gear and the menus warn from here
HELPER_NAME = "romp-login-helper"   # bin/romp-login-helper: the per-login apiKeyHelper

# The pick vocabulary every door shares (set_auth, the picker's create, the tab menu's setAuth):
# "login" (the machine's own), "key", or "login:<id>" (a stored login by its record id).
PICK_RE = re.compile(r"^login:([0-9a-f]{12})$")


def kind_word(subscription_type) -> str:
    """The kind word for a CLI subscriptionType, or "" when the word is unknown (never guessed)."""
    return KINDS.get(str(subscription_type or "").strip().lower(), "")


def parse_pick(value) -> tuple:
    """(side, login_id) for a Billing pick value: ("login", ""), ("key", ""), ("login", <id>) for
    "login:<id>", and ("", "") for anything else (a junk value refuses at every door)."""
    v = str(value or "")
    if v in ("login", "key"):
        return v, ""
    m = PICK_RE.match(v)
    if m:
        return "login", m.group(1)
    return "", ""


def pick_value(side: str, login_id: str = "") -> str:
    """The inverse of parse_pick: the one string a reg's (auth, authLogin) pair reads as."""
    if side == "login" and login_id:
        return "login:" + login_id
    return side or ""


def logins_dir(state_dir) -> Path:
    return Path(state_dir) / LOGINS_DIR


def record_path(state_dir, login_id: str) -> Path:
    return logins_dir(state_dir) / ("%s.json" % login_id)


def mint_id() -> str:
    """A fresh record id: 12 hex, the width of the account digests the kernel prints, unrelated to any
    credential or account identifier."""
    return secrets.token_hex(6)


def helper_command(login_id: str, state_dir=None, bin_dir=None) -> str:
    """The apiKeyHelper a session billed to `login_id` gets in its per-session settings layer: this
    checkout's bin/romp-login-helper, the record id, and the state directory the record lives under
    (explicit, so the child needs no romp environment). Shell-quoted: the CLI runs the value in /bin/sh."""
    b = Path(bin_dir) if bin_dir else Path(__file__).resolve().parent.parent / "bin"
    parts = [shlex.quote(str(b / HELPER_NAME)), login_id]
    if state_dir:
        parts.append(shlex.quote(str(state_dir)))
    return " ".join(parts)


def read_record(state_dir, login_id: str):
    """The record dict for `login_id`, or None (no such record, an unreadable file, or a junk id)."""
    if not ID_RE.match(str(login_id or "")):
        return None
    try:
        d = json.loads(record_path(state_dir, login_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("id") != login_id:
        return None
    return d


def write_record(state_dir, rec: dict) -> None:
    """Write one record whole (a writer-unique temp in the same directory, then os.replace, the kernel's
    _atomic_write idiom). Metadata only: a caller that puts a token in here has misread the module."""
    lid = str(rec.get("id") or "")
    if not ID_RE.match(lid):
        raise ValueError("a login record needs a 12-hex id")
    d = logins_dir(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    # 0700 / 0600, the serve-token treatment: the record holds no token, but a user may type a literal into a
    # token command, and a default-umask file is world-readable on a shared host
    os.chmod(d, 0o700)
    p = record_path(state_dir, lid)
    tmp = p.with_name("%s.%d.%s.tmp" % (p.name, os.getpid(), secrets.token_hex(4)))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True))
    os.replace(tmp, p)
    os.chmod(p, 0o600)   # a pre-existing record keeps its mode through the rename: tighten it too


def _with_state(rec: dict, now: float) -> dict:
    """The record plus its derived, unstored facts: hasCmd, expiresAt, expiresSoon, expired."""
    out = dict(rec)
    out["hasCmd"] = has_token_cmd(rec)
    added = rec.get("addedAt")
    if isinstance(added, (int, float)) and added > 0:
        out["expiresAt"] = int(added + TOKEN_LIFE_S)
        out["expiresSoon"] = now >= added + EXPIRY_WARN_S
        out["expired"] = now >= added + TOKEN_LIFE_S
    else:
        out["expiresAt"] = None
        out["expiresSoon"] = False
        out["expired"] = False
    return out


def records(state_dir, now=None) -> list:
    """Every stored login, oldest first (then by label), each with its derived state (_with_state).
    A record file that does not read is skipped: the registry is metadata, and a half-written file
    must not hide the others."""
    now = time.time() if now is None else now
    d = logins_dir(state_dir)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json"):
            continue
        rec = read_record(state_dir, n[:-5])
        if rec:
            out.append(_with_state(rec, now))
    out.sort(key=lambda r: (r.get("addedAt") or 0, str(r.get("label") or "")))
    return out


def record_state(state_dir, login_id: str, now=None):
    """One record with its derived state, or None."""
    rec = read_record(state_dir, login_id)
    if rec is None:
        return None
    return _with_state(rec, time.time() if now is None else now)


def display(rec) -> str:
    """The label a surface shows for a stored login: the user's label, then the email when it adds
    something, the organisation and the kind word when known, joined with middle dots. Never a token,
    never the command, never an id."""
    if not isinstance(rec, dict):
        return ""
    label = str(rec.get("label") or "").strip()
    email = str(rec.get("email") or "").strip()
    org = str(rec.get("org") or "").strip()
    kind = str(rec.get("kind") or "").strip().lower()
    if kind not in KINDS.values():
        kind = kind_word(kind)          # a stored CLI word folds; anything else is no kind word
    pieces = [label]
    if email and email.lower() != label.lower():
        pieces.append(email)
    if org:
        pieces.append(org)
    if kind:
        pieces.append(kind)
    return " · ".join(p for p in pieces if p)


def why_unavailable(rec, now=None) -> str:
    """Why a session cannot be billed to this stored login just now, as one plain sentence for the
    refusal toast, the greyed menu option and the problem ring, or "" when it can. A record with its
    derived state (records / record_state) or a bare one."""
    if not isinstance(rec, dict):
        return "no stored login with that id"
    now = time.time() if now is None else now
    label = str(rec.get("label") or "this")
    if rec.get("refused"):
        return "the %s login was refused: %s" % (label, str(rec.get("refused")))
    added = rec.get("addedAt")
    expired = rec.get("expired") if "expired" in rec else (
        isinstance(added, (int, float)) and added > 0 and now >= added + TOKEN_LIFE_S)
    if expired:
        return "the %s login's token is a year old and has expired; add it again" % label
    has_cmd = rec.get("hasCmd") if "hasCmd" in rec else has_token_cmd(rec)
    if not has_cmd:
        return "no token command is recorded for the %s login; add it again" % label
    return ""


def mark_refused(state_dir, login_id: str, why: str) -> bool:
    """Record that the API refused this login (an auth error on a session billed to it), so every menu
    greys it with the reason until it is removed or added again. Idempotent: an unchanged reason writes
    nothing. True when a record was written."""
    rec = read_record(state_dir, login_id)
    if rec is None:
        return False
    why = " ".join(str(why or "refused").split())[:200]
    if rec.get("refused") == why:
        return False
    rec["refused"] = why
    rec["refusedAt"] = int(time.time())
    write_record(state_dir, rec)
    return True


def clear_refused(state_dir, login_id: str) -> bool:
    rec = read_record(state_dir, login_id)
    if rec is None or not rec.get("refused"):
        return False
    rec.pop("refused", None)
    rec.pop("refusedAt", None)
    write_record(state_dir, rec)
    return True


def resolve(state_dir, ref: str) -> tuple:
    """(login_id, error) for a CLI reference: a record id, or a label. A label held by two records
    refuses and names the count, never picks one."""
    ref = str(ref or "").strip()
    if not ref:
        return "", "a login id or label is required"
    if ID_RE.match(ref) and read_record(state_dir, ref):
        return ref, ""
    hits = [r for r in records(state_dir) if str(r.get("label") or "") == ref]
    if len(hits) == 1:
        return str(hits[0]["id"]), ""
    if len(hits) > 1:
        return "", "%d stored logins carry the label %r; name one by id (romp login list shows them)" % (len(hits), ref)
    return "", "no stored login named %r" % ref


def remove(state_dir, login_id: str) -> bool:
    """Forget a stored login: its record goes (the token stays wherever the user keeps it). True when one existed."""
    if not ID_RE.match(str(login_id or "")):
        return False
    try:
        os.unlink(record_path(state_dir, login_id))
    except FileNotFoundError:
        return False
    return True
