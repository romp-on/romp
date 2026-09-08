#!/usr/bin/env bash
# romp-lab: a hermetic, headless, full-stack romp + the scripted highlight loop (see README.md).
# HERMETICITY FIRST: the state root moves to a temp dir BEFORE any romp code runs — the
# never-load-romp-modules-against-live-state rule. Nothing here may touch live state, live
# postal, or any visible display.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEEP=0
MODE=all
for a in "$@"; do case "$a" in
  --keep) KEEP=1 ;;
  --banner-only) MODE=banner ;;       # the T119 reload-banner phase alone (no model spend)
  --highlight-only) MODE=highlight ;; # the T102/T106 highlight loop alone
  --modes-only) MODE=modes ;;         # the T139 permission-mode sweep alone
  --login-only) MODE=login ;;         # the T157 in-dashboard login flow (mocked CLI, no model spend)
esac; done

LAB="$(mktemp -d /tmp/romp-lab-XXXXXX)"
mkdir -p "$LAB/state" "$LAB/shots" "$LAB/project"
echo "# a synthetic scratch project for the lab session" > "$LAB/project/README.md"

export XDG_STATE_HOME="$LAB/state"
unset ROMP_STATE_DIR || true
# never the machine's real manager: an inherited ROMP_MANAGER_PORT would let the lab kernel
# restart the live deployment. Poisoned to a dead port, not unset — absent maps to the DEFAULT
# (live) port in the update path, so only a dead value is safe against every consumer.
export ROMP_MANAGER_PORT=1
# the SDK venv is PACKAGES, not state — symlink the machine's real one into the hermetic root so
# lab sessions can actually run their CLI (without it every SDK session reports unable to start).
# A symlink shares bytes only; nothing here writes into the venv.
REAL_VENV="${HOME}/.local/state/romp/sdkvenv"
if [ -d "$REAL_VENV" ]; then
  mkdir -p "$LAB/state/romp"
  ln -s "$REAL_VENV" "$LAB/state/romp/sdkvenv"
fi
export ROMP_KERNEL_NO_OPEN=1
export ROMP_SERVE_TOKEN="labtok-$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
# a free port: bind 0 and read it back. A function, with only the call substituted: bash 3.2
# (macOS's system bash) pairs quotes and parens in the raw text of a $( ... ) with no notion of
# a heredoc, so a heredoc body inside one is read as shell and parses only while its quotes
# happen to balance (tests/test_shell_bash32_portability.py bans the shape tree-wide).
_lab_free_port() {
  python3 - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}
PORT=$(_lab_free_port)
export ROMP_KERNEL_PORT="$PORT"

# serve the FRESH build, never a stale bundle (the T106 triage's first lesson)
( cd "$ROOT/vscode-extension" && node esbuild.js >/dev/null 2>&1 )

# …and serve a COPY of it (T119): the banner phase simulates rebuilds by bumping dist mtimes, and
# the repo's real dist is what the LIVE kernel serves — a lab run must never raise reload banners
# on the user's real dashboard. ROMP_DIST_DIR is the kernel's test seam for exactly this; it also
# stands the kernel's own dist-vs-source converge down, so the lab controls every mtime it asserts.
mkdir -p "$LAB/dist"
cp "$ROOT/vscode-extension/dist/"*.js "$LAB/dist/" 2>/dev/null || true
cp "$ROOT/vscode-extension/dist/"*.css "$LAB/dist/" 2>/dev/null || true
if [ -d "$ROOT/vscode-extension/dist/fonts" ]; then cp -r "$ROOT/vscode-extension/dist/fonts" "$LAB/dist/fonts"; fi
export ROMP_DIST_DIR="$LAB/dist"
# a fast heartbeat so the banner phase's one-heartbeat assertions run in seconds, not tens of them
export ROMP_WS_KEEPALIVE=2

if [ "$MODE" = "login" ]; then
  # the T157 phase drives the LOGIN flow only (no sessions run): the kernel resolves claude through
  # ROMP_CLAUDE_BIN, so pointing it at the mock makes the whole flow synthetic — never a real account
  export ROMP_CLAUDE_BIN="$ROOT/tools/romp-lab/login-mock-claude.py"
  export LOGIN_MOCK_MARKER="$LAB/login-code-sha.txt"
fi
"$ROOT/bin/romp-kernel" > "$LAB/kernel.log" 2>&1 &
KPID=$!
cleanup() {
  # the banner phase restarts the kernel (its reconnect assertion) and records the new pid
  [ -f "$LAB/kernel.pid" ] && KPID="$(cat "$LAB/kernel.pid")"
  kill "$KPID" 2>/dev/null || true
  wait "$KPID" 2>/dev/null || true
  if [ "$KEEP" = 1 ]; then echo "kept: $LAB (kernel.log, shots/)"; else rm -rf "$LAB"; fi
}
trap cleanup EXIT

for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
  sleep 0.5
  kill -0 "$KPID" 2>/dev/null || { echo "kernel died at boot — $LAB/kernel.log:"; tail -20 "$LAB/kernel.log"; exit 1; }
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || { echo "kernel never became healthy"; exit 1; }
echo "lab kernel up on :$PORT (state: $LAB/state)"

RC=0
if [ "$MODE" != "highlight" ] && [ "$MODE" != "modes" ] && [ "$MODE" != "login" ]; then
  # the reload-banner contract (T119) — first, because it spends no model turns
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" KPID="$KPID" KERNEL_BIN="$ROOT/bin/romp-kernel" \
    node "$ROOT/tools/romp-lab/banner-loop.mjs" || RC=$?
  echo "banner loop exit: $RC (shots: $LAB/shots)"
  [ -f "$LAB/kernel.pid" ] && KPID="$(cat "$LAB/kernel.pid")"
fi
if [ "$MODE" != "banner" ] && [ "$MODE" != "modes" ] && [ "$MODE" != "login" ] && [ "$RC" = 0 ]; then
  # the kernel may have been bounced by the banner phase — wait for health before driving it
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
  done
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" PROJECT_DIR="$LAB/project" \
    node "$ROOT/tools/romp-lab/highlight-loop.mjs" || RC=$?
  echo "highlight loop exit: $RC (shots: $LAB/shots)"
fi
if [ "$MODE" = "login" ] && [ "$RC" = 0 ]; then
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" LOGIN_MOCK_MARKER="$LOGIN_MOCK_MARKER" \
    node "$ROOT/tools/romp-lab/login-loop.mjs" || RC=$?
  echo "login loop exit: $RC (shots: $LAB/shots)"
fi
if [ "$MODE" != "banner" ] && [ "$MODE" != "highlight" ] && [ "$MODE" != "login" ] && [ "$RC" = 0 ]; then
  # the T139 permission-mode sweep: every mode's ask contract on the real stack
  for i in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5
  done
  LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" PROJECT_DIR="$LAB/project" \
    node "$ROOT/tools/romp-lab/modes-loop.mjs" || RC=$?
  echo "modes loop exit: $RC (shots: $LAB/shots)"
fi
[ "$RC" = 0 ] || KEEP=1
exit "$RC"
