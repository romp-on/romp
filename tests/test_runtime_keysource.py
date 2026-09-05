"""Runtime credential source contract, using synthetic secrets and mocked op only.

These tests distinguish inspecting a configured reference from retrieving its value,
and exercise source removal and failure without accessing the developer's credentials.
"""
import os
import stat
import subprocess
import tempfile
import traceback
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
ks = SourceFileLoader(
    "romp_runtime_keysource_test", str(ROOT / "kernel" / "keysource.py")
).load_module()

REF = "op://test-vault/test-item/credential"
OTHER_REF = "op://test-vault/other-item/credential"
BOOT_KEY = "sk-ant-TEST-startup-do-not-reuse"
OLD_KEY = "sk-ant-TEST-provider-old"
NEW_KEY = "sk-ant-TEST-provider-new"
SECRET_STDERR = "TEST-sensitive-provider-stderr"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "service.env"
    monkeypatch.setenv("ROMP_SERVICE_ENV_FILE", str(path))
    monkeypatch.setenv("ROMP_SERVICE_ENV", str(path))
    monkeypatch.delenv("ROMP_API_KEY_REF", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ROMP_SUPERVISED", raising=False)
    ks._CACHE = ((), "")
    ks._AUTHORITATIVE_PATHS.clear()
    ks._ENV_PROVIDER_PATHS.clear()
    # Unexpected retrieval fails locally: no test may invoke the real executable.
    with mock.patch.object(
        ks.subprocess, "run", side_effect=AssertionError("unexpected credential retrieval")
    ) as op:
        yield SimpleNamespace(path=path, root=tmp_path, op=op)
    ks._CACHE = ((), "")
    ks._AUTHORITATIVE_PATHS.clear()
    ks._ENV_PROVIDER_PATHS.clear()


def result(value=OLD_KEY.encode(), returncode=0):
    return subprocess.CompletedProcess(
        ["op", "read", "--no-newline", REF], returncode,
        stdout=value, stderr=SECRET_STDERR.encode(),
    )


def safe_failure(call, *hidden):
    with pytest.raises(ks.KeySourceError) as caught:
        call()
    error = caught.value
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for value in (OLD_KEY, NEW_KEY, BOOT_KEY, SECRET_STDERR, *hidden):
        assert value not in str(error)
        assert value not in repr(error)
        assert value not in rendered
    return error


def test_metadata_and_legacy_reader_never_retrieve_provider(env):
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    source = ks.select_source(BOOT_KEY)

    assert source.kind == "op"
    assert source.configured
    source.validate()
    assert source.fingerprint()
    assert source.fingerprint() == ks.read_source().fingerprint()
    assert source == ks.parse_source(env.path.read_text())
    assert REF not in repr(source)
    assert ks.read_key() == ""
    env.op.assert_not_called()


def test_resolve_executes_literal_reference_with_bounded_noninteractive_io(env):
    # Shell syntax and spaces must stay inside one argument, never be evaluated.
    reference = "op://test vault/item;$(touch SHOULD_NOT_EXIST)/credential field"
    env.op.side_effect = None
    env.op.return_value = result()

    assert ks.KeySource("op", reference).resolve() == OLD_KEY

    args, kwargs = env.op.call_args
    assert args == (["op", "read", "--no-newline", reference],)
    assert not kwargs.get("shell", False)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert 0 < kwargs["timeout"] <= 30
    assert kwargs["check"] is False
    assert not (env.root / "SHOULD_NOT_EXIST").exists()


def test_each_resolution_observes_rotation_without_caching_or_disk_writes(env):
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    source = ks.select_source(BOOT_KEY)
    body = env.path.read_bytes()
    names = sorted(env.root.iterdir())
    env.op.side_effect = [result(OLD_KEY.encode()), result(NEW_KEY.encode())]

    with mock.patch.object(ks, "open", create=True, side_effect=AssertionError("disk access")), \
            mock.patch.object(ks.os, "open", side_effect=AssertionError("disk access")), \
            mock.patch.object(ks.tempfile, "mkstemp", side_effect=AssertionError("disk access")):
        assert source.resolve() == OLD_KEY
        assert source.resolve() == NEW_KEY

    assert env.op.call_count == 2
    assert env.path.read_bytes() == body
    assert sorted(env.root.iterdir()) == names
    assert ks._CACHE[1].value == REF
    assert source.value == REF
    assert all(key not in repr(vars(ks)) for key in (OLD_KEY, NEW_KEY))


@pytest.mark.parametrize("provider_failure", ["nonzero", "missing", "timeout", "oserror"])
def test_provider_failures_are_safe_credential_errors(env, provider_failure):
    env.op.side_effect = None
    if provider_failure == "nonzero":
        env.op.return_value = result(returncode=1)
    elif provider_failure == "missing":
        env.op.side_effect = FileNotFoundError(SECRET_STDERR)
    elif provider_failure == "timeout":
        env.op.side_effect = subprocess.TimeoutExpired(
            ["op", "read", REF], 15, output=OLD_KEY.encode(), stderr=SECRET_STDERR.encode()
        )
    else:
        env.op.side_effect = OSError(SECRET_STDERR)

    safe_failure(ks.KeySource("op", REF).resolve)
    assert env.op.call_count == 1


@pytest.mark.parametrize("value", [
    b"", b"\n", OLD_KEY.encode() + b"\n", OLD_KEY.encode() + b"\nsecond-line",
    OLD_KEY.encode() + b"\x00", OLD_KEY.encode() + b" trailing-space",
    b"\xff\xfe", b"x" * 16385,
])
def test_empty_malformed_or_oversized_provider_output_fails_safely(env, value):
    env.op.side_effect = None
    env.op.return_value = result(value)
    safe_failure(ks.KeySource("op", REF).resolve)


@pytest.mark.parametrize("reference", [
    "", "not-a-reference", "op://", "op://vault/item", "op://vault//field",
    "op://vault/item/", "op://vault/item/section/field/extra",
    "op://vault/item/field\nPRIVATE_REF_MARKER", "op://vault/item/field\0PRIVATE_REF_MARKER",
])
def test_invalid_reference_never_runs_op_and_does_not_echo_input(env, reference):
    source = ks.KeySource("op", reference)
    assert source.configured  # Invalid explicit configuration cannot become login mode.
    safe_failure(source.resolve, "PRIVATE_REF_MARKER")
    env.op.assert_not_called()


@pytest.mark.parametrize("body", [
    f"ANTHROPIC_API_KEY={BOOT_KEY}\nROMP_API_KEY_REF={REF}\n",
    f"ROMP_API_KEY_REF={REF}\nANTHROPIC_API_KEY={BOOT_KEY}\n",
])
def test_file_provider_wins_both_file_and_ambient_legacy_key(env, monkeypatch, body):
    monkeypatch.setenv("ANTHROPIC_API_KEY", BOOT_KEY)
    env.path.write_text(body)
    source = ks.select_source(BOOT_KEY)
    assert (source.kind, source.value) == ("op", REF)
    env.op.assert_not_called()


def test_foreground_provider_wins_ambient_legacy_key(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    monkeypatch.setenv("ANTHROPIC_API_KEY", BOOT_KEY)
    source = ks.select_source(BOOT_KEY)
    assert (source.kind, source.value) == ("op", REF)
    env.op.assert_not_called()


@pytest.mark.parametrize("startup_key", ["", BOOT_KEY])
def test_removing_foreground_provider_cannot_become_login_or_ambient_key(env, monkeypatch, startup_key):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    assert ks.select_source(startup_key).kind == "op"
    # Remembering selection must not block subsequent reads while it remains configured.
    assert ks.select_source(startup_key).value == REF
    monkeypatch.delenv("ROMP_API_KEY_REF")

    source = ks.select_source(startup_key)
    assert source.configured
    assert source.kind == "error"
    safe_failure(source.resolve)
    env.op.assert_not_called()


def test_empty_foreground_provider_stays_explicit_until_reconfigured(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    assert ks.select_source(BOOT_KEY).kind == "op"
    monkeypatch.setenv("ROMP_API_KEY_REF", "")
    assert ks.select_source(BOOT_KEY).configured
    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())
    monkeypatch.setenv("ROMP_API_KEY_REF", OTHER_REF)
    assert ks.select_source(BOOT_KEY).value == OTHER_REF
    env.op.assert_not_called()


def test_restoring_foreground_reference_recovers_from_removal(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    assert ks.select_source(BOOT_KEY).value == REF
    monkeypatch.delenv("ROMP_API_KEY_REF")
    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())
    monkeypatch.setenv("ROMP_API_KEY_REF", OTHER_REF)

    assert ks.select_source(BOOT_KEY).value == OTHER_REF
    env.op.assert_not_called()


def test_foreground_provider_authority_is_isolated_by_service_file_path(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    assert ks.select_source(BOOT_KEY).kind == "op"
    monkeypatch.delenv("ROMP_API_KEY_REF")
    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())

    other_path = env.root / "other-service.env"
    monkeypatch.setenv("ROMP_SERVICE_ENV_FILE", str(other_path))
    assert ks.select_source(BOOT_KEY).resolve() == BOOT_KEY
    monkeypatch.setenv("ROMP_SERVICE_ENV_FILE", str(env.path))
    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())
    env.op.assert_not_called()


def test_explicit_file_key_can_replace_a_removed_foreground_provider(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    assert ks.select_source(BOOT_KEY).kind == "op"
    monkeypatch.delenv("ROMP_API_KEY_REF")
    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())

    ks.write_source(ks.KeySource("file", NEW_KEY))
    assert ks.select_source(BOOT_KEY).resolve() == NEW_KEY
    env.op.assert_not_called()


def test_explicit_file_source_takes_priority_over_foreground_provider(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", OTHER_REF)
    env.path.write_text(f"ANTHROPIC_API_KEY={OLD_KEY}\n")
    source = ks.select_source(BOOT_KEY)
    assert (source.kind, source.resolve()) == ("file", OLD_KEY)
    env.op.assert_not_called()


def test_removing_selected_file_provider_cannot_restore_foreground_provider(env, monkeypatch):
    monkeypatch.setenv("ROMP_API_KEY_REF", OTHER_REF)
    assert ks.select_source(BOOT_KEY).value == OTHER_REF
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    assert ks.select_source(BOOT_KEY).value == REF
    env.path.unlink()

    source = ks.select_source(BOOT_KEY)
    assert source.configured
    safe_failure(source.resolve)
    env.op.assert_not_called()


@pytest.mark.parametrize("credential", ["raw", "provider"])
@pytest.mark.parametrize("edit", ["remove-file", "empty-file", "remove-assignment"])
def test_supervised_kernel_restart_cannot_restore_removed_source(env, monkeypatch, credential, edit):
    monkeypatch.setenv("ROMP_SUPERVISED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", BOOT_KEY)
    if credential == "provider":
        monkeypatch.setenv("ROMP_API_KEY_REF", REF)
        env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
        assert ks.select_source(BOOT_KEY).value == REF
    else:
        env.path.write_text(f"ANTHROPIC_API_KEY={OLD_KEY}\n")
        assert ks.select_source(BOOT_KEY).value == OLD_KEY
    if edit == "remove-file":
        env.path.unlink()
    elif edit == "empty-file":
        env.path.write_text("")
    else:
        env.path.write_text("ROMP_PERF=1\n")

    # A fresh kernel under the still-running manager receives its original environment,
    # but none of the previous kernel's authority maps or discarded startup-key state.
    restarted = SourceFileLoader(
        "romp_runtime_keysource_restarted_test", str(ROOT / "kernel" / "keysource.py")
    ).load_module()
    assert not restarted._AUTHORITATIVE_PATHS
    assert not restarted._ENV_PROVIDER_PATHS
    source = restarted.select_source(BOOT_KEY)
    if credential == "provider":
        assert source.kind == "error"
        assert source.configured
        with pytest.raises(restarted.KeySourceError) as caught:
            source.resolve()
        assert REF not in str(caught.value)
        assert BOOT_KEY not in str(caught.value)
        monkeypatch.delenv("ROMP_API_KEY_REF")
        assert restarted.select_source("") == source
    else:
        assert source.kind == "file"
        assert not source.configured
        assert source.resolve() == ""
        assert restarted.select_source("") == source
    env.op.assert_not_called()


@pytest.mark.parametrize("credential", ["raw", "provider"])
def test_foreground_fresh_kernel_keeps_explicit_environment_auth(env, monkeypatch, credential):
    monkeypatch.setenv("ANTHROPIC_API_KEY", BOOT_KEY)
    if credential == "provider":
        monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    restarted = SourceFileLoader(
        "romp_runtime_keysource_foreground_restart_test", str(ROOT / "kernel" / "keysource.py")
    ).load_module()
    source = restarted.select_source(BOOT_KEY)
    if credential == "provider":
        assert (source.kind, source.value) == ("op", REF)
    else:
        assert (source.kind, source.resolve()) == ("environment", BOOT_KEY)
    env.op.assert_not_called()


def test_supervised_service_without_key_source_still_allows_login(env, monkeypatch):
    monkeypatch.setenv("ROMP_SUPERVISED", "1")
    source = ks.select_source()
    assert not source.configured
    assert source.resolve() == ""
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    assert ks.select_source().value == REF
    env.op.assert_not_called()


def test_provider_failure_does_not_fall_back_to_competing_keys(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", BOOT_KEY)
    env.path.write_text(f"ANTHROPIC_API_KEY={BOOT_KEY}\nROMP_API_KEY_REF={REF}\n")
    env.op.side_effect = None
    env.op.return_value = result(returncode=1)

    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())
    assert ks.select_source(BOOT_KEY).kind == "op"
    assert env.op.call_count == 1


def test_provider_failure_after_success_cannot_reuse_previous_resolved_key(env):
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    env.op.side_effect = [result(), result(returncode=1)]
    assert ks.select_source(BOOT_KEY).resolve() == OLD_KEY

    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())
    assert env.op.call_count == 2


@pytest.mark.parametrize("edit", ["remove-file", "remove-assignment", "empty-reference"])
def test_removing_or_emptying_selected_provider_cannot_revive_startup_key(env, edit):
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    assert ks.select_source(BOOT_KEY).kind == "op"
    if edit == "remove-file":
        env.path.unlink()
    elif edit == "remove-assignment":
        env.path.write_text("ROMP_PERF=1\n")
    else:
        env.path.write_text(f"ROMP_API_KEY_REF=\nANTHROPIC_API_KEY={BOOT_KEY}\n")

    selected = ks.select_source(BOOT_KEY)
    assert selected.configured
    safe_failure(selected.resolve)
    env.op.assert_not_called()


@pytest.mark.parametrize("edit", ["remove-file", "remove-assignment", "empty-key"])
def test_removing_or_emptying_legacy_source_cannot_revive_startup_key(env, edit):
    env.path.write_text(f"ANTHROPIC_API_KEY={OLD_KEY}\n")
    assert ks.select_source(BOOT_KEY).resolve() == OLD_KEY
    if edit == "remove-file":
        env.path.unlink()
    elif edit == "remove-assignment":
        env.path.write_text("ROMP_PERF=1\n")
    else:
        env.path.write_text("ANTHROPIC_API_KEY=\n")

    assert ks.select_source(BOOT_KEY).resolve() == ""
    env.op.assert_not_called()


@pytest.mark.parametrize("file_source", [f"ROMP_API_KEY_REF={REF}", f"ANTHROPIC_API_KEY={OLD_KEY}"])
@pytest.mark.parametrize("failure_at", ["stat", "open"])
def test_unreadable_previously_selected_source_cannot_reuse_cached_value(env, file_source, failure_at):
    env.path.write_text(file_source + "\n")
    assert ks.select_source(BOOT_KEY).configured
    if failure_at == "stat":
        context = mock.patch.object(ks.os, "stat", side_effect=PermissionError(SECRET_STDERR))
    else:
        # Permission-only changes must invalidate the cache even if bytes/mtime are unchanged.
        env.path.chmod(0o000)
        context = mock.patch.object(ks, "open", create=True, side_effect=PermissionError(SECRET_STDERR))
    with context:
        selected = ks.select_source(BOOT_KEY)
    assert selected.kind == "error"
    assert selected.configured
    safe_failure(selected.resolve)
    env.op.assert_not_called()


def test_explicit_new_source_recovers_from_removed_provider(env):
    env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    assert ks.select_source(BOOT_KEY).kind == "op"
    env.path.unlink()
    safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())

    ks.write_source(ks.KeySource("op", OTHER_REF))
    assert ks.select_source(BOOT_KEY).value == OTHER_REF
    ks.write_source(ks.KeySource("file", NEW_KEY))
    assert ks.select_source(BOOT_KEY).resolve() == NEW_KEY
    env.op.assert_not_called()


