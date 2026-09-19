#!/usr/bin/env bats

# .githooks/pre-push: the ADDRESS scan, driven by hand against real commits. The
# author and committer addresses each new commit is stamped with, and an
# annotated tag's own tagger.
#
# A commit is published with its metadata, and the hook's two content scans (the
# tip's tree, each new commit's added lines) read none of it. A clone with no
# user.email in any config scope has git stamp <login>@<hostname -f> on every
# commit it makes, and a machine's name and its domain suffix are exactly the
# strings a private-strings denylist holds: such an address rode a branch's
# commits and a pushed merge through both content scans and gitleaks
# (2026-09-09). So the hook greps the DOMAIN of each address a new commit
# carries, like a line of content, for every commit no fetched remote has yet.
#
# Two limits, both pinned here because a scan of the whole identity would refuse
# every push its author makes: the login before the @ and the names are not read
# (they are the author's own, on the denylist too because a FILE must not name
# them, and on every commit they make); and an address the clone is configured
# to use (user.email in any scope, or GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL /
# EMAIL in the environment) is excused whatever its domain says, since it is the
# author's to publish and its domain may well be their own name. An unset
# user.email chooses nothing, which is the case the scan exists for.
#
# An annotated TAG carries a tagger the same way, and every other read the hook
# makes peels a tag to the commit it names, so the tag object's own address is
# read too: the tag cases at the end hold that.
#
# Every identifier below is SYNTHETIC: the denylist, the logins, the hosts and
# the domains are invented per test (the repo may go public, and a real one
# written here would be the very leak the hook exists to stop).
# pre-push-hook.bats holds the content scans' cases and pre-push-message.bats
# the message scan's; this file holds the address scan's.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
HOOK="$ROMP_DIR/.githooks/pre-push"

load git-hermetic

setup() {
    # Hermetic git: the fixture commits with plain defaults under a synthetic identity
    # (git-hermetic exports GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL as tests@example.invalid).
    # A developer's global config, above all their user.email, must reach neither the
    # commits nor the hook, whose whole subject is which address a clone is configured to use.
    git_hermetic
    # git_hermetic overrides GIT_AUTHOR_EMAIL and GIT_COMMITTER_EMAIL and leaves EMAIL,
    # git's own fallback for an unset user.email, to the developer's shell; the hook
    # reads it as a chosen address, so "not configured" below must not depend on it.
    unset EMAIL
    TEST_DIR="$(mktemp -d)"
    REPO="$TEST_DIR/repo"
    mkdir -p "$REPO"
    git -C "$REPO" init -q
    git -C "$REPO" symbolic-ref HEAD refs/heads/main     # whatever init.defaultBranch says
    # No user.email in the repo's config: the clone the scan exists for. A test that
    # models a configured address sets one itself.
    git -C "$REPO" config user.name Tester
    # The hook under test is run BY HAND below; the fixture's own git operations must
    # not run this machine's hooks.
    mkdir -p "$TEST_DIR/no-hooks"
    git -C "$REPO" config core.hooksPath "$TEST_DIR/no-hooks"

    # the denylist: an invented login, host and domain suffix, nothing that exists on any real machine
    STRINGS="$TEST_DIR/private-strings.txt"
    printf '# synthetic\nzzsynthuser\nTESTHOST\nzzsynthnet\n' > "$STRINGS"

    export ROMP_PRIVATE_STRINGS="$STRINGS"
    export ROMP_NO_GITLEAKS=1          # the credential half has its own test file
}

teardown() { rm -rf "${TEST_DIR:-}"; }

ZERO=0000000000000000000000000000000000000000

# What an unset user.email would stamp on this fixture's machine: the login and the
# machine's FQDN, all three parts on the denylist. The login alone is a banned string
# too, which the login-only case below relies on.
STAMPED="zzsynthuser@TESTHOST.zzsynthnet.example"

# Run the hook from inside the repo, the way git does. bats' `run` executes the
# command in a subshell, so the cd here does not leak into the test. The hook sees
# the hermetic identity in its environment (tests@example.invalid), never the
# address a fixture commit was stamped with: commit_as sets that for one command.
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

