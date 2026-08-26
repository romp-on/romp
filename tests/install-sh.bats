#!/usr/bin/env bats

# ./install.sh — "install romp normally, then everything (incl. federating this host from another
# dashboard) just works". Hermetic: HOME points at a temp dir; the login service, VS Code extension
# and SDK-venv steps are opted out (ROMP_NO_SERVICE / ROMP_NO_EXT / ROMP_NO_SDK) — they touch the
# real machine or the network. What's covered: hook symlinks, the idempotent settings.json merge,
# and the MCP/skills symlinks.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    mkdir -p "$HOME"
    export ROMP_NO_SERVICE=1 ROMP_NO_EXT=1 ROMP_NO_SDK=1
    # One try only: the closing dashboard-link block polls for the kernel's token
    # file, which never appears in this hermetic HOME — don't wait 10s for it.
    export ROMP_INSTALL_TOKEN_TRIES=1
    # Redirect the git pre-push hook symlink into a temp dir so install.sh never
    # writes into the REAL repo's .git/hooks while these tests run.
    export ROMP_GITHOOK_DIR="$TEST_DIR/githooks"
}

teardown() { rm -rf "$TEST_DIR"; }

count_cmd() {   # occurrences of a hook script in one event's rules
    python3 - "$HOME/.claude/settings.json" "$1" "$2" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
n = sum(1 for r in s.get("hooks", {}).get(sys.argv[2], []) for h in r.get("hooks", [])
        if h.get("command", "").endswith(sys.argv[3]))
print(n)
PY
}

@test "install.sh: wires hooks, settings.json, and the MCP config on a fresh machine" {
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ -L "$HOME/.claude/hooks/tmux-status.sh" ]
    [[ "$(readlink "$HOME/.claude/hooks/tmux-status.sh")" == *"/hooks/tmux-status.sh" ]]
    [ "$(count_cmd Stop tmux-status.sh)" = "1" ]
    [ "$(count_cmd Stop romp-summarize.sh)" = "1" ]
    [ "$(count_cmd Stop romp-postal-drain.sh)" = "1" ]
    [ "$(count_cmd SessionStart romp-postal-ensure.sh)" = "1" ]
    [ "$(count_cmd PostToolUse tmux-status.sh)" = "1" ]
    [ -L "$HOME/.claude/romp-postal.mcp.json" ]
}

@test "install.sh: idempotent — a second run adds no duplicate hook entries" {
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"already registered"* ]]
    [ "$(count_cmd Stop tmux-status.sh)" = "1" ]
    [ "$(count_cmd UserPromptSubmit romp-summarize.sh)" = "1" ]
    # regression: a re-run used to FOLLOW the existing skill dir-symlink and drop a new link INSIDE
    # the repo (claude/skills/romp-postal/romp-postal → an absolute personal path). ln -sfn replaces
    # the link.
    [ ! -e "$ROMP_DIR/claude/skills/romp-postal/romp-postal" ]
    [ -L "$HOME/.claude/skills/romp-postal" ]
}

@test "install.sh: the retired bundled manager skill unlinks ONLY when it points into this repo" {
    # The manager skill moved to the user's dotfiles 2026-08-23 (romp ships primitives; workflows
    # live outside). The dotfiles successor claims the SAME ~/.claude/skills/manager name, so the
    # upgrade cleanup must remove a link into $ROMP_DIR and leave any other target alone — a plain
    # unlink here would delete the successor the moment a user reinstalls romp.
    mkdir -p "$HOME/.claude/skills"   # the hermetic HOME starts empty; ln needs the parent
    ln -s "$ROMP_DIR/claude/skills/manager" "$HOME/.claude/skills/manager"
    run bash "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ ! -e "$HOME/.claude/skills/manager" ] && [ ! -L "$HOME/.claude/skills/manager" ]
    mkdir -p "$HOME/dotfiles-skills/manager"
    ln -s "$HOME/dotfiles-skills/manager" "$HOME/.claude/skills/manager"
    run bash "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ -L "$HOME/.claude/skills/manager" ]   # the dotfiles successor link survives the re-run
}