def test_atomic_reference_selection_removes_competing_keys_without_resolving(env):
    original = (
        f"# keep this comment\nANTHROPIC_API_KEY={OLD_KEY}\nROMP_PERF=1\n"
        f"ROMP_API_KEY_REF={OTHER_REF}\nANTHROPIC_API_KEY={BOOT_KEY}\n"
    )
    env.path.write_text(original)
    env.path.chmod(0o644)
    real_replace = os.replace
    replacements = []

    def observe_replace(source_path, destination_path):
        source_path = Path(source_path)
        assert Path(destination_path) == env.path
        assert source_path.parent == env.path.parent
        assert env.path.read_text() == original  # Original survives until the atomic replace.
        assert stat.S_IMODE(source_path.stat().st_mode) == 0o600
        pending = source_path.read_text()
        assert "ANTHROPIC_API_KEY=" not in pending
        assert pending.count("ROMP_API_KEY_REF=") == 1
        assert f"ROMP_API_KEY_REF={REF}" in pending
        replacements.append(source_path)
        real_replace(source_path, destination_path)

    with mock.patch.object(ks.os, "replace", side_effect=observe_replace):
        ks.write_source(ks.KeySource("op", REF))

    assert len(replacements) == 1
    assert stat.S_IMODE(env.path.stat().st_mode) == 0o600
    assert env.path.read_text() == f"# keep this comment\nROMP_PERF=1\nROMP_API_KEY_REF={REF}\n"
    assert sorted(env.root.iterdir()) == [env.path]
    assert ks.select_source(BOOT_KEY).value == REF
    env.op.assert_not_called()


