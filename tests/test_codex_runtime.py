"""The managed runtime is independent of PATH and the SDK's older dependency."""
import json
import os
import subprocess
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)

HERE = Path(__file__).resolve().parents[1]
rt = SourceFileLoader('test_romp_codex_runtime', str(HERE / 'kernel/codex_runtime.py')).load_module()


def package(target, version=None):
    root = target / 'codex_cli_bin'
    for name in ('bin/codex', 'bin/codex-code-mode-host', 'codex-path/rg',
                 'codex-resources/bwrap', 'codex-resources/zsh/bin/zsh'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic executable')
        path.chmod(0o755)
    (root / 'codex-package.json').write_text(json.dumps({'version': version or rt.VERSION}))
    return root / 'bin/codex'


class ManagedRuntime(unittest.TestCase):
    def test_all_supported_platforms_have_pinned_official_wheels(self):
        for system, machine in [('Linux', 'x86_64'), ('Linux', 'aarch64'),
                                ('Darwin', 'x86_64'), ('Darwin', 'arm64')]:
            with self.subTest(system=system, machine=machine):
                url = rt.wheel_url(system, machine)
                self.assertTrue(url.startswith('https://github.com/openai/codex/releases/download/rust-v0.153.3/'))
                self.assertRegex(url, r'\.whl#sha256=[0-9a-f]{64}$')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
            rt.wheel_url('Linux', 'riscv64')

    def test_missing_runtime_fails_with_setup_hint(self):
        with tempfile.TemporaryDirectory() as state:
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)

    def test_incomplete_or_wrong_version_is_not_usable(self):
        with tempfile.TemporaryDirectory() as state:
            target = Path(state) / 'codex-runtime' / rt.VERSION
            exe = package(target, '0.144.4')
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)
            package(target)
            self.assertEqual(rt.runtime_path(state), exe)
            (target / 'codex_cli_bin/codex-path/rg').unlink()
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                rt.runtime_path(state)

    def test_install_stages_verified_wheel_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='romp runtime ') as state:
            def run(args, **kwargs):
                if '--target' in args:
                    target = Path(args[args.index('--target') + 1])
                    self.assertNotEqual(target, Path(state) / 'codex-runtime' / rt.VERSION)
                    package(target)
                    self.assertIn('--require-hashes', args)
                    self.assertIn('--no-deps', args)
                    self.assertIn('--no-index', args)
                    self.assertIn('--only-binary=:all:', args)
                    self.assertIn('#sha256=', args[-1])
                return subprocess.CompletedProcess(args, 0, 'codex-cli ' + rt.VERSION + '\n')
            with mock.patch.object(rt.subprocess, 'run', side_effect=run) as install:
                exe = rt.install_runtime(state)
                self.assertEqual(exe, rt.runtime_path(state))
                calls = install.call_count
                self.assertEqual(rt.install_runtime(state), exe)
                self.assertEqual(install.call_count, calls)

    def test_failed_install_never_publishes_partial_runtime(self):
        with tempfile.TemporaryDirectory() as state:
            old = package(Path(state) / 'codex-runtime/0.144.4', '0.144.4')
            with mock.patch.object(rt.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pip')):
                with self.assertRaises(subprocess.CalledProcessError):
                    rt.install_runtime(state)
            self.assertTrue(old.exists())
            self.assertFalse((Path(state) / 'codex-runtime' / rt.VERSION).exists())

    def test_setup_repairs_an_incomplete_existing_target(self):
        for kind in ('file', 'directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as state:
                target = Path(state) / 'codex-runtime' / rt.VERSION
                target.parent.mkdir(parents=True)
                if kind == 'file':
                    target.write_text('incomplete installation')
                else:
                    target.mkdir()
                def run(args, **kwargs):
                    if '--target' in args:
                        package(Path(args[args.index('--target') + 1]))
                    return subprocess.CompletedProcess(args, 0, 'codex-cli ' + rt.VERSION + '\n')
                with mock.patch.object(rt.subprocess, 'run', side_effect=run):
                    self.assertEqual(rt.install_runtime(state), rt.runtime_path(state))

    def test_wrong_executable_version_is_not_published(self):
        with tempfile.TemporaryDirectory() as state:
            def run(args, **kwargs):
                if '--target' in args:
                    package(Path(args[args.index('--target') + 1]))
                return subprocess.CompletedProcess(args, 0, 'codex-cli 0.144.4\n')
            with mock.patch.object(rt.subprocess, 'run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'version'):
                    rt.install_runtime(state)
            self.assertFalse((Path(state) / 'codex-runtime' / rt.VERSION).exists())


class BackendRuntimeSelection(unittest.TestCase):
    def test_managed_executable_and_helpers_win_over_path(self):
        cb = SourceFileLoader('runtime_selection_backend', str(HERE / 'kernel/codex_backend.py')).load_module()
        with tempfile.TemporaryDirectory() as state:
            exe = package(Path(state) / 'codex-runtime' / rt.VERSION)
            with mock.patch.dict('os.environ', {'PATH': '/TESTBIN'}):
                config = cb._codex_config(lambda **kwargs: kwargs, None, state)
            self.assertEqual(config['codex_bin'], str(exe))
            self.assertEqual(config['env']['PATH'], str(exe.parent.parent / 'codex-path') + ':/TESTBIN')
            profile = config['config_overrides'][0]
            for path in (exe, exe.parent / 'codex-code-mode-host',
                         exe.parent.parent / 'codex-package.json',
                         exe.parent.parent / 'codex-resources', exe.parent.parent / 'codex-path'):
                self.assertIn(json.dumps(str(path)) + ' = "read"', profile)
            self.assertNotIn(json.dumps(state) + ' = "read"', profile)
            self.assertNotIn(json.dumps(str(exe.parent.parent)) + ' = "read"', profile)
            self.assertNotIn('":root"', profile)

    def test_missing_runtime_does_not_fall_back_to_sdk_or_path(self):
        cb = SourceFileLoader('runtime_missing_backend', str(HERE / 'kernel/codex_backend.py')).load_module()
        with tempfile.TemporaryDirectory() as state:
            config = mock.Mock()
            with self.assertRaisesRegex(RuntimeError, 'romp-codex-setup'):
                cb._codex_config(config, None, state)
            config.assert_not_called()

    def test_explicit_packaged_runtime_keeps_its_matching_helpers(self):
        cb = SourceFileLoader('runtime_override_backend', str(HERE / 'kernel/codex_backend.py')).load_module()
        with tempfile.TemporaryDirectory() as state:
            exe = package(Path(state) / 'custom')
            config = cb._codex_config(lambda **kwargs: kwargs, str(exe))
            self.assertEqual(config['codex_bin'], str(exe))
            self.assertTrue(config['env']['PATH'].startswith(str(exe.parent.parent / 'codex-path')))

    def test_bare_executable_does_not_grant_reads_to_its_parent(self):
        cb = SourceFileLoader('runtime_bare_backend', str(HERE / 'kernel/codex_backend.py')).load_module()
        with tempfile.TemporaryDirectory(prefix='romp runtime ') as state:
            exe = Path(state) / 'codex'
            exe.touch()
            (Path(state) / 'private.json').write_text('synthetic private fixture')
            profile = cb._codex_config(lambda **kwargs: kwargs, str(exe))['config_overrides'][0]
            self.assertIn(json.dumps(str(exe)) + ' = "read"', profile)
            self.assertNotIn(json.dumps(state) + ' = "read"', profile)
            self.assertNotIn('private.json', profile)
