"""The playspecs equal their 6502 playroutines on every reachable branch direction.

Marked ``oracle`` (playroutine code is built at test time from cached archives and
HVSC tunes): each build's proof songs leave the spec's memory equal to the 6502's
and run every branch direction but those :func:`tests._coverage.unreachable` names.
"""

import sys
from pathlib import Path

import pytest

from tests import _coverage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import spec_coverage  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.oracle

BUILDS = [b.name for b in _coverage.builds()]


@pytest.mark.parametrize("name", BUILDS)
def test_proof_songs_run_every_reachable_branch_direction(name):
    build = _coverage.builds([name])[0]
    cov = spec_coverage.merged(map(spec_coverage.run, _coverage.cases(build)))[1]
    found = spec_coverage.gaps(build.view(), cov, build.span(), build.entries())
    assert spec_coverage.keys(found, build.labels()) == set(_coverage.unreachable(name))


@pytest.mark.parametrize("name", BUILDS)
def test_spec_memory_equals_the_6502_on_every_proof_song(name):
    cases = _coverage.cases(_coverage.builds([name])[0])
    results = [spec_coverage.run(case)[0] for case in cases]
    assert [r for r in results if r["frame"] != "ok"] == []
