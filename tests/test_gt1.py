"""GoatTracker V1.x packed tunes: recognition, decompile round trip and playback.

Synthetic songs are packed with each V1.5x playroutine and played both natively
and by running the packed 6502 playroutine itself on jennings.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from pysidtracker.oracle import EmuPlayer

from pygoattracker import SidParseError
from pygoattracker.errors import GoatTrackerError
from pygoattracker.gt1 import (
    Gt1Instrument,
    Gt1Song,
    decompile_gt1,
    find_player,
    pattern_len,
    reference,
    speedcode_timer,
)
from pygoattracker.gt1player import POWERUP_RAM, Gt1Player
from pygoattracker._gt1_refs import PLAYERS

REPO = Path(__file__).resolve().parent.parent
NATIVE = [name for name, entry in PLAYERS.items() if entry["native"]]
FRAMES = 700


def _n(note, instr=0, cmd=0, param=0):
    return bytes((note, instr << 3 | cmd, param))


def _note(note):
    return bytes((note + 0x60,))


INSTRUMENTS = [
    Gt1Instrument(0x09, 0x00, 0x48, 0x20, 0x02, 0x0C, 0x00, 1),
    Gt1Instrument(0x0A, 0xA9, 0x00, 0x11, 0x01, 0x0E, 0x04, 5),
    Gt1Instrument(0x00, 0xF9, 0x80, 0x00, 0x00, 0x00, 0x00, 7),
    Gt1Instrument(0x22, 0x84, 0x31, 0x41, 0x03, 0x09, 0x0C, 11),
]
WAVETABLE = [
    (0x41, 0x00),
    (0x02, 0xB0),
    (0x40, 0x07),
    (0xFF, 0x00),
    (0x21, 0x00),
    (0xFF, 0x00),
    (0x81, 0xC0),
    (0x41, 0xA0),
    (0x80, 0x00),
    (0xFF, 0x00),
    (0x11, 0x0C),
    (0x01, 0x00),
    (0xFF, 0x01),
]
FILTERTABLE = bytes(
    (0x00, 0x00, 0x04, 0x06)
    + (0xF1, 0x1F, 0x40, 0x08)
    + (0x00, 0x10, 0x02, 0x0C)
    + (0x00, 0x08, 0xFE, 0x04)
)
PATTERNS = [
    _n(0x30, 1)
    + _note(0x32)
    + bytes((0xFD,))
    + _n(0x34, 1, 0, 0x37)
    + _n(0x34, 0, 0, 0xB7)
    + _note(0x5E)
    + _note(0x5F)
    + _n(0x35, 1, 1, 0x10)
    + _n(0x37, 0, 4, 0x24)
    + _note(0x37)
    + bytes((0xFF,)),
    _n(0x30, 2)
    + _n(0x3C, 0, 3, 0x08)
    + _note(0x3C)
    + _n(0x30, 0, 3, 0x00)
    + _n(0x2B, 0, 2, 0x20)
    + _n(0x2B, 0, 6, 0xF8)
    + _n(0x2D, 0, 5, 0x04)
    + _n(0x2D, 0, 7, 0x03)
    + _n(0x5F, 0, 7, 0x81)
    + _n(0x5F, 0, 7, 0xEF)
    + _n(0x5F, 0, 7, 0xF8)
    + _n(0x29, 1, 7, 0x04)
    + bytes((0xFF,)),
    _n(0x24, 2) + _n(0x26, 3) + bytes((0xF8,)) + _note(0x28) + bytes((0xFF,)),
    _n(0x18, 2, 7, 0x00)
    + bytes((0xFA,))
    + _n(0x30, 2, 0, 0)
    + _n(0x24, 0, 3, 0x10)
    + _n(0x24, 0, 4, 0x83)
    + _note(0x24)
    + _n(0x1A, 4, 0, 0x47)
    + bytes((0xFF,)),
    _n(0x30, 3) + _note(0x5F) + _n(0x31, 3) + _note(0x5E) + bytes((0xFF,)),
]
ORDERLISTS = [
    [
        bytes((0xD1, 0x00, 0xF3, 0x01, 0xE2, 0x02, 0xFF, 0x00)),
        bytes((0x03, 0xFF, 0x00)),
        bytes((0x04, 0x04, 0xFF, 0x01)),
    ],
    [
        bytes((0x02, 0x01, 0xFF, 0x00)),
        bytes((0xF1, 0x00, 0xFF, 0x00)),
        bytes((0x03, 0xFF, 0x00)),
    ],
]


def make_song(player="1.53", address=0x1000, **fields) -> Gt1Song:
    """A synthetic song exercising every V1.5x command and table feature."""
    values = {
        "instruments": list(INSTRUMENTS),
        "wavetable": list(WAVETABLE),
        "filtertable": FILTERTABLE,
        "orderlists": [list(chans) for chans in ORDERLISTS],
        "patterns": list(PATTERNS),
    }
    values.update(fields)
    return Gt1Song(player=player, address=address, **values)


def emulated(song: Gt1Song, subtune: int, frames: int = FRAMES):
    """The register grid of the packed 6502 playroutine itself."""
    address, data = song.pack()
    emu = EmuPlayer(data, address, address, address + 3, subtune)
    return emu.render_grid(frames)


@pytest.mark.parametrize("player", NATIVE)
@pytest.mark.parametrize("subtune", [0, 1])
def test_native_player_matches_6502(player, subtune):
    song = make_song(player)
    grid = Gt1Player(song, subtune).render_grid(FRAMES)
    assert grid == emulated(song, subtune)


@pytest.mark.parametrize("params", [(4, 2, 0x0F, 0x00), (6, 4, 0x00, 0x00)])
def test_multispeed_gate_timer_params(params):
    song = make_song("1.53", address=0x2000, params=params)
    assert Gt1Player(song).render_grid(300) == emulated(song, 0, 300)


@pytest.mark.parametrize("player", NATIVE)
def test_sid_round_trip(player):
    song = make_song(player, address=0x3000)
    raw = song.to_sid("SONG", "AUTHOR", "2026")
    decoded = decompile_gt1(raw)
    assert decoded.player in NATIVE
    assert decoded.pack() == song.pack()
    assert decoded.instruments == song.instruments
    assert decoded.wavetable == song.wavetable
    assert decoded.orderlists == song.orderlists
    assert decoded.patterns == song.patterns


def test_code_identical_players_told_apart_by_frequency_table():
    for player in ("1.52", "1.51"):
        raw = make_song(player).to_sid()
        assert decompile_gt1(raw).player == player


def test_reference_relocation_walk():
    ref = reference("1.53")
    assert len(ref.params) == 4
    assert ref.code_end == ref.labels["mt_timecounter"]
    assert ref.labels["mt_chnwave"] + 84 == ref.labels["mt_freqtblhi"]
    assert all(0 <= pos < ref.code_end for pos in ref.smc)
    for name in PLAYERS:
        assert reference(name).regex.match(PLAYERS[name]["code"]) is not None


def test_state_and_zeropage_survive_round_trip():
    song = make_song("1.52")
    ref = song.ref
    song.state = {pos: 0x33 for pos in ref.zeropage}
    song.state[ref.labels["mt_volume"] + 1] = 0xF7
    decoded = decompile_gt1(song.to_sid())
    assert decoded.state[ref.labels["mt_volume"] + 1] == 0xF7
    assert decoded.pack() == song.pack()


def test_volume_state_applies():
    song = make_song("1.53")
    song.state = {song.ref.labels["mt_volume"] + 1: 0xF3}
    grid = Gt1Player(song).render_grid(4)
    assert grid == emulated(song, 0, 4)
    assert grid[-1][0x18] & 0x0F <= 3


def test_multispeed_init_speedcode():
    song = make_song("1.53", address=0x1000)
    address, data = song.pack()
    raw = bytearray(song.to_sid())
    stub = bytes((0xA2, 0x34, 0x8E, 0x04, 0xDC, 0xA2, 0x12, 0x8E, 0x05, 0xDC))
    raw[0x0A:0x0C] = (address - 10).to_bytes(2, "big")
    raw[0x7C:0x7E] = (address - 10).to_bytes(2, "little")
    raw[0x7E:] = stub + data
    decoded = decompile_gt1(bytes(raw))
    assert not decoded.initialized
    assert decoded.pack() == (address, data)
    mem = bytearray(0x10000)
    mem[address - 10 : address] = stub
    assert speedcode_timer(mem, address - 10) == 0x1234
    assert speedcode_timer(mem, address) is None


def test_init_stub_runs_in_emulator():
    song = make_song("1.53", address=0x1000)
    address, data = song.pack()
    stub = bytes((0xA2, 0x01, 0x8E, 0x05, 0xDC, 0x4C)) + address.to_bytes(2, "little")
    raw = bytearray(song.to_sid())
    raw[0x0A:0x0C] = (address - len(stub)).to_bytes(2, "big")
    raw[0x7C:0x7E] = (address - len(stub)).to_bytes(2, "little")
    raw[0x7E:] = stub + data
    decoded = decompile_gt1(bytes(raw), subtune=1)
    assert decoded.initialized
    assert Gt1Player(decoded, 1).render_grid(200) == emulated(song, 1, 200)


def test_play_address_must_be_the_jump_table():
    raw = bytearray(make_song().to_sid())
    raw[0x0C:0x0E] = (0x1006).to_bytes(2, "big")
    with pytest.raises(SidParseError, match="play address"):
        decompile_gt1(bytes(raw))


def test_not_a_gt1_tune():
    raw = bytearray(make_song().to_sid())
    raw[0x7E:0x84] = bytes(6)
    with pytest.raises(SidParseError, match="no GoatTracker V1.x"):
        decompile_gt1(bytes(raw))
    assert find_player(bytearray(0x10000)) is None


def test_broken_layout_rejected():
    song = make_song()
    address, data = song.pack()
    mem = bytearray(0x10000)
    mem[address : address + len(data)] = data
    found = find_player(mem)
    order = song.layout()["songs"]
    mem[order + 1 : order + 8] = bytes(7)
    from pygoattracker.gt1 import decompile_image  # pylint: disable=C0415

    with pytest.raises(SidParseError):
        decompile_image(mem, found)


def test_truncated_last_pattern():
    song = make_song()
    cut = len(song.pack()[1]) - 3
    raw = song.to_sid()[: 0x7E + cut]
    decoded = decompile_gt1(raw)
    assert decoded.patterns[-1] == song.patterns[-1][:-3]
    mem = bytes(range(0x60)) * 4
    with pytest.raises(SidParseError, match="no end mark"):
        pattern_len(bytes(0x10000), 0)
    assert pattern_len(mem, 0, 10) == 10


def test_uninitialised_ram_is_sidplayfp_pattern():
    assert len(POWERUP_RAM) == 0x10000
    assert POWERUP_RAM[0x1D2B] == 0xFF and POWERUP_RAM[0x1D28] == 0x00
    assert POWERUP_RAM[0x4002] == 0x00 and POWERUP_RAM[0x4000] == 0xFF


def test_effect_index_outside_jump_table():
    song = make_song("1.53")
    player = Gt1Player(song)
    player.play_frame()
    player._mem[player._chn + 44] = 9  # pylint: disable=protected-access
    player._mem[player._chn + 66] = 3  # pylint: disable=protected-access
    with pytest.raises(GoatTrackerError, match="jump table"):
        player.play_frame()


def test_orderlist_without_pattern_raises():
    song = make_song(orderlists=[[bytes((0xF0, 0xFF, 0x00))] * 3])
    with pytest.raises(GoatTrackerError, match="never reaches"):
        Gt1Player(song).render_grid(4)


def test_generated_references_are_current():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "gt1_refs.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