@test "install.sh: upgrading unlinks the retired romp skill, leaving no dangling link" {
    # An install from before 2026-07-27 has ~/.claude/skills/romp pointing at a directory this repo
    # no longer ships. Upgrading must clear it: a dangling symlink puts a broken skill in front of
    # every session.
    mkdir -p "$HOME/.claude/skills"
    ln -sfn "$ROMP_DIR/claude/skills/romp" "$HOME/.claude/skills/romp"
    [ -L "$HOME/.claude/skills/romp" ]

    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]

    [ ! -L "$HOME/.claude/skills/romp" ]
    [ ! -e "$HOME/.claude/skills/romp" ]
    [ -L "$HOME/.claude/skills/romp-postal" ]      # its neighbour is untouched
}

@test "install.sh: a real directory named romp survives — only a symlink is removed" {
    # Someone else's skill of that name is theirs, not ours to delete.
    mkdir -p "$HOME/.claude/skills/romp"
    echo "mine" > "$HOME/.claude/skills/romp/SKILL.md"

    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]

    [ -f "$HOME/.claude/skills/romp/SKILL.md" ]
}

@test "install.sh: preflight fails clearly when node is missing" {
    ROMP_NODE=romp-test-no-such-node run "$ROMP_DIR/install.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"Node.js not found"* ]]
    [[ "$output" == *"brew install node"* ]]
    # nothing was installed: the preflight runs before any mutation
    [ ! -e "$HOME/.claude/hooks/tmux-status.sh" ]
}

@test "install.sh: ROMP_SKIP_PREFLIGHT bypasses the checks" {
    ROMP_NODE=romp-test-no-such-node ROMP_SKIP_PREFLIGHT=1 run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ -L "$HOME/.claude/hooks/tmux-status.sh" ]
}

# The login-service step (the user's rescue_me, 2026-07-21): a webview deploy must never bootout a
# HEALTHY romp-manager, and must FAIL LOUDLY (not `|| echo`-swallow) if an install it DID attempt fails —
# the swallowed failure is what left the dashboard dead on :29855. ROMP_SERVICE_BIN stubs romp-service.
_svc_stub() {   # write a fake romp-service to $1; behavior toggled by ROMP_SVC_RUNNING / ROMP_SVC_FAIL
    cat > "$1" <<'SH'
#!/usr/bin/env bash
echo "$1" >> "$ROMP_SVC_LOG"
case "$1" in
  status) echo "installed: /tmp/plist"; [[ -n "${ROMP_SVC_RUNNING:-}" ]] && echo "running" ;;
  install) [[ -n "${ROMP_SVC_FAIL:-}" ]] && { echo "romp-service: bootstrap lost the drain-race" >&2; exit 1; } ;;
esac
exit 0
SH
    chmod +x "$1"
}

@test "install.sh: skips the service bootout when romp-manager is already running" {
    unset ROMP_NO_SERVICE
    _svc_stub "$TEST_DIR/romp-service"
    export ROMP_SVC_LOG="$TEST_DIR/svc.log" ROMP_SVC_RUNNING=1
    ROMP_SERVICE_BIN="$TEST_DIR/romp-service" run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"already running"* ]]
    # it asked status but NEVER ran install — the healthy manager was left up
    grep -qx status "$TEST_DIR/svc.log"
    ! grep -qx install "$TEST_DIR/svc.log"
}

@test "install.sh: installs the service when romp-manager is NOT running" {
    unset ROMP_NO_SERVICE
    _svc_stub "$TEST_DIR/romp-service"
    export ROMP_SVC_LOG="$TEST_DIR/svc.log"   # ROMP_SVC_RUNNING unset -> not running
    ROMP_SERVICE_BIN="$TEST_DIR/romp-service" run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    grep -qx install "$TEST_DIR/svc.log"
}