# Feed the hook a TAG ref line: refs/tags/<name>, pushed as new (the remote sha zero).
run_hook_tag() {   # <name>
    local sha
    sha="$(git -C "$REPO" rev-parse "refs/tags/$1")"
    run _hook_in "$REPO" "$HOOK" origin git@example.invalid:x/y.git <<< \
        "refs/tags/$1 $sha refs/tags/$1 $ZERO"
}

# A commit of one clean file, stamped with the given author and committer addresses
# (the environment identity outranks config, so this is exactly the commit's identity).
commit_as() {   # <author_email> <committer_email> <path> <message>
    printf '%s\n' "the web session's work on $3" > "$REPO/$3"
    git -C "$REPO" add "$3"
    GIT_AUTHOR_EMAIL="$1" GIT_COMMITTER_EMAIL="$2" git -C "$REPO" commit -qm "$4"
}

# A commit under the hermetic identity, with a clean tree: the control.
commit_clean() {   # <path> <message>
    commit_as tests@example.invalid tests@example.invalid "$1" "$2"
}

@test "a commit under the hermetic identity passes (the control)" {
    commit_clean ok.txt "clean"
    run_hook
    [ "$status" -eq 0 ]
}

@test "an address whose domain carries a banned string is refused, naming the commit, the roles and the address" {
    commit_as "$STAMPED" "$STAMPED" web.txt "stamped by an unset user.email"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is authored as <$STAMPED>"* ]]
    [[ "$output" == *"commit ${sha:0:10} is committed as <$STAMPED>"* ]]
    [[ "$output" == *"whose domain carries a personal identifier"* ]]
    [[ "$output" == *"BLOCKED"* ]]
    # the remedy covers both readings: the address is yours (configure it), or git filled it in (set yours, rewrite)
    [[ "$output" == *"git config --global user.email"* ]]
    [[ "$output" == *"user.useConfigOnly true"* ]]
    [[ "$output" == *"--reset-author"* ]]
}

@test "the address remedy is not printed for a content hit" {
    printf '%s\n' "home is /home/zzsynthuser/code" > "$REPO/leak.txt"
    git -C "$REPO" add leak.txt
    git -C "$REPO" commit -qm "leak in a file"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"personal identifier in:"* ]]
    [[ "$output" != *"--reset-author"* ]]
}

@test "the domain is matched case-insensitively, like a line of content" {
    commit_as "dev@testhost.example" "dev@testhost.example" web.txt "lowercase host"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"is authored as <dev@testhost.example>"* ]]
}

@test "the login before the @ is the author's own and is not read" {
    commit_as "zzsynthuser@example.invalid" "zzsynthuser@example.invalid" web.txt "login on the denylist"
    run_hook
    [ "$status" -eq 0 ]
}

@test "the address the clone is configured to use is excused, whatever its domain says" {
    # the author's own address, with their own name for a domain: on every commit they make
    git -C "$REPO" config user.email dev@zzsynthuser.example
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example web.txt "the configured address"
    run_hook
    [ "$status" -eq 0 ]
}

@test "the same address on a clone that did NOT configure it is refused" {
    # an unset user.email vouches for nothing: the hook cannot tell the author's own address
    # from one git filled in, and the remedy says which one-line config settles it
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example web.txt "the same address, unconfigured"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is authored as <dev@zzsynthuser.example>, an address this clone is not configured to use"* ]]
    [[ "$output" == *"if it is yours, say so (git config --global user.email <address>)"* ]]
}

@test "an address the environment chooses is excused too (EMAIL, git's own fallback for an unset user.email)" {
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example web.txt "the address EMAIL names"
    export EMAIL=dev@zzsynthuser.example          # the test body is its own subshell: nothing leaks
    run_hook
    [ "$status" -eq 0 ]
}

@test "the address GIT_AUTHOR_EMAIL and GIT_COMMITTER_EMAIL choose is excused, whatever its domain says" {
    # the floor's own identity (git_hermetic exports both variables), with its domain put
    # on the denylist: every control in this file rests on this excuse, so it is pinned
    printf 'example.invalid\n' >> "$STRINGS"
    commit_clean web.txt "under the environment's identity"
    [ "$(git -C "$REPO" log -1 --format='%ae|%ce')" = "tests@example.invalid|tests@example.invalid" ]
    run_hook
    [ "$status" -eq 0 ]
}

