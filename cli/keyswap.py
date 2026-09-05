#!/usr/bin/env python3
"""romp-keyswap — point every romp session at another API key, without restarting anything.

`romp keyswap` selects a credential source in the manager's env file
(`~/.config/romp/service.env`), taken from a sibling profile (`service.env.highprio`,
`service.env.lowprio`, …). A profile contains `ROMP_API_KEY_REF=op://…` for runtime 1Password
retrieval, or `ANTHROPIC_API_KEY=…` for a conventional key. Switching removes competing credential
assignments. This command never resolves a 1Password reference. The kernel reads the source live:

  * a session started or revived from then on bills the new key with no further action;
  * a session already running keeps the key its CLI process started with, because the key rides
    the launch environment — `--cycle <names>` or `--cycle-all` reconnects those sessions so they
    re-present the new one, each resuming its own conversation with its history intact;
  * the manager itself never restarts, so no session loses an open turn; a session with subagents or
    background tasks in flight is skipped by --cycle (a reconnect would kill them) and named, so you
    cycle it again once they are done.

No key value is ever printed, logged or passed over the wire. Keys and references are represented
by fingerprints. Reference comparisons confirm the kernel's configured source; runtime retrieval
and rotation checks happen in the kernel.

Usage:
    romp keyswap                            # what is live now, and which candidates exist
    romp keyswap lowprio                    # rewrite the key line from service.env.lowprio
    romp keyswap lowprio --cycle web,api    # …and reconnect those two sessions onto it
    romp keyswap lowprio --cycle-all        # …and reconnect every live key-billed session
    romp keyswap /path/to/other.env         # an explicit file instead of a sibling name
"""
import json
import os
import sys
import urllib.error
import urllib.request
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The SAME module the kernel reads the key with, not a second copy of the rules: writer and reader
# then cannot disagree about which file holds the key, which line wins, or how it is parsed.
ks = sys.modules.get("romp_keysource") or SourceFileLoader(
    "romp_keysource", str(ROOT / "kernel" / "keysource.py")).load_module()

KPORTS = ["http://127.0.0.1:29855", "http://127.0.0.1:7878", "http://127.0.0.1:7432"]


def _kernel_urls():
    """Where the local kernel may answer. ROMP_KERNEL_PORT / ROMP_SERVE_PORT when set — the port
    bin/romp and the installer resolve — and ONLY that one: a renumbered second-OS-user instance must
    never hand its serve token to whatever answers on the primary user's default port (review find,
    2026-09-04). Unset, the defaults `romp version` and `romp update` probe."""
    for var in ("ROMP_KERNEL_PORT", "ROMP_SERVE_PORT"):
        p = (os.environ.get(var) or "").strip()
        if not p:
            continue
        if not (p.isdigit() and 0 < int(p) < 65536):
            # an unusable override is refused, never silently replaced by the default ports — that
            # replacement is exactly the token-to-the-wrong-kernel path this function exists to close
            raise ValueError("%s=%r is not a port" % (var, p))
        return ["http://127.0.0.1:%s" % p]
    return list(KPORTS)