@test "install.sh: a FAILED service install fails the whole run loudly (never swallowed)" {
    unset ROMP_NO_SERVICE
    _svc_stub "$TEST_DIR/romp-service"
    export ROMP_SVC_LOG="$TEST_DIR/svc.log" ROMP_SVC_FAIL=1   # not running + install exits 1
    ROMP_SERVICE_BIN="$TEST_DIR/romp-service" run "$ROMP_DIR/install.sh"
    [ "$status" -ne 0 ]
    [[ "$output" == *"romp-service install FAILED"* ]]
    [[ "$output" == *"dashboard will be dead"* ]]
    grep -qx install "$TEST_DIR/svc.log"
}

# ── The closing dashboard link (the user 2026-07-25, who wanted installing alone to be
# enough to reach the dashboard) ── install.sh ends with the TOKENED link when the kernel
# has minted the token, and an honest pointer when it hasn't; it must never print a bare
# URL that bounces the first-time user to the paste-a-token login page.

@test "install.sh: ends with the tokened dashboard link when the token exists" {
    mkdir -p "$HOME/.local/state/romp"
    printf 'tok123\n' > "$HOME/.local/state/romp/serve-token"
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"http://127.0.0.1:29855/?token=tok123"* ]]
    [[ "$output" == *"romp url"* ]]
}

@test "install.sh: ROMP_NO_SERVICE with no token points at romp up, never a dead link" {
    run "$ROMP_DIR/install.sh"     # setup sets ROMP_NO_SERVICE=1; no serve-token exists
    [ "$status" -eq 0 ]
    grep -q "romp up" <<<"$output"
    grep -q "romp url" <<<"$output"
    ! grep -q "?token=" <<<"$output"
}

@test "install.sh: service up but token not minted yet, says how to get the link" {
    unset ROMP_NO_SERVICE
    _svc_stub "$TEST_DIR/romp-service"
    export ROMP_SVC_LOG="$TEST_DIR/svc.log" ROMP_SVC_RUNNING=1
    ROMP_SERVICE_BIN="$TEST_DIR/romp-service" run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    grep -q "still starting" <<<"$output"
    grep -q "romp url" <<<"$output"
    ! grep -q "?token=" <<<"$output"
}

@test "install.sh: merges into an existing settings.json without clobbering the user's own config" {
    mkdir -p "$HOME/.claude"
    cat > "$HOME/.claude/settings.json" <<'JSON'
{
  "model": "opus",
  "hooks": {
    "Stop": [ { "hooks": [ { "type": "command", "command": "my-own-hook.sh" } ] } ]
  }
}
JSON
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    python3 - "$HOME/.claude/settings.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
assert s["model"] == "opus", "unrelated settings preserved"
stop = [h["command"] for r in s["hooks"]["Stop"] for h in r["hooks"]]
assert "my-own-hook.sh" in stop, stop
assert any(c.endswith("tmux-status.sh") for c in stop), stop
PY
}

# ── vscode-extension/install.sh: the build version is stamped, never committed ──
# The editor caches extension code BY VERSION, so each install must carry a strictly
# newer one; that number used to be written back into package.json and committed,
# which produced a version-churn commit per install and a package.json version that
# read like a romp release version without being one. The stamp now lives only in the
# packaged .vsix. These pin that: package.json comes back byte-identical, and the
# stamp it briefly held is strictly greater than the committed baseline.

ext_setup() {   # a throwaway copy so nothing here can touch the real extension dir
    EXT="$TEST_DIR/ext"
    mkdir -p "$EXT"
    cp "$ROMP_DIR/vscode-extension/install.sh" "$EXT/install.sh"
    printf '{\n  "name": "romp-chat-view",\n  "version": "0.4.0"\n}\n' > "$EXT/package.json"
    echo 'require("fs").writeFileSync("dist.marker","built")' > "$EXT/esbuild.js"
    # stub the toolchain the script shells out to; real node does the stamping
    mkdir -p "$TEST_DIR/stub"
    printf '#!/bin/sh\nexit 0\n' > "$TEST_DIR/stub/npm"
    printf '#!/bin/sh\ntouch romp-chat-view.vsix\nexit 0\n' > "$TEST_DIR/stub/npx"
    chmod +x "$TEST_DIR/stub/npm" "$TEST_DIR/stub/npx"
    export PATH="$TEST_DIR/stub:$PATH"
}