@test "each of the two variables chooses on its own: the address one names is excused in either role, the other stamped address is still refused" {
    commit_as dev@zzsynthuser.example dev@TESTHOST.example web.txt "two addresses, neither configured"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    export GIT_AUTHOR_EMAIL=dev@zzsynthuser.example          # GIT_COMMITTER_EMAIL stays the floor's
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is committed as <dev@TESTHOST.example>"* ]]
    [[ "$output" != *"is authored as"* ]]
    export GIT_AUTHOR_EMAIL=tests@example.invalid GIT_COMMITTER_EMAIL=dev@TESTHOST.example
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is authored as <dev@zzsynthuser.example>"* ]]
    [[ "$output" != *"is committed as"* ]]
}

@test "a chosen address is matched whole: one that a stamped address is a prefix of does not excuse it" {
    git -C "$REPO" config user.email dev@zzsynthuser.example.zzsynthnet
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example web.txt "a prefix of the configured address"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is authored as <dev@zzsynthuser.example>"* ]]
}

@test "a chosen address is matched case-insensitively, like a line of content" {
    git -C "$REPO" config user.email DEV@ZZSYNTHUSER.EXAMPLE
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example web.txt "the configured address, in lowercase"
    run_hook
    [ "$status" -eq 0 ]
}

@test "user.email in ANY scope is a chosen address: the global one is read alongside the repository's" {
    # the global config is the floor's own file; a repository-scope address on top of it
    # is the case a --get of the last value would miss
    printf '[user]\n\temail = dev@zzsynthuser.example\n' >> "$GIT_CONFIG_GLOBAL"
    git -C "$REPO" config user.email dev@example.invalid
    [ "$(git -C "$REPO" config --get user.email)" = dev@example.invalid ]
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example web.txt "the global address"
    run_hook
    [ "$status" -eq 0 ]
}

@test "the configured address excuses only itself: a second stamped address is still refused" {
    git -C "$REPO" config user.email dev@zzsynthuser.example
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example ok.txt "the configured address"
    commit_as "$STAMPED" "$STAMPED" web.txt "stamped elsewhere"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is authored as <$STAMPED>"* ]]
    [[ "$output" != *"dev@zzsynthuser.example"* ]]
}

@test "a commit authored cleanly but COMMITTED under the machine's name is refused as committed, not authored" {
    # the shape of a cherry-pick or a rebase on the unconfigured clone: the author kept, the committer stamped
    commit_as tests@example.invalid "$STAMPED" web.txt "rebased here"
    sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${sha:0:10} is committed as <$STAMPED>"* ]]
    [[ "$output" != *"is authored as"* ]]
}

@test "an empty address is nothing to publish" {
    # `<>` is what an explicitly empty identity leaves on a commit; there is no domain in it
    commit_as "" "" web.txt "no address at all"
    [ "$(git -C "$REPO" log -1 --format='%ae|%ce')" = "|" ]
    run_hook
    [ "$status" -eq 0 ]
}

@test "a stamped INTERMEDIATE commit is caught and named when the tip's identity is clean" {
    commit_as "$STAMPED" "$STAMPED" web.txt "stamped"
    stamped_sha="$(git -C "$REPO" rev-parse HEAD)"
    commit_clean api.txt "configured since"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${stamped_sha:0:10} is authored as <$STAMPED>"* ]]
}

@test "a stamped commit a remote already has is not rechecked: only what THIS push publishes counts" {
    # the address is already public through that remote; refusing every later push of the
    # branch would fix nothing (its remedy is a deliberate rewrite and force-push, the author's call)
    commit_as "$STAMPED" "$STAMPED" web.txt "stamped, then pushed"
    git -C "$REPO" update-ref refs/remotes/origin/main HEAD
    commit_clean api.txt "the one commit new to every remote"
    run_hook "$(git -C "$REPO" rev-parse origin/main)"
    [ "$status" -eq 0 ]
}

@test "no denylist file means no address scan (a fresh clone is unaffected)" {
    export ROMP_PRIVATE_STRINGS="$TEST_DIR/does-not-exist.txt"
    commit_as "$STAMPED" "$STAMPED" web.txt "stamped"
    run_hook
    [ "$status" -eq 0 ]
}

