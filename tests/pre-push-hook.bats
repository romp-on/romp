#!/usr/bin/env bats

# .githooks/pre-push — the identifier scan, driven by hand against real commits.
#
# The hook refuses to publish a commit carrying a string from the machine's
# private-strings denylist. Every identifier below is SYNTHETIC (the repo may go
# public, and a real one written here would be the very leak the hook exists to
# stop): the denylist, the paths and the hostnames are all invented per test.
#
# The two CONTENT rules, both about what a push changes on the remote: the TIP
# tree of each pushed ref must be clean (that is what a push exposes), and each
# commit no fetched remote already has must ADD no banned line: a commit that
# only inherits an older leak in its tree is not refused, a commit that
# introduced one is, even if a later commit removed it again. The hook's two
# METADATA rules (each new commit's author and committer addresses, and its
# message, with an annotated tag's own tagger and message under the same two)
# have their own files, pre-push-identity.bats and pre-push-message.bats.
# install-sh.bats exercises the hook through a real `git push`; this file feeds
# it ref lines directly, so it can model a remote and its remote-tracking refs
# the way a clone has them.
#
# The tip scan reads symlink TARGETS as well as regular files: git grep reads
# regular-file blobs only, and a committed link's target is its blob content, so
# a link pointing into a home directory is a leak the grep pass alone cannot see.
# The added-lines pass sees a NEW link the way it sees any added line.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
HOOK="$ROMP_DIR/.githooks/pre-push"

load git-hermetic

setup() {
    # Hermetic git: the fixtures commit and merge with plain defaults, and a developer's global
    # config (merge.ff=only, commit.gpgsign, a hooks path) must not reach them (the #968 review).
    # The floor (tests/git-hermetic.bash) also forbids background git work in the repos below and
    # in the bare remote the pushes land in, and its exported identity is the one every commit
    # carries; the user.* lines below give the repo a configured user for anything that reads one.
    git_hermetic
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    mkdir -p "$HOME"
    REPO="$TEST_DIR/repo"
    mkdir -p "$REPO"
    git -C "$REPO" init -q
    git -C "$REPO" symbolic-ref HEAD refs/heads/main     # whatever init.defaultBranch says
    git -C "$REPO" config user.email t@example.invalid
    git -C "$REPO" config user.name  Tester
    # The hook under test is run BY HAND below; the fixture's own git operations
    # (commits, pushes to the bare remote) must not run this machine's hooks.
    mkdir -p "$TEST_DIR/no-hooks"
    git -C "$REPO" config core.hooksPath "$TEST_DIR/no-hooks"

    # the denylist: invented identifiers, nothing that exists on any real box
    STRINGS="$TEST_DIR/private-strings.txt"
    printf '# synthetic\nzzsynthuser\nTESTHOST\n' > "$STRINGS"

    export ROMP_PRIVATE_STRINGS="$STRINGS"
    export ROMP_NO_GITLEAKS=1          # the credential half has its own test file
}

teardown() { rm -rf "${TEST_DIR:-}"; }

ZERO=0000000000000000000000000000000000000000

# Run the hook from inside the repo, the way git does. bats' `run` executes the
# command in a subshell, so the cd here does not leak into the test. (A cd
# helper rather than `env -C`, which BSD env lacks.)
_hook_in() { cd "$1" && shift && bash "$@"; }

# Feed the hook a ref line the way git does: <local_ref> <sha> <remote_ref> <remote_sha>.
# The default remote sha of zero means a new branch, i.e. every commit here is
# being published; pass the sha the remote holds to model updating a branch it has.
run_hook() {
    local sha remote_sha="${1:-$ZERO}"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run _hook_in "$REPO" "$HOOK" origin git@example.invalid:x/y.git <<< \
        "refs/heads/main $sha refs/heads/main $remote_sha"
}

commit_file() {   # <path> <content> <message>
    printf '%s\n' "$2" > "$REPO/$1"
    git -C "$REPO" add "$1"
    git -C "$REPO" commit -qm "$3"
}

# A bare remote named origin, so refs/remotes/origin/* exist the way they do in a
# real clone: the hook decides what is "already on the remote" from those refs.
add_remote() {
    git init -q --bare "$TEST_DIR/remote.git"
    git -C "$REPO" remote add origin "$TEST_DIR/remote.git"
}

remove_file() {   # <path> <message>
    git -C "$REPO" rm -q "$1"
    git -C "$REPO" commit -qm "$2"
}

# main publishes an identifier (LEAK_SHA, pushed, so the remote has it); a branch
# is cut from there and commits work of its own, whose tree INHERITS the leak
# although its diff is clean. Leaves HEAD on the branch; main has not yet redacted.
branch_inheriting_mains_leak() {
    add_remote
    commit_file base.txt "notes-api" "base"
    commit_file leak.txt "home is /home/zzsynthuser/code" "leak"
    LEAK_SHA="$(git -C "$REPO" rev-parse HEAD)"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q -b feature
    commit_file web.txt "the web session's work" "branch work"
}

