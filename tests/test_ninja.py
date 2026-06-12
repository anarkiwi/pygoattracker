"""NinjaTracker 2 parsing and writing."""

import pytest

from pygoattracker import ninja
from pygoattracker.errors import NinjaParseError, NinjaValidationError
from pygoattracker.model import Orderlist, PlayPattern, Subtune, Transpose
from pygoattracker.ninja import (
    NinjaCommand,
    NinjaPattern,
    NinjaRow,
    NinjaSong,
    build_nt2,
    parse_nt2,
    read_nt2,
    rle_decode,
    rle_encode,
    track_entry_from_byte,
    track_entry_to_byte,
    write_nt2,
)

from tests._fixture_cache import (
    NT2_CLEAN_SONGS,
    NT2_EXAMPLE_SONGS,
    nt2_example_song,
)


def rich_song() -> NinjaSong:
    song = NinjaSong(hr_param=0x0F, first_wave=0x01)
    song.wavetable.left = [0x41, 0x90, 0xFF]
    song.wavetable.right = [0x00, 0x00, 0x00]
    song.pulsetable.left = [0x88, 0xFF]
    song.pulsetable.right = [0x00, 0x00]
    song.filtertable.left = [0x97, 0xFF]
    song.filtertable.right = [0x40, 0x00]
    song.commands = [
        NinjaCommand(0x0F, 0x00, 1, 1, 1, "lead"),
        NinjaCommand(0x4A, 0xB8, 2, 0, 0, "soft bass"),
    ]
    song.patterns = [
        NinjaPattern(
            rows=[
                NinjaRow(note=24, command=1, duration=8),
                NinjaRow(note=ninja.NT2_KEYOFF),
                NinjaRow(note=36, command=0x82, duration=3),
                NinjaRow(note=ninja.NT2_KEYON, command=2),
                NinjaRow(note=ninja.NT2_NONE, command=1, duration=65),
                NinjaRow(note=ninja.NT2_NONE, duration=12),
                NinjaRow(note=95),
            ]
        ),
        NinjaPattern(rows=[NinjaRow(note=48, command=2, duration=6)]),
    ]
    song.subtunes = [
        Subtune(
            channels=[
                Orderlist([PlayPattern(1), PlayPattern(2)], restart=1),
                Orderlist([Transpose(3), PlayPattern(2)], restart=0),
                Orderlist([Transpose(-12), PlayPattern(1)], restart=1),
            ]
        ),
        Subtune(
            channels=[
                Orderlist([PlayPattern(2)], restart=0),
                Orderlist([Transpose(0), PlayPattern(1)], restart=1),
                Orderlist([PlayPattern(1)], restart=0),
            ]
        ),
    ]
    return song


def test_rle_round_trip():
    block = bytes([0] * 300 + [1, 2, 3] + [ninja.NT2_ESCBYTE] + [7] * 2 + [9])
    encoded = rle_encode(block)
    assert rle_decode(encoded) == block
    # Long runs split at 255; lone escape bytes encode as a 1-run.
    assert encoded.startswith(bytes([ninja.NT2_ESCBYTE, 0, 255]))
    assert bytes([ninja.NT2_ESCBYTE, ninja.NT2_ESCBYTE, 1]) in encoded
    assert bytes([ninja.NT2_ESCBYTE, 7, 2]) in encoded


def test_rle_truncated():
    with pytest.raises(NinjaParseError, match="truncated"):
        rle_decode(bytes([ninja.NT2_ESCBYTE, 1]))


def test_track_entry_codec():
    for value in list(range(1, 0x80)) + list(range(0x80, 0x100)):
        assert track_entry_to_byte(track_entry_from_byte(value)) == value
    assert track_entry_from_byte(0xFF) == Transpose(0)
    assert track_entry_from_byte(0x82) == Transpose(3)
    assert track_entry_from_byte(0xC0) == Transpose(-63)
    assert track_entry_from_byte(0xBF) == Transpose(-64)
    assert track_entry_from_byte(0x7F) == PlayPattern(0x7F)
    with pytest.raises(NinjaParseError):
        track_entry_from_byte(0)
    with pytest.raises(NinjaValidationError):
        track_entry_to_byte(PlayPattern(0))
    with pytest.raises(NinjaValidationError):
        track_entry_to_byte(Transpose(64))
    with pytest.raises(NinjaValidationError):
        track_entry_to_byte("nope")


def test_note_names():
    assert ninja.note_name(ninja.NT2_NONE) == "..."
    assert ninja.note_name(ninja.NT2_KEYOFF) == "---"
    assert ninja.note_name(ninja.NT2_KEYON) == "+++"
    assert ninja.note_name(12) == "C-1"
    assert ninja.note_name(95) == "B-7"
    with pytest.raises(ValueError):
        ninja.note_name(96)


def test_row_str():
    assert str(NinjaRow(note=24, command=1, duration=8)) == "C-2 01 08"
    assert str(NinjaRow()) == "... .. .."


def test_default_song_round_trip():
    song = NinjaSong()
    assert parse_nt2(build_nt2(song)) == song