@test "a merge commit's addresses are read like any other commit's" {
    # the shape of the pushed merge: a clean tree on both sides, the merge itself stamped
    commit_clean base.txt "base"
    git -C "$REPO" checkout -q -b feature
    commit_clean web.txt "branch work"
    git -C "$REPO" checkout -q main
    commit_clean api.txt "main work"
    git -C "$REPO" checkout -q feature
    GIT_AUTHOR_EMAIL="$STAMPED" GIT_COMMITTER_EMAIL="$STAMPED" git -C "$REPO" merge -q --no-ff -m "merge main" main
    merge_sha="$(git -C "$REPO" rev-parse HEAD)"
    run_hook
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${merge_sha:0:10} is committed as <$STAMPED>"* ]]
}

# ── annotated tags ────────────────────────────────────────────────────────
# A tag object has a tagger of its own, stamped the way a committer is, and every
# other read the hook makes (git grep, rev-list, log) peels the tag to its commit.

@test "an annotated tag's TAGGER is read like a committer: stamped by an unset user.email, the tag is refused naming the tag, the address and its own remedy" {
    commit_clean ok.txt "clean"
    # the tagger is the committer identity of the clone that cut the tag
    GIT_COMMITTER_EMAIL="$STAMPED" git -C "$REPO" tag -a v1 -m "release one"
    sha="$(git -C "$REPO" rev-parse refs/tags/v1)"
    [ "$(git -C "$REPO" cat-file -t "$sha")" = tag ]
    run_hook_tag v1
    [ "$status" -ne 0 ]
    [[ "$output" == *"tag refs/tags/v1 (${sha:0:10}) is tagged as <$STAMPED>, an address this clone is not configured to use"* ]]
    [[ "$output" == *"git tag -f -a <name> <commit>"* ]]
    # the commit the tag names is clean, and is reported as nothing
    [[ "$output" != *"is authored as"* ]]
    [[ "$output" != *"is committed as"* ]]
}

@test "an annotated tag under the configured address passes, and a lightweight tag has no metadata of its own" {
    git -C "$REPO" config user.email dev@zzsynthuser.example
    commit_as dev@zzsynthuser.example dev@zzsynthuser.example ok.txt "the configured address"
    GIT_COMMITTER_EMAIL=dev@zzsynthuser.example git -C "$REPO" tag -a v1 -m "release one"
    run_hook_tag v1
    [ "$status" -eq 0 ]
    git -C "$REPO" tag light
    [ "$(git -C "$REPO" cat-file -t refs/tags/light)" = commit ]
    run_hook_tag light
    [ "$status" -eq 0 ]
}

@test "the commit an annotated tag names is still read through the tag: a clean tagger over a stamped commit is refused naming the commit" {
    commit_as "$STAMPED" "$STAMPED" web.txt "stamped by an unset user.email"
    commit_sha="$(git -C "$REPO" rev-parse HEAD)"
    git -C "$REPO" tag -a v1 -m "release one"     # the hermetic identity: an address the environment chose
    run_hook_tag v1
    [ "$status" -ne 0 ]
    [[ "$output" == *"commit ${commit_sha:0:10} is authored as <$STAMPED>"* ]]
    [[ "$output" != *"is tagged as"* ]]
}

@test "a tag of a tag is peeled one object at a time: a stamped INNER tag under a clean outer one is refused" {
    commit_clean ok.txt "clean"
    GIT_COMMITTER_EMAIL="$STAMPED" git -C "$REPO" tag -a v1 -m "release one"
    git -C "$REPO" tag -a v1-outer refs/tags/v1 -m "the outer tag"     # the hermetic identity
    inner="$(git -C "$REPO" rev-parse refs/tags/v1)"
    [ "$(git -C "$REPO" cat-file -p refs/tags/v1-outer | sed -n 2p)" = "type tag" ]
    run_hook_tag v1-outer
    [ "$status" -ne 0 ]
    [[ "$output" == *"tag refs/tags/v1-outer (${inner:0:10}) is tagged as <$STAMPED>"* ]]
}
