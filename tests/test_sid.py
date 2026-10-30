"""Decompiling packed GoatTracker .sid files.

The packed tunes are GoatTracker 2.76's own packer output for songs built by
``scripts/pack_fixtures.py``. An optional real-HVSC smoke test runs only when
``PYGOATTRACKER_SID`` points at a file.
"""

import dataclasses
import json
import os
import re
import struct
import sys
import types
from pathlib import Path

import numpy as np
import pysidtracker
import pytest

from pysidtracker import PlayroutineKind, SidError, SidImage

from pygoattracker import constants
from pygoattracker import sid
from pygoattracker.errors import GoatTrackerError, SidParseError
from pygoattracker.packed import (
    orderlist_bytes,
    packed_orderlist,
    pattern_bytes,
    pattern_rows,
)
from pygoattracker.player import Player
from tests.conftest import pass_frames
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

REST = constants.REST
LOAD = 0x1000
DATA = Path(__file__).parent / "data"
_FREQ = 0x11DD - LOAD  # the minimal tune's frequency table, C-4 up


def _psid(
    data: bytes,
    init=LOAD,
    play=LOAD,
    songs=1,
    name="TEST",
    author="AUTH",
    released="2026",
) -> bytes:
    """Wrap C64 ``data`` (loaded at LOAD) in a minimal PSID v2 container."""
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)  # version
    struct.pack_into(">H", header, 0x06, 0x7C)  # data offset
    struct.pack_into(">H", header, 0x08, 0)  # load addr in file
    struct.pack_into(">H", header, 0x0A, init)
    struct.pack_into(">H", header, 0x0C, play)
    struct.pack_into(">H", header, 0x0E, songs)
    struct.pack_into(">H", header, 0x10, 1)  # start song
    header[0x16 : 0x16 + len(name)] = name.encode("latin-1")
    header[0x36 : 0x36 + len(author)] = author.encode("latin-1")
    header[0x56 : 0x56 + len(released)] = released.encode("latin-1")
    return bytes(header) + struct.pack("<H", LOAD) + data


def _minimal_packed(trailer: bytes = b"", freq_lo=None, songs=1) -> bytes:
    """The minimal packed tune: one instrument, a 2-row wavetable, two patterns.

    ``trailer`` appends bytes after the song data; ``freq_lo`` retunes the low
    column of the frequency table; ``songs`` sets the PSID header count.
    """
    raw = (DATA / "gt2_minimal.sid").read_bytes()
    data = bytearray(raw[0x7E:])
    if freq_lo is not None:
        data[_FREQ : _FREQ + 13] = bytes(freq_lo[48 : 48 + 13])
    return _psid(bytes(data) + trailer, LOAD, LOAD + 3, songs, "TEST", "AUTH")


@pytest.fixture(name="packed")
def packed_fixture() -> bytes:
    return _minimal_packed()


def test_header_fields(packed):
    header = sid.parse_sid_header(packed)
    assert header.magic == b"PSID"
    assert header.version == 2
    assert header.songs == 1
    assert header.name == "TEST"
    assert header.author == "AUTH"


def test_not_a_sid():
    with pytest.raises(SidParseError, match="not a SID file"):
        sid.parse_sid_header(b"NOPE" + b"\0" * 120)


def test_decompile_minimal(packed):
    result = sid.decompile_sid(packed)
    song = result.song
    assert song.name == "TEST"
    assert len(song.subtunes) == 1
    assert len(song.subtunes[0].channels) == 3
    assert len(song.patterns) == 2
    assert len(song.instruments) == 1


def test_minimal_notes_and_instrument(packed):
    song = sid.decompile_sid(packed).song
    row = song.patterns[0].rows[0]
    assert row.note == constants.note_value("C-4")
    assert row.instrument == 1
    assert song.patterns[1].rows[0].note == REST
    instr = song.instruments[0]
    assert instr.attack_decay == 0x09
    assert instr.wave_ptr == 1


def test_minimal_wavetable_reversed(packed):
    song = sid.decompile_sid(packed).song
    # right column note byte reverses via ^0x80; the stop jump row is preserved.
    assert song.wavetable.left == [0x41, 0xFF]
    assert song.wavetable.right == [0x00, 0x00]


def test_orderlist_patterns(packed):
    song = sid.decompile_sid(packed).song
    channels = song.subtunes[0].channels
    assert channels[0].entries[0].num == 0
    assert channels[1].entries[0].num == 1


