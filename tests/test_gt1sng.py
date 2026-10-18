"""GoatTracker V1.25 editor songs (``GTS!``, musicroutine 11.x).

The pinned ``goatold.zip`` example set is the corpus: every song must
parse, round-trip byte for byte, and play identical to the packed 6502
playroutine (``EmuPlayer``) once the F9 relocator runs.
"""

import sys
from pathlib import Path

import pytest
from pysidtracker.oracle import EmuPlayer

from pygoattracker.errors import SngParseError, SngValidationError
from pygoattracker.gt1 import Gt1Song, decompile_gt1
from pygoattracker.gt1player import Gt1Player
from pygoattracker.gt1sng import Gt1Sng, parse_gts1, relocate, write_gts1
from tests._fixture_cache import GOATOLD_SONGS, goatold_song, goattrk_song

REPO = Path(__file__).resolve().parent.parent
ORACLE_SID = "MUSICIANS/C/Cadaver/GoatTracker_Classical_Example.sid"
SONGS = sorted(GOATOLD_SONGS)


def _oracle_sid() -> Path:
    sys.path.insert(0, str(REPO / "scripts"))
    import fetch_tunes
    from pysidtracker.testing import resolve_tune

    path = resolve_tune(ORACLE_SID, cache_dir=fetch_tunes.CACHE)
    if path is None:
        pytest.skip("oracle sid unavailable (no local HVSC tree, mirror offline)")
    return Path(path)


_EMPTY_WAVE = b"\x00\x00\xff\x00"
_REST_ROW = b"\x5f\x00\x00"
_END_ROW = b"\xff\x00\x00"


def _sng(
    orderlists=None,
    instruments=None,
    patterns=None,
    name=b"SONG",
    author=b"AUTHOR",
    copyright_text=b"CC",
    magic=b"GTS!",
):
    out = bytearray(magic)
    for text in (name, author, copyright_text):
        out += bytes(text)[:32].ljust(32, b"\x00")
    orderlists = [[bytes((0, 0xFF, 0))] * 3] if orderlists is None else orderlists
    out.append(len(orderlists))
    for chans in orderlists:
        for blob in chans:
            out.append(len(blob) - 1)
            out += bytes(blob)
    instruments = (
        [(bytes(7), len(_EMPTY_WAVE), bytes(16), _EMPTY_WAVE)] * 31
        if instruments is None
        else instruments
    )
    for params, wavelen, insname, wave in instruments:
        out += bytes(params) + bytes((wavelen,)) + insname + wave
    patterns = [b"\x5f\x00\x00\xff\x00\x00"] if patterns is None else patterns
    out.append(len(patterns))
    for body in patterns:
        out.append(len(body))
        out += bytes(body)
    return bytes(out)


def emulated(song: Gt1Song, frames: int, subtune: int = 0):
    """The register grid of the packed 6502 playroutine itself."""
    address, data = song.pack()
    emu = EmuPlayer(data, address, address, address + 3, subtune)
    return emu.render_grid(frames)


@pytest.mark.parametrize("name", SONGS)
def test_parse_and_round_trip(name):
    data = goatold_song(name)
    song = parse_gts1(data)
    assert song.sng is not None
    assert len(song.instruments) == 31
    assert "filtertable" not in song.segments
    assert write_gts1(song) == data


@pytest.mark.parametrize("name", SONGS)
def test_playback_matches_6502_playroutine(name):
    song = parse_gts1(goatold_song(name))
    assert Gt1Player(song).render_grid(120) == emulated(song, 120)


def test_goatclas_matches_gvsc_sid():
    """The relocator must reproduce the editor's packed tune byte for byte."""
    sid = _oracle_sid().read_bytes()
    image = sid[0x7E:]
    song = parse_gts1(goatold_song("GOATCLAS.SNG"), "11.3")
    assert song.sng.name == b"GoatTracker classical example"
    assert relocate(song).pack() == (0x1000, image)
    decoded = decompile_gt1(sid)
    assert decoded.player == "11.3"
    assert Gt1Player(song).render_grid(300) == Gt1Player(decoded).render_grid(300)
    assert Gt1Player(song).render_grid(300) == emulated(song, 300)


