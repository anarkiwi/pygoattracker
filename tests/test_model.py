"""Model and constants behavior."""

import pytest

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import (
    Instrument,
    PlayPattern,
    Repeat,
    Row,
    Song,
    Table,
    Transpose,
    entry_from_byte,
)


def test_note_names_round_trip():
    for value in range(constants.FIRSTNOTE, constants.LASTNOTE + 1):
        assert constants.note_value(constants.note_name(value)) == value
    assert constants.note_name(constants.REST) == "..."
    assert constants.note_name(constants.KEYOFF) == "---"
    assert constants.note_name(constants.KEYON) == "+++"
    assert constants.note_name(constants.ENDPATT) == "END"
    assert constants.note_value("A-4") == constants.FIRSTNOTE + 57


@pytest.mark.parametrize("bad", [0x00, 0x5F, 0xC0, 0xFE])
def test_bad_note_byte(bad):
    with pytest.raises(ValueError):
        constants.note_name(bad)


@pytest.mark.parametrize("bad", ["", "H-4", "C-8", "C#9", "c-4x"])
def test_bad_note_name(bad):
    with pytest.raises(ValueError):
        constants.note_value(bad)


def test_freq_table():
    assert len(constants.FREQ_TABLE) == 128
    # A-4 in the GoatTracker PAL table.
    assert constants.FREQ_TABLE[57] == 0x1D46
    notes = constants.FREQ_TABLE[: constants.MAX_NOTES]
    assert list(notes) == sorted(notes)
    assert all(value == 0 for value in constants.FREQ_TABLE[constants.MAX_NOTES :])


def test_order_entries_round_trip():
    for value in range(0xFF):
        assert entry_from_byte(value).to_byte() == value
    assert entry_from_byte(0x10) == PlayPattern(0x10)
    assert entry_from_byte(0xD3) == Repeat(3)
    assert entry_from_byte(0xE1) == Transpose(-15)
    assert entry_from_byte(0xFE) == Transpose(14)
    with pytest.raises(GoatTrackerError):
        entry_from_byte(constants.LOOPSONG)


@pytest.mark.parametrize(
    "entry",
    [PlayPattern(-1), PlayPattern(0xD0), Repeat(16), Repeat(-1), Transpose(15)],
)
def test_bad_order_entries(entry):
    with pytest.raises(GoatTrackerError):
        entry.to_byte()


def test_table_add():
    table = Table()
    assert table.add(0x41, 0) == 1
    assert table.add(0xFF, 0) == 2
    assert len(table) == 2
    table.left = [0] * constants.MAX_TABLELEN
    table.right = list(table.left)
    with pytest.raises(GoatTrackerError):
        table.add(0, 0)


def test_song_defaults():
    song = Song()
    assert len(song.subtunes) == 1
    assert len(song.subtunes[0].channels) == constants.MAX_CHN
    assert song.subtunes[0].channels[0].entries == [PlayPattern(0)]
    assert len(song.patterns) == 1
    assert len(song.patterns[0].rows) == 64
    assert song.instrument(0) == Instrument()
    assert song.instrument(1) == Instrument()
    song.instruments.append(Instrument(name="LEAD"))
    assert song.instrument(1).name == "LEAD"
    assert [len(table) for table in song.tables()] == [0, 0, 0, 0]


def test_row_str():
    row = Row(note=constants.note_value("C-4"), instrument=1, command=0xF, data=4)
    assert str(row) == "C-4 01F04"
    assert str(Row()) == "... 00000"