def test_roundtrips_through_writer(packed):
    song = sid.decompile_sid(packed).song
    reparsed = parse_sng(build_sng(song))
    assert len(reparsed.patterns) == len(song.patterns)
    assert len(reparsed.instruments) == len(song.instruments)
    assert reparsed.wavetable.left == song.wavetable.left


def test_pattern_count_ignores_trailing_bytes():
    # Bytes trailing the pattern block (e.g. author info, or an unbounded
    # decrunched image) must not be decoded as extra patterns.
    junk = bytes([0x01, 0x60, 0x00] * 8)  # would parse as spurious patterns
    song = sid.decompile_sid(_minimal_packed(trailer=junk)).song
    assert len(song.patterns) == 2


def test_no_frequency_table():
    # A PSID whose payload is not a GoatTracker player.
    blob = _psid(b"\xea" * 64, init=LOAD, play=LOAD)
    with pytest.raises(SidParseError):
        sid.decompile_sid(blob)


def test_read_sid_convenience(packed):
    song = sid.read_sid(packed)
    assert len(song.patterns) == 2


def test_load_image_lays_the_tune_over_power_on_ram(packed):
    from pysidtracker.poweron import power_on_ram

    image = sid._load_image(packed)
    ram = power_on_ram()
    assert image.mem[: image.load] == ram[: image.load]
    assert image.mem[image.end :] == ram[image.end :]


def test_decode_orderlist_repeat_and_transpose():
    # transpose +3, pattern 5 repeated 3 extra times, endmark + restart 1.
    mem = bytearray([0xF3, 0x05, 0xD3, 0xFF, 0x01])
    olist, end = packed_orderlist(mem, 0)
    assert end == 5
    assert olist.restart == 1
    kinds = [type(e).__name__ for e in olist.entries]
    assert kinds == ["Transpose", "Repeat", "PlayPattern"]
    assert olist.entries[0].semitones == 3
    assert olist.entries[1].count == 3
    assert olist.entries[2].num == 5


def test_load_image_explicit_load_address():
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)
    struct.pack_into(">H", header, 0x06, 0x7C)
    struct.pack_into(">H", header, 0x08, 0x2000)  # explicit load address
    struct.pack_into(">H", header, 0x0E, 1)
    image = sid._load_image(bytes(header) + b"\x01\x02\x03")
    assert image.load == 0x2000
    assert image.mem[0x2000] == 0x01
    assert image.end == 0x2003


def test_load_image_overrun():
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)
    struct.pack_into(">H", header, 0x06, 0x7C)
    struct.pack_into(">H", header, 0x08, 0xFFFF)
    with pytest.raises(SidParseError, match="overruns"):
        sid._load_image(bytes(header) + b"\x00" * 8)


def test_read_sid_from_path_and_file(tmp_path, packed):
    path = tmp_path / "tune.sid"
    path.write_bytes(packed)
    assert len(sid.read_sid(path).patterns) == 2
    assert len(sid.read_sid(str(path)).patterns) == 2
    with path.open("rb") as handle:
        assert len(sid.read_sid(handle).patterns) == 2


def test_read_sid_bad_type():
    with pytest.raises(TypeError):
        sid.read_sid(1234)


def test_cli_sid2sng(tmp_path, packed):
    from pygoattracker.cli import main

    sid_path = tmp_path / "tune.sid"
    out = tmp_path / "tune.sng"
    sid_path.write_bytes(packed)
    assert main(["sid2sng", str(sid_path), str(out)]) == 0
    reparsed = parse_sng(out.read_bytes())
    assert len(reparsed.patterns) == 2


def test_error_base_is_sid_error():
    # The pygoattracker error hierarchy is re-parented onto pysidtracker's.
    assert issubclass(GoatTrackerError, SidError)
    assert issubclass(SidParseError, SidError)
    assert issubclass(SidParseError, pysidtracker.SidParseError)


def test_parser_detects_direct_load(packed):
    detection = sid.GoatTrackerSidParser().detect(packed)
    assert detection.kind is PlayroutineKind.DIRECT
    assert detection.trustworthy_header
    assert not detection.ran_init
    assert detection.anchor  # the frequency-table anchor tuple


def test_parser_parse_returns_decompiled_sid(packed):
    decoded = sid.GoatTrackerSidParser().parse(packed)
    assert isinstance(decoded, sid.DecompiledSid)
    assert len(decoded.song.patterns) == 2
    frames = pass_frames(decoded.player())
    assert decoded.player().render_grid(frames) == sid.decompile_sid(
        packed
    ).player().render_grid(frames)


def test_parser_recognizes_packed_player(packed):
    image = SidImage.from_sid(packed)
    assert sid.GoatTrackerSidParser().recognize(image) == sid._recognize(image)
    assert sid.GoatTrackerSidParser().recognize(image) is not None


