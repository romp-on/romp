#!/usr/bin/env bats

# `romp login add | list | remove` (T346; docs/reference.md "Several Claude logins"): the CLI door to the stored
# Claude logins. Run against a mock curl (the kernel's /logins answers), recording its calls: `add` hands the kernel
# a label and the COMMAND that prints the token (or the 1Password reference shorthand), never a token; `list`
# prints labels only.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    MOCK_DIR="$TEST_DIR/mock"
    export MOCK_LOG="$TEST_DIR/mock.log"
    mkdir -p "$MOCK_DIR"
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    mkdir -p "$HOME"
    unset ROMP_STATE_DIR
    export ROMP_MANAGER_PORT=1 ROMP_SERVE_PORT=1 ROMP_KERNEL_PORT=1
    export ROMP_SERVE_TOKEN=testtok
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null
url=""; body=""; prev=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; [[ "$prev" == "-d" ]] && body="$a"; prev="$a"; done
if [[ "$url" == */logins && -n "$body" ]]; then
  echo "body $body" >> "$MOCK_LOG"
  if [[ "$body" == *'"remove"'* ]]; then
    if [[ "$body" == *'"Nope"'* ]]; then echo '{"ok": false, "error": "no stored login named '"'"'Nope'"'"'"}'; else echo '{"ok": true, "removed": "0123456789ab", "label": "Work"}'; fi
  else
    echo '{"ok": true, "id": "0123456789ab", "label": "Work", "display": "Work"}'
  fi
  exit 0
fi
if [[ "$url" == */logins ]]; then
  echo '{"ok": true, "machine": {"label": "user@example.com · personal"}, "logins": [{"id": "0123456789ab", "label": "Work", "display": "Work · Acme · enterprise", "addedAt": 1700000000, "hasCmd": true, "why": "", "expiresSoon": false}, {"id": "abcdefabcdef", "label": "Old", "display": "Old", "addedAt": 1600000000, "hasCmd": true, "why": "the Old login was refused: expired", "expiresSoon": false}]}'
  exit 0
fi
echo '{"ok": true}'
MOCK
    chmod +x "$MOCK_DIR/curl"
    export PATH="$MOCK_DIR:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp help lists the verb" {
    run "$ROMP_SCRIPT" help
    [[ "$output" == *"romp login add|list|remove <label>"* ]]
}

@test "login list prints the machine login and the stored labels with their state, never a command" {
    run "$ROMP_SCRIPT" login list
    [ "$status" -eq 0 ]
    [[ "$output" == *"machine login: user@example.com · personal"* ]]
    [[ "$output" == *"Work · Acme · enterprise  (id 0123456789ab, added 2023-11-1"* ]]
    [[ "$output" == *"Old  (id abcdefabcdef"*"the Old login was refused: expired)"* ]]
    [[ "$output" != *"op read"* ]]
    grep -q 'curl .*/logins' "$MOCK_LOG"
}

@test "login add --cmd records the command verbatim and nothing else" {
    run "$ROMP_SCRIPT" login add Work --cmd 'cat ~/.secrets/enterprise-token'
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: added 'Work'"* ]]
    grep -q '"add": {"label": "Work", "tokenCmd": "cat ~/.secrets/enterprise-token"}' "$MOCK_LOG"
}

@test "login add --op hands the kernel the 1Password reference shorthand" {
    run "$ROMP_SCRIPT" login add Work --op 'op://Private/claude setup-token/credential'
    [ "$status" -eq 0 ]
    grep -q '"add": {"label": "Work", "opRef": "op://Private/claude setup-token/credential"}' "$MOCK_LOG"
    # one of the two, never both, never neither
    run "$ROMP_SCRIPT" login add Work
    [ "$status" -eq 2 ]
    [[ "$output" == *"give ONE of --cmd"* ]]
    run "$ROMP_SCRIPT" login add Work --cmd 'cat x' --op 'op://a/b/c'
    [ "$status" -eq 2 ]
}

@test "login remove relays the kernel's answer; usage errors exit 2" {
    run "$ROMP_SCRIPT" login remove Work
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp login: removed Work"* ]]
    grep -q '"remove": "Work"' "$MOCK_LOG"
    run "$ROMP_SCRIPT" login remove Nope
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp login: refused: no stored login named"* ]]
    run "$ROMP_SCRIPT" login
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" login remove
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" login list extra
    [ "$status" -eq 2 ]
}

@test "without a serve token every subcommand says the kernel is not running" {
    unset ROMP_SERVE_TOKEN
    run "$ROMP_SCRIPT" login list
    [ "$status" -eq 1 ]
    [[ "$output" == *"no serve token"* ]]
    run "$ROMP_SCRIPT" login add Work --cmd 'cat x'
    [ "$status" -eq 1 ]
}