def test_failed_atomic_selection_preserves_old_configuration_and_cleans_temp_file(env):
    original = f"ANTHROPIC_API_KEY={OLD_KEY}\nROMP_PERF=1\n"
    env.path.write_text(original)
    with mock.patch.object(ks.os, "replace", side_effect=OSError("synthetic rename failure")):
        with pytest.raises(OSError, match="synthetic rename failure"):
            ks.write_source(ks.KeySource("op", REF))
    assert env.path.read_text() == original
    assert sorted(env.root.iterdir()) == [env.path]
    env.op.assert_not_called()


@pytest.mark.parametrize("consumer,attribute", [
    ("kernel/sdk_backend.py", "_keysrc"), ("cli/keyswap.py", "ks"),
])
@pytest.mark.parametrize("origin", ["file", "environment"])
def test_loading_a_consumer_preserves_prior_provider_removal(env, monkeypatch, consumer, attribute, origin):
    import sys
    if origin == "file":
        env.path.write_text(f"ROMP_API_KEY_REF={REF}\n")
    else:
        monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    assert ks.select_source(BOOT_KEY).kind == "op"
    if origin == "file":
        env.path.unlink()
    else:
        monkeypatch.delenv("ROMP_API_KEY_REF")

    # The judge may select a provider before the SDK is loaded lazily. Loading a consumer must
    # share that module, including its remembered authority, rather than re-execute and reset it.
    monkeypatch.setitem(sys.modules, "romp_keysource", ks)
    module_name = "romp_lazy_source_consumer_test"
    try:
        loaded = SourceFileLoader(module_name, str(ROOT / consumer)).load_module()
        assert getattr(loaded, attribute) is ks
        assert ks.select_source(BOOT_KEY).kind == "error"
        safe_failure(lambda: ks.select_source(BOOT_KEY).resolve())
        env.op.assert_not_called()
    finally:
        sys.modules.pop(module_name, None)


