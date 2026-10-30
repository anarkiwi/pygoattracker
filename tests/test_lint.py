"""The package passes pysidtracker's shared lint: no dead symbols, no unresolved
doc references, and every render comparison in the tests covers a whole song."""

from pathlib import Path

from pysidtracker.lint import check_project

ROOT = Path(__file__).resolve().parent.parent


def test_lint(monkeypatch):
    monkeypatch.chdir(ROOT)
    assert check_project("src/pygoattracker", tests=("tests",), docs=("docs",)) == []