# ...and main redacts the leak (pushed), and the branch merges main: its own
# earlier commit still has the leaky tree, but its tip is clean. The leak is
# leak.txt unless a path is given.
main_redacts_and_branch_merges() {   # [<path>]
    git -C "$REPO" checkout -q main
    remove_file "${1:-leak.txt}" "redact"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q feature
    git -C "$REPO" merge -q -m "merge main" main
}

# The same shape with a SYMLINK: main commits and pushes a link whose target is
# a home path (a node_modules link made to run tests in a worktree, swept up by
# a broad `git add`), and a branch cut from there inherits it. Leaves HEAD on
# the branch; main has not yet removed the link.
branch_inheriting_mains_symlink_leak() {
    add_remote
    commit_file base.txt "notes-api" "base"
    ln -s /home/zzsynthuser/code/romp/vscode-extension/node_modules "$REPO/node_modules"
    git -C "$REPO" add node_modules
    git -C "$REPO" commit -qm "symlink leak"
    LEAK_SHA="$(git -C "$REPO" rev-parse HEAD)"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q -b feature
    commit_file web.txt "the web session's work" "branch work"
}

@test "a clean commit passes" {
    commit_file file.txt "nothing to see" "clean"
    run_hook
    [ "$status" -eq 0 ]
}

@test "an identifier in a regular file is blocked" {
    commit_file file.txt "home is /home/zzsynthuser/code" "leak"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"personal identifier"* ]]
}

@test "an identifier in an INTERMEDIATE commit is caught, not just the tip" {
    commit_file bad.txt "home is /home/zzsynthuser/x" "leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    remove_file bad.txt "remove it"               # tip is clean; history is not
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS a personal identifier"* ]]
    [[ "$output" == *"  bad.txt"* ]]
}

@test "an added line shaped like a diff header is content, not a new path" {
    # In a patch, content `++ b/decoy.txt` renders as `+++ b/decoy.txt`, the same
    # text as a file header. Only a header names the path the next hit belongs to.
    printf '%s\n' '++ b/decoy.txt' 'home is /home/zzsynthuser/x' > "$REPO/real.txt"
    git -C "$REPO" add real.txt
    git -C "$REPO" commit -qm "leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    remove_file real.txt "remove it"              # tip is clean, so the added-lines pass decides
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS a personal identifier"* ]]
    [[ "$output" == *"  real.txt"* ]]
    [[ "$output" != *"decoy.txt"* ]]
}

@test "no denylist file means no identifier scan (a fresh clone is unaffected)" {
    export ROMP_PRIVATE_STRINGS="$TEST_DIR/does-not-exist.txt"
    commit_file bad.txt "home is /home/zzsynthuser/x" "leak"
    run_hook
    [ "$status" -eq 0 ]
}

# ── which commits a push publishes ────────────────────────────────────────
# A leak already on the remote is fixed forward on main, not by refusing every
# branch cut since: the tip tree must be clean, and only commits the remote lacks
# are read, for the lines they ADD.

@test "a branch that only INHERITED main's leak in its tree passes once its tip merged the redaction" {
    branch_inheriting_mains_leak
    main_redacts_and_branch_merges
    run_hook                                    # new ref: range = the branch commit + the merge
    [ "$status" -eq 0 ]
}

@test "the same branch is refused while its TIP still carries the inherited leak" {
    branch_inheriting_mains_leak
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the tip of refs/heads/main"* ]]   # named as the tip, not as an introduction
    [[ "$output" == *"leak.txt"* ]]
    [[ "$output" != *"ADDS"* ]]
    [[ "$output" == *"merge the main that has since redacted it"* ]]   # the remedy is spelled out
}

@test "a leak ADDED by a branch commit and removed by a later one is refused, naming the commit" {
    commit_file base.txt "notes-api" "base"
    commit_file leak.txt "home is /home/zzsynthuser/code" "leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    remove_file leak.txt "redact"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS a personal identifier"* ]]
    [[ "$output" == *"leak.txt"* ]]
}

@test "an identifier in the branch's OWN new commit is still refused after merging main" {
    branch_inheriting_mains_leak
    main_redacts_and_branch_merges
    commit_file api.txt "home is /home/zzsynthuser/api" "branch leak"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"ADDS a personal identifier"* ]]
    [[ "$output" == *"api.txt"* ]]
    [[ "$output" != *"leak.txt"* ]]       # main's commits were skipped, not re-flagged
}

# ── symlinks ──────────────────────────────────────────────────────────────
# A committed symlink is a blob holding its target, and git grep reads
# regular-file blobs only, so the tip pass reads each link's target itself. A
# link into a home directory, made to run tests in a worktree and swept up by a
# broad `git add`, reached a public branch while a git grep of its tree saw it
# clean. The repo's own bin/ links are relative and must not trip it.