def test_parser_ignores_bare_frequency_table():
    data = bytes(constants.FREQ_LO[:12]) + bytes(constants.FREQ_HI[:12])
    image = SidImage.from_sid(_psid(data))
    assert sid._recognize(image) is not None
    assert sid.GoatTrackerSidParser().recognize(image) is None


# --- finetuned frequency table (stock high column, retuned low column) --------


def _retuned_low():
    # A low column that differs from the stock table so the exact both-column
    # anchor misses, but the high column is left stock so the fallback finds it.
    return tuple((b + 3) & 0xFF for b in constants.FREQ_LO)


def test_finetuned_freqtable_split_anchor_misses():
    image = SidImage.from_sid(_minimal_packed(freq_lo=_retuned_low()))
    # exact both-column match fails on the retuned low bytes ...
    assert (
        image.find_split_table(constants.FREQ_LO, constants.FREQ_HI, min_length=8)
        is None
    )
    # ... but the high-column fallback recovers the anchor.
    assert sid._find_freq_hi_anchor(image) is not None


def test_finetuned_freqtable_decompiles():
    song = sid.decompile_sid(_minimal_packed(freq_lo=_retuned_low())).song
    assert len(song.patterns) == 2
    assert song.patterns[0].rows[0].note == constants.note_value("C-4")


def test_finetuned_freqtable_recovers_retuned_low_bytes():
    lo = _retuned_low()
    result = sid.decompile_sid(_minimal_packed(freq_lo=lo))
    # The reported frequency table reflects the retuned low bytes read from the
    # image, not the stock table.
    freq = result.info.freq_table
    assert (freq[0] & 0xFF) == lo[result.info.firstnote]


def test_parser_detects_finetuned_as_direct():
    detection = sid.GoatTrackerSidParser().detect(
        _minimal_packed(freq_lo=_retuned_low())
    )
    assert detection.kind is PlayroutineKind.DIRECT


# --- subtune count from the song table the player reads, not the header -------


def test_subtune_count_derived_when_header_wrong():
    # Header advertises 2 subtunes but the song table the player reads holds 1.
    song = sid.decompile_sid(_minimal_packed(songs=2)).song
    assert len(song.subtunes) == 1
    assert len(song.patterns) == 2


# --- streams the 8-bit cursor wraps through -------------------------------------


def test_orderlist_without_end_mark_wraps_to_its_start():
    mem = bytearray(range(0x80)) * 4  # no $FF LOOPSONG within the cursor's reach
    olist, end = packed_orderlist(mem, 0)
    assert end == 0x100 and olist.restart == 0
    assert orderlist_bytes(olist) == bytes(mem[:0x100])


def test_pattern_filling_the_reach_has_no_end_mark():
    mem = bytearray([0x60] * 0x100 + [0x60, 0x00])
    rows, end = pattern_rows(mem, 0)
    assert end == 0x100 and len(rows) == 0x100
    assert pattern_bytes(rows) == bytes(mem[:0x100])


def test_pattern_row_cut_by_the_reach_ends_before_it():
    mem = bytearray([0x60] * 0xFF + [0x41, 0x01, 0x60, 0x00])
    rows, end = pattern_rows(mem, 0)
    assert end == 0xFF and len(rows) == 0xFF


REAL_SID = os.environ.get("PYGOATTRACKER_SID")


@pytest.mark.skipif(not REAL_SID, reason="set PYGOATTRACKER_SID to a packed GT .sid")
def test_real_sid_roundtrips():
    song = sid.read_sid(REAL_SID)
    assert song.patterns
    parse_sng(build_sng(song))  # recovered song is a valid .SNG


def test_survey_render(packed):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import oracle_hvsc  # pylint: disable=import-outside-toplevel

    d = sid.decompile_sid(packed)
    frames = pass_frames(d.player())
    grid, info = oracle_hvsc.render(packed, 1, frames)
    want = Player(
        d.typed, freq_table=d.info.note_read, simplepulse=d.info.playback.simplepulse
    ).render_grid(frames)
    assert grid.shape == (frames, 25) and (grid == np.asarray(want)).all()
    oracle = oracle_hvsc.player(packed, 1).render_grid(frames)
    assert (np.asarray(oracle) == grid).all()
    assert json.loads(json.dumps(info))["simplepulse"] == d.info.playback.simplepulse
    assert {"adparam", "firstnohr", "pulseopt", "realtime", "state"} <= set(info)
    with pytest.raises(pysidtracker.SidParseError):
        oracle_hvsc.render(b"PSID" + b"\0" * 200, 1, 10)
    assert re.search(oracle_hvsc.PLAYER, "GoatTracker_V2.x", re.I)


