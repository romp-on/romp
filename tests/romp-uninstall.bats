#!/usr/bin/env bats

# romp-uninstall must leave the machine looking un-installed, so a from-scratch reinstall really is
# from scratch — and must do it WITHOUT collateral damage to the user's own Claude Code config.
# Hermetic: HOME is a temp dir; the login service and network steps are opted out.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

# The uninstaller is NEVER run from the real clone here. It tears down the login service and kills
# the manager, and its ROMP_DIR comes from its own $0 — so running the repo copy would take down the
# developer's live romp mid-test-suite (it did, 2026-07-27). Instead each test gets a THROWAWAY clone:
# the real script, a stubbed romp-service, and disposable build artifacts. Everything the uninstaller
# does to $HOME is still exercised for real, because HOME is a temp dir too.
fake_clone() {
    CLONE="$TEST_DIR/clone"
    mkdir -p "$CLONE/bin" "$CLONE/vscode-extension"
    cp "$ROMP_DIR/bin/romp-uninstall" "$CLONE/bin/romp-uninstall"
    chmod +x "$CLONE/bin/romp-uninstall"
    cat > "$CLONE/bin/romp-service" <<EOF
#!/usr/bin/env bash
echo "romp-service \$* (stub)" >> "$TEST_DIR/service.log"
EOF
    chmod +x "$CLONE/bin/romp-service"
}

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    mkdir -p "$HOME"
    export ROMP_NO_SERVICE=1 ROMP_NO_EXT=1 ROMP_NO_SDK=1
    export ROMP_INSTALL_TOKEN_TRIES=1
    export ROMP_GITHOOK_DIR="$TEST_DIR/githooks"
    export ROMP_STATE_DIR="$TEST_DIR/state"
    mkdir -p "$ROMP_STATE_DIR"
    fake_clone
}

# Claude Code's project-dir encoding, as kernel/judge.py's _proj_dir implements it: every
# non-alphanumeric character replaced, over the realpath. Tests must build the expected directory
# with THIS, never with a shell ${p//\//-} — that only replaces slashes, and a test using it
# agrees with a buggy implementation instead of with Claude Code.
_proj_slug() {
    python3 - "$1" <<'PYEOF'
import os, re, sys
print(re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(sys.argv[1])))
PYEOF
}

teardown() { rm -rf "$TEST_DIR"; }

hook_count() {   # how many romp hook commands are left registered across all events
    python3 - "$HOME/.claude/settings.json" <<'PY'
import json, sys, os
try:
    s = json.load(open(sys.argv[1]))
except (IOError, OSError, ValueError):
    print(0); raise SystemExit
OURS = ("tmux-status.sh", "romp-summarize.sh", "romp-postal-drain.sh", "romp-postal-ensure.sh",
        "romp-postal-revive.sh", "romp-postal-context.sh", "romp-usertodo-context.sh", "romp-wake.sh")
n = sum(1 for rules in (s.get("hooks") or {}).values() for r in rules for h in r.get("hooks", [])
        if h.get("command", "").rsplit("/", 1)[-1] in OURS)
print(n)
PY
}

@test "romp-uninstall: removes the hook symlinks, skills and MCP config that install.sh created" {
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ -L "$HOME/.claude/hooks/tmux-status.sh" ]
    [ -L "$HOME/.claude/romp-postal.mcp.json" ]
    [ "$(hook_count)" -gt 0 ]

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]

    [ ! -e "$HOME/.claude/hooks/tmux-status.sh" ]
    [ ! -e "$HOME/.claude/hooks/romp-summarize.sh" ]
    [ ! -e "$HOME/.claude/hooks/romp-postal-drain.sh" ]
    [ ! -e "$HOME/.claude/skills/romp" ]
    [ ! -e "$HOME/.claude/skills/romp-postal" ]
    [ ! -e "$HOME/.claude/romp-postal.mcp.json" ]
    [ "$(hook_count)" -eq 0 ]
}

# One hook per registered name, counted by its exact command string — hook_count folds every romp
# hook into one number, which cannot tell a name the uninstaller forgot from the ones it removed.
cmd_count() {   # $1 = hook basename
    python3 - "$HOME/.claude/settings.json" "$1" <<'PY'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except (IOError, OSError, ValueError):
    print(0); raise SystemExit
print(sum(1 for rules in (s.get("hooks") or {}).values() for r in rules for h in r.get("hooks", [])
          if h.get("command", "") == "~/.claude/hooks/" + sys.argv[2]))
PY
}

