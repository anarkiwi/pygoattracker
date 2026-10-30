"""The GoatTracker 2 spec proved against real tunes' own playroutines.

Marked ``oracle`` (HVSC tunes and the GoatTracker 2 release archives are downloaded,
never committed): the spec, bound to the playroutine source matched in each tune's
code, leaves memory equal to the tune's own 6502 after every call of the whole song,
and its row reader is translation-validated against the tune's ``mt_getnewnote``.
"""

import sys
from pathlib import Path

import pytest

from pysidtracker import TuneFetchError, resolve_tune
from pysidtracker.playspec import tv

from pygoattracker import gt2spec
from tests._gt2inplace import InPlace
from tests.test_oracle_hvsc import TUNES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import spec_memcheck  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.oracle

MEMORY = (
    "hammurabi",
    "a_crack_in_the_facade",
    "10_orbyte",
    "cheese_wings",
    "puzzle_bobble",
    "metroid_brinstar",
    "dirty_cheater",
    "two_channels",
    "stereo",
    "double_call",
)


def _tune(name: str) -> tuple:
    relpath, song = TUNES[name]
    path = resolve_tune(relpath)
    if path is None:
        raise TuneFetchError(f"tune {relpath} unavailable (offline, not cached)")
    return Path(path).read_bytes(), song


@pytest.mark.parametrize("name", MEMORY)
def test_spec_memory_equals_the_tunes_own_6502_every_call(name):
    data, song = _tune(name)
    assert spec_memcheck.check(data, song)["frame"] == "ok"


def test_row_reader_is_translation_valid():
    data, song = _tune("hammurabi")
    player = InPlace(data, song - 1)
    view = player.view()
    found = player.found(view)
    result = tv.validate(
        gt2spec.spec(),
        player.binding(view, found["mt_play"]),
        "chan[0].getnewnote",
        view,
        found["mt_getnewnote"],
        registers={"X": 0},
        stop=found["mt_loadregs"],
    )
    assert result.proven, result.counterexample
