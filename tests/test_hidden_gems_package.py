"""Build and inspect the installable Hidden Gems wheel without network access."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _build_wheel(tmp_path: Path) -> Path:
    output = tmp_path / "wheelhouse"
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(output.glob("hiddengems-0.1.0-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_wheel_contains_provider_package_and_declared_dependencies(tmp_path):
    wheel = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))

    assert "hiddengems/gem_provider.py" in names
    assert "hiddengems/gems/dotenv_provider.py" in names
    assert "hiddengems/gems/k8s_provider.py" in names
    assert "hiddengems/gems/keyring_provider.py" in names
    assert "hiddengems/gems/onepassword_provider.py" in names
    requirements = metadata.get_all("Requires-Dist") or []
    assert any(requirement.startswith("python-dotenv") for requirement in requirements)
    assert any(requirement.startswith("kubernetes") for requirement in requirements)
    assert any(requirement.startswith("PyYAML") for requirement in requirements)
    if platform.system() == "Darwin":
        assert "hiddengems/gems/libkeychainorpasswordread.dylib" in names
    else:
        assert "hiddengems/gems/libkeychainorpasswordread.dylib" not in names


def test_wheel_installs_and_imports_without_source_tree(tmp_path):
    wheel = _build_wheel(tmp_path)
    target = tmp_path / "site-packages"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-index",
            "--target",
            str(target),
            str(wheel),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    script = """
import sys

sys.path.insert(0, sys.argv[1])

from hiddengems.gem_provider import GemProvider
from hiddengems.gems.keyring_provider import KeyringProvider
from dotenv import dotenv_values
import platform

assert GemProvider.provider_types
assert callable(dotenv_values)
records = KeyringProvider.detect()
if platform.system() == "Darwin":
    assert len(records) == 1
else:
    assert records == ()
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script, str(target)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