@test "romp-uninstall: the user-todos SessionStart hook goes with the rest (link and registration)" {
    # romp-usertodo-context.sh is registered by install.sh (plans/user-todos.md slice 3); the
    # uninstaller's rm loop and its OURS set must name it too, or an uninstall leaves its
    # ~/.claude/hooks link and its SessionStart entry behind: a hook pointing into a clone that may
    # be gone, firing on every session start until settings.json is edited by hand.
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ -L "$HOME/.claude/hooks/romp-usertodo-context.sh" ]
    [ "$(cmd_count romp-usertodo-context.sh)" = "1" ]

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]
    [ ! -e "$HOME/.claude/hooks/romp-usertodo-context.sh" ]
    [ "$(cmd_count romp-usertodo-context.sh)" = "0" ]
    # every hook install.sh links has a matching rm: the two lists are read from the scripts themselves
    for h in $(grep -o '"[a-z-]*\.sh"' "$ROMP_DIR/install.sh" | tr -d '"' | sort -u); do
        [ ! -e "$HOME/.claude/hooks/$h" ]
        [ "$(cmd_count "$h")" = "0" ]
    done
}

@test "romp-uninstall: leaves the user's OWN hooks in settings.json untouched" {
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]

    # A hook of the user's own, in an event romp also writes to.
    python3 - "$HOME/.claude/settings.json" <<'PY'
import json, sys
p = sys.argv[1]
s = json.load(open(p))
s["hooks"].setdefault("Stop", []).append(
    {"hooks": [{"type": "command", "command": "~/.claude/hooks/my-own-notify.sh", "timeout": 5}]})
s["permissions"] = {"allow": ["Bash(ls:*)"]}      # a non-hook setting, must also survive
json.dump(s, open(p, "w"), indent=2)
PY

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]

    run python3 -c "
import json; s = json.load(open('$HOME/.claude/settings.json'))
cmds = [h['command'] for rules in (s.get('hooks') or {}).values() for r in rules for h in r.get('hooks', [])]
assert '~/.claude/hooks/my-own-notify.sh' in cmds, cmds
assert s['permissions'] == {'allow': ['Bash(ls:*)']}, s.get('permissions')
print('preserved')
"
    [ "$status" -eq 0 ]
    [[ "$output" == *"preserved"* ]]
}

@test "romp-uninstall: keeps recorded state by default, deletes it only with --purge" {
    echo '{"synthetic": "record"}' > "$ROMP_STATE_DIR/serve-token"
    mkdir -p "$ROMP_STATE_DIR/sdkvenv/bin"

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]
    [ -f "$ROMP_STATE_DIR/serve-token" ]          # records survive a plain uninstall...
    [ ! -d "$ROMP_STATE_DIR/sdkvenv" ]            # ...but the venv is an install artifact, so it goes
    [[ "$output" == *"state dir kept"* ]]

    run "$CLONE/bin/romp-uninstall" --yes --purge
    [ "$status" -eq 0 ]
    [ ! -d "$ROMP_STATE_DIR" ]
}

@test "romp-uninstall: strips the PATH line bootstrap.sh added, and nothing else" {
    cat > "$HOME/.bashrc" <<EOF
export EDITOR=vim

# romp
export PATH="\$PATH:$CLONE/bin"
alias ll='ls -la'
EOF

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]

    # `run` + status, NOT a bare `! grep`: `!` is exempt from set -e, so mid-test it asserts nothing.
    run grep -qF "$CLONE/bin" "$HOME/.bashrc"
    [ "$status" -ne 0 ]
    run grep -q '^# romp$' "$HOME/.bashrc"
    [ "$status" -ne 0 ]
    grep -q 'EDITOR=vim' "$HOME/.bashrc"          # the user's own lines are untouched
    grep -q "alias ll=" "$HOME/.bashrc"
}

