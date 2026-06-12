"""Enforce the coverage floor from within the test suite.

Runs the suite (minus this module and the lint gate) in a subprocess
with coverage measurement and fails unless coverage stays above 85%.
CI's top-level ``pytest --cov`` run enforces the same floor; this test
makes a plain local ``pytest`` run enforce it too.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COVERAGE_FLOOR = 85


@pytest.mark.skipif(
    importlib.util.find_spec("pytest_cov") is None,
    reason="pytest-cov not installed",
)
def test_coverage_floor():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "-p",
            "no:cacheprovider",
            "--ignore=tests/test_coverage.py",
            "--ignore=tests/test_lint.py",
            "--cov=pygoattracker",
            "--cov-report=term",
            f"--cov-fail-under={COVERAGE_FLOOR}",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "COVERAGE_FILE": str(REPO_ROOT / ".coverage.floor"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"coverage below {COVERAGE_FLOOR}% (or tests failed):\n"
        f"{result.stdout}\n{result.stderr}"
    )