@test "a branch that only INHERITED main's SYMLINK leak passes once its tip merged the removal" {
    branch_inheriting_mains_symlink_leak
    main_redacts_and_branch_merges node_modules
    run_hook
    [ "$status" -eq 0 ]
}

@test "the same branch is refused while its TIP still carries an inherited SYMLINK leak" {
    branch_inheriting_mains_symlink_leak
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"the tip of refs/heads/main"* ]]   # named as the tip, not as an introduction
    [[ "$output" == *"SYMLINK TARGET"* ]]
    [[ "$output" == *"node_modules -> /home/zzsynthuser/"* ]]
    [[ "$output" != *"ADDS"* ]]                          # main published the link; the branch added nothing
    [[ "$output" == *"merge the main that has since redacted it"* ]]
}

@test "a RELATIVE symlink with no identifier passes" {
    # the repo's own bin/romp-* links have this shape
    mkdir -p "$REPO/kernel" "$REPO/bin"
    printf '%s\n' "x" > "$REPO/kernel/kernel.py"
    ln -s ../kernel/kernel.py "$REPO/bin/romp-kernel"
    git -C "$REPO" add kernel bin
    git -C "$REPO" commit -qm "relative link"
    run_hook
    [ "$status" -eq 0 ]
}

@test "a NEW symlink at the tip is named with its link and target, by both passes" {
    ln -s /home/ZZSynthUser/notes "$REPO/notes-link"      # mixed case: matched case-insensitively, like a file
    git -C "$REPO" add notes-link
    git -C "$REPO" commit -qm "symlink leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"SYMLINK TARGET of notes-link -> /home/ZZSynthUser/notes"* ]]   # the tip pass
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS a personal identifier"* ]]   # and the commit that added it, as for a regular file
    [[ "$output" == *"  notes-link"* ]]
}

@test "an identifier in an INTERMEDIATE commit's SYMLINK TARGET is caught by the added-lines pass" {
    ln -s /home/zzsynthuser/x "$REPO/bad"
    git -C "$REPO" add bad
    git -C "$REPO" commit -qm "leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    remove_file bad "remove it"                   # tip is clean; history is not
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS a personal identifier"* ]]   # a new link's target is an added line of that path
    [[ "$output" == *"  bad"* ]]
    [[ "$output" != *"SYMLINK TARGET"* ]]        # the tip has no link left to name
}

@test "a dot in a denylist entry matches only a dot, in a file and in a symlink target" {
    # A hostname's dots are text, not "any character": every pass greps the
    # denylist as fixed strings, the symlink pass the same as the other two.
    printf 'nas.zzsynth.invalid\n' > "$STRINGS"
    commit_file mounts.txt "share on /mnt/nasXzzsynthXinvalid/" "near miss in a file"
    ln -s /mnt/nasXzzsynthXinvalid/share "$REPO/share"
    git -C "$REPO" add share
    git -C "$REPO" commit -qm "near miss in a link"
    run_hook
    [ "$status" -eq 0 ]
}

@test "a symlink whose blob cannot be read does not end the hook before its verdict" {
    # Under set -e a failed `target=$(git cat-file ...)` would exit the hook with
    # no message; such a link counts as empty and the other findings still print.
    commit_file leak.txt "home is /home/zzsynthuser/code" "leak"
    ln -s ../elsewhere "$REPO/link"
    git -C "$REPO" add link
    git -C "$REPO" commit -qm "link"
    blob="$(git -C "$REPO" rev-parse HEAD:link)"
    rm "$REPO/.git/objects/${blob:0:2}/${blob:2}"   # loose in a fresh repo; ls-tree still lists the entry
    run_hook
    [ "$status" -eq 1 ]                              # the hook's refusal; a set -e death exits 128
    [[ "$output" == *"leak.txt"* ]]
    [[ "$output" == *"BLOCKED"* ]]                   # the verdict was reached
}

# ── two remotes: a fork and the project it forked from ────────────────────
# "New" means new to EVERY fetched remote, not only the one being pushed to. With
# the exclusion scoped to the pushed-to remote, a clean branch cut from the
# project's main was refused on its way to the fork as a new ref, over a commit
# only the project's main reaches — named as ADDING a string the project had
# since redacted, which no rewrite of the branch could fix — and syncing the
# fork's main to the project's was refused the same way (the #968 review). A
# string a remote already holds is fixed forward on that remote, whichever one.