def test_rich_song_round_trip():
    song = rich_song()
    data = build_nt2(song)
    assert data[:2] == ninja.NT2_MAGIC
    parsed = parse_nt2(data)
    assert parsed == song
    assert build_nt2(parsed) == data


def test_file_round_trip(tmp_path):
    song = rich_song()
    path = tmp_path / "song.nt2"
    write_nt2(song, path)
    assert read_nt2(path) == song
    assert read_nt2(str(path)) == song
    assert read_nt2(path.read_bytes()) == song
    with open(path, "rb") as handle:
        assert read_nt2(handle) == song
    with open(tmp_path / "out.nt2", "wb") as handle:
        write_nt2(song, handle)
    assert (tmp_path / "out.nt2").read_bytes() == build_nt2(song)
    with pytest.raises(TypeError):
        read_nt2(12345)
    with pytest.raises(TypeError):
        write_nt2(song, 12345)


def test_accessors():
    song = rich_song()
    assert song.pattern(2) == song.patterns[1]
    assert song.pattern(99) == NinjaPattern()
    assert song.command(2).name == "soft bass"
    # The legato bit is ignored for lookup.
    assert song.command(0x82).name == "soft bass"
    assert song.command(99) == NinjaCommand()


def test_bad_magic():
    with pytest.raises(NinjaParseError, match="NinjaTracker"):
        parse_nt2(b"GTS5" + b"\0" * 100)


def test_bad_block_size():
    with pytest.raises(NinjaParseError, match="memory size"):
        parse_nt2(ninja.NT2_MAGIC + b"\x01\x02\x03")


def expect_invalid(song, match):
    with pytest.raises(NinjaValidationError, match=match):
        build_nt2(song)


def test_validation_errors():
    song = rich_song()
    song.patterns[0].rows[0].duration = 67
    expect_invalid(song, "duration")

    song = rich_song()
    song.patterns[0].rows[0].note = 5
    expect_invalid(song, "bad note")

    song = rich_song()
    song.patterns[0].rows[0].command = 0x80
    expect_invalid(song, "bad command")

    song = rich_song()
    song.patterns[0].rows[0].command = 3
    expect_invalid(song, "not defined")

    song = rich_song()
    song.subtunes[0].channels[0].entries = [PlayPattern(99), PlayPattern(1)]
    expect_invalid(song, "pattern 99 not defined")

    song = rich_song()
    song.subtunes[0].channels[0].restart = 2
    expect_invalid(song, "loop position")

    song = rich_song()
    song.subtunes[0].channels[0].entries = [PlayPattern(1)] * 260
    expect_invalid(song, "longer than 256")

    song = rich_song()
    song.subtunes[0].channels.pop()
    expect_invalid(song, "3 tracks")

    song = rich_song()
    song.commands[0].wave_ptr = 99
    expect_invalid(song, "beyond table end")

    song = rich_song()
    song.commands[0].name = "X" * 10
    expect_invalid(song, "name too long")

    song = rich_song()
    song.commands[0].name = "ゴート"
    expect_invalid(song, "latin-1")

    song = rich_song()
    song.wavetable.right.pop()
    expect_invalid(song, "lengths differ")

    song = rich_song()
    song.hr_param = 0x100
    expect_invalid(song, "hr_param")

    song = rich_song()
    song.patterns[0].rows = [NinjaRow(note=24, command=1, duration=8)] * 64
    expect_invalid(song, "too long")


def test_long_pattern_fits():
    # 63 plain note rows + terminator squeeze into one 192-byte slot.
    song = NinjaSong(
        patterns=[NinjaPattern(rows=[NinjaRow(note=24)] * 63)],
        commands=[NinjaCommand()],
    )
    assert parse_nt2(build_nt2(song)) == song


@pytest.mark.parametrize("name", sorted(NT2_EXAMPLE_SONGS))
def test_example_parses(name):
    song = parse_nt2(nt2_example_song(name))
    assert song.commands, name
    assert song.patterns, name
    assert any(subtune.channels for subtune in song.subtunes)
    # Every example uses real notes somewhere.
    notes = [
        row.note
        for pattern in song.patterns
        for row in pattern.rows
        if row.note >= ninja.NT2_FIRSTNOTE
    ]
    assert notes, name


@pytest.mark.parametrize("name", sorted(NT2_EXAMPLE_SONGS))
def test_example_semantic_round_trip(name):
    song = parse_nt2(nt2_example_song(name))
    assert parse_nt2(build_nt2(song)) == song


@pytest.mark.parametrize("name", sorted(NT2_CLEAN_SONGS))
def test_example_byte_identical_round_trip(name):
    data = nt2_example_song(name)
    assert build_nt2(parse_nt2(data)) == data


def test_consultant_content():
    song = parse_nt2(nt2_example_song("CONSULTANT"))
    assert song.first_wave == 0x09
    names = [command.name for command in song.commands]
    assert any(names)
    assert len(song.subtunes) == 2
    assert all(len(subtune.channels) == 3 for subtune in song.subtunes)
    # Both tunes' first track entries are transpose zero ($FF).
    assert song.subtunes[0].channels[0].entries[0] == Transpose(0)