def _token():
    """The serve token — required on every kernel request, loopback included (Jupyter's model).
    Same resolution romp-update uses: env override, else the 0600 state file."""
    t = (os.environ.get("ROMP_SERVE_TOKEN") or "").strip()
    if t:
        return t
    try:
        root = Path(os.environ.get("ROMP_STATE_DIR")
                    or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")
        return (root / "serve-token").read_text().strip()
    except OSError:
        return ""


def _kernel():
    """The base URL of the running local kernel, or None."""
    try:
        urls = _kernel_urls()
    except ValueError:
        return None                                  # the callers report it (they check _kernel_urls first)
    for u in urls:
        try:
            with urllib.request.urlopen(u + "/version", timeout=1.5) as r:   # /version is auth-exempt
                if r.status == 200:
                    return u
        except Exception:
            continue
    return None


def _post(u, path, body):
    req = urllib.request.Request(u + path, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json",
                                         "X-Romp-Token": _token()}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fp(key):
    """A key as the only thing that may be shown of it."""
    f = ks.fingerprint(key)
    return ("sha256:" + f) if f else "(none)"


def _source_label(source):
    """Describe configuration without retrieving a provider's secret or exposing a literal key."""
    try:
        source.validate()
    except ks.KeySourceError:
        return "(invalid key source)"
    if source.kind == "op":
        return "1Password reference " + source.fingerprint()
    return _fp(source.value)


def _legacy_key_present(path):
    """Detect plaintext hidden by a higher-priority reference so selecting it also cleans up."""
    try:
        with open(path, encoding="utf-8") as fh:
            return bool(ks.parse_key(fh.read()))
    except (OSError, UnicodeError):
        # Attempt the write so its ordinary error path reports an unreadable live file.
        return True


def _candidates(path, out):
    """List the sibling `service.env.<name>` files, each by fingerprint only — so `romp keyswap`
    with no argument answers both "which key is live" and "what can I swap to"."""
    d = os.path.dirname(path) or "."
    base = os.path.basename(path) + "."
    try:
        names = sorted(n[len(base):] for n in os.listdir(d) if n.startswith(base) and not n.endswith("~"))
    except OSError:
        names = []
    live = ks.read_source(path)
    if not names:
        out("candidates  none — keep one file per key beside it, e.g. %s.lowprio (chmod 600),"
            % os.path.basename(path))
        out("            each a single ROMP_API_KEY_REF=op://… or %s=… line" % ks.KEY_VAR)
        return
    out("candidates")
    for n in names:
        source = ks.read_source(ks.sibling_path(n, path))
        mark = "  <- live" if (source.configured and source == live) else ""
        label = _source_label(source) if source.kind != "none" else "(no key source)"
        out("  %-14s %s%s" % (n, label, mark))


def _kernel_check(path, out):
    """Compare what the KERNEL reads with what the FILE says — the check the operator procedure used
    to ask for by eye (review find, 2026-09-04). The two can differ: the service was installed with
    another env-file path that never reached the kernel's environment, the file is unreadable to the
    kernel, the kernel still holds its startup key because the file has no key line, or the kernel
    predates this feature. Reads the fingerprint through /keycycle with no sessions named — a read,
    nothing cycles. Returns 0 when they agree (or no kernel is up to ask), 1 when they do not — or when
    the port override is unusable, which is a misconfiguration to fix, not "no kernel"."""
    try:
        _kernel_urls()
    except ValueError as e:
        out("kernel      NOT ASKED — %s; fix the variable and re-run" % e)
        return 1
    u = _kernel()
    if not u:
        out("kernel      not running — sessions read the file when it is")
        return 0
    body = _post(u, "/keycycle", {"sessions": []})
    if body.get("error") == "HTTP 404":
        out("kernel      predates `romp keyswap` (no /keycycle route): it is still on the key it booted")
        out("            with. Take the patch once with `romp refresh`; every swap after that is restart-free.")
        return 1
    if not body.get("ok"):
        out("kernel      could not be asked — %s" % (body.get("error") or body.get("detail") or "unknown"))
        return 1
    return _compare(body, path, out)


def _compare(body, path, out):
    """Compare provider configuration, or conventional keys for compatibility with older kernels."""
    source = ks.read_source(path)
    try:
        source.validate()
    except ks.KeySourceError as exc:
        out("MISMATCH    this file has an invalid key source — %s" % exc)
        return 1
    if source.kind == "op":
        if "sourceFp" not in body:
            out("kernel      predates runtime key sources; update it with `romp refresh` before cycling")
            return 1
        source_fp = body.get("sourceFp") or ""
        out("kernel      source %s" % (source_fp or "(none)"))
        if source_fp == source.fingerprint():
            out("            1Password reference matches; credentials are retrieved at runtime")
            return 0
    else:
        kfp = body.get("keyFp") or ""
        out("kernel      reads %s" % (("sha256:" + kfp) if kfp else "(none)"))
        if kfp == source.fingerprint():
            return 0
    out("MISMATCH    the kernel is not reading this file's key source. Usual causes: the service was installed")
    out("            with another env-file path that the kernel's environment does not carry (re-run")
    out("            `romp service install`), the file is unreadable to the kernel, or its credential")
    out("            configuration differs from the running manager's.")
    return 1


def _cycle(sessions, all_, out, path=None):
    """Reconnect the named sessions through the kernel, and report what it did per session. The
    kernel's fingerprint is READ and compared with the file's FIRST: cycling a session while the kernel
    reads another file would re-present the kernel's unchanged key, so a mismatch refuses to cycle."""
    try:
        _kernel_urls()
    except ValueError as e:
        out("cycle       NOT DONE — %s; fix the variable and re-run" % e)
        return 1
    u = _kernel()
    if not u:
        out("cycle       NOT DONE — no running kernel found (is romp on? `romp status`).")
        out("            The file is already swapped: every session picks the new key up at its")
        out("            next launch or revive. Re-run `romp keyswap --cycle…` once romp is up.")
        return 1
    body = _post(u, "/keycycle", {"sessions": []})          # the read: which key does the kernel hold?
    if body.get("error") == "HTTP 404":
        # The one restart this feature genuinely needs: a kernel started before this code has no
        # /keycycle route AND no live key read, so it is still on the key it booted with.
        out("cycle       NOT DONE — the running kernel predates `romp keyswap` (no /keycycle route).")
        out("            Take the patch once with `romp refresh`, and every swap after that is")
        out("            restart-free. The file is already swapped.")
        return 1
    if not body.get("ok"):
        out("cycle       FAILED — %s" % (body.get("error") or body.get("detail") or "unknown"))
        return 1
    if _compare(body, path or ks.service_env_path(), out):
        out("cycle       NOT DONE — the kernel's key source could not be verified; fix the issue above first")
        return 1
    expected_fp = body.get("sourceFp", body.get("keyFp") or "") or ""
    request = {"all": True} if all_ else {"sessions": sessions}
    request["expectedSourceFp"] = expected_fp
    body = _post(u, "/keycycle", request)
    if not body.get("ok"):
        out("cycle       FAILED — %s" % (body.get("error") or body.get("detail") or "unknown"))
        return 1
    if (body.get("sourceFp", body.get("keyFp") or "") or "") != expected_fp:
        out("cycle       FAILED — the key source changed during the request; check it before cycling again")
        return 1
    rows = body.get("rows") or []
    if not rows:
        out("cycle       no sessions matched")
        return 0
    for r in rows:
        out("  %-14s %s" % (str(r.get("session"))[:14], _explain(str(r.get("status") or ""))))
    if any(str(r.get("status")) == "working" for r in rows):
        out("            re-run --cycle for the skipped sessions once those are quiet")
    return 1 if any(str(r.get("status")).startswith("error:") for r in rows) else 0


def _explain(status):
    return {
        "cycling": "reconnecting now — history kept",
        "current": "already on this key — nothing to do",
        "login":   "skipped: bills the machine login, not the key",
        "dormant": "not running — its next launch reads the new key",
        "working": "skipped: a turn, subagents or background tasks are in flight (a reconnect would kill "
                   "the work) — cycle it again when it is quiet; its next launch reads the new key anyway. "
                   "A standing background task (a dev server, a monitor) never goes quiet: end it, or "
                   "revive the session",
        "unknown": "no such session",
    }.get(status, status)


def parse_args(argv):
    """(source, cycle-list, cycle-all, error). Positional: at most one source name or path."""
    src, cycle, cycle_all, err = "", [], False, ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--cycle-all":
            cycle_all = True
        elif a == "--cycle" or a.startswith("--cycle="):
            if a == "--cycle":
                i += 1
                val = argv[i] if i < len(argv) else ""
            else:
                val = a.split("=", 1)[1]
            cycle = [s.strip() for s in val.split(",") if s.strip()]
            if not cycle:
                err = err or "--cycle needs a session list, e.g. --cycle web,api"
        elif a.startswith("-"):
            err = err or ("unknown option %s" % a)
        elif not src:
            src = a
        else:
            err = err or "one source at a time (got %s and %s)" % (src, a)
        i += 1
    return src, cycle, cycle_all, err


def main(argv, out=None):
    out = out or (lambda line: sys.stdout.write(line + "\n"))
    src_arg, cycle, cycle_all, err = parse_args(list(argv))
    if err:
        sys.stderr.write("romp keyswap: %s\n" % err)
        return 2
    args = [src_arg] if src_arg else []
    path = ks.service_env_path()
    if not args:
        # Read-only report. Deliberately the no-argument behaviour: a swap is a real change and
        # should be asked for by name.
        out("service.env %s" % path)
        current = ks.read_source(path)
        out("live key    %s" % _source_label(current))
        _candidates(path, out)
        try:
            current.validate()
        except ks.KeySourceError as exc:
            out("configuration invalid — %s" % exc)
            return 1
        if cycle or cycle_all:
            return _cycle(cycle, cycle_all, out, path)
        rc = _kernel_check(path, out)
        out("")
        out("swap with:  romp keyswap <name> [--cycle <session,…> | --cycle-all]")
        return rc
    src = ks.sibling_path(args[0], path)
    if not os.path.exists(src):
        sys.stderr.write("romp keyswap: no such key file: %s\n" % src)
        sys.stderr.write("             keep one per key beside the env file (e.g. %s.lowprio, chmod 600)\n"
                         % os.path.basename(path))
        return 2
    new = ks.read_source(src)
    try:
        new.validate()
    except ks.KeySourceError as exc:
        sys.stderr.write("romp keyswap: %s has an invalid key source — %s (file untouched)\n"
                         % (src, exc))
        return 2
    if not new.configured or not new.value:
        # Refuse rather than write an empty key: the CLI reads an empty ANTHROPIC_API_KEY as
        # "API-key mode, no key" and every session would then fail to authenticate.
        sys.stderr.write("romp keyswap: %s has no usable ROMP_API_KEY_REF= or %s= line — "
                         "nothing to swap to (file untouched)\n" % (src, ks.KEY_VAR))
        return 2
    cur = ks.read_source(path)
    # A pre-existing reference may still sit beside a legacy plaintext key. Selecting that same
    # reference also migrates the file: remove the leftover secret instead of treating it as done.
    if new == cur and not (new.kind == "op" and _legacy_key_present(path)):
        out("service.env %s" % path)
        out("live key    %s — already this key source, nothing rewritten" % _source_label(cur))
    else:
        try:
            res = ks.write_source(new, path)
        except OSError as e:
            sys.stderr.write("romp keyswap: could not rewrite %s: %s\n" % (path, e))
            return 1
        # Verify from the FILE, not from what we meant to write: re-read and fingerprint it.
        landed = ks.read_source(path)
        if landed != new:
            sys.stderr.write("romp keyswap: the rewrite did not land (file reads %s, expected %s) — "
                             "check %s by hand\n" % (_source_label(landed), _source_label(new), path))
            return 1
        out("service.env %s" % res["path"])
        if res.get("target") and res["target"] != res["path"]:
            out("            (a link: written through to %s)" % res["target"])
        out("key source  %s -> %s  (%d lines, mode %o%s)"
            % (_source_label(res["old"]), _source_label(new), res["lines"], res["mode"],
               ", tightened from a group/other-readable mode" if res["tightened"] else ""))
        out("source      %s" % src)
    out("effect      new and revived sessions use this key source; no manager restart needed")
    if new.kind == "op":
        out("            the kernel retrieves the key through op at runtime; no key was copied to disk")
    if cycle or cycle_all:
        return _cycle(cycle, cycle_all, out, path)
    rc = _kernel_check(path, out)
    out("running     sessions keep the key they launched with — reconnect them with")
    out("            romp keyswap %s --cycle-all   (or --cycle <session,…>)" % args[0])
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
