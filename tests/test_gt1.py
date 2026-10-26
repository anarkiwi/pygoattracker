"""GoatTracker V1.x packed tunes: recognition, decompile round trip and playback.

Synthetic songs are packed with each V1.x playroutine and played both natively
and by running the packed 6502 playroutine itself on jennings.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from pysidtracker.oracle import EmuPlayer
from pysidtracker.testing import resolve_tune

from pygoattracker import SidParseError
from pygoattracker.errors import GoatTrackerError
from pygoattracker.gt1 import (
    ORDERLIST_ENDSONG,
    Gt1Instrument,
    Gt1Song,
    decompile_gt1,
    find_player,
    pattern_len,
    reference,
    speedcode_timer,
)
from pygoattracker.gt1player import Gt1Player
from pygoattracker.sid import _load_image, parse_sid_header
from pygoattracker._gt1_refs import PLAYERS
from tests._fixture_cache import _archive_member

REPO = Path(__file__).resolve().parent.parent
V15_PLAYERS = [name for name, entry in PLAYERS.items() if entry["family"] == "1.5"]
M11_PLAYERS = [name for name, entry in PLAYERS.items() if entry["family"] == "11"]
V14_PLAYERS = [name for name, entry in PLAYERS.items() if entry["family"] == "1.4"]
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


INSTRUMENTS_11 = INSTRUMENTS + [
    Gt1Instrument(0x0A, 0xA9, 0x00, 0x11, 0x01, 0x0E, 0x21, 5),
    Gt1Instrument(0x31, 0x59, 0x22, 0x12, 0x02, 0x0D, 0x00, 0),
]
PATTERNS_11 = [
    _n(0x30, 1, 0, 0x37)
    + _note(0x32)
    + bytes((0xFD,))
    + _n(0x34, 4, 0, 0xB7)
    + _note(0x5E)
    + _note(0x5F)
    + _n(0x35, 1, 1, 0x10)
    + _n(0x37, 0, 1, 0x90)
    + _n(0x37, 0, 4, 0x46)
    + _note(0x37)
    + bytes((0xFF,)),
    _n(0x30, 5)
    + _n(0x3C, 0, 3, 0x08)
    + _note(0x3C)
    + _n(0x2B, 0, 3, 0x88)
    + _n(0x2B, 0, 6, 0xF8)
    + _n(0x2D, 0, 5, 0xF1)
    + _n(0x2D, 0, 2, 0x02)
    + _n(0x2D, 0, 7, 0x03)
    + _n(0x5F, 0, 7, 0x81)
    + _n(0x5F, 0, 7, 0xC5)
    + _n(0x29, 6, 7, 0x04)
    + bytes((0xFF,)),
    _n(0x24, 3) + _n(0x26, 3) + bytes((0xF8,)) + _note(0x28) + bytes((0xFF,)),
    _n(0x18, 4, 7, 0x06) + bytes((0xFA,)) + _n(0x1A, 4, 0, 0x47) + bytes((0xFF,)),
    _n(0x30, 3) + _note(0x5F) + _n(0x31, 2) + _note(0x5E) + bytes((0xFF,)),
    bytes((0x0A, 0x00, 0x88, 0x30, 0x41, 0x32, 0xFE, 0x34, 0x81, 0x35, 0x00, 0x00)),
]
ORDERLISTS_11 = [
    [
        bytes((0x00, 0x01, 0x02, 0xFF, 0x01)),
        bytes((0x03, 0xFF, 0x00)),
        bytes((0x04, 0x04, 0xFF, 0x01)),
    ],
    [
        bytes((0x02, 0x01, 0xFF, 0x00)),
        bytes((0x00, 0xFF, 0x00)),
        bytes((0x03, 0xFF, 0x00)),
    ],
]


# Before V1.15: $FE repeats the last pattern and $FF loops to the start.
ORDERLISTS_ENDSONG = [
    [
        bytes((0x00, 0x01, 0x02, 0xFF)),
        bytes((0x03, 0x01, 0xFE)),
        bytes((0x04, 0x04, 0xFF)),
    ],
    [
        bytes((0x02, 0x01, 0xFE)),
        bytes((0x00, 0xFF)),
        bytes((0x03, 0xFF)),
    ],
]


def make_song11(player="11.3", address=0x1000, **fields) -> Gt1Song:
    """A synthetic song exercising every musicroutine 11.x command."""
    orders = ORDERLISTS_ENDSONG if player in ORDERLIST_ENDSONG else ORDERLISTS_11
    values = {
        "instruments": list(INSTRUMENTS_11),
        "wavetable": list(WAVETABLE),
        "orderlists": [list(chans) for chans in orders],
        "patterns": list(PATTERNS_11),
    }
    values.update(fields)
    return Gt1Song(player=player, address=address, **values)


def with_sfx(song: Gt1Song, x: int = 7) -> Gt1Song:
    """``song`` with a sound effect started on channel ``x`` (as ``playsfx`` does)."""
    ref = song.ref
    sfx = song.layout()["patterns"] + sum(len(p) for p in song.patterns[:-1])
    data = bytearray(song.data or ref.code[ref.code_end :])
    note = ref.labels["mt_chnnote"] - ref.code_end + x
    data[note : note + 3] = bytes((0x81, sfx & 0xFF, sfx >> 8))
    song.data = bytes(data)
    return song


class DriverEmu(EmuPlayer):
    """An :class:`EmuPlayer` entering play with the flags init returned with.

    sidplayfp's PSID driver idles after init and calls play from its interrupt
    handler, whose ``rti`` restores the idle loop's flags before the next call.
    """

    def _init(self, subtune: int) -> None:
        super()._init(subtune)
        self._flags = self._mpu.p

    def _frame(self) -> None:
        self._mpu.p = self._flags
        super()._frame()


def emulated(song: Gt1Song, subtune: int, frames: int = FRAMES):
    """The register grid of the packed 6502 playroutine itself, as the packer saved it."""
    raw = song.to_sid()
    header = parse_sid_header(raw)
    emu = DriverEmu(
        raw[0x7E:],
        header.real_load_address,
        header.init_address,
        header.play_address,
        subtune,
    )
    return emu.render_grid(frames)


@pytest.mark.parametrize("player", V15_PLAYERS)
@pytest.mark.parametrize("subtune", [0, 1])
def test_native_player_matches_6502(player, subtune):
    song = make_song(player)
    grid = Gt1Player(song, subtune).render_grid(FRAMES)
    assert grid == emulated(song, subtune)


INSTRUMENTS_14 = [
    Gt1Instrument(0x09, 0x00, 0x48, 0x20, 0x02, 0x0C, 0x00, 1),
    Gt1Instrument(0x0A, 0xA9, 0x00, 0x01, 0x01, 0x0E, 0x04, 5),
    Gt1Instrument(0x00, 0xF9, 0x83, 0x31, 0x03, 0x09, 0x00, 7),
    Gt1Instrument(0x22, 0x84, 0x31, 0x41, 0x03, 0x09, 0x0C, 11),
]
FILTERTABLE_14 = bytes(
    (0x00, 0x03, 0xFC, 0x04)
    + (0xF1, 0x1F, 0x40, 0x08)
    + (0x00, 0x05, 0x02, 0x0C)
    + (0xF2, 0x2F, 0x00, 0x00)
)
PATTERNS_14 = [
    _n(0x30, 1, 0, 0x37)
    + _note(0x32)
    + bytes((0xFD,))
    + _n(0x34, 2, 0, 0xB7)
    + _note(0x5E)
    + _note(0x5F)
    + _n(0x35, 1, 1, 0x10)
    + _n(0x37, 0, 2, 0x50)
    + _n(0x37, 3, 4, 0x24)
    + _n(0x38, 0, 4, 0xC3)
    + _note(0x37)
    + bytes((0xFF,)),
    _n(0x30, 3)
    + _n(0x3C, 0, 3, 0x08)
    + _note(0x3C)
    + _n(0x24, 0, 3, 0x30)
    + _n(0x18, 0, 3, 0x7F)
    + _n(0x24, 0, 3, 0x7F)
    + _n(0x30, 0, 3, 0x00)
    + _n(0x2B, 0, 6, 0xF8)
    + _n(0x5F, 2, 6, 0x48)
    + _n(0x2D, 0, 5, 0x04)
    + _n(0x2D, 0, 7, 0x03)
    + _n(0x5F, 0, 7, 0x84)
    + _n(0x5F, 0, 7, 0xC5)
    + _n(0x29, 1, 7, 0x06)
    + bytes((0xFF,)),
    _n(0x24, 2) + _n(0x26, 4) + bytes((0xF8,)) + _note(0x28) + bytes((0xFF,)),
    _n(0x30, 4) + _note(0x5F) + _n(0x31, 1, 1, 0x04) + _note(0x5E) + bytes((0xFF,)),
]
ORDERLISTS_14 = [
    [
        bytes((0xD1, 0x00, 0xF3, 0x01, 0xE2, 0x02, 0xFF, 0x00)),
        bytes((0x03, 0xFF, 0x00)),
        bytes((0x01, 0x02, 0xFF, 0x01)),
    ],
    [
        bytes((0x02, 0x01, 0xFF, 0x00)),
        bytes((0xF1, 0x00, 0xFF, 0x00)),
        bytes((0x03, 0xFF, 0x00)),
    ],
]


def make_song14(player="1.4b", **fields) -> Gt1Song:
    """A synthetic song exercising every V1.4x command, multispeed hard restart timer set."""
    values = {
        "instruments": list(INSTRUMENTS_14),
        "orderlists": [list(chans) for chans in ORDERLISTS_14],
        "patterns": list(PATTERNS_14),
        "filtertable": FILTERTABLE_14,
    }
    values.update(fields)
    song = make_song(player, **values)
    timer = song.ref.labels.get("mt_hrtimer")
    if timer is not None:
        song.state = {**song.state, timer: 3}
    return song


@pytest.mark.parametrize("player", V14_PLAYERS)
@pytest.mark.parametrize("subtune", [0, 1])
def test_v14_matches_6502(player, subtune):
    song = make_song14(player)
    assert Gt1Player(song, subtune).render_grid(FRAMES) == emulated(song, subtune)


@pytest.mark.parametrize("player", V14_PLAYERS)
def test_v14_round_trip(player):
    song = make_song14(player, address=0x2400)
    decoded = decompile_gt1(song.to_sid())
    assert decoded.player == player
    assert decoded.pack() == song.pack()
    assert decoded.segments == song.segments
    timer = song.ref.labels.get("mt_hrtimer")
    if timer is not None:
        assert decoded.state[timer] == 3


@pytest.mark.parametrize("player", M11_PLAYERS)
@pytest.mark.parametrize("subtune", [0, 1])
def test_musicroutine11_matches_6502(player, subtune):
    song = make_song11(player)
    assert song.segments[-1] == "patterns" and "filtertable" not in song.segments
    grid = Gt1Player(song, subtune).render_grid(FRAMES)
    assert grid == emulated(song, subtune)


@pytest.mark.parametrize("player", ["11.2", "11.4"])
def test_musicroutine11_sound_effect(player):
    song = with_sfx(make_song11(player))
    assert Gt1Player(song).render_grid(300) == emulated(song, 0, 300)


@pytest.mark.parametrize("player", M11_PLAYERS)
def test_musicroutine11_round_trip(player):
    song = make_song11(player, address=0x2000)
    decoded = decompile_gt1(song.to_sid())
    assert decoded.player == player
    assert decoded.pack() == song.pack()
    assert decoded.segments == song.segments


def test_orderlists_before_patterntable():
    segments = (
        "instruments",
        "wavetable",
        "songtable",
        "orderlists",
        "patterntable",
        "patterns",
    )
    song = make_song11("11.1", segments=segments)
    decoded = decompile_gt1(song.to_sid())
    assert decoded.segments == segments
    assert decoded.pack() == song.pack()
    assert Gt1Player(decoded).render_grid(200) == emulated(song, 0, 200)


@pytest.mark.parametrize("params", [(4, 2, 0x0F, 0x00), (6, 4, 0x00, 0x00)])
def test_multispeed_gate_timer_params(params):
    song = make_song("1.53", address=0x2000, params=params)
    assert Gt1Player(song).render_grid(300) == emulated(song, 0, 300)


@pytest.mark.parametrize("player", V15_PLAYERS)
def test_sid_round_trip(player):
    song = make_song(player, address=0x3000)
    raw = song.to_sid("SONG", "AUTHOR", "2026")
    decoded = decompile_gt1(raw)
    assert decoded.player == player
    assert decoded.pack() == song.pack()
    assert decoded.instruments == song.instruments
    assert decoded.wavetable == song.wavetable
    assert decoded.orderlists == song.orderlists
    assert decoded.patterns == song.patterns


def test_code_identical_players_told_apart_by_frequency_table():
    for player in ("1.52", "1.51"):
        raw = make_song(player).to_sid()
        assert decompile_gt1(raw).player == player
    for player in ("0.96", "1.01"):
        raw = make_song11(player).to_sid()
        assert decompile_gt1(raw).player == player


@pytest.mark.parametrize("player", M11_PLAYERS)
def test_musicroutine11_song_start_resets_channel_state(player):
    song = make_song11(player)
    ref = song.ref
    data = bytearray(ref.code[ref.code_end :])
    for label, value in (("mt_chnwave", 0x41), ("mt_chnpulsedir", 0x81)):
        pos = ref.labels[label] - ref.code_end
        data[pos : pos + 15 : 7] = bytes((value,)) * 3
    song.data = bytes(data)
    for subtune in (0, 1):
        assert Gt1Player(song, subtune).render_grid(200) == emulated(song, subtune, 200)


def test_stub_init_play_entry_carry():
    song = make_song11("0.96")
    assert song.ref.stub_init and song.ref.song_bias == 3
    assert Gt1Player(song, 0x7F).render_grid(50) == emulated(song, 0x7F, 50)


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


# One HVSC tune per playroutine cluster no assembled reference recognizes.
UNMATCHED_HVSC = {
    "MUSICIANS/B/Butka/Eargasm.sid": "V1.4c-V1.4f: 1.4b plus the and #$ff/sta $d418 fader",
    "DEMOS/A-F/Dont_You_Want_Me.sid": "V1.3-V1.4: 1.4b without the 1-frame hard restart",
    "MUSICIANS/D/Dr_Fiemost/Mr_Tod.sid": "musicroutine 11.3 with the pre-V1.22 edits",
    "MUSICIANS/C/Chapelier/Antti_Goes_to_Berkeley.sid": (
        "musicroutine 11.4 with the pre-V1.21 edits"
    ),
    "MUSICIANS/C/Cerror/Christmass_02.sid": "11.4 whose set-sustain bmi skips the lda",
    "MUSICIANS/K/Kozaki_Soft/Sawlarmes_2SID.sid": "V1.4b Stereo, second SID at $d420",
    "MUSICIANS/B/Bayliss_Richard/Summer_Timebooze_2SID.sid": "V1.53 Stereo",
    "MUSICIANS/N/Nata/Advanced_Chemistry.sid": "V1.4b Stereo, second-SID stores nopped",
}

# GoatTracker V0.93 packed examples (musicroutine 11, goattrk093.zip src/PLAYER.S).
GOATTRK093_SIDS = {
    "examples/example.sid": (
        "4548b3bbb761efe30902d615a5c6d2f6adcc57da3202084f97fbca948bafa6dc"
    ),
    "examples/drumtest.sid": (
        "aea6bed968e31296ceeae32c0efc43265d47acf9ff4842f80a16d223b2d8d3fd"
    ),
}


def _assert_decompiles(raw: bytes):
    """``raw`` decompiles and packs back its recognized playroutine byte for byte."""
    image = _load_image(raw)
    song = decompile_gt1(raw)
    found = find_player(image.mem)
    address, packed = song.pack()
    end = address + found.ref.code_end
    assert address == found.address
    assert packed[: found.ref.code_end] == bytes(image.mem[address:end])


@pytest.mark.xfail(
    strict=True,
    raises=SidParseError,
    reason="#35: playroutine with no assembled reference",
)
@pytest.mark.parametrize("rel", sorted(UNMATCHED_HVSC))
def test_unmatched_hvsc_playroutine(rel):
    path = resolve_tune(rel)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    _assert_decompiles(Path(path).read_bytes())


@pytest.mark.xfail(
    strict=True,
    raises=SidParseError,
    reason="#35: V0.93 src/PLAYER.S is not an assembled reference",
)
@pytest.mark.parametrize("member", sorted(GOATTRK093_SIDS))
def test_goattrk093_example_sid(member):
    _assert_decompiles(
        _archive_member("goattrk093.zip", member, GOATTRK093_SIDS[member])
    )