def test_op_credentials_are_claimed_out_of_the_environment_and_reach_only_the_op_subprocess(env, monkeypatch):
    """op's own credential (a service-account token, a signed-in session token) must reach the kernel for
    headless use — and no child of the kernel: claim_op_env() takes the names out of os.environ once, and
    resolve() gives them back to the `op read` subprocess alone (review find, 2026-09-05: they were inherited
    by every Claude session and every judge call, a vault-wide credential in every agent's shell)."""
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "synthetic-op-token")
    monkeypatch.setenv("OP_SESSION_my_account", "synthetic-op-session")
    monkeypatch.setenv("OP_ACCOUNT", "my.1password.example")
    monkeypatch.setenv("UNRELATED_VAR", "kept")
    ks._OP_ENV.clear(); ks._OP_CLAIM_SAID = False
    try:
        # no reference anywhere: romp is not the op consumer (a session-side helper may be) → hands off
        assert ks.claim_op_env() == {} and "OP_SERVICE_ACCOUNT_TOKEN" in os.environ
        assert ks.strip_op_env({"OP_SERVICE_ACCOUNT_TOKEN": "x", "A": "1"}) == {"OP_SERVICE_ACCOUNT_TOKEN": "x", "A": "1"}
        monkeypatch.setenv("ROMP_API_KEY_REF", REF)                  # now romp runs op itself
        claimed = ks.claim_op_env()
        assert set(claimed) == {"OP_SERVICE_ACCOUNT_TOKEN", "OP_SESSION_my_account", "OP_ACCOUNT"}
        for name in claimed:
            assert name not in os.environ, name
        assert os.environ["UNRELATED_VAR"] == "kept"
        # a value that appears later is claimed too, and the stash is idempotent
        monkeypatch.setenv("OP_CONNECT_TOKEN", "synthetic-connect")
        assert "OP_CONNECT_TOKEN" in ks.claim_op_env() and "OP_CONNECT_TOKEN" not in os.environ
        env.op.side_effect = None
        env.op.return_value = result(NEW_KEY.encode())
        assert ks.KeySource("op", REF).resolve() == NEW_KEY
        sub_env = env.op.call_args.kwargs["env"]
        assert sub_env["OP_SERVICE_ACCOUNT_TOKEN"] == "synthetic-op-token"
        assert sub_env["OP_SESSION_my_account"] == "synthetic-op-session"
        assert sub_env["UNRELATED_VAR"] == "kept", "op still sees the ordinary environment (PATH, HOME, config)"
        assert "OP_SERVICE_ACCOUNT_TOKEN" not in os.environ, "resolving hands nothing back to the process"
        # a child env built before the claim is scrubbed the same way
        child = {"OP_SERVICE_ACCOUNT_TOKEN": "x", "OP_SESSION_acct": "y", "PATH": "/bin", "OPTIONAL": "keep"}
        assert ks.strip_op_env(child) == {"PATH": "/bin", "OPTIONAL": "keep"}
        assert ks.is_op_env_name("OP_SESSION_anything") and not ks.is_op_env_name("OPTIONAL")
    finally:
        ks._OP_ENV.clear()


