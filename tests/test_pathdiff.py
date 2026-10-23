"""Strict xfails of the top ``pysidtracker.pathdiff`` fixes against the sidtrace oracle.

Each case in ``tests/data/pathdiff.json`` is one representative subtune of a
corpus-wide fix, regenerated with ``python -m pysidtracker.pathdiff xfails``
(``docs/oracle-testing.md``). A case fails with whydiff's explanation until its
fix lands; it then passes, the strict xfail turns the run red, and the case is
pruned with ``xfails --prune``.
"""

from pathlib import Path

import pytest

from pysidtracker.pathdiff import check_case, xfail_params

MANIFEST = Path(__file__).resolve().parent / "data" / "pathdiff.json"

pytestmark = pytest.mark.oracle


@pytest.mark.parametrize("case", xfail_params(MANIFEST))
def test_pathdiff(case):
    check_case(case)
