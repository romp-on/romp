# tests/free-port.bash: loopback TCP ports nothing is bound to, for the bats suites whose subject
# binds one (the ones that start a real bin/romp-manager).
#
# Use from a bats file: `load free-port` at the top, then `free_port CPORT MPORT` in setup() or a
# test (`local cport mport; free_port cport mport` inside a test). Each named variable is assigned a
# distinct port. The function fails, with a `free-port:` line on stderr, when python3 is missing or
# no port is found, and bats then fails the test right there: an empty port would otherwise reach
# bin/romp-manager as `Number('') || 7432`, the default control port, where a developer's live manager
# may be listening.
#
# Why not a literal: a literal shared by two files collides within one run (romp-manager-ensure.bats
# once ran `up` on romp-manager-origin.bats's control port, and a manager SIGTERM'd there outlives
# the kill by shutdownAll's exit grace, so `up` exited "already running" and never called tmux), and
# any literal collides when two checkouts run bats at once on one machine.
#
# Ports come from 20000-24999: below Linux's ephemeral range (32768-60999) and macOS's (49152-65535),
# so a transient outgoing connection cannot hold the pick as its SOURCE port between the pick and the
# subject's bind (the lesson tests/romp-postal.bats's header records), and below the project's own
# defaults (the postal bus's 25302, the postal tests' 27200-28300, the kernel's 29855; the manager's
# 7432 sits under the band). The probe is a plain bind with no SO_REUSEADDR: a port in TIME_WAIT from
# an earlier test reads busy and is skipped (node's listen would bind it anyway, so skipping is the safe
# side). What remains is the window between the pick and the subject's bind, milliseconds long, while
# bats runs files one after another; a second bats run on the same machine competes on a random pick
# out of 5000, where a literal collided every time.
#
# The locals carry a prefix because bash scopes dynamically: with plain names, `local name; free_port
# name` would assign the helper's own local and hand the caller an empty string, the exact failure the
# helper exists to prevent.

# The probe is a function of its own, with only the call substituted: bash 3.2 (macOS's system
# bash) pairs quotes and parens in the raw text of a $( ... ) with no notion of a heredoc, so a
# heredoc body inside one is read as shell and parses only while its quotes happen to balance
# (tests/test_shell_bash32_portability.py bans the shape tree-wide).
_fp_probe() {   # _fp_probe N: prints N distinct free loopback ports on one line, or fails
    python3 - "$1" <<'PY'
import random, socket, sys
n = int(sys.argv[1])
picked = []
for _ in range(500):
    p = random.randint(20000, 24999)
    if p in picked:
        continue
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
    except OSError:
        continue
    finally:
        s.close()
    picked.append(p)
    if len(picked) == n:
        break
if len(picked) < n:
    sys.exit(1)
print(" ".join(str(p) for p in picked))
PY
}

free_port() {   # free_port VAR...: assigns each VAR a distinct free loopback port
    local _fp_ports _fp_picks _fp_i=0 _fp_name
    if [ $# -eq 0 ]; then
        echo "free-port: usage: free_port VAR..." >&2
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "free-port: python3 not found on PATH; cannot probe for a free port" >&2
        return 1
    fi
    _fp_ports="$(_fp_probe "$#")" || { echo "free-port: no free loopback port in 20000-24999 after 500 tries" >&2; return 1; }
    read -ra _fp_picks <<< "$_fp_ports"
    for _fp_name in "$@"; do
        printf -v "$_fp_name" '%s' "${_fp_picks[_fp_i]}"
        _fp_i=$((_fp_i + 1))
    done
}
