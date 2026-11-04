"""Exercise copied INSTALL.sh with fake uv; never invoke production tooling."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parent / "INSTALL.sh"


@pytest.fixture
def copied_install(tmp_path):
    project = tmp_path / "synthetic-project"
    script = project / "experiments/mvp/batch-d132/INSTALL.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(SOURCE, script)
    binary = tmp_path / "bin"
    binary.mkdir()
    capture = tmp_path / "uv-invocation.txt"
    uv = binary / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$D132_TEST_CAPTURE"\n')
    uv.chmod(0o700)
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
               D132_TEST_CAPTURE=str(capture))

    def run(*args):
        return subprocess.run(["/bin/sh", str(script), *args], cwd=tmp_path,
                              env=env, capture_output=True, text=True, timeout=3)

    return run, capture, project


@pytest.mark.parametrize("flag", [
    "--skip-fast-tests", "--s", "--sk", "--ski", "--skip", "--skip-",
    "--skip-f", "--skip-fa", "--skip-fas", "--skip-fast", "--skip-fast-",
    "--skip-fast-t", "--skip-fast-te", "--skip-fast-tes", "--skip-fast-test",
    "--skip-fast-tests=true",
])
def test_private_skip_flag_and_abbreviations_never_reach_python(copied_install, flag):
    run, capture, _ = copied_install
    result = run("--check-only", flag)
    assert result.returncode == 2
    assert "refuses the private --skip-fast-tests" in result.stderr
    assert not capture.exists()


def test_normal_check_only_reaches_fake_uv_unchanged(copied_install):
    run, capture, project = copied_install
    result = run("--check-only")
    assert result.returncode == 0 and not result.stderr
    argv = capture.read_text().splitlines()
    assert argv == ["run", "--project", str(project), "--offline", "--no-sync", "python",
                    str(project / "experiments/mvp/batch-d132/assembly/batch_tool.py"),
                    "install", "--check-only"]


def test_explicit_scratch_root_remains_supported(copied_install, tmp_path):
    run, capture, _ = copied_install
    root = tmp_path / "another-scratch-root"
    result = run("--root", str(root), "--check-only")
    assert result.returncode == 0
    assert capture.read_text().splitlines()[-3:] == ["--root", str(root), "--check-only"]
