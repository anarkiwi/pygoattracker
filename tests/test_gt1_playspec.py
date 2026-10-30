"""The GoatTracker V1.x playspecs against the tune's own 6502 and the song grammar.

The ``oracle``-marked tests need the HVSC tune cache; the memory gate runs every
GT1 oracle tune's whole song.
"""

import pytest
from pysidtracker.playspec.tv import validate
from pysidtracker import SidImage
from pysidtracker.testing import resolve_tune, song_frames

from pygoattracker import gt1spec
from pygoattracker.gt1 import decompile_gt1, find_player
from pygoattracker.gt1player import Gt1Player
from tests.test_gt1 import DriverEmu, make_song, make_song11, with_sfx
from tests.conftest import pass_frames
from tests.test_oracle_hvsc import TUNES

RAM = ((0, 0x100), (0x200, 0xD000), (0xE000, 0x10000))
GT1_TUNES = {k: v for k, v in TUNES.items() if k.startswith("gt1_")}
JOKE = "DEMOS/G-L/Joke.sid"


def assert_memory_equals_the_6502(raw: bytes, subtune: int, frames: int):
    """After every call the SID registers and the RAM outside the stack page and
    I/O equal the tune's own play routine's, entered with the flags init left."""
    spec = Gt1Player(decompile_gt1(raw, subtune), subtune, sid=raw)
    image = SidImage.from_bytes(raw)
    head = image.header
    cpu = DriverEmu(
        image.image, image.load, head.init_address, head.play_address, subtune
    )
    for frame in range(frames):
        spec.play_frame()
        cpu.play_frame()
        assert spec.regs == cpu.regs, frame
        mine, real = spec.memory, bytes(cpu._mem)  # pylint: disable=protected-access
        for lo, hi in RAM:
            assert mine[lo:hi] == real[lo:hi], (frame, hex(lo))


@pytest.mark.parametrize(
    "song",
    [make_song("1.53"), make_song11("11.4"), with_sfx(make_song11("11.2"))],
    ids=["1.53", "11.4", "11.2-sfx"],
)
def test_spec_memory_equals_the_6502_on_synthetic_songs(song):
    assert_memory_equals_the_6502(song.to_sid(), 0, pass_frames(Gt1Player(song)))


@pytest.mark.parametrize("family", ["1.5", "1.4", "11"])
def test_every_family_spec_passes_the_static_checks(family):
    assert gt1spec.spec(family).layout


def test_streams_the_player_reads_parse_under_the_song_grammar():
    song = make_song("1.53")
    player = Gt1Player(song, validate=True)
    player.render_grid(pass_frames(Gt1Player(song)))
    assert player.validator.results
    assert not player.validator.failures()


def test_loops_count_orderlist_restarts():
    player = Gt1Player(make_song("1.53"))
    while not (player.loops and all(player.loops.values())):
        player.play_frame()
    assert sorted(player.loops) == [0, 7, 14]


@pytest.mark.oracle
@pytest.mark.parametrize("path, song", GT1_TUNES.values(), ids=GT1_TUNES)
def test_spec_memory_equals_the_6502_for_every_gt1_oracle_tune(path, song):
    raw = resolve_tune(path).read_bytes()
    assert_memory_equals_the_6502(raw, song - 1, song_frames(raw, song))


@pytest.mark.oracle
def test_row_reader_is_proven_against_the_6502():
    """``chan[0].getnewnotes`` equals the V1.53 row reader for all initial memory."""
    raw = resolve_tune(JOKE).read_bytes()
    player = Gt1Player(decompile_gt1(raw), sid=raw)
    view = player.view()
    start, stop = gt1spec.row_reader(find_player(view))
    result = validate(
        gt1spec.spec("1.5"),
        gt1spec.binding(view, player.entry),
        "chan[0].getnewnotes",
        view,
        start,
        registers={"X": 0},
        preserve=("X",),
        stop=stop,
    )
    assert result.proven, result.counterexample and {
        k: v for k, v in result.counterexample.items() if k != "memory"
    }
