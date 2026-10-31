"""The GoatTracker 2 spec proved against real tunes' own playroutines.

Marked ``oracle`` (HVSC tunes and the GoatTracker 2 release archives are downloaded,
never committed): the spec, bound to the playroutine source matched in each tune's
code, leaves memory equal to the tune's own 6502 after every call of the whole song,
and its row reader is translation-validated against the tune's ``mt_getnewnote``.
``SidFormat.spec_player`` plays the same tunes' own images, with their note tables or
the standard one.
"""

import sys
from pathlib import Path

import pytest

from pysidtracker import TuneFetchError, resolve_tune
from pysidtracker.irqhost import TunePlayer
from pysidtracker.testing import song_frames
from pysidtracker.notes import frequency
from pysidtracker.playspec import tv

from pygoattracker import gt2spec
from pygoattracker.formats import GOATTRACKER2
from pygoattracker.sid import decompile_sid
from pygoattracker.typed import speed_bytes
from tests._gt2inplace import InPlace
from tests.test_oracle_hvsc import TUNES

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import spec_memcheck  # noqa: E402  pylint: disable=wrong-import-position

pytestmark = pytest.mark.oracle

SUBTUNES = TUNES | {
    "cruiser_x_79_1": ("MUSICIANS/B/Bayliss_Richard/Cruiser-X_79.sid", 1),
    "captain_ishtar_1": ("MUSICIANS/B/Bayliss_Richard/Captain_Ishtar.sid", 1),
    "ninja_carnage_1": ("MUSICIANS/D/Dya/Ninja_Carnage.sid", 1),
}
RELOCATION_MISSED = (
    "cruiser_x_79_1",
    "cruiser_x_79_4",
    "captain_ishtar_1",
    "captain_ishtar_5",
    "ninja_carnage_1",
    "greaties_tune_3",
    "trance_sector_12",
)

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
    *RELOCATION_MISSED,
)


STANDARD = (
    "cheese_wings",
    "a_crack_in_the_facade",
    "stereo",
)
CLOCK = 985248


def _tune(name: str) -> tuple:
    relpath, song = SUBTUNES[name]
    path = resolve_tune(relpath)
    if path is None:
        raise TuneFetchError(f"tune {relpath} unavailable (offline, not cached)")
    return Path(path).read_bytes(), song


@pytest.mark.parametrize("name", MEMORY)
def test_spec_memory_equals_the_tunes_own_6502_every_call(name):
    data, song = _tune(name)
    assert spec_memcheck.check(data, song)["frame"] == "ok"


@pytest.mark.parametrize(
    "name", [n for n in RELOCATION_MISSED if n != "greaties_tune_3"]
)
def test_binding_reads_the_tables_the_typed_song_was_read_from(name):
    data, song = _tune(name)
    model = decompile_sid(data, song - 1)
    layout = gt2spec.binder(model)(model.memory, 0)
    rows = [speed_bytes(row) for row in model.typed.speedtable]
    left = [model.memory[layout["speedleft"] + 1 + k] for k in range(len(rows))]
    right = [model.memory[layout["speedright"] + 1 + k] for k in range(len(rows))]
    assert list(zip(left, right)) == rows


def test_row_reader_is_translation_valid():
    data, song = _tune("hammurabi")
    player = InPlace(data, song - 1)
    view = player.view()
    found = player.binder.match(view)
    result = tv.validate(
        gt2spec.spec(),
        player.binder(view, found["mt_play"]),
        "chan[0].getnewnote",
        view,
        found["mt_getnewnote"],
        registers={"X": 0},
        stop=found["mt_loadregs"],
    )
    assert result.proven, result.counterexample


def _standard_table(data: bytes, model, clock: int) -> bytes:
    """``data`` with the note table its playroutine reads set to the standard words."""
    view = GOATTRACKER2.spec_player(model).view()
    binder = gt2spec.binder(model)
    layout = binder(view, *binder.entries(view))
    low, high = layout["freqlo_t"], layout["freqhi_t"]
    base = len(data) - len(model.image.image) - model.header.real_load_address
    out = bytearray(data)
    for note in range(high - low):
        word = frequency(note, clock)
        out[base + low + note] = word & 0xFF
        out[base + high + note] = word >> 8
    return bytes(out)


@pytest.mark.parametrize("name", MEMORY)
def test_format_spec_player_grid_equals_the_tunes_6502(name):
    data, song = _tune(name)
    model = GOATTRACKER2.load(data, song - 1)
    frames = song_frames(data, song)
    player = GOATTRACKER2.spec_player(model)
    assert player.render_grid(frames) == TunePlayer(data, song - 1).render_grid(frames)


@pytest.mark.parametrize("name", STANDARD)
def test_standard_notes_equal_the_6502_on_a_standard_table(name):
    """The tune's own code run on a copy whose note table holds the standard words
    plays what the original does read from the standard words."""
    data, song = _tune(name)
    model = GOATTRACKER2.load(data, song - 1)
    frames = song_frames(data, song)
    standard = GOATTRACKER2.spec_player(model, notes=CLOCK).render_grid(frames)
    patched = _standard_table(data, model, CLOCK)
    assert standard == TunePlayer(patched, song - 1).render_grid(frames)