def test_a_stray_byte_in_an_unrelated_line_does_not_turn_the_source_into_an_error(env):
    env.path.write_bytes(b"# caf\xe9 note\nROMP_API_KEY_REF=" + REF.encode() + b"\n")
    assert ks.read_source(str(env.path)) == ks.KeySource("op", REF)


def test_the_claim_says_which_names_it_took_once_and_never_a_value(env, monkeypatch, capsys):
    monkeypatch.setenv("ROMP_API_KEY_REF", REF)
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "synthetic-op-token-value")
    ks._OP_ENV.clear(); ks._OP_CLAIM_SAID = False
    try:
        ks.claim_op_env(); ks.claim_op_env()
        err = capsys.readouterr().err
        assert err.count("op credentials claimed") == 1
        assert "OP_SERVICE_ACCOUNT_TOKEN" in err and "synthetic-op-token-value" not in err
    finally:
        ks._OP_ENV.clear(); ks._OP_CLAIM_SAID = False


def test_one_reserved_names_rule_for_the_doors_the_launch_and_the_fork():
    op = ks.KeySource("op", REF); err = ks.KeySource("error", error="x"); plain = ks.KeySource("file", "k")
    all3 = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
    assert ks.runtime_reserved_names("key", op) == all3
    assert ks.runtime_reserved_names("", op) == all3, "no pick with a configured source launches keyed"
    assert ks.runtime_reserved_names("login", op) == ("ANTHROPIC_API_KEY",), "a login session keeps its own token"
    assert ks.runtime_reserved_names("login", err) == ("ANTHROPIC_API_KEY",)
    assert ks.runtime_reserved_names("key", plain) == () and ks.runtime_reserved_names("", None) == ()