@test "romp-uninstall: removes the build artifacts a reinstall must rebuild" {
    # Stand-ins for what vscode-extension/install.sh produces, in the throwaway clone — never the
    # real one, whose dist/ is what the running kernel is serving to the browser right now.
    mkdir -p "$CLONE/vscode-extension/node_modules" "$CLONE/vscode-extension/dist"
    touch "$CLONE/vscode-extension/dist/render.js" "$CLONE/vscode-extension/romp-chat-view.vsix"

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]

    [ ! -d "$CLONE/vscode-extension/node_modules" ]
    [ ! -d "$CLONE/vscode-extension/dist" ]
    [ ! -f "$CLONE/vscode-extension/romp-chat-view.vsix" ]
}

@test "romp-uninstall: never touches a DIFFERENT clone's install (kills only its own manager)" {
    # Two clones coexist on a dev machine (worktrees are the norm here). Uninstalling one must
    # leave the other's build artifacts — and, by the same path-scoped match, its manager — alone.
    other="$TEST_DIR/other-clone"
    mkdir -p "$other/vscode-extension/dist"
    touch "$other/vscode-extension/dist/render.js"

    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]

    [ -f "$other/vscode-extension/dist/render.js" ]
    # The pkill pattern must be this clone's absolute path, not a bare 'bin/romp-manager'.
    grep -q 'pkill -f "\$ROMP_DIR/bin/romp-manager"' "$CLONE/bin/romp-uninstall"
}

@test "romp-uninstall: is idempotent — a second run on a clean machine still exits 0" {
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]
    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]
}

@test "romp uninstall: reachable as a verb and listed in help" {
    run "$ROMP_DIR/bin/romp" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp uninstall"* ]]
}

# ── the invisible consequence of --purge ─────────────────────────────────────
# The dashboard is token-gated and the first visit stores a year-long cookie, so an open tab
# keeps working until the state dir goes and the next kernel mints a NEW token. Then that tab
# drops to the token page, which reads as "the dashboard never loaded" rather than "signed out"
# — which is exactly how it landed on a real reinstall. The teardown step that causes it must
# say so, so the cycle is self-explanatory without anyone diagnosing it.

@test "romp-uninstall: --purge warns the browser session is invalidated, and how to get back in" {
    run "$CLONE/bin/romp-uninstall" --yes --purge
    [ "$status" -eq 0 ]
    [[ "$output" == *"signed out"* || "$output" == *"token page"* ]]
    [[ "$output" == *"romp url"* ]]                 # the exact command that fixes it
    [[ "$output" == *"not a failed install"* ]]     # names the misreading it prevents
}

@test "romp-uninstall: says nothing about tokens when state is kept (no new token is minted)" {
    run "$CLONE/bin/romp-uninstall" --yes
    [ "$status" -eq 0 ]
    [[ "$output" != *"signed out"* ]]
    [[ "$output" != *"token page"* ]]
}

# ── the judge scratch's Claude Code project dir, which lives OUTSIDE the state dir ───────────
# romp runs judges as `claude` rooted at JUDGE_SCRATCH, so Claude Code writes their transcripts to
# a project dir keyed on that path — outside $STATE, so --purge missed them and a reinstalled romp
# kept showing judge records from the PREVIOUS install. They are romp's own droppings, never a
# session anyone started, so the teardown owns them.
#
# The scratch itself moved from /tmp/romp-judge into "$STATE/judge-scratch", so --purge now takes the
# directory as part of the state root; the DERIVED project dir is still keyed on the path, which is
# what the default below has to track.

@test "romp-uninstall: the judge scratch default follows the state root, not /tmp" {
    # A stale default would leave the derived project dir behind — the 2026-07-27 incomplete-teardown
    # symptom, returning under a new name. ROMP_JUDGE_SCRATCH is deliberately NOT set here.
    export CLAUDE_CONFIG_DIR="$HOME/.claude"
    scratch="$ROMP_STATE_DIR/judge-scratch"
    mkdir -p "$scratch"
    # Build the expected directory the way CLAUDE CODE does — kernel/judge.py's _proj_dir rule,
    # every non-alphanumeric replaced over the realpath — NOT with the shell substitution the
    # script under test uses. Deriving it the same way the code does is how this test passed while
    # --purge swept a directory that never existed: it asserted the implementation against itself.
    proj="$CLAUDE_CONFIG_DIR/projects/$(_proj_slug "$scratch")"
    mkdir -p "$proj"
    echo '{"synthetic":"judge call"}' > "$proj/11111111-2222-3333-4444-555555555555.jsonl"

    run "$CLONE/bin/romp-uninstall" --yes --purge
    [ "$status" -eq 0 ]
    [ ! -d "$proj" ]
}

