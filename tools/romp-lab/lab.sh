#!/usr/bin/env bash
# romp-lab: a hermetic, headless, full-stack romp + the scripted highlight loop (see README.md).
# HERMETICITY FIRST: the state root moves to a temp dir BEFORE any romp code runs — the
# never-load-romp-modules-against-live-state rule. Nothing here may touch live state, live
# postal, or any visible display.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KEEP=0
for a in "$@"; do case "$a" in --keep) KEEP=1 ;; esac; done

LAB="$(mktemp -d /tmp/romp-lab-XXXXXX)"
mkdir -p "$LAB/state" "$LAB/shots" "$LAB/project"
echo "# a synthetic scratch project for the lab session" > "$LAB/project/README.md"

export XDG_STATE_HOME="$LAB/state"
unset ROMP_STATE_DIR || true
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
# a free port: bind 0 and read it back
PORT=$(python3 - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
)
export ROMP_KERNEL_PORT="$PORT"

# serve the FRESH build, never a stale bundle (the T106 triage's first lesson)
( cd "$ROOT/vscode-extension" && node esbuild.js >/dev/null 2>&1 )

"$ROOT/bin/romp-kernel" > "$LAB/kernel.log" 2>&1 &
KPID=$!
cleanup() {
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

LAB_DIR="$LAB" PORT="$PORT" TOKEN="$ROMP_SERVE_TOKEN" PROJECT_DIR="$LAB/project" \
  node "$ROOT/tools/romp-lab/highlight-loop.mjs"
RC=$?
echo "highlight loop exit: $RC (shots: $LAB/shots)"
[ "$RC" = 0 ] || KEEP=1
exit "$RC"
