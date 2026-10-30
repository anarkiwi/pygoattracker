"""The GoatTracker 2 playroutine sources for tests, skipping when they cannot be
downloaded."""

import pytest
from pysidtracker.testing import TuneFetchError

from pygoattracker import gt2sources


def sources() -> dict:
    """:func:`pygoattracker.gt2sources.sources`, or a skip while offline."""
    try:
        return gt2sources.sources()
    except TuneFetchError as exc:
        pytest.skip(f"cannot download: {exc}")