def test_goatclas_relocates_instruments_and_wavetable():
    packed = relocate(parse_gts1(goatold_song("GOATCLAS.SNG")))
    assert [ins.wave_ptr for ins in packed.instruments] == [1, 1, 3]
    assert packed.wavetable == [
        (0x21, 0),
        (0xFF, 0),
        (0x11, 0),
        (0xFF, 0),
    ]
    assert all(ins.name == b"" for ins in packed.instruments)


def test_old_endmark_parses_round_trips_and_relocates():
    data = goatold_song("GOATCLAS.SNG")
    song = parse_gts1(data)
    original, old = song.orderlists[0][0], song.orderlists[0][0][:-2] + b"\xfe"
    song.orderlists[0][0] = old
    assert write_gts1(song) == data.replace(b"\x06" + original, b"\x05" + old, 1)
    packed = relocate(song)
    assert packed.orderlists[0][0] == original[:-2] + b"\xff\x00"


def test_tempo_data_decrement_and_timing_marks():
    song = parse_gts1(_sng(patterns=[_REST_ROW + _END_ROW]))
    song.patterns[0] = bytes((0x30, 0x07, 0x9A)) + bytes((0x31, 0x07, 0xC0)) + _END_ROW
    keep = relocate(song)
    assert keep.patterns[0] == bytes((0x30, 0x07, 0x99, 0x31, 0x07, 0xC0, 0xFF))
    body = bytes((0x30, 0x07, 0x9A)) + bytes((0x31, 0x07, 0xC0)) + _END_ROW
    strip = relocate(parse_gts1(_sng(patterns=[body]), "11.1"))
    assert strip.patterns[0] == bytes((0x30, 0x07, 0x99, 0x31, 0x00, 0x00, 0xFF))


def test_vibrato_nybble_swap_and_sticky_instrument():
    body = bytes((0x30, 0x24, 0x12)) + bytes((0x31, 0x00, 0x34)) + _END_ROW
    song = parse_gts1(_sng(patterns=[body]))
    assert relocate(song).patterns[0] == bytes(
        (0x30, 0x24, 0x21, 0x31, 0x20, 0x34, 0xFF)
    )


def test_packed_rest_runs_and_max_length():
    def packed(rows):
        body = _REST_ROW * rows + _END_ROW
        return relocate(parse_gts1(_sng(patterns=[body]))).patterns[0]

    assert packed(3) == b"\x5f\x00\x00\xbf\xbf\xff"
    assert packed(4) == b"\x5f\x00\x00\xfe\xbf\xff"
    assert packed(66) == b"\x5f\x00\x00\xc0\xbf\xff"
    assert packed(67) == b"\x5f\x00\x00\xc0\xbf\xbf\xff"


def test_unreferenced_patterns_are_not_packed():
    data = goatold_song("GOATCLAS.SNG")
    song = parse_gts1(data)
    song.patterns[1] = _END_ROW
    song.patterns[2] = _END_ROW
    song.orderlists[0] = [bytes((0, 0xFF, 0))] * 3
    packed = relocate(song)
    assert len(packed.patterns) == 1
    assert len(packed.instruments) <= len(song.instruments)


def test_relocate_is_idempotent_and_inert_for_packed_songs():
    song = parse_gts1(goatold_song("GOATCLAS.SNG"))
    once, twice = relocate(song), relocate(relocate(song))
    assert once.sng is None
    assert twice.pack() == once.pack()
    assert relocate(once) is once


def test_write_gts1_refuses_packed_and_malformed_songs():
    packed = relocate(parse_gts1(goatold_song("GOATCLAS.SNG")))
    with pytest.raises(SngValidationError):
        write_gts1(packed)
    song = parse_gts1(goatold_song("GOATCLAS.SNG"))
    song.instruments = song.instruments[:30]
    with pytest.raises(SngValidationError):
        write_gts1(song)
    song2 = parse_gts1(goatold_song("GOATCLAS.SNG"))
    song2.orderlists[0] = [b"\x00\xff\x00", b"\x00\xff\x00"]
    with pytest.raises(SngValidationError):
        write_gts1(song2)
    song3 = parse_gts1(goatold_song("GOATCLAS.SNG"))
    song3.sng = Gt1Sng(b"x" * 33, b"", b"")
    with pytest.raises(SngValidationError, match="32"):
        write_gts1(song3)