def test_init_regs_are_the_init_routine_s_sid_writes():
    data = _minimal_packed()[0x7E:]
    stub = LOAD + len(data)
    code = bytes.fromhex("A9 20 8D 12 D4 A9 FF 8D 0E D4 8D 0F D4 8D 20 D4 A9 00")
    code += bytes([0x4C, LOAD & 0xFF, LOAD >> 8])
    d = sid.decompile_sid(_psid(data + code, init=stub, play=LOAD + 3))
    assert d.info.playback.init_regs[:3] == ((0x0E, 0xFF), (0x0F, 0xFF), (0x12, 0x20))
    assert all(
        r not in (0x0E, 0x0F, 0x12)
        for r, _ in sid.decompile_sid(_minimal_packed()).info.playback.init_regs
    )


def _redefine(d, **defines):
    """``d`` playing its build with ``defines`` replaced, the song-data options kept."""
    pb = d.info.playback
    d.info.build = dataclasses.replace(d.info.build, **defines)
    d.info.playback = d.info.build.resolve(
        simplepulse=pb.simplepulse, live_vibrato=pb.live_vibrato, init_regs=pb.init_regs
    )


def test_player_uses_the_build_s_hard_restart_adsr():
    d = sid.decompile_sid(_minimal_packed())
    stock = d.player()
    assert stock.build.adparam == constants.DEFAULT_ADPARAM
    _redefine(d, adparam=0xFF, srparam=0x01)
    player = d.player()
    assert player.build.adparam == 0xFF01
    frames = pass_frames(d.player())
    assert player.render_grid(frames) != stock.render_grid(frames)


def test_player_uses_the_build_s_pulse_and_realtime_optimizations():
    d = sid.decompile_sid(_minimal_packed())
    stock = d.player()
    assert (stock.build.optimize_pulse, stock.build.optimize_realtime) == (True, True)
    _redefine(d, pulseopt=None, realtime=True)
    assert d.player().build.optimize_pulse is True  # None stays the editor default
    _redefine(d, pulseopt=False, realtime=False)
    player = d.player()
    assert (player.build.optimize_pulse, player.build.optimize_realtime) == (
        False,
        False,
    )


def test_voices_fold_onto_the_chips_the_header_declares():
    raw = bytearray(0x7C)
    raw[4:6] = (0, 4)
    raw[0x7A], raw[0x7B] = 0x42, 0x44
    assert sid._chips(bytes(raw)) == {0, 1, 2}
    raw[4:6] = (0, 3)
    assert sid._chips(bytes(raw)) == {0, 1}
    raw[4:6] = (0, 2)
    assert sid._chips(bytes(raw)) == {0}
    assert sid._mirror((0, 0x27, 0x47, None), {0, 1}) == (0, 0x27, 0x07, None)
    assert sid._mirror(None, {0}) is None


def _image(packed):
    return packed[0x7C + 2 :]


def test_prg_and_bin_match_psid(packed, tmp_path):
    want = sid.decompile_sid(packed)
    prg = LOAD.to_bytes(2, "little") + _image(packed)
    for got in (
        sid.decompile_sid(prg),
        sid.decompile_sid(_image(packed), load_address=LOAD),
    ):
        # no header, so no title/author/copyright
        assert dataclasses.replace(
            got.typed, name="", author="", copyright=""
        ) == dataclasses.replace(want.typed, name="", author="", copyright="")
        assert got.repack() == want.repack()
    path = tmp_path / "out.bin"
    path.write_bytes(_image(packed))
    assert sid.read_sid(path).subtunes == sid.read_sid(packed).subtunes


def test_too_short_prg_rejected():
    with pytest.raises(sid.SidParseError):
        sid.decompile_sid(b"\x00\x10")


def test_the_anchor_is_that_of_the_player_the_init_ran(monkeypatch):
    anchors = [(0x1B2A, 0x17, 0x21), (0x202B, 0, 0x60)]
    monkeypatch.setattr(sid, "_all_anchors", lambda image: anchors)
    monkeypatch.setattr(
        sid,
        "player_span",
        lambda image, data, entered: types.SimpleNamespace(
            start=0x1800 if data < 0x2000 else None
        ),
    )
    assert sid._played_anchor(None, anchors[1], {0x1800}) == anchors[0]
    assert sid._played_anchor(None, anchors[1], {0x1000}) == anchors[1]