@test "vscode-extension/install.sh: restores package.json, leaving the committed version untouched" {
    ext_setup
    before="$(cat "$EXT/package.json")"
    ROMP_EXT_PACKAGE_ONLY=1 run "$EXT/install.sh"
    [ "$status" -eq 0 ]
    [ "$(cat "$EXT/package.json")" = "$before" ]   # byte-identical
    [ ! -f "$EXT/package.json.orig" ]              # no scratch file left behind
    [[ "$output" == *"build version -> 0.4."* ]]
    [[ "$output" == *"not committed"* ]]
}

@test "vscode-extension/install.sh: the stamped version is strictly newer than the committed baseline" {
    ext_setup
    ROMP_EXT_PACKAGE_ONLY=1 run "$EXT/install.sh"
    [ "$status" -eq 0 ]
    stamped="$(echo "$output" | sed -n 's/.*build version -> \([0-9.]*\).*/\1/p')"
    [ -n "$stamped" ]
    python3 - "$stamped" <<'PY'
import sys
base = (0, 4, 0)                       # the committed baseline in this fixture
got = tuple(int(x) for x in sys.argv[1].split("."))
assert got[:2] == base[:2], f"major.minor must not move (a lower one reads as a DOWNGRADE): {got}"
assert got > base, f"stamp must be strictly newer than the baseline: {got} !> {base}"
PY
}

@test "vscode-extension/install.sh: restores package.json even when packaging FAILS" {
    ext_setup
    printf '#!/bin/sh\nexit 3\n' > "$TEST_DIR/stub/npx"   # vsce package blows up mid-run
    chmod +x "$TEST_DIR/stub/npx"
    before="$(cat "$EXT/package.json")"
    ROMP_EXT_PACKAGE_ONLY=1 run "$EXT/install.sh"
    [ "$status" -ne 0 ]                            # the failure still surfaces
    [ "$(cat "$EXT/package.json")" = "$before" ]   # ...and the trap still restored
    [ ! -f "$EXT/package.json.orig" ]
}

# ── git pre-push identifier hook ──────────────────────────────────────
# install.sh symlinks .githooks/pre-push into the shared git hooks dir. The hook
# reads the banned strings from ~/.config/romp/private-strings.txt (so it arms
# EVERY worktree, not just the one holding an untracked scanner) and greps each
# PUSHED commit's tree — the working tree is not what gets published, and a leak
# in an intermediate commit ships even when the tip is clean. No strings file →
# a no-op, so a contributor's clone is unaffected. (ROMP_GITHOOK_DIR redirects
# install.sh's symlink target below; the behaviour tests copy the hook directly.)

@test "install.sh: symlinks the pre-push hook into the git hooks dir" {
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ -L "$ROMP_GITHOOK_DIR/pre-push" ]
    [[ "$(readlink "$ROMP_GITHOOK_DIR/pre-push")" == *"/.githooks/pre-push" ]]
}

@test "install.sh: ROMP_NO_GITHOOK skips the pre-push hook" {
    ROMP_NO_GITHOOK=1 run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [ ! -e "$ROMP_GITHOOK_DIR/pre-push" ]
}

# Behaviour of the hook itself, exercised through a real `git push` to a bare
# remote, with a SYNTHETIC denylist (never a real identifier) via XDG_CONFIG_HOME.
setup_hook_repo() {
    export XDG_CONFIG_HOME="$TEST_DIR/cfg"
    mkdir -p "$XDG_CONFIG_HOME/romp"
    printf '# synthetic denylist\n\nZZBANNEDZZ\n' > "$XDG_CONFIG_HOME/romp/private-strings.txt"
    git init -q "$TEST_DIR/remote.git" --bare
    WORK="$TEST_DIR/work"
    git init -q "$WORK"
    git -C "$WORK" config user.email t@e.invalid
    git -C "$WORK" config user.name t
    cp "$ROMP_DIR/.githooks/pre-push" "$WORK/.git/hooks/pre-push"
    git -C "$WORK" remote add origin "$TEST_DIR/remote.git"
}

