#!/usr/bin/env python3
"""Install ROMP's pinned CLI separately from the Python SDK's runtime dependency.

The CLI wheel is an official release artifact, not yet published on PyPI. Keep
its complete package layout (including sandbox and code-mode helpers) intact.
"""
import fcntl
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = '0.153.3'
# SHA-256 digests published with OpenAI's rust-v0.153.3 GitHub release.
_WHEELS = {
    ('Linux', 'x86_64'): ('manylinux_2_17_x86_64', '35e8524f7d3ad16afa2fbabfff8730a932b74db09831514ffe7b0181e6169b65'),
    ('Linux', 'aarch64'): ('manylinux_2_17_aarch64', '52649619ca29d09981e4502d054c03358be6e9fdc992f073117157453e69014d'),
    ('Darwin', 'x86_64'): ('macosx_10_9_x86_64', '1cb81fb889433556de776211eea39ab475410f740e66916b02e3196735409535'),
    ('Darwin', 'arm64'): ('macosx_11_0_arm64', 'd8908affb145935f506554d7575178d8b66ff5434d816d533a5ae253c97e6983'),
}


def wheel_url(system=None, machine=None):
    key = (system or platform.system(), machine or platform.machine())
    try:
        tag, digest = _WHEELS[key]
    except KeyError:
        raise RuntimeError('Unsupported Codex runtime platform: %s %s' % key) from None
    name = 'openai_codex_cli_bin-%s-py3-none-%s.whl' % (VERSION, tag)
    return ('https://github.com/openai/codex/releases/download/rust-v%s/%s#sha256=%s'
            % (VERSION, name, digest))


def _package_path(target):
    root = Path(target) / 'codex_cli_bin'
    try:
        metadata = json.loads((root / 'codex-package.json').read_text())
        if metadata.get('version') != VERSION:
            raise ValueError('wrong runtime version')
        files = ['bin/codex', 'bin/codex-code-mode-host', 'codex-path/rg']
        if platform.system() == 'Linux':
            files += ['codex-resources/bwrap', 'codex-resources/zsh/bin/zsh']
        if any(not (root / name).is_file() or not os.access(root / name, os.X_OK)
               for name in files):
            raise ValueError('missing runtime executable or helper')
    except (OSError, ValueError, AttributeError) as e:
        raise RuntimeError('Codex runtime %s is missing or incomplete. '
                           'Run romp-codex-setup, then restart the ROMP kernel.' % VERSION) from e
    return root / 'bin/codex'


def runtime_path(state_dir):
    return _package_path(Path(state_dir) / 'codex-runtime' / VERSION)


def install_runtime(state_dir):
    url = wheel_url()  # Refuse unsupported hosts before creating an installation.
    base = Path(state_dir).resolve() / 'codex-runtime'
    base.mkdir(parents=True, exist_ok=True)
    target = base / VERSION
    # Serialize concurrent setup calls and publish only a validated full package.
    with (base / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _package_path(target)
        except RuntimeError:
            pass
        with tempfile.TemporaryDirectory(prefix='.install-', dir=base) as scratch:
            staging = Path(scratch) / 'runtime'
            subprocess.run([
                sys.executable, '-m', 'pip', '--isolated', 'install', '--quiet',
                '--disable-pip-version-check', '--no-index', '--no-deps',
                '--only-binary=:all:', '--require-hashes', '--target', str(staging), url,
            ], check=True)
            exe = _package_path(staging)
            result = subprocess.run([str(exe), '--version'], check=True,
                                    capture_output=True, text=True, timeout=15)
            if result.stdout.strip() != 'codex-cli ' + VERSION:
                raise RuntimeError('Downloaded Codex runtime has an unexpected version')
            # A corrupt previous installation can be repaired; other versions remain intact.
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
            staging.rename(target)
    return _package_path(target)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: codex_runtime.py STATE_DIR')
    try:
        print(install_runtime(sys.argv[1]))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        sys.exit('romp-codex-setup: %s' % exc)