@test "romp-uninstall: removes romp's judge scratch and its transcripts" {
    export ROMP_JUDGE_SCRATCH="$TEST_DIR/romp-judge"
    export CLAUDE_CONFIG_DIR="$HOME/.claude"
    mkdir -p "$ROMP_JUDGE_SCRATCH"
    proj="$CLAUDE_CONFIG_DIR/projects/$(_proj_slug "$ROMP_JUDGE_SCRATCH")"
    mkdir -p "$proj"
    echo '{"synthetic":"judge call"}' > "$proj/11111111-2222-3333-4444-555555555555.jsonl"

    run "$CLONE/bin/romp-uninstall" --yes --purge
    [ "$status" -eq 0 ]

    [ ! -d "$ROMP_JUDGE_SCRATCH" ]
    [ ! -d "$proj" ]
}

@test "romp-uninstall: never touches YOUR sessions under ~/.claude/projects" {
    export ROMP_JUDGE_SCRATCH="$TEST_DIR/romp-judge"
    export CLAUDE_CONFIG_DIR="$HOME/.claude"
    mkdir -p "$ROMP_JUDGE_SCRATCH"
    mkdir -p "$CLAUDE_CONFIG_DIR/projects/$(_proj_slug "$ROMP_JUDGE_SCRATCH")"
    # A real project dir of the user's, which must survive untouched — deleting it would
    # destroy their own Claude Code history, which is not romp's to remove.
    mine="$CLAUDE_CONFIG_DIR/projects/-home-someone-notes-api"
    mkdir -p "$mine"
    echo '{"synthetic":"my session"}' > "$mine/99999999-8888-7777-6666-555555555555.jsonl"

    run "$CLONE/bin/romp-uninstall" --yes --purge
    [ "$status" -eq 0 ]

    [ -f "$mine/99999999-8888-7777-6666-555555555555.jsonl" ]
    [ -d "$CLAUDE_CONFIG_DIR/projects" ]
}

@test "romp-uninstall: a mangled judge scratch can never resolve to the projects dir itself" {
    export CLAUDE_CONFIG_DIR="$HOME/.claude"
    mine="$CLAUDE_CONFIG_DIR/projects/-home-someone-notes-api"
    mkdir -p "$mine"
    echo '{"synthetic":"my session"}' > "$mine/99999999-8888-7777-6666-555555555555.jsonl"

    # Each of these would expand to a catastrophic `rm -rf` if taken at face value.
    for bad in "/" "" "$HOME" "/tmp" "/tmp/romp-judge/.."; do
        ROMP_JUDGE_SCRATCH="$bad" run "$CLONE/bin/romp-uninstall" --yes --purge
        [ "$status" -eq 0 ]                       # skipped, not aborted
        [ -d "$CLAUDE_CONFIG_DIR/projects" ]
        [ -f "$mine/99999999-8888-7777-6666-555555555555.jsonl" ]
        [ -d "$HOME" ]
    done
    [ -d "/tmp" ]
}

# ── one directory, many spellings ────────────────────────────────────────────
# Every guard on the scratch path compares strings, and "$HOME" and "$HOME/" are different
# strings for the same directory — as are "$HOME/.", "$DIR//home", "$DIR/./home" and a value
# with a trailing newline. Each one walks past the equality checks and reaches the `rm -rf`.
# These enumerate the spelling CLASS as inputs and assert the protected directory survives,
# rather than the two or three spellings any one normalisation happens to handle.

@test "romp-uninstall: no spelling of \$HOME reaches rm -rf (trailing slash, /., //, newline)" {
    # The hole needs a home path that passes the *romp* name check — real ones exist (a user
    # named after the project, a home under /srv/romp). Everything in it is then at stake.
    export HOME="$TEST_DIR/romp-home"
    mkdir -p "$HOME/keep"
    echo "irreplaceable" > "$HOME/keep/precious.txt"
    for bad in "$HOME/" "$HOME//" "$HOME/." "$HOME/./" "$HOME/"$'\n' \
               "$TEST_DIR//romp-home" "$TEST_DIR/./romp-home" "$TEST_DIR/romp-home/."$'\n'; do
        ROMP_JUDGE_SCRATCH="$bad" run "$CLONE/bin/romp-uninstall" --yes
        [ -f "$HOME/keep/precious.txt" ]          # the whole home directory, one character away
        [ "$status" -eq 0 ]                       # skipped loudly, never aborted mid-run
        [[ "$output" == *"skipped"* ]]
    done
}