def test_a_garbled_key_line_is_an_error_while_a_garbled_comment_is_not(env):
    env.path.write_bytes(b"ANTHROPIC_API_KEY=sk-ant-TEST-\xff\xfe-garbled\n")
    src = ks.read_source(str(env.path))
    assert src.kind == "error" and "not valid UTF-8" in src.error and "ANTHROPIC_API_KEY" in src.error
    env.path.write_bytes(b"# caf\xe9\nROMP_API_KEY_REF=" + REF.encode() + b"\n")
    ks._CACHE = ((), "")
    assert ks.read_source(str(env.path)) == ks.KeySource("op", REF)


def test_tmux_server_scrub_runs_one_unset_per_credential_name(env):
    with mock.patch.object(ks.subprocess, "run") as run:
        ran = ks.tmux_unset_global(["OP_SERVICE_ACCOUNT_TOKEN", "OP_SESSION_acct", "PATH", "OP_ACCOUNT"], socket="probe")
    assert ran == [["tmux", "-L", "probe", "set-environment", "-gu", "OP_ACCOUNT"],
                   ["tmux", "-L", "probe", "set-environment", "-gu", "OP_SERVICE_ACCOUNT_TOKEN"],
                   ["tmux", "-L", "probe", "set-environment", "-gu", "OP_SESSION_acct"]]
    assert run.call_count == 3
    with mock.patch.object(ks.subprocess, "run", side_effect=FileNotFoundError):
        assert ks.tmux_unset_global(["OP_ACCOUNT"]) == [], "no tmux: nothing to do, nothing raised"
