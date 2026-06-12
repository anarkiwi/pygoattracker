"""Synthetic write -> read -> write round trips."""

from pygoattracker import constants
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Subtune,
    Transpose,
)
from pygoattracker.reader import parse_sng, read_sng
from pygoattracker.writer import build_sng, write_sng

from tests.conftest import basic_song, note


def rich_song() -> Song:
    """A song touching every format feature."""
    song = Song(name="RICH", author="AN AUTHOR", copyright="2026 NOBODY")
    song.wavetable.left = [0x09, 0x41, 0xFF]
    song.wavetable.right = [0x00, 0x00, 0x02]
    song.pulsetable.left = [0x88, 0x20, 0xFF]
    song.pulsetable.right = [0x00, 0x40, 0x00]
    song.filtertable.left = [0x90, 0x00, 0xFF]
    song.filtertable.right = [0xF1, 0x40, 0x00]
    song.speedtable.left = [0x03, 0x01]
    song.speedtable.right = [0x40, 0x00]
    song.instruments = [
        Instrument(0x09, 0x00, 1, 1, 1, 1, 2, 2, 0x09, "LEAD"),
        Instrument(0x0A, 0x8A, 2, 0, 0, 0, 0, 0x42, 0x00, "LEGATO"),
    ]
    song.patterns = [
        Pattern(
            rows=[
                Row(note("C-4"), 1, 0, 0),
                Row(constants.REST, 0, 0xF, 3),
                Row(note("G#7"), 2, 4, 1),
                Row(constants.KEYOFF, 0, 0, 0),
            ]
        ),
        Pattern.empty(128),
        Pattern(rows=[Row(constants.KEYON, 0, 0xD, 0x0A)]),
    ]
    song.subtunes = [
        Subtune(
            channels=[
                Orderlist([PlayPattern(0), PlayPattern(1)], restart=1),
                Orderlist([Transpose(-15), PlayPattern(1)], restart=0),
                Orderlist([Repeat(3), PlayPattern(2)], restart=0),
            ]
        ),
        Subtune(
            channels=[
                Orderlist([Transpose(14), Repeat(0), PlayPattern(0)], restart=0),
                Orderlist([PlayPattern(2)], restart=0),
                Orderlist([PlayPattern(1)], restart=0),
            ]
        ),
    ]
    return song


def test_default_song_round_trip():
    song = Song()
    assert parse_sng(build_sng(song)) == song


def test_basic_song_round_trip():
    song = basic_song()
    assert parse_sng(build_sng(song)) == song


def test_rich_song_round_trip():
    song = rich_song()
    data = build_sng(song)
    assert data[:4] == constants.SNG_MAGIC
    parsed = parse_sng(data)
    assert parsed == song
    assert build_sng(parsed) == data


def test_header_fields():
    data = build_sng(rich_song())
    assert data[4:8] == b"RICH"
    assert data[36:45] == b"AN AUTHOR"
    assert data[68:79] == b"2026 NOBODY"
    assert data[100] == 2


def test_file_round_trip(tmp_path):
    path = tmp_path / "song.sng"
    song = rich_song()
    write_sng(song, path)
    assert read_sng(path) == song
    assert read_sng(str(path)) == song
    assert read_sng(path.read_bytes()) == song
    with open(path, "rb") as handle:
        assert read_sng(handle) == song
    with open(tmp_path / "out.sng", "wb") as handle:
        write_sng(song, handle)
    assert (tmp_path / "out.sng").read_bytes() == build_sng(song)


def test_gts3_magic_accepted():
    data = bytearray(build_sng(basic_song()))
    data[:4] = b"GTS3"
    assert parse_sng(bytes(data)) == basic_song()
