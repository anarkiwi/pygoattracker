"""Run black and pylint as part of the test suite.

The CI lint steps run the same commands, so a green local ``pytest``
implies a green CI lint.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LINT_TARGETS = ["src/pygoattracker", "tests"]


def run_tool(*argv) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(not shutil.which("black"), reason="black not installed")
def test_black_clean():
    result = run_tool("black", "--check", *LINT_TARGETS)
    assert (
        result.returncode == 0
    ), f"black --check failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(not shutil.which("pylint"), reason="pylint not installed")
def test_pylint_clean():
    result = run_tool("pylint", *LINT_TARGETS)
    assert result.returncode == 0, f"pylint failed:\n{result.stdout}\n{result.stderr}"