@test "pre-push hook: allows a push when every pushed tree is clean" {
    setup_hook_repo
    echo "clean" > "$WORK/ok.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm clean
    run git -C "$WORK" push origin HEAD:main
    [ "$status" -eq 0 ]
}

@test "pre-push hook: blocks a push that would leak an identifier" {
    setup_hook_repo
    printf 'leak ZZBANNEDZZ here\n' > "$WORK/leak.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm leak
    run git -C "$WORK" push origin HEAD:main
    [ "$status" -ne 0 ]
    [[ "$output" == *"BLOCKED"* ]]
}

@test "pre-push hook: blocks a leak in an intermediate commit whose tip is clean" {
    # The regression that motivated the pushed-tree scan: a string introduced and
    # then removed mid-branch still ships in history even though the tip greps clean.
    setup_hook_repo
    printf 'leak ZZBANNEDZZ here\n' > "$WORK/leak.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm leak
    git -C "$WORK" rm -q leak.txt && git -C "$WORK" commit -qm remove
    run git -C "$WORK" push origin HEAD:main
    [ "$status" -ne 0 ]
    [[ "$output" == *"BLOCKED"* ]]
}

@test "pre-push hook: only commits NEW to the remote are scanned" {
    # A string that already escaped to the remote (before the denylist knew it)
    # must not block every future push — only what this push publishes counts.
    setup_hook_repo
    printf 'old ZZBANNEDZZ\n' > "$WORK/old.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm old
    git -C "$WORK" push --no-verify -q origin HEAD:main
    git -C "$WORK" rm -q old.txt && git -C "$WORK" commit -qm clean-tip
    run git -C "$WORK" push origin HEAD:main
    [ "$status" -eq 0 ]
}

@test "pre-push hook: --no-verify bypasses the block" {
    setup_hook_repo
    printf 'leak ZZBANNEDZZ here\n' > "$WORK/leak.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm leak
    run git -C "$WORK" push --no-verify origin HEAD:main
    [ "$status" -eq 0 ]
}

@test "pre-push hook: no denylist file means the hook stays out of the way" {
    setup_hook_repo
    rm "$XDG_CONFIG_HOME/romp/private-strings.txt"
    printf 'would leak ZZBANNEDZZ\n' > "$WORK/leak.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm leak
    run git -C "$WORK" push origin HEAD:main
    [ "$status" -eq 0 ]
}

@test "pre-push hook: a denylist of only comments and blanks bans nothing" {
    setup_hook_repo
    printf '# just a comment\n\n   \n' > "$XDG_CONFIG_HOME/romp/private-strings.txt"
    printf 'ZZBANNEDZZ\n' > "$WORK/leak.txt"
    git -C "$WORK" add -A && git -C "$WORK" commit -qm leak
    run git -C "$WORK" push origin HEAD:main
    [ "$status" -eq 0 ]
}

# ─── Claude Code version notice ──────────────────────────────────────

_stub_claude() {   # $1 = the version the stub reports; PATH-prepended so install.sh's probe sees it
    mkdir -p "$TEST_DIR/mock"
    cat > "$TEST_DIR/mock/claude" <<STUB
#!/usr/bin/env bash
echo "$1 (Claude Code)"
STUB
    chmod +x "$TEST_DIR/mock/claude"
    export PATH="$TEST_DIR/mock:$PATH"
}

@test "install.sh: an old Claude Code gets the upgrade notice at the end" {
    _stub_claude "2.1.220"
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"2.1.220"* ]]
    [[ "$output" == *"claude update"* ]]
}

@test "install.sh: a current Claude Code gets no upgrade notice" {
    _stub_claude "2.1.226"
    run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" != *"claude update"* ]]
}

@test "install.sh: the version floor matches bin/romp's (no drift)" {
    a="$(sed -n 's/^ROMP_CLAUDE_FLOOR="\(.*\)"$/\1/p' "$ROMP_DIR/install.sh" | head -1)"
    b="$(sed -n 's/^ROMP_CLAUDE_FLOOR="\(.*\)"$/\1/p' "$ROMP_DIR/bin/romp" | head -1)"
    [ -n "$a" ]
    [ "$a" = "$b" ]
}