# The project (upstream) publishes an identifier and redacts it; the fork
# (origin) still holds only the base. Leaves HEAD on main, at the project's tip.
project_leaked_and_redacted_fork_behind() {
    add_remote
    git init -q --bare "$TEST_DIR/upstream.git"
    git -C "$REPO" remote add upstream "$TEST_DIR/upstream.git"
    commit_file base.txt "notes-api" "base"
    git -C "$REPO" push -q origin main
    git -C "$REPO" push -q upstream main
    commit_file leak.txt "home is /home/zzsynthuser/code" "leak"
    LEAK_SHA="$(git -C "$REPO" rev-parse HEAD)"
    remove_file leak.txt "redact"
    git -C "$REPO" push -q upstream main          # the fork's main is still at the base
}

@test "a clean branch cut from the project's main passes to the fork as a NEW ref" {
    project_leaked_and_redacted_fork_behind
    git -C "$REPO" checkout -q -b feature
    commit_file web.txt "the web session's work" "branch work"
    run_hook                                      # to origin, which reaches none of the project's commits
    [ "$status" -eq 0 ]
}

@test "syncing the fork's main to the project's passes" {
    project_leaked_and_redacted_fork_behind
    run_hook "$(git -C "$REPO" rev-parse origin/main)"   # updating origin's main from the base
    [ "$status" -eq 0 ]
}

@test "a commit no remote has is still scanned on the way to either remote" {
    # the exclusion excuses what a remote already published, never a leak still local to this clone
    project_leaked_and_redacted_fork_behind
    git -C "$REPO" checkout -q -b feature
    commit_file api.txt "home is /home/zzsynthuser/api" "branch leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    remove_file api.txt "remove it"               # tip is clean; the branch's own history is not
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS a personal identifier"* ]]
    [[ "$output" == *"  api.txt"* ]]
    [[ "$output" != *"${LEAK_SHA:0:10}"* ]]       # the project's own commit is not re-flagged
}

# ── merges ────────────────────────────────────────────────────────────────
# A merge's own additions are the lines in NONE of its parents: a conflict
# resolution, a line typed into the merge. A merge of main taken while main
# carried a string brings it in through the second parent, which the remote
# already has, and a diff against the first parent alone named the merge as
# adding it although the branch's tip was clean (the #968 review).

# A branch cut BEFORE main publishes an identifier merges main while the string
# is live (the merge's first-parent diff adds it; its second parent has it),
# then merges the redaction: the tip is clean. Leaves HEAD on the branch.
branch_merged_main_while_leak_was_live() {
    add_remote
    commit_file base.txt "notes-api" "base"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q -b feature
    commit_file web.txt "the web session's work" "branch work"
    git -C "$REPO" checkout -q main
    commit_file leak.txt "home is /home/zzsynthuser/code" "leak"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q feature
    git -C "$REPO" merge -q -m "merge main while the leak is live" main
    LIVE_MERGE_SHA="$(git -C "$REPO" rev-parse HEAD)"
    git -C "$REPO" checkout -q main
    remove_file leak.txt "redact"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q feature
    git -C "$REPO" merge -q -m "merge the redaction" main
}

@test "a merge of main taken while the leak was live is not the merge's own addition" {
    branch_merged_main_while_leak_was_live
    run_hook                # new ref: the branch commit and both merges are in the range, main's commits are not
    [ "$status" -eq 0 ]
}

@test "a string typed into a merge's conflict resolution is the merge's own addition, naming the merge" {
    add_remote
    commit_file notes.txt "notes-api" "base"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q -b feature
    commit_file notes.txt "the web session's line" "branch side"
    git -C "$REPO" checkout -q main
    commit_file notes.txt "the api session's line" "main side"
    git -C "$REPO" push -q origin main
    git -C "$REPO" checkout -q feature
    run git -C "$REPO" merge -q -m "merge main" main
    [ "$status" -ne 0 ]                           # both sides changed notes.txt: a conflict to resolve
    printf '%s\n' "resolved at /home/zzsynthuser/notes" > "$REPO/notes.txt"   # in neither parent
    git -C "$REPO" add notes.txt
    git -C "$REPO" commit -qm "merge main, resolved"
    merge_sha="$(git -C "$REPO" rev-parse HEAD)"
    commit_file notes.txt "settled" "settle"      # tip is clean, so the added-lines pass decides
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${merge_sha:0:10} ADDS a personal identifier"* ]]
    [[ "$output" == *"  notes.txt"* ]]
}

@test "with no remote-tracking refs, the same rule covers everything the remote ref lacks (the fallback)" {
    commit_file base.txt "notes-api" "base"
    remote_sha="$(git -C "$REPO" rev-parse HEAD)"
    commit_file leak.txt "home is /home/zzsynthuser/code" "leak"
    leak_sha="$(git -C "$REPO" rev-parse HEAD)"
    remove_file leak.txt "redact"
    commit_file web.txt "the web session's work" "more work"   # tip is clean; the range is not
    run_hook "$remote_sha"
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${leak_sha:0:10} ADDS"* ]]
}