def test_parse_rejects_bad_input():
    base = _sng()
    with pytest.raises(SngParseError, match="identifier"):
        parse_gts1(_sng(magic=b"GTS2"))
    with pytest.raises(SngParseError, match="11.x"):
        parse_gts1(base, "1.53")
    with pytest.raises(SngParseError, match="unknown playroutine"):
        parse_gts1(base, "1.59")
    with pytest.raises(SngParseError, match="truncated"):
        parse_gts1(base[:-4])
    with pytest.raises(SngParseError, match="endmark"):
        parse_gts1(_sng(orderlists=[[b"\x00\x01\x02"] * 3]))
    with pytest.raises(SngParseError, match="end row"):
        parse_gts1(_sng(patterns=[b"\x00" * 9]))
    with pytest.raises(SngParseError, match="bad row length"):
        parse_gts1(_sng(patterns=[b"\x00\x00\x00\x00"]))
    with pytest.raises(SngParseError, match="trailing bytes"):
        parse_gts1(base + b"\x00" * 255)
    with pytest.raises(SngParseError, match="filtertable"):
        parse_gts1(base + b"\x00" * 256)


def test_parse_rejects_bad_limits():
    with pytest.raises(SngParseError, match="subtune"):
        parse_gts1(_sng(orderlists=[[bytes((0, 0xFF, 0))] * 3] * 33))
    with pytest.raises(SngParseError, match="pattern count"):
        parse_gts1(_sng(patterns=[]))
    with pytest.raises(SngParseError, match="wavetable size"):
        parse_gts1(_sng(instruments=[(bytes(7), 5, bytes(16), b"\x00" * 5)] * 31))
    with pytest.raises(SngParseError, match="references pattern"):
        parse_gts1(_sng(orderlists=[[bytes((0x50, 0xFF, 0))] * 3]))
    with pytest.raises(SngParseError, match="zero length"):
        parse_gts1(_sng(orderlists=[[b"\x00"] * 3]))
    truncated = goatold_song("BOFH.SNG")[:400]
    with pytest.raises(SngParseError, match="truncated"):
        parse_gts1(truncated)


def test_legacy_reader_directs_to_native_parser():
    from pygoattracker.reader import parse_sng

    with pytest.raises(SngParseError, match="parse_gts1"):
        parse_sng(goatold_song("GOATCLAS.SNG"))


def test_v153_layout_still_imports_through_gt2_path():
    v153 = goattrk_song("examples/dojo.sng")
    assert v153[:4] == b"GTS!"
    with pytest.raises(SngParseError, match="filtertable"):
        parse_gts1(v153)
    from pygoattracker.reader import parse_sng

    assert parse_sng(v153).name  # the GT2 import path accepts the layout


def test_header_fields_are_raw_bytes():
    song = parse_gts1(goatold_song("GOATCLAS.SNG"))
    assert isinstance(song.sng, Gt1Sng)
    assert song.sng.author == b"Lasse \xd6\xf6rni"
    assert song.sng.copyright == b"11/2001"


def test_author_block_per_playroutine():
    data = goatold_song("GOATCLAS.SNG")
    author = b"Lasse \xd6\xf6rni".ljust(32, b" ")
    for player in ("11.1", "11.2"):
        assert relocate(parse_gts1(data, player)).author == b""
    for player in ("11.3", "11.4"):
        assert relocate(parse_gts1(data, player)).author == author
    assert relocate(parse_gts1(data, "11.5")).author == author + b"\x00"


@pytest.mark.parametrize("player", ("11.1", "11.2", "11.3", "11.4", "11.5"))
def test_playback_matches_6502_for_each_playroutine(player):
    song = parse_gts1(goatold_song("GOATCLAS.SNG"), player)
    assert Gt1Player(song).render_grid(120) == emulated(song, 120)