@test "romp-uninstall: no spelling of the state dir reaches rm -rf — a plain --yes keeps every record" {
    # The state dir contains "romp" on every default install, so it passes the name check, and
    # this teardown is NOT gated on --purge. Without an explicit refusal a scratch aimed at the
    # state root deletes the very records a no---purge uninstall promises to keep — and the
    # state dir is only ever removed by --purge, behind its own separate confirmation.
    export ROMP_STATE_DIR="$TEST_DIR/state-romp"
    mkdir -p "$ROMP_STATE_DIR"
    echo '{"synthetic":"record"}' > "$ROMP_STATE_DIR/serve-token"
    for bad in "$ROMP_STATE_DIR" "$ROMP_STATE_DIR/" "$ROMP_STATE_DIR/." "$ROMP_STATE_DIR/"$'\n' \
               "$TEST_DIR//state-romp" "$TEST_DIR/./state-romp" "$TEST_DIR/state-romp/."$'\n'; do
        ROMP_JUDGE_SCRATCH="$bad" run "$CLONE/bin/romp-uninstall" --yes
        [ -f "$ROMP_STATE_DIR/serve-token" ]      # a plain --yes keeps the records, always
        [ "$status" -eq 0 ]
        [[ "$output" == *"skipped"* ]]
    done
}

@test "romp-uninstall: a symlinked scratch is refused, trailing slash or not" {
    # A symlink at the scratch path points the teardown at a directory romp never created, so
    # there is nothing romp-owned under it to clean. Refused on the final component only, so a
    # symlinked ANCESTOR — a relocated state dir — still works. The trailing-slash spelling is
    # the one that matters: `[[ -L "$p/" ]]` is FALSE for a symlink to a directory, because the
    # slash makes the test resolve the link instead of lstat'ing it.
    export CLAUDE_CONFIG_DIR="$HOME/.claude"
    mkdir -p "$TEST_DIR/someone-elses-project"
    echo "not romp's" > "$TEST_DIR/someone-elses-project/keep.txt"
    ln -s "$TEST_DIR/someone-elses-project" "$TEST_DIR/romp-judge"
    for spell in "$TEST_DIR/romp-judge/" "$TEST_DIR/romp-judge"; do
        ROMP_JUDGE_SCRATCH="$spell" run "$CLONE/bin/romp-uninstall" --yes
        # `rm -rf "$link/"` cannot unlink the link, so it empties the directory behind it.
        [ -f "$TEST_DIR/someone-elses-project/keep.txt" ]
        [ "$status" -eq 0 ]
        [[ "$output" == *"skipped"* ]]
        [ -L "$TEST_DIR/romp-judge" ]
    done
}

@test "romp-uninstall: romp's own scratch is still cleaned when spelt with // or a trailing slash" {
    # Normalising must not become a reason to SKIP a legitimate scratch: "$DIR//romp-judge" and
    # "$DIR/romp-judge/" both name romp's own directory and must still be torn down, transcripts
    # and all.
    export CLAUDE_CONFIG_DIR="$HOME/.claude"
    scratch="$TEST_DIR/romp-judge"
    for spell in "$TEST_DIR//romp-judge" "$scratch/" "$scratch/."; do
        mkdir -p "$scratch"
        proj="$CLAUDE_CONFIG_DIR/projects/$(_proj_slug "$scratch")"
        mkdir -p "$proj"
        echo '{"synthetic":"judge call"}' > "$proj/11111111-2222-3333-4444-555555555555.jsonl"

        ROMP_JUDGE_SCRATCH="$spell" run "$CLONE/bin/romp-uninstall" --yes --purge
        [ "$status" -eq 0 ]
        [[ "$output" != *"skipped:"* ]]           # NOT skipped — it is romp's own
        [ ! -d "$scratch" ]
        [ ! -d "$proj" ]
    done
}
