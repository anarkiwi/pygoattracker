"""Decompiling packed GoatTracker .sid files.

Self-contained: a minimal packed image is assembled in memory (mirroring
greloc's data layout) so no external .sid files are needed. An optional
real-HVSC smoke test runs only when ``PYGOATTRACKER_SID`` points at a file.
"""

import os
import struct

import pytest

from pygoattracker import constants
from pygoattracker import sid
from pygoattracker.errors import SidParseError
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

REST = constants.REST
LOAD = 0x1000


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


def _minimal_packed() -> bytes:
    """One subtune, one instrument, a 2-row wavetable, two 1-row patterns."""
    note_c4 = constants.note_value("C-4")
    mem = bytearray(0x100)
    freqlen = 12
    pos = 0

    def emit(chunk):
        nonlocal pos
        start = pos
        mem[pos : pos + len(chunk)] = bytes(chunk)
        pos += len(chunk)
        return start

    emit(constants.FREQ_LO[:freqlen])
    emit(constants.FREQ_HI[:freqlen])
    songtbllo = emit([0, 0, 0])
    songtblhi = emit([0, 0, 0])
    patttbllo = emit([0, 0])
    patttblhi = emit([0, 0])
    emit([0x09])  # mt_insad
    emit([0x00])  # mt_inssr
    emit([0x01])  # mt_inswaveptr
    emit([0x41, 0xFF])  # wavetable left (waveform, jump)
    emit([0x00, 0x01])  # wavetable right (packed note, jump target)
    order0 = emit([0x00, 0xFF, 0x00])  # channel 0: pattern 0
    order1 = emit([0x01, 0xFF, 0x00])  # channel 1: pattern 1
    order2 = emit([0x00, 0xFF, 0x00])  # channel 2: pattern 0
    patt0 = emit([0x01, note_c4, 0x00])  # instr 1, C-4, end
    patt1 = emit([REST, 0x00])  # one rest row, end

    for i, addr in enumerate((order0, order1, order2)):
        mem[songtbllo + i] = (LOAD + addr) & 0xFF
        mem[songtblhi + i] = (LOAD + addr) >> 8
    for i, addr in enumerate((patt0, patt1)):
        mem[patttbllo + i] = (LOAD + addr) & 0xFF
        mem[patttblhi + i] = (LOAD + addr) >> 8
    return _psid(bytes(mem[:pos]))


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
    with pytest.raises(SidParseError, match="PSID/RSID"):
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
    # right column note byte reverses via ^0x80; the jump row is preserved.
    assert song.wavetable.left == [0x41, 0xFF]
    assert song.wavetable.right == [0x80, 0x01]


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


def test_no_frequency_table():
    # A PSID whose payload is not a GoatTracker player.
    blob = _psid(b"\xea" * 64, init=LOAD, play=LOAD)
    with pytest.raises(SidParseError):
        sid.decompile_sid(blob)


def test_read_sid_convenience(packed):
    song = sid.read_sid(packed)
    assert len(song.patterns) == 2


def test_decode_packed_pattern_grammar():
    # instrument change, FX+note, packed rest run, endmark.
    mem = bytearray([0x02, 0x43, 0x11, constants.note_value("E-4"), 0xFE, 0x00])
    rows, end = sid.decode_packed_pattern(mem, 0)
    assert end == 6
    assert rows[0].instrument == 2
    assert rows[0].command == 0x3
    assert rows[0].data == 0x11
    assert rows[0].note == constants.note_value("E-4")
    # 0xFE == packed rest of 2 rows, carrying the running command.
    assert len(rows) == 3
    assert rows[1].note == REST and rows[2].note == REST


def test_decode_orderlist_repeat_and_transpose():
    # transpose +3, pattern 5 repeated 3 extra times, endmark + restart 1.
    mem = bytearray([0xF3, 0x05, 0xD3, 0xFF, 0x01])
    olist, end = sid.decode_packed_orderlist(mem, 0)
    assert end == 5
    assert olist.restart == 1
    kinds = [type(e).__name__ for e in olist.entries]
    assert kinds == ["Transpose", "Repeat", "PlayPattern"]
    assert olist.entries[0].semitones == 3
    assert olist.entries[1].count == 3
    assert olist.entries[2].num == 5


def test_load_sid_explicit_load_address():
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)
    struct.pack_into(">H", header, 0x06, 0x7C)
    struct.pack_into(">H", header, 0x08, 0x2000)  # explicit load address
    struct.pack_into(">H", header, 0x0E, 1)
    mem, _, load, end = sid.load_sid(bytes(header) + b"\x01\x02\x03")
    assert load == 0x2000
    assert mem[0x2000] == 0x01
    assert end == 0x2003


def test_load_sid_overrun():
    header = bytearray(0x7C)
    header[0:4] = b"PSID"
    struct.pack_into(">H", header, 0x04, 2)
    struct.pack_into(">H", header, 0x06, 0x7C)
    struct.pack_into(">H", header, 0x08, 0xFFFF)
    with pytest.raises(SidParseError, match="overruns"):
        sid.load_sid(bytes(header) + b"\x00" * 8)


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


REAL_SID = os.environ.get("PYGOATTRACKER_SID")


@pytest.mark.skipif(not REAL_SID, reason="set PYGOATTRACKER_SID to a packed GT .sid")
def test_real_sid_roundtrips():
    song = sid.read_sid(REAL_SID)
    assert song.patterns
    parse_sng(build_sng(song))  # recovered song is a valid .SNG
