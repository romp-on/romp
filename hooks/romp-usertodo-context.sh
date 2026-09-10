#!/usr/bin/env bash
# romp-usertodo-context.sh — SessionStart hook (sources: resume, compact): re-hand a session its
# OPEN user todos (plans/user-todos.md slice 3) as a PASSIVE context block — the agent's own
# outstanding notes to the person it works for, with ids and an invitation to withdraw any that
# are met or moot. This is how an agent remembers its asks after its working memory is wiped: a
# compaction drops SessionStart context along with everything else (the CLI re-fires SessionStart
# with source=compact for exactly this), and without the block a moot todo sits visible until the
# user dismisses it by hand. Passive on purpose — additionalContext costs no turn; idle check-in
# turns were rejected outright (same doc).
#
# NOT tmux-gated (unlike romp-postal-context.sh): SDK sessions need this block too. Both backends
# put the STABLE romp identity in the CLI env as ROMP_SID (bin/romp's launch line, sdk_backend
# _options) — no ROMP_SID means not a romp session, and the hook payload's session_id would be
# the wrong key anyway (it is the CURRENT transcript fsid, which a /clear fork moves off the
# stable id every store is keyed by). The KERNEL renders the text (POST /usertodo/context — the
# store's owner, never a direct file read here), so test_injected_voice.py scans the exact words
# a session receives. Kernel down/unreachable → silent exit 0, the romp-wake.sh posture: a
# SessionStart hook must never fail the turn, and the todos stay visible on the user's own
# surfaces regardless.
#
# The feature is switchable and OFF by default (the user 2026-09-03). The kernel is the authority on
# that too: its /usertodo/context answer carries `enabled`, and while it is false this hook emits
# NOTHING — no direct read of the switch file here, for the same reason the block itself is not read
# from the store here. An older kernel that sends no `enabled` field is read as on (the block alone
# decides), so the hook keeps working across a mixed upgrade.
set -uo pipefail

[[ -n "${ROMP_SUMMARIZING:-}" ]] && exit 0
[[ -n "${ROMP_SID:-}" ]] || exit 0
# The sid is interpolated into a JSON request body below — the same shape gate bin/romp applies
# before interpolating a resume id, so a mangled or crafted value dies here, not on the wire.
[[ "$ROMP_SID" =~ ^[0-9a-zA-Z][0-9a-zA-Z-]*$ ]] || exit 0

input="$(cat)"
[[ "$input" =~ \"source\":\"([^\"]+)\" ]] && source_kind="${BASH_REMATCH[1]}" || source_kind=""
# resume = a restart/revival continued this conversation; compact = the context was just
# rewritten. startup cannot have todos yet (a fresh sid, an empty store) and clear/fork are the
# user's own reset of the conversation — all three stay silent (the plan's chosen sources).
case "$source_kind" in resume|compact) ;; *) exit 0 ;; esac

# Either spelling of the kernel port (bin/romp-serve exports both); ROMP_SERVE_PORT first, so a
# session under an aux kernel asks ITS kernel. Token resolution and transport are romp-wake.sh's:
# env override, else the 0600 state file — and the token travels on STDIN as a curl config,
# never in argv (/proc/<pid>/cmdline is world-readable).
port="${ROMP_SERVE_PORT:-${ROMP_KERNEL_PORT:-29855}}"
tok="${ROMP_SERVE_TOKEN:-}"
[[ -n "$tok" ]] || tok="$(cat "${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/serve-token" 2>/dev/null || true)"
esc="${tok//\\/\\\\}"; esc="${esc//\"/\\\"}"
resp="$(printf 'header = "X-Romp-Token: %s"\n' "$esc" \
    | curl -sf -m 3 --config - -X POST "http://127.0.0.1:${port}/usertodo/context" \
           -H "Content-Type: application/json" --data "{\"id\":\"$ROMP_SID\"}" 2>/dev/null)" || exit 0
[[ -n "$resp" ]] || exit 0

# An empty block means nothing to say — print NOTHING at all (a zero-todo session gets no noise),
# and so does the kernel saying the feature is off (`enabled: false`), whatever else rides along.
python3 - "$resp" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
    block = "" if d.get("enabled") is False else str(d.get("block") or "").strip()
except Exception:
    block = ""
if block:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": block}}))
PY
exit 0
